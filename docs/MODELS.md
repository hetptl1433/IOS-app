# Forecasting and training models

SPARC keeps price prediction separate from charging decisions. A model returns
hourly-average prices in **cents/kWh**. The simulator turns those predictions into
a charging schedule and bills simulated energy against the actual completed
hourly average. Forecast MAE and charging savings are different measurements.

## What is actually implemented

| Adapter | Runs without dependencies? | Training | Forecast used |
| --- | --- | --- | --- |
| Seasonal naive | Yes | None | Latest observed same weekday/hour, then daily seasonality/hour-of-day mean |
| Published day-ahead | Yes, if data supplied | None | Only published day-ahead values; missing values cause a clear error |
| Ridge | Yes | Real regularized regression | Direct hourly mean prediction with causal lag/calendar/day-ahead features |
| LightGBM | Optional `lightgbm` | Real native boosted trees | Same causal feature schema as ridge |
| Chronos-Bolt Tiny | Optional PyTorch + Chronos + local checkpoint | Native quantile-loss fine-tuning implemented | Actual model median; no baseline substitution |
| Kronos hourly-mean | No | **Not implemented** | Disabled experimental adapter; never produces pretend Kronos results |

The core app and the ridge training tests require only Python's standard library.
Optional neural-model dependencies and weights are not bundled. This release
tests the built-in models automatically. A real Chronos-Bolt Tiny 24-hour CPU
forecast was also exercised locally with PyTorch 2.14.1, Chronos 2.3.2 and
Transformers 5.20.0; its first-call load plus forecast took about 3.9 seconds on
the development Mac. Fine-tuning and Pi performance remain unverified. “Available”
checks native dependency imports and expected checkpoints, not a Pi speed benchmark.

## Run the built-in experiment

From the repository root:

```sh
python3 scripts/train.py train ridge --demo
python3 scripts/train.py list
```

The browser's Models page invokes the same `train_model` function. The synthetic
demo proves the workflow; it is not evidence of ComEd forecast accuracy or real
money saved. Choose an arrival **after the artifact's `trained_until`** timestamp
to simulate an honest future session. The last portion of the demo is reserved
for such tests.

To train on imported five-minute interval-start records:

```sh
python3 scripts/train.py train ridge --csv /path/to/comed.csv --train-fraction 0.70
```

See [DATA.md](DATA.md) for the CSV schema. Training requires at least 504
contiguous completed hourly records (21 days); 2–3 years of genuine history is a
more useful research target. Keep negative prices and real spikes. Gaps must be
handled explicitly; the trainer rejects non-contiguous hourly histories rather
than compressing time.

### Chronological evaluation

The default split is the oldest 70% for training, the following 15% for
validation, and the newest 15% for test. No random data split is used. A default
example uses up to 28 days of observed history to predict the following 24
hours. Origins are spaced six hours apart, so forecast errors cover multiple
issue hours. Overlapping horizons are permitted and each forecasted point
counts as one error observation. `--horizon-hours` supports 1–48 hours;
`--stride-hours` controls origin spacing.

Forecasts accept an explicit `as_of` issue timestamp. The simulator passes its
actual five-minute decision time, so history and day-ahead publication checks
use that time, including observations published partway through an hour. Direct
callers that omit `as_of` use the first target hour as the conservative origin.

Ridge uses standardized causal features: latest price, trailing means, available
daily/weekly lags, Chicago hour/weekday cycles, weekend flag, forecast horizon,
and published day-ahead price plus a missingness flag. Missing lag values use
observed seasonal statistics. Feature scaling is fitted on training only. The
ridge penalty is chosen using validation MAE. LightGBM chooses tree leaf count
on validation. Neither model refits on test data.

`trained_until` includes all training **and validation** label publication times:
validation influences selection, even though validation labels never fit the
weights. The simulator rejects running an artifact before this timestamp. Test
histories may update causally between origins; test labels do not fit weights
or choose settings. The guard cannot undo information used to design an
experiment manually—avoid repeatedly selecting models against the same test.

Historical source data may contain revisions and lack original publication
vintages. Timestamp checks prevent obvious future information but cannot
reconstruct unknowable historical vintages. Mark this limitation in research
results and record genuinely observed prices going forward.

Artifacts are atomic JSON files under ignored `runtime/artifacts/`, containing
feature schema, settings, training cutoff, split dates, sample counts, dataset
fingerprint, and validation/test scores. LightGBM uses its native model text
inside JSON. No uploaded pickle is executed. New training replaces the active
artifact for that model; saved runs retain their historical outcomes.

## Optional LightGBM

Use an isolated Python 3.11 or 3.12 environment for the optional stack:

```sh
python3.12 -m venv .venv-ml
.venv-ml/bin/python -m pip install -r requirements-ml.txt
.venv-ml/bin/python scripts/train.py train lightgbm --csv /path/to/comed.csv
```

If you only need LightGBM, install `lightgbm` rather than all neural dependencies.
Some platforms also require OpenMP support; use the official installation guide.
The app must run under that same environment to see the installed packages.

## Optional real Chronos-Bolt Tiny

The adapter uses `amazon/chronos-bolt-tiny` with the upstream
`ChronosBoltPipeline`. First download the official safetensors checkpoint
**explicitly** from a trusted workstation:

```sh
.venv-ml/bin/python scripts/train.py prepare-chronos
```

Or register an already-downloaded official checkpoint directory:

```sh
.venv-ml/bin/python scripts/train.py prepare-chronos --checkpoint /path/to/chronos-bolt-tiny
```

