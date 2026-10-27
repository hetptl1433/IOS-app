import asyncio
import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import AsyncMock, Mock, patch
from types import SimpleNamespace
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from sparc_controller.app_test import local_instant
from sparc_controller.protocol import FrameBuffer, ProtocolError, SecureChannel, frame, pairing_code_key, setup_key
from sparc_controller.service import ControllerService, initialize
from sparc_controller.__main__ import parse_args, run

UTC = timezone.utc


class AppTestServiceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name)
        self.identity = initialize(self.path)
        # Tuesday, 00:59 in America/Chicago, before the example smart window.
        self.now = datetime(2026, 10, 27, 5, 59, tzinfo=UTC)
        self.service = self.make_service()
        self.channel = SecureChannel(setup_key(self.identity['setupKey']), self.service.info)

    async def asyncTearDown(self):
        self.temp.cleanup()

    def make_service(self):
        return ControllerService(self.path, allow_wifi_provision=False,
                                 app_test=True, clock=lambda: self.now)

    async def exchange(self, operation, body=None):
        request = {'id': str(uuid.uuid4()), 'timestamp': time.time(),
                   'operation': operation, 'body': body or {}}
        packet = self.channel.seal(request, 'c2d')
        incoming = FrameBuffer()
        framed = frame(packet)
        decoded = None
        for offset in range(0, len(framed), 20):
            decoded = incoming.append(framed[offset:offset + 20])
        response = await self.service.handle(decoded, 'ble')
        outgoing = FrameBuffer()
        framed = frame(response)
        for offset in range(0, len(framed), 20):
            decoded = outgoing.append(framed[offset:offset + 20])
        result = self.channel.open(decoded, 'd2c')
        self.assertEqual(result['id'], request['id'])
        return result

    async def fixed_schedule(self):
        weekly = dict(self.service.settings['weekly'])
        weekly.update(mode='fixed', fixedStartMinutes=360, departureMinutes=420, days=[2])
        result = await self.exchange('set_weekly', weekly)
        self.assertTrue(result['ok'])
        self.assertTrue(result['body']['storedOnly'])
        self.assertFalse(result['body']['controlOnly'])
        self.assertTrue(result['body']['virtualCar'])
        return weekly

    async def snapshot(self):
        response = await self.exchange('get_snapshot')
        self.assertTrue(response['ok'])
        return response['body']

    async def test_explicit_mode_defaults_to_plugged_virtual_car_without_real_vehicle(self):
        public = self.service.info.public()
        self.assertNotIn('controlOnly', public)
        self.assertFalse(public['vehicleAdapterAvailable'])
        self.assertIn('app-test', public['capabilities'])
        self.assertNotIn('wifi.provision', public['capabilities'])
        snapshot = await self.snapshot()
        self.assertFalse(snapshot['controlOnly'])
        self.assertTrue(snapshot['virtualCar'])
        self.assertTrue(snapshot['pluggedIn'])
        self.assertEqual(snapshot['batteryPercent'], 40)
        self.assertEqual(snapshot['state'], 'waiting')
        for key in ('powerKW', 'energyKWh', 'monthlySavings', 'estimatedCost', 'sessionSavings'):
            self.assertEqual(snapshot[key], 0, key)
        self.assertGreater(snapshot['remainingMinutes'], 0)
        self.assertEqual(snapshot['vehicleName'], 'Virtual car')
        self.assertTrue((await self.exchange('get_settings'))['body']['virtualCarEnabled'])

    async def test_fixed_schedule_transitions_at_actual_clock_boundaries(self):
        await self.fixed_schedule()
        self.assertEqual((await self.snapshot())['state'], 'waiting')
        self.now = datetime(2026, 10, 27, 11, 0, tzinfo=UTC)
        self.assertEqual((await self.snapshot())['state'], 'charging')
        self.now = datetime(2026, 10, 27, 11, 29, 59, tzinfo=UTC)
        self.assertEqual((await self.snapshot())['state'], 'charging')
        self.now = datetime(2026, 10, 27, 11, 30, tzinfo=UTC)
        # A short fixed window can end below target: never claim a full battery.
        self.assertEqual((await self.snapshot())['state'], 'waiting')
        self.now = datetime(2026, 10, 27, 12, 0, tzinfo=UTC)
        self.assertEqual((await self.snapshot())['state'], 'waiting')

    async def test_fixed_overnight_start_belongs_to_preceding_day(self):
        weekly = await self.fixed_schedule()
        weekly['fixedStartMinutes'] = 23 * 60
        await self.exchange('set_weekly', weekly)
        self.now = datetime(2026, 10, 27, 3, 59, tzinfo=UTC)
        self.assertEqual((await self.snapshot())['state'], 'waiting')
        self.now = datetime(2026, 10, 27, 4, 0, tzinfo=UTC)
        snapshot = await self.snapshot()
        self.assertEqual(snapshot['state'], 'charging')
        self.assertEqual(snapshot['windowStart'], '2026-10-27T04:00:00Z')

    async def test_start_equal_to_ready_deadline_does_not_start_day_early(self):
        weekly = await self.fixed_schedule()
        weekly['fixedStartMinutes'] = 390  # 06:30, equal to the ready deadline.
        await self.exchange('set_weekly', weekly)
        self.now = datetime(2026, 10, 27, 11, 0, tzinfo=UTC)
        snapshot = await self.snapshot()
        self.assertEqual(snapshot['state'], 'waiting')
        self.assertEqual(snapshot['windowStart'], '2026-10-27T11:30:00Z')

    async def test_commands_and_restart_preserve_state_without_replacing_identity(self):
        for command, expected in [('charge_now', 'charging'), ('pause', 'paused'),
                                  ('resume', 'charging'), ('end', 'waiting')]:
            result = await self.exchange(command)
            self.assertTrue(result['ok'])
            self.assertEqual(result['body']['state'], expected)
            restarted = self.make_service()
            self.assertEqual(restarted.info.device_id, self.identity['deviceId'])
            self.assertEqual(restarted.app_test.snapshot(restarted.settings)['state'], expected)
        self.assertEqual(json.loads((self.path/'identity.json').read_text()), self.identity)
        self.assertEqual((self.path/'settings.json').stat().st_mode & 0o777, 0o600)

    async def test_end_suppresses_current_fixed_window_until_departure(self):
        await self.fixed_schedule()
        self.now = datetime(2026, 10, 27, 11, 10, tzinfo=UTC)
        self.assertEqual((await self.snapshot())['state'], 'charging')
        await self.exchange('end')
        self.now = datetime(2026, 10, 27, 11, 40, tzinfo=UTC)
        self.assertEqual((await self.snapshot())['state'], 'waiting')
        self.now = datetime(2026, 11, 3, 12, 10, tzinfo=UTC)  # 06:10 after DST ends
        self.assertEqual((await self.snapshot())['state'], 'charging')

    async def test_pause_stops_virtual_energy_and_resume_advances_it(self):
        await self.fixed_schedule()
        self.now = datetime(2026, 10, 27, 11, 10, tzinfo=UTC)
        paused = (await self.exchange('pause'))['body']
        self.assertEqual(paused['state'], 'paused')
        self.now += timedelta(minutes=5)
        self.assertEqual((await self.snapshot())['energyKWh'], paused['energyKWh'])
        self.assertEqual((await self.exchange('resume'))['body']['state'], 'charging')
        self.now += timedelta(minutes=5)
        self.assertAlmostEqual((await self.snapshot())['energyKWh'], paused['energyKWh'] + 11.5 / 12)

    async def test_saved_schedule_takes_over_manual_control_atomically(self):
        await self.exchange('charge_now')
        weekly = await self.fixed_schedule()
        self.assertEqual((await self.snapshot())['state'], 'waiting')
        self.assertEqual((await self.exchange('get_settings'))['body'], {'weekly': weekly, 'today': None, 'virtualCarEnabled': True})
        before = self.service.settings
        weekly = dict(weekly, targetPercent=90)
        with patch('sparc_controller.service.atomic_save', side_effect=OSError('test disk failure')):
            self.assertFalse((await self.exchange('set_weekly', weekly))['ok'])
        self.assertEqual(self.service.settings, before)

    async def test_failed_command_preserves_acknowledged_state(self):
        await self.exchange('charge_now')
        before = (self.path/'settings.json').read_bytes()
        with patch('sparc_controller.service.atomic_save', side_effect=OSError('test disk failure')):
            self.assertFalse((await self.exchange('pause'))['ok'])
        snapshot = await self.snapshot()
        self.assertEqual(snapshot['state'], 'charging')
        self.assertEqual(snapshot['batteryPercent'], 40)
        self.assertEqual((self.path/'settings.json').read_bytes(), before)

    async def test_today_override_is_independent_expires_and_restores_weekly(self):
        weekly = await self.fixed_schedule()
        today = {'targetPercent': 95, 'departure': '2026-10-27T13:00:00Z'}
        self.assertTrue((await self.exchange('set_today', {'today': today}))['ok'])
        self.assertEqual((await self.snapshot())['targetPercent'], 95)
        self.assertEqual(self.service.settings['weekly'], weekly)
        restarted = self.make_service()
        self.assertEqual(restarted.settings['today']['targetPercent'], 95)
        self.now = datetime(2026, 10, 27, 13, 0, tzinfo=UTC)
        self.assertIsNone((await self.exchange('get_settings'))['body']['today'])
        self.assertEqual((await self.snapshot())['targetPercent'], weekly['targetPercent'])

    async def test_disabled_weekly_schedule_waits_but_explicit_manual_control_works(self):
        weekly = dict(self.service.settings['weekly'], enabled=False, days=[])
        await self.exchange('set_weekly', weekly)
        self.assertEqual((await self.snapshot())['state'], 'waiting')
        await self.exchange('charge_now')
        self.now += timedelta(days=1)
        snapshot = await self.snapshot()
        self.assertEqual(snapshot['state'], 'complete')
        self.assertEqual(snapshot['batteryPercent'], 80)

    async def test_virtual_smart_window_accrues_while_app_is_closed(self):
        snapshot = await self.snapshot()
        self.assertEqual(snapshot['state'], 'waiting')
        self.assertEqual(snapshot['windowStart'], '2026-10-27T06:00:00Z')
        self.now = datetime(2026, 10, 27, 7, 0, tzinfo=UTC)
        snapshot = await self.snapshot()
        self.assertEqual(snapshot['state'], 'charging')
        self.assertEqual(snapshot['batteryPercent'], 50)
        self.assertEqual(snapshot['powerKW'], 11.5)
        self.assertAlmostEqual(snapshot['energyKWh'], 11.5)
        self.assertAlmostEqual(snapshot['estimatedCost'], 11.5 * .07)

    async def test_ble_controls_survive_closed_setup_but_new_pairing_does_not(self):
        self.service.setup_deadline = time.monotonic() - 1
        self.assertFalse((await self.exchange('pair'))['ok'])
        self.assertTrue((await self.exchange('ping'))['ok'])
        self.assertEqual((await self.exchange('charge_now'))['body']['state'], 'charging')

    async def test_pairing_code_pairs_after_setup_window_without_rewriting_identity(self):
        before = (self.path / 'identity.json').read_bytes()
        self.service = ControllerService(self.path, 0, allow_wifi_provision=False, app_test=True,
                                         clock=lambda: self.now, pairing_code='0000')
        self.assertIn('pairing-code', self.service.info.public()['capabilities'])
        self.channel = SecureChannel(setup_key(pairing_code_key('0000')), self.service.info)
        self.assertEqual((await self.exchange('pair'))['body']['deviceId'], self.identity['deviceId'])
        self.assertEqual((await self.exchange('charge_now'))['body']['state'], 'charging')
        request = {'id': str(uuid.uuid4()), 'timestamp': time.time(), 'operation': 'ping', 'body': {}}
        identity_channel = SecureChannel(setup_key(self.identity['setupKey']), self.service.info)
        with self.assertRaises(ProtocolError):
            await self.service.handle(identity_channel.seal(request, 'c2d'), 'ble')
        self.assertEqual((self.path / 'identity.json').read_bytes(), before)

    async def test_pairing_code_requires_app_test_and_digits(self):
        # Shared with the iOS app's ControllerPairingCode derivation.
        self.assertEqual(pairing_code_key('0000'), '0216e0d358ef2470c76d54687226ce99925a8dd45541d139f08806e18c574987')
        self.assertNotIn('pairing-code', self.service.info.public()['capabilities'])
        for code in ('', '123', '123456789', '12a4', '٠٠٠٠'):
            with self.assertRaises(ProtocolError):
                pairing_code_key(code)
        with self.assertRaises(ValueError):
            ControllerService(self.path, allow_wifi_provision=False, pairing_code='0000')
        with patch.dict('os.environ', {'SPARC_PAIRING_CODE': '0000'}):
            self.assertEqual(parse_args(['--app-test']).pairing_code, '0000')
            with self.assertRaises(SystemExit), patch('sys.stderr'):
                parse_args([])
        with self.assertRaises(SystemExit), patch('sys.stderr'):
            parse_args(['--app-test', '--pairing-code', '12'])
        with patch.dict('os.environ', {'SPARC_PAIRING_CODE': ''}):
            self.assertIsNone(parse_args(['--app-test']).pairing_code)

    async def test_authenticated_ble_commands_reject_replay_wrong_key_and_old_boot(self):
        request = {'id': str(uuid.uuid4()), 'timestamp': time.time(), 'operation': 'charge_now', 'body': {}}
        packet = self.channel.seal(request, 'c2d')
        reply = await self.service.handle(packet, 'ble')
        self.assertTrue(self.channel.open(reply, 'd2c')['ok'])
        with self.assertRaises(ProtocolError):
            await self.service.handle(packet, 'ble')
        wrong = SecureChannel(b'x'*32, self.service.info)
        with self.assertRaises(ProtocolError):
            await self.service.handle(wrong.seal(dict(request, id=str(uuid.uuid4())), 'c2d'), 'ble')
        with self.assertRaises(ProtocolError):
            await self.make_service().handle(packet, 'ble')

    async def test_virtual_toggle_is_acknowledged_idempotent_and_resets_on_reenable(self):
        await self.exchange('charge_now')
        self.now += timedelta(hours=1)
        charged = await self.snapshot()
        self.assertEqual(charged['batteryPercent'], 50)
        self.assertTrue((await self.exchange('set_virtual_car', {'enabled': True}))['ok'])
        self.assertEqual((await self.snapshot())['energyKWh'], charged['energyKWh'])
        self.assertTrue((await self.exchange('set_virtual_car', {'enabled': False}))['ok'])
        off = await self.snapshot()
        self.assertFalse(off['virtualCar'])
        self.assertTrue(off['controlOnly'])
        self.assertEqual(off['state'], 'unplugged')
        self.assertEqual(off['powerKW'], 0)
        for command in ('charge_now', 'pause', 'resume', 'end'):
            self.assertFalse((await self.exchange(command))['ok'])
        self.assertTrue((await self.exchange('set_virtual_car', {'enabled': True}))['ok'])
        reset = await self.snapshot()
        self.assertEqual(reset['batteryPercent'], 40)
        self.assertEqual(reset['energyKWh'], 0)
        self.assertTrue(reset['pluggedIn'])
        self.assertTrue((await self.exchange('get_settings'))['body']['virtualCarEnabled'])

    async def test_virtual_toggle_rejects_non_boolean_and_disk_failure_without_optimism(self):
        for value in ('true', 1, None):
            self.assertFalse((await self.exchange('set_virtual_car', {'enabled': value}))['ok'])
        with patch('sparc_controller.service.atomic_save', side_effect=OSError('test disk failure')):
            self.assertFalse((await self.exchange('set_virtual_car', {'enabled': False}))['ok'])
        self.assertTrue((await self.snapshot())['virtualCar'])

    async def test_virtual_target_clamps_energy_and_stops_even_after_long_interval(self):
        await self.exchange('set_weekly', dict(self.service.settings['weekly'], enabled=False, days=[]))
        await self.exchange('charge_now')
        self.now += timedelta(days=1)
        snapshot = await self.snapshot()
        self.assertEqual(snapshot['batteryPercent'], 80)
        self.assertEqual(snapshot['state'], 'complete')
        self.assertEqual(snapshot['powerKW'], 0)
        self.assertEqual(snapshot['remainingMinutes'], 0)
        self.assertAlmostEqual(snapshot['energyKWh'], 40 / .9)
        self.now += timedelta(days=2)
        self.assertEqual((await self.snapshot())['energyKWh'], snapshot['energyKWh'])

    async def test_fixed_schedule_no_poll_gap_only_accrues_inside_window(self):
        await self.fixed_schedule()
        self.now = datetime(2026, 10, 27, 12, 10, tzinfo=UTC)
        snapshot = await self.snapshot()
        self.assertAlmostEqual(snapshot['energyKWh'], 11.5 / 2)
        self.assertEqual(snapshot['batteryPercent'], 45)
        self.assertEqual(snapshot['state'], 'waiting')

    async def test_virtual_state_persists_without_accrual_during_process_downtime(self):
        await self.exchange('charge_now')
        self.now += timedelta(hours=1)
        before = await self.snapshot()
        self.now += timedelta(hours=2)
        self.service = self.make_service()
        self.channel = SecureChannel(setup_key(self.identity['setupKey']), self.service.info)
        after = await self.snapshot()
        self.assertEqual(after['batteryPercent'], before['batteryPercent'])
        self.assertEqual(after['energyKWh'], before['energyKWh'])
        self.now += timedelta(minutes=1)
        self.assertAlmostEqual((await self.snapshot())['energyKWh'], before['energyKWh'] + 11.5 / 60)

    async def test_manual_charge_after_ready_buffer_runs_until_departure_or_target(self):
        await self.fixed_schedule()
        self.now = datetime(2026, 10, 27, 11, 40, tzinfo=UTC)
        self.assertEqual((await self.snapshot())['state'], 'waiting')
        self.assertEqual((await self.exchange('charge_now'))['body']['state'], 'charging')
        before = await self.snapshot()
        self.now += timedelta(minutes=10)
        self.assertGreater((await self.snapshot())['energyKWh'], before['energyKWh'])
        self.now = datetime(2026, 10, 27, 12, 1, tzinfo=UTC)
        self.assertEqual((await self.snapshot())['state'], 'waiting')


