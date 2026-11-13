"""Authenticated connection setup shared by the screen and paired apps.

Saving a setup never moves the running research campaign to real hardware.
Hardware probes are read-only; real relay actions are separate explicit calls.
"""
from copy import deepcopy
import asyncio
from datetime import datetime, timezone
import ipaddress
import json
from pathlib import Path
import re
import socket

from .connection import normalize, updated, view
from .clients import control_state
from .shelly import ShellyClient, ShellyError, local_address


async def resolve_shelly_address(value):
    """Resolve one safe mDNS label once, then persist only its private IPv4.

    Numeric addresses never use DNS. RPC connections always retain their fixed
    numeric-IP/port/path restrictions and independently verify the device.
    """
    if value in (None, ''):
        return None
    try:
        return local_address(value)
    except ValueError:
        pass
    if (not isinstance(value,str) or len(value)>64
            or re.fullmatch(r'[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?\.local',value,re.IGNORECASE) is None):
        raise ValueError('Enter a private local IP address or a single Shelly .local name.')
    try:
        answers = await asyncio.wait_for(asyncio.to_thread(
            socket.getaddrinfo,value.lower(),80,socket.AF_INET,socket.SOCK_STREAM),3)
    except (OSError, asyncio.TimeoutError):
        raise ValueError('The Shelly .local name could not be resolved. Try its private local IP address.') from None
    if isinstance(answers,(list,tuple)) and len(answers)<=64:
        for answer in answers:
            try:
                family,kind,_,_,address = answer
                candidate = address[0]
                if family != socket.AF_INET or kind != socket.SOCK_STREAM or ipaddress.ip_address(candidate).version != 4:
                    continue
                return local_address(candidate)
            except (ValueError,TypeError,IndexError):
                continue
    raise ValueError('The Shelly .local name did not resolve to a private local IPv4 address.')


