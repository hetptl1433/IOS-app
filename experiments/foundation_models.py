"""Zero-shot time-series foundation models against the day-ahead price and SPARC's own forecasts.

Every decision of SPARC's forecast evaluation is replayed: each night from 14:00 to 06:00,
the model sees the hourly real-time prices posted by then (the last `--context` hours)
and, if it takes covariates, the day-ahead prices known by then, for the past and for the
hours it forecasts. It forecasts every hour up to 07:00 the next morning. Forecasts are
scored on ComEd's settled hourly prices, like `sparc.forecast.evaluate`, and then drive
the charging planner in the same backtest as every other strategy.

    python experiments/foundation_models.py --models seasonal-naive,chronos-2
    python experiments/foundation_models.py --list

The models come from Hugging Face; `pip install chronos-forecasting` (Chronos-2, Chronos-Bolt),
`pip install timesfm` (TimesFM 2.5) or `pip install tirex-ts` (TiRex) first. They run on a CPU,
slowly: allow 10-60 minutes per model for 18 months of nights.

Status: the day-ahead and seasonal-naive baselines have been run (day-ahead reproduces the
1.872-cent mean error of SPARC's own evaluation). The Chronos, TimesFM and TiRex adapters
follow each package's documentation but have not been run yet: Hugging Face was not
reachable from the environment this was written in.
"""
from __future__ import annotations

import argparse
import json
import math
import sys
import time as _time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, time, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sparc import backtest  # noqa: E402
from sparc.backtest import PROFILES  # noqa: E402
from sparc.clock import HOUR, at, hour_start  # noqa: E402
from sparc.forecast import DECISION_HOURS, LAST_TARGET  # noqa: E402
from sparc.market import Market, latest_visible_interval  # noqa: E402
from sparc.store import Store  # noqa: E402
from sparc.strategies import ChargeNow, DayAhead, Hindsight, _Planned  # noqa: E402
from sparc.tariff import STANDARD, TIME_OF_DAY  # noqa: E402


@dataclass
class Decision:
    """One moment a plan is made, and everything a model may know then."""

    now: int
    targets: list[int]  # consecutive hour starts to forecast, from the hour under way
    history: list[float]  # hourly real-time price as posted by `now`, oldest first, ending the hour before
    history_day_ahead: list[float]  # day-ahead price of the same past hours
    future_day_ahead: list[float]  # day-ahead price of the targets as known at `now`
    actual: list[float | None]  # settled price of the targets, None if unpriced (for scoring only)


def decisions(market: Market, first: date, last: date, context: int) -> list[Decision]:
    """The decisions behind every night first..last, as in `sparc.forecast.samples`."""
    posted = _posted_hours(market)
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
            history, ahead = _history(market, posted, now, context)
            result.append(Decision(now, targets, history, ahead, known, [market.hourly.get(h) for h in targets]))
        day += timedelta(days=1)
    return result


def _posted_hours(market: Market) -> dict[int, list[tuple[int, float]]]:
    hours: dict[int, list[tuple[int, float]]] = {}
    for t, price in market.five_minute.items():
        hours.setdefault(hour_start(t), []).append((t, price))
    return hours


def _history(market, posted, now, context):
    """Hourly means of the 5-minute prices posted by `now`, one per hour up to the hour before.

    A missing hour repeats the hour before it, so the series keeps its daily rhythm.
    """
    newest = latest_visible_interval(now)
    history, ahead = [], []
    for hour in range(hour_start(now) - context * HOUR, hour_start(now), HOUR):
        seen = [p for t, p in posted.get(hour, ()) if t <= newest]
        value = sum(seen) / len(seen) if seen else (history[-1] if history else None)
        day_ahead = market.day_ahead.get(hour, ahead[-1] if ahead else None)
        if value is None or day_ahead is None:
            continue  # only before the stored history begins
        history.append(value)
        ahead.append(day_ahead)
    return history, ahead


