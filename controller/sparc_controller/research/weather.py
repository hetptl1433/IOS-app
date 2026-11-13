"""Chicago weather from Open-Meteo: what happened, and what was forecast a day ahead.

Prices in the ComEd zone follow load, and load follows the weather. A price
forecast may only use weather that was known when the plan was made, so besides
each hour's analysis (the weather model's estimate of what happened) SPARC keeps
the forecast made 24 hours before the hour, from Open-Meteo's Previous Runs API.
That archive starts in January 2024.

Weather data by Open-Meteo.com (CC BY 4.0), free for non-commercial use.
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass
from datetime import date, timedelta
from urllib.parse import urlencode

from .comed import ComEdError, Get

URL = "https://previous-runs-api.open-meteo.com/v1/forecast"
LATITUDE, LONGITUDE = 41.88, -87.63  # downtown Chicago, the middle of ComEd's load
MODEL = "gfs_seamless"  # NOAA's GFS and HRRR: the longest archive of past runs
VARIABLES = {"temp": "temperature_2m", "dew": "dew_point_2m", "wind": "wind_speed_10m", "cloud": "cloud_cover"}
LIMITS = {"temp": (-60, 60), "dew": (-80, 50), "wind": (0, 300), "cloud": (0, 100)}  # °C, °C, km/h, %
CHUNK_DAYS = 120


class WeatherError(RuntimeError):
    """A weather request failed, or the response did not have the expected shape."""


@dataclass(frozen=True)
class WeatherHour:
    hour: int  # hour start, epoch seconds
    temp_c: float | None  # what happened
    dew_c: float | None
    wind_kmh: float | None
    cloud_pct: float | None
    temp_fc: float | None  # forecast made 24 hours before the hour
    dew_fc: float | None
    wind_fc: float | None
    cloud_fc: float | None


def url(first: date, last: date) -> str:
    """Request for every hour of the UTC dates first..last."""
    hourly = [name for variable in VARIABLES.values() for name in (variable, f"{variable}_previous_day1")]
    return f"{URL}?" + urlencode({"latitude": LATITUDE, "longitude": LONGITUDE, "hourly": ",".join(hourly),
                                  "models": MODEL, "start_date": first.isoformat(), "end_date": last.isoformat(),
                                  "timeformat": "unixtime"})


def parse(body: bytes | str) -> list[WeatherHour]:
    try:
        data = json.loads(body)
    except ValueError as exc:
        raise WeatherError(f"Open-Meteo did not return JSON: {exc}") from exc
    if not isinstance(data, dict) or data.get("error"):
        reason = data.get("reason", "unexpected response") if isinstance(data, dict) else data
        raise WeatherError(f"Open-Meteo: {reason}")
    hourly = data.get("hourly")
    if not isinstance(hourly, dict) or not isinstance(hourly.get("time"), list):
        raise WeatherError("Open-Meteo response has no hourly data")
    times = hourly["time"]
    columns = {}
    for key, variable in VARIABLES.items():
        for suffix, name in (("now", variable), ("fc", f"{variable}_previous_day1")):
            values = hourly.get(name)
            if not isinstance(values, list) or len(values) != len(times):
                raise WeatherError(f"Open-Meteo response is missing {name}")
            columns[key, suffix] = [_value(v, key, name) for v in values]
    rows = []
    for i, hour in enumerate(times):
        if not isinstance(hour, int) or hour % 3600:
            raise WeatherError(f"Open-Meteo time {hour!r} is not an hour")
        rows.append(WeatherHour(hour, *(columns[key, "now"][i] for key in VARIABLES),
                                *(columns[key, "fc"][i] for key in VARIABLES)))
    return rows


def _value(value, key: str, name: str) -> float | None:
    if value is None:
        return None
    low, high = LIMITS[key]
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) \
            or not low <= value <= high:
        raise WeatherError(f"Open-Meteo {name} value {value!r} is not plausible")
    return float(value)


def fetch(first: date, last: date, get: Get) -> list[WeatherHour]:
    """Hourly weather for the UTC dates first..last."""
    try:
        return parse(get(url(first, last)))
    except ComEdError as exc:  # the HTTP client is shared with the ComEd feeds
        raise WeatherError(str(exc)) from exc


def chunks(first: date, last: date) -> list[tuple[date, date]]:
    """UTC date ranges covering the Chicago dates first..last (Chicago runs 5-6 hours behind UTC)."""
    spans, start, end = [], first, last + timedelta(days=1)
    while start <= end:
        stop = min(end, start + timedelta(days=CHUNK_DAYS - 1))
        spans.append((start, stop))
        start = stop + timedelta(days=1)
    return spans
