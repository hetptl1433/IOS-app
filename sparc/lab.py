"""Backtest runs and model training, shared by the command line and the web app.

Everything lives in one data folder (runtime/ by default): the price database,
saved backtest runs and trained models. None of it belongs in git, because
ComEd's terms do not allow redistributing its prices.
"""
from __future__ import annotations

import json
import math
import re
import time as _time
import uuid
from collections import defaultdict
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path

from . import backtest
from .backtest import PROFILES, Cancelled, Night, Profile
from .clock import DAY, at
from .forecast import WALK_FORWARD_NAMES, Forecaster, lightgbm_available, load_all, samples, walk_forward
from .forecast import train as train_forecaster
from .history import check, stored_range
from .market import Market
from .store import Store
from .strategies import ChargeNow, DayAhead, Forecasted, Hindsight, Live, Threshold, Timer, fit_decay
from .tariff import STANDARD, TIME_OF_DAY

TARIFFS = {"standard": STANDARD, "time-of-day": TIME_OF_DAY}
TIMER_STARTS = (time(21), time(22), time(23), time(0), time(1))
PRICE_CAPS = (2, 3, 5)  # cents/kWh; overnight prices in 2025-2026 had a median of 2.5
WALK_FORWARD = {"walk:ridge": ("ridge", "prices"), "walk:ridge+weather": ("ridge", "weather"),
                "walk:lightgbm": ("lightgbm", "prices"), "walk:lightgbm+weather": ("lightgbm", "weather")}
WEATHER_COVERAGE = 0.95  # share of hours a weather forecast needs, or it refuses to run
MIN_CALIBRATION = 30  # days; for strategies that learn from the period before the first night
MAX_PROFILES = 8
MAX_NIGHTS = 1500
Progress = Callable[[int, int, str], None]  # (done, total, message); total 0 when unknown


@dataclass(frozen=True)
class Folders:
    root: Path

    @property
    def db(self) -> Path:
        return self.root / "sparc.db"

    @property
    def runs(self) -> Path:
        return self.root / "runs"

    @property
    def models(self) -> Path:
        return self.root / "models"


GROUPS = {  # shared description of each family of strategies
    "Timer": "Start at a fixed time, or right away when plugged in up to six hours after it, as a Tesla's "
             "scheduled charging does. Start earlier if needed to finish in time.",
    "Price cap": "Charge while the newest posted 5-minute price is at or under the cap, and pause above it. "
                 "Start regardless when waiting longer would not leave time to finish. Ignores the delivery "
                 "rate and capacity-risk hours.",
}


@dataclass(frozen=True)
class Choice:
    id: str
    label: str
    group: str
    description: str
    usable_from: str | None = None  # saved models: the first night they can be tested on
    weather: bool = False  # needs weather forecasts for the whole period


def strategy_choices(folders: Folders) -> list[Choice]:
    choices = [Choice("charge-now", ChargeNow.label, "Baseline",
                      "Charges as soon as the car is plugged in. Savings are measured against it.")]
    for start in TIMER_STARTS:
        choices.append(Choice(f"timer-{start:%H%M}", f"Timer {start:%H:%M}", "Timer", ""))
    for cents in PRICE_CAPS:
        choices.append(Choice(f"cap-{cents}", Threshold(cents).label, "Price cap", ""))
    choices.append(Choice("day-ahead", DayAhead.label, "Plan",
                          "Charges in the hours with the lowest day-ahead price, and replans when "
                          "the next day's prices come out."))
    choices.append(Choice("live", Live.label, "Plan",
                          "The day-ahead plan, revised every 5 minutes as real-time prices are posted."))
    method = {"ridge": "a ridge-regression", "lightgbm": "a gradient-boosted-trees (LightGBM)"}
    source = {"prices": "from prices and the calendar",
              "weather": "from prices, the calendar, holidays and Chicago weather forecasts"}
    for ident, (kind, inputs) in WALK_FORWARD.items():
        if kind == "lightgbm" and not lightgbm_available():
            continue
        choices.append(Choice(ident, f"Forecast: {WALK_FORWARD_NAMES[kind, inputs]}", "Forecast",
                              f"Plans from {method[kind]} forecast of each hour's price {source[inputs]}, "
                              "refitted every month on all the nights before it.", weather=inputs == "weather"))
    for model in load_all(folders.models):
        choices.append(Choice(f"model:{model.id}", f"Forecast: {model.name}", "Saved model",
                              f"Trained on nights {_day(model.train[0])} to {_day(model.train[1])}"
                              + (", with weather." if model.inputs == "weather" else "."),
                              model.usable_from.isoformat(), weather=model.inputs == "weather"))
    choices.append(Choice("hindsight", Hindsight.label, "Reference",
                          "Knows every hour's final price in advance and, like the planners, stays out "
                          "of capacity-risk hours: the lowest cost without risking the capacity charge. "
                          "Always included."))
    return choices


