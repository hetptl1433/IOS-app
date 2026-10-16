"""Learned forecasts of each hour's settled real-time price.

A model predicts how far an hour's real-time price lands from its day-ahead price,
using only what was known at decision time: day-ahead prices already released,
5-minute prices already posted, and the calendar. Planning with these forecasts is
one more strategy in the backtest. It earns its place only by beating the plain
day-ahead plan on nights the model never saw.
"""
from __future__ import annotations

import importlib.util
import json
import math
import uuid
from bisect import bisect_left, bisect_right
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path

from .clock import HOUR, at, hour_start, local
from .market import Market, day_ahead_release, latest_visible_interval

FEATURES = (
    "day_ahead",  # the hour's day-ahead price, or the same hour a day earlier if not yet released
    "day_ahead_known",  # 1 once the hour's own day-ahead price is released
    "hours_ahead",  # 0 for the hour under way
    "hour_sin", "hour_cos",
    "weekend",
    "season_sin", "season_cos",
    "gap_latest",  # newest posted 5-minute price minus its hour's day-ahead price
    "gap_1h", "gap_3h", "gap_24h",  # mean of those gaps over the last 1, 3 and 24 hours
)

# Training covers the decisions one night of charging needs: every hour from 2 p.m. to
# 6 a.m., each forecasting the hours up to 7 a.m.
DECISION_HOURS = (14, 15, 16, 17, 18, 19, 20, 21, 22, 23, 0, 1, 2, 3, 4, 5, 6)
LAST_TARGET = time(7)
PENALTIES = (0.001, 0.01, 0.1, 1.0)


def lightgbm_available() -> bool:
    return importlib.util.find_spec("lightgbm") is not None


@dataclass(frozen=True)
class Recent:
    """Gaps between posted 5-minute prices and day-ahead, as seen at one moment."""

    latest: float
    hour: float
    three_hours: float
    day: float

    @classmethod
    def seen_by(cls, market: Market, now: int) -> Recent:
        # Only past hours are involved, so their day-ahead prices were released long ago.
        newest = latest_visible_interval(now)
        deviations = market.deviations
        means = []
        for intervals in (12, 36, 288):
            total, count = deviations.window(newest, intervals)
            means.append(total / count if count else 0.0)
        latest = deviations.last(newest, 288)
        return cls(0.0 if latest is None else latest, *means)


def features(market: Market, hour: int, now: int, recent: Recent) -> list[float] | None:
    day_ahead = market.known_day_ahead(hour, now)
    if day_ahead is None:
        return None
    wall = local(hour)
    hour_angle = 2 * math.pi * wall.hour / 24
    season_angle = 2 * math.pi * (wall.timetuple().tm_yday - 1) / 365.25
    return [day_ahead,
            float(hour in market.day_ahead and day_ahead_release(hour) <= now),
            (hour - hour_start(now)) / HOUR,
            math.sin(hour_angle), math.cos(hour_angle),
            float(wall.weekday() >= 5),
            math.sin(season_angle), math.cos(season_angle),
            recent.latest, recent.hour, recent.three_hours, recent.day]


@dataclass
class Samples:
    rows: list[list[float]] = field(default_factory=list)
    gaps: list[float] = field(default_factory=list)  # settled price minus the day_ahead feature
    days: list[date] = field(default_factory=list)  # the night each sample belongs to


def samples(market: Market, first: date, last: date) -> Samples:
    """Training examples for the nights first..last (plug-in dates)."""
    data = Samples()
    day = first
    while day <= last:
        end = at(day + timedelta(days=1), LAST_TARGET)
        for decision in DECISION_HOURS:
            now = at(day if decision >= 14 else day + timedelta(days=1), time(decision))
            recent = Recent.seen_by(market, now)
            for hour in range(hour_start(now), end, HOUR):
                if hour not in market.hourly:
                    continue
                row = features(market, hour, now, recent)
                if row is None:
                    continue
                data.rows.append(row)
                data.gaps.append(market.hourly[hour] - row[0])
                data.days.append(day)
        day += timedelta(days=1)
    return data


