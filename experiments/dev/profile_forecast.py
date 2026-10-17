"""Profile one walk-forward forecast strategy (Ridge, refit monthly) in the default backtest.

    python experiments/dev/profile_forecast.py
"""
import cProfile
import pstats
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from shared import RUNS, database  # noqa: E402
from sparc import lab  # noqa: E402
from sparc.store import Store  # noqa: E402

store = Store(database())
folders = lab.Folders(RUNS)
defaults = lab.default_config(store, folders)
defaults["strategies"] = ["walk:ridge"]
config = lab.RunConfig.parse(defaults, lab.strategy_choices(folders))
profiler = cProfile.Profile()
profiler.enable()
result = lab.run_backtest(store, config, folders)
profiler.disable()
print("elapsed", result["elapsed_s"])
pstats.Stats(profiler).sort_stats("tottime").print_stats(14)
