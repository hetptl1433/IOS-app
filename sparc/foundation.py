"""AI price forecasters (time-series foundation models from Hugging Face), and a one-click test of them.

SPARC plans charging from ComEd's day-ahead prices. These models forecast the real-time price
instead, from the hourly prices posted so far and, for some, the day-ahead prices too.
`run_test` replays every decision of the study: each night from 14:00 to 06:00, a model
forecasts every hour up to 07:00 the next morning from what was known at that moment. The
forecasts are scored against ComEd's settled hourly prices, then drive the same charging
planner as the day-ahead plan, night by night, so the two can be compared in dollars.

The models need PyTorch, so they are optional. `install` adds what a model needs to the Python
environment SPARC runs in (start.command makes one in .venv) and downloads its weights from
Hugging Face. Nothing heavy is imported until a model is used.
"""
from __future__ import annotations

import gc
import importlib
import importlib.util
import json
import math
import os
import re
import subprocess
import sys
import time as _time
import uuid
from collections import defaultdict
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path

from . import backtest
from .backtest import TIERS, Cancelled, Night
from .clock import HOUR, SLOT, at, hour_start
from .forecast import DECISION_HOURS, LAST_TARGET
from .market import Market, latest_visible_interval
from .strategies import ChargeNow, DayAhead, Hindsight, _Planned
from .tariff import STANDARD, TIME_OF_DAY

CONTEXT = 2048  # hours of price history a model sees, as in the study
BATCH = 64
STUDY = (date(2025, 4, 2), date(2026, 10, 14))  # the nights behind the report's numbers
Progress = Callable[[int, int, str], None]  # (done, total, message); total 0 when unknown

NEEDS_VENV = ("SPARC can only install models into its own Python environment. Close it and start it "
              "with start.command, which makes one in the .venv folder.")


# ---------- the models ----------

CHRONOS = ("torch", "chronos-forecasting>=2.3,<3")
TIMESFM = ("torch", "timesfm[torch]>=3.0,<4")
TIREX = ("torch", "tirex-ts>=1.4,<2")


@dataclass(frozen=True)
class ModelInfo:
    id: str
    name: str
    maker: str
    summary: str  # one plain sentence
    packages: tuple[str, ...] = ()  # pip requirements
    modules: tuple[str, ...] = ()  # what must import once they are installed
    weights: tuple[tuple[str, str], ...] = ()  # (Hugging Face repository, its weights file)
    size_mb: int = 0  # download size of the weights
    uses_day_ahead: bool = False
    licence: str = ""
    seconds_per_1000: float = 0.0  # CPU time per 1,000 decisions on an Apple M3 (TimesFM 3.0 estimated)
    in_report: bool = True  # tested in the study


MODELS = {m.id: m for m in (
    ModelInfo("day-ahead", "Day-ahead price", "PJM, published by ComEd",
              "The price set the afternoon before. SPARC's plan uses it today, so every model is compared with it."),
    ModelInfo("seasonal-naive", "Same hour yesterday", "Simple rule",
              "Guesses that each hour will cost what it did 24 hours earlier. A baseline any model should beat."),
    ModelInfo("chronos-bolt-small", "Chronos-Bolt (small)", "Amazon",
              "A small, fast model that sees past prices only.",
              CHRONOS, ("chronos",), (("amazon/chronos-bolt-small", "model.safetensors"),), 191,
              seconds_per_1000=14),
    ModelInfo("chronos-2-small", "Chronos-2 (small)", "Amazon",
              "A small version of Chronos-2. Sees past prices and the day-ahead prices. Not in the report yet.",
              CHRONOS, ("chronos",), (("autogluon/chronos-2-small", "model.safetensors"),), 112,
              uses_day_ahead=True, seconds_per_1000=27, in_report=False),
    ModelInfo("chronos-2", "Chronos-2", "Amazon",
              "Sees past prices and the day-ahead prices.",
              CHRONOS, ("chronos",), (("amazon/chronos-2", "model.safetensors"),), 478,
              uses_day_ahead=True, seconds_per_1000=110),
    ModelInfo("chronos-2-no-covariates", "Chronos-2, prices only", "Amazon",
              "The same Chronos-2, without the day-ahead prices.",
              CHRONOS, ("chronos",), (("amazon/chronos-2", "model.safetensors"),), 478, seconds_per_1000=175),
    ModelInfo("tirex", "TiRex", "NX-AI",
              "A small recurrent (xLSTM) model that sees past prices only.",
              TIREX, ("tirex",), (("NX-AI/TiRex", "model.ckpt"),), 141,
              licence="NX-AI's own licence: see huggingface.co/NX-AI/TiRex", seconds_per_1000=110),
    ModelInfo("timesfm-2.5", "TimesFM 2.5", "Google",
              "Google's 200-million-parameter model. Sees past prices only.",
              TIMESFM, ("timesfm",), (("google/timesfm-2.5-200m-pytorch", "model.safetensors"),), 925,
              seconds_per_1000=170),
    ModelInfo("timesfm-3", "TimesFM 3.0", "Google",
              "Google's newest model. Sees past prices and the day-ahead prices.",
              TIMESFM, ("timesfm3",), (("google/timesfm-3.0-pytorch", "model.safetensors"),), 1323,
              uses_day_ahead=True, licence="Weights for non-commercial, non-production use only",
              seconds_per_1000=300),
)}
DOWNLOAD_PATTERNS = {"NX-AI/TiRex": ["*.ckpt", "*.json", "*.yaml", "*.txt", "*.md"]}  # skip the ONNX copy


