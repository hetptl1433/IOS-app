# SPARC Linux controller

## Raspberry Pi 4: Bluetooth app controls, with no vehicle connected

The physical iPhone can use the encrypted Bluetooth channel for pairing,
schedule settings, charging-state updates and charge/pause/resume/end controls.
This does not require the phone and Pi to communicate over Wi-Fi. Keep the Pi's
working Wi-Fi and Raspberry Pi Connect configuration unchanged.

Run the controller with **`--app-test --no-wifi-provision`** for this stage of
the project. `--app-test` is an explicit software control mode: it never contacts
Tesla or any charger. A virtual car is enabled by default, plugged in at 40%.
Its snapshots carry `virtualCar:true` and `controlOnly:false`; all battery,
power, energy and prices are explicitly virtual examples. Without `--app-test`,
snapshots and charging commands continue to fail
until a real vehicle adapter is installed. The normal service never silently
falls back to test data.

In app-test mode:

- **Charge now / Resume** sets the confirmed software state to charging;
  **Pause** sets paused; **End** sets waiting. The iPhone's charging light follows
  this acknowledged state. No electrical charging operation occurs.
- Weekly settings and one-time departures are saved atomically on the Pi.
  A schedule change clears the current manual override in the same transaction.
- A **fixed-time** routine starts its test charging state at the saved start
  time and stops that window 30 minutes before departure. A start
  later than that ready deadline belongs to the previous calendar day. The app
  observes these clock-based states when it requests a snapshot. Completion
  requires the virtual battery to reach its target, not merely the clock deadline.
- A valid one-time departure takes priority, retaining the weekly mode and fixed
  start time. Expiry restores the weekly routine. Schedule times use
  **America/Chicago**, matching the existing app, including DST handling.
- Manual charging or pause lasts until the current ready deadline, or until
  departure if issued after that deadline. End suppresses the current departure's
  automatic window until departure. With no enabled goal, explicit manual state
  remains until another command or schedule change. These states survive a
  controller restart in the existing mode-0600 `settings.json`.
- **Smart** mode computes a virtual window preferring 01:00, starting earlier
  when necessary to reach the target 30 minutes before departure. This is an
  example plan, not a tariff research or vehicle optimizer integration.
- The virtual model uses a 100 kWh battery, 11.5 kW AC power and 90% efficiency.
  Example Chicago prices are 7 cents/kWh from 01:00 to 05:00 and 21 cents
  otherwise. Energy, cost and savings versus the 21-cent baseline accrue only
  during actual virtual charging intervals, including gaps between app polls.
  Charging stops at the target. Monthly savings remain zero.
- `get_settings` includes `virtualCarEnabled`; encrypted `set_virtual_car`
  accepts `{ "enabled": true }` or `false`. An unchanged flag preserves state.
  Turning it off reports unplugged, `virtualCar:false`, `controlOnly:true` and
  zero telemetry, and rejects charge commands. Turning it back on resets to 40%
  with zero session energy. This option does not enable any real vehicle adapter.
- Virtual battery, counters and settings persist atomically in `settings.json`.
  Controller process downtime never adds energy; restarting resumes from the
  saved values with a fresh time anchor. The existing identity remains unchanged.

Pairing still requires the existing private controller setup key and the
ten-minute local setup window. The app must authenticate a Bluetooth pair and
ping before saving. Bluetooth advertising stays available after that window so
an already paired iPhone can reconnect normally; discovery alone grants no
access. Restarting the service reopens the setup window without replacing the
identity or schedules.

Before enabling Bluetooth, inspect the actual host and BlueZ adapter with
`controller/tools/inspect_host.py`. Confirm a working adapter exposes
`org.bluez.GattManager1` and `org.bluez.LEAdvertisingManager1`. Do not infer a
working radio from the board name. Install BlueZ through the OS package manager
if it is missing. No NetworkManager installation, Netplan change or Wi-Fi
renderer migration is needed for `--no-wifi-provision`.
The app-test unit explicitly runs `/usr/sbin/rfkill unblock bluetooth` before
starting its GATT service, so the inspected radio remains available after a boot
that restores a Bluetooth software block. Hard-blocked or missing hardware still
requires local repair; the unit does not bypass a hardware block.

After transferring the updated controller source to `/opt/sparc-controller`, run
the runtime helper and complete controller tests on the Pi, as in the installation
instructions below. Preserve `/var/lib/sparc-controller/identity.json` and its
existing pairing key; **do not run `--init` again** during an upgrade.

