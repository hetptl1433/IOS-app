import asyncio
from copy import deepcopy
from datetime import datetime, timedelta
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch
import uuid

from sparc_controller.app_test import AppTestControl
from sparc_controller.campaign import DEFAULT_CAMPAIGN, arm_settings, sessions
from sparc_controller.display import DisplayTransport
from sparc_controller.protocol import MAX_FRAME, SecureChannel, setup_key
from sparc_controller.routine import FIELDS, normalize, updated, view
from sparc_controller.service import ControllerService, atomic_save, initialize

def at(text): return datetime.fromisoformat(text)

class RoutineControlsTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name)
        self.identity = initialize(self.path)
        self.now = at('2026-11-05T21:00:00-06:00')
        (self.path/'campaign.json').write_text(json.dumps(DEFAULT_CAMPAIGN))
        self.service = ControllerService(self.path, allow_wifi_provision=False, app_test=True, clock=lambda:self.now)
        self.service.history.log_root = self.path/'test-logs'
        armed = arm_settings(self.service.settings, DEFAULT_CAMPAIGN, 1, self.now)
        atomic_save(self.path/'settings.json', armed); self.service.settings = armed
        self.channel = SecureChannel(setup_key(self.identity['setupKey']), self.service.info)
        self.display = DisplayTransport(self.service, 8767)

    def body(self, **changes):
        config = self.service.settings['campaign']
        return {**{k:config[k] for k in FIELDS}, 'expectedRevision':config['revision'], **changes}

    async def exchange(self, op, body=None):
        request={'id':str(uuid.uuid4()), 'timestamp':time.time(),'operation':op,'body':body or {}}
        result=await self.service.handle(self.channel.seal(request,'c2d'),'ble')
        self.assertLess(len(result),MAX_FRAME)
        return self.channel.open(result,'d2c')

    async def http(self, method, path, body=None, origin=None):
        reader=asyncio.StreamReader()
        payload=json.dumps(body).encode() if body is not None else b''
        headers=f'{method} {path} HTTP/1.1\r\nHost: 127.0.0.1:8767\r\n'
        if body is not None: headers+=f'Content-Type: application/json\r\nContent-Length: {len(payload)}\r\n'
        if origin: headers+=f'Origin: {origin}\r\n'
        reader.feed_data((headers+'\r\n').encode()+payload);reader.feed_eof()
        status,_,raw=await self.display.request(reader)
        return status,json.loads(raw)

    async def test_migrates_legacy_campaign_and_both_interfaces_show_current_schedule(self):
        result=(await self.exchange('get_settings'))['body']['campaign']
        self.assertEqual((result['nights'],result['startMinutes'],result['departureMinutes']),(7,1320,420))
        self.assertEqual((result['initialBatteryPercent'],result['targetBatteryPercent']),(20,80))
        self.assertEqual(result,self.display.state()['campaign'])
        self.assertEqual(json.loads((self.path/'settings.json').read_text())['campaign']['revision'],1)

    async def test_screen_save_is_read_back_on_phone_and_last_valid_save_wins(self):
        old=self.body()
        status,screen=await self.http('POST','/api/campaign',self.body(targetBatteryPercent=85))
        self.assertEqual(status,200)
        phone=(await self.exchange('get_settings'))['body']['campaign']
        self.assertEqual(phone,screen['campaign']);self.assertEqual(phone['targetBatteryPercent'],85)
        latest=await self.exchange('set_campaign',old)
        self.assertTrue(latest['ok'])
        self.assertEqual(self.service.settings['campaign']['targetBatteryPercent'],80)
        self.assertEqual(self.service.settings['controlState']['sequence'],2)

    async def test_invalid_routine_and_failed_write_preserve_confirmed_state(self):
        before=deepcopy(self.service.settings)
        for change in ({'nights':0},{'departureMinutes':True},{'targetBatteryPercent':10}, {'startDate':'2020-01-01'}):
            reply=await self.exchange('set_campaign',self.body(**change));self.assertFalse(reply['ok'])
            self.assertEqual(self.service.settings,before)
        with patch('sparc_controller.service.atomic_save',side_effect=OSError('full disk')):
            self.assertFalse((await self.exchange('set_campaign',self.body(targetBatteryPercent=85)))['ok'])
        self.assertEqual(self.service.settings,before)

    async def test_pause_stops_energy_even_manual_charge_and_resume_keeps_battery(self):
        self.now=at('2026-11-05T23:00:00-06:00');self.service._virtual_anchor=self.now
        self.service.settings['virtualCarState'].update(batteryPercent=45.0,energyKWh=27.777)
        await self.exchange('set_campaign',self.body(enabled=False))
        before=deepcopy(self.service.settings['virtualCarState'])
        await self.exchange('charge_now')
        self.now+=timedelta(minutes=10)
        snapshot=(await self.exchange('get_snapshot'))['body']
        self.assertEqual(snapshot['powerKW'],0)
        self.assertEqual(self.service.settings['virtualCarState'],before)
        self.assertTrue((await self.exchange('set_campaign',self.body(enabled=True)))['ok'])
        self.assertEqual(self.service.settings['virtualCarState'],before)

    async def test_future_initial_edit_keeps_started_night_energy_and_starting_battery(self):
        self.now=at('2026-11-05T23:00:00-06:00');self.service._virtual_anchor=self.now
        self.service.settings['virtualCarState'].update(batteryPercent=45.0,energyKWh=27.777)
        before=deepcopy(self.service.settings['virtualCarState'])
        await self.exchange('set_campaign',self.body(initialBatteryPercent=30))
        self.assertEqual(self.service.settings['campaign']['initialBatteryPercent'],30)
        self.assertEqual(self.service.settings['overnightRun']['initialBatteryPercent'],20)
        self.assertEqual(self.service.settings['virtualCarState'],before)

    async def test_history_phone_and_screen_agree_and_malformed_pages_are_rejected(self):
        phone=await self.exchange('get_history')
        self.assertTrue(phone['ok'])
        status,screen=await self.http('GET','/api/history?cursor=0&limit=8')
        self.assertEqual(status,200);self.assertEqual(screen,phone['body'])
        self.assertEqual(len(screen['week']['days']),7)
        self.assertEqual(len(screen['sessions']),1)
        for query in ('cursor=-1','cursor=a','limit=9','cursor=0&cursor=1','wrong=1'):
            self.assertEqual((await self.http('GET','/api/history?'+query))[0],400)

    async def test_screen_routine_rejects_cross_origin_and_legacy_schedule_does_not_conflict(self):
        self.assertEqual((await self.http('POST','/api/campaign',self.body(),origin='https://example.com'))[0],403)
        result=await self.exchange('set_weekly',self.service.settings['weekly'])
        self.assertFalse(result['ok']);self.assertIn('Nightly routine',result['error']['message'])

    async def test_live_routine_survives_controller_restart_and_keeps_the_identity(self):
        await self.exchange('set_campaign',self.body(targetBatteryPercent=85))
        fresh=ControllerService(self.path,allow_wifi_provision=False,app_test=True,clock=lambda:self.now)
        self.assertEqual(fresh.settings,self.service.settings)
        self.assertEqual(fresh.info.device_id,self.service.info.device_id)

    async def test_history_capture_is_durable_without_affecting_car_progress(self):
        before=deepcopy(self.service.settings)
        await self.service.capture_history(force=True)
        self.assertEqual(self.service.settings,before)
        self.assertEqual(len(list((self.path/'history').glob('*.json'))),1)
