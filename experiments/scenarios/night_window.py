"""Every strategy for drivers who plug in at 10 p.m. and leave at 7 or 8 a.m. (7.7 kW, 15 or 30 kWh a night)."""
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from shared import RUNS, database  # noqa: E402
from sparc import lab  # noqa: E402
from sparc.__main__ import Progress, print_run  # noqa: E402
from sparc.store import Store  # noqa: E402

folders = lab.Folders(RUNS)
store = Store(database())
start, end, calibrate = date(2025, 4, 2), date(2026, 10, 14), 90
profiles = [{"name": f"10pm-{depart[:2].lstrip('0')}am {kwh}kWh", "arrive": "22:00", "depart": depart,
             "battery_kwh": kwh, "power_kw": 7.7, "efficiency": 0.9, "reserve_min": 30}
            for depart in ("07:00", "08:00") for kwh in (15, 30)]
choices = lab.strategy_choices(folders)
weather = lab.weather_ready(store, start - timedelta(days=calibrate), end)
config = lab.RunConfig.parse({"start": start.isoformat(), "end": end.isoformat(), "calibrate_days": calibrate,
                              "tariffs": list(lab.TARIFFS), "strategies": lab.eligible(choices, start, calibrate, weather),
                              "profiles": profiles}, choices)
result = lab.run_backtest(store, config, folders, progress=Progress())
print_run(result)
print("run id", result["id"])
