"""The parts of a ComEd Hourly Pricing bill that depend on when you charge.

Supply is the real-time hourly price. Delivery here is the Distribution Facility
Charge for a single-family home without electric heat (CUB fact sheet, March 2026).
Other per-kWh charges are flat, so they change the bill but not the best time to charge.
"""
from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache

from .clock import local


@dataclass(frozen=True)
class Delivery:
    name: str
    bands: tuple[tuple[int, float], ...]  # (first Chicago hour of the band, cents/kWh), from hour 0

    def cents(self, hour: int) -> float:
        wall_hour = _wall_hour(hour)
        rate = self.bands[0][1]
        for first, cents in self.bands:
            if wall_hour >= first:
                rate = cents
        return rate


STANDARD = Delivery("Standard delivery", ((0, 6.228),))
TIME_OF_DAY = Delivery("Delivery Time-of-Day",
                       ((0, 2.984), (6, 4.009), (13, 10.712), (19, 3.747), (21, 2.984)))


@lru_cache(maxsize=None)
def capacity_risk(hour: int) -> bool:
    """Summer weekday afternoons.

    Next year's capacity charge is set by your demand in the ten summer hours when
    PJM and ComEd peak, historically between noon and 6 p.m. on weekdays.
    """
    t = local(hour)
    return 6 <= t.month <= 9 and t.weekday() < 5 and 12 <= t.hour < 19


@lru_cache(maxsize=None)
def _wall_hour(hour: int) -> int:
    return local(hour).hour
