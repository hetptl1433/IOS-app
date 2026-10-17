"""Walk-forward test: do weather and holidays make the price forecast, and the charging plan, better?

Ridge and LightGBM forecasts with and without weather inputs, refit monthly on the 90 days before, for the
four example drivers on both delivery rates, 2025-04-02 to 2026-10-14. Writes the run summary next to
this script as results/weather_test.json.
"""
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from shared import RUNS, database  # noqa: E402
from sparc import lab  # noqa: E402
from sparc.store import Store  # noqa: E402

store = Store(database())
folders = lab.Folders(RUNS)
strategies = ["day-ahead", "live", "walk:ridge", "walk:ridge+weather", "walk:lightgbm", "walk:lightgbm+weather"]
settings = {"start": "2025-04-02", "end": "2026-10-14", "calibrate_days": 90, "tariffs": ["standard", "time-of-day"],
            "strategies": strategies, "profiles": [lab.profile_json(p) for p in lab.PROFILES]}
config = lab.RunConfig.parse(settings, lab.strategy_choices(folders))
began = time.monotonic()
result = lab.run_backtest(store, config, folders)
print(f"run {result['id']}")
print("\nForecast error on nights each model never saw (cents/kWh):")
print(f"  {'':40}{'mean err':>9}{'day-ahead':>10}{'change':>8}{'RMS err':>9}{'day-ahead':>10}{'change':>8}")
for label, f in result["forecasts"].items():
    t = f["test"]
    print(f"  {label:40}{t['mae']:9.3f}{t['day_ahead_mae']:10.3f}{100 * (t['mae'] / t['day_ahead_mae'] - 1):+7.1f}%"
          f"{t['rmse']:9.3f}{t['day_ahead_rmse']:10.3f}{100 * (t['rmse'] / t['day_ahead_rmse'] - 1):+7.1f}%")
S = {(s["profile"], s["delivery"], s["strategy"]): s for s in result["summaries"]}
labels = [label for label in result["strategies"] if label not in ("Charge on plug-in",)]
print("\nCost per night minus the day-ahead plan's, cents (negative = cheaper than the plan):")
print(f"  {'':40}" + "".join(f"{p['name'][:11]:>12}" for p in result["profiles"]))
for tariff in result["tariffs"]:
    print(f" {tariff}")
    for label in labels:
        row = [100 * (S[p["name"], tariff, label]["cost"] - S[p["name"], tariff, "Day-ahead plan"]["cost"])
               for p in result["profiles"]]
        print(f"  {label:40}" + "".join(f"{v:12.2f}" for v in row))
print("\nCheapest strategy and ties:")
for w in result["winners"]:
    print(f"  {w['profile']:18} {w['tariff']:22} {w['best']:42} tied: {', '.join(w['tied']) or '-'}")
json.dump(result, open(Path(__file__).parent / "results" / "weather_test.json", "w"))