@dataclass
class RunConfig:
    start: date  # first plug-in date
    end: date  # last plug-in date
    profiles: list[Profile]
    tariffs: list[str]
    strategies: list[str]  # choice ids, in catalogue order, without the baseline and hindsight
    calibrate_days: int = 90

    def to_json(self) -> dict:
        return {"start": self.start.isoformat(), "end": self.end.isoformat(),
                "calibrate_days": self.calibrate_days, "tariffs": self.tariffs,
                "strategies": self.strategies, "profiles": [profile_json(p) for p in self.profiles]}

    @classmethod
    def parse(cls, data: dict, choices: list[Choice]) -> RunConfig:
        if not isinstance(data, dict):
            raise ValueError("Send the run settings as an object.")
        start, end = parse_date(data.get("start"), "First night"), parse_date(data.get("end"), "Last night")
        if end < start:
            raise ValueError("The last night must not be before the first.")
        if (end - start).days + 1 > MAX_NIGHTS:
            raise ValueError(f"Choose at most {MAX_NIGHTS} nights.")
        calibrate = _number(data.get("calibrate_days", 90), "Calibration days", 0, 730, whole=True)
        tariffs = _strings(data.get("tariffs"), "tariffs")
        if not tariffs or any(t not in TARIFFS for t in tariffs):
            raise ValueError("Choose at least one delivery rate: standard or time-of-day.")
        catalogue = {c.id: i for i, c in enumerate(choices)}
        strategies = _strings(data.get("strategies"), "strategies")
        unknown = [s for s in strategies if s not in catalogue]
        if unknown:
            raise ValueError(f"Unknown strategy: {unknown[0]}")
        strategies = sorted((s for s in strategies if s not in ("charge-now", "hindsight")), key=catalogue.get)
        if not strategies:
            raise ValueError("Choose at least one strategy to compare with charging on plug-in.")
        learning = [s for s in strategies if s == "live" or s in WALK_FORWARD]
        if learning and calibrate < MIN_CALIBRATION:
            label = next(c.label for c in choices if c.id == learning[0])
            raise ValueError(f"{label} learns from the days before the first night: "
                             f"use at least {MIN_CALIBRATION} calibration days.")
        for choice in choices:
            if choice.id in strategies and choice.usable_from and start < date.fromisoformat(choice.usable_from):
                raise ValueError(f"{choice.label} was trained on nights up to two days before "
                                 f"{choice.usable_from}. Start on {choice.usable_from} or later, so it is "
                                 f"only tested on nights it never saw.")
        raw_profiles = data.get("profiles")
        if not isinstance(raw_profiles, list) or not 1 <= len(raw_profiles) <= MAX_PROFILES:
            raise ValueError(f"Use 1 to {MAX_PROFILES} driver profiles.")
        profiles = [parse_profile(p) for p in raw_profiles]
        if len({p.name for p in profiles}) != len(profiles):
            raise ValueError("Give each driver profile a different name.")
        return cls(start, end, profiles, tariffs, strategies, calibrate)


def profile_json(profile: Profile) -> dict:
    return {"name": profile.name, "arrive": f"{profile.arrive:%H:%M}", "depart": f"{profile.depart:%H:%M}",
            "battery_kwh": profile.battery_kwh, "power_kw": profile.power_kw,
            "efficiency": profile.efficiency, "reserve_min": profile.reserve_min}


