# SPARC

Charge an EV in the cheapest hours of ComEd's Hourly Pricing plan and still be
ready to leave on time. SPARC is built to run on a Raspberry Pi. Today it fetches
prices, backtests charging strategies on real price history (from a local web app
or the command line), trains price forecasts, and records live prices. Tesla
control comes next, once a strategy has proven itself on real prices.

## Quick start

Python 3.11 or newer. No third-party packages (LightGBM is optional).

```sh
python -m sparc serve        # opens http://127.0.0.1:8765
```

1. **Data**: fetch prices from ComEd and Chicago weather from Open-Meteo, for
   example from 2025-01-01 to today. A year is about 425 requests at one a second.
   Everything is stored in `runtime/sparc.db` on your computer; an interrupted
   fetch picks up where it stopped.
2. **Run**: one click backtests every strategy below on every night with prices,
   for an average, a moderate and a heavy driver (or the four example drivers;
   both editable) and both delivery rates. 18 months takes about a minute.
3. **Results**: what each driver saves against the default setup, the cheapest
   strategy for each driver, which others are statistically tied with it,
   savings per year, month-by-month comparisons, forecast accuracy, and every
   night as CSV. Past runs are kept.
4. **Models**: train a ridge or LightGBM price forecast on any period, score it on
   a later one, and backtest it.

The app listens on 127.0.0.1 only; the only requests it makes are to ComEd and Open-Meteo.
`pip install lightgbm` adds the LightGBM forecast.

## What a charge costs

- **Supply.** ComEd bills every kWh at the average of the twelve 5-minute
  real-time prices in its hour. If prices spike at 1:10, every kWh charged
  anywhere from 1:00 to 2:00 pays the spiked average.
- **Delivery.** 6.228¢/kWh on the standard rate. On Delivery Time-of-Day it is
  2.984¢ overnight (9 p.m.–6 a.m.) and 10.712¢ from 1 to 7 p.m. (single-family
  home without electric heat, CUB fact sheet, March 2026).
- **Capacity charge.** Next year's charge follows your demand in the ten summer
  hours when PJM and ComEd peak, historically weekday afternoons. Charging at
  7.7–11.5 kW through one of those hours adds roughly $90–140 to a year of bills,
  so the planner avoids summer weekday afternoons unless that's the only way to
  finish.

Other per-kWh charges are flat: they change the bill, not the best time to charge.

## Strategies

| Strategy | Knows | Does |
|---|---|---|
| Charge on plug-in | nothing | the baseline every saving is measured against |
| Timer 21:00 … 01:00 | the clock | starts at a fixed time, or at once if plugged in up to 6 hours after it (as a Tesla's schedule does) |
| Below 2¢, 3¢, 5¢ | the latest 5-minute price | charges while the price is at or under the cap, pauses above it |
| Day-ahead plan | ComEd's day-ahead prices, out around 4:30 p.m. CT for the next day | charges in the cheapest hours |
| Day-ahead + live prices | each 5-minute price as well, about 7 minutes after its interval starts | re-plans every 5 minutes |
| Forecast, refit monthly | a learned forecast of each hour's price (ridge or LightGBM), with or without weather and holidays | plans from the forecast, re-plans hourly |
| Perfect hindsight | every hour's final price | the lowest cost reachable without capacity risk: a reference, not a strategy |

Every strategy starts early when waiting would mean not finishing. The planned
strategies share one planner (`sparc/planner.py`). It fills the cheapest hours
first, tries to finish 30 minutes before departure, and keeps out of
capacity-risk hours. Timers and price caps ignore those hours, as a car's own
schedule or a simple price rule would.

The forecasts predict how far each hour's settled price lands from its day-ahead
price, from what is known when the plan is made: day-ahead prices already out,
the last day of 5-minute prices, the hour, weekday and season. The weather
versions add holidays, Chicago's weather forecast made a day ahead (temperature,
dew point, wind, cloud), and how far the last three hours missed their
forecast. In a backtest,
each month's model learns only from the nights before that month (minus two
days, so no prices are shared). A model trained in the Models tab is only used
on nights after its training period.

## What a driver saves

