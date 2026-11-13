"""Norwegian residential charging (Dataset1) replayed on ComEd 2025 prices with the Pi's planner.

    python3 results/sparc-live-replay/replay_norway.py

Each user's sessions are grouped into nights (noon to noon by plug-in time) and the night's total
energy is charged 6 PM -> 7 AM at 11.5 kW, like every other experiment here. Source dates
(2018-2021, Oslo clock) are moved to the nearest same weekday in the same month of 2025, as the
repo's Norway study does, because ComEd's day-ahead history in the repo starts in 2022.
Uses every core but three; nights are independent.
"""
import csv
import json
import multiprocessing
import os
import sys
from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]

saved, sys.argv = sys.argv, [str(HERE / "replay_history.py")]
NS = {"__file__": str(HERE / "replay_history.py")}
exec(open(HERE / "replay_history.py").read().split("# Usage:")[0], NS)
sys.argv = saved


def to_2025(day):
    """The 2025 date in the same month, on the same weekday, nearest the same day of the month."""
    candidates = [date(2025, day.month, d) for d in range(1, 32)
                  if _valid(2025, day.month, d) and date(2025, day.month, d).weekday() == day.weekday()]
    return min(candidates, key=lambda c: (abs(c.day - day.day), c.day))


def _valid(y, m, d):
    try:
        date(y, m, d)
        return True
    except ValueError:
        return False


def replay(item):
    (user, source_day), energy = item
    day = to_2025(source_day)
    capped = min(energy, NS["WINDOW_KWH"])
    arrive = datetime.combine(day, NS["time"](18), NS["CH"]).timestamp()
    depart = datetime.combine(day + timedelta(1), NS["time"](7), NS["CH"]).timestamp()
    six = NS["at_six"](arrive, capped)
    sparc, got, fallback = NS["sparc"](arrive, depart, capped)
    bill = NS["bill"]
    return {"night": day.isoformat(), "sourceNight": source_day.isoformat(), "user": user,
            "kwh": energy, "kwhSimulated": capped, "sparcKWh": got, "fallbackSteps": fallback,
            "fixed6pm": bill(six, NS["fixed"], NS["flat"]), "hourly6pm": bill(six, NS["hour_price"], NS["flat"]),
            "hourlyTOD6pm": bill(six, NS["hour_price"], NS["tod"]),
            "hourlyTODsparc": bill(sparc, NS["hour_price"], NS["tod"]),
            "hindsight": bill(NS["hindsight"](arrive, depart, capped), NS["hour_price"], NS["tod"])}


if __name__ == "__main__":
    nights = defaultdict(float)
    with open(REPO / "Dataset1_charging_reports (1).csv", encoding="utf-8-sig", newline="") as stream:
        for r in csv.DictReader(stream, delimiter=";"):
            plugged = datetime.strptime(r["plugin_time"], "%Y-%m-%d %H:%M:%S")
            nights[(r["user_id"], (plugged - timedelta(hours=12)).date())] += float(r["energy_session"].replace(",", "."))
    items = sorted(nights.items())
    workers = max(1, (os.cpu_count() or 4) - 3)
    rows = []
    with multiprocessing.get_context("fork").Pool(workers) as pool:
        for i, row in enumerate(pool.imap(replay, items, chunksize=50), 1):
            rows.append(row)
            if i % 2000 == 0:
                print(i, "/", len(items), flush=True)
    json.dump(rows, open(HERE / "out/norway.json", "w"))
    print("done", len(rows), "user-nights")