def parse_profile(data: dict) -> Profile:
    if not isinstance(data, dict):
        raise ValueError("Each driver profile must be an object.")
    name = str(data.get("name") or "").strip()
    if not 1 <= len(name) <= 40:
        raise ValueError("Driver profile names need 1 to 40 characters.")
    arrive = _clock(data.get("arrive"), f"{name}: plug-in time")
    depart = _clock(data.get("depart"), f"{name}: leave time")
    if arrive == depart:
        raise ValueError(f"{name}: plug-in and leave times must differ.")
    profile = Profile(name, arrive, depart,
                      _number(data.get("battery_kwh"), f"{name}: energy added (kWh)", 0.5, 150),
                      _number(data.get("power_kw"), f"{name}: charging power (kW)", 1, 20),
                      _number(data.get("efficiency", 0.9), f"{name}: charging efficiency", 0.5, 1),
                      _number(data.get("reserve_min", 30), f"{name}: finish-early buffer (minutes)", 0, 180,
                              whole=True))
    if profile.reserve_min % 5:
        raise ValueError(f"{name}: the finish-early buffer must be a multiple of 5 minutes.")
    session = profile.session(date(2026, 1, 15))  # no clock change
    hours = (session.departure - session.arrival) / 3600
    if session.energy_kwh > hours * profile.power_kw:
        raise ValueError(f"{name}: {profile.battery_kwh:g} kWh at {profile.efficiency:.0%} efficiency takes "
                         f"{session.energy_kwh / profile.power_kw:.1f} hours at {profile.power_kw:g} kW, "
                         f"but the car is plugged in for {hours:g}.")
    return profile


def default_config(store: Store, folders: Folders) -> dict:
    """Every night with data, after a 90-day calibration period, and every strategy."""
    span = stored_range(store)
    calibrate = 90
    start = end = None
    if span:
        first, last = span
        end = last - timedelta(days=1)  # a night needs the next morning's prices
        start = max(first + timedelta(days=calibrate + 1), end - timedelta(days=MAX_NIGHTS - 1))
        if start > end:  # not enough history to calibrate
            calibrate, start = 0, min(first + timedelta(days=1), end)
    strategies = []
    if start:
        strategies = eligible(strategy_choices(folders), start, calibrate,
                              weather_ready(store, start - timedelta(days=calibrate), end))
    return {"start": start.isoformat() if start else None, "end": end.isoformat() if end else None,
            "calibrate_days": calibrate, "tariffs": list(TARIFFS), "strategies": strategies,
            "profiles": [profile_json(p) for p in PROFILES]}


def eligible(choices: list[Choice], start: date, calibrate_days: int, weather: bool = True) -> list[str]:
    """Every strategy that can run from `start` with this much calibration (and weather, if there is any)."""
    return [c.id for c in choices
            if c.id != "hindsight" and (calibrate_days >= MIN_CALIBRATION or not _learns(c.id))
            and not (c.usable_from and start < date.fromisoformat(c.usable_from)) and (weather or not c.weather)]


def weather_ready(store: Store, first: date, last: date) -> bool:
    """Whether weather forecasts cover the nights first..last, with the day before and the morning after."""
    start, end = at(first, time(0)) - DAY, at(last + timedelta(days=2), time(0))
    try:
        _check_weather(Market({}, {}, weather=store.weather(start, end)), start, end)
    except ValueError:
        return False
    return True


def _learns(ident: str) -> bool:
    return ident == "live" or ident in WALK_FORWARD


