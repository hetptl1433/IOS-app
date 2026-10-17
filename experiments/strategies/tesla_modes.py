"""SPARC's plan against both modes of Tesla's scheduler, tiers 8 pm to 7 am, Time-of-Day delivery.

Scheduled charging: start at a fixed time (the backtest's timers). Off-peak charging under scheduled
departure: start as late as possible so charging ends at a fixed off-peak end time.
"""
import json
import math
import sys
from datetime import date, time, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from shared import TIERS_RUN, database  # noqa: E402
from sparc.backtest import TIERS, usable  # noqa: E402
from sparc.clock import DAY, SLOT, at, hour_start  # noqa: E402
from sparc.market import Market  # noqa: E402
from sparc.store import Store  # noqa: E402
from sparc.tariff import STANDARD, TIME_OF_DAY  # noqa: E402

store = Store(database())
first, last = date(2025, 4, 2), date(2026, 10, 14)
start, end = at(first, time(0)) - 2 * DAY, at(last + timedelta(days=2), time(0))
market = Market(store.five_minute(start, end), store.day_ahead(start, end), store.published_real_time(start, end))
run = json.loads(TIERS_RUN.read_text())  # the plan and timers, from the tiers backtest
saved = {(r["profile"], r["strategy"]): r["saved_per_year"] for r in run["default_setup"]}


def cost(slots, delivery):
    return sum(kwh * (market.hourly[hour_start(t)] + delivery.cents(hour_start(t))) / 100 for t, kwh in slots)


for profile in TIERS:
    results = {}
    for finish in (time(5), time(6), time(7)):
        total = default_total = nights = 0
        for i in range((last - first).days + 1):
            day = first + timedelta(days=i)
            session = profile.session(day)
            if not usable(session, market):
                continue
            nights += 1
            done = min(at(day + timedelta(days=1), finish), session.departure)
            count = math.ceil(session.energy_kwh / session.slot_kwh - 1e-9)
            slots, remaining = [], session.energy_kwh
            for t in range(done - count * SLOT, done, SLOT):  # the last slots before the off-peak end
                kwh = min(session.slot_kwh, remaining)
                slots.append((t, kwh))
                remaining -= kwh
            total += cost(slots, TIME_OF_DAY)
            first_slots, remaining = [], session.energy_kwh
            for t in range(session.arrival, session.departure, SLOT):
                if remaining <= 1e-9:
                    break
                kwh = min(session.slot_kwh, remaining)
                first_slots.append((t, kwh))
                remaining -= kwh
            default_total += cost(first_slots, STANDARD)
        results[finish] = 365 * (default_total - total) / nights
    plan = saved[profile.name, "Day-ahead plan"]
    timers = {f"start {t}": saved[profile.name, f"Timer {t}"] for t in ("21:00", "22:00", "23:00", "00:00", "01:00")}
    print(profile.name, f"plan ${plan:,.1f}")
    for label, value in timers.items():
        print(f"   scheduled charging, {label}: ${value:,.1f}  -> plan saves ${plan - value:,.1f} more")
    for finish, value in results.items():
        print(f"   off-peak charging, finish by {finish:%H:%M}: ${value:,.1f}  -> plan saves ${plan - value:,.1f} more")
