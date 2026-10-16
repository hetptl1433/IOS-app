"""Backfill ComEd price history, and check what was stored."""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta

from . import comed
from .clock import HOUR, at, local
from .market import Market
from .store import Store


def backfill(store: Store, first: date, last: date, get: comed.Get,
             log: Callable[[str], None] = print) -> list[str]:
    """Fetch 5-minute prices a week at a time and hourly tables a day at a time.

    Finished requests are recorded, so an interrupted backfill resumes where it
    stopped. Returns the dates whose hourly table could not be read.
    """
    done = store.fetched("five-minute")
    chunks = [c for c in comed.day_chunks(first, last) if f"{c[0]}/{c[1]}" not in done]
    for n, (start, end) in enumerate(chunks, 1):
        prices = comed.five_minute_prices(datetime.combine(start, time(0)),
                                          datetime.combine(end, time(23, 55)), get)
        store.add_five_minute(prices)
        store.mark_fetched("five-minute", f"{start}/{end}")
        log(f"5-minute prices {start} to {end}: {len(prices)} intervals ({n}/{len(chunks)})")

    # The table for date D starts with the hour before midnight, so one extra day
    # covers the last evening.
    done = store.fetched("hourly")
    days = [d for d in _days(first, last + timedelta(days=1)) if d.isoformat() not in done]
    failed = []
    for n, day in enumerate(days, 1):
        try:
            store.add_hourly(comed.hourly_table(day, get))
        except comed.ComEdError as exc:
            failed.append(day.isoformat())
            log(f"hourly table {day}: {exc}")
            continue
        store.mark_fetched("hourly", day.isoformat())
        if n % 30 == 0 or n == len(days):
            log(f"hourly tables through {day} ({n}/{len(days)})")
    return failed


@dataclass
class Coverage:
    first: date
    last: date
    hours: int = 0
    complete_hours: int = 0
    day_ahead_hours: int = 0
    compared: int = 0
    agree: int = 0
    worst: list[tuple[int, float, float]] = field(default_factory=list)  # hour, 5-min mean, published

    def __str__(self) -> str:
        lines = [f"{self.first} to {self.last}: {self.hours} hours",
                 f"  complete 5-minute hours: {self.complete_hours} "
                 f"({_percent(self.complete_hours, self.hours)})",
                 f"  day-ahead prices:        {self.day_ahead_hours} "
                 f"({_percent(self.day_ahead_hours, self.hours)})",
                 f"  5-minute mean matches ComEd's published hourly price (to 0.05 cents): "
                 f"{self.agree} of {self.compared} ({_percent(self.agree, self.compared)})"]
        for hour, mean, published in self.worst:
            lines.append(f"    {local(hour):%Y-%m-%d %H:%M %Z}: 5-minute mean {mean:.3f}, published {published}")
        return "\n".join(lines)


def check(store: Store, first: date, last: date, show: int = 5) -> Coverage:
    start, end = at(first, time(0)), at(last + timedelta(days=1), time(0))
    market = Market(store.five_minute(start, end), store.day_ahead(start, end))
    published = store.published_real_time(start, end)
    report = Coverage(first, last, hours=(end - start) // HOUR)
    report.complete_hours = len(market.hourly)
    report.day_ahead_hours = len(market.day_ahead)
    gaps = []
    for hour, price in published.items():
        if hour in market.hourly:
            report.compared += 1
            gap = abs(market.hourly[hour] - price)
            report.agree += gap <= 0.05 + 1e-9
            gaps.append((gap, hour))
    report.worst = [(hour, market.hourly[hour], published[hour])
                    for gap, hour in sorted(gaps, reverse=True)[:show] if gap > 0.05 + 1e-9]
    return report


def _days(first: date, last: date):
    while first <= last:
        yield first
        first += timedelta(days=1)


def _percent(part: int, whole: int) -> str:
    return f"{100 * part / whole:.2f}%" if whole else "n/a"
