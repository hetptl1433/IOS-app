"""Charging strategies. Each one decides using only what it could have known at the time."""
from __future__ import annotations

from bisect import bisect_right
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import time, timedelta
from math import ceil
from typing import Protocol

from .clock import DAY, HOUR, SLOT, at, hour_start, local
from .forecast import Forecaster, Recent, WalkForward, features
from .market import Market, day_ahead_release, latest_visible_interval
from .planner import EPS, Need, plan
from .tariff import Delivery, capacity_risk


@dataclass(frozen=True)
class Session:
    arrival: int
    departure: int
    energy_kwh: float  # grid energy to deliver
    power_kw: float
    reserve_s: int = 1800  # aim to finish this long before departure

    @property
    def slot_kwh(self) -> float:
        return self.power_kw * SLOT / HOUR

    @property
    def hours(self) -> range:
        return range(hour_start(self.arrival), self.departure, HOUR)


class Strategy(Protocol):
    label: str

    def begin(self, session: Session, market: Market, delivery: Delivery) -> None: ...

    def energy(self, now: int, remaining_kwh: float, charging: bool) -> float:
        """kWh to deliver in the 5-minute slot starting at `now`."""


class ChargeNow:
    label = "Charge on plug-in"

    def begin(self, session: Session, market: Market, delivery: Delivery) -> None:
        self.full = session.slot_kwh

    def energy(self, now: int, remaining_kwh: float, charging: bool) -> float:
        return self.full


class Timer:
    """Scheduled charging: start at a fixed wall-clock time, or earlier if that would finish too late.

    As with a Tesla's "Start at" schedule, a car plugged in up to six hours after the
    start time begins charging at once. Plugged in later, it waits for the next day's.
    """

    GRACE = 6 * HOUR

    def __init__(self, start: time):
        self.start = start
        self.label = f"Timer {start:%H:%M}"

    def begin(self, session: Session, market: Market, delivery: Delivery) -> None:
        day = local(session.arrival).date()
        if at(day, self.start) > session.arrival:
            day -= timedelta(days=1)
        if session.arrival - at(day, self.start) <= self.GRACE:
            target = session.arrival
        else:
            target = at(day + timedelta(days=1), self.start)
        duration = ceil(session.energy_kwh / session.slot_kwh - EPS) * SLOT
        latest = session.departure - session.reserve_s - duration
        if latest < session.arrival:
            latest = session.departure - duration
        self.begins = max(session.arrival, min(target, latest))
        self.full = session.slot_kwh

    def energy(self, now: int, remaining_kwh: float, charging: bool) -> float:
        return self.full if now >= self.begins else 0.0


class Threshold:
    """Charges while the newest posted 5-minute price is at or below a cap, and pauses above it.

    The simplest way to "stop charging when prices are high". It starts regardless
    once waiting any longer would not leave time to finish, and it ignores the
    delivery rate and capacity-risk hours.
    """

    def __init__(self, cents: float):
        self.cents = cents
        self.label = f"Below {cents:g}¢"

    def begin(self, session: Session, market: Market, delivery: Delivery) -> None:
        self.market = market
        self.full = session.slot_kwh
        self.deadline = session.departure - session.reserve_s

    def energy(self, now: int, remaining_kwh: float, charging: bool) -> float:
        if now + ceil(remaining_kwh / self.full - EPS) * SLOT >= self.deadline:
            return self.full
        price = self.market.five_minute.get(latest_visible_interval(now))
        if price is None:  # no new price: keep doing what it was doing
            return self.full if charging else 0.0
        return self.full if price <= self.cents else 0.0


class _Planned:
    """Follows a plan from `planner.plan`, rebuilt whenever `information(now)` changes."""

    stickiness = 0.0

    def begin(self, session: Session, market: Market, delivery: Delivery) -> None:
        self.session, self.market, self.delivery = session, market, delivery
        self.blocked = {hour for hour in session.hours if capacity_risk(hour)}
        self.cents = {hour: delivery.cents(hour) for hour in session.hours}
        self.releases = sorted(day_ahead_release(hour) for hour in session.hours)
        self.schedule: dict[int, float] = {}
        self.version: object = None

    def energy(self, now: int, remaining_kwh: float, charging: bool) -> float:
        version = self.information(now)
        if version != self.version:
            self.version = version
            need = Need(now, self.session.departure, remaining_kwh, self.session.power_kw,
                        self.reserve())
            self.schedule = plan(need, self.costs(now), self.blocked,
                                 charging=charging, stickiness=self.stickiness)
        return self.schedule.get(now, 0.0)

    def reserve(self) -> int:
        return self.session.reserve_s

    def released(self, now: int) -> int:
        """How many of the session's hours have a published day-ahead price by `now`."""
        return bisect_right(self.releases, now)

    def information(self, now: int) -> object:
        raise NotImplementedError

    def costs(self, now: int) -> dict[int, float]:
        raise NotImplementedError


