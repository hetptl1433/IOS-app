# SPARC, explained

Everything about SPARC in plain words, in one file, so it can be read aloud or given to an AI assistant to
answer questions about. Every number comes from the results files in this repository.

## What SPARC is

SPARC decides when to charge an electric car on ComEd's Hourly Pricing plan in Chicago, where the price of
electricity changes every hour. It charges in the cheapest hours and still has the car ready by morning. It
was tested by replaying real ComEd prices, night by night.

## What was tested

- The car: a Tesla Cybertruck (123 kWh battery) on an 11.5 kW Wall Connector, plugged in from 8 p.m. to
  7 a.m.
- Three drivers: Average adds 15 kWh a night (about 37 miles), Moderate 30 kWh (75 miles), Heavy 80 kWh
  (200 miles).
- 554 real nights of prices, from April 2, 2025 to October 14, 2026.
- Every saving is measured against the default setup: ComEd's standard delivery rate, charging as soon as
  the car is plugged in.

## The main results, for a year

| | Average | Moderate | Heavy |
|---|---|---|---|
| The default setup costs | $658 | $1,252 | $2,995 |
| Switching to Time-of-Day delivery alone saves | $165 | $363 | $1,020 |
| SPARC's plan saves | $355 (54% less) | $643 (51% less) | $1,224 (41% less) |
| SPARC plus the battery buffer saves | $410 (62% less) | $697 (56% less) | $1,233 (41% less) |
| Perfect hindsight would save | $393 | $697 | $1,297 |

So for the Average driver, a year of charging costs $658 the default way, $303 with SPARC, and $248 with SPARC
plus the battery buffer.

## Five findings

1. **Switching rate plans pays by itself.** ComEd's Time-of-Day delivery rate is cheaper at night. Switching
   saves $165 a year for the Average driver even when charging as soon as the car is plugged in.
2. **SPARC comes close to perfect.** Its plan saves 90% (Average), 92% (Moderate) and 94% (Heavy) of what
   perfect hindsight saves. Timers, price limits and live reactions to prices all did worse or the same.
3. **Better forecasts did not save more money.** The most accurate AI model, TimesFM 3.0, predicted prices
   21% better than the day-ahead price, yet saved $341 a year against SPARC's $355 for the Average driver.
   Planning only needs to know which hours are cheapest, and the day-ahead price already ranks them well.
4. **Using the battery across nights adds more.** A 123 kWh battery doesn't need a full refill every night.
   Buying extra on cheap nights and less on expensive ones adds $54 a year for the Average and Moderate
   drivers, and $10 for the Heavy driver, whose battery has little spare room.
5. **Plug in early.** Plugged in at 8 p.m., the plan saves $190 a year over charging right away on the same
   rate. Plugged in at 11 p.m., only $51, because the expensive evening hours are already over.

## How the cost is calculated

- **The price of one kWh** in an hour is ComEd's price for that hour plus the delivery charge for that hour.
  - ComEd posts a price every 5 minutes, and you pay the average of the hour's twelve prices. At night it is
    usually 2 to 5 cents per kWh; it can spike, and it can go below zero.
  - Standard delivery is 6.228 cents per kWh at every hour.
  - Time-of-Day delivery is 2.984 cents from midnight to 6 a.m., 4.009 from 6 a.m. to 1 p.m., 10.712 from
    1 to 7 p.m., 3.747 from 7 to 9 p.m., and 2.984 from 9 p.m. to midnight.
  - Fixed monthly charges and per-kWh charges that are the same at every hour, such as taxes, are left out:
    they don't depend on when you charge.
- **The cost of a night** is, for every hour, the kWh charged in that hour times the price of a kWh in that
  hour, all added up.
- **Charging loses about 10%** as heat: adding 15 kWh to the battery takes 16.7 kWh from the wall. At
  11.5 kW that is 0.96 kWh every 5 minutes, so about 1 hour 27 minutes of charging.

## One real night, worked through

The Average driver on Monday, December 1, 2025:

- **The default setup** charged from 8:00 to 9:25 p.m., when ComEd's price was 7.2 and 5.0 cents, plus
  6.228 cents standard delivery: $1.09 for the electricity plus $1.04 for delivery, $2.12 in all.
- **SPARC** charged from 2:00 to 3:25 a.m., at 4.1 and 3.5 cents, plus 2.984 cents Time-of-Day delivery:
  $0.65 plus $0.50, $1.15 in all.
- **SPARC saved $0.97** that night, about the same as on an average night.
- SPARC chose 2 a.m. because the day-ahead prices said those hours were cheapest (3.9 cents). The real
  cheapest hour turned out to be 4 to 5 a.m. (3.2 cents). A plan made the evening before can't know every
  price, which is why perfect hindsight saves a little more.

## From one night to a year

