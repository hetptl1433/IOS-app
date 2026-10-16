"""Backtest runs and model training as the web app and command line use them."""
import csv
import json
import shutil
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

from fakes import FakeComEd
from sparc import lab
from sparc.backtest import PROFILES, Cancelled, Night, Outcome
from sparc.history import backfill, backfill_weather
from sparc.store import Store


class LabTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.source.cleanup)
        store = Store(Path(cls.source.name) / "sparc.db")
        # January and February 2026: no clock change.
        backfill(store, date(2026, 1, 1), date(2026, 2, 28), FakeComEd(), log=lambda message: None,
                 today=date(2026, 3, 15))
        backfill_weather(store, date(2026, 1, 1), date(2026, 2, 28), FakeComEd(), today=date(2026, 3, 15))
        store.close()

    def setUp(self):
        root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        shutil.copyfile(Path(self.source.name) / "sparc.db", root / "sparc.db")
        self.folders = lab.Folders(root)
        self.store = Store(self.folders.db)
        self.addCleanup(self.store.close)

    def settings(self, **changes):
        data = {"start": "2026-02-05", "end": "2026-02-12", "calibrate_days": 30,
                "tariffs": ["standard", "time-of-day"],
                "strategies": ["walk:ridge", "day-ahead", "cap-3", "timer-2300", "live"],
                "profiles": [lab.profile_json(p) for p in PROFILES[:2]]}
        data.update(changes)
        return data

    def parse(self, **changes):
        return lab.RunConfig.parse(self.settings(**changes), lab.strategy_choices(self.folders))

    def test_a_run_is_saved_listed_and_deleted(self):
        messages = []
        result = lab.run_backtest(self.store, self.parse(), self.folders,
                                  progress=lambda done, total, message: messages.append(message))
        self.assertEqual(result["strategies"], ["Charge on plug-in", "Timer 23:00", "Below 3¢", "Day-ahead plan",
                                                "Day-ahead + live prices", "Forecast: Ridge, refit monthly",
                                                "Perfect hindsight"])
        self.assertEqual(result["groups"]["Below 3¢"], "Price cap")
        self.assertEqual(result["nights"], {"Commuter": 8, "Big day": 8})
        self.assertEqual(result["skipped"], {})
        self.assertEqual(len(result["winners"]), 4)
        self.assertEqual(len(result["summaries"]), 2 * 2 * 7)
        self.assertIn("Simulating nights", messages)
        (month,) = result["forecasts"]["Forecast: Ridge, refit monthly"]["months"]
        self.assertEqual(month["train"], ["2026-01-06", "2026-02-03"])  # 30 days before, and two days clear

        (listed,) = lab.list_runs(self.folders)
        self.assertEqual(listed["id"], result["id"])
        self.assertEqual(lab.load_run(self.folders, result["id"]), json.loads(json.dumps(result, default=str)))
        with lab.run_csv(self.folders, result["id"]).open() as handle:
            self.assertEqual(len(list(csv.reader(handle))), 1 + 8 * 2 * 2 * 7)
        lab.delete_run(self.folders, result["id"])
        self.assertEqual(lab.list_runs(self.folders), [])
        for ident in (result["id"], "../sparc", ""):
            with self.assertRaises(LookupError):
                lab.load_run(self.folders, ident)
            with self.assertRaises(LookupError):
                lab.run_csv(self.folders, ident)

    def test_a_saved_model_is_only_tested_on_nights_it_never_saw(self):
        settings = {"kind": "ridge", "train_start": "2026-01-02", "train_end": "2026-02-03", "name": "January"}
        model = lab.train_model(self.store, lab.TrainConfig.parse(settings), self.folders)
        self.assertEqual(model["usable_from"], "2026-02-05")
        ident = f"model:{model['id']}"
        with self.assertRaisesRegex(ValueError, "Start on 2026-02-05"):
            self.parse(start="2026-02-04", strategies=[ident])
        result = lab.run_backtest(self.store, self.parse(strategies=[ident]), self.folders)
        self.assertIn("Forecast: January", result["strategies"])
        self.assertEqual(result["forecasts"]["Forecast: January"]["train"], ["2026-01-02", "2026-02-03"])
        defaults = lab.default_config(self.store, self.folders)
        self.assertNotIn(ident, defaults["strategies"])  # the default run starts before it is usable
        with self.assertRaisesRegex(ValueError, "already called"):
            lab.train_model(self.store, lab.TrainConfig.parse(settings), self.folders)
        lab.delete_model(self.folders, model["id"])
        self.assertEqual(lab.list_models(self.folders), [])
        with self.assertRaises(LookupError):
            lab.delete_model(self.folders, model["id"])

    def test_a_default_model_name_is_numbered_rather_than_refused(self):
        settings = lab.TrainConfig.parse({"kind": "ridge", "train_start": "2026-01-02", "train_end": "2026-01-31"})
        names = [lab.train_model(self.store, settings, self.folders)["name"] for _ in range(2)]
        self.assertEqual(names, ["Ridge, trained Jan 2026–Jan 2026", "Ridge, trained Jan 2026–Jan 2026 (2)"])

    def test_a_forecast_with_weather_runs_and_needs_weather_data(self):
        result = lab.run_backtest(self.store, self.parse(strategies=["walk:ridge+weather"]), self.folders)
        (month,) = result["forecasts"]["Forecast: Ridge + weather, refit monthly"]["months"]
        self.assertGreater(month["samples"], 0)
        def eligible():
            ready = lab.weather_ready(self.store, date(2026, 1, 6), date(2026, 2, 12))
            return lab.eligible(lab.strategy_choices(self.folders), date(2026, 2, 5), 30, ready)

        self.assertIn("walk:ridge+weather", eligible())
        with self.store.db:
            self.store.db.execute("DELETE FROM weather")
        self.assertNotIn("walk:ridge+weather", eligible())
        self.assertIn("walk:ridge", eligible())
        with self.assertRaisesRegex(ValueError, "weather forecast"):
            lab.run_backtest(self.store, self.parse(strategies=["walk:ridge+weather"]), self.folders)

    def test_a_model_trained_with_weather_is_saved_and_reloaded(self):
        settings = {"kind": "ridge", "inputs": "weather", "train_start": "2026-01-02", "train_end": "2026-02-03"}
        model = lab.train_model(self.store, lab.TrainConfig.parse(settings), self.folders)
        self.assertEqual((model["inputs"], model["name"]), ("weather", "Ridge + weather, trained Jan 2026–Feb 2026"))
        self.assertIn("temp_surprise", model["effects"])
        (choice,) = [c for c in lab.strategy_choices(self.folders) if c.id == f"model:{model['id']}"]
        self.assertTrue(choice.weather)
        lab.delete_model(self.folders, model["id"])

    def test_training_settings_are_checked(self):
        cases = [({"kind": "svm"}, "model type"),
                 ({"train_end": "2026-01-01"}, "must not end before"),
                 ({"test_start": "2026-02-04", "test_end": "2026-02-20"}, "two days after"),
                 ({"train_start": "January"}, "date like"),
                 ({"inputs": "satellites"}, "inputs")]
        for change, message in cases:
            with self.subTest(change=change), self.assertRaisesRegex(ValueError, message):
                lab.TrainConfig.parse({"kind": "ridge", "train_start": "2026-01-02", "train_end": "2026-02-03",
                                       **change})

    def test_run_settings_are_checked(self):
        commuter = lab.profile_json(PROFILES[0])
        cases = [({"end": "2026-02-01"}, "must not be before"),
                 ({"start": "2026-2-5"}, "date like"),
                 ({"strategies": ["charge-now", "hindsight"]}, "at least one strategy"),
                 ({"strategies": ["teleport"]}, "Unknown strategy"),
                 ({"strategies": "day-ahead"}, "list of names"),
                 ({"tariffs": []}, "delivery rate"),
                 ({"tariffs": ["flat"]}, "delivery rate"),
                 ({"calibrate_days": 10}, "at least 30 calibration days"),
                 ({"calibrate_days": 2.5}, "whole number"),
                 ({"calibrate_days": True}, "enter a number"),
                 ({"profiles": []}, "1 to 8"),
                 ({"profiles": [{**commuter, "arrive": "18:03"}]}, "5-minute mark"),
                 ({"profiles": [{**commuter, "power_kw": "fast"}]}, "enter a number"),
                 ({"profiles": [{**commuter, "battery_kwh": 120}]}, "plugged in for 13"),
                 ({"profiles": [{**commuter, "reserve_min": 7}]}, "multiple of 5"),
                 ({"profiles": [commuter, commuter]}, "different name")]
        for change, message in cases:
            with self.subTest(change=change), self.assertRaisesRegex(ValueError, message):
                self.parse(**change)

    def test_ties_are_within_two_standard_errors(self):
        nights = []
        for i in range(200):
            day = date(2026, 1, 1) + timedelta(days=i)
            base = 1 + (i % 7) / 3
            costs = {"A": base, "B": base + (0.05 if i % 2 else -0.04), "C": base + 0.1, "Perfect hindsight": base - 1}
            nights += [Night(day, "p", "Standard delivery", label, Outcome(supply=cost)) for label, cost in costs.items()]
        (result,) = lab.winners(nights, ["A", "B", "C", "Perfect hindsight"])
        self.assertEqual(result["best"], "A")
        self.assertEqual(result["tied"], ["B"])  # half a cent a night worse, give or take 0.3
        self.assertAlmostEqual(result["behind"]["B"]["cents_per_night"], 0.5)
        self.assertAlmostEqual(result["behind"]["C"]["cents_per_night"], 10.0)

    def test_a_cancelled_run_saves_nothing(self):
        checks = []

        def stop():
            checks.append(1)
            return len(checks) > 5

        with self.assertRaises(Cancelled):
            lab.run_backtest(self.store, self.parse(), self.folders, should_stop=stop)
        self.assertEqual(lab.list_runs(self.folders), [])

    def test_defaults_and_coverage(self):
        defaults = lab.default_config(self.store, self.folders)
        # Two months of prices are too few for 90 calibration days, so the learning strategies wait.
        self.assertEqual((defaults["start"], defaults["end"], defaults["calibrate_days"]),
                         ("2026-01-02", "2026-02-27", 0))
        self.assertNotIn("live", defaults["strategies"])
        self.assertIn("cap-2", defaults["strategies"])
        lab.RunConfig.parse(defaults, lab.strategy_choices(self.folders))
        status = lab.data_status(self.store)
        self.assertEqual((status["first"], status["last"], status["unpriced"]), ("2026-01-01", "2026-02-28", 0))
        self.assertEqual(status["months"],
                         [{"month": "2026-01", "hours": 744, "priced": 744, "day_ahead": 744, "weather": 744},
                          {"month": "2026-02", "hours": 672, "priced": 672, "day_ahead": 672, "weather": 672}])


if __name__ == "__main__":
    unittest.main()
