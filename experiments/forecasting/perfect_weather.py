"""What if the weather forecasts had been perfect? An upper bound on what weather inputs can add.

Copies the price database to a temporary folder, replaces every 24-hour-ahead weather forecast with the
weather that was then observed, and reruns the weather forecasts of weather_test.py on it. Forecast
error, and cents a night against the day-ahead plan, for the four example drivers.
"""
import shutil
import sqlite3
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from shared import database  # noqa: E402
from sparc import lab  # noqa: E402
from sparc.store import Store  # noqa: E402

with tempfile.TemporaryDirectory() as folder:
    root = Path(folder)
    shutil.copyfile(database(), root / "sparc.db")
    db = sqlite3.connect(root / "sparc.db")
    with db:
        db.execute("UPDATE weather SET temp_fc = temp_c, dew_fc = dew_c, wind_fc = wind_kmh, cloud_fc = cloud_pct")
    db.close()
    store = Store(root / "sparc.db")
    folders = lab.Folders(root)
    settings = {"start": "2025-04-02", "end": "2026-10-14", "calibrate_days": 90,
                "tariffs": ["standard", "time-of-day"],
                "strategies": ["day-ahead", "walk:ridge+weather", "walk:lightgbm+weather"],
                "profiles": [lab.profile_json(p) for p in lab.PROFILES]}
    result = lab.run_backtest(store, lab.RunConfig.parse(settings, lab.strategy_choices(folders)), folders)
    store.close()
for label, f in result["forecasts"].items():
    t = f["test"]
    print(f"PERFECT WEATHER {label:40} mean err {t['mae']:.3f} (day-ahead {t['day_ahead_mae']:.3f}, "
          f"{100 * (t['mae'] / t['day_ahead_mae'] - 1):+.1f}%)  RMS {t['rmse']:.3f} "
          f"({100 * (t['rmse'] / t['day_ahead_rmse'] - 1):+.1f}%)")
S = {(s["profile"], s["delivery"], s["strategy"]): s for s in result["summaries"]}
for tariff in result["tariffs"]:
    for label in result["forecasts"]:
        row = [100 * (S[p["name"], tariff, label]["cost"] - S[p["name"], tariff, "Day-ahead plan"]["cost"])
               for p in result["profiles"]]
        print(f"  {tariff:22} {label:45}" + "".join(f"{v:8.2f}" for v in row) + "  cents/night vs the plan")
