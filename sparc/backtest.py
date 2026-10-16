"""Replay real prices through each charging strategy, night after night."""
from __future__ import annotations

import csv
from collections import defaultdict
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from datetime import date, time, timedelta
from pathlib import Path

from .clock import DAY, SLOT, at, hour_start
from .market import Market
from .planner import EPS
from .strategies import Session, Strategy
from .tariff import Delivery, capacity_risk


@dataclass(frozen=True)
class Profile:
    name: str
    arrive: time
    depart: time  # the next day when not after `arrive`
    battery_kwh: float  # energy added to the battery
    power_kw: float
    efficiency: float = 0.9
    reserve_min: int = 30

    def session(self, day: date) -> Session:
        leave = day + timedelta(days=1) if self.depart <= self.arrive else day
        return Session(at(day, self.arrive), at(leave, self.depart),
                       self.battery_kwh / self.efficiency, self.power_kw, self.reserve_min * 60)

    @property
    def description(self) -> str:
        return (f"plug in {self.arrive:%H:%M}, leave {self.depart:%H:%M}, "
                f"+{self.battery_kwh:g} kWh at {self.power_kw:g} kW")


PROFILES = (
    Profile("Commuter", time(18), time(7), 15, 7.7),
    Profile("Big day", time(18), time(7), 40, 7.7),
    Profile("Late arrival", time(22), time(6), 50, 11.5),
    Profile("Afternoon plug-in", time(14), time(7), 30, 7.7),
)


@dataclass
class Outcome:
    supply: float = 0.0  # dollars
    delivery: float = 0.0  # dollars
    energy_kwh: float = 0.0
    risk_kwh: float = 0.0  # delivered in capacity-risk hours
    starts: int = 0
    pauses: int = 0  # stops before the target was reached
    ready: bool = False

    @property
    def cost(self) -> float:
        return self.supply + self.delivery


@dataclass(frozen=True)
class Night:
    day: date
    profile: str
    delivery: str
    strategy: str
    outcome: Outcome = field(compare=False)


def simulate(session: Session, strategy: Strategy, market: Market, delivery: Delivery) -> Outcome:
    """Charge at constant power and bill each kWh at its hour's final average price."""
    strategy.begin(session, market, delivery)
    cents = {hour: delivery.cents(hour) for hour in session.hours}
    risky = {hour for hour in session.hours if capacity_risk(hour)}
    result = Outcome()
    full = session.slot_kwh
    remaining = session.energy_kwh
    charging = False
    for now in range(session.arrival, session.departure, SLOT):
        kwh = 0.0
        if remaining > EPS:
            kwh = max(0.0, min(strategy.energy(now, remaining, charging), full, remaining))
        on = kwh > EPS
        if on and not charging:
            result.starts += 1
        elif charging and not on:
            result.pauses += 1
        hour = hour_start(now)
        result.supply += kwh * market.hourly[hour] / 100
        result.delivery += kwh * cents[hour] / 100
        if hour in risky:
            result.risk_kwh += kwh
        result.energy_kwh += kwh
        remaining -= kwh
        charging = on and kwh >= full - EPS and remaining > EPS
        if on and not charging and remaining > EPS:
            result.pauses += 1  # stopped part-way through the slot
    result.ready = remaining <= 1e-6
    return result


def usable(session: Session, market: Market) -> bool:
    """Every hour has a complete real-time price and a day-ahead price (and the day before's)."""
    return all(hour in market.hourly and hour in market.day_ahead and hour - DAY in market.day_ahead
               for hour in session.hours)


class Cancelled(Exception):
    pass


def run(market: Market, days: Iterable[date], profiles: Sequence[Profile],
        strategies: Sequence[Strategy], deliveries: Sequence[Delivery], *,
        progress: Callable[[int, int], None] | None = None,
        should_stop: Callable[[], bool] | None = None) -> tuple[list[Night], dict[str, int]]:
    nights: list[Night] = []
    skipped: dict[str, int] = defaultdict(int)
    days = list(days)
    total, done = len(profiles) * len(days), 0
    for profile in profiles:
        for day in days:
            if should_stop and should_stop():
                raise Cancelled
            done += 1
            session = profile.session(day)
            if not usable(session, market):
                skipped[profile.name] += 1
                continue
            for delivery in deliveries:
                for strategy in strategies:
                    outcome = simulate(session, strategy, market, delivery)
                    nights.append(Night(day, profile.name, delivery.name, strategy.label, outcome))
            if progress:
                progress(done, total)
    return nights, dict(skipped)


@dataclass(frozen=True)
class Summary:
    profile: str
    delivery: str
    strategy: str
    nights: int
    cost: float  # mean dollars per night
    saved_per_year: float  # vs charging on plug-in, if every night looked like this
    share: float | None  # of the savings perfect hindsight would have made
    worst: float  # most expensive night
    starts: float  # per night
    pauses: float  # per night
    risk_kwh_per_year: float
    ready: float  # share of nights finished on time