class Ridge:
    """Least squares with an L2 penalty on standardized features."""

    kind = "ridge"

    def __init__(self, means: list[float], scales: list[float], weights: list[float], bias: float):
        self.means, self.scales, self.weights, self.bias = means, scales, weights, bias

    def predict_many(self, rows: list[list[float]]) -> list[float]:
        return [self.bias + sum(w * (x - m) / s for w, x, m, s in zip(self.weights, row, self.means, self.scales))
                for row in rows]

    def to_json(self) -> dict:
        return {"means": self.means, "scales": self.scales, "weights": self.weights, "bias": self.bias}

    @classmethod
    def from_json(cls, data: dict) -> Ridge:
        return cls(data["means"], data["scales"], data["weights"], data["bias"])

    def effects(self) -> dict[str, float]:
        """Change in the forecast gap (cents) for a one-standard-deviation change in each feature."""
        return dict(zip(FEATURES, self.weights))


@dataclass
class Moments:
    """Sums over a set of training rows. A ridge fit needs nothing else, so the sums
    for each night are computed once and added up for whichever nights a fit uses."""

    count: int
    x: list[float]  # sum of each feature
    xx: list[list[float]]  # sum of each product of two features
    y: float  # sum of the targets
    xy: list[float]  # sum of each feature times the target
    yy: float  # sum of squared targets

    @classmethod
    def of(cls, rows: list[list[float]], targets: list[float]) -> Moments:
        k = len(FEATURES)
        x, xy, xx = [0.0] * k, [0.0] * k, [[0.0] * k for _ in range(k)]
        y = yy = 0.0
        for row, target in zip(rows, targets):
            y += target
            yy += target * target
            for i, value in enumerate(row):
                x[i] += value
                xy[i] += value * target
                products = xx[i]
                for j in range(i, k):
                    products[j] += value * row[j]
        for i in range(k):
            for j in range(i):
                xx[i][j] = xx[j][i]
        return cls(len(rows), x, xx, y, xy, yy)

    @classmethod
    def total(cls, parts: list[Moments]) -> Moments:
        k = len(FEATURES)
        x, xy, xx = [0.0] * k, [0.0] * k, [[0.0] * k for _ in range(k)]
        count, y, yy = 0, 0.0, 0.0
        for part in parts:
            count, y, yy = count + part.count, y + part.y, yy + part.yy
            for i in range(k):
                x[i] += part.x[i]
                xy[i] += part.xy[i]
                row, add = xx[i], part.xx[i]
                for j in range(k):
                    row[j] += add[j]
        return cls(count, x, xx, y, xy, yy)

    def scaling(self) -> tuple[list[float], list[float]]:
        """Mean and standard deviation of each feature (1 for a feature that never varies)."""
        means = [s / self.count for s in self.x]
        scales = []
        for i, mean in enumerate(means):
            variance = self.xx[i][i] / self.count - mean * mean
            scales.append(math.sqrt(variance) if variance > 1e-12 * (1 + mean * mean) else 1.0)
        return means, scales

    def system(self, means: list[float], scales: list[float]) -> tuple[list[list[float]], list[float]]:
        """X'X and X'y for the standardized features, with an intercept column first."""
        k, n = len(means), self.count
        gram = [[0.0] * (k + 1) for _ in range(k + 1)]
        rhs = [self.y] + [(self.xy[i] - means[i] * self.y) / scales[i] for i in range(k)]
        gram[0][0] = n
        for i in range(k):
            gram[0][i + 1] = gram[i + 1][0] = (self.x[i] - n * means[i]) / scales[i]
            for j in range(k):
                gram[i + 1][j + 1] = ((self.xx[i][j] - means[i] * self.x[j] - means[j] * self.x[i]
                                       + n * means[i] * means[j]) / (scales[i] * scales[j]))
        return gram, rhs

    def rmse(self, model: Ridge) -> float:
        """Root-mean-square error of `model` on these rows, computed from the sums alone."""
        gram, rhs = self.system(model.means, model.scales)
        w = [model.bias, *model.weights]
        fitted = sum(w[i] * gram[i][j] * w[j] for i in range(len(w)) for j in range(len(w)))
        squares = fitted - 2 * sum(a * b for a, b in zip(w, rhs)) + self.yy
        return math.sqrt(max(squares, 0.0) / self.count)


