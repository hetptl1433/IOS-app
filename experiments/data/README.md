# Data: where the prices come from, and why they are not in this repository

## What the studies use

| Data | Source | In this repository? |
|---|---|---|
| ComEd 5-minute real-time prices | ComEd's documented API, `hourlypricing.comed.com/api?type=5minutefeed` | No; rebuild it as below |
| ComEd day-ahead and settled hourly prices | the table behind ComEd's pricing-table page | No; rebuild it as below |
| Chicago weather: what happened, and the forecast made 24 hours before | Open-Meteo Previous Runs API (`gfs_seamless`, downtown Chicago) | Yes: `chicago_weather.csv` |
| NERC holidays | coded in `sparc/holidays.py` | Yes |
| ComEd delivery rates | ComEd's Rate BES-H and Delivery Time-of-Day tariffs, coded in `sparc/tariff.py` | Yes |

## Why the ComEd prices are left out

The repository is public, and ComEd's API terms forbid republishing what the API returns. Section 5,
"Prohibitions on content", of the
[ComEd Hourly Pricing APIs Terms of Service](https://hourlypricing.comed.com/comed-hourly-pricing-apis-terms-of-service)
says users will not, unless the content owner or applicable law permits it:

> Scrape, build databases, or otherwise create permanent copies of such content, or keep cached copies
> longer than permitted by the cache header; Copy, translate, modify, create a derivative work of, sell,
> lease, lend, convey, distribute, publicly display, or sublicense to any third party

So the price database (`runtime/sparc.db`) is git-ignored, and so are the per-night CSV files the
backtests write. A night's cost under a fixed charging schedule is close to a copy of that night's prices.
What is committed is analysis: yearly and monthly averages, savings, error statistics, and a few
individual hours quoted as evidence where a finding depends on them (the January 2026 negative prices in
`../battery_buffer/results/negative_prices.txt`, and the revised spikes below).

The weather is licensed CC BY 4.0, which allows sharing with attribution. It is included.
*Weather data by [Open-Meteo.com](https://open-meteo.com/), CC BY 4.0.*

## Rebuilding the price database

Anyone can download the same prices from ComEd's free public API. From the repository root:

```sh
python -m sparc fetch 2024-01-01 2026-10-16     # prices and weather into runtime/sparc.db; resumable
python -m sparc check 2024-01-01 2026-10-16     # coverage, and agreement with ComEd's hourly prices
```

Python 3.11 or newer is all SPARC needs. A year of data is about 425 requests at one a second, so this
range takes roughly 20 minutes. Interrupted, the fetch resumes where it stopped. Open-Meteo sometimes
answers "429 Too Many Requests". Wait a minute and run the same command again.

Every script in `experiments/` reads `runtime/sparc.db`, or `$SPARC_DATA/sparc.db` if that variable is
set. Most studies need nights from 2025-01-01. The 2024 runs need prices from 2024-01-01 onwards.

## Coverage and quality

`results/coverage_2025-01-01_2026-10-15.txt` is `python -m sparc check` on the data the studies used:
- 15,671 hours. 99.45% are billed at ComEd's published hourly price, 56 hours from the 5-minute feed
  alone, and 30 hours have no price.
- 99.84% of hours have a day-ahead price. The table for the day the clocks fall back cannot be read,
  and nights that need it are skipped and counted.
- Weather forecasts cover every hour.
- The 5-minute feed keeps prices as first posted, and ComEd's published hourly price is the settled
  one. They differ by more than rounding in 15 of 15,020 hours. Each time, a posted spike was later
  revised down (for example 2025-10-03 16:00, posted at 73.85 cents and settled at 10.4). That is why
  the backtests decide on the 5-minute feed and bill on the published price.

The second environment that ran the 2024 studies fetched 2024-01-01 to 2026-10-15 directly from ComEd.
Its check:
- 24,455 hours. 99.48% are at the published price, 88 hours from the 5-minute feed only, and 39 hours
  have no price.
- 99.80% of hours have a day-ahead price.
- Weather covers 98.21%. Open-Meteo's archive of past forecasts starts on 2024-01-19.
- The 5-minute mean differs from the published price in 24 of 23,512 hours.
- On the shared nights it matched this environment's backtest exactly: $355, $643 and $1,224 a year.

## How this environment got the data

The cloud environment these studies ran in could not reach `hourlypricing.comed.com`. The same API URLs
that `python -m sparc fetch` requests were fetched through the Firecrawl scraping service instead, and
imported with:
- `import_firecrawl.py`: 5-minute prices, each response checked against the date range it asked for;
- `import_tables.py`: day-ahead and settled hourly tables, parsed by the same code as `sparc fetch`.

Both are kept for the record. With direct access, `python -m sparc fetch` does the same thing.
`export_weather.py` writes `chicago_weather.csv` from the database. `python -m sparc export` writes every
stored price and hour of weather to `comed-data/` at the repository root, one CSV per year, and
`python -m sparc load` reads them back in seconds. ComEd's archive has hourly prices from 2007-01-01 and
5-minute prices from 2012-10-29: `python -m sparc fetch 2007-01-01 <today>` gets them all (weather only
exists from 2024). ComEd's terms restrict redistributing its prices, so share `comed-data/` only in a
private repository.
