"""Backend-only checks for independent encrypted app installations."""
import asyncio
from copy import deepcopy
from datetime import datetime
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch
import uuid

from sparc_controller.campaign import DEFAULT_CAMPAIGN, arm_settings
from sparc_controller.clients import ClientRegistry, MAX_CLIENTS, clean_client
from sparc_controller.pricing import ComEdPricing
from sparc_controller.protocol import MAX_FRAME, ProtocolError, SecureChannel, setup_key
from sparc_controller.routine import FIELDS
from sparc_controller.service import ControllerService, atomic_save, initialize


def at(value):
    return datetime.fromisoformat(value.replace('Z', '+00:00'))


class MultiAppServiceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name)
        self.identity = initialize(self.path)
        self.identity_bytes = (self.path/'identity.json').read_bytes()
        (self.path/'campaign.json').write_text(json.dumps(DEFAULT_CAMPAIGN))
        self.now = at('2026-11-05T23:00:00-06:00')
        self.service = self.make_service()
        self.service.settings = arm_settings(self.service.settings, DEFAULT_CAMPAIGN, 1, self.now)
        atomic_save(self.path/'settings.json', self.service.settings)
        self.apps = {
            'a':{'id':str(uuid.uuid4()), 'name':'Phone A', 'appVersion':'1.0', 'build':'100'},
            'b':{'id':str(uuid.uuid4()), 'name':'Phone B', 'appVersion':'1.0', 'build':'101'},
        }
        self.channels = self.make_channels()

    def make_service(self):
        service = ControllerService(self.path, allow_wifi_provision=False, app_test=True, clock=lambda:self.now)
        service.history.log_root = self.path/'test-logs'
        return service

    def make_channels(self):
        return {key:SecureChannel(setup_key(self.identity['setupKey']), self.service.info) for key in ('a','b')}

    def body(self, **changes):
        campaign = self.service.settings['campaign']
        return {**{key:campaign[key] for key in FIELDS}, 'expectedRevision':campaign['revision'], **changes}

    async def request(self, app, operation, body=None, *, client=None):
        payload = {'id':str(uuid.uuid4()), 'timestamp':time.time(), 'operation':operation,
                   'body':{} if body is None else body, 'client':self.apps[app] if client is None else client}
        sealed = self.channels[app].seal(payload, 'c2d')
        response = await self.service.handle(sealed, 'lan' if app == 'a' else 'ble')
        self.assertLessEqual(len(response), MAX_FRAME)
        return self.channels[app].open(response, 'd2c')

    async def pair_both(self):
        for app in ('a','b'):
            result = await self.request(app, 'pair')
            self.assertTrue(result['ok'])
            self.assertEqual(result['body']['clientId'], self.apps[app]['id'])

    async def test_rate_history_reads_over_both_encrypted_transports_without_mutating_charging(self):
        self.service.pricing = ComEdPricing(lambda:self.now)
        before = (self.path/'settings.json').read_bytes()
        for app in ('a','b'):
            result = await self.request(app,'get_rates',{'day':'2026-11-05'})
            self.assertTrue(result['ok'])
            self.assertEqual(result['body']['day'],'2026-11-05')
            self.assertEqual(len(result['body']['hours']),24)
        rejected = await self.request('a','get_rates',{'day':'2026-11-05','extra':1})
        self.assertFalse(rejected['ok'])
        self.assertEqual((self.path/'settings.json').read_bytes(),before)

    async def test_calendar_sync_is_owned_and_persisted_over_encrypted_phone_links(self):
        await self.pair_both()
        payload={'enabled':True,'windows':[{'id':'sleep-one','start':'2026-11-06T22:00:00-06:00','departure':'2026-11-07T07:00:00-06:00'}],'targetPercent':80}
        before=self.service.settings['virtualCarState']['batteryPercent']
        first=await self.request('a','set_sleep_windows',payload)
        self.assertTrue(first['ok'],first)
        self.assertTrue(first['body']['sleepCalendar']['enabled'])
        self.assertEqual(self.service.settings['virtualCarState']['batteryPercent'],before)
        rejected=await self.request('b','set_sleep_windows',payload)
        self.assertFalse(rejected['ok'])
        rejected=await self.request('a','set_campaign',self.body())
        self.assertFalse(rejected['ok'])
        restored=self.make_service()
        self.assertTrue(restored.settings['sleepCalendar']['enabled'])
        stopped=await self.request('a','set_sleep_windows',dict(payload,enabled=False,windows=[]))
        self.assertTrue(stopped['ok']);self.assertFalse(stopped['body']['sleepCalendar']['enabled'])

    async def test_recorded_combined_savings_never_loads_the_simulation_into_history(self):
        # A malformed replay is rejected independently; recorded accounting still works.
        (self.path/'savings-replay.json').write_text('{}')
        replay=await self.request('a','get_savings',{'kind':'simulation'})
        self.assertFalse(replay['ok'])
        before=(self.path/'settings.json').read_bytes()
        for app in ('a','b'):
            recorded=await self.request(app,'get_savings',{'kind':'recorded'})
            self.assertTrue(recorded['ok'],recorded)
            self.assertEqual(recorded['body']['kind'],'recorded')
            self.assertLessEqual(recorded['body']['summary']['nightCount'],15)
        self.assertEqual((self.path/'settings.json').read_bytes(),before)

    async def test_two_apps_pair_persist_and_restart_without_rotating_controller_identity(self):
        await self.pair_both()
        saved = json.loads((self.path/'paired-apps.json').read_text())
        self.assertEqual(set(saved['clients']), {self.apps['a']['id'], self.apps['b']['id']})
        self.assertEqual((self.path/'identity.json').read_bytes(), self.identity_bytes)
        old_boot = self.service.info.boot_id
        self.service = self.make_service()
        self.channels = self.make_channels()
        self.assertNotEqual(self.service.info.boot_id, old_boot)
        self.assertEqual(self.service.info.device_id, self.identity['deviceId'])
        self.assertEqual((self.path/'identity.json').read_bytes(), self.identity_bytes)
        for app in ('a','b'):
            result = await self.request(app, 'get_settings')
            self.assertTrue(result['ok'])
            self.assertEqual(result['body']['clients']['pairedCount'], 2)
            self.assertEqual(result['body']['clients']['currentClientId'], self.apps[app]['id'])

    async def test_last_accepted_charge_pause_order_has_one_shared_sequence_and_writer(self):
        await self.pair_both()
        charged = await self.request('a', 'charge_now')
        self.assertTrue(charged['ok'])
        self.assertEqual(charged['body']['control']['sequence'], 1)
        self.assertEqual(charged['body']['control']['clientId'], self.apps['a']['id'])
        self.assertGreater(charged['body']['powerKW'], 0)
        paused = await self.request('b', 'pause')
        self.assertTrue(paused['ok'])
        expected = paused['body']['control']
        self.assertEqual((expected['sequence'], expected['operation'], expected['clientName']), (2, 'pause', 'Phone B'))
        for app in ('a','b'):
            snapshot = (await self.request(app, 'get_snapshot'))['body']
            self.assertEqual(snapshot['control'], expected)
            self.assertEqual(snapshot['powerKW'], 0)
            self.assertEqual((await self.request(app, 'get_settings'))['body']['control'], expected)
        charged = await self.request('a', 'charge_now')
        self.assertEqual(charged['body']['control']['sequence'], 3)
        self.assertEqual(charged['body']['control']['clientId'], self.apps['a']['id'])

    async def test_concurrent_older_routine_revision_is_last_accepted_writer_wins(self):
        await self.pair_both()
        a, b = self.body(targetBatteryPercent=85), self.body(targetBatteryPercent=90)
        results = await asyncio.gather(self.request('a','set_campaign',a), self.request('b','set_campaign',b))
        self.assertTrue(all(result['ok'] for result in results))
        self.assertEqual([result['body']['control']['sequence'] for result in results], [1,2])
        self.assertEqual(self.service.settings['campaign']['revision'], 3)
        self.assertEqual(self.service.settings['campaign']['targetBatteryPercent'], 90)
        self.assertEqual(self.service.settings['virtualCarState']['batteryPercent'], 20)
        for app in ('a','b'):
            saved = (await self.request(app,'get_settings'))['body']
            self.assertEqual(saved['campaign']['targetBatteryPercent'], 90)
            self.assertEqual(saved['control']['clientId'], self.apps['b']['id'])
            self.assertEqual(saved['control']['sequence'], 2)

    async def test_invalid_payload_and_future_revision_never_advance_control_sequence(self):
        await self.pair_both()
        before = deepcopy(self.service.settings)
        invalid = [self.body(nights=0), self.body(expectedRevision=99), self.body(expectedRevision=True),
                   self.body(targetBatteryPercent=101), {**self.body(), 'unexpected':True}]
        for body in invalid:
            with self.subTest(body=body):
                result = await self.request('b','set_campaign',body)
                self.assertFalse(result['ok'])
                self.assertEqual(self.service.settings, before)
        result = await self.request('a','set_virtual_car_state',{'batteryPercent':101,'pluggedIn':True})
        self.assertFalse(result['ok'])
        self.assertEqual(self.service.settings, before)

    async def test_replayed_packet_and_cross_client_reused_request_id_cannot_repeat_mutation(self):
        await self.pair_both()
        payload = {'id':str(uuid.uuid4()),'timestamp':time.time(),'operation':'charge_now','body':{},'client':self.apps['a']}
        wire = self.channels['a'].seal(payload,'c2d',nonce=b'\x11'*12)
        response = await self.service.handle(wire,'lan')
        self.assertTrue(self.channels['a'].open(response,'d2c')['ok'])
        with self.assertRaises(ProtocolError):
            await self.service.handle(wire,'lan')
        other = dict(payload, operation='pause', client=self.apps['b'])
        with self.assertRaises(ProtocolError):
            await self.service.handle(self.channels['b'].seal(other,'c2d',nonce=b'\x22'*12),'ble')
        state = (await self.request('b','get_settings'))['body']['control']
        self.assertEqual((state['sequence'],state['operation'],state['clientId']), (1,'charge_now',self.apps['a']['id']))

    async def test_failed_atomic_command_write_does_not_advance_shared_control_sequence(self):
        await self.pair_both()
        before = deepcopy(self.service.settings)
        with patch('sparc_controller.service.atomic_save', side_effect=OSError('full disk')):
            result = await self.request('b','pause')
        self.assertFalse(result['ok'])
        self.assertEqual(self.service.settings, before)
        self.assertEqual(json.loads((self.path/'settings.json').read_text()), before)
        accepted = await self.request('a','charge_now')
        self.assertEqual(accepted['body']['control']['sequence'], 1)

    async def test_developer_adjustment_archives_closed_eighty_percent_night_without_resetting_energy(self):
        self.now = at('2026-11-06T12:59:00Z')
        self.service._virtual_anchor = self.now
        buckets = {f'2026-11-06T{hour:02}:00:00+00:00':11.5 for hour in range(4,9)}
        buckets['2026-11-06T09:00:00+00:00'] = 66.6666666667-sum(buckets.values())
        self.service.settings['virtualCarState'].update(batteryPercent=80.0, energyKWh=66.6666666667, energyByHour=buckets)
        self.service.settings['appTestControl'] = {'manualState':'paused','manualUntil':None}
        self.service.pricing = ComEdPricing(lambda:self.now)
        for hour in range(4,10):
            self.service.pricing.settled_hourly[int(at(f'2026-11-06T{hour:02}:00:00Z').timestamp())] = -1
        self.service.app_test.pricing = self.service.pricing
        await self.service.capture_history(force=True)
        self.service.settings['overnightRun']['finalizedAt'] = '2026-11-06T13:06:00Z'
        self.now = at('2026-11-06T14:00:00Z')
        self.service._virtual_anchor = self.now
        atomic_save(self.path/'settings.json',self.service.settings)
        before = deepcopy(self.service.settings['virtualCarState'])
        result = await self.request('a','set_virtual_car_state',{'batteryPercent':20,'pluggedIn':False})
        self.assertTrue(result['ok'])
        self.assertEqual(result['body']['developer']['batteryPercent'],20)
        self.assertFalse(result['body']['developer']['pluggedIn'])
        for key in ('energyKWh','energyByHour','cost','immediateCost'):
            self.assertEqual(self.service.settings['virtualCarState'][key],before[key])
        self.assertEqual(self.service.settings['appTestControl'],{'manualState':'paused','manualUntil':None})
        history = (await self.request('b','get_history'))['body']['sessions'][0]
        self.assertEqual(history['batteryPercent'],80)
        self.assertAlmostEqual(history['energyKWh'],66.6666666667,places=5)
        self.assertTrue(history['targetReachedByDeadline'])
        self.assertLess(history['costUSD'],0)
        self.assertEqual((await self.request('b','get_snapshot'))['body']['powerKW'],0)

    async def test_active_unplug_and_replug_keep_pause_and_all_measured_counters(self):
        await self.request('a','pause')
        before = deepcopy(self.service.settings['virtualCarState'])
        unplugged = await self.request('b','set_virtual_car_state',{'batteryPercent':25.5,'pluggedIn':False})
        self.assertTrue(unplugged['ok'])
        self.assertEqual((await self.request('a','get_snapshot'))['body']['state'],'unplugged')
        plugged = await self.request('a','set_virtual_car_state',{'batteryPercent':25.5,'pluggedIn':True})
        self.assertTrue(plugged['ok'])
        snapshot = (await self.request('b','get_snapshot'))['body']
        self.assertEqual(snapshot['powerKW'],0)
        self.assertEqual(snapshot['state'],'paused')
        self.assertEqual(snapshot['control']['sequence'],3)
        for key in ('energyKWh','energyByHour','cost','immediateCost'):
            self.assertEqual(self.service.settings['virtualCarState'].get(key),before.get(key))

    async def test_invalid_or_oversized_metadata_cannot_mutate_control_or_register_an_app(self):
        await self.pair_both()
        before = deepcopy(self.service.settings)
        ids = set(self.service.clients.clients)
        invalid = [{'id':'not-a-uuid'}, dict(self.apps['a'], name=False), dict(self.apps['a'],name=0),
                   dict(self.apps['a'],name=[]), dict(self.apps['a'],name='x'*129),
                   dict(self.apps['a'],name='é'*65), dict(self.apps['a'],name='bad\nname'),
                   dict(self.apps['a'],appVersion='x'*33), dict(self.apps['a'],build=True),
                   dict(self.apps['a'],unexpected='field')]
        for client in invalid:
            with self.subTest(client=client):
                response = await self.request('a','charge_now',client=client)
                self.assertFalse(response['ok'])
                self.assertEqual(self.service.settings,before)
                self.assertEqual(set(self.service.clients.clients),ids)


class ClientMetadataBoundsTests(unittest.TestCase):
    def test_registry_capacity_is_bounded_without_replacing_existing_clients(self):
        saved = []
        now = at('2026-11-06T14:00:00Z')
        with tempfile.TemporaryDirectory() as directory:
            registry = ClientRegistry(Path(directory), lambda path,value:saved.append(value), lambda:now)
            for index in range(MAX_CLIENTS):
                registry.touch(clean_client({'id':str(uuid.UUID(int=index+1)),'name':f'App {index}'}),'lan',paired=True)
            old = deepcopy(registry.clients)
            with self.assertRaises(ValueError):
                registry.touch(clean_client({'id':str(uuid.uuid4()),'name':'Too many'}),'ble',paired=True)
            self.assertEqual(registry.clients,old)
            self.assertEqual(len(saved),MAX_CLIENTS)


if __name__ == '__main__':
    unittest.main()
