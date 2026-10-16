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
- ComEd settles real-time prices over 24 hours, so the feed can differ from the
  final billing price. Historical feeds also cannot say when each price was
  published. `collect` records that from now on.
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

1. Run the backtest on four years of prices and pick the rung to ship.
2. Shadow mode on the Pi: decide live, log, but send no commands.
3. Tesla Fleet API through `tesla-http-proxy`, using `add_charge_schedule` so the
   car still charges if the Pi goes offline. Commands cost $1 per 1,000 and data
   reads $1 per 500, with a $10 monthly credit, so the controller must not poll.
