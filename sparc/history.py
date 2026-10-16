"""Backfill ComEd price history, and check what was stored."""
from __future__ import annotations

import time as _time
from bisect import bisect_left
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta

from . import comed, weather
from .backtest import Cancelled
from .clock import HOUR, at, local
from .market import Market
from .store import Store


def backfill(store: Store, first: date, last: date, get: comed.Get, *,
             progress: Callable[[int, int, str], None] | None = None,
             should_stop: Callable[[], bool] | None = None,
             log: Callable[[str], None] = print, today: date | None = None) -> list[str]:
    """Fetch 5-minute prices a week at a time and hourly tables a day at a time.

    Finished requests are recorded, so an interrupted backfill resumes where it
    stopped. Requests reaching today or later are not recorded, because their prices
    are still coming in. Returns the dates whose hourly table could not be read.
    """
    today = today or local(int(_time.time())).date()
    chunks = [c for c in comed.day_chunks(first, last)
              if f"{c[0]}/{c[1]}" not in store.fetched("five-minute")]
    # The table for date D starts with the hour before midnight, so one extra day
    # covers the last evening.
    done_days = store.fetched("hourly")
    days = [d for d in _days(first, last + timedelta(days=1)) if d.isoformat() not in done_days]
    total, finished, failed = len(chunks) + len(days), 0, []

    def step(message: str) -> None:
        nonlocal finished
        finished += 1
        if progress:
            progress(finished, total, message)
        if should_stop and should_stop():
            raise Cancelled

    for start, end in chunks:
        prices = comed.five_minute_prices(datetime.combine(start, time(0)),
                                          datetime.combine(end, time(23, 55)), get)
        store.add_five_minute(prices)
        if end < today:
            store.mark_fetched("five-minute", f"{start}/{end}")
        step(f"5-minute prices {start} to {end}")
    for day in days:
        try:
            store.add_hourly(comed.hourly_table(day, get))
        except comed.ComEdError as exc:
            failed.append(day.isoformat())
            log(f"hourly table {day}: {exc}")
        else:
            if day < today:
                store.mark_fetched("hourly", day.isoformat())
        step(f"hourly table {day}")
    return failed


def backfill_weather(store: Store, first: date, last: date, get: comed.Get, *,
                     progress: Callable[[int, int, str], None] | None = None,
                     should_stop: Callable[[], bool] | None = None, today: date | None = None) -> None:
    """Fetch Chicago weather a few months at a time, skipping finished requests like `backfill`."""
    today = today or local(int(_time.time())).date()
    done = store.fetched("weather")
    spans = [span for span in weather.chunks(first, last) if f"{span[0]}/{span[1]}" not in done]
    for number, (start, end) in enumerate(spans, 1):
        store.add_weather(weather.fetch(start, end, get))
        if end < today - timedelta(days=1):  # recent hours are still forecasts, so fetch them again later
            store.mark_fetched("weather", f"{start}/{end}")
        if progress:
            progress(number, len(spans), f"weather {start} to {end}")
        if should_stop and should_stop():
            raise Cancelled


def stored_range(store: Store) -> tuple[date, date] | None:
    """First and last Chicago dates with 5-minute prices."""
    first, last = store.db.execute("SELECT MIN(start), MAX(start) FROM rt_5min").fetchone()
    return (local(first).date(), local(last).date()) if first is not None else None


ROUNDING = 0.15  # both series are rounded to 0.1 cent, so their difference can reach ~0.1


@dataclass(frozen=True)
class Month:
    month: str  # YYYY-MM
    hours: int
    priced: int  # hours with a billing price
    day_ahead: int
    weather: int


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
    weather: int = 0  # hours with a day-ahead weather forecast
    months: list[Month] = field(default_factory=list)

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
                 f"  weather forecasts:                        {self.weather} "
                 f"({_percent(self.weather, self.hours)})",
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
    forecast = sorted(hour for hour, row in store.weather(start, end).items() if row.temp_fc is not None)
    report.weather = len(forecast)
    report.months = _months(first, last, market, forecast)
    return report


def _months(first: date, last: date, market: Market, forecast: list[int]) -> list[Month]:
    priced, ahead = sorted(market.hourly), sorted(market.day_ahead)
    months = []
    month = first.replace(day=1)
    while month <= last:
        following = (month + timedelta(days=32)).replace(day=1)
        start = at(max(month, first), time(0))
        end = at(min(following, last + timedelta(days=1)), time(0))
        months.append(Month(f"{month:%Y-%m}", (end - start) // HOUR,
                            bisect_left(priced, end) - bisect_left(priced, start),
                            bisect_left(ahead, end) - bisect_left(ahead, start),
                            bisect_left(forecast, end) - bisect_left(forecast, start)))
        month = following
    return months


def _days(first: date, last: date):
    while first <= last:
        yield first
        first += timedelta(days=1)


def _percent(part: int, whole: int) -> str:
    return f"{100 * part / whole:.2f}%" if whole else "n/a"
