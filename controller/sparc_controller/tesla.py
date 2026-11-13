"""Tesla Fleet OAuth and charging-only adapter with private local token storage.

References: developer.tesla.com/docs/fleet-api/authentication/third-party-tokens
and github.com/teslamotors/vehicle-command. No OAuth or vehicle mutation retries.
"""
from __future__ import annotations

import asyncio
import base64
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import re
import secrets
import ssl
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, HTTPSHandler, ProxyHandler, Request, build_opener
import uuid

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec

UTC = timezone.utc
AUTHORIZE_URL = 'https://auth.tesla.com/oauth2/v3/authorize'
TOKEN_URL = 'https://fleet-auth.prd.vn.cloud.tesla.com/oauth2/v3/token'
AUDIENCES = ('https://fleet-api.prd.na.vn.cloud.tesla.com','https://fleet-api.prd.eu.vn.cloud.tesla.com')
SCOPES = ('openid','offline_access','vehicle_device_data','vehicle_charging_cmds')
PROXY_URL = 'https://localhost:4443'
MAX_BODY = 262144
VIN = re.compile(r'[A-HJ-NPR-Z0-9]{17}\Z')
ACCOUNT_ID = re.compile(r'[a-z0-9]{1,32}\Z')


class TeslaError(ValueError):
    """Safe constant errors; never expose Tesla's raw response or credentials."""


class TeslaAuthenticationError(TeslaError):
    pass


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _stamp(value):
    return value.astimezone(UTC).isoformat().replace('+00:00','Z')


def _instant(value):
    parsed = datetime.fromisoformat(value.replace('Z','+00:00'))
    if parsed.tzinfo is None:
        raise ValueError()
    return parsed.astimezone(UTC)


def _read_private(path):
    if not path.exists():
        return {}
    if path.is_symlink() or path.stat().st_mode & 0o077:
        raise TeslaError('Tesla private configuration requires owner-only file permissions.')
    with path.open('rb') as stream:
        body = stream.read(MAX_BODY+1)
    if len(body) > MAX_BODY:
        raise TeslaError('Tesla private configuration exceeded its size limit.')
    try:
        value = json.loads(body)
        if not isinstance(value,dict):
            raise ValueError()
        return value
    except (ValueError,TypeError):
        raise TeslaError('Tesla private configuration is invalid.') from None


