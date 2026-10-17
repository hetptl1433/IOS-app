"""Search plug-in time, departure and nightly energy for the largest yearly saving (Cybertruck, 11.5 kW).

Two grids, each listed largest saving first, against charging on plug-in:
1. Plugged in at 12:00, 13:00 or 14:00, leaving at 7 or 8 a.m., adding 80, 100 or 123 kWh (a full
   battery), on both delivery rates.
2. Plugged in at 11:00, 12:00 or 13:00, leaving at 8 or 9 a.m., adding 90, 100 or 110 kWh, on
   Delivery Time-of-Day.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from shared import RUNS, database  # noqa: E402
from sparc import lab  # noqa: E402
from sparc.store import Store  # noqa: E402

folders = lab.Folders(RUNS)
store = Store(database())
choices = lab.strategy_choices(folders)
GRIDS = [
    ([(a, d, k) for a in ("12:00", "13:00", "14:00") for d in ("07:00", "08:00") for k in (80, 100, 123)],
     ["standard", "time-of-day"]),
    ([(a, d, k) for a in ("11:00", "12:00", "13:00") for d in ("08:00", "09:00") for k in (90, 100, 110)],
     ["time-of-day"]),
]
for number, (grid, tariffs) in enumerate(GRIDS, 1):
    results = []
    for i in range(0, len(grid), 6):
        profiles = [{"name": f"{a}-{d} +{k}", "arrive": a, "depart": d, "battery_kwh": k, "power_kw": 11.5,
                     "efficiency": 0.9, "reserve_min": 30} for a, d, k in grid[i:i + 6]]
        config = lab.RunConfig.parse({"start": "2025-04-02", "end": "2026-10-14", "calibrate_days": 90,
                                      "tariffs": tariffs, "strategies": ["charge-now", "day-ahead", "live"],
                                      "profiles": profiles}, choices)
        result = lab.run_backtest(store, config, folders)
        rows = {(s["profile"], s["delivery"], s["strategy"]): s for s in result["summaries"]}
        for p in profiles:
            for tariff in result["tariffs"]:
                base = rows[(p["name"], tariff, "Charge on plug-in")]
                for strategy in ("Day-ahead plan", "Day-ahead + live prices"):
                    s = rows[(p["name"], tariff, strategy)]
                    results.append((s["saved_per_year"], p["name"], tariff, strategy, base["cost"], s["cost"],
                                    s["share"], s["ready"], base["risk_kwh_per_year"], s["risk_kwh_per_year"],
                                    s["nights"]))
    results.sort(reverse=True)
    print(f"Grid {number}: the twelve largest savings")
    for r in results[:12]:
        print(f"${r[0]:,.0f}/yr  {r[1]:<16} {r[2]:<21} {r[3]:<24} plug-in ${r[4]:.2f} -> ${r[5]:.2f}/night "
              f"({100 * (1 - r[5] / r[4]):.0f}% cheaper)  {100 * r[6]:.0f}% of max  ready {100 * r[7]:.0f}%  "
              f"peak-hour kWh/yr {r[8]:.0f} -> {r[9]:.0f}  nights {r[10]}")
    print()
