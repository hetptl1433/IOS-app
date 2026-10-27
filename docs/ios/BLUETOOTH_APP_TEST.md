# Bluetooth and virtual car on a physical iPhone

Open `ios/SPARC.xcodeproj` from the `sparc APP` checkout. Select the **SPARC**
scheme and your connected iPhone, choose your existing development team in
Signing & Capabilities if needed, and press Run. Keep `com.hetpatel.sparc` to
update the installed app. This workflow does not need an iPhone Simulator.

## Try the app first

Settings contains **Demo mode**, then **Virtual car** immediately below it.
Virtual car is enabled by default and starts plugged in at **40%**. Demo mode
runs the virtual car on the phone. Fresh demo launches start waiting, while
keeping saved weekly preferences.

- **Charge now** starts charging and turns on the vehicle's animated light.
- **Pause charging** stops the light and sets power to zero. **Resume charging**
  continues the charge; **End session** returns to waiting.
- Battery and energy advance with elapsed charging time, rather than jumping to
  the target. The model uses 100 kWh capacity and 11.5 kW charging at 90% efficiency.
- Weekly and one-time schedules select the target and departure. Fixed start uses
  the chosen time; Smart uses clearly labeled example lower-cost hours. Times
  are America/Chicago, with a 30-minute ready buffer.
- Turning Virtual car off and back on resets it to plugged in at 40%.

Costs use example prices: 7¢/kWh from 1–5 a.m. and 21¢ otherwise. They are not
utility rates. Demo history remains labeled sample history. No Tesla account,
vehicle API, relay, or charging hardware is connected by this feature.

## Pair the Pi over Bluetooth

The Pi must run `sparc-controller-app-test.service`. It retains its existing
controller identity, Wi-Fi connection and saved schedule. Bluetooth supports
charging controls and schedule edits directly; the phone and Pi do not have to
join the same Wi-Fi network. Keep the devices within Bluetooth range.

Settings → **Status** shows the Pi's connection, Bluetooth, Wi-Fi, uptime,
temperature and charging state. If the Pi isn't on Wi-Fi (or restarted less than
ten minutes ago), the same screen finds nearby networks and sends the chosen
network and password to the Pi over Bluetooth. A failed join returns the Pi to its
previous network.

1. On the phone, open Settings → Connect home controller → Search for controllers.
   Allow Bluetooth when iOS requests it. Tap the nearby **SPARC-D0D4**. The app
   connects with the Pi's pairing code **0000** automatically; nothing is typed.
2. Tap **Use home controller**. This turns Demo mode off.
3. Leave **Virtual car on**. Home shows
   **PI CONNECTED · VIRTUAL CAR**. The battery starts at 40% on a fresh virtual
   model. After use, the Pi retains its virtual progress; off→on resets it to 40%.
4. Try Charge now, Pause, Resume and End on the Charge tab. Save a weekly schedule
   and a one-time departure, reconnect, and confirm the settings remain saved.

The Pi owns the virtual state and schedule in controller mode, so its schedule
continues while the app is closed. Phone-only Demo mode catches up elapsed time when refreshed or reopened from the
background; a fresh app launch starts a new waiting car at 40%. The Virtual car preference for Demo mode is independent of the Pi's
preference. A Pi toggle changes only after the controller acknowledges it.

Pairing and commands still use authenticated encryption, but the key now comes
from the shared code in `/etc/sparc-controller/ble.conf`
(`SPARC_PAIRING_CODE=0000`), so **anyone nearby who knows the code can pair and
send controls** at any time. This is an app-test convenience for the virtual car;
`identity.json` and its private key are unchanged. Before real charging hardware
is connected, delete that line and restart `sparc-controller-app-test.service`:
the Pi returns to the private key, `sudo sparc-pair` and its ten-minute pairing
window, and the phone must be paired again with the key. If a controller doesn't
offer a code, or the code fails, the app shows a field for a code or the 64-character key.

If the Pi cannot be found, verify it is powered on and Bluetooth is enabled on
the phone. If Bluetooth permission was denied, use Settings → iPhone permissions.
This build has no internet control tunnel for the app: Raspberry Pi Connect is
for the owner's terminal, and the phone uses nearby Bluetooth or a private LAN.

## Verification boundary

An unsigned physical-iPhone build compiles the app and test targets without
installing or launching them. Python controller tests verify the virtual model,
encrypted API and schedule behavior. Actual phone radio pairing, animation and
button acceptance must be checked after the owner runs this build in Xcode.

### Pi deployment, October 27, 2026

Installed on `het-pi` (Raspberry Pi OS Lite 64-bit, Debian 13) with the owner
entering sudo privately. Bundle SHA-256 matched after transfer; on the Pi the
installer passed the runtime check and 52/52 controller tests, backed up the
previous source to `/var/backups/sparc-controller/`, kept `identity.json`
unchanged, disabled `sparc-controller.service` and enabled
`sparc-controller-app-test.service`. Its local check confirmed encrypted
charge_now → pause → resume → end (11.5 kW only while charging) and the
off → on reset to 40%. After a cold reboot the service started with no restarts,
Bluetooth was unblocked and powered, and one advertisement was active.

App follow-up: Bluetooth exchanges are now queued, so a tapped control waits for
an in-flight 15-second poll instead of failing as busy. Write chunks are sized to
one ATT packet. The app and test targets compiled again for a generic iOS device
(unsigned). XCTest has not run on the phone, and no phone has paired yet.

Owner request, same day: pair with code 0000 instead of the private key.
`apply-code.sh` (kept in the Codex handoff workspace under `pi-setup/pairing-code/`)
ran on the Pi with owner-entered sudo:
54/54 controller tests passed, the source was backed up again, the line above was
added, and a local check paired and authenticated with 0000 (virtual car 40%,
waiting). Advertising stayed active and identity was unchanged. The app
auto-pairs when the Pi offers `pairing-code`; it compiled again for a generic iOS
device.

Physical acceptance, same day, as reported by the owner on the iPhone 15 run from
Xcode: Search → SPARC-D0D4 connected with code 0000 and showed
**PI CONNECTED · VIRTUAL CAR**. Charge now (light, 11.5 kW), Pause (light off,
0 kW), Resume, End, battery advancement, stopping at a 50% one-time target,
weekly and one-time schedules surviving an app relaunch, reconnecting after a Pi
reboot, and the Virtual car off/on reset all passed. XCTest has still not been run
on the phone.
