"""About 100 charging strategies on the pitch setup, chosen on nights before SPLIT and checked on the rest.

    python experiments/strategies/sweep.py FIRST LAST SPLIT [OUT]
    e.g. 2025-04-02 2026-10-14 2026-01-01 (run 1) or 2024-04-20 2026-10-14 2026-01-01 (run 2)

Tiers: a Cybertruck at 11.5 kW plugged in 8 p.m. to 7 a.m., adding 15, 30 or 80 kWh. Every strategy is on
Time-of-Day delivery and is scored against the default setup (standard delivery, charging on plug-in).
Every strategy decides only from what it could know at the time. No limit on switching (smart switch).
"""
import json
import sys
from collections import defaultdict
from datetime import date, time, timedelta
from math import ceil
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from shared import database  # noqa: E402
from sparc.backtest import TIERS, run  # noqa: E402
from sparc.clock import DAY, SLOT, at, local  # noqa: E402
from sparc.forecast import samples, walk_forward  # noqa: E402
from sparc.market import Market  # noqa: E402
from sparc.planner import EPS  # noqa: E402
from sparc.store import Store  # noqa: E402
from sparc.strategies import (ChargeNow, DayAhead, Forecasted, Hindsight, Live, Threshold, Timer,  # noqa: E402
                              fit_decay)
from sparc.tariff import STANDARD, TIME_OF_DAY  # noqa: E402

FIRST, LAST, SPLIT = (date.fromisoformat(a) for a in (sys.argv[1:4] or ["2025-04-02", "2026-10-14", "2026-01-01"]))
OUT = sys.argv[4] if len(sys.argv) > 4 else Path(__file__).parent / "results" / f"sweep_{FIRST}_{SPLIT}.json"
store = Store(database())
first_training = FIRST - timedelta(days=90)
calibrate_from, first_night = at(first_training, time(0)), at(FIRST, time(0))
start, end = calibrate_from - DAY, at(LAST + timedelta(days=2), time(0))
market = Market(store.five_minute(start, end), store.day_ahead(start, end), store.published_real_time(start, end),
                store.weather(start, end))
days = [FIRST + timedelta(days=i) for i in range((LAST - FIRST).days + 1)]
decay = fit_decay(market, calibrate_from, first_night)

# Typical gap between the settled and day-ahead price for each clock hour, over the 60 days before a night.
gap = defaultdict(dict)
for hour, price in market.hourly.items():
    ahead = market.day_ahead.get(hour)
    if ahead is not None:
        wall = local(hour)
        gap[wall.hour][wall.date()] = abs(price - ahead)
volatility_cache = {}


def volatility(hour: int, night: date) -> float:
    key = (local(hour).hour, night)
    if key not in volatility_cache:
        series = gap[key[0]]
        values = [series[d] for d in (night - timedelta(days=k) for k in range(1, 61)) if d in series]
        volatility_cache[key] = sum(values) / len(values) if values else 0.0
    return volatility_cache[key]


class FinishBy:
    """Tesla's off-peak charging: start as late as possible to finish at a fixed time."""

    def __init__(self, finish: time):
        self.finish, self.label = finish, f"Tesla off-peak, finish by {finish:%H:%M}"

    def begin(self, session, market, delivery):
        finish = min(at(local(session.arrival).date() + timedelta(days=1), self.finish), session.departure)
        duration = ceil(session.energy_kwh / session.slot_kwh - EPS) * SLOT
        self.begins, self.full = max(session.arrival, finish - duration), session.slot_kwh

    def energy(self, now, remaining_kwh, charging):
        return self.full if now >= self.begins else 0.0


class Relative(Threshold):
    """A price cap set each night at a percentile of that night's day-ahead prices."""

    def __init__(self, share: float):
        super().__init__(0)
        self.share, self.label = share, f"Cap at the {share:.0%} cheapest day-ahead hour"

    def begin(self, session, market, delivery):
        super().begin(session, market, delivery)
        prices = sorted(market.known_day_ahead(h, session.arrival) for h in session.hours)
        self.cents = prices[min(len(prices) - 1, int(self.share * len(prices)))]


class Risky(DayAhead):
    """The day-ahead plan, with each hour made dearer by how far that clock hour has recently strayed."""

    def __init__(self, weight: float):
        self.weight, self.label = weight, f"Day-ahead plan, risk penalty {weight:g}"

    def costs(self, now):
        night = local(self.session.arrival).date()
        return {h: c + self.weight * volatility(h, night) for h, c in super().costs(now).items()}


class Lively(Live):
    def __init__(self, label, decay, stickiness, weight=0.0):
        super().__init__(decay, stickiness)
        self.label, self.weight = label, weight

    def costs(self, now):
        night = local(self.session.arrival).date()
        return {h: c + self.weight * volatility(h, night) for h, c in super().costs(now).items()}


