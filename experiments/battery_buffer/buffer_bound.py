"""How much could a battery buffer save at best? Linear programs over every night with hindsight.

- nightly hindsight: each night buys exactly its own drive in its cheapest real-time hours.
- buffer hindsight: knows every future real-time price; any night may buy for later ones.
- buffer, day-ahead foresight: knows every future day-ahead price (not real-time), billed real-time.
Battery between 20% (after the day's drive) and 90% (after charging), starting and ending at 50%.

Needs numpy and scipy (pip install scipy).

    python experiments/battery_buffer/buffer_bound.py [FIRST] [LAST]    (default 2024-04-20 2026-10-14)
"""
from collections import defaultdict

import numpy as np
from scipy.optimize import linprog
from scipy.sparse import lil_matrix

import buffer as y

TOP = 0.9


def nights(drive):
    out = []
    for day in y.days:
        s = y.session(day, drive)
        if y.usable(s, y.market):
            out.append((day, list(s.hours)))
    return out


def solve(drive, plan_cents, top=TOP):
    """Grid kWh per (night, hour) minimising sum(plan_cents * kWh) under the battery limits."""
    rows = nights(drive)
    index = [(i, h) for i, (_, hours) in enumerate(rows) for h in hours]
    n, m = len(rows), len(index)
    start = 0.5 * y.CAPACITY
    # Variables: the kWh of each (night, hour), then the battery after charging on each night.
    cost = np.concatenate([np.array([plan_cents(h) for _, h in index]), np.zeros(n)])
    a = lil_matrix((n + 1, m + n))
    b = np.zeros(n + 1)
    for column, (i, _) in enumerate(index):
        a[i, column] = y.EFFICIENCY
    for i in range(n):
        a[i, m + i] = -1.0  # after charging = before + efficiency * kWh
        if i:
            a[i, m + i - 1] = 1.0
            b[i] = drive
        else:
            b[i] = -start
    a[n, m + n - 1] = 1.0  # and it ends where it started, or as high as a heavy driver can
    b[n] = min(start, top * y.CAPACITY - drive) + drive
    bounds = [(0, y.POWER)] * m + [(y.FLOOR * y.CAPACITY + drive, top * y.CAPACITY)] * n
    result = linprog(cost, A_eq=a.tocsr(), b_eq=b, bounds=bounds, method="highs")
    assert result.status == 0, result.message
    return {key: kwh for key, kwh in zip(index, result.x[:m])}, rows


def nightly_hindsight(drive):
    plan = {}
    for i, (_, hours) in enumerate(nights(drive)):
        need = drive / y.EFFICIENCY
        for h in sorted(hours, key=billed):
            take = min(y.POWER, need)
            plan[i, h] = take
            need -= take
            if need <= 1e-9:
                break
    return plan, nights(drive)


def billed(h):
    return y.market.hourly[h] + y.TIME_OF_DAY.cents(h)


def by_period(plan, rows):
    totals = defaultdict(lambda: [0.0, 0.0])
    for (i, h), kwh in plan.items():
        period = str(rows[i][0].year)
        totals[period][0] += kwh * billed(h) / 100
        totals[period][1] += kwh
    return {p: 100 * c / k for p, (c, k) in totals.items() if k}


if __name__ == "__main__":
    y.load(*y.arguments())
    print(f"¢/kWh billed (and $ a year against the nightly day-ahead plan, as in buffer.py), nights {y.FIRST}-{y.LAST}")
    for name, drive in y.DRIVES.items():
        per_year = drive / y.EFFICIENCY * 365
        months, _ = y.simulate(name, {})
        base = {}
        for p in y.PERIODS:
            chosen = [m for m in months if m.startswith(p)]
            if chosen:
                base[p] = 100 * sum(months[m][0] for m in chosen) / sum(months[m][1] for m in chosen)
        cases = {"nightly hindsight": nightly_hindsight(drive),
                 "buffer hindsight": solve(drive, billed),
                 "buffer, day-ahead foresight": solve(drive, lambda h: y.market.day_ahead[h] + y.TIME_OF_DAY.cents(h))}
        print(f"\n{name}: nightly day-ahead plan " + ", ".join(f"{p} {c:.3f}" for p, c in base.items()))
        for label, (plan, rows) in cases.items():
            cents = by_period(plan, rows)
            print(f"  {label:<30}" + "   ".join(f"{p} {cents[p]:.3f} (${(base[p] - cents[p]) * per_year / 100:+.0f})"
                                               for p in base))
