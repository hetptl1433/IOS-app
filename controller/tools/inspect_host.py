#!/usr/bin/env python3
"""Read-only inventory. Reports Netplan renderers without outputting credentials."""
from __future__ import annotations

import json
import os
import platform
import re
import subprocess
from pathlib import Path


def command(*args: str):
    try:
        result = subprocess.run(args, capture_output=True, text=True, timeout=8, check=False)
        return {"status": result.returncode, "output": result.stdout.strip()}
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return {"status": None, "output": "unavailable"}


def fields(path: Path, names: tuple[str, ...]) -> dict[str, str]:
    try:
        lines = path.read_text().splitlines()
    except (OSError, UnicodeError):
        return {}
    allowed = {}
    for line in lines:
        key, separator, value = line.partition("=")
        if separator and key in names:
            allowed[key] = value.strip('"\'')
    return allowed


def json_command(*args: str):
    result = command(*args)
    try:
        return json.loads(result["output"]) if result["status"] == 0 else result
    except json.JSONDecodeError:
        return {"status": result["status"], "output": "unreadable JSON"}


def inventory() -> dict:
    interfaces = []
    for path in sorted(Path("/sys/class/net").glob("*")):
        interfaces.append({"name": path.name, "wireless": (path / "wireless").exists()})
    netplan = []
    # Report only filenames and renderer declarations, never SSIDs/passwords.
    for directory in ("/lib/netplan", "/etc/netplan", "/run/netplan"):
        for path in sorted(Path(directory).glob("*.yaml")):
            entry = {"file": str(path)}
            try:
                entry["renderers"] = re.findall(r"(?m)^\s*renderer:\s*(networkd|NetworkManager)\s*(?:#.*)?$", path.read_text())
            except (OSError, UnicodeError):
                entry["renderers"] = "unreadable; run locally with sudo if needed"
            netplan.append(entry)
    ssh = os.environ.get("SSH_CONNECTION", "").split()
    ssh_route = None
    if len(ssh) == 4:
        # Only a parsed IP may become an iproute argument, never arbitrary environment text.
        import ipaddress
        try:
            peer = str(ipaddress.ip_address(ssh[0]))
            ssh_route = json_command("ip", "-j", "route", "get", peer)
        except ValueError:
            ssh_route = "unavailable"
    model = Path("/proc/device-tree/model")
    try:
        board = model.read_text().rstrip("\0\n")
    except (OSError, UnicodeError):
        board = "unavailable"
    return {
        "readOnly": True,
        "boardModel": board,
        "kernelArchitecture": platform.machine(),
        "userspaceArchitecture": command("dpkg", "--print-architecture"),
        "python": platform.python_version(),
        "clock": command("timedatectl", "show", "-p", "NTPSynchronized", "-p", "TimeUSec", "-p", "Timezone"),
        "os": fields(Path("/etc/os-release"), ("ID", "VERSION_ID", "PRETTY_NAME")),
        "armbian": fields(Path("/etc/armbian-release"), ("BOARD", "BOARD_NAME", "VERSION", "IMAGE_TYPE")),
        "interfaces": interfaces,
        "addresses": json_command("ip", "-j", "address", "show"),
        "defaultRoutes": json_command("ip", "-j", "route", "show", "default"),
        "sshPeerRoute": ssh_route,
        "services": {name: command("systemctl", "is-active", name) for name in (
            "systemd-networkd.service", "NetworkManager.service", "avahi-daemon.service", "bluetooth.service")},
        "netplan": netplan,
        "bluetoothAdapters": [p.name for p in sorted(Path("/sys/class/bluetooth").glob("hci*"))],
        "bluezPaths": command("busctl", "--system", "--list", "tree", "org.bluez"),
        "packages": command("dpkg-query", "-W", "-f=${Package} ${Version} ${Architecture}\n",
                            "python3", "python3-cryptography", "python3-dbus-next", "netplan.io"),
    }


if __name__ == "__main__":
    print(json.dumps(inventory(), indent=2))
