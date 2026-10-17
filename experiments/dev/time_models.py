"""How long loading prices, building forecast samples and training each model take.

    python experiments/dev/time_models.py
"""
import sys
import time
from datetime import date, time as wall
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from shared import database  # noqa: E402
from sparc.clock import DAY, at  # noqa: E402
from sparc.forecast import samples, train  # noqa: E402
from sparc.market import Market  # noqa: E402
from sparc.store import Store  # noqa: E402

store = Store(database())
start, end = at(date(2025, 1, 1), wall(0)) - DAY, at(date(2026, 10, 16), wall(0))
t = time.monotonic()
market = Market(store.five_minute(start, end), store.day_ahead(start, end), store.published_real_time(start, end))
print(f"market {time.monotonic() - t:.1f}s")
t = time.monotonic()
data = samples(market, date(2025, 1, 2), date(2026, 10, 14))
print(f"samples 651 nights: {len(data.rows)} rows in {time.monotonic() - t:.1f}s")
for kind in ("ridge", "lightgbm"):
    t = time.monotonic()
    model = train(market, kind, date(2025, 1, 2), date(2025, 12, 31))
    print(f"{kind} 1 year: {time.monotonic() - t:.1f}s settings={ {k: v for k, v in model.settings.items() if k != 'validation_rmse'} }")