def hub_cache() -> Path:
    """Where Hugging Face keeps downloaded models, following its own environment variables."""
    if os.environ.get("HF_HUB_CACHE"):
        return Path(os.environ["HF_HUB_CACHE"])
    home = os.environ.get("HF_HOME") or Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache") / "huggingface"
    return Path(home) / "hub"


def downloaded(repo: str, filename: str) -> bool:
    snapshots = hub_cache() / f"models--{repo.replace('/', '--')}" / "snapshots"
    return snapshots.is_dir() and any((s / filename).exists() for s in snapshots.iterdir())


def installed(info: ModelInfo) -> bool:
    importlib.invalidate_caches()
    return all(importlib.util.find_spec(m) is not None for m in info.modules)


def status() -> list[dict]:
    """Every model, with whether its libraries and weights are on this computer."""
    rows = []
    for info in MODELS.values():
        libraries = installed(info)
        weights = all(downloaded(repo, filename) for repo, filename in info.weights)
        rows.append({"id": info.id, "name": info.name, "maker": info.maker, "summary": info.summary,
                     "size_mb": info.size_mb, "uses_day_ahead": info.uses_day_ahead, "licence": info.licence,
                     "builtin": not info.packages, "in_report": info.in_report,
                     "seconds_per_1000": info.seconds_per_1000,
                     "installed": libraries, "downloaded": weights, "ready": libraries and weights})
    return rows


def environment() -> dict:
    in_venv = sys.prefix != sys.base_prefix
    return {"python": sys.version.split()[0], "venv": in_venv, "can_install": in_venv, "executable": sys.executable}


def install(ids: Sequence[str], progress: Progress | None = None,
            should_stop: Callable[[], bool] | None = None) -> dict:
    """Install the libraries the chosen models need and download their weights."""
    report = _reporter(progress, should_stop)
    infos = [MODELS[i] for i in dict.fromkeys(ids) if i in MODELS]
    missing = [info for info in infos if info.packages and not installed(info)]
    packages = list(dict.fromkeys(p for info in missing for p in info.packages))
    if packages:
        if sys.prefix == sys.base_prefix:
            raise ValueError(NEEDS_VENV)
        _pip(packages, report)
        still = [info.name for info in missing if not installed(info)]
        if still:
            raise ValueError(f"The libraries installed, but {', '.join(still)} still cannot be loaded. "
                             "Restart SPARC and try again.")
    todo = [(info, repo) for info in infos for repo, filename in info.weights if not downloaded(repo, filename)]
    todo = list({repo: (info, repo) for info, repo in todo}.values())  # Chronos-2 twice is one download
    if todo:
        from huggingface_hub import snapshot_download
        for number, (info, repo) in enumerate(todo):
            report(number, len(todo), f"Downloading {info.name} ({info.size_mb:,} MB), model {number + 1} of {len(todo)}")
            try:
                snapshot_download(repo, allow_patterns=DOWNLOAD_PATTERNS.get(repo))
            except Exception as exc:  # no network, Hugging Face down, disk full
                raise ValueError(f"Downloading {info.name} failed: {exc}") from exc
        report(len(todo), len(todo), "Downloads finished")
    return {"installed": packages, "downloaded": [repo for _, repo in todo]}


