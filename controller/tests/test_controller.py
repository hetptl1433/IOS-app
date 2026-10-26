import asyncio
import base64
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import struct
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import AsyncMock, Mock, patch
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from sparc_controller.protocol import DeviceInfo, FrameBuffer, MAX_FRAME, ProtocolError, SecureChannel, compact_json, derive_key, frame, setup_key
from sparc_controller.network import NetworkManagerWiFi, WiFiError, validate_wifi
from sparc_controller.service import ControllerService, initialize, validate_weekly
from sparc_controller.http import HTTPTransport, local_peer

FIXTURE = json.loads((Path(__file__).resolve().parents[1] / "fixtures/wire-v1.json").read_text())


def fixture_channel():
    info = FIXTURE["info"]
    return SecureChannel(setup_key(FIXTURE["setupKeyHex"]), DeviceInfo(info["deviceId"], info["bootId"], info["name"]))


class ProtocolTests(unittest.TestCase):
    def test_cross_language_golden_vectors(self):
        channel = fixture_channel()
        master = setup_key(FIXTURE["setupKeyHex"])
        self.assertEqual(derive_key(master, channel.info.device_id, "c2d").hex(), FIXTURE["clientToDeviceKeyHex"])
        self.assertEqual(derive_key(master, channel.info.device_id, "d2c").hex(), FIXTURE["deviceToClientKeyHex"])
        encoded = channel.seal(FIXTURE["request"], "c2d", bytes.fromhex(FIXTURE["requestNonceHex"]))
        self.assertEqual(json.loads(encoded), FIXTURE["requestEnvelope"])
        self.assertEqual(channel.open_request(encoded, now=1700000000), FIXTURE["request"])
        response = channel.open(compact_json(FIXTURE["responseEnvelope"]), "d2c")
        self.assertEqual(response, FIXTURE["response"])

    def test_replay_and_stale_request_rejected(self):
        channel = fixture_channel()
        packet = compact_json(FIXTURE["requestEnvelope"])
        channel.open_request(packet, now=1700000000)
        with self.assertRaises(ProtocolError): channel.open_request(packet, now=1700000000)
        with self.assertRaises(ProtocolError): fixture_channel().open_request(packet, now=1700000091)
        with self.assertRaises(ProtocolError): fixture_channel().open_request(packet, now=1699999909)

    def test_wrong_key_boot_direction_and_tamper_rejected(self):
        packet = compact_json(FIXTURE["requestEnvelope"])
        channel = fixture_channel()
        wrong_key = SecureChannel(b"x"*32, channel.info)
        wrong_boot = SecureChannel(setup_key(FIXTURE["setupKeyHex"]), DeviceInfo(channel.info.device_id, str(uuid.uuid4()), "Other boot"))
        for other in (wrong_key, wrong_boot):
            with self.assertRaises(ProtocolError): other.open(packet, "c2d")
        with self.assertRaises(ProtocolError): channel.open(packet, "d2c")
        envelope = dict(FIXTURE["requestEnvelope"])
        decoded = bytearray(base64.b64decode(envelope["box"])); decoded[-1] ^= 1
        envelope["box"] = base64.b64encode(decoded).decode()
        with self.assertRaises(ProtocolError): channel.open(compact_json(envelope), "c2d")

    def test_invalid_key_and_envelope_are_bounded(self):
        for key in ("", "0"*63, "0"*65, "g"*64, "0"*63+"é", "0"*64+"\u0301"):
            with self.assertRaises(ProtocolError): setup_key(key)
        for packet in (b"", b"{"*20, b"x"*(MAX_FRAME+1), b'{"v":true,"box":""}', b'{"v":2,"box":""}'):
            with self.assertRaises(ProtocolError): fixture_channel().open(packet, "c2d")

    def test_replay_cache_fails_closed_at_capacity(self):
        channel = fixture_channel()
        channel.seen.update({str(uuid.uuid4()): 1700000100 for _ in range(4096)})
        with self.assertRaises(ProtocolError): channel.open_request(compact_json(FIXTURE["requestEnvelope"]), now=1700000000)

    def test_future_dated_request_stays_protected(self):
        channel = fixture_channel()
        request = dict(FIXTURE["request"]); request["timestamp"] += 80
        packet = channel.seal(request, "c2d")
        channel.open_request(packet, now=1700000000)
        with self.assertRaises(ProtocolError): channel.open_request(packet, now=1700000100)

    def test_frame_all_chunk_sizes_and_boundaries(self):
        payload = compact_json(FIXTURE["requestEnvelope"])
        for size in range(1, 41):
            buffer = FrameBuffer(); packet = frame(payload); output = None
            for offset in range(0, len(packet), size): output = buffer.append(packet[offset:offset+size])
            self.assertEqual(output, payload)
        self.assertEqual(FrameBuffer().append(frame(b"x"*MAX_FRAME)), b"x"*MAX_FRAME)
        for packet in (struct.pack(">I", 0), struct.pack(">I", MAX_FRAME+1), frame(b"x")+b"x"):
            with self.assertRaises(ProtocolError): FrameBuffer().append(packet)

    def test_partial_frame_expires(self):
        with patch("sparc_controller.protocol.time.monotonic", return_value=100): buffer = FrameBuffer(); buffer.append(b"\0\0")
        with patch("sparc_controller.protocol.time.monotonic", return_value=116): self.assertEqual(buffer.append(frame(b"fresh")), b"fresh")


