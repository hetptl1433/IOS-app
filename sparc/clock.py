"""Time conventions.

Internally every timestamp is an integer number of seconds since the Unix epoch.
Wall-clock rules (tariff periods, publication times, sessions) use Chicago time.
Chicago's UTC offset is always a whole number of hours, so local hour boundaries
and UTC hour boundaries coincide.
"""
from __future__ import annotations

from datetime import date, datetime, time
from zoneinfo import ZoneInfo

CT = ZoneInfo("America/Chicago")
SLOT = 300  # seconds in one 5-minute price interval
HOUR = 3600
DAY = 24 * HOUR


def hour_start(t: int) -> int:
    return t - t % HOUR


def local(t: int) -> datetime:
    return datetime.fromtimestamp(t, CT)


def at(day: date, wall: time) -> int:
    """Epoch seconds for a Chicago wall-clock time (first occurrence when ambiguous)."""
    return int(datetime.combine(day, wall, tzinfo=CT).timestamp())