def run_backtest(store: Store, config: RunConfig, folders: Folders, progress: Progress | None = None,
                 should_stop: Callable[[], bool] | None = None) -> dict:
    """Simulate every night of the period and save the run under folders.runs."""
    began = _time.monotonic()
    report = _reporter(progress, should_stop)
    report(0, 0, "Loading prices")
    first_training = config.start - timedelta(days=config.calibrate_days)
    calibrate_from, first_night = at(first_training, time(0)), at(config.start, time(0))
    start, end = calibrate_from - DAY, at(config.end + timedelta(days=2), time(0))
    market = Market(store.five_minute(start, end), store.day_ahead(start, end),
                    store.published_real_time(start, end), store.weather(start, end))
    days = [config.start + timedelta(days=i) for i in range((config.end - config.start).days + 1)]
    selected = ["charge-now", *config.strategies, "hindsight"]
    saved = {m.id: m for m in load_all(folders.models)}
    if any(_inputs(ident, saved) == "weather" for ident in selected):
        _check_weather(market, calibrate_from, end)

    decay = None
    if "live" in selected:
        report(0, 0, "Fitting how long real-time price swings last")
        decay = fit_decay(market, calibrate_from, first_night)
    forecasts, walks = {}, {}
    examples = {}  # by input set
    for ident in [s for s in selected if s in WALK_FORWARD]:
        kind, inputs = WALK_FORWARD[ident]
        if inputs not in examples:
            report(0, 0, "Building forecast training examples" + (" with weather" if inputs == "weather" else ""))
            examples[inputs] = samples(market, first_training, config.end, inputs)
        walks[ident] = walk_forward(market, kind, first_training, days, data=examples[inputs], inputs=inputs,
                                    progress=lambda message: report(0, 0, message))
    strategies = []
    for ident in selected:
        strategy = _strategy(ident, decay, saved, walks, config.start)
        strategies.append(strategy)
        if isinstance(strategy, Forecasted):
            source = strategy.source
            forecasts[strategy.label] = source.describe() if ident in walks else _saved_summary(source)
    labels = [s.label for s in strategies]
    if len(set(labels)) != len(labels):
        raise ValueError("Two strategies have the same name. Rename one of the saved models.")

    nights, skipped = backtest.run(market, days, config.profiles, strategies,
                                   [TARIFFS[t] for t in config.tariffs],
                                   progress=lambda done, total: report(done, total, "Simulating nights"),
                                   should_stop=should_stop)
    if not nights:
        raise ValueError("No night in this period has complete prices. Fetch prices for it first.")
    summaries = backtest.summarize(nights, baseline=ChargeNow.label, best=Hindsight.label)
    created = datetime.now(timezone.utc)
    ident = created.strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:4]
    result = {
        "id": ident, "created": created.isoformat(timespec="seconds"),
        "config": config.to_json(), "strategies": labels,
        "groups": {s.label: g for s, g in zip(strategies, _groups(selected))},
        "profiles": [{"name": p.name, "description": p.description} for p in config.profiles],
        "tariffs": [TARIFFS[t].name for t in config.tariffs],
        "nights": _counts(nights), "skipped": skipped,
        "summaries": [asdict(s) for s in summaries],
        "winners": winners(nights, labels),
        "monthly": monthly(nights),
        "decay": decay, "forecasts": forecasts,
        "elapsed_s": round(_time.monotonic() - began, 1),
    }
    folders.runs.mkdir(parents=True, exist_ok=True)
    backtest.write_csv(nights, folders.runs / f"{ident}.csv")
    _write_json(folders.runs / f"{ident}.json", result)  # last, so a listed run is always complete
    return result


def _inputs(ident: str, saved: dict[str, Forecaster]) -> str | None:
    if ident in WALK_FORWARD:
        return WALK_FORWARD[ident][1]
    model = saved.get(ident.removeprefix("model:")) if ident.startswith("model:") else None
    return model.inputs if model else None


def _check_weather(market: Market, start: int, end: int) -> None:
    hours = (end - start) // 3600
    forecast = sum(1 for hour in range(start, end, 3600)
                   if (row := market.weather.get(hour)) is not None and row.temp_fc is not None)
    if forecast < WEATHER_COVERAGE * hours:
        raise ValueError(f"Only {forecast / hours:.0%} of the hours in this period have a weather forecast. "
                         "Fetch prices again on the Data tab: it fetches weather too.")


def _strategy(ident: str, decay, saved: dict[str, Forecaster], walks: dict, start: date):
    if ident == "charge-now":
        return ChargeNow()
    if ident == "day-ahead":
        return DayAhead()
    if ident == "live":
        return Live(decay)
    if ident == "hindsight":
        return Hindsight()
    if match := re.fullmatch(r"timer-(\d\d)(\d\d)", ident):
        return Timer(time(int(match[1]), int(match[2])))
    if match := re.fullmatch(r"cap-(\d+)", ident):
        return Threshold(int(match[1]))
    if ident in walks:
        return Forecasted(walks[ident])
    if ident.startswith("model:"):
        model = saved.get(ident.removeprefix("model:"))
        if model is None:
            raise ValueError(f"The saved model {ident.removeprefix('model:')} was not found.")
        model.for_night(start)  # refuses nights the model was trained on
        return Forecasted(model)
    raise ValueError(f"Unknown strategy: {ident}")


def _groups(selected: Sequence[str]) -> list[str]:
    def group(ident):
        if ident.startswith("timer-"):
            return "Timer"
        if ident.startswith("cap-"):
            return "Price cap"
        if ident in WALK_FORWARD:
            return "Forecast"
        if ident.startswith("model:"):
            return "Saved model"
        return {"charge-now": "Baseline", "day-ahead": "Plan", "live": "Plan", "hindsight": "Reference"}[ident]
    return [group(ident) for ident in selected]


def _saved_summary(model: Forecaster) -> dict:
    return {"kind": model.kind, "name": model.name, "train": [d.isoformat() for d in model.train],
            "test": model.metrics.get("test")}


def _counts(nights: Sequence[Night]) -> dict[str, int]:
    days = defaultdict(set)
    for n in nights:
        days[n.profile].add(n.day)
    return {profile: len(d) for profile, d in days.items()}