The new `sparc-controller-app-test.service` expects a root-owned
`/etc/sparc-controller/ble.conf` containing only the inspected adapter path:

```ini
SPARC_BLUEZ_ADAPTER=/org/bluez/hci0
```

`/org/bluez/hci0` is an example and must match the inspection. Once the file is
created, use the application-specific unit instead of the LAN-only service:

```sh
sudo chmod 0600 /etc/sparc-controller/ble.conf
sudo install -m 0644 controller/sparc-controller-app-test.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl disable --now sparc-controller.service
sudo systemctl enable --now sparc-controller-app-test.service
sudo systemctl status sparc-controller-app-test.service --no-pager
```

If the separate Bluetooth Wi-Fi-provisioning unit was previously installed,
disable it as well; these units must not run together. The app-test unit starts
only Bluetooth and the existing LAN listener, uses the existing state directory,
and has no dependency on NetworkManager. A BLE registration/adapter failure
remains visible and causes the service to restart rather than pretending to be
connected.

The controller's real-radio and physical-iPhone acceptance still requires
on-device checks. Host tests cover encrypted BLE-style framed exchanges,
wrong keys/replay/old-boot rejection, command and schedule persistence,
manual/scheduled state boundaries, DST, and the production guard. They do not
establish Bluetooth radio reachability or app installation/signing on a phone.

## Raspberry Pi touchscreen: plug in a display and it starts

A screen on the Pi's HDMI port (a 7-inch panel or a wide car-style display) shows
charging, the weekly routine and the next trip, with one-tap Charge now, Pause,
Resume and End. It matches the app's look and lays itself out for 800×480,
1024×600, wide 1280×400–1920×720 and portrait panels. With no display connected
nothing runs; plugging one in starts the screen within a few seconds, and
unplugging stops it.

- `sparc_controller/display.py` serves `display/` and a small JSON API on
  `127.0.0.1:8767` only. It is enabled by `SPARC_DISPLAY_PORT=8767` in
  `/etc/sparc-controller/ble.conf`. Because it never leaves the Pi it uses no
  pairing key; it rejects other hosts, cross-site requests and non-JSON bodies,
  and reading state never writes to the SD card.
- `kiosk/` holds the system side: `sparc-display.service` runs Chromium full
  screen inside `cage` on the first console as the `sparc-display` user, and a
  udev rule starts, stops or restarts it when a display is connected or removed.
- Settings for a sideways panel or extra browser flags go in
  `/etc/sparc-controller/display.conf`.

Install from a bundle holding `controller-source.tgz`, its checksum and
`install-display.sh`: `sudo sh install-display.sh`. It runs the controller tests,
installs `cage`, `chromium`, `fonts-inter` and `wlr-randr`, backs up the current
controller, and restores it if any check fails. Identity, Wi-Fi and pairing are
unchanged. If the screen stays dark, `journalctl -b -u sparc-display` shows the
service, and `journalctl -b _UID=$(id -u sparc-display)` shows cage and Chromium,
which run in the kiosk user's own session. Without a display,
`sudo sh controller/kiosk/kiosk-smoke.sh` runs the same service on a virtual
screen and reports what the page shows, then puts everything back.

The screen shows the Pi's demo car (on by default, plugged in at 40%), the same
one the phone sees. To turn it off on the screen, tap the SPARC logo seven times
to open the hidden Developer sheet; turning it back on starts again at 40%.

## Existing Banana Pi Pro / Armbian Minimal deployment

The default controller persists weekly schedules and one-time departures.
First-time pairing works over the existing LAN with the controller's private
setup key; Bluetooth is optional. Vehicle authorization, telemetry, planning
execution and physical charging commands remain unfinished. Saving a schedule
does not command a vehicle. The explicit app-test mode above only exercises
software state through the physical app connection.

These instructions target **Armbian 26.11.0 Minimal, Debian 13** on a Banana Pi
Pro. Hardware inspection is pending: the board's exact interfaces, services,
Bluetooth adapter, package versions and phone connection have not been verified.
Do not infer a working Bluetooth radio from the board name.

## 1. First boot and inspect before configuring networking

If Armbian is already running, keep its working connection. For a fresh boot,
connect Ethernet to your home router, insert the prepared microSD card and power
on. Allow about two minutes for the first boot. Find its DHCP address in your
router. Connect from your Mac (replace `BOARD_IP` with that address):

