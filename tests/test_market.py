import unittest
from datetime import datetime, timezone

from sparc.clock import DAY, HOUR, SLOT
from sparc.market import Market, day_ahead_release, latest_visible_interval
from sparc.tariff import STANDARD, TIME_OF_DAY, capacity_risk


def utc(*args):
    return int(datetime(*args, tzinfo=timezone.utc).timestamp())


class MarketTests(unittest.TestCase):
    def test_hourly_price_needs_all_twelve_intervals(self):
        t0 = utc(2026, 1, 5, 6)
        five = {t0 + i * SLOT: float(i) for i in range(12)}
        five.update({t0 + HOUR + i * SLOT: 1.0 for i in range(11)})
        self.assertEqual(Market(five, {}).hourly, {t0: 5.5})

    def test_day_ahead_release_is_the_afternoon_before_the_operating_day(self):
        midnight = utc(2026, 7, 2, 5)  # 00:00 CDT, operating day Jul 2
        self.assertEqual(day_ahead_release(midnight), utc(2026, 7, 1, 22))  # 17:00 CDT Jul 1
        eleven_pm = utc(2026, 7, 2, 4)  # 23:00 CDT Jul 1, still operating day Jul 1
        self.assertEqual(day_ahead_release(eleven_pm), utc(2026, 6, 30, 22))
        winter = utc(2026, 1, 15, 6)  # 00:00 CST
        self.assertEqual(day_ahead_release(winter), utc(2026, 1, 14, 23))  # 17:00 CST

    def test_unpublished_day_ahead_falls_back_to_the_day_before(self):
        hour = utc(2026, 7, 2, 6)  # 01:00 CDT Jul 2
        market = Market({}, {hour: 4.0, hour - DAY: 2.5})
        self.assertEqual(market.known_day_ahead(hour, utc(2026, 7, 1, 21)), 2.5)  # 16:00 CDT Jul 1
        self.assertEqual(market.known_day_ahead(hour, utc(2026, 7, 1, 22)), 4.0)
        self.assertIsNone(Market({}, {}).known_day_ahead(hour, hour))

    def test_latest_visible_interval_lags_by_publication_delay(self):
        now = utc(2026, 10, 16, 6, 15)
        self.assertEqual(latest_visible_interval(now), utc(2026, 10, 16, 6, 5))
        self.assertEqual(latest_visible_interval(now + 2 * 60), utc(2026, 10, 16, 6, 10))


class TariffTests(unittest.TestCase):
    def test_time_of_day_bands_in_chicago_time(self):
        cases = {(2026, 1, 15, 11): 2.984,  # 05:00 CST overnight
                 (2026, 1, 15, 12): 4.009,  # 06:00 morning
                 (2026, 1, 15, 18): 4.009,  # 12:00
                 (2026, 1, 15, 19): 10.712,  # 13:00 peak
                 (2026, 1, 16, 0): 10.712,  # 18:00
                 (2026, 1, 16, 1): 3.747,  # 19:00 evening
                 (2026, 1, 16, 3): 2.984,  # 21:00 overnight
                 (2026, 7, 15, 18): 10.712}  # 13:00 CDT
        for args, cents in cases.items():
            with self.subTest(args=args):
                self.assertEqual(TIME_OF_DAY.cents(utc(*args)), cents)
        self.assertEqual(STANDARD.cents(utc(2026, 7, 15, 18)), 6.228)

    def test_capacity_risk_is_summer_weekday_afternoons(self):
        self.assertTrue(capacity_risk(utc(2026, 7, 15, 17)))  # Wed 12:00 CDT
        self.assertTrue(capacity_risk(utc(2026, 7, 15, 23)))  # Wed 18:00
        self.assertFalse(capacity_risk(utc(2026, 7, 16, 0)))  # Wed 19:00
        self.assertFalse(capacity_risk(utc(2026, 7, 18, 19)))  # Saturday
        self.assertFalse(capacity_risk(utc(2026, 10, 14, 19)))  # October


if __name__ == "__main__":
    unittest.main()
