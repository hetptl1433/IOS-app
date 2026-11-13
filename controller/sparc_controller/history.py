"""Durable real-price accounting for the simulated car and existing Pi runs.

Amounts cover ComEd electricity supply only. The comparison charges immediately
from the configured start at the same power and for the same measured grid kWh.
No example price or extrapolated driving/charging session enters this ledger.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import math
import os
from pathlib import Path
import re
import threading
import uuid
from zoneinfo import ZoneInfo

from .app_test import VIRTUAL_POWER_KW

UTC = timezone.utc
CHICAGO = ZoneInfo("America/Chicago")
MAX_JSON_BYTES = 1024 * 1024
TAIL_BYTES = 512 * 1024
MAX_SESSIONS = 2048
NIGHT = re.compile(r"(?:overnight|night)-(\d{4}-\d{2}-\d{2})\Z")


def instant(value):
    if isinstance(value, datetime):
        result = value
    elif isinstance(value, str):
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    else:
        raise ValueError("An aware timestamp is required.")
    if result.tzinfo is None:
        raise ValueError("An aware timestamp is required.")
    return result.astimezone(UTC)


def iso(value):
    return instant(value).isoformat().replace("+00:00", "Z")


def number(value, *, nonnegative=False):
    if type(value) not in (int, float) or not math.isfinite(value) or (nonnegative and value < 0):
        return None
    return float(value)


def read_json(path, maximum=MAX_JSON_BYTES):
    try:
        with Path(path).open("rb") as stream:
            raw = stream.read(maximum + 1)
        if len(raw) > maximum:
            return None
        value = json.loads(raw)
        return value if isinstance(value, dict) else None
    except (OSError, ValueError):
        return None


def save_json(path, value):
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    temporary = path.with_name("." + path.name + "." + uuid.uuid4().hex + ".tmp")
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "w") as stream:
            json.dump(value, stream, allow_nan=False, separators=(",", ":"))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        temporary.unlink(missing_ok=True)


def session_id(config, night_date=None):
    campaign, session = config.get("campaignId"), config.get("sessionId")
    if isinstance(campaign, str) and campaign and isinstance(session, str) and session:
        key = "campaign:" + campaign + ":" + session
    else:
        key = "overnight:" + (night_date or instant(config["start"]).astimezone(CHICAGO).date().isoformat())
    return str(uuid.uuid5(uuid.NAMESPACE_URL, "sparc:history:" + key))


def clean_buckets(value):
    if not isinstance(value, dict) or len(value) > 5000:
        return {}
    result = {}
    for raw_hour, raw_energy in value.items():
        energy = number(raw_energy, nonnegative=True)
        try:
            hour = instant(raw_hour)
        except (ValueError, TypeError, OverflowError):
            continue
        if energy is not None and not (hour.minute or hour.second or hour.microsecond):
            key = hour.isoformat()
            result[key] = result.get(key, 0.0) + energy
    return result


def immediate_buckets(start, energy, power=VIRTUAL_POWER_KW):
    """Allocate measured grid energy from start; UTC boundaries handle DST."""
    cursor, remaining, result = instant(start), energy, {}
    if number(power, nonnegative=True) in (None, 0) or energy > power * 24 * 7:
        return {}
    while remaining > 1e-9:
        hour = cursor.replace(minute=0, second=0, microsecond=0)
        boundary = hour + timedelta(hours=1)
        delivered = min(remaining, power * (boundary-cursor).total_seconds()/3600)
        result[hour.isoformat()] = result.get(hour.isoformat(), 0.0) + delivered
        remaining -= delivered
        cursor = boundary
    return result


def cost_for(pricing, buckets, energy, previous=None):
    """Retain known accounting when ComEd's rolling cache has expired."""
    previous = previous if isinstance(previous, dict) else {}
    same_energy = abs((number(previous.get("energyKWh"), nonnegative=True) or 0)-energy) < 1e-7
    saved = previous if same_energy and number(previous.get("cost")) is not None else None
    if energy < 1e-9:
        return {"energyKWh": energy, "cost": 0.0, "final": True, "unpricedEnergyKWh": 0.0}
    if pricing is not None and abs(sum(buckets.values())-energy) < 1e-7:
        try:
            priced = pricing.energy_cost(buckets)
            missing = number(priced.get("unpricedEnergyKWh"), nonnegative=True)
            cost = number(priced.get("cost"))
            if missing is not None and missing < 1e-8 and cost is not None:
                candidate = {"energyKWh": energy, "cost": cost, "final": priced.get("final") is True,
                             "unpricedEnergyKWh": 0.0}
                # Never downgrade settled historical accounting to a partial-hour mean.
                return saved if saved and saved.get("final") and not candidate["final"] else candidate
        except (ValueError, TypeError, KeyError, OverflowError):
            pass
    return saved or {"energyKWh": energy, "cost": None, "final": False, "unpricedEnergyKWh": energy}