def _default_save(path, value):
    path.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
    temporary = path.with_name('.'+path.name+'.'+secrets.token_hex(8)+'.tmp')
    fd = os.open(temporary,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
    try:
        with os.fdopen(fd,'w') as stream:
            json.dump(value,stream,allow_nan=False,separators=(',',':'))
            stream.flush(); os.fsync(stream.fileno())
        os.replace(temporary,path)
        directory = os.open(path.parent,os.O_RDONLY|getattr(os,'O_DIRECTORY',0))
        try: os.fsync(directory)
        finally: os.close(directory)
    finally:
        temporary.unlink(missing_ok=True)


async def https_transport(method, url, headers, data, timeout, context=None):
    """Bounded HTTPS, certificate verification, no environment proxies/redirects."""
    def execute():
        opener = build_opener(ProxyHandler({}),_NoRedirect(),HTTPSHandler(context=context or ssl.create_default_context()))
        try:
            with opener.open(Request(url,data=data,headers=headers,method=method),timeout=timeout) as response:
                body = response.read(MAX_BODY+1)
                if len(body) > MAX_BODY:
                    raise TeslaError('Tesla response exceeded its size limit.')
                return response.status, body
        except HTTPError as exc:
            # The error body may contain identifiers or credentials. Discard it.
            status = exc.code
            exc.close()
            return status,b''
        except (URLError,OSError,ValueError):
            raise TeslaError('Tesla could not be reached securely.') from None
    return await asyncio.to_thread(execute)


def _subject(token):
    """A fingerprint of the Tesla user named by an access token (a JWT), used only to recognise a repeat sign-in.

    The token's signature is not checked: the fingerprint never grants access, it only avoids listing one
    Tesla account twice. Tokens that are not JWTs give None, and their sign-ins are kept as separate accounts.
    """
    parts = token.split('.') if isinstance(token,str) else []
    if len(parts) != 3:
        return None
    try:
        payload = json.loads(base64.urlsafe_b64decode(parts[1]+'='*(-len(parts[1])%4)))
    except (ValueError,TypeError):
        return None
    subject = payload.get('sub') if isinstance(payload,dict) else None
    if not isinstance(subject,str) or not 1 <= len(subject) <= 256:
        return None
    return hashlib.sha256(subject.encode()).hexdigest()[:32]


def _client(value):
    if value is None:
        return None
    try:
        return str(uuid.UUID(value))
    except (ValueError,TypeError,AttributeError):
        raise TeslaAuthenticationError('Use a valid authenticated app identity.') from None


class TeslaAdapter:
    def __init__(self,state_dir,save=None,clock=None,transport=None):
        self.state_dir = Path(state_dir)
        self.config_path = self.state_dir/'tesla-client.json'
        self.tokens_path = self.state_dir/'tesla-tokens.json'
        self._save = save or _default_save
        self._clock = clock or (lambda:datetime.now(UTC))
        self._transport = transport or https_transport
        self._lock = asyncio.Lock()
        self._pending = None
        self._config = {}
        # One entry per Tesla account: {'path', 'tokens', 'failed', 'error'}. The first account keeps the
        # original tesla-tokens.json file, so a Pi set up before accounts existed reads it unchanged.
        self._accounts = {}
        # The account API calls go through: the one that owns the selected car, or the one being worked on.
        self._active = None
        self._vehicles = []
        self._selected = None
        self._key_paired = False
        self._proxy_checked_at = None
        self._fleet_checked_at = None
        self._error = None
        self._charge_state = None
        try:
            self._config = self._load_config()
        except (TeslaError,OSError,ValueError,TypeError,KeyError):
            self._error = 'Tesla private setup needs review.'
        self._load_accounts()
        self._rebuild()

    @property
    def _tokens(self):
        account = self._accounts.get(self._active)
        return account['tokens'] if account else {}

    @property
    def _auth_failed(self):
        account = self._accounts.get(self._active)
        return bool(account and account['failed'])

    @_auth_failed.setter
    def _auth_failed(self,value):
        account = self._accounts.get(self._active)
        if account:
            account['failed'] = bool(value)

    def _load_accounts(self):
        extra = sorted(path for path in self.state_dir.glob('tesla-tokens-*.json') if not path.name.startswith('.'))
        for path in [self.tokens_path,*extra]:
            try:
                tokens = _read_private(path)
                if not tokens:
                    continue
                self._validate_tokens(tokens)
                tokens['vehicles'] = self._clean_vehicles(tokens.get('vehicles',[]))
                account_id = tokens.get('accountId','primary')
                label = tokens.get('label')
                if (not isinstance(account_id,str) or ACCOUNT_ID.fullmatch(account_id) is None or account_id in self._accounts
                        or (label is not None and (not isinstance(label,str) or not 1 <= len(label) <= 64))):
                    raise ValueError()
                if 'subject' not in tokens:
                    tokens['subject'] = _subject(tokens['accessToken'])
                self._accounts[account_id] = {'path':path,'tokens':tokens,'failed':False,'error':None}
            except (TeslaError,OSError,ValueError,TypeError,KeyError):
                self._error = 'Tesla private setup needs review.'

    def _account_ok(self,account_id):
        account = self._accounts.get(account_id)
        return bool(account and account['tokens'] and not account['tokens'].get('refreshPending') and not account['failed'])

    def _label(self,account_id):
        label = self._accounts[account_id]['tokens'].get('label')
        return label or 'Tesla account '+str(list(self._accounts).index(account_id)+1)

    def _rebuild(self):
        """One car list across accounts; the selected car decides which account API calls use."""
        chosen_account,chosen_car = next(((account_id,account['tokens']['vehicleId'])
            for account_id,account in self._accounts.items() if self._account_ok(account_id)
            and any(car['id']==account['tokens'].get('vehicleId') for car in account['tokens'].get('vehicles',[]))),(None,None))
        flat,seen = [],set()
        for account_id,account in self._accounts.items():
            if not self._account_ok(account_id):
                continue
            for car in account['tokens'].get('vehicles',[]):
                if car['id'] in seen:
                    continue
                seen.add(car['id'])
                # A car shared by two accounts is listed once, through the account that chose it.
                flat.append(dict(car,accountId=chosen_account if car['id']==chosen_car else account_id))
        previous = self._selected['id'] if self._selected else None
        self._vehicles = flat
        self._selected = next((car for car in flat if car['id']==chosen_car),None)
        if self._selected is not None:
            self._active = self._selected['accountId']
        elif self._active not in self._accounts:
            self._active = next(iter(self._accounts),None)
        if self._selected is None or self._selected['id'] != previous:
            self._charge_state = None

    def _load_config(self):
        config = _read_private(self.config_path)
        if not config:
            return {}
        try:
            client = str(uuid.UUID(config['clientId']))
            secret, domain = config['clientSecret'], config['domain']
            redirect = urlsplit(config['redirectURI'])
            if (not isinstance(secret,str) or not 1 <= len(secret) <= 4096 or any(ord(c)<32 for c in secret)
                    or not isinstance(domain,str) or not re.fullmatch(r'[A-Za-z0-9](?:[A-Za-z0-9.-]{0,251}[A-Za-z0-9])?',domain)
                    or '.' not in domain or redirect.scheme!='https' or redirect.hostname!=domain.lower()
                    or redirect.username is not None or redirect.password is not None or redirect.port not in (None,443)
                    or redirect.query or redirect.fragment or not redirect.path.startswith('/')
                    or config['audience'] not in AUDIENCES
                    or config.get('commandProxyURL',PROXY_URL) != PROXY_URL):
                raise ValueError()
            return dict(config,clientId=client,domain=domain.lower())
        except (ValueError,KeyError,TypeError,AttributeError):
            raise TeslaError('Tesla private client configuration is invalid.') from None

    def _persist(self,value):
        account = self._accounts[self._active]
        self._save(account['path'],value)
        if account['path'].exists() and account['path'].stat().st_mode & 0o077:
            raise TeslaError('Tesla token storage requires owner-only permissions.')
        account['tokens'] = value

    def _validate_tokens(self,value):
        for key in ('accessToken','refreshToken'):
            token = value.get(key)
            if not isinstance(token,str) or not 1 <= len(token) <= 16384 or not re.fullmatch(r'[A-Za-z0-9._~+/=-]+',token):
                raise TeslaAuthenticationError('Tesla authorization must be completed again.')
        _instant(value['expiresAt'])
        return value

    def _authorized(self):
        return any(self._account_ok(account_id) for account_id in self._accounts)

    def _commands_ready(self):
        now = self._clock()
        return (self._account_ok(self._active) and self._selected is not None and self._key_paired
                and self._proxy_checked_at is not None and (now-self._proxy_checked_at).total_seconds()<300
                and self._fleet_checked_at is not None and (now-self._fleet_checked_at).total_seconds()<300
                and 'vehicle_charging_cmds' in self._tokens.get('scopes',[]))

    def public_status(self):
        authorized = self._authorized()
        status = ('not_configured' if not self._config else 'authorization_required' if not authorized else
                  'vehicle_not_selected' if self._selected is None else 'connected')
        pairing = None
        if self._config:
            pairing = 'https://tesla.com/_ak/'+self._config['domain']
            if self._selected:
                pairing += '?'+urlencode({'vin':self._selected['vin']})
        accounts = []
        for account_id,account in self._accounts.items():
            item = {'id':account_id,'label':self._label(account_id),'authorized':self._account_ok(account_id),
                    'vehicleCount':len(account['tokens'].get('vehicles',[]))}
            if account['error']:
                item['error'] = account['error']
            accounts.append(item)
        result = {'configured':bool(self._config),'authorized':authorized,'status':status,
                'vehicleName':self._selected['displayName'] if self._selected else None,
                'vehicleId':self._selected['id'] if self._selected else None,'vehicles':deepcopy(self._vehicles) if authorized else [],
                'accounts':accounts,'accountId':self._selected['accountId'] if self._selected else None,
                'keyPairingURL':pairing,'commandsReady':bool(self._commands_ready()),'error':self._error}
        if authorized and self._charge_state is not None:
            result['chargeState']=deepcopy(self._charge_state)
        return result

    async def authorize_start(self,client_id=None):
        async with self._lock:
            self._config = self._load_config()
            if not self._config:
                raise TeslaAuthenticationError('Configure the private Tesla developer client on the Pi first.')
            client = _client(client_id)
            state = secrets.token_hex(32)
            expires = self._clock().astimezone(UTC)+timedelta(minutes=10)
            self._pending = {'state':state,'clientId':client,'expiresAt':expires}
            params = {'response_type':'code','client_id':self._config['clientId'],'redirect_uri':self._config['redirectURI'],
                      'scope':' '.join(SCOPES),'state':state,'nonce':secrets.token_hex(32),
                      'prompt_missing_scopes':'true','require_requested_scopes':'true'}
            return {'authorizationURL':AUTHORIZE_URL+'?'+urlencode(params),'expiresAt':_stamp(expires)}

    async def _request(self,method,url,*,form=None,payload=None,bearer=None,proxy=False,raw=False):
        if proxy:
            if not url.startswith(PROXY_URL+'/'):
                raise TeslaError('Invalid local Tesla command proxy.')
            ca = self._config.get('commandProxyCA',self._config.get('caCertFile'))
            if not isinstance(ca,str) or not Path(ca).is_file():
                raise TeslaError('Configure a trusted local Tesla proxy certificate.')
            try: context = ssl.create_default_context(cafile=ca)
            except (OSError,ssl.SSLError): raise TeslaError('The local Tesla proxy certificate is invalid.') from None
        else:
            allowed = url == TOKEN_URL or any(url.startswith(base+'/api/1/') for base in AUDIENCES)
            if not allowed:
                raise TeslaError('Invalid Tesla service endpoint.')
            context = None
        headers = {'Accept':'application/json'}
        if bearer is not None:
            headers['Authorization'] = 'Bearer '+bearer
        if form is not None:
            data = urlencode(form).encode()
            headers['Content-Type'] = 'application/x-www-form-urlencoded'
        elif payload is not None:
            data = json.dumps(payload,allow_nan=False,separators=(',',':')).encode()
            headers['Content-Type'] = 'application/json'
        else:
            data = None
        try:
            status,body = await asyncio.wait_for(self._transport(method,url,headers,data,8,context),8.5)
        except (OSError,asyncio.TimeoutError):
            raise TeslaError('Tesla request could not be confirmed; it was not retried.') from None
        if status in (401,403):
            # Only a rejected credential of the account in use marks it; a failed new sign-in leaves others alone.
            if bearer is not None or (form or {}).get('grant_type') == 'refresh_token':
                self._auth_failed = True
            raise TeslaAuthenticationError('Tesla authorization or vehicle permission must be reviewed.')
        if status != 200 or not isinstance(body,bytes) or len(body)>MAX_BODY:
            raise TeslaError('Tesla did not accept the request; it was not retried.')
        if raw:
            return body
        try:
            value = json.loads(body)
            if not isinstance(value,dict): raise ValueError()
            return value
        except (ValueError,TypeError):
            raise TeslaError('Tesla returned an invalid response.') from None

    def _token_result(self,result):
        life = result.get('expires_in')
        if type(life) not in (int,float) or not math.isfinite(life) or not 0 < life <= 31*86400:
            raise TeslaAuthenticationError('Tesla returned invalid authorization details.')
        if str(result.get('token_type','Bearer')).lower() != 'bearer':
            raise TeslaAuthenticationError('Tesla returned unsupported authorization details.')
        scopes = result.get('scope',' '.join(SCOPES))
        if not isinstance(scopes,str) or not set(SCOPES) <= set(scopes.split()):
            raise TeslaAuthenticationError('Tesla did not grant the requested charging permissions.')
        tokens = {'schemaVersion':1,'accessToken':result.get('access_token'),'refreshToken':result.get('refresh_token'),
                  'expiresAt':_stamp(self._clock().astimezone(UTC)+timedelta(seconds=life)), 'scopes':list(SCOPES),
                  'refreshPending':False}
        return self._validate_tokens(tokens)

    async def complete(self,code,state,client_id=None):
        async with self._lock:
            pending = self._pending
            if (not isinstance(state,str) or not re.fullmatch(r'[a-f0-9]{64}',state) or pending is None
                    or not secrets.compare_digest(state,pending['state']) or self._clock()>=pending['expiresAt']
                    or (pending['clientId'] is not None and _client(client_id)!=pending['clientId'])):
                raise TeslaAuthenticationError('Tesla authorization state expired or does not match this app.')
            if not isinstance(code,str) or not 1 <= len(code)<=4096 or any(ord(c)<33 or ord(c)>126 for c in code):
                raise TeslaAuthenticationError('Send a valid Tesla authorization code.')
            self._pending = None  # a valid completion attempt is single use, including failures
            result = await self._request('POST',TOKEN_URL,form={'grant_type':'authorization_code',
                'client_id':self._config['clientId'],'client_secret':self._config['clientSecret'],
                'code':code,'audience':self._config['audience'],'redirect_uri':self._config['redirectURI']})
            tokens = self._token_result(result)
            subject = _subject(tokens['accessToken'])
            same = next((account_id for account_id,account in self._accounts.items()
                         if subject and account['tokens'].get('subject')==subject),None)
            if same:
                # Signing in again to an account already here renews it and keeps its cars and chosen car.
                old = self._accounts[same]['tokens']
                tokens.update(accountId=same,subject=subject,vehicles=old.get('vehicles',[]),vehicleId=old.get('vehicleId'))
                if old.get('label'): tokens['label'] = old['label']
                self._active = same
            else:
                account_id = secrets.token_hex(6)
                used = {account['path'] for account in self._accounts.values()}
                path = self.tokens_path if self.tokens_path not in used else self.state_dir/('tesla-tokens-'+account_id+'.json')
                numbers = [int(match.group(1)) for known in self._accounts
                           if (match := re.fullmatch(r'Tesla account (\d+)',self._label(known)))]
                tokens.update(accountId=account_id,subject=subject,vehicles=[],vehicleId=None,
                              label='Tesla account '+str(max(numbers,default=len(self._accounts))+1))
                self._accounts[account_id] = {'path':path,'tokens':{},'failed':False,'error':None}
                self._active = account_id
            # Adding another account leaves the chosen car's approval and last reading in place.
            renewing_selected = self._selected is None or self._selected['accountId'] == self._active
            self._persist(tokens)
            self._accounts[self._active].update(failed=False,error=None)
            self._error = None
            if renewing_selected:
                self._key_paired = False
                self._proxy_checked_at = self._fleet_checked_at = None
            self._rebuild()
            if renewing_selected:
                self._charge_state = None
            return self.public_status()

    async def _refresh(self,force=False):
        if not self._config or not self._tokens or self._tokens.get('refreshPending'):
            raise TeslaAuthenticationError('Complete Tesla authorization again.')
        if not force and _instant(self._tokens['expiresAt']) > self._clock()+timedelta(seconds=60):
            return self._tokens['accessToken']
        previous = deepcopy(self._tokens)
        # Persist uncertainty before spending a single-use refresh token.
        self._persist(dict(previous,refreshPending=True))
        result = await self._request('POST',TOKEN_URL,form={'grant_type':'refresh_token',
                    'client_id':self._config['clientId'],'refresh_token':previous['refreshToken']})
        tokens = self._token_result(result)
        for key in ('vehicles','vehicleId','accountId','label','subject'):
            if key in previous: tokens[key]=previous[key]
        self._persist(tokens)
        self._auth_failed,self._error = False,None
        return tokens['accessToken']

    async def refresh(self):
        async with self._lock:
            await self._refresh(force=True)
            return self.public_status()

    async def _api(self,method,path,payload=None):
        access = await self._refresh()
        value = await self._request(method,self._config['audience']+path,payload=payload,bearer=access)
        if 'response' not in value:
            raise TeslaError('Tesla did not return the requested vehicle information.')
        return value['response']

    @staticmethod
    def _clean_vehicles(rows):
        if not isinstance(rows,list) or len(rows)>100:
            raise TeslaError('Tesla returned an unsupported vehicle list.')
        result,seen = [],set()
        for raw in rows[:20]:
            if not isinstance(raw,dict): raise TeslaError('Tesla returned invalid vehicle information.')
            ident = raw.get('id_s',raw.get('id'))
            if type(ident) is int: ident=str(ident)
            vin = raw.get('vin')
            name = raw.get('displayName',raw.get('display_name')) or 'Tesla'
            if (not isinstance(ident,str) or not re.fullmatch(r'[0-9]{1,32}',ident)
                    or not isinstance(vin,str) or VIN.fullmatch(vin) is None
                    or not isinstance(name,str) or len(name.encode())>128 or any(ord(c)<32 for c in name) or ident in seen):
                raise TeslaError('Tesla returned invalid vehicle information.')
            result.append({'id':ident,'vin':vin,'displayName':name})
            seen.add(ident)
        return result

    async def _list(self):
        """Read every account's cars. One account failing does not hide the others' cars."""
        attempted,failures = 0,[]
        try:
            for account_id in list(self._accounts):
                if not self._account_ok(account_id):
                    continue
                attempted += 1
                self._active = account_id
                account = self._accounts[account_id]
                try:
                    cars = self._clean_vehicles(await self._api('GET','/api/1/vehicles'))
                except TeslaError as error:
                    failures.append(error)
                    account['error'] = ('Sign in to this Tesla account again.' if not self._account_ok(account_id)
                                        else 'This Tesla account could not be reached.')
                    continue
                account['error'] = None
                chosen = account['tokens'].get('vehicleId')
                self._persist(dict(account['tokens'],vehicles=cars,
                                   vehicleId=chosen if any(car['id']==chosen for car in cars) else None))
        finally:
            self._rebuild()
        if not attempted:
            raise TeslaAuthenticationError('Complete Tesla authorization again.')
        if len(failures) == attempted:
            raise failures[-1]
        return deepcopy(self._vehicles)

    async def vehicles(self):
        async with self._lock:
            return await self._list()

    def _proxy_files_ready(self):
        key,ca = self._config.get('privateKeyPath'),self._config.get('commandProxyCA',self._config.get('caCertFile'))
        if not isinstance(key,str) or not isinstance(ca,str): return False
        path = Path(key)
        if not path.is_file() or path.is_symlink() or path.stat().st_mode & 0o077 or not Path(ca).is_file(): return False
        try:
            if path.stat().st_size>16384: return False
            private = serialization.load_pem_private_key(path.read_bytes(),password=None)
            return isinstance(private,ec.EllipticCurvePrivateKey) and isinstance(private.curve,ec.SECP256R1)
        except (ValueError,TypeError,OSError):
            return False

    async def _readiness(self):
        self._key_paired = False
        self._proxy_checked_at = self._fleet_checked_at = None
        if not self._selected:
            return
        self._active = self._selected['accountId']
        self._error = None
        vin = self._selected['vin']
        fleet = await self._api('POST','/api/1/vehicles/fleet_status',{'vins':[vin]})
        if not isinstance(fleet,dict): raise TeslaError('Tesla returned invalid key-pairing status.')
        paired = fleet.get('key_paired_vins',[])
        self._key_paired = isinstance(paired,list) and vin in paired
        self._fleet_checked_at = self._clock()
        if self._key_paired and self._proxy_files_ready():
            try:
                body = await self._request('GET',PROXY_URL+'/health',proxy=True,raw=True)
                if body.strip()==b'OK': self._proxy_checked_at=self._clock()
            except TeslaError:
                self._error='Tesla data is connected; the signed local charging proxy is not ready.'

    async def select_vehicle(self,vehicle_id):
        async with self._lock:
            if not isinstance(vehicle_id,str) or not re.fullmatch(r'[0-9]{1,32}',vehicle_id):
                raise TeslaError('Select a vehicle returned by Tesla.')
            await self._list()
            selected = next((vehicle for vehicle in self._vehicles if vehicle['id']==vehicle_id),None)
            if selected is None:
                raise TeslaError('Select a vehicle returned by Tesla.')
            # Only one account holds the chosen car, so the others give theirs up.
            for account_id,account in self._accounts.items():
                if account_id != selected['accountId'] and account['tokens'].get('vehicleId') is not None:
                    self._active = account_id
                    self._persist(dict(account['tokens'],vehicleId=None))
            self._active = selected['accountId']
            self._persist(dict(self._tokens,vehicleId=vehicle_id))
            self._rebuild()
            await self._readiness()
            return self.public_status()

    async def check_readiness(self):
        """Explicit setup check; never sends a vehicle command or wakes the car."""
        async with self._lock:
            await self._readiness()
            return self.public_status()

    async def charge_state(self):
        async with self._lock:
            if not self._selected:
                raise TeslaError('Select a Tesla vehicle before reading its charging state.')
            self._active = self._selected['accountId']
            if self._charge_state is not None:
                self._charge_state['readFailed'] = True
            result = await self._api('GET','/api/1/vehicles/'+self._selected['id']+'/vehicle_data?endpoints=charge_state')
            charge = result.get('charge_state') if isinstance(result,dict) else None
            if not isinstance(charge,dict): raise TeslaError('Tesla charging data is unavailable; the vehicle may be asleep.')
            battery,power,state = charge.get('battery_level'),charge.get('charger_power'),charge.get('charging_state')
            states = {'Charging':'charging','Complete':'complete','Stopped':'paused','Disconnected':'unplugged','NoPower':'waiting','Starting':'waiting'}
            if (type(battery) not in (int,float) or not math.isfinite(battery) or not 0<=battery<=100
                    or type(power) not in (int,float) or not math.isfinite(power) or power<0 or state not in states):
                raise TeslaError('Tesla returned incomplete charging measurements.')
            stamp = charge.get('timestamp',result.get('timestamp'))
            observed,source = self._clock(),'received'
            if type(stamp) in (int,float) and math.isfinite(stamp):
                try: observed=datetime.fromtimestamp(stamp/1000,UTC);source='vehicle'
                except (ValueError,OverflowError,OSError): raise TeslaError('Tesla returned an invalid observation time.') from None
                if observed>self._clock()+timedelta(minutes=5): raise TeslaError('Tesla returned a future observation time.')
            self._charge_state = {'batteryPercent':float(battery),'pluggedIn':state!='Disconnected','powerKW':float(power),
                    'state':states[state],'updatedAt':_stamp(observed),'timestampSource':source,
                    'vehicleName':self._selected['displayName'],'source':'TeslaFleetAPI'}
            limit = charge.get('charge_limit_soc')
            if type(limit) is int and 50 <= limit <= 100:
                self._charge_state['targetPercent'] = limit
            remaining = charge.get('time_to_full_charge')
            if type(remaining) in (int,float) and math.isfinite(remaining) and 0 <= remaining <= 168:
                self._charge_state['remainingMinutes'] = math.ceil(remaining*60)
            # The car's own "kWh added" counter for its current charge (battery side).
            added = charge.get('charge_energy_added')
            if type(added) in (int,float) and math.isfinite(added) and 0 <= added <= 500:
                self._charge_state['energyAddedKWh'] = float(added)
            return deepcopy(self._charge_state)

    async def command(self,name,value=None):
        if name not in ('charge_start','charge_stop','set_charge_limit'):
            raise TeslaError('Only Tesla charging commands are supported.')
        if name=='set_charge_limit' and (type(value) is not int or not 50<=value<=100):
            raise TeslaError('Choose a Tesla charge limit from 50 to 100 percent.')
        if name!='set_charge_limit' and value is not None:
            raise TeslaError('This Tesla charging command does not take parameters.')
        async with self._lock:
            await self._readiness()
            if not self._commands_ready():
                raise TeslaError('Tesla signed charging commands are not ready; review virtual-key pairing and the local proxy.')
            vehicle = await self._api('GET','/api/1/vehicles/'+self._selected['vin'])
            if not isinstance(vehicle,dict) or vehicle.get('state') != 'online':
                raise TeslaError('The Tesla is asleep or offline. Wake it in the Tesla app, then refresh before charging.')
            access = await self._refresh()
            path = '/api/1/vehicles/'+self._selected['vin']+'/command/'+name
            result = await self._request('POST',PROXY_URL+path,proxy=True,bearer=access,
                                         payload={'percent':value} if name=='set_charge_limit' else {})
            response = result.get('response')
            if not isinstance(response,dict) or response.get('result') is not True:
                raise TeslaError('Tesla did not confirm the charging command; it was not retried.')
            return {'accepted':True,'command':name}

    async def disconnect(self,account_id=None):
        """Forget one Tesla account's sign-in, or every account's when none is named."""
        async with self._lock:
            if account_id is not None and account_id not in self._accounts:
                raise TeslaError('Choose a connected Tesla account.')
            targets = list(self._accounts) if account_id is None else [account_id]
            losing_selected = self._selected is not None and self._selected['accountId'] in targets
            for target in targets:
                self._accounts.pop(target)['path'].unlink(missing_ok=True)
            if account_id is None:
                self.tokens_path.unlink(missing_ok=True)
                self._pending = None
            if self.state_dir.exists():
                directory=os.open(self.state_dir,os.O_RDONLY|getattr(os,'O_DIRECTORY',0))
                try: os.fsync(directory)
                finally: os.close(directory)
            if losing_selected or not self._accounts:
                self._key_paired=False;self._proxy_checked_at=None;self._fleet_checked_at=None
            self._active = None
            self._error = None
            self._rebuild()
            return dict(self.public_status(),disconnectedLocally=True)
