"""Import boundaries and provenance for the supplied residential CSVs."""
import importlib.util
import unittest
from pathlib import Path

spec = importlib.util.spec_from_file_location("residential_data", Path(__file__).resolve().parents[1] / "night-shift/build-residential-data.py")
data = importlib.util.module_from_spec(spec)
spec.loader.exec_module(data)


class ResidentialDataTests(unittest.TestCase):
    def test_decimal_commas_missing_values_and_nonfinite_values(self):
        self.assertEqual(data.number("13,2"), 13.2)
        self.assertEqual(data.number("0"), 0)
        self.assertIsNone(data.number("NA"))
        self.assertIsNone(data.number(None))
        with self.assertRaises(ValueError):
            data.number("NaN")

    def test_join_keys_must_be_unique(self):
        with self.assertRaisesRegex(ValueError, "Duplicate"):
            data.unique([{"id": "one"}, {"id": "one"}], "id")

    def test_oslo_duration_crosses_spring_dst_by_elapsed_time(self):
        start, issue = data.timestamp("2019-03-30 20:00:00")
        end, end_issue = data.timestamp("2019-03-31 07:00:00")
        self.assertIsNone(issue)
        self.assertIsNone(end_issue)
        self.assertEqual(end-start, 10*3600)

    def test_missing_and_ambiguous_times_remain_explicit(self):
        self.assertIsNone(data.timestamp("NA")[0])
        self.assertIn("Missing", data.timestamp("NA")[1])
        self.assertIn("Ambiguous", data.timestamp("2019-10-27 02:30:00")[1])
        self.assertIn("Ambiguous", data.timestamp("2019-03-31 02:30:00")[1])