One Cybertruck on an 11.5 kW Wall Connector, plugged in from 8 p.m. to 7 a.m.
every night from 2025-04-02 to 2026-10-14 (554 nights), for three amounts of driving.
The default setup is standard delivery and charging as soon as the car is
plugged in. SPARC puts the car on Time-of-Day delivery and charges it in the
cheapest hours of the day-ahead plan.

| Driver | Charged a night | Default setup, $/night | SPARC, $/night | Saved a year | Cheaper |
|---|---|---|---|---|---|
| Average, about 37 miles a day | 15 kWh | 1.80 | 0.83 | $355 | 54% |
| Moderate, about 75 miles a day | 30 kWh | 3.43 | 1.67 | $643 | 51% |
| Heavy, about 200 miles a day | 80 kWh | 8.21 | 4.85 | $1,224 | 41% |

Every strategy on Time-of-Day delivery, saved a year against the default setup:

| Strategy | Average | Moderate | Heavy |
|---|---|---|---|
| Charge on plug-in (the rate alone) | 165 | 363 | 1,020 |
| Timer 01:00 | 346 | 638 | 1,167 |
| Below 3¢ | 290 | 553 | 1,173 |
| Day-ahead plan | 355 | 643 | 1,224 |
| Day-ahead + live prices | 348 | 641 | 1,228 |
| Forecast: LightGBM + weather, refit monthly | 345 | 636 | 1,226 |
| Perfect hindsight | 393 | 697 | 1,297 |

- The delivery rate alone is worth $165–1,020 a year here, and the plan's timing
  adds $190, $280 and $204 on top. Time-of-Day delivery charges 10.7¢ from 1 to
  7 p.m., against 6.2¢ on standard, so it only pays when charging stays out of
  the afternoon.
- The plan gets 90–94% of what perfect hindsight would save. It saves more than
  every timer and price cap; live prices and the learned forecasts, with or
  without weather, come within $12 a year of it.
- Heavy driving leaves the least room to choose: 80 kWh takes nearly eight of
  the eleven hours plugged in.

Reproduce with `python -m sparc backtest 2025-04-02 2026-10-14`; the app shows
the same table at the top of Results.

## Four example drivers

Every night from 2025-04-02 to 2026-10-14 (540–555 per driver, after skipping
nights with a missing price), billed at ComEd's settled hourly prices. Costs are
supply plus the distribution charge; flat per-kWh charges are left out. The live
strategy and the first month's forecasts learned from the 90 days before.

Commuter: plug in at 6 p.m., leave at 7 a.m., add 15 kWh at 7.7 kW.

| Strategy | Standard delivery, $/night | saved $/yr | Time-of-Day delivery, $/night | saved $/yr |
|---|---|---|---|---|
| Charge on plug-in | 2.40 | – | 2.52 | – |
| Timer 23:00 | 1.49 | 333 | 0.94 | 575 |
| Timer 01:00 | 1.39 | 368 | 0.85 | 610 |
| Below 2¢ | 1.51 | 322 | 1.07 | 529 |
| Below 3¢ | 1.54 | 314 | 1.17 | 493 |
| Day-ahead plan | 1.37 | 374 | 0.83 | 616 |
| Day-ahead + live prices | 1.38 | 371 | 0.84 | 613 |
| Forecast: ridge, refit monthly | 1.39 | 368 | 0.85 | 610 |
| Forecast: LightGBM, refit monthly | 1.39 | 369 | 0.85 | 611 |
| Perfect hindsight | 1.27 | 410 | 0.74 | 649 |

Savings per year against charging on plug-in, standard delivery:

| Driver | Timer 23:00 | Below 3¢ | Day-ahead | + live prices | Ridge forecast | Perfect hindsight |
|---|---|---|---|---|---|---|
| Commuter: 6 p.m.–7 a.m., +15 kWh, 7.7 kW | 333 | 314 | 374 | 371 | 368 | 410 |
| Big day: 6 p.m.–7 a.m., +40 kWh, 7.7 kW | 503 | 457 | 517 | 519 | 514 | 574 |
| Late arrival: 10 p.m.–6 a.m., +50 kWh, 11.5 kW | 55 | 70 | 92 | 98 | 90 | 152 |
| Afternoon plug-in: 2 p.m.–7 a.m., +30 kWh, 7.7 kW | 452 | 418 | 490 | 495 | 484 | 557 |

