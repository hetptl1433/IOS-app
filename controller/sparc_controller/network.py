"""NetworkManager D-Bus provisioning. Credentials never enter argv, shell or logs."""
from __future__ import annotations

import asyncio
import ipaddress
import socket
import uuid


class WiFiError(RuntimeError):
    pass


def validate_wifi(ssid: object, password: object) -> tuple[str, str]:
    if not isinstance(ssid, str) or not 1 <= len(ssid.encode()) <= 32 or "\0" in ssid:
        raise WiFiError("Enter a Wi-Fi name containing 1–32 bytes.")
    if not isinstance(password, str):
        raise WiFiError("A personal Wi-Fi password is required.")
    valid_hex = len(password) == 64 and all(c in "0123456789abcdefABCDEF" for c in password)
    if "\0" in password or not (8 <= len(password.encode()) <= 63 or valid_hex):
        raise WiFiError("Use a WPA2/WPA3 personal password of 8–63 bytes or 64 hex digits.")
    return ssid, password


def local_hostname() -> str:
    # Avahi supplies the machine's .local host record; use an IP address in-app if mDNS is blocked.
    name = socket.gethostname().split(".")[0].lower()
    if not name or any(c not in "abcdefghijklmnopqrstuvwxyz0123456789-" for c in name):
        return "sparc.local"
    return name + ".local"


class NetworkManagerWiFi:
    def __init__(self, interface: str = "wlan0"):
        self.interface = interface

    async def connect(self, ssid: str, password: str) -> dict:
        ssid, password = validate_wifi(ssid, password)
        from dbus_next import BusType, Variant
        from dbus_next.aio import MessageBus

        bus = await MessageBus(bus_type=BusType.SYSTEM).connect()
        profile = None
        previous_profile = "/"
        device = "/"
        try:
            root = bus.get_proxy_object("org.freedesktop.NetworkManager", "/org/freedesktop/NetworkManager",
                await bus.introspect("org.freedesktop.NetworkManager", "/org/freedesktop/NetworkManager"))
            manager = root.get_interface("org.freedesktop.NetworkManager")
            device = await manager.call_get_device_by_ip_iface(self.interface)
            device_object = bus.get_proxy_object("org.freedesktop.NetworkManager", device,
                await bus.introspect("org.freedesktop.NetworkManager", device))
            properties = device_object.get_interface("org.freedesktop.DBus.Properties")
            previous_active = (await properties.call_get("org.freedesktop.NetworkManager.Device", "ActiveConnection")).value
            if previous_active != "/":
                old = bus.get_proxy_object("org.freedesktop.NetworkManager", previous_active,
                    await bus.introspect("org.freedesktop.NetworkManager", previous_active))
                previous_profile = (await old.get_interface("org.freedesktop.DBus.Properties").call_get(
                    "org.freedesktop.NetworkManager.Connection.Active", "Connection")).value
            settings = {
                "connection": {"type": Variant("s", "802-11-wireless"), "uuid": Variant("s", str(uuid.uuid4())),
                               "id": Variant("s", "SPARC home"), "autoconnect": Variant("b", True)},
                "802-11-wireless": {"ssid": Variant("ay", ssid.encode()), "mode": Variant("s", "infrastructure"),
                                     "security": Variant("s", "802-11-wireless-security")},
                "802-11-wireless-security": {"key-mgmt": Variant("s", "wpa-psk"), "psk": Variant("s", password)},
                "ipv4": {"method": Variant("s", "auto")}, "ipv6": {"method": Variant("s", "auto")},
            }
            profile, active = await manager.call_add_and_activate_connection(settings, device, "/")
            for _ in range(55):
                state = (await properties.call_get("org.freedesktop.NetworkManager.Device", "State")).value
                current = (await properties.call_get("org.freedesktop.NetworkManager.Device", "ActiveConnection")).value
                if state == 100 and current == active:
                    return {"host": local_hostname(), "connected": True}
                if state in (20, 120):
                    raise WiFiError("The Pi could not join that network. Check the Wi-Fi name and password.")
                await asyncio.sleep(1)
            raise WiFiError("Wi-Fi connection timed out. Check the network and retry.")
        except (Exception, asyncio.CancelledError) as failure:
            # Delete only the profile created by this attempt; preserve the previous known network.
            try:
                async with asyncio.timeout(5):
                    if profile:
                        try:
                            proxy = bus.get_proxy_object("org.freedesktop.NetworkManager", profile,
                                await bus.introspect("org.freedesktop.NetworkManager", profile))
                            await proxy.get_interface("org.freedesktop.NetworkManager.Settings.Connection").call_delete()
                        except Exception:
                            pass
                    if previous_profile != "/" and device != "/":
                        try:
                            await manager.call_activate_connection(previous_profile, device, "/")
                        except Exception:
                            pass
            except TimeoutError:
                pass
            # NetworkManager errors can include SSIDs. Return only this safe, actionable message.
            if isinstance(failure, asyncio.CancelledError):
                raise
            raise WiFiError("The Pi could not join that network. Check its signal, Wi-Fi name and password, then retry.") from None
        finally:
            bus.disconnect()
