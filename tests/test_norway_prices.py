"""Price units, source integrity, and original Oslo clock boundaries."""
import csv
from datetime import date, datetime
from decimal import Decimal
import importlib.util
import io
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location("norway_price_import", Path(__file__).resolve().parents[1] / "night-shift/download-norway-prices.py")
prices = importlib.util.module_from_spec(spec)
spec.loader.exec_module(prices)


def source(days, value="265.8"):
    stream = io.StringIO()
    writer = csv.writer(stream, lineterminator="\n")
    writer.writerow(["Price_area", "Date", "Hour", "Price_NOK_MWh", "Price_NOK_kWh"])
    for area in prices.AREAS:
        for day in days:
            for hour in range(1, 25):
                writer.writerow([area, day.isoformat(), hour, value, "0.27"])
    return stream.getvalue().encode()


class NorwayPriceTests(unittest.TestCase):
    def test_mwh_precision_and_oslo_midnight_are_preserved(self):
        day = date(2019, 7, 1)
        rows, excluded, _, _ = prices.normalize(source([day]), day, day)
        self.assertEqual(len(rows), 96)
        self.assertEqual(excluded, {})
        first = rows[0]
        self.assertEqual(Decimal(first["price_nok_kwh"]), Decimal("0.2658"))
        self.assertEqual(first["hour_utc"], "2019-06-30T22:00:00Z")
        self.assertEqual(first["published_assumed_utc"], "2019-06-30T11:00:00Z")
        self.assertEqual(datetime.fromisoformat(first["hour_utc"].replace("Z", "+00:00")), datetime.fromisoformat(first["hour_local"]))

    def test_synthetic_clock_change_dates_are_omitted_entirely(self):
        transition, normal = date(2019, 3, 31), date(2019, 4, 1)
        rows, excluded, _, _ = prices.normalize(source([transition, normal]), transition, normal)
        self.assertEqual(len(rows), 96)
        self.assertIn("2019-03-31", excluded)
        self.assertTrue(all(row["hour_local"].startswith("2019-04-01") for row in rows))
        self.assertTrue(prices.clock_change(date(2019, 10, 27)))
        self.assertFalse(prices.clock_change(normal))

    def test_duplicate_or_incomplete_source_hours_are_rejected(self):
        day = date(2019, 7, 1)
        raw = source([day])
        first_data = raw.splitlines()[1] + b"\n"
        with self.assertRaisesRegex(ValueError, "Duplicate"):
            prices.normalize(raw + first_data, day, day)
        with self.assertRaisesRegex(ValueError, "incomplete"):
            prices.normalize(b"\n".join(raw.splitlines()[:-1]) + b"\n", day, day)

    def test_no_prices_are_invented_outside_source_history(self):
        day = date(2019, 7, 1)
        with self.assertRaisesRegex(ValueError, "beyond"):
            prices.normalize(source([day]), date(2019, 6, 30), day)

    def test_negative_prices_are_retained(self):
        day = date(2019, 7, 1)
        rows, _, _, values = prices.normalize(source([day], "-19.25"), day, day)
        self.assertEqual(Decimal(rows[0]["price_nok_kwh"]), Decimal("-0.01925"))
        self.assertLess(min(values), 0)
