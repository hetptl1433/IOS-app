"""Prepare the four Norwegian residential CSVs for the offline ComEd scenario.

Run from the repository root with Python 3.11+:
    python3 night-shift/build-residential-data.py
The originals are retained. NA is missing, decimal commas are numeric, and the
large hourly file is streamed for reconciliation rather than shipped to browsers.
"""
from __future__ import annotations

import csv
import hashlib
import json
import math
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
OSLO = ZoneInfo("Europe/Oslo")
FILES = ("Dataset1_charging_reports (1).csv", "Dataset2_user_predictions.csv",
         "Dataset3_session_predictions.csv", "Dataset4_hourly_predictions.csv")
COLUMNS = ("id", "userId", "site", "startLocal", "endLocal", "sourceStart", "sourceEnd",
           "gridKwh", "powerKw", "capacityKwh", "predictedStartSoc", "predictedSocDiff",
           "chargingHours", "idleHours", "hourlyKwh", "hourlyRows", "issue", "qualityFlags")


def number(value: str | None) -> float | None:
    if value is None or value.strip() in ("", "NA"):
        return None
    result = float(value.replace(",", "."))
    if not math.isfinite(result):
        raise ValueError("Nonfinite CSV value")
    return result


def records(path: Path):
    with path.open(encoding="utf-8-sig", newline="") as stream:
        yield from csv.DictReader(stream, delimiter=";")


def unique(rows, key):
    result = {}
    for row in rows:
        if row[key] in result:
            raise ValueError(f"Duplicate {key}: {row[key]}")
        result[row[key]] = row
    return result


def timestamp(value: str) -> tuple[int | None, str | None]:
    if value in ("", "NA"):
        return None, "Missing plug-out time; connection window is unknown."
    wall = datetime.fromisoformat(value)
    first, second = wall.replace(tzinfo=OSLO, fold=0), wall.replace(tzinfo=OSLO, fold=1)
    if first.utcoffset() != second.utcoffset():
        return int(first.timestamp()), "Ambiguous or nonexistent Europe/Oslo timestamp."
    return int(first.timestamp()), None


def prepare(root: Path = ROOT) -> dict:
    reports = unique(records(root / FILES[0]), "session_id")
    users = unique(records(root / FILES[1]), "user_id")
    sessions = unique(records(root / FILES[2]), "session_id")
    for sid, prediction in sessions.items():
        if sid not in reports or prediction["user_id"] != reports[sid]["user_id"]:
            raise ValueError(f"Session prediction has no matching report/user: {sid}")

    hourly = defaultdict(lambda: [0.0, 0])
    hourly_counts = Counter()
    for row in records(root / FILES[3]):
        hourly_counts["rows"] += 1
        if row["session_id"] in ("", "NA"):
            hourly_counts["unkeyedRows"] += 1
            continue
        sid = row["session_id"]
        if sid not in reports or row["user_id"] != reports[sid]["user_id"]:
            raise ValueError(f"Hourly prediction has no matching report/user: {sid}")
        energy = number(row["energy_charged_i"])
        # NA charging energy denotes an idle hour in this supplied model. It is
        # not a missing session's measured energy and is never a planner input.
        if energy is not None:
            hourly[sid][0] += energy
        hourly[sid][1] += 1

    rows, issues, flags = [], Counter(), Counter()
    for sid, item in reports.items():
        if item["user_id"] not in users:
            raise ValueError(f"Report has no user metadata: {sid}")
        user = users[item["user_id"]]
        prediction = sessions.get(sid, {})
        start, start_issue = timestamp(item["plugin_time"])
        end, end_issue = timestamp(item["plugout_time"])
        energy = number(item["energy_session"])
        power = number(user["charging_power"])
        capacity = number(user["battery_capacity"])
        issue = start_issue or end_issue
        quality = []
        if energy is None or energy <= 0 or (end is not None and end <= start):
            issue = "Invalid session energy or connection duration."
        duration = (end - start) / 3600 if end is not None else None
        reported_duration = number(item["connection_time"])
        if duration is not None and reported_duration is not None and abs(duration - reported_duration) > .01:
            quality.append("reported_duration_differs_from_elapsed")
        if power is None or power <= 0:
            quality.append("missing_predicted_power")
        elif duration is not None and energy / power > duration + .001:
            quality.append("predicted_power_cannot_finish")
        hourly_energy, hourly_rows = hourly.get(sid, (None, 0))
        if hourly_energy is not None and abs(hourly_energy - energy) > .01:
            quality.append("hourly_energy_mismatch")
        row = dict(zip(COLUMNS, (sid, item["user_id"], item["location"], item["plugin_time"],
            None if item["plugout_time"] == "NA" else item["plugout_time"], start, end, energy,
            power, capacity, number(prediction.get("SoC_start")), number(prediction.get("SoC_diff")),
            number(prediction.get("charging_time")), number(prediction.get("idle_time")),
            round(hourly_energy, 8) if hourly_energy is not None else None, hourly_rows, issue, quality)))
        rows.append(row)
        if issue:
            issues[issue] += 1
        flags.update(quality)
    rows.sort(key=lambda row: (row["startLocal"], row["id"]))
    sources = [{"file": name, "sha256": hashlib.sha256((root / name).read_bytes()).hexdigest()}
               for name in FILES]
    return {"version": 1, "source": FILES[0], "sources": sources,
        "sourceTimezone": "Europe/Oslo", "timezone": "America/Chicago",
        "citation": "https://doi.org/10.5281/zenodo.12730566",
        "methodology": "https://pmc.ncbi.nlm.nih.gov/articles/PMC11404051/",
        "defaultPriceYear": 2025, "priceYears": [2025, 2026],
        "stats": {"sessions": len(rows), "users": len(users),
            "usersWithPower": sum(number(u["charging_power"]) is not None for u in users.values()),
            "sessionPredictions": len(sessions), "hourly": dict(hourly_counts),
            "hourlySessions": len(hourly), "issues": dict(issues), "flags": dict(flags)},
        "columns": COLUMNS, "rows": [[row[key] for key in COLUMNS] for row in rows]}


def main():
    bundle = prepare()
    target = ROOT / "night-shift/dist/residential-data.js"
    target.write_text("window.SPARC_RESIDENTIAL_DATA=" + json.dumps(bundle, ensure_ascii=True,
        allow_nan=False, separators=(",", ":")) + ";\n")
    report = ROOT / "results/charging-history/residential-data-quality.json"
    report.write_text(json.dumps({key: value for key, value in bundle.items()
        if key not in ("rows", "columns")}, indent=2) + "\n")
    print(json.dumps(bundle["stats"], indent=2))
    print(f"Wrote {target.relative_to(ROOT)} and {report.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