def _priced(session):
    """True priced, False missing energy or prices, None for an empty session.

    The virtual car needs its immediate baseline too; a manual Tesla charge has
    no baseline, so its supply cost alone prices it.
    """
    energy = session["energyKWh"]
    if energy is None:
        return False
    if energy <= 1e-9:
        return None
    needed = [session["costUSD"]]
    if session["car"]["kind"] == "virtual":
        needed.append(session["baselineCostUSD"])
    return all(value is not None for value in needed)


def _totals(sessions):
    result = {"energyKWh": round(sum(item["energyKWh"] or 0 for item in sessions), 6)}
    for key in ("costUSD", "baselineCostUSD", "savingsUSD"):
        result[key] = None if any(item[key] is None for item in sessions) else round(sum(item[key] for item in sessions), 6)
    return result


def history_page(sessions, now, cursor, limit, car):
    """One car's Chicago week and a page of its sessions, newest first."""
    local_day = now.astimezone(CHICAGO).date()
    beginning = local_day-timedelta(days=local_day.weekday())
    end = beginning+timedelta(days=7)
    days = []
    weekly = [session for session in sessions if beginning <= instant(session["start"]).astimezone(CHICAGO).date() < end]
    for offset in range(7):
        day = beginning+timedelta(days=offset)
        chosen = [session for session in weekly if instant(session["start"]).astimezone(CHICAGO).date() == day]
        days.append({"date": day.isoformat(), **_totals(chosen), "sessionCount": len(chosen)})
    priced = sum(_priced(session) is True for session in weekly)
    unpriced = sum(_priced(session) is False for session in weekly)
    totals = _totals(weekly)
    baseline, savings = totals["baselineCostUSD"], totals["savingsUSD"]
    week = {"start": beginning.isoformat(), "end": end.isoformat(), **totals,
            "savingsPercent": round(100*savings/abs(baseline), 6) if savings is not None and abs(baseline) > 1e-9 else None,
            "pricedSessions": priced, "unpricedSessions": unpriced,
            "provisional": any(session["provisional"] for session in weekly), "days": days}
    return {"schemaVersion": 1, "generatedAt": iso(now), "timezone": "America/Chicago", "costScope": "electricity_supply",
            "benchmark": "immediate_same_energy" if car["kind"] == "virtual" else "none", "car": car,
            "week": week, "sessions": sessions[cursor:cursor+limit],
            "nextCursor": cursor+limit if cursor+limit < len(sessions) else None}


