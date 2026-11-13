"""Tesla charging history: recorded from SPARC's own Start/Stop and bounded reads, kept apart from the virtual car."""
import base64
from copy import deepcopy
from datetime import timedelta
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from sparc_controller.history import HistoryStore, instant
from sparc_controller.pricing import ComEdPricing
from sparc_controller.tesla_history import (MAX_FAILURES, POLL_SECONDS, TeslaSessionStore, measured_energy)
import test_connections_service as connections

VEHICLE = {'id': '123', 'name': 'Actual Fleet vehicle'}


def reading(at, state, power, energy, battery=50, target=80):
    value = {'batteryPercent': battery, 'pluggedIn': state != 'unplugged', 'powerKW': power, 'state': state,
             'updatedAt': at, 'timestampSource': 'vehicle', 'targetPercent': target, 'source': 'TeslaFleetAPI'}
    if energy is not None:
        value['energyAddedKWh'] = energy
    return value


def counter(at, energy, state='charging', power=7.2):
    return {'at': at, 'state': state, 'powerKW': power, 'batteryPercent': 50, 'energyAddedKWh': energy, 'targetPercent': 80}


class TeslaSessionStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.state = Path(self.temp.name) / 'state'
        self.now = instant('2026-11-10T04:00:00Z')
        self.history = HistoryStore(self.state, Path(self.temp.name) / 'logs', lambda: self.now)
        self.store = self.history.tesla
        self.pricing = ComEdPricing(lambda: self.now)
        for hour, cents in ((4, 3.0), (5, 5.0)):
            self.pricing.settled_hourly[int(instant(f'2026-11-10T{hour:02}:00:00Z').timestamp())] = cents

    def at(self, value):
        self.now = instant(value)
        return value

    def start(self, before_energy=12.3, after=True):
        before = reading('2026-11-10T03:59:30Z', 'paused', 0, before_energy, battery=40)
        later = reading('2026-11-10T04:00:10Z', 'waiting', 0, before_energy, battery=40) if after else None
        return self.store.open_session(VEHICLE, before, later, self.at('2026-11-10T04:00:20Z'), pricing=self.pricing)

    def poll(self, at, state, power, energy, battery):
        self.at(at)
        return self.store.observe('123', reading(at, state, power, energy, battery), self.now, polled=True, pricing=self.pricing)

    def page(self, **kwargs):
        return self.history.view({}, self.pricing, now=self.now, car='tesla', **kwargs)

    def test_start_polls_and_completion_record_a_priced_tesla_session(self):
        self.assertIsNotNone(self.start())
        self.assertIsNone(self.store.poll_due(instant('2026-11-10T04:15:19Z')))
        self.assertEqual(self.store.poll_due(instant('2026-11-10T04:15:20Z')), '123')
        # The car reset its counter when charging began, then added energy at 7.2 kW.
        for minute, energy, battery in ((15, 1.5, 42), (30, 3.3, 45), (45, 5.1, 48)):
            self.poll(f'2026-11-10T04:{minute}:20Z', 'charging', 7.2, energy, battery)
        self.poll('2026-11-10T05:00:20Z', 'charging', 7.2, 6.9, 51)
        self.poll('2026-11-10T05:15:20Z', 'complete', 0, 7.5, 80)
        self.assertIsNone(self.store.poll_due(self.now + timedelta(hours=2)))
        record = json.loads(next((self.state/'history'/'tesla').glob('*.json')).read_text())
        self.assertEqual(record['car'], {'kind': 'tesla', 'vehicleId': '123'})
        self.assertAlmostEqual(record['energyByHour']['2026-11-10T04:00:00+00:00'], 6.9 - 7.2 * 20 / 3600)
        page = self.page()
        self.assertEqual(page['car'], {'kind': 'tesla', 'vehicleId': None})
        self.assertEqual(page['benchmark'], 'none')
        session = page['sessions'][0]
        self.assertEqual((session['status'], session['endReason'], session['end']), ('complete', 'limit_reached', '2026-11-10T05:15:20Z'))
        self.assertEqual((session['initialBatteryPercent'], session['batteryPercent'], session['targetBatteryPercent']), (40, 80, 80))
        self.assertEqual(session['energyKWh'], 7.5)
        self.assertEqual(session['energySource'], 'vehicle_reported')
        self.assertFalse(session['simulated'])
        self.assertIsNone(session['departure'])
        self.assertTrue(session['costFinal'])
        self.assertFalse(session['provisional'])
        hour4 = 6.9 - 7.2 * 20 / 3600
        self.assertAlmostEqual(session['costUSD'], round(hour4 * 0.03 + (7.5 - hour4) * 0.05, 6))
        # A manual charge has no shifted baseline, so no savings are claimed.
        for key in ('baselineCostUSD', 'savingsUSD', 'savingsPercent', 'targetReachedByDeadline'):
            self.assertIsNone(session[key])
        week = page['week']
        self.assertEqual((week['pricedSessions'], week['unpricedSessions'], week['energyKWh']), (1, 0, 7.5))
        self.assertEqual(week['costUSD'], session['costUSD'])
        self.assertIsNone(week['savingsPercent'])
        self.assertEqual(next(day for day in week['days'] if day['sessionCount'])['date'], '2026-11-09')

    def test_virtual_history_is_unchanged_and_never_lists_tesla_sessions(self):
        settings = {'overnightRun': {'initialBatteryPercent': 20, 'targetBatteryPercent': 80, 'nightDate': '2026-11-09',
                                     'start': '2026-11-09T22:00:00-06:00', 'departure': '2026-11-10T07:00:00-06:00'},
                    'virtualCarEnabled': True,
                    'virtualCarState': {'batteryPercent': 60, 'energyKWh': 11.5,
                                        'energyByHour': {'2026-11-10T04:00:00+00:00': 11.5}}}
        self.history.capture(settings, self.pricing, now=self.now, force=True)
        before = self.history.view(settings, self.pricing, now=self.now)
        self.start()
        self.poll('2026-11-10T04:15:20Z', 'charging', 7.2, 1.5, 42)
        after = self.history.view(settings, self.pricing, now=self.now)
        self.assertEqual(before['sessions'], after['sessions'])
        self.assertEqual(before['week'], after['week'])
        self.assertEqual(after['benchmark'], 'immediate_same_energy')
        self.assertTrue(all(row['car'] == {'kind': 'virtual'} and row['simulated'] for row in after['sessions']))
        teslas = self.page()['sessions']
        self.assertEqual([row['car']['kind'] for row in teslas], ['tesla'])
        self.assertEqual(len(list((self.state/'history').glob('*.json'))), 1)

    def test_counter_resets_and_rounding_never_count_more_than_the_car_reported(self):
        rows = [counter('2026-11-10T04:00:00Z', 5.0, 'paused', 0), counter('2026-11-10T04:10:00Z', 0.2),
                counter('2026-11-10T04:20:00Z', 3.0), counter('2026-11-10T04:30:00Z', 2.98),
                counter('2026-11-10T04:40:00Z', 4.0)]
        energy, buckets = measured_energy(rows)
        self.assertAlmostEqual(energy, 4.0)
        self.assertAlmostEqual(sum(buckets.values()), 4.0)
        self.assertEqual(measured_energy(rows[:1]), (None, {}))

    def test_missing_counter_stays_unavailable_never_zero_or_estimated(self):
        self.start(before_energy=None)
        self.poll('2026-11-10T04:15:20Z', 'charging', 7.2, None, 45)
        session = self.page()['sessions'][0]
        self.assertEqual(session['status'], 'active')
        self.assertIsNone(session['energyKWh'])
        self.assertIsNone(session['costUSD'])
        self.assertIsNone(session['unpricedEnergyKWh'])
        week = self.page()['week']
        self.assertEqual((week['energyKWh'], week['unpricedSessions'], week['pricedSessions']), (0, 1, 0))
        self.assertIsNone(week['costUSD'])

    def test_sparc_stop_closes_and_a_failed_final_read_marks_energy_partial(self):
        self.start()
        self.poll('2026-11-10T04:15:20Z', 'charging', 7.2, 1.5, 42)
        self.store.stopped('123', None, self.at('2026-11-10T04:20:00Z'), pricing=self.pricing)
        session = self.page()['sessions'][0]
        self.assertEqual((session['status'], session['endReason'], session['end']), ('incomplete', 'stopped', '2026-11-10T04:20:00Z'))
        self.assertTrue(session['energyPartial'])
        self.assertEqual(session['energyKWh'], 1.5)
        self.assertIsNone(self.store.poll_due(self.now + timedelta(hours=1)))

    def test_repeated_read_failures_stop_polling_and_close_without_inventing_energy(self):
        self.start()
        self.poll('2026-11-10T04:15:20Z', 'charging', 7.2, 1.5, 42)
        moment = self.now
        for _ in range(MAX_FAILURES):
            moment += timedelta(seconds=POLL_SECONDS)
            self.assertEqual(self.store.poll_due(moment), '123')
            self.store.read_failed('123', moment)
        self.assertIsNone(self.store.poll_due(moment + timedelta(hours=1)))
        session = self.page()['sessions'][0]
        self.assertEqual((session['endReason'], session['end'], session['energyKWh']), ('not_observed', '2026-11-10T04:15:20Z', 1.5))
        self.assertTrue(session['energyPartial'])

    def test_post_start_paused_reading_does_not_end_but_later_states_do(self):
        first = self.start()
        rows = lambda: {row['id']: row for row in self.page()['sessions']}
        self.store.observe('123', reading('2026-11-10T04:00:40Z', 'paused', 0, 12.3), self.at('2026-11-10T04:00:45Z'))
        self.assertEqual(rows()[first]['status'], 'active')
        self.poll('2026-11-10T04:15:20Z', 'waiting', 0, 0.0, 40)
        self.assertEqual(rows()[first]['status'], 'active')
        self.poll('2026-11-10T04:30:20Z', 'waiting', 0, 0.0, 40)
        self.assertEqual((rows()[first]['endReason'], rows()[first]['energyKWh']), ('no_power', 0))
        second = self.start()
        self.poll('2026-11-10T04:15:20Z', 'unplugged', 0, 0.0, 40)
        self.assertEqual(rows()[second]['endReason'], 'unplugged')
        self.assertEqual(rows()[first]['endReason'], 'no_power')

    def test_restart_resumes_the_open_session_and_its_poll_schedule(self):
        self.start()
        restarted = TeslaSessionStore(self.state, lambda: self.now)
        self.assertIsNone(restarted.poll_due(instant('2026-11-10T04:10:00Z')))
        self.assertEqual(restarted.poll_due(instant('2026-11-10T04:15:20Z')), '123')
        restarted.observe('123', reading('2026-11-10T04:15:20Z', 'charging', 7.2, 1.5), instant('2026-11-10T04:15:22Z'))
        rows = HistoryStore(self.state, Path(self.temp.name)/'logs', lambda: self.now).view({}, None, car='tesla')['sessions']
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['energyKWh'], 1.5)

    def test_each_tesla_keeps_its_own_history_and_a_changed_car_closes_the_old_session(self):
        self.start()
        self.store.abandon(self.at('2026-11-10T04:05:00Z'), keep_vehicle='123')
        self.assertEqual(self.page()['sessions'][0]['status'], 'active')
        self.store.abandon(self.now, keep_vehicle='456')
        self.assertEqual(self.page()['sessions'][0]['endReason'], 'not_observed')
        other = reading('2026-11-10T04:06:00Z', 'paused', 0, 0.0)
        self.store.open_session({'id': '456', 'name': 'Second'}, other, None, self.at('2026-11-10T04:06:05Z'))
        self.assertEqual([row['car']['vehicleId'] for row in self.page(vehicle_id='123')['sessions']], ['123'])
        self.assertEqual([row['vehicleName'] for row in self.page(vehicle_id='456')['sessions']], ['Second'])
        self.assertEqual(len(self.page()['sessions']), 2)
        for kwargs in ({'car': 'cybertruck'}, {'car': 'virtual', 'vehicle_id': '123'}, {'car': 'tesla', 'vehicle_id': 'abc'}):
            with self.assertRaises(ValueError):
                self.history.view({}, None, **kwargs)

    def test_eight_tesla_sessions_fit_one_encrypted_frame(self):
        for index in range(10):
            base = instant('2026-11-09T04:00:00Z') + timedelta(hours=2 * index)
            stamp = lambda offset: (base + timedelta(minutes=offset)).isoformat().replace('+00:00', 'Z')
            self.store.open_session({'id': '12345678901234567890', 'name': 'N' * 128},
                                    reading(stamp(0), 'paused', 0, 33.3), None, base + timedelta(seconds=5))
            self.store.observe('12345678901234567890', reading(stamp(15), 'charging', 11.5, 2.875),
                               base + timedelta(minutes=15, seconds=5))
            self.store.stopped('12345678901234567890', None, base + timedelta(minutes=20))
        self.now = instant('2026-11-10T04:00:00Z')
        page = self.page()
        self.assertEqual(len(page['sessions']), 8)
        self.assertLess(len(base64.b64encode(json.dumps(page).encode())) + 1024, 16384)


