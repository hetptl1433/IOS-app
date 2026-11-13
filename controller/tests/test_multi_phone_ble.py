"""BlueZ backend tests only: no adapter, iPhone, application launch or radio."""
import asyncio
import contextlib
import io
import json
from pathlib import Path
import struct
import sys
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from dbus_next import DBusError, Variant
from sparc_controller.bluez import (
    MAX_ACTIVE_PEERS, MAX_PENDING_PER_PEER, InfoCharacteristic, RxCharacteristic, TxCharacteristic,
)
from sparc_controller.protocol import BLE_MAX_VALUE, DeviceInfo, FrameBuffer, MAX_FRAME, SecureChannel, frame


def peer(index):
    return f'/org/bluez/hci0/dev_00_00_00_00_00_{index:02X}'


def options(index):
    return {'device':Variant('o',peer(index))}


class AuthenticatedService:
    def __init__(self):
        self.info=DeviceInfo(str(uuid.uuid4()),str(uuid.uuid4()),'Backend test')
        self.channel=SecureChannel(bytes([17])*32,self.info)
        self.commands=[]

    def request(self,label):
        ident=str(uuid.uuid4())
        return ident,self.channel.seal({'id':ident,'timestamp':time.time(),'operation':'test',
                                        'body':{'label':label}},'c2d')

    async def handle(self,packet,transport):
        request=self.channel.open_request(packet)
        self.commands.append(request['body']['label'])
        await asyncio.sleep(0)
        return self.channel.seal({'id':request['id'],'ok':True,'body':request['body']},'d2c')


class FunctionService:
    def __init__(self,handle): self.handle=handle


