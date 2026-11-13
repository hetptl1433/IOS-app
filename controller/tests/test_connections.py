from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import json
import unittest
from urllib.request import parse_http_list, parse_keqv_list

from sparc_controller.connection import normalize, updated, view
from sparc_controller.shelly import HTTPReply, MODEL, ShellyClient, ShellyError, local_address

NOW = datetime(2026,11,6,17,tzinfo=timezone.utc)
IDENT = 'shellyplugusg4-aabbccddeeff'


class FakePlug:
    def __init__(self, auth=False):
        self.auth = auth
        self.calls = []
        self.model = MODEL
        self.set_status = 200
        self.meter = {'id':0,'output':False,'apower':0,'voltage':120.1,'current':0,'aenergy':{'total':1234.5}}

    async def __call__(self,address,payload,headers,timeout):
        value = json.loads(payload)
        self.calls.append((value,headers))
        method = value['method']
        if method == 'Shelly.GetDeviceInfo':
            result = {'id':IDENT,'model':self.model,'gen':4,'auth_en':self.auth,'auth_domain':IDENT if self.auth else None,
                      'cloudSecret':'must-not-escape'}
        elif self.auth and not headers.get('Authorization'):
            return HTTPReply(401,{'www-authenticate':f'Digest qop="auth", realm="{IDENT}", nonce="abc123", algorithm=SHA-256'},b'')
        elif method == 'Switch.GetStatus':
            result = self.meter
        else:
            if self.set_status != 200:
                return HTTPReply(self.set_status,{},b'raw-secret-error')
            result = {'was_on':False}
        return HTTPReply(200,{},json.dumps({'id':value['id'],'src':IDENT,'result':result}).encode())


class ConnectionDomainTests(unittest.TestCase):
    def setUp(self):
        self.settings = {'virtualCarEnabled':True,'virtualCarState':{'batteryPercent':44,'pluggedIn':True,'energyKWh':23,
                         'cost':-0.2,'immediateCost':0,'energyByHour':{'2026-11-06T04:00:00+00:00':23}},
                         'appTestControl':{'manualState':'paused','manualUntil':None},
                         'overnightRun':{'initialBatteryPercent':20},'unrelated':{'retain':True}}

    def test_defaults_and_profile_only_edit_preserve_accounting_and_manual_pause(self):
        self.assertEqual(normalize()['mode'],'virtual')
        before = deepcopy(self.settings)
        result = updated(self.settings,{'mode':'virtual','virtual':{'model':'model_3'},'expectedRevision':0},NOW)
        self.assertEqual(result['virtualCarState'],before['virtualCarState'])
        self.assertEqual(result['appTestControl'],before['appTestControl'])
        self.assertEqual(result['overnightRun'],before['overnightRun'])
        self.assertEqual(self.settings,before)
        self.assertTrue(view(result)['virtual']['profileOnly'])
        self.assertEqual(view(result)['virtual']['model'],'model_3')

    def test_explicit_soc_calibration_changes_no_measured_energy_or_initial_battery(self):
        result = updated(self.settings,{'mode':'virtual','virtual':{'batteryPercent':20,'pluggedIn':False},'expectedRevision':0},NOW)
        self.assertEqual(result['virtualCarState']['batteryPercent'],20)
        for key in ('energyKWh','cost','immediateCost','energyByHour'):
            self.assertEqual(result['virtualCarState'][key],self.settings['virtualCarState'][key])
        self.assertEqual(result['overnightRun']['initialBatteryPercent'],20)
        self.assertEqual(result['appTestControl'],self.settings['appTestControl'])

    def test_follow_virtual_defaults_false_validates_and_is_exposed(self):
        old = deepcopy(self.settings)
        old['connection'] = {**normalize(None),'smartPlug':{'address':None,'vehicleSource':'virtual'}}
        self.assertIs(normalize(old['connection'])['smartPlug']['followVirtual'],False)
        result = updated(old,{'mode':'virtual','smartPlug':{'followVirtual':True},'expectedRevision':0},NOW)
        self.assertIs(result['connection']['smartPlug']['followVirtual'],True)
        self.assertIs(view(result)['smartPlug']['followVirtual'],True)
        with self.assertRaises(ValueError):
            updated(old,{'mode':'virtual','smartPlug':{'followVirtual':1},'expectedRevision':0},NOW)

    def test_saved_real_mode_is_not_hardware_readiness_and_public_view_omits_secrets(self):
        result = updated(self.settings,{'mode':'smart_plug','smartPlug':{'address':'192.168.1.90','vehicleSource':'tesla'},'expectedRevision':0},NOW)
        public = view(result,{'tesla':{'configured':True,'authorized':False,'clientSecret':'secret'}})
        self.assertFalse(public['ready'])
        self.assertEqual(public['smartPlug']['status'],'unverified')
        self.assertNotIn('secret',json.dumps(public))
        self.assertEqual(result['virtualCarEnabled'],True)
        runtime = {'smartPlug':{'address':'192.168.1.90','model':MODEL,'metering':True,'status':'connected','powerW':0,'lastCheckedAt':NOW.isoformat()},
                   'tesla':{'configured':True,'authorized':True,'commandsReady':False}}
        self.assertTrue(view(result,runtime,now=NOW)['ready'])
        result['connection']['smartPlug']['address'] = '192.168.1.91'
        self.assertFalse(view(result,runtime,now=NOW)['ready'])

    def test_invalid_inputs_and_secret_fields_are_rejected(self):
        for body in ({'mode':'fake','expectedRevision':0}, {'mode':'virtual','expectedRevision':True},
                     {'mode':'virtual','expectedRevision':1}, {'mode':'virtual','expectedRevision':0,'password':'secret'},
                     {'mode':'virtual','expectedRevision':0,'virtual':{'capacityKWh':120}},
                     {'mode':'smart_plug','expectedRevision':0,'smartPlug':{'address':'http://192.168.1.2/rpc'}}):
            with self.subTest(body=body), self.assertRaises(ValueError):
                updated(self.settings,body,NOW)

    def test_stale_metering_is_not_connected_and_authentication_errors_remain_visible(self):
        result = updated(self.settings,{'mode':'smart_plug','smartPlug':{'address':'192.168.1.90'},'expectedRevision':0},NOW)
        runtime = {'smartPlug':{'address':'192.168.1.90','model':MODEL,'metering':True,'status':'connected',
                               'powerW':100,'lastCheckedAt':(NOW-timedelta(seconds=121)).isoformat()},
                   'tesla':{'chargeState':{'batteryPercent':62,'updatedAt':(NOW-timedelta(minutes=3)).isoformat()}}}
        public = view(result,runtime,now=NOW)
        self.assertEqual(public['smartPlug']['status'],'stale')
        self.assertIsNone(public['smartPlug']['powerW'])
        self.assertFalse(public['ready'])
        self.assertTrue(public['tesla']['chargeState']['stale'])
        runtime['smartPlug'].update(status='auth_required',error='The local plug requires its password.')
        public = view(result,runtime,now=NOW)
        self.assertEqual(public['smartPlug']['status'],'auth_required')
        self.assertEqual(public['smartPlug']['error'],'The local plug requires its password.')