class ConnectionServiceMixin:
    def init_connections(self, save):
        from .tesla import TeslaAdapter
        normalize(self.settings.get('connection'))
        self._connection_save = save
        self._connection_lock = asyncio.Lock()
        active = self.settings.get('activeConnection', {'mode':'virtual'})
        if not isinstance(active,dict) or active.get('mode') not in ('virtual','tesla'):
            raise ValueError('The active connection setting needs review on the Pi.')
        self._connection_runtime = {'activeMode':active['mode'], 'smartPlug':{}}
        self._shelly_client = None
        self._shelly_address = None
        self._plug_demo_on = False
        self.tesla = TeslaAdapter(self.state_dir, save=save, clock=self.clock)

    @property
    def plug_demo_following(self):
        return bool(normalize(self.settings.get('connection'))['smartPlug']['followVirtual']
                    and self.active_connection_mode == 'virtual' and self.virtual_car_enabled)

    async def sync_plug_demo(self):
        """Demo only: the verified plug follows the active virtual car's charging."""
        if self.plug_demo_following:
            async with self._lock:
                self.advance_virtual()
                want = self.app_test.state_at(self.settings, self.clock().astimezone(timezone.utc))[0] == 'charging'
        elif self._plug_demo_on:
            want = False
        else:
            return
        async with self._connection_lock:
            adapter = self._shelly_client
            address = normalize(self.settings.get('connection'))['smartPlug']['address']
            runtime = self._connection_runtime['smartPlug']
            if (adapter is None or address is None or self._shelly_address != address
                    or runtime.get('address') != address or runtime.get('status') != 'connected'):
                return
            if runtime.get('switchOn') is want:
                self._plug_demo_on = want
                return
            try:
                await adapter.set_power(want)
                status = await adapter.status()
            except (OSError, ValueError, asyncio.TimeoutError):
                return
            self._connection_runtime['smartPlug'] = {**status, 'address':address, 'status':'connected'}
            self._plug_demo_on = want

    @property
    def active_connection_mode(self):
        return self._connection_runtime['activeMode']

    def _active_tesla_selected(self):
        selected = self.tesla.public_status().get('vehicleId')
        return selected is not None and selected == self.settings.get('activeConnection',{}).get('vehicleId')

    def tesla_snapshot(self):
        """Cached vehicle measurements only. Routine/price simulation is never a Tesla plan."""
        now = self.clock().astimezone(timezone.utc)
        status = self.connections_view()['tesla']
        charge = status.get('chargeState') or {}
        available = (status['authorized'] and self._active_tesla_selected()
                     and charge.get('stale') is False and charge.get('timestampSource') == 'vehicle')
        stamp = now.isoformat().replace('+00:00','Z')
        return {'state':charge.get('state','offline') if available else 'offline',
                'batteryPercent':int(charge.get('batteryPercent',0)),
                'targetPercent':charge.get('targetPercent',80), 'targetAvailable':'targetPercent' in charge,
                'readyBy':stamp,'windowStart':stamp,'windowEnd':stamp,
                'powerKW':charge.get('powerKW',0) if available else 0,
                'pluggedIn':charge.get('pluggedIn',False),
                'energyKWh':0,'remainingMinutes':charge.get('remainingMinutes',0) if available else 0,
                'monthlySavings':0,'currentRateCents':0,'estimatedCost':0,'sessionSavings':0,
                'updatedAt':charge.get('updatedAt',stamp), 'vehicleName':status.get('vehicleName') or 'Tesla',
                'virtualCar':False,'controlOnly':False,'manualOnly':True,'source':'TeslaFleetAPI',
                'telemetryStale':not available,'energyAvailable':False,'rateAvailable':False,
                'costAvailable':False,'costFinal':False,'savingsAvailable':False,'scheduleAvailable':False,
                'control':control_state(self.settings)}

    async def activate_connection(self,body,transport,client):
        if not isinstance(body,dict) or set(body) != {'mode'} or body['mode'] not in ('virtual','tesla'):
            raise ValueError('Choose an explicit Tesla or Virtual connection.')
        mode = body['mode']
        if mode == 'tesla':
            status = await self.tesla.check_readiness()
            if not status.get('commandsReady'):
                raise ValueError('Finish Tesla authorization, vehicle selection and virtual-key pairing before activating it.')
            self._observe_tesla(await self.tesla.charge_state())
            charge = self.connections_view()['tesla'].get('chargeState') or {}
            if charge.get('stale') is not False or charge.get('timestampSource') != 'vehicle':
                raise ValueError('Tesla data is stale. Wake the car in the Tesla app, then refresh before activating it.')
        async with self._lock:
            self.advance_virtual()
            if mode == 'tesla':
                old,prices,now = self.history_inputs()
                await asyncio.to_thread(self.history.capture,old,prices,now=now,force=True)
            candidate = deepcopy(self.settings)
            active = {'mode':mode,'activatedAt':self.clock().astimezone(timezone.utc).isoformat().replace('+00:00','Z')}
            if mode == 'tesla':
                active['vehicleId'] = self.tesla.public_status()['vehicleId']
                if (candidate.get('campaign') or {}).get('enabled',True) and candidate.get('campaign'):
                    campaign = candidate['campaign']
                    candidate['campaign'] = dict(campaign,enabled=False,revision=campaign.get('revision',1)+1)
            candidate['activeConnection'] = active
            candidate = self.command_candidate(candidate,'activate_connection',transport,client)
            self._connection_save(self.state_dir/'settings.json',candidate)
            self.settings = candidate
            self._connection_runtime['activeMode'] = mode
            self._virtual_anchor = self.clock().astimezone(timezone.utc)
        return {'storedOnly':True,**self.shared_state(client)}

    async def tesla_command(self,body,transport,client,operation='tesla_command'):
        if (not isinstance(body,dict) or set(body)-{'command','value'} or 'command' not in body
                or body['command'] not in ('charge_start','charge_stop','set_charge_limit')
                or (body['command'] == 'set_charge_limit' and (set(body) != {'command','value'}
                    or type(body['value']) is not int or not 50 <= body['value'] <= 100))
                or (body['command'] != 'set_charge_limit' and set(body) != {'command'})):
            raise ValueError('Choose Charge now, Stop, or a charge limit from 50 to 100 percent.')
        if self.active_connection_mode != 'tesla' or not self._active_tesla_selected():
            raise ValueError('Activate the selected Tesla before sending a charging command.')
        before = None
        if body['command'] == 'charge_start':
            before = await self.tesla.charge_state()
            snapshot = self.tesla_snapshot()
            if snapshot['telemetryStale'] or not snapshot['pluggedIn']:
                raise ValueError('Refresh the Tesla and connect its charging cable before starting a charge.')
        result = await self.tesla.command(body['command'],body.get('value'))
        accepted_at = self.clock()
        # Vehicle acceptance cannot be undone if local auditing or read-back fails.
        # Never turn an accepted command into a generic retryable response.
        warning = None
        try:
            await self._connection_event(operation,transport,client)
        except OSError:
            warning = 'Tesla accepted the command, but its local activity record could not be saved.'
        after = None
        try:
            after = await self.tesla.charge_state()
        except (ValueError,OSError,asyncio.TimeoutError):
            warning = 'Tesla accepted the command. Refresh to confirm its current charging state.'
        # Charging history reuses the reads above; it never adds a Tesla request.
        vehicle = self._tesla_vehicle()
        if vehicle is not None:
            sessions = self.history.tesla
            if body['command'] == 'charge_start':
                saved = self._record_tesla(sessions.open_session,vehicle,before,after,accepted_at)
            elif body['command'] == 'charge_stop':
                saved = self._record_tesla(sessions.stopped,vehicle['id'],after,accepted_at)
            else:
                saved = after is None or self._record_tesla(sessions.observe,vehicle['id'],after,self.clock())
            if not saved and warning is None:
                warning = 'Tesla accepted the command, but SPARC could not save it to charging history.'
        return {**self.tesla_snapshot(),**result,**self.shared_state(client), 'warning':warning}

    def _tesla_vehicle(self):
        status = self.tesla.public_status()
        if not status.get('authorized') or not status.get('vehicleId'):
            return None
        return {'id':status['vehicleId'],'name':status.get('vehicleName')}

    def _record_tesla(self,action,*args):
        """A history write never turns into a failed or retried vehicle command."""
        try:
            action(*args,pricing=self.history_pricing())
            return True
        except (OSError,ValueError,TypeError,KeyError):
            return False

    def _observe_tesla(self,reading=None):
        """Give an explicit read to an open session; close it if its car is no longer selected."""
        vehicle = self._tesla_vehicle()
        self._record_tesla(self.history.tesla.abandon,self.clock(),vehicle['id'] if vehicle else None)
        if vehicle is not None and reading is not None:
            self._record_tesla(self.history.tesla.observe,vehicle['id'],reading,self.clock())

    async def poll_tesla_session(self):
        """Read the Tesla only while a charge SPARC started is open (see tesla_history)."""
        sessions = self.history.tesla
        due = sessions.poll_due(self.clock())
        if due is None:
            return False
        async with self._connection_lock:
            if sessions.poll_due(self.clock()) != due:
                return False  # a Start/Stop or refresh just read the car
            vehicle = self._tesla_vehicle()
            if vehicle is None or vehicle['id'] != due:
                self._record_tesla(sessions.abandon,self.clock())
                return False
            try:
                reading = await self.tesla.charge_state()
            except (ValueError,OSError,asyncio.TimeoutError):
                self._record_tesla(sessions.read_failed,due,self.clock())
                return True
            try:
                sessions.observe(due,reading,self.clock(),polled=True,pricing=self.history_pricing())
            except (OSError,ValueError,TypeError,KeyError):
                return True
            return True

    def connections_view(self):
        runtime = {**self._connection_runtime, 'tesla':self.tesla.public_status()}
        return view(self.settings, runtime, self.clock())

    async def refresh_connections(self):
        """Read the local meter; it never actuates or polls billable Tesla APIs."""
        async with self._connection_lock:
            address = normalize(self.settings.get('connection'))['smartPlug']['address']
            marker = self.state_dir/'smart-plug-secret.json'
            if not address or not marker.exists():
                return
            try:
                saved = json.loads(marker.read_text())
                if saved.get('address') != address:
                    return
                adapter = self._shelly_client
                if adapter is None or self._shelly_address != address:
                    adapter = ShellyClient(address, password=saved.get('password'))
                    result = await adapter.probe()
                    if saved.get('deviceId') != result['deviceId']:
                        raise ValueError('The saved plug changed.')
                    self._shelly_client, self._shelly_address = adapter, address
                else:
                    result = await adapter.status()
                self._connection_runtime['smartPlug'] = {**result,'address':address,'status':'connected'}
            except (OSError, ValueError, asyncio.TimeoutError):
                self._shelly_client = None
                self._connection_runtime['smartPlug'] = {'address':address,'status':'error',
                    'error':'The saved plug could not be read. Connect it again to verify its identity and meter.'}

    def virtual_profile_name(self):
        names = {'cybertruck':'Cybertruck', 'model_x':'Model X', 'model_3':'Model 3'}
        return names[normalize(self.settings.get('connection'))['virtual']['model']]

    async def _connection_event(self, operation, transport, client):
        async with self._lock:
            candidate = self.command_candidate(self.settings, operation, transport, client)
            self._connection_save(self.state_dir/'settings.json', candidate)
            self.settings = candidate

    def _saved_plug_password(self, address):
        path = self.state_dir/'smart-plug-secret.json'
        if not path.exists():
            return None
        try:
            saved = json.loads(path.read_text())
            if saved.get('address') == address:
                return saved.get('password')
        except (OSError, ValueError, AttributeError):
            raise ValueError('The saved local plug credential needs review on the Pi.') from None
        return None

    async def connection_dispatch(self, operation, body, transport, client):
        if not isinstance(body, dict):
            raise ValueError('Send connection settings as an object.')
        if operation == 'activate_connection':
            return await self.activate_connection(body,transport,client)
        if operation == 'tesla_command':
            return await self.tesla_command(body,transport,client)
        if operation == 'set_connection_settings':
            if (self.active_connection_mode == 'tesla' and isinstance(body.get('virtual'),dict)
                    and {'batteryPercent','pluggedIn'} & set(body['virtual'])):
                raise ValueError('Switch to Virtual before changing the simulated battery or cable.')
            body = deepcopy(body)
            plug = body.get('smartPlug')
            if isinstance(plug,dict) and 'address' in plug:
                plug['address'] = await resolve_shelly_address(plug['address'])
            async with self._lock:
                self.advance_virtual()
                candidate = updated(self.settings, body, self.clock(), last_writer_wins=True)
                if candidate.get('virtualCarState') != self.settings.get('virtualCarState'):
                    old, prices, now = self.history_inputs()
                    await asyncio.to_thread(self.history.capture, old, prices, now=now, force=True)
                candidate = self.command_candidate(candidate, operation, transport, client)
                if self.pricing is not None:
                    self.refresh_smart_plan(force=True, cause='connectionSettingsChanged', settings=candidate)
                # refresh_smart_plan can legitimately skip a completed session.
                if self.settings != candidate and self.settings.get('connection') != candidate.get('connection'):
                    self._connection_save(self.state_dir/'settings.json', candidate)
                    self.settings = candidate
                elif self.settings.get('controlState') != candidate.get('controlState'):
                    self._connection_save(self.state_dir/'settings.json', candidate)
                    self.settings = candidate
                return {'storedOnly':True, **self.shared_state(client)}

        if operation == 'check_smart_plug':
            if set(body)-{'password'}:
                raise ValueError('Send only the optional local plug password.')
            if 'password' in body and (not isinstance(body['password'],str) or len(body['password'].encode())>256):
                raise ValueError('Enter a valid local plug password.')
            address = normalize(self.settings.get('connection'))['smartPlug']['address']
            if address is None:
                raise ValueError('Save the Shelly plug local IP address first.')
            password = body.get('password') or self._saved_plug_password(address)
            adapter = ShellyClient(address, password=password)
            try:
                result = await adapter.probe()
            except (ValueError, asyncio.TimeoutError):
                self._shelly_client, self._shelly_address = None, None
                self._connection_runtime['smartPlug'] = {'address':address, 'status':'error',
                    'error':'The plug could not be verified. Check its Wi-Fi, local IP and password.'}
                raise ValueError('The plug could not be verified. Check its Wi-Fi, local IP and password.') from None
            async with self._lock:
                if normalize(self.settings.get('connection'))['smartPlug']['address'] != address:
                    raise ValueError('The selected plug changed. Connect the saved address again.')
                self._connection_save(self.state_dir/'smart-plug-secret.json',
                    {'address':address, 'password':password, 'deviceId':result['deviceId']})
                self._shelly_client, self._shelly_address = adapter, address
                self._connection_runtime['smartPlug'] = {**result, 'address':address, 'status':'connected'}
            return {'readOnlyProbe':True, **self.shared_state(client)}

        if operation == 'set_smart_plug_power':
            if set(body) != {'on'} or type(body['on']) is not bool:
                raise ValueError('Choose an explicit plug On or Off command.')
            address = normalize(self.settings.get('connection'))['smartPlug']['address']
            adapter = self._shelly_client
            if adapter is None or address != self._shelly_address:
                raise ValueError('Connect and verify the selected plug before controlling it.')
            result = await adapter.set_power(body['on'])
            await self._connection_event(operation, transport, client)
            try:
                status = await adapter.status()
                self._connection_runtime['smartPlug'] = {**status, 'address':address, 'status':'connected'}
            except (ValueError, asyncio.TimeoutError):
                self._connection_runtime['smartPlug'] = {'address':address, 'status':'error',
                    'error':'The command was accepted, but current relay status could not be read. Reconnect before retrying.'}
            return {**result, **self.shared_state(client)}

        if operation == 'tesla_authorize':
            if body:
                raise ValueError('Begin Tesla authorization without extra fields.')
            return await self.tesla.authorize_start(client.get('id') if client else None)
        if operation == 'tesla_complete_authorization':
            if set(body) != {'code','state'}:
                raise ValueError('Complete Tesla authorization with its code and state.')
            await self.tesla.complete(body['code'], body['state'], client.get('id') if client else None)
            await self._connection_event(operation, transport, client)
        elif operation == 'tesla_refresh':
            if body:
                raise ValueError('Refresh Tesla without extra fields.')
            await self.tesla.vehicles()
            self._observe_tesla()
            if self.tesla.public_status().get('vehicleId'):
                await self.tesla.check_readiness()
                self._observe_tesla(await self.tesla.charge_state())
        elif operation == 'tesla_select_vehicle':
            if set(body) != {'vehicleId'}:
                raise ValueError('Select a Tesla from the authorized vehicle list.')
            if self.active_connection_mode == 'tesla' and body['vehicleId'] != self.settings['activeConnection']['vehicleId']:
                raise ValueError('Switch to Virtual before selecting a different Tesla.')
            await self.tesla.select_vehicle(body['vehicleId'])
            self._observe_tesla()
            await self._connection_event(operation, transport, client)
            self._observe_tesla(await self.tesla.charge_state())
        elif operation == 'tesla_disconnect':
            account = body.get('accountId')
            if set(body)-{'accountId'} or ('accountId' in body and (not isinstance(account,str)
                    or re.fullmatch(r'[a-z0-9]{1,32}',account) is None)):
                raise ValueError('Disconnect Tesla, or one Tesla account, without extra fields.')
            # Home must never point at a car SPARC can no longer reach.
            if self.active_connection_mode == 'tesla':
                tesla = self.tesla.public_status()
                if account is None or account == tesla.get('accountId') or len(tesla.get('accounts') or []) <= 1:
                    raise ValueError('Switch to the virtual car before removing the Tesla account in use.')
            if account is None:
                await self.tesla.disconnect()
            else:
                await self.tesla.disconnect(account)
            self._observe_tesla()
            await self._connection_event(operation, transport, client)
        else:
            raise ValueError('Choose a supported connection action.')
        return self.shared_state(client)