def _pip(packages: Sequence[str], report: Progress) -> None:
    command = [sys.executable, "-m", "pip", "install", "--disable-pip-version-check", "--progress-bar", "off",
               *packages]
    report(0, 0, "Installing PyTorch and the model libraries (about 1 GB, a few minutes)")
    process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    tail: list[str] = []
    try:
        for line in process.stdout:
            line = line.strip()
            if not line:
                continue
            tail = (tail + [line])[-8:]
            if line.startswith(("Collecting", "Downloading", "Installing", "Successfully")):
                report(0, 0, line[:140])
        code = process.wait()
    except Cancelled:
        process.terminate()
        process.wait()
        raise
    importlib.invalidate_caches()
    if code:
        raise ValueError("Installing the model libraries failed. The last lines pip printed:\n" + "\n".join(tail))


def device() -> str:
    """CPU, as in the study. SPARC_DEVICE=mps uses a Mac's GPU, which is faster but not bit-for-bit the same."""
    return os.environ.get("SPARC_DEVICE", "cpu")


# ---------- decisions: what a model may know at each moment a plan is made ----------

@dataclass
class Decision:
    now: int
    targets: list[int]  # consecutive hour starts to forecast, from the hour under way
    future_day_ahead: list[float]  # day-ahead price of the targets as known at `now`
    actual: list[float | None]  # settled price of the targets, None if unpriced (for scoring only)
    history: list[float] = field(default_factory=list)  # filled a batch at a time by `History.fill`
    history_day_ahead: list[float] = field(default_factory=list)


def decisions(market: Market, first: date, last: date) -> list[Decision]:
    """The decisions behind every night first..last, as in `sparc.forecast.samples`."""
    result = []
    day = first
    while day <= last:
        end = at(day + timedelta(days=1), LAST_TARGET)
        for decision in DECISION_HOURS:
            now = at(day if decision >= 14 else day + timedelta(days=1), time(decision))
            targets = list(range(hour_start(now), end, HOUR))  # consecutive, like the models' output
            known = [market.known_day_ahead(h, now) for h in targets]
            if any(v is None for v in known) or not any(h in market.hourly for h in targets):
                continue
            result.append(Decision(now, targets, known, [market.hourly.get(h) for h in targets]))
        day += timedelta(days=1)
    return result


class History:
    """Hourly means of the 5-minute prices as posted, for the history each decision sees."""

    def __init__(self, market: Market, context: int = CONTEXT):
        self.market, self.context = market, context
        self.posted: dict[int, list[tuple[int, float]]] = {}
        for t, price in market.five_minute.items():
            self.posted.setdefault(hour_start(t), []).append((t, price))
        self.full = {hour: sum(p for _, p in seen) / len(seen) for hour, seen in self.posted.items()}

    def fill(self, batch: Sequence[Decision]) -> None:
        for d in batch:
            d.history, d.history_day_ahead = self.series(d.now)

    def series(self, now: int) -> tuple[list[float], list[float]]:
        """One price per hour up to the hour before `now`; a missing hour repeats the hour before it."""
        newest = latest_visible_interval(now)
        history: list[float] = []
        ahead: list[float] = []
        day_ahead = self.market.day_ahead
        for hour in range(hour_start(now) - self.context * HOUR, hour_start(now), HOUR):
            if hour + HOUR - SLOT <= newest:
                value = self.full.get(hour)
            else:
                seen = [p for t, p in self.posted.get(hour, ()) if t <= newest]
                value = sum(seen) / len(seen) if seen else None
            if value is None:
                value = history[-1] if history else None
            known = day_ahead.get(hour, ahead[-1] if ahead else None)
            if value is None or known is None:
                continue  # only before the stored history begins
            history.append(value)
            ahead.append(known)
        return history, ahead


# ---------- adapters: each turns a batch of decisions into point forecasts ----------

Model = Callable[[list[Decision]], list[list[float]]]


def _day_ahead(batch):
    return [d.future_day_ahead for d in batch]


