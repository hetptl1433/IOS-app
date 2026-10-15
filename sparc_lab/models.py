"""Honest model adapters, chronological training, and portable JSON artifacts.

The standard-library models run offline. Optional packages are imported only
when requested; missing dependencies/weights never turn into baseline forecasts.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from functools import lru_cache
import hashlib
import importlib
import importlib.util
import json
import math
import os
from pathlib import Path
import statistics
import tempfile
from zoneinfo import ZoneInfo

UTC = timezone.utc
CHICAGO = ZoneInfo("America/Chicago")
HOUR = timedelta(hours=1)
FEATURES = ["last", "mean_24", "mean_168", "lag_24_or_hour_mean", "lag_168_or_hour_mean",
            "hour_sin", "hour_cos", "weekday_sin", "weekday_cos", "weekend",
            "horizon_days", "day_ahead", "has_day_ahead"]
SPECS = [
    ("seasonal-naive", "Seasonal naive", "Repeats the latest observed same weekday/hour; falls back to daily seasonality.", "baseline", False),
    ("day-ahead", "Published day-ahead", "Uses only already-published day-ahead prices. Missing values explicitly fail.", "baseline", False),
    ("ridge", "Trained ridge", "Direct multi-horizon regression with lag, calendar and available day-ahead features.", "trained", True),
    ("lightgbm", "LightGBM", "Optional native boosted-tree regression using the same causal features.", "optional", True),
    ("chronos-bolt", "Chronos-Bolt Tiny", "Optional real Amazon pretrained hourly time-series model; local weights required.", "optional", False),
    ("kronos-mean", "Kronos hourly-mean (experimental)", "Integration design only: financial candlesticks need a custom hourly-mean prediction head.", "experimental", True),
]


def _dt(value):
    result = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if result.tzinfo is None:
        raise ValueError("Model timestamps must include a timezone.")
    return result.astimezone(UTC)


def _iso(value):
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _finite(value, name="value"):
    try:
        result = float(value)
    except (ValueError, TypeError) as exc:
        raise ValueError(f"{name} must be a finite number.") from exc
    if not math.isfinite(result):
        raise ValueError(f"{name} must be a finite number.")
    return result


def _artifact_path(directory, model_id):
    return Path(directory or "runtime/artifacts") / f"{model_id}.json"


def _read_artifact(directory, model_id):
    path = _artifact_path(directory, model_id)
    try:
        with path.open(encoding="utf-8") as handle:
            artifact = json.load(handle)
    except FileNotFoundError as exc:
        raise ValueError(f"{model_id} has not been trained/prepared. See the Models page.") from exc
    except (OSError, ValueError) as exc:
        raise ValueError(f"Cannot read {model_id} artifact: {exc}") from exc
    if artifact.get("model_id") != model_id or artifact.get("schema_version") != 1:
        raise ValueError(f"Unsupported or mismatched {model_id} artifact.")
    return artifact


def _atomic_json(path, content):
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=".model-", suffix=".json", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(content, handle, allow_nan=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _installed(name):
    try:
        return importlib.util.find_spec(name) is not None
    except (ValueError, ImportError):
        return False


@lru_cache(maxsize=8)
def _dependency_error(name):
    if not _installed(name):
        return f"{name} package is not installed"
    try:
        importlib.import_module(name)
    except (ImportError, OSError, RuntimeError) as exc:
        return f"{name} cannot load: {exc}"
    return None


def _metadata(artifact):
    return {key: value for key, value in artifact.items()
            if key not in {"weights", "means", "scales", "booster_text"}}


def list_artifacts(artifacts_dir):
    """Read bounded JSON metadata only, never deserialize executable objects."""
    result = []
    directory = Path(artifacts_dir or "runtime/artifacts")
    for model_id, *_ in SPECS:
        path = directory / f"{model_id}.json"
        if not path.is_file():
            continue
        try:
            artifact = _read_artifact(directory, model_id)
            result.append(_metadata(artifact))
        except ValueError:
            continue
    return result


def get_model_catalog(artifacts_dir=None):
    artifacts = {item["model_id"]: item for item in list_artifacts(artifacts_dir)}
    result = []
    for model_id, label, description, kind, training in SPECS:
        available, reason = True, "Ready to compare."
        if model_id in {"ridge", "lightgbm"} and model_id not in artifacts:
            available, reason = False, "Train this model on the Models page first."
        if model_id == "lightgbm" and (error := _dependency_error("lightgbm")):
            available, reason = False, f"Optional LightGBM dependency unavailable: {error}. See docs/MODELS.md."
        if model_id == "chronos-bolt":
            if error := _dependency_error("chronos") or _dependency_error("torch"):
                available, reason = False, f"Optional Chronos dependency unavailable: {error}. Prepare trusted local weights after installation."
            elif model_id not in artifacts:
                available, reason = False, "Prepare local weights with scripts/train.py prepare-chronos."
            else:
                folder = artifacts[model_id].get("checkpoint_path", "")
                if not folder or not Path(folder).is_dir():
                    available, reason = False, "Prepared local Chronos checkpoint is missing."
        if model_id == "kronos-mean":
            available, reason = False, "Custom hourly-mean adaptation is not implemented; no Kronos result is simulated. See docs/MODELS.md."
        result.append({"id": model_id, "label": label, "description": description,
                       "available": available, "reason": reason, "kind": kind,
                       "requires_training": training, "trainable": model_id == "ridge" or (model_id == "lightgbm" and not _dependency_error("lightgbm")) or (model_id == "chronos-bolt" and available),
                       "artifact": artifacts.get(model_id)})
    return result


def _clean_history(history, origin=None):
    cleaned = sorted(history, key=lambda record: _dt(record["timestamp"]))
    seen = set()
    for record in cleaned:
        stamp = _dt(record["timestamp"])
        if stamp in seen:
            raise ValueError("Duplicate hourly history timestamp.")
        seen.add(stamp)
        _finite(record.get("price"), "History price")
        available = _dt(record.get("available_at", _iso(stamp + HOUR)))
        if origin is not None and (stamp + HOUR > origin or available > origin):
            raise ValueError("History includes prices unavailable at the forecast origin.")
    if not cleaned:
        raise ValueError("Forecast requires at least one completed historical hour.")
    return cleaned


class _Context:
    def __init__(self, history):
        self.history = history[-672:]
        self.lookup = {_dt(record["timestamp"]): float(record["price"]) for record in self.history}
        self.last = float(self.history[-1]["price"])
        self.mean24 = statistics.fmean(float(r["price"]) for r in self.history[-24:])
        self.mean168 = statistics.fmean(float(r["price"]) for r in self.history[-168:])
        self.hour_values = {}
        for record in self.history:
            key = _dt(record["timestamp"]).astimezone(CHICAGO).hour
            self.hour_values.setdefault(key, []).append(float(record["price"]))

    def seasonal(self, target):
        for days in (7, 1, 14, 2, 21, 3):
            if target - timedelta(days=days) in self.lookup:
                return self.lookup[target - timedelta(days=days)]
        values = self.hour_values.get(target.astimezone(CHICAGO).hour)
        return statistics.fmean(values) if values else self.mean168

    def features(self, future, origin):
        stamp = _dt(future["timestamp"])
        calendar = stamp.astimezone(CHICAGO)
        hour_angle = 2 * math.pi * calendar.hour / 24
        weekday_angle = 2 * math.pi * calendar.weekday() / 7
        seasonal = self.seasonal(stamp)
        ahead = future.get("day_ahead")
        publication = future.get("day_ahead_available_at")
        if publication and _dt(publication) > origin:
            ahead = None
        ahead = _finite(ahead, "Day-ahead price") if ahead is not None else None
        return [self.last, self.mean24, self.mean168,
                self.lookup.get(stamp - timedelta(days=1), seasonal),
                self.lookup.get(stamp - timedelta(days=7), seasonal),
                math.sin(hour_angle), math.cos(hour_angle), math.sin(weekday_angle),
                math.cos(weekday_angle), float(calendar.weekday() >= 5),
                (stamp - origin).total_seconds() / 86400,
                ahead if ahead is not None else 0.0, float(ahead is not None)]


def _check_guard(artifact, origin):
    trained_until = artifact.get("trained_until")
    if trained_until and _dt(trained_until) > origin:
        raise ValueError(f"Lookahead blocked: {artifact['model_id']} used labels through {trained_until}; choose a session after this time.")


def forecast(model_id, history, future, *, artifact_dir=None, as_of=None):
    """Return aligned point forecasts; future actual prices are never inspected."""
    if model_id not in {spec[0] for spec in SPECS}:
        raise ValueError(f"Unknown model: {model_id}")
    if not future:
        return []
    stamps = [_dt(record["timestamp"]) for record in future]
    if stamps != sorted(set(stamps)):
        raise ValueError("Future hours must be unique and chronological.")
    origin = _dt(as_of) if as_of is not None else stamps[0]
    if stamps[0] < origin.replace(minute=0, second=0, microsecond=0):
        raise ValueError("Future targets cannot include hours already completed at the forecast origin.")
    history = _clean_history(history, origin)
    context = _Context(history)
    if model_id == "seasonal-naive":
        predictions = [context.seasonal(stamp) for stamp in stamps]
    elif model_id == "day-ahead":
        predictions = []
        for record in future:
            if record.get("day_ahead") is None:
                raise ValueError("Day-ahead forecast unavailable for one or more requested hours. Choose another model or import published day-ahead data.")
            if record.get("day_ahead_available_at") and _dt(record["day_ahead_available_at"]) > origin:
                raise ValueError("Day-ahead price was not published by the forecast origin.")
            predictions.append(_finite(record["day_ahead"], "Day-ahead price"))
    elif model_id == "kronos-mean":
        raise ValueError("Kronos hourly-mean adaptation is not implemented. Financial candle forecasts are not hourly-average electricity prices. See docs/MODELS.md.")
    else:
        artifact = _read_artifact(artifact_dir, model_id)
        _check_guard(artifact, origin)
        if model_id == "ridge":
            predictions = [_predict_ridge(artifact, context.features(record, origin)) for record in future]
        elif model_id == "lightgbm":
            if error := _dependency_error("lightgbm"):
                raise ValueError(f"LightGBM dependency unavailable: {error}")
            import lightgbm as lgb
            import numpy as np
            booster = _cached_booster(artifact["booster_text"])
            predictions = booster.predict(np.asarray([context.features(record, origin) for record in future])).tolist()
        else:
            predictions = _forecast_chronos(artifact, history, stamps)
    return [_finite(value, "Forecast") for value in predictions]


@lru_cache(maxsize=2)
def _cached_booster(model_text):
    import lightgbm as lgb
    return lgb.Booster(model_str=model_text)


def _predict_ridge(artifact, raw):
    if artifact.get("features") != FEATURES or len(artifact.get("weights", [])) != len(FEATURES) + 1:
        raise ValueError("Ridge artifact feature schema is invalid.")
    scaled = [1.0] + [(value - mean) / scale for value, mean, scale in zip(raw, artifact["means"], artifact["scales"])]
    return sum(a * b for a, b in zip(scaled, artifact["weights"]))


def _solve(matrix, values):
    """Pivoted Gaussian elimination; tiny ridge systems do not need NumPy."""
    n = len(values)
    rows = [list(row) + [float(value)] for row, value in zip(matrix, values)]
    for column in range(n):
        pivot = max(range(column, n), key=lambda i: abs(rows[i][column]))
        rows[column], rows[pivot] = rows[pivot], rows[column]
        denominator = rows[column][column]
        if abs(denominator) < 1e-12:
            raise ValueError("Training system is singular; use a larger ridge penalty.")
        rows[column] = [v / denominator for v in rows[column]]
        for i in range(n):
            if i == column:
                continue
            factor = rows[i][column]
            if factor:
                rows[i] = [a - factor * b for a, b in zip(rows[i], rows[column])]
    return [row[-1] for row in rows]


def _fit_ridge(examples, alpha):
    count = len(examples)
    raw = [example[0] for example in examples]
    means = [statistics.fmean(row[i] for row in raw) for i in range(len(FEATURES))]
    scales = [max(math.sqrt(statistics.fmean((row[i] - means[i]) ** 2 for row in raw)), 1e-6) for i in range(len(FEATURES))]
    size = len(FEATURES) + 1
    gram = [[0.0] * size for _ in range(size)]
    rhs = [0.0] * size
    for row, target in examples:
        x = [1.0] + [(value - mean) / scale for value, mean, scale in zip(row, means, scales)]
        for i in range(size):
            rhs[i] += x[i] * target / count
            for j in range(i + 1):
                gram[i][j] += x[i] * x[j] / count
    for i in range(size):
        for j in range(i):
            gram[j][i] = gram[i][j]
        if i:
            gram[i][i] += alpha
    return {"features": FEATURES, "means": means, "scales": scales,
            "weights": _solve(gram, rhs), "alpha": alpha}


def _examples(records, start, end, horizon, stride):
    """Block-origin examples with causal histories and publication-aware covariates."""
    result = []
    for index in range(max(168, start), end, stride):
        origin = _dt(records[index]["timestamp"])
        known = [r for r in records[max(0, index - 672):index]
                 if _dt(r.get("available_at", _iso(_dt(r["timestamp"]) + HOUR))) <= origin]
        if not known:
            continue
        context = _Context(known)
        for target in records[index:min(index + horizon, end)]:
            # Never cross the split end to borrow validation/test labels.
            result.append((context.features(target, origin), float(target["price"])))
    return result


def _mae(actual, predicted):
    if not actual or len(actual) != len(predicted):
        raise ValueError("MAE requires equal nonempty arrays.")
    return statistics.fmean(abs(a - b) for a, b in zip(actual, predicted))


def _validate_training(hourly, train_fraction, config):
    records = _clean_history(hourly)
    if len(records) < 24 * 21:
        raise ValueError("Training requires at least 21 days (504 complete hourly records).")
    for earlier, later in zip(records, records[1:]):
        if _dt(later["timestamp"]) - _dt(earlier["timestamp"]) != HOUR:
            raise ValueError("Training requires contiguous hourly data. Fill source gaps explicitly before training.")
    fraction = _finite(train_fraction, "Training fraction")
    if not 0.5 <= fraction <= 0.85:
        raise ValueError("Training fraction must be between 0.50 and 0.85.")
    horizon = int(config.get("horizon_hours", 24))
    stride = int(config.get("stride_hours", 6))
    if not 1 <= horizon <= 48 or not 1 <= stride <= 168:
        raise ValueError("Use horizon_hours 1–48 and stride_hours 1–168.")
    train_end = int(len(records) * fraction)
    val_end = train_end + (len(records) - train_end) // 2
    if min(train_end - 168, val_end - train_end, len(records) - val_end) < 24:
        raise ValueError("Split leaves insufficient training, validation or test data.")
    return records, train_end, val_end, horizon, stride


def train_model(model_id, hourly, artifacts_dir, train_fraction=0.7, config=None):
    """Fit a real model, select settings on validation, score the untouched test.

    The selected model stays fitted on training only. trained_until nevertheless
    includes validation because choosing alpha/rounds consumes its labels.
    """
    if model_id == "chronos-bolt":
        return _train_chronos(hourly, artifacts_dir, train_fraction, config or {})
    if model_id not in {"ridge", "lightgbm"}:
        raise ValueError(f"Training is not implemented for {model_id}; no substitute model will be trained.")
    if model_id == "lightgbm" and (error := _dependency_error("lightgbm")):
        raise ValueError(f"Optional LightGBM dependency missing or unavailable: {error}. See docs/MODELS.md.")
    config = dict(config or {})
    records, train_end, val_end, horizon, stride = _validate_training(hourly, train_fraction, config)
    train = _examples(records, 168, train_end, horizon, stride)
    validation = _examples(records, train_end, val_end, horizon, stride)
    test = _examples(records, val_end, len(records), horizon, stride)
    if not train or not validation or not test:
        raise ValueError("Training configuration produces an empty split.")
    candidates = []
    if model_id == "ridge":
        alphas = config.get("alphas", [0.001, 0.01, 0.1, 1.0])
        if not isinstance(alphas, list) or not 1 <= len(alphas) <= 10:
            raise ValueError("alphas must contain 1–10 positive penalties.")
        for value in alphas:
            alpha = _finite(value, "Ridge alpha")
            if not 0 < alpha <= 10000:
                raise ValueError("Ridge alpha must be greater than zero and at most 10000.")
            fitted = _fit_ridge(train, alpha)
            error = _mae([y for _, y in validation], [_predict_ridge(fitted, x) for x, _ in validation])
            candidates.append((error, fitted))
        validation_mae, fitted = min(candidates, key=lambda candidate: candidate[0])
        predictions = [_predict_ridge(fitted, x) for x, _ in test]
        settings = {"alpha": fitted["alpha"]}
    else:
        import lightgbm as lgb
        # Fixed candidate grid chosen before test scoring, deterministic CPU fit.
        for leaves in (7, 15):
            import numpy as np
            training_data = lgb.Dataset(np.asarray([x for x, _ in train]), label=np.asarray([y for _, y in train]))
            regressor = lgb.train({"objective": "regression_l1", "learning_rate": 0.04,
                                   "num_leaves": leaves, "min_data_in_leaf": 30,
                                   "lambda_l2": 1.0, "verbosity": -1,
                                   "seed": 7, "num_threads": 1,
                                   "deterministic": True, "force_col_wise": True},
                                  training_data, num_boost_round=150)
            error = _mae([y for _, y in validation], regressor.predict(np.asarray([x for x, _ in validation])).tolist())
            candidates.append((error, regressor, leaves))
        validation_mae, regressor, leaves = min(candidates, key=lambda candidate: candidate[0])
        predictions = regressor.predict(np.asarray([x for x, _ in test])).tolist()
        fitted = {"features": FEATURES, "booster_text": regressor.model_to_string()}
        settings = {"num_leaves": leaves, "n_estimators": 150}
    signature = hashlib.sha256(json.dumps([(r["timestamp"], r["price"], r.get("day_ahead"), r.get("day_ahead_available_at")) for r in records], separators=(",", ":"), allow_nan=False).encode()).hexdigest()
    artifact = {"schema_version": 1, "model_id": model_id,
                "id": f"{model_id}-{signature[:10]}",
                "created_at": _iso(datetime.now(UTC)),
                "trained_until": _iso(max(_dt(row.get("available_at", _iso(_dt(row["timestamp"]) + HOUR))) for row in records[:val_end])),
                "fitted_until": records[train_end - 1]["timestamp"],
                "dataset_sha256": signature, "target": "hourly_average_cents_per_kwh",
                "settings": settings, "horizon_hours": horizon, "stride_hours": stride,
                "split": {"method": "chronological train / validation / untouched test",
                          "train": {"start": records[0]["timestamp"], "end": records[train_end - 1]["timestamp"], "hours": train_end, "examples": len(train)},
                          "validation": {"start": records[train_end]["timestamp"], "end": records[val_end - 1]["timestamp"], "hours": val_end - train_end, "examples": len(validation)},
                          "test": {"start": records[val_end]["timestamp"], "end": records[-1]["timestamp"], "hours": len(records) - val_end, "examples": len(test)}},
                "metrics": {"validation_mae": validation_mae,
                            "test_mae": _mae([y for _, y in test], predictions),
                            "units": "cents/kWh", "quantiles": None},
                "notes": ["Point forecasts, not calibrated uncertainty intervals.",
                          "Test history updates causally between forecast blocks; no test labels fit weights.",
                          "Model selection consumes validation labels; trained_until includes validation.",
                          "Historical source vintages may be revised; publication timestamps determine covariate eligibility."],
                **fitted}
    _atomic_json(_artifact_path(artifacts_dir, model_id), artifact)
    return _metadata(artifact)


@lru_cache(maxsize=1)
def _chronos_pipeline(checkpoint):
    if not (_installed("chronos") and _installed("torch")):
        raise ValueError("Optional Chronos and PyTorch packages are missing.")
    import torch
    from chronos import ChronosBoltPipeline
    return ChronosBoltPipeline.from_pretrained(checkpoint, device_map="cpu", torch_dtype=torch.float32,
                                              local_files_only=True, trust_remote_code=False)


def _forecast_chronos(artifact, history, stamps):
    if not (_installed("chronos") and _installed("torch")):
        raise ValueError("Optional Chronos and PyTorch packages are missing.")
    import torch
    checkpoint = artifact.get("checkpoint_path")
    if not checkpoint or not Path(checkpoint).is_dir():
        raise ValueError("Trusted local Chronos checkpoint is missing. Run prepare-chronos first.")
    if len(stamps) > 48:
        raise ValueError("Chronos adapter supports forecasts up to 48 hours.")
    for earlier, later in zip(stamps, stamps[1:]):
        if later - earlier != HOUR:
            raise ValueError("Chronos requires contiguous hourly future timestamps.")
    for earlier, later in zip(history, history[1:]):
        if _dt(later["timestamp"]) - _dt(earlier["timestamp"]) != HOUR:
            raise ValueError("Chronos requires contiguous hourly history; gaps cannot be compressed.")
    # Fill any unobserved hour between history and the requested first hour by
    # forecasting it too, then slice; never shift the prediction calendar.
    gap = int((stamps[0] - (_dt(history[-1]["timestamp"]) + HOUR)).total_seconds() / 3600)
    if gap < 0 or gap + len(stamps) > 64:
        raise ValueError("Chronos history-to-target gap exceeds the supported prediction window.")
    pipeline = _chronos_pipeline(str(Path(checkpoint).resolve()))
    tensor = torch.tensor([float(row["price"]) for row in history[-2048:]], dtype=torch.float32)
    with torch.inference_mode():
        quantiles, _mean = pipeline.predict_quantiles(tensor, prediction_length=gap + len(stamps), quantile_levels=[0.5])
    # Official output shape: batch x prediction_length x quantiles.
    return quantiles[0, gap:, 0].cpu().tolist()


def prepare_chronos(artifacts_dir, checkpoint_path=None):
    """Explicit CLI-only download/registration, never triggered by simulation."""
    if not (_installed("chronos") and _installed("torch")):
        raise ValueError("Install chronos-forecasting and torch before preparing Chronos weights.")
    if checkpoint_path is None:
        from huggingface_hub import snapshot_download
        cache_dir = Path(artifacts_dir or "runtime/artifacts").resolve() / "downloads"
        checkpoint_path = snapshot_download("amazon/chronos-bolt-tiny", cache_dir=str(cache_dir), allow_patterns=["*.json", "*.safetensors"])
    checkpoint = Path(checkpoint_path).expanduser().resolve()
    if not checkpoint.is_dir() or not (checkpoint / "config.json").is_file() or not list(checkpoint.glob("*.safetensors")):
        raise ValueError("Chronos checkpoint must contain config.json and safetensors weights.")
    with (checkpoint / "config.json").open(encoding="utf-8") as handle:
        model_config = json.load(handle)
    if model_config.get("chronos_pipeline_class") != "ChronosBoltPipeline":
        raise ValueError("Checkpoint is not a Chronos-Bolt model.")
    # Loading now catches dependency or incompatible-checkpoint issues before
    # marking the model available in the browser.
    _chronos_pipeline(str(checkpoint))
    artifact = {"schema_version": 1, "model_id": "chronos-bolt", "id": "chronos-bolt-tiny-pretrained",
                "created_at": _iso(datetime.now(UTC)), "trained_until": None,
                "checkpoint_path": str(checkpoint), "checkpoint": "amazon/chronos-bolt-tiny",
                "mode": "pretrained zero-shot", "target": "hourly_average_cents_per_kwh",
                "metrics": None, "notes": ["No SPARC fine-tuning performed.", "Upstream pretraining corpus overlap is not auditable here.",
                                              "Uses the actual model median (0.5 quantile); no substituted baseline.",
                                              "CPU latency and Pi compatibility require measurement; no Hailo support is claimed."]}
    _atomic_json(_artifact_path(artifacts_dir, "chronos-bolt"), artifact)
    return artifact


def _train_chronos(hourly, artifacts_dir, train_fraction, config):
    """Native PyTorch fine-tuning using Chronos-Bolt's real quantile loss.

    All weights are local safetensors, and no remote load/download occurs here.
    Chronological validation chooses an epoch. The test split is scored once.
    """
    if not (_installed("chronos") and _installed("torch")):
        raise ValueError("Chronos fine-tuning requires optional chronos-forecasting and torch packages.")
    import torch
    from chronos import ChronosBoltPipeline
    source = _read_artifact(artifacts_dir, "chronos-bolt")
    if source.get("trained_until"):
        raise ValueError("Prepare the pristine pretrained Chronos checkpoint again before a new fine-tuning experiment. Reusing a previously fine-tuned checkpoint can leak evaluation labels.")
    checkpoint = Path(source.get("checkpoint_path", ""))
    if not checkpoint.is_dir():
        raise ValueError("Prepare a local trusted Chronos checkpoint before fine-tuning.")
    records, train_end, val_end, horizon, stride = _validate_training(hourly, train_fraction, config)
    context_hours = int(config.get("context_hours", 168))
    epochs = int(config.get("epochs", 3))
    batch_size = int(config.get("batch_size", 8))
    learning_rate = _finite(config.get("learning_rate", 1e-5), "Learning rate")
    device = str(config.get("device", "cpu"))
    if not 24 <= context_hours <= 2048 or not 1 <= epochs <= 20 or not 1 <= batch_size <= 64:
        raise ValueError("Use context_hours 24–2048, epochs 1–20 and batch_size 1–64.")
    if not 0 < learning_rate <= 0.01 or device not in {"cpu", "cuda", "mps"}:
        raise ValueError("Use learning_rate in (0, 0.01] and device cpu, cuda or mps.")
    if device == "cuda" and not torch.cuda.is_available():
        raise ValueError("CUDA requested but unavailable; select cpu or use a CUDA workstation.")
    if device == "mps" and not torch.backends.mps.is_available():
        raise ValueError("MPS requested but unavailable.")
    torch.manual_seed(7)
    pipeline = ChronosBoltPipeline.from_pretrained(str(checkpoint), device_map=device,
                                                  torch_dtype=torch.float32, local_files_only=True,
                                                  trust_remote_code=False)
    model = pipeline.model
    if horizon > model.chronos_config.prediction_length:
        raise ValueError("Training horizon exceeds the checkpoint's direct prediction length.")
    values = [float(row["price"]) for row in records]

    def windows(start, end):
        result = []
        for index in range(max(context_hours, start), end - horizon + 1, stride):
            origin = _dt(records[index]["timestamp"])
            # Direct fixed-rate tensors cannot compress missing observations.
            context_rows = records[index - context_hours:index]
            if any(_dt(row.get("available_at", _iso(_dt(row["timestamp"]) + HOUR))) > origin for row in context_rows):
                continue
            result.append((values[index - context_hours:index], values[index:index + horizon]))
        if not result:
            raise ValueError("Chronos split has no causal full-horizon windows. Shorten context/horizon or use more contiguous data.")
        return result

    train = windows(0, train_end)
    validation = windows(train_end, val_end)
    test = windows(val_end, len(records))
    optimizer = torch.optim.AdamW(model.parameters(), lr=learning_rate, weight_decay=0.01)
    quantile_levels = list(model.chronos_config.quantiles)
    median_index = quantile_levels.index(0.5)

    def evaluate(examples):
        model.eval()
        absolute, pinball, count = 0.0, 0.0, 0
        levels = torch.tensor(quantile_levels, device=device).view(1, -1, 1)
        with torch.inference_mode():
            for index in range(0, len(examples), batch_size):
                batch = examples[index:index + batch_size]
                context = torch.tensor([x for x, _ in batch], device=device)
                target = torch.tensor([y for _, y in batch], device=device)
                predicted = model(context=context).quantile_preds[..., :horizon]
                absolute += (predicted[:, median_index, :] - target).abs().sum().item()
                difference = target.unsqueeze(1) - predicted
                # Ordinary pinball loss; no hidden x2 or scale normalization.
                pinball += torch.maximum(levels * difference, (levels - 1) * difference).sum().item()
                count += target.numel()
        return {"mae": absolute / count, "mean_pinball": pinball / (count * len(quantile_levels))}

    best_mae, best_state, best_epoch = math.inf, None, 0
    training_log = []
    for epoch in range(epochs):
        model.train()
        epoch_loss = 0.0
        # Training blocks stay in temporal order; no validation/test windows fit weights.
        for index in range(0, len(train), batch_size):
            batch = train[index:index + batch_size]
            context = torch.tensor([x for x, _ in batch], device=device)
            target = torch.tensor([y for _, y in batch], device=device)
            optimizer.zero_grad(set_to_none=True)
            loss = model(context=context, target=target).loss
            if not torch.isfinite(loss):
                raise ValueError("Chronos training produced a non-finite loss; checkpoint not saved.")
            loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
            optimizer.step()
            epoch_loss += loss.item()
        score = evaluate(validation)
        training_log.append({"epoch": epoch + 1, "training_quantile_loss": epoch_loss / math.ceil(len(train) / batch_size), "validation_mae": score["mae"]})
        if score["mae"] < best_mae:
            best_mae, best_epoch = score["mae"], epoch + 1
            best_state = {name: tensor.detach().cpu().clone() for name, tensor in model.state_dict().items()}
    model.load_state_dict(best_state)
    test_score = evaluate(test)
    signature = hashlib.sha256(json.dumps([(row["timestamp"], row["price"]) for row in records], separators=(",", ":"), allow_nan=False).encode()).hexdigest()
    output = Path(artifacts_dir or "runtime/artifacts").resolve() / f"chronos-finetuned-{signature[:10]}-{datetime.now(UTC).strftime('%Y%m%d%H%M%S%f')}"
    output.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=".chronos-", dir=output.parent))
    try:
        model.to("cpu")
        model.save_pretrained(str(staging), safe_serialization=True)
        # Ensure the native checkpoint reloads via the official pipeline.
        model.config.chronos_pipeline_class = "ChronosBoltPipeline"
        model.config.save_pretrained(str(staging))
        os.replace(staging, output)
    finally:
        if staging.exists():
            import shutil
            shutil.rmtree(staging)
    artifact = {"schema_version": 1, "model_id": "chronos-bolt", "id": output.name,
                "created_at": _iso(datetime.now(UTC)), "checkpoint_path": str(output),
                "checkpoint": "amazon/chronos-bolt-tiny", "mode": "native fine-tuned quantile model",
                "trained_until": _iso(max(_dt(row.get("available_at", _iso(_dt(row["timestamp"]) + HOUR))) for row in records[:val_end])),
                "target": "hourly_average_cents_per_kwh", "dataset_sha256": signature,
                "settings": {"epochs": epochs, "selected_epoch": best_epoch, "context_hours": context_hours,
                             "horizon_hours": horizon, "stride_hours": stride, "batch_size": batch_size,
                             "learning_rate": learning_rate, "device": device},
                "split": {"train": {"end": records[train_end - 1]["timestamp"], "windows": len(train)},
                          "validation": {"start": records[train_end]["timestamp"], "end": records[val_end - 1]["timestamp"], "windows": len(validation)},
                          "test": {"start": records[val_end]["timestamp"], "end": records[-1]["timestamp"], "windows": len(test)}},
                "metrics": {"validation_mae": best_mae, "test_mae": test_score["mae"],
                            "test_mean_pinball": test_score["mean_pinball"], "quantile_levels": quantile_levels,
                            "units": "cents/kWh"}, "training_log": training_log,
                "notes": ["Native Chronos-Bolt parameters updated using upstream quantile loss.",
                          "Validation selects the epoch; test scores never affect weights or selection.",
                          "Pretraining data overlap is not auditable here; CPU/Pi latency needs measurement."]}
    _atomic_json(_artifact_path(artifacts_dir, "chronos-bolt"), artifact)
    _chronos_pipeline.cache_clear()
    return artifact
