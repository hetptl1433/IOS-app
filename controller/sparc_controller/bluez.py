"""BlueZ GATT peripheral; responses are encrypted and framed into 20-byte notifications."""
import asyncio
import time

from dbus_next import BusType, DBusError, Variant
from dbus_next.aio import MessageBus
from dbus_next.constants import PropertyAccess
from dbus_next.service import ServiceInterface, dbus_property, method

from .protocol import FrameBuffer, ProtocolError, compact_json, frame

SERVICE_UUID = "7c49b5a0-8d22-4fb8-a3a8-6d7703100001"
INFO_UUID = "7c49b5a0-8d22-4fb8-a3a8-6d7703100002"
RX_UUID = "7c49b5a0-8d22-4fb8-a3a8-6d7703100003"
TX_UUID = "7c49b5a0-8d22-4fb8-a3a8-6d7703100004"
ROOT = "/app/sparc"
SERVICE_PATH = ROOT + "/service0"


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

    @method()
    def ReadValue(self, options: 'a{sv}') -> 'ay':
        offset = options.get("offset", Variant("q", 0)).value
        value = compact_json(self.service.info.public())
        if offset > len(value): raise DBusError("org.bluez.Error.InvalidOffset", "Invalid read offset")
        return value[offset:]


class TxCharacteristic(Characteristic):
    def __init__(self):
        super().__init__(TX_UUID, ["notify"])
        self.notifying = False
        self.value = b""
        self.send_lock = asyncio.Lock()

    @dbus_property(access=PropertyAccess.READ)
    def Notifying(self) -> 'b': return self.notifying

    @dbus_property(access=PropertyAccess.READ)
    def Value(self) -> 'ay': return self.value

    @method()
    def StartNotify(self):
        self.notifying = True
        self.emit_properties_changed({"Notifying": True})

    @method()
    def StopNotify(self):
        self.notifying = False
        self.emit_properties_changed({"Notifying": False})

    async def send(self, data):
        async with self.send_lock:
            packet = frame(data)
            for offset in range(0, len(packet), 20):
                if not self.notifying: return
                self.value = packet[offset:offset+20]
                self.emit_properties_changed({"Value": self.value})
                await asyncio.sleep(0.015)


class RxCharacteristic(Characteristic):
    def __init__(self, service, tx):
        super().__init__(RX_UUID, ["write"])
        self.service, self.tx = service, tx
        self.buffers = {}
        self.processing = set()
        self.tasks = set()

    @method()
    def WriteValue(self, value: 'ay', options: 'a{sv}'):
        peer = options.get("device", Variant("o", "/")).value
        if options.get("offset", Variant("q", 0)).value != 0:
            raise DBusError("org.bluez.Error.InvalidOffset", "Use sequential framed writes")
        if peer in self.processing:
            raise DBusError("org.bluez.Error.InProgress", "Request is already running")
        now = time.monotonic()
        self.buffers = {key: pair for key, pair in self.buffers.items() if now - pair[0] < 15}
        if peer not in self.buffers and len(self.buffers) >= 4:
            raise DBusError("org.bluez.Error.InProgress", "Setup capacity reached")
        _, buffer = self.buffers.setdefault(peer, (now, FrameBuffer()))
        try:
            packet = buffer.append(bytes(value))
        except ProtocolError:
            self.buffers.pop(peer, None)
            raise DBusError("org.bluez.Error.InvalidValueLength", "Invalid framed request") from None
        if packet is not None:
            self.buffers.pop(peer, None)
            self.processing.add(peer)
            # ATT write is acknowledged promptly; long Wi-Fi activation reports via TX.
            task = asyncio.create_task(self.process(peer, packet))
            self.tasks.add(task)
            task.add_done_callback(self.tasks.discard)

    async def process(self, peer, packet):
        try:
            response = await self.service.handle(packet, "ble")
            await self.tx.send(response)
        except ProtocolError:
            # A response cannot be trusted without a valid authenticated request. Let the client time out.
            pass
        finally:
            self.processing.discard(peer)


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
        advertising = True
        await asyncio.Future()
    finally:
        for task in rx.tasks: task.cancel()
        if advertising:
            try: await advertiser.call_unregister_advertisement(advertisement_path)
            except Exception: pass
        if registered:
            try: await manager.call_unregister_application(ROOT)
            except Exception: pass
        bus.disconnect()
