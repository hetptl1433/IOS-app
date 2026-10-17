"""Profile the web app's default backtest (every strategy, the three driver tiers): where does the time go?

    python experiments/dev/profile_backtest.py
"""
import cProfile
import pstats
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from shared import RUNS, database  # noqa: E402
from sparc import lab  # noqa: E402
from sparc.store import Store  # noqa: E402

store = Store(database())
folders = lab.Folders(RUNS)
defaults = lab.default_config(store, folders)
print("defaults:", {k: v for k, v in defaults.items() if k != "profiles"})
config = lab.RunConfig.parse(defaults, lab.strategy_choices(folders))
began = time.monotonic()
stages = set()


def progress(done, total, message):
    if message not in stages:
        stages.add(message)
        print(f"{time.monotonic() - began:6.1f}s {message}", flush=True)


profiler = cProfile.Profile()
profiler.enable()
result = lab.run_backtest(store, config, folders, progress)
profiler.disable()
print("elapsed", result["elapsed_s"], "nights", result["nights"], "skipped", result["skipped"])
for w in result["winners"]:
    print(w["profile"], "|", w["tariff"], "| best:", w["best"], "| tied:", w["tied"])
pstats.Stats(profiler).sort_stats("cumulative").print_stats(18)
