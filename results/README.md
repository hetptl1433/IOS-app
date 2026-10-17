# SPARC results page

`index.html` shows every headline result from the studies in [`experiments/`](../experiments/README.md) on one
page. Open it in a browser: double-click it, or run `open results/index.html` on a Mac. The charts load the
d3 library from a CDN, so they need an internet connection.

| Tab | What it shows |
|---|---|
| Overview | Yearly savings for the three drivers against the default setup, plug-in time, and the four main findings |
| Battery buffer | The cross-night rule, its gain by year with standard errors, what each part adds, and how much of the hindsight ceiling it captures |
| Strategies | Every strategy ranked by yearly saving, and the strategies tuned on 2025 tested on 2026 against the day-ahead plan |
| Forecasting | Forecast accuracy against yearly saving: every model is more accurate than the day-ahead price, and every one saves less |
| Accuracy | The RMS error of every forecast, against the day-ahead price's |
| Method | How the backtests work, and their limits |

The Average / Moderate / Heavy buttons switch the page to that driver (15, 30 or 80 kWh a night).

The numbers are in `data.js`, each block with the results file it comes from. None are computed here,
apart from simple ratios (the plan's share of hindsight, the buffer's share of its ceiling, and each
forecast's RMS error relative to the day-ahead price).
