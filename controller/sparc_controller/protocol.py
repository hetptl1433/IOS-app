"""Versioned, authenticated application channel shared with CryptoKit on iOS."""
from __future__ import annotations

import base64
import hashlib
import json
import math
import secrets
import struct
import time
import uuid
from collections import OrderedDict
from dataclasses import dataclass

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import ChaCha20Poly1305
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

MAX_FRAME = 16_384
MAX_AGE_SECONDS = 90


class ProtocolError(ValueError):
    """A deliberately generic error; never include credential/payload contents."""


def compact_json(value: object) -> bytes:
    return json.dumps(value, separators=(",", ":"), ensure_ascii=False, allow_nan=False, sort_keys=True).encode()


def setup_key(value: str) -> bytes:
    if len(value) != 64 or any(c not in "0123456789abcdefABCDEF" for c in value):
        raise ProtocolError("Setup key must contain exactly 64 hexadecimal characters.")
    return bytes.fromhex(value)


def pairing_code_key(code: str) -> str:
    """App-test convenience: a short shared code replaces the private key, so anyone who knows it can pair."""
    if not 4 <= len(code) <= 8 or any(c not in "0123456789" for c in code):
        raise ProtocolError("Pairing code must contain 4 to 8 digits.")
    return hashlib.sha256(b"SPARC pairing code\n" + code.encode()).hexdigest()


def derive_key(master: bytes, device_id: str, direction: str) -> bytes:
    if len(master) != 32 or direction not in ("c2d", "d2c"):
        raise ProtocolError("Invalid channel parameters.")
    return HKDF(algorithm=hashes.SHA256(), length=32, salt=device_id.encode(),
                info=f"SPARC/1/{direction}".encode()).derive(master)


@dataclass(frozen=True)
class DeviceInfo:
    device_id: str
    boot_id: str
    name: str
    wifi_provision: bool = True
    app_test: bool = False
    pairing_code: bool = False

    def public(self, now: float | None = None) -> dict:
        return {"version": 1, "deviceId": self.device_id, "bootId": self.boot_id,
                "name": self.name, "serverTime": time.time() if now is None else now,
                "capabilities": ["pair", "ping"] + (["wifi.provision"] if self.wifi_provision else [])
                    + (["app-test", "virtual-car", "set_virtual_car", "get_snapshot", "charge_now", "pause", "resume", "end"] if self.app_test else [])
                    + (["pairing-code"] if self.pairing_code else []),
                "vehicleAdapterAvailable": False}


class SecureChannel:
    def __init__(self, master: bytes, info: DeviceInfo):
        self.info = info
        self.ciphers = {d: ChaCha20Poly1305(derive_key(master, info.device_id, d)) for d in ("c2d", "d2c")}
        # Never evict live entries: fail closed when full, preventing replay after eviction.
        self.seen: OrderedDict[str, float] = OrderedDict()

    def aad(self, direction: str) -> bytes:
        return f"SPARC/1\n{self.info.device_id}\n{self.info.boot_id}\n{direction}".encode()

    def seal(self, payload: dict, direction: str, nonce: bytes | None = None) -> bytes:
        nonce = secrets.token_bytes(12) if nonce is None else nonce
        if len(nonce) != 12:
            raise ProtocolError("Invalid nonce.")
        combined = nonce + self.ciphers[direction].encrypt(nonce, compact_json(payload), self.aad(direction))
        encoded = compact_json({"v": 1, "box": base64.b64encode(combined).decode()})
        if len(encoded) > MAX_FRAME:
            raise ProtocolError("Frame too large.")
        return encoded

    def open(self, encoded: bytes, direction: str) -> dict:
        if not encoded or len(encoded) > MAX_FRAME:
            raise ProtocolError("Invalid frame length.")
        try:
            envelope = json.loads(encoded)
            if envelope.get("v") != 1 or isinstance(envelope.get("v"), bool):
                raise ValueError()
            combined = base64.b64decode(envelope["box"], validate=True)
            if len(combined) < 28:
                raise ValueError()
            payload = json.loads(self.ciphers[direction].decrypt(combined[:12], combined[12:], self.aad(direction)))
            if not isinstance(payload, dict):
                raise ValueError()
            return payload
        except Exception as exc:
            raise ProtocolError("Authentication failed or malformed message.") from exc

    def open_request(self, encoded: bytes, now: float | None = None) -> dict:
        now = time.time() if now is None else now
        message = self.open(encoded, "c2d")
        message_id, timestamp = message.get("id"), message.get("timestamp")
        try:
            uuid.UUID(message_id)
        except (ValueError, TypeError, AttributeError) as exc:
            raise ProtocolError("Invalid request identifier.") from exc
        if (not isinstance(timestamp, (int, float)) or isinstance(timestamp, bool)
                or not math.isfinite(timestamp) or abs(now - timestamp) > MAX_AGE_SECONDS):
            raise ProtocolError("Request expired; reconnect to refresh the device clock offset.")
        for key, expiry in list(self.seen.items()):
            if expiry < now:
                del self.seen[key]
        if message_id.lower() in self.seen:
            raise ProtocolError("Duplicate request.")
        if len(self.seen) >= 4096:
            raise ProtocolError("Request capacity reached; retry later.")
        if not isinstance(message.get("operation"), str) or not isinstance(message.get("body"), dict):
            raise ProtocolError("Invalid request.")
        # Future-dated messages remain replay-protected through their full acceptance window.
        self.seen[message_id.lower()] = timestamp + MAX_AGE_SECONDS
        return message


class FrameBuffer:
    """4-byte big-endian length plus JSON bytes, permitting arbitrary ATT chunk boundaries."""
    def __init__(self):
        self.data = bytearray()
        self.started = time.monotonic()

    def append(self, value: bytes) -> bytes | None:
        if time.monotonic() - self.started > 15:
            self.data.clear()
            self.started = time.monotonic()
        self.data.extend(value)
        if len(self.data) < 4:
            return None
        length = struct.unpack(">I", self.data[:4])[0]
        if length < 1 or length > MAX_FRAME or len(self.data) > length + 4:
            self.data.clear()
            raise ProtocolError("Invalid frame length.")
        if len(self.data) == length + 4:
            output = bytes(self.data[4:])
            self.data.clear()
            self.started = time.monotonic()
            return output
        return None


def frame(data: bytes) -> bytes:
    if not data or len(data) > MAX_FRAME:
        raise ProtocolError("Invalid frame length.")
    return struct.pack(">I", len(data)) + data
