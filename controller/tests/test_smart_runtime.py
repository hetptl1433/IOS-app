"""Device-level smart charging with an injected clock and finite price fixtures."""
import asyncio
import copy
from datetime import datetime,timedelta,timezone
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from sparc_controller.__main__ import watch_virtual
from sparc_controller.app_test import schedule_goal
from sparc_controller.research.market import latest_visible_interval
from sparc_controller.service import ControllerService,atomic_save,initialize
from sparc_controller.smart_charging import SOURCE_COMMIT

UTC=timezone.utc
HOUR=3600
SLOT=300


def instant(text):return datetime.fromisoformat(text.replace('Z','+00:00'))


class SmartRuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.environment=patch.dict(os.environ,{'SPARC_REAL_PRICING':'1'})
        self.environment.start();self.addCleanup(self.environment.stop)
        os.environ.pop('SPARC_PRICE_LOG',None)
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.path=Path(self.temp.name);initialize(self.path)
        self.arrival=instant('2026-11-05T02:00:00Z')
        self.deadline=instant('2026-11-05T14:00:00Z')
        self.now=self.arrival-timedelta(minutes=5)
        weekly={'targetPercent':90,'departureMinutes':480,'days':[1,2,3,4,5],
                'enabled':False,'mode':'fixed','fixedStartMinutes':1200}
        config={'configuredAt':self.now.isoformat(),'start':'2026-11-04T20:00:00-06:00',
                'departure':'2026-11-05T08:00:00-06:00','initialBatteryPercent':20,'targetBatteryPercent':90,
                'screenSleepMinutes':5,'strategy':'day_ahead_live','previousWeekly':dict(weekly)}
        settings={'weekly':weekly,'today':{'targetPercent':90,'departure':config['departure']},
                  'overnightRun':config,'virtualCarEnabled':True,
                  'appTestControl':{'manualState':None,'manualUntil':None},
                  'virtualCarState':{'batteryPercent':20.0,'pluggedIn':True,'energyKWh':0.0,'cost':0.0,'immediateCost':0.0,'energyByHour':{}}}
        atomic_save(self.path/'settings.json',settings)
        # A deterministic supplied fit avoids training on, or requesting, external data.
        (self.path/'live-decay.json').write_text(json.dumps({'sourceCommit':SOURCE_COMMIT,'decay':[0.0]*24}))
        first=int(self.arrival.timestamp())-72*HOUR
        self.fixture={hour:2.0 for hour in range(first,int(self.deadline.timestamp())+24*HOUR,HOUR)}
        for offset,price in enumerate([10.0,1.0,8.0,1.0,8.0,1.0,8.0,1.0,8.0,1.0,8.0,1.0]):
            self.fixture[int(self.arrival.timestamp())+offset*HOUR]=price
        self.service=self.make_service()
        self.service.refresh_smart_plan(force=True)

    def make_service(self):
        service=ControllerService(self.path,allow_wifi_provision=False,app_test=True,clock=lambda:self.now)
        service.pricing.clock=lambda:self.now
        service.pricing.day_ahead=dict(self.fixture)
        self.publish(service)
        return service

    def publish(self,service=None):
        service=service or self.service
        visible=latest_visible_interval(int(self.now.timestamp()))
        first=int(self.arrival.timestamp())-48*HOUR
        service.pricing.samples={stamp:self.fixture.get(stamp//HOUR*HOUR,2.0) for stamp in range(first,visible+SLOT,SLOT)}
        service.pricing.settled_hourly={hour:price for hour,price in self.fixture.items() if hour+HOUR<=self.now.timestamp()}
        service.pricing.price=self.fixture.get(int(self.now.timestamp())//HOUR*HOUR,2.0)
        service.pricing.stamp=float(visible)

    def body(self,**changes):
        result={'dayOffset':0,'startMinutes':1200,'departureMinutes':480,'initialBatteryPercent':20,'targetPercent':90,'screenSleepMinutes':5}
        result.update(changes)
        # After midnight the unchanged overnight start belongs to the previous date.
        result['dayOffset']=(self.arrival.date()-self.now.date()).days
        # UTC and Chicago cross midnight at different instants.
        from sparc_controller.app_test import CHICAGO
        result['dayOffset']=(self.arrival.astimezone(CHICAGO).date()-self.now.astimezone(CHICAGO).date()).days
        return result

    async def tick(self,when,replan=True):
        self.now=when;self.publish()
        async with self.service._lock:
            self.service.advance_virtual()
            if replan:self.service.refresh_smart_plan()
        return self.service.app_test.snapshot(self.service.settings)

    async def test_full_overnight_replanning_reaches_ninety_with_price_based_pauses(self):
        self.assertEqual(self.service.settings['virtualCarState']['batteryPercent'],20)
        self.assertEqual(self.service.app_test.state_at(self.service.settings,self.now)[0],'waiting')
        states=[];cheap_on=False;expensive_off=False
        when=self.arrival
        while when<=self.deadline:
            snapshot=await self.tick(when)
            if when<self.deadline:
                states.append(snapshot['state'])
                relative_hour=int((when-self.arrival).total_seconds()//HOUR)
                cheap_on=cheap_on or (relative_hour==1 and snapshot['state']=='charging')
                expensive_off=expensive_off or (relative_hour==0 and snapshot['state']=='waiting')
            when+=timedelta(minutes=5)
        model=self.service.settings['virtualCarState']
        self.assertTrue(cheap_on)
        self.assertTrue(expensive_off)
        self.assertGreaterEqual(sum(a!=b for a,b in zip(states,states[1:])),4)
        self.assertAlmostEqual(model['batteryPercent'],90,places=6)
        self.assertAlmostEqual(model['energyKWh'],70/0.9,places=6)
        self.assertEqual(snapshot['state'],'complete')
        self.assertAlmostEqual(sum(model['energyByHour'].values()),model['energyKWh'],places=6)
        immediate=0.0;left=70/0.9
        for offset in range(12):
            amount=min(left,11.5);immediate+=amount*self.fixture[int(self.arrival.timestamp())+offset*HOUR]/100;left-=amount
            if left<=1e-8:break
        self.assertLess(model['cost'],immediate*0.75)
        saved=json.loads((self.path/'settings.json').read_text())
        self.assertAlmostEqual(saved['virtualCarState']['batteryPercent'],90,places=6)

    async def test_committed_past_interval_is_not_rewritten_by_new_prices(self):
        await self.tick(self.arrival+timedelta(hours=1))
        self.assertEqual(self.service.app_test.state_at(self.service.settings,self.now)[0],'charging')
        old_plan=copy.deepcopy(self.service.settings['smartChargePlan'])
        old_energy=self.service.settings['virtualCarState']['energyKWh']
        self.service.pricing.day_ahead[int(self.now.timestamp())]=50.0
        await self.tick(self.now+timedelta(minutes=5),replan=False)
        self.assertAlmostEqual(self.service.settings['virtualCarState']['energyKWh']-old_energy,11.5/12)
        self.assertEqual(self.service.settings['smartChargePlan'],old_plan)
        self.service.refresh_smart_plan(force=True)
        self.assertEqual(self.service.app_test.state_at(self.service.settings,self.now)[0],'waiting')

    async def test_restart_does_not_count_controller_downtime_as_charging(self):
        await self.tick(self.arrival+timedelta(hours=1))
        await self.tick(self.now+timedelta(minutes=5))
        previous=copy.deepcopy(self.service.settings['virtualCarState'])
        self.now+=timedelta(minutes=30)
        self.service=self.make_service()
        self.service.refresh_smart_plan(force=True,cause='restart')
        self.assertEqual(self.service.display_state()['batteryPercent'],previous['batteryPercent'])
        self.assertEqual(self.service.settings['virtualCarState']['energyKWh'],previous['energyKWh'])
        await self.tick(self.now+timedelta(minutes=5))
        self.assertAlmostEqual(self.service.settings['virtualCarState']['energyKWh']-previous['energyKWh'],11.5/12)

    async def test_production_watch_loop_replans_immediately_after_same_slot_restart(self):
        await self.tick(self.arrival+timedelta(hours=1,minutes=5))
        prior_battery=self.service.settings['virtualCarState']['batteryPercent']
        prior_energy=self.service.settings['virtualCarState']['energyKWh']
        old_plan=copy.deepcopy(self.service.settings['smartChargePlan'])
        self.now+=timedelta(seconds=30)
        self.service=self.make_service()
        self.assertEqual(old_plan['decisionSlot'],int(self.now.timestamp())//SLOT)
        # Exercise the actual production watcher, not an explicitly forced test-only call.
        with patch('sparc_controller.__main__.asyncio.sleep',side_effect=asyncio.CancelledError):
            with self.assertRaises(asyncio.CancelledError):
                await watch_virtual(self.service)
        updated=self.service.settings['smartChargePlan']
        self.assertNotEqual(updated['generatedAt'],old_plan['generatedAt'])
        self.assertEqual(instant(updated['generatedAt']),self.now)
        self.assertEqual(self.service.settings['virtualCarState']['batteryPercent'],prior_battery)
        self.assertEqual(self.service.settings['virtualCarState']['energyKWh'],prior_energy)

    async def test_manual_pause_and_resume_returns_to_price_optimizer(self):
        await self.tick(self.arrival)
        self.assertEqual(self.service.app_test.state_at(self.service.settings,self.now)[0],'waiting')
        now_reply=await self.service.dispatch('charge_now',{},'display')
        self.assertEqual(now_reply['state'],'charging')
        await self.tick(self.now+timedelta(minutes=5))
        paused=await self.service.dispatch('pause',{},'display')
        self.assertEqual(paused['state'],'paused')
        energy=self.service.settings['virtualCarState']['energyKWh']
        await self.tick(self.now+timedelta(minutes=5))
        self.assertEqual(self.service.settings['virtualCarState']['energyKWh'],energy)
        resumed=await self.service.dispatch('resume',{},'display')
        self.assertEqual(self.service.app_test.control_state(self.service.settings)[0],None)
        self.assertEqual(resumed['state'],'waiting')
        self.assertEqual(self.service.settings['smartChargePlan']['cause'],'manualCommand:resume')

    async def test_eight_am_is_real_deadline_not_seven_thirty(self):
        self.now=self.deadline-timedelta(minutes=30)
        self.service._virtual_anchor=self.now
        self.service.settings['virtualCarState'].update(batteryPercent=85,energyKWh=0,cost=0,immediateCost=0,energyByHour={})
        self.publish();self.service.refresh_smart_plan(force=True)
        goal=schedule_goal(self.service.settings,self.now)
        self.assertEqual(goal.ready_by,self.deadline)
        self.assertEqual(goal.departure,self.deadline)
        states=[]
        for minutes in range(0,31,5):
            snapshot=await self.tick(self.deadline-timedelta(minutes=30-minutes))
            states.append(snapshot['state'])
        self.assertIn('charging',states)
        self.assertAlmostEqual(self.service.settings['virtualCarState']['batteryPercent'],90,places=6)
        self.assertEqual(snapshot['state'],'complete')

    async def test_screen_target_change_replans_atomically_without_resetting_battery(self):
        await self.tick(self.arrival+timedelta(hours=1))
        await self.tick(self.now+timedelta(minutes=15))
        before=copy.deepcopy(self.service.settings['virtualCarState'])
        with patch('sparc_controller.service.atomic_save',wraps=atomic_save) as saved:
            await self.service.dispatch('set_overnight',self.body(targetPercent=95),'display')
        self.assertEqual(saved.call_count,1)
        self.assertEqual(self.service.settings['virtualCarState'],before)
        self.assertEqual(self.service.settings['smartChargePlan']['goalSignature'][2],95)
        self.assertEqual(self.service.settings['smartChargePlan']['cause'],'screenSettingsChanged')
        disk=json.loads((self.path/'settings.json').read_text())
        self.assertEqual(disk['overnightRun']['targetBatteryPercent'],95)
        self.assertEqual(disk['smartChargePlan']['goalSignature'][2],95)

    async def test_screen_sleep_only_change_keeps_manual_pause_and_charge_progress(self):
        await self.tick(self.arrival)
        await self.service.dispatch('pause',{},'display')
        before=copy.deepcopy(self.service.settings['virtualCarState'])
        await self.service.dispatch('set_overnight',self.body(screenSleepMinutes=10),'display')
        self.assertEqual(self.service.settings['overnightRun']['screenSleepMinutes'],10)
        self.assertEqual(self.service.app_test.control_state(self.service.settings)[0],'paused')
        self.assertEqual(self.service.settings['virtualCarState'],before)
        self.assertEqual(self.service.app_test.snapshot(self.service.settings)['state'],'paused')

    async def test_failed_screen_save_keeps_the_old_goal_and_committed_plan(self):
        before=copy.deepcopy(self.service.settings)
        disk=(self.path/'settings.json').read_bytes()
        with patch('sparc_controller.service.atomic_save',side_effect=OSError('unavailable disk')):
            with self.assertRaises(OSError):await self.service.dispatch('set_overnight',self.body(targetPercent=95),'display')
        self.assertEqual(self.service.settings,before)
        self.assertEqual((self.path/'settings.json').read_bytes(),disk)

    async def test_replanning_is_once_per_five_minute_slot(self):
        old=copy.deepcopy(self.service.settings['smartChargePlan'])
        self.now+=timedelta(seconds=30)
        self.service.refresh_smart_plan()
        self.assertEqual(self.service.settings['smartChargePlan'],old)
        await self.tick(self.arrival)
        self.assertGreater(self.service.settings['smartChargePlan']['decisionSlot'],old['decisionSlot'])
        self.assertEqual(self.service.settings['smartChargePlan']['cause'],'fiveMinuteReplan')


if __name__=='__main__':unittest.main()
