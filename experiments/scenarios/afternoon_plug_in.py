"""Plugged in from early afternoon: does a long window with a big charge save more? (Cybertruck, 11.5 kW)

An evening commuter (6 p.m. to 7 a.m., 15 or 30 kWh) against a car plugged in at 12:00, 13:00 or 14:00
and leaving at 4, 5 or 6 a.m. with 80 or 100 kWh. Also the energy each charges in summer weekday
afternoons, when next year's capacity charge is set.
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
cases = [("18:00", "07:00", 15), ("18:00", "07:00", 30)]
cases += [(a, d, k) for a in ("12:00", "13:00", "14:00") for d in ("04:00", "05:00", "06:00") for k in (80, 100)]
out = []
for i in range(0, len(cases), 8):
    profiles = [{"name": f"{a}-{d} +{k}", "arrive": a, "depart": d, "battery_kwh": k, "power_kw": 11.5,
                 "efficiency": 0.9, "reserve_min": 30} for a, d, k in cases[i:i + 8]]
    config = lab.RunConfig.parse({"start": "2025-04-02", "end": "2026-10-14", "calibrate_days": 90,
                                  "tariffs": list(lab.TARIFFS), "strategies": ["charge-now", "day-ahead"],
                                  "profiles": profiles}, choices)
    result = lab.run_backtest(store, config, folders)
    rows = {(s["profile"], s["delivery"], s["strategy"]): s for s in result["summaries"]}
    for p in profiles:
        for tariff in ("Standard delivery", "Delivery Time-of-Day"):
            base, plan = rows[(p["name"], tariff, "Charge on plug-in")], rows[(p["name"], tariff, "Day-ahead plan")]
            out.append((p["name"], tariff, plan["saved_per_year"], base["cost"], plan["cost"], plan["share"],
                        plan["ready"], base["risk_kwh_per_year"], plan["risk_kwh_per_year"], plan["nights"]))
for r in out:
    if r[0].startswith("18:00") or r[1] == "Delivery Time-of-Day":
        print(f"{r[0]:<16} {r[1]:<21} saves ${r[2]:,.0f}/yr  ${r[3]:.2f} -> ${r[4]:.2f}/day "
              f"({100 * (1 - r[4] / r[3]):.0f}% cheaper)  {100 * r[5]:.0f}% of max  ready {100 * r[6]:.0f}%  "
              f"peak kWh/yr {r[7]:.0f} -> {r[8]:.0f}  days {r[9]}")
