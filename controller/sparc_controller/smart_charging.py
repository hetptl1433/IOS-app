"""Live-device adapter for the pinned SPARC main charging strategies.

The research modules are vendored without edits. This adapter translates an exact
wall-clock window into their five-minute planning grid, and translates chosen grid
energy back into full-power on/off intervals. Callers persist the returned plan
before applying it; this module never reads files, performs I/O, or rewrites history.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime, timedelta, timezone
import json
import math

from .research.clock import HOUR, SLOT, hour_start
from .research.market import Market, day_ahead_release, latest_visible_interval
from .research.planner import Need, plan
from .research.strategies import DayAhead, Live, Session, fit_decay
from .research.tariff import Delivery

SOURCE_REPOSITORY = "https://github.com/hetptl1433/sparc"
SOURCE_COMMIT = "28d838d0d68ee175b56861622dbd0847a9cdced1"
SUPPLY_ONLY = Delivery("Energy supply only; account delivery charges not supplied", ((0, 0.0),))
UTC = timezone.utc
EPS = 1e-8


def _instant(value: datetime, name: str) -> float:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(name + " must be a timezone-aware datetime.")
    stamp = value.timestamp()
    if not math.isfinite(stamp):
        raise ValueError("Invalid " + name + ".")
    return stamp


def _iso(stamp: float) -> str:
    return datetime.fromtimestamp(stamp, UTC).isoformat().replace("+00:00", "Z")


def _number(value, name: str, low: float, high: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not low <= value <= high:
        raise ValueError("Invalid " + name + ".")
    return float(value)


def _prices(values: Mapping[int, float], alignment: int, cutoff: int | None = None) -> dict[int, float]:
    if not isinstance(values, Mapping):
        raise ValueError("Prices must be a timestamp-to-price mapping.")
    result = {}
    for stamp, price in values.items():
        if type(stamp) is not int or stamp % alignment or isinstance(price, bool) or not isinstance(price, (int, float)) or not math.isfinite(price):
            raise ValueError("Malformed real price input.")
        if cutoff is None or stamp <= cutoff:
            result[stamp] = float(price)
    return result


def _decay(market: Market, visible: int, supplied) -> tuple[list[float], dict]:
    if supplied is not None:
        metadata = dict(supplied) if isinstance(supplied, Mapping) else {}
        values = metadata.pop("decay", None) if isinstance(supplied, Mapping) else supplied
        if not isinstance(values, Sequence) or isinstance(values, (str, bytes)) or len(values) != 24:
            raise ValueError("Supply exactly 24 fitted live-decay coefficients.")
        fitted = []
        for value in values:
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
                raise ValueError("Live-decay coefficients must be finite numbers.")
            fitted.append(float(value))
        # Bootstrap metadata comes from a saved JSON fit; retain it without adding
        # made-up dates or sample counts when only the coefficient list was supplied.
        metadata = json.loads(json.dumps(metadata, allow_nan=False))
        if metadata.get("sourceCommit", SOURCE_COMMIT) != SOURCE_COMMIT:
            raise ValueError("The fitted decay belongs to a different research source commit.")
        return fitted, {**metadata, "source": "supplied_pinned_fit", "coefficients": fitted}
    first, end = visible + SLOT - 24 * HOUR, visible + SLOT
    fitted = fit_decay(market, first, end, steps=24)
    observed = {stamp: deviation for stamp, deviation in market.deviations.values.items() if first <= stamp < end}
    counts = [sum(stamp + lag * SLOT in observed for stamp in observed) for lag in range(1, 25)]
    return fitted, {"source": "fit_decay_recent_observed_history", "sourceCommit": SOURCE_COMMIT,
                    "trainingStartInclusive": _iso(first), "trainingEndExclusive": _iso(end),
                    "observations": len(observed), "pairsPerLag": counts, "coefficients": fitted}


def _make_intervals(schedule: Mapping[int, float], first: float, last: float,
                    power: float, prices: Mapping[int, float], charging: bool) -> list[dict]:
    intervals = []
    previous_end = None
    for slot, energy in sorted(schedule.items()):
        start, end = max(float(slot), first), min(float(slot + SLOT), last)
        energy = min(float(energy), max(0.0, end - start) * power / HOUR)
        if energy <= EPS:
            continue
        seconds = energy / power * HOUR
        # The research planner puts a partial block at its beginning when leading
        # into a following block, and at its end when continuing one already on.
        # Right-align a new partial interval, left-align an ongoing interval.
        continues = previous_end is not None and abs(previous_end - start) < 1e-5
        continues = continues or (charging and start <= first < end and slot <= first < slot + SLOT)
        if continues or seconds >= end - start - 1e-6:
            end = min(end, start + seconds)
        else:
            start = max(start, end - seconds)
        price = prices.get(hour_start(slot))
        item = {"start": _iso(start), "end": _iso(end), "energyKWh": (end - start) * power / HOUR,
                "priceCents": price}
        # Merge adjacent same-hour pieces; retain hour boundaries for audit and
        # settled billing, even if two hours happen to have equal forecasts.
        if intervals and abs(previous_end - start) < 1e-5 and hour_start(slot) == hour_start(int(datetime.fromisoformat(intervals[-1]["start"].replace("Z", "+00:00")).timestamp())) and intervals[-1]["priceCents"] == price:
            intervals[-1]["end"] = item["end"]
            intervals[-1]["energyKWh"] += item["energyKWh"]
        else:
            intervals.append(item)
        previous_end = end
    return intervals


def _fill_earliest(first: float, last: float, needed: float, power: float) -> dict[int, float]:
    schedule = {}
    cursor = first
    while cursor < last and needed > EPS:
        slot = int(cursor // SLOT) * SLOT
        end = min(last, float(slot + SLOT))
        energy = min(needed, (end - cursor) * power / HOUR)
        schedule[slot] = energy
        needed -= energy
        cursor = end
    return schedule


def make_plan(now: datetime, earliest: datetime, deadline: datetime,
              battery_percent: float, target_percent: float,
              day_ahead: Mapping[int, float], live_samples: Mapping[int, float],
              previous_charging: bool = False, mode: str = "live", *,
              power_kw: float = 11.5, capacity_kwh: float = 100.0,
              efficiency: float = 0.9, reserve_seconds: int = 0,
              decay: Sequence[float] | Mapping | None = None,
              delivery_cents: Mapping[int, float] | None = None) -> dict:
    """Return a JSON-safe, full-power charging plan without side effects.

    `day_ahead` maps UTC hour starts to ComEd cents/kWh; `live_samples` maps UTC
    five-minute interval STARTS to published prices. The exact repository strategy
    applies its publication-time guard before using them. A supplied fitted decay
    JSON (or its 24 coefficients) is used unchanged; otherwise the repository's
    fit_decay is applied to the last 24 hours of available real observations.

    The cheapest ranking is optimal for the chosen strategy's current price
    forecast and safeguards. Future realized prices are unknown. Missing required
    forecasts trigger an explicit earliest-charge deadline-protection fallback.
    """
    moment = _instant(now, "now")
    arrival = _instant(earliest, "earliest")
    departure = _instant(deadline, "deadline")
    if departure <= arrival or departure - arrival > 48 * HOUR:
        raise ValueError("Choose a charging window of at most 48 hours.")
    battery = _number(battery_percent, "battery percentage", 0, 100)
    target = _number(target_percent, "target percentage", 0, 100)
    power = _number(power_kw, "charging power", 0.001, 1000)
    capacity = _number(capacity_kwh, "battery capacity", 0.001, 10000)
    eta = _number(efficiency, "charging efficiency", 0.001, 1)
    if mode not in ("live", "day_ahead") or type(previous_charging) is not bool:
        raise ValueError("Choose the pinned day_ahead or live strategy.")
    if type(reserve_seconds) is not int or not 0 <= reserve_seconds < departure - arrival:
        raise ValueError("Invalid departure reserve.")
    end = departure
    first = max(moment, arrival)
    required = max(0.0, target - battery) / 100 * capacity / eta
    available = max(0.0, end - first) * power / HOUR
    shortfall = max(0.0, required - available)
    feasible = shortfall < EPS
    next_replan = min((math.floor(moment / SLOT) + 1) * SLOT, end)
    result = {"generatedAt": _iso(moment), "effectiveFrom": _iso(moment),
              "validUntil": _iso(max(moment, next_replan)), "chargeNow": False,
              "currentUntil": _iso(max(moment, end)), "reason": "target_reached",
              "mode": mode, "strategy": Live.label if mode == "live" else DayAhead.label,
              "sourceRepository": SOURCE_REPOSITORY, "sourceCommit": SOURCE_COMMIT,
              "replanSeconds": SLOT, "reserveSeconds": reserve_seconds,
              "gridEnergyNeededKWh": required, "feasible": feasible,
              "shortfallKWh": shortfall, "latestSafeStart": _iso(end - required / power * HOUR),
              "intervals": [], "estimatedCostUSD": 0.0, "fallback": False,
              "nextChargeAt": None, "estimatedFinishAt": None,
              "costScope": "ComEd energy supply only; account delivery and taxes excluded",
              "optimality": "Optimal for this strategy's forecast and safeguards; future prices are unknown",
              "training": None, "forecastHourly": []}
    if required <= EPS:
        return result
    if first >= end:
        result.update(reason="deadline_passed", feasible=False, shortfallKWh=required, fallback=True)
        return result
    aligned_first = int(first // SLOT) * SLOT
    aligned_end = int(math.ceil(end / SLOT)) * SLOT
    visible = latest_visible_interval(int(moment))
    ahead = _prices(day_ahead, HOUR)
    samples = _prices(live_samples, SLOT, visible)
    market = Market(samples, ahead)
    needed_hours = list(range(hour_start(aligned_first), aligned_end, HOUR))
    missing = [hour for hour in needed_hours if market.known_day_ahead(hour, int(moment)) is None]
    result["missingForecastHours"] = [_iso(hour) for hour in missing]
    result["liveInput"] = {"latestVisibleInterval": _iso(visible),
                            "latestObservedInterval": _iso(max(samples)) if samples else None,
                            "visibleSampleCount": len(samples), "currentReadingAvailable": visible in samples}
    rates = _prices(delivery_cents, HOUR) if delivery_cents is not None else None
    if rates is not None and any(hour not in rates for hour in needed_hours):
        raise ValueError("A verified delivery price is required for every planning hour.")
    costs = {}
    if missing or not feasible:
        result["fallback"] = True
        result["reason"] = "infeasible_deadline" if not feasible else "missing_forecast_deadline_protection"
        # Known hour values can still be shown; missing prices stay None, never a
        # fabricated fallback tariff or a claim that unknown electricity is free.
        costs = {hour: market.known_day_ahead(hour, int(moment)) for hour in needed_hours}
        if rates is not None:
            costs = {hour: price + rates[hour] if price is not None else None for hour, price in costs.items()}
        schedule = _fill_earliest(first, end, min(required, available), power)
        intervals = _make_intervals(schedule, first, end, power, costs, True)
    else:
        session = Session(aligned_first, aligned_end, required, power, reserve_s=reserve_seconds)
        if mode == "live":
            fitted, metadata = _decay(market, visible, decay)
            strategy = Live(fitted)
            result["training"] = metadata
        else:
            strategy = DayAhead()
        delivery = SUPPLY_ONLY
        if delivery_cents is not None:
            class DatedDelivery:
                def cents(self, hour): return rates[hour]
            delivery = DatedDelivery()
        strategy.begin(session, market, delivery)
        costs = strategy.costs(int(moment))
        if any(not math.isfinite(price) for price in costs.values()):
            raise ValueError("The selected strategy returned a non-finite forecast.")
        # The exact research planner assumes full five-minute slots. Boundaries
        # on an active device can be partial (restart, user edit, or odd deadline).
        # Preserve its ordering/partial-block preference, increasing requested grid
        # allocation just enough to replace capacity clipped at the two edges.
        def selected(energy):
            raw = plan(Need(aligned_first, aligned_end, energy, power, reserve_seconds), costs, strategy.blocked,
                       charging=previous_charging, stickiness=strategy.stickiness)
            return {slot: min(amount, max(0.0, min(slot+SLOT, end)-max(float(slot), first))*power/HOUR)
                    for slot, amount in raw.items()}
        schedule = selected(required)
        if sum(schedule.values()) < required - EPS:
            low, high = required, (aligned_end-aligned_first)*power/HOUR
            for _ in range(45):
                middle = (low+high)/2
                if sum(selected(middle).values()) >= required: high = middle
                else: low = middle
            schedule = selected(high)
            excess = max(0.0, sum(schedule.values()) - required)
            for slot in reversed(list(schedule)):
                remove = min(excess, schedule[slot]);schedule[slot] -= remove;excess -= remove
                if excess <= 1e-12:break
        intervals = _make_intervals(schedule, first, end, power, costs, previous_charging)
        result["reason"] = "price_plan"
        if mode == "live" and visible not in samples:
            result["reason"] = "day_ahead_plan_live_reading_missing"
    if delivery_cents is not None:
        result["costScope"] = "ComEd energy supply plus Time-of-Day distribution; other bill charges excluded"
    result["intervals"] = intervals
    result["forecastHourly"] = [{"hour": _iso(hour), "priceCents": costs.get(hour),
                                  "dayAheadCents": market.known_day_ahead(hour, int(moment)),
                                  "previousDayFallback": not (hour in ahead and day_ahead_release(hour) <= moment)}
                                 for hour in needed_hours]
    planned = sum(item["energyKWh"] for item in intervals)
    if feasible and planned < required - 1e-6:
        # No hidden undercharge if an unforeseen edge case in grid conversion
        # prevents the research plan from delivering its required energy.
        result.update(fallback=True, reason="grid_conversion_deadline_protection")
        intervals = _make_intervals(_fill_earliest(first,end,required,power),first,end,power,costs,True)
        result["intervals"] = intervals
    if any(item["priceCents"] is None for item in intervals):
        result["estimatedCostUSD"] = None
    else:
        result["estimatedCostUSD"] = sum(item["energyKWh"]*item["priceCents"]/100 for item in intervals)
    parsed = [(datetime.fromisoformat(item["start"].replace("Z", "+00:00")).timestamp(),
               datetime.fromisoformat(item["end"].replace("Z", "+00:00")).timestamp()) for item in intervals]
    active = next(((start,stop) for start,stop in parsed if start <= moment < stop), None)
    result["chargeNow"] = active is not None
    result["currentUntil"] = _iso(active[1]) if active else _iso(next((start for start,_ in parsed if start > moment),end))
    result["nextChargeAt"] = _iso(next((start for start,_ in parsed if start > moment), active[0] if active else end)) if parsed else None
    result["estimatedFinishAt"] = _iso(parsed[-1][1]) if parsed else None
    if moment < arrival:
        result["reason"] = "before_window" if not result["fallback"] else result["reason"]
    elif end-moment <= required/power*HOUR+SLOT and not result["fallback"]:
        result["reason"] = "deadline_protection"
    return result