def _solve(matrix, vector):
    n = len(vector)
    rows = [list(r) + [v] for r, v in zip(matrix, vector)]
    for col in range(n):
        pivot = max(range(col, n), key=lambda r: abs(rows[r][col]))
        rows[col], rows[pivot] = rows[pivot], rows[col]
        if abs(rows[col][col]) < 1e-12:
            raise ValueError("singular training system")
        for r in range(n):
            if r != col and rows[r][col]:
                factor = rows[r][col] / rows[col][col]
                rows[r] = [a - factor * b for a, b in zip(rows[r], rows[col])]
    return [rows[i][n] / rows[i][i] for i in range(n)]


def _ridge(sums: Moments, penalty: float, means: list[float], scales: list[float]) -> Ridge:
    gram, rhs = sums.system(means, scales)
    for i in range(1, len(gram)):  # the intercept is not penalized
        gram[i][i] += penalty * sums.count
    solution = _solve(gram, rhs)
    return Ridge(means, scales, solution[1:], solution[0])


def fit_ridge(early: Moments, late: Moments) -> tuple[Ridge, dict]:
    """Choose the penalty that fits `early` and best predicts `late`, then refit on both."""
    both = Moments.total([early, late])
    means, scales = both.scaling()
    scores = {penalty: late.rmse(_ridge(early, penalty, means, scales)) for penalty in PENALTIES}
    penalty = min(scores, key=scores.get)
    return _ridge(both, penalty, means, scales), {"penalty": penalty, "validation_rmse": scores}


class Boosted:
    """LightGBM gradient-boosted trees (optional dependency)."""

    kind = "lightgbm"

    def __init__(self, text: str):
        import lightgbm

        self.text = text
        self.booster = lightgbm.Booster(model_str=text)

    def predict_many(self, rows: list[list[float]]) -> list[float]:
        import numpy

        return [float(v) for v in self.booster.predict(numpy.asarray(rows, dtype=float))] if rows else []

    def to_json(self) -> dict:
        return {"text": self.text}

    @classmethod
    def from_json(cls, data: dict) -> Boosted:
        return cls(data["text"])

    def effects(self) -> dict[str, float]:
        """Share of the total split gain each feature contributes."""
        gains = self.booster.feature_importance(importance_type="gain")
        total = float(sum(gains)) or 1.0
        return {name: float(g) / total for name, g in zip(FEATURES, gains)}


LIGHTGBM_PARAMS = {"objective": "regression", "learning_rate": 0.05, "num_leaves": 15,
                   "min_data_in_leaf": 100, "feature_fraction": 0.9, "bagging_fraction": 0.8,
                   "bagging_freq": 1, "lambda_l2": 1.0, "verbosity": -1, "seed": 7,
                   "deterministic": True, "num_threads": 1}


def fit_lightgbm(rows: list[list[float]], gaps: list[float], cut: int) -> tuple[Boosted, dict]:
    """Find the number of boosting rounds on rows[:cut] -> rows[cut:], then refit on all rows."""
    import lightgbm
    import numpy

    def dataset(first, last):
        return lightgbm.Dataset(numpy.asarray(rows[first:last], dtype=float),
                                numpy.asarray(gaps[first:last], dtype=float))

    probe = lightgbm.train(LIGHTGBM_PARAMS, dataset(0, cut), num_boost_round=1000,
                           valid_sets=[dataset(cut, len(rows))],
                           callbacks=[lightgbm.early_stopping(50, verbose=False)])
    rounds = max(50, probe.best_iteration)
    booster = lightgbm.train(LIGHTGBM_PARAMS, dataset(0, len(rows)), num_boost_round=rounds)
    return Boosted(booster.model_to_string()), {"rounds": rounds}


