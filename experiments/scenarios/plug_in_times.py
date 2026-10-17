"""The three driver tiers (15, 30 and 80 kWh a day) plugging in at 8 p.m. until 7 a.m., or 11 p.m. until 8 a.m.

A Cybertruck on an 11.5 kW Wall Connector. For each window: what the day-ahead plan, the plan revised with
live prices and perfect hindsight save against charging on plug-in, on each delivery rate.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from shared import RUNS, database  # noqa: E402
from sparc import lab  # noqa: E402
from sparc.store import Store  # noqa: E402

folders = lab.Folders(RUNS)
store = Store(database())
for arrive, depart in (("20:00", "07:00"), ("23:00", "08:00")):
    print(f"Plug in {arrive}, leave {depart}")
    profiles = [{"name": f"+{k}", "arrive": arrive, "depart": depart, "battery_kwh": k, "power_kw": 11.5,
                 "efficiency": 0.9, "reserve_min": 30} for k in (15, 30, 80)]
    config = lab.RunConfig.parse({"start": "2025-04-02", "end": "2026-10-14", "calibrate_days": 90,
                                  "tariffs": list(lab.TARIFFS), "strategies": ["charge-now", "day-ahead", "live"],
                                  "profiles": profiles}, lab.strategy_choices(folders))
    result = lab.run_backtest(store, config, folders)
    rows = {(s["profile"], s["delivery"], s["strategy"]): s for s in result["summaries"]}
    for p in profiles:
        for tariff in ("Standard delivery", "Delivery Time-of-Day"):
            base = rows[(p["name"], tariff, "Charge on plug-in")]
            for strategy in ("Day-ahead plan", "Day-ahead + live prices", "Perfect hindsight"):
                s = rows[(p["name"], tariff, strategy)]
                print(f"{p['name']:>5} kWh {tariff:<21} {strategy:<24} saves ${s['saved_per_year']:,.0f}/yr  "
                      f"${base['cost']:.2f} -> ${s['cost']:.2f}/day ({100 * (1 - s['cost'] / base['cost']):.0f}% cheaper)  "
                      f"ready {100 * s['ready']:.0f}%  days {s['nights']}")
    for w in result["winners"]:
        print(w["profile"], w["tariff"], "best:", w["best"], "tied:", w["tied"])
    print()
