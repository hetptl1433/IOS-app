# SPARC Night Shift

[Open the hosted simulator](https://sparc-nightshift-lab-het.hetptl1433.chatgpt.site).

The complete browser-based EV charging simulator from SPARC's ChatGPT Site, including the historical ComEd price dataset. Race charging strategies, take manual control, and compare results across historical nights. The simulator runs locally in your browser and does not control a real charger.

## Run locally

From the root of this Git repository:

```sh
python3 -m http.server 8000 --bind 127.0.0.1 --directory night-shift/dist
```

Open **http://127.0.0.1:8000** in your browser. Stop the server with **Ctrl+C**. Python 3 is the only tool needed to serve it; there is no build step or package installation. The replay data is included, so running the simulator does not require ComEd or Tesla API credentials.

The **Charging history** link opens `charging-history.html`, a separate simulator using
`charging-history-data/charging_sessions_tm1.csv`. It is also available from the local SPARC app at
**http://127.0.0.1:8765/simulation/charging-history.html** after starting `python -m sparc serve`.

In Night Shift's **Set the scene**, the **Charging window** subsection sits below **Plug in** and
**Ready by**. Select **Set start & stop times** to choose **Charge from** and **Stop by** in five-minute
steps. For example, plug in at 8 PM, allow charging from midnight to 6 AM, and be ready by 7 AM.
These limits apply to every strategy, including SPARC, timers, and manual charging. SPARC still chooses
cheaper hours inside the allowed window. The replay retains the full plug-in-to-departure timeline.
A stop earlier than the start crosses midnight. Charging stops at the target, the stop time, or departure;
a short window reports a missed target. Times are saved with your settings and used in the full historical
test. Saved results from unrestricted charging are not reused for a custom window.

## Simulate charging history

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
| `build-history-data.py` | Rebuilds the charging-history bundle from repository CSVs. |
| `test-history.cjs` | Energy, billing, window, DST, export, and full-dataset checks. |
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
