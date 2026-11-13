# SPARC live replay: what the Pi's planner saves on real nights

Seven experiments replay the Raspberry Pi's own charging planner (`make_plan` in
`controller/sparc_controller/smart_charging.py`, mode `live`: ComEd day-ahead prices plus live
5-minute prices, planning on hourly supply plus Time-of-Day delivery) over real ComEd prices.

**No hindsight.** Every 5 minutes from plug-in, the planner is called with only the prices published
by that moment: 5-minute prices about 7 minutes after each interval, tomorrow's day-ahead prices
after 5 PM. The car then charges exactly as that plan says until the next replan. Bills use ComEd's
settled hourly prices.

## Setup shared by every experiment

- ComEd, Chicago area. 11.5 kW charger (48 A Wall Connector). Plugged in at 6 PM, must be ready by 6:30 AM
  (the 7 AM plug-out minus the Pi's 30-minute ready buffer).
- **Baselines** (standard delivery): fixed plan or hourly pricing, charging nonstop from 6 PM or from 11 PM.
  A fixed plan costs the same at any hour, so its 6 PM and 11 PM rows barely differ.
- **SPARC**: hourly pricing + Time-of-Day delivery, scheduled by the Pi's planner.
- **Perfect hindsight**: the cheapest possible hours on hourly + Time-of-Day delivery, knowing every
  price in advance. Not possible in real life; shows the ceiling.
- Fixed price: that month's ComEd fixed rate (8.413¢ for Oct 2026 onward). Delivery: 2026 standard and
  Time-of-Day rates on every night (no Time-of-Day delivery history is in the repo).
- Cost is energy supply plus delivery. The hourly-plan capacity charge, which the fixed price already
  includes, is left out, so savings against the fixed plan are somewhat overstated. Savings against the
  hourly baselines are not affected (the capacity charge doesn't depend on when you charge).
- **Known Pi bug, kept in:** on every fall-back daylight-saving day (Nov 6 2022, Nov 5 2023, Nov 3 2024,
  Nov 2 2025, Nov 1 2026) ComEd's table has 24 rows for a 25-hour day. The Pi's parser rejects it, the
  planner has no forecast for one evening hour, and it falls back to charging right away from 6 PM.

"SPARC saves" is against that row's baseline; "Max possible" is perfect hindsight against the same baseline.

## 1. Cybertruck, 50% → 90% every night

15 nights, Oct 29 – Nov 12, 2026 · 820 kWh

| Scenario | Cost | ¢/kWh | SPARC saves | Max possible |
|---|---:|---:|---:|---:|
| Fixed plan, from 6 PM | $94.50 | 11.5 | $60.87 · 64% | 69% |
| Fixed plan, from 11 PM | $94.44 | 11.5 | $60.81 · 64% | 68% |
| Hourly, from 6 PM | $51.56 | 6.3 | $17.93 · 35% | 42% |
| Hourly, from 11 PM | $46.84 | 5.7 | $13.21 · 28% | 36% |
| **Hourly + TOD delivery + SPARC** | **$33.62** | **4.1** | — | |
| *Perfect hindsight* | *$29.75* | *3.6* | | |

- 54.7 kWh from the grid every night (123 kWh usable pack, 90% charging efficiency).
- Nov 1 (clocks fell back): the DST bug made the Pi charge from 6 PM. Reading the table correctly would
  have cost $2.27 instead of $3.66 that night.

## 2. TeslaMate car, real nightly charging

649 nights, Sep 2024 – Oct 2026 · 9,169 kWh

| Scenario | Cost | ¢/kWh | SPARC saves | Max possible |
|---|---:|---:|---:|---:|
| Fixed plan, from 6 PM | $927.36 | 10.1 | $579.25 · 62% | 70% |
| Fixed plan, from 11 PM | $927.62 | 10.1 | $579.52 · 62% | 70% |
| Hourly, from 6 PM | $981.40 | 10.7 | $633.30 · 65% | 71% |
| Hourly, from 11 PM | $520.58 | 5.7 | $172.47 · 33% | 46% |
| **Hourly + TOD delivery + SPARC** | **$348.11** | **3.8** | — | |
| *Perfect hindsight* | *$282.14* | *3.1* | | |

- Each night's real home kWh from `charging-history-data/charging_sessions_tm1.csv` (TeslaMate, average
  14 kWh), grouped noon to noon so a 1 AM session counts toward the night before.
- TeslaMate doesn't record plug-in or unplug times, and 19% of this car's energy was really charged in
  the daytime. The replay moves it all into the 6 PM – 7 AM window.
- Hourly from 6 PM costs more than fixed: short charges all land in the evening price peak.
- 4 nights left out for missing prices.

## 3. Car 1, Model X LR 2022

1,435 nights, Jun 2022 – Oct 2026 · 32,036 kWh

| Scenario | Cost | ¢/kWh | SPARC saves | Max possible |
|---|---:|---:|---:|---:|
| Fixed plan, from 6 PM | $3,178.81 | 9.9 | $1,926.07 · 61% | 66% |
| Fixed plan, from 11 PM | $3,178.11 | 9.9 | $1,925.38 · 61% | 66% |
| Hourly, from 6 PM | $2,765.06 | 8.6 | $1,512.33 · 55% | 61% |
| Hourly, from 11 PM | $1,798.59 | 5.6 | $545.86 · 30% | 40% |
| **Hourly + TOD delivery + SPARC** | **$1,252.73** | **3.9** | — | |
| *Perfect hindsight* | *$1,078.98* | *3.4* | | |

- 12 nights left out for missing prices. 2022–2023 day-ahead prices were downloaded from ComEd
  (`data/`), since `comed-data/hourly/` skips those years.

## 4. Car 2, Model Y LR 2024

560 nights, Jun 2024 – Oct 2026 · 6,683 kWh

| Scenario | Cost | ¢/kWh | SPARC saves | Max possible |
|---|---:|---:|---:|---:|
| Fixed plan, from 6 PM | $652.69 | 9.8 | $407.91 · 62% | 68% |
| Fixed plan, from 11 PM | $652.88 | 9.8 | $408.10 · 63% | 68% |
| Hourly, from 6 PM | $690.98 | 10.3 | $446.20 · 65% | 70% |
| Hourly, from 11 PM | $380.39 | 5.7 | $135.61 · 36% | 45% |
| **Hourly + TOD delivery + SPARC** | **$244.78** | **3.7** | — | |
| *Perfect hindsight* | *$209.26* | *3.1* | | |

- 2 nights left out for missing prices.

## 5. Car 3, Model 3 LR 2023

63 nights, Apr – Jul 2026 · 435 kWh

| Scenario | Cost | ¢/kWh | SPARC saves | Max possible |
|---|---:|---:|---:|---:|
| Fixed plan, from 6 PM | $49.74 | 11.4 | $31.52 · 63% | 71% |
| Fixed plan, from 11 PM | $49.74 | 11.4 | $31.52 · 63% | 71% |
| Hourly, from 6 PM | $73.92 | 17.0 | $55.70 · 75% | 80% |
| Hourly, from 11 PM | $28.78 | 6.6 | $10.57 · 37% | 49% |
| **Hourly + TOD delivery + SPARC** | **$18.22** | **4.2** | — | |
| *Perfect hindsight* | *$14.56* | *3.3* | | |

- Only 4 months, spring into summer. Summer evening price spikes made hourly from 6 PM 17¢/kWh.

## 6. Car 4, Model 3 SR 2019

201 nights, Oct 2025 – Oct 2026 · 3,276 kWh

| Scenario | Cost | ¢/kWh | SPARC saves | Max possible |
|---|---:|---:|---:|---:|
| Fixed plan, from 6 PM | $366.88 | 11.2 | $238.53 · 65% | 71% |
| Fixed plan, from 11 PM | $366.88 | 11.2 | $238.53 · 65% | 71% |
| Hourly, from 6 PM | $438.76 | 13.4 | $310.41 · 71% | 76% |
| Hourly, from 11 PM | $200.52 | 6.1 | $72.16 · 36% | 47% |
| **Hourly + TOD delivery + SPARC** | **$128.35** | **3.9** | — | |
| *Perfect hindsight* | *$106.54* | *3.3* | | |

- The car is from zip 46514 (Elkhart, Indiana, outside ComEd), but it is priced on ComEd like the others.
- No data for Dec 2025, Jan, Jun or Aug 2026. Average 16 kWh a night, the most 38 kWh.
- Late Oct 2026 prices are past the end of `comed-data/`, so they were downloaded into `data/`.
- 2 nights left out for missing prices (Sep 6 and Oct 31, 2026). The Nov 2, 2025 night hit the Pi's
  daylight-saving bug.

## 7. Norwegian homes, charged in Chicago

27,824 user-nights from 267 households · mapped onto 2025 · 445,470 kWh

| Scenario | Cost | ¢/kWh | SPARC saves | Max possible |
|---|---:|---:|---:|---:|
| Fixed plan, from 6 PM | $43,751.25 | 9.8 | $25,824.81 · 59% | 65% |
| Fixed plan, from 11 PM | $43,752.33 | 9.8 | $25,825.90 · 59% | 65% |
| Hourly, from 6 PM | $39,791.29 | 8.9 | $21,864.86 · 55% | 62% |
| Hourly, from 11 PM | $26,610.26 | 6.0 | $8,683.83 · 33% | 43% |
| **Hourly + TOD delivery + SPARC** | **$17,926.43** | **4.0** | — | |
| *Perfect hindsight* | *$15,154.96* | *3.4* | | |

- Only the charging amounts are Norwegian: each user's real kWh per night from
  `Dataset1_charging_reports (1).csv` (2018–2021), grouped noon to noon by plug-in time. Prices, rates
  and the 6 PM – 7 AM window are Chicago's, as in every other experiment.
- Dates move to the nearest same weekday in the same month of 2025, as in the repo's existing Norway
  study, so winter nights stay winter nights and weekdays stay weekdays.
- The real Norwegian plug-in times and charger speeds are not used; the repo's Norway study in
  `results/charging-history/` covers those.
- These are many separate households added together, not one home's bill.
- SPARC beat hourly-from-11 PM on 96% of user-nights. 71 user-nights (2,233 kWh) are left out for
  missing ComEd prices on Jan 30, Jul 1 and Nov 1, 2025. 96 user-nights fell on the Nov 2, 2025
  daylight-saving night and hit the Pi bug.

Cars 1–4 are Tesla app **Charge Stats** exports in `tesla_datasets_all_20261104-220003/` (cars 1–3 added in
commit `2e97af2`, car 4 later). Each day's "Home" kWh is charged that night, 6 PM → 7 AM.
The daily values were measured from the app's bar chart in screenshots and scaled so each month matches
the app's exact total. The kWh are treated as energy from the wall.

## Reproduce

Python 3.9 or newer, from the repository root. The Pi code is imported unchanged from `controller/`.

```sh
python3 results/sparc-live-replay/replay_15_nights.py
python3 results/sparc-live-replay/replay_history.py teslamate
python3 results/sparc-live-replay/replay_history.py car0001 2022-06-01 2026-10-16
python3 results/sparc-live-replay/replay_history.py car0003 2024-01-01 2026-10-16
python3 results/sparc-live-replay/replay_history.py car0006 2024-01-01 2026-10-16
python3 results/sparc-live-replay/replay_history.py car0008 2025-10-01 2026-10-31
python3 results/sparc-live-replay/replay_norway.py
python3 results/sparc-live-replay/summarize.py
```

The history runs take a few minutes each; the Norway run uses all but three cores and takes about
10 minutes on an M5 Pro. `fetch_prices.py` re-downloads the three price files in `data/`
(about 20 minutes); they are already saved.

| File | What it holds |
|---|---|
| `out/summary.json` | Every number in the tables above |
| `out/<experiment>.csv` | One row per night: kWh, the cost of each scenario, and how many 5-minute steps fell back to charging right away |
| `out/<experiment>.json` | The replays' raw per-night output (the Cybertruck file also lists the hours SPARC charged in); `norway.json` is not committed, run `replay_norway.py` to rebuild it |
| `data/` | ComEd prices that `comed-data/` doesn't have |