def _seasonal_naive(batch):
    """The same hour a day earlier, as posted."""
    return [[d.history[len(d.history) - 24 + (h - d.now // HOUR * HOUR) // HOUR % 24]
             if len(d.history) >= 24 else d.future_day_ahead[i] for i, h in enumerate(d.targets)] for d in batch]


def _by_horizon(forecast: Callable[[list[Decision], int], list]) -> Model:
    """Calls `forecast(decisions, horizon)` once per horizon, so no model is asked for hours it won't be scored on.

    A decision at 14:00 forecasts 17 hours, one at 06:00 only the hour under way. Padding a
    batch to its longest horizon would mean inventing future covariates and, for models that
    attend across the whole horizon, could change the earlier hours' forecasts.
    """
    def predict(batch):
        result: list[list[float] | None] = [None] * len(batch)
        groups: dict[int, list[int]] = {}
        for i, d in enumerate(batch):
            groups.setdefault(len(d.targets), []).append(i)
        for horizon, indices in groups.items():
            for i, values in zip(indices, forecast([batch[i] for i in indices], horizon)):
                values = [float(v) for v in values]
                if len(values) != horizon:
                    raise ValueError(f"model returned {len(values)} hours for a horizon of {horizon}")
                result[i] = values
        return result

    return predict


def _chronos(model_id: str, covariates: bool) -> Model:
    import numpy
    import torch
    from chronos import BaseChronosPipeline

    pipeline = BaseChronosPipeline.from_pretrained(model_id, device_map=device(), dtype=torch.float32)

    def forecast(batch, horizon):
        if covariates:  # Chronos-2: the day-ahead price is known for the past and the forecast hours
            inputs = [{"target": numpy.asarray(d.history, dtype=float),
                       "past_covariates": {"day_ahead": numpy.asarray(d.history_day_ahead, dtype=float)},
                       "future_covariates": {"day_ahead": numpy.asarray(d.future_day_ahead, dtype=float)}}
                      for d in batch]
        else:
            inputs = [torch.tensor(d.history, dtype=torch.float32) for d in batch]
        # Median forecasts: a list of (1, horizon) tensors for Chronos-2, a (batch, horizon) tensor for Bolt.
        _, median = pipeline.predict_quantiles(inputs, prediction_length=horizon, quantile_levels=[0.5])
        return [m.reshape(-1).tolist() for m in median]

    return _by_horizon(forecast)


def _timesfm_25() -> Model:
    import numpy
    import timesfm

    model = timesfm.TimesFM_2p5_200M_torch.from_pretrained("google/timesfm-2.5-200m-pytorch", torch_compile=False)
    # Prices can go negative, so forecasts are not clipped at zero when a context happens to be positive.
    model.compile(timesfm.ForecastConfig(max_context=2048, max_horizon=32, normalize_inputs=True,
                                         per_core_batch_size=16, use_continuous_quantile_head=True,
                                         fix_quantile_crossing=True, infer_is_positive=False))

    def forecast(batch, horizon):
        point, _ = model.forecast(horizon=horizon, inputs=[numpy.asarray(d.history, dtype=float) for d in batch])
        return list(point)

    return _by_horizon(forecast)


def _timesfm_3() -> Model:
    """TimesFM 3.0 with the day-ahead price as a past-and-future covariate.

    Its weights are licensed for non-commercial, non-production use only.
    """
    import numpy
    from timesfm3 import TimesFM3Forecaster

    model = TimesFM3Forecaster.from_pretrained("google/timesfm-3.0-pytorch", device=device(), per_core_batch_size=64)

    def forecast(batch, horizon):
        outputs = model.predict_batch(
            contexts=[numpy.asarray(d.history, dtype=numpy.float32) for d in batch], horizon=horizon,
            past_future_covariates=[numpy.asarray([d.history_day_ahead + d.future_day_ahead], dtype=numpy.float32)
                                    for d in batch])
        return [output.forecast for output in outputs]

    return _by_horizon(forecast)


def _tirex() -> Model:
    import torch
    from tirex import load_model

    model = load_model("NX-AI/TiRex", device=device())

    def forecast(batch, horizon):
        context = [torch.tensor(d.history, dtype=torch.float32) for d in batch]
        _, median = model.forecast(context=context, prediction_length=horizon)
        return median.reshape(len(batch), horizon).tolist()  # TiRex squeezes a one-hour horizon away

    return _by_horizon(forecast)


LOADERS: dict[str, Callable[[], Model]] = {
    "day-ahead": lambda: _day_ahead,
    "seasonal-naive": lambda: _seasonal_naive,
    "chronos-bolt-small": lambda: _chronos("amazon/chronos-bolt-small", covariates=False),
    "chronos-2-small": lambda: _chronos("autogluon/chronos-2-small", covariates=True),
    "chronos-2": lambda: _chronos("amazon/chronos-2", covariates=True),
    "chronos-2-no-covariates": lambda: _chronos("amazon/chronos-2", covariates=False),
    "tirex": _tirex,
    "timesfm-2.5": _timesfm_25,
    "timesfm-3": _timesfm_3,
}


def load(model_id: str) -> Model:
    """The model ready to forecast. Raises ValueError with a plain message if it cannot be loaded."""
    info = MODELS[model_id]
    if info.packages and not installed(info):
        raise ValueError(f"{info.name} is not installed yet. Press Download, or Run test, which downloads it first.")
    try:
        return LOADERS[model_id]()
    except (ImportError, OSError) as exc:
        raise ValueError(f"{info.name} could not be loaded ({type(exc).__name__}: {exc}).") from exc


def forecast_all(model_id: str, batch: Sequence[Decision], history: History, cache: Path | None = None,
                 progress: Progress | None = None, batch_size: int = BATCH) -> list[list[float]]:
    """Forecasts for every decision, reusing any saved in `cache` and saving new ones as it goes."""
    report = progress or (lambda done, total, message: None)
    name = MODELS[model_id].name
    saved = _read_cache(cache)
    todo = [i for i, d in enumerate(batch) if len(saved.get(str(d.now), ())) != len(d.targets)]
    if todo:
        report(0, len(batch), f"Loading {name}")
        model = load(model_id)
        todo.sort(key=lambda i: len(batch[i].targets))  # batches of one horizon each, as in the study
        began, done = _time.monotonic(), len(batch) - len(todo)
        for number, start in enumerate(range(0, len(todo), batch_size)):
            chunk = [batch[i] for i in todo[start:start + batch_size]]
            history.fill(chunk)
            for d, values in zip(chunk, model(chunk)):
                saved[str(d.now)] = values
                d.history, d.history_day_ahead = [], []  # keep memory down on long tests
            done += len(chunk)
            left = (_time.monotonic() - began) / (start + len(chunk)) * (len(todo) - start - len(chunk))
            report(done, len(batch), f"{name}: forecast {done:,} of {len(batch):,} decisions"
                   + (f", about {_duration(left)} left" if start + len(chunk) < len(todo) else ""))
            if cache and number % 20 == 19:
                _write_cache(cache, saved)
        if cache:
            _write_cache(cache, saved)
        del model
        _free_memory()
    return [saved[str(d.now)] for d in batch]


def score(batch: Sequence[Decision], forecasts: Sequence[Sequence[float]]) -> dict:
    """Mean and RMS error against the settled price, beside the day-ahead price's on the same hours."""
    errors, baseline = [], []
    for d, forecast in zip(batch, forecasts):
        if len(forecast) != len(d.targets):
            raise ValueError(f"forecast of {len(forecast)} hours for {len(d.targets)} targets")
        for predicted, actual, ahead in zip(forecast, d.actual, d.future_day_ahead):
            if actual is None:
                continue
            errors.append(predicted - actual)
            baseline.append(ahead - actual)
    n = len(errors)
    if not n:
        raise ValueError("No forecast hour has a settled price to score against.")
    return {"samples": n, "mae": sum(map(abs, errors)) / n, "rmse": math.sqrt(sum(e * e for e in errors) / n),
            "day_ahead_mae": sum(map(abs, baseline)) / n, "day_ahead_rmse": math.sqrt(sum(e * e for e in baseline) / n)}


Table = dict[int, tuple[int, list[float]]]  # decision time -> (first hour forecast, one price per hour)


def table(batch: Sequence[Decision], forecasts: Sequence[list[float]]) -> Table:
    return {d.now: (d.targets[0], list(f)) for d, f in zip(batch, forecasts)}


class Precomputed(_Planned):
    """Plans from forecasts made in advance for every decision time, replanning hourly like `Forecasted`.

    An hour the latest forecast does not cover is priced at its known day-ahead price.
    """

    stickiness = 0.5

    def __init__(self, label: str, forecasts: Table):
        self.label, self.forecasts = label, forecasts

    def information(self, now):
        return hour_start(now), self.released(now)

    def costs(self, now):
        first, values = self.forecasts.get(hour_start(now), (0, ()))
        result = {}
        for hour in self.session.hours:
            i = (hour - first) // HOUR
            price = values[i] if values and 0 <= i < len(values) else self.market.known_day_ahead(hour, now)
            result[hour] = price + self.cents[hour]
        return result


# ---------- the one-click test ----------

def periods(first: date | None, last: date | None, today: date | None = None) -> dict:
    """The test periods the stored prices allow: the study's nights, the last year, or every night."""
    if first is None or last is None:
        return {}
    earliest = first + timedelta(days=math.ceil((CONTEXT + 48) / 24))  # a full history before the first night
    latest = last - timedelta(days=1)  # the last night needs the next morning's prices
    if latest < earliest:
        return {}
    options = {}
    if earliest <= STUDY[0] and STUDY[1] <= latest:  # only when every one of the report's nights is there
        options["study"] = {"label": "Same nights as the report", "first": STUDY[0], "last": STUDY[1]}
    year = (max(earliest, latest - timedelta(days=364)), latest)
    options["year"] = {"label": "The last 12 months", "first": year[0], "last": year[1]}
    options["all"] = {"label": "Every night with 5-minute prices", "first": earliest, "last": latest}
    for option in options.values():
        option["nights"] = (option["last"] - option["first"]).days + 1
        option["first"], option["last"] = option["first"].isoformat(), option["last"].isoformat()
    return options


def market_for(store, first: date, last: date) -> Market:
    """The prices a test of nights first..last needs, with a full history before the first night."""
    start = at(first, time(0)) - (CONTEXT + 48) * HOUR
    end = at(last + timedelta(days=2), time(0))
    return Market(store.five_minute(start, end), store.day_ahead(start, end), store.published_real_time(start, end))


def run_test(market: Market, ids: Sequence[str], first: date, last: date, *, cache_folder: Path | None = None,
             out_folder: Path | None = None, progress: Progress | None = None,
             should_stop: Callable[[], bool] | None = None, period: str = "") -> dict:
    """Forecast, score and backtest each chosen model over nights first..last, and save the result."""
    report = _reporter(progress, should_stop)
    began = _time.monotonic()
    chosen = [i for i in dict.fromkeys(["day-ahead", *ids]) if i in MODELS]
    report(0, 0, "Working out every decision")
    batch = decisions(market, first, last)
    if not batch:
        raise ValueError("No night in this period has the prices the models need. Update the prices first.")
    history = History(market)
    rows, tables = [], {}
    for number, model_id in enumerate(chosen, 1):
        info = MODELS[model_id]
        started = _time.monotonic()
        cache = cache_folder / f"{model_id}-{CONTEXT}.json" if cache_folder else None
        step = f"Model {number} of {len(chosen)}. " if len(chosen) > 1 else ""
        forecasts = forecast_all(model_id, batch, history, cache,
                                 lambda done, total, message: report(done, total, step + message))
        accuracy = score(batch, forecasts)
        rows.append({"id": model_id, "name": info.name, "maker": info.maker, "uses_day_ahead": info.uses_day_ahead,
                     "in_report": info.in_report, "mae": accuracy["mae"], "rmse": accuracy["rmse"],
                     "day_ahead_mae": accuracy["day_ahead_mae"], "day_ahead_rmse": accuracy["day_ahead_rmse"],
                     "rmse_change": accuracy["rmse"] / accuracy["day_ahead_rmse"] - 1,
                     "seconds": round(_time.monotonic() - started)})
        if model_id != "day-ahead":
            tables[model_id] = table(batch, forecasts)
    report(0, 0, "Simulating every night")
    labels = {"day-ahead": DayAhead.label, **{m: f"Forecast: {MODELS[m].name}" for m in tables}}
    planned = [DayAhead(), *[Precomputed(labels[m], forecasts) for m, forecasts in tables.items()], Hindsight()]
    days = [first + timedelta(days=i) for i in range((last - first).days + 1)]
    nights, skipped = backtest.run(market, days, TIERS, [ChargeNow()], [STANDARD], should_stop=should_stop)
    more, _ = backtest.run(market, days, TIERS, [ChargeNow(), *planned], [TIME_OF_DAY],
                           progress=lambda done, total: report(done, total, "Simulating every night"),
                           should_stop=should_stop)
    nights += more
    if not more:
        raise ValueError("No night in this period has complete prices.")
    savings = _savings(nights)
    for row in rows:
        label = labels[row["id"]]
        row["saved"] = {p.name: savings.get((p.name, label)) for p in TIERS}
        row["vs_plan"] = {p.name: _paired(nights, p.name, label) for p in TIERS}
    created = datetime.now(timezone.utc)
    result = {
        "id": created.astimezone().strftime("%Y-%m-%d-%H%M-") + uuid.uuid4().hex[:4],  # this computer's clock
        "created": created.isoformat(timespec="seconds"),
        "period": period, "first": first.isoformat(), "last": last.isoformat(),
        "nights": {p.name: sum(1 for n in nights if n.profile == p.name and n.strategy == DayAhead.label)
                   for p in TIERS},
        "skipped": skipped, "decisions": len(batch),
        "hourly_forecasts": sum(a is not None for d in batch for a in d.actual),
        "drivers": [{"name": p.name, "kwh": p.battery_kwh, "description": p.description} for p in TIERS],
        "default_cost_per_year": {p.name: savings.get((p.name, "default")) for p in TIERS},
        "hindsight": {p.name: savings.get((p.name, Hindsight.label)) for p in TIERS},
        "rows": rows, "device": device(), "context_hours": CONTEXT,
        "elapsed_s": round(_time.monotonic() - began),
    }
    result["verdict"] = verdict(result)
    if out_folder:
        save_test(result, out_folder)
    return result


def _savings(nights: Sequence[Night]) -> dict[tuple[str, str], float]:
    """Dollars a year each strategy on Time-of-Day delivery saves against the default setup.

    The default setup is standard delivery, charging as soon as the car is plugged in. The
    ("profile", "default") entry is the default setup's own cost a year.
    """
    default = {(n.profile, n.day): n.outcome.cost for n in nights
               if n.delivery == STANDARD.name and n.strategy == ChargeNow.label}
    groups: dict[tuple[str, str], list[Night]] = defaultdict(list)
    for n in nights:
        if n.delivery == TIME_OF_DAY.name and (n.profile, n.day) in default:
            groups[n.profile, n.strategy].append(n)
    result = {}
    for (profile, strategy), group in groups.items():
        before = sum(default[profile, n.day] for n in group) / len(group)
        after = sum(n.outcome.cost for n in group) / len(group)
        result[profile, strategy] = (before - after) * 365
        result[profile, "default"] = before * 365
    return result


def _paired(nights: Sequence[Night], profile: str, label: str) -> dict | None:
    """Dollars a year `label` saves beyond the day-ahead plan on Time-of-Day delivery, ± one standard error."""
    costs: dict[str, dict[date, float]] = defaultdict(dict)
    for n in nights:
        if n.profile == profile and n.delivery == TIME_OF_DAY.name and n.strategy in (label, DayAhead.label):
            costs[n.strategy][n.day] = n.outcome.cost
    gaps = [costs[DayAhead.label][day] - cost for day, cost in costs[label].items() if day in costs[DayAhead.label]]
    if not gaps:
        return None
    n = len(gaps)
    mean = sum(gaps) / n
    error = math.sqrt(sum((g - mean) ** 2 for g in gaps) / (n - 1) / n) if n > 1 else 0.0
    return {"per_year": mean * 365, "error_per_year": error * 365, "nights": n}


def compared(vs_plan: dict | None) -> str:
    """'better', 'worse' or 'same' as the day-ahead plan, counting anything within two standard errors as the same."""
    if not vs_plan or abs(vs_plan["per_year"]) <= 2 * vs_plan["error_per_year"] or abs(vs_plan["per_year"]) < 0.5:
        return "same"
    return "better" if vs_plan["per_year"] > 0 else "worse"


def verdict(result: dict) -> str:
    """The test's outcome in a sentence or two, for the Average driver."""
    others = [r for r in result["rows"] if r["id"] != "day-ahead"]
    if not others:
        return "Only the day-ahead price was tested. Pick one or more models to compare with it."
    driver = result["drivers"][0]["name"]
    gain = {r["id"]: (r["vs_plan"].get(driver) or {"per_year": 0.0})["per_year"] for r in others}
    better = [r for r in others if compared(r["vs_plan"].get(driver)) == "better"]
    accurate = min(others, key=lambda r: r["rmse"])
    if better:
        best = max(better, key=lambda r: gain[r["id"]])
        return (f"{best['name']} saved more than SPARC's day-ahead plan: ${gain[best['id']]:,.0f} a year more for "
                f"the {driver} driver. Run it on more nights before trusting it.")
    if accurate["rmse_change"] >= 0:
        closest = max(others, key=lambda r: gain[r["id"]])
        return (f"No model beat the day-ahead price, on accuracy or on savings. The closest on savings was "
                f"{closest['name']}: {_money_gap(gain[closest['id']])} for the {driver} driver.")
    return (f"No model saved more than SPARC's day-ahead plan. The most accurate was {accurate['name']}, "
            f"{-accurate['rmse_change']:.0%} more accurate than the day-ahead price, yet it "
            f"{_money_gap(gain[accurate['id']])} for the {driver} driver.")


def _money_gap(per_year: float) -> str:
    if per_year < -0.5:
        return f"saved ${-per_year:,.0f} a year less"
    if per_year > 0.5:
        return f"saved ${per_year:,.0f} a year more, too little to be sure of"
    return "saved about the same"


# ---------- saved tests ----------

RESULTS = Path(__file__).resolve().parents[1] / "results"


def tests_folder(data_root: Path) -> Path:
    """results/model-tests in a copy of the repository, so tests can be committed; else beside the database."""
    return RESULTS / "model-tests" if RESULTS.is_dir() else data_root / "model-tests"


def save_test(result: dict, folder: Path) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{result['id']}.md").write_text(markdown(result))
    path = folder / f"{result['id']}.json"
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(result, indent=1))
    temporary.replace(path)  # last, so a listed test is always complete


def list_tests(folder: Path) -> list[dict]:
    tests = []
    for path in sorted(folder.glob("*.json"), reverse=True) if folder.is_dir() else []:
        try:
            result = json.loads(path.read_text())
        except (OSError, ValueError):
            continue
        tests.append({"id": result.get("id"), "created": result.get("created"), "first": result.get("first"),
                      "last": result.get("last"), "models": [r["name"] for r in result.get("rows", [])],
                      "verdict": result.get("verdict")})
    return tests


def load_test(folder: Path, ident: str) -> dict:
    if not re.fullmatch(r"[\w-]+", ident):
        raise LookupError("No such test.")
    path = folder / f"{ident}.json"
    if not path.is_file():
        raise LookupError("No such test.")
    return json.loads(path.read_text())


def markdown(result: dict) -> str:
    """The test as a short Markdown report, to read on GitHub."""
    drivers = [d["name"] for d in result["drivers"]]
    nights = result["nights"].get(drivers[0], 0)
    lines = [f"# Model test {result['id']}", "",
             f"Nights {result['first']} to {result['last']} ({nights:,} nights with complete prices), "
             f"{result['decisions']:,} forecasts of up to 17 hours, {result['hourly_forecasts']:,} hours scored.",
             "Cybertruck on an 11.5 kW charger, plugged in 8 p.m. to 7 a.m., Time-of-Day delivery. Savings are a year, "
             "against charging on plug-in with standard delivery. In brackets: the difference from SPARC's "
             "day-ahead plan, ± one standard error.", "", result["verdict"], "",
             "| Model | RMS error (¢/kWh) | vs day-ahead | " + " | ".join(drivers) + " |",
             "|---|---|---|" + "---|" * len(drivers)]
    for row in result["rows"]:
        cells = []
        for driver in drivers:
            saved = row["saved"].get(driver)
            gap = row["vs_plan"].get(driver)
            text = "—" if saved is None else f"${saved:,.0f}"
            if row["id"] != "day-ahead" and gap:
                text += f" ({'+' if gap['per_year'] >= 0 else '−'}${abs(gap['per_year']):,.0f} ± {gap['error_per_year']:,.0f})"
            cells.append(text)
        change = "—" if row["id"] == "day-ahead" else f"{row['rmse_change']:+.1%}".replace("-", "−")
        name = row["name"] + (" (SPARC's plan)" if row["id"] == "day-ahead" else "")
        lines.append(f"| {name} | {row['rmse']:.3f} | {change} | " + " | ".join(cells) + " |")
    hindsight = " | ".join(f"${result['hindsight'][d]:,.0f}" if result["hindsight"].get(d) is not None else "—"
                           for d in drivers)
    lines += [f"| Perfect hindsight (the most possible) | — | — | {hindsight} |", "",
              f"Ran on {result['device'].upper()} in {_duration(result['elapsed_s'])}, with {result['context_hours']:,} "
              "hours of price history per forecast. Made with SPARC's Test models page (`./start.command`).", ""]
    return "\n".join(lines)


# ---------- helpers ----------

def _reporter(progress: Progress | None, should_stop: Callable[[], bool] | None) -> Progress:
    """Pass progress on, and make every report a point where the work can be cancelled."""
    def report(done: int, total: int, message: str) -> None:
        if progress:
            progress(done, total, message)
        if should_stop and should_stop():
            raise Cancelled
    return report


def _read_cache(path: Path | None) -> dict[str, list[float]]:
    if not path or not path.is_file():
        return {}
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return {}


def _write_cache(path: Path, saved: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(saved))
    temporary.replace(path)


def _free_memory() -> None:
    gc.collect()
    torch = sys.modules.get("torch")
    if torch is not None and getattr(torch.backends, "mps", None) and torch.backends.mps.is_available():
        torch.mps.empty_cache()


def _duration(seconds: float) -> str:
    seconds = max(0, round(seconds))
    if seconds < 90:
        return f"{seconds} s"
    if seconds < 90 * 60:
        return f"{round(seconds / 60)} min"
    return f"{seconds / 3600:.1f} h"