def winners(nights: Sequence[Night], labels: Sequence[str]) -> list[dict]:
    """The cheapest strategy for each profile and tariff, and every strategy statistically tied with it.

    A strategy is tied when its mean extra cost per night, paired night by night, is
    within two standard errors of zero.
    """
    costs: dict[tuple[str, str], dict[str, dict[date, float]]] = defaultdict(lambda: defaultdict(dict))
    for n in nights:
        costs[n.profile, n.delivery][n.strategy][n.day] = n.outcome.cost
    result = []
    for (profile, tariff), by_strategy in costs.items():
        candidates = [s for s in labels if s != Hindsight.label and s in by_strategy]
        means = {s: sum(by_strategy[s].values()) / len(by_strategy[s]) for s in candidates}
        best = min(candidates, key=means.get)
        tied, behind = [], {}
        for s in candidates:
            if s == best:
                continue
            diff = [by_strategy[s][d] - cost for d, cost in by_strategy[best].items()]
            mean = sum(diff) / len(diff)
            spread = math.sqrt(sum((x - mean) ** 2 for x in diff) / max(1, len(diff) - 1))
            error = spread / math.sqrt(len(diff))
            behind[s] = {"cents_per_night": 100 * mean, "standard_error": 100 * error}
            if mean <= 2 * error:
                tied.append(s)
        result.append({"profile": profile, "tariff": tariff, "best": best, "cost": means[best],
                       "tied": tied, "behind": behind})
    return result


def monthly(nights: Sequence[Night]) -> list[dict]:
    """Mean cost per night in each calendar month."""
    totals: dict[tuple[str, str, str, str], list[float]] = defaultdict(list)
    for n in nights:
        totals[n.profile, n.delivery, n.strategy, f"{n.day:%Y-%m}"].append(n.outcome.cost)
    return [{"profile": p, "tariff": t, "strategy": s, "month": m, "cost": sum(v) / len(v), "nights": len(v)}
            for (p, t, s, m), v in totals.items()]


@dataclass(frozen=True)
class TrainConfig:
    kind: str
    train: tuple[date, date]  # first and last plug-in dates
    test: tuple[date, date] | None
    name: str | None
    inputs: str = "prices"

    @classmethod
    def parse(cls, data: dict) -> TrainConfig:
        if not isinstance(data, dict):
            raise ValueError("Send the training settings as an object.")
        kind = data.get("kind")
        if kind not in ("ridge", "lightgbm"):
            raise ValueError("Choose a model type: ridge or lightgbm.")
        if kind == "lightgbm" and not lightgbm_available():
            raise ValueError("LightGBM is not installed. Install it with: pip install lightgbm")
        span = (parse_date(data.get("train_start"), "Training start"),
                parse_date(data.get("train_end"), "Training end"))
        if span[1] < span[0]:
            raise ValueError("Training must not end before it starts.")
        test = None
        if data.get("test_start") or data.get("test_end"):
            test = (parse_date(data.get("test_start"), "Test start"),
                    parse_date(data.get("test_end"), "Test end"))
            if test[1] < test[0]:
                raise ValueError("The test period must not end before it starts.")
            if test[0] <= span[1] + timedelta(days=1):
                raise ValueError("Start the test period at least two days after training ends, "
                                 "so no night shares prices with the training data.")
        name = " ".join(str(data.get("name") or "").split())[:60] or None
        inputs = data.get("inputs") or "prices"
        if inputs not in ("prices", "weather"):
            raise ValueError("Choose the inputs: prices, or prices with weather.")
        return cls(kind, span, test, name, inputs)


def train_model(store: Store, config: TrainConfig, folders: Folders, progress: Progress | None = None,
                should_stop: Callable[[], bool] | None = None) -> dict:
    report = _reporter(progress, should_stop)
    last = config.test[1] if config.test else config.train[1]
    start, end = at(config.train[0], time(0)) - DAY, at(last + timedelta(days=2), time(0))
    report(0, 0, "Loading prices")
    market = Market(store.five_minute(start, end), store.day_ahead(start, end),
                    store.published_real_time(start, end), store.weather(start, end))
    if config.inputs == "weather":
        _check_weather(market, start + DAY, end)
    model = train_forecaster(market, config.kind, *config.train, test=config.test, name=config.name,
                             progress=lambda message: report(0, 0, message), inputs=config.inputs)
    taken = {m.name for m in load_all(folders.models)} | set(WALK_FORWARD_NAMES.values())
    if config.name is None:  # number a default name rather than refuse it
        base, number = model.name, 2
        while model.name in taken:
            model.name, number = f"{base} ({number})", number + 1
    elif model.name in taken:
        raise ValueError(f"A model is already called {model.name!r}. Choose another name.")
    model.save(folders.models)
    return model.describe()


