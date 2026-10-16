"""Weather, holidays, and what a forecast may know about them."""
import json
import unittest
from datetime import date, time, timedelta

from fakes import FakeComEd
from sparc.clock import DAY, HOUR, at
from sparc.forecast import INPUTS, PRICE_FEATURES, WEATHER_FEATURES, Recent, features
from sparc.history import backfill_weather
from sparc.holidays import holidays, is_holiday
from sparc.market import Market
from sparc.store import Store
from sparc.weather import WeatherError, WeatherHour, chunks, parse

from test_forecast import FIRST, persistent_gap_market


class HolidayTests(unittest.TestCase):
    def test_matches_pjms_published_lists(self):
        # PJM business calendars: NERC off-peak days, plus PJM's day after Thanksgiving.
        self.assertEqual(sorted(holidays(2025)), [
            date(2025, 1, 1), date(2025, 5, 26), date(2025, 7, 4), date(2025, 9, 1),
            date(2025, 11, 27), date(2025, 11, 28), date(2025, 12, 25)])
        self.assertEqual(sorted(holidays(2026)), [
            date(2026, 1, 1), date(2026, 5, 25), date(2026, 7, 4), date(2026, 9, 7),  # a Saturday stays put
            date(2026, 11, 26), date(2026, 11, 27), date(2026, 12, 25)])

    def test_a_sunday_holiday_moves_to_monday(self):
        self.assertTrue(is_holiday(date(2027, 7, 5)))
        self.assertFalse(is_holiday(date(2027, 7, 4)))
        self.assertTrue(is_holiday(date(2027, 12, 25)))  # Saturday


class ParseTests(unittest.TestCase):
    def body(self, **changes):
        hourly = {"time": [1767225600, 1767229200]}
        for name in ("temperature_2m", "dew_point_2m", "wind_speed_10m", "cloud_cover"):
            hourly[name] = [1.5, None]
            hourly[f"{name}_previous_day1"] = [2.0, 3.0]
        hourly.update(changes)
        return json.dumps({"hourly": hourly})

    def test_reads_values_and_gaps(self):
        first, second = parse(self.body())
        self.assertEqual((first.hour, first.temp_c, first.temp_fc, first.cloud_fc), (1767225600, 1.5, 2.0, 2.0))
        self.assertIsNone(second.temp_c)

    def test_refuses_implausible_or_missing_data(self):
        for changes in ({"temperature_2m": [99.0, 1.0]}, {"time": [1767225600, 1767229201]},
                        {"wind_speed_10m": [1.0]}, {"cloud_cover": ["clear", 1.0]}):
            with self.subTest(changes=changes), self.assertRaises(WeatherError):
                parse(self.body(**changes))
        with self.assertRaises(WeatherError):
            parse(json.dumps({"error": True, "reason": "Parameter 'start_date' is out of range"}))
        with self.assertRaises(WeatherError):
            parse("<html>")

    def test_requests_cover_chicago_dates_in_utc(self):
        self.assertEqual(chunks(date(2026, 1, 1), date(2026, 1, 10)), [(date(2026, 1, 1), date(2026, 1, 11))])
        spans = chunks(date(2025, 1, 1), date(2025, 12, 31))
        self.assertEqual((spans[0][0], spans[-1][1]), (date(2025, 1, 1), date(2026, 1, 1)))
        self.assertTrue(all(b[0] == a[1] + timedelta(days=1) for a, b in zip(spans, spans[1:])))


class BackfillTests(unittest.TestCase):
    def test_stores_weather_and_resumes(self):
        store = Store(":memory:")
        self.addCleanup(store.close)
        fake = FakeComEd()
        backfill_weather(store, date(2026, 1, 1), date(2026, 1, 31), fake, today=date(2026, 3, 1))
        rows = store.weather(at(date(2026, 1, 1), time(0)), at(date(2026, 2, 1), time(0)))
        self.assertEqual(len(rows), 31 * 24)
        self.assertTrue(all(r.temp_fc is not None and r.temp_c is not None for r in rows.values()))
        fake.requests.clear()
        backfill_weather(store, date(2026, 1, 1), date(2026, 1, 31), fake, today=date(2026, 3, 1))
        self.assertEqual(fake.requests, [])


class WeatherFeatureTests(unittest.TestCase):
    def market(self):
        market = persistent_gap_market(days=10)
        start = at(FIRST, time(0)) - 2 * DAY
        rows = {h: WeatherHour(h, -5.0, -9.0, 20.0, 80.0, -4.0, -10.0, 25.0, 90.0)
                for h in range(start, start + 14 * DAY, HOUR)}
        return Market(market.five_minute, market.day_ahead, weather=rows)

    def test_uses_only_forecasts_already_out_and_hours_already_seen(self):
        market = self.market()
        now = at(FIRST + timedelta(days=3), time(19, 5))
        target = at(FIRST + timedelta(days=4), time(2))
        before = features(market, target, now, Recent.seen_by(market, now), "weather")
        self.assertEqual(len(before), len(PRICE_FEATURES) + len(WEATHER_FEATURES))
        # Change what was not known at 19:05: the analysis from 19:00 on, and the forecasts
        # of hours more than 18 hours ahead.
        known_until = (now // HOUR) * HOUR
        tampered = {}
        for hour, row in market.weather.items():
            analysis = row.temp_c if hour < known_until else 40.0
            forecast = row.temp_fc if hour - now <= 18 * HOUR else 40.0
            tampered[hour] = WeatherHour(hour, analysis, row.dew_c, row.wind_kmh, row.cloud_pct,
                                         forecast, row.dew_fc, row.wind_fc, row.cloud_fc)
        changed = Market(market.five_minute, market.day_ahead, weather=tampered)
        self.assertEqual(before, features(changed, target, now, Recent.seen_by(changed, now), "weather"))

    def test_weather_columns(self):
        market = self.market()
        now = at(FIRST + timedelta(days=3), time(19))
        target = at(FIRST + timedelta(days=4), time(2))
        row = dict(zip(INPUTS["weather"], features(market, target, now, Recent.seen_by(market, now), "weather")))
        self.assertEqual(row["holiday"], 0.0)
        self.assertEqual((row["heating_fc"], row["cooling_fc"]), (22.0, 0.0))  # forecast -4 °C, base 18
        self.assertEqual((row["dew_point_fc"], row["wind_fc"], row["cloud_fc"]), (-10.0, 25.0, 90.0))
        self.assertEqual(row["temp_surprise"], -1.0)  # it was -5 °C against a forecast of -4
        prices = features(market, target, now, Recent.seen_by(market, now))
        self.assertEqual(prices, [row[name] for name in PRICE_FEATURES])

    def test_missing_weather_falls_back_to_typical_values(self):
        market = persistent_gap_market(days=10)
        now = at(FIRST + timedelta(days=3), time(19))
        row = dict(zip(INPUTS["weather"], features(market, now + 7 * HOUR, now, Recent.seen_by(market, now),
                                                    "weather")))
        self.assertEqual((row["heating_fc"], row["cooling_fc"], row["temp_surprise"]), (0.0, 0.0, 0.0))


if __name__ == "__main__":
    unittest.main()
