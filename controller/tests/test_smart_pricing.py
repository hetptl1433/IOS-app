import asyncio
from datetime import date,datetime,timedelta,timezone
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from sparc_controller.pricing import (ComEdError,ComEdPricing,DAY_AHEAD_REFRESH_SECONDS,HOUR,
                                      HourRow,fetch_hourly_table,hour_key,hourly_table_url,parse_hourly_table)

UTC=timezone.utc
LABELS=['12:00 a.m.']+[f'{n}:00 a.m.' for n in range(1,12)]+['12:00 p.m.']+[f'{n}:00 p.m.' for n in range(1,12)]


def table(labels=LABELS,price='1.2',settled='n/a'):
    ahead='n/a' if price is None else price+'&cent;'
    actual='n/a' if settled is None or settled=='n/a' else settled+'&cent;'
    return ''.join(f'<tr><td>{label}</td><td>{ahead}</td><td>{actual}</td></tr>' for label in labels).encode()


class SmartPricingTests(unittest.TestCase):
    def setUp(self):
        self.now=datetime(2026,11,5,0,35,tzinfo=UTC)
        self.feed=ComEdPricing(lambda:self.now)

    def test_main_parser_preserves_hour_ending_chicago_semantics(self):
        day=date(2026,11,5)
        rows=parse_hourly_table(table(price='-1.2'),day)
        self.assertEqual(rows[0],HourRow(int(datetime(2026,11,5,5,tzinfo=UTC).timestamp()),-1.2,None))
        self.assertEqual(rows[-1].hour,int(datetime(2026,11,6,4,tzinfo=UTC).timestamp()))
        self.assertEqual(hourly_table_url(day),'https://hourlypricing.comed.com/rrtp/ServletFeed?type=pricingtabledual&date=20261105')

    def test_dst_labels_are_checked_and_repeated_one_am_is_supported(self):
        day=date(2026,11,1)
        with self.assertRaises(ComEdError):parse_hourly_table(table(),day)
        labels=['12:00 a.m.','1:00 a.m.','1:00 a.m.']+LABELS[2:]
        rows=parse_hourly_table(table(labels),day)
        self.assertEqual(len(rows),25)
        self.assertEqual(rows[2].hour-rows[1].hour,HOUR)

    def test_table_http_is_one_bounded_ten_second_request(self):
        with patch('sparc_controller.pricing.urlopen') as opened:
            response=opened.return_value.__enter__.return_value
            response.read.return_value=table()
            raw,rows=fetch_hourly_table(date(2026,11,5))
            self.assertEqual(len(rows),24)
            self.assertEqual(opened.call_count,1)
            self.assertEqual(opened.call_args.kwargs['timeout'],10)
            self.assertEqual(response.read.call_args.args,(131073,))
            response.read.return_value=b'x'*131073
            with self.assertRaises(ValueError):fetch_hourly_table(date(2026,11,5))
            response.read.return_value=table(['12:00 a.m.'])
            with self.assertRaises(ComEdError):fetch_hourly_table(date(2026,11,5))

    def test_actual_october_spike_confirms_start_labels_and_settled_billing(self):
        hour=int(datetime(2026,10,16,6,tzinfo=UTC).timestamp())  # 1 AM Chicago
        # Received ComEd labels01:00..01:55, published table price7.9 cents.
        prices=[2.6,2.1,33.9,25.1,3.2,23.5,2.1,2.7,-2.0,1.0,1.7,-1.3]
        self.feed.samples={hour+i*300:price for i,price in enumerate(prices)}
        self.feed.samples[hour+HOUR]=-1.4
        cost=self.feed.energy_cost({hour_key(hour):10})
        self.assertAlmostEqual(cost['cost'],10*(sum(prices)/12)/100)
        self.assertTrue(cost['final'])
        self.feed.settled_hourly[hour]=7.9
        self.assertAlmostEqual(self.feed.energy_cost({hour_key(hour):10})['cost'],0.79)
        # Settlement can finalize an hour even when its five-minute feed has gaps.
        self.feed.samples={}
        self.assertEqual(self.feed.energy_cost({hour_key(hour):10}),{'cost':0.79,'unpricedEnergyKWh':0.0,'final':True})

    def test_cache_retains_real_forecasts_and_settlement_when_quote_is_stale(self):
        hour=int(datetime(2026,11,5,6,tzinfo=UTC).timestamp())
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'prices.json'
            path.write_text(json.dumps({'quote':[{'millisUTC':'1000','price':'21'}],'samples':[],
                                        'dayAhead':{str(hour):1.3},'settledHourly':{str(hour-HOUR):2.2},
                                        'dayAheadFeeds':{'2026-11-05':{'source':hourly_table_url(date(2026,11,5)),'fetchedAt':self.now.isoformat()}}}))
            restored=ComEdPricing(lambda:self.now,cache=path)
            self.assertIsNone(restored.state()['rateCents'])
            self.assertEqual(restored.day_ahead,{hour:1.3})
            self.assertEqual(restored.settled_hourly,{hour-HOUR:2.2})
            self.assertEqual(restored.state()['dayAheadFeeds']['2026-11-05']['source'],hourly_table_url(date(2026,11,5)))


class RefreshTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.now=datetime(2026,11,5,0,35,tzinfo=UTC)
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.cache=Path(self.temp.name)/'cache.json'
        self.log=Path(self.temp.name)/'comed.jsonl'
        self.feed=ComEdPricing(lambda:self.now,cache=self.cache,log=self.log)

    def sample(self):
        return [{'millisUTC':str(int(self.now.timestamp())//300*300000),'price':'3.0'}]

    async def test_three_dates_fetch_in_worker_threads_and_refresh_only_each_fifteen_minutes(self):
        requested=[];worker_threads=[];main_thread=threading.get_ident()
        def fetched(day):
            requested.append(day);worker_threads.append(threading.get_ident())
            raw=table(price='1.3',settled='2.0')
            return raw,parse_hourly_table(raw,day)
        with patch('sparc_controller.pricing.fetch_price',side_effect=self.sample), patch('sparc_controller.pricing.fetch_json',side_effect=lambda _:self.sample()), patch('sparc_controller.pricing.fetch_hourly_table',side_effect=fetched):
            await self.feed.refresh()
            self.assertEqual(requested,[date(2026,11,3),date(2026,11,4),date(2026,11,5)])
            self.assertTrue(all(thread!=main_thread for thread in worker_threads))
            self.assertEqual(len(self.feed.day_ahead),72)
            self.assertTrue(self.feed.settled_hourly)
            await self.feed.refresh()
            self.assertEqual(len(requested),3)
            self.now+=timedelta(seconds=DAY_AHEAD_REFRESH_SECONDS)
            await self.feed.refresh()
            self.assertEqual(len(requested),6)
        saved=json.loads(self.cache.read_text())
        self.assertEqual(len(saved['dayAhead']),72)
        metadata=self.feed.state()['dayAheadFeeds']['2026-11-05']
        self.assertEqual(metadata['status'],'available')
        self.assertEqual(metadata['availableHours'],24)
        self.assertIn('firstSeenAt',metadata)
        self.assertIn('firstAvailableAt',metadata)
        self.assertEqual(len([json.loads(line) for line in self.log.read_text().splitlines() if json.loads(line)['type']=='hourlyTable']),6)

    async def test_feed_failure_preserves_received_forecasts_and_logs_errors_without_fake_prices(self):
        hour=int(datetime(2026,11,5,6,tzinfo=UTC).timestamp())
        self.feed.day_ahead={hour:-1.5}
        with patch('sparc_controller.pricing.fetch_price',side_effect=OSError('offline')),patch('sparc_controller.pricing.fetch_json',side_effect=OSError('offline')),patch('sparc_controller.pricing.fetch_hourly_table',side_effect=OSError('offline')):
            await self.feed.refresh()
        self.assertEqual(self.feed.day_ahead,{hour:-1.5})
        self.assertIsNone(self.feed.state()['rateCents'])
        events=[json.loads(line) for line in self.log.read_text().splitlines()]
        self.assertEqual(len(events),5)
        self.assertTrue(all(event['type']=='feedError' for event in events))
        self.assertEqual(self.feed._next_day_ahead_refresh,self.now.timestamp()+60)

    async def test_not_published_table_has_no_invented_forecasts(self):
        raw=table(price=None)
        with patch('sparc_controller.pricing.fetch_hourly_table',side_effect=lambda day:(raw,parse_hourly_table(raw,day))):
            await self.feed.refresh_day_ahead()
        self.assertEqual(self.feed.day_ahead,{})
        self.assertEqual(self.feed.state()['dayAheadStatus'],'unavailable')
        self.assertTrue(all(value['status']=='notPublished' for value in self.feed.day_ahead_feeds.values()))
        self.assertEqual(self.feed._next_day_ahead_refresh,self.now.timestamp()+60)
