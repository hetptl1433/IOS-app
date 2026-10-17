"""Marginal fill-up: add each extra hour of charging tonight only if that hour is cheaper than a typical night.

    python experiments/battery_buffer/fillup_marginal.py [FIRST LAST SPLIT]
"""
import statistics
from collections import defaultdict
from math import ceil

from fillup_rules import (CAPACITY, EFFICIENCY, POWER, SPLIT, TIERS, TIME_OF_DAY, DayAhead, days, market, session,
                          simulate, tonight_price, usable)


def hour_prices(day, need_kwh):
    s = session(day, need_kwh)
    return sorted(market.known_day_ahead(h, s.arrival) + TIME_OF_DAY.cents(h) for h in s.hours)


def run3(profile, rule, quantile, back, top):
    drive, battery, history = profile.battery_kwh, 0.5 * CAPACITY, []
    totals, short, floor = defaultdict(lambda: [0.0, 0.0]), 0, 0.2 * CAPACITY
    per_hour = POWER * EFFICIENCY  # battery kWh per hour of charging
    for day in days:
        if not usable(session(day, drive), market):
            continue
        price = tonight_price(day, drive)
        minimum = max(0.0, drive + floor - battery)
        most = max(minimum, top * CAPACITY - battery)
        recent = history[-back:]
        if rule == "nightly" or len(recent) < 10:
            want = max(minimum, min(drive, most))
        else:
            reference = statistics.quantiles(recent, n=20)[int(20 * quantile) - 1]
            prices = hour_prices(day, drive)
            want = minimum
            used = ceil(minimum / per_hour - 1e-9)  # hours the minimum already takes
            for p in prices[used:]:
                if p >= reference or want >= most - 1e-6:
                    break
                want = min(most, want + per_hour)
        want = min(want, max(0.0, CAPACITY - battery))
        got = 0.0
        if want > 1e-6:
            outcome = simulate(session(day, want), DayAhead(), market, TIME_OF_DAY)
            got = outcome.energy_kwh * EFFICIENCY
            period = "train" if day < SPLIT else "test"
            totals[period][0] += outcome.cost
            totals[period][1] += outcome.energy_kwh
        battery += got - drive
        if battery < floor - 1e-6:
            short += 1
            battery = floor
        history.append(price)
    return {k: 100 * v[0] / v[1] for k, v in totals.items()}, short


rules = [("nightly", 0, 0, 0.9)] + [("marginal", q, b, t) for t in (0.8, 0.9) for q in (0.2, 0.3, 0.4, 0.5)
                                    for b in (30, 60)]
results = {(r, p.name): run3(p, *r) for r in rules for p in TIERS}
for r in rules:
    print(f"{str(r):<30}" + "".join(f"{results[r, p.name][0]['train']:>12.3f} / {results[r, p.name][0]['test']:<6.3f}"
                                    + (f"*{results[r, p.name][1]}" if results[r, p.name][1] else "  ") for p in TIERS))
print("\nBest on the tuning nights, scored on the test nights (gain over nightly):")
for p in TIERS:
    grid = p.battery_kwh / EFFICIENCY * 365
    base = results[rules[0], p.name][0]
    best = min((r for r in rules[1:] if not results[r, p.name][1]), key=lambda r: results[r, p.name][0]["train"])
    cpk = results[best, p.name][0]
    print(f"  {p.name}: {best}: tuning ${(base['train'] - cpk['train']) * grid / 100:+.0f}/yr, "
          f"test ${(base['test'] - cpk['test']) * grid / 100:+.0f}/yr")
