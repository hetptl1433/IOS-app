"""What if ComEd billed each 5-minute interval at its own price? Tiers, 8 pm to 7 am, Time-of-Day delivery.

Real billing: every kWh pays its hour's average. Hypothetical: every kWh pays its own 5-minute price.
Perfect hindsight at 5-minute level is the ceiling: no real strategy can know a 5-minute price before
it is posted, about 7 minutes after the interval starts.
"""
import sys
from datetime import date, time, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from shared import database  # noqa: E402
from sparc.backtest import TIERS, usable  # noqa: E402
from sparc.clock import DAY, SLOT, at, hour_start  # noqa: E402
from sparc.market import Market  # noqa: E402
from sparc.store import Store  # noqa: E402
from sparc.strategies import ChargeNow, DayAhead  # noqa: E402
from sparc.tariff import STANDARD, TIME_OF_DAY  # noqa: E402

store = Store(database())
first, last = date(2025, 4, 2), date(2026, 10, 14)
start, end = at(first, time(0)) - 2 * DAY, at(last + timedelta(days=2), time(0))
market = Market(store.five_minute(start, end), store.day_ahead(start, end), store.published_real_time(start, end))
days = [first + timedelta(days=i) for i in range((last - first).days + 1)]


def price5(t):
    return market.five_minute.get(t, market.hourly[hour_start(t)])


def billed(outcome_slots, delivery, five_minute):
    return sum(kwh * ((price5(t) if five_minute else market.hourly[hour_start(t)]) + delivery.cents(hour_start(t))) / 100
               for t, kwh in outcome_slots)


def run_slots(session, strategy, delivery):
    """The strategy's kWh in each slot, as backtest.simulate charges it."""
    strategy.begin(session, market, delivery)
    slots, remaining, charging = [], session.energy_kwh, False
    for now in range(session.arrival, session.departure, SLOT):
        kwh = max(0.0, min(strategy.energy(now, remaining, charging), session.slot_kwh, remaining)) if remaining > 1e-9 else 0.0
        if kwh > 1e-9:
            slots.append((now, kwh))
        remaining -= kwh
        charging = kwh > 1e-9 and kwh >= session.slot_kwh - 1e-9 and remaining > 1e-9
    return slots


def slot_hindsight(session, delivery, five_minute):
    """Fill the cheapest slots first, before the finish-early buffer when possible."""
    soft = session.departure - session.reserve_s
    cost = lambda t: (price5(t) if five_minute else market.hourly[hour_start(t)]) + delivery.cents(hour_start(t))
    order = sorted(range(session.arrival, session.departure, SLOT), key=lambda t: (t >= soft, cost(t), t))
    slots, remaining = [], session.energy_kwh
    for t in order:
        if remaining <= 1e-9:
            break
        kwh = min(session.slot_kwh, remaining)
        slots.append((t, kwh))
        remaining -= kwh
    return slots


totals = {}
for profile in TIERS:
    sums, nights = {}, 0
    for day in days:
        session = profile.session(day)
        if not usable(session, market):
            continue
        nights += 1
        default = run_slots(session, ChargeNow(), STANDARD)
        plan = run_slots(session, DayAhead(), TIME_OF_DAY)
        for key, value in {
            "default, hourly billing": billed(default, STANDARD, False),
            "default, 5-minute billing": billed(default, STANDARD, True),
            "plan, hourly billing": billed(plan, TIME_OF_DAY, False),
            "plan, 5-minute billing": billed(plan, TIME_OF_DAY, True),
            "hindsight by hour, hourly billing": billed(slot_hindsight(session, TIME_OF_DAY, False), TIME_OF_DAY, False),
            "hindsight by 5 minutes, 5-minute billing": billed(slot_hindsight(session, TIME_OF_DAY, True), TIME_OF_DAY, True),
        }.items():
            sums[key] = sums.get(key, 0.0) + value
    means = {k: v / nights for k, v in sums.items()}
    print(f"{profile.name} ({nights} nights)")
    for billing in ("hourly", "5-minute"):
        default = means[f"default, {billing} billing"]
        print(f"  {billing} billing: default ${default:.3f}/night; plan saves ${365 * (default - means[f'plan, {billing} billing']):,.0f}/yr")
    print(f"  ceiling, real hourly billing:        ${365 * (means['default, hourly billing'] - means['hindsight by hour, hourly billing']):,.0f}/yr")
    print(f"  ceiling, hypothetical 5-minute bill: ${365 * (means['default, 5-minute billing'] - means['hindsight by 5 minutes, 5-minute billing']):,.0f}/yr")
