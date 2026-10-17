"""Month-by-month check of the fill-up rules chosen on 2025 against nightly charging, on 2026 test months.

The rules are fillup_rules.py's best on Apr-Dec 2025 nights for each driver.

    python experiments/battery_buffer/fillup_months.py
"""
import statistics
import sys
from collections import defaultdict

sys.argv[1:] = []  # the default nights: 2025-04-02 to 2026-10-14, split at 2026-01-01
from fillup_rules import (CAPACITY, EFFICIENCY, TIERS, TIME_OF_DAY, DayAhead, days, market, session,  # noqa: E402
                          simulate, tonight_price, usable)


def monthly(profile, rule, low, high, top):
    """Like run(), but cost and kWh per calendar month, plus the battery's lowest level."""
    drive, battery, history = profile.battery_kwh, 0.5 * CAPACITY, []
    months = defaultdict(lambda: [0.0, 0.0])
    lowest = CAPACITY
    for day in days:
        s = session(day, drive)
        if not usable(s, market):
            continue
        price = tonight_price(day, drive)
        floor = 0.2 * CAPACITY
        minimum = max(0.0, drive + floor - battery)
        most = max(minimum, top * CAPACITY - battery)
        if rule == "nightly":
            want = max(minimum, drive)
        else:
            recent = history[-30:]
            q = statistics.quantiles(recent, n=20) if len(recent) >= 10 else None
            cheap = q is not None and price <= q[int(20 * low) - 1]
            dear = q is not None and price >= q[int(20 * high) - 1]
            want = most if cheap else minimum if dear else max(minimum, drive)
        want = min(want, max(0.0, CAPACITY - battery))
        got = 0.0
        if want > 0:
            outcome = simulate(session(day, want), DayAhead(), market, TIME_OF_DAY)
            got = outcome.energy_kwh * EFFICIENCY
            months[f"{day:%Y-%m}"][0] += outcome.cost
            months[f"{day:%Y-%m}"][1] += outcome.energy_kwh
        battery = max(floor, battery + got - drive)
        lowest = min(lowest, battery)
        history.append(price)
    return months, lowest


picks = {"Average": ("percentile", 0.4, 0.6, 0.9), "Moderate": ("percentile", 0.3, 0.7, 1.0),
         "Heavy": ("percentile", 0.4, 0.6, 1.0)}
for p in TIERS:
    base, _ = monthly(p, "nightly", 0, 0, 0.9)
    pick, lowest = monthly(p, *picks[p.name])
    wins = [m for m in base if m >= "2026-01" and 100 * pick[m][0] / pick[m][1] < 100 * base[m][0] / base[m][1]]
    test = [m for m in base if m >= "2026-01"]
    grid = p.battery_kwh / EFFICIENCY * 365
    def cpk(t, months): return 100 * sum(t[m][0] for m in months) / sum(t[m][1] for m in months)
    gain = {period: (cpk(base, ms) - cpk(pick, ms)) * grid / 100
            for period, ms in (("2025", [m for m in base if m < "2026-01"]), ("2026", test))}
    print(f"{p.name}: rule {picks[p.name]}; 2026 months won {len(wins)}/{len(test)}; extra saving a year "
          f"2025 ${gain['2025']:.0f}, 2026 ${gain['2026']:.0f}; battery never below {lowest:.0f} kWh")
