# Forecasting: can predicting the real-time price beat the day-ahead price?

SPARC plans from published day-ahead prices. The real-time price it is billed at often lands away from
them. These studies ask whether a forecast of the real-time price, learned, weather-aware or from a large
pretrained model, plans better. Every forecast uses only what was known when the plan was made, and is
scored on nights it never trained on.

| Script | Question | Result file |
|---|---|---|
| `trained_forecasts.py` | Ridge and LightGBM trained on 2025, tested on 2026: error, and savings when the planner uses them | `results/trained_forecasts.txt` |
| `weather_test.py` | Walk-forward forecasts (refit monthly) with and without weather and holidays | `results/weather_test.txt`, `.json` |
| `perfect_weather.py` | The same, as if the weather forecasts had been perfect: an upper bound for weather inputs | `results/perfect_weather.txt` |
| `weather_signal.py` | Is there any weather signal in the gap between real-time and day-ahead prices? | `results/weather_signal.txt` |
| `../foundation_models.py` | Six zero-shot time-series foundation models (Chronos, TimesFM, TiRex) through the same test | its docstring; `results/foundation_models_baselines.txt` |

## What they show

**Learned forecasts are more accurate but don't plan better.**
- `weather_test.txt`, refit monthly on the 90 days before each month, nights 2025-04-02 to 2026-10-14:
  ridge matches the day-ahead price's mean error (1.872 cents) with an 11% lower RMS error (5.632
  against 6.320). LightGBM is 2.5% better on mean error and 5% on RMS.
- Planning from either still costs 0.3 to 1.7 cents a night more than planning from the day-ahead price.
- `trained_forecasts.txt`, one model trained on 2025 and tested on 2026, gives the same picture. The
  error is lower (mean 2.249 against 2.405 cents, RMS 6.644 against 7.947 for ridge). But each model
  saves $4–7 a year less than the plan in seven of the eight model-and-driver pairs. The exception is
  LightGBM, which saves $4 more for the 2 p.m. plug-in.

**Weather adds nothing the day-ahead price doesn't already know.**
- With weather and holidays as inputs, ridge's mean error goes from 1.872 to 1.880 cents and
  LightGBM's from 1.825 to 1.842. Costs move by under 0.3 cents a night.
- `weather_signal.txt` explains why. Extreme cold predicts volatility: below −7 °C the real-time price
  misses day-ahead by more than 5 cents in 20.5% of hours, against 3.3% in mild weather. But it doesn't
  say which way. Hours that came in colder or warmer than forecast show no relation to the gap
  (correlation −0.025).
- `perfect_weather.txt` gives the models the weather that actually happened in place of the forecast.
  Ridge's mean error is then 1.882 cents and LightGBM's 1.828, about the same as with the real forecasts,
  and planning still costs 0.5 to 1.7 cents a night more than the day-ahead plan.

**Large pretrained models forecast best and still plan worse.** `../foundation_models.py` replays
every decision of the forecast test (9,528 decisions, 85,550 hourly forecasts) through six zero-shot
models: Chronos-Bolt-small, Chronos-2 with and without the day-ahead price as a covariate, TiRex,
TimesFM 2.5, and TimesFM 3.0 with the covariate.
- Against the day-ahead price's 1.872 and 6.323 cents, they cut the mean error by 9.5–19.1% and the
  RMS error by 10.5–21.1%. The best are TimesFM 3.0 (1.514, 4.987) and Chronos-2 with the covariate
  (1.523, 5.131).
- Every one of them plans worse than the day-ahead price, in all eight driver-and-rate combinations, by
  1.3 to 5.0 cents a night.
- The table with each model is in that script's docstring. A second environment with the models
  installed ran them, so their saved forecasts are not here.

For the pitch setup (Cybertruck, 11.5 kW, 20:00–07:00, Time-of-Day delivery against the default
setup), that environment computed each model's yearly saving from those saved forecasts:

| Strategy | Average (15 kWh) | Moderate (30 kWh) | Heavy (80 kWh) |
|---|---|---|---|
| Day-ahead plan | $355 | $643 | $1,224 |
| Chronos-Bolt-small | $333 | $620 | $1,205 |
| TiRex | $337 | $624 | $1,208 |
| TimesFM 2.5 | $337 | $619 | $1,209 |
| Chronos-2, no covariates | $336 | $623 | $1,210 |
| Chronos-2, day-ahead covariate | $340 | $629 | $1,221 |
| TimesFM 3.0, day-ahead covariate | $341 | $634 | $1,220 |
| Perfect hindsight | $393 | $697 | $1,297 |

At 15 and 30 kWh every model costs 2.4 to 6.5 cents a night more than the plan, each more than two
standard errors. At 80 kWh, Chronos-2 and TimesFM 3.0 with the covariate are statistically tied with
the plan. This table was computed outside this repository's scripts, so it cannot be regenerated from
here. The docstring's table can, with the models installed.

**Why accuracy doesn't translate.** The planner only needs to know which hours tonight are cheapest.
Forecasts that are better on average still pick the night's cheapest hour less often than the
day-ahead price does: 18.9–23.7% of decisions six or more hours ahead, against 24.0%.
