"""Tesla charging sessions that SPARC started, priced with real ComEd supply data.

Cost: Tesla bills every Fleet API ``vehicle_data`` response below HTTP 500,
including the 408 an asleep car returns, and frequent polling can keep a parked
car awake. A session therefore opens only when SPARC's own ``charge_start`` is
accepted. Its readings are the reads SPARC already makes around Start/Stop and
explicit refreshes, plus one ``charge_state`` read every ``POLL_SECONDS`` while
that session is open. A charging car is awake anyway; polling stops at the first
reading that shows charging ended, after ``MAX_FAILURES`` failed reads in a row,
or after ``MAX_POLLS`` reads. Nothing here wakes the car.

Energy is the car's own ``charge_energy_added`` counter: energy added to the
battery. The wall supplies more because of charging losses, so the supply cost
is a lower bound. A missing counter stays unavailable; nothing is simulated.
"""
from __future__ import annotations

from datetime import timedelta
from pathlib import Path
import re
import threading
import uuid

from .history import CHICAGO, clean_buckets, cost_for, immediate_buckets, instant, iso, number, read_json, save_json

POLL_SECONDS = 15 * 60
MAX_FAILURES = 3
MAX_WAITING = 2
MAX_POLLS = 96
MAX_READINGS = 200
MAX_FILES = 4096
RESET_KWH = 0.05
START_GRACE_SECONDS = 120
REPRICE_DAYS = 8
STATES = ("charging", "complete", "paused", "unplugged", "waiting")
END_REASONS = ("limit_reached", "stopped", "unplugged", "no_power", "not_observed")
VEHICLE_ID = re.compile(r"[0-9]{1,32}\Z")


def valid_vehicle_id(value):
    return isinstance(value, str) and VEHICLE_ID.fullmatch(value) is not None


def clean_reading(charge, now):
    """A measured reading from TeslaAdapter.charge_state(), or None."""
    if not isinstance(charge, dict) or charge.get("state") not in STATES:
        return None
    battery = number(charge.get("batteryPercent"), nonnegative=True)
    power = number(charge.get("powerKW"), nonnegative=True)
    try:
        at = instant(charge["updatedAt"])
    except (KeyError, ValueError, TypeError, OverflowError):
        return None
    if battery is None or battery > 100 or power is None or at > instant(now) + timedelta(minutes=5):
        return None
    added = number(charge.get("energyAddedKWh"), nonnegative=True)
    limit = charge.get("targetPercent")
    return {"at": iso(at), "state": charge["state"], "batteryPercent": battery, "powerKW": power,
            "energyAddedKWh": added if added is not None and added <= 500 else None,
            "targetPercent": float(limit) if type(limit) is int and 50 <= limit <= 100 else None}


def measured_energy(readings):
    """Sum the car's counter increases and place each one in UTC hours.

    A drop larger than RESET_KWH means the car began a new counter; only the new
    value is added. A smaller drop is rounding and is ignored. This never counts
    more than the car reported. Each increase is placed from the earlier reading
    at the car's reported charging power (like the immediate baseline), or spread
    evenly when that power could not deliver it in time. Returns (None, {}) when
    fewer than two readings carry the counter.
    """
    total, buckets, previous, seen = 0.0, {}, None, 0
    for reading in readings:
        value = reading.get("energyAddedKWh")
        if value is None:
            continue
        seen += 1
        if previous is None:
            previous = reading
            continue
        before = previous["energyAddedKWh"]
        if value >= before:
            delta = value - before
        elif value < before - RESET_KWH:
            delta = value
        else:
            continue
        if delta > 1e-9:
            start, end = instant(previous["at"]), instant(reading["at"])
            seconds = (end - start).total_seconds()
            rate = next((item["powerKW"] for item in (previous, reading)
                         if item["state"] == "charging" and item["powerKW"] > 0), 0.0)
            placed = immediate_buckets(start, delta, max(rate, delta * 3600 / seconds)) if seconds > 0 else {}
            if not placed:
                buckets = None
            elif buckets is not None:
                for hour, energy in placed.items():
                    buckets[hour] = buckets.get(hour, 0.0) + energy
            total += delta
        previous = reading
    if seen < 2:
        return None, {}
    return total, buckets or {}


