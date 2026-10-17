"""What knowing the driver's habits is worth. Tiers, 8 pm plug-in, Time-of-Day delivery, vs the default setup.

1. Knowing when they leave: the day-ahead plan told the car leaves at 7 a.m., against a cautious guess of
   5 or 6 a.m.
2. Knowing how much they drive: with a 123 kWh battery, the car need not refill every night. Upper bound
   with perfect price knowledge: charge each block of N nights' energy in the cheapest hours of those N nights.
"""
import sys
from datetime import date, time, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from shared import database  # noqa: E402
from sparc.backtest import TIERS, Profile, run, usable  # noqa: E402
from sparc.clock import DAY, SLOT, at, hour_start  # noqa: E402
from sparc.market import Market  # noqa: E402
from sparc.store import Store  # noqa: E402
from sparc.strategies import ChargeNow, DayAhead  # noqa: E402
from sparc.tariff import STANDARD, TIME_OF_DAY  # noqa: E402

store = Store(database())
first, last = date(2025, 4, 2), date(2026, 10, 14)
start, end = at(first, time(0)) - 2 * DAY, at(last + timedelta(days=9), time(0))
market = Market(store.five_minute(start, end), store.day_ahead(start, end), store.published_real_time(start, end))
days = [first + timedelta(days=i) for i in range((last - first).days + 1)]

print("1. Knowing when the driver leaves (the car really leaves at 7 a.m.):")
default_nights, _ = run(market, days, TIERS, [ChargeNow()], [STANDARD])
default = {(n.profile, n.day): n.outcome.cost for n in default_nights}
for guess in (time(5), time(6), time(7)):
    profiles = [Profile(p.name, p.arrive, guess, p.battery_kwh, p.power_kw) for p in TIERS]
    nights, _ = run(market, days, profiles, [DayAhead()], [TIME_OF_DAY])
    cells = []
    for p in TIERS:
        mine = [default[p.name, n.day] - n.outcome.cost for n in nights if n.profile == p.name and (p.name, n.day) in default]
        cells.append(f"{p.name} ${365 * sum(mine) / len(mine):,.0f}")
    print(f"   plan aims to finish by {guess:%H:%M} minus 30 min: " + ", ".join(cells))

print("2. Ceiling for refilling only on the cheapest nights (perfect price knowledge):")
for p in TIERS:
    cells = []
    for block in (1, 2, 3, 7):
        saved = count = 0
        for i in range(0, len(days) - block + 1, block):
            sessions = [p.session(days[i + k]) for k in range(block)]
            if not all(usable(s, market) for s in sessions):
                continue
            slots = sorted((market.hourly[hour_start(t)] + TIME_OF_DAY.cents(hour_start(t)), t)
                           for s in sessions for t in range(s.arrival, s.departure - s.reserve_s, SLOT))
            need, cost = block * sessions[0].energy_kwh, 0.0
            for price, t in slots:
                kwh = min(sessions[0].slot_kwh, need)
                cost += kwh * price / 100
                need -= kwh
                if need <= 1e-9:
                    break
            if need > 1e-9:
                continue
            base = sum(default[p.name, days[i + k]] for k in range(block))
            saved += base - cost
            count += block
        cells.append(f"every {block} night{'s' if block > 1 else ''}: ${365 * saved / count:,.0f}")
    print(f"   {p.name}: " + ", ".join(cells))
