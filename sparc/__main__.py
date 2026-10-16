"""python -m sparc {fetch,check,backtest,collect}"""
from __future__ import annotations

import argparse
import os
import sys
from datetime import date, time, timedelta
from pathlib import Path

from . import backtest, comed
from .clock import DAY, at
from .collector import collect
from .history import backfill, check
from .market import Market
from .store import Store
from .strategies import ChargeNow, DayAhead, Hindsight, Live, Timer, fit_decay
from .tariff import STANDARD, TIME_OF_DAY


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m sparc",
                                     description="Price-aware EV charging for ComEd Hourly Pricing.")
    parser.add_argument("--db", type=Path, default=Path(os.environ.get("SPARC_DB", "runtime/sparc.db")),
                        help="SQLite database (default: runtime/sparc.db, or $SPARC_DB)")
    commands = parser.add_subparsers(dest="command", required=True)

    fetch = commands.add_parser("fetch", help="backfill 5-minute and day-ahead prices from ComEd")
    fetch.add_argument("start", type=date.fromisoformat)
    fetch.add_argument("end", type=date.fromisoformat)

    report = commands.add_parser("check", help="data coverage, and agreement with ComEd's hourly prices")
    report.add_argument("start", type=date.fromisoformat)
    report.add_argument("end", type=date.fromisoformat)

    replay = commands.add_parser("backtest", help="replay stored prices through each strategy")
    replay.add_argument("start", type=date.fromisoformat, help="first plug-in date")
    replay.add_argument("end", type=date.fromisoformat, help="last plug-in date")
    replay.add_argument("--calibrate", type=int, default=365, metavar="DAYS",
                        help="days before START used to fit how long price deviations last (default 365)")
    replay.add_argument("--csv", type=Path, help="write every night's result to this file")

    commands.add_parser("collect", help="record live prices every 5 minutes (run this on the Pi)")

    args = parser.parse_args(argv)
    store = Store(args.db)
    try:
        if args.command == "fetch":
            failed = backfill(store, args.start, args.end, comed.Fetcher())
            if failed:
                print(f"{len(failed)} hourly tables could not be read: {', '.join(failed)}")
        elif args.command == "check":
            print(check(store, args.start, args.end))
        elif args.command == "backtest":
            run_backtest(store, args.start, args.end, args.calibrate, args.csv)
        else:
            collect(store, comed.Fetcher())
    except comed.ComEdError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130
    finally:
        store.close()
    return 0


def run_backtest(store: Store, first: date, last: date, calibrate_days: int, csv: Path | None) -> None:
    calibrate_from = at(first - timedelta(days=calibrate_days), time(0))
    start, end = calibrate_from - DAY, at(last + timedelta(days=2), time(0))
    market = Market(store.five_minute(start, end), store.day_ahead(start, end),
                    store.published_real_time(start, end))
    decay = fit_decay(market, calibrate_from, at(first, time(0)))
    print("Share of a 5-minute price's gap from day-ahead still present after "
          + ", ".join(f"{5 * k} min: {decay[k - 1]:.2f}" for k in (1, 3, 6, 12, 24)))

    strategies = [ChargeNow(), Timer(time(23)), DayAhead(), Live(decay), Hindsight()]
    days = [first + timedelta(days=i) for i in range((last - first).days + 1)]
    nights, skipped = backtest.run(market, days, backtest.PROFILES, strategies, [STANDARD, TIME_OF_DAY])
    for profile in backtest.PROFILES:
        print(f"{profile.name}: {profile.description}")
    summaries = backtest.summarize(nights, baseline=ChargeNow.label, best=Hindsight.label)
    print("Savings are against charging on plug-in, as if every night of the year looked like these. "
          "\"of max\" is the share of perfect hindsight's savings. \"risk kWh/yr\" is energy charged "
          "on summer weekday afternoons, when the capacity charge is set.")
    print(backtest.format_table(summaries, [s.label for s in strategies]))
    print(f"\n{Live.label} vs {DayAhead.label}:")
    for h in backtest.head_to_head(nights, Live.label, DayAhead.label):
        day, gain = h.best_night
        print(f"  {h.profile} · {h.delivery}: {round(100 * h.mean_gain, 1) + 0.0:+.1f} cents/night; "
              f"better by 5+ cents on {h.nights_won} of {h.nights} nights, worse on {h.nights_lost}; "
              f"best night {day} ({gain:+.2f} $)")
    if skipped:
        print("\nNights skipped for missing prices: "
              + ", ".join(f"{name} {count}" for name, count in skipped.items()))
    if csv:
        backtest.write_csv(nights, csv)
        print(f"\nPer-night results: {csv}")


if __name__ == "__main__":
    sys.exit(main())
