"""A Cybertruck (123 kWh) on an 11.5 kW Wall Connector: savings by nightly energy and plug-in time.

Plugged in at 6 p.m. (20 to 120 kWh a night) or 10 p.m. (20 to 80 kWh), leaving at 7 a.m.
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
strategies = ["charge-now", "timer-2300", "timer-0100", "cap-3", "day-ahead", "live"]
runs = {"18:00": (20, 40, 60, 80, 100, 120), "22:00": (20, 40, 60, 80)}
for arrive, energies in runs.items():
    profiles = [{"name": f"{arrive} +{kwh}", "arrive": arrive, "depart": "07:00", "battery_kwh": kwh,
                 "power_kw": 11.5, "efficiency": 0.9, "reserve_min": 30} for kwh in energies]
    config = lab.RunConfig.parse({"start": "2025-04-02", "end": "2026-10-14", "calibrate_days": 90,
                                  "tariffs": list(lab.TARIFFS), "strategies": strategies, "profiles": profiles},
                                 choices)
    result = lab.run_backtest(store, config, folders)
    rows = {(s["profile"], s["delivery"], s["strategy"]): s for s in result["summaries"]}
    best = {(w["profile"], w["tariff"]): (w["best"], w["tied"]) for w in result["winners"]}
    for p in profiles:
        for tariff in ("Standard delivery", "Delivery Time-of-Day"):
            base = rows[(p["name"], tariff, "Charge on plug-in")]
            plan = rows[(p["name"], tariff, "Day-ahead plan")]
            live = rows[(p["name"], tariff, "Day-ahead + live prices")]
            hind = rows[(p["name"], tariff, "Perfect hindsight")]
            print(f"{p['name']:>10} kWh {tariff:<21} plug-in ${base['cost']:.2f}  plan ${plan['cost']:.2f} "
                  f"saves ${plan['saved_per_year']:.0f}/yr ({100 * (1 - plan['cost'] / base['cost']):.0f}% cheaper, "
                  f"{100 * plan['share']:.0f}% of max ${hind['saved_per_year']:.0f})  live ${live['saved_per_year']:.0f}  "
                  f"ready {100 * plan['ready']:.0f}%  best: {best[(p['name'], tariff)][0]}")
