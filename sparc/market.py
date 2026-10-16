"""Price history, and what a controller could have known at any moment."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import time, timedelta
from functools import lru_cache

from .clock import DAY, SLOT, at, hour_start, local

# ComEd: 5-minute prices "become available approximately 7 minutes after the start of the hour",
# i.e. about 2 minutes after each interval ends.
RT_DELAY = 7 * 60
# ComEd: tomorrow's day-ahead prices "become available each day at approximately 4:30 p.m. CT".
# Half an hour of margin.
DA_RELEASE = time(17, 0)


@dataclass
class Market:
    five_minute: dict[int, float]  # interval start -> cents/kWh
    day_ahead: dict[int, float]  # hour start -> cents/kWh
    hourly: dict[int, float] = field(init=False)  # billed price: mean of all 12 intervals

    def __post_init__(self):
        totals: dict[int, float] = {}
        counts: dict[int, int] = {}
        for start, price in self.five_minute.items():
            hour = hour_start(start)
            totals[hour] = totals.get(hour, 0.0) + price
            counts[hour] = counts.get(hour, 0) + 1
        self.hourly = {hour: total / 12 for hour, total in totals.items() if counts[hour] == 12}

    def known_day_ahead(self, hour: int, now: int) -> float | None:
        """The hour's day-ahead price if published by `now`, else the same hour one day earlier."""
        for candidate in (hour, hour - DAY):
            if candidate in self.day_ahead and day_ahead_release(candidate) <= now:
                return self.day_ahead[candidate]
        return None


def latest_visible_interval(now: int) -> int:
    """Start of the newest 5-minute interval whose price has been published by `now`."""
    return (now - RT_DELAY) // SLOT * SLOT


@lru_cache(maxsize=None)
def day_ahead_release(hour: int) -> int:
    """When the day-ahead price for `hour` is published: the afternoon before its operating day."""
    operating_day = local(hour).date()
    return at(operating_day - timedelta(days=1), DA_RELEASE)
