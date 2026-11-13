"""Run: python3 results/sparc-live-replay/replay_15_nights.py

Closed-loop replay of the Pi's live SPARC planner, 15 nights, Cybertruck 50% -> 90%.

Every 5 minutes from plug-in, the Pi's own make_plan() is called with ONLY prices
published by that moment (make_plan applies the ~7-minute real-time delay and the
5 PM day-ahead release itself), and the car charges exactly as that plan says until
the next replan. Billing then uses ComEd's settled hourly prices.
"""
import json, re, sys
from pathlib import Path
HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
from datetime import datetime, date, time, timedelta, timezone
from zoneinfo import ZoneInfo

sys.path.insert(0, str(REPO / "controller"))
from sparc_controller.smart_charging import make_plan
from sparc_controller.billing import cents, fixed_supply
from sparc_controller.history import immediate_buckets

CH = ZoneInfo("America/Chicago"); UTC = timezone.utc
CAP, POWER, ETA = 123.0, 11.5, 0.9          # Cybertruck usable kWh, 48 A Wall Connector, charging efficiency
START_PCT, TARGET_PCT = 50.0, 90.0
RESERVE = 30 * 60                            # ready 30 min before 7 AM (Pi replay default)
FIRST, LAST = date(2026, 10, 29), date(2026, 11, 12)

raw = json.load(open(HERE / "data/comed-2026-10-27-to-11-13.json"))
five = {int(k): v for k, v in raw["five"].items()}
table = {int(k): v for k, v in raw["hourly"].items()}
# What the Pi itself could parse: day-ahead from tables that parsed (Nov 1 did not).
day_ahead = {h: v["da"] for h, v in table.items() if v["da"] is not None}

# Billing prices: ComEd's settled hourly price; else the mean of 10+ five-minute prices.
settled = {h: v["rt"] for h, v in table.items() if v["rt"] is not None}
# Nov 1 (DST end): ComEd's table has 24 rows; map them by hand for billing only.
nov1 = [3.1, 28.8, 2.9, 2.6, 2.9, 3.1, 2.7, 3.0, 2.8, 4.1, 2.6, 2.3, 3.0, 3.1, 3.2, 3.4, 2.9, 2.4, 3.1, 2.9,
        3.0, 2.2, 3.1, 2.2, 3.1, 2.3, 3.2, 2.6, 3.2, 2.9, 3.4, 3.3, 4.6, 4.7, 8.6, 4.9, 7.1, 4.8, 4.5, 4.4,
        3.8, 5.3, 3.4, 3.3, 3.1, 3.4, 2.7, 2.6]
rt_rows = nov1[1::2]
base = int(datetime(2026, 11, 1, 4, tzinfo=UTC).timestamp())   # 11 PM CDT Oct 31
for i, price in enumerate(rt_rows):
    if i <= 1: hours = [base + i * 3600]
    elif i == 2: hours = [base + 2 * 3600, base + 3 * 3600]       # "2:00 a.m." row spans both 1 AMs
    else: hours = [base + (i + 1) * 3600]
    for h in hours: settled.setdefault(h, price)
def hour_price(h):
    if h in settled: return settled[h]
    vals = [five[t] for t in range(h, h + 3600, 300) if t in five]
    return sum(vals) / len(vals) if len(vals) >= 10 else None

def tod(h): return cents(datetime.fromtimestamp(h, UTC))
def flat(h): return cents(datetime.fromtimestamp(h, UTC), standard=True)
def fixed(h): return fixed_supply(datetime.fromtimestamp(h, UTC))

def bill(buckets, supply, delivery):
    total = 0.0
    for h, kwh in buckets.items():
        s, d = supply(h), delivery(h)
        if s is None or d is None: return None
        total += kwh * (s + d) / 100
    return total

def iso(s): return datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()

