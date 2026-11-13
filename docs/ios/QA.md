# iOS quality checks

SPARC's native app targets iPhone on iOS 17 or later. The checked-in Xcode project
has no third-party Swift packages and opens directly in Xcode. The approved Site
is the visual reference: pitch black, neutral glossy cards, dark green data,
compact Home schedule access, and the realistic Cybertruck cutout with a separate
charging-only white light bar.

## Bluetooth virtual-car acceptance (October 27 update)

The [physical-iPhone guide](BLUETOOTH_APP_TEST.md) covers this update. Use the
physical iPhone in Xcode; the owner will install and run the app. Confirm:

- Virtual car is immediately below Demo mode, default on, plugged in at 40%.
- Charge now animates the light and reports 11.5 kW; Pause turns the light off and
  reports zero; Resume continues; End returns to waiting.
- Battery and energy advance with elapsed time and stop at the target.
- Turning Virtual car off/on resets 40%; the preference survives relaunch.
- Bluetooth pairing succeeds without a LAN ping, then commands and schedule
  edits use the saved Bluetooth pairing. Pi settings survive a service restart.
- Virtual values and example prices are labeled, and vehicle control remains
  disconnected. The local Demo preference is independent of the Pi preference.

Unsigned device compilation is recorded separately from actual phone acceptance;
a successful compile does not establish Bluetooth radio behavior.

## Run the checks

On any Python 3 host:

```sh
python3 scripts/ios/generate_project.py --check
python3 scripts/ios/validate_project.py
```

On a Mac with Xcode and an installed iOS Simulator runtime:

```sh
bash scripts/ios/test.sh
SPARC_SIMULATOR_KIND=small SPARC_TEST_SCOPE=ui bash scripts/ios/test.sh
```

Or open `ios/SPARC.xcodeproj`, choose the shared **SPARC** scheme and an iPhone
simulator, then press **Command-U**. The script chooses an available regular
iPhone, or creates an iPhone SE (3rd generation) for the small-device run. Set
`SPARC_SIMULATOR_ID` to use a particular installed simulator.

## Automated coverage

| Area | Checks |
|---|---|
| Project | Every Swift file is referenced, all object references resolve, app/test targets and shared scheme exist, assets load, app icon is opaque 1024 × 1024, purpose strings and privacy manifest exist, ATS is limited to local networking. |
| Schedule | Chicago time zone, weekday/weekend/year transitions, spring missing hour, first fall repeated hour, exact-departure expiry, 30-minute ready buffer, disabled routine, invalid persisted values, fixed starts on the preceding evening, early temporary departures, and honest estimates when the target cannot fit before the ready buffer. |
| App state | Fresh demos always wait while keeping the saved routine; today-only edits expire without changing weekly settings; invalid persisted overrides are discarded; failed live commands and saves do not fabricate success; an acknowledged command with an unconfirmed result clears live telemetry and can recover; disabled demo schedules hide their deadline/window; unchanged demo polling emits no interface updates; disconnected mode starts without demo metrics; controller schedules load even when vehicle telemetry is unavailable. |
| Wire protocol | A shared Python/Swift known-answer fixture; authenticated first-time LAN pair + ping before saving; forged discovery, tampered tags, wrong key, boot/request/device identity, failed replacement/cancellation, redirects and response limits; BLE frames split across arbitrary MTUs, maximum and oversized payloads, zero/trailing data, parser recovery, strict local addresses, invalid setup keys, forged/wrong-direction envelopes, credentials absent from serialized requests. |
| UI | All five tabs, six Home states, constant **Charge now** label, light-bar state, schedule sheet, today/weekly isolation, canceled drafts, saved weekly edits, start/pause/resume, controller setup entry, accessibility text, screenshots. |

