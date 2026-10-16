"""Charging strategies. Each one decides using only what it could have known at the time."""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import time, timedelta
from math import ceil
from typing import Protocol

from .clock import HOUR, SLOT, at, hour_start, local
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
    """Start at a fixed wall-clock time, or earlier if that would finish too late."""

    def __init__(self, start: time):
        self.start = start
        self.label = f"Timer {start:%H:%M}"

    def begin(self, session: Session, market: Market, delivery: Delivery) -> None:
        day = local(session.arrival).date()
        target = at(day, self.start)
        if target < session.arrival:
            target = at(day + timedelta(days=1), self.start)
        duration = ceil(session.energy_kwh / session.slot_kwh - EPS) * SLOT
        latest = session.departure - session.reserve_s - duration
        if latest < session.arrival:
            latest = session.departure - duration
        self.begins = max(session.arrival, min(target, latest))
        self.full = session.slot_kwh

    def energy(self, now: int, remaining_kwh: float, charging: bool) -> float:
        return self.full if now >= self.begins else 0.0


class _Planned:
    """Follows a plan from `planner.plan`, rebuilt whenever `information(now)` changes."""

    stickiness = 0.0

    def begin(self, session: Session, market: Market, delivery: Delivery) -> None:
        self.session, self.market, self.delivery = session, market, delivery
        self.blocked = {hour for hour in session.hours if capacity_risk(hour)}
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

    def information(self, now: int) -> object:
        raise NotImplementedError

    def costs(self, now: int) -> dict[int, float]:
        raise NotImplementedError


class DayAhead(_Planned):
    """Plans from published day-ahead prices and replans only when new ones are released."""

    label = "Day-ahead plan"

    def information(self, now: int) -> object:
        return sum(day_ahead_release(hour) <= now for hour in self.session.hours)

    def costs(self, now: int) -> dict[int, float]:
        return {hour: self.market.known_day_ahead(hour, now) + self.delivery.cents(hour)
                for hour in self.session.hours}


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

    def information(self, now: int) -> object:
        return now

    def costs(self, now: int) -> dict[int, float]:
        market = self.market
        expected = {hour: market.known_day_ahead(hour, now) for hour in self.session.hours}
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
        return {hour: price + self.delivery.cents(hour) for hour, price in expected.items()}


class Hindsight(_Planned):
    """Knows every hour's final price in advance: the lowest achievable cost, not a strategy."""

    label = "Perfect hindsight"

    def information(self, now: int) -> object:
        return 0

    def reserve(self) -> int:
        return 0

    def costs(self, now: int) -> dict[int, float]:
        return {hour: self.market.hourly[hour] + self.delivery.cents(hour) for hour in self.session.hours}


def fit_decay(market: Market, start: int, end: int, steps: int = 24) -> list[float]:
    """Least-squares share of a 5-minute price's deviation from day-ahead that remains k steps later."""
    deviation = {}
    for t, price in market.five_minute.items():
        if start <= t < end and hour_start(t) in market.day_ahead:
            deviation[t] = price - market.day_ahead[hour_start(t)]
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
