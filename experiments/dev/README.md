# Development tools

Used while building SPARC rather than to answer a research question. Kept for completeness.

| Script | What it does |
|---|---|
| `check_recent.py` | Checks the fast running-sum version of `sparc.forecast.Recent` against a direct recount at about 21,000 moments, including the edges of the data. Prints the largest difference (2.9e-12 cents) |
| `profile_backtest.py` | Profiles the web app's default backtest and times each stage |
| `profile_forecast.py` | Profiles one walk-forward forecast strategy |
| `time_models.py` | Times loading prices, building forecast samples, and training Ridge and LightGBM |
