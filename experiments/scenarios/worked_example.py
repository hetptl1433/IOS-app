"""One real night, hour by hour: what the default setup and SPARC's plan charge, and what each costs.

For the "How it works" page (results/how-it-works.html). The Average driver (Cybertruck, +15 kWh,
11.5 kW, plugged in 20:00-07:00) on the study's nights, 2025-04-02 to 2026-10-14. The night shown
is the one whose saving is closest to the period's average, among nights without price spikes,
so it is typical. Every number comes from `sparc.backtest.simulate`, the code behind every result.
Writes results/example.js and prints the same numbers.

    python experiments/scenarios/worked_example.py
"""
import json
import sys
from datetime import date, time, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from shared import ROOT, database  # noqa: E402
from sparc.backtest import TIERS, simulate, usable  # noqa: E402
from sparc.clock import DAY, HOUR, at, local  # noqa: E402
from sparc.market import Market  # noqa: E402
from sparc.store import Store  # noqa: E402
from sparc.strategies import ChargeNow, DayAhead, Hindsight  # noqa: E402
from sparc.tariff import STANDARD, TIME_OF_DAY  # noqa: E402

FIRST, LAST = date(2025, 4, 2), date(2026, 10, 14)
driver = TIERS[0]
store = Store(database())
start, end = at(FIRST, time(0)) - 2 * DAY, at(LAST + timedelta(days=2), time(0))
market = Market(store.five_minute(start, end), store.day_ahead(start, end), store.published_real_time(start, end))

nights = []
for i in range((LAST - FIRST).days + 1):
    day = FIRST + timedelta(days=i)
    session = driver.session(day)
    if not usable(session, market):
        continue
    default = simulate(session, ChargeNow(), market, STANDARD)
    plan = simulate(session, DayAhead(), market, TIME_OF_DAY)
    hindsight = simulate(session, Hindsight(), market, TIME_OF_DAY)
    calm = all(-2 <= market.hourly[h] <= 12 for h in session.hours)
    nights.append((day, default.cost, plan.cost, hindsight.cost, calm))

mean_default = sum(n[1] for n in nights) / len(nights)
mean_plan = sum(n[2] for n in nights) / len(nights)
mean_hindsight = sum(n[3] for n in nights) / len(nights)
mean_saving = mean_default - mean_plan
day, *_ = min((n for n in nights if n[4]), key=lambda n: abs((n[1] - n[2]) - mean_saving))

session = driver.session(day)
traces = {}
outcomes = {}
for key, strategy, delivery in (("default", ChargeNow(), STANDARD), ("plan", DayAhead(), TIME_OF_DAY)):
    traces[key] = {}
    outcomes[key] = simulate(session, strategy, market, delivery, trace=traces[key])

hours = []
for hour in session.hours:
    t = local(hour)
    label = t.strftime("%I %p").lstrip("0").replace("AM", "a.m.").replace("PM", "p.m.")
    hours.append({"label": f"{label} to {local(hour + HOUR).strftime('%I %p').lstrip('0').replace('AM', 'a.m.').replace('PM', 'p.m.')}",
                  "price": round(market.hourly[hour], 3), "day_ahead": round(market.day_ahead[hour], 3),
                  "standard": STANDARD.cents(hour), "tod": TIME_OF_DAY.cents(hour),
                  "default_kwh": round(traces["default"].get(hour, 0.0), 4),
                  "plan_kwh": round(traces["plan"].get(hour, 0.0), 4)})

for key, delivery in (("default", STANDARD), ("plan", TIME_OF_DAY)):  # the per-hour table adds up to the bill
    supply = sum(h[f"{key}_kwh"] * h["price"] for h in hours) / 100
    assert abs(supply - outcomes[key].supply) < 0.005, (key, supply, outcomes[key].supply)

example = {
    "night": day.isoformat(), "weekday": f"{day:%A}",
    "driver": {"name": driver.name, "battery_kwh": driver.battery_kwh, "efficiency": driver.efficiency,
               "grid_kwh": round(session.energy_kwh, 3), "power_kw": driver.power_kw,
               "slot_kwh": round(session.slot_kwh, 4), "arrive": f"{driver.arrive:%H:%M}",
               "depart": f"{driver.depart:%H:%M}", "reserve_min": driver.reserve_min},
    "hours": hours,
    "default": {"supply": round(outcomes["default"].supply, 4), "delivery": round(outcomes["default"].delivery, 4),
                "cost": round(outcomes["default"].cost, 4)},
    "plan": {"supply": round(outcomes["plan"].supply, 4), "delivery": round(outcomes["plan"].delivery, 4),
             "cost": round(outcomes["plan"].cost, 4)},
    "delivery_rates": {"standard": STANDARD.bands, "tod": TIME_OF_DAY.bands},
    "period": {"first": FIRST.isoformat(), "last": LAST.isoformat(), "nights": len(nights),
               "default_mean": round(mean_default, 5), "plan_mean": round(mean_plan, 5),
               "hindsight_mean": round(mean_hindsight, 5), "saved_per_year": round(mean_saving * 365, 4)},
}

out = ROOT / "results" / "example.js"
out.write_text("// One real night for results/how-it-works.html, written by experiments/scenarios/worked_example.py.\n"
               "// Every number comes from sparc.backtest.simulate.\n"
               f"window.SPARC_EXAMPLE = {json.dumps(example, indent=1)};\n")

print(f"Average driver, night of {day:%A %Y-%m-%d} (saving closest to the average of {len(nights)} nights)")
print(f"{'hour':<22}{'price':>7}{'day-ahead':>11}{'std':>7}{'ToD':>7}{'default kWh':>13}{'plan kWh':>10}")
for h in hours:
    print(f"{h['label']:<22}{h['price']:>7.2f}{h['day_ahead']:>11.2f}{h['standard']:>7.3f}{h['tod']:>7.3f}"
          f"{h['default_kwh']:>13.3f}{h['plan_kwh']:>10.3f}")
for key in ("default", "plan"):
    o = example[key]
    print(f"{key:>8}: energy ${o['supply']:.2f} + delivery ${o['delivery']:.2f} = ${o['cost']:.2f}")
print(f"saving this night: ${example['default']['cost'] - example['plan']['cost']:.2f}")
p = example["period"]
print(f"all {p['nights']} nights: default ${p['default_mean']:.4f}/night, plan ${p['plan_mean']:.4f}/night, "
      f"saving ${p['default_mean'] - p['plan_mean']:.4f}/night = ${p['saved_per_year']:.1f} a year")
print(f"wrote {out}")
