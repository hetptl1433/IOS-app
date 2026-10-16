import math
import random
import tempfile
import unittest
from datetime import date, time, timedelta
from pathlib import Path

from sparc.backtest import Profile, simulate
from sparc.clock import DAY, HOUR, SLOT, at, local
from sparc.forecast import (FEATURES, Forecaster, Moments, Recent, Ridge, features, lightgbm_available,
                            samples, train, walk_forward)
from sparc.market import Market, day_ahead_release, latest_visible_interval
from sparc.strategies import DayAhead, Forecasted
from sparc.tariff import STANDARD

FIRST = date(2026, 1, 5)


def persistent_gap_market(days=60, seed=1):
    """Each night (noon to noon) real-time prices sit a random amount off day-ahead."""
    rng = random.Random(seed)
    start = at(FIRST, time(0)) - DAY
    five, ahead = {}, {}
    level = 0.0
    for hour in range(start, start + (days + 2) * DAY, HOUR):
        wall = local(hour)
        if wall.hour == 12:
            level = rng.gauss(0, 3)
        ahead[hour] = round(3 + 2 * math.sin(2 * math.pi * wall.hour / 24), 1)
        for i in range(12):
            five[hour + i * SLOT] = round(ahead[hour] + level + rng.gauss(0, 0.5), 1)
    return Market(five, ahead)


class FeatureTests(unittest.TestCase):
    def test_features_use_only_what_was_known(self):
        market = persistent_gap_market(days=10)
        now = at(FIRST + timedelta(days=3), time(19, 5))
        target = at(FIRST + timedelta(days=4), time(2))
        before = features(market, target, now, Recent.seen_by(market, now))
        latest = latest_visible_interval(now)
        five = {t: (p if t <= latest else 999.0) for t, p in market.five_minute.items()}
        ahead = {h: (p if day_ahead_release(h) <= now else -999.0) for h, p in market.day_ahead.items()}
        tampered = Market(five, ahead)
        self.assertEqual(before, features(tampered, target, now, Recent.seen_by(tampered, now)))
        self.assertEqual(len(before), len(FEATURES))

    def test_unreleased_day_ahead_is_flagged(self):
        market = persistent_gap_market(days=10)
        target = at(FIRST + timedelta(days=4), time(2))
        afternoon = at(FIRST + timedelta(days=3), time(15))
        evening = at(FIRST + timedelta(days=3), time(18))
        recent = Recent.seen_by(market, afternoon)
        self.assertEqual(features(market, target, afternoon, recent)[1], 0.0)
        self.assertEqual(features(market, target, evening, recent)[1], 1.0)
        self.assertEqual(features(market, target, afternoon, recent)[0], market.day_ahead[target - DAY])


class MomentTests(unittest.TestCase):
    def test_sums_give_the_standardized_system_and_its_error(self):
        rng = random.Random(2)
        k = len(FEATURES)
        rows = [[rng.gauss(3, 2) for _ in range(k - 1)] + [1.0] for _ in range(300)]  # last never varies
        targets = [rng.gauss(0, 1) for _ in rows]
        whole = Moments.of(rows, targets)
        parts = Moments.total([Moments.of(rows[:120], targets[:120]), Moments.of(rows[120:], targets[120:])])
        means, scales = whole.scaling()
        self.assertEqual(scales[-1], 1.0)
        n = len(rows)
        for j in range(k - 1):
            mean = sum(r[j] for r in rows) / n
            self.assertAlmostEqual(means[j], mean, places=12)
            self.assertAlmostEqual(scales[j], math.sqrt(sum((r[j] - mean) ** 2 for r in rows) / n), places=10)
        design = [[1.0] + [(v - m) / s for v, m, s in zip(r, means, scales)] for r in rows]
        for sums in (whole, parts):
            gram, rhs = sums.system(means, scales)
            for i in range(k + 1):
                self.assertAlmostEqual(rhs[i], sum(x[i] * y for x, y in zip(design, targets)), places=8)
                for j in range(k + 1):
                    self.assertAlmostEqual(gram[i][j], sum(x[i] * x[j] for x in design), places=8)
        model = Ridge(means, scales, [rng.gauss(0, 1) for _ in range(k)], 0.3)
        errors = [p - y for p, y in zip(model.predict_many(rows), targets)]
        self.assertAlmostEqual(whole.rmse(model), math.sqrt(sum(e * e for e in errors) / n), places=9)


class TrainingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.market = persistent_gap_market()
        cls.train_nights = (FIRST, FIRST + timedelta(days=39))
        cls.test_nights = (FIRST + timedelta(days=42), FIRST + timedelta(days=55))

    def test_ridge_learns_a_persistent_gap(self):
        model = train(self.market, "ridge", *self.train_nights, test=self.test_nights)
        scores = model.metrics["test"]
        self.assertGreater(scores["samples"], 1000)
        self.assertLess(scores["mae"], 0.6 * scores["day_ahead_mae"])
        self.assertEqual(model.usable_from, FIRST + timedelta(days=41))

    def test_test_period_must_not_overlap_training(self):
        with self.assertRaisesRegex(ValueError, "two days"):
            train(self.market, "ridge", *self.train_nights, test=(FIRST + timedelta(days=40), FIRST + timedelta(days=50)))
        with self.assertRaisesRegex(ValueError, "examples"):
            train(self.market, "ridge", FIRST, FIRST + timedelta(days=2))

    def test_a_model_refuses_nights_it_learned_from(self):
        model = train(self.market, "ridge", *self.train_nights)
        with self.assertRaisesRegex(ValueError, "only be tested on nights from"):
            model.for_night(self.train_nights[1] + timedelta(days=1))
        self.assertIs(model.for_night(model.usable_from), model)
        session = Profile("p", time(18), time(7), 15, 7.7).session(self.train_nights[1])
        with self.assertRaises(ValueError):
            simulate(session, Forecasted(model), self.market, STANDARD)

    def test_walk_forward_models_never_see_the_nights_they_forecast(self):
        nights = [FIRST + timedelta(days=d) for d in range(40, 58)]  # 2026-02-14 to 2026-03-03
        walk = walk_forward(self.market, "ridge", FIRST, nights)
        self.assertEqual([m.test for m in walk.models],
                         [(nights[0], date(2026, 2, 28)), (date(2026, 3, 1), nights[-1])])
        for model in walk.models:
            self.assertEqual(model.train, (FIRST, model.test[0] - timedelta(days=2)))
            self.assertEqual(model.usable_from, model.test[0])
            self.assertGreater(model.metrics["test"]["samples"], 0)
        self.assertIs(walk.for_night(date(2026, 2, 28)), walk.models[0])
        self.assertIs(walk.for_night(nights[-1]), walk.models[1])
        with self.assertRaises(ValueError):
            walk.for_night(nights[0] - timedelta(days=1))
        summary = walk.describe()
        self.assertEqual(summary["test"]["samples"], sum(m.metrics["test"]["samples"] for m in walk.models))
        self.assertLess(summary["test"]["mae"], summary["test"]["day_ahead_mae"])

    def test_saved_model_predicts_the_same(self):
        model = train(self.market, "ridge", *self.train_nights)
        rows = samples(self.market, *self.test_nights).rows[:50]
        with tempfile.TemporaryDirectory() as folder:
            loaded = Forecaster.load(model.save(Path(folder)))
        self.assertEqual(loaded.describe()["train"], model.describe()["train"])
        for a, b in zip(model.predict_many(rows), loaded.predict_many(rows)):
            self.assertAlmostEqual(a, b, places=9)

    @unittest.skipUnless(lightgbm_available(), "LightGBM not installed")
    def test_lightgbm_learns_a_persistent_gap(self):
        model = train(self.market, "lightgbm", *self.train_nights, test=self.test_nights)
        scores = model.metrics["test"]
        self.assertLess(scores["mae"], 0.7 * scores["day_ahead_mae"])
        with tempfile.TemporaryDirectory() as folder:
            loaded = Forecaster.load(model.save(Path(folder)))
        rows = samples(self.market, *self.test_nights).rows[:20]
        self.assertEqual(model.predict_many(rows), loaded.predict_many(rows))

    def test_forecast_plan_finds_hours_that_day_ahead_misprices(self):
        # Real-time prices from 2 to 5 a.m. always settle 3 cents under day-ahead.
        # A model that learns this should plan cheaper nights than day-ahead does.
        rng = random.Random(4)
        start = at(FIRST, time(0)) - DAY
        five, ahead = {}, {}
        for hour in range(start, start + 62 * DAY, HOUR):
            wall = local(hour).hour
            ahead[hour] = round(3 + math.sin(2 * math.pi * wall / 24), 1)
            bias = -3.0 if 2 <= wall < 5 else 0.0
            for i in range(12):
                five[hour + i * SLOT] = round(ahead[hour] + bias + rng.gauss(0, 0.5), 1)
        market = Market(five, ahead)
        model = train(market, "ridge", *self.train_nights)
        profile = Profile("p", time(18), time(7), 15, 7.7)
        planned = forecasted = 0.0
        for offset in range(42, 56):
            session = profile.session(FIRST + timedelta(days=offset))
            a = simulate(session, DayAhead(), market, STANDARD)
            b = simulate(session, Forecasted(model), market, STANDARD)
            self.assertTrue(a.ready and b.ready)
            planned, forecasted = planned + a.cost, forecasted + b.cost
        self.assertLess(forecasted, 0.9 * planned)


if __name__ == "__main__":
    unittest.main()
