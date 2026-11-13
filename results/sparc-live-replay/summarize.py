"""Add the 11 PM baselines to every replay, then write out/summary.json and one CSV per experiment.

Run after the replays: python3 results/sparc-live-replay/summarize.py

The 11 PM baselines charge nonstop from 11 PM on standard delivery, like the 6 PM ones; they
need no planner, so they are priced here from the same prices and tariffs as each replay.
"""
import csv, json, sys
from datetime import datetime, date, time
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUT = HERE / "out"

def load(script, marker):
    """The prices, tariffs and bill function of a replay script, without running its replay."""
    namespace = {"__file__": str(HERE / script)}
    saved, sys.argv = sys.argv, [str(HERE / script)]
    exec(open(HERE / script).read().split(marker)[0], namespace)
    sys.argv = saved
    return namespace

def from_eleven(day, kwh, ns):
    start = datetime.combine(day, time(23), ns["CH"]).timestamp()
    buckets, left, t = {}, kwh, start
    while left > 1e-9:
        hour = int(t) // 3600 * 3600
        take = min(left, (hour + 3600 - t) * ns["POWER"] / 3600)
        buckets[hour] = buckets.get(hour, 0) + take
        left -= take
        t = hour + 3600
    return buckets

KEYS = ["fixed6pm", "fixed11pm", "hourly6pm", "hourly11pm", "hourlyTODsparc", "hindsight"]

def summarize(name, rows):
    priced = [r for r in rows if all(r.get(k) is not None for k in KEYS)]
    kwh = sum(r["kwhSimulated"] for r in priced)
    totals = {k: sum(r[k] for r in priced) for k in KEYS}
    return {"experiment": name, "pricedNights": len(priced), "excludedNights": len(rows) - len(priced),
            "kWh": kwh, "firstNight": min(r["night"] for r in priced), "lastNight": max(r["night"] for r in priced),
            "costUSD": totals, "centsPerKWh": {k: 100 * totals[k] / kwh for k in KEYS},
            "sparcSavesUSD": {k: totals[k] - totals["hourlyTODsparc"] for k in KEYS[:4]},
            "sparcSavesPercent": {k: 100 * (totals[k] - totals["hourlyTODsparc"]) / totals[k] for k in KEYS[:4]},
            "maxPossiblePercent": {k: 100 * (totals[k] - totals["hindsight"]) / totals[k] for k in KEYS[:4]},
            "fallbackNights": [r["night"] for r in rows if r.get("fallbackSteps")]}

def write_csv(path, rows):
    with open(path, "w", newline="") as stream:
        out = csv.writer(stream)
        out.writerow(["night", "kwh"] + KEYS + ["fallbackSteps"])
        for r in rows:
            out.writerow([r["night"], round(r["kwhSimulated"], 3)] +
                         ["" if r.get(k) is None else round(r[k], 4) for k in KEYS] + [r.get("fallbackSteps", 0)])

summaries = []

# The 15-night Cybertruck replay (its own prices, incl. the hand-mapped Nov 1 DST table)
cy_ns = load("replay_15_nights.py", "energy = (TARGET_PCT")
cy = json.load(open(OUT / "cybertruck-15-nights.json"))
rows = []
for n in cy["nights"]:
    b = from_eleven(date.fromisoformat(n["night"]), cy["energyKWhPerNight"], cy_ns)
    rows.append({"night": n["night"], "kwhSimulated": cy["energyKWhPerNight"],
                 "fixed6pm": n["A_fixed_now"], "fixed11pm": cy_ns["bill"](b, cy_ns["fixed"], cy_ns["flat"]),
                 "hourly6pm": n["B_hourly_now"], "hourly11pm": cy_ns["bill"](b, cy_ns["hour_price"], cy_ns["flat"]),
                 "hourlyTODsparc": n["E_hourlyTOD_sparc"], "hindsight": n["H_hourlyTOD_hindsight"],
                 "fallbackSteps": n["fallback_steps_E"]})
write_csv(OUT / "cybertruck-15-nights.csv", rows)
summaries.append(summarize("Cybertruck 50-90%, 15 nights", rows))

# Full-history replays
hist_ns = load("replay_history.py", "# Usage:")
for name, file in [("TeslaMate car", "teslamate"), ("Car 1, Model X LR 2022", "car0001"),
                   ("Car 2, Model Y LR 2024", "car0003"), ("Car 3, Model 3 LR 2023", "car0006"),
                   ("Norwegian homes on ComEd 2025", "norway")]:
    if not (OUT / f"{file}.json").exists():
        continue
    rows = json.load(open(OUT / f"{file}.json"))
    for r in rows:
        b = from_eleven(date.fromisoformat(r["night"]), r["kwhSimulated"], hist_ns)
        r["fixed11pm"] = hist_ns["bill"](b, hist_ns["fixed"], hist_ns["flat"])
        r["hourly11pm"] = hist_ns["bill"](b, hist_ns["hour_price"], hist_ns["flat"])
    write_csv(OUT / f"{file}.csv", rows)
    summaries.append(summarize(name, rows))

json.dump(summaries, open(OUT / "summary.json", "w"), indent=1)
for s in summaries:
    print(f"{s['experiment']}: {s['pricedNights']} nights, {s['kWh']:.0f} kWh, SPARC ${s['costUSD']['hourlyTODsparc']:.2f}, "
          + ", ".join(f"{k} saves {s['sparcSavesPercent'][k]:.1f}%" for k in KEYS[:4]))
