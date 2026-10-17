"""Zero-shot time-series foundation models against the day-ahead price and SPARC's own forecasts.

Every decision of SPARC's forecast evaluation is replayed: each night from 14:00 to 06:00,
the model sees the hourly real-time prices posted by then (the last `--context` hours)
and, if it takes covariates, the day-ahead prices known by then, for the past and for the
hours it forecasts. It forecasts every hour up to 07:00 the next morning. Forecasts are
scored on ComEd's settled hourly prices, like `sparc.forecast.evaluate`, and then drive
the charging planner in the same backtest as every other strategy.

    python experiments/foundation_models.py --models seasonal-naive,chronos-2
    python experiments/foundation_models.py --list

The models, their adapters and the decisions they replay are in `sparc/foundation.py`, which the
web app's Test models page also uses (start.command, then pick models and press Run test; or
`python -m sparc test-models chronos-2,timesfm-3`). They come from Hugging Face and need, in a
virtual environment so SPARC itself stays dependency-free, `torch`, `chronos-forecasting`
(Chronos-2, Chronos-Bolt), `"timesfm[torch]"` (TimesFM 2.5, and TimesFM 3.0, whose weights are for
non-commercial use only) and `tirex-ts` (TiRex); the Test models page installs them. They run on
a CPU, slowly: 5-80 minutes per model for 18 months of nights on four cores, 2-30 on an Apple M3.

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
from datetime import date, time, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sparc import backtest, foundation  # noqa: E402
from sparc.backtest import PROFILES  # noqa: E402
from sparc.clock import HOUR, at  # noqa: E402
from sparc.market import Market  # noqa: E402
from sparc.store import Store  # noqa: E402
from sparc.strategies import ChargeNow, DayAhead, Hindsight  # noqa: E402
from sparc.tariff import STANDARD, TIME_OF_DAY  # noqa: E402


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
        print("\n".join(foundation.MODELS))
        return 0
    store = Store(args.data / "sparc.db")
    start = at(args.start, time(0)) - (args.context + 48) * HOUR
    end = at(args.end + timedelta(days=2), time(0))
    market = Market(store.five_minute(start, end), store.day_ahead(start, end), store.published_real_time(start, end))
    batch = foundation.decisions(market, args.start, args.end)
    history = foundation.History(market, args.context)
    print(f"{len(batch):,} decisions, {sum(a is not None for d in batch for a in d.actual):,} hourly forecasts scored")
    strategies = [ChargeNow(), DayAhead()]
    report = {}
    for name in args.models.split(","):
        began = _time.monotonic()
        forecasts = foundation.forecast_all(
            name, batch, history, batch_size=args.batch,
            progress=lambda done, total, message: print(f"\r  {message}", end="", file=sys.stderr, flush=True))
        print(file=sys.stderr)
        saved = args.data / "foundation_forecasts"
        saved.mkdir(exist_ok=True)
        (saved / f"{name}.json").write_text(json.dumps({d.now: f for d, f in zip(batch, forecasts)}))
        result = foundation.score(batch, forecasts)
        result["seconds"] = round(_time.monotonic() - began)
        report[name] = result
        print(f"{name:26} mean error {result['mae']:.3f} (day-ahead {result['day_ahead_mae']:.3f}, "
              f"{100 * (result['mae'] / result['day_ahead_mae'] - 1):+.1f}%)  RMS {result['rmse']:.3f} "
              f"(day-ahead {result['day_ahead_rmse']:.3f}, {100 * (result['rmse'] / result['day_ahead_rmse'] - 1):+.1f}%)"
              f"  {result['seconds']} s")
        if name != "day-ahead":
            strategies.append(foundation.Precomputed(f"Forecast: {name}", foundation.table(batch, forecasts)))
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
