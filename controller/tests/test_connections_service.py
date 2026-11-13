"""Backend integration only: all plug and Fleet operations use local fakes."""
import asyncio
from copy import deepcopy
from datetime import timedelta
import json
import socket
from unittest.mock import patch
import unittest

from sparc_controller.shelly import MODEL
from sparc_controller.connections_service import resolve_shelly_address
from sparc_controller.service import atomic_save
import test_multi_app_service as fixtures


class FakeShelly:
    def __init__(self,address,clock,password=None,block=None,entered=None,fail=False,device_id=None):
        self.address,self.clock,self.password = address,clock,password
        self.block,self.entered,self.fail = block,entered,fail
        self.calls = []
        self.device_id = device_id or 'shellyplugusg4-'+address.replace('.','')
        self.on = False

    def reading(self):
        return {'model':MODEL,'deviceId':self.device_id,'metering':True,'powerW':0,'voltageV':120,
                'switchOn':self.on,'authRequired':self.password is not None,'lastCheckedAt':self.clock().isoformat()}

    async def probe(self):
        self.calls.append(('probe',None))
        if self.entered:
            self.entered.set()
        if self.block:
            await self.block.wait()
        if self.fail:
            raise ValueError('raw-private-password-error')
        return self.reading()

    async def status(self):
        self.calls.append(('status',None))
        return self.reading()

    async def set_power(self,on):
        self.calls.append(('set',on))
        old,self.on = self.on,on
        return {'accepted':True,'requestedOn':on,'previousOn':old}


class FakeFleet:
    def __init__(self,clock):
        self.clock,self.calls = clock,[]
        self.fail_read = False
        self.fail_command = False
        self.stale = False
        self.state = {'configured':True,'authorized':False,'status':'authorization_required','vehicleName':None,
                      'vehicleId':None,'vehicles':[],'commandsReady':False,'error':None}

    def public_status(self):
        return deepcopy(self.state)

    async def authorize_start(self,client):
        self.calls.append(('authorize_start',client))
        return {'authorizationURL':'https://auth.tesla.com/oauth2/v3/authorize?state=fake-state',
                'expiresAt':(self.clock()+timedelta(minutes=10)).isoformat()}

    async def complete(self,code,state,client):
        self.calls.append(('complete',client))
        self.state.update(authorized=True,status='vehicle_not_selected')

    async def vehicles(self):
        self.calls.append(('vehicles',None))
        self.state['vehicles'] = [{'id':'123','vin':'5YJ3E1EA7KF123456','displayName':'Actual Fleet vehicle'}]
        return deepcopy(self.state['vehicles'])

    async def select_vehicle(self,vehicle):
        self.calls.append(('select_vehicle',vehicle))
        self.state.update(vehicleId=vehicle,vehicleName='Actual Fleet vehicle',status='connected')

    async def charge_state(self):
        self.calls.append(('charge_state',None))
        if self.fail_read:
            raise ValueError('Tesla could not be read.')
        stamp = self.clock()-timedelta(minutes=10) if self.stale else self.clock()
        self.state['chargeState'] = {'batteryPercent':62,'pluggedIn':True,'powerKW':7,'updatedAt':stamp.isoformat(),
                                    'state':'charging','timestampSource':'vehicle','targetPercent':80}
        return deepcopy(self.state['chargeState'])

    async def check_readiness(self):
        self.calls.append(('check_readiness',None))
        return self.public_status()

    async def command(self,name,value=None):
        self.calls.append(('command',(name,value)))
        if self.fail_command:
            raise ValueError('Tesla could not confirm the command; it was not retried.')
        return {'accepted':True,'command':name}

    async def disconnect(self,account_id=None):
        self.calls.append(('disconnect',account_id))
        if account_id is None or account_id == self.state.get('accountId'):
            self.state.update(authorized=False,status='authorization_required',vehicleId=None,vehicleName=None,vehicles=[])


