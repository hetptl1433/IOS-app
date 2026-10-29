"""Bundle charging history and missing ComEd hours for the offline history simulator.

Run from the repository root: python3 night-shift/build-history-data.py
Local timestamps are interpreted in America/Chicago. Ambiguous/nonexistent wall
times are retained with an issue flag rather than silently choosing a DST fold.
"""
from __future__ import annotations

import csv
import hashlib
import json
import math
import sys
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from sparc.clock import CT  # noqa: E402
from sparc.market import day_ahead_release  # noqa: E402
from sparc.tariff import capacity_risk  # noqa: E402


def local_timestamp(value: str) -> tuple[float, str | None]:
    wall = datetime.fromisoformat(value)
    first, second = wall.replace(tzinfo=CT, fold=0), wall.replace(tzinfo=CT, fold=1)
    issue = None
    if first.utcoffset() != second.utcoffset():
        issue = "Ambiguous or nonexistent Chicago time; the CSV has no UTC offset."
    return round(first.timestamp(), 3), issue


def main() -> None:
    source = ROOT / "charging-history-data/charging_sessions_tm1.csv"
    rows = []
    with source.open(newline="", encoding="utf-8-sig") as stream:
        for item in csv.DictReader(stream):
            start, start_issue = local_timestamp(item["start_local"])
            end, end_issue = local_timestamp(item["end_local"])
            row = {
                "id": int(item["id"]), "startLocal": item["start_local"],
                "endLocal": item["end_local"], "start": start, "end": end,
                "gridKwh": float(item["kwh_used"]), "batteryKwh": float(item["kwh_added"]),
                "startSoc": float(item["start_battery_level"]),
                "endSoc": float(item["end_battery_level"]),
                "powerKw": float(item["max_kw"]), "loggedCost": float(item["cost"]),
                "location": item["location"], "dcFast": item["dc_fast"].lower() == "t",
                "issue": start_issue or end_issue,
            }
            numeric = (row[key] for key in ("gridKwh", "batteryKwh", "powerKw", "loggedCost", "startSoc", "endSoc"))
            if not all(math.isfinite(n) for n in numeric) or end <= start or row["gridKwh"] <= 0:
                row["issue"] = "Invalid duration, energy, or numeric value in this record."
            rows.append(row)
    rows.sort(key=lambda row: (row["start"], row["id"]))
    if len({row["id"] for row in rows}) != len(rows):
        raise ValueError("Charging session IDs must be unique.")

    original = (ROOT / "night-shift/dist/simulation-data.js").read_text()
    market = json.loads(original.split("=", 1)[1].rstrip(";\n "))
    existing = {hour[0] for hour in market["hours"]}
    first = min(row["start"] for row in rows) - 2 * 86400
    last = max(row["end"] for row in rows) + 2 * 86400
    hours = []
    files = []
    for year in range(datetime.fromtimestamp(first, CT).year, datetime.fromtimestamp(last, CT).year + 1):
        price_path = ROOT / f"comed-data/hourly/{year}.csv"
        files.append(str(price_path.relative_to(ROOT)))
        with price_path.open(newline="") as stream:
            for item in csv.DictReader(stream):
                epoch = int(datetime.fromisoformat(item["hour_utc"].replace("Z", "+00:00")).timestamp())
                if epoch in existing or not first <= epoch <= last:
                    continue
                wall = datetime.fromtimestamp(epoch, CT)
                price = lambda key: float(item[key]) if item[key] else None
                hours.append([epoch, wall.date().isoformat(), wall.hour,
                              price("day_ahead_cents"), price("real_time_cents"),
                              int(capacity_risk(epoch)), day_ahead_release(epoch)])
    bundle = {
        "version": 1, "source": str(source.relative_to(ROOT)),
        "sourceSha256": hashlib.sha256(source.read_bytes()).hexdigest(),
        "timezone": "America/Chicago", "priceFiles": files,
        "rows": rows, "hours": sorted(hours),
    }
    target = ROOT / "night-shift/dist/charging-history-data.js"
    target.write_text("window.SPARC_CHARGING_HISTORY=" + json.dumps(bundle, ensure_ascii=True, allow_nan=False, separators=(",", ":")) + ";\n")
    print(f"Bundled {len(rows):,} sessions and {len(hours):,} additional price hours.")
    print(f"Timestamp/data issues: {sum(bool(row['issue']) for row in rows)}; output: {target.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