What the numbers say:

- **Charging overnight is most of the saving.** The 23:00 timer gets 81% of what
  perfect hindsight could save for the commuter (89% with Time-of-Day delivery). A
  1 a.m. timer comes within 2 cents a night of the day-ahead plan.
- **Planning from day-ahead prices adds $12–41 a year** over the 23:00 timer across
  the drivers and both delivery rates. It also adjusts to how much energy is needed and to unusual nights.
  It is not always safer: in the January 2026 cold snap it chose overnight hours
  that spiked, and its worst commuter night was $6.45 against the timer's $3.57.
- **Reacting to live 5-minute prices adds nothing on average.** Against the
  day-ahead plan it ranged from −1.4 to +1.5 cents a night, always within two
  standard errors. Varying the publication lag, hysteresis and persistence model
  never lifted it above that, and strong hysteresis made it clearly worse. It wins
  on some spike nights and loses on false alarms, including spikes that ComEd later
  revised away.
- **"Stop charging when the price is high" loses to the plan.** A 2¢ cap saves the
  commuter $322 a year against the plan's $374, starting and stopping the charger
  six times a night (the plan: under two), and each stop is a command to the car.
  Caps also charge in capacity-risk hours (698 kWh a year for the 2 p.m. plug-in at
  3¢) and ignore the 10.7¢ afternoon delivery rate: on Time-of-Day delivery the
  2 p.m. plug-in saves $953 with a 3¢ cap and $1,427 with the plan.
- **Learned forecasts don't pay.** Refit every month, ridge regression forecast
  each hour's settled price with the same mean error as the day-ahead price
  (1.87¢) and an 11% lower RMS error (5.63¢ against 6.32¢); LightGBM was 2.5% and
  5% better. Planning from them still cost 0.3–1.5 cents a night more than planning
  from day-ahead prices: tied with the cheapest strategy for 3 of the 8 driver and
  rate combinations, behind for the other 5. Better spike forecasts don't change
  which hours are cheapest often enough.
- **Weather and holidays don't help either.** With them, ridge's mean error went
  from 1.872¢ to 1.880¢ and LightGBM's from 1.825¢ to 1.842¢ (day-ahead: 1.872¢),
  and the cost per night moved by under 0.3 cents either way: still 0.1–1.7 cents
  more than the day-ahead plan. Even given the weather that actually happened
  instead of the forecast, neither model got better. Extreme weather does predict
  volatility: below −7 °C the real-time price misses day-ahead by over 5 cents in
  21% of hours, against 3% in mild weather. But it does not say which way, and
  PJM's day-ahead prices already reflect the weather forecast. Hours that came in
  colder or warmer than forecast show no relation to the price gap (correlation
  −0.03).
- **A better forecast could add at most $33–67 a year** per driver: the gap
  between the day-ahead plan and perfect hindsight.
- **The capacity charge is the big daytime risk.** Plugging in at 2 p.m. and
  charging straight away overlapped 4 of PJM's 5 peak hours in summer 2025. That
  adds about $366 a year to capacity charges from PJM's half of the formula alone;
  ComEd's own five peak hours count too. All planned strategies avoided those hours.
- **Delivery Time-of-Day makes overnight charging about $200 a year cheaper** for
  the commuter. That counts the charging energy only; the rest of the home's
  usage decides whether enrolling pays.

Reproduce with `python -m sparc fetch 2025-01-01 2026-10-15` and then
`python -m sparc backtest 2025-04-02 2026-10-14 --drivers examples`, or the same
dates and the four example drivers in the app.

## Command line

The web app and the command line share everything in `runtime/` (choose another
folder with `--data DIR` or `SPARC_DATA`).

