"""Download the ComEd prices the replays need that comed-data/ does not have. Already saved in data/.

    python3 results/sparc-live-replay/fetch_prices.py

* data/comed-2026-10-27-to-11-13.json: 5-minute prices and the hourly table (day-ahead and settled)
  for the 15-night Cybertruck replay.
* data/comed-hourly-2022-05-30-to-2024-01-01.json: the hourly table for 2022-2023, which comed-data/
  lacks (its hourly files skip 2015-2023).

Tables are read with the Pi's own parser, so fall-back DST days fail exactly as they do on the Pi.
"""
import json
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1] / "controller"))
import sparc_controller.research.comed as comed

comed.USER_AGENT = "price-research-script/1.0"
get = comed.Fetcher(min_interval=1.5)


def tables(first, last):
    hours, errors = {}, {}
    while first <= last:
        try:
            for row in comed.hourly_table(first, get):
                hours[str(row.hour)] = {"da": row.day_ahead, "rt": row.real_time}
        except comed.ComEdError as exc:
            errors[first.isoformat()] = str(exc)
        first += timedelta(days=1)
    return hours, errors


five, day = {}, date(2026, 10, 27)
while day <= date(2026, 11, 13):
    start = datetime(day.year, day.month, day.day)
    for stamp, price in comed.five_minute_prices(start, start + timedelta(hours=23, minutes=55), get):
        five[str(stamp)] = price
    day += timedelta(days=1)
hourly, errors = tables(date(2026, 10, 27), date(2026, 11, 13))
json.dump({"five": five, "hourly": hourly, "errors": errors}, open(HERE / "data/comed-2026-10-27-to-11-13.json", "w"))

hourly, errors = tables(date(2022, 5, 30), date(2024, 1, 1))
json.dump({"hourly": hourly, "errors": errors}, open(HERE / "data/comed-hourly-2022-05-30-to-2024-01-01.json", "w"))
print("saved; tables that failed to parse:", errors)
