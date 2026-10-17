"""The battery as a buffer across nights: the simulation every buffer_*.py script shares.

One Cybertruck (123 kWh, 90% charging efficiency) on an 11.5 kW Wall Connector, plugged in 20:00-07:00,
Delivery Time-of-Day, driven 15, 30 or 80 kWh a day. The battery is carried from night to night and is
never planned below 20% after the day's drive, nor above 90% after charging.

Each night a rule picks how much to add (`want`), the day-ahead planner picks the hours, and live rules
may add or skip charging as 5-minute prices are posted. Every decision uses only what was visible at the
time: tonight's day-ahead prices (released the afternoon before), the 5-minute feed as posted (7 minutes
after each interval starts) and the prices of earlier nights. Billing uses the settled hourly price.

A rule's options (`DEFAULTS` lists them):
- gamma: None charges each night's drive ("nightly plan"). A number fills up on cheap nights: with
  `rank` tonight's cheap-hours price among the last `back` nights (0 = cheapest),
  want = minimum + (most - minimum) * (1 - rank) ** gamma.
- live: ("q", p, margin) also charges whenever the hour's live estimate plus delivery is below the p-th
  percentile of recent cheap-night prices minus `margin`, up to the cap; ("fixed", cents) uses a fixed
  limit instead, as evcc does.
- skip: ("q", p, margin) drops a planned slot whose live estimate plus delivery is above the p-th
  percentile plus `margin`, as long as tonight's charge still covers the floor.
- add_only: top-ups use only the room above the plan instead of replacing planned charging still to come.
- confirm: a top-up also needs the newest posted 5-minute price under the limit.
- top, floor: the cap after charging and the floor after driving, as shares of the battery.
- back: how many earlier nights the percentiles look at. ref: "billed" compares live prices with
  earlier nights' billed cheap-hour prices instead of their day-ahead ones.
"""
import random
import statistics
import sys
from collections import defaultdict
from datetime import date, time, timedelta
from math import ceil
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from shared import database  # noqa: E402
from sparc.backtest import usable  # noqa: E402
from sparc.clock import HOUR, SLOT, at, hour_start  # noqa: E402
from sparc.market import Market, latest_visible_interval  # noqa: E402
from sparc.planner import Need, plan  # noqa: E402
from sparc.store import Store  # noqa: E402
from sparc.strategies import Session  # noqa: E402
from sparc.tariff import TIME_OF_DAY, capacity_risk  # noqa: E402

CAPACITY, EFFICIENCY, POWER, FLOOR = 123.0, 0.9, 11.5, 0.2
DRIVES = {"Average": 15.0, "Moderate": 30.0, "Heavy": 80.0}
PERIODS = ("2024", "2025", "2026")
STORM = frozenset(date(2026, 1, d) for d in range(25, 30))  # Jan 25-29, 2026: real-time prices down to -33 cents
DEFAULTS = {"gamma": None, "live": None, "top": 0.9, "floor": FLOOR, "back": 30, "add_only": False, "confirm": False,
            "skip": None, "ref": "day-ahead"}
Q20 = ("q", 20, 0.0)
STATS = None  # set to a dict to count starts, top-ups and skips

market = None  # the prices, set by load()
days: list[date] = []
FIRST, LAST = date(2024, 4, 20), date(2026, 10, 14)


def arguments():
    """The first and last night from the command line: python <script> [FIRST] [LAST]."""
    first = date.fromisoformat(sys.argv[1]) if len(sys.argv) > 1 else FIRST
    last = date.fromisoformat(sys.argv[2]) if len(sys.argv) > 2 else LAST
    return first, last


def load(first, last):
    """Read the prices for nights first..last (and 70 days before, for the look-backs)."""
    global market, days, FIRST, LAST
    store = Store(database())
    start, end = at(first - timedelta(days=70), time(0)), at(last + timedelta(days=3), time(0))
    market = Market(store.five_minute(start, end), store.day_ahead(start, end),
                    store.published_real_time(start, end), store.weather(start, end))
    store.close()
    days = [first + timedelta(days=i) for i in range((last - first).days + 1)]
    FIRST, LAST = first, last


def session(day, battery_kwh):
    return Session(at(day, time(20)), at(day + timedelta(days=1), time(7)), battery_kwh / EFFICIENCY, POWER)


def cheap_price(day, battery_kwh):
    """Mean day-ahead price plus delivery of the cheapest hours that charging battery_kwh takes tonight."""
    s = session(day, battery_kwh)
    prices = sorted(market.known_day_ahead(h, s.arrival) + TIME_OF_DAY.cents(h) for h in s.hours)
    hours = max(1, ceil(s.energy_kwh / POWER))
    return sum(prices[:hours]) / hours


