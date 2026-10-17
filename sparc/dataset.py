"""Prices and weather as plain CSV files, one per year, so the data can be kept in git.

    comed-data/five_minute/YYYY.csv  start_utc, start_chicago, price_cents
    comed-data/hourly/YYYY.csv       hour_utc, hour_chicago, day_ahead_cents, real_time_cents
    comed-data/weather/YYYY.csv      hour_utc, hour_chicago, temp_c, dew_c, wind_kmh, cloud_pct, temp_fc, ...

Each label is the start of its interval, in UTC and in Chicago time. `export` writes the database
out; `load` reads the files back in, which takes seconds where fetching from ComEd takes hours.
ComEd's 5-minute prices start on 2012-10-29 and its hourly ones on 2007-01-01; the weather
(Open-Meteo, CC BY 4.0) starts in 2024.
"""
from __future__ import annotations

import csv
from collections.abc import Callable, Iterable
from datetime import date, datetime, time, timezone
from pathlib import Path

from .clock import at, local
from .comed import HourRow
from .store import WEATHER_COLUMNS, Store
from .weather import WeatherHour

FOLDER = Path(__file__).resolve().parents[1] / "comed-data"
HEADERS = {
    "five_minute": ("start_utc", "start_chicago", "price_cents"),
    "hourly": ("hour_utc", "hour_chicago", "day_ahead_cents", "real_time_cents"),
    "weather": ("hour_utc", "hour_chicago", *WEATHER_COLUMNS),
}
QUERIES = {
    "five_minute": "SELECT start, price FROM rt_5min WHERE start >= ? AND start < ? ORDER BY start",
    "hourly": "SELECT hour, day_ahead, real_time FROM hourly WHERE hour >= ? AND hour < ? ORDER BY hour",
    "weather": f"SELECT hour, {', '.join(WEATHER_COLUMNS)} FROM weather WHERE hour >= ? AND hour < ? ORDER BY hour",
}
Progress = Callable[[int, int, str], None]


def available(folder: Path = FOLDER) -> bool:
    """Whether the folder holds exported prices."""
    return (folder / "hourly").is_dir() and any((folder / "hourly").glob("*.csv"))


def export(store: Store, folder: Path = FOLDER, years: Iterable[int] | None = None) -> dict[str, int]:
    """Write every stored row (or only those of `years`) to one CSV per kind and Chicago year."""
    bounds = store.db.execute("SELECT MIN(hour), MAX(hour) FROM hourly").fetchone()
    five = store.db.execute("SELECT MIN(start), MAX(start) FROM rt_5min").fetchone()
    stamps = [t for t in (*bounds, *five) if t is not None]
    if not stamps:
        return {}
    wanted = sorted(set(years) if years is not None else range(local(min(stamps)).year, local(max(stamps)).year + 1))
    counts = dict.fromkeys(HEADERS, 0)
    for kind, header in HEADERS.items():
        for year in wanted:
            rows = store.db.execute(QUERIES[kind], (at(date(year, 1, 1), time(0)), at(date(year + 1, 1, 1), time(0))))
            path = folder / kind / f"{year}.csv"
            written = _write(path, header, rows)
            if not written:
                path.unlink(missing_ok=True)
            counts[kind] += written
    return counts


def _write(path: Path, header: tuple[str, ...], rows) -> int:
    count = 0
    temporary = path.with_suffix(".tmp")
    for row in rows:
        if count == 0:
            path.parent.mkdir(parents=True, exist_ok=True)
            handle = temporary.open("w", newline="")
            writer = csv.writer(handle)
            writer.writerow(header)
        stamp, *values = row
        writer.writerow((_utc(stamp), local(stamp).strftime("%Y-%m-%dT%H:%M%z"),
                         *("" if v is None else f"{v:g}" for v in values)))
        count += 1
    if count:
        handle.close()
        temporary.replace(path)
    return count


def load(store: Store, folder: Path = FOLDER, progress: Progress | None = None) -> dict[str, int]:
    """Read every CSV in the folder into the database. Rows already there are updated, not duplicated."""
    files = [(kind, path) for kind in HEADERS for path in sorted((folder / kind).glob("*.csv"))]
    counts = dict.fromkeys(HEADERS, 0)
    for number, (kind, path) in enumerate(files):
        if progress:
            progress(number, len(files), f"Loading {kind.replace('_', '-')} {path.stem}")
        with path.open(newline="") as handle:
            reader = csv.reader(handle)
            if tuple(next(reader, ())) != HEADERS[kind]:
                raise ValueError(f"{path} does not have the expected columns {', '.join(HEADERS[kind])}.")
            rows = [(_epoch(row[0]), *(float(v) if v else None for v in row[2:])) for row in reader if row]
        if kind == "five_minute":
            store.add_five_minute((stamp, price) for stamp, price in rows if price is not None)
        elif kind == "hourly":
            store.add_hourly(HourRow(stamp, day_ahead, real_time) for stamp, day_ahead, real_time in rows)
        else:
            store.add_weather(WeatherHour(*row) for row in rows)
        counts[kind] += len(rows)
    if progress:
        progress(len(files), len(files), "Loaded")
    return counts


def _utc(stamp: int) -> str:
    return datetime.fromtimestamp(stamp, timezone.utc).strftime("%Y-%m-%dT%H:%MZ")


def _epoch(text: str) -> int:
    return int(datetime.fromisoformat(text).timestamp())
