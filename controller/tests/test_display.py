import asyncio
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import patch
import uuid
from urllib.parse import parse_qs, urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from sparc_controller.display import STATIC, DisplayTransport, loopback
from sparc_controller.protocol import SecureChannel, setup_key
from sparc_controller.service import ControllerService, initialize
from sparc_controller.__main__ import parse_args

UTC = timezone.utc


class DisplayTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name)
        self.identity = initialize(self.path)
        # Tuesday 12:00 in America/Chicago (CDT).
        self.now = datetime(2026, 10, 27, 17, 0, tzinfo=UTC)
        self.service = ControllerService(self.path, allow_wifi_provision=False, app_test=True, clock=lambda: self.now)
        await self.serve(self.service)

    async def serve(self, service):
        self.transport = DisplayTransport(service, 0)
        self.server = await asyncio.start_server(self.transport.handle, "127.0.0.1", 0)
        self.transport.port = self.server.sockets[0].getsockname()[1]

    async def asyncTearDown(self):
        self.server.close()
        await self.server.wait_closed()
        self.temp.cleanup()

    async def request(self, method, path, body=None, headers=None):
        reader, writer = await asyncio.open_connection("127.0.0.1", self.transport.port)
        payload = b"" if body is None else body if isinstance(body, bytes) else json.dumps(body).encode()
        fields = {"Host": f"127.0.0.1:{self.transport.port}"}
        if body is not None:
            fields.update({"Content-Type": "application/json", "Content-Length": str(len(payload))})
        fields.update(headers or {})
        head = f"{method} {path} HTTP/1.1\r\n" + "".join(f"{k}: {v}\r\n" for k, v in fields.items() if v is not None)
        writer.write((head + "\r\n").encode() + payload)
        await writer.drain()
        raw = await reader.read()
        writer.close()
        header, _, content = raw.partition(b"\r\n\r\n")
        lines = header.decode().split("\r\n")
        return int(lines[0].split(" ")[1]), dict(line.split(": ", 1) for line in lines[1:]), content

    async def post(self, path, body, headers=None):
        status, _, content = await self.request("POST", path, body, headers)
        return status, json.loads(content)

    async def state(self):
        status, _, content = await self.request("GET", "/api/state")
        self.assertEqual(status, 200)
        return json.loads(content)

    async def test_page_and_assets_are_served_with_a_strict_policy(self):
        status, headers, content = await self.request("GET", "/")
        self.assertEqual(status, 200)
        self.assertTrue(headers["Content-Type"].startswith("text/html"))
        self.assertIn("default-src 'self'", headers["Content-Security-Policy"])
        self.assertEqual(headers["X-Content-Type-Options"], "nosniff")
        self.assertIn(b"<title>SPARC</title>", content)
        for path, (_, content_type) in STATIC.items():
            status, headers, _ = await self.request("GET", path)
            self.assertEqual((status, headers["Content-Type"]), (200, content_type))
        # The kiosk opens the page with a query string.
        status, _, content = await self.request("GET", "/?kiosk=1")
        self.assertEqual(status, 200)
        self.assertIn(b"<title>SPARC</title>", content)
        self.assertEqual((await self.request("GET", "/api/state?t=1"))[0], 200)
        self.assertEqual((await self.request("GET", "/healthz"))[0], 200)

    async def test_state_projects_charging_without_writing_settings(self):
        status, reply = await self.post("/api/command", {"command": "charge_now"})
        self.assertEqual(status, 200)
        self.assertEqual(reply["snapshot"]["state"], "charging")
        self.now += timedelta(minutes=30)
        with patch("sparc_controller.service.atomic_save") as save:
            state = await self.state()
        save.assert_not_called()
        self.assertAlmostEqual(state["batteryPercent"], 40 + 11.5 * 0.5 * 0.9)
        self.assertAlmostEqual(state["batteryPercentPerHour"], 10.35)
        self.assertEqual(state["snapshot"]["powerKW"], 11.5)
        self.assertEqual(self.service.settings["virtualCarState"]["batteryPercent"], 40)
        self.assertEqual(state["local"], {"date": "2026-10-27", "minutes": 750})

    async def test_tesla_activation_and_commands_use_explicit_local_endpoints(self):
        from test_connections_service import FakeFleet
        fleet = FakeFleet(lambda:self.now)
        fleet.state.update(authorized=True,status='connected',vehicleId='123',
                           vehicleName='Actual Fleet vehicle',commandsReady=True)
        self.service.tesla = fleet
        car = dict(self.service.settings['virtualCarState'])
        # A saved setup, status read, and invalid activation cannot actuate.
        status,reply = await self.post('/api/connections/activate',{'mode':'smart_plug'})
        self.assertEqual(status,400)
        self.assertEqual(self.service.active_connection_mode,'virtual')
        status,reply = await self.post('/api/connections/activate',{'mode':'tesla'})
        self.assertEqual(status,200,reply)
        self.assertEqual(reply['connections']['activeMode'],'tesla')
        self.assertFalse(any(name=='command' for name,_ in fleet.calls))
        state = await self.state()
        self.assertEqual(state['snapshot']['batteryPercent'],62)
        self.assertTrue(state['snapshot']['manualOnly'])
        self.assertFalse(state['snapshot']['virtualCar'])
        status,reply = await self.post('/api/connections/tesla/command',{'command':'charge_stop'})
        self.assertEqual(status,200,reply)
        self.assertTrue(reply['actionResult']['accepted'])
        self.assertEqual([call for call in fleet.calls if call[0]=='command'],[('command',('charge_stop',None))])
        self.assertEqual(self.service.settings['virtualCarState'],car)
        status,reply = await self.post('/api/connections/activate',{'mode':'virtual'})
        self.assertEqual(status,200)
        self.assertEqual(reply['connections']['activeMode'],'virtual')
        status,reply = await self.post('/api/connections/tesla/command',{'command':'charge_start'})
        self.assertEqual(status,400)
        self.assertEqual(sum(name=='command' for name,_ in fleet.calls),1)

    def configure_tesla_authorization(self):
        from test_tesla import FakeTesla
        from sparc_controller.tesla import AUDIENCES, TeslaAdapter, _default_save
        _default_save(self.path/'tesla-client.json',{
            'clientId':'687f9e0c-2855-4d0e-b617-780ebd52843a', 'clientSecret':'private-client-secret',
            'redirectURI':'https://sparc.ipodeskai.com/auth/tesla/callback',
            'audience':AUDIENCES[0], 'domain':'sparc.ipodeskai.com'})
        fleet = FakeTesla(lambda:self.now)
        self.service.tesla = TeslaAdapter(self.path,clock=lambda:self.now,transport=fleet)
        return fleet

    async def test_tesla_screen_can_finish_its_own_authorization_once_without_vehicle_commands(self):
        fleet = self.configure_tesla_authorization()
        status,reply = await self.post('/api/connections/tesla/authorize',{})
        self.assertEqual(status,200,reply)
        state = parse_qs(urlsplit(reply['actionResult']['authorizationURL']).query)['state'][0]
        self.assertEqual((await self.post('/api/connections/tesla/complete',{'code':'private-code','state':'0'*64}))[0],400)
        self.assertEqual(fleet.calls,[])
        status,reply = await self.post('/api/connections/tesla/complete',{'code':'private-code','state':state})
        self.assertEqual(status,200,reply)
        self.assertTrue(reply['connections']['tesla']['authorized'])
        self.assertEqual(reply['connections']['activeMode'],'virtual')
        self.assertNotIn('private-code',json.dumps(reply))
        self.assertNotIn('access-1',json.dumps(reply))
        self.assertEqual(fleet.token_count,1)
        self.assertEqual(len(fleet.calls),1)
        self.assertEqual((await self.post('/api/connections/tesla/complete',{'code':'private-code','state':state}))[0],400)
        self.assertEqual(fleet.token_count,1)

    async def test_tesla_screen_completion_preserves_origin_expiry_and_app_binding_checks(self):
        fleet = self.configure_tesla_authorization()
        started = await self.service.tesla.authorize_start(str(uuid.uuid4()))
        state = parse_qs(urlsplit(started['authorizationURL']).query)['state'][0]
        body = {'code':'private-code','state':state}
        self.assertEqual((await self.post('/api/connections/tesla/complete',body))[0],400)
        status,reply = await self.post('/api/connections/tesla/authorize',{})
        body['state'] = parse_qs(urlsplit(reply['actionResult']['authorizationURL']).query)['state'][0]
        self.assertEqual((await self.post('/api/connections/tesla/complete',body,{'Origin':'https://attacker.example'}))[0],403)
        self.assertEqual((await self.post('/api/connections/tesla/complete',dict(body,extra='field')))[0],400)
        self.now += timedelta(minutes=10)
        self.assertEqual((await self.post('/api/connections/tesla/complete',body))[0],400)
        self.assertEqual(fleet.calls,[])

    async def test_tesla_display_activation_rejects_stale_data_without_touching_virtual_state(self):
        from test_connections_service import FakeFleet
        fleet = FakeFleet(lambda:self.now)
        fleet.state.update(authorized=True,status='connected',vehicleId='123',
                           vehicleName='Actual Fleet vehicle',commandsReady=True)
        fleet.stale = True
        self.service.tesla = fleet
        settings = json.loads(json.dumps(self.service.settings))
        status,reply = await self.post('/api/connections/activate',{'mode':'tesla'})
        self.assertEqual(status,400)
        self.assertIn('stale',reply['error'])
        self.assertEqual(self.service.settings,settings)
        self.assertFalse(any(name=='command' for name,_ in fleet.calls))

    async def test_controls_weekly_and_trip_round_trip(self):
        self.assertIsNone((await self.state())["manual"])
        for command, expected in [("charge_now", "charging"), ("pause", "paused"), ("resume", "charging"), ("end", "waiting")]:
            status, reply = await self.post("/api/command", {"command": command})
            self.assertEqual((status, reply["snapshot"]["state"]), (200, expected))
        # End holds off tomorrow's window until the Wednesday 7:00 departure.
        self.assertEqual(reply["manual"], {"state": "waiting", "until": "2026-10-28T12:00:00+00:00"})
        weekly = {"targetPercent": 90, "departureMinutes": 450, "fixedStartMinutes": 0,
                  "days": [5, 1, 3], "mode": "fixed", "enabled": True}
        status, reply = await self.post("/api/weekly", weekly)
        self.assertEqual(status, 200)
        self.assertEqual(reply["weekly"], dict(weekly, days=[1, 3, 5]))
        self.assertEqual(json.loads((self.path / "settings.json").read_text())["weekly"]["targetPercent"], 90)
        status, reply = await self.post("/api/weekly", dict(weekly, days=[]))
        self.assertEqual(status, 400)
        self.assertIn("charging day", reply["error"])
        status, reply = await self.post("/api/trip", {"dayOffset": 1, "departureMinutes": 420, "targetPercent": 100})
        self.assertEqual(status, 200)
        departure = datetime.fromisoformat(reply["trip"]["departure"])
        self.assertEqual((departure, reply["trip"]["targetPercent"]), (datetime(2026, 10, 28, 12, 0, tzinfo=UTC), 100))
        status, reply = await self.post("/api/trip", {"dayOffset": 0, "departureMinutes": 600, "targetPercent": 80})
        self.assertEqual(status, 400)
        self.assertIn("48 hours", reply["error"])
        for bad in ({"dayOffset": 2, "departureMinutes": 420}, {"dayOffset": True, "departureMinutes": 420},
                    {"dayOffset": 1, "departureMinutes": 1440}):
            self.assertEqual((await self.post("/api/trip", dict(bad, targetPercent=80)))[0], 400)
        status, reply = await self.post("/api/trip", {"clear": True})
        self.assertEqual((status, reply["trip"]), (200, None))

    async def test_rejects_other_hosts_origins_and_malformed_requests(self):
        command = {"command": "charge_now"}
        self.assertEqual((await self.request("GET", "/api/state", headers={"Host": "attacker.example"}))[0], 421)
        self.assertEqual((await self.request("GET", "/api/state", headers={"Host": None}))[0], 421)
        self.assertEqual((await self.post("/api/command", command, {"Origin": "http://attacker.example"}))[0], 403)
        self.assertEqual((await self.post("/api/command", command, {"Sec-Fetch-Site": "cross-site"}))[0], 403)
        self.assertEqual((await self.post("/api/command", command, {"Content-Type": "text/plain"}))[0], 415)
        self.assertEqual((await self.post("/api/command", command, {"Content-Length": "5000"}))[0], 413)
        self.assertEqual((await self.post("/api/command", b"{not json"))[0], 400)
        self.assertEqual((await self.post("/api/command", ["charge_now"]))[0], 400)
        self.assertEqual((await self.post("/api/command", {"command": "set_virtual_car"}))[0], 400)
        self.assertEqual((await self.request("GET", "/../sparc_controller/service.py"))[0], 404)
        self.assertEqual((await self.request("GET", "/api/missing"))[0], 404)
        self.assertEqual((await self.request("POST", "/api/state", {}))[0], 405)
        self.assertEqual((await self.request("GET", "/", headers={"host": "127.0.0.1"}))[0], 400)
        self.assertEqual(self.service.settings["appTestControl"]["manualState"], None)
        status, reply = await self.post("/api/command", command, {"Origin": f"http://localhost:{self.transport.port}",
                                                                 "Host": f"localhost:{self.transport.port}"})
        self.assertEqual((status, reply["snapshot"]["state"]), (200, "charging"))

    def test_only_loopback_peers_are_served(self):
        for address, expected in [("127.0.0.1", True), ("::1", True), ("::ffff:127.0.0.1", True),
                                  ("192.168.1.5", False), ("10.0.0.7", False), ("not an ip", False)]:
            self.assertIs(loopback(address), expected, address)

    async def test_status_reports_phone_contact_bluetooth_and_identity_prefix(self):
        controller = (await self.state())["controller"]
        self.assertEqual(controller["id"], self.identity["deviceId"][:4].upper())
        self.assertEqual((controller["phoneSeconds"], controller["phoneTransport"], controller["bluetooth"]), (None, None, False))
        self.assertIn("temperatureC", controller)
        channel = SecureChannel(setup_key(self.identity["setupKey"]), self.service.info)
        packet = channel.seal({"id": str(uuid.uuid4()), "timestamp": time.time(), "operation": "ping", "body": {}}, "c2d")
        await self.service.handle(packet, "ble")
        self.service.ble_advertising = True
        controller = (await self.state())["controller"]
        self.assertEqual((controller["phoneSeconds"], controller["phoneTransport"], controller["bluetooth"]), (0, "ble", True))

    async def test_developer_switch_turns_the_demo_car_off_and_back_on_at_40(self):
        await self.post("/api/command", {"command": "charge_now"})
        self.now += timedelta(minutes=30)
        status, reply = await self.post("/api/demo", {"enabled": False})
        self.assertEqual(status, 200)
        self.assertTrue(reply["snapshot"]["controlOnly"])
        self.assertFalse(reply["snapshot"]["virtualCar"])
        status, reply = await self.post("/api/demo", {"enabled": True})
        self.assertEqual(status, 200)
        self.assertTrue(reply["snapshot"]["virtualCar"])
        self.assertEqual((reply["batteryPercent"], reply["snapshot"]["state"]), (40, "waiting"))
        for bad in ({}, {"enabled": "yes"}, {"enabled": 1}):
            self.assertEqual((await self.post("/api/demo", bad))[0], 400)

    async def test_switched_off_car_and_plain_controller_report_no_vehicle(self):
        await self.service.dispatch("set_virtual_car", {"enabled": False}, "ble")
        state = await self.state()
        self.assertTrue(state["snapshot"]["controlOnly"])
        self.assertIsNone(state["batteryPercent"])
        status, reply = await self.post("/api/command", {"command": "charge_now"})
        self.assertEqual(status, 400)
        self.assertIn("Virtual car is off", reply["error"])
        self.server.close()
        await self.server.wait_closed()
        await self.serve(ControllerService(self.path, allow_wifi_provision=False, clock=lambda: self.now))
        state = await self.state()
        self.assertIsNone(state["snapshot"])
        self.assertEqual((await self.post("/api/command", {"command": "charge_now"}))[0], 400)

    def test_display_port_comes_from_environment_and_is_validated(self):
        with patch.dict("os.environ", {"SPARC_DISPLAY_PORT": "8767"}):
            self.assertEqual(parse_args(["--app-test"]).display_port, 8767)
        with patch.dict("os.environ", {"SPARC_DISPLAY_PORT": ""}):
            self.assertIsNone(parse_args([]).display_port)
        for value in ("80", "8766", "70000", "abc", "٨٧٦٧"):
            with self.assertRaises(SystemExit), patch("sys.stderr"):
                parse_args(["--display-port", value])


if __name__ == "__main__":
    unittest.main()
