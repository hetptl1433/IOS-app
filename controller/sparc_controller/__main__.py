"""Run explicitly on the Pi. Importing this package never changes networking."""
import argparse
import asyncio
import json
import signal
from pathlib import Path

from .http import run_http
from .service import ControllerService, initialize


async def advertise_lan(port, device_id):
    """Avahi already publishes the host's .local A/AAAA records; also publish a Bonjour service."""
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


async def run(args):
    service = ControllerService(args.state_dir, args.setup_window)
    tasks = [asyncio.create_task(run_http(service, args.host, args.port))]
    if not args.no_ble:
        from .bluez import run_ble
        tasks.append(asyncio.create_task(run_ble(service, args.adapter)))
    if not args.no_bonjour:
        tasks.append(asyncio.create_task(advertise_lan(args.port, service.info.device_id)))
    # Fail visibly if Bluetooth registration fails instead of running a misleading half-configured service.
    try:
        await asyncio.gather(*tasks)
    finally:
        for task in tasks: task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


def main():
    parser = argparse.ArgumentParser(description="SPARC Raspberry Pi 4 provisioning companion")
    parser.add_argument("--state-dir", type=Path, default=Path("/var/lib/sparc-controller"))
    parser.add_argument("--init", action="store_true", help="Create an identity once; never replaces an existing pairing key")
    parser.add_argument("--show-key", action="store_true", help="Print the setup key locally for the owner's app setup")
    parser.add_argument("--setup-window", type=int, default=600, help="Seconds BLE provisioning is permitted after startup (default 10 minutes)")
    parser.add_argument("--host", default="0.0.0.0", help="LAN listener; accepted clients are restricted to private/link-local/loopback IPs")
    parser.add_argument("--port", type=int, default=8766)
    parser.add_argument("--adapter", default="/org/bluez/hci0")
    parser.add_argument("--no-ble", action="store_true", help="Host-side protocol testing only")
    parser.add_argument("--no-bonjour", action="store_true", help="Host-side protocol testing only")
    args = parser.parse_args()
    if not 1 <= args.port <= 65535 or not 0 <= args.setup_window <= 3600:
        parser.error("Choose a valid port and setup window from 0 to 3600 seconds.")
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