def closed_loop(arrive, depart, delivery_fn):
    """Run the Pi's planner every 5 minutes; return grid kWh per UTC hour and log."""
    battery, charging, buckets = START_PCT, False, {}
    reasons, fallbacks = {}, 0
    t = arrive
    while t < depart:
        if battery >= TARGET_PCT - 1e-9: break
        now = datetime.fromtimestamp(t, UTC)
        hours = range(int(t) // 3600 * 3600, int(depart), 3600)
        delivery = {h: delivery_fn(h) for h in hours}
        plan = make_plan(now, datetime.fromtimestamp(arrive, UTC), datetime.fromtimestamp(depart, UTC),
                         battery, TARGET_PCT, day_ahead, five, previous_charging=charging, mode="live",
                         power_kw=POWER, capacity_kwh=CAP, efficiency=ETA, reserve_seconds=RESERVE,
                         delivery_cents=delivery)
        reasons[plan["reason"]] = reasons.get(plan["reason"], 0) + 1
        fallbacks += plan["fallback"]
        step_end = min(t + 300, depart)
        charged_to = None
        for iv in plan["intervals"]:
            a, b = max(iso(iv["start"]), t), min(iso(iv["end"]), step_end)
            if b <= a: continue
            need = (TARGET_PCT - battery) / 100 * CAP / ETA
            kwh = min((b - a) * POWER / 3600, need)
            if kwh <= 0: continue
            # split across hour boundaries (none inside a 5-min step, but be safe)
            h = int(a) // 3600 * 3600
            buckets[h] = buckets.get(h, 0.0) + kwh
            battery = min(TARGET_PCT, battery + kwh * ETA / CAP * 100)
            charged_to = a + kwh / POWER * 3600
        charging = charged_to is not None and charged_to >= step_end - 1
        t = step_end
    return buckets, battery, reasons, fallbacks

def hindsight(arrive, depart, supply, delivery, energy):
    """Best possible with perfect knowledge of settled prices (reference only)."""
    end = depart - RESERVE
    slots = []
    for h in range(int(arrive) // 3600 * 3600, int(end), 3600):
        a, b = max(h, arrive), min(h + 3600, end)
        if b > a and supply(h) is not None: slots.append((supply(h) + delivery(h), h, (b - a) * POWER / 3600))
    left, buckets = energy, {}
    for _, h, cap in sorted(slots):
        take = min(cap, left); buckets[h] = take; left -= take
        if left <= 1e-9: break
    return buckets

energy = (TARGET_PCT - START_PCT) / 100 * CAP / ETA
nights = []
day = FIRST
while day <= LAST:
    arrive = datetime.combine(day, time(18), CH).timestamp()
    depart = datetime.combine(day + timedelta(1), time(7), CH).timestamp()
    now_b = {int(datetime.fromisoformat(k.replace("Z", "+00:00")).timestamp()) if isinstance(k, str) else
             int(k.timestamp()) if hasattr(k, "timestamp") else int(k): v
             for k, v in immediate_buckets(datetime.fromtimestamp(arrive, UTC), energy, POWER).items()}
    sp_tod, bat_tod, why_tod, fb_tod = closed_loop(arrive, depart, tod)
    sp_flat, bat_flat, why_flat, fb_flat = closed_loop(arrive, depart, flat)
    row = {
        "night": day.isoformat(),
        "A_fixed_now": bill(now_b, fixed, flat),
        "B_hourly_now": bill(now_b, hour_price, flat),
        "C_hourly_sparc": bill(sp_flat, hour_price, flat),
        "D_hourlyTOD_now": bill(now_b, hour_price, tod),
        "E_hourlyTOD_sparc": bill(sp_tod, hour_price, tod),
        "H_hourlyTOD_hindsight": bill(hindsight(arrive, depart, hour_price, tod, energy), hour_price, tod),
        "kwh_E": sum(sp_tod.values()), "kwh_C": sum(sp_flat.values()), "kwh_now": sum(now_b.values()),
        "battery_E": bat_tod, "battery_C": bat_flat,
        "hours_E": sorted(datetime.fromtimestamp(h, CH).strftime("%H") for h, k in sp_tod.items() if k > 0.05),
        "reasons_E": why_tod, "fallback_steps_E": fb_tod,
        "avg_supply_E": None,
    }
    nights.append(row)
    print(row["night"], {k: round(v, 2) for k, v in row.items() if k[:2] in ("A_", "B_", "C_", "D_", "E_", "H_") and v is not None},
          "bat", round(bat_tod, 1), round(bat_flat, 1), "hrs", row["hours_E"], "fb", fb_tod, flush=True)
    day += timedelta(1)
json.dump({"energyKWhPerNight": energy, "nights": nights}, open(HERE / "out/cybertruck-15-nights.json", "w"), indent=1, default=str)