def billed_cheap_price(day, battery_kwh):
    """Mean billed price plus delivery of the cheapest hours charging battery_kwh took that night (known by morning)."""
    s = session(day, battery_kwh)
    prices = sorted(market.hourly[h] + TIME_OF_DAY.cents(h) for h in s.hours)
    hours = max(1, ceil(s.energy_kwh / POWER))
    return sum(prices[:hours]) / hours


def hour_estimate(hour, now):
    """The hour's posted 5-minute prices, with the rest halfway between the latest posted one and day-ahead."""
    latest = latest_visible_interval(now)
    seen = [market.five_minute[t] for t in range(hour, min(hour + HOUR, latest + SLOT), SLOT)
            if t in market.five_minute]
    if not seen:
        return None
    tail = market.five_minute.get(latest, seen[-1])
    return (sum(seen) + (12 - len(seen)) * (0.5 * tail + 0.5 * market.known_day_ahead(hour, now))) / 12


def night(day, want, most, v, recent, minimum):
    """Charge `want` battery kWh in the day-ahead plan's hours, plus live top-ups up to `most`. -> ($, grid kWh)

    A top-up replaces planned charging still to come, unless v["add_only"]: then the plan runs as
    made and top-ups only use the room above it.
    """
    s = session(day, max(want, 0.01))
    planned, ceiling = want / EFFICIENCY, max(want, most) / EFFICIENCY
    full = s.slot_kwh
    schedule = {}
    if planned > 1e-9:
        costs = {h: market.known_day_ahead(h, s.arrival) + TIME_OF_DAY.cents(h) for h in s.hours}
        blocked = {h for h in s.hours if capacity_risk(h)}
        schedule = plan(Need(s.arrival, s.departure, planned, POWER, s.reserve_s), costs, blocked)
    live = v["live"] if len(recent) >= 10 else None
    threshold = None
    if live:  # ("q", percentile, margin) of recent cheap-night prices, or ("fixed", cents) as in evcc
        threshold = live[1] if live[0] == "fixed" else statistics.quantiles(recent, n=100)[live[1] - 1] - live[2]
    skip = v["skip"] if len(recent) >= 10 else None
    high = statistics.quantiles(recent, n=100)[skip[1] - 1] + skip[2] if skip else None
    floor_grid = minimum / EFFICIENCY
    delivered = done = extra = cost = 0.0  # all kWh, planned kWh, top-up kWh
    for now in range(s.arrival, s.departure, SLOT):
        hour = hour_start(now)
        kwh = min(schedule.get(now, 0.0), max(0.0, planned - (done if v["add_only"] else delivered)))
        if skip and kwh > 1e-9 and planned - kwh >= floor_grid - 1e-9:
            estimate = hour_estimate(hour, now)
            if estimate is not None and estimate + TIME_OF_DAY.cents(hour) > high:
                if STATS is not None:
                    STATS["skipped_kwh"] += kwh
                    STATS["skipped_cost"] += kwh * (market.hourly[hour] + TIME_OF_DAY.cents(hour)) / 100
                planned -= kwh  # skip it: tonight's target drops, never below what the floor needs
                ceiling = max(ceiling - kwh, planned)
                kwh = 0.0
        room = (ceiling - planned - extra) if v["add_only"] else (ceiling - delivered - kwh)
        top_up = 0.0
        if live and kwh < full - 1e-9 and room > 1e-9:
            estimate = hour_estimate(hour, now)
            fire = estimate is not None and estimate + TIME_OF_DAY.cents(hour) < threshold
            if fire and v["confirm"]:
                newest = market.five_minute.get(latest_visible_interval(now))
                fire = newest is not None and newest + TIME_OF_DAY.cents(hour) < threshold
            if fire:
                top_up = min(full - kwh, room)
        cost += (kwh + top_up) * (market.hourly[hour] + TIME_OF_DAY.cents(hour)) / 100
        if STATS is not None:
            on = kwh + top_up > 1e-9
            STATS["starts"] += on and not STATS.get("_on", False)
            STATS["_on"] = on
            STATS["top_up_kwh"] += top_up
            STATS["top_up_cost"] += top_up * (market.hourly[hour] + TIME_OF_DAY.cents(hour)) / 100
        delivered += kwh + top_up
        done += kwh
        extra += top_up
    return cost, delivered


