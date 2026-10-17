# Web app checks and screenshots

SPARC's local web app (`python -m sparc serve`) was checked in a real browser with these Playwright
scripts, against data and runs on this machine. They are not part of the unit tests, because they need
a running server and real prices.

| Script | What it checks |
|---|---|
| `walkthrough.js` | Clicks through every tab (data, run, results with tooltips, models) on desktop, phone and dark mode, and takes screenshots 1–10 in `screenshots/app/`. Reports console errors, page errors and failed requests |
| `model_backtest_checks.js` | Backtests a saved model from the Models tab: the first night moves to the model's first usable night, only the chosen strategies run, the delivery-rate toggle changes the figures, charts have table views, and a running backtest can be cancelled (screenshots 11–12) |
| `weather_checks.js` | The weather features: coverage on the Data tab, the weather forecast ticked by default, training a model with weather, and a one-click backtest that includes the weather forecasts (screenshots w1–w4) |
| `tiers_checks.js` | The driver presets, and the "Against the default setup" headline: $355 / $643 / $1,228 for the tiers run, 54% cheaper, one highlighted best cell per driver, no sideways scroll at phone width. Takes `screenshots/tiers/` |
| `overflow.js` | Lists any element wider than a phone screen |

To run one:

```sh
npm install playwright && npx playwright install chromium
python -m sparc serve --no-browser &        # http://127.0.0.1:8765
node experiments/webapp/tiers_checks.js /tmp/shots
```

`eslint.config.mjs` is the lint setup used for `sparc/web/app.js`, with the browser globals it uses.

## Screenshots

- `screenshots/app/`: the app on 2026-10-16, before the driver tiers were added. Data coverage, run
  settings, a run in progress, results with tooltips, month-by-month comparisons, models, a cancelled
  run, and phone and dark-mode views.
- `screenshots/tiers/`: the driver presets and the headline card, in light (desktop) and dark (phone).