@dataclass
class Forecaster:
    id: str
    name: str
    kind: str
    created: str
    train: tuple[date, date]  # plug-in dates whose nights were used to fit
    test: tuple[date, date] | None
    metrics: dict
    model: Ridge | Boosted
    settings: dict = field(default_factory=dict)

    def predict_many(self, rows: list[list[float]]) -> list[float]:
        return self.model.predict_many(rows)

    @property
    def usable_from(self) -> date:
        """First plug-in date whose night shares no prices with the training data."""
        return self.train[1] + timedelta(days=2)

    def for_night(self, day: date) -> Forecaster:
        if day < self.usable_from:
            raise ValueError(f"{self.name} learned from nights through {self.train[1]}; "
                             f"it can only be tested on nights from {self.usable_from}")
        return self

    def describe(self) -> dict:
        return {"id": self.id, "name": self.name, "kind": self.kind, "created": self.created,
                "train": [d.isoformat() for d in self.train],
                "test": [d.isoformat() for d in self.test] if self.test else None,
                "usable_from": self.usable_from.isoformat(), "metrics": self.metrics,
                "settings": self.settings, "effects": self.model.effects()}

    def save(self, folder: Path) -> Path:
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / f"{self.id}.json"
        content = {**self.describe(), "features": list(FEATURES), "model": self.model.to_json()}
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(content, indent=1))
        temporary.replace(path)
        return path

    @classmethod
    def load(cls, path: Path) -> Forecaster:
        data = json.loads(path.read_text())
        if data.get("features") != list(FEATURES):
            raise ValueError(f"{path.name} was trained with different features; train it again")
        model = (Ridge if data["kind"] == "ridge" else Boosted).from_json(data["model"])
        test = tuple(date.fromisoformat(d) for d in data["test"]) if data["test"] else None
        return cls(data["id"], data["name"], data["kind"], data["created"],
                   tuple(date.fromisoformat(d) for d in data["train"]), test,
                   data["metrics"], model, data.get("settings", {}))


def evaluate(model, data: Samples) -> dict:
    """Forecast error on settled hourly prices, next to day-ahead's own error."""
    if not data.rows:
        return {"samples": 0}
    return _errors(model.predict_many(data.rows), data.gaps)


def _errors(predicted: list[float], gaps: list[float]) -> dict:
    errors = [p - g for p, g in zip(predicted, gaps)]
    n = len(errors)
    return {"samples": n,
            "mae": sum(map(abs, errors)) / n, "rmse": math.sqrt(sum(e * e for e in errors) / n),
            # the day-ahead price forecasts a zero gap
            "day_ahead_mae": sum(map(abs, gaps)) / n, "day_ahead_rmse": math.sqrt(sum(g * g for g in gaps) / n)}


KINDS = {"ridge": "Ridge", "lightgbm": "LightGBM"}
WALK_FORWARD_NAMES = {kind: f"{label}, refit monthly" for kind, label in KINDS.items()}
MIN_EXAMPLES = 1000


def _check_kind(kind: str) -> None:
    if kind not in KINDS:
        raise ValueError(f"unknown model kind {kind!r}")
    if kind == "lightgbm" and not lightgbm_available():
        raise ValueError("LightGBM is not installed: pip install lightgbm")


def _fit(kind: str, data: Samples, end: int, nightly: list[Moments] | None = None) -> tuple[Ridge | Boosted, dict]:
    """Fit on data.rows[:end], choosing settings on its last fifth of nights."""
    if end < MIN_EXAMPLES:
        raise ValueError(f"only {end} training examples; use a longer training period with prices")
    days = data.days[:end]
    nights = sorted(set(days))
    cut = bisect_left(days, nights[int(len(nights) * 0.8)])
    if kind == "lightgbm":
        return fit_lightgbm(data.rows[:end], data.gaps[:end], cut)
    if nightly is None:
        nightly = _nightly(data)
    early = Moments.total([m for day, m in nightly if day < days[cut]])
    late = Moments.total([m for day, m in nightly if days[cut] <= day <= days[-1]])
    return fit_ridge(early, late)


def _nightly(data: Samples) -> list[tuple[date, Moments]]:
    """Moments of each night's examples, in night order."""
    result = []
    start = 0
    while start < len(data.days):
        day = data.days[start]
        end = bisect_right(data.days, day)
        result.append((day, Moments.of(data.rows[start:end], data.gaps[start:end])))
        start = end
    return result