class DayAhead(_Planned):
    """Plans from published day-ahead prices and replans only when new ones are released."""

    label = "Day-ahead plan"

    def information(self, now: int) -> object:
        return self.released(now)

    def costs(self, now: int) -> dict[int, float]:
        return {hour: self.market.known_day_ahead(hour, now) + self.cents[hour] for hour in self.session.hours}


class Live(_Planned):
    """The day-ahead plan, revised every 5 minutes as real-time prices are published.

    An hour already under way is priced from its published 5-minute prices plus an
    estimate of the rest: the day-ahead price, plus the latest deviation from it
    scaled by `decay[k - 1]` for an interval k steps after the latest published one.
    """

    label = "Day-ahead + live prices"

    def __init__(self, decay: Sequence[float], stickiness: float = 0.5):
        self.decay = list(decay)
        self.stickiness = stickiness

    def begin(self, session: Session, market: Market, delivery: Delivery) -> None:
        super().begin(session, market, delivery)
        # The day-ahead price known for an hour changes only when its own or the day
        # before's is released.
        self.changes = sorted({day_ahead_release(hour) for hour in session.hours}
                              | {day_ahead_release(hour - DAY) for hour in session.hours})
        self.known_until = None

    def information(self, now: int) -> object:
        return now

    def costs(self, now: int) -> dict[int, float]:
        market = self.market
        changed = bisect_right(self.changes, now)
        if changed != self.known_until:
            self.known_until = changed
            self.known = {hour: market.known_day_ahead(hour, now) for hour in self.session.hours}
        expected = dict(self.known)
        latest = latest_visible_interval(now)
        reference = market.known_day_ahead(hour_start(latest), now)
        if latest in market.five_minute and reference is not None:
            deviation = market.five_minute[latest] - reference
            reach = latest + len(self.decay) * SLOT
            for hour in self.session.hours:
                if hour < hour_start(now) or hour > reach:
                    continue
                total = 0.0
                for t in range(hour, hour + HOUR, SLOT):
                    if t <= latest and t in market.five_minute:
                        total += market.five_minute[t]
                    else:
                        k = (t - latest) // SLOT
                        persists = self.decay[k - 1] if 1 <= k <= len(self.decay) else 0.0
                        total += expected[hour] + persists * deviation
                expected[hour] = total / 12
        return {hour: price + self.cents[hour] for hour, price in expected.items()}


class Forecasted(_Planned):
    """Plans from a learned forecast of each hour's real-time price, revised every hour.

    `source` is one trained model, or a `WalkForward` series that picks the model
    fitted for each night.
    """

    stickiness = 0.5
    CACHE_LIMIT = 20_000

    def __init__(self, source: Forecaster | WalkForward, label: str | None = None):
        self.source = source
        self.label = label or f"Forecast: {source.name}"
        self.predicted: dict[tuple[str, int], dict[int, float]] = {}  # forecasts shared across sessions

    def begin(self, session: Session, market: Market, delivery: Delivery) -> None:
        if market is not getattr(self, "market", market):
            self.predicted.clear()
        super().begin(session, market, delivery)
        self.forecaster = self.source.for_night(local(session.arrival).date())

    def information(self, now: int) -> object:
        return hour_start(now), self.released(now)

    def costs(self, now: int) -> dict[int, float]:
        hours = [hour for hour in self.session.hours if hour >= hour_start(now)]
        key = (self.forecaster.id, now)
        if key not in self.predicted and len(self.predicted) >= self.CACHE_LIMIT:
            self.predicted.clear()
        known = self.predicted.setdefault(key, {})
        missing = [hour for hour in hours if hour not in known]
        if missing:
            recent = Recent.seen_by(self.market, now)
            rows = [features(self.market, hour, now, recent, self.forecaster.inputs) for hour in missing]
            for hour, row, gap in zip(missing, rows, self.forecaster.predict_many(rows)):
                known[hour] = row[0] + gap
        return {hour: known[hour] + self.cents[hour] for hour in hours}


class Hindsight(_Planned):
    """Knows every hour's final price in advance: a reference, not a strategy.

    It keeps the planners' guard against capacity-risk hours, so it is the lowest
    cost reachable without that risk. A strategy that ignores the guard can cost
    less on summer afternoons and still lose on next year's capacity charge.
    """

    label = "Perfect hindsight"

    def information(self, now: int) -> object:
        return 0

    def reserve(self) -> int:
        return 0

    def costs(self, now: int) -> dict[int, float]:
        return {hour: self.market.hourly[hour] + self.cents[hour] for hour in self.session.hours}


def fit_decay(market: Market, start: int, end: int, steps: int = 24) -> list[float]:
    """Least-squares share of a 5-minute price's deviation from day-ahead that remains k steps later."""
    deviation = {t: d for t, d in market.deviations.values.items() if start <= t < end}
    decay = []
    for k in range(1, steps + 1):
        cross = square = 0.0
        for t, d in deviation.items():
            later = deviation.get(t + k * SLOT)
            if later is not None:
                cross += d * later
                square += d * d
        decay.append(cross / square if square else 0.0)
    return decay
