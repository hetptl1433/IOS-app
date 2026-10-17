"""Does free 5-minute switching (no limit on turning the charger on and off) save more? Tiers, 8 pm to 7 am."""
import sys
from datetime import date, time, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from shared import database  # noqa: E402
import sparc.strategies as strategies  # noqa: E402
from sparc import lab  # noqa: E402
from sparc.backtest import TIERS, run  # noqa: E402
from sparc.clock import DAY, SLOT, at  # noqa: E402
from sparc.market import Market, latest_visible_interval  # noqa: E402
from sparc.store import Store  # noqa: E402
from sparc.strategies import ChargeNow, DayAhead, Hindsight, Live, Threshold, fit_decay  # noqa: E402
from sparc.tariff import STANDARD, TIME_OF_DAY  # noqa: E402

store = Store(database())
first, last = date(2025, 4, 2), date(2026, 10, 14)
calibrate_from, first_night = at(first - timedelta(days=90), time(0)), at(first, time(0))
start, end = calibrate_from - DAY, at(last + timedelta(days=2), time(0))
market = Market(store.five_minute(start, end), store.day_ahead(start, end), store.published_real_time(start, end))
decay = fit_decay(market, calibrate_from, first_night)
days = [first + timedelta(days=i) for i in range((last - first).days + 1)]


class Named(Live):
    def __init__(self, label, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.label = label


variants = [
    (ChargeNow(), None), (DayAhead(), None),
    (Named("Live, as backtested (some hysteresis)", decay), None),
    (Named("Live, free switching every 5 min", decay, stickiness=0.0), None),
    (Named("Live, free switching, sees prices 2 min sooner", decay, stickiness=0.0), 300),
    (Threshold(2), None), (Threshold(3), None), (Hindsight(), None),
]
results = {}
for strategy, lag in variants:
    if lag is not None:
        strategies.latest_visible_interval = lambda now: (now - lag) // SLOT * SLOT
    nights, _ = run(market, days, TIERS, [strategy], [STANDARD, TIME_OF_DAY])
    strategies.latest_visible_interval = latest_visible_interval
    results[strategy.label] = nights
default_nights = [n for n in results[ChargeNow.label] if n.delivery == STANDARD.name]
for label, nights in results.items():
    rows = {r["profile"]: r for r in lab.against_default(default_nights + [n for n in nights if n.delivery == TIME_OF_DAY.name])
            if r["strategy"] == label}
    tod = [n for n in nights if n.delivery == TIME_OF_DAY.name]
    switches = {p.name: sum(n.outcome.starts + n.outcome.pauses for n in tod if n.profile == p.name)
                / sum(1 for n in tod if n.profile == p.name) for p in TIERS}
    print(f"{label:<48}" + "".join(f"  {p.name}: ${rows[p.name]['saved_per_year']:>6,.0f} ({switches[p.name]:.1f} switches)"
                                   for p in TIERS))
