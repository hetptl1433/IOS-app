"""More fill-up rules: proportional targets, different look-backs, weekend fills. Same setup as fillup_rules.py.

    python experiments/battery_buffer/fillup_rules2.py [FIRST LAST SPLIT]
"""
import statistics
from collections import defaultdict

from fillup_rules import (CAPACITY, EFFICIENCY, SPLIT, TIERS, TIME_OF_DAY, DayAhead, days, market, session,
                          simulate, tonight_price, usable)

# How cheap is each night of the week? (mean tonight_price for the average driver, tuning nights)
by_weekday = defaultdict(list)
for day in days:
    if day < SPLIT and usable(session(day, 15), market):
        by_weekday[day.strftime("%a")].append(tonight_price(day, 15))
print("mean cheap-hours price by plug-in night (c/kWh, incl. delivery), tuning nights:",
      {k: round(sum(v) / len(v), 2) for k, v in by_weekday.items()})


def run2(profile, rule, a, b, top):
    drive, battery, history = profile.battery_kwh, 0.5 * CAPACITY, []
    totals, short, floor = defaultdict(lambda: [0.0, 0.0]), 0, 0.2 * CAPACITY
    for day in days:
        if not usable(session(day, drive), market):
            continue
        price = tonight_price(day, drive)
        minimum = max(0.0, drive + floor - battery)
        most = max(minimum, top * CAPACITY - battery)
        normal = max(minimum, min(drive, most))
        recent = history[-int(b):] if b else history[-30:]
        if rule == "nightly":
            want = normal
        elif rule == "proportional" and len(recent) >= 10:
            rank = sum(1 for x in recent if x < price) / len(recent)  # 0 = cheapest of the look-back
            want = minimum + (most - minimum) * (1 - rank) ** a
        elif rule == "percentile" and len(recent) >= 10:
            q = statistics.quantiles(recent, n=20)
            want = most if price <= q[int(20 * a) - 1] else minimum if price >= q[int(20 * (1 - a)) - 1] else normal
        elif rule == "weekend-min":
            want = most if day.weekday() in (4, 5) else minimum
        else:
            want = normal
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


rules = [("nightly", 0, 0, 0.9)]
for top in (0.8, 0.9):
    for gamma in (0.5, 1, 2, 3):
        for back in (14, 30, 60):
            rules.append(("proportional", gamma, back, top))
    for share in (0.3, 0.4):
        for back in (14, 60):
            rules.append(("percentile", share, back, top))
    rules.append(("weekend-min", 0, 0, top))
results = {}
for rule in rules:
    for p in TIERS:
        results[rule, p.name] = run2(p, *rule)
print(f"{'rule':<34}" + "".join(f"{p.name + ' train/test c/kWh':>30}" for p in TIERS))
for rule in rules:
    print(f"{str(rule):<34}" + "".join(f"{results[rule, p.name][0]['train']:>14.3f} / {results[rule, p.name][0]['test']:<6.3f}"
                                       + (f"*{results[rule, p.name][1]}" if results[rule, p.name][1] else "  ") for p in TIERS))
print("\nBest on the tuning nights for each driver, scored on the test nights (gain over nightly):")
for p in TIERS:
    grid = p.battery_kwh / EFFICIENCY * 365
    base = results[("nightly", 0, 0, 0.9), p.name][0]
    best = min((r for r in rules if r[0] != "nightly" and not results[r, p.name][1]),
               key=lambda r: results[r, p.name][0]["train"])
    cpk = results[best, p.name][0]
    print(f"  {p.name}: {best}: tuning ${(base['train'] - cpk['train']) * grid / 100:+.0f}/yr, "
          f"test ${(base['test'] - cpk['test']) * grid / 100:+.0f}/yr")