class ShellyClientTests(unittest.IsolatedAsyncioTestCase):
    async def test_probe_is_read_only_exact_model_and_meter_units_are_preserved(self):
        plug = FakePlug()
        client = ShellyClient('192.168.1.90',transport=plug,clock=lambda:NOW)
        result = await client.probe()
        self.assertEqual([value['method'] for value,_ in plug.calls],['Shelly.GetDeviceInfo','Switch.GetStatus'])
        self.assertEqual(result['model'],MODEL)
        self.assertEqual(result['totalEnergyWh'],1234.5)
        self.assertEqual(result['powerW'],0)
        self.assertNotIn('cloudSecret',result)
        self.assertNotIn('authDomain',result)

    async def test_unsupported_device_or_missing_metering_can_never_be_controlled(self):
        plug = FakePlug()
        client = ShellyClient('192.168.1.90',transport=plug)
        with self.assertRaises(ShellyError):
            await client.set_power(True)
        self.assertEqual(plug.calls,[])
        plug.model = 'SNPL-00116US'
        with self.assertRaises(ShellyError):
            await client.probe()
        plug.model = MODEL
        plug.meter.pop('aenergy')
        with self.assertRaises(ShellyError):
            await client.probe()
        with self.assertRaises(ShellyError):
            await client.set_power(True)
        self.assertNotIn('Switch.Set',[value['method'] for value,_ in plug.calls])

    async def test_sha256_digest_auth_uses_real_http_method_and_uri(self):
        plug = FakePlug(auth=True)
        client = ShellyClient('192.168.1.90',password='private-password',transport=plug)
        await client.probe()
        header = plug.calls[-1][1]['Authorization']
        fields = parse_keqv_list(parse_http_list(header[7:]))
        digest = lambda value:hashlib.sha256(value.encode()).hexdigest()
        expected = digest(':'.join((digest('admin:'+IDENT+':private-password'),'abc123',fields['nc'],fields['cnonce'],'auth',digest('POST:/rpc'))))
        self.assertEqual(fields['response'],expected)
        self.assertNotIn('private-password',header)
        await client.set_power(True)
        mutations = [value for value,_ in plug.calls if value['method']=='Switch.Set']
        self.assertEqual(mutations[0]['params'],{'id':0,'on':True})

    async def test_mutation_is_not_retried_after_authentication_failure(self):
        plug = FakePlug(auth=True)
        client = ShellyClient('192.168.1.90',password='private-password',transport=plug)
        await client.probe()
        plug.set_status = 401
        with self.assertRaises(ShellyError) as caught:
            await client.set_power(False)
        self.assertEqual(sum(value['method']=='Switch.Set' for value,_ in plug.calls),1)
        self.assertNotIn('raw-secret-error',str(caught.exception))
        self.assertNotIn('private-password',str(caught.exception))

    async def test_faults_block_switch_on_but_not_explicit_switch_off(self):
        plug = FakePlug()
        plug.meter['errors'] = ['overtemp']
        client = ShellyClient('192.168.1.90',transport=plug)
        await client.probe()
        with self.assertRaises(ShellyError):
            await client.set_power(True)
        self.assertEqual(sum(value['method']=='Switch.Set' for value,_ in plug.calls),0)
        self.assertTrue((await client.set_power(False))['accepted'])

    async def test_public_ip_urls_ports_and_arbitrary_methods_are_rejected(self):
        for address in ('8.8.8.8','127.0.0.1','169.254.169.254','example.com','http://192.168.1.2','192.168.1.2:8080','192.168.1.2/rpc','::1'):
            with self.subTest(address=address), self.assertRaises(ShellyError):
                local_address(address)
        self.assertEqual(local_address('192.168.1.2'),'192.168.1.2')
        self.assertEqual(local_address('fd00::1'),'fd00::1')
        client = ShellyClient('192.168.1.2',transport=FakePlug())
        with self.assertRaises(ShellyError):
            await client._rpc('Shelly.SetAuth',{'password':'secret'},mutation=True)
