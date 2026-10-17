"""Prices and weather written to yearly CSV files and read back."""
import tempfile
import unittest
from datetime import date
from pathlib import Path

from fakes import FakeComEd
from sparc import dataset
from sparc.history import backfill, backfill_weather
from sparc.store import WEATHER_COLUMNS, Store

TABLES = {"rt_5min": "start, price", "hourly": "hour, day_ahead, real_time", "weather": f"hour, {', '.join(WEATHER_COLUMNS)}"}


class DatasetTests(unittest.TestCase):
    def test_round_trip(self):
        folder = Path(self.enterContext(tempfile.TemporaryDirectory()))
        source = Store(folder / "source.db")
        self.addCleanup(source.close)
        fake = FakeComEd()
        backfill(source, date(2025, 12, 28), date(2026, 1, 3), fake, log=lambda message: None, today=date(2026, 3, 1))
        backfill_weather(source, date(2025, 12, 28), date(2026, 1, 3), fake, today=date(2026, 3, 1))

        counts = dataset.export(source, folder / "csv")
        self.assertEqual(sorted(p.name for p in (folder / "csv" / "five_minute").iterdir()), ["2025.csv", "2026.csv"])
        self.assertTrue(dataset.available(folder / "csv"))
        header = (folder / "csv" / "hourly" / "2026.csv").read_text().splitlines()[0]
        self.assertEqual(header, "hour_utc,hour_chicago,day_ahead_cents,real_time_cents")

        copy = Store(folder / "copy.db")
        self.addCleanup(copy.close)
        loaded = dataset.load(copy, folder / "csv")
        self.assertEqual(loaded, counts)
        for table, columns in TABLES.items():
            query = f"SELECT {columns} FROM {table} ORDER BY 1"
            self.assertEqual(copy.db.execute(query).fetchall(), source.db.execute(query).fetchall(), table)
        self.assertEqual(dataset.load(copy, folder / "csv"), counts)  # loading twice changes nothing
        self.assertEqual(copy.db.execute("SELECT COUNT(*) FROM rt_5min").fetchone()[0], counts["five_minute"])

    def test_one_year_only(self):
        folder = Path(self.enterContext(tempfile.TemporaryDirectory()))
        store = Store(folder / "sparc.db")
        self.addCleanup(store.close)
        backfill(store, date(2025, 12, 30), date(2026, 1, 2), FakeComEd(), log=lambda message: None,
                 today=date(2026, 3, 1))
        dataset.export(store, folder / "csv", years=[2026])
        self.assertEqual([p.name for p in (folder / "csv" / "hourly").iterdir()], ["2026.csv"])
        self.assertFalse(dataset.available(folder / "missing"))


if __name__ == "__main__":
    unittest.main()
