"""Price history, and what a controller could have known at any moment."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import time, timedelta
from functools import cached_property, lru_cache

from .clock import DAY, SLOT, at, hour_start, local

# ComEd: 5-minute prices "become available approximately 7 minutes after the start of the hour",
# i.e. about 2 minutes after each interval ends.
RT_DELAY = 7 * 60
# ComEd: tomorrow's day-ahead prices "become available each day at approximately 4:30 p.m. CT".
# Half an hour of margin.
DA_RELEASE = time(17, 0)
# ComEd's 5-minute feed has gaps. Without a published price, an hour missing one or two readings
# is billed at the mean of the rest (off by at most 1/12 of a missing reading's deviation).
MIN_INTERVALS = 10


@dataclass
class Market:
    """Prices as a controller saw them live, and as they were billed.

    The 5-minute feed keeps prices as first posted. ComEd's published hourly price is the
    settled one: across 2025-2026 it differs from the 5-minute mean by more than a cent in
    only 12 of 15,020 hours, each a posted spike that was later revised down.
    So decisions use the 5-minute feed and billing uses the published price where it exists.
    """

    five_minute: dict[int, float]  # interval start -> cents/kWh, as first posted
    day_ahead: dict[int, float]  # hour start -> cents/kWh
    published: dict[int, float] = field(default_factory=dict)  # ComEd's settled hourly price
    hourly: dict[int, float] = field(init=False)  # the billed price of each hour
    means: dict[int, float] = field(init=False)  # mean of the hour's 5-minute readings
    complete: set[int] = field(init=False)  # hours with all twelve readings
    partial: set[int] = field(init=False)  # hours billed from 10-11 readings

    def __post_init__(self):
        totals: dict[int, float] = {}
        counts: dict[int, int] = {}
        for start, price in self.five_minute.items():
            hour = hour_start(start)
            totals[hour] = totals.get(hour, 0.0) + price
            counts[hour] = counts.get(hour, 0) + 1
        self.complete = {hour for hour, n in counts.items() if n == 12}
        self.means = {hour: totals[hour] / n for hour, n in counts.items() if n >= MIN_INTERVALS}
        self.hourly = {**self.means, **self.published}
        self.partial = {hour for hour in self.means
                        if hour not in self.complete and hour not in self.published}

    @cached_property
    def deviations(self) -> Deviations:
        return Deviations(self.five_minute, self.day_ahead)

    def known_day_ahead(self, hour: int, now: int) -> float | None:
        """The hour's day-ahead price if published by `now`, else the same hour one day earlier."""
        for candidate in (hour, hour - DAY):
            if candidate in self.day_ahead and day_ahead_release(candidate) <= now:
                return self.day_ahead[candidate]
        return None


class Deviations:
    """Each posted 5-minute price minus its hour's day-ahead price.

    Running totals over a regular 5-minute grid make the mean over any run of
    intervals two lookups.
    """

    def __init__(self, five_minute: dict[int, float], day_ahead: dict[int, float]):
        self.values = {t: price - day_ahead[hour_start(t)] for t, price in five_minute.items()
                       if hour_start(t) in day_ahead}
        self.origin = min(self.values, default=0)
        size = (max(self.values) - self.origin) // SLOT + 1 if self.values else 0
        self.sums, self.counts, self.latest = [0.0] * (size + 1), [0] * (size + 1), [-1] * size
        last = -1
        for i in range(size):
            value = self.values.get(self.origin + i * SLOT)
            if value is not None:
                last = i
            self.latest[i] = last
            self.sums[i + 1] = self.sums[i] + (value if value is not None else 0.0)
            self.counts[i + 1] = self.counts[i] + (value is not None)

    def window(self, newest: int, intervals: int) -> tuple[float, int]:
        """Sum and number of the deviations in the `intervals` intervals up to and including `newest`."""
        i = (newest - self.origin) // SLOT
        high, low = min(i + 1, len(self.latest)), max(i + 1 - intervals, 0)
        if high <= low:
            return 0.0, 0
        return self.sums[high] - self.sums[low], self.counts[high] - self.counts[low]

    def last(self, newest: int, intervals: int) -> float | None:
        """The most recent deviation in the `intervals` intervals up to and including `newest`."""
        i = (newest - self.origin) // SLOT
        j = self.latest[min(i, len(self.latest) - 1)] if i >= 0 and self.latest else -1
        return self.values[self.origin + j * SLOT] if j >= 0 and j > i - intervals else None


def latest_visible_interval(now: int) -> int:
    """Start of the newest 5-minute interval whose price has been published by `now`."""
    return (now - RT_DELAY) // SLOT * SLOT


@lru_cache(maxsize=None)
def day_ahead_release(hour: int) -> int:
    """When the day-ahead price for `hour` is published: the afternoon before its operating day."""
    operating_day = local(hour).date()
    return at(operating_day - timedelta(days=1), DA_RELEASE)
