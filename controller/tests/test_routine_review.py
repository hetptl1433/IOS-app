"""Review regressions for one-night edits alongside the shared routine."""
import asyncio
from copy import deepcopy
from datetime import datetime
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from sparc_controller.campaign import DEFAULT_CAMPAIGN, arm_settings
from sparc_controller.routine import FIELDS, normalize, updated
import test_routine_controls as controls


def at(value):
    return datetime.fromisoformat(value)


class RoutineEditReviewTests(unittest.TestCase):
    def setUp(self):
        self.now = at('2026-11-05T23:00:00-06:00')
        settings = {'weekly': {'enabled':False, 'mode':'fixed', 'days':[1,2,3,4,5],
                                'targetPercent':80, 'departureMinutes':420, 'fixedStartMinutes':1320},
                    'today':None, 'campaign':normalize(DEFAULT_CAMPAIGN)}
        self.settings = arm_settings(settings, DEFAULT_CAMPAIGN, 1, at('2026-11-05T20:00:00-06:00'))
        self.settings['overnightRun'].update(start='2026-11-05T21:00:00-06:00',
                                              departure='2026-11-06T08:00:00-06:00', targetBatteryPercent=85)
        self.settings['virtualCarState'].update(batteryPercent=45, energyKWh=27.777,
                                                energyByHour={'2026-11-06T03:00:00+00:00':27.777})

    def body(self, **changes):
        campaign = self.settings['campaign']
        return {**{key:campaign[key] for key in FIELDS}, 'expectedRevision':campaign['revision'], **changes}

    def assert_current_retained(self, result):
        for key in ('start', 'departure', 'targetBatteryPercent', 'initialBatteryPercent'):
            self.assertEqual(result['overnightRun'][key], self.settings['overnightRun'][key], key)
        self.assertEqual(result['virtualCarState'], self.settings['virtualCarState'])

    def test_pause_after_screen_edited_start_keeps_current_target_window_and_ledger(self):
        before = deepcopy(self.settings)
        result = updated(self.settings, self.body(enabled=False), self.now)
        self.assertFalse(result['campaign']['enabled'])
        self.assert_current_retained(result)
        self.assertEqual(self.settings, before)

    def test_pause_before_screen_edited_start_keeps_screen_initial_battery(self):
        self.now = at('2026-11-05T20:00:00-06:00')
        self.settings['overnightRun']['initialBatteryPercent'] = 30
        self.settings['virtualCarState'].update(batteryPercent=30, energyKWh=0, energyByHour={})
        result = updated(self.settings, self.body(enabled=False), self.now)
        self.assert_current_retained(result)

    def test_resume_uses_extended_current_deadline_for_feasibility(self):
        self.now = at('2026-11-06T06:30:00-06:00')
        self.settings['campaign']['enabled'] = False
        self.settings['virtualCarState']['batteryPercent'] = 75
        result = updated(self.settings, self.body(enabled=True), self.now)
        self.assertTrue(result['campaign']['enabled'])
        self.assert_current_retained(result)

    def test_sleep_only_edit_preserves_absolute_one_night_day_offset(self):
        self.now = at('2026-11-05T20:00:00-06:00')
        self.settings['overnightRun'].update(start='2026-11-06T22:00:00-06:00', departure='2026-11-07T08:00:00-06:00')
        result = updated(self.settings, self.body(screenSleepMinutes=7), self.now)
        self.assert_current_retained(result)
        self.assertEqual(result['overnightRun']['screenSleepMinutes'], 7)

    def test_future_initial_edit_does_not_reapply_base_target_to_started_night(self):
        result = updated(self.settings, self.body(initialBatteryPercent=30), self.now)
        self.assertEqual(result['campaign']['initialBatteryPercent'], 30)
        self.assert_current_retained(result)

    def test_changed_departure_uses_actual_current_start_day(self):
        self.now = at('2026-11-05T20:00:00-06:00')
        self.settings['overnightRun'].update(start='2026-11-06T22:00:00-06:00', departure='2026-11-07T08:00:00-06:00')
        result = updated(self.settings, self.body(departureMinutes=9*60), self.now)
        self.assertEqual(result['overnightRun']['start'], '2026-11-06T22:00:00-06:00')
        self.assertEqual(result['overnightRun']['departure'], '2026-11-07T09:00:00-06:00')
        self.assertEqual(result['overnightRun']['targetBatteryPercent'], 85)
        self.assertEqual(result['virtualCarState'], self.settings['virtualCarState'])


class RoutineIntegrationReviewTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = controls.RoutineControlsTests.asyncSetUp
    body = controls.RoutineControlsTests.body
    exchange = controls.RoutineControlsTests.exchange

    async def test_concurrent_same_revision_saves_keep_last_accepted_edit(self):
        first, second = self.body(targetBatteryPercent=85), self.body(targetBatteryPercent=90)
        results = await asyncio.gather(self.service.dispatch('set_campaign', first, 'display'),
                                       self.service.dispatch('set_campaign', second, 'lan'), return_exceptions=True)
        self.assertEqual(sum(isinstance(item, Exception) for item in results), 0)
        self.assertEqual(self.service.settings['campaign']['revision'], 3)
        self.assertEqual(self.service.settings['campaign']['targetBatteryPercent'],90)
        self.assertEqual(self.service.settings['controlState']['sequence'],2)
        self.assertEqual(self.service.settings['virtualCarState']['batteryPercent'], 20)

    async def test_history_io_runs_on_worker_thread_with_detached_inputs(self):
        main_thread = threading.get_ident()
        captured = []

        def observe(settings, pricing, **kwargs):
            captured.append(threading.get_ident())
            settings['virtualCarState']['batteryPercent'] = 99
            return {'ok':True}

        with patch.object(self.service.history, 'view', side_effect=observe):
            self.assertEqual(await self.service.history_view(), {'ok':True})
        self.assertNotEqual(captured, [main_thread])
        self.assertEqual(self.service.settings['virtualCarState']['batteryPercent'], 20)

    async def test_real_eight_session_history_fits_encrypted_transport_frame(self):
        self.service.history.log_root = self.path/'logs'
        for index in range(8):
            day = 2+index % 4
            folder = self.path/'logs'/f'campaign-history-{index}'/f'night-2026-11-{day:02}'
            folder.mkdir(parents=True)
            config = dict(DEFAULT_CAMPAIGN, campaignId=f'campaign-history-{index}',
                          sessionId=f'history-{index}', nightDate=f'2026-11-{day:02}',
                          start=f'2026-11-{day:02}T20:00:00-06:00', departure=f'2026-11-{day+1:02}T07:00:00-06:00')
            (folder/'manifest.json').write_text(json.dumps(config))
            (folder/'summary.json').write_text(json.dumps({'manifest':config,
                'firstTargetObservedAt':config['departure'], 'finishedAt':config['departure'],
                'latest':{'timestamp':config['departure'], 'energyKWh':66.666667, 'batteryPercent':80,
                          'energyCostUSD':-2.123456, 'costAvailable':True, 'costFinal':True, 'unpricedEnergyKWh':0}}))
        result = await self.exchange('get_history')
        self.assertTrue(result['ok'])
        self.assertEqual(len(result['body']['sessions']), 8)
        self.assertIsNotNone(result['body']['nextCursor'])


if __name__ == '__main__':
    unittest.main()