class ConnectionsServiceTests(unittest.IsolatedAsyncioTestCase):
    make_service = fixtures.MultiAppServiceTests.make_service
    make_channels = fixtures.MultiAppServiceTests.make_channels
    request = fixtures.MultiAppServiceTests.request

    async def asyncSetUp(self):
        await fixtures.MultiAppServiceTests.asyncSetUp(self)
        self.instances = []
        self.plug_options = {}

        def factory(address,password=None):
            plug = FakeShelly(address,lambda:self.now,password,**self.plug_options)
            self.instances.append(plug)
            return plug

        changed = patch('sparc_controller.connections_service.ShellyClient',side_effect=factory)
        changed.start()
        self.addCleanup(changed.stop)
        self.fleet = FakeFleet(lambda:self.now)
        self.service.tesla = self.fleet

    async def save_plug(self,address='192.168.1.80'):
        revision = self.service.connections_view()['revision']
        result = await self.request('a','set_connection_settings',{'mode':'smart_plug','expectedRevision':revision,
            'smartPlug':{'address':address,'vehicleSource':'virtual'}})
        self.assertTrue(result['ok'])
        return result

    async def ready_tesla(self):
        self.fleet.state.update(authorized=True,status='connected',vehicleId='123',
                                vehicleName='Actual Fleet vehicle',commandsReady=True)

    async def test_tesla_activation_is_read_only_and_preserves_virtual_campaign_data(self):
        await self.ready_tesla()
        before = deepcopy(self.service.settings)
        result = await self.request('a','activate_connection',{'mode':'tesla'})
        self.assertTrue(result['ok'],result)
        self.assertTrue(result['body']['storedOnly'])
        self.assertEqual(result['body']['connections']['activeMode'],'tesla')
        self.assertFalse(self.service.settings['campaign']['enabled'])
        self.assertEqual(self.service.settings['campaign']['revision'],before['campaign']['revision']+1)
        for key in ('virtualCarState','overnightRun','weekly','today','appTestControl','smartChargePlan'):
            self.assertEqual(self.service.settings.get(key),before.get(key))
        self.assertEqual(self.fleet.calls,[('check_readiness',None),('charge_state',None)])
        self.assertEqual((self.path/'identity.json').read_bytes(),self.identity_bytes)
        self.now += timedelta(minutes=1)
        self.service.advance_virtual()
        self.assertEqual(self.service.settings['virtualCarState'],before['virtualCarState'])
        history = await self.request('a','get_history')
        self.assertTrue(all(session['simulated'] for session in history['body']['sessions']))
        back = await self.request('a','activate_connection',{'mode':'virtual'})
        self.assertTrue(back['ok'])
        self.assertFalse(self.service.settings['campaign']['enabled'])
        self.assertEqual(self.service.settings['virtualCarState'],before['virtualCarState'])

    async def test_activation_requires_ready_fresh_vehicle_data_and_failed_save_does_not_switch(self):
        before = deepcopy(self.service.settings)
        denied = await self.request('a','activate_connection',{'mode':'tesla'})
        self.assertFalse(denied['ok'])
        self.assertEqual(self.service.settings,before)
        await self.ready_tesla()
        self.fleet.stale = True
        denied = await self.request('a','activate_connection',{'mode':'tesla'})
        self.assertFalse(denied['ok'])
        self.assertEqual(self.service.settings,before)
        self.fleet.stale = False
        with patch.object(self.service,'_connection_save',side_effect=OSError('disk full')):
            denied = await self.request('a','activate_connection',{'mode':'tesla'})
        self.assertFalse(denied['ok'])
        self.assertEqual(self.service.active_connection_mode,'virtual')
        self.assertEqual(self.service.settings,before)
        self.assertFalse(any(name=='command' for name,_ in self.fleet.calls))

    async def test_tesla_home_commands_are_real_cached_snapshots_are_not_paid_polls(self):
        await self.ready_tesla()
        await self.request('a','activate_connection',{'mode':'tesla'})
        before = deepcopy(self.service.settings['virtualCarState'])
        calls = len(self.fleet.calls)
        for _ in range(3):
            snap = await self.request('a','get_snapshot')
            self.assertTrue(snap['ok'])
            self.assertFalse(snap['body']['virtualCar'])
            self.assertTrue(snap['body']['manualOnly'])
            self.assertFalse(snap['body']['scheduleAvailable'])
            self.assertEqual(snap['body']['batteryPercent'],62)
        self.assertEqual(len(self.fleet.calls),calls)
        for operation,name in (('charge_now','charge_start'),('pause','charge_stop'),('resume','charge_start'),('end','charge_stop')):
            result = await self.request('b',operation)
            self.assertTrue(result['ok'],result)
            self.assertTrue(result['body']['accepted'])
            self.assertEqual(result['body']['command'],name)
        self.assertEqual(self.service.settings['virtualCarState'],before)
        self.assertEqual(self.service.settings['controlState']['sequence'],5)
        self.assertEqual(self.service.settings['controlState']['clientId'],self.apps['b']['id'])
        self.now += timedelta(minutes=3)
        stale = (await self.request('a','get_snapshot'))['body']
        self.assertEqual(stale['state'],'offline')
        self.assertEqual(stale['powerKW'],0)
        self.assertTrue(stale['telemetryStale'])
        self.assertEqual(self.service.display_state()['snapshot']['state'],'offline')

    async def test_real_failures_never_change_virtual_state_or_retry_commands(self):
        await self.ready_tesla()
        await self.request('a','activate_connection',{'mode':'tesla'})
        before = deepcopy(self.service.settings)
        self.fleet.fail_command = True
        result = await self.request('a','pause')
        self.assertFalse(result['ok'])
        self.assertEqual(self.service.settings,before)
        self.assertEqual(sum(name=='command' for name,_ in self.fleet.calls),1)
        self.fleet.fail_command = False
        self.fleet.stale = True
        result = await self.request('a','charge_now')
        self.assertFalse(result['ok'])
        self.assertEqual(sum(name=='command' for name,_ in self.fleet.calls),1)
        self.fleet.stale = False
        self.fleet.fail_read = True
        accepted = await self.request('a','pause')
        self.assertTrue(accepted['ok'])
        self.assertTrue(accepted['body']['accepted'])
        self.assertIn('Refresh',accepted['body']['warning'])
        self.assertEqual(self.service.settings['virtualCarState'],before['virtualCarState'])

    async def test_real_commands_require_activation_validate_payload_and_persist_across_restart(self):
        await self.ready_tesla()
        denied = await self.request('a','tesla_command',{'command':'charge_stop'})
        self.assertFalse(denied['ok'])
        await self.request('a','activate_connection',{'mode':'tesla'})
        for body in ({'command':'wake_up'},{'command':'charge_stop','value':None},
                     {'command':'set_charge_limit','value':True},{'command':'set_charge_limit','value':49},
                     {'command':'charge_start','extra':True}):
            self.assertFalse((await self.request('a','tesla_command',body))['ok'])
        self.assertFalse(any(name=='command' for name,_ in self.fleet.calls))
        accepted = await self.request('a','tesla_command',{'command':'set_charge_limit','value':80})
        self.assertTrue(accepted['ok'])
        self.service = self.make_service()
        self.channels = self.make_channels()
        self.assertEqual(self.service.active_connection_mode,'tesla')
        self.assertFalse(self.service.settings['campaign']['enabled'])
        self.assertEqual((await self.request('a','get_snapshot'))['body']['state'],'offline')
        self.assertFalse((await self.request('a','pause'))['ok'])

    async def test_default_view_preserves_legacy_settings_car_history_and_identity(self):
        before = deepcopy(self.service.settings)
        settings_bytes = (self.path/'settings.json').read_bytes()
        result = await self.request('a','get_settings')
        self.assertEqual(result['body']['connections']['mode'],'virtual')
        self.assertEqual(result['body']['connections']['virtual']['model'],'cybertruck')
        self.assertEqual(result['body']['connections']['activeMode'],'virtual')
        self.assertEqual(self.service.settings,before)
        self.assertEqual((self.path/'settings.json').read_bytes(),settings_bytes)
        self.assertEqual((self.path/'identity.json').read_bytes(),self.identity_bytes)
        self.assertFalse((self.path/'history').exists())
        self.assertNotIn('connection',self.service.settings)
        self.assertEqual(self.fleet.calls,[])

    async def test_profile_only_save_preserves_progress_that_moved_since_the_form_was_read(self):
        previous = deepcopy(self.service.settings)
        anchor = self.service._virtual_anchor
        self.now += timedelta(minutes=1)
        expected = self.service.app_test.advance_virtual(previous,anchor,self.now)
        result = await self.request('a','set_connection_settings',{'mode':'virtual','expectedRevision':0,
            'virtual':{'model':'model_3'}})
        self.assertTrue(result['ok'])
        self.assertGreater(expected['batteryPercent'],previous['virtualCarState']['batteryPercent'])
        self.assertEqual(self.service.settings['virtualCarState'],expected)
        self.assertEqual(self.service.settings['overnightRun'],previous['overnightRun'])
        self.assertEqual(self.service.settings['appTestControl'],previous['appTestControl'])
        self.assertEqual(result['body']['connections']['virtual']['model'],'model_3')
        self.assertTrue(result['body']['connections']['virtual']['profileOnly'])
        self.assertEqual(self.service.settings['overnightRun']['simulatedCapacityKWh'],100)

    async def test_explicit_soc_save_archives_closed_session_without_rewriting_eighty_percent_history(self):
        self.now = fixtures.at('2026-11-06T12:59:00Z')
        self.service._virtual_anchor = self.now
        buckets = {'2026-11-06T04:00:00+00:00':66.6666666667}
        self.service.settings['virtualCarState'].update(batteryPercent=80,energyKWh=66.6666666667,energyByHour=buckets)
        await self.service.capture_history(force=True)
        self.service.settings['overnightRun']['finalizedAt']='2026-11-06T13:06:00Z'
        self.now = fixtures.at('2026-11-06T14:00:00Z')
        self.service._virtual_anchor = self.now
        before = deepcopy(self.service.settings['virtualCarState'])
        atomic_save(self.path/'settings.json',self.service.settings)
        result = await self.request('a','set_connection_settings',{'mode':'virtual','expectedRevision':0,
            'virtual':{'model':'model_x','batteryPercent':20,'pluggedIn':False}})
        self.assertTrue(result['ok'])
        self.assertEqual(result['body']['connections']['virtual']['batteryPercent'],20)
        for key in ('energyKWh','energyByHour','cost','immediateCost'):
            self.assertEqual(self.service.settings['virtualCarState'][key],before[key])
        history = (await self.request('b','get_history'))['body']['sessions'][0]
        self.assertEqual(history['batteryPercent'],80)
        self.assertAlmostEqual(history['energyKWh'],66.6666666667,places=5)
        self.assertTrue(history['targetReachedByDeadline'])

    async def test_probe_is_read_only_saves_secret_privately_and_never_exposes_it_publicly(self):
        await self.save_plug()
        before = deepcopy(self.service.settings['virtualCarState'])
        result = await self.request('b','check_smart_plug',{'password':'not-a-real-password'})
        self.assertTrue(result['ok'])
        self.assertTrue(result['body']['readOnlyProbe'])
        self.assertEqual(self.instances[0].calls,[('probe',None)])
        secret = self.path/'smart-plug-secret.json'
        self.assertEqual(secret.stat().st_mode & 0o777,0o600)
        self.assertEqual(json.loads(secret.read_text())['password'],'not-a-real-password')
        self.assertNotIn('not-a-real-password',json.dumps(result))
        public = await self.request('a','get_settings')
        self.assertNotIn('not-a-real-password',json.dumps(public))
        self.assertTrue(public['body']['connections']['smartPlug']['verified'])
        self.assertEqual(public['body']['control']['sequence'],1)  # only the settings save is a control edit
        self.assertEqual(public['body']['connections']['activeMode'],'virtual')
        self.assertEqual(self.service.settings['virtualCarState'],before)

    async def test_address_save_waits_for_probe_and_does_not_reuse_old_verification(self):
        await self.save_plug()
        entered,release = asyncio.Event(),asyncio.Event()
        self.plug_options.update(block=release,entered=entered)
        probing = asyncio.create_task(self.request('a','check_smart_plug',{}))
        await asyncio.wait_for(entered.wait(),1)
        saving = asyncio.create_task(self.request('b','set_connection_settings',{'mode':'smart_plug','expectedRevision':1,
            'smartPlug':{'address':'192.168.1.81','vehicleSource':'virtual'}}))
        await asyncio.sleep(0)
        self.assertFalse(saving.done())
        self.assertEqual(self.service.connections_view()['smartPlug']['address'],'192.168.1.80')
        release.set()
        probed,saved = await asyncio.gather(probing,saving)
        self.assertTrue(probed['ok']);self.assertTrue(saved['ok'])
        public = saved['body']['connections']['smartPlug']
        self.assertEqual(public['address'],'192.168.1.81')
        self.assertFalse(public['verified'])
        self.assertEqual(public['status'],'unverified')
        command = await self.request('a','set_smart_plug_power',{'on':True})
        self.assertFalse(command['ok'])
        self.assertFalse(any(call[0]=='set' for plug in self.instances for call in plug.calls))

    async def test_manual_on_requires_current_verified_device_and_updates_shared_writer_only_on_acceptance(self):
        await self.save_plug()
        rejected = await self.request('b','set_smart_plug_power',{'on':True})
        self.assertFalse(rejected['ok'])
        self.assertEqual(self.service.settings['controlState']['sequence'],1)
        self.assertEqual(self.instances,[])
        await self.request('a','check_smart_plug',{})
        accepted = await self.request('b','set_smart_plug_power',{'on':True})
        self.assertTrue(accepted['ok'])
        self.assertEqual([call for call in self.instances[0].calls if call[0]=='set'],[('set',True)])
        self.assertTrue(accepted['body']['connections']['smartPlug']['switchOn'])
        self.assertEqual(accepted['body']['control']['sequence'],2)
        self.assertEqual(accepted['body']['control']['clientId'],self.apps['b']['id'])

    async def test_failed_probe_invalidates_old_client_without_bad_sequence_or_raw_error(self):
        await self.save_plug()
        await self.request('a','check_smart_plug',{})
        old = self.instances[0]
        self.plug_options['fail']=True
        failed = await self.request('b','check_smart_plug',{'password':'not-a-real-password'})
        self.assertFalse(failed['ok'])
        self.assertNotIn('raw-private-password-error',json.dumps(failed))
        self.assertEqual(self.service.connections_view()['smartPlug']['status'],'error')
        self.assertEqual(self.service.settings['controlState']['sequence'],1)
        self.assertIsNone(self.service._shelly_client)
        self.assertFalse((await self.request('a','set_smart_plug_power',{'on':True}))['ok'])
        self.assertFalse(any(call[0]=='set' for call in old.calls))

    async def demo_plug(self,charging=True):
        await self.save_plug()
        await self.request('a','check_smart_plug',{})
        revision = self.service.connections_view()['revision']
        self.assertTrue((await self.request('a','set_connection_settings',{'mode':'smart_plug','expectedRevision':revision,'smartPlug':{'followVirtual':True}}))['ok'])
        self.service.app_test.state_at = lambda *a: ('charging' if charging else 'waiting',None)
        return self.instances[0]

    @staticmethod
    def sets(plug):
        return [call for call in plug.calls if call[0]=='set']

    async def test_plug_demo_follows_virtual_charging_once(self):
        plug = await self.demo_plug(True)
        await self.service.sync_plug_demo()
        await self.service.sync_plug_demo()
        self.assertEqual(self.sets(plug),[('set',True)])
        self.service.app_test.state_at = lambda *a: ('waiting',None)
        await self.service.sync_plug_demo()
        self.assertEqual(self.sets(plug),[('set',True),('set',False)])

    async def test_plug_demo_ignored_when_plug_unverified_or_already_in_state(self):
        await self.save_plug()
        revision = self.service.connections_view()['revision']
        self.assertTrue((await self.request('a','set_connection_settings',{'mode':'smart_plug','expectedRevision':revision,'smartPlug':{'followVirtual':True}}))['ok'])
        self.service.app_test.state_at = lambda *a: ('charging',None)
        await self.service.sync_plug_demo()
        self.assertEqual(self.instances,[])
        await self.request('a','check_smart_plug',{})
        plug = self.instances[0]
        plug.on = True
        await self.service.refresh_connections()
        await self.service.sync_plug_demo()
        self.assertEqual(self.sets(plug),[])

    async def test_turning_demo_off_switches_outlet_off_once(self):
        plug = await self.demo_plug(True)
        await self.service.sync_plug_demo()
        revision = self.service.connections_view()['revision']
        self.assertTrue((await self.request('a','set_connection_settings',{'mode':'smart_plug','expectedRevision':revision,'smartPlug':{'followVirtual':False}}))['ok'])
        await self.service.sync_plug_demo()
        await self.service.sync_plug_demo()
        self.assertEqual(self.sets(plug),[('set',True),('set',False)])

    async def test_refresh_connections_reads_only_local_meter_and_never_polls_tesla_or_actuates(self):
        await self.service.refresh_connections()
        self.assertEqual(self.fleet.calls,[])
        await self.save_plug()
        await self.request('a','check_smart_plug',{})
        await self.service.refresh_connections()
        await self.service.refresh_connections()
        self.assertEqual(self.instances[0].calls,[('probe',None),('status',None),('status',None)])
        self.assertEqual(self.fleet.calls,[])
        self.assertEqual(self.service.settings['controlState']['sequence'],1)

    async def test_saved_device_identity_mismatch_fails_closed_after_restart_cache_loss(self):
        await self.save_plug()
        await self.request('a','check_smart_plug',{})
        self.service._shelly_client=None
        self.plug_options['device_id']='different-shelly-identity'
        await self.service.refresh_connections()
        self.assertIsNone(self.service._shelly_client)
        self.assertEqual(self.service.connections_view()['smartPlug']['status'],'error')
        self.assertEqual(self.service.settings['controlState']['sequence'],1)
        self.assertEqual(self.fleet.calls,[])

    async def test_tesla_explicit_setup_actions_share_metadata_without_touching_virtual_campaign(self):
        before = deepcopy(self.service.settings['virtualCarState'])
        started = await self.request('a','tesla_authorize',{})
        self.assertTrue(started['ok'])
        self.assertIn('authorizationURL',started['body'])
        completed = await self.request('a','tesla_complete_authorization',{'code':'fake-code','state':'fake-state'})
        self.assertTrue(completed['ok'])
        self.assertTrue(completed['body']['connections']['tesla']['authorized'])
        self.assertEqual(completed['body']['control']['sequence'],1)
        refreshed = await self.request('b','tesla_refresh',{})
        self.assertEqual(refreshed['body']['connections']['tesla']['vehicles'][0]['id'],'123')
        self.assertEqual(refreshed['body']['control']['sequence'],1)
        selected = await self.request('b','tesla_select_vehicle',{'vehicleId':'123'})
        self.assertTrue(selected['ok'])
        self.assertEqual(selected['body']['control']['sequence'],2)
        self.assertEqual(selected['body']['control']['clientId'],self.apps['b']['id'])
        self.assertEqual(selected['body']['connections']['tesla']['chargeState']['batteryPercent'],62)
        self.assertFalse(selected['body']['connections']['tesla']['commandsReady'])
        self.assertEqual(selected['body']['connections']['activeMode'],'virtual')
        disconnected = await self.request('a','tesla_disconnect',{})
        self.assertTrue(disconnected['ok'])
        self.assertFalse(disconnected['body']['connections']['tesla']['authorized'])
        self.assertEqual(disconnected['body']['control']['sequence'],3)
        self.assertEqual(self.service.settings['virtualCarState'],before)
        self.assertEqual(self.fleet.calls[0],('authorize_start',self.apps['a']['id']))
        self.assertFalse(any(name=='command' for name,_ in self.fleet.calls))

    async def test_tesla_account_in_use_cannot_be_removed_and_one_account_can_be_removed_alone(self):
        await self.ready_tesla()
        self.fleet.state.update(accountId='abc123',accounts=[{'id':'abc123'},{'id':'def456'}])
        activated = await self.request('a','activate_connection',{'mode':'tesla'})
        self.assertTrue(activated['ok'],activated)
        for body in ({},{'accountId':'abc123'}):
            denied = await self.request('a','tesla_disconnect',body)
            self.assertFalse(denied['ok'])
            self.assertIn('virtual car',denied['error']['message'])
        other = await self.request('a','tesla_disconnect',{'accountId':'def456'})
        self.assertTrue(other['ok'],other)
        self.assertEqual(self.fleet.calls[-1],('disconnect','def456'))
        for body in ({'accountId':'../x'},{'accountId':7},{'accountId':'abc','extra':1}):
            bad = await self.request('a','tesla_disconnect',body)
            self.assertFalse(bad['ok'])
        self.assertFalse(any(call == ('disconnect',None) for call in self.fleet.calls))
        back = await self.request('a','activate_connection',{'mode':'virtual'})
        self.assertTrue(back['ok'])
        removed = await self.request('a','tesla_disconnect',{})
        self.assertTrue(removed['ok'])
        self.assertEqual(self.fleet.calls[-1],('disconnect',None))

    async def test_local_name_save_pins_private_ip_without_changing_input_or_probing_hardware(self):
        body = {'mode':'smart_plug','expectedRevision':0,
                'smartPlug':{'address':'ShellyPlugUSG4-AABBCCDDEEFF.local','vehicleSource':'virtual'}}
        original = deepcopy(body)
        answer = [(socket.AF_INET,socket.SOCK_STREAM,socket.IPPROTO_TCP,'',('192.168.1.80',80))]
        with patch('sparc_controller.connections_service.socket.getaddrinfo',return_value=answer) as resolver:
            result = await self.service.dispatch('set_connection_settings',body,'lan',client=self.apps['a'])
        resolver.assert_called_once_with('shellyplugusg4-aabbccddeeff.local',80,socket.AF_INET,socket.SOCK_STREAM)
        self.assertEqual(body,original)
        self.assertEqual(result['connections']['smartPlug']['address'],'192.168.1.80')
        self.assertEqual(self.service.settings['connection']['smartPlug']['address'],'192.168.1.80')
        self.assertEqual(self.instances,[])
        self.assertFalse((self.path/'smart-plug-secret.json').exists())
        # Verification uses only the pinned IP and never resolves the name again.
        with patch('sparc_controller.connections_service.socket.getaddrinfo',side_effect=AssertionError('Unexpected DNS')):
            probed = await self.request('a','check_smart_plug',{})
        self.assertTrue(probed['ok'])
        self.assertEqual(self.instances[0].address,'192.168.1.80')