class TeslaSessionStore:
    """One open session at a time; records live in state/history/tesla/<id>.json."""

    def __init__(self, state_dir, clock):
        self.directory = Path(state_dir) / "history" / "tesla"
        self.clock = clock
        self._lock = threading.RLock()
        self._open = None
        self._loaded = False

    # Storage -----------------------------------------------------------------
    def _records(self):
        result = {}
        if not self.directory.is_dir():
            return result
        for path in list(self.directory.glob("*.json"))[:MAX_FILES]:
            record = read_json(path)
            try:
                if (not record or record.get("id") != path.stem or str(uuid.UUID(record["id"])) != record["id"]
                        or record.get("car", {}).get("kind") != "tesla" or not valid_vehicle_id(record["car"].get("vehicleId"))):
                    continue
                instant(record["start"])
                if record.get("end") is not None:
                    instant(record["end"])
                result[record["id"]] = record
            except (ValueError, KeyError, TypeError, AttributeError, OverflowError):
                continue
        return result

    def _save(self, record):
        save_json(self.directory / (record["id"] + ".json"), record)

    def _current(self):
        if not self._loaded:
            # Restart: resume the newest open session; older ones were never closed.
            opened = sorted((record for record in self._records().values() if record.get("end") is None),
                            key=lambda record: instant(record["start"]))
            self._loaded = True
            for stale in opened[:-1]:
                self._close(stale, "not_observed", self._last_at(stale), partial=True)
            self._open = opened[-1] if opened else None
        return self._open

    # Derived values ----------------------------------------------------------
    @staticmethod
    def _last_at(record):
        readings = record.get("readings") or []
        return readings[-1]["at"] if readings else record["start"]

    @staticmethod
    def _derive(record):
        readings = record.get("readings") or []
        energy, buckets = measured_energy(readings)
        record["energyKWh"], record["energyByHour"] = energy, buckets
        if readings:
            record["batteryPercent"] = readings[-1]["batteryPercent"]
            record["lastObservedAt"] = readings[-1]["at"]
        target = next((item["targetPercent"] for item in reversed(readings) if item.get("targetPercent") is not None), None)
        if target is not None:
            record["targetBatteryPercent"] = target

    @staticmethod
    def _append(record, reading):
        readings = record.setdefault("readings", [])
        # Tesla can return the same cached sample twice; an older one is stale.
        if reading is None or (readings and instant(reading["at"]) <= instant(readings[-1]["at"])):
            return False
        readings.append(reading)
        del readings[:-MAX_READINGS]
        return True

    def _accounting(self, record, pricing):
        energy = record.get("energyKWh")
        if energy is None:
            record["actualAccounting"] = None
            return
        record["actualAccounting"] = cost_for(pricing, clean_buckets(record.get("energyByHour")), energy,
                                              record.get("actualAccounting"))

    def _close(self, record, reason, at, *, partial=False, pricing=None):
        record.update(end=iso(at), endReason=reason, partial=bool(partial or record.get("partial")))
        self._derive(record)
        self._accounting(record, pricing)
        self._save(record)
        if self._open is not None and self._open["id"] == record["id"]:
            self._open = None

    # Session events ----------------------------------------------------------
    def open_session(self, vehicle, before, after, now, pricing=None):
        """SPARC's charge_start was accepted. before/after are the reads around it."""
        now = instant(now)
        with self._lock:
            if not isinstance(vehicle, dict) or not valid_vehicle_id(vehicle.get("id")):
                return None
            first, second = clean_reading(before, now), clean_reading(after, now)
            current = self._current()
            if current is not None and current["car"]["vehicleId"] == vehicle["id"]:
                # Charge now while SPARC's session is still open continues it.
                self._append(current, first)
                self._append(current, second)
                current.update(lastAttemptAt=iso(now), failedReads=0)
                self._derive(current)
                self._accounting(current, pricing)
                self._save(current)
                return current["id"]
            if current is not None:
                self._close(current, "not_observed", self._last_at(current), partial=True, pricing=pricing)
            if first is None:
                return None
            name = vehicle.get("name") if isinstance(vehicle.get("name"), str) else None
            record = {"schemaVersion": 1, "id": str(uuid.uuid4()),
                      "car": {"kind": "tesla", "vehicleId": vehicle["id"]},
                      "vehicleName": name[:128] if name else None,
                      "date": iso(now), "start": iso(now), "nightDate": now.astimezone(CHICAGO).date().isoformat(),
                      "initialBatteryPercent": first["batteryPercent"], "targetBatteryPercent": None,
                      "batteryPercent": first["batteryPercent"], "readings": [], "energyKWh": None, "energyByHour": {},
                      "lastAttemptAt": iso(now), "failedReads": 0, "waitingReads": 0, "polls": 0,
                      "end": None, "endReason": None, "partial": False, "actualAccounting": None}
            self._append(record, first)
            self._append(record, second)
            self._derive(record)
            self._accounting(record, pricing)
            self._save(record)
            self._open = record
            return record["id"]

    def observe(self, vehicle_id, charge, now, *, polled=False, pricing=None):
        """Any successful read of the selected car. Never opens a session."""
        now = instant(now)
        with self._lock:
            record = self._current()
            if record is None or record["car"]["vehicleId"] != vehicle_id:
                return None
            reading = clean_reading(charge, now)
            record.update(lastAttemptAt=iso(now), failedReads=0)
            if polled:
                record["polls"] = record.get("polls", 0) + 1
            added = self._append(record, reading)
            reason = None
            if added and instant(reading["at"]) >= instant(record["start"]) + timedelta(seconds=START_GRACE_SECONDS):
                state = reading["state"]
                reason = {"complete": "limit_reached", "paused": "stopped", "unplugged": "unplugged"}.get(state)
                record["waitingReads"] = record.get("waitingReads", 0) + 1 if state == "waiting" else 0
                if reason is None and record["waitingReads"] >= MAX_WAITING:
                    reason = "no_power"
            if reason:
                self._close(record, reason, reading["at"], pricing=pricing)
            elif record.get("polls", 0) >= MAX_POLLS:
                self._close(record, "not_observed", self._last_at(record), partial=True, pricing=pricing)
            else:
                self._derive(record)
                self._accounting(record, pricing)
                self._save(record)
            return record["id"]

    def stopped(self, vehicle_id, charge, now, pricing=None):
        """SPARC's charge_stop was accepted; charge is the read after it, if any."""
        now = instant(now)
        with self._lock:
            record = self._current()
            if record is None or record["car"]["vehicleId"] != vehicle_id:
                return None
            reading = clean_reading(charge, now)
            self._append(record, reading)
            self._close(record, "stopped", now, partial=reading is None, pricing=pricing)
            return record["id"]

    def read_failed(self, vehicle_id, now, pricing=None):
        now = instant(now)
        with self._lock:
            record = self._current()
            if record is None or record["car"]["vehicleId"] != vehicle_id:
                return None
            record.update(lastAttemptAt=iso(now), failedReads=record.get("failedReads", 0) + 1)
            if record["failedReads"] >= MAX_FAILURES:
                self._close(record, "not_observed", self._last_at(record), partial=True, pricing=pricing)
            else:
                self._save(record)
            return record["id"]

    def abandon(self, now, keep_vehicle=None, pricing=None):
        """Tesla removed or another car selected: stop reading the old one."""
        with self._lock:
            record = self._current()
            if record is None or record["car"]["vehicleId"] == keep_vehicle:
                return None
            self._close(record, "not_observed", self._last_at(record), partial=True, pricing=pricing)
            return record["id"]

    def poll_due(self, now):
        """The open session's vehicle id when its next read is due, else None."""
        with self._lock:
            record = self._current()
            if record is None:
                return None
            try:
                waited = (instant(now) - instant(record["lastAttemptAt"])).total_seconds()
            except (KeyError, ValueError, TypeError):
                waited = POLL_SECONDS
            return record["car"]["vehicleId"] if waited >= POLL_SECONDS else None

    # Views -------------------------------------------------------------------
    def capture(self, pricing, now):
        """Settle prices for recent sessions; ComEd keeps about seven days of hours."""
        now = instant(now)
        changed = 0
        with self._lock:
            current = self._current()
            for record in self._records().values():
                if current is not None and record["id"] == current["id"]:
                    record = current
                accounting = record.get("actualAccounting") or {}
                if (record.get("energyKWh") is None or accounting.get("final")
                        or instant(record["start"]) < now - timedelta(days=REPRICE_DAYS)):
                    continue
                before = dict(accounting)
                self._accounting(record, pricing)
                if record["actualAccounting"] != before:
                    self._save(record)
                    changed += 1
        return changed

    def sessions(self, pricing, now, vehicle_id=None):
        now = instant(now)
        with self._lock:
            self.capture(pricing, now)
            current = self._current()
            records = [current if current is not None and record["id"] == current["id"] else record
                       for record in self._records().values()
                       if vehicle_id is None or record["car"]["vehicleId"] == vehicle_id]
            rows = [self._public(record) for record in records]
        return sorted(rows, key=lambda item: (item["start"], item["id"]), reverse=True)

    @staticmethod
    def _public(record):
        energy = number(record.get("energyKWh"), nonnegative=True)
        accounting = record.get("actualAccounting") or {}
        cost = number(accounting.get("cost")) if energy is not None else None
        final = cost is not None and accounting.get("final") is True
        closed = record.get("end") is not None
        unpriced = None
        if energy is not None:
            unpriced = number(accounting.get("unpricedEnergyKWh"), nonnegative=True)
            unpriced = energy if unpriced is None else unpriced
        reason = record.get("endReason") if record.get("endReason") in END_REASONS else None
        public = {"id": record["id"], "car": {"kind": "tesla", "vehicleId": record["car"]["vehicleId"]},
                  "vehicleName": record.get("vehicleName"), "date": record["start"], "nightDate": record["nightDate"],
                  "start": record["start"], "departure": None, "end": record.get("end"),
                  "endReason": reason if closed else None, "lastObservedAt": record.get("lastObservedAt"),
                  "status": "active" if not closed else "complete" if reason == "limit_reached" else "incomplete",
                  "initialBatteryPercent": number(record.get("initialBatteryPercent"), nonnegative=True),
                  "targetBatteryPercent": number(record.get("targetBatteryPercent"), nonnegative=True),
                  "batteryPercent": number(record.get("batteryPercent"), nonnegative=True),
                  "energyKWh": energy, "energySource": "vehicle_reported", "energyPartial": record.get("partial") is True,
                  "simulated": False, "source": "ComEd", "costUSD": cost, "baselineCostUSD": None,
                  "savingsUSD": None, "savingsPercent": None, "costFinal": final, "baselineFinal": None,
                  "provisional": not closed or (energy is not None and not final),
                  "unpricedEnergyKWh": unpriced, "targetReachedByDeadline": None}
        return {key: round(value, 6) if type(value) is float else value for key, value in public.items()}
