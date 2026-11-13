from datetime import datetime,timezone
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from sparc_controller.service import ControllerService, initialize
import test_display as display_fixtures

UTC=timezone.utc


class NightControlTests(unittest.IsolatedAsyncioTestCase):
    serve = display_fixtures.DisplayTests.serve
    asyncTearDown = display_fixtures.DisplayTests.asyncTearDown
    request = display_fixtures.DisplayTests.request
    post = display_fixtures.DisplayTests.post
    state = display_fixtures.DisplayTests.state
    async def asyncSetUp(self):
        await display_fixtures.DisplayTests.asyncSetUp(self)
        self.now=datetime(2026,11,5,0,tzinfo=UTC)  # November 4, 18:00 Chicago
        self.service._virtual_anchor=self.now
        self.service.settings['virtualCarState'].update(batteryPercent=20.0,energyKWh=0.0,cost=0.0,immediateCost=0.0,energyByHour={})
        self.service.settings['overnightRun']={
            'configuredAt':self.now.isoformat(),'start':'2026-11-04T20:00:00-06:00',
            'departure':'2026-11-05T08:00:00-06:00','initialBatteryPercent':20,'targetBatteryPercent':90,
            'previousWeekly':dict(self.service.settings['weekly'])}
        self.body={'dayOffset':0,'startMinutes':1200,'departureMinutes':480,'initialBatteryPercent':20,'targetPercent':90,'screenSleepMinutes':15}

    async def test_onscreen_change_updates_atomic_schedule_and_initial_charge(self):
        code,state=await self.post('/api/overnight',dict(self.body,startMinutes=1215,targetPercent=85,initialBatteryPercent=25,screenSleepMinutes=20))
        self.assertEqual(code,200)
        self.assertEqual(state['overnight']['revision'],1)
        self.assertEqual(state['overnight']['start'],'2026-11-04T20:15:00-06:00')
        self.assertEqual(state['overnight']['departure'],'2026-11-05T08:00:00-06:00')
        self.assertEqual(state['batteryPercent'],25)
        self.assertEqual(state['snapshot']['targetPercent'],85)
        self.assertEqual(state['snapshot']['state'],'waiting')
        self.assertEqual(state['overnight']['screenSleepMinutes'],20)
        saved=json.loads((self.path/'settings.json').read_text())
        self.assertFalse(saved['weekly']['enabled'])
        self.assertEqual(saved['today']['targetPercent'],85)
        original=state['overnight']['previousWeekly']
        await self.post('/api/overnight',dict(self.body,targetPercent=95,initialBatteryPercent=25))
        self.assertEqual((await self.state())['overnight']['previousWeekly'],original)

    async def test_rejects_infeasible_window_and_invalid_fields_without_changing_plan(self):
        original=dict(self.service.settings['overnightRun'])
        invalid=[dict(self.body,initialBatteryPercent=95),dict(self.body,targetPercent=True),
                 dict(self.body,screenSleepMinutes=0),dict(self.body,startMinutes=60),
                 dict(self.body,departureMinutes=1320),dict(self.body,dayOffset=2)]
        for body in invalid:
            code,_=await self.post('/api/overnight',body)
            self.assertEqual(code,400,body)
            self.assertEqual(self.service.settings['overnightRun'],original)

    async def test_target_changes_during_charge_preserve_progress_and_lock_initial(self):
        self.now=datetime(2026,11,5,3,tzinfo=UTC)  # 21:00 Chicago
        self.service._virtual_anchor=self.now
        self.service.settings['virtualCarState'].update(batteryPercent=30.35,energyKWh=11.5)
        code,state=await self.post('/api/overnight',dict(self.body,targetPercent=95))
        self.assertEqual(code,200)
        self.assertEqual(state['batteryPercent'],30.35)
        self.assertEqual(state['snapshot']['energyKWh'],11.5)
        code,_=await self.post('/api/overnight',dict(self.body,initialBatteryPercent=25))
        self.assertEqual(code,400)
        self.assertEqual(self.service.settings['virtualCarState']['batteryPercent'],30.35)

    async def test_failed_save_does_not_acknowledge_or_change_schedule(self):
        original=dict(self.service.settings['overnightRun'])
        with patch('sparc_controller.service.atomic_save',side_effect=OSError('disk unavailable')):
            code,_=await self.post('/api/overnight',dict(self.body,targetPercent=95))
        self.assertEqual(code,500)
        self.assertEqual(self.service.settings['overnightRun'],original)

    async def test_new_controls_reject_cross_origin_posts(self):
        code,_=await self.post('/api/overnight',self.body,{'Origin':'https://untrusted.example'})
        self.assertEqual(code,403)