def summarize(nights: Sequence[Night], baseline: str, best: str) -> list[Summary]:
    groups: dict[tuple[str, str, str], list[Night]] = defaultdict(list)
    for night in nights:
        groups[night.profile, night.delivery, night.strategy].append(night)
    summaries = []
    for (profile, delivery, strategy), group in groups.items():
        base = {n.day: n.outcome.cost for n in groups[profile, delivery, baseline]}
        ideal = {n.day: n.outcome.cost for n in groups[profile, delivery, best]}
        count = len(group)
        saved = sum(base[n.day] - n.outcome.cost for n in group)
        possible = sum(base[n.day] - ideal[n.day] for n in group)
        summaries.append(Summary(
            profile, delivery, strategy, count,
            cost=sum(n.outcome.cost for n in group) / count,
            saved_per_year=saved / count * 365,
            share=saved / possible if possible > 1e-9 else None,
            worst=max(n.outcome.cost for n in group),
            starts=sum(n.outcome.starts for n in group) / count,
            pauses=sum(n.outcome.pauses for n in group) / count,
            risk_kwh_per_year=sum(n.outcome.risk_kwh for n in group) / count * 365,
            ready=sum(n.outcome.ready for n in group) / count))
    return summaries


def format_table(summaries: Sequence[Summary], order: Sequence[str]) -> str:
    """One table per delivery tariff and profile, in the order they were simulated."""
    lines = []
    width = max([26, *(len(s.strategy) + 2 for s in summaries)])
    rank = {label: i for i, label in enumerate(order)}
    deliveries = {s.delivery: i for i, s in enumerate(reversed(summaries))}
    profiles = {s.profile: i for i, s in enumerate(reversed(summaries))}
    keyed = sorted(summaries, key=lambda s: (-deliveries[s.delivery], -profiles[s.profile],
                                             rank.get(s.strategy, len(rank))))
    current = None
    for s in keyed:
        if (s.delivery, s.profile) != current:
            current = (s.delivery, s.profile)
            lines += ["", f"{s.profile} · {s.delivery} · {s.nights} nights",
                      f"  {'strategy':<{width}}{'$/night':>9}{'saved $/yr':>12}{'of max':>8}"
                      f"{'worst $':>9}{'starts':>8}{'pauses':>8}{'risk kWh/yr':>13}{'ready':>7}"]
        share = "—" if s.share is None else f"{100 * s.share:.0f}%"
        lines.append(f"  {s.strategy:<{width}}{s.cost:>9.2f}{s.saved_per_year:>12.0f}{share:>8}"
                     f"{s.worst:>9.2f}{s.starts:>8.2f}{s.pauses:>8.2f}{s.risk_kwh_per_year:>13.0f}"
                     f"{100 * s.ready:>6.0f}%")
    return "\n".join(lines)


@dataclass(frozen=True)
class HeadToHead:
    profile: str
    delivery: str
    nights: int
    mean_gain: float  # dollars per night that `better` saves over `worse`
    nights_won: int  # nights it saved at least `margin`
    nights_lost: int  # nights it cost at least `margin` more
    best_night: tuple[date, float]


def head_to_head(nights: Sequence[Night], better: str, worse: str, margin: float = 0.05) -> list[HeadToHead]:
    costs: dict[tuple[str, str], dict[str, dict[date, float]]] = defaultdict(lambda: defaultdict(dict))
    for n in nights:
        costs[n.profile, n.delivery][n.strategy][n.day] = n.outcome.cost
    result = []
    for (profile, delivery), by_strategy in costs.items():
        gains = {day: by_strategy[worse][day] - cost for day, cost in by_strategy[better].items()}
        best_day = max(gains, key=gains.get)
        result.append(HeadToHead(profile, delivery, len(gains), sum(gains.values()) / len(gains),
                                 sum(g >= margin for g in gains.values()),
                                 sum(g <= -margin for g in gains.values()),
                                 (best_day, gains[best_day])))
    deliveries = list(dict.fromkeys(h.delivery for h in result))
    return sorted(result, key=lambda h: deliveries.index(h.delivery))  # stable: profiles keep their order


CSV_COLUMNS = ("day", "profile", "tariff", "strategy", "cost_usd", "supply_usd", "delivery_usd",
               "energy_kwh", "risk_kwh", "starts", "pauses", "ready")


def write_csv(nights: Sequence[Night], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(CSV_COLUMNS)
        for n in nights:
            o = n.outcome
            writer.writerow([n.day.isoformat(), n.profile, n.delivery, n.strategy, round(o.cost, 6),
                             round(o.supply, 6), round(o.delivery, 6), round(o.energy_kwh, 6),
                             round(o.risk_kwh, 6), o.starts, o.pauses, o.ready])