Preparation verifies the checkpoint class, requires safetensors, and loads the
model before recording availability. Normal HTTP simulations and training never
download weights, request a remote model implementation, or silently replace a
failed model. Inference is CPU float32 and uses the genuine 0.5 quantile. The
adapter accounts for missing time between the last observation and the requested
first target by forecasting the gap before slicing the result. Internal gaps in
history cause an explicit error.

### Actual native fine-tuning

After preparing the local checkpoint:

```sh
.venv-ml/bin/python scripts/train.py train chronos-bolt --csv /path/to/comed.csv --epochs 3 --batch-size 8 --context-hours 168 --horizon-hours 24 --stride-hours 6 --device cuda
```

Choose `--device cpu` or `mps` when appropriate. This code updates the real
Chronos-Bolt parameters with AdamW, using the upstream model's quantile loss.
Training windows cannot cross split boundaries. Each epoch is evaluated on
validation median MAE; the best validation epoch is restored, then the untouched
test is scored once. The saved checkpoint uses safetensors. JSON metadata
records selected epoch, training loss history, split dates, and test MAE plus
ordinary mean pinball loss averaged across all trained quantiles and targets.

This is a direct PyTorch adaptation, not an AutoGluon wrapper, and has not been
benchmarked in the standard-library CI environment. It requires more RAM and
compute than ridge. Start with a small workstation run to establish a supported
dependency combination and reasonable loss before longer experiments. Upstream
pretraining data overlap is not auditable here, so a “zero-shot” evaluation does
not prove that pretraining never saw related data.

The browser uses whichever Chronos checkpoint is currently registered—pretrained
or fine-tuned. Preserve separate artifact directories when comparing both:

```sh
.venv-ml/bin/python scripts/train.py prepare-chronos --artifacts runtime/chronos-pretrained
.venv-ml/bin/python scripts/train.py prepare-chronos --artifacts runtime/chronos-finetuned
.venv-ml/bin/python scripts/train.py train chronos-bolt --csv /path/to/comed.csv --artifacts runtime/chronos-finetuned --device cuda
```

A fresh training experiment must start from the pristine prepared checkpoint.
Prepare it again before repeating fine-tuning: continuing from a previously
fine-tuned checkpoint is deliberately rejected because it may already contain
validation/test information from an earlier run.

The current dashboard has one active slot per adapter, so compare those runs
sequentially and retain the saved run results. It does not display two Chronos
variants as simultaneous strategies.

## Why Kronos is disabled

The shiyu-coder Kronos model is a financial candlestick model. A predicted
open/high/low/close candle does not determine an hour's average electricity
price. Computing `(open + high + low + close) / 4` and labelling that a trained
hourly-mean forecast would be misleading. Setting volume and amount to zero is
permitted by upstream data preparation, but does not solve the target mismatch.

The exact proposed integration is an experimental **frozen Kronos encoder plus
a trained direct hourly-mean head**:

1. Aggregate genuine five-minute records into complete hourly OHLC candles and
   preserve the actual hourly mean as a separate target. Keep zero volume and
   amount because no traded volume is available; do not invent financial data.
2. Install the official Kronos repository in a separate environment; load the
   official tokenizer and Kronos-mini checkpoint. Fit any electricity-specific
   scaling only on training data.
3. Freeze tokenizer/transformer parameters and extract representations at each
   causal origin. Define a stable extraction contract against a pinned upstream
   revision; current candlestick prediction helpers alone do not provide the
   required direct mean output.
4. Train a direct 24/48-hour regression head on those representations,
   historical hourly means, and calendar features. Only include day-ahead
   covariates with publication times before the issue. Select settings on later
   validation, then evaluate once on untouched test.
5. Save trusted safetensors plus a JSON feature/schema/cutoff manifest; implement
   the same forecast interface and lookahead guard. Test negative prices,
   chronological splits, target alignment, and feature extraction on held-out
   data. Compare against an identical head without Kronos representations.

Those steps are an integration plan, **not executable support** in this release.
The catalog and UI keep `kronos-mean` unavailable, and calling it raises a clear
error. Kronos remains a research comparison until this adaptation is implemented
and its results beat simpler baselines.

## Raspberry Pi and selection criteria

Train on a workstation, then copy trusted model artifacts to the Pi. The core
ridge model is small and portable. Measure RAM, forecast latency, and full
replanning latency for each optional model on your actual Pi. There is no
claimed Hailo/accelerator support and no conversion to an accelerator runtime.
PyTorch and LightGBM packaging can differ by Pi OS/architecture.

Choose models by unseen-session readiness, billed cost, missed targets, and
switch count as well as MAE. Evaluate many seasons and spike periods. The
perfect-foresight oracle is a hindsight reference and cannot be deployed. A
price model never replaces Tesla/charger protections or makes a guaranteed
final billed-price cap.

## Primary references

- [Amazon Chronos repository and model sizes](https://github.com/amazon-science/chronos-forecasting)
- [Official Chronos-Bolt implementation and native forward loss](https://github.com/amazon-science/chronos-forecasting/blob/main/src/chronos/chronos_bolt.py)
- [Official Chronos-Bolt Tiny checkpoint](https://huggingface.co/amazon/chronos-bolt-tiny)
- [LightGBM native training API](https://lightgbm.readthedocs.io/en/stable/pythonapi/lightgbm.train.html)
- [LightGBM installation](https://lightgbm.readthedocs.io/en/stable/Installation-Guide.html)
- [Official Kronos repository](https://github.com/shiyu-coder/Kronos)
- [Kronos CSV fine-tuning workflow](https://github.com/shiyu-coder/Kronos/tree/master/finetune_csv)