def list_models(folders: Folders) -> list[dict]:
    return sorted((m.describe() for m in load_all(folders.models)), key=lambda m: m["created"], reverse=True)


def list_runs(folders: Folders) -> list[dict]:
    runs = []
    for path in sorted(folders.runs.glob("*.json"), reverse=True):
        try:
            data = json.loads(path.read_text())
            runs.append({"id": data["id"], "created": data["created"], "start": data["config"]["start"],
                         "end": data["config"]["end"], "profiles": [p["name"] for p in data["profiles"]],
                         "tariffs": data["tariffs"], "strategies": len(data["strategies"]),
                         "nights": data["nights"], "winners": data["winners"]})
        except (OSError, ValueError, KeyError, TypeError):
            continue  # unreadable, or written by an older version
    return runs


def load_run(folders: Folders, ident: str) -> dict:
    return json.loads(_run_path(folders, ident, ".json").read_text())


def run_csv(folders: Folders, ident: str) -> Path:
    path = _run_path(folders, ident, ".csv")
    if not path.is_file():
        raise LookupError("No such run.")
    return path


def delete_run(folders: Folders, ident: str) -> None:
    for suffix in (".json", ".csv"):
        _run_path(folders, ident, suffix).unlink(missing_ok=True)


def delete_model(folders: Folders, ident: str) -> None:
    path = folders.models / f"{ident}.json"
    if not re.fullmatch(r"[a-z]+(-weather)?-\d{8}-\d{8}-[0-9a-f]{6}", ident) or not path.is_file():
        raise LookupError("No such model.")
    path.unlink()


def data_status(store: Store) -> dict:
    span = stored_range(store)
    if not span:
        return {"empty": True}
    coverage = check(store, *span)
    return {"empty": False, "first": span[0].isoformat(), "last": span[1].isoformat(),
            "hours": coverage.hours, "published": coverage.published, "complete": coverage.complete,
            "partial": coverage.partial, "unpriced": coverage.unpriced, "day_ahead": coverage.day_ahead,
            "revised": len(coverage.revised), "compared": coverage.compared, "weather": coverage.weather,
            "months": [asdict(m) for m in coverage.months]}


def _reporter(progress: Progress | None, should_stop: Callable[[], bool] | None) -> Progress:
    """Pass progress on, and make every report a point where the work can be cancelled."""
    def report(done: int, total: int, message: str) -> None:
        if progress:
            progress(done, total, message)
        if should_stop and should_stop():
            raise Cancelled
    return report


def _run_path(folders: Folders, ident: str, suffix: str) -> Path:
    if not re.fullmatch(r"\d{8}-\d{6}-[0-9a-f]{4}", ident):
        raise LookupError("No such run.")
    path = folders.runs / f"{ident}{suffix}"
    if suffix == ".json" and not path.is_file():
        raise LookupError("No such run.")
    return path


def _write_json(path: Path, value) -> None:
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, default=str))
    temporary.replace(path)


def _day(value: date) -> str:
    return f"{value:%b} {value.day}, {value.year}"


def _strings(value, label: str) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        raise ValueError(f"{label}: send a list of names.")
    return list(dict.fromkeys(value))


def parse_date(value, label: str) -> date:
    try:
        return date.fromisoformat(str(value))
    except (TypeError, ValueError):
        raise ValueError(f"{label}: use a date like 2025-04-01.") from None


def _clock(value, label: str) -> time:
    match = re.fullmatch(r"([01]\d|2[0-3]):([0-5]\d)", str(value or ""))
    if not match or int(match[2]) % 5:
        raise ValueError(f"{label}: use a 24-hour time on a 5-minute mark, like 18:00.")
    return time(int(match[1]), int(match[2]))


def _number(value, label: str, low: float, high: float, whole: bool = False):
    if isinstance(value, bool):
        raise ValueError(f"{label}: enter a number.")
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise ValueError(f"{label}: enter a number.") from None
    if not math.isfinite(number) or not low <= number <= high:
        raise ValueError(f"{label}: must be between {low:g} and {high:g}.")
    if whole:
        if number != int(number):
            raise ValueError(f"{label}: must be a whole number.")
        return int(number)
    return number
