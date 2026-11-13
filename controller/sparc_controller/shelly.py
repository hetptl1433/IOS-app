"""Bounded local RPC for the Shelly Plug US Gen4 (S4PL-00116US).

Official references:
https://kb.shelly.cloud/knowledge-base/shelly-plug-us-gen4
https://shelly-api-docs.shelly.cloud/gen2/General/Authentication/
https://shelly-api-docs.shelly.cloud/gen2/ComponentsAndServices/Switch/

Probe is read-only. No passwords, raw responses, or credentials are logged.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import ipaddress
import json
import math
import re
import secrets
from urllib.request import parse_http_list, parse_keqv_list

MODEL = 'S4PL-00116US'
DEVICE_NAME = 'Shelly Plug US Gen4'
MAX_BODY = 65536
MAX_HEADERS = 8192
LOCAL_NETWORKS = tuple(ipaddress.ip_network(value) for value in (
    '10.0.0.0/8', '172.16.0.0/12', '192.168.0.0/16', 'fc00::/7'))


class ShellyError(ValueError):
    """Safe, bounded error text; never use a device's raw error message."""


class ShellyAuthenticationError(ShellyError):
    pass


def local_address(value):
    if not isinstance(value, str) or len(value) > 64 or value != value.strip() or '%' in value:
        raise ShellyError('Enter the plug\'s local IP address without a URL or port.')
    try:
        address = ipaddress.ip_address(value)
    except ValueError:
        raise ShellyError('Enter the plug\'s local IP address without a URL or port.') from None
    if not any(address.version == network.version and address in network for network in LOCAL_NETWORKS):
        raise ShellyError('Choose a private local-network IP address for the plug.')
    return address.compressed


@dataclass(frozen=True, repr=False)
class HTTPReply:
    status: int
    headers: dict
    body: bytes


async def local_rpc_transport(address, payload, headers, timeout):
    """Use a numeric pinned IP, fixed port/path, no proxy, redirect, or DNS."""
    address = local_address(address)
    writer = None
    try:
        async with asyncio.timeout(timeout):
            reader, writer = await asyncio.open_connection(address, 80, limit=MAX_HEADERS)
            host = '['+address+']' if ':' in address else address
            request_headers = {'Host':host, 'Content-Type':'application/json', 'Accept':'application/json',
                               'Connection':'close', 'Content-Length':str(len(payload)), **headers}
            request = 'POST /rpc HTTP/1.1\r\n'+''.join(key+': '+value+'\r\n' for key,value in request_headers.items())+'\r\n'
            writer.write(request.encode('ascii')+payload)
            await writer.drain()
            raw = await reader.readuntil(b'\r\n\r\n')
            if len(raw) > MAX_HEADERS:
                raise ShellyError('The plug returned an invalid response.')
            lines = raw.decode('ascii').split('\r\n')
            match = re.fullmatch(r'HTTP/1\.[01] ([0-9]{3})(?: [^\r\n]*)?', lines[0])
            if match is None:
                raise ShellyError('The plug returned an invalid response.')
            received = {}
            for line in lines[1:]:
                if not line:
                    continue
                key, separator, value = line.partition(':')
                key = key.strip().lower()
                if not separator or key in received:
                    raise ShellyError('The plug returned an invalid response.')
                received[key] = value.strip()
            encoding = received.get('transfer-encoding', '').lower()
            if encoding and ('content-length' in received or encoding != 'chunked'):
                raise ShellyError('The plug returned an unsupported response.')
            if received.get('content-encoding', 'identity').lower() != 'identity':
                raise ShellyError('The plug returned an unsupported response.')
            if encoding:
                chunks, total = [], 0
                while True:
                    line = await reader.readline()
                    if len(line) > 128 or not re.fullmatch(b'[0-9a-fA-F]+\r\n', line):
                        raise ShellyError('The plug returned an invalid response.')
                    length = int(line, 16)
                    total += length
                    if total > MAX_BODY:
                        raise ShellyError('The plug response exceeded its size limit.')
                    if length == 0:
                        if await reader.readexactly(2) != b'\r\n':
                            raise ShellyError('The plug returned an unsupported response.')
                        break
                    chunks.append(await reader.readexactly(length))
                    if await reader.readexactly(2) != b'\r\n':
                        raise ShellyError('The plug returned an invalid response.')
                body = b''.join(chunks)
            elif 'content-length' in received:
                raw_length = received['content-length']
                if not raw_length.isascii() or not raw_length.isdigit() or int(raw_length) > MAX_BODY:
                    raise ShellyError('The plug response exceeded its size limit.')
                body = await reader.readexactly(int(raw_length))
            else:
                chunks, total = [], 0
                while True:
                    chunk = await reader.read(min(8192,MAX_BODY-total+1))
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > MAX_BODY:
                        raise ShellyError('The plug response exceeded its size limit.')
                    chunks.append(chunk)
                body = b''.join(chunks)
            return HTTPReply(int(match.group(1)), received, body)
    except (OSError, UnicodeError, asyncio.TimeoutError, asyncio.IncompleteReadError, asyncio.LimitOverrunError):
        raise ShellyError('The local plug could not be reached or returned an incomplete response.') from None
    finally:
        if writer is not None:
            writer.close()
            try:
                await asyncio.wait_for(writer.wait_closed(), 0.25)
            except (OSError, asyncio.TimeoutError):
                pass


