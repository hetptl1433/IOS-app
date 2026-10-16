import io
import json
import unittest
from datetime import date, datetime, timezone
from urllib.error import HTTPError

from sparc.clock import HOUR, local
from sparc.comed import ComEdError, Fetcher, HourRow, day_chunks, parse_five_minute, parse_hourly_table
from sparc.store import Store


def utc(*args):
    return int(datetime(*args, tzinfo=timezone.utc).timestamp())


def table_body(day, labels, cell=lambda i: ("2.0¢", "1.5¢"), markup=True):
    rows = []
    for i, label in enumerate(labels):
        day_ahead, real_time = cell(i)
        hour, half = label.split()
        if markup:
            rows.append(f"<tr><td>{hour} <small>{half}</small></td><td>{day_ahead.replace('¢', '&cent;')}</td>"
                        f"<td>{real_time.replace('¢', '&cent;')}</td></tr>")
        else:
            rows.append(f"{hour} <small>{half}</small>{day_ahead}{real_time}")
    return "".join(rows).encode("cp1252")


NORMAL_LABELS = (["12:00 a.m."] + [f"{h}:00 a.m." for h in range(1, 12)]
                 + ["12:00 p.m."] + [f"{h}:00 p.m." for h in range(1, 12)])


class FiveMinuteFeedTests(unittest.TestCase):
    def test_parses_strings_and_sorts(self):
        body = json.dumps([{"millisUTC": "1434686700000", "price": "2.0"},
                           {"millisUTC": "1434686100000", "price": "-2.5"}]).encode()
        self.assertEqual(parse_five_minute(body), [(1434686100, -2.5), (1434686700, 2.0)])

    def test_identical_duplicates_collapse_and_conflicts_fail(self):
        same = json.dumps([{"millisUTC": "1434686700000", "price": "2.0"}] * 2).encode()
        self.assertEqual(len(parse_five_minute(same)), 1)
        conflict = json.dumps([{"millisUTC": "1434686700000", "price": "2.0"},
                               {"millisUTC": "1434686700000", "price": "2.1"}]).encode()
        with self.assertRaises(ComEdError):
            parse_five_minute(conflict)

    def test_rejects_off_grid_nonfinite_and_malformed(self):
        for records in ([{"millisUTC": "1434686760000", "price": "2"}],
                        [{"millisUTC": "1434686700000", "price": "nan"}],
                        [{"price": "2"}], {"not": "a list"}):
            with self.subTest(records=records), self.assertRaises(ComEdError):
                parse_five_minute(json.dumps(records).encode())
        with self.assertRaises(ComEdError):
            parse_five_minute(b"<html>maintenance</html>")


class HourlyTableTests(unittest.TestCase):
    def test_first_row_is_the_hour_before_midnight(self):
        day = date(2026, 10, 16)
        rows = parse_hourly_table(table_body(day, NORMAL_LABELS, lambda i: (f"{i}.5¢", "n/a" if i > 9 else "-0.9¢")), day)
        self.assertEqual(len(rows), 24)
        # 11 p.m. CDT on Oct 15 is 04:00 UTC on Oct 16.
        self.assertEqual(rows[0], HourRow(utc(2026, 10, 16, 4), 0.5, -0.9))
        self.assertEqual(rows[-1].hour, utc(2026, 10, 17, 3))
        self.assertIsNone(rows[10].real_time)
        self.assertEqual([b.hour - a.hour for a, b in zip(rows, rows[1:])], [HOUR] * 23)

    def test_accepts_bare_cp1252_cells(self):
        day = date(2026, 10, 16)
        rows = parse_hourly_table(table_body(day, NORMAL_LABELS, markup=False), day)
        self.assertEqual((rows[0].day_ahead, rows[0].real_time), (2.0, 1.5))

    def test_fall_back_day_has_two_one_am_rows(self):
        day = date(2026, 11, 1)
        labels = ["12:00 a.m.", "1:00 a.m.", "1:00 a.m."] + NORMAL_LABELS[2:]
        rows = parse_hourly_table(table_body(day, labels), day)
        self.assertEqual(len(rows), 25)
        self.assertEqual([local(r.hour + HOUR).strftime("%H %Z") for r in rows[1:3]], ["01 CDT", "01 CST"])

    def test_table_that_breaks_the_labelling_rule_is_rejected(self):
        day = date(2026, 11, 1)  # 25-hour day presented as 24 ordinary rows
        with self.assertRaisesRegex(ComEdError, "row 3"):
            parse_hourly_table(table_body(day, NORMAL_LABELS), day)
        with self.assertRaises(ComEdError):
            parse_hourly_table(b"<p>No data</p>", day)


class FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class FetcherTests(unittest.TestCase):
    def test_retries_transient_errors_then_succeeds(self):
        calls, sleeps = [], []

        def opener(request, timeout):
            calls.append(request.get_header("User-agent"))
            if len(calls) == 1:
                raise HTTPError(request.full_url, 503, "busy", {}, None)
            return FakeResponse(b"ok")

        fetch = Fetcher(min_interval=0, opener=opener, sleep=sleeps.append)
        self.assertEqual(fetch("https://example.test/x"), b"ok")
        self.assertEqual(len(calls), 2)
        self.assertIn("sparc", calls[0])
        self.assertEqual(sleeps, [2.0])

    def test_client_errors_are_not_retried(self):
        def opener(request, timeout):
            raise HTTPError(request.full_url, 404, "missing", {}, None)

        fetch = Fetcher(min_interval=0, opener=opener, sleep=lambda s: None)
        with self.assertRaisesRegex(ComEdError, "HTTP 404"):
            fetch("https://example.test/x")


class ChunkTests(unittest.TestCase):
    def test_chunks_cover_range_without_overlap(self):
        chunks = list(day_chunks(date(2026, 1, 1), date(2026, 1, 16)))
        self.assertEqual(chunks, [(date(2026, 1, 1), date(2026, 1, 7)), (date(2026, 1, 8), date(2026, 1, 14)),
                                  (date(2026, 1, 15), date(2026, 1, 16))])


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.store = Store(":memory:")
        self.addCleanup(self.store.close)

    def test_first_seen_time_survives_later_updates(self):
        self.store.add_five_minute([(300, 2.0)], seen_at=1000)
        self.store.add_five_minute([(300, 2.5)], seen_at=2000)
        self.store.add_five_minute([(600, 1.0)])
        self.assertEqual(self.store.five_minute(0, 900), {300: 2.5, 600: 1.0})
        seen = dict(self.store.db.execute("SELECT start, seen_at FROM rt_5min"))
        self.assertEqual(seen, {300: 1000, 600: None})

    def test_missing_cells_do_not_erase_known_prices(self):
        self.store.add_hourly([HourRow(3600, 2.0, None)], seen_at=50)
        self.store.add_hourly([HourRow(3600, None, 1.7)], seen_at=90)
        self.assertEqual(self.store.day_ahead(0, 7200), {3600: 2.0})
        self.assertEqual(self.store.published_real_time(0, 7200), {3600: 1.7})
        self.assertEqual(self.store.db.execute("SELECT da_seen_at FROM hourly").fetchone(), (50,))

    def test_fetch_log(self):
        self.store.mark_fetched("table", "2026-10-16")
        self.assertEqual(self.store.fetched("table"), {"2026-10-16"})
        self.assertEqual(self.store.fetched("rt5"), set())


if __name__ == "__main__":
    unittest.main()
