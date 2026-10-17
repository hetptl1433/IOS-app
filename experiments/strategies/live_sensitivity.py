"""Does reacting to live 5-minute prices pay under any reasonable setting?

The Commuter and Big day example drivers on standard delivery: the day-ahead plan revised with live
prices, with no or strong hysteresis, with no persistence of the latest deviation, or seeing each price
the moment its interval ends instead of 7 minutes after it starts. Cents a night against the plan.
"""
import sys
from datetime import date, time, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from shared import database  # noqa: E402
import sparc.strategies as strategies  # noqa: E402
from sparc.backtest import PROFILES, run  # noqa: E402
from sparc.clock import DAY, SLOT, at  # noqa: E402
from sparc.market import Market, latest_visible_interval  # noqa: E402
from sparc.store import Store  # noqa: E402
from sparc.strategies import DayAhead, Live, fit_decay  # noqa: E402
from sparc.tariff import STANDARD  # noqa: E402

store = Store(database())
first, last = date(2025, 4, 1), date(2026, 10, 14)
cal = at(date(2025, 1, 1), time(0))
start, end = cal - DAY, at(last + timedelta(days=2), time(0))
market = Market(store.five_minute(start, end), store.day_ahead(start, end), store.published_real_time(start, end))
decay = fit_decay(market, cal, at(first, time(0)))
days = [first + timedelta(days=i) for i in range((last - first).days + 1)]
profiles = [p for p in PROFILES if p.name in ("Commuter", "Big day")]


def mean_cost(strategy, lag=None):
    if lag is not None:
        strategies.latest_visible_interval = lambda now: (now - lag) // SLOT * SLOT
    nights, _ = run(market, days, profiles, [strategy], [STANDARD])
    strategies.latest_visible_interval = latest_visible_interval
    by_profile = {}
    for n in nights:
        by_profile.setdefault(n.profile, []).append(n.outcome)
    return {p: (sum(o.cost for o in v) / len(v), sum(o.pauses for o in v) / len(v)) for p, v in by_profile.items()}


base = mean_cost(DayAhead())
variants = [("live, as backtested (lag 10 min, stickiness 0.5)", Live(decay), None),
            ("no hysteresis", Live(decay, stickiness=0.0), None),
            ("strong hysteresis (2 cents)", Live(decay, stickiness=2.0), None),
            ("no persistence beyond observed readings", Live([0.0] * 24), None),
            ("optimistic lag: see each price the moment its interval ends", Live(decay), 300)]
for label, strategy, lag in variants:
    result = mean_cost(strategy, lag)
    cells = "; ".join(f"{p}: {100 * (base[p][0] - c):+.1f} c/night vs day-ahead, pauses {pz:.2f}" for p, (c, pz) in result.items())
    print(f"{label:62s} {cells}")
