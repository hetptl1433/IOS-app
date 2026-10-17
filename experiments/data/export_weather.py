"""Export the Chicago weather the forecasts use to CSV: experiments/data/chicago_weather.csv.

Hourly, downtown Chicago, from Open-Meteo's Previous Runs API (gfs_seamless): what happened
(temp_c, dew_c, wind_kmh, cloud_pct) and the forecast made 24 hours before the hour (*_fc).
Weather data by Open-Meteo.com, licensed CC BY 4.0 (https://open-meteo.com/en/licence).

    python experiments/data/export_weather.py
"""
import csv
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from shared import database  # noqa: E402
from sparc.clock import local  # noqa: E402

COLUMNS = ("temp_c", "dew_c", "wind_kmh", "cloud_pct", "temp_fc", "dew_fc", "wind_fc", "cloud_fc")
out = Path(__file__).parent / "chicago_weather.csv"
db = sqlite3.connect(database())
rows = db.execute(f"SELECT hour, {', '.join(COLUMNS)} FROM weather ORDER BY hour").fetchall()
with out.open("w", newline="") as handle:
    writer = csv.writer(handle)
    writer.writerow(("hour_utc", "hour_chicago", *COLUMNS))
    for hour, *values in rows:
        writer.writerow((datetime.fromtimestamp(hour, timezone.utc).strftime("%Y-%m-%dT%H:%MZ"),
                         local(hour).strftime("%Y-%m-%dT%H:%M%z"),
                         *("" if v is None else f"{v:g}" for v in values)))
print(f"{len(rows):,} hours written to {out}")
