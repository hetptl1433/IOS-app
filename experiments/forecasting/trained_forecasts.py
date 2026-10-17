"""Forecast models trained on 2025 and tested on 2026: price error, and what planning with them saves.

A Ridge and a LightGBM model learn how far each hour's settled real-time price lands from its day-ahead
price (nights 2025-01-02..2025-12-31), then are scored on nights they never saw (2026-01-02..2026-10-13)
and drive the charging planner for the four example drivers on standard delivery.
"""
import sys
import time as clock
from datetime import date, time, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from shared import database  # noqa: E402
from sparc.backtest import PROFILES, run, summarize  # noqa: E402
from sparc.clock import DAY, at  # noqa: E402
from sparc.forecast import train  # noqa: E402
from sparc.market import Market  # noqa: E402
from sparc.store import Store  # noqa: E402
from sparc.strategies import ChargeNow, DayAhead, Forecasted, Hindsight, Live, Timer, fit_decay  # noqa: E402
from sparc.tariff import STANDARD  # noqa: E402

store = Store(database())
start, end = at(date(2025, 1, 1), time(0)) - DAY, at(date(2026, 10, 16), time(0))
market = Market(store.five_minute(start, end), store.day_ahead(start, end), store.published_real_time(start, end))
train_nights, test_nights = (date(2025, 1, 2), date(2025, 12, 31)), (date(2026, 1, 2), date(2026, 10, 13))
models = []
for kind in ("ridge", "lightgbm"):
    t = clock.perf_counter()
    model = train(market, kind, *train_nights, test=test_nights)
    m = model.metrics["test"]
    print(f"{kind:8s} trained on {model.metrics['train']['samples']:,} examples; "
          f"test MAE {m['mae']:.3f} vs day-ahead {m['day_ahead_mae']:.3f} cents; RMSE {m['rmse']:.3f} vs {m['day_ahead_rmse']:.3f}")
    models.append(model)
decay = fit_decay(market, at(date(2025, 1, 1), time(0)), at(date(2026, 1, 1), time(0)))
days = [test_nights[0] + timedelta(days=i) for i in range((test_nights[1] - test_nights[0]).days + 1)]
strategies = [ChargeNow(), Timer(time(23)), DayAhead(), Live(decay)] + [Forecasted(m) for m in models] + [Hindsight()]
nights, skipped = run(market, days, PROFILES, strategies, [STANDARD])
print(f"backtest: {len(nights):,} simulated nights; skipped {skipped}")
for s in summarize(nights, baseline=ChargeNow.label, best=Hindsight.label):
    if s.strategy not in (ChargeNow.label,):
        print(f"  {s.profile:18s} {s.strategy:44s} ${s.cost:5.2f}/night  saved ${s.saved_per_year:4.0f}/yr  "
              f"{100 * (s.share or 0):3.0f}% of max  starts {s.starts:.2f}")
