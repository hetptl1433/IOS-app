"""python -m sparc {serve,fetch,check,backtest,strategies,train,collect}"""
from __future__ import annotations

import argparse
import os
import shutil
import sys
from datetime import date
from pathlib import Path

from . import backtest, comed, lab
from .collector import collect
from .history import backfill, check
from .store import Store


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m sparc",
                                     description="Price-aware EV charging for ComEd Hourly Pricing.")
    parser.add_argument("--data", type=Path, default=Path(os.environ.get("SPARC_DATA", "runtime")),
                        help="folder for prices (sparc.db), backtest runs and models "
                             "(default: runtime, or $SPARC_DATA)")
    commands = parser.add_subparsers(dest="command", required=True)

    web = commands.add_parser("serve", help="open the web app: fetch prices, run backtests, train models")
    web.add_argument("--port", type=int, default=8765)
    web.add_argument("--no-browser", action="store_true", help="do not open a browser window")

    fetch = commands.add_parser("fetch", help="download 5-minute and day-ahead prices from ComEd")
    fetch.add_argument("start", type=date.fromisoformat)
    fetch.add_argument("end", type=date.fromisoformat)

    report = commands.add_parser("check", help="data coverage, and agreement with ComEd's hourly prices")
    report.add_argument("start", type=date.fromisoformat)
    report.add_argument("end", type=date.fromisoformat)

    replay = commands.add_parser("backtest", help="replay stored prices through the charging strategies")
    replay.add_argument("start", type=date.fromisoformat, help="first plug-in date")
    replay.add_argument("end", type=date.fromisoformat, help="last plug-in date")
    replay.add_argument("--calibrate", type=int, default=90, metavar="DAYS",
                        help="days before START that the live strategy and the forecasts learn from (default 90)")
    replay.add_argument("--strategies", metavar="IDS",
                        help="comma-separated strategy ids (default: every one that can run; "
                             "see `python -m sparc strategies`)")
    replay.add_argument("--csv", type=Path, help="also copy every night's result to this file")

    commands.add_parser("strategies", help="list the strategy ids that backtest accepts")

    learn = commands.add_parser("train", help="train a price forecast for the forecast strategy")
    learn.add_argument("kind", choices=["ridge", "lightgbm"])
    learn.add_argument("start", type=date.fromisoformat, help="first training night")
    learn.add_argument("end", type=date.fromisoformat, help="last training night")
    learn.add_argument("--test", nargs=2, type=date.fromisoformat, metavar=("START", "END"),
                       help="score the forecast on these nights")
    learn.add_argument("--name", help="a name to show in backtests")

    commands.add_parser("collect", help="record live prices every 5 minutes (run this on the Pi)")

    args = parser.parse_args(argv)
    folders = lab.Folders(args.data)
    if args.command == "serve":
        from .server import serve
        return serve(folders, args.port, open_browser=not args.no_browser)
    store = Store(folders.db)
    try:
        if args.command == "fetch":
            failed = backfill(store, args.start, args.end, comed.Fetcher(), progress=Progress())
            if failed:
                print(f"{len(failed)} hourly tables could not be read: {', '.join(failed)}")
        elif args.command == "check":
            print(check(store, args.start, args.end))
        elif args.command == "backtest":
            run_backtest(store, folders, args)
        elif args.command == "strategies":
            group = None
            for choice in lab.strategy_choices(folders):
                if choice.group != group:
                    group = choice.group
                    print(f"\n{group}" + (f": {lab.GROUPS[group]}" if group in lab.GROUPS else ""))
                print(f"  {choice.id:<22}{choice.label}" + (f". {choice.description}" if choice.description else ""))
        elif args.command == "train":
            train(store, folders, args)
        else:
            collect(store, comed.Fetcher())
    except (ValueError, comed.ComEdError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130
    finally:
        store.close()
    return 0


class Progress:
    """Progress on stderr: one line per step, a counter rewritten in place on a terminal."""

    def __init__(self):
        self.message, self.shown = None, -1

    def __call__(self, done: int, total: int, message: str) -> None:
        tty = sys.stderr.isatty()
        if message != self.message:
            if self.message is not None and tty and self.shown >= 0:
                print(file=sys.stderr)
            self.message, self.shown = message, -1
            if not total:
                print(message, file=sys.stderr)
        if total:
            percent = 100 * done // total
            if tty:
                print(f"\r{message}: {percent}%", end="" if done < total else "\n", file=sys.stderr, flush=True)
            elif percent // 10 != self.shown // 10:
                print(f"{message}: {percent}%", file=sys.stderr)
            self.shown = percent


def run_backtest(store: Store, folders: lab.Folders, args) -> None:
    choices = lab.strategy_choices(folders)
    strategies = (args.strategies.split(",") if args.strategies
                  else lab.eligible(choices, args.start, args.calibrate))
    config = lab.RunConfig.parse({"start": args.start.isoformat(), "end": args.end.isoformat(),
                                  "calibrate_days": args.calibrate, "tariffs": list(lab.TARIFFS),
                                  "strategies": strategies,
                                  "profiles": [lab.profile_json(p) for p in backtest.PROFILES]}, choices)
    result = lab.run_backtest(store, config, folders, progress=Progress())
    print_run(result)
    if args.csv:
        args.csv.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(lab.run_csv(folders, result["id"]), args.csv)
        print(f"\nPer-night results: {args.csv}")
    print(f"\nSaved as run {result['id']} in {folders.runs}. Browse it with: python -m sparc serve")


def print_run(result: dict) -> None:
    if result["decay"]:
        decay = result["decay"]
        print("Share of a 5-minute price's gap from day-ahead still present after "
              + ", ".join(f"{5 * k} min: {decay[k - 1]:.2f}" for k in (1, 3, 6, 12, 24)))
    for profile in result["profiles"]:
        print(f"{profile['name']}: {profile['description']}")
    print("Savings are against charging on plug-in, as if every night of the year looked like these. "
          "\"of max\" is the share of perfect hindsight's savings. \"risk kWh/yr\" is energy charged "
          "on summer weekday afternoons, when the capacity charge is set.")
    summaries = [backtest.Summary(**s) for s in result["summaries"]]
    print(backtest.format_table(summaries, result["strategies"]))
    print("\nCheapest strategy (\"tied\": within two standard errors, comparing night by night):")
    for w in result["winners"]:
        tied = f"; tied: {', '.join(w['tied'])}" if w["tied"] else ""
        print(f"  {w['profile']} · {w['tariff']}: {w['best']}, ${w['cost']:.2f}/night{tied}")
    if result["forecasts"]:
        print("\nForecast error on nights each model never saw (cents/kWh; day-ahead in brackets):")
        for label, forecast in result["forecasts"].items():
            test = forecast.get("test") or {}
            if test.get("samples"):
                print(f"  {label}: MAE {test['mae']:.2f} ({test['day_ahead_mae']:.2f}), "
                      f"RMSE {test['rmse']:.2f} ({test['day_ahead_rmse']:.2f})")
    if result["skipped"]:
        print("\nNights skipped for missing prices: "
              + ", ".join(f"{name} {count}" for name, count in result["skipped"].items()))


def train(store: Store, folders: lab.Folders, args) -> None:
    config = lab.TrainConfig.parse({"kind": args.kind, "train_start": args.start.isoformat(),
                                    "train_end": args.end.isoformat(),
                                    "test_start": args.test[0].isoformat() if args.test else None,
                                    "test_end": args.test[1].isoformat() if args.test else None,
                                    "name": args.name})
    model = lab.train_model(store, config, folders, progress=Progress())
    for period in ("train", "test"):
        scores = model["metrics"].get(period)
        if scores and scores.get("samples"):
            print(f"{period}: {scores['samples']:,} hourly forecasts, MAE {scores['mae']:.3f} cents "
                  f"(day-ahead {scores['day_ahead_mae']:.3f}), RMSE {scores['rmse']:.3f} "
                  f"(day-ahead {scores['day_ahead_rmse']:.3f})")
    print(f"Saved {model['name']!r} as {model['id']}. Backtest it on nights from {model['usable_from']} "
          f"with --strategies model:{model['id']}")


if __name__ == "__main__":
    sys.exit(main())
