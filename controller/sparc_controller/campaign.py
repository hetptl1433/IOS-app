"""Pure settings transitions for a finite, nightly simulated-car campaign.

The campaign manager owns service stops, atomic writes and the session journal.
These functions never touch a clock, file, network connection or real vehicle.
The durable identity lives with the battery in ``overnightRun`` so restarting a
manager cannot reset the same night's progress a second time.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
import re

from .app_test import (CHICAGO, local_instant, VIRTUAL_CAPACITY_KWH,
                       VIRTUAL_POWER_KW, VIRTUAL_EFFICIENCY)
from .smart_charging import SOURCE_COMMIT

UTC = timezone.utc
DEFAULT_CAMPAIGN = {
    "campaignId": "campaign-2026-11-05",
    "startDate": "2026-11-05",
    "nights": 7,
    "startMinutes": 22 * 60,
    "departureMinutes": 7 * 60,
    "initialBatteryPercent": 20,
    "targetBatteryPercent": 80,
    "screenSleepMinutes": 5,
    "strategy": "day_ahead_live",
    "timezone": "America/Chicago",
}


def _aware(value: datetime) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("Use a timezone-aware current time.")
    return value.astimezone(UTC)


def _stamp(value: datetime) -> str:
    return _aware(value).isoformat().replace("+00:00", "Z")


def validate_campaign(value: dict | None = None) -> dict:
    """Return a normalized JSON configuration, rejecting unsafe or ambiguous input."""
    if value is not None and not isinstance(value, dict):
        raise ValueError("Invalid campaign configuration.")
    config = dict(DEFAULT_CAMPAIGN, **(value or {}))
    ident = config["campaignId"]
    if not isinstance(ident, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,95}", ident):
        raise ValueError("Choose a simple, nonempty campaign ID.")
    try:
        first = date.fromisoformat(config["startDate"])
        if first.isoformat() != config["startDate"]:
            raise ValueError()
    except (TypeError, ValueError):
        raise ValueError("Use an ISO campaign start date.") from None
    bounds = {"nights": (1, 3660), "startMinutes": (0, 1439),
              "departureMinutes": (0, 1439), "initialBatteryPercent": (0, 99),
              "targetBatteryPercent": (50, 100), "screenSleepMinutes": (1, 120)}
    for key, (low, high) in bounds.items():
        if type(config[key]) is not int or not low <= config[key] <= high:
            raise ValueError(f"Invalid campaign {key}.")
    if config["initialBatteryPercent"] >= config["targetBatteryPercent"]:
        raise ValueError("The initial battery must be below the target.")
    if config["strategy"] != "day_ahead_live" or config["timezone"] != "America/Chicago":
        raise ValueError("This campaign requires Day-ahead + Live and Chicago time.")
    try:
        first + timedelta(days=config["nights"])
    except OverflowError:
        raise ValueError("The campaign ends outside the supported calendar.") from None
    return {key: config[key] for key in DEFAULT_CAMPAIGN}


def sessions(campaign: dict | None = None) -> list[dict]:
    """All finite nightly identities; dates, rather than 24-hour UTC jumps, drive them."""
    config = validate_campaign(campaign)
    first = date.fromisoformat(config["startDate"])
    result = []
    for index in range(config["nights"]):
        day = first + timedelta(days=index)
        start = local_instant(day, config["startMinutes"])
        end_day = day + timedelta(days=config["departureMinutes"] <= config["startMinutes"])
        departure = local_instant(end_day, config["departureMinutes"])
        result.append({
            "campaignId": config["campaignId"],
            "sessionId": f"{config['campaignId']}:{day.isoformat()}",
            "nightIndex": index + 1,
            "nightDate": day.isoformat(),
            "start": start.astimezone(CHICAGO).isoformat(),
            "departure": departure.astimezone(CHICAGO).isoformat(),
            "initialBatteryPercent": config["initialBatteryPercent"],
            "targetBatteryPercent": config["targetBatteryPercent"],
            "screenSleepMinutes": config["screenSleepMinutes"],
            "strategy": config["strategy"],
        })
    return result


def session_at(campaign: dict | None, now: datetime) -> dict | None:
    """The active [start, departure) session, or None during the daily gap or after the last night."""
    now = _aware(now)
    return next((night for night in sessions(campaign)
                 if datetime.fromisoformat(night["start"]) <= now
                 < datetime.fromisoformat(night["departure"])), None)


def next_session(campaign: dict | None, now: datetime) -> dict | None:
    """The next not-yet-started night; missed sessions are never replayed."""
    now = _aware(now)
    return next((night for night in sessions(campaign)
                 if datetime.fromisoformat(night["start"]) > now), None)


def _night(campaign: dict, value: dict | str | int) -> dict:
    choices = sessions(campaign)
    if type(value) is int:
        match = next((night for night in choices if night["nightIndex"] == value), None)
    elif isinstance(value, str):
        match = next((night for night in choices if value in (night["nightDate"], night["sessionId"])), None)
    elif isinstance(value, dict):
        match = next((night for night in choices if night["sessionId"] == value.get("sessionId")), None)
        if match and any(value.get(key, match[key]) != match[key] for key in match):
            raise ValueError("The requested session does not match the campaign.")
    else:
        match = None
    if match is None:
        raise ValueError("The requested night is outside this campaign.")
    return match


def _same_session(saved: dict, night: dict) -> bool:
    return all(saved.get(key) == night[key] for key in ("campaignId", "sessionId"))


def arm_settings(settings: dict, campaign: dict | None, night: dict | str | int,
                 now: datetime) -> dict:
    """Arm a new identity once; re-arming the same identity preserves all user edits/progress.

    A manager may pre-arm tonight for the screen. Late startup begins with the
    stored initial battery at the actual startup time, with no invented energy
    during downtime. The controller's in-memory anchor starts when it restarts.
    """
    config = validate_campaign(campaign)
    desired = _night(config, night)
    now = _aware(now)
    result = deepcopy(settings)
    old = result.get("overnightRun") or {}
    if _same_session(old, desired):
        return result
    if now >= datetime.fromisoformat(desired["departure"]):
        raise ValueError("A finished night cannot be armed retroactively.")
    if old.get("strategy") == "day_ahead_live" and not old.get("finalizedAt"):
        try:
            active = (datetime.fromisoformat(old["start"]) <= now
                      < datetime.fromisoformat(old["departure"]))
        except (KeyError, TypeError, ValueError):
            raise ValueError("The existing overnight window is invalid.") from None
        if active:
            raise ValueError("Finalize the active session before arming a different night.")
    previous_weekly = deepcopy(old.get("previousWeekly", result["weekly"]))
    result["overnightRun"] = dict(desired, configuredAt=_stamp(now), armedAt=_stamp(now),
                                  revision=1, replanSeconds=300, sourceCommit=SOURCE_COMMIT,
                                  strategySourceCommit=SOURCE_COMMIT,
                                  simulation=True, priceSource="ComEd official API",
                                  simulatedCapacityKWh=VIRTUAL_CAPACITY_KWH,
                                  simulatedChargingPowerKW=VIRTUAL_POWER_KW,
                                  simulatedEfficiency=VIRTUAL_EFFICIENCY,
                                  costScope="Wholesale electricity supply only; delivery, taxes and account charges excluded",
                                  costMethod="Energy by UTC hour multiplied by the mean of ComEd five-minute prices for that hour; provisional until all 12 samples arrive",
                                  previousWeekly=previous_weekly)
    result["weekly"] = dict(result["weekly"], enabled=False, mode="fixed",
                            fixedStartMinutes=config["startMinutes"],
                            departureMinutes=config["departureMinutes"],
                            targetPercent=config["targetBatteryPercent"])
    result["today"] = {"targetPercent": config["targetBatteryPercent"],
                       "departure": desired["departure"]}
    result["virtualCarEnabled"] = True
    result["virtualCarState"] = {
        "batteryPercent": float(config["initialBatteryPercent"]), "pluggedIn": True,
        "energyKWh": 0.0, "cost": 0.0, "immediateCost": 0.0, "energyByHour": {},
    }
    result["appTestControl"] = {"manualState": None, "manualUntil": None}
    result.pop("smartChargePlan", None)
    return result


def finalize_settings(settings: dict, campaign: dict | None, night: dict | str | int,
                      now: datetime) -> dict:
    """Mark the current identity finished, retaining its battery, ledger, plan and UI edits."""
    desired = _night(validate_campaign(campaign), night)
    now = _aware(now)
    result = deepcopy(settings)
    saved = result.get("overnightRun") or {}
    if not _same_session(saved, desired):
        raise ValueError("Only the saved session can be finalized.")
    if saved.get("finalizedAt"):
        return result
    if now < datetime.fromisoformat(saved["departure"]):
        raise ValueError("The configured session deadline has not arrived.")
    saved["finalizedAt"] = _stamp(now)
    result["weekly"] = dict(result["weekly"], enabled=False)
    result["today"] = None
    result["appTestControl"] = {"manualState": None, "manualUntil": None}
    return result