```sh
python -m sparc serve [--port 8765]               # the web app
python -m sparc fetch 2025-01-01 2026-10-15       # backfill prices and weather; resumable
python -m sparc check 2025-01-01 2026-10-15       # coverage, and agreement with ComEd's hourly prices
python -m sparc backtest 2025-04-02 2026-10-14    # every strategy; saved as a run the app can show
python -m sparc backtest 2025-04-02 2026-10-14 --drivers examples    # the four example drivers
python -m sparc backtest 2025-04-02 2026-10-14 --strategies day-ahead,cap-3 --csv nights.csv
python -m sparc strategies                        # the strategy ids
python -m sparc train ridge 2025-01-02 2025-12-31 --test 2026-01-02 2026-10-14 [--weather]
python -m sparc collect                           # on the Pi: record live prices every 5 minutes
python -m unittest discover -s tests              # tests
```

## Data

- **5-minute prices** come from ComEd's documented API
  (`hourlypricing.comed.com/api?type=5minutefeed`). Each label is the start of its
  interval. Averaging the labels HH:00–HH:55 reproduces ComEd's published hourly
  prices; `check` measures that agreement on everything you fetch.
- **Day-ahead prices** come from the table behind ComEd's pricing-table page.
  It is not part of the documented API and can change without notice. Its table
  for the day the clocks fall back cannot be read, so nights needing that day's
  prices are skipped. PJM Data Miner (`da_hrl_lmps`, ComEd zone) is the official source.
  It needs a free PJM account, and non-members are limited to 6 requests a minute.
- The 5-minute feed keeps prices as first posted. ComEd's published hourly price
  is the settled one. Across 2025–2026 the two agree to rounding in all but 15 of
  15,020 hours. In 12 of those 15 they differ by more than a cent, and each time a
  posted spike was later revised down. So the backtest decides on the 5-minute
  feed and bills on the published price.
- About 4% of hours miss at least one 5-minute reading. Such hours are billed at
  the published price, or at the mean of 10–11 readings when no published price
  exists. Nights with an unpriced hour are skipped and counted.
- **Weather** comes from Open-Meteo's Previous Runs API (`gfs_seamless`, downtown
  Chicago): each hour's analysis, and the forecast made 24 hours before it. A
  forecast only uses a weather forecast that was already out when the plan was
  made, and observed weather only for hours already over. Weather data by
  Open-Meteo.com (CC BY 4.0), free for non-commercial use.
- **Holidays** are NERC's six off-peak days as PJM publishes them, plus the day
  after Thanksgiving.
- Historical feeds cannot say when each price was actually published. `collect`
  records that from now on.
- ComEd's API terms restrict redistributing its data, so `runtime/` is git-ignored.
  Keep price databases local.

## Layout

| Module | |
|---|---|
| `clock.py` | epoch-second timestamps; Chicago wall-clock rules |
| `comed.py` | ComEd client: 5-minute feed, hourly table, polite fetcher |
| `store.py` | SQLite storage |
| `market.py` | hourly billing prices; when each price became public |
| `tariff.py` | delivery rates, capacity-risk hours |
| `planner.py` | cheapest-hours plan with buffer, blocked hours and hysteresis |
| `strategies.py` | the strategies above |
| `forecast.py` | forecast features, ridge and LightGBM fits, monthly refits |
| `weather.py`, `holidays.py` | Chicago weather from Open-Meteo; NERC holidays |
| `backtest.py` | night-by-night simulation and summaries |
| `lab.py` | backtest runs and model training, shared by the app and the command line |
| `server.py`, `web/` | the local web app |
| `history.py`, `collector.py` | backfill and live recording |

## Next

1. Ship the day-ahead plan with the capacity guard. Live 5-minute reaction and
   learned forecasts, with or without weather, stay research: none beat it on
   held-out nights. The one input left untested is PJM's own load forecast and
   generation data (Data Miner, free account), though recent price gaps, which
   the forecasts already use, should carry most of what it would add.
2. Shadow mode on the Pi: run `collect`, plan every evening, and log what would
   have happened, without sending commands.
3. Tesla Fleet API through `tesla-http-proxy`, using `add_charge_schedule` so the
   car still charges if the Pi goes offline. The day-ahead plan starts charging
   about twice a night, well inside the $10 monthly credit. Commands cost $1
   per 1,000 and data reads $1 per 500, so the controller must not poll.
