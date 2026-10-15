import json
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from sparc_lab.data import demo_dataset, parse_csv_dataset, hourly_records, dataset_summary, fetch_comed_dataset


def csv_rows(count=24, start="2026-10-01T00:00:00+00:00", price=4):
    stamp = datetime.fromisoformat(start)
    return "timestamp,price\n" + "\n".join(f"{(stamp + timedelta(minutes=5*i)).isoformat()},{price}" for i in range(count))


class DataTests(unittest.TestCase):
    def test_complete_hour_means_preserve_negative_and_spike(self):
        text = csv_rows(12).splitlines()
        prices = [-7, 90] + [2] * 10
        for i, value in enumerate(prices):
            text[i + 1] = text[i + 1].rsplit(",", 1)[0] + f",{value}"
        dataset = parse_csv_dataset("\n".join(text))
        hour = hourly_records(dataset)[0]
        self.assertAlmostEqual(hour["price"], sum(prices) / 12)
        self.assertEqual((hour["open"], hour["high"], hour["low"], hour["close"]), (-7, 90, -7, 2))
        self.assertEqual(hour["available_at"], "2026-10-01T01:00:00Z")

    def test_partial_and_missing_hour_excluded(self):
        lines = csv_rows(30).splitlines()
        del lines[4]
        dataset = parse_csv_dataset("\n".join(lines))
        hours = hourly_records(dataset)
        self.assertEqual(len(hours), 1)
        self.assertEqual(hours[0]["timestamp"], "2026-10-01T01:00:00Z")
        summary = dataset_summary(dataset)
        self.assertNotIn("points", summary)
        self.assertTrue(any("gap" in warning for warning in summary["warnings"]))
        self.assertTrue(any("incomplete" in warning for warning in summary["warnings"]))

    def test_utc_equivalent_duplicates_rejected(self):
        with self.assertRaisesRegex(ValueError, "Duplicate timestamp"):
            parse_csv_dataset("timestamp,price\n2026-10-01T00:00:00Z,3\n2026-09-30T19:00:00-05:00,4")

    def test_invalid_numbers_timestamps_and_headers_rejected(self):
        for text in ("timestamp,price\n2026-10-01T00:00:00Z,nan", "timestamp,price\n2026-10-01T00:00:00Z,inf",
                     "timestamp,price\n2026-10-01T00:03:00Z,4", "timestamp,price\n2026-10-01T00:00:00,4",
                     "timestamp,price_dollars\n2026-10-01T00:00:00Z,0.04", "timestamp,price,price\n2026-10-01T00:00:00Z,4,4"):
            with self.subTest(text=text):
                with self.assertRaises(ValueError):
                    parse_csv_dataset(text)

    def test_publication_cannot_precede_observed_interval(self):
        with self.assertRaisesRegex(ValueError, "cannot precede"):
            parse_csv_dataset("timestamp,price,published_at\n2026-10-01T00:00:00Z,4,2026-10-01T00:01:00Z")

    def test_day_ahead_requires_explicit_vintage(self):
        with self.assertRaisesRegex(ValueError, "both be supplied"):
            parse_csv_dataset("timestamp,price,day_ahead\n2026-10-01T00:00:00Z,4,3")
        text = csv_rows(12).splitlines()
        text[0] += ",day_ahead,day_ahead_available_at"
        for i in range(1, len(text)):
            text[i] += ",3,2026-09-30T22:00:00Z"
        dataset = parse_csv_dataset("\n".join(text))
        self.assertEqual(hourly_records(dataset)[0]["day_ahead"], 3)
        self.assertTrue(dataset_summary(dataset)["has_day_ahead"])
        text[-1] = text[-1].replace(",3,2026-09-30", ",4,2026-09-30")
        with self.assertRaisesRegex(ValueError, "consistent day_ahead"):
            parse_csv_dataset("\n".join(text))

    def test_late_publication_retained(self):
        lines = csv_rows(12).splitlines()
        lines[0] += ",published_at"
        for i in range(1, len(lines)):
            lines[i] += ",2026-10-01T02:00:00Z"
        dataset = parse_csv_dataset("\n".join(lines))
        self.assertEqual(hourly_records(dataset)[0]["available_at"], "2026-10-01T02:00:00Z")

    def test_demo_deterministic_long_and_labeled(self):
        first, second = demo_dataset(), demo_dataset()
        self.assertEqual(first["id"], second["id"])
        summary = dataset_summary(first)
        self.assertTrue(summary["is_synthetic"])
        self.assertEqual(summary["point_count"], 190 * 24 * 12)
        self.assertEqual(summary["hourly_count"], 190 * 24)
        self.assertEqual(summary["end"], "2026-10-16T12:00:00Z")
        self.assertTrue(any("SYNTHETIC" in warning for warning in summary["warnings"]))
        self.assertTrue(any(p["price"] < 0 for p in first["points"]))

    def test_comed_bounded_dates(self):
        for start, end in (("2025-01-01", "2025-03-01"), ("2025-01-02", "2025-01-01"), ("yesterday", "today")):
            with self.assertRaises(ValueError):
                fetch_comed_dataset(start, end)

    def test_comed_uses_official_api_and_preserves_provider_prices(self):
        stamp = datetime(2026, 1, 1, 6, tzinfo=timezone.utc)
        payload = [{"millisUTC": str(int((stamp + timedelta(minutes=5*i)).timestamp() * 1000)), "price": str(-2 if i == 0 else 4)} for i in range(12)]
        class Response:
            def __enter__(self): return self
            def __exit__(self, *args): return False
            def read(self, limit): return json.dumps(payload).encode()
        with patch("sparc_lab.data.urlopen", return_value=Response()) as mocked:
            dataset = fetch_comed_dataset("2026-01-01", "2026-01-01")
        self.assertIn("https://hourlypricing.comed.com/api?", mocked.call_args[0][0].full_url)
        self.assertEqual(mocked.call_args[1]["timeout"], 10)
        self.assertEqual(dataset["points"][0]["price"], -2)
        self.assertEqual(dataset["points"][0]["timestamp"], "2026-01-01T06:00:00Z")
        self.assertFalse(dataset_summary(dataset)["has_day_ahead"])
        self.assertTrue(any("revision" in w for w in dataset["warnings"]))


if __name__ == "__main__":
    unittest.main()
