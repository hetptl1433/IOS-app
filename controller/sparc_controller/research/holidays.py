"""Holidays that change electricity demand in PJM.

These are NERC's six off-peak holidays, which PJM uses for its peak and
off-peak products: a holiday on a Sunday moves to Monday, one on a Saturday
stays put. Also the day after Thanksgiving, which PJM itself observes and
which has holiday-like demand.
"""
from __future__ import annotations

from datetime import date, timedelta
from functools import lru_cache


@lru_cache(maxsize=None)
def holidays(year: int) -> frozenset[date]:
    thanksgiving = _nth_weekday(year, 11, 3, 4)
    days = {_observed(date(year, 1, 1)), _last_weekday(year, 5, 0), _observed(date(year, 7, 4)),
            _nth_weekday(year, 9, 0, 1), thanksgiving, thanksgiving + timedelta(days=1),
            _observed(date(year, 12, 25))}
    return frozenset(days)


def is_holiday(day: date) -> bool:
    return day in holidays(day.year)


def _observed(day: date) -> date:
    return day + timedelta(days=1) if day.weekday() == 6 else day


def _nth_weekday(year: int, month: int, weekday: int, n: int) -> date:
    first = date(year, month, 1)
    return first + timedelta(days=(weekday - first.weekday()) % 7 + 7 * (n - 1))


def _last_weekday(year: int, month: int, weekday: int) -> date:
    following = date(year + month // 12, month % 12 + 1, 1)
    last = following - timedelta(days=1)
    return last - timedelta(days=(last.weekday() - weekday) % 7)
