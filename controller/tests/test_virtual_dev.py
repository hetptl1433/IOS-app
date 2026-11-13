from copy import deepcopy
from datetime import datetime
import json
from pathlib import Path
import tempfile
import unittest

from sparc_controller.app_test import AppTestControl
from sparc_controller.history import HistoryStore
from sparc_controller.pricing import ComEdPricing
from sparc_controller.virtual_dev import updated, view


def at(value):
    return datetime.fromisoformat(value.replace('Z', '+00:00'))


class VirtualDeveloperTests(unittest.TestCase):
    def setUp(self):
        self.now = at('2026-11-06T06:00:00Z')
        self.settings = {
            'virtualCarEnabled':True,
            'weekly':{'enabled':False, 'mode':'fixed', 'days':[1,2,3,4,5], 'targetPercent':80,
                      'fixedStartMinutes':1320, 'departureMinutes':420},
            'today':{'targetPercent':80, 'departure':'2026-11-06T07:00:00-06:00'},
            'campaign':{'enabled':True},
            'overnightRun':{'campaignId':'campaign-2026-11-05', 'sessionId':'campaign-2026-11-05:2026-11-05',
                            'nightDate':'2026-11-05', 'initialBatteryPercent':20, 'targetBatteryPercent':80,
                            'start':'2026-11-05T22:00:00-06:00', 'departure':'2026-11-06T07:00:00-06:00',
                            'strategy':'day_ahead_live'},
            'virtualCarState':{'batteryPercent':45.0, 'pluggedIn':True, 'energyKWh':23.0,
                               'cost':0.23, 'immediateCost':0.0,
                               'energyByHour':{'2026-11-06T04:00:00+00:00':11.5, '2026-11-06T05:00:00+00:00':11.5},
                               'retainedCustomCounter':17},
            'appTestControl':{'manualState':'paused', 'manualUntil':None},
            'smartChargePlan':{'stale':'plan'},
        }

    def test_calibration_preserves_every_energy_counter_schedule_and_manual_control(self):
        before = deepcopy(self.settings)
        result = updated(self.settings, {'batteryPercent':20, 'pluggedIn':False}, self.now)
        expected_model = dict(before['virtualCarState'], batteryPercent=20.0, pluggedIn=False)
        self.assertEqual(result['virtualCarState'], expected_model)
        for key in ('weekly', 'today', 'overnightRun', 'campaign', 'appTestControl'):
            self.assertEqual(result[key], before[key], key)
        self.assertNotIn('smartChargePlan', result)
        self.assertEqual(self.settings, before)
        result['virtualCarState']['energyByHour'].clear()
        self.assertEqual(self.settings, before)

    def test_view_marks_simulated_calibration_and_persists_adjustment_history(self):
        first = updated(self.settings, {'batteryPercent':20.25, 'pluggedIn':False}, self.now)
        second = updated(first, {'batteryPercent':100, 'pluggedIn':True}, self.now)
        self.assertEqual(second['developerState']['revision'], 2)
        history = second['developerState']['adjustments']
        self.assertEqual(len(history), 2)
        self.assertEqual(history[0], first['developerState']['lastAdjustment'])
        self.assertEqual(history[1]['from'], {'batteryPercent':20.25, 'pluggedIn':False})
        self.assertEqual(history[1]['to'], {'batteryPercent':100.0, 'pluggedIn':True})
        self.assertEqual(view(second, self.now), {'enabled':True, 'simulated':True, 'batteryPercent':100.0,
            'pluggedIn':True, 'revision':2, 'updatedAt':'2026-11-06T06:00:00Z', 'manualCalibration':True})

    def test_unchanged_state_is_a_noop_and_soc_endpoints_are_valid(self):
        self.assertEqual(updated(self.settings, {'batteryPercent':45, 'pluggedIn':True}, self.now), self.settings)
        for battery in (0, 100, 0.125):
            result = updated(self.settings, {'batteryPercent':battery, 'pluggedIn':True}, self.now)
            self.assertEqual(result['virtualCarState']['batteryPercent'], battery)

    def test_invalid_numbers_booleans_fields_and_disabled_virtual_car_are_rejected(self):
        for battery in (-1, 101, True, None, '20', float('nan'), float('inf'), -float('inf')):
            with self.subTest(battery=battery), self.assertRaises(ValueError):
                updated(self.settings, {'batteryPercent':battery, 'pluggedIn':True}, self.now)
        for plugged in (0, 1, None, 'true'):
            with self.subTest(plugged=plugged), self.assertRaises(ValueError):
                updated(self.settings, {'batteryPercent':20, 'pluggedIn':plugged}, self.now)
        for body in ({}, {'batteryPercent':20}, {'batteryPercent':20, 'pluggedIn':True, 'cost':0}):
            with self.assertRaises(ValueError):
                updated(self.settings, body, self.now)
        with self.assertRaises(ValueError):
            updated(dict(self.settings, virtualCarEnabled=False), {'batteryPercent':20, 'pluggedIn':True}, self.now)
        with self.assertRaises(ValueError):
            updated(self.settings, {'batteryPercent':20, 'pluggedIn':True}, self.now.replace(tzinfo=None))

    def test_unplug_stops_and_replug_keeps_user_pause_intent(self):
        model = AppTestControl(lambda:self.now)
        disconnected = updated(self.settings, {'batteryPercent':30, 'pluggedIn':False}, self.now)
        self.assertEqual(model.state_at(disconnected, self.now)[0], 'unplugged')
        plugged = updated(disconnected, {'batteryPercent':30, 'pluggedIn':True}, self.now)
        self.assertEqual(model.state_at(plugged, self.now)[0], 'paused')
        following = deepcopy(plugged)
        following['appTestControl'] = {'manualState':None, 'manualUntil':None}
        # With no manual override the real optimizer supplies the charging policy.
        following['smartChargePlan'] = {'intervals':[]}
        self.assertEqual(model.state_at(following, self.now)[0], 'waiting')

    def make_store(self, root):
        return HistoryStore(root/'state', root/'logs', lambda:self.now)

    def test_completed_ledger_keeps_original_soc_and_deadline_outcome_after_adjustment(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = self.make_store(root)
            completed = deepcopy(self.settings)
            completed['virtualCarState']['batteryPercent'] = 80
            self.now = at('2026-11-06T12:59:00Z')
            store.capture(completed, None, force=True)
            completed['overnightRun']['finalizedAt'] = '2026-11-06T13:06:00Z'
            self.now = at('2026-11-06T14:00:00Z')
            store.capture(completed, None, force=True)  # root's required pre-adjustment archive
            expected = store.view(completed, None)['sessions'][0]
            changed = updated(completed, {'batteryPercent':20, 'pluggedIn':False}, self.now)
            store.capture(changed, None, force=True)
            store = self.make_store(root)
            actual = store.view(changed, None)['sessions'][0]
            self.assertEqual(actual, expected)
            self.assertEqual(actual['batteryPercent'], 80)
            self.assertTrue(actual['targetReachedByDeadline'])

    def test_finished_first_archive_is_allowed_once_but_later_calibration_cannot_rewrite_it(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = self.make_store(root)
            completed = deepcopy(self.settings)
            completed['overnightRun']['finalizedAt'] = '2026-11-06T13:06:00Z'
            completed['virtualCarState']['batteryPercent'] = 80
            self.now = at('2026-11-06T14:00:00Z')
            self.assertEqual(store.capture(completed, None, force=True), 1)
            expected = store.view(completed, None)['sessions'][0]
            changed = updated(completed, {'batteryPercent':0, 'pluggedIn':True}, self.now)
            store.capture(changed, None, force=True)
            self.assertEqual(store.view(changed, None)['sessions'][0], expected)

    def test_missing_completed_archive_is_not_invented_from_post_finish_calibration(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.now = at('2026-11-06T14:00:00Z')
            completed = deepcopy(self.settings)
            completed['overnightRun']['finalizedAt'] = '2026-11-06T13:06:00Z'
            changed = updated(completed, {'batteryPercent':20, 'pluggedIn':False}, self.now)
            store = self.make_store(root)
            self.assertEqual(store.capture(changed, None, force=True), 0)
            self.assertEqual(store.view(changed, None)['sessions'], [])

    def test_imported_completed_log_remains_authoritative_and_real_cost_stays_signed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            completed = deepcopy(self.settings)
            config = completed['overnightRun']
            config['finalizedAt'] = '2026-11-06T13:06:00Z'
            folder = root/'logs'/config['campaignId']/'night-2026-11-05'
            folder.mkdir(parents=True)
            (folder/'manifest.json').write_text(json.dumps(config))
            (folder/'summary.json').write_text(json.dumps({'manifest':config, 'firstTargetObservedAt':'2026-11-06T12:59:00Z',
                'latest':{'timestamp':'2026-11-06T13:05:00Z', 'batteryPercent':80, 'energyKWh':23,
                          'costAvailable':True, 'costFinal':True, 'unpricedEnergyKWh':0, 'energyCostUSD':-0.23}}))
            self.now = at('2026-11-06T14:00:00Z')
            changed = updated(completed, {'batteryPercent':20, 'pluggedIn':True}, self.now)
            row = self.make_store(root).view(changed, None)['sessions'][0]
            self.assertEqual(row['batteryPercent'], 80)
            self.assertTrue(row['targetReachedByDeadline'])
            self.assertEqual(row['costUSD'], -0.23)

    def test_active_calibration_is_annotated_without_inventing_energy_or_real_cost(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = self.make_store(root)
            pricing = ComEdPricing(lambda:self.now)
            for hour in (4, 5):
                pricing.settled_hourly[int(at(f'2026-11-06T{hour:02}:00:00Z').timestamp())] = -1
            store.capture(self.settings, pricing, force=True)
            before = store.view(self.settings, pricing)['sessions'][0]
            calibrated = updated(self.settings, {'batteryPercent':20, 'pluggedIn':False}, self.now)
            store.capture(calibrated, pricing, force=True)
            after = store.view(calibrated, pricing)['sessions'][0]
            self.assertEqual(after['batteryPercent'], 20)
            self.assertTrue(after['manualCalibration'])
            for key in ('energyKWh', 'costUSD', 'baselineCostUSD', 'savingsUSD'):
                self.assertEqual(after[key], before[key], key)


if __name__ == '__main__':
    unittest.main()
