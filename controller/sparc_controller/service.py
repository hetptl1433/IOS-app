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

from .app_test import CHICAGO, VIRTUAL_CAPACITY_KWH, VIRTUAL_EFFICIENCY, AppTestControl
from .network import NetworkManagerWiFi, WiFiError, local_hostname
from .protocol import DeviceInfo, ProtocolError, SecureChannel, compact_json, pairing_code_key, setup_key


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


def validate_today(value: object, *, now: datetime | None = None) -> dict | None:
    if value is None:
        return None
    if not isinstance(value, dict) or type(value.get("targetPercent")) is not int or not 50 <= value["targetPercent"] <= 100:
        raise ValueError("Choose a valid one-time charge target.")
    try:
        departure = datetime.fromisoformat(value["departure"].replace("Z", "+00:00"))
        remaining = (departure - (now if now is not None else datetime.now(timezone.utc))).total_seconds()
        if not 0 < remaining <= 48 * 3600:
            raise ValueError()
    except (ValueError, TypeError, KeyError, AttributeError):
        raise ValueError("Choose a departure within the next 48 hours.") from None
    return {"targetPercent": value["targetPercent"], "departure": departure.isoformat()}


class ControllerService:
    def __init__(self, state_dir: Path, setup_seconds: int = 600, wifi=None, *, allow_wifi_provision: bool = True,
                 app_test: bool = False, clock=None, pairing_code: str | None = None):
        identity = json.loads((state_dir / "identity.json").read_text())
        if pairing_code is not None and not app_test:
            raise ValueError("A pairing code is available in app-test mode only.")
        # A new boot ID invalidates all captured packets across process restarts.
        self.info = DeviceInfo(str(uuid.UUID(identity["deviceId"])), str(uuid.uuid4()),
                               "SPARC app test controller" if app_test else "SPARC home controller",
                               wifi_provision=allow_wifi_provision, app_test=app_test,
                               pairing_code=pairing_code is not None)
        # The code replaces the identity key for this process only; identity.json is never rewritten.
        key = pairing_code_key(pairing_code) if pairing_code is not None else identity["setupKey"]
        self.channel = SecureChannel(setup_key(key), self.info)
        self.state_dir = state_dir
        self.setup_deadline = time.monotonic() + max(0, setup_seconds)
        self.allow_wifi_provision = allow_wifi_provision
        self.clock = clock if clock is not None else lambda: datetime.now(timezone.utc)
        self.app_test = AppTestControl(self.clock) if app_test else None
        # LAN-only never creates a network provisioning adapter or needs its D-Bus package.
        self.wifi = (wifi if wifi is not None else NetworkManagerWiFi()) if allow_wifi_provision else None
        self.busy_wifi = False
        self._lock = asyncio.Lock()
        self.started = time.monotonic()
        self.last_contact: tuple[str, float] | None = None
        self.ble_advertising = False
        default = {"weekly": {"targetPercent": 80, "departureMinutes": 420, "days": [1,2,3,4,5],
                              "enabled": True, "mode": "smart", "fixedStartMinutes": 60}, "today": None}
        self.settings = json.loads((state_dir / "settings.json").read_text()) if (state_dir / "settings.json").exists() else default
        self.settings["weekly"] = validate_weekly(self.settings["weekly"])
        if self.app_test:
            self.app_test.control_state(self.settings)
            updated = dict(self.settings)
            if "virtualCarEnabled" not in updated:
                updated["virtualCarEnabled"] = True
                updated["appTestControl"] = {"manualState": None, "manualUntil": None}
                updated["virtualCarState"] = self.app_test.initial_virtual_state()
            elif "virtualCarState" not in updated:
                updated["virtualCarState"] = self.app_test.initial_virtual_state()
            self.app_test.virtual_enabled(updated)
            self.app_test.virtual_state(updated)
            if updated != self.settings:
                atomic_save(self.state_dir / "settings.json", updated)
                self.settings = updated
        # An in-memory anchor intentionally excludes time while the process is
        # stopped. Persist SOC/counters, never extrapolate electricity downtime.
        self._virtual_anchor = self.clock().astimezone(timezone.utc)
        self.expire_today()

    @property
    def virtual_car_enabled(self) -> bool:
        return self.app_test is not None and self.app_test.virtual_enabled(self.settings)

    def mode_info(self) -> dict:
        return {"vehicleAdapterAvailable": False, "virtualCar": self.virtual_car_enabled,
                "controlOnly": self.app_test is not None and not self.virtual_car_enabled}

    def advance_virtual(self) -> None:
        """Call under _lock before replacing the schedule/manual control state."""
        now = self.clock().astimezone(timezone.utc)
        if self.virtual_car_enabled and now > self._virtual_anchor:
            model = self.app_test.advance_virtual(self.settings, self._virtual_anchor, now)
            if model != self.settings["virtualCarState"]:
                updated = dict(self.settings, virtualCarState=model)
                atomic_save(self.state_dir / "settings.json", updated)
                self.settings = updated
        self._virtual_anchor = now

    def display_state(self) -> dict:
        """What the Pi's own screen shows, projected to now without writing settings to disk."""
        now = self.clock().astimezone(timezone.utc)
        settings, snapshot, battery, per_hour, manual = self.settings, None, None, 0.0, None
        if self.app_test is not None:
            if self.virtual_car_enabled and now > self._virtual_anchor:
                settings = dict(settings, virtualCarState=self.app_test.advance_virtual(settings, self._virtual_anchor, now))
            snapshot = self.app_test.snapshot(settings)
            # End or Pause can hold off the schedule until departure; the screen says so.
            held, until = self.app_test.control_state(settings)
            if held is not None and (until is None or until > now):
                manual = {"state": held, "until": None if until is None else until.astimezone(timezone.utc).isoformat()}
            if self.virtual_car_enabled:
                battery = settings["virtualCarState"]["batteryPercent"]
                if snapshot["state"] == "charging":
                    per_hour = snapshot["powerKW"] * VIRTUAL_EFFICIENCY / VIRTUAL_CAPACITY_KWH * 100
        try:
            trip = validate_today(settings.get("today"), now=now)
        except ValueError:
            trip = None
        local = now.astimezone(CHICAGO)
        contact = self.last_contact
        return {"snapshot": snapshot, "batteryPercent": battery, "batteryPercentPerHour": per_hour,
                "manual": manual, "weekly": settings["weekly"], "trip": trip,
                "local": {"date": local.date().isoformat(), "minutes": local.hour * 60 + local.minute},
                "controller": {"id": self.info.device_id[:4].upper(), "bluetooth": self.ble_advertising,
                               "uptimeSeconds": int(time.monotonic() - self.started),
                               "phoneSeconds": None if contact is None else int(time.monotonic() - contact[1]),
                               "phoneTransport": None if contact is None else contact[0]}}

    @property
    def setup_open(self) -> bool:
        return time.monotonic() < self.setup_deadline

    def expire_today(self) -> None:
        if self.settings.get("today") is not None:
            try:
                self.settings["today"] = validate_today(self.settings["today"], now=self.clock())
            except ValueError:
                self.settings["today"] = None

    async def handle(self, data: bytes, transport: str) -> bytes:
        request = self.channel.open_request(data)
        self.last_contact = (transport, time.monotonic())
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
            return {"connected": True, "deviceId": self.info.device_id, **self.mode_info()}
        if operation == "pair":
            # handle() has already authenticated this request and consumed its replay ID.
            # Discovery alone is untrusted; the phone must authenticate this sealed reply.
            # Anyone who knows a shared pairing code can already send commands, so a window adds nothing.
            if transport not in ("ble", "lan") or not (self.setup_open or self.info.pairing_code):
                raise ValueError("Open the controller’s local setup window, then pair again.")
            return {"deviceId": self.info.device_id, "host": local_hostname(), **self.mode_info()}
        if operation == "wifi.provision":
            if not self.allow_wifi_provision:
                raise ValueError("Wi-Fi provisioning is disabled in LAN-only mode. Use the controller’s existing network connection.")
            if transport != "ble" or not self.setup_open:
                raise ValueError("Open the controller’s local setup window and reconnect over Bluetooth.")
            if self.busy_wifi:
                raise ValueError("Wi-Fi setup is already running. Wait for it to finish.")
            self.busy_wifi = True
            try:
                return await asyncio.wait_for(self.wifi.connect(body.get("ssid"), body.get("password")), timeout=65)
            finally:
                self.busy_wifi = False
        if operation == "get_settings":
            async with self._lock:
                self.advance_virtual()
                self.expire_today()
                result = {"weekly": self.settings["weekly"], "today": self.settings.get("today")}
                if self.app_test:
                    result["virtualCarEnabled"] = self.virtual_car_enabled
                return result
        if operation == "set_virtual_car":
            if self.app_test is None:
                raise ValueError("Virtual car is available in app-test mode only.")
            enabled = body.get("enabled")
            if type(enabled) is not bool:
                raise ValueError("Choose whether Virtual car is enabled.")
            async with self._lock:
                self.advance_virtual()
                if enabled != self.virtual_car_enabled:
                    updated = dict(self.settings, virtualCarEnabled=enabled)
                    updated["appTestControl"] = {"manualState": None, "manualUntil": None}
                    if enabled:
                        updated["virtualCarState"] = self.app_test.initial_virtual_state()
                    atomic_save(self.state_dir / "settings.json", updated)
                    self.settings = updated
                return {"virtualCarEnabled": self.virtual_car_enabled, **self.mode_info()}
        if operation in ("set_weekly", "set_today"):
            async with self._lock:
                self.advance_virtual()
                updated = dict(self.settings)
                if operation == "set_weekly":
                    updated["weekly"] = validate_weekly(body)
                else:
                    updated["today"] = validate_today(body.get("today"), now=self.clock())
                if self.app_test:
                    # Schedule and manual-override changes commit together, so a
                    # failed disk write never acknowledges a partial update.
                    updated["appTestControl"] = {"manualState": None, "manualUntil": None}
                atomic_save(self.state_dir / "settings.json", updated)
                self.settings = updated
                return {"storedOnly": True, **self.mode_info()}
        if operation in ("get_snapshot", "charge_now", "pause", "resume", "end"):
            if self.app_test is None:
                raise ValueError("Your Pi is connected. Install the vehicle adapter before live charging data and controls become available.")
            async with self._lock:
                self.advance_virtual()
                self.expire_today()
                if operation != "get_snapshot":
                    if not self.virtual_car_enabled:
                        raise ValueError("Virtual car is off. Turn it on before using test charging controls.")
                    updated = dict(self.settings)
                    updated["appTestControl"] = self.app_test.command_value(operation, updated)
                    atomic_save(self.state_dir / "settings.json", updated)
                    self.settings = updated
                return self.app_test.snapshot(self.settings)
        raise ValueError("This controller does not support that operation. Update the app and controller together.")
