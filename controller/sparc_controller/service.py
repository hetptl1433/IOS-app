from __future__ import annotations

import asyncio
import json
import os
import secrets
import socket
import tempfile
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

from .network import NetworkManagerWiFi, WiFiError, local_hostname
from .protocol import DeviceInfo, ProtocolError, SecureChannel, compact_json, setup_key


def atomic_save(path: Path, value: dict, *, overwrite: bool = True) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    # A unique exclusive temporary file also keeps simultaneous initializations
    # from truncating each other's identity before the final atomic operation.
    fd, temporary_name = tempfile.mkstemp(prefix="." + path.name + ".", suffix=".tmp", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(fd, "w") as stream:
            json.dump(value, stream, separators=(",", ":"), allow_nan=False)
            stream.flush()
            os.fsync(stream.fileno())
        if overwrite:
            os.replace(temporary, path)
        else:
            # Unlike a check followed by replace, link never overwrites an
            # identity created by another process between those operations.
            os.link(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        temporary.unlink(missing_ok=True)


def initialize(state_dir: Path) -> dict:
    path = state_dir / "identity.json"
    identity = {"deviceId": str(uuid.uuid4()), "setupKey": secrets.token_hex(32)}
    try:
        atomic_save(path, identity, overwrite=False)
    except FileExistsError:
        raise ValueError("Identity already exists. It was not replaced.") from None
    return identity


def validate_weekly(body: dict) -> dict:
    target, departure, fixed, days = (body.get(k) for k in ("targetPercent", "departureMinutes", "fixedStartMinutes", "days"))
    if (type(target) is not int or not 50 <= target <= 100 or type(departure) is not int or not 0 <= departure < 1440
            or type(fixed) is not int or not 0 <= fixed < 1440 or not isinstance(days, list)
            or (not days and body.get("enabled") is True) or len(days) > 7 or any(type(x) is not int or not 1 <= x <= 7 for x in days)
            or len(set(days)) != len(days) or body.get("mode") not in ("smart", "fixed") or type(body.get("enabled")) is not bool):
        raise ValueError("Choose a valid target, departure time and at least one charging day.")
    return {"targetPercent": target, "departureMinutes": departure, "fixedStartMinutes": fixed,
            "days": sorted(days), "mode": body["mode"], "enabled": body["enabled"]}


def validate_today(value: object) -> dict | None:
    if value is None:
        return None
    if not isinstance(value, dict) or type(value.get("targetPercent")) is not int or not 50 <= value["targetPercent"] <= 100:
        raise ValueError("Choose a valid one-time charge target.")
    try:
        departure = datetime.fromisoformat(value["departure"].replace("Z", "+00:00"))
        remaining = (departure - datetime.now(timezone.utc)).total_seconds()
        if not 0 < remaining <= 48 * 3600:
            raise ValueError()
    except (ValueError, TypeError, KeyError, AttributeError):
        raise ValueError("Choose a departure within the next 48 hours.") from None
    return {"targetPercent": value["targetPercent"], "departure": departure.isoformat()}


class ControllerService:
    def __init__(self, state_dir: Path, setup_seconds: int = 600, wifi=None):
        identity = json.loads((state_dir / "identity.json").read_text())
        # A new boot ID invalidates all captured packets across process restarts.
        self.info = DeviceInfo(str(uuid.UUID(identity["deviceId"])), str(uuid.uuid4()), "SPARC Raspberry Pi 4")
        self.channel = SecureChannel(setup_key(identity["setupKey"]), self.info)
        self.state_dir = state_dir
        self.setup_deadline = time.monotonic() + max(0, setup_seconds)
        self.wifi = wifi or NetworkManagerWiFi()
        self.busy_wifi = False
        self._lock = asyncio.Lock()
        default = {"weekly": {"targetPercent": 80, "departureMinutes": 420, "days": [1,2,3,4,5],
                              "enabled": True, "mode": "smart", "fixedStartMinutes": 60}, "today": None}
        self.settings = json.loads((state_dir / "settings.json").read_text()) if (state_dir / "settings.json").exists() else default
        self.settings["weekly"] = validate_weekly(self.settings["weekly"])
        self.expire_today()

    @property
    def setup_open(self) -> bool:
        return time.monotonic() < self.setup_deadline

    def expire_today(self) -> None:
        if self.settings.get("today") is not None:
            try:
                self.settings["today"] = validate_today(self.settings["today"])
            except ValueError:
                self.settings["today"] = None

    async def handle(self, data: bytes, transport: str) -> bytes:
        request = self.channel.open_request(data)
        message_id = request["id"]
        try:
            result = await self.dispatch(request["operation"], request["body"], transport)
            response = {"id": message_id, "ok": True, "body": result}
        except (ValueError, WiFiError) as exc:
            response = {"id": message_id, "ok": False, "error": {"code": "invalid_request", "message": str(exc)}}
        except Exception:
            response = {"id": message_id, "ok": False, "error": {"code": "unavailable", "message": "The controller could not complete this request. Retry or inspect the controller service."}}
        return self.channel.seal(response, "d2c")

    async def dispatch(self, operation: str, body: dict, transport: str) -> dict:
        if operation == "ping":
            return {"connected": True, "deviceId": self.info.device_id, "vehicleAdapterAvailable": False}
        if operation in ("pair", "wifi.provision"):
            if transport != "ble" or not self.setup_open:
                raise ValueError("Open the controller’s local setup window and reconnect over Bluetooth.")
            if operation == "pair":
                return {"deviceId": self.info.device_id, "host": local_hostname(), "vehicleAdapterAvailable": False}
            if self.busy_wifi:
                raise ValueError("Wi-Fi setup is already running. Wait for it to finish.")
            self.busy_wifi = True
            try:
                return await asyncio.wait_for(self.wifi.connect(body.get("ssid"), body.get("password")), timeout=65)
            finally:
                self.busy_wifi = False
        if operation == "get_settings":
            self.expire_today()
            return self.settings
        if operation in ("set_weekly", "set_today"):
            async with self._lock:
                updated = dict(self.settings)
                if operation == "set_weekly":
                    updated["weekly"] = validate_weekly(body)
                else:
                    updated["today"] = validate_today(body.get("today"))
                atomic_save(self.state_dir / "settings.json", updated)
                self.settings = updated
                return {"storedOnly": True, "vehicleAdapterAvailable": False}
        if operation in ("get_snapshot", "charge_now", "pause", "resume", "end"):
            raise ValueError("Your Pi is connected. Install the vehicle adapter before live charging data and controls become available.")
        raise ValueError("This controller does not support that operation. Update the app and controller together.")