class MultiPhoneBLETests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tx=TxCharacteristic()
        self.notifications=[]
        self.properties=[]
        self.tx.emit_properties_changed=self.emit
        self.tx.StartNotify()
        self.rx=None
        original_sleep=asyncio.sleep
        async def yield_only(_delay): await original_sleep(0)
        self.fast=patch('sparc_controller.bluez.asyncio.sleep',side_effect=yield_only)
        self.fast.start()

    async def asyncTearDown(self):
        if self.rx is not None: await self.rx.close()
        self.fast.stop()

    def emit(self,changes):
        self.properties.append(changes)
        if 'Value' in changes: self.notifications.append(bytes(changes['Value']))

    def create_rx(self,service):
        self.rx=RxCharacteristic(service,self.tx)
        return self.rx

    async def drain(self):
        tasks=tuple(self.rx.tasks)
        if tasks: await asyncio.wait_for(asyncio.gather(*tasks),1)

    def decoded_packets(self):
        decoder=FrameBuffer()
        packets=[]
        for value in self.notifications:
            packet=decoder.append(value)
            if packet is not None: packets.append(packet)
        self.assertEqual(decoder.data,b'')
        return packets

    async def test_two_phones_interleaved_writes_receive_whole_authenticated_broadcast_frames(self):
        service=AuthenticatedService();rx=self.create_rx(service)
        first,one=service.request('phone one')
        second,two=service.request('phone two')
        one,two=frame(one),frame(two)
        # ATT writes can be interleaved and need not align with a length header.
        chunks=[[(one[i:i+7],options(1)) for i in range(0,len(one),7)],
                [(two[i:i+11],options(2)) for i in range(0,len(two),11)]]
        while any(chunks):
            for pending in chunks:
                if pending:
                    value,opts=pending.pop(0);rx.WriteValue(value,opts)
        await self.drain()
        self.assertCountEqual(service.commands,['phone one','phone two'])
        self.assertTrue(all(1<=len(value)<=20 for value in self.notifications))
        for _subscriber in range(2):
            replies=[service.channel.open(packet,'d2c') for packet in self.decoded_packets()]
            self.assertCountEqual([reply['id'] for reply in replies],[first,second])
        self.assertFalse(rx.queues)
        self.assertFalse(rx.processing)

    async def test_one_peer_fifo_accepts_bounded_requests_while_another_peer_stays_responsive(self):
        blocked=asyncio.Event();entered=asyncio.Event();other_done=asyncio.Event();calls=[]
        async def handle(packet,transport):
            calls.append(packet)
            if packet==b'A1': entered.set();await blocked.wait()
            if packet==b'B1': other_done.set()
            return packet
        rx=self.create_rx(FunctionService(handle))
        rx.WriteValue(frame(b'A1'),options(1))
        await asyncio.wait_for(entered.wait(),1)
        for n in range(2,MAX_PENDING_PER_PEER+1): rx.WriteValue(frame(f'A{n}'.encode()),options(1))
        with self.assertRaises(DBusError) as caught: rx.WriteValue(frame(b'too many'),options(1))
        self.assertEqual(caught.exception.type,'org.bluez.Error.InProgress')
        rx.WriteValue(frame(b'B1'),options(2))
        await asyncio.wait_for(other_done.wait(),1)
        blocked.set();await self.drain()
        self.assertEqual([value for value in calls if value.startswith(b'A')],
                         [f'A{n}'.encode() for n in range(1,MAX_PENDING_PER_PEER+1)])

    async def test_anonymous_or_non_bluez_identity_cannot_share_a_fragment_buffer(self):
        rx=self.create_rx(AuthenticatedService())
        for opts in ({},{'device':Variant('o','/')},{'device':Variant('o','/some/other/device')},
                     {'device':Variant('o','/org/bluez/hci0/dev_invalid')}):
            with self.subTest(opts=opts),self.assertRaises(DBusError) as caught:
                rx.WriteValue(frame(b'hello'),opts)
            self.assertEqual(caught.exception.type,'org.bluez.Error.NotAuthorized')
        self.assertFalse(rx.buffers)
        self.assertFalse(rx.tasks)

    async def test_partial_buffers_and_active_workers_share_the_global_peer_bound(self):
        held=asyncio.Event()
        async def handle(packet,transport): await held.wait();return packet
        rx=self.create_rx(FunctionService(handle))
        for index in range(1,MAX_ACTIVE_PEERS+1):
            if index%2: rx.WriteValue(frame(b'partial')[:4],options(index))
            else: rx.WriteValue(frame(b'working'),options(index))
        with self.assertRaises(DBusError) as caught: rx.WriteValue(frame(b'excess'),options(99))
        self.assertEqual(caught.exception.type,'org.bluez.Error.InProgress')
        self.assertLessEqual(len(rx.buffers)+len(rx.queues),MAX_ACTIVE_PEERS)

    async def test_expired_partial_buffers_release_capacity_without_touching_other_peers(self):
        rx=self.create_rx(AuthenticatedService())
        with patch('sparc_controller.bluez.time.monotonic',return_value=0):
            for index in range(1,MAX_ACTIVE_PEERS+1): rx.WriteValue(frame(b'partial')[:4],options(index))
        with patch('sparc_controller.bluez.time.monotonic',return_value=16):
            rx.WriteValue(frame(b'new')[:4],options(99))
        self.assertEqual(set(rx.buffers),{peer(99)})

    async def test_malformed_or_oversized_frame_drops_only_its_peer_buffer(self):
        service=AuthenticatedService();rx=self.create_rx(service)
        _,payload=service.request('valid')
        packet=frame(payload)
        rx.WriteValue(packet[:9],options(1))
        for bad in (struct.pack('>I',MAX_FRAME+1),b'x'*(MAX_FRAME+5),b''):
            with self.assertRaises(DBusError) as caught: rx.WriteValue(bad,options(2))
            self.assertEqual(caught.exception.type,'org.bluez.Error.InvalidValueLength')
            self.assertIn(peer(1),rx.buffers)
            self.assertNotIn(peer(2),rx.buffers)
        rx.WriteValue(packet[9:],options(1));await self.drain()
        self.assertEqual(service.commands,['valid'])

    async def test_unauthenticated_request_does_not_poison_following_peer_requests(self):
        service=AuthenticatedService();rx=self.create_rx(service)
        first,payload=service.request('valid after bad')
        second,other=service.request('other phone')
        rx.WriteValue(frame(b'not authenticated'),options(1))
        rx.WriteValue(frame(payload),options(1))
        rx.WriteValue(frame(other),options(2))
        await self.drain()
        replies=[service.channel.open(packet,'d2c') for packet in self.decoded_packets()]
        self.assertCountEqual([reply['id'] for reply in replies],[first,second])

    async def test_global_send_lock_serializes_all_chunks_of_concurrent_frames(self):
        first,second=b'A'*251,b'B'*319
        await asyncio.gather(self.tx.send(first),self.tx.send(second))
        self.assertEqual(self.decoded_packets(),[first,second])

    async def test_repeated_start_notify_is_idempotent_and_one_final_stop_disables_it(self):
        epoch=self.tx.notification_epoch
        self.tx.StartNotify();self.tx.StartNotify()
        self.assertEqual(self.tx.notification_epoch,epoch)
        self.assertEqual(sum('Notifying' in change for change in self.properties),1)
        # BlueZ forwards this callback only after its last CCC subscriber leaves.
        self.tx.StopNotify()
        self.assertFalse(self.tx.notifying)
        self.assertFalse(await self.tx.send(b'not subscribed'))
        self.tx.StopNotify()
        self.assertEqual(self.tx.notification_epoch,epoch+1)

    async def test_stop_start_during_send_does_not_leak_the_old_frame_tail(self):
        def rotate_after_first(changes):
            self.emit(changes)
            if 'Value' in changes and len(self.notifications)==1:
                self.tx.StopNotify();self.tx.StartNotify()
        self.tx.emit_properties_changed=rotate_after_first
        self.assertFalse(await self.tx.send(b'A'*100))
        self.assertEqual(len(self.notifications),1)
        self.tx.emit_properties_changed=self.emit
        self.notifications.clear()
        self.assertTrue(await self.tx.send(b'fresh'))
        self.assertEqual(self.decoded_packets(),[b'fresh'])

    async def test_response_from_old_subscription_epoch_is_dropped_after_reconnect(self):
        entered=asyncio.Event();release=asyncio.Event()
        async def handle(packet,transport): entered.set();await release.wait();return packet
        rx=self.create_rx(FunctionService(handle))
        rx.WriteValue(frame(b'old request'),options(1));await asyncio.wait_for(entered.wait(),1)
        self.tx.StopNotify();self.tx.StartNotify();release.set();await self.drain()
        self.assertEqual(self.notifications,[])
        rx.WriteValue(frame(b'new request'),options(2));await self.drain()
        self.assertEqual(self.decoded_packets(),[b'new request'])

    async def test_unexpected_worker_failure_preserves_queue_and_never_logs_payload(self):
        async def handle(packet,transport):
            if packet==b'bad': raise RuntimeError('secret-credential-sentinel')
            return packet
        rx=self.create_rx(FunctionService(handle))
        with contextlib.redirect_stdout(io.StringIO()) as output:
            rx.WriteValue(frame(b'bad'),options(1));rx.WriteValue(frame(b'good'),options(1))
            await self.drain()
        self.assertNotIn('secret-credential-sentinel',output.getvalue())
        self.assertEqual(self.decoded_packets(),[b'good'])

    async def test_shutdown_cancels_and_releases_all_peer_work(self):
        entered=asyncio.Event()
        async def handle(packet,transport): entered.set();await asyncio.Future()
        rx=self.create_rx(FunctionService(handle))
        rx.WriteValue(frame(b'waiting'),options(1));await asyncio.wait_for(entered.wait(),1)
        rx.WriteValue(frame(b'queued'),options(1));rx.WriteValue(frame(b'partial')[:5],options(2))
        tasks=tuple(rx.tasks)
        await rx.close()
        self.assertTrue(all(task.done() for task in tasks))
        for values in (rx.tasks,rx.buffers,rx.queues,rx.processing,rx.inflight): self.assertFalse(values)


