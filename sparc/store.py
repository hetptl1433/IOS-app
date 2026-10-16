"""SQLite storage for prices."""
from __future__ import annotations

import sqlite3
import time
from collections.abc import Iterable
from pathlib import Path

from .comed import HourRow
from .weather import WeatherHour

SCHEMA = """
CREATE TABLE IF NOT EXISTS rt_5min (
    start   INTEGER PRIMARY KEY,  -- interval start, epoch seconds
    price   REAL NOT NULL,        -- cents/kWh
    seen_at INTEGER               -- when the live collector first saw it; NULL if backfilled
);
CREATE TABLE IF NOT EXISTS hourly (
    hour       INTEGER PRIMARY KEY,  -- hour start, epoch seconds
    day_ahead  REAL,                 -- cents/kWh
    real_time  REAL,                 -- ComEd's published hourly price, rounded to 0.1 cent
    da_seen_at INTEGER               -- when the live collector first saw the day-ahead price
);
CREATE TABLE IF NOT EXISTS weather (
    hour      INTEGER PRIMARY KEY,  -- hour start, epoch seconds
    temp_c    REAL,                 -- Chicago, what happened (weather-model analysis)
    dew_c     REAL,
    wind_kmh  REAL,
    cloud_pct REAL,
    temp_fc   REAL,                 -- the forecast made 24 hours before the hour
    dew_fc    REAL,
    wind_fc   REAL,
    cloud_fc  REAL
);
CREATE TABLE IF NOT EXISTS fetched (
    kind TEXT NOT NULL,
    key  TEXT NOT NULL,
    at   INTEGER NOT NULL,
    PRIMARY KEY (kind, key)
);
"""


WEATHER_COLUMNS = ("temp_c", "dew_c", "wind_kmh", "cloud_pct", "temp_fc", "dew_fc", "wind_fc", "cloud_fc")


class Store:
    def __init__(self, path: str | Path):
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.executescript(SCHEMA)

    def close(self) -> None:
        self.db.close()

    def add_five_minute(self, prices: Iterable[tuple[int, float]], seen_at: int | None = None) -> None:
        with self.db:
            self.db.executemany(
                "INSERT INTO rt_5min (start, price, seen_at) VALUES (?, ?, ?) "
                "ON CONFLICT (start) DO UPDATE SET price = excluded.price, "
                "seen_at = COALESCE(rt_5min.seen_at, excluded.seen_at)",
                ((start, price, seen_at) for start, price in prices))

    def add_hourly(self, rows: Iterable[HourRow], seen_at: int | None = None) -> None:
        with self.db:
            self.db.executemany(
                "INSERT INTO hourly (hour, day_ahead, real_time, da_seen_at) VALUES (?, ?, ?, ?) "
                "ON CONFLICT (hour) DO UPDATE SET "
                "day_ahead = COALESCE(excluded.day_ahead, hourly.day_ahead), "
                "real_time = COALESCE(excluded.real_time, hourly.real_time), "
                "da_seen_at = COALESCE(hourly.da_seen_at, excluded.da_seen_at)",
                ((row.hour, row.day_ahead, row.real_time,
                  seen_at if row.day_ahead is not None else None) for row in rows))

    def add_weather(self, rows: Iterable[WeatherHour]) -> None:
        columns = WEATHER_COLUMNS
        with self.db:
            self.db.executemany(
                f"INSERT INTO weather (hour, {', '.join(columns)}) VALUES ({', '.join('?' * (len(columns) + 1))}) "
                "ON CONFLICT (hour) DO UPDATE SET "
                + ", ".join(f"{c} = COALESCE(excluded.{c}, weather.{c})" for c in columns),
                ((row.hour, *(getattr(row, c) for c in columns)) for row in rows))

    def weather(self, start: int, end: int) -> dict[int, WeatherHour]:
        return {row[0]: WeatherHour(*row) for row in self.db.execute(
            f"SELECT hour, {', '.join(WEATHER_COLUMNS)} FROM weather WHERE hour >= ? AND hour < ?", (start, end))}

    def mark_fetched(self, kind: str, key: str) -> None:
        with self.db:
            self.db.execute("INSERT OR REPLACE INTO fetched (kind, key, at) VALUES (?, ?, ?)",
                            (kind, key, int(time.time())))

    def fetched(self, kind: str) -> set[str]:
        return {key for (key,) in self.db.execute("SELECT key FROM fetched WHERE kind = ?", (kind,))}

    def five_minute(self, start: int, end: int) -> dict[int, float]:
        return dict(self.db.execute(
            "SELECT start, price FROM rt_5min WHERE start >= ? AND start < ?", (start, end)))

    def day_ahead(self, start: int, end: int) -> dict[int, float]:
        return dict(self.db.execute(
            "SELECT hour, day_ahead FROM hourly WHERE hour >= ? AND hour < ? "
            "AND day_ahead IS NOT NULL", (start, end)))

    def published_real_time(self, start: int, end: int) -> dict[int, float]:
        return dict(self.db.execute(
            "SELECT hour, real_time FROM hourly WHERE hour >= ? AND hour < ? "
            "AND real_time IS NOT NULL", (start, end)))
