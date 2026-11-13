import asyncio
import base64
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import ssl
import tempfile
import unittest
from urllib.parse import parse_qs, urlsplit
import uuid

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

from sparc_controller.tesla import (AUDIENCES, PROXY_URL, SCOPES, TOKEN_URL, TeslaAdapter,
                                    TeslaError, _default_save)

VIN = '5YJ3E1EA7KF123456'
VIN2 = '7SAYGDEE5PF654321'
VIN3 = '5YJYGDEE1LF000003'


def jwt(subject,count):
    """An unsigned JWT shaped like Tesla's: only 'sub' matters to the adapter."""
    def part(value):
        return base64.urlsafe_b64encode(json.dumps(value).encode()).decode().rstrip('=')
    return part({'alg':'none'})+'.'+part({'sub':subject})+'.access-'+str(count)


class FakeTesla:
    """Fleet API stand-in with several Tesla users; `user` is whoever signs in next."""
    def __init__(self,clock):
        self.clock = clock
        self.calls = []
        self.token_count = 0
        self.fail_refresh = False
        self.reject_command = False
        self.paired = False
        self.online = True
        self.fail_charge_read = False
        self.user = 'user-1'
        self.cars = {'user-1':[{'id_s':'123456789','vin':VIN,'display_name':'My Tesla'}]}
        self.owner = {}
        self.rejected = set()
        self.reject_code = False

    async def __call__(self,method,url,headers,data,timeout,context):
        self.calls.append((method,url,headers,data,context))
        await asyncio.sleep(0)
        if url == TOKEN_URL:
            form = parse_qs(data.decode())
            if self.fail_refresh and form['grant_type']==['refresh_token']:
                raise OSError('raw-secret-error')
            if self.reject_code and form['grant_type']==['authorization_code']:
                return 401,b'raw-secret-error'
            user = self.owner.get(form['refresh_token'][0],self.user) if form['grant_type']==['refresh_token'] else self.user
            self.token_count += 1
            access,refresh = jwt(user,self.token_count),f'refresh-{self.token_count}'
            self.owner[access] = self.owner[refresh] = user
            value = {'access_token':access,'refresh_token':refresh,
                     'expires_in':3600,'scope':' '.join(SCOPES),'token_type':'Bearer'}
        elif url.endswith('/health'):
            return 200,b'OK'
        elif url.endswith('/fleet_status'):
            value = {'response':{'key_paired_vins':json.loads(data)['vins'] if self.paired else [],'unpaired_vins':[]}}
        elif '/command/' in url:
            if self.reject_command:
                return 401,b'raw-secret-error'
            value = {'response':{'result':True}}
        elif '/vehicle_data?' in url:
            if self.fail_charge_read:
                return 408,b''
            value = {'response':{'charge_state':{'battery_level':62,'charging_state':'Charging','charger_power':7,
                      'timestamp':int(self.clock().timestamp()*1000)},'drive_state':{'location':'must-not-escape'}}}
        elif '/api/1/vehicles/' in url:
            value = {'response':{'state':'online' if self.online else 'asleep'}}
        else:
            user = self.owner.get(headers.get('Authorization','').removeprefix('Bearer '))
            if user in self.rejected:
                return 401,b'raw-secret-error'
            value = {'response':self.cars.get(user,[])}
        return 200,json.dumps(value).encode()


class TeslaAdapterTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.now = datetime(2026,11,6,17,tzinfo=timezone.utc)
        self.client = str(uuid.uuid4())
        self.other = str(uuid.uuid4())
        self.config = {'clientId':'687f9e0c-2855-4d0e-b617-780ebd52843a','clientSecret':'private-client-secret',
                       'redirectURI':'https://sparc.ipodeskai.com/auth/tesla/callback','audience':AUDIENCES[0],
                       'domain':'sparc.ipodeskai.com'}
        _default_save(self.root/'tesla-client.json',self.config)
        self.http = FakeTesla(lambda:self.now)
        self.adapter = TeslaAdapter(self.root,clock=lambda:self.now,transport=self.http)

    async def authorize(self,client=None):
        started = await self.adapter.authorize_start(client)
        state = parse_qs(urlsplit(started['authorizationURL']).query)['state'][0]
        await self.adapter.complete('private-code',state,client)
        return state

    async def test_minimal_scopes_secret_free_authorize_url_and_state_bound_single_use(self):
        started = await self.adapter.authorize_start(self.client)
        query = parse_qs(urlsplit(started['authorizationURL']).query)
        self.assertEqual(query['scope'][0].split(),list(SCOPES))
        self.assertNotIn('vehicle_location',query['scope'][0])
        self.assertNotIn('vehicle_cmds',query['scope'][0])
        self.assertNotIn('private-client-secret',started['authorizationURL'])
        state = query['state'][0]
        self.assertEqual(len(state),64)
        for candidate,client in (('0'*64,self.client),(state,self.other)):
            with self.assertRaises(TeslaError):
                await self.adapter.complete('private-code',candidate,client)
        self.assertEqual(self.http.calls,[])
        await self.adapter.complete('private-code',state,self.client)
        with self.assertRaises(TeslaError):
            await self.adapter.complete('private-code',state,self.client)
        self.assertEqual(self.http.token_count,1)
        call = self.http.calls[0]
        self.assertEqual(call[1],TOKEN_URL)
        self.assertNotIn('private-code',call[1])
        self.assertIn('client_secret',parse_qs(call[3].decode()))
        self.assertEqual((self.root/'tesla-tokens.json').stat().st_mode & 0o777,0o600)

    async def test_display_oauth_completion_may_use_an_authenticated_app_and_expiry_is_enforced(self):
        started = await self.adapter.authorize_start(None)
        state = parse_qs(urlsplit(started['authorizationURL']).query)['state'][0]
        result = await self.adapter.complete('private-code',state,self.other)
        self.assertTrue(result['authorized'])
        started = await self.adapter.authorize_start(self.client)
        state = parse_qs(urlsplit(started['authorizationURL']).query)['state'][0]
        self.now += timedelta(minutes=10)
        with self.assertRaises(TeslaError):
            await self.adapter.complete('private-code',state,self.client)
        self.assertEqual(self.http.token_count,1)

    async def test_expired_access_refresh_is_serialized_and_rotated_token_survives_restart(self):
        await self.authorize()
        self.now += timedelta(hours=2)
        first,second = await asyncio.gather(self.adapter.vehicles(),self.adapter.vehicles())
        self.assertEqual(first,second)
        refresh_calls = [parse_qs(call[3].decode()) for call in self.http.calls if call[1]==TOKEN_URL]
        self.assertEqual(len(refresh_calls),2)  # one authorization + one refresh
        self.assertEqual(refresh_calls[1]['refresh_token'],['refresh-1'])
        tokens = json.loads((self.root/'tesla-tokens.json').read_text())
        self.assertEqual(tokens['refreshToken'],'refresh-2')
        fresh = TeslaAdapter(self.root,clock=lambda:self.now,transport=self.http)
        self.assertTrue(fresh.public_status()['authorized'])
        self.assertFalse(fresh.public_status()['commandsReady'])
        self.assertNotIn('access-',json.dumps(fresh.public_status()))

    async def test_uncertain_refresh_token_is_not_reused_even_after_restart(self):
        await self.authorize()
        self.http.fail_refresh = True
        with self.assertRaises(TeslaError) as caught:
            await self.adapter.refresh()
        self.assertNotIn('raw-secret-error',str(caught.exception))
        count = len(self.http.calls)
        with self.assertRaises(TeslaError):
            await self.adapter.refresh()
        fresh = TeslaAdapter(self.root,clock=lambda:self.now,transport=self.http)
        self.assertFalse(fresh.public_status()['authorized'])
        with self.assertRaises(TeslaError):
            await fresh.refresh()
        self.assertEqual(len(self.http.calls),count)

    async def test_refresh_save_failure_does_not_reuse_the_spent_refresh_token(self):
        await self.authorize()
        saves = []

        def fail_second_save(path,value):
            saves.append(value)
            if len(saves)==2:
                raise OSError('full disk')
            _default_save(path,value)

        fresh = TeslaAdapter(self.root,save=fail_second_save,clock=lambda:self.now,transport=self.http)
        with self.assertRaises(OSError):
            await fresh.refresh()
        self.assertTrue(json.loads((self.root/'tesla-tokens.json').read_text())['refreshPending'])
        count = len(self.http.calls)
        with self.assertRaises(TeslaError):
            await fresh.refresh()
        self.assertEqual(len(self.http.calls),count)
        self.assertFalse(fresh.public_status()['authorized'])

    async def test_failed_authorization_exchange_consumes_state_without_retrying_code(self):
        calls = []

        async def reject(*args):
            calls.append(args)
            return 500,b'raw-secret-error'

        self.adapter = TeslaAdapter(self.root,clock=lambda:self.now,transport=reject)
        started = await self.adapter.authorize_start(self.client)
        state = parse_qs(urlsplit(started['authorizationURL']).query)['state'][0]
        with self.assertRaises(TeslaError) as caught:
            await self.adapter.complete('private-code',state,self.client)
        self.assertNotIn('raw-secret-error',str(caught.exception))
        with self.assertRaises(TeslaError):
            await self.adapter.complete('private-code',state,self.client)
        self.assertEqual(len(calls),1)
        self.assertFalse((self.root/'tesla-tokens.json').exists())

    async def test_vehicle_selection_is_owned_charge_read_is_narrow_and_authorization_is_not_command_readiness(self):
        await self.authorize()
        vehicles = await self.adapter.vehicles()
        account = self.adapter.public_status()['accounts'][0]['id']
        self.assertEqual(vehicles,[{'id':'123456789','vin':VIN,'displayName':'My Tesla','accountId':account}])
        with self.assertRaises(TeslaError):
            await self.adapter.select_vehicle('999')
        state = await self.adapter.select_vehicle('123456789')
        self.assertTrue(state['authorized'])
        self.assertFalse(state['commandsReady'])
        charge = await self.adapter.charge_state()
        self.assertEqual(charge['source'],'TeslaFleetAPI')
        self.assertEqual((charge['batteryPercent'],charge['pluggedIn'],charge['powerKW']),(62,True,7))
        self.assertNotIn('must-not-escape',json.dumps(charge))
        self.assertTrue(any(call[1].endswith('/vehicle_data?endpoints=charge_state') for call in self.http.calls))
        with self.assertRaises(TeslaError):
            await self.adapter.command('charge_start')
        self.assertFalse(any('/command/' in call[1] for call in self.http.calls))

    def proxy_files(self):
        private = ec.generate_private_key(ec.SECP256R1())
        key_path = self.root/'vehicle-key.pem'
        key_path.write_bytes(private.private_bytes(serialization.Encoding.PEM,serialization.PrivateFormat.PKCS8,serialization.NoEncryption()))
        key_path.chmod(0o600)
        subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME,'localhost')])
        cert = (x509.CertificateBuilder().subject_name(subject).issuer_name(subject).public_key(private.public_key())
                .serial_number(x509.random_serial_number()).not_valid_before(self.now-timedelta(days=1))
                .not_valid_after(self.now+timedelta(days=365)).add_extension(x509.BasicConstraints(ca=True,path_length=None),critical=True)
                .sign(private,hashes.SHA256()))
        ca_path = self.root/'proxy-ca.pem'
        ca_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
        self.config.update(commandProxyURL=PROXY_URL,commandProxyCA=str(ca_path),privateKeyPath=str(key_path))
        _default_save(self.root/'tesla-client.json',self.config)
        self.adapter = TeslaAdapter(self.root,clock=lambda:self.now,transport=self.http)

    async def test_mock_signed_proxy_requires_trusted_tls_valid_key_and_fleet_pairing_and_never_retries(self):
        self.proxy_files()
        self.http.paired = True
        await self.authorize()
        state = await self.adapter.select_vehicle('123456789')
        self.assertTrue(state['commandsReady'])
        result = await self.adapter.command('set_charge_limit',80)  # mocked transport; no actual vehicle
        self.assertTrue(result['accepted'])
        commands = [call for call in self.http.calls if '/command/' in call[1]]
        self.assertEqual(len(commands),1)
        self.assertEqual(commands[0][1],PROXY_URL+'/api/1/vehicles/'+VIN+'/command/set_charge_limit')
        self.assertEqual(commands[0][4].verify_mode,ssl.CERT_REQUIRED)
        self.assertTrue(commands[0][4].check_hostname)
        self.http.reject_command = True
        with self.assertRaises(TeslaError) as caught:
            await self.adapter.command('charge_stop')
        self.assertNotIn('raw-secret-error',str(caught.exception))
        self.assertEqual(sum('/command/' in call[1] for call in self.http.calls),2)

    async def test_disconnect_removes_only_local_tokens_and_pending_state_not_client_setup(self):
        await self.authorize()
        await self.adapter.vehicles()
        started = await self.adapter.authorize_start(self.client)
        result = await self.adapter.disconnect()
        self.assertTrue(result['configured'])
        self.assertFalse(result['authorized'])
        self.assertTrue(result['disconnectedLocally'])
        self.assertFalse((self.root/'tesla-tokens.json').exists())
        self.assertTrue((self.root/'tesla-client.json').exists())
        state = parse_qs(urlsplit(started['authorizationURL']).query)['state'][0]
        with self.assertRaises(TeslaError):
            await self.adapter.complete('private-code',state,self.client)

    async def test_explicit_readiness_never_actuates_and_offline_commands_do_not_wake_or_retry(self):
        self.proxy_files()
        self.http.paired = True
        await self.authorize()
        await self.adapter.select_vehicle('123456789')
        state = await self.adapter.check_readiness()
        self.assertTrue(state['commandsReady'])
        self.assertFalse(any('/command/' in call[1] for call in self.http.calls))
        self.http.online = False
        with self.assertRaisesRegex(TeslaError,'asleep or offline'):
            await self.adapter.command('charge_start')
        self.assertFalse(any('/command/' in call[1] or 'wake_up' in call[1] for call in self.http.calls))

    async def test_failed_charge_read_marks_cached_data_unconfirmed_and_reauthorization_clears_it(self):
        await self.authorize()
        await self.adapter.select_vehicle('123456789')
        await self.adapter.charge_state()
        self.http.fail_charge_read = True
        with self.assertRaises(TeslaError):
            await self.adapter.charge_state()
        self.assertTrue(self.adapter.public_status()['chargeState']['readFailed'])
        self.assertEqual(self.adapter.public_status()['chargeState']['batteryPercent'],62)
        await self.authorize()
        self.assertNotIn('chargeState',self.adapter.public_status())

    async def test_unsafe_endpoints_or_open_private_files_fail_closed_without_network(self):
        self.config['audience']='https://example.com'
        _default_save(self.root/'tesla-client.json',self.config)
        bad = TeslaAdapter(self.root,clock=lambda:self.now,transport=self.http)
        self.assertFalse(bad.public_status()['configured'])
        with self.assertRaises(TeslaError):
            await bad.authorize_start(self.client)
        self.assertEqual(self.http.calls,[])
        self.config['audience']=AUDIENCES[0]
        self.config['commandProxyURL']='https://example.com'
        _default_save(self.root/'tesla-client.json',self.config)
        self.assertFalse(TeslaAdapter(self.root).public_status()['configured'])
        self.config.pop('commandProxyURL')
        _default_save(self.root/'tesla-client.json',self.config)
        os.chmod(self.root/'tesla-client.json',0o644)
        self.assertFalse(TeslaAdapter(self.root).public_status()['configured'])

    async def test_non_charging_commands_and_unlisted_vehicle_identifiers_are_rejected(self):
        for command in ('door_unlock','wake_up','remote_start','charge_start/../../doors'):
            with self.subTest(command=command), self.assertRaises(TeslaError):
                await self.adapter.command(command)
        for limit in (True,49,101,80.1):
            with self.subTest(limit=limit), self.assertRaises(TeslaError):
                await self.adapter.command('set_charge_limit',limit)
        with self.assertRaises(TeslaError):
            await self.adapter.select_vehicle('../other')
        self.assertEqual(self.http.calls,[])

    async def sign_in(self,user):
        self.http.user = user
        await self.authorize()

    def account_files(self):
        return [self.root/'tesla-tokens.json',*sorted(self.root.glob('tesla-tokens-*.json'))]

    async def test_several_accounts_list_every_car_and_commands_go_through_the_owning_account(self):
        self.proxy_files()
        self.http.paired = True
        self.http.cars['user-2'] = [{'id_s':'222','vin':VIN2,'display_name':'Second Tesla'}]
        self.http.cars['user-3'] = [{'id_s':'333','vin':VIN3,'display_name':'Third Tesla'}]
        for user in ('user-1','user-2','user-3'):
            await self.sign_in(user)
        cars = await self.adapter.vehicles()
        self.assertEqual([car['id'] for car in cars],['123456789','222','333'])
        status = self.adapter.public_status()
        self.assertEqual([account['label'] for account in status['accounts']],['Tesla account 1','Tesla account 2','Tesla account 3'])
        self.assertEqual([account['vehicleCount'] for account in status['accounts']],[1,1,1])
        self.assertEqual(len({car['accountId'] for car in cars}),3)
        self.assertEqual(len(list(self.root.glob('tesla-tokens-*.json'))),2)
        for path in self.account_files():
            self.assertEqual(path.stat().st_mode & 0o777,0o600)
        state = await self.adapter.select_vehicle('222')
        self.assertEqual(state['accountId'],cars[1]['accountId'])
        self.assertTrue(state['commandsReady'])
        await self.adapter.command('charge_start')
        command = [call for call in self.http.calls if '/command/' in call[1]][-1]
        self.assertIn(VIN2,command[1])
        self.assertEqual(self.http.owner[command[2]['Authorization'].removeprefix('Bearer ')],'user-2')
        # Signing in again to another account leaves the chosen car ready to charge.
        await self.sign_in('user-3')
        self.assertTrue(self.adapter.public_status()['commandsReady'])
        self.assertEqual(self.adapter.public_status()['vehicleId'],'222')
        # Choosing a car from another account moves the choice; only one account keeps a chosen car.
        await self.adapter.select_vehicle('333')
        chosen = [json.loads(path.read_text()).get('vehicleId') for path in self.account_files()]
        self.assertEqual(sorted(value or '' for value in chosen),['','','333'])
        fresh = TeslaAdapter(self.root,clock=lambda:self.now,transport=self.http)
        self.assertEqual(fresh.public_status()['vehicleId'],'333')
        self.assertEqual(len(fresh.public_status()['accounts']),3)
        self.assertEqual(len(fresh.public_status()['vehicles']),3)
        self.assertNotIn('access-',json.dumps(fresh.public_status()))
        self.assertNotIn('user-',json.dumps(fresh.public_status()))

    async def test_signing_in_again_to_an_account_renews_it_without_a_duplicate_or_losing_its_car(self):
        await self.sign_in('user-1')
        await self.adapter.select_vehicle('123456789')
        await self.sign_in('user-1')
        status = self.adapter.public_status()
        self.assertEqual(len(status['accounts']),1)
        self.assertEqual(status['vehicleId'],'123456789')
        self.assertEqual(list(self.root.glob('tesla-tokens-*.json')),[])
        self.assertEqual(json.loads((self.root/'tesla-tokens.json').read_text())['refreshToken'],'refresh-2')

    async def test_removing_one_account_keeps_the_others_and_the_next_sign_in_reuses_the_first_file(self):
        self.http.cars['user-2'] = [{'id_s':'222','vin':VIN2,'display_name':'Second Tesla'}]
        await self.sign_in('user-1')
        await self.sign_in('user-2')
        await self.adapter.select_vehicle('222')
        second = self.adapter.public_status()['accountId']
        first = next(account['id'] for account in self.adapter.public_status()['accounts'] if account['id'] != second)
        state = await self.adapter.disconnect(first)
        self.assertEqual([car['id'] for car in state['vehicles']],['222'])
        self.assertEqual(state['vehicleId'],'222')
        self.assertFalse((self.root/'tesla-tokens.json').exists())
        with self.assertRaises(TeslaError):
            await self.adapter.disconnect('missing')
        state = await self.adapter.disconnect(second)
        self.assertFalse(state['authorized'])
        self.assertIsNone(state['vehicleId'])
        self.assertEqual(state['accounts'],[])
        self.assertEqual(list(self.root.glob('tesla-tokens*.json')),[])
        await self.sign_in('user-1')
        self.assertTrue((self.root/'tesla-tokens.json').exists())

    async def test_one_failing_account_does_not_hide_the_others_and_a_shared_car_is_listed_once(self):
        self.http.cars['user-2'] = [{'id_s':'222','vin':VIN2,'display_name':'Second Tesla'},
                                    {'id_s':'123456789','vin':VIN,'display_name':'My Tesla'}]
        await self.sign_in('user-1')
        await self.sign_in('user-2')
        cars = await self.adapter.vehicles()
        self.assertEqual(sorted(car['id'] for car in cars),['123456789','222'])
        self.http.rejected.add('user-1')
        cars = await self.adapter.vehicles()
        self.assertEqual(sorted(car['id'] for car in cars),['123456789','222'])
        status = self.adapter.public_status()
        self.assertEqual([account['authorized'] for account in status['accounts']],[False,True])
        self.assertEqual(status['accounts'][0]['error'],'Sign in to this Tesla account again.')
        self.assertTrue(status['authorized'])
        self.assertNotIn('raw-secret-error',json.dumps(status))
        self.http.rejected.add('user-2')
        with self.assertRaises(TeslaError):
            await self.adapter.vehicles()
        self.assertFalse(self.adapter.public_status()['authorized'])
        self.http.rejected.clear()
        await self.sign_in('user-1')
        self.assertEqual([account['authorized'] for account in self.adapter.public_status()['accounts']],[True,False])

    async def test_a_rejected_sign_in_for_another_account_leaves_the_connected_one_working(self):
        await self.sign_in('user-1')
        await self.adapter.select_vehicle('123456789')
        self.http.reject_code = True
        with self.assertRaises(TeslaError):
            await self.sign_in('user-2')
        status = self.adapter.public_status()
        self.assertTrue(status['authorized'])
        self.assertEqual(len(status['accounts']),1)
        self.assertEqual(status['vehicleId'],'123456789')
        self.assertEqual(len(await self.adapter.vehicles()),1)

    async def test_a_pi_set_up_before_accounts_keeps_its_sign_in_and_can_add_another(self):
        legacy = {'schemaVersion':1,'accessToken':'legacy-access','refreshToken':'legacy-refresh',
                  'expiresAt':(self.now+timedelta(hours=1)).isoformat().replace('+00:00','Z'),
                  'scopes':list(SCOPES),'refreshPending':False,
                  'vehicles':[{'id':'123456789','vin':VIN,'displayName':'My Tesla'}],'vehicleId':'123456789'}
        _default_save(self.root/'tesla-tokens.json',legacy)
        self.adapter = TeslaAdapter(self.root,clock=lambda:self.now,transport=self.http)
        status = self.adapter.public_status()
        self.assertEqual(status['accounts'],[{'id':'primary','label':'Tesla account 1','authorized':True,'vehicleCount':1}])
        self.assertEqual(status['vehicleId'],'123456789')
        self.assertEqual(status['vehicles'][0]['accountId'],'primary')
        self.http.cars['user-2'] = [{'id_s':'222','vin':VIN2,'display_name':'Second Tesla'}]
        await self.sign_in('user-2')
        status = self.adapter.public_status()
        self.assertEqual([account['label'] for account in status['accounts']],['Tesla account 1','Tesla account 2'])
        self.assertEqual(status['vehicleId'],'123456789')
        self.assertEqual(json.loads((self.root/'tesla-tokens.json').read_text())['accessToken'],'legacy-access')


if __name__ == '__main__':
    unittest.main()
