"""Backfill -> check -> backtest against a fake ComEd server."""
import io
import json
import math
import unittest
from contextlib import redirect_stdout
from datetime import date, datetime, time, timedelta
from urllib.parse import parse_qs, urlparse

from sparc.__main__ import run_backtest
from sparc.clock import CT, HOUR, SLOT, at, hour_start, local
from sparc.collector import collect, has_day_ahead, seconds_to_next_poll
from sparc.history import backfill, check
from sparc.store import Store


def price(t):
    wall = local(t)
    return round(3 + 2 * math.sin(wall.hour / 24 * 2 * math.pi) + (20 if wall.minute == 10 and wall.hour == 1 else 0), 1)


def day_ahead(hour):
    return round(3 + 2 * math.sin(local(hour).hour / 24 * 2 * math.pi), 1)


class FakeComEd:
    def __init__(self):
        self.requests = []

    def __call__(self, url):
        query = {k: v[0] for k, v in parse_qs(urlparse(url).query).items()}
        self.requests.append(query)
        if query["type"] == "5minutefeed":
            first = datetime.strptime(query["datestart"], "%Y%m%d%H%M").replace(tzinfo=CT)
            last = datetime.strptime(query["dateend"], "%Y%m%d%H%M").replace(tzinfo=CT)
            labels = range(int(first.timestamp()), int(last.timestamp()) + 1, SLOT)
            return json.dumps([{"millisUTC": str(t * 1000), "price": str(price(t))}
                               for t in reversed(labels)]).encode()
        day = datetime.strptime(query["date"], "%Y%m%d").date()
        first_hour = at(day, time(0)) - HOUR
        cells = []
        for hour in range(first_hour, at(day + timedelta(days=1), time(0)) - HOUR, HOUR):
            ends = local(hour + HOUR)
            mean = sum(price(t) for t in range(hour, hour + HOUR, SLOT)) / 12
            cells.append(f"{ends:%I}".lstrip("0") + f":00 <small>{'a' if ends.hour < 12 else 'p'}.m.</small>"
                         f"{day_ahead(hour)}¢{round(mean, 1)}¢")
        return "".join(cells).encode("cp1252")


class PipelineTests(unittest.TestCase):
    def test_backfill_check_and_backtest(self):
        store = Store(":memory:")
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
        with redirect_stdout(output):
            run_backtest(store, date(2026, 1, 12), date(2026, 1, 16), calibrate_days=6, csv=None)
        text = output.getvalue()
        self.assertIn("Commuter · Standard delivery · 5 nights", text)
        self.assertIn("Perfect hindsight", text)
        self.assertNotIn("skipped", text)


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
