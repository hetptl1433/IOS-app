"""Full-history replay (TeslaMate car, or one of three Tesla Charge Stats cars): each night's real home charging energy, plugged in 6 PM -> 7 AM.

Energy: TeslaMate home AC sessions (kwh_used, from the wall), grouped noon-to-noon, so
a 1 AM session belongs to the night that began the evening before.
Prices: real ComEd 5-minute and hourly (day-ahead + settled) prices from comed-data/.
Decisions: the Pi's own make_plan() every 5 minutes, seeing only prices published by then.
Fixed supply: that month's real ComEd fixed price. Delivery: today's (Nov 2026) standard
and Time-of-Day delivery rates applied to every night (no TOD history is in the repo).
"""
import csv, json, sys
from bisect import bisect_left, bisect_right
from collections import defaultdict
from datetime import datetime, date, time, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
import subprocess
sys.path.insert(0, str(REPO / "controller"))
from sparc_controller.smart_charging import make_plan
from sparc_controller.billing import cents

CH = ZoneInfo("America/Chicago"); UTC = timezone.utc
POWER, RESERVE, CAP = 11.5, 30 * 60, 200.0
WINDOW_KWH = 12.5 * POWER

def stamp(s): return int(datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp())

five, ahead, settled = {}, {}, {}
for y in (2022, 2023, 2024, 2025, 2026):
    for r in csv.DictReader(open(REPO / f"comed-data/five_minute/{y}.csv")):
        five[stamp(r["start_utc"])] = float(r["price_cents"])
    if not (REPO / f"comed-data/hourly/{y}.csv").exists(): continue
    for r in csv.DictReader(open(REPO / f"comed-data/hourly/{y}.csv")):
        h = stamp(r["hour_utc"])
        if r["day_ahead_cents"]: ahead[h] = float(r["day_ahead_cents"])
        if r["real_time_cents"]: settled[h] = float(r["real_time_cents"])
if (HERE / "data/comed-hourly-2022-05-30-to-2024-01-01.json").exists():
    # Downloaded from ComEd's hourly table with the Pi's own parser (DST days fail, as on the Pi).
    for h, v in json.load(open(HERE / "data/comed-hourly-2022-05-30-to-2024-01-01.json"))["hourly"].items():
        if v["da"] is not None: ahead.setdefault(int(h), v["da"])
        if v["rt"] is not None: settled.setdefault(int(h), v["rt"])
five_keys = sorted(five); ahead_keys = sorted(ahead)

js = open(REPO / "night-shift/dist/comed-cost-data.js").read()
fixed_by_month = {m: v["fixed_price"] for m, v in json.loads(js[js.index("{"):js.rindex("}") + 1])["rates"].items()}

def hour_price(h):
    if h in settled: return settled[h]
    v = [five[t] for t in range(h, h + 3600, 300) if t in five]
    return sum(v) / len(v) if len(v) >= 10 else None

def today_rate(h, standard):
    local = datetime.fromtimestamp(h, CH)
    return cents(datetime(2026, 11, 16, local.hour, tzinfo=CH), standard=standard)

def fixed(h): return fixed_by_month[datetime.fromtimestamp(h, CH).strftime("%Y-%m")]
def tod(h): return today_rate(h, False)
def flat(h): return today_rate(h, True)

def bill(buckets, supply, delivery):
    total = 0.0
    for h, kwh in buckets.items():
        s = supply(h)
        if s is None: return None
        total += kwh * (s + delivery(h)) / 100
    return total

def window(keys, src, a, b): return {k: src[k] for k in keys[bisect_left(keys, a):bisect_right(keys, b)]}
def iso(s): return datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()

def at_six(arrive, energy):
    b, left, t = {}, energy, arrive
    while left > 1e-9:
        h = int(t) // 3600 * 3600; take = min(left, (h + 3600 - t) * POWER / 3600)
        b[h] = b.get(h, 0) + take; left -= take; t = h + 3600
    return b

