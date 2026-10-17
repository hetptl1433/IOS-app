"""Fill up on cheap nights: the battery as a buffer across nights. Tiers, 8 pm to 7 am, Time-of-Day delivery.

Each night the car arrives with some charge, and must leave with enough to drive the next day and still be
back above a reserve. A rule decides how much to add tonight from what is known at 8 p.m.: tonight's
day-ahead prices, past nights' prices and the day of the week. The day-ahead plan then picks the hours.
Rules are chosen on nights before SPLIT and checked on the rest.

    python experiments/battery_buffer/fillup_rules.py [FIRST LAST SPLIT]
    (default 2025-04-02 2026-10-14 2026-01-01; the 2024 run used 2024-04-20 2026-10-14 2026-01-01)
"""
import statistics
import sys
from collections import defaultdict
from datetime import date, time, timedelta
from math import ceil
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from shared import database  # noqa: E402
from sparc.backtest import TIERS, simulate, usable  # noqa: E402
from sparc.clock import at  # noqa: E402
from sparc.market import Market  # noqa: E402
from sparc.store import Store  # noqa: E402
from sparc.strategies import DayAhead, Session  # noqa: E402
from sparc.tariff import TIME_OF_DAY  # noqa: E402

CAPACITY, EFFICIENCY, POWER = 123.0, 0.9, 11.5  # Cybertruck usable kWh; Wall Connector kW
FIRST, LAST, SPLIT = (date.fromisoformat(a) for a in (sys.argv[1:4] or ["2025-04-02", "2026-10-14", "2026-01-01"]))
store = Store(database())
start, end = at(FIRST - timedelta(days=40), time(0)), at(LAST + timedelta(days=2), time(0))
market = Market(store.five_minute(start, end), store.day_ahead(start, end), store.published_real_time(start, end))
days = [FIRST + timedelta(days=i) for i in range((LAST - FIRST).days + 1)]


def session(day, battery_kwh):
    return Session(at(day, time(20)), at(day + timedelta(days=1), time(7)), battery_kwh / EFFICIENCY, POWER)


def tonight_price(day, need_kwh):
    """Mean day-ahead price plus delivery of the cheapest hours that a normal night's charge needs."""
    s = session(day, need_kwh)
    prices = sorted(market.known_day_ahead(h, s.arrival) + TIME_OF_DAY.cents(h) for h in s.hours)
    hours = max(1, ceil(s.energy_kwh / POWER))
    return sum(prices[:hours]) / hours


def run(profile, rule, low, high, top):
    """Total cost and grid kWh by period, and nights the car could not get what it needed."""
    drive = profile.battery_kwh
    battery = 0.5 * CAPACITY
    history = []  # tonight_price of past nights
    totals = defaultdict(lambda: [0.0, 0.0])
    short = 0
    for day in days:
        s = session(day, drive)
        if not usable(s, market) or day - timedelta(days=1) not in market_days:
            battery = max(0.2 * CAPACITY, battery)  # a missing night: assume it charged normally
            continue
        price = tonight_price(day, drive)
        floor = 0.2 * CAPACITY
        minimum = max(0.0, drive + floor - battery)
        most = max(minimum, top * CAPACITY - battery)
        if rule == "nightly":
            want = max(minimum, drive)
        elif rule == "percentile":
            recent = history[-30:]
            cheap = len(recent) >= 10 and price <= statistics.quantiles(recent, n=20)[int(20 * low) - 1]
            dear = len(recent) >= 10 and price >= statistics.quantiles(recent, n=20)[int(20 * high) - 1]
            want = most if cheap else minimum if dear else max(minimum, drive)
        elif rule == "weekend":
            want = most if day.weekday() in (4, 5) else max(minimum, min(drive, most))
        else:  # every k-th night
            k = int(low)
            want = most if day.toordinal() % k == 0 else minimum
        want = min(want, max(0.0, CAPACITY - battery))
        if want > 0:
            outcome = simulate(session(day, want), DayAhead(), market, TIME_OF_DAY)
            got = outcome.energy_kwh * EFFICIENCY
            period = "train" if day < SPLIT else "test"
            totals[period][0] += outcome.cost
            totals[period][1] += outcome.energy_kwh
            if got + 1e-6 < minimum:
                short += 1
        else:
            got = 0.0
        battery = battery + got - drive
        if battery < floor - 1e-6:
            short += 1
            battery = floor
        history.append(price)
    return totals, short


market_days = {d for d in (FIRST - timedelta(days=k) for k in range(1, 40))} | set(days)
rules = [("nightly", 0, 0, 0.9)]
for top in (0.8, 0.9, 1.0):
    for low, high in ((0.2, 0.8), (0.3, 0.7), (0.4, 0.6), (0.5, 0.5)):
        rules.append(("percentile", low, high, top))
    rules.append(("weekend", 0, 0, top))
    for k in (2, 3):
        rules.append((f"every {k}", k, 0, top))


def main():
    print(f"{'rule':<34}" + "".join(f"{p.name + ' train/test c/kWh':>32}" for p in TIERS))
    results = {}
    for rule in rules:
        cells = []
        for p in TIERS:
            totals, short = run(p, *rule)
            cpk = {k: 100 * v[0] / v[1] for k, v in totals.items()}
            results[rule, p.name] = (cpk, short)
            cells.append(f"{cpk['train']:.3f} / {cpk['test']:.3f}" + (f" ({short} short)" if short else ""))
        name, low, high, top = rule
        label = name if name == "nightly" else f"{name} {low:g}/{high:g}, max {top:.0%}"
        print(f"{label:<34}" + "".join(f"{c:>32}" for c in cells))

    print()
    print("Best rule on the tuning nights for each driver, and how it did on the test nights:")
    for p in TIERS:
        grid = p.battery_kwh / EFFICIENCY * 365
        base = results[("nightly", 0, 0, 0.9), p.name][0]
        best = min((r for r in rules if r[0] != "nightly"), key=lambda r: results[r, p.name][0]["train"])
        cpk, short = results[best, p.name]
        print(f"  {p.name}: {best}: tuning {base['train'] - cpk['train']:+.3f} c/kWh (${(base['train'] - cpk['train']) * grid / 100:+.0f}/yr), "
              f"test {base['test'] - cpk['test']:+.3f} c/kWh (${(base['test'] - cpk['test']) * grid / 100:+.0f}/yr), short nights {short}")


if __name__ == "__main__":
    main()
