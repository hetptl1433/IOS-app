# SPARC

Charge an EV in the cheapest hours of ComEd's Hourly Pricing plan and still be
ready to leave on time. SPARC is built to run on a Raspberry Pi. Today it fetches
prices, backtests charging strategies on real price history, and records live
prices. Tesla control comes next, once a strategy has proven itself on real prices.

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

| | Strategy | Knows |
|---|---|---|
| 0 | Charge on plug-in | nothing |
| 1 | Timer 23:00 | the clock |
| 2 | Day-ahead plan | ComEd's day-ahead prices, published around 4:30 p.m. CT for the next day |
| 3 | Day-ahead + live prices | each 5-minute price as well, about 7 minutes after its interval starts |
| – | Perfect hindsight | every hour's final price: the lowest possible cost, not a strategy |

Rungs 2 and 3 share one planner (`sparc/planner.py`). It fills the cheapest
hours first, tries to finish 30 minutes before departure, and keeps out of
capacity-risk hours. Rung 3 re-plans every 5 minutes. When a spike lands in an
hour it is charging in, the rest of that hour's energy moves to a cheaper hour.
A machine-learned price forecast is worth adding only if it beats rung 3 on
held-out years.

## Results: ComEd prices, April 2025 to October 2026

Every night of the period (541–556 per profile, after skipping nights with a
missing price), billed at ComEd's settled hourly prices. Costs are supply plus the distribution charge; flat per-kWh charges are
left out. The live strategy's price-persistence model was fitted on January–March
2025, before the test period.

Commuter: plug in at 6 p.m., leave at 7 a.m., add 15 kWh at 7.7 kW.

| Strategy | Standard delivery, $/night | saved $/yr | Time-of-Day delivery, $/night | saved $/yr |
|---|---|---|---|---|
| Charge on plug-in | 2.40 | – | 2.52 | – |
| Timer 23:00 | 1.48 | 333 | 0.94 | 575 |
| Day-ahead plan | 1.37 | 374 | 0.83 | 616 |
| Day-ahead + live prices | 1.38 | 371 | 0.84 | 613 |
| Perfect hindsight | 1.27 | 410 | 0.74 | 649 |

Savings per year against charging on plug-in, standard delivery:

| Profile | Timer 23:00 | Day-ahead | + live prices | Perfect hindsight |
|---|---|---|---|---|
| Commuter: 6 p.m.–7 a.m., +15 kWh, 7.7 kW | 333 | 374 | 371 | 410 |
| Big day: 6 p.m.–7 a.m., +40 kWh, 7.7 kW | 502 | 517 | 518 | 573 |
| Late arrival: 10 p.m.–6 a.m., +50 kWh, 11.5 kW | 55 | 92 | 97 | 151 |
| Afternoon plug-in: 2 p.m.–7 a.m., +30 kWh, 7.7 kW | 451 | 489 | 494 | 556 |

What the numbers say:

- **Charging overnight is most of the saving.** The 23:00 timer gets 81% of what
  perfect hindsight could save for the commuter (89% with Time-of-Day delivery). A
  1 a.m. timer comes within 2 cents a night of the day-ahead plan.
- **Planning from day-ahead prices adds $13–41 a year** over the 23:00 timer across
  the profiles and both delivery rates. It also adjusts to how much energy is needed and to unusual nights.
  It is not always safer: in the January 2026 cold snap it chose overnight hours
  that spiked, and its worst commuter night was $6.45 against the timer's $3.57.
- **Reacting to live 5-minute prices adds nothing on average.** Against the
  day-ahead plan it ranged from −1.4 to +1.5 cents a night. Varying the publication
  lag, hysteresis and persistence model never lifted it above that, and strong
  hysteresis made it clearly worse. It wins on some spike nights and loses on false
  alarms, including spikes that ComEd later revised away.
- **A better price forecast could add at most $33–67 a year** per profile: the gap
  between the day-ahead plan and perfect hindsight. A forecasting model has to
  close a real share of that gap to be worth running.
- **The capacity charge is the big daytime risk.** Plugging in at 2 p.m. and
  charging straight away overlapped 4 of PJM's 5 peak hours in summer 2025. That
  adds about $366 a year to capacity charges from PJM's half of the formula alone;
  ComEd's own five peak hours count too. All planned strategies avoided those hours.
- **Delivery Time-of-Day makes overnight charging about $200 a year cheaper** for
  the commuter. That counts the charging energy only; the rest of the home's
  usage decides whether enrolling pays.

Reproduce with `python -m sparc fetch 2025-01-01 2026-10-15` and then
`python -m sparc backtest 2025-04-01 2026-10-14 --calibrate 90`.

## Usage

Python 3.11 or newer. No third-party packages.

```sh
python -m sparc fetch 2022-01-01 2026-10-15     # backfill into runtime/sparc.db; resumable
python -m sparc check 2022-01-01 2026-10-15     # coverage, and agreement with ComEd's hourly prices
python -m sparc backtest 2023-01-01 2026-10-13 --csv runtime/nights.csv
python -m sparc collect                          # on the Pi: record live prices every 5 minutes
python -m unittest discover -s tests             # tests
```

`backtest` replays four driver profiles (in `sparc/backtest.py`) through every
strategy, under both delivery rates. It reports dollars per night, yearly
savings against charging on plug-in, the share of perfect hindsight's savings
achieved, the worst night, how often the charger starts and pauses, and energy
charged in capacity-risk hours.

## Data

- **5-minute prices** come from ComEd's documented API
  (`hourlypricing.comed.com/api?type=5minutefeed`). Each label is the start of its
  interval. Averaging the labels HH:00–HH:55 reproduces ComEd's published hourly
  prices; `check` measures that agreement on everything you fetch.
- **Day-ahead prices** come from the table behind ComEd's pricing-table page.
  It is not part of the documented API and can change without notice. PJM Data
  Miner (`da_hrl_lmps`, ComEd zone) is the official source. It needs a free PJM
  account, and non-members are limited to 6 requests a minute.
- The 5-minute feed keeps prices as first posted. ComEd's published hourly price
  is the settled one. Across 2025–2026 the two agree to rounding in all but 15 of
  15,020 hours. In 12 of those 15 they differ by more than a cent, and each time a
  posted spike was later revised down. So the backtest decides on the 5-minute
  feed and bills on the published price.
- About 4% of hours miss at least one 5-minute reading. Such hours are billed at
  the published price, or at the mean of 10–11 readings when no published price
  exists. Nights with an unpriced hour are skipped and counted.
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
| `strategies.py` | the ladder above |
| `backtest.py` | night-by-night simulation and summaries |
| `history.py`, `collector.py` | backfill and live recording |

## Next

1. Ship rung 2, the day-ahead plan, with the capacity guard. Live 5-minute
   reaction and a learned price forecast stay research until they beat it on
   held-out data.
2. Shadow mode on the Pi: run `collect`, plan every evening, and log what would
   have happened, without sending commands.
3. Tesla Fleet API through `tesla-http-proxy`, using `add_charge_schedule` so the
   car still charges if the Pi goes offline. The day-ahead plan starts charging
   about twice a night, well inside the $10 monthly credit. Commands cost $1
   per 1,000 and data reads $1 per 500, so the controller must not poll.