def sparc(arrive, depart, energy):
    target = energy / CAP * 100
    samples = window(five_keys, five, int(arrive) - 30 * 3600, int(depart))
    da = window(ahead_keys, ahead, int(arrive) - 2 * 86400, int(depart) + 86400)
    got, charging, buckets, fallback = 0.0, False, {}, 0
    t = arrive
    while t < depart and got < energy - 1e-9:
        delivery = {h: tod(h) for h in range(int(t) // 3600 * 3600, int(depart), 3600)}
        plan = make_plan(datetime.fromtimestamp(t, UTC), datetime.fromtimestamp(arrive, UTC),
                         datetime.fromtimestamp(depart, UTC), got / CAP * 100, target, da, samples,
                         previous_charging=charging, mode="live", power_kw=POWER, capacity_kwh=CAP,
                         efficiency=1.0, reserve_seconds=RESERVE, delivery_cents=delivery)
        fallback += plan["fallback"]
        end, charged_to = min(t + 300, depart), None
        for iv in plan["intervals"]:
            a, b = max(iso(iv["start"]), t), min(iso(iv["end"]), end)
            kwh = min((b - a) * POWER / 3600, energy - got) if b > a else 0
            if kwh <= 0: continue
            h = int(a) // 3600 * 3600
            buckets[h] = buckets.get(h, 0) + kwh; got += kwh; charged_to = a + kwh / POWER * 3600
        charging = charged_to is not None and charged_to >= end - 1
        t = end
    return buckets, got, fallback

def hindsight(arrive, depart, energy):
    end, slots = depart - RESERVE, []
    for h in range(int(arrive) // 3600 * 3600, int(end), 3600):
        a, b = max(h, arrive), min(h + 3600, end)
        if b > a and hour_price(h) is not None: slots.append((hour_price(h) + tod(h), h, (b - a) * POWER / 3600))
    left, out = energy, {}
    for _, h, cap in sorted(slots):
        take = min(cap, left); out[h] = take; left -= take
        if left <= 1e-9: break
    return out

# Usage:
#   replay_history.py teslamate
#   replay_history.py car0001|car0003|car0006 FIRST LAST
# Car files are Tesla app Charge Stats exports, removed from the working tree but kept in
# commit 2e97af2; they are read from there with git show.
CARS = {"car0001": "car0001_zip60025_tesla-X-LR-2022", "car0003": "car0003_zip60056_tesla-Y-LR-2024",
        "car0006": "car0006_zip60025_tesla-3-LR-2023"}
which = sys.argv[1] if len(sys.argv) > 1 else "teslamate"
nights = defaultdict(float)
if which in CARS:
    first, last = date.fromisoformat(sys.argv[2]), date.fromisoformat(sys.argv[3])
    text = subprocess.run(["git", "-C", str(REPO), "show", f"2e97af2:tesla_datasets_all_20261104-220003/{CARS[which]}.csv"],
                          check=True, capture_output=True, text=True).stdout
    # Day D's home kWh is charged in night D (6 PM -> 7 AM).
    for r in csv.DictReader(text.splitlines()):
        d, kwh = date.fromisoformat(r["date"]), float(r["home_kwh"])
        if kwh > 0 and first <= d <= last: nights[d] += kwh
    OUT = HERE / f"out/{which}.json"
else:
    for r in csv.DictReader(open(REPO / "charging-history-data/charging_sessions_tm1.csv")):
        start = datetime.fromisoformat(r["start_local"][:19])
        nights[(start - timedelta(hours=12)).date()] += float(r["kwh_used"])
    OUT = HERE / "out/teslamate.json"

rows, done = [], 0
for day in sorted(nights):
    energy = nights[day]
    capped = min(energy, WINDOW_KWH)
    arrive = datetime.combine(day, time(18), CH).timestamp()
    depart = datetime.combine(day + timedelta(1), time(7), CH).timestamp()
    six = at_six(arrive, capped)
    sp, got, fb = sparc(arrive, depart, capped)
    row = {"night": day.isoformat(), "kwh": energy, "kwhSimulated": capped, "sparcKWh": got, "fallbackSteps": fb,
           "fixed6pm": bill(six, fixed, flat), "hourly6pm": bill(six, hour_price, flat),
           "hourlyTOD6pm": bill(six, hour_price, tod), "hourlyTODsparc": bill(sp, hour_price, tod),
           "hindsight": bill(hindsight(arrive, depart, capped), hour_price, tod)}
    rows.append(row); done += 1
    if done % 50 == 0: print(done, "/", len(nights), row["night"], flush=True)
json.dump(rows, open(OUT, "w"), indent=1)
print("done", len(rows), "nights")
