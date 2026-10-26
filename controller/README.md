# SPARC Raspberry Pi 4 companion

This optional service lets the native iPhone app discover a **Raspberry Pi 4** over Bluetooth, verify a device-specific setup key, securely provision a personal Wi-Fi network, and reconnect over the local network. It stores the app's weekly routine and temporary departure on the Pi. It does **not** implement Tesla vehicle authorization, telemetry, or charging commands yet. Those operations return an explicit unavailable response instead of invented vehicle data.

The existing SPARC research web server and planner continue to run independently. Importing this package does not configure the Pi or change its Wi-Fi.

## Install on your Pi

Use Raspberry Pi OS with BlueZ, NetworkManager, and Avahi running. Current Raspberry Pi OS uses NetworkManager. A keyboard/monitor or existing SSH connection is required for this first installation and for retrieving the private setup key. Use a 2.4 GHz network if your home's 5 GHz network is not reachable at the controller.

From the repository's `controller` directory:

```sh
python3 -m venv ../.venv-controller
../.venv-controller/bin/python -m pip install -r requirements.txt
sudo ../.venv-controller/bin/python -m sparc_controller --init
sudo ../.venv-controller/bin/python -m sparc_controller --show-key
sudo ../.venv-controller/bin/python -m sparc_controller --setup-window 600
```

`--init` creates a random 256-bit setup key once in `/var/lib/sparc-controller/identity.json` with mode 0600. It refuses to replace an existing identity. `--show-key` is an explicit local command for the owner; the normal service never prints the key or Wi-Fi password. Keep the displayed key private. Each Pi needs its own generated identity; the public fixtures in this repository are only for tests.

In the iPhone app, open Settings → Home controller → Scan nearby. Choose your SPARC controller, paste its 64-character setup key, then enter your Wi-Fi name and password. Wi-Fi credentials travel in an authenticated encrypted message and go directly into NetworkManager over D-Bus; there is no shell command or process argument containing the password. The app saves the pairing to this iPhone's Keychain only after it authenticates the Pi over the home network. Cancelling setup preserves any previous saved pairing.

Both devices must be on the same home network. The app can retry using the Pi's `.local` hostname or private IPv4 address when mDNS is blocked. The default LAN port is 8766. Guest-network isolation can block phone-to-Pi traffic. This service supports WPA2/WPA3 personal networks; open networks and enterprise authentication need a separate provisioning adapter.

Bluetooth advertising and Wi-Fi configuration are permitted for ten minutes after service startup. Restart the service locally to open another setup window. The high-entropy setup key is required throughout; the Bluetooth device name alone never establishes trust. BLE carries setup traffic; the installed app's normal connection uses Wi-Fi.

## Start automatically

The included `sparc-controller.service` assumes the repository is at `/opt/sparc` and its virtual environment is `/opt/sparc/.venv-controller`. Update those two paths if you installed elsewhere. Initialize the identity first, then:

```sh
sudo install -m 0644 sparc-controller.service /etc/systemd/system/sparc-controller.service
sudo systemctl daemon-reload
sudo systemctl enable --now sparc-controller
sudo systemctl status sparc-controller
```

The service uses privileged D-Bus access to BlueZ and NetworkManager, restricts writable files to its state directory, and never invokes a shell. It accepts only private, loopback, and link-local client addresses. Keep this port on the home LAN; it is not a cloud service. Forgetting a controller removes the iPhone's local credential. To revoke a lost phone's key on the Pi, stop the service, remove its identity file locally, generate a new identity with `--init`, and pair your devices again. This intentionally changes the Pi identity; it does not erase the research database.

## Run automated checks without hardware

From the repository root:

```sh
python3 -m pip install -r controller/requirements.txt
python3 -m unittest discover -s controller/tests -v
```

Tests cover shared Swift/Python cipher vectors, tampering, wrong keys, boot binding, replay rejection, message-size bounds, fragmented ATT frames, settings validation and durable writes, setup-window enforcement, private-address filtering, and a real loopback HTTP exchange. D-Bus interface introspection can run on a development host; Bluetooth radio and NetworkManager activation require the Pi.

For a local protocol-only process, initialize a disposable state directory, then run with `--no-ble --no-bonjour --host 127.0.0.1 --state-dir /path/to/disposable-state`. These flags are for development; they do not simulate a successful radio connection.

## Hardware acceptance checks

Before distributing a signed build, test the app and this service together on a physical iPhone and Pi 4: allow/deny Bluetooth and Local Network permissions; scan with Bluetooth off; verify the correct and incorrect setup key; provision valid and incorrect Wi-Fi passwords; power-cycle and reconnect; cancel during pairing and Wi-Fi setup; test a closed setup window; verify the prior network is restored on an activation failure; and verify forgetting/re-pairing. Confirm the headlights remain off for unavailable live data and illuminate only with confirmed charging telemetry after the vehicle adapter exists.

No radio, router, Tesla, or App Store/TestFlight integration is claimed by the host tests. The protocol uses standard HKDF-SHA256 and ChaCha20-Poly1305; this first implementation still requires hardware verification and an independent security review before broad distribution. See [the wire protocol](../docs/ios/CONTROLLER_PROTOCOL.md) and [iOS setup](../ios/README.md).
