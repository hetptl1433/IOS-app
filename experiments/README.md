# SPARC experiments

Every study behind SPARC's results: the question, the script, the output and what it showed. Start
here. Each folder has its own README with the details. [`../results/index.html`](../results/README.md)
shows the headline results as charts on one page.

SPARC plans EV charging on ComEd's Hourly Pricing in Chicago, where every kWh pays that hour's
wholesale real-time price plus a delivery charge. The studies replay real prices night by night
(2024-04-20 to 2026-10-14) to measure what a smarter charger saves. The code they test is the
`sparc` package in this repository, the same code the web app and command line run.

## The main findings

The pitch setup is a Cybertruck (123 kWh) on an 11.5 kW Wall Connector, plugged in from 8 p.m. to
7 a.m. It adds 15, 30 or 80 kWh a night: an average driver (about 37 miles a day), a moderate one (75)
and a heavy one (200). Savings are a year, against the default setup: standard delivery, charging as
soon as the car is plugged in.

| | Average | Moderate | Heavy | Where |
|---|---|---|---|---|
| Delivery Time-of-Day rate alone | $165 | $363 | $1,020 | `scenarios/` |
| SPARC's day-ahead plan, on Time-of-Day delivery | **$355** (54%) | **$643** (51%) | **$1,224** (41%) | `scenarios/` |
| Perfect hindsight, one night at a time | $393 | $697 | $1,297 | `scenarios/` |
| Battery buffer (new, not yet in the app) | **$410** (62%) | **$697** (56%) | **$1,233** (41%) | `battery_buffer/` |

1. **The day-ahead plan is hard to beat within a night.** It charges in the cheapest hours by the
   published day-ahead prices and gets 90–94% of what perfect hindsight saves. 97 other strategies were
   tuned on earlier nights and scored on 2026, including live 5-minute reaction, price caps, Tesla's own
   scheduler modes and learned forecasts. On the 2026 test nights none is more than $9 a year ahead of
   the plan for any driver, and none of the top picks is ahead by two standard errors (`strategies/`).
2. **Better forecasts don't mean better plans.** Ridge, LightGBM, weather inputs and six pretrained
   time-series foundation models all have a lower RMS error than the day-ahead price, by up to 21%.
   Every one still plans worse (`forecasting/`).
3. **The battery is the lever.** A 123 kWh battery need not refill every night. Buying more on cheap
   nights, topping up when a live price dips, and skipping planned hours when one spikes adds $54 a
   year for the average and moderate drivers. For the average driver that beats perfect single-night
   hindsight; for the moderate one it matches it. It holds in 2024, 2025 and 2026, including the years
   the rules were not tuned on (`battery_buffer/`).
4. **Plug-in time matters most.** For the average driver on Time-of-Day delivery, the plan saves $190
   a year over charging on plug-in when plugged in at 8 p.m., but only $51 when plugged in at 11 p.m.
   Much bigger "savings" ($3,000+) only come from comparing with a car that would start charging as
   soon as it is plugged in around midday (`scenarios/`).

## How the backtests work

- **Prices as they were known.** A strategy deciding at time t sees:
  - day-ahead prices published by t (the afternoon before);
  - 5-minute real-time prices posted by t (about 7 minutes after each interval starts), as first
    posted;
  - weather forecasts issued by t.

  It never sees the settled price it is billed at. Billing follows ComEd: each kWh pays its hour's
  settled price plus the delivery rate of that hour.
- **Physical limits.** Charging power per 5-minute slot, a 30-minute finish-early margin, 90% charging
  efficiency. Summer weekday afternoons (12–7 p.m., June to September) are blocked for the planned
  strategies, because energy in ComEd's and PJM's peak hours sets next year's capacity charge.
- **Fair comparisons.** Every strategy runs on the same nights. A night missing any price it needs is
  skipped for all of them, and the skips are counted.
  - "Tied" means within two standard errors of the night-by-night (paired) difference.
  - Tuned rules are chosen on earlier nights and scored on later ones (`strategies/sweep.py`,
    `battery_buffer/`).
  - The battery-buffer results carry standard errors from resampling whole months, because each night
    depends on the one before.
- **Checks on the data.** Coverage and ComEd's own published hourly prices are compared with the
  5-minute feed (`data/README.md`). A second environment fetched the prices directly from ComEd and
  reproduced the backtest exactly.

## Folders

| Folder | Question |
|---|---|
| [`scenarios/`](scenarios/README.md) | Which drivers, plug-in windows and rates save the most |
| [`strategies/`](strategies/README.md) | Can any single-night strategy beat the day-ahead plan? |
| [`forecasting/`](forecasting/README.md) and `foundation_models.py` | Can forecasting the real-time price beat the day-ahead price? |
| [`battery_buffer/`](battery_buffer/README.md) | What does using the battery across nights add? |
| [`data/`](data/README.md) | Where the data comes from, its coverage, and why the prices are not in this repository |
| [`webapp/`](webapp/README.md) | Browser checks and screenshots of the web app |
| [`dev/`](dev/README.md) | Profiling and an equivalence check used during development |

Every script writes what it prints. The committed `results/` files are those outputs, regenerated from
these scripts on the data described in `data/`. A few come from a second environment that had 2024
prices, and say so at the top.

## Running them

The ComEd prices are not in this repository: ComEd's API terms forbid republishing them
(`data/README.md`). Download them once from ComEd's public API (about 20 minutes):

```sh
python -m sparc fetch 2024-01-01 2026-10-16      # into runtime/sparc.db
python experiments/battery_buffer/buffer_skip.py 2025-04-02 2026-10-14
```

- **Requirements.** Python 3.11 or newer. The LightGBM forecasts need `pip install lightgbm`, the
  hindsight bound needs `pip install scipy`, and the foundation models need the packages listed in
  `foundation_models.py`.
- **Data folder.** Scripts read `runtime/sparc.db`, or `$SPARC_DATA/sparc.db` if that is set. Backtest
  runs they save go to `runtime/experiment-runs/`, apart from the web app's runs.
- **Run times on four cores.**
  - The 97-strategy sweep takes about 20 minutes.
  - The walk-forward forecasting runs take 2–6 minutes each.
  - Most others take under a minute.

## Limitations

- Results are a backtest on 2024–2026 Chicago prices. Future prices, especially extremes like the
  January 2026 storm, will differ. Each folder reports which findings depend on such nights.
- Driving is the same every day, and the car is plugged in every night. Real drivers vary.
- No strategy here has run on a real car yet. Battery wear, charger and API quirks, and the home's
  other load are not modelled.
- The supply cost is ComEd Hourly Pricing's real-time price. Transmission charges and the capacity
  charge on the rest of the bill are outside these comparisons, apart from avoiding capacity-risk hours.