class ValidationTests(unittest.TestCase):
    def test_wifi_unicode_and_shell_characters_remain_data(self):
        self.assertEqual(validate_wifi('Home;$(never-run)', 'secret;"$pass'), ('Home;$(never-run)', 'secret;"$pass'))
        self.assertEqual(validate_wifi("家庭", "a"*64), ("家庭", "a"*64))
        for ssid, password in (("", "password1"), ("x"*33, "password1"), ("home", "short"), ("home", "g"*64), ("home\0", "password1")):
            with self.assertRaises(WiFiError): validate_wifi(ssid, password)

    def test_local_peer_allowlist(self):
        for address in ("192.168.1.4", "10.0.0.3", "172.16.4.9", "127.0.0.1", "fe80::1", "fd12::1", "::ffff:192.168.1.2"):
            self.assertTrue(local_peer(address), address)
        for address in ("8.8.8.8", "172.32.0.1", "192.0.2.1", "2606:4700::1111", "invalid"):
            self.assertFalse(local_peer(address), address)


class FakeWiFi:
    def __init__(self): self.calls = []
    async def connect(self, ssid, password):
        validate_wifi(ssid, password); self.calls.append((ssid, password))
        return {"host": "sparc-test.local", "connected": True}


class NetworkManagerTests(unittest.IsolatedAsyncioTestCase):
    def network_bus(self, state=100):
        from dbus_next import Variant
        manager = Mock()
        manager.call_get_device_by_ip_iface = AsyncMock(return_value="/device/wlan0")
        manager.call_add_and_activate_connection = AsyncMock(return_value=("/settings/new", "/active/new"))
        manager.call_activate_connection = AsyncMock()
        properties = Mock()
        properties.call_get = AsyncMock(side_effect=[Variant("o", "/active/old"), Variant("u", state), Variant("o", "/active/new")])
        previous = Mock()
        previous.call_get = AsyncMock(return_value=Variant("o", "/settings/old"))
        attempted = Mock()
        attempted.call_delete = AsyncMock()
        interfaces = {"/org/freedesktop/NetworkManager": manager, "/device/wlan0": properties,
                      "/active/old": previous, "/settings/new": attempted}
        bus = Mock()
        bus.introspect = AsyncMock(return_value=None)
        bus.get_proxy_object.side_effect = lambda destination, path, introspection: Mock(get_interface=Mock(return_value=interfaces[path]))
        factory = Mock(connect=AsyncMock(return_value=bus))
        return factory, bus, manager, properties, attempted

    async def test_success_waits_for_new_active_connection(self):
        factory, bus, manager, _, attempted = self.network_bus()
        with patch("dbus_next.aio.MessageBus", return_value=factory):
            self.assertTrue((await NetworkManagerWiFi().connect("home", "test-only-password"))["connected"])
        manager.call_activate_connection.assert_not_awaited()
        attempted.call_delete.assert_not_awaited()
        bus.disconnect.assert_called_once()

    async def test_failed_activation_deletes_only_attempt_and_restores_previous(self):
        factory, bus, manager, _, attempted = self.network_bus(state=120)
        with patch("dbus_next.aio.MessageBus", return_value=factory):
            with self.assertRaises(WiFiError):
                await NetworkManagerWiFi().connect("home", "test-only-password")
        attempted.call_delete.assert_awaited_once()
        manager.call_activate_connection.assert_awaited_once_with("/settings/old", "/device/wlan0", "/")
        bus.disconnect.assert_called_once()

    async def test_cancelled_activation_restores_previous_and_propagates_cancellation(self):
        from dbus_next import Variant
        factory, bus, manager, properties, attempted = self.network_bus()
        properties.call_get.side_effect = [Variant("o", "/active/old"), asyncio.CancelledError()]
        with patch("dbus_next.aio.MessageBus", return_value=factory):
            with self.assertRaises(asyncio.CancelledError):
                await NetworkManagerWiFi().connect("home", "test-only-password")
        attempted.call_delete.assert_awaited_once()
        manager.call_activate_connection.assert_awaited_once_with("/settings/old", "/device/wlan0", "/")
        bus.disconnect.assert_called_once()


class ServiceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.path = Path(self.temp.name)
        self.identity = initialize(self.path); self.wifi = FakeWiFi()
        self.service = ControllerService(self.path, wifi=self.wifi)
        self.channel = SecureChannel(setup_key(self.identity["setupKey"]), self.service.info)

    async def asyncTearDown(self): self.temp.cleanup()

    async def exchange(self, operation, body=None, transport="lan"):
        request = {"id":str(uuid.uuid4()), "timestamp":time.time(), "operation":operation, "body":body or {}}
        reply = await self.service.handle(self.channel.seal(request, "c2d"), transport)
        return self.channel.open(reply, "d2c")

    async def test_pair_and_wifi_require_ble_and_open_window(self):
        self.assertFalse((await self.exchange("pair"))["ok"])
        self.assertTrue((await self.exchange("pair", transport="ble"))["ok"])
        body = {"ssid":"home", "password":"test-only-password"}
        self.assertFalse((await self.exchange("wifi.provision", body))["ok"])
        self.assertEqual(self.wifi.calls, [])
        self.assertTrue((await self.exchange("wifi.provision", body, "ble"))["ok"])
        self.service.setup_deadline = time.monotonic()-1
        self.assertFalse((await self.exchange("wifi.provision", body, "ble"))["ok"])
        self.assertEqual(len(self.wifi.calls), 1)

    async def test_no_fake_vehicle_success(self):
        for operation in ("get_snapshot", "charge_now", "pause", "resume", "end"):
            response = await self.exchange(operation)
            self.assertFalse(response["ok"])
            self.assertIn("vehicle adapter", response["error"]["message"])

    async def test_weekly_saved_durably_and_today_independent(self):
        weekly = dict(self.service.settings["weekly"]); weekly["targetPercent"] = 90
        response = await self.exchange("set_weekly", weekly)
        self.assertTrue(response["ok"]); self.assertTrue(response["body"]["storedOnly"])
        reloaded = ControllerService(self.path, wifi=self.wifi)
        self.assertEqual(reloaded.settings["weekly"], weekly)
        self.assertEqual((self.path/"settings.json").stat().st_mode & 0o777, 0o600)
        self.assertTrue((await self.exchange("set_today", {"today":None}))["ok"])
        self.assertEqual(self.service.settings["weekly"], weekly)

    async def test_save_failure_leaves_in_memory_settings_unchanged(self):
        previous = dict(self.service.settings["weekly"])
        updated = dict(previous); updated["targetPercent"] = 95
        with patch("sparc_controller.service.atomic_save", side_effect=OSError("test disk error")):
            self.assertFalse((await self.exchange("set_weekly", updated))["ok"])
        self.assertEqual(self.service.settings["weekly"], previous)

    async def test_enabled_requires_days_but_disabled_can_be_empty(self):
        value = dict(self.service.settings["weekly"]); value["days"] = []
        with self.assertRaises(ValueError): validate_weekly(value)
        value["enabled"] = False
        self.assertEqual(validate_weekly(value)["days"], [])

    async def test_identity_never_overwritten(self):
        with self.assertRaises(ValueError): initialize(self.path)
        self.assertEqual(json.loads((self.path/"identity.json").read_text()), self.identity)

    async def test_simultaneous_initialization_preserves_winning_identity(self):
        destination = self.path / "new-controller"
        barrier = threading.Barrier(2)
        def attempt():
            barrier.wait()
            try:
                return initialize(destination)
            except ValueError:
                return None
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: attempt(), range(2)))
        winners = [identity for identity in results if identity is not None]
        self.assertEqual(len(winners), 1)
        self.assertEqual(json.loads((destination / "identity.json").read_text()), winners[0])
        self.assertEqual(list(destination.glob("*.tmp")), [])

    async def test_http_loopback_encrypted_settings_and_replay_rejection(self):
        server = await asyncio.start_server(HTTPTransport(self.service).handle, "127.0.0.1", 0, limit=8192)
        port = server.sockets[0].getsockname()[1]
        async def request(packet):
            reader, writer = await asyncio.open_connection("127.0.0.1", port)
            writer.write(packet); await writer.drain()
            response = await reader.read(); writer.close(); await writer.wait_closed()
            header, payload = response.split(b"\r\n\r\n", 1)
            return header.split(b"\r\n")[0], payload
        try:
            status, body = await request(b"GET /v1/info HTTP/1.1\r\nHost: localhost\r\n\r\n")
            self.assertIn(b"200", status); self.assertEqual(json.loads(body)["deviceId"], self.identity["deviceId"])
            payload = self.channel.seal({"id":str(uuid.uuid4()),"timestamp":time.time(),"operation":"get_settings","body":{}}, "c2d")
            packet = b"POST /v1/exchange HTTP/1.1\r\nHost: localhost\r\nContent-Length: "+str(len(payload)).encode()+b"\r\n\r\n"+payload
            status, body = await request(packet)
            self.assertIn(b"200", status); self.assertTrue(self.channel.open(body,"d2c")["ok"])
            status, _ = await request(packet); self.assertIn(b"401", status)
            status, _ = await request(b"POST /v1/exchange HTTP/1.1\r\nContent-Length: 99999999\r\n\r\n")
            self.assertIn(b"413", status)
        finally:
            server.close(); await server.wait_closed()


if __name__ == "__main__": unittest.main()