[GitHub Actions for this branch](https://github.com/hetptl1433/IOS-app/actions/workflows/ios.yml?query=branch%3Afeat%2Fnative-ios-controller)
is configured to run the unit and UI suite on a regular iPhone simulator, and the
UI suite on iPhone SE. It saves the Xcode log, `.xcresult`, exported screenshot
attachments and final simulator screenshot in **sparc-ios-regular-test-results**
and **sparc-ios-small-test-results**. Screenshots capture every tab, all Home
charging states, schedule edits, controller entry and accessibility text.

The Python workflow also installs the companion's dependencies and runs its
protocol/service tests on Python 3.11, 3.12 and 3.13, alongside the existing
research tests and native project validation.

Use deterministic launch arguments only when testing:

```text
--uitesting --state waiting
--uitesting --state charging
--uitesting --state paused
--uitesting --state complete
--uitesting --state unplugged
--uitesting --state offline
```

`--uitesting` isolates the launch from persisted demo settings. Normal app
launches retain weekly preferences and valid one-time goals. A fresh normal demo
opens waiting; live mode displays only confirmed controller responses.

## Physical acceptance run

Simulator tests cannot verify radio behavior. Before a hardware release, run this
matrix on an actual iPhone and controller with the companion service. Banana Pi
Pro / Armbian checks remain pending until the board is available; package metadata
and host tests do not establish that board's running services, architecture or
radio support.

| Scenario | Expected result |
|---|---|
| Read-only board inspection | Record actual CPU/userspace architecture, Python, interfaces, route to the SSH client, and active network services before choosing configuration. No connection is restarted or migrated. |
| First LAN pairing, Bluetooth absent or denied | Private IP/`.local` plus correct key and an open setup window authenticate the controller and save Keychain credentials; no BLE is required. |
| Forged public information, wrong key, tampered response or mismatched device ID | No trusted or saved pairing; previous credentials remain unchanged. |
| Setup window closed | First-time `pair` fails; an existing saved pairing can still ping and save schedules. |
| LAN-only restart without BlueZ/NetworkManager/Avahi | HTTP serves private-IP connections; persisted schedules and identity survive; optional Bonjour absence does not stop HTTP. |
| Weekly and one-time schedules | Save, restart service, reconnect/relaunch app and read the same values; clearing today leaves weekly intact. Vehicle adapter absence does not prevent storage. |
| First scan, Bluetooth allowed | Nearby advertised SPARC controllers appear; other BLE devices are filtered out. |
| Bluetooth off or permission denied | A clear recovery message and iPhone Settings entry; local-network setup remains available. |
| Wrong setup key | Authenticated exchange fails; credentials and paired-controller storage remain unchanged. |
| Correct key, setup window open | Controller identity is verified before Wi-Fi details are sent. |
| Wrong Wi-Fi password or unavailable SSID | A recoverable failure; no false connected state. |
| Bluetooth pair and ping | Both replies authenticate over Bluetooth before Keychain save; Wi-Fi/LAN is optional. |
| Cancel, disconnect or timeout during setup | Pending operations finish once, the app can scan/retry, and a prior saved controller remains available. |
| Different Wi-Fi, denied local-network access, Pi offline | A recovery message; Home has no fabricated live telemetry. |
| Pi restart or repeated encrypted request | New boot challenge invalidates old messages; duplicate requests are rejected. |
| Forget controller | The key is removed from this iPhone; Pi Wi-Fi and schedules remain on the Pi. |
| Real vehicle adapter absent | App-test mode provides explicitly virtual readings and controls; ordinary production mode still requires a real adapter. |
| Reduce Motion on | Headlight is steady while charging; transitions do not pulse or scale. |

Real Tesla authorization/control and live session history require a Pi vehicle
adapter. This build does not report them as connected. For App Store/TestFlight,
select the signing team in Xcode and verify capabilities, app privacy answers,
real-device permissions, radio tests and an archive with that team's credentials.

## Evidence boundaries

The October 27 adaptation passed **63 native tests (56 unit + 7 UI)** on Xcode 27 /
iOS 26.5, **29 controller tests** on macOS arm64 / Python 3.13.7 with cryptography
43.0.3, and **94 research tests with 1 skipped**. Host runtime and project checks
also passed. Exact logs and result bundle paths are in [current status](STATUS.md).
Three targeted iPhone SE UI tests also passed for LAN setup, the fresh waiting
demo/all tabs, and Charge now/headlight behavior across Home states; the full
small UI suite was not rerun for this adaptation.
Hardware checks remain pending; the Debian ARM installation route is supported
by package metadata and supplied target-side verification commands.

Previous October 26 verification on Xcode 27 / iOS 26.5 passed **55 tests in one clean iPhone
17 Pro run: 48 unit tests and all 7 UI tests**. All seven UI scenarios also have
passing small-iPhone evidence across the full run and a focused regression after
the tab hit-area correction. The unsigned Release build for the iPhone device SDK
passed. The research suite passed 94 tests with one skipped, and the companion
suite passed 21 tests.

The dated [current status](STATUS.md) records the exact result bundles under the
canonical checkout's `artifacts/ios/`, including the distinction between a clean
full run and focused regression evidence. An authored test or configured GitHub
workflow alone does not establish a passing remote run; inspect the linked
workflow's branch results separately.

Simulator checks do not establish successful Bluetooth pairing, router Wi-Fi
activation, vehicle control, signing, or App Store/TestFlight publication. Those
require the physical acceptance run and the owner's signing credentials.
