"""Export every stored ComEd price to CSV, one file per year, in comed-data/ at the repository root.

    comed-data/five_minute/YYYY.csv  interval start, cents/kWh (the 5-minute feed, from 2012-10-29)
    comed-data/hourly/YYYY.csv       hour start, day-ahead and real-time cents/kWh (from 2007-01-01)

Times are given in UTC and Chicago time; each label is the start of its interval. The real-time
hourly price is the one ComEd bills: the average of the hour's twelve 5-minute prices. comed-data/
is git-ignored, because ComEd's API terms restrict redistributing its data.

    python experiments/data/export_comed.py
"""
import csv
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from shared import ROOT, database  # noqa: E402
from sparc.clock import local  # noqa: E402

OUT = ROOT / "comed-data"


def stamp(seconds):
    return (datetime.fromtimestamp(seconds, timezone.utc).strftime("%Y-%m-%dT%H:%MZ"),
            local(seconds).strftime("%Y-%m-%dT%H:%M%z"))


def cents(value):
    return "" if value is None else f"{value:g}"


def export(name, query, header, row):
    """Write one CSV per Chicago calendar year; return (rows, files, first, last)."""
    folder = OUT / name
    folder.mkdir(parents=True, exist_ok=True)
    count, files, first, last, year, handle, writer = 0, 0, None, None, None, None, None
    for record in db.execute(query):
        when = local(record[0])
        if when.year != year:
            if handle:
                handle.close()
            year = when.year
            handle = (folder / f"{year}.csv").open("w", newline="")
            writer = csv.writer(handle)
            writer.writerow(header)
            files += 1
        writer.writerow(row(record))
        count += 1
        first = first or when
        last = when
    if handle:
        handle.close()
    return count, files, first, last


db = sqlite3.connect(database())
five = export("five_minute", "SELECT start, price FROM rt_5min ORDER BY start",
              ("start_utc", "start_chicago", "price_cents"),
              lambda r: (*stamp(r[0]), cents(r[1])))
hourly = export("hourly", "SELECT hour, day_ahead, real_time FROM hourly ORDER BY hour",
                ("hour_utc", "hour_chicago", "day_ahead_cents", "real_time_cents"),
                lambda r: (*stamp(r[0]), cents(r[1]), cents(r[2])))
for name, (count, files, first, last) in (("5-minute prices", five), ("hourly prices", hourly)):
    if count:
        print(f"{name}: {count:,} rows, {first:%Y-%m-%d} to {last:%Y-%m-%d}, {files} files")
print(f"written to {OUT}")