class HistoryStore:
    def __init__(self, state_dir, log_root="/var/log/sparc", clock=None):
        from .tesla_history import TeslaSessionStore
        self.directory = Path(state_dir) / "history"
        self.log_root = Path(log_root)
        self.clock = clock or (lambda: datetime.now(UTC))
        self._lock = threading.RLock()
        self._last_capture = None
        self._last_capture_id = None
        self._log_cache = {}
        # Tesla sessions live in history/tesla/, apart from the virtual ledger.
        self.tesla = TeslaSessionStore(state_dir, lambda: self.clock())

    def _now(self, now):
        return instant(now if now is not None else self.clock())

    @staticmethod
    def _base(config, night_date=None):
        start, departure = instant(config["start"]), instant(config["departure"])
        if departure <= start:
            raise ValueError("Invalid historical charging window.")
        night_date = config.get("nightDate") or night_date or start.astimezone(CHICAGO).date().isoformat()
        return {"schemaVersion": 1, "id": session_id(config, night_date), "date": iso(start), "nightDate": night_date,
                "start": iso(start), "departure": iso(departure),
                "initialBatteryPercent": number(config.get("initialBatteryPercent"), nonnegative=True),
                "targetBatteryPercent": number(config.get("targetBatteryPercent"), nonnegative=True),
                "simulated": True, "source": "ComEd", "campaignId": config.get("campaignId"),
                "sessionId": config.get("sessionId"), "finalizedAt": config.get("finalizedAt")}

    def _live(self, settings, now, prior):
        config, model = settings.get("overnightRun"), settings.get("virtualCarState")
        if settings.get("virtualCarEnabled", True) is not True or not isinstance(config, dict) or not isinstance(model, dict):
            return None
        try:
            record = self._base(config)
        except (ValueError, TypeError, KeyError, OverflowError):
            return None
        energy, battery = number(model.get("energyKWh"), nonnegative=True), number(model.get("batteryPercent"), nonnegative=True)
        if energy is None or battery is None or battery > 100:
            return None
        previous = prior.get(record["id"], {})
        developer = settings.get("developerState") or {}
        adjustment = developer.get("lastAdjustment") or {}
        matching_adjustment = ((adjustment.get("campaignId"), adjustment.get("sessionId")) ==
                               (config.get("campaignId"), config.get("sessionId"))) if config.get("sessionId") else adjustment.get("start") == config.get("start")
        if config.get("finalizedAt"):
            # A completed session belongs to its archived/logged car state. The
            # developer may now calibrate the same virtual car for another test.
            if previous:
                frozen = dict(previous, finalizedAt=config["finalizedAt"])
                # A real log summary may omit its UTC buckets. Their measured
                # counter survives calibration and can still settle old prices.
                if (not frozen.get("energyByHour") and number(model.get("energyKWh"), nonnegative=True) is not None
                        and abs(model["energyKWh"]-frozen["energyKWh"]) < 1e-7):
                    frozen["energyByHour"] = clean_buckets(model.get("energyByHour"))
                return frozen
            try:
                if matching_adjustment and instant(adjustment["updatedAt"]) >= instant(config["finalizedAt"]):
                    return None
            except (KeyError, TypeError, ValueError):
                pass
        same_goal = record["targetBatteryPercent"] == previous.get("targetBatteryPercent")
        first = previous.get("firstTargetObservedAt") if same_goal else None
        below_after = previous.get("belowTargetAfterDeadlineAt") if same_goal else None
        target = record["targetBatteryPercent"]
        if target is not None and battery >= target-1e-8 and first is None:
            first = iso(now)
        elif target is not None and battery < target-1e-8 and now >= instant(record["departure"]):
            below_after = iso(now)
        if below_after and instant(below_after) < instant(record["departure"]):
            below_after = None
        record.update(energyKWh=energy, batteryPercent=battery, energyByHour=clean_buckets(model.get("energyByHour")),
                      observedAt=iso(now), firstTargetObservedAt=first, belowTargetAfterDeadlineAt=below_after)
        if matching_adjustment:
            record["manualCalibration"] = True
        return record

    @staticmethod
    def _signature(root):
        result = []
        for name in ("manifest.json", "summary.json", "snapshots.jsonl"):
            try:
                stat = (root/name).stat()
                result.append((name, stat.st_mtime_ns, stat.st_size))
            except OSError:
                result.append((name, None, None))
        return tuple(result)

    @staticmethod
    def _last_snapshot(path, config):
        try:
            with path.open("rb") as stream:
                stream.seek(0, os.SEEK_END)
                size = stream.tell()
                stream.seek(max(0, size-TAIL_BYTES))
                raw = stream.read(TAIL_BYTES)
            lines = raw.splitlines()
            if size > TAIL_BYTES:
                lines = lines[1:]
            for line in reversed(lines):
                try:
                    item = json.loads(line)
                    state = item["data"]
                    active = state.get("overnight") or {}
                    if config.get("campaignId") and (active.get("campaignId"), active.get("sessionId")) != (config.get("campaignId"), config.get("sessionId")):
                        continue
                    if isinstance(state, dict):
                        return item
                except (ValueError, KeyError, TypeError, AttributeError):
                    continue
        except OSError:
            pass
        return {}

    def _import_log(self, root):
        signature = self._signature(root)
        cached = self._log_cache.get(root)
        if cached and cached[0] == signature:
            return cached[1]
        summary = read_json(root/"summary.json") or {}
        config = read_json(root/"manifest.json") or summary.get("manifest") or {}
        if not isinstance(config, dict):
            return None
        try:
            record = self._base(config, NIGHT.fullmatch(root.name).group(1))
        except (ValueError, TypeError, KeyError, AttributeError, OverflowError):
            return None
        if config.get("campaignId") and summary.get("campaignId", config["campaignId"]) != config["campaignId"]:
            summary = {}
        if config.get("sessionId") and summary.get("sessionId", config["sessionId"]) != config["sessionId"]:
            summary = {}
        latest = summary.get("latest") or {}
        latest = latest if isinstance(latest, dict) else {}
        observation = self._last_snapshot(root/"snapshots.jsonl", config)
        state = observation.get("data") or {}
        snap = state.get("snapshot") or {}
        try:
            newer = instant(observation.get("recordedAt")) > instant(latest.get("timestamp"))
        except (ValueError, TypeError):
            newer = bool(observation) and not latest
        if newer or number(latest.get("energyKWh"), nonnegative=True) is None:
            latest = {"timestamp": observation.get("recordedAt"), "observedAt": snap.get("updatedAt"),
                      "energyKWh": snap.get("energyKWh"), "batteryPercent": state.get("batteryPercent", snap.get("batteryPercent")),
                      "energyCostUSD": snap.get("estimatedCost"), "costAvailable": snap.get("costAvailable"),
                      "costFinal": snap.get("costFinal"), "unpricedEnergyKWh": snap.get("unpricedEnergyKWh")}
        energy = number(latest.get("energyKWh"), nonnegative=True)
        if energy is None:
            # A configured future night alone is not an invented charging session.
            return None
        model = state.get("virtualCarState") or {}
        buckets = clean_buckets(model.get("energyByHour", state.get("energyByHour", {})))
        record.update(energyKWh=energy, batteryPercent=number(latest.get("batteryPercent"), nonnegative=True),
                      energyByHour=buckets, observedAt=latest.get("observedAt") or latest.get("timestamp"),
                      firstTargetObservedAt=summary.get("firstTargetObservedAt"),
                      belowTargetAfterDeadlineAt=summary.get("firstBelowTargetAfterDeadlineAt"),
                      finishedAt=summary.get("finishedAt"))
        if (latest.get("costAvailable") is True and number(latest.get("energyCostUSD")) is not None
                and (number(latest.get("unpricedEnergyKWh"), nonnegative=True) or 0) < 1e-8):
            record["actualAccounting"] = {"energyKWh": energy, "cost": float(latest["energyCostUSD"]),
                                         "final": latest.get("costFinal") is True, "unpricedEnergyKWh": 0.0}
        # Legacy summaries did not distinguish an unknown deadline from a miss.
        try:
            observed = instant(record["observedAt"])
            if (record["batteryPercent"] is not None and record["targetBatteryPercent"] is not None
                    and observed >= instant(record["departure"]) and record["batteryPercent"] < record["targetBatteryPercent"]-1e-8):
                record["belowTargetAfterDeadlineAt"] = iso(observed)
        except (ValueError, TypeError):
            pass
        self._log_cache[root] = signature, record
        return record

    def _stored(self):
        result = {}
        if self.directory.exists():
            for path in sorted(self.directory.glob("*.json"))[:MAX_SESSIONS]:
                record = read_json(path)
                if record and record.get("id") == path.stem:
                    try:
                        uuid.UUID(record["id"])
                        instant(record["start"])
                        instant(record["departure"])
                        if number(record.get("energyKWh"), nonnegative=True) is None:
                            continue
                        result[record["id"]] = record
                    except (ValueError, KeyError, TypeError):
                        pass
        return result

    def _logs(self):
        roots = list(self.log_root.glob("overnight-????-??-??"))
        roots += list(self.log_root.glob("campaign*/night-????-??-??"))
        for root in sorted(roots, reverse=True)[:MAX_SESSIONS]:
            if root.is_dir() and NIGHT.fullmatch(root.name):
                record = self._import_log(root)
                if record:
                    yield record

    @staticmethod
    def _merge(old, new):
        merged = {**old, **new}
        # A log summary may lag behind a captured settings ledger. Never roll back
        # a session's delivered energy, nor discard its durable hourly buckets.
        if old and old.get("energyKWh", 0) > new.get("energyKWh", 0)+1e-7:
            for key in ("energyKWh", "batteryPercent", "energyByHour", "observedAt"):
                merged[key] = old.get(key)
        elif not new.get("energyByHour") and old.get("energyByHour"):
            merged["energyByHour"] = old["energyByHour"]
        for key in ("firstTargetObservedAt", "belowTargetAfterDeadlineAt"):
            if old.get("targetBatteryPercent") == new.get("targetBatteryPercent") and not new.get(key):
                merged[key] = old.get(key)
        return merged

    def _collect(self, settings, pricing, now):
        stored = self._stored()
        records = {key: dict(value) for key, value in stored.items()}
        for imported in self._logs():
            key = imported["id"]
            records[key] = self._merge(records.get(key, {}), imported)
        live = self._live(settings, now, records)
        if live:
            records[live["id"]] = self._merge(records.get(live["id"], {}), live)
        for key, record in records.items():
            energy = record["energyKWh"]
            previous = stored.get(key, {})
            actual = record.get("actualAccounting") or previous.get("actualAccounting")
            settled = previous.get("actualAccounting") or {}
            if settled.get("final") and abs(settled.get("energyKWh", -1)-energy) < 1e-7:
                actual = settled
            record["actualAccounting"] = cost_for(pricing, clean_buckets(record.get("energyByHour")), energy, actual)
            benchmark = immediate_buckets(record["start"], energy)
            record["baselineAccounting"] = cost_for(pricing, benchmark, energy, previous.get("baselineAccounting"))
        return records, stored, live

    def capture(self, settings, pricing, now=None, force=False):
        """Fsync current ledger and imported accounting, normally at most every 30s.

        Call with force=True before resetting a campaign car. pricing=None saves
        measured kWh without trusting the virtual model's example cost fields.
        """
        now = self._now(now)
        with self._lock:
            config = settings.get("overnightRun") or {}
            try:
                key = session_id(config)
            except (ValueError, KeyError, TypeError):
                key = None
            if (not force and self._last_capture is not None and key == self._last_capture_id
                    and 0 <= (now-self._last_capture).total_seconds() < 30 and not config.get("finalizedAt")):
                return 0
            active_key = key
            records, stored, _ = self._collect(settings, pricing, now)
            changed = 0
            for key, record in records.items():
                if record != stored.get(key):
                    save_json(self.directory/(key+".json"), record)
                    changed += 1
            changed += self.tesla.capture(pricing, now)
            self._last_capture, self._last_capture_id = now, active_key
            return changed

    @staticmethod
    def _public(record, now):
        actual, baseline = record["actualAccounting"], record["baselineAccounting"]
        cost, reference = number(actual.get("cost")), number(baseline.get("cost"))
        savings = reference-cost if cost is not None and reference is not None else None
        first, missed, departure = record.get("firstTargetObservedAt"), record.get("belowTargetAfterDeadlineAt"), instant(record["departure"])
        try:
            reached = True if first and instant(first) <= departure else False if missed and instant(missed) >= departure else None
        except (ValueError, TypeError):
            reached = None
        closed = now >= departure or bool(record.get("finalizedAt") or record.get("finishedAt"))
        status = ("complete" if reached is True else "incomplete") if closed else "scheduled" if now < instant(record["start"]) else "active"
        public = {key: record.get(key) for key in ("id", "date", "nightDate", "start", "departure", "initialBatteryPercent",
                                                  "targetBatteryPercent", "batteryPercent", "energyKWh", "simulated", "source")}
        public.update(car={"kind": "virtual"}, status=status, costUSD=cost, baselineCostUSD=reference, savingsUSD=savings,
                      savingsPercent=100*savings/abs(reference) if savings is not None and abs(reference) > 1e-9 else None,
                      costFinal=actual.get("final") is True, baselineFinal=baseline.get("final") is True,
                      provisional=not closed or not actual.get("final") or not baseline.get("final"),
                      unpricedEnergyKWh=actual.get("unpricedEnergyKWh", record["energyKWh"]), targetReachedByDeadline=reached)
        if record.get("manualCalibration"):
            public["manualCalibration"] = True
        return {key: round(value, 6) if type(value) is float else value for key, value in public.items()}

    def view(self, settings, pricing, cursor=0, limit=8, now=None, car="virtual", vehicle_id=None):
        """One car's history. Virtual and Tesla sessions are never mixed."""
        from .tesla_history import valid_vehicle_id
        if type(cursor) is not int or cursor < 0 or type(limit) is not int or not 1 <= limit <= 8:
            raise ValueError("Choose a nonnegative history cursor and a limit from 1 to 8.")
        if car not in ("virtual", "tesla") or (vehicle_id is not None and (car != "tesla" or not valid_vehicle_id(vehicle_id))):
            raise ValueError("Choose the virtual car or a Tesla for history.")
        now = self._now(now)
        if car == "tesla":
            sessions = self.tesla.sessions(pricing, now, vehicle_id)
            identity = {"kind": "tesla", "vehicleId": vehicle_id}
        else:
            with self._lock:
                records, _, _ = self._collect(settings, pricing, now)
                sessions = sorted((self._public(record, now) for record in records.values()), key=lambda item: (item["start"], item["id"]), reverse=True)
            identity = {"kind": "virtual"}
        return history_page(sessions, now, cursor, limit, identity)