class TeslaHistoryServiceTests(connections.ConnectionsServiceTests):
    """The real request path: Charge now, the background read, Stop and get_history."""

    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.fleet.script = []
        original = self.fleet.charge_state

        async def scripted():
            if not self.fleet.script or self.fleet.fail_read:
                return await original()
            self.fleet.calls.append(('charge_state', None))
            state, power, energy, battery = self.fleet.script.pop(0)
            self.fleet.state['chargeState'] = reading(self.now.isoformat(), state, power, energy, battery)
            return deepcopy(self.fleet.state['chargeState'])
        self.fleet.charge_state = scripted

    def reads(self):
        return sum(name == 'charge_state' for name, _ in self.fleet.calls)

    async def charging_tesla(self):
        await self.ready_tesla()
        self.fleet.script = [('paused', 0, 12.3, 40)]
        self.assertTrue((await self.request('a', 'activate_connection', {'mode': 'tesla'}))['ok'])
        pricing = ComEdPricing(lambda: self.now)
        for hour in (5, 6):
            pricing.settled_hourly[int(instant(f'2026-11-06T{hour:02}:00:00Z').timestamp())] = 4.0
        self.service.pricing = pricing
        self.fleet.script = [('paused', 0, 12.3, 40), ('waiting', 0, 12.3, 40)]
        started = await self.request('b', 'charge_now')
        self.assertTrue(started['ok'], started)
        self.assertIsNone(started['body']['warning'])

    async def test_poller_reads_nothing_until_sparc_starts_a_charge(self):
        await self.ready_tesla()
        await self.request('a', 'activate_connection', {'mode': 'tesla'})
        before = len(self.fleet.calls)
        for _ in range(3):
            self.now += timedelta(hours=1)
            self.assertFalse(await self.service.poll_tesla_session())
        self.assertEqual(len(self.fleet.calls), before)

    async def test_charge_now_then_bounded_reads_then_stop_records_only_under_the_tesla(self):
        await self.charging_tesla()
        reads = self.reads()
        self.now += timedelta(minutes=14)
        self.assertFalse(await self.service.poll_tesla_session())
        self.now += timedelta(minutes=1)
        self.fleet.script = [('charging', 7.2, 1.8, 45)]
        self.assertTrue(await self.service.poll_tesla_session())
        self.assertFalse(await self.service.poll_tesla_session())
        self.now += timedelta(minutes=15)
        self.fleet.script = [('charging', 7.2, 3.6, 50)]
        await self.service.poll_tesla_session()
        self.assertEqual(self.reads(), reads + 2)
        self.now += timedelta(minutes=5)
        self.fleet.script = [('paused', 0, 3.9, 51)]
        stopped = await self.request('a', 'pause')
        self.assertTrue(stopped['ok'], stopped)
        self.now += timedelta(hours=2)
        reads = self.reads()
        self.assertFalse(await self.service.poll_tesla_session())
        self.assertEqual(self.reads(), reads)
        tesla = await self.request('a', 'get_history', {'car': 'tesla', 'vehicleId': '123', 'limit': 4})
        self.assertTrue(tesla['ok'], tesla)
        session = tesla['body']['sessions'][0]
        self.assertEqual(session['car'], {'kind': 'tesla', 'vehicleId': '123'})
        self.assertEqual((session['status'], session['endReason'], session['energyKWh']), ('incomplete', 'stopped', 3.9))
        self.assertEqual((session['initialBatteryPercent'], session['batteryPercent']), (40, 51))
        self.assertAlmostEqual(session['costUSD'], 0.156)
        self.assertEqual(session['vehicleName'], 'Actual Fleet vehicle')
        virtual = await self.request('a', 'get_history')
        self.assertTrue(virtual['ok'])
        self.assertEqual(virtual['body']['car'], {'kind': 'virtual'})
        self.assertTrue(all(row['simulated'] and row['car']['kind'] == 'virtual' for row in virtual['body']['sessions']))
        other = await self.request('a', 'get_history', {'car': 'tesla', 'vehicleId': '999'})
        self.assertEqual(other['body']['sessions'], [])

    async def test_removing_the_tesla_closes_its_session_and_stops_reads(self):
        await self.charging_tesla()
        self.assertTrue((await self.request('a', 'activate_connection', {'mode': 'virtual'}))['ok'])
        self.assertTrue((await self.request('a', 'tesla_disconnect', {}))['ok'])
        reads = self.reads()
        self.now += timedelta(hours=1)
        self.assertFalse(await self.service.poll_tesla_session())
        self.assertEqual(self.reads(), reads)
        rows = self.service.history.view({}, None, car='tesla')['sessions']
        self.assertEqual((rows[0]['endReason'], rows[0]['energyPartial']), ('not_observed', True))

    async def test_history_write_failure_never_turns_an_accepted_command_into_an_error(self):
        await self.ready_tesla()
        await self.request('a', 'activate_connection', {'mode': 'tesla'})
        with patch('sparc_controller.tesla_history.save_json', side_effect=OSError('disk full')):
            result = await self.request('a', 'charge_now')
        self.assertTrue(result['ok'], result)
        self.assertTrue(result['body']['accepted'])
        self.assertIn('charging history', result['body']['warning'])
        self.assertEqual(sum(name == 'command' for name, _ in self.fleet.calls), 1)

    async def test_history_requests_reject_unknown_cars_and_malformed_vehicle_ids(self):
        for body in ({'car': 'cybertruck'}, {'vehicleId': '123'}, {'car': 'tesla', 'vehicleId': 123},
                     {'car': 'tesla', 'vehicleId': '12a'}, {'car': 'virtual', 'vehicleId': '123'}, {'car': None}):
            result = await self.request('a', 'get_history', body)
            self.assertFalse(result['ok'], body)


# Run only the Tesla history cases from this module, not the inherited connection suite twice.
def load_tests(loader, tests, pattern):
    suite = unittest.TestSuite()
    suite.addTests(loader.loadTestsFromTestCase(TeslaSessionStoreTests))
    for name in loader.getTestCaseNames(TeslaHistoryServiceTests):
        if name in TeslaHistoryServiceTests.__dict__:
            suite.addTest(TeslaHistoryServiceTests(name))
    return suite


if __name__ == '__main__':
    unittest.main()
