"""Check the fast `Recent.seen_by` (running sums) against a direct recount, at about 21,000 moments.

`Recent` summarises the posted 5-minute prices' gap from day-ahead over the last hour, three hours and
day, as forecast inputs. This recomputes them interval by interval, including moments before, at and
after the ends of the data, and asserts the newest gap is identical and the means agree.

    python experiments/dev/check_recent.py
"""
import random
import sys
from datetime import date, time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from shared import database  # noqa: E402
from sparc.clock import DAY, SLOT, at, hour_start  # noqa: E402
from sparc.forecast import Recent  # noqa: E402
from sparc.market import Market, latest_visible_interval  # noqa: E402
from sparc.store import Store  # noqa: E402


def old(market, now):
    newest = latest_visible_interval(now)
    spans = (12, 36, 288)
    sums, counts = [0.0] * 3, [0] * 3
    latest = None
    for k in range(spans[-1]):
        t = newest - k * SLOT
        price = market.five_minute.get(t)
        reference = market.day_ahead.get(hour_start(t))
        if price is None or reference is None:
            continue
        gap = price - reference
        if latest is None:
            latest = gap
        for i, span in enumerate(spans):
            if k < span:
                sums[i] += gap
                counts[i] += 1
    means = [s / n if n else 0.0 for s, n in zip(sums, counts)]
    return (latest or 0.0, *means)


store = Store(database())
start, end = at(date(2025, 1, 1), time(0)) - DAY, at(date(2026, 10, 17), time(0))
market = Market(store.five_minute(start, end), store.day_ahead(start, end))
rng = random.Random(3)
worst, n = 0.0, 0
# random moments, plus moments before, at and after the ends of the data
moments = [rng.randrange(start - 2 * DAY, end + 2 * DAY, 60) for _ in range(20000)]
moments += [start + k * 60 for k in range(-30, 400)] + [end + k * 60 for k in range(-1500, 1500, 7)]
for now in moments:
    a, b = old(market, now), Recent.seen_by(market, now)
    b = (b.latest, b.hour, b.three_hours, b.day)
    assert a[0] == b[0], (now, a, b)
    worst = max(worst, max(abs(x - y) for x, y in zip(a, b)))
    n += 1
print(f"{n} moments; latest identical; largest difference in a window mean: {worst:.2e} cents")
