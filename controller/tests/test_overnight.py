import asyncio
from datetime import datetime,timedelta,timezone
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from sparc_controller.app_test import AppTestControl
from sparc_controller.pricing import ComEdPricing,hour_key
from sparc_controller.service import ControllerService,initialize

UTC=timezone.utc


class RealEnergyTests(unittest.TestCase):
    def setUp(self):
        self.now=datetime(2026,11,5,2,tzinfo=UTC)  # November 4, 8 PM Chicago
        self.feed=ComEdPricing(lambda:self.now)

    def samples(self,hour,rate,count=12):
        base=hour.timestamp()
        for i in range(count): self.feed.samples[int(base+i*300)]=rate

    def test_signed_real_prices_and_hour_boundary_assignment(self):
        self.samples(self.now,-2.4)
        cost=self.feed.energy_cost({hour_key(self.now.timestamp()):10})
        self.assertAlmostEqual(cost['cost'],-0.24)
        self.assertEqual(cost['unpricedEnergyKWh'],0)
        self.assertTrue(cost['final'])
        # Exactly hh:00 starts the new hour; hh:55 is its final labelled interval.
        self.assertIn(int(self.now.timestamp()),self.feed.samples)
        self.assertIn(int((self.now+timedelta(minutes=55)).timestamp()),self.feed.samples)

    def test_missing_tariff_is_unpriced_and_partial_hour_is_provisional(self):
        buckets={hour_key(self.now.timestamp()):10}
        self.assertEqual(self.feed.energy_cost(buckets)['unpricedEnergyKWh'],10)
        self.samples(self.now,1.2,count=3)
        self.assertAlmostEqual(self.feed.energy_cost(buckets)['cost'],0.12)
        self.assertFalse(self.feed.energy_cost(buckets)['final'])

    def test_late_real_data_reprices_existing_energy_without_fabrication(self):
        buckets={hour_key(self.now.timestamp()):10}
        self.samples(self.now,1.0,count=1)
        self.assertAlmostEqual(self.feed.energy_cost(buckets)['cost'],0.10)
        self.samples(self.now,2.0)
        self.assertAlmostEqual(self.feed.energy_cost(buckets)['cost'],0.20)
        self.assertTrue(self.feed.energy_cost(buckets)['final'])

    def test_overnight_twenty_to_ninety_and_hold_through_eight(self):
        settings={'weekly':{'targetPercent':90,'departureMinutes':480,'days':[1,2,3,4,5],
                            'enabled':False,'mode':'fixed','fixedStartMinutes':1200},
                  'today':{'targetPercent':90,'departure':'2026-11-05T08:00:00-06:00'},
                  'virtualCarEnabled':True,'appTestControl':{'manualState':None,'manualUntil':None},
                  'virtualCarState':{'batteryPercent':20.0,'pluggedIn':True,'energyKWh':0.0,'cost':0.0,'immediateCost':0.0,'energyByHour':{}}}
        control=AppTestControl(lambda:self.now,self.feed)
        self.assertEqual(control.state_at(settings,self.now-timedelta(seconds=1))[0],'waiting')
        self.assertEqual(control.state_at(settings,self.now)[0],'charging')
        for hour in range(7): self.samples(self.now+timedelta(hours=hour),2.0)
        end=self.now+timedelta(hours=12)-timedelta(seconds=1)
        with patch.object(control,'example_rate',side_effect=AssertionError('Example tariff used')):
            model=control.advance_virtual(settings,self.now-timedelta(hours=2),end)
        self.assertAlmostEqual(model['batteryPercent'],90)
        self.assertAlmostEqual(model['energyKWh'],70/0.9)
        self.assertAlmostEqual(model['cost'],(70/0.9)*0.02)
        self.assertEqual(settings['virtualCarState']['energyByHour'],{})
        self.assertEqual(control.state_at(dict(settings,virtualCarState=model),end)[0],'complete')
        self.assertAlmostEqual(sum(model['energyByHour'].values()),model['energyKWh'])

    def test_negative_cost_saved_state_is_valid_but_negative_energy_is_not(self):
        state={'batteryPercent':20,'pluggedIn':True,'energyKWh':1,'cost':-0.5,'immediateCost':0}
        self.assertEqual(AppTestControl.virtual_state({'virtualCarState':state})['cost'],-0.5)
        with self.assertRaises(ValueError): AppTestControl.virtual_state({'virtualCarState':dict(state,energyKWh=-1)})

    def test_history_cache_retains_real_samples(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'prices.json'
            sample={'price':'-2.4','millisUTC':str(int(self.now.timestamp()*1000))}
            path.write_text(json.dumps({'quote':[sample],'samples':[sample]}))
            loaded=ComEdPricing(lambda:self.now,cache=path)
            self.assertEqual(loaded.state()['rateCents'],-2.4)
            self.assertEqual(loaded.samples[int(self.now.timestamp())],-2.4)


class PersistenceTests(unittest.IsolatedAsyncioTestCase):
    async def test_progress_persists_without_display_or_phone(self):
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ,{'SPARC_REAL_PRICING':'1'}):
            path=Path(folder);initialize(path)
            now=datetime(2026,11,5,2,tzinfo=UTC)
            service=ControllerService(path,allow_wifi_provision=False,app_test=True,clock=lambda:now)
            service.settings['weekly'].update(mode='fixed',fixedStartMinutes=1200,departureMinutes=480,targetPercent=90,enabled=False)
            service.settings['today']={'targetPercent':90,'departure':'2026-11-05T08:00:00-06:00'}
            service.settings['virtualCarState'].update(batteryPercent=20,energyKWh=0,cost=0,immediateCost=0,energyByHour={})
            now+=timedelta(hours=1)
            async with service._lock: service.advance_virtual()
            saved=json.loads((path/'settings.json').read_text())
            self.assertAlmostEqual(saved['virtualCarState']['batteryPercent'],30.35)
            self.assertAlmostEqual(saved['virtualCarState']['energyKWh'],11.5)
            self.assertEqual(service.app_test.snapshot(service.settings)['unpricedEnergyKWh'],11.5)
