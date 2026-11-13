# Combined savings and historical replay

The chosen baseline is fixed ComEd supply plus standard delivery, compared with hourly supply, Time-of-Day delivery and SPARC scheduling. The owner selected single-family with electric heat. Values estimate the supply and distribution subtotal; capacity, other supply charges, taxes and fixed fees are excluded. They are not verified whole-bill savings.

Every scenario uses the same grid energy. The comparison has four costs:

1. Fixed supply + standard distribution, charging immediately on arrival.
2. Hourly supply + standard distribution, at those same immediate times.
3. Hourly supply + TOD distribution, at those same immediate times.
4. Hourly supply + TOD distribution, at SPARC's chosen times.

The three gains are 1−2, 2−3 and 3−4. They add exactly to 1−4. Losses remain negative; missing inputs never become zero. The headline percentage is total dollar difference divided by the absolute total fixed baseline, rather than the mean of nightly percentages. Screens show only that percentage up front ("67% saved", or "4% more" for a loss); the dollars are one tap away in the ⓘ "How it's calculated" sheet and in each night's detail.

## Last 15 completed nights

Night starts: October 28–November 11, 2026. The replay uses the owner's confirmed existing Cybertruck profile: 100 kWh capacity, 20%→80% every night, 11.5 kW, 90% efficiency, 10pm–7am Chicago time, with a 30-minute ready buffer. The planner reruns every five minutes, observes publication-time guards, and fits live decay only before each night. Realized prices bill the resulting schedule; they do not choose it.

| Result | Amount |
|---|---:|
| Nights | 15 |
| Priced / missing-price nights | 14 / 1 |
| Priced grid energy | 933.333 kWh |
| Fixed baseline | $107.57 |
| Hourly + TOD + SPARC | $35.96 |
| Combined difference | $71.61 (66.6%) |
| Hourly supply contribution | $58.49 |
| TOD delivery contribution | $12.19 |
| Scheduling contribution | $0.93 |

All 15 replayed nights reach the target by the ready buffer. October 31's overnight window lacks historical billing prices for two November 1 daylight-saving hours. Its costs/savings are unpriced, excluded from totals, and never filled with invented prices. Refetching the surrounding dates did not recover them.

This is reconstructed virtual-car history, not evidence that the Pi operated those sessions. The phone retains the replay in a bounded, validated cache per Pi, so it survives offline restarts and is marked stale until refreshed. The app shows it under the virtual profile's Past 15 days and Records; record details retain the Historical replay source. Recorded Pi sessions stay in their original ledger and separate feed. Installing the replay never resets the car's battery or invents measured energy.

## Other comparisons

Settings → Savings comparison (virtual car) can switch the phone from the fixed-price plan to ComEd hourly pricing. Without SPARC, the same energy then charges right away at 11.5 kW on the hourly price with standard delivery, starting at 6 PM (the default) or at plug-in (10 PM, the replay's arrival). SPARC is priced exactly as above. The gain splits into Time-of-Day delivery (standard − TOD at the immediate times) and smart timing (TOD at the immediate times − SPARC); the two add exactly to the total.

| Compared with | Priced nights | Without SPARC | With SPARC | Saved | Delivery | Timing |
|---|---:|---:|---:|---:|---:|---:|
| Hourly, charging at 6 PM | 13 | $53.11 | $32.89 | $20.21 (38%) | $3.83 | $16.39 |
| Hourly, charging at plug-in | 14 | $49.08 | $35.96 | $13.12 (27%) | $12.19 | $0.93 |

The plug-in comparison reuses the replay's own steps 2 and 3. The 6 PM costs come from `python3 -m sparc_controller.replay_baselines REPLAY.json OUT.json`, which stores `at6PM: {hourlyStandardCostUSD, hourlyTODCostUSD}` on each night (`null` when any hour lacks a price). It prices with ComEd's hourly billing table (`pricingtabledual`, real-time column; rows are labelled by hour end, and hours touching a daylight-saving change are left out). ComEd's 5-minute averages do not reproduce the replay; the billing table does. Before adding anything, the tool recomputes every priced night's plug-in costs and stops if any differs by more than half a cent. November 6 has no 6 PM price (ComEd lists none for 8–9 PM) and October 31 has no SPARC price, so the 6 PM comparison covers 13 nights. Unpriced nights are left out of the totals and counted in the ⓘ note.

The Pi screen always uses the fixed-price plan; the setting belongs to the phone.

## Tariffs and transport

`billing.py` contains dated October–December 2026 distribution adjustments and the residential nonsummer fixed energy rate of 8.413¢/kWh. Each adjustment is rounded separately as required by the tariff. November–December electric-heat TOD distribution is 1.790¢ overnight, 2.289¢ morning, 5.999¢ peak and 2.168¢ evening. The adjusted standard distribution baseline is 3.084¢. See [official rate sources](RATES.md).

The planner now ranks supply + TOD distribution when a verified dated tariff covers the complete window. Missing forecasts retain deadline protection and honest unknown cost. Expired tariffs do not silently extend into another year.

`get_savings {kind: simulation|recorded}` uses the existing encrypted LAN/Bluetooth channel. The replay lives at `/var/lib/sparc-controller/savings-replay.json`. The 15-row encrypted reply is 12,892 bytes with the 6 PM costs (11,564 before), below the 16,384-byte frame limit; both sizes are computed from the sealed-frame format. Loopback display access is `GET /api/savings?kind=simulation|recorded`. Internal `kind:simulation` and `simulated:true` preserve provenance even where the UI uses Past 15 days.

## Verification

Local controller suite and staged real-Pi suite: **285 tests run, OK (skipped=8)**. The owner installed the Pi update; service returned `active`, backup `/var/backups/sparc-controller/source-20261112T170305Z-before-combined-savings-calendar.tgz`. All 14 installed source/test/display files matched staging; the later recovery fix updates only `combined-savings.js`.

Live replay API returned 15 rows, 14 priced, $71.6111666673 / 66.5696269218%. Live recorded API returned 8 actual virtual-car sessions, 7 priced. These counts differ deliberately.

The Pi Savings page was rendered against isolated local controller state on `http://127.0.0.1:8895/` in Chrome. Savings → Past 15 days showed the correct totals and missing night; Recorded showed an honest empty state for the isolated ledger. No relevant browser warnings/errors appeared. This verifies the same display assets, not a screenshot of the Pi's physical screen.

Real-iPhone verification, current limitations and failure records are in [STATUS](STATUS.md) and [car-profile edge cases](CAR_PROFILE_QA.md). Local logs, report, hourly audit and final xcresult are retained under ignored `artifacts/verification/20261112-savings-calendar-profiles/`.

The browser recovery fault was corrected and verified with the isolated service: offline → Retrying → automatic return of the same report after connection restoration. A versioned asset URL prevents the kiosk from retaining an older script after installation.

Percent headline and hourly comparisons: real-iPhone unit suite **148 tests, 0 failures**; staged real-Pi suite **290 run, OK (skipped=8)**, including `test_replay_baselines`. The staged display files match the repo by SHA-256. `sudo bash ~/sparc-qa/install-6pm.sh` on the Pi copies the live display files and replay to `~/sparc-qa/backup-<time>`, installs the display, the tool and the augmented replay, restarts the controller and kiosk, and waits until the loopback API returns `at6PM`.