class ScheduleTimeTests(unittest.TestCase):
    def test_dst_gap_uses_next_valid_time_and_fold_uses_first_occurrence(self):
        self.assertEqual(local_instant(date(2026, 3, 8), 150), datetime(2026, 3, 8, 8, 0, tzinfo=UTC))
        self.assertEqual(local_instant(date(2026, 11, 1), 90), datetime(2026, 11, 1, 6, 30, tzinfo=UTC))


class BluetoothRuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def test_ble_app_test_has_no_networkmanager_dependency(self):
        with tempfile.TemporaryDirectory() as directory:
            initialize(Path(directory))
            args = parse_args(['--state-dir', directory, '--no-wifi-provision', '--app-test', '--no-bonjour'])
            async def http(*args):
                await asyncio.Future()
            async def ble(service, adapter):
                self.assertIsNone(service.wifi)
                self.assertFalse(service.allow_wifi_provision)
                self.assertTrue(service.info.app_test)
                raise RuntimeError('finished test adapter check')
            with patch('sparc_controller.__main__.NetworkManagerWiFi', side_effect=AssertionError('must not create Wi-Fi adapter')), \
                 patch('sparc_controller.__main__.run_http', http), \
                 patch.dict(sys.modules, {'sparc_controller.bluez': SimpleNamespace(run_ble=ble)}):
                with self.assertRaisesRegex(RuntimeError, 'finished test adapter check'):
                    await run(args)

    async def test_ble_advertising_continues_after_pairing_window_closed(self):
        from sparc_controller.bluez import run_ble
        from sparc_controller.protocol import DeviceInfo
        registered = asyncio.Event()
        advertiser = Mock()
        async def register(*args):
            registered.set()
        advertiser.call_register_advertisement = AsyncMock(side_effect=register)
        advertiser.call_unregister_advertisement = AsyncMock()
        manager = Mock(call_register_application=AsyncMock(), call_unregister_application=AsyncMock())
        properties = Mock(call_set=AsyncMock())
        interfaces = {'org.freedesktop.DBus.Properties': properties,
                      'org.bluez.GattManager1': manager, 'org.bluez.LEAdvertisingManager1': advertiser}
        proxy = Mock(get_interface=Mock(side_effect=interfaces.__getitem__))
        bus = Mock(introspect=AsyncMock(return_value=None), get_proxy_object=Mock(return_value=proxy))
        factory = Mock(connect=AsyncMock(return_value=bus))
        service = SimpleNamespace(info=DeviceInfo(str(uuid.uuid4()), str(uuid.uuid4()), 'Test'), setup_open=False)
        with patch('sparc_controller.bluez.MessageBus', return_value=factory):
            task = asyncio.create_task(run_ble(service))
            await asyncio.wait_for(registered.wait(), 1)
            await asyncio.sleep(0)
            self.assertFalse(task.done())
            advertiser.call_unregister_advertisement.assert_not_awaited()
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
        advertiser.call_unregister_advertisement.assert_awaited_once()
        manager.call_unregister_application.assert_awaited_once()
        bus.disconnect.assert_called_once()


if __name__ == '__main__':
    unittest.main()
