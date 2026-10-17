"""Every strategy for the pitch setup: a Cybertruck on an 11.5 kW Wall Connector, 8 p.m. to 7 a.m., 15/30/80 kWh a day.

Each strategy runs on Delivery Time-of-Day and is compared with the default setup (standard delivery,
charging on plug-in): dollars saved a year, percent cheaper, and cost per night.
"""
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from shared import RUNS, database  # noqa: E402
from sparc import lab  # noqa: E402
from sparc.__main__ import Progress  # noqa: E402
from sparc.store import Store  # noqa: E402

folders = lab.Folders(RUNS)
store = Store(database())
start, end, calibrate = date(2025, 4, 2), date(2026, 10, 14), 90
names = {15: "Average", 30: "Moderate", 80: "Heavy"}
profiles = [{"name": names[k], "arrive": "20:00", "depart": "07:00", "battery_kwh": k, "power_kw": 11.5,
             "efficiency": 0.9, "reserve_min": 30} for k in names]
choices = lab.strategy_choices(folders)
weather = lab.weather_ready(store, start - timedelta(days=calibrate), end)
strategies = lab.eligible(choices, start, calibrate, weather)
print("strategies:", strategies)
config = lab.RunConfig.parse({"start": start.isoformat(), "end": end.isoformat(), "calibrate_days": calibrate,
                              "tariffs": list(lab.TARIFFS), "strategies": strategies, "profiles": profiles}, choices)
result = lab.run_backtest(store, config, folders, progress=Progress())
rows = {(s["profile"], s["delivery"], s["strategy"]): s for s in result["summaries"]}
print("run", result["id"], "nights", result["nights"], "skipped", result["skipped"])
print(f"{'strategy':<44}" + "".join(f"{n:>22}" for n in names.values()))
for label in result["strategies"]:
    cells = []
    for name in names.values():
        default = rows[(name, "Standard delivery", "Charge on plug-in")]["cost"]
        tod = rows[(name, "Delivery Time-of-Day", label)]["cost"]
        cells.append(f"${365 * (default - tod):>6,.0f} {100 * (1 - tod / default):>3.0f}%  ${tod:>5.2f}")
    print(f"{label:<44}" + "".join(f"{c:>22}" for c in cells))
print("default setup $/night:",
      {n: round(rows[(n, "Standard delivery", "Charge on plug-in")]["cost"], 4) for n in names.values()})
for w in result["winners"]:
    print(w["profile"], "|", w["tariff"], "| best:", w["best"], "| tied:", ", ".join(w["tied"]))
for label, forecast in result["forecasts"].items():
    t = forecast.get("test") or {}
    print(label, f"MAE {t.get('mae', 0):.3f} RMSE {t.get('rmse', 0):.3f} "
                 f"(DA {t.get('day_ahead_mae', 0):.3f} / {t.get('day_ahead_rmse', 0):.3f})")