def simulate(name, options, skip=frozenset()):
    """Every night for one driver under one rule -> ({month: [$, grid kWh]}, lowest battery kWh after a drive)."""
    v = {**DEFAULTS, **options}
    drive, battery, history, billed, floor = DRIVES[name], 0.5 * CAPACITY, [], [], v["floor"] * CAPACITY
    months = defaultdict(lambda: [0.0, 0.0])
    lowest = CAPACITY
    for day in days:
        if day in skip or not usable(session(day, drive), market):
            continue
        price = cheap_price(day, drive)
        minimum = max(0.0, drive + floor - battery)
        most = max(minimum, v["top"] * CAPACITY - battery)
        recent = history[-v["back"]:]
        if v["gamma"] is None or len(recent) < 10:
            want = max(minimum, min(drive, most))
        else:
            rank = sum(1 for x in recent if x < price) / len(recent)
            want = minimum + (most - minimum) * (1 - rank) ** v["gamma"]
        reference = billed[-v["back"]:] if v["ref"] == "billed" else recent
        cost, grid = night(day, want, most, v, reference, minimum)
        month = months[day.strftime("%Y-%m")]
        month[0] += cost
        month[1] += grid
        battery = battery + grid * EFFICIENCY - drive
        lowest = min(lowest, battery)
        battery = max(battery, floor)
        history.append(price)
        billed.append(billed_cheap_price(day, drive))
    return dict(months), lowest


def cents(months):
    """Average billed cents per grid kWh over a set of months."""
    return 100 * sum(c for c, _ in months.values()) / sum(k for _, k in months.values())


def gain(base, variant, months, per_year):
    cb, kb = sum(base[m][0] for m in months), sum(base[m][1] for m in months)
    cv, kv = sum(variant[m][0] for m in months), sum(variant[m][1] for m in months)
    return (cb / kb - cv / kv) * per_year if kb and kv else float("nan")


def run(variants):
    """Every driver under every rule, on all nights and without the January 2026 storm."""
    return {(name, label, bool(skip)): simulate(name, options, skip)
            for name in DRIVES for label, options in variants for skip in (frozenset(), STORM)}


def report(results, variants, increments=()):
    """$ a year saved against the nightly plan, by calendar year, with standard errors from resampling months.

    `increments` lists (rule, the rule it adds to, title): the same, measured against that rule.
    """
    rng = random.Random(1)

    def cells(reference, reference_storm, months, months_storm, per_year):
        out = []
        for period, b, v in [(p, reference, months) for p in PERIODS] + [("2026", reference_storm, months_storm)]:
            chosen = sorted(m for m in v if m.startswith(period))
            if not chosen:
                out.append(f"{'-':>16}")
                continue
            boot = [gain(b, v, [rng.choice(chosen) for _ in chosen], per_year) for _ in range(400)]
            out.append(f"{gain(b, v, chosen, per_year):+10.0f} ±{statistics.stdev(boot):3.0f}")
        return "".join(out)

    print(f"Data {database()}, nights {FIRST} to {LAST}")
    for name, drive in DRIVES.items():
        per_year = drive / EFFICIENCY * 365
        base, base_storm = results[name, "nightly plan", False][0], results[name, "nightly plan", True][0]
        print(f"\n{name} ({drive:g} kWh a day): $ a year saved against the nightly plan, ± 1 SE (months resampled)")
        print(f"  {'':<34}" + "".join(f"{p:>16}" for p in PERIODS) + f"{'2026 no storm':>16}{'lowest':>8}")
        for label, _ in variants:
            months, lowest = results[name, label, False]
            row = cells(base, base_storm, months, results[name, label, True][0], per_year)
            print(f"  {label:<34}" + row + f"{lowest / CAPACITY:8.0%}")
        if increments:
            print("  each rule against the one it adds to (same months resampled):")
        for label, reference, title in increments:
            row = cells(results[name, reference, False][0], results[name, reference, True][0],
                        results[name, label, False][0], results[name, label, True][0], per_year)
            print(f"    {title:<34}" + row)
        nights = {p: sum(1 for d in days if str(d.year) == p and usable(session(d, drive), market)) for p in PERIODS}
        average = {p: 100 * sum(base[m][0] for m in base if m.startswith(p)) /
                   max(1e-9, sum(base[m][1] for m in base if m.startswith(p))) for p in PERIODS}
        print("  nights: " + ", ".join(f"{p} {nights[p]}" for p in PERIODS)
              + "; nightly plan ¢/kWh: " + ", ".join(f"{p} {average[p]:.2f}" for p in PERIODS if nights[p]))