class Blend(Forecasted):
    """The day-ahead price moved part of the way towards a learned forecast."""

    def __init__(self, source, weight, stickiness, label):
        super().__init__(source, label)
        self.weight, self.stickiness = weight, stickiness

    def costs(self, now):
        full = super().costs(now)
        return {h: self.market.known_day_ahead(h, now) + self.cents[h]
                + self.weight * (c - self.cents[h] - self.market.known_day_ahead(h, now)) for h, c in full.items()}


strategies = [ChargeNow(), DayAhead(), Hindsight()]
strategies += [Timer(time(h % 24, m)) for h in range(20, 27) for m in (0, 30) if (20, 30) <= (h, m) <= (26, 0)]
strategies += [FinishBy(time(h, m)) for h in range(3, 8) for m in (0, 30) if (h, m) <= (7, 0)]
strategies += [Threshold(c) for c in (1, 1.5, 2, 2.5, 3, 4, 5, 7)]
strategies += [Relative(s) for s in (0.2, 0.3, 0.4, 0.5, 0.6)]
strategies += [Risky(w) for w in (0.1, 0.25, 0.5, 1, 2)]
decays = {"fitted": decay, "none": [0.0] * 24, "half": [0.5] * 24, "full": [1.0] * 24}
for name, d in decays.items():
    for stick in (0.0, 0.1, 0.25, 0.5, 1.0):
        strategies.append(Lively(f"Live, {name} persistence, stickiness {stick:g}", d, stick))
for w in (0.25, 0.5, 1.0):
    for stick in (0.0, 0.5):
        strategies.append(Lively(f"Live, fitted persistence, stickiness {stick:g}, risk penalty {w:g}", decay, stick, w))
for kind in ("ridge", "lightgbm"):
    for inputs in ("prices", "weather"):
        data = samples(market, first_training, LAST, inputs)
        walk = walk_forward(market, kind, first_training, days, data=data, inputs=inputs)
        for weight in (0.25, 0.5, 0.75, 1.0):
            for stick in (0.0, 0.5):
                strategies.append(Blend(walk, weight, stick, f"Forecast {kind}{'+weather' if inputs == 'weather' else ''}, "
                                                             f"weight {weight:g}, stickiness {stick:g}"))
print(len(strategies) - 3, "strategies besides the baseline, the day-ahead plan and hindsight", file=sys.stderr)

default_nights, _ = run(market, days, TIERS, [ChargeNow()], [STANDARD])
default = {(n.profile, n.day): n.outcome.cost for n in default_nights}
nights, _ = run(market, days, TIERS, strategies, [TIME_OF_DAY],
                progress=lambda done, total: print(f"\r{done}/{total}", end="", file=sys.stderr))
print(file=sys.stderr)
saving = defaultdict(lambda: defaultdict(list))  # (strategy, period) -> profile -> savings per night
for n in nights:
    period = "train" if n.day < SPLIT else "test"
    saving[n.strategy, period][n.profile].append(default[n.profile, n.day] - n.outcome.cost)
    saving[n.strategy, "all"][n.profile].append(default[n.profile, n.day] - n.outcome.cost)
table = {}
for (label, period), by_profile in saving.items():
    table.setdefault(label, {})[period] = {p: 365 * sum(v) / len(v) for p, v in by_profile.items()}
json.dump(table, open(OUT, "w"), indent=1)
names = [p.name for p in TIERS]
rank = sorted((t for t in table if t != "Perfect hindsight"), key=lambda t: -sum(table[t]["train"].values()))


def against_plan(label: str, profile: str) -> tuple[float, float]:
    """Extra saving a year over the day-ahead plan on test nights, and its standard error (paired by night)."""
    diff = [a - b for a, b in zip(saving[label, "test"][profile], saving["Day-ahead plan", "test"][profile])]
    mean = sum(diff) / len(diff)
    spread = (sum((x - mean) ** 2 for x in diff) / (len(diff) - 1)) ** 0.5
    return 365 * mean, 365 * spread / len(diff) ** 0.5
print(f"{'strategy':<62}{'train avg/mod/heavy':>30}{'test avg/mod/heavy':>30}")
for label in rank[:20] + ["Day-ahead plan", "Perfect hindsight"]:
    train = "/".join(f"{table[label]['train'][p]:,.0f}" for p in names)
    test = "/".join(f"{table[label]['test'][p]:,.0f}" for p in names)
    edge = "  ".join("{:+,.0f}±{:,.0f}".format(*against_plan(label, p)) for p in names)
    print(f"{label:<62}{train:>30}{test:>30}  rank {rank.index(label) + 1 if label in rank else '-'}  vs plan on test: {edge}")