def train(market: Market, kind: str, first: date, last: date, test: tuple[date, date] | None = None,
          name: str | None = None, progress=None) -> Forecaster:
    """Fit on nights first..last; choose settings on the last fifth of them; score on `test`."""
    _check_kind(kind)
    if test and test[0] <= last + timedelta(days=1):
        raise ValueError("the test period must start at least two days after training ends")
    report = progress or (lambda message: None)
    report("Building training examples")
    data = samples(market, first, last)
    report(f"Fitting {KINDS[kind]} on {len(data.rows):,} examples")
    model, settings = _fit(kind, data, len(data.rows))
    metrics = {"train": evaluate(model, data)}
    if test:
        report("Scoring the test period")
        metrics["test"] = evaluate(model, samples(market, *test))
    created = datetime.now(timezone.utc).isoformat(timespec="seconds")
    ident = f"{kind}-{first:%Y%m%d}-{last:%Y%m%d}-{uuid.uuid4().hex[:6]}"
    label = name or f"{KINDS[kind]}, trained {first:%b %Y}–{last:%b %Y}"
    return Forecaster(ident, label, kind, created, (first, last), test, metrics, model, settings)


@dataclass
class WalkForward:
    """A model refitted for every month on all the nights before it.

    Each month's model learns from nights `first` through two days before the
    month's first night, so every night is forecast by a model that never saw it.
    """

    kind: str
    name: str
    models: list[Forecaster]  # in order of the month they forecast

    def for_night(self, day: date) -> Forecaster:
        index = bisect_right([m.usable_from for m in self.models], day) - 1
        if index < 0:
            raise ValueError(f"{self.name}: no model was fitted before {day}")
        return self.models[index]

    def describe(self) -> dict:
        tested = [m.metrics["test"] for m in self.models if m.metrics.get("test", {}).get("samples")]
        n = sum(t["samples"] for t in tested)
        overall = {"samples": n}
        if n:
            for key in ("mae", "day_ahead_mae"):
                overall[key] = sum(t[key] * t["samples"] for t in tested) / n
            for key in ("rmse", "day_ahead_rmse"):
                overall[key] = math.sqrt(sum(t[key] ** 2 * t["samples"] for t in tested) / n)
        return {"kind": self.kind, "name": self.name, "test": overall,
                "months": [{"month": f"{m.test[0]:%Y-%m}", "train": [d.isoformat() for d in m.train],
                            **m.metrics.get("test", {}), "settings": m.settings} for m in self.models]}


def walk_forward(market: Market, kind: str, first: date, nights: list[date], data: Samples | None = None,
                 progress=None) -> WalkForward:
    """Refit a `kind` model for each calendar month of `nights` (plug-in dates).

    `data`, if given, must be `samples(market, first, last night)`.
    """
    _check_kind(kind)
    report = progress or (lambda message: None)
    nights = sorted(nights)
    months: list[list[date]] = []
    for day in nights:
        if months and (day.year, day.month) == (months[-1][0].year, months[-1][0].month):
            months[-1].append(day)
        else:
            months.append([day])
    if months[0][0] - timedelta(days=2) < first:
        raise ValueError("the first night must be at least two days after the first training night")
    if data is None:
        report("building training examples")
        data = samples(market, first, nights[-1])
    nightly = _nightly(data) if kind == "ridge" else None
    created = datetime.now(timezone.utc).isoformat(timespec="seconds")
    models = []
    for number, month in enumerate(months, 1):
        last = month[0] - timedelta(days=2)
        report(f"Fitting {KINDS[kind]} for {month[0]:%B %Y} ({number} of {len(months)})")
        end = bisect_right(data.days, last)
        try:
            model, settings = _fit(kind, data, end, nightly)
        except ValueError as exc:
            raise ValueError(f"{KINDS[kind]} for {month[0]:%B %Y}: {exc}; use more calibration days") from None
        start, stop = bisect_left(data.days, month[0]), bisect_right(data.days, month[-1])
        tested = Samples(data.rows[start:stop], data.gaps[start:stop], data.days[start:stop])
        models.append(Forecaster(f"{kind}-{first:%Y%m%d}-{last:%Y%m%d}-monthly", WALK_FORWARD_NAMES[kind],
                                 kind, created, (first, last), (month[0], month[-1]),
                                 {"test": evaluate(model, tested)}, model, settings))
    return WalkForward(kind, WALK_FORWARD_NAMES[kind], models)


def load_all(folder: Path) -> list[Forecaster]:
    models = []
    for path in sorted(folder.glob("*.json")):
        try:
            models.append(Forecaster.load(path))
        except (ValueError, KeyError, ImportError, OSError):
            continue  # a model saved by an older version, or LightGBM since uninstalled
    return models
