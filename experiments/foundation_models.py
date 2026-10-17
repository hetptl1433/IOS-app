"""Zero-shot time-series foundation models against the day-ahead price and SPARC's own forecasts.

Every decision of SPARC's forecast evaluation is replayed: each night from 14:00 to 06:00,
the model sees the hourly real-time prices posted by then (the last `--context` hours)
and, if it takes covariates, the day-ahead prices known by then, for the past and for the
hours it forecasts. It forecasts every hour up to 07:00 the next morning. Forecasts are
scored on ComEd's settled hourly prices, like `sparc.forecast.evaluate`, and then drive
the charging planner in the same backtest as every other strategy.

    python experiments/foundation_models.py --models seasonal-naive,chronos-2
    python experiments/foundation_models.py --list

The models come from Hugging Face. Install them in a separate virtual environment, so SPARC
itself stays dependency-free: `pip install torch --index-url https://download.pytorch.org/whl/cpu`,
then `chronos-forecasting` (Chronos-2, Chronos-Bolt), `"timesfm[torch]"` (TimesFM 2.5, and
TimesFM 3.0, whose weights are for non-commercial use only) and `tirex-ts` (TiRex). They run
on a CPU, slowly: 5-80 minutes per model for 18 months of nights on four cores.

Status (October 2026; torch 2.14.1, chronos-forecasting 2.3.2, timesfm 3.0.2, tirex-ts 1.4.2):
every model below except chronos-2-small has been run on all 9,528 decisions of 2025-04-02 to
2026-10-14 (85,550 hourly forecasts). Mean and RMS error in cents, against day-ahead's 1.872
and 6.323; then the cost per night above the day-ahead plan's across the four drivers and
both delivery rates, and what the commuter saves a year on standard delivery (plan: $374).

    chronos-bolt-small        1.695 (-9.5%)   5.501 (-13.0%)   +2.8 to +5.0 cents   $357
    tirex                     1.626 (-13.2%)  5.579 (-11.8%)   +2.4 to +4.4 cents   $360
    timesfm-2.5               1.670 (-10.8%)  5.657 (-10.5%)   +2.7 to +5.0 cents   $360
    chronos-2-no-covariates   1.610 (-14.0%)  5.352 (-15.4%)   +2.6 to +4.2 cents   $359
    chronos-2                 1.523 (-18.7%)  5.131 (-18.8%)   +1.4 to +2.9 cents   $363
    timesfm-3                 1.514 (-19.1%)  4.987 (-21.1%)   +1.3 to +3.3 cents   $365

The day-ahead price as a covariate (chronos-2, timesfm-3) helps most. Every model beats the
day-ahead price on both errors, and SPARC's ridge (1.872, 5.632) and LightGBM (1.825, 5.976)
on mean error; all but timesfm-2.5 beat ridge on RMS. Yet every one plans worse than the
day-ahead price itself in all eight driver and rate combinations, by more than two standard
errors in 40 of the 48. None picks a night's cheapest hour as often as the day-ahead price
does: 18.9-23.7% of decisions six or more hours ahead, against 24.0%.
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


def chronos(model_id: str, covariates: bool) -> Model:
    import numpy
    import torch
    from chronos import BaseChronosPipeline

    pipeline = BaseChronosPipeline.from_pretrained(model_id, device_map="cpu", dtype=torch.float32)

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


def timesfm_25() -> Model:
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


def timesfm_3() -> Model:
    """TimesFM 3.0 with the day-ahead price as a past-and-future covariate.

    Its weights are licensed for non-commercial, non-production use only.
    """
    import numpy
    from timesfm3 import TimesFM3Forecaster

    model = TimesFM3Forecaster.from_pretrained("google/timesfm-3.0-pytorch", device="cpu", per_core_batch_size=64)

    def forecast(batch, horizon):
        outputs = model.predict_batch(
            contexts=[numpy.asarray(d.history, dtype=numpy.float32) for d in batch], horizon=horizon,
            past_future_covariates=[numpy.asarray([d.history_day_ahead + d.future_day_ahead], dtype=numpy.float32)
                                    for d in batch])
        return [output.forecast for output in outputs]

    return _by_horizon(forecast)


def tirex() -> Model:
    import torch
    from tirex import load_model

    model = load_model("NX-AI/TiRex", device="cpu")

    def forecast(batch, horizon):
        context = [torch.tensor(d.history, dtype=torch.float32) for d in batch]
        _, median = model.forecast(context=context, prediction_length=horizon)
        return median.reshape(len(batch), horizon).tolist()  # TiRex squeezes a one-hour horizon away

    return _by_horizon(forecast)


MODELS: dict[str, Callable[[], Model]] = {
    "day-ahead": lambda: day_ahead,
    "seasonal-naive": lambda: seasonal_naive,
    "chronos-2": lambda: chronos("amazon/chronos-2", covariates=True),
    "chronos-2-no-covariates": lambda: chronos("amazon/chronos-2", covariates=False),
    "chronos-2-small": lambda: chronos("autogluon/chronos-2-small", covariates=True),
    "chronos-bolt-small": lambda: chronos("amazon/chronos-bolt-small", covariates=False),
    "timesfm-2.5": timesfm_25,
    "timesfm-3": timesfm_3,
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
        # Batches of one horizon each: the foundation models forecast one horizon per call.
        order = sorted(range(len(batch)), key=lambda i: len(batch[i].targets))
        forecasts: list[list[float]] = [[] for _ in batch]
        for start in range(0, len(order), args.batch):
            chunk = order[start:start + args.batch]
            for i, forecast in zip(chunk, model([batch[i] for i in chunk])):
                forecasts[i] = forecast
            done = min(len(batch), start + args.batch)
            print(f"\r  {name}: {done:,}/{len(batch):,}", end="", file=sys.stderr, flush=True)
        print(file=sys.stderr)
        saved = args.data / "foundation_forecasts"
        saved.mkdir(exist_ok=True)
        (saved / f"{name}.json").write_text(json.dumps({d.now: f for d, f in zip(batch, forecasts)}))
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
    for strategy in strategies[2:-1]:
        compared = against_day_ahead(nights, strategy.label)
        report[strategy.label.removeprefix("Forecast: ")]["backtest"] = compared
        print(f"\n{strategy.label}, cost per night minus the day-ahead plan's (cents, ± one standard error):")
        for row in compared:
            print(f"  {row['profile']:18} {row['delivery']:22} {row['cents']:+6.2f} ± {row['standard_error']:.2f}"
                  f"   saved ${row['saved_per_year']:.0f}/yr (day-ahead plan ${row['day_ahead_saved_per_year']:.0f})")
    path = args.data / "foundation_models.json"
    try:  # keep the results of models run separately
        previous = json.loads(path.read_text())
    except (OSError, ValueError):
        previous = {}
    for name, result in report.items():
        result.update(start=args.start.isoformat(), end=args.end.isoformat(), context=args.context)
        previous[name] = result
    path.write_text(json.dumps(previous, indent=1))
    return 0


def against_day_ahead(nights: list[backtest.Night], label: str) -> list[dict]:
    """Per driver and delivery rate: the mean cost per night above the day-ahead plan's, paired by night."""
    costs: dict[tuple[str, str], dict[str, dict[date, float]]] = {}
    for n in nights:
        costs.setdefault((n.profile, n.delivery), {}).setdefault(n.strategy, {})[n.day] = n.outcome.cost
    rows = []
    for (profile, delivery), by_strategy in costs.items():
        plan, base = by_strategy[DayAhead.label], by_strategy[ChargeNow.label]
        gaps = [100 * (cost - plan[day]) for day, cost in by_strategy[label].items()]
        n = len(gaps)
        mean = sum(gaps) / n
        spread = math.sqrt(sum((g - mean) ** 2 for g in gaps) / (n - 1) / n) if n > 1 else float("nan")
        rows.append({"profile": profile, "delivery": delivery, "nights": n, "cents": mean, "standard_error": spread,
                     "saved_per_year": sum(base[d] - c for d, c in by_strategy[label].items()) / n * 365,
                     "day_ahead_saved_per_year": sum(base[d] - c for d, c in plan.items()) / n * 365})
    return rows


if __name__ == "__main__":
    sys.exit(main())