# ---------- models: each turns a batch of decisions into point forecasts ----------

Model = Callable[[list[Decision]], list[list[float]]]


def day_ahead(batch):
    return [d.future_day_ahead for d in batch]


def seasonal_naive(batch):
    """The same hour a day earlier, as posted."""
    return [[d.history[len(d.history) - 24 + (h - d.now // HOUR * HOUR) // HOUR % 24]
             if len(d.history) >= 24 else d.future_day_ahead[i] for i, h in enumerate(d.targets)] for d in batch]


def chronos(model_id: str, covariates: bool) -> Model:
    import numpy
    import torch
    from chronos import BaseChronosPipeline

    pipeline = BaseChronosPipeline.from_pretrained(model_id, device_map="cpu", torch_dtype=torch.float32)

    def predict(batch):
        if covariates:  # Chronos-2: the day-ahead price is known for the past and the forecast hours
            inputs = [{"target": numpy.asarray(d.history, dtype=float),
                       "past_covariates": {"day_ahead": numpy.asarray(d.history_day_ahead, dtype=float)},
                       "future_covariates": {"day_ahead": numpy.asarray(d.future_day_ahead, dtype=float)}}
                      for d in batch]
            horizon = max(len(d.targets) for d in batch)
            for item, d in zip(inputs, batch):  # pad the known future to a common horizon
                future = item["future_covariates"]["day_ahead"]
                item["future_covariates"]["day_ahead"] = numpy.pad(future, (0, horizon - len(future)), mode="edge")
            quantiles, mean = pipeline.predict_quantiles(inputs, prediction_length=horizon, quantile_levels=[0.5])
            return [m[0, :len(d.targets)].tolist() for m, d in zip(mean, batch)]
        horizon = max(len(d.targets) for d in batch)
        context = [torch.tensor(d.history, dtype=torch.float32) for d in batch]
        quantiles, mean = pipeline.predict_quantiles(context, prediction_length=horizon, quantile_levels=[0.5])
        return [mean[i, :len(d.targets)].tolist() for i, d in enumerate(batch)]

    return predict


def timesfm_25() -> Model:
    import numpy
    import timesfm

    model = timesfm.TimesFM_2p5_200M_torch.from_pretrained("google/timesfm-2.5-200m-pytorch")
    model.compile(timesfm.ForecastConfig(max_context=2048, max_horizon=32, normalize_inputs=True,
                                         use_continuous_quantile_head=True, fix_quantile_crossing=True))

    def predict(batch):
        horizon = max(len(d.targets) for d in batch)
        point, _ = model.forecast(horizon=horizon, inputs=[numpy.asarray(d.history, dtype=float) for d in batch])
        return [point[i, :len(d.targets)].tolist() for i, d in enumerate(batch)]

    return predict


def tirex() -> Model:
    import torch
    from tirex import load_model

    model = load_model("NX-AI/TiRex", device="cpu")

    def predict(batch):
        horizon = max(len(d.targets) for d in batch)
        context = [torch.tensor(d.history, dtype=torch.float32) for d in batch]
        quantiles, mean = model.forecast(context=context, prediction_length=horizon)
        return [mean[i, :len(d.targets)].tolist() for i, d in enumerate(batch)]

    return predict


MODELS: dict[str, Callable[[], Model]] = {
    "day-ahead": lambda: day_ahead,
    "seasonal-naive": lambda: seasonal_naive,
    "chronos-2": lambda: chronos("amazon/chronos-2", covariates=True),
    "chronos-2-no-covariates": lambda: chronos("amazon/chronos-2", covariates=False),
    "chronos-2-small": lambda: chronos("autogluon/chronos-2-small", covariates=True),
    "chronos-bolt-small": lambda: chronos("amazon/chronos-bolt-small", covariates=False),
    "timesfm-2.5": timesfm_25,
    "tirex": tirex,
}


# ---------- scoring and the charging backtest ----------

def score(batch: list[Decision], forecasts: list[list[float]]) -> dict:
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
    return {"samples": n, "mae": sum(map(abs, errors)) / n, "rmse": math.sqrt(sum(e * e for e in errors) / n),
            "day_ahead_mae": sum(map(abs, baseline)) / n, "day_ahead_rmse": math.sqrt(sum(e * e for e in baseline) / n)}


class Precomputed(_Planned):
    """Plans from forecasts made in advance for every decision time, replanning hourly like `Forecasted`."""

    stickiness = 0.5

    def __init__(self, label: str, table: dict[int, dict[int, float]]):
        self.label, self.table = label, table

    def information(self, now):
        return hour_start(now), self.released(now)

    def costs(self, now):
        known = self.table.get(hour_start(now), {})
        return {hour: known.get(hour, self.market.known_day_ahead(hour, now)) + self.cents[hour]
                for hour in self.session.hours}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--data", type=Path, default=Path("runtime"))
    parser.add_argument("--models", default="day-ahead,seasonal-naive")
    parser.add_argument("--start", type=date.fromisoformat, default=date(2025, 4, 2), help="first night")
    parser.add_argument("--end", type=date.fromisoformat, default=date(2026, 10, 14), help="last night")
    parser.add_argument("--context", type=int, default=2048, help="hours of history the model sees")
    parser.add_argument("--batch", type=int, default=64)
    parser.add_argument("--list", action="store_true")
    args = parser.parse_args(argv)
    if args.list:
        print("\n".join(MODELS))
        return 0
    store = Store(args.data / "sparc.db")
    start = at(args.start, time(0)) - (args.context + 48) * HOUR
    end = at(args.end + timedelta(days=2), time(0))
    market = Market(store.five_minute(start, end), store.day_ahead(start, end), store.published_real_time(start, end))
    batch = decisions(market, args.start, args.end, args.context)
    print(f"{len(batch):,} decisions, {sum(a is not None for d in batch for a in d.actual):,} hourly forecasts scored")
    strategies = [ChargeNow(), DayAhead()]
    report = {}
    for name in args.models.split(","):
        began = _time.monotonic()
        model = MODELS[name]()
        forecasts = []
        for i in range(0, len(batch), args.batch):
            forecasts += model(batch[i:i + args.batch])
            done = min(len(batch), i + args.batch)
            print(f"\r  {name}: {done:,}/{len(batch):,}", end="", file=sys.stderr, flush=True)
        print(file=sys.stderr)
        result = score(batch, forecasts)
        result["seconds"] = round(_time.monotonic() - began)
        report[name] = result
        print(f"{name:26} mean error {result['mae']:.3f} (day-ahead {result['day_ahead_mae']:.3f}, "
              f"{100 * (result['mae'] / result['day_ahead_mae'] - 1):+.1f}%)  RMS {result['rmse']:.3f} "
              f"(day-ahead {result['day_ahead_rmse']:.3f}, {100 * (result['rmse'] / result['day_ahead_rmse'] - 1):+.1f}%)"
              f"  {result['seconds']} s")
        table: dict[int, dict[int, float]] = {}
        for d, forecast in zip(batch, forecasts):
            table[d.now] = dict(zip(d.targets, forecast))
        if name != "day-ahead":
            strategies.append(Precomputed(f"Forecast: {name}", table))
    strategies.append(Hindsight())
    days = [args.start + timedelta(days=i) for i in range((args.end - args.start).days + 1)]
    nights, _ = backtest.run(market, days, PROFILES, strategies, [STANDARD, TIME_OF_DAY])
    summaries = backtest.summarize(nights, baseline=ChargeNow.label, best=Hindsight.label)
    print(backtest.format_table(summaries, [s.label for s in strategies]))
    (args.data / "foundation_models.json").write_text(json.dumps(report, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
