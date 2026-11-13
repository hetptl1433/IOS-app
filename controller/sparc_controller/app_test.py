"""Software control state for exercising the physical app link, never a vehicle."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
import math
from typing import Callable
from zoneinfo import ZoneInfo

CHICAGO = ZoneInfo("America/Chicago")
UTC = timezone.utc
VIRTUAL_CAPACITY_KWH = 100.0
VIRTUAL_POWER_KW = 11.5
VIRTUAL_EFFICIENCY = 0.9


def utc_string(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def local_instant(day: date, minutes: int) -> datetime:
    """Match the app: first repeated instant, or next valid minute in a DST gap."""
    wall = datetime.combine(day, time(minutes // 60, minutes % 60))
    for _ in range(181):
        candidate = wall.replace(tzinfo=CHICAGO, fold=0)
        round_trip = candidate.astimezone(UTC).astimezone(CHICAGO)
        if round_trip.replace(tzinfo=None) == wall:
            return candidate.astimezone(UTC)
        wall += timedelta(minutes=1)
    raise ValueError("The selected local schedule time could not be resolved.")


@dataclass(frozen=True)
class TestGoal:
    target: int
    departure: datetime
    ready_by: datetime
    window_start: datetime
    fixed: bool
    automatic: bool


def schedule_goal(settings: dict, now: datetime, remaining_seconds: float | None = None) -> TestGoal | None:
    weekly = settings["weekly"]
    overnight = settings.get("overnightRun") or {}
    if smart_plan_enabled(settings, now):
        departure = datetime.fromisoformat(overnight["departure"]).astimezone(UTC)
        if now < departure:
            start = datetime.fromisoformat(overnight["start"]).astimezone(UTC)
            return TestGoal(overnight["targetBatteryPercent"], departure, departure-timedelta(minutes=overnight.get("readyBufferMinutes",0)), start, False, True)
    override = settings.get("today")
    if override:
        departure = datetime.fromisoformat(override["departure"].replace("Z", "+00:00")).astimezone(UTC)
        if departure <= now:
            override = None
    if override:
        target = override["targetPercent"]
    elif weekly["enabled"]:
        departure = None
        first_day = now.astimezone(CHICAGO).date()
        for offset in range(9):
            day = first_day + timedelta(days=offset)
            if day.isoweekday() not in weekly["days"]:
                continue
            candidate = local_instant(day, weekly["departureMinutes"])
            if candidate > now:
                departure = candidate
                break
        if departure is None:
            return None
        target = weekly["targetPercent"]
    else:
        return None
    ready_by = departure - timedelta(minutes=30)
    start_day = ready_by.astimezone(CHICAGO).date()
    start = local_instant(start_day, weekly["fixedStartMinutes"])
    if start > ready_by:
        start = local_instant(start_day - timedelta(days=1), weekly["fixedStartMinutes"])
    fixed = weekly["mode"] == "fixed"
    automatic = fixed or remaining_seconds is not None
    if not fixed:
        if remaining_seconds is None:
            start = ready_by
        else:
            # An explicitly virtual example plan, not the research tariff
            # optimizer: prefer 01:00, or start earlier to meet the ready goal.
            preferred = local_instant(start_day, 60)
            start = min(preferred, ready_by - timedelta(seconds=remaining_seconds))
    return TestGoal(target, departure, ready_by, start, fixed, automatic)


def smart_plan_enabled(settings, now: datetime | None = None):
    config = settings.get("overnightRun") or {}
    if config.get("strategy") != "day_ahead_live" or config.get("finalizedAt"):
        return False
    if config.get("campaignId") and (settings.get("campaign") or {}).get("enabled") is False:
        return False
    if now is not None:
        return now < datetime.fromisoformat(config["departure"]).astimezone(UTC)
    return True


def smart_intervals(settings):
    plan = settings.get("smartChargePlan") or {}
    result = []
    for value in plan.get("intervals", []):
        try:
            start = datetime.fromisoformat(value["start"]).astimezone(UTC)
            end = datetime.fromisoformat(value["end"]).astimezone(UTC)
            if end > start: result.append((start, end))
        except (ValueError, TypeError, KeyError):
            continue
    return result


class AppTestControl:
    def __init__(self, clock: Callable[[], datetime], pricing=None):
        self.clock = clock
        self.pricing = pricing

    @staticmethod
    def initial_virtual_state() -> dict:
        return {"batteryPercent": 40.0, "pluggedIn": True, "energyKWh": 0.0,
                "cost": 0.0, "immediateCost": 0.0}

    @staticmethod
    def virtual_enabled(settings: dict) -> bool:
        value = settings.get("virtualCarEnabled", True)
        if type(value) is not bool:
            raise ValueError("Invalid virtual-car setting.")
        return value

    @classmethod
    def virtual_state(cls, settings: dict) -> dict:
        value = settings.get("virtualCarState")
        if not isinstance(value, dict) or type(value.get("pluggedIn")) is not bool:
            raise ValueError("Invalid saved virtual-car state.")
        for key in ("batteryPercent", "energyKWh", "cost", "immediateCost"):
            item = value.get(key)
            if type(item) not in (int, float) or not math.isfinite(item) or (key in ("batteryPercent", "energyKWh") and item < 0):
                raise ValueError("Invalid saved virtual-car state.")
        if value["batteryPercent"] > 100:
            raise ValueError("Invalid saved virtual-car battery.")
        buckets = value.get("energyByHour", {})
        if not isinstance(buckets, dict) or len(buckets) > 5000:
            raise ValueError("Invalid saved energy ledger.")
        for hour, energy in buckets.items():
            if not isinstance(hour, str) or type(energy) not in (int, float) or not math.isfinite(energy) or energy < 0:
                raise ValueError("Invalid saved energy ledger.")
            try:
                parsed = datetime.fromisoformat(hour)
                if parsed.tzinfo is None or parsed.minute or parsed.second or parsed.microsecond: raise ValueError()
            except (TypeError, ValueError):
                raise ValueError("Invalid saved energy ledger.") from None
        return dict(value)

    @staticmethod
    def example_rate(now: datetime) -> float:
        return 7.0 if 1 <= now.astimezone(CHICAGO).hour < 5 else 21.0

    @staticmethod
    def remaining_seconds(settings: dict, now: datetime, battery: float) -> float:
        goal = schedule_goal(settings, now)
        target = goal.target if goal else settings["weekly"]["targetPercent"]
        battery_energy = max(0.0, target - battery) / 100 * VIRTUAL_CAPACITY_KWH
        return battery_energy / (VIRTUAL_POWER_KW * VIRTUAL_EFFICIENCY) * 3600

    @staticmethod
    def control_state(settings: dict) -> tuple[str | None, datetime | None]:
        value = settings.get("appTestControl", {})
        if not isinstance(value, dict):
            raise ValueError("Invalid saved app-test control state.")
        state = value.get("manualState")
        if state not in (None, "charging", "paused", "waiting"):
            raise ValueError("Invalid saved app-test control state.")
        until = value.get("manualUntil")
        if until is not None and not isinstance(until, str):
            raise ValueError("Invalid saved app-test control deadline.")
        try:
            until = datetime.fromisoformat(until.replace("Z", "+00:00")) if until else None
        except ValueError:
            raise ValueError("Invalid saved app-test control deadline.") from None
        if until is not None and until.tzinfo is None:
            raise ValueError("Invalid saved app-test control deadline.")
        return state, until

    def command_value(self, command: str, settings: dict) -> dict:
        now = self.clock().astimezone(UTC)
        goal = schedule_goal(settings, now)
        if command == "resume" and smart_plan_enabled(settings, now):
            return {"manualState": None, "manualUntil": None}
        state = {"charge_now": "charging", "resume": "charging", "pause": "paused", "end": "waiting"}[command]
        until = None
        if goal:
            # End suppresses the rest of this departure's automatic window.
            # A late manual charge/pause remains possible until departure.
            until = goal.departure if command == "end" or goal.ready_by <= now else goal.ready_by
        return {"manualState": state, "manualUntil": utc_string(until) if until else None}

    def state_at(self, settings: dict, now: datetime) -> tuple[str, TestGoal | None]:
        virtual = self.virtual_enabled(settings)
        model = self.virtual_state(settings) if virtual else None
        remaining = self.remaining_seconds(settings, now, model["batteryPercent"]) if model else None
        goal = schedule_goal(settings, now, remaining)
        if not virtual:
            return "unplugged", goal
        manual, manual_until = self.control_state(settings)
        if manual_until is not None and now >= manual_until:
            manual = None
        state = "waiting"
        if goal and goal.automatic:
            if now >= goal.ready_by:
                state = "complete" if not virtual else "waiting"
            elif now >= goal.window_start:
                if smart_plan_enabled(settings, now):
                    plan = settings.get("smartChargePlan")
                    if plan:
                        state = "charging" if any(start <= now < end for start, end in smart_intervals(settings)) else "waiting"
                    else:
                        # No committed plan: charge within the window to protect the deadline.
                        state = "charging"
                else:
                    state = "charging"
        if manual:
            state = manual
        if model:
            target = goal.target if goal else settings["weekly"]["targetPercent"]
            if not model["pluggedIn"]:
                state = "unplugged"
            elif model["batteryPercent"] >= target - 1e-8:
                state = "complete"
            # A campaign is a finite overnight simulation. Even a manual charge
            # cannot escape its current configured window, including UI edits.
            config = settings.get("overnightRun") or {}
            if config.get("campaignId") and config.get("sessionId") and model["pluggedIn"]:
                start = datetime.fromisoformat(config["start"]).astimezone(UTC)
                departure = datetime.fromisoformat(config["departure"]).astimezone(UTC)
                disabled = (settings.get("campaign") or {}).get("enabled") is False
                if disabled or config.get("finalizedAt") or not start <= now < departure:
                    state = "complete" if model["batteryPercent"] >= config["targetBatteryPercent"] - 1e-8 else "waiting"
        return state, goal

    def advance_virtual(self, settings: dict, start: datetime, end: datetime) -> dict:
        """Integrate active intervals, never a stale state across the whole gap."""
        model = self.virtual_state(settings)
        if end <= start or not model["pluggedIn"]:
            return model
        if self.pricing is not None:
            model["energyByHour"] = dict(model.get("energyByHour", {}))
        working = dict(settings, virtualCarState=model)
        cursor = start.astimezone(UTC)
        end = end.astimezone(UTC)
        while cursor < end:
            # Once every possible current/future goal is already met, no more
            # energy can accrue. This also bounds very long idle intervals.
            maximum_target = settings["weekly"]["targetPercent"]
            override = settings.get("today")
            if override and datetime.fromisoformat(override["departure"].replace("Z", "+00:00")) > cursor:
                maximum_target = max(maximum_target, override["targetPercent"])
            if model["batteryPercent"] >= maximum_target - 1e-8:
                break
            manual, until = self.control_state(settings)
            if manual in ("paused", "waiting") and until is None:
                break
            state, goal = self.state_at(working, cursor)
            next_hour = cursor.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
            boundary = min(end, next_hour)
            if smart_plan_enabled(settings, cursor):
                for begins, finishes in smart_intervals(settings):
                    for instant in (begins, finishes):
                        if instant > cursor: boundary = min(boundary, instant)
            if until and until > cursor:
                boundary = min(boundary, until)
            if goal:
                for instant in (goal.window_start, goal.ready_by, goal.departure):
                    if instant > cursor:
                        boundary = min(boundary, instant)
            if state == "charging":
                needed = self.remaining_seconds(working, cursor, model["batteryPercent"])
                seconds = min((boundary - cursor).total_seconds(), needed)
                energy = VIRTUAL_POWER_KW * max(0.0, seconds) / 3600
                target = goal.target if goal else settings["weekly"]["targetPercent"]
                model["batteryPercent"] = min(float(target), model["batteryPercent"]
                    + energy * VIRTUAL_EFFICIENCY / VIRTUAL_CAPACITY_KWH * 100)
                model["energyKWh"] += energy
                if self.pricing is None:
                    model["cost"] += energy * self.example_rate(cursor) / 100
                    model["immediateCost"] += energy * 0.21
                else:
                    hour = cursor.replace(minute=0, second=0, microsecond=0).isoformat()
                    model["energyByHour"][hour] = model["energyByHour"].get(hour, 0.0) + energy
            cursor = boundary
        if self.pricing is not None:
            model["cost"] = self.pricing.energy_cost(model.get("energyByHour", {}))["cost"]
            model["immediateCost"] = 0.0
        return model

    def snapshot(self, settings: dict) -> dict:
        now = self.clock().astimezone(UTC)
        virtual = self.virtual_enabled(settings)
        state, goal = self.state_at(settings, now)
        ready = goal.ready_by if goal else now
        start = goal.window_start if goal else now
        result = {
            "state": state, "controlOnly": not virtual, "virtualCar": virtual,
            "batteryPercent": 0, "targetPercent": goal.target if goal else settings["weekly"]["targetPercent"],
            "readyBy": utc_string(ready), "windowStart": utc_string(start), "windowEnd": utc_string(ready),
            "powerKW": 0, "energyKWh": 0, "remainingMinutes": 0,
            "monthlySavings": 0, "currentRateCents": 0, "estimatedCost": 0, "sessionSavings": 0,
            "updatedAt": utc_string(now), "vehicleName": "No vehicle connected",
        }
        if virtual:
            model = self.virtual_state(settings)
            remaining = self.remaining_seconds(settings, now, model["batteryPercent"])
            start = max(now, start)
            end = start + timedelta(seconds=remaining)
            costs = self.pricing.energy_cost(model.get("energyByHour", {})) if self.pricing is not None else None
            if costs is not None:
                costs["unpricedEnergyKWh"] += max(0.0, model["energyKWh"] - sum(model.get("energyByHour", {}).values()))
                if costs["unpricedEnergyKWh"] > 1e-8: costs["final"] = False
                model["cost"] = costs["cost"]
            result.update({
                "batteryPercent": min(100, int(math.floor(model["batteryPercent"] + 1e-8))),
                "pluggedIn": model["pluggedIn"],
                "powerKW": VIRTUAL_POWER_KW if state == "charging" else 0.0,
                "energyKWh": model["energyKWh"], "estimatedCost": model["cost"],
                "sessionSavings": max(0.0, model["immediateCost"] - model["cost"]),
                "remainingMinutes": 0 if state == "complete" else int(math.ceil(remaining / 60)),
                "currentRateCents": self.example_rate(now) if self.pricing is None else (self.pricing.state()["rateCents"] or 0.0),
                "windowStart": utc_string(start), "windowEnd": utc_string(end),
                "vehicleName": "Virtual car",
            })
        if virtual and self.pricing is not None:
            result.update({"realPricing": True, "rateAvailable": self.pricing.state()["rateCents"] is not None,
                           "costAvailable": costs["unpricedEnergyKWh"] < 1e-8,
                           "costFinal": costs["final"], "unpricedEnergyKWh": costs["unpricedEnergyKWh"],
                           "savingsAvailable": False, "sessionSavings": 0.0})
        if virtual and smart_plan_enabled(settings, now):
            plan = settings.get("smartChargePlan") or {}
            pending = [(begins, finishes) for begins, finishes in smart_intervals(settings) if finishes > now]
            result.update({"smartCharging": True, "plannerReason": plan.get("reason", "Awaiting first plan"),
                           "plannerFeasible": plan.get("feasible", True),
                           "plannedFinishAt": utc_string(pending[-1][1]) if pending else None,
                           "nextChargeAt": utc_string(max(now, pending[0][0])) if pending else None})
            if pending:
                result["windowStart"] = utc_string(max(now, pending[0][0]))
                result["windowEnd"] = utc_string(pending[-1][1])
        return result
