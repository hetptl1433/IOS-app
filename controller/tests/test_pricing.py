import asyncio
from datetime import datetime, timedelta, timezone
import json
import unittest
from unittest.mock import patch

from sparc_controller.pricing import ComEdPricing, MAX_AGE_SECONDS, fetch_price, parse_price
from sparc_controller.display import DisplayTransport


class PricingTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 11, 4, 23, 10, tzinfo=timezone.utc)
        self.sample = {"millisUTC": str(int(self.now.timestamp() * 1000)), "price": "-2.4"}

    def test_signed_decimal_and_latest_timestamp_are_preserved(self):
        older = dict(self.sample, millisUTC=str(int((self.now-timedelta(minutes=5)).timestamp()*1000)), price="21")
        self.assertEqual(parse_price([self.sample, older], self.now), (-2.4, self.now.timestamp()))
        self.assertEqual(parse_price([dict(self.sample, price="0.0")], self.now)[0], 0.0)

    def test_bad_stale_and_future_samples_are_rejected(self):
        for payload in ([], {}, [None], [dict(self.sample, price="NaN")],
                        [dict(self.sample, price="Infinity")], [dict(self.sample, price=True)],
                        [dict(self.sample, millisUTC="Infinity")],
                        [dict(self.sample, millisUTC=str(int((self.now-timedelta(seconds=MAX_AGE_SECONDS+1)).timestamp()*1000)))],
                        [dict(self.sample, millisUTC=str(int((self.now+timedelta(minutes=6)).timestamp()*1000)))]):
            with self.subTest(payload=payload), self.assertRaises((ValueError, TypeError)):
                parse_price(payload, self.now)

    def test_feed_never_substitutes_demo_rate_and_expires(self):
        feed = ComEdPricing(lambda: self.now)
        self.assertIsNone(feed.state()["rateCents"])
        feed.price, feed.stamp = parse_price([self.sample], self.now)
        self.assertEqual(feed.state()["rateCents"], -2.4)
        self.now += timedelta(seconds=MAX_AGE_SECONDS+1)
        self.assertEqual(feed.state()["status"], "unavailable")
        self.assertIsNone(feed.state()["rateCents"])

    def test_network_timeout_and_response_limit(self):
        with patch("sparc_controller.pricing.urlopen") as opened:
            opened.return_value.__enter__.return_value.read.return_value = json.dumps([self.sample]).encode()
            self.assertEqual(fetch_price(), [self.sample])
            self.assertEqual(opened.call_args.kwargs["timeout"], 10)
            opened.return_value.__enter__.return_value.read.return_value = b"x"*65537
            with self.assertRaises(ValueError): fetch_price()

    def test_display_adds_live_price_without_changing_virtual_accounting(self):
        class Service:
            def display_state(self):
                return {"controller": {}, "snapshot": {"currentRateCents": 21.0}}
        feed = ComEdPricing(lambda: self.now)
        feed.price, feed.stamp = parse_price([self.sample], self.now)
        state = DisplayTransport(Service(), 8767, feed).state()
        self.assertEqual(state["pricing"]["rateCents"], -2.4)
        self.assertEqual(state["snapshot"]["currentRateCents"], 21.0)


class PollTests(unittest.IsolatedAsyncioTestCase):
    async def test_poll_survives_network_failure_and_can_be_cancelled(self):
        feed = ComEdPricing()
        reached_sleep = asyncio.Event()
        async def sleeping(_):
            reached_sleep.set()
            await asyncio.Future()
        with patch("sparc_controller.pricing.fetch_hourly_table", side_effect=OSError("offline")), patch("sparc_controller.pricing.fetch_json", side_effect=OSError("offline")), patch("sparc_controller.pricing.fetch_price", side_effect=OSError("offline")), patch("sparc_controller.pricing.asyncio.sleep", sleeping):
            task = asyncio.create_task(feed.run())
            await asyncio.wait_for(reached_sleep.wait(), 2)
            self.assertIsNone(feed.state()["rateCents"])
            task.cancel()
            with self.assertRaises(asyncio.CancelledError): await task


class RateHistoryTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026,11,4,23,10,tzinfo=timezone.utc)
        self.feed = ComEdPricing(lambda:self.now)

    def test_history_keeps_negative_prices_missing_hours_and_future_estimates_distinct(self):
        start = int(datetime(2026,11,4,6,tzinfo=timezone.utc).timestamp())
        self.feed.settled_hourly[start] = -4.5
        self.feed.samples[start+3600] = 1.7
        self.feed.price,self.feed.stamp = -2.4,self.now.timestamp()
        self.feed.day_ahead[start+20*3600] = 8.2
        page = self.feed.rate_history('2026-11-04')
        self.assertEqual(len(page['hours']),24)
        self.assertEqual(page['hours'][0]['supplyCents'],-4.5)
        self.assertEqual(page['hours'][0]['status'],'settled')
        self.assertEqual(page['hours'][1]['status'],'provisional')
        self.assertIsNone(page['hours'][2]['supplyCents'])
        self.assertEqual(page['hours'][2]['status'],'unavailable')
        self.assertEqual(page['hours'][17]['status'],'current')
        self.assertEqual(page['hours'][20]['status'],'dayAhead')
        self.assertEqual(page['currentSupplyCents'],-2.4)
        self.assertLess(len(json.dumps(page).encode())*1.4,16384)

    def test_fall_back_keeps_both_one_am_hours_and_spring_forward_has_23_hours(self):
        page = self.feed.rate_history('2026-11-01')
        self.assertEqual(len(page['hours']),25)
        self.assertNotEqual(page['hours'][1]['start'],page['hours'][2]['start'])
        self.now = datetime(2026,3,10,17,tzinfo=timezone.utc)
        self.assertEqual(len(self.feed.rate_history('2026-03-08')['hours']),23)

    def test_bad_future_and_out_of_retention_dates_are_rejected(self):
        for day in ['20261104','2026-11-05','2026-10-27','not-a-day',True,{}]:
            with self.subTest(day=day),self.assertRaises(ValueError):self.feed.rate_history(day)

    def test_stale_quote_is_not_shown_as_current_or_replaced_by_day_ahead(self):
        self.feed.price,self.feed.stamp = 3,self.now.timestamp()-MAX_AGE_SECONDS-1
        hour = int(self.now.timestamp())//3600*3600
        self.feed.day_ahead[hour] = 9
        page = self.feed.rate_history()
        self.assertIsNone(page['currentSupplyCents'])
        self.assertIsNone(next(x for x in page['hours'] if x['start']==datetime.fromtimestamp(hour,timezone.utc).isoformat())['supplyCents'])
