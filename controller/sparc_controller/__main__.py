"""Run explicitly on the controller. Importing this package never changes networking."""
import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

from .http import run_http
from .network import NetworkManagerWiFi
from .service import ControllerService, initialize


async def advertise_lan(port, device_id):
    """Avahi already publishes the host's .local A/AAAA records; also publish Bonjour."""
    from dbus_next import BusType
    from dbus_next.aio import MessageBus
    bus = await MessageBus(bus_type=BusType.SYSTEM).connect()
    group = None
    try:
        server = bus.get_proxy_object("org.freedesktop.Avahi", "/", await bus.introspect("org.freedesktop.Avahi", "/"))
        path = await server.get_interface("org.freedesktop.Avahi.Server").call_entry_group_new()
        proxy = bus.get_proxy_object("org.freedesktop.Avahi", path, await bus.introspect("org.freedesktop.Avahi", path))
        group = proxy.get_interface("org.freedesktop.Avahi.EntryGroup")
        await group.call_add_service(-1, -1, 0, "SPARC-" + device_id[:8], "_sparc._tcp", "", "", port, [b"id=" + device_id.encode(), b"v=1"])
        await group.call_commit()
        await asyncio.Future()
    finally:
        if group:
            try: await group.call_free()
            except Exception: pass
        bus.disconnect()


async def optional_bonjour(port, device_id):
    try:
        await advertise_lan(port, device_id)
    except asyncio.CancelledError:
        raise
    except Exception:
        # Discovery is optional: an IP address still works without Avahi or D-Bus.
        # Do not print an exception that could contain external D-Bus payloads.
        print("Bonjour unavailable; connect using the controller's private IP address.", file=sys.stderr)


async def optional_display(service, port):
    from .display import run_display
    try:
        await run_display(service, port)
    except asyncio.CancelledError:
        raise
    except Exception:
        # The screen is a convenience; phones keep working over Bluetooth without it.
        print(f"Touchscreen page unavailable on 127.0.0.1:{port}.", file=sys.stderr)


async def watch_wifi(service):
    """Keep the Wi-Fi state current, so an offline Pi accepts a new network from the phone."""
    while True:
        await service.refresh_wifi()
        await asyncio.sleep(30)


async def run(args):
    lan_only = args.lan_only
    allow_wifi = not lan_only and not args.no_wifi_provision
    wifi = NetworkManagerWiFi(args.wifi_interface) if allow_wifi else None
    service = ControllerService(args.state_dir, args.setup_window, wifi=wifi,
                                allow_wifi_provision=allow_wifi, app_test=args.app_test,
                                pairing_code=args.pairing_code)
    if not lan_only:
        # Import before starting HTTP so a missing optional BLE dependency leaves no task behind.
        from .bluez import run_ble
    required = [asyncio.create_task(run_http(service, args.host, args.port))]
    tasks = list(required)
    if not lan_only:
        required.append(asyncio.create_task(run_ble(service, args.adapter)))
        tasks.append(required[-1])
    if not args.no_bonjour:
        tasks.append(asyncio.create_task(optional_bonjour(args.port, service.info.device_id)))
    if args.display_port is not None:
        tasks.append(asyncio.create_task(optional_display(service, args.display_port)))
    if service.wifi is not None:
        tasks.append(asyncio.create_task(watch_wifi(service)))
    try:
        # HTTP must remain alive. In BLE mode an adapter failure must also be visible.
        done, _ = await asyncio.wait(required, return_when=asyncio.FIRST_COMPLETED)
        for task in done:
            task.result()
        raise RuntimeError("A required controller transport stopped.")
    finally:
        for task in tasks: task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="SPARC Linux home controller")
    parser.add_argument("--state-dir", type=Path, default=Path("/var/lib/sparc-controller"))
    identity = parser.add_mutually_exclusive_group()
    identity.add_argument("--init", action="store_true", help="Create an identity once; never replaces an existing pairing key")
    identity.add_argument("--show-key", action="store_true", help="Print the setup key locally for the owner's app setup")
    parser.add_argument("--setup-window", type=int, default=600, help="Seconds first-time LAN/BLE pairing and BLE Wi-Fi setup are permitted after startup (default 10 minutes)")
    parser.add_argument("--host", default="0.0.0.0", help="LAN listener; accepted clients are restricted to private/link-local/loopback IPs")
    parser.add_argument("--port", type=int, default=8766)
    parser.add_argument("--adapter", default="/org/bluez/hci0", help="Inspected BlueZ adapter object path, used only in BLE mode")
    parser.add_argument("--wifi-interface", default="wlan0", help="Inspected NetworkManager Wi-Fi interface, used only in BLE mode")
    parser.add_argument("--lan-only", "--no-ble", dest="lan_only", action="store_true", help="Run on the existing LAN without Bluetooth or Wi-Fi provisioning; --no-ble is an alias")
    parser.add_argument("--no-bonjour", action="store_true", help="Skip optional Avahi/D-Bus Bonjour advertising; connect by private IP")
    parser.add_argument("--no-wifi-provision", action="store_true", help="Keep Bluetooth control enabled without creating a NetworkManager Wi-Fi provisioning adapter")
    parser.add_argument("--app-test", action="store_true", help="Enable the default-on Virtual car for the physical app; never connects to a real vehicle")
    parser.add_argument("--pairing-code", default=os.environ.get("SPARC_PAIRING_CODE") or None,
                        help="App-test only: 4-8 digit code the app pairs with instead of the private key (default: $SPARC_PAIRING_CODE)")
    parser.add_argument("--display-port", default=os.environ.get("SPARC_DISPLAY_PORT") or None,
                        help="Serve the Pi's own touchscreen on 127.0.0.1 at this port (default: $SPARC_DISPLAY_PORT; off when unset)")
    args = parser.parse_args(argv)
    if args.pairing_code is not None and (not args.app_test or not 4 <= len(args.pairing_code) <= 8
                                          or any(c not in "0123456789" for c in args.pairing_code)):
        parser.error("A pairing code needs --app-test and must contain 4 to 8 digits.")
    if args.display_port is not None:
        port = args.display_port
        if not port or any(c not in "0123456789" for c in port) or not 1024 <= int(port) <= 65535 or int(port) == args.port:
            parser.error("Choose a display port from 1024 to 65535 that differs from --port.")
        args.display_port = int(port)
    if not 1 <= args.port <= 65535 or not 0 <= args.setup_window <= 3600:
        parser.error("Choose a valid port and setup window from 0 to 3600 seconds.")
    if not args.adapter.startswith("/org/bluez/") or any(c not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_/" for c in args.adapter):
        parser.error("Choose a valid BlueZ adapter object path.")
    if not args.wifi_interface or len(args.wifi_interface) > 15 or any(c not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_.:-" for c in args.wifi_interface):
        parser.error("Choose a valid Wi-Fi interface name.")
    return args


def main(argv=None):
    args = parse_args(argv)
    if args.init:
        identity = initialize(args.state_dir)
        print("Created controller identity " + identity["deviceId"] + ". Use --show-key locally to view its setup key.")
        return
    if args.show_key:
        identity = json.loads((args.state_dir / "identity.json").read_text())
        print("Controller: " + identity["deviceId"] + "\nSetup key: " + identity["setupKey"])
        return
    try:
        asyncio.run(run(args))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__": main()
