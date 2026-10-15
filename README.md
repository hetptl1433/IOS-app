# SPARC Lab

A local research workbench for comparing electricity-price forecasts and EV charging schedules. Enter a charging request, choose forecasting models, and replay it against five-minute price data with **hourly ComEd billing**. The dashboard shows whether each strategy reaches the target, what it costs, forecast error, and when charging happens.

**No vehicle commands are sent.** This repository is the model-testing and simulation stage of SPARC. Tesla integration can be developed separately after a strategy has been validated.

## Run in one command

From this repository, with Python 3.11 or newer:

```bash
python3 -m sparc_lab
```

Open **http://127.0.0.1:8765**. No API key, npm build, Python package installation, or GPU is required for the included simulator and Ridge model. For optional ML packages, use a separate Python 3.11 or 3.12 virtual environment.

The included 190-day dataset is **deterministic synthetic data**, including synthetic day-ahead forecasts. It is useful for verifying the experiment pipeline; it is not evidence of real-world savings. The default session runs October 14, 2026 at 6 p.m. through October 15 at 7 a.m., America/Chicago.

## First experiment

1. Open **Simulator** and enter arrival, departure, battery capacity, current/target charge, power, and charging efficiency.
2. Select the synthetic sample or a dataset imported on the **Data** page.
3. Select available forecasting strategies and run the comparison. Charge-now and a clearly labeled perfect-foresight oracle are included automatically.
4. Compare **readiness first**, then cost, savings, forecast error, and switching. A cheaper strategy that undercharges has not fulfilled the same request.
5. Open **Models**, train the built-in Ridge model, and compare it on a session strictly after the model's training/validation cutoff.
6. Reopen saved experiments or export their comparison as CSV on **Experiments**.

The app stores imported data, trained models, jobs, and results in ignored `runtime/`. Everything stays on the machine running the server.

## Models

| Model | Default setup | Purpose |
|---|---|---|
| Same hour last week | Included | Seasonal forecasting baseline |
| Published day-ahead | Included when data contains publication vintages | Existing external price guidance |
| Ridge regression | Train locally, no extra packages | Genuine lightweight learned baseline |
| LightGBM | Optional packages + training | Feature-based learned forecast |
| Chronos-Bolt Tiny | Optional packages + explicitly prepared local weights | Pretrained general time-series forecasting |
| Kronos mean adapter | Experimental, unavailable until implemented | Financial candlesticks require a validated hourly-mean adaptation |

Unavailable optional models are shown honestly and cannot generate substitute results. See [model setup and training](docs/MODELS.md) for supported adapters, dependencies, and validation details.

## Data

Upload a CSV or paste its contents on the Data page. Canonical columns are:

```csv
timestamp,price,published_at,day_ahead,day_ahead_available_at
2026-10-14T23:00:00Z,6.2,2026-10-14T23:05:00Z,5.8,2026-10-13T22:00:00Z
```

Each row represents the **start of a five-minute interval**. `price` and `day_ahead` are **cents/kWh**. The example above demonstrates schema only; it is not a complete billing hour. Include all twelve intervals to form an hour.

`published_at` defaults to interval end when omitted; this is an explicit availability assumption. Day-ahead prices require `day_ahead_available_at`, so the simulator can avoid using information before publication. Duplicate timestamps and invalid/non-finite values are rejected. Negative prices remain valid.

The Data page also downloads bounded historical ranges from ComEd's public five-minute API. These downloads have no historical day-ahead forecast vintages. Publication/revision timing is approximated and clearly reported. Imported records are never described as perfectly reconstructed live data. Details: [data and billing](docs/DATA.md).

## How the simulator makes decisions

The scheduler replans from information available at each decision time. Forecasts estimate prices; a separate constrained scheduler selects charging periods. Five-minute observations update the current-hour estimate without revealing the remainder of that hour. All realized costs use the completed hour's average price for every kWh charged in that hour.

Charging efficiency separates grid kWh from battery kWh. The last interval can use fractional energy to finish exactly at the target. The initial implementation assumes constant available charging power; vehicle-specific taper, cabin conditioning, battery temperature, outages, and demand/capacity charges are not modeled. An optional variable delivery adder can be included; the result is not a complete utility bill.

**Ready-by policy** can override the soft forecast price threshold when needed to preserve the departure target. **Strict-price policy** respects the predicted-price threshold and reports any shortfall. A forecast threshold cannot guarantee the final billed hourly rate. Minimum session duration reduces switching, with exceptions for completion, hard forecast caps, and deadline feasibility.

An oracle uses future prices and is an analysis-only lower bound under the implemented physical assumptions. It is excluded from deployable-model winner selection. Review all assumptions and warnings beside each result.

## Reproducible validation

```bash
python3 -m unittest discover -s tests -v
python3 -m compileall -q sparc_lab scripts
node --check sparc_lab/web/app.js
```

The manually triggered GitHub Actions workflow runs the Python suite on Python 3.11, 3.12, and 3.13 and checks browser JavaScript syntax. It does not run automatically when code is pushed. Core tests do not download weights or install ML packages. Optional heavy adapters require their own environment and real-weight benchmark before deployment.

## Raspberry Pi

Use 64-bit Raspberry Pi OS, Python 3.11+, and the same launch command. Begin with included baselines/Ridge and benchmark optional models on the actual Pi. Do not assume the AI HAT automatically accelerates PyTorch transformers.

By default the app listens only on localhost. For an explicitly trusted local network, use `--host 0.0.0.0`; this is a development server with no authentication. Do not expose it to the public internet. See [deployment notes](docs/DEPLOYMENT.md) for a sample systemd service.

Docker is optional:

```bash
docker compose up --build
```

## Sources

- [ComEd five-minute API](https://hourlypricing.comed.com/hp-api/)
- [ComEd hourly billing, day-ahead guidance, and settlement](https://hourlypricing.comed.com/live-prices/)
- [Amazon Chronos](https://github.com/amazon-science/chronos-forecasting)
- [Financial Kronos](https://github.com/shiyu-coder/Kronos)
- [Tesla Fleet API](https://developer.tesla.com/docs/fleet-api)

MIT licensed. This independent research project is not affiliated with ComEd, Tesla, Amazon, or the Kronos authors. Third-party models retain their own licenses.
