"""Record live prices with the time they were first seen.

Historical feeds cannot say when a price became available. Recording that on the
Pi from day one is what makes later backtests trustworthy.
"""
from __future__ import annotations

import time as _time
from collections.abc import Callable
from datetime import date, time, timedelta

from . import comed
from .clock import SLOT, at, local
from .store import Store

POLL_OFFSET = 150  # seconds after each 5-minute boundary; prices appear about 2 minutes after
DAY_AHEAD_FROM = time(16, 35)


def collect(store: Store, get: comed.Get, *, clock: Callable[[], float] = _time.time,
            sleep: Callable[[float], None] = _time.sleep, log: Callable[[str], None] = print,
            cycles: int | None = None) -> None:
    done = 0
    while cycles is None or done < cycles:
        now = int(clock())
        try:
            store.add_five_minute(comed.latest_five_minute_prices(get), seen_at=now)
            tomorrow = local(now).date() + timedelta(days=1)
            if local(now).time() >= DAY_AHEAD_FROM and not has_day_ahead(store, tomorrow):
                # Tomorrow's last hour is the first row of the following day's table.
                for day in (tomorrow, tomorrow + timedelta(days=1)):
                    store.add_hourly(comed.hourly_table(day, get), seen_at=now)
        except comed.ComEdError as exc:
            log(f"{local(now):%Y-%m-%d %H:%M} {exc}")
        done += 1
        if cycles is None or done < cycles:
            sleep(seconds_to_next_poll(clock()))


def has_day_ahead(store: Store, day: date) -> bool:
    start, end = at(day, time(0)), at(day + timedelta(days=1), time(0))
    return len(store.day_ahead(start, end)) == (end - start) // 3600


def seconds_to_next_poll(now: float) -> float:
    next_poll = (int(now - POLL_OFFSET) // SLOT + 1) * SLOT + POLL_OFFSET
    return next_poll - now