class ShellyLocalResolutionTests(unittest.IsolatedAsyncioTestCase):
    async def test_numeric_addresses_and_clear_value_do_not_use_dns(self):
        with patch('sparc_controller.connections_service.socket.getaddrinfo') as resolver:
            self.assertEqual(await resolve_shelly_address('192.168.1.80'),'192.168.1.80')
            self.assertEqual(await resolve_shelly_address('fd00::1'),'fd00::1')
            self.assertIsNone(await resolve_shelly_address(None))
            self.assertIsNone(await resolve_shelly_address(''))
        resolver.assert_not_called()

    async def test_first_private_ipv4_is_pinned_and_unsafe_answers_are_never_used(self):
        answers = [(socket.AF_INET,socket.SOCK_STREAM,socket.IPPROTO_TCP,'',(ip,80))
                   for ip in ('8.8.8.8','127.0.0.1','169.254.169.254','192.168.1.81','10.1.2.3')]
        with patch('sparc_controller.connections_service.socket.getaddrinfo',return_value=answers) as resolver:
            self.assertEqual(await resolve_shelly_address('shelly-plug.local'),'192.168.1.81')
        resolver.assert_called_once_with('shelly-plug.local',80,socket.AF_INET,socket.SOCK_STREAM)

    async def test_unsafe_names_urls_credentials_and_public_numeric_ips_never_reach_dns(self):
        invalid = ('http://shelly.local','https://admin:private-password@shelly.local/rpc',
                   'admin:private-password@shelly.local','shelly.local:8080','shelly.local/rpc',
                   '-plug.local','plug-.local','plug.local.evil.com','nested.plug.local','plug_local.local',
                   'p'*59+'.local','8.8.8.8','127.0.0.1','169.254.169.254','::1','plug.local\n')
        with patch('sparc_controller.connections_service.socket.getaddrinfo') as resolver:
            for value in invalid:
                with self.subTest(value=value), self.assertRaises(ValueError) as caught:
                    await resolve_shelly_address(value)
                self.assertNotIn('private-password',str(caught.exception))
        resolver.assert_not_called()

    async def test_missing_public_loopback_linklocal_or_malformed_answers_fail_closed(self):
        samples = ([],[(socket.AF_INET,socket.SOCK_STREAM,6,'',('8.8.8.8',80))],
                   [(socket.AF_INET,socket.SOCK_STREAM,6,'',('127.0.0.1',80))],
                   [(socket.AF_INET,socket.SOCK_STREAM,6,'',('169.254.1.1',80))],
                   [(socket.AF_INET6,socket.SOCK_STREAM,6,'',('fd00::1',80))],
                   [('malformed',)],[(socket.AF_INET,socket.SOCK_STREAM,6,'',('192.168.1.1',80))]*65)
        for answers in samples:
            with self.subTest(answers=answers), patch('sparc_controller.connections_service.socket.getaddrinfo',return_value=answers), self.assertRaises(ValueError):
                await resolve_shelly_address('plug.local')

    async def test_resolution_timeout_and_os_error_are_bounded_and_sanitized(self):
        async def expire(awaitable,timeout):
            self.assertEqual(timeout,3)
            awaitable.close()
            raise asyncio.TimeoutError()

        with patch('sparc_controller.connections_service.asyncio.wait_for',side_effect=expire), self.assertRaises(ValueError):
            await resolve_shelly_address('plug.local')
        with patch('sparc_controller.connections_service.socket.getaddrinfo',side_effect=socket.gaierror('raw-private-error')):
            with self.assertRaises(ValueError) as caught:
                await resolve_shelly_address('plug.local')
        self.assertNotIn('raw-private-error',str(caught.exception))


if __name__ == '__main__':
    unittest.main()