Over all 554 nights, the default setup cost $1.80 a night on average and SPARC cost $0.83. The difference,
$0.97 a night, times 365 nights is $355 a year. "Percent cheaper" is the saving divided by the default cost:
0.97 divided by 1.80 is 54%.

## How the simulation works

1. Pick a night and a driver. The car plugs in at 8 p.m. and has to be charged by 7 a.m.
2. Cut the night into 132 five-minute steps.
3. At each step the strategy decides whether to charge at full power or wait. It may only use what was known
   at that moment: the day-ahead prices, which come out around 4:30 p.m. the day before (the simulation waits
   until 5 p.m. to be safe), and the 5-minute prices posted so far, each about 7 minutes after it starts.
4. SPARC's plan ranks the night's hours by day-ahead price plus delivery charge and fills the cheapest first.
   It aims to finish 30 minutes early, and stays out of summer weekday afternoons, when using power raises
   next year's capacity charge.
5. Each kWh is billed at its hour's final price plus delivery. No strategy sees the final price ahead of time.
6. The car must be charged by 7 a.m. In these tests it always was.
7. Repeat for every night and all three drivers. A night with missing prices is skipped for every strategy
   alike, so the comparison stays fair.

## What is compared

- **The default setup:** standard delivery, charging as soon as the car is plugged in.
- **SPARC's plan:** Time-of-Day delivery, charging in the hours the day-ahead prices say are cheapest.
- **Perfect hindsight:** knows every real price in advance. Impossible in real life; it shows the most anyone
  could save.
- **The battery buffer:** buys more on cheap nights and less on expensive ones, keeping the battery between
  20% and 90% full. Tested with scripts; not in the app yet.
- **AI forecasts:** SPARC's planner using an AI model's price forecast instead of the day-ahead price.

## The AI price forecasters

RMS error is the typical size of a forecast's miss, in cents per kWh, where big misses count extra. Lower is
more accurate. Savings are a year for the Average driver on the same 554 nights.

| Forecast | Made by | RMS error (cents per kWh) | Saves a year (Average) |
|---|---|---|---|
| Day-ahead price, what SPARC uses | PJM, published by ComEd | 6.32 | $355 |
| TimesFM 3.0, with day-ahead prices | Google | 4.99 | $341 |
| Chronos-2, with day-ahead prices | Amazon | 5.13 | $340 |
| Chronos-2, prices only | Amazon | 5.35 | $336 |
| Chronos-Bolt (small) | Amazon | 5.50 | $333 |
| TiRex | NX-AI | 5.58 | $337 |
| TimesFM 2.5 | Google | 5.66 | $337 |
| Ridge | trained by SPARC | 5.63 | $343 |
| LightGBM | trained by SPARC | 5.98 | $345 |
| Same hour yesterday | simple rule | 7.36 | $330 |

Every AI model was more accurate than the day-ahead price, and every one saved less when SPARC planned with it.

## When is a difference real?

Two strategies are compared night by night on the same nights. If the average gap is smaller than about two
standard errors (a measure of how much the nightly gaps jump around), it counts as a tie, because the gap could
be luck.

## How to use the app

1. Clone the repository: `git clone https://github.com/hetptl1433/sparc.git`.
2. Double-click `start.command` (or run `./start.command`). The first start sets up Python and loads the
   prices from `comed-data/` in about 10 seconds.
3. The browser opens at http://127.0.0.1:8765 on the **Test models** page. Tick some models, pick which nights
   to replay, and press **Run test**. That one click downloads any model that is missing, runs the test, and
   saves the result in `results/model-tests/`. Commit that folder to share results.
4. On the report's nights, a test takes about 2 minutes for Chronos-Bolt, 4 for Chronos-2 (small), 18 for
   Chronos-2 or TiRex, 27 for TimesFM 2.5, 28 for Chronos-2 (prices only) and about 48 for TimesFM 3.0, on an
   Apple M3. The libraries are about 1 GB, and each model 0.1 to 1.3 GB to download.

The menu also has **Results**, **Accuracy**, **How it works** and **Details** (the full studies), and an
**Advanced lab** for fetching prices, backtesting any strategy and training forecasts.

## Where things are

- `results/`: the results pages, with every number in `results/data.js`.
- `experiments/`: every study, with its script and output. Start at `experiments/README.md`.
- `comed-data/`: ComEd's prices as CSV files, one per year (5-minute prices from 2012, hourly prices from
  2007), and Chicago weather from 2024.
- `sparc/`: the code. The simulation is `sparc/backtest.py`, the planner `sparc/planner.py`, the AI model
  test `sparc/foundation.py`.

## Limits

- The results come from 2025-2026 prices. Future prices, especially extreme ones like the January 2026 storm,
  will differ.
- The driver drives the same distance every day and plugs in every night. Real drivers vary.
- SPARC has not controlled a real car yet. Battery wear and the home's other electricity use are not counted.
