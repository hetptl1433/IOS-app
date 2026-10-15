import copy
from datetime import datetime, timedelta, timezone
import json
import math
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from sparc_lab.models import forecast, get_model_catalog, list_artifacts, train_model


def hourly_fixture(days=45):
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    result = []
    for index in range(24 * days):
        stamp = start + timedelta(hours=index)
        price = 6 + 4 * math.sin(2 * math.pi * stamp.hour / 24) + 0.2 * (stamp.weekday() >= 5)
        ahead = price + 0.1 * math.sin(index)
        result.append({"timestamp": stamp.isoformat(), "price": price,
                       "available_at": (stamp + timedelta(hours=1)).isoformat(),
                       "day_ahead": ahead,
                       "day_ahead_available_at": (stamp - timedelta(hours=12)).isoformat()})
    return result


class ModelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.records = hourly_fixture()

    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)

    def test_catalog_is_honest_without_artifacts(self):
        catalog = {r["id"]: r for r in get_model_catalog(self.folder.name)}
        self.assertTrue(catalog["seasonal-naive"]["available"])
        self.assertFalse(catalog["ridge"]["available"])
        self.assertFalse(catalog["kronos-mean"]["available"])

    def test_seasonal_forecast_ignores_future_actual_prices(self):
        history = self.records[:500]
        future = [{"timestamp": r["timestamp"], "price": -999999} for r in self.records[500:524]]
        expected = forecast("seasonal-naive", history, future)
        for row in future:
            row["price"] = 999999
        self.assertEqual(expected, forecast("seasonal-naive", history, future))
        self.assertAlmostEqual(expected[0], self.records[500 - 168]["price"])

    def test_negative_and_zero_dayahead_are_preserved(self):
        future = [{"timestamp": self.records[500 + i]["timestamp"], "day_ahead": price} for i, price in enumerate([-4, 0, 100])]
        self.assertEqual(forecast("day-ahead", self.records[:500], future), [-4, 0, 100])

    def test_dayahead_cannot_substitute_missing(self):
        with self.assertRaisesRegex(ValueError, "unavailable"):
            forecast("day-ahead", self.records[:500], [{"timestamp": self.records[500]["timestamp"]}])

    def test_as_of_enforces_exact_dayahead_publication(self):
        hour = datetime.fromisoformat(self.records[500]["timestamp"])
        future = [{"timestamp": hour.isoformat(), "day_ahead": -3,
                   "day_ahead_available_at": (hour + timedelta(minutes=20)).isoformat()}]
        with self.assertRaisesRegex(ValueError, "not published"):
            forecast("day-ahead", self.records[:500], future)
        with self.assertRaisesRegex(ValueError, "not published"):
            forecast("day-ahead", self.records[:500], future, as_of=(hour + timedelta(minutes=10)).isoformat())
        self.assertEqual(forecast("day-ahead", self.records[:500], future, as_of=(hour + timedelta(minutes=20)).isoformat()), [-3])

    def test_as_of_checks_history_availability_within_hour(self):
        history = copy.deepcopy(self.records[:500])
        hour = datetime.fromisoformat(self.records[500]["timestamp"])
        history[-1]["available_at"] = (hour + timedelta(minutes=5)).isoformat()
        future = [{"timestamp": hour.isoformat()}]
        with self.assertRaisesRegex(ValueError, "unavailable"):
            forecast("seasonal-naive", history, future)
        self.assertEqual(len(forecast("seasonal-naive", history, future, as_of=(hour + timedelta(minutes=10)).isoformat())), 1)

    def test_as_of_checks_training_cutoff_against_actual_issue(self):
        train_model("ridge", self.records, self.folder.name)
        path = Path(self.folder.name) / "ridge.json"
        artifact = json.loads(path.read_text())
        hour = datetime.fromisoformat(self.records[1000]["timestamp"])
        artifact["trained_until"] = (hour + timedelta(minutes=5)).isoformat()
        path.write_text(json.dumps(artifact))
        future = [{"timestamp": hour.isoformat()}]
        with self.assertRaisesRegex(ValueError, "Lookahead blocked"):
            forecast("ridge", self.records[:1000], future, artifact_dir=self.folder.name)
        self.assertEqual(len(forecast("ridge", self.records[:1000], future, artifact_dir=self.folder.name,
                                      as_of=(hour + timedelta(minutes=10)).isoformat())), 1)

    def test_future_history_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "unavailable"):
            forecast("seasonal-naive", self.records[:501], [{"timestamp": self.records[500]["timestamp"]}])

    def test_ridge_real_fit_and_metadata(self):
        artifact = train_model("ridge", self.records, self.folder.name)
        self.assertEqual(artifact["model_id"], "ridge")
        self.assertLess(artifact["metrics"]["test_mae"], 0.3)
        self.assertLess(artifact["split"]["train"]["end"], artifact["split"]["validation"]["start"])
        self.assertLess(artifact["split"]["validation"]["end"], artifact["split"]["test"]["start"])
        future = [{"timestamp": row["timestamp"], "day_ahead": row["day_ahead"]} for row in self.records[1000:1024]]
        predicted = forecast("ridge", self.records[:1000], future, artifact_dir=self.folder.name)
        self.assertEqual(len(predicted), 24)
        self.assertTrue(all(math.isfinite(value) for value in predicted))
        self.assertEqual(list_artifacts(self.folder.name)[0]["id"], artifact["id"])
        self.assertNotIn("weights", list_artifacts(self.folder.name)[0])
        self.assertTrue(next(m for m in get_model_catalog(self.folder.name) if m["id"] == "ridge")["available"])

    def test_training_artifact_cannot_look_back_into_training(self):
        train_model("ridge", self.records, self.folder.name)
        with self.assertRaisesRegex(ValueError, "Lookahead blocked"):
            forecast("ridge", self.records[:500], [{"timestamp": self.records[500]["timestamp"]}], artifact_dir=self.folder.name)

    def test_test_labels_do_not_change_weights_or_selected_alpha(self):
        train_model("ridge", self.records, self.folder.name)
        original = json.loads((Path(self.folder.name) / "ridge.json").read_text())
        changed = copy.deepcopy(self.records)
        test_start = int(len(changed) * 0.7) + (len(changed) - int(len(changed) * 0.7)) // 2
        for row in changed[test_start:]:
            row["price"] += 10000
        train_model("ridge", changed, self.folder.name)
        updated = json.loads((Path(self.folder.name) / "ridge.json").read_text())
        for field in ("weights", "means", "scales", "alpha", "trained_until"):
            self.assertEqual(original[field], updated[field], field)
        self.assertGreater(updated["metrics"]["test_mae"], 100)

    def test_unpublished_dayahead_does_not_train_on_future_information(self):
        records = copy.deepcopy(self.records)
        for row in records:
            row["day_ahead_available_at"] = "2099-01-01T00:00:00Z"
        train_model("ridge", records, self.folder.name)
        original = json.loads((Path(self.folder.name) / "ridge.json").read_text())
        for row in records:
            row["day_ahead"] = -123456
        train_model("ridge", records, self.folder.name)
        updated = json.loads((Path(self.folder.name) / "ridge.json").read_text())
        self.assertEqual(original["weights"], updated["weights"])

    def test_unknown_and_unimplemented_models_do_not_impersonate(self):
        for model in ("kronos-mean", "invented"):
            with self.assertRaises(ValueError):
                forecast(model, self.records[:500], [{"timestamp": self.records[500]["timestamp"]}])
        with patch("sparc_lab.models._installed", return_value=False):
            with self.assertRaisesRegex(ValueError, "missing"):
                train_model("lightgbm", self.records, self.folder.name)

    def test_bad_training_inputs_rejected(self):
        with self.assertRaisesRegex(ValueError, "21 days"):
            train_model("ridge", self.records[:200], self.folder.name)
        with self.assertRaisesRegex(ValueError, "contiguous"):
            train_model("ridge", self.records[:300] + self.records[301:], self.folder.name)
        with self.assertRaisesRegex(ValueError, "fraction"):
            train_model("ridge", self.records, self.folder.name, 0.99)
        with self.assertRaisesRegex(ValueError, "positive|greater"):
            train_model("ridge", self.records, self.folder.name, config={"alphas": [-1]})


if __name__ == "__main__":
    unittest.main()