class InfoCharacteristicTests(unittest.TestCase):
    # A real Pi clock reading at its longest (18 characters of JSON).
    NOW=1794492611.1228213

    def setUp(self):
        # The longest real name with every capability switched on: the biggest info a Pi can send.
        self.info=DeviceInfo(str(uuid.uuid4()),str(uuid.uuid4()),'SPARC home controller',
                             wifi_provision=True,app_test=True,pairing_code=True)
        self.characteristic=InfoCharacteristic(SimpleNamespace(info=self.info))
        clock=patch('sparc_controller.protocol.time',SimpleNamespace(time=lambda:self.NOW,monotonic=time.monotonic))
        clock.start();self.addCleanup(clock.stop)

    def test_info_fits_one_bluetooth_value_and_keeps_what_setup_needs(self):
        value=self.characteristic.value_at(0)
        self.assertLessEqual(len(value),BLE_MAX_VALUE)
        info,full=json.loads(value),self.info.public()
        # The app pairs with the code automatically only when it sees "pairing-code".
        self.assertEqual(info['capabilities'],['pair','ping','wifi.provision','app-test','pairing-code'])
        self.assertEqual({k:v for k,v in info.items() if k!='capabilities'},
                         {k:v for k,v in full.items() if k!='capabilities'})
        self.assertIn('tesla_authorize',full['capabilities'])

    def test_short_reads_reassemble_the_whole_info(self):
        # The smallest Bluetooth packet size returns 22 bytes per read.
        value=self.characteristic.value_at(0)
        chunks=[self.characteristic.value_at(offset)[:22] for offset in range(0,len(value),22)]
        self.assertEqual(b''.join(chunks),value)
        self.assertEqual(self.characteristic.value_at(len(value)),b'')
        with self.assertRaises(DBusError): self.characteristic.value_at(len(value)+1)


if __name__=='__main__': unittest.main()
