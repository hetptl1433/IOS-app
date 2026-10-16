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


ROUNDING = 0.15  # both series are rounded to 0.1 cent, so their difference can reach ~0.1


@dataclass
class Coverage:
    first: date
    last: date
    hours: int = 0
    published: int = 0  # billed at ComEd's settled hourly price
    complete: int = 0  # all twelve 5-minute readings
    partial: int = 0  # unpublished, billed from 10-11 readings
    unpriced: int = 0
    day_ahead: int = 0
    compared: int = 0  # hours with both a published price and all twelve readings
    revised: list[tuple[int, float, float]] = field(default_factory=list)  # hour, 5-min mean, published

    def __str__(self) -> str:
        lines = [f"{self.first} to {self.last}: {self.hours} hours",
                 f"  billed at ComEd's published hourly price: {self.published} "
                 f"({_percent(self.published, self.hours)})",
                 f"  billed from the 5-minute feed only:       {self.hours - self.published - self.unpriced}"
                 f" ({self.partial} of them from 10-11 readings)",
                 f"  no price:                                 {self.unpriced}",
                 f"  all twelve 5-minute readings:             {self.complete} "
                 f"({_percent(self.complete, self.hours)})",
                 f"  day-ahead prices:                         {self.day_ahead} "
                 f"({_percent(self.day_ahead, self.hours)})",
                 f"  5-minute mean differs from the published price by more than rounding: "
                 f"{len(self.revised)} of {self.compared} hours"]
        for hour, mean, published in self.revised[:8]:
            lines.append(f"    {local(hour):%Y-%m-%d %H:%M %Z}: 5-minute mean {mean:.2f}, published {published}")
        return "\n".join(lines)


def check(store: Store, first: date, last: date) -> Coverage:
    start, end = at(first, time(0)), at(last + timedelta(days=1), time(0))
    published = store.published_real_time(start, end)
    market = Market(store.five_minute(start, end), store.day_ahead(start, end), published)
    report = Coverage(first, last, hours=(end - start) // HOUR)
    report.published = len(published)
    report.complete = len(market.complete)
    report.partial = len(market.partial)
    report.unpriced = report.hours - len(market.hourly)
    report.day_ahead = len(market.day_ahead)
    compared = [hour for hour in published if hour in market.complete]
    report.compared = len(compared)
    report.revised = sorted(((hour, market.means[hour], published[hour]) for hour in compared
                             if abs(market.means[hour] - published[hour]) > ROUNDING),
                            key=lambda row: -abs(row[1] - row[2]))
    return report


def _days(first: date, last: date):
    while first <= last:
        yield first
        first += timedelta(days=1)


def _percent(part: int, whole: int) -> str:
    return f"{100 * part / whole:.2f}%" if whole else "n/a"