```sh
ssh root@BOARD_IP
```

An uncustomized Armbian image uses the initial password `1234`; follow the first
login prompts to change it and create a normal sudo user. Imager customization
may have already supplied different credentials. Subsequently use that normal
user. A local HDMI/serial console is an alternative. See Armbian's
[first boot instructions](https://docs.armbian.com/getting-started/first-boot-and-login/).

Inspect the board **before** selecting an interface or network renderer. Run on
the board, from the transferred source directory (transfer commands follow):

```sh
python3 controller/tools/inspect_host.py
```

If Netplan files are unreadable and you need their renderer declarations:

```sh
sudo --preserve-env=SSH_CONNECTION python3 controller/tools/inspect_host.py
```

The helper reports architecture, interface names/addresses, default and SSH-peer
routes, selected service states, Netplan renderer declarations, BlueZ object
paths and dependency package versions. It reads Netplan YAML locally to extract
only renderer declarations; it never outputs configuration contents, keys or
passwords. It never reads the controller identity or service journals, and never
changes networking. Review its output locally; private IPs and host details are
still personal data.
Before the source is transferred, these minimal read-only commands suffice:

```sh
uname -m
dpkg --print-architecture
python3 --version
timedatectl status
ip -brief link
ip -brief address
ip route show default
systemctl is-active systemd-networkd NetworkManager avahi-daemon bluetooth
ls /sys/class/bluetooth
```

Armbian's [networking guide](https://docs.armbian.com/user-guide/networking/)
states that Minimal uses Netplan with `systemd-networkd`; regular images use
NetworkManager. The LAN-only service works with either existing stack. Keep the
current Ethernet/Wi-Fi configuration; **no Netplan change, NetworkManager
installation, interface rename or network service restart is needed**. Reserve
its existing DHCP address in your router if you want a stable numeric address.
The phone may use Wi-Fi while the board stays on Ethernet on the same LAN.

If Wi-Fi is not configured, use the first-login wireless prompt or configure it
from a local console after inspecting the actual wireless interface. Use a
2.4 GHz network for the Banana Pro's onboard Wi-Fi. Do not paste passwords into
shell command arguments, chat, logs or Git. Keep any Netplan credential file
root-owned and mode 0600. Never migrate the renderer or run `netplan apply`
through your only SSH connection. If you later choose to change networking,
keep a local console and recovery copy; [Netplan try](https://netplan.readthedocs.io/en/stable/netplan-try/)
offers a confirmation timeout but its documentation requires verifying rollback.
No configuration for an uninspected interface is supplied here.

Pairing uses the controller's clock challenge, so an incorrect first-boot clock
does not require weakening authentication. One-time departures are absolute
timestamps: confirm `timedatectl status` shows the correct time and the existing
time service has synchronized before saving them. Weekly schedules store local
minutes; the app continues to interpret them in America/Chicago. Correct a bad
clock locally using the image's existing time configuration before testing
one-time schedule persistence.

## 2. Transfer this app checkout, then install ARM-compatible dependencies

On your Mac, copy only the controller source and public test fixtures from the
**app checkout**. These commands include uncommitted source changes. They do not
access or overwrite the separate sibling project named `sparc`:

```sh
COPYFILE_DISABLE=1 tar --exclude='__pycache__' --exclude='*.pyc' \
  -C '/Users/hetpatel/Documents/project/sparc APP' \
  -czf /private/tmp/sparc-controller-source.tgz \
  controller/sparc_controller controller/tools controller/tests controller/fixtures \
  controller/requirements.txt controller/sparc-controller.service \
  controller/sparc-controller-ble.service
scp /private/tmp/sparc-controller-source.tgz YOUR_USER@BOARD_IP:~/
```

On the board, using the sudo user created at first login:

```sh
mkdir -p ~/sparc-controller-install
tar -xzf ~/sparc-controller-source.tgz -C ~/sparc-controller-install
cd ~/sparc-controller-install
python3 controller/tools/inspect_host.py
sudo apt-get update
sudo apt-get install -y python3 python3-venv python3-cryptography python3-dbus-next
sudo install -d -m 0755 /opt/sparc-controller
sudo tar --no-same-owner -xzf ~/sparc-controller-source.tgz -C /opt/sparc-controller
sudo python3 -m venv --system-site-packages /opt/sparc-controller/.venv-controller
/opt/sparc-controller/.venv-controller/bin/python /opt/sparc-controller/controller/tools/check_runtime.py
cd /opt/sparc-controller
.venv-controller/bin/python -m unittest discover -s controller/tests -v
```

A genuine Banana Pro uses a 32-bit ARMv7 Cortex-A7 / Debian `armhf` userspace;
confirm `uname -m` and `dpkg --print-architecture` rather than choosing an ARM64
wheel. [Debian's board description](https://wiki.debian.org/FreedomBox/Hardware/BananaPro)
identifies `armhf`. Debian 13 provides [Python 3.13](https://packages.debian.org/trixie/python3),
[cryptography 43 with an armhf binary](https://packages.debian.org/trixie/armhf/python3-cryptography)
and [dbus-next 0.2.3](https://packages.debian.org/trixie/python3-dbus-next).
The supported dependency floor includes Debian's cryptography 43, with Debian's
security patches. `--system-site-packages` exposes those distribution packages
inside the venv. Do not replace the OS Python or use `sudo pip`.

This route avoids compiling Rust/OpenSSL bindings on a board with 1 GB RAM.
Upstream [cryptography installation guidance](https://cryptography.io/en/46.0.3/installation/)
explains that source builds require a compiler, Rust and development headers;
an appropriate PyPI ARMv7 wheel must not be assumed. No pip installation is
required for this Debian route. The runtime helper actually exercises HKDF,
ChaCha20-Poly1305 and tamper rejection, and imports the D-Bus dependency. Run it
and the complete companion tests **on the board** before enabling the service;
a test on a Mac does not establish ARM compatibility.

## 3. Initialize once and start the LAN-only systemd service

```sh
cd /opt/sparc-controller/controller
sudo ../.venv-controller/bin/python -m sparc_controller --init
sudo install -m 0644 sparc-controller.service /etc/systemd/system/sparc-controller.service
sudo systemctl daemon-reload
sudo systemctl enable --now sparc-controller.service
sudo systemctl status sparc-controller.service --no-pager
curl --fail http://127.0.0.1:8766/v1/info
```

`--init` generates a random 256-bit setup key in
`/var/lib/sparc-controller/identity.json` with mode 0600. It refuses to replace an
existing identity: on an upgrade, skip initialization and keep that file.
Schedules are stored separately in the same state directory. The unit uses
`/opt/sparc-controller`, runs with `--lan-only`, and has no BlueZ or NetworkManager
service dependency. LAN-only never changes the board's Wi-Fi. Bonjour advertising
is optional; numeric IPv4 works when Avahi is absent. To enable `.local` hostnames
if desired, install/start Avahi separately:

```sh
sudo apt-get install -y avahi-daemon
sudo systemctl enable --now avahi-daemon.service
hostname
hostname -I
```

Use the first private IPv4 address on the inspected home-LAN interface, or the
actual hostname plus `.local`. The default port is `8766`. Do not use
`0.0.0.0`, a public address, `http://` or a URL path in the app's host field. Bare
DNS names, public DNS names and manually entered IPv6 are not accepted. Guest
network/client isolation can prevent communication. If an existing firewall
blocks the port, allow TCP 8766 only from the actual home-LAN subnet using that
firewall's existing policy; do not flush it or add an internet port forward.

## 4. Retrieve the private key and connect the iPhone

In your own unrecorded terminal on the board, explicitly display the key:

```sh
cd /opt/sparc-controller/controller
sudo ../.venv-controller/bin/python -m sparc_controller --show-key
```

Copy the 64 hexadecimal characters privately. Do not run this command through a
chat/tool session that records output, save it to a log, or commit it. The normal
service never prints setup keys or Wi-Fi passwords. Public fixtures contain fake
keys and must never be reused as device credentials.

Within ten minutes of service start, on a physical iPhone with the updated app:

1. Join the same home LAN and open **Settings → Home controller**.
2. Under **Pair on your home network**, enter the controller's private IPv4
   address or actual `.local` hostname, and paste its private setup key. If a
   controller is already saved, first choose **Pair another controller**.
3. Choose **Pair over home network** and allow Local Network access when asked.
   Bluetooth permission is unnecessary for this path.
4. Wait for **Your controller is connected**, then choose **Use home controller**.
   The app authenticates encrypted responses before saving the new credentials
   in this iPhone's Keychain using device-only, when-unlocked access. The candidate
   stays in memory until authentication succeeds. Wrong keys, cancellation and
   failed verification preserve the previous saved pairing.
5. Save a weekly routine and one-time departure. Reconnect after restarting the
   controller and confirm both schedules remain. Live telemetry and charging
   controls remain unavailable until the vehicle integration exists.

If the setup window has closed, open a fresh ten-minute window locally:

```sh
sudo systemctl restart sparc-controller.service
```

Existing saved pairings can reconnect and read/write schedules after the setup
window closes. Restarting preserves identity and settings. If mDNS fails, use
the numeric private IPv4 address. In iPhone Settings, re-enable SPARC's Local
Network permission if it was denied. Do not forget an existing controller just
to retry an unsuccessful new pairing.

## 5. Optional Bluetooth setup

Use LAN-only unless the inspection finds a real working BlueZ adapter. BLE
registration alone also requires a physical radio with peripheral/advertising
support. A supported USB adapter may be needed. Check the actual object paths
and NetworkManager ownership of the actual wireless interface. BLE pairing
remains available; **phone-driven Wi-Fi provisioning additionally requires
NetworkManager**, and is not a systemd-networkd/Netplan adapter.

Do not install or switch NetworkManager just to run this service on Armbian
Minimal. If an independently configured NetworkManager setup and working BLE
adapter already exist, create the required non-secret configuration file:

```sh
sudo install -d -m 0755 /etc/sparc-controller
sudoedit /etc/sparc-controller/ble.conf
```

Enter two lines, replacing the example values with the **inspected** adapter
object path and wireless interface name (the file contains no setup key/password):

```ini
SPARC_BLUEZ_ADAPTER=/org/bluez/hci0
SPARC_WIFI_INTERFACE=wlan0
```

Do not use these example names until the inventory confirms them. Then:

```sh
sudo chmod 0600 /etc/sparc-controller/ble.conf
cd /opt/sparc-controller/controller
sudo install -m 0644 sparc-controller-ble.service /etc/systemd/system/sparc-controller-ble.service
sudo systemctl daemon-reload
sudo systemctl disable --now sparc-controller.service
sudo systemctl enable --now sparc-controller-ble.service
sudo systemctl status sparc-controller-ble.service --no-pager
```

Bluetooth and NetworkManager must already be functional for that configuration;
the BLE unit starts those services and fails visibly if registration fails.
During the setup window, use **Search for controllers**, choose the controller,
enter its setup key, then use **Set up Wi-Fi** if needed. Do Wi-Fi provisioning
from a local console rather than the interface carrying active SSH. Its failure
rollback is best effort and requires physical verification. The two units must
not run together; both use the same identity/settings. To return to LAN-only:

```sh
sudo systemctl disable --now sparc-controller-ble.service
sudo systemctl enable --now sparc-controller.service
```

## Verification boundaries and unfinished integration

Host automation covers shared Swift/Python cipher vectors, wrong keys,
tampering, boot binding and replay rejection; LAN pairing/setup-window rules;
message/address restrictions; durable schedule writes; cancellation and saved
credential preservation; and real loopback HTTP exchanges. Run from the app
repository root on a development machine with the dependencies installed:

```sh
python3 -m unittest discover -s controller/tests -v
python3 scripts/ios/generate_project.py --check
python3 scripts/ios/validate_project.py
bash scripts/ios/test.sh
```

Physical acceptance is still required: target-ARM runtime/tests; first pairing
with Bluetooth unavailable; correct/wrong key; Local Network permission denial;
cancellation while pairing; closed setup window; IP/mDNS reachability; service
restart/reboot with schedules retained; existing pairing recovery; optional BLE
radio/permissions; and NetworkManager Wi-Fi failure/rollback only where that
adapter is actually installed. No board, radio, router or signed physical-iPhone
verification is implied by simulator/loopback tests.

For lost-phone revocation, stop the service and deliberately rotate its identity
locally; every phone then needs re-pairing. Forgetting in the app removes only
that iPhone's credential. Back up or retain `settings.json` as needed. Never
rotate an identity as an ordinary install/upgrade step.

Vehicle authorization, telemetry, actual charge commands and planner-to-vehicle
execution are separate unfinished work. The approved app design, permanent
**Charge now** label and fresh demo's **Waiting for a better charging time**
default remain intact. See [wire protocol](../docs/ios/CONTROLLER_PROTOCOL.md)
and [iOS setup](../ios/README.md).
