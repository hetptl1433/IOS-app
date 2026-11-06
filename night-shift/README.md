# SPARC Night Shift

[Open the hosted simulator](https://sparc-nightshift-lab-het.hetptl1433.chatgpt.site).

The complete browser-based EV charging simulator from SPARC's ChatGPT Site, including the historical ComEd price dataset. Race charging strategies, take manual control, and compare results across historical nights. The simulator runs locally in your browser and does not control a real charger.

## Run locally

From the root of this Git repository:

```sh
python3 -m http.server 8000 --bind 127.0.0.1 --directory night-shift/dist
```

Open **http://127.0.0.1:8000** in your browser. Stop the server with **Ctrl+C**. Python 3 is the only tool needed to serve it; there is no build step or package installation. The replay data is included, so running the simulator does not require ComEd or Tesla API credentials.

## ComEd bill simulation from sparc-lite

**ComEd bill costs** opens `comed-costs.html`, or
**http://127.0.0.1:8765/simulation/comed-costs.html** in the local SPARC app.
The SPARC home page and Night Shift page also show the verified default comparison.
This view uses the Model X's daily home-charging totals and historical ComEd inputs from
`sparc-lite`, with an assumed 8 p.m.–8 a.m. window and 11.5 kW charger. It compares
fixed-price immediate charging, hourly-price immediate charging, and a day-ahead SPARC
schedule on the same completed-energy days. Connection times are hypothetical; the daily
energy totals are supplied observations. No extra charging-loss factor is added.

The comparison itemizes supply, transmission, distribution, riders and credits, Illinois
excise tax, Glenview municipal tax, and the EV's allocated monthly service and capacity costs.
The state tax uses the verified first 2,000 kWh/month tier, correcting the older input's
first-500-kWh description. Hourly bills use the average of twelve complete five-minute
readings; end-stamped source intervals are assigned to their actual delivery hour.
Missing prices and infeasible energy targets exclude a day from every method.

Other home usage (default 600 kWh/month) and account capacity obligation (default 2.5 kW)
are adjustable assumptions. The page explains how monthly account charges are allocated
by EV energy share, shows full-account charges separately, and prorates partial calendar
months. The same capacity obligation applies to both hourly strategies; this trace cannot
establish avoided peak-demand capacity charges. Calendar months approximate unknown billing cycles.

These are estimated EV costs, not reconstructed utility bills. The source lacks hourly-specific
PEA and procurement charges and uses fixed-plan transmission as an hourly-plan proxy.
Historical rate assumptions and backfills are retained in the monthly notes. The charging
address was confirmed to be inside Glenview. State and municipal excise taxes use the
same filed kWh rules on fixed BES and hourly BESH service; tax is not a percentage of the
energy price. A visible table identifies each plan's tax rates, basis, sources and status.
Glenview's first tier is 0.556¢/kWh for the first 2,000 monthly household kWh, including the
collection-cost addition already included in the filed rate. Higher tiers are modeled
progressively. Historical carry-forward assumptions are identified in the tax provenance.
Independent optional fixed/hourly municipal overrides are clearly marked user scenarios;
blank values use the filed Glenview schedule. The exports identify supply plan and tax rule,
basis and override status. Percentage franchise adjustments remain absent. Chicago-only
taxes are not applied. Sources include [ComEd Rider TAX, sheets 258 and 261](https://www.comed.com/cdn/assets/v3/assets/blt3ebb3fed6084be2a/bltb8485cefee950e7c/67b4f3aef14e110d1e948678/18_Rider_TAX.pdf?branch=prod_alias),
[Illinois tax tiers](https://tax.illinois.gov/research/taxrates/excise.html), and
[ComEd's fixed/hourly billing comparison](https://hourlypricing.comed.com/faqs/).
Input SHA-256 hashes are preserved.

Reproduce from the repository root:

```sh
python3 night-shift/build-comed-cost-data.py --source /path/to/sparc-lite
node night-shift/run-comed-costs.cjs
node --test night-shift/test-comed-costs.cjs
```

The builder leaves the source CSVs unchanged. The runner writes the full report and monthly
CSV to `results/charging-history/`, plus a small default-result bundle for the SPARC home pages.

The **Charging history** link opens `charging-history.html`. Its default dataset is now
`Dataset1_charging_reports (1).csv`, with the three companion prediction files joined and checked.
The previous TeslaMate history remains available in the **Charging dataset** selector.
**History windows** on the main simulation page compares those TeslaMate events at their recorded
times with a fixed **8 p.m.–8 a.m.** window. Cost bars use one unchanged recorded-time immediate
baseline; the hourly chart shows when the same grid energy is delivered. Date filters and the
delivery-rate selector recalculate both windows on the same completed events. The panel preserves
each event's modeled power, reports exclusions, and labels additional cost when the fixed window
costs more. These independent event scenarios are separate from the vehicle controls above.
Verify with `node --test night-shift/test-charging-windows.cjs`.
The page is also available from the local SPARC app at
**http://127.0.0.1:8765/simulation/charging-history.html** after starting `python -m sparc serve`.

The separate **Chicago comparison** page is `norway.html`, also available locally at
**http://127.0.0.1:8765/simulation/norway.html**. This dedicated Dataset 1 page shows the verified
31-strategy comparison using **time-of-day delivery**, against immediate charging on standard
delivery. Summary cards explain the best strategy and timer; the main table
explains each method, and an expandable table contains all 24 timer hours. **Download all results**
exports the 31 time-of-day results, completion and switching metrics. The page is a full-study 2025 snapshot;
replay filters and what-if settings do not change it. Its results are not shown for TeslaMate.

## Norway sessions with Norwegian prices

**Norway simulator** opens `norway-local.html`, available in the running local app at
**http://127.0.0.1:8765/simulation/norway-local.html**. This second page uses the original Norway
session dates, Europe/Oslo clocks, regional Norwegian hourly spot prices, and NOK. The existing
`norway.html` remains a separate Chicago-price comparison with a link to this page.

The page starts with the verified full-history result. Filter original dates, Norwegian price area,
or charging location, then **Run historical comparison**. It compares all available methods on
the same completed sessions, counts exclusions, and exports session/strategy CSV results. Replay
any individual source report to compare cumulative energy and cost and inspect the charged
intervals in NOK/kWh or øre/kWh. The live-price method is explicitly unavailable because this
source contains hourly day-ahead spot prices, not real-time signals. A saved summary can be viewed
immediately; run the comparison to generate per-session downloads for the current selection.

Price source: Hofmann, Bjarghov and Nessa (2023),
[Norwegian hourly residential electricity demand data with consumer characteristics during the European energy crisis](https://zenodo.org/records/8423312),
CC BY 4.0. The original download and attribution are retained in `norway-price-data/`. Prices
cover July 1, 2019 through August 7, 2021 in the replay input. All 35,377 charging reports remain
available; earlier or otherwise unpriced sessions are excluded from savings. The twelve charging
locations map to four areas: NO1, NO3, NO4 and NO5; no Dataset 1 sites use NO2. See the price-directory
README and provenance JSON for the verified location mapping, units and source checksums.

The price dataset forces every day to 24 hours by altering clock-change periods. The importer
omits four entire transition dates, and sessions needing those hours are excluded. It uses the
precise `Price_NOK_MWh / 1000` field and preserves negative prices. Publication at 13:00 Oslo on
the previous day is an explicit normal-day assumption; exact historical auction publication logs
are absent. Before tomorrow's publication, SPARC forecasts its hours from the prior local day's
same-hour prices, falling back to the current published price if necessary. Settled prices are
used for billing, and hindsight alone can inspect future unreleased prices.

Costs cover spot energy as provided. No grid fees, supplier markup, VAT, taxes, fixed charges or
subsidies are added. Each strategy knows the actual reported energy and unplug deadline. All
available strategies share original connection limits and estimated user power; no Chicago
delivery rates, US dollars, capacity-risk preference or year remapping enter this simulation.

Rebuild and reproduce locally (Python 3.11+ and Node):

```sh
python3 night-shift/download-norway-prices.py --source norway-price-data/source/prices.csv
python3 night-shift/build-norway-local-data.py
node night-shift/run-norway-local.cjs
node --test night-shift/test-norway-local.cjs night-shift/test-norway-local-page.cjs
```

Omit `--source` to fetch and verify the pinned public price source again. The run writes
`results/charging-history/norway-local-history.json`, `norway-local-strategies.csv`, and the
saved full-history summary in the offline browser bundle. Input hashes, attribution, license,
missing price dates and publication assumptions remain embedded in the report and page.

In Night Shift's **Set the scene**, the **Charging window** subsection sits below **Plug in** and
**Ready by**. Select **Set start & stop times** to choose **Charge from** and **Stop by** in five-minute
steps. For example, plug in at 8 PM, allow charging from midnight to 6 AM, and be ready by 7 AM.
These limits apply to every strategy, including SPARC, timers, and manual charging. SPARC still chooses
cheaper hours inside the allowed window. The replay retains the full plug-in-to-departure timeline.
A stop earlier than the start crosses midnight. Charging stops at the target, the stop time, or departure;
a short window reports a missed target. Times are saved with your settings and used in the full historical
test. Saved results from unrestricted charging are not reused for a custom window.

## Simulate charging history

The default **Dataset 1 · Norwegian residential** mode uses all 35,377 connection reports from
267 users at 12 Norwegian residential locations. Filter by source date, location, user, or session
energy, choose the ComEd scenario year, then **Run filtered sessions**. Dates in the filters and
table are the original Norway dates. Session details identify the mapped Chicago date and clock.

These are independent charging scenarios using known session energy and unplug deadlines. The
2018–2021 observations are not Illinois charging records or observed ComEd bills. Each source date
maps to the nearest matching weekday in the same month of 2025 (default) or 2026 (partial coverage).
Source timestamps use `Europe/Oslo`; the Chicago scenario preserves the local plug-in clock and
the original elapsed connection duration, including source daylight-saving changes. Ambiguous or
nonexistent source timestamps and mapped Chicago plug-in times are flagged and skipped. Each
five-minute boundary interval is limited to its actual connected seconds; no charging capacity is
created by rounding. Source observations from different years can map to the same price date, so
totals are not a simultaneous fleet load or an annual forecast. No battery charging loss factor is
added to the reported charger energy. The study's fixed delivery rates apply in both replay years.

The prediction files have distinct roles:

| File | Use in the simulation |
| --- | --- |
| `Dataset2_user_predictions.csv` | Estimated charger kW by `user_id`, plus labeled estimated battery capacity. 261 users have estimates; missing power requires an explicit what-if selection. |
| `Dataset3_session_predictions.csv` | Estimated SoC and idle time, joined by session and checked against user ID. These describe the supplied model, including its assumed 95% end SoC. They do not override energy or deadlines. |
| `Dataset4_hourly_predictions.csv` | Streamed reconciliation of hourly charged energy to Dataset 1 totals. All 34,537 keyed session totals reconcile within 0.01 kWh. Its 6,710 unkeyed rows are excluded from joins. The 41 MB raw file is not loaded by the browser. |

The supplied estimates use retrospective session information and user history. They are not
electricity-price forecasts or validated predictions available at plug-in. The uncontrolled track
uses constant selected power from plug-in, following the source model's convention, and is labeled
as modeled. The default standard-delivery comparison isolates timing savings. Switching to
time-of-day delivery also includes a rate change. Shortfalls never count as savings and power is
never raised to make a session finish.

Rebuild and reproduce the full scenario from the repository root (Python 3.11+ and Node required):

```sh
python3 night-shift/build-residential-data.py
node --test night-shift/test-residential.cjs night-shift/test-history.cjs
node night-shift/run-residential.cjs 2025
```

The builder preserves the original CSVs and records their SHA-256 hashes. It writes the compact
browser bundle and `results/charging-history/residential-data-quality.json`. The runner writes
`residential-scenario-2025.json` and `residential-daily-2025.csv` in the same results directory.
Daily exports group costs by **original Norway plug-in date**, while identifying the scenario year.
Session exports include source timestamps and time zone, mapped UTC boundaries, exact availability,
the estimated power/capacity/SoC fields, quality flags, and both tariffs.

Dataset and methodology: Åse Lekang Sørensen, Igor Sartori, Karen Byskov Lindberg and Inger Andresen,
[Electric vehicle charging dataset with 35,000 charging sessions from 12 residential locations in Norway](https://pmc.ncbi.nlm.nih.gov/articles/PMC11404051/),
[data repository](https://doi.org/10.5281/zenodo.12730566).

### Compare every automated strategy on Norway data

```sh
node night-shift/compare-residential.cjs 2025
```

This writes `results/charging-history/norway-strategies-2025.md`, `.csv`, and `.json`.
It also refreshes `dist/norway-comparison-data.js`, the exact report shown by the Norway page.
It tests immediate charging, all 24 timer start hours, the benchmark's 2¢/3¢/5¢ supply-price
thresholds, SPARC day-ahead, SPARC + live prices, and hindsight: 31 configurations on both
standard and time-of-day delivery. Manual actions and custom windows require explicit schedules
and have no invented fixed results.

All cost rows use the same intersection of completed, equal-energy sessions across every
configuration and both tariffs. The CSV also reports completion across all sessions that immediate
charging can finish, starts/pauses, and energy charged in capacity-risk hours. The JSON records
exclusions, hashes, missing signal counts, and the exact common-cohort hash. Savings always use
standard-delivery immediate charging as the baseline. Standard rows isolate timing; time-of-day
rows also include the delivery-rate change. Timer starts retain the existing early-start deadline
override and six-hour grace. Price-cap overrides account for actual partial boundary capacity.

The comparison loads the repository's five-minute feeds for the complete scenario and calibration
period instead of relying on the browser bundle's shorter signal history. The live model is refit
using only the 90 days before January 1, 2025, with no scenario-year observations in calibration.
Actual missing posted readings retain the existing hold-state/day-ahead fallback. The fixed
delivery rates and capacity-risk preference are shared with the simulator; capacity-charge dollars
are excluded. Hindsight is a reference with those same risk preferences, not a deployable strategy.

### Previous TeslaMate history

Select **TeslaMate · previous history** to use `charging-history-data/charging_sessions_tm1.csv`.
Choose a date range and session size, then **Run filtered sessions**. Replay any individual session,
compare the recorded-pattern estimate, immediate charging, a 1 AM timer, SPARC's day-ahead plan, and
perfect hindsight. Change power or delivery rate to explore alternatives, then export per-session results
with **Session CSV** or **Daily CSV**. The simulator uses the existing `simulation-engine.js` planner and billing code.

**Daily CSV** groups complete session costs by the session's start date in America/Chicago, in chronological
order, with one row for every date in the selected range. An overnight session is assigned to its start date.
It includes daily costs, dollars saved, daily percentages, cumulative totals, and compared/skipped/shortfall
counts. Dates without sessions have zero costs and a blank daily percentage. Dates with only excluded sessions
have blank daily costs and percentages. Amounts retain six decimal places to avoid rounding cents before summing.
The overall percentage is **total dollars saved ÷ total standard-delivery cost × 100**, weighted by cost.
It is not an arithmetic average of daily percentages. The last cumulative row reconciles to the page headline.

- The default window uses each row's recorded start and finish, rounded outward to five-minute boundaries.
  These are charging-event timestamps, not verified connection times. Rows are replayed independently.
- **Stay connected until morning** assumes extra availability, ending at the chosen departure hour or the
  next recorded session's start, while preserving the original window. This is a scenario, not observed behavior.
- `kwh_used` is grid energy; `kwh_added`, battery percentages, and CSV cost are displayed separately.
  No extra charging losses or assumed vehicle capacity are added. The recorded-pattern estimate spreads energy
  uniformly over the recorded window because interval power traces are absent.
- Recorded peak power is raised and flagged when it cannot explain the recorded energy within the rounded
  window. Lower what-if power may cause a shortfall. Only completed equal-energy comparisons enter savings totals.
- The baseline is immediate charging on standard delivery. SPARC and the alternative plans default to
  time-of-day delivery, so savings include rate and timing changes. **Planned delivery rate** changes the plans'
  rate while keeping the baseline on standard; choosing standard isolates timing savings. The existing study's
  fixed delivery rates apply across all years. This models supply plus distribution, excluding other flat and
  capacity charges. Logged CSV cost is a reference and is not the savings baseline. CSV exports identify both
  `baseline_tariff` and `planned_tariff`. Perfect hindsight is a reference, not a deployable strategy.
- Missing-price windows and ambiguous Chicago daylight-saving times are skipped and counted. Earlier prices
  needed by the history are bundled from the repository's hourly ComEd CSVs; no live APIs are called.

After changing the source CSV or price data, rebuild the browser bundle and verify the simulator:

```sh
python3 night-shift/build-history-data.py
node --test night-shift/test-history.cjs
```

These commands run from the repository root. The generated bundle records its source CSV's SHA-256 hash.

## Edit the website

| File | Purpose |
| --- | --- |
| `dist/index.html` | Homepage served at `/`. |
| `dist/simulation.html` | The same page at `/simulation.html`, used by internal home links. |
| `dist/simulation.css` | Layout, vehicle artwork styling, and animation. |
| `dist/simulation.js` | Controls, replay UI, manual challenge, and historical comparisons. |
| `dist/simulation-engine.js` | Charging strategies, planning, and billing calculations. |
| `dist/simulation-charge-target.js` | Battery target and energy calculations. |
| `dist/simulation-view.js` | Replay progress and battery presentation helpers. |
| `dist/simulation-data.js` | Bundled historical ComEd data and study metadata. |
| `dist/simulation-strategies.md` | Strategy comparison notes and study assumptions. |
| `dist/charging-history.html`, `.css`, `.js` | Recorded-session controls, replay, batch comparisons, and export. |
| `dist/charging-history-engine.js` | History windows, equal-energy comparisons, summaries, and CSV serialization. |
| `dist/charging-history-data.js` | Bundled charging records and additional historical price hours. |
| `dist/residential-data.js` | Compact normalized Dataset 1 rows with prediction metadata and reconciliation results. |
| `dist/residential-engine.js` | Explicit Norway-to-ComEd scenario mapping and source adapters. |
| `dist/norway-local.html`, `.css`, `.js` | Original-date Norway-price simulator, filters, session replay, source attribution, and exports. |
| `dist/norway-local-engine.js` | Regional Norwegian spot-price strategies, exact connection limits, and common-cohort totals. |
| `dist/norway-local-data.js` | Norwegian area prices, original sessions, provenance, and saved full-history results. |
| `download-norway-prices.py`, `build-norway-local-data.py`, `run-norway-local.cjs` | Reproducible original-date Norwegian price retrieval, bundling, and historical comparison. |
| `dist/norway.html`, `norway-comparison.css`, `norway-comparison.js` | Norway-only time-of-day results page, explanatory tables, and CSV download. |
| `dist/norway-comparison-data.js` | Verified full-study comparison embedded for the offline Norway results page. |
| `build-history-data.py` | Rebuilds the charging-history bundle from repository CSVs. |
| `build-residential-data.py` | Validates and joins all four residential CSVs without changing originals. |
| `run-residential.cjs` | Reproduces the complete residential scenario and daily exports. |
| `compare-residential.cjs` | Compares all 31 automated strategy configurations on the same Norway sessions and exports the table. |
| `test-history.cjs` | Energy, billing, window, DST, export, and full-dataset checks. |
| `test-residential.cjs` | Source joins, scenario mapping, exact boundaries, missing data, estimates, and exports. |
| `test-norway-page.cjs` | Norway-only data guards, time-of-day totals, table rendering, and downloads. |
| `dist/LICENSE` | Original MIT license. |
| `.openai/hosting.json` | Original ChatGPT Sites project and static directory configuration. |

The two HTML entrypoints currently match. After editing `simulation.html`, update the homepage too:

```sh
cp night-shift/dist/simulation.html night-shift/dist/index.html
```

## Source and publishing

Exported on October 29, 2026 from **SPARC Night Shift**, Sites project `appgprj_6ad8c3874df08191aeff361f759f5b95`, source commit `eaf258ba5296ee195a4c2f4388e504075d61dd7e`.

The original export has since been extended locally with the charging-history simulator. GitHub commits do
not automatically publish to the hosted ChatGPT Site, and later changes made through Sites need to be copied
back here to keep the two in sync. For another static host, publish the contents of `night-shift/dist`.
