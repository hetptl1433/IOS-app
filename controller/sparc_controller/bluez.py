"""BlueZ GATT peripheral; responses are encrypted and framed into 20-byte notifications."""
import asyncio
from collections import deque
import re
import time

from dbus_next import BusType, DBusError, Variant
from dbus_next.aio import MessageBus
from dbus_next.constants import PropertyAccess
from dbus_next.service import ServiceInterface, dbus_property, method

from .protocol import MAX_FRAME, FrameBuffer, ProtocolError, compact_json, frame

SERVICE_UUID = "7c49b5a0-8d22-4fb8-a3a8-6d7703100001"
INFO_UUID = "7c49b5a0-8d22-4fb8-a3a8-6d7703100002"
RX_UUID = "7c49b5a0-8d22-4fb8-a3a8-6d7703100003"
TX_UUID = "7c49b5a0-8d22-4fb8-a3a8-6d7703100004"
ROOT = "/app/sparc"
SERVICE_PATH = ROOT + "/service0"
MAX_ACTIVE_PEERS = 8
MAX_PENDING_PER_PEER = 4
FRAME_TTL_SECONDS = 15
DEVICE_PATH = re.compile(r"/org/bluez/hci[0-9]+/dev_(?:[0-9A-Fa-f]{2}_){5}[0-9A-Fa-f]{2}\Z")


class GattService(ServiceInterface):
    def __init__(self):
        super().__init__("org.bluez.GattService1")

    @dbus_property(access=PropertyAccess.READ)
    def UUID(self) -> 's': return SERVICE_UUID

    @dbus_property(access=PropertyAccess.READ)
    def Primary(self) -> 'b': return True

    def properties(self):
        return {"UUID": Variant("s", SERVICE_UUID), "Primary": Variant("b", True)}


class Characteristic(ServiceInterface):
    def __init__(self, uuid, flags):
        super().__init__("org.bluez.GattCharacteristic1")
        self.uuid, self.flags = uuid, flags

    @dbus_property(access=PropertyAccess.READ)
    def UUID(self) -> 's': return self.uuid

    @dbus_property(access=PropertyAccess.READ)
    def Service(self) -> 'o': return SERVICE_PATH

    @dbus_property(access=PropertyAccess.READ)
    def Flags(self) -> 'as': return self.flags

    def properties(self):
        return {"UUID": Variant("s", self.uuid), "Service": Variant("o", SERVICE_PATH), "Flags": Variant("as", self.flags)}


class InfoCharacteristic(Characteristic):
    def __init__(self, service):
        super().__init__(INFO_UUID, ["read"])
        self.service = service

    def value_at(self, offset: int) -> bytes:
        value = compact_json(self.service.info.ble_public())
        if offset > len(value): raise DBusError("org.bluez.Error.InvalidOffset", "Invalid read offset")
        return value[offset:]

    @method()
    def ReadValue(self, options: 'a{sv}') -> 'ay':
        return self.value_at(options.get("offset", Variant("q", 0)).value)


class TxCharacteristic(Characteristic):
    def __init__(self):
        super().__init__(TX_UUID, ["notify"])
        self.notifying = False
        self.value = b""
        self.send_lock = asyncio.Lock()
        self.notification_epoch = 0

    @dbus_property(access=PropertyAccess.READ)
    def Notifying(self) -> 'b': return self.notifying

    @dbus_property(access=PropertyAccess.READ)
    def Value(self) -> 'ay': return self.value

    @method()
    def StartNotify(self):
        # For an exported local characteristic BlueZ may call StartNotify for
        # each subscriber, but calls StopNotify only after its last subscriber
        # leaves. Do not count these peerless callbacks as matched pairs.
        if not self.notifying:
            self.notifying = True
            self.notification_epoch += 1
            self.emit_properties_changed({"Notifying": True})

    @method()
    def StopNotify(self):
        if self.notifying:
            self.notifying = False
            self.notification_epoch += 1
            self.emit_properties_changed({"Notifying": False})

    async def send(self, data, *, epoch=None):
        epoch = self.notification_epoch if epoch is None else epoch
        async with self.send_lock:
            packet = frame(data)
            for offset in range(0, len(packet), 20):
                # Never continue a partial old response into a new subscription
                # session, including a stop/start while this sender is queued.
                if not self.notifying or epoch != self.notification_epoch: return False
                self.value = packet[offset:offset+20]
                # Value notifications fan out to all BlueZ subscribers. The
                # encrypted response ID lets each app select its own reply;
                # the lock covers every chunk of the complete framed packet.
                self.emit_properties_changed({"Value": self.value})
                await asyncio.sleep(0.015)
            return True