def _finite(value, *, nonnegative=False):
    if type(value) not in (int, float) or not math.isfinite(value) or (nonnegative and value < 0):
        raise ShellyError('The plug did not return valid power metering.')
    return float(value)


def _quote(value):
    return value.replace('\\', '\\\\').replace('"', '\\"')


class ShellyClient:
    def __init__(self, address, password=None, *, timeout=2.5, transport=None, clock=None):
        self.address = local_address(address)
        if password is not None and (not isinstance(password, str) or not password or len(password.encode()) > 256):
            raise ShellyAuthenticationError('Enter a valid local plug password.')
        if type(timeout) not in (int, float) or not math.isfinite(timeout) or not 0 < timeout <= 5:
            raise ShellyError('Choose a bounded plug timeout.')
        self._password = password
        self.timeout = timeout
        self._transport = transport or local_rpc_transport
        self._clock = clock or (lambda:datetime.now(timezone.utc))
        self._lock = asyncio.Lock()
        self._counter = 0
        self._device = None
        self._verified = False
        self._challenge = None
        self._nonce_count = 0

    def _accept_challenge(self, reply):
        header = reply.headers.get('www-authenticate', '')
        if len(header) > 2048 or not header.startswith('Digest '):
            raise ShellyAuthenticationError('The plug returned unsupported authentication.')
        try:
            fields = parse_keqv_list(parse_http_list(header[7:]))
            realm, nonce = fields['realm'], fields['nonce']
            if (fields.get('algorithm', '').upper() != 'SHA-256' or 'auth' not in fields.get('qop', '').split(',')
                    or not 1 <= len(realm) <= 128 or not 1 <= len(nonce) <= 512
                    or any(ord(char) < 32 or ord(char) > 126 for char in realm+nonce)):
                raise ValueError()
            expected = (self._device or {}).get('authDomain') or (self._device or {}).get('deviceId')
            if expected is None or realm != expected:
                raise ValueError()
        except (KeyError, ValueError, TypeError):
            raise ShellyAuthenticationError('The plug returned unsupported authentication.') from None
        if self._challenge is None or self._challenge['nonce'] != nonce:
            self._nonce_count = 0
        self._challenge = {'realm':realm, 'nonce':nonce}

    def _authorization(self):
        if self._password is None or self._challenge is None:
            return {}
        self._nonce_count += 1
        realm, nonce = self._challenge['realm'], self._challenge['nonce']
        nc, cnonce = f'{self._nonce_count:08x}', secrets.token_hex(16)
        digest = lambda value:hashlib.sha256(value.encode()).hexdigest()
        ha1, ha2 = digest('admin:'+realm+':'+self._password), digest('POST:/rpc')
        response = digest(':'.join((ha1,nonce,nc,cnonce,'auth',ha2)))
        value = ('Digest username="admin", realm="'+_quote(realm)+'", nonce="'+_quote(nonce)+
                 '", uri="/rpc", algorithm=SHA-256, response="'+response+'", qop=auth, nc='+nc+', cnonce="'+cnonce+'"')
        return {'Authorization':value}

    async def _rpc(self, method, params, *, mutation=False):
        allowed = {'Shelly.GetDeviceInfo':{}, 'Switch.GetStatus':{'id':0}}
        if method == 'Switch.Set':
            valid = mutation and set(params) == {'id','on'} and params['id'] == 0 and type(params['on']) is bool
        else:
            valid = not mutation and method in allowed and params == allowed[method]
        if not valid:
            raise ShellyError('Unsupported plug operation.')
        self._counter += 1
        request_id = self._counter
        payload = json.dumps({'id':request_id,'method':method,'params':params}, allow_nan=False, separators=(',',':')).encode()
        for attempt in range(1 if mutation else 2):
            headers = self._authorization() if method != 'Shelly.GetDeviceInfo' else {}
            try:
                reply = await asyncio.wait_for(self._transport(self.address,payload,headers,self.timeout), self.timeout+0.5)
            except (OSError, asyncio.TimeoutError):
                text = 'The plug command could not be confirmed; check its status before retrying.' if mutation else 'The local plug could not be reached.'
                raise ShellyError(text) from None
            if reply.status == 401:
                if mutation:
                    raise ShellyAuthenticationError('The plug did not authorize the command; it was not retried.')
                if self._password is None:
                    raise ShellyAuthenticationError('This plug requires its local admin password.')
                self._accept_challenge(reply)
                if attempt:
                    raise ShellyAuthenticationError('The plug did not accept its local admin password.')
                continue
            if reply.status != 200 or len(reply.body) > MAX_BODY:
                raise ShellyError('The plug returned an unexpected response.')
            try:
                response = json.loads(reply.body)
                if (not isinstance(response, dict) or type(response.get('id')) is not int or response['id'] != request_id
                        or not isinstance(response.get('result'), dict) or 'error' in response):
                    raise ValueError()
                if self._device and response.get('src', self._device['deviceId']) != self._device['deviceId']:
                    raise ValueError()
                return response['result']
            except (ValueError, TypeError):
                raise ShellyError('The plug returned an invalid RPC response.') from None
        raise ShellyAuthenticationError('The plug did not accept its local admin password.')

    async def _device_info(self):
        raw = await self._rpc('Shelly.GetDeviceInfo', {})
        ident = raw.get('id')
        if (raw.get('model') != MODEL or type(raw.get('gen')) is not int or raw['gen'] != 4
                or not isinstance(ident, str) or not re.fullmatch(r'[A-Za-z0-9_.-]{1,96}',ident)
                or type(raw.get('auth_en')) is not bool):
            self._verified = False
            raise ShellyError('Connect the supported Shelly Plug US Gen4 model S4PL-00116US.')
        previous = self._device
        if previous and previous['deviceId'] != ident:
            self._verified = False
            raise ShellyError('The device at this IP changed; verify the plug again.')
        domain = raw.get('auth_domain')
        if domain is not None and (not isinstance(domain,str) or not 1 <= len(domain) <= 128):
            raise ShellyError('The plug returned invalid authentication information.')
        self._device = {'deviceId':ident, 'model':MODEL, 'generation':4, 'deviceName':DEVICE_NAME,
                        'authRequired':raw['auth_en'], 'authDomain':domain}
        return self._device

    async def _status(self):
        raw = await self._rpc('Switch.GetStatus', {'id':0})
        if type(raw.get('id')) is not int or raw['id'] != 0 or type(raw.get('output')) is not bool:
            raise ShellyError('The plug did not return valid switch status.')
        energy = raw.get('aenergy') or {}
        if not isinstance(energy, dict):
            raise ShellyError('The plug did not return valid power metering.')
        errors = raw.get('errors', [])
        if (not isinstance(errors,list) or len(errors)>16
                or any(not isinstance(error,str) or not re.fullmatch(r'[a-z_]{1,40}',error) for error in errors)):
            raise ShellyError('The plug returned invalid safety status.')
        return {'switchOn':raw['output'], 'powerW':_finite(raw.get('apower')), 'voltageV':_finite(raw.get('voltage'),nonnegative=True),
                'currentA':_finite(raw.get('current'),nonnegative=True), 'totalEnergyWh':_finite(energy.get('total'),nonnegative=True),
                'errors':errors, 'metering':True, 'lastCheckedAt':self._clock().astimezone(timezone.utc).isoformat()}

    async def probe(self):
        """Verify the exact model and meter without changing relay/configuration."""
        async with self._lock, asyncio.timeout(min(8,self.timeout*3+0.5)):
            self._verified = False
            device = await self._device_info()
            status = await self._status()
            self._verified = True
            return {key:value for key,value in {**device,**status}.items() if key != 'authDomain'}

    async def status(self):
        if not self._verified:
            return await self.probe()
        async with self._lock:
            status = await self._status()
            return {key:value for key,value in {**self._device,**status}.items() if key != 'authDomain'}

    async def set_power(self, on):
        """An explicit verified command, sent once; never called by probe."""
        if type(on) is not bool:
            raise ShellyError('Choose an explicit on or off state.')
        async with self._lock, asyncio.timeout(min(8,self.timeout*3+0.5)):
            if not self._verified:
                raise ShellyError('Verify the plug and metering before controlling it.')
            await self._device_info()
            status = await self._status()  # refresh safe auth challenge before mutation
            if on and status['errors']:
                raise ShellyError('The plug reports a safety fault; resolve it before switching on.')
            if self._device['authRequired'] and (self._password is None or self._challenge is None):
                raise ShellyAuthenticationError('Verify the plug\'s local authentication before controlling it.')
            result = await self._rpc('Switch.Set', {'id':0,'on':on}, mutation=True)
            if type(result.get('was_on')) is not bool:
                raise ShellyError('The plug command response could not be confirmed; read its status.')
            return {'accepted':True, 'requestedOn':on, 'previousOn':result['was_on']}
