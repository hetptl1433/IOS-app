from __future__ import annotations

import asyncio
from copy import copy, deepcopy
import hashlib
import json
import os
import secrets
import socket
import tempfile
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .app_test import CHICAGO, VIRTUAL_CAPACITY_KWH, VIRTUAL_EFFICIENCY, VIRTUAL_POWER_KW, AppTestControl, smart_plan_enabled
from .network import NetworkManagerWiFi, WiFiError, local_hostname, pi_temperature
from .overnight import current_config, updated_settings
from .routine import normalize as normalize_campaign, view as campaign_view, updated as update_campaign
from .history import HistoryStore
from .clients import ClientRegistry, clean_client, control_state, command_settings
from .virtual_dev import view as developer_view, updated as developer_updated
from .connections_service import ConnectionServiceMixin
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


class ControllerService(ConnectionServiceMixin):
    def __init__(self, state_dir: Path, setup_seconds: int = 600, wifi=None, *, allow_wifi_provision: bool = True,
                 app_test: bool = False, clock=None, pairing_code: str | None = None):
        identity = json.loads((state_dir / "identity.json").read_text())
        if pairing_code is not None and not app_test:
            raise ValueError("A pairing code is available in app-test mode only.")
        # A new boot ID invalidates all captured packets across process restarts.
        self.info = DeviceInfo(str(uuid.UUID(identity["deviceId"])), str(uuid.uuid4()),
                               "SPARC controller" if app_test else "SPARC home controller",
                               wifi_provision=allow_wifi_provision, app_test=app_test,
                               pairing_code=pairing_code is not None)
        # The code replaces the identity key for this process only; identity.json is never rewritten.
        key = pairing_code_key(pairing_code) if pairing_code is not None else identity["setupKey"]
        self.channel = SecureChannel(setup_key(key), self.info)
        self.state_dir = state_dir
        self.setup_deadline = time.monotonic() + max(0, setup_seconds)
        self.allow_wifi_provision = allow_wifi_provision
        self.clock = clock if clock is not None else lambda: datetime.now(timezone.utc)
        self.pricing = None
        if app_test and os.environ.get("SPARC_REAL_PRICING") == "1":
            from .pricing import ComEdPricing
            self.pricing = ComEdPricing(cache=state_dir / "comed-prices.json", log=os.environ.get("SPARC_PRICE_LOG"))
        self.app_test = AppTestControl(self.clock, self.pricing) if app_test else None
        # LAN-only never creates a network provisioning adapter or needs its D-Bus package.
        self.wifi = (wifi if wifi is not None else NetworkManagerWiFi()) if allow_wifi_provision else None
        self.busy_wifi = False
        self.wifi_state: dict | None = None
        self._lock = asyncio.Lock()
        self.started = time.monotonic()
        self.last_contact: tuple[str, float] | None = None
        self.ble_advertising = False
        default = {"weekly": {"targetPercent": 80, "departureMinutes": 420, "days": [1,2,3,4,5],
                              "enabled": True, "mode": "smart", "fixedStartMinutes": 60}, "today": None}
        self.settings = json.loads((state_dir / "settings.json").read_text()) if (state_dir / "settings.json").exists() else default
        # Import the already-running finite campaign once. Settings then become
        # the single atomic source used by both interfaces and the supervisor.
        if "campaign" not in self.settings and (state_dir / "campaign.json").exists():
            self.settings["campaign"] = normalize_campaign(json.loads((state_dir / "campaign.json").read_text()))
            atomic_save(state_dir / "settings.json", self.settings)
        self.history = HistoryStore(state_dir, log_root=Path(os.environ.get("SPARC_HISTORY_LOG_ROOT", "/var/log/sparc")), clock=self.clock)
        self.clients = ClientRegistry(state_dir, atomic_save, self.clock)
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
        self.init_connections(atomic_save)
        self.expire_today()

    def history_inputs(self):
        pricing = copy(self.pricing) if self.pricing is not None else None
        if pricing is not None:
            for name in ("samples", "settled_hourly", "day_ahead"):
                setattr(pricing, name, dict(getattr(pricing, name)))
        return deepcopy(self.settings), pricing, self.clock()

    def command_candidate(self,candidate,operation,transport,client):
        return command_settings(self.settings,candidate,operation,transport,client,self.clock())

    def shared_state(self,client=None):
        from .sleep_calendar import public as calendar_public
        result={"sleepCalendar":calendar_public(self.settings,self.clock()),"control":control_state(self.settings),"clients":self.clients.summary(client),
                "connections":self.connections_view()}
        if self.app_test is not None: result["developer"]=developer_view(self.settings,self.clock())
        return result

    async def capture_history(self, *, force=False):
        async with self._lock:
            settings, pricing, now = self.history_inputs()
        await asyncio.to_thread(self.history.capture, settings, pricing, now=now, force=force)

    def history_pricing(self):
        """Live ComEd prices for history written on the event loop, or None."""
        return self.pricing

    async def history_view(self, body=None):
        body = body or {}
        if not isinstance(body, dict) or set(body) - {"cursor", "limit", "car", "vehicleId"}:
            raise ValueError("Choose a valid history page.")
        cursor, limit = body.get("cursor", 0), body.get("limit", 8)
        if type(cursor) is not int or not 0 <= cursor <= 100000 or type(limit) is not int or not 1 <= limit <= 8:
            raise ValueError("Choose a valid history page.")
        # Each car keeps its own history; omitting the car keeps older apps on the virtual car.
        car, vehicle = body.get("car", "virtual"), body.get("vehicleId")
        if car not in ("virtual", "tesla") or (vehicle is not None and (car != "tesla" or not isinstance(vehicle, str))):
            raise ValueError("Choose the virtual car or a Tesla for history.")
        async with self._lock:
            self.advance_virtual()
            settings, pricing, now = self.history_inputs()
        return await asyncio.to_thread(self.history.view, settings, pricing, cursor=cursor, limit=limit, now=now,
                                       car=car, vehicle_id=vehicle)

    async def savings_view(self, body=None):
        from .billing import compare, summary, DEFAULT_CLASS, CHICAGO
        from .history import read_json, clean_buckets, immediate_buckets, instant, iso
        body = {} if body is None else body
        if not isinstance(body,dict) or set(body)-{"kind"} or body.get("kind","simulation") not in ("simulation","recorded"):
            raise ValueError("Choose recorded savings or the historical simulation.")
        kind=body.get("kind","simulation")
        if kind=="simulation":
            report=read_json(self.state_dir/"savings-replay.json",maximum=65536)
            if not isinstance(report,dict) or report.get("schemaVersion")!=1 or report.get("kind")!="simulation" or len(report.get("nights",[]))>15:
                raise ValueError("No historical savings simulation has been installed yet.")
            return report
        async with self._lock:
            self.advance_virtual()
            settings,pricing,now=self.history_inputs()
        def collect():
            with self.history._lock:
                records,_,_=self.history._collect(settings,pricing,now)
            today=now.astimezone(CHICAGO).date()
            rows=[]
            for record in records.values():
                day=instant(record['start']).astimezone(CHICAGO).date()
                if not today-timedelta(days=14)<=day<=today or record['energyKWh']<=1e-9:continue
                actual=clean_buckets(record.get('energyByHour'))
                baseline=immediate_buckets(record['start'],record['energyKWh'])
                row=compare(record['energyKWh'],record['actualAccounting'].get('cost'),record['baselineAccounting'].get('cost'),actual,baseline)
                row.update(id=record['id'],date=str(day),simulated=record.get('simulated',True),ready=None,
                           note=None if row['priced'] else "Missing prices or hourly energy allocation")
                rows.append(row)
            rows=sorted(rows,key=lambda x:(x['date'],x['id']))[-15:]
            return {"schemaVersion":1,"kind":"recorded","benchmark":"fixed_supply_standard_delivery",
                    "costScope":"electricity_supply_distribution_estimate","generatedAt":iso(now),
                    "start":str(today-timedelta(days=14)),"end":str(today),"deliveryClass":DEFAULT_CLASS,
                    "fixedSupplyCents":8.413,"source":"ComEd","summary":summary(rows),"nights":rows,
                    "limitations":["Recorded virtual-car progress; supply and distribution only. Other bill charges excluded."]}
        return await asyncio.to_thread(collect)

    @property
    def virtual_car_enabled(self) -> bool:
        return self.app_test is not None and self.app_test.virtual_enabled(self.settings)

    def mode_info(self) -> dict:
        active_virtual = self.active_connection_mode == 'virtual'
        return {"vehicleAdapterAvailable": self.tesla.public_status()['configured'],
                "virtualCar": active_virtual and self.virtual_car_enabled,
                "controlOnly": active_virtual and self.app_test is not None and not self.virtual_car_enabled}

    def advance_virtual(self) -> None:
        """Call under _lock before replacing the schedule/manual control state."""
        now = self.clock().astimezone(timezone.utc)
        if self.active_connection_mode == 'virtual' and self.virtual_car_enabled and now > self._virtual_anchor:
            model = self.app_test.advance_virtual(self.settings, self._virtual_anchor, now)
            if model != self.settings["virtualCarState"]:
                updated = dict(self.settings, virtualCarState=model)
                atomic_save(self.state_dir / "settings.json", updated)
                self.settings = updated
        self._virtual_anchor = now

    def refresh_calendar_window(self):
        """Call under _lock after settling old progress, before replanning."""
        if self.active_connection_mode!='virtual' or not self.virtual_car_enabled:return
        from .sleep_calendar import apply_window
        now=self.clock().astimezone(timezone.utc)
        candidate=apply_window(self.settings,now)
        if candidate is self.settings:return
        self.history.capture(self.settings,self.pricing,now=now,force=True)
        atomic_save(self.state_dir/"settings.json",candidate);self.settings=candidate

    def refresh_smart_plan(self, *, force=False, cause="fiveMinuteReplan", settings=None) -> None:
        """Called under _lock, after settling progress using the previous stored plan."""
        candidate = self.settings if settings is None else settings
        if self.active_connection_mode != 'virtual' or self.pricing is None or not self.virtual_car_enabled or not smart_plan_enabled(candidate):
            return
        from .smart_charging import make_plan
        config = candidate["overnightRun"]
        now = self.clock().astimezone(timezone.utc)
        deadline = datetime.fromisoformat(config["departure"])
        if now >= deadline:
            if settings is not None:
                atomic_save(self.state_dir / "settings.json", candidate)
                self.settings = candidate
            return
        signature = [config["start"], config["departure"], config["targetBatteryPercent"], config["initialBatteryPercent"]]
        old = candidate.get("smartChargePlan") or {}
        if not force and old.get("decisionSlot") == int(now.timestamp()) // 300 and old.get("goalSignature") == signature:
            return
        # A supplied fit is trained exclusively on historical prices; it is never a future observation.
        decay_path = self.state_dir / "live-decay.json"
        decay = None
        if decay_path.exists():
            raw_decay = decay_path.read_bytes()
            saved_decay = json.loads(raw_decay)
            decay = {key: saved_decay[key] for key in ("decay", "sourceCommit", "calibrateDays", "trainingStartInclusive", "trainingEndExclusive", "observations") if key in saved_decay}
            decay["artifactSha256"] = hashlib.sha256(raw_decay).hexdigest()
        previous_charging = self.app_test.state_at(self.settings, now)[0] == "charging"
        from .billing import delivery_hours
        delivery = delivery_hours(now,deadline)
        plan = make_plan(now, datetime.fromisoformat(config["start"]), deadline,
                         candidate["virtualCarState"]["batteryPercent"], config["targetBatteryPercent"],
                         self.pricing.day_ahead, self.pricing.samples,
                         previous_charging=previous_charging, mode="live", decay=decay,
                         delivery_cents=delivery, reserve_seconds=int(config.get("readyBufferMinutes",0))*60)
        plan.update(decisionSlot=int(now.timestamp()) // 300, goalSignature=signature, cause=cause)
        updated = dict(candidate, smartChargePlan=plan)
        atomic_save(self.state_dir / "settings.json", updated)
        self.settings = updated
        price_log = os.environ.get("SPARC_PRICE_LOG")
        if price_log:
            path = Path(price_log).with_name("decisions.jsonl")
            try:
                with path.open("a") as stream:
                    stream.write(json.dumps({"recordedAt": now.isoformat(), "batteryPercent": updated["virtualCarState"]["batteryPercent"],
                                             "manual": self.app_test.control_state(updated)[0], "plan": plan}, allow_nan=False) + "\n")
                    stream.flush()
                    os.fsync(stream.fileno())
            except OSError:
                print("SPARC optimizer decision committed; audit log write failed.", flush=True)

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
        if self.active_connection_mode == 'tesla':
            snapshot = self.tesla_snapshot()
            battery, per_hour, manual = snapshot['batteryPercent'], 0.0, None
        try:
            trip = validate_today(settings.get("today"), now=now)
        except ValueError:
            trip = None
        local = now.astimezone(CHICAGO)
        contact = self.last_contact
        if snapshot is not None: snapshot["control"]=control_state(settings)
        from .sleep_calendar import public as calendar_public
        return {"sleepCalendar":calendar_public(self.settings,now),"snapshot": snapshot, "batteryPercent": battery, "batteryPercentPerHour": per_hour,
                "manual": manual, "weekly": settings["weekly"], "trip": trip,
                "overnight": current_config(settings),
                "campaign": campaign_view(settings, now),
                "developer": developer_view(settings,now) if self.app_test is not None else None,
                "control":control_state(settings), "clients":self.clients.summary(),
                "connections":self.connections_view(),
                "optimizer": settings.get("smartChargePlan"),
                "local": {"date": local.date().isoformat(), "minutes": local.hour * 60 + local.minute},
                "controller": {"id": self.info.device_id[:4].upper(), "bluetooth": self.ble_advertising,
                               "uptimeSeconds": int(time.monotonic() - self.started),
                               "phoneSeconds": None if contact is None else int(time.monotonic() - contact[1]),
                               "phoneTransport": None if contact is None else contact[0],
                               "wifi": self.wifi_view()}}

    @property
    def setup_open(self) -> bool:
        return time.monotonic() < self.setup_deadline

    @property
    def wifi_setup_open(self) -> bool:
        """New Wi-Fi is accepted while the Pi is offline, or in the setup window after it starts."""
        return self.setup_open or (self.wifi_state is not None and self.wifi_state["connected"] is False)

    async def refresh_wifi(self) -> None:
        if self.wifi is None:
            return
        try:
            self.wifi_state = await asyncio.wait_for(self.wifi.status(), timeout=10)
        except Exception:
            # An unreadable network state never opens Wi-Fi setup by itself.
            self.wifi_state = None

    def wifi_view(self) -> dict | None:
        if self.wifi is None:
            return None
        state = self.wifi_state or {}
        return {"connected": state.get("connected") is not False, "ssid": state.get("ssid"),
                "canSetUp": self.wifi_setup_open}

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
            client=clean_client(request.get("client"))
            self.clients.touch(client,transport,paired=request["operation"]=="pair")
            result = await self.dispatch(request["operation"], request["body"], transport,client=client)
            response = {"id": message_id, "ok": True, "body": result}
        except (ValueError, WiFiError) as exc:
            response = {"id": message_id, "ok": False, "error": {"code": "invalid_request", "message": str(exc)}}
        except Exception:
            response = {"id": message_id, "ok": False, "error": {"code": "unavailable", "message": "The controller could not complete this request. Retry or inspect the controller service."}}
        return self.channel.seal(response, "d2c")

    async def dispatch(self, operation: str, body: dict, transport: str, *, client=None) -> dict:
        if operation in ('set_connection_settings', 'check_smart_plug', 'set_smart_plug_power',
                         'tesla_authorize', 'tesla_complete_authorization', 'tesla_refresh',
                         'tesla_select_vehicle', 'tesla_disconnect','activate_connection','tesla_command'):
            async with self._connection_lock:
                return await self.connection_dispatch(operation, body, transport, client)
        if operation == "set_sleep_windows":
            from .sleep_calendar import updated, public
            if transport not in ('ble','lan') or client is None:raise ValueError("Sync Calendar from your paired phone.")
            if self.active_connection_mode!='virtual' or not self.virtual_car_enabled:raise ValueError("Apple Calendar schedules currently control the virtual car only.")
            async with self._lock:
                self.advance_virtual()
                now=self.clock().astimezone(timezone.utc)
                candidate=updated(self.settings,body,now,client.get('id'))
                self.history.capture(self.settings,self.pricing,now=now,force=True)
                candidate=self.command_candidate(candidate,operation,transport,client)
                atomic_save(self.state_dir/"settings.json",candidate);self.settings=candidate
                self.refresh_calendar_window()
                self.refresh_smart_plan(force=True,cause="appleCalendarChanged")
                return {"storedOnly":True,"sleepCalendar":public(self.settings,now),**self.shared_state(client)}
        if operation == "get_savings":
            return await self.savings_view(body)
        if operation == "get_rates":
            if not isinstance(body,dict) or set(body)-{"day"}:
                raise ValueError("Choose a day without extra rate-history fields.")
            if self.pricing is None:
                raise ValueError("ComEd rate history is unavailable on this controller.")
            return self.pricing.rate_history(body.get("day"))
        if operation == "ping":
            return {"connected": True, "deviceId": self.info.device_id, **self.mode_info()}
        if operation == "pair":
            # handle() has already authenticated this request and consumed its replay ID.
            # Discovery alone is untrusted; the phone must authenticate this sealed reply.
            # Anyone who knows a shared pairing code can already send commands, so a window adds nothing.
            if transport not in ("ble", "lan") or not (self.setup_open or self.info.pairing_code):
                raise ValueError("Open the controller’s local setup window, then pair again.")
            return {"deviceId": self.info.device_id, "host": local_hostname(), **self.mode_info(),
                    "clientRegistered":client is not None,"clientId":client.get("id") if client else None,
                    **self.shared_state(client)}
        if operation == "status":
            await self.refresh_wifi()
            return {"deviceId": self.info.device_id, "uptimeSeconds": int(time.monotonic() - self.started),
                    "temperatureC": pi_temperature(), "bluetooth": self.ble_advertising,
                    "wifi": self.wifi_view(), **self.mode_info()}
        if operation in ("wifi.scan", "wifi.provision"):
            if not self.allow_wifi_provision:
                raise ValueError("Wi-Fi setup is off on this controller. It keeps its existing network connection.")
            if transport != "ble":
                raise ValueError("Set up Wi-Fi over Bluetooth with your Pi nearby.")
            await self.refresh_wifi()
            if not self.wifi_setup_open:
                raise ValueError("Your Pi is already on Wi-Fi. To switch networks, restart the Pi and set Wi-Fi within 10 minutes.")
            if operation == "wifi.scan":
                return {"networks": await asyncio.wait_for(self.wifi.scan(), timeout=20)}
            if self.busy_wifi:
                raise ValueError("Wi-Fi setup is already running. Wait for it to finish.")
            self.busy_wifi = True
            try:
                result = await asyncio.wait_for(self.wifi.connect(body.get("ssid"), body.get("password")), timeout=65)
            finally:
                self.busy_wifi = False
            await self.refresh_wifi()
            return result
        if operation == "get_settings":
            async with self._lock:
                self.advance_virtual()
                self.expire_today()
                result = {"weekly": self.settings["weekly"], "today": self.settings.get("today")}
                if self.app_test:
                    result["virtualCarEnabled"] = self.virtual_car_enabled
                if self.settings.get("campaign") is not None:
                    result["campaign"] = campaign_view(self.settings, self.clock())
                if self.app_test is not None or client is not None: result.update(self.shared_state(client))
                return result
        if operation == "get_history":
            return await self.history_view(body)
        if operation == "set_campaign":
            if (self.settings.get("sleepCalendar") or {}).get("enabled"):
                raise ValueError("Disconnect Apple sleep calendar before editing the nightly routine.")
            if self.active_connection_mode != 'virtual' or self.app_test is None or not self.virtual_car_enabled:
                raise ValueError("Nightly simulation routines require the virtual car.")
            async with self._lock:
                self.advance_virtual()
                candidate = update_campaign(self.settings, body, self.clock(),last_writer_wins=True)
                candidate = self.command_candidate(candidate,operation,transport,client)
                if candidate != self.settings:
                    if self.pricing is not None and smart_plan_enabled(candidate):
                        self.refresh_smart_plan(force=True, cause="routineSettingsChanged", settings=candidate)
                    else:
                        atomic_save(self.state_dir / "settings.json", candidate)
                        self.settings = candidate
                return {"campaign": campaign_view(self.settings, self.clock()), "storedOnly": True,**self.shared_state(client)}
        if operation == "set_virtual_car_state":
            if self.active_connection_mode != 'virtual' or self.app_test is None or not self.virtual_car_enabled:
                raise ValueError("Developer car controls require the virtual car.")
            async with self._lock:
                self.advance_virtual()
                candidate=developer_updated(self.settings,body,self.clock())
                # Archive the observed session before a manual SOC calibration.
                settings,pricing,now=self.history_inputs()
                await asyncio.to_thread(self.history.capture,settings,pricing,now=now,force=True)
                candidate=self.command_candidate(candidate,operation,transport,client)
                if self.pricing is not None and smart_plan_enabled(candidate):
                    self.refresh_smart_plan(force=True,cause="virtualCarCalibration",settings=candidate)
                else:
                    atomic_save(self.state_dir/"settings.json",candidate)
                    self.settings=candidate
                return {"storedOnly":True,**self.shared_state(client)}
        if operation == "set_virtual_car":
            if self.active_connection_mode != 'virtual' or self.app_test is None:
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
                    updated=self.command_candidate(updated,operation,transport,client)
                    atomic_save(self.state_dir / "settings.json", updated)
                    self.settings = updated
                else:
                    updated=self.command_candidate(self.settings,operation,transport,client)
                    atomic_save(self.state_dir/"settings.json",updated);self.settings=updated
                return {"virtualCarEnabled": self.virtual_car_enabled, **self.mode_info(),**self.shared_state(client)}
        if operation == "set_overnight":
            if self.active_connection_mode != 'virtual' or self.app_test is None or not self.virtual_car_enabled:
                raise ValueError("Overnight test controls require the virtual car.")
            async with self._lock:
                self.advance_virtual()
                updated = updated_settings(self.settings, body, self.clock())
                if updated.get("campaign"):
                    updated["campaign"] = dict(updated["campaign"], revision=updated["campaign"]["revision"]+1)
                updated=self.command_candidate(updated,operation,transport,client)
                if self.pricing is not None and smart_plan_enabled(updated):
                    self.refresh_smart_plan(force=True, cause="screenSettingsChanged", settings=updated)
                else:
                    atomic_save(self.state_dir / "settings.json", updated)
                    self.settings = updated
                return {"storedOnly": True, **self.mode_info(),**self.shared_state(client)}
        if operation in ("set_weekly", "set_today"):
            if (self.settings.get("sleepCalendar") or {}).get("enabled"):
                raise ValueError("Disconnect Apple sleep calendar before editing this schedule.")
            if self.active_connection_mode == 'tesla':
                raise ValueError('Tesla currently supports manual charging. Switch to Virtual to edit a simulated schedule.')
            if self.settings.get("campaign"):
                raise ValueError("This Pi uses a Nightly routine. Edit that routine to change its charging schedule.")
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
                updated=self.command_candidate(updated,operation,transport,client)
                atomic_save(self.state_dir / "settings.json", updated)
                self.settings = updated
                return {"storedOnly": True, **self.mode_info(),**self.shared_state(client)}
        if operation in ("get_snapshot", "charge_now", "pause", "resume", "end"):
            if self.active_connection_mode == 'tesla':
                if not isinstance(body,dict) or body:
                    raise ValueError('Send this charging action without extra fields.')
                if operation == 'get_snapshot':
                    return self.tesla_snapshot()
                async with self._connection_lock:
                    name = 'charge_start' if operation in ('charge_now','resume') else 'charge_stop'
                    return await self.tesla_command({'command':name},transport,client,operation)
            if self.app_test is None:
                raise ValueError("Your Pi is connected. Install the vehicle adapter before live charging data and controls become available.")
            async with self._lock:
                if self.active_connection_mode != 'virtual':
                    raise ValueError('The active connection changed. Refresh before sending another charging action.')
                self.advance_virtual()
                self.expire_today()
                if operation != "get_snapshot":
                    if not self.virtual_car_enabled:
                        raise ValueError("Virtual car is off. Turn it on before using test charging controls.")
                    updated = dict(self.settings)
                    updated["appTestControl"] = self.app_test.command_value(operation, updated)
                    updated=self.command_candidate(updated,operation,transport,client)
                    if self.pricing is not None and smart_plan_enabled(updated):
                        self.refresh_smart_plan(force=True, cause="manualCommand:" + operation, settings=updated)
                    else:
                        atomic_save(self.state_dir / "settings.json", updated)
                        self.settings = updated
                return dict(self.app_test.snapshot(self.settings),control=control_state(self.settings))
        raise ValueError("This controller does not support that operation. Update the app and controller together.")
