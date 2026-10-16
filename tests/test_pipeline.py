"""Backfill -> check -> backtest against a fake ComEd server."""
import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from datetime import date, time
from pathlib import Path

from fakes import FakeComEd
from sparc.__main__ import main
from sparc.clock import HOUR, SLOT, at
from sparc.collector import collect, has_day_ahead, seconds_to_next_poll
from sparc.history import backfill, check
from sparc.store import Store


class PipelineTests(unittest.TestCase):
    def test_backfill_check_and_backtest(self):
        folder = Path(self.enterContext(tempfile.TemporaryDirectory()))
        store = Store(folder / "sparc.db")
        self.addCleanup(store.close)
        fake = FakeComEd()
        first, last = date(2026, 1, 5), date(2026, 1, 18)
        failed = backfill(store, first, last, fake, log=lambda message: None)
        self.assertEqual(failed, [])
        five_minute_requests = [r for r in fake.requests if r["type"] == "5minutefeed"]
        self.assertEqual([r["datestart"] for r in five_minute_requests], ["202601050000", "202601120000"])
        self.assertEqual(five_minute_requests[0]["dateend"], "202601112355")

        coverage = check(store, first, last)
        self.assertEqual(coverage.hours, 14 * 24)
        self.assertEqual(coverage.complete, coverage.hours)
        self.assertEqual(coverage.published, coverage.hours)
        self.assertEqual((coverage.partial, coverage.unpriced), (0, 0))
        self.assertEqual(coverage.day_ahead, coverage.hours)
        self.assertEqual(coverage.compared, coverage.hours)
        self.assertEqual(coverage.revised, [])

        # Resuming does not repeat finished requests.
        fake.requests.clear()
        backfill(store, first, last, fake, log=lambda message: None)
        self.assertEqual(fake.requests, [])

        output = io.StringIO()
        with redirect_stdout(output), redirect_stderr(io.StringIO()):
            status = main(["--data", str(folder), "backtest", "2026-01-12", "2026-01-16", "--calibrate", "6",
                           "--csv", str(folder / "nights.csv")])
        text = output.getvalue()
        self.assertEqual(status, 0)
        self.assertIn("Commuter · Standard delivery · 5 nights", text)
        self.assertIn("Below 3¢", text)
        self.assertIn("Perfect hindsight", text)
        self.assertNotIn("Day-ahead + live prices", text)  # needs 30 calibration days
        self.assertNotIn("skipped", text)
        self.assertEqual(len((folder / "nights.csv").read_text().splitlines()), 1 + 5 * 4 * 2 * 11)
        self.assertEqual(len(list((folder / "runs").glob("*.json"))), 1)


class CollectorTests(unittest.TestCase):
    def test_records_first_seen_time_and_tomorrows_day_ahead(self):
        store = Store(":memory:")
        self.addCleanup(store.close)
        fake = FakeComEd()
        now = at(date(2026, 1, 14), time(16, 40))

        def get(url):
            if "5minutefeed" in url and "datestart" not in url:
                start = now - 24 * HOUR
                start -= start % SLOT
                return json.dumps([{"millisUTC": str(t * 1000), "price": "2.0"}
                                   for t in range(start, now - 7 * 60, SLOT)]).encode()
            return fake(url)

        collect(store, get, clock=lambda: now, sleep=lambda s: None, log=self.fail, cycles=1)
        seen = {s for (s,) in store.db.execute("SELECT DISTINCT seen_at FROM rt_5min")}
        self.assertEqual(seen, {now})
        self.assertTrue(has_day_ahead(store, date(2026, 1, 15)))

    def test_polls_shortly_after_each_price_is_published(self):
        boundary = at(date(2026, 1, 14), time(16, 40))
        self.assertEqual(seconds_to_next_poll(boundary + 100), 50)
        self.assertEqual(seconds_to_next_poll(boundary + 200), 250)


if __name__ == "__main__":
    unittest.main()