class RxCharacteristic(Characteristic):
    def __init__(self, service, tx):
        super().__init__(RX_UUID, ["write"])
        self.service, self.tx = service, tx
        self.buffers = {}
        self.queues = {}
        self.processing = set()
        self.inflight = set()
        self.tasks = set()

    @method()
    def WriteValue(self, value: 'ay', options: 'a{sv}'):
        device = options.get("device")
        peer = getattr(device, "value", None)
        if not isinstance(peer, str) or DEVICE_PATH.fullmatch(peer) is None:
            # BlueZ supplies the actual peer object path. An anonymous fallback
            # would combine fragments from unrelated phones into one buffer.
            raise DBusError("org.bluez.Error.NotAuthorized", "A Bluetooth peer identity is required")
        if options.get("offset", Variant("q", 0)).value != 0:
            raise DBusError("org.bluez.Error.InvalidOffset", "Use sequential framed writes")
        now = time.monotonic()
        self.buffers = {key: pair for key, pair in self.buffers.items() if now-pair[0] < FRAME_TTL_SECONDS}
        peers = self.buffers.keys() | self.queues.keys() | self.processing
        if peer not in peers and len(peers) >= MAX_ACTIVE_PEERS:
            raise DBusError("org.bluez.Error.InProgress", "Bluetooth peer capacity reached; retry shortly")
        pending = len(self.queues.get(peer, ())) + (peer in self.inflight)
        if peer not in self.buffers and pending >= MAX_PENDING_PER_PEER:
            raise DBusError("org.bluez.Error.InProgress", "Bluetooth request queue is full; retry shortly")
        _, buffer = self.buffers.setdefault(peer, (now, FrameBuffer()))
        if not value or len(value) + len(buffer.data) > MAX_FRAME + 4:
            self.buffers.pop(peer, None)
            raise DBusError("org.bluez.Error.InvalidValueLength", "Invalid framed request")
        try:
            packet = buffer.append(bytes(value))
        except ProtocolError:
            self.buffers.pop(peer, None)
            raise DBusError("org.bluez.Error.InvalidValueLength", "Invalid framed request") from None
        if packet is not None:
            self.buffers.pop(peer, None)
            self.queues.setdefault(peer, deque()).append((packet, self.tx.notification_epoch))
            # ATT write is acknowledged promptly; long Wi-Fi activation reports via TX.
            if peer not in self.processing:
                self.processing.add(peer)
                task = asyncio.create_task(self.process(peer))
                self.tasks.add(task)
                task.add_done_callback(self.tasks.discard)

    async def process(self, peer):
        try:
            queue = self.queues[peer]
            while queue:
                packet, epoch = queue.popleft()
                self.inflight.add(peer)
                try:
                    response = await self.service.handle(packet, "ble")
                    await self.tx.send(response, epoch=epoch)
                except ProtocolError:
                    # Unauthenticated data gets no reply and cannot disrupt
                    # another phone or poison this peer's following request.
                    pass
                except Exception as exc:
                    # Do not include a request, credential, or exception text.
                    print(f"Bluetooth request failed ({type(exc).__name__})", flush=True)
                finally:
                    self.inflight.discard(peer)
        finally:
            self.processing.discard(peer)
            self.queues.pop(peer, None)

    async def close(self):
        tasks = tuple(self.tasks)
        for task in tasks: task.cancel()
        if tasks: await asyncio.gather(*tasks, return_exceptions=True)
        self.tasks.clear()
        self.buffers.clear()
        self.queues.clear()
        self.processing.clear()
        self.inflight.clear()


class Application(ServiceInterface):
    def __init__(self, objects):
        super().__init__("org.freedesktop.DBus.ObjectManager")
        self.objects = objects

    @method()
    def GetManagedObjects(self) -> 'a{oa{sa{sv}}}':
        return {path: {obj.name: obj.properties()} for path, obj in self.objects.items()}


class Advertisement(ServiceInterface):
    def __init__(self, device_id):
        super().__init__("org.bluez.LEAdvertisement1")
        self.device_id = device_id

    @dbus_property(access=PropertyAccess.READ)
    def Type(self) -> 's': return "peripheral"

    @dbus_property(access=PropertyAccess.READ)
    def ServiceUUIDs(self) -> 'as': return [SERVICE_UUID]

    @dbus_property(access=PropertyAccess.READ)
    def LocalName(self) -> 's': return "SPARC-" + self.device_id[:4].upper()

    @method()
    def Release(self): pass


async def run_ble(service, adapter="/org/bluez/hci0"):
    bus = await MessageBus(bus_type=BusType.SYSTEM).connect()
    tx = TxCharacteristic()
    rx = RxCharacteristic(service, tx)
    objects = {SERVICE_PATH: GattService(), SERVICE_PATH + "/char0": InfoCharacteristic(service),
               SERVICE_PATH + "/char1": rx, SERVICE_PATH + "/char2": tx}
    app = Application(objects)
    bus.export(ROOT, app)
    for path, obj in objects.items(): bus.export(path, obj)
    advertisement_path = ROOT + "/advertisement0"
    bus.export(advertisement_path, Advertisement(service.info.device_id))
    registered = False
    advertising = False
    try:
        proxy = bus.get_proxy_object("org.bluez", adapter, await bus.introspect("org.bluez", adapter))
        await proxy.get_interface("org.freedesktop.DBus.Properties").call_set("org.bluez.Adapter1", "Powered", Variant("b", True))
        manager = proxy.get_interface("org.bluez.GattManager1")
        advertiser = proxy.get_interface("org.bluez.LEAdvertisingManager1")
        await manager.call_register_application(ROOT, {})
        registered = True
        # Advertising must outlive the first-pairing window so saved phones can
        # reconnect for normal authenticated control. dispatch('pair') continues
        # to enforce setup_open; discovery grants no new access by itself.
        await advertiser.call_register_advertisement(advertisement_path, {})
        advertising = service.ble_advertising = True
        await asyncio.Future()
    finally:
        service.ble_advertising = False
        await rx.close()
        if advertising:
            try: await advertiser.call_unregister_advertisement(advertisement_path)
            except Exception: pass
        if registered:
            try: await manager.call_unregister_application(ROOT)
            except Exception: pass
        bus.disconnect()
