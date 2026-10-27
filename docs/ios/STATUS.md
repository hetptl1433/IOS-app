# SPARC iOS current status

Updated October 27, 2026. This file is a durable handoff for continuing the app
without relying on the earlier interrupted chat.

## Bluetooth and virtual car update

The physical-iPhone workflow now supports encrypted Bluetooth controls and
schedules without a same-LAN connection. Demo mode has a Virtual car switch
directly below it, default on, plugged in at 40%. The explicit Pi app-test service
uses the same virtual-car model. Battery, energy and example costs progress with
charging time, pause at zero power, and stop at the target. No Tesla integration
was added. See [setup and acceptance](BLUETOOTH_APP_TEST.md).

The Bluetooth app-test service is installed and verified on the Pi, including
after a reboot. The phone pairs with app-test code 0000 (revert before real
hardware), and the owner reported all physical-iPhone acceptance checks passing.
The owner will run the changed app in Xcode. Updated compilation and Pi checks
are recorded in the Bluetooth guide; historical simulator results below apply
to the earlier revisions and do not establish acceptance of this update.

## Pi touchscreen (October 27, 2026)

A display on the Pi's HDMI port now shows SPARC full screen: Charge, Schedule and
Status, one-tap controls, and the app's look (see `controller/README.md`). It was
installed on `het-pi` with `controller/kiosk/install-display.sh` and the owner's
sudo entry: 62/62 controller tests passed on the Pi, `cage`, Chromium 156 and
Inter were installed, the page answered on 127.0.0.1 only, Chromium rendered it
as the kiosk user, identity was unchanged and Bluetooth kept advertising. With no
display attached the screen service skips itself. A dry run on a virtual display
(`controller/kiosk/kiosk-smoke.sh`) then started the real service: the login
session was active on seat0/VT1, Chromium loaded the page (title SPARC, Connected,
Waiting, 40.0%), polled every 2 seconds, and left no processes after stopping. That
run caught and fixed two bugs: the `?kiosk=1` page address returned 404, and the
Bluetooth status read Off. The screen's demo car is the Pi's virtual car (on by
default at 40%); seven taps on the logo open a hidden Developer switch for it.
**Not yet verified with a real display:** HDMI output, touch and the plug-in
event need the physical panel. Review screenshots are in `artifacts/display/`.

## Status screen and Wi-Fi over Bluetooth (October 27, 2026)

Settings → **Status** (also linked from the controller's ready card) shows the
paired Pi's connection, Bluetooth, Wi-Fi network, uptime, temperature and charging
state from a new encrypted `status` operation. When the Pi is offline, or within ten
minutes of starting, the screen scans nearby networks (`wifi.scan`) and joins one
(`wifi.provision`) over the Bluetooth pairing, which stays a Bluetooth pairing
afterward. An online Pi past that window refuses, and a failed join restores the
previous network. The Pi touchscreen shows "No Wi-Fi" and points to the app. The
app-test service no longer passes `--no-wifi-provision`. **Not yet verified:**
joining a network from a real phone; the Pi stayed on its existing network during
verification.

## Project location

- User's app checkout: `~/Documents/project/sparc APP`.
- Source repository: <https://github.com/hetptl1433/IOS-app>.
- Native app branch: `feat/native-ios-controller`.
- Xcode project: `ios/SPARC.xcodeproj`, shared scheme **SPARC**, iOS 17+.
- The sibling `~/Documents/project/sparc` is a different project. Do not overwrite,
  replace, or combine it with the app checkout.
- Files synced under a ChatGPT project's `sources/` are read-only references.

## Implemented

- Native SwiftUI Home, Charge, Plan, Insights and Records tabs, settings, and the
  approved black visual design with a balanced battery/vehicle card. The vehicle
  light illuminates only in the charging state and honors Reduce Motion.
- Home always says **Charge now**. Every fresh demo launch begins with
  **Waiting for a better charging time**; saved routines remain intact.
- Separate weekly and one-time schedule editors, persistence, automatic temporary
  goal expiry, Chicago daylight-saving handling, and a 30-minute ready buffer.
- First-time LAN pairing with private IP/`.local` hostname and setup key,
  authenticated encrypted pair + ping before Keychain storage, and preserved
  existing credentials on cancellation or failed replacement. Bluetooth is optional.
- Bluetooth discovery/pairing flow, setup-key verification, Wi-Fi provisioning,
  authenticated local-network reconnection, Keychain storage, cancellation and
  recovery UI, and forgetting a saved controller.
- Linux companion with a setup window, encrypted/replay-protected protocol,
  optional NetworkManager Wi-Fi adapter, durable settings, LAN-only systemd unit
  and optional BLE unit. Swift and Python share protocol test vectors. Armbian
  installation leaves the existing Netplan/systemd-networkd configuration intact.
- Unit/UI tests, screenshots captured by the UI suite, structural project checks,
  and configured GitHub Actions for regular/small iPhone simulators plus Python
  research and companion tests.
- Recovery fixes include the native font build error, overnight demo window
  placement, hiding a disabled demo schedule, and clearing unconfirmed live
  telemetry after an acknowledged command cannot be refreshed. Unchanged demo
  polling no longer invalidates the interface, and icon/tab controls have explicit
  hit areas. UI tests check fully visible controls, modal scope and large text.

## October 27 Armbian adaptation verification

- **63 native tests passed in one clean run: 56 unit tests and 7 UI tests** on
  iPhone 17 Pro / iOS 26.5 with Xcode 27. Result bundle:
  `artifacts/ios/SPARC-regular-20261027T131424Z-3829.xcresult`.
  New tests exercise the real URLSession path with a simulated controller that
  decrypts requests and encrypts responses, including forged discovery, wrong
  keys, tampering, mismatched request/device IDs, rejected pair/ping responses,
  redirects, size limits, cancellation, and simulated credential-store write
  failure. These tests inject a memory credential store; physical Keychain
  behavior remains part of signed-iPhone acceptance.
- **3 targeted small-iPhone UI tests passed** on iPhone SE (3rd generation) /
  iOS 26.5: first-time LAN setup entry and required key, fresh waiting demo/all
  five tabs, and permanent Charge now/headlight behavior across all Home states.
  Result bundle: `artifacts/ios/Armbian-small-targeted.xcresult`. This was a
  focused small-device run, not the full seven-test small UI suite.
- **29 controller tests passed** on macOS arm64 / Python 3.13.7 with
  cryptography 43.0.3 and dbus-next 0.2.3, including actual loopback HTTP,
  setup-window/replay rules, LAN-only/optional-discovery lifecycle, BLE failure
  cleanup, and weekly/one-time schedule persistence across service instances.
- **Research regression suite: 94 passed, 1 skipped** on the same host. The
  research code was unchanged. Native project generation/structural checks and
  the runtime helper's HKDF, authenticated encryption, tamper rejection and D-Bus
  import checks passed. Logs are under `artifacts/verification/`.
- The first simulator attempt caught a mock-server request-body stream issue;
  its helper was corrected. That attempt also had one undelivered Home tap;
  the unchanged Home implementation passed all seven UI tests in the clean run
  above. The earlier bundle is retained as regression evidence.
- **Banana Pi Pro physical verification is pending at the owner's request while
  the board is being set up.** No board interfaces, running services, SSH route,
  ARM runtime, radio, router, or physical-iPhone connection was inspected or
  verified. Debian armhf package availability was checked in official metadata;
  the supplied target-side runtime/test commands still need to be run there.
  See [exact installation and connection steps](../../controller/README.md).

## Previous verification evidence (October 26)

- **55 native tests passed in one clean run: 48 unit tests and 7 UI tests**, on
  iPhone 17 Pro / iOS 26.5 with Xcode 27. Result bundle:
  `artifacts/ios/QA-regular-complete.xcresult`.
- **All 7 UI tests passed in a clean small-iPhone CI run** for published revision
  `8a761326`, in the completed GitHub run linked below. Earlier local bundles
  remain available for regression history: `artifacts/ios/QA-small-complete.xcresult`
  contains six passing scenarios and the earlier tab failure, while
  `artifacts/ios/QA-small-tabs.xcresult` confirms that scenario passed after its
  hit-area correction.
- **Release build for the iPhone device SDK succeeded**. This is an unsigned
  build check, not a signed archive or a physical-device acceptance run.
- **94 research tests passed, with 1 skipped** (95 tests total). Local test log:
  `/private/tmp/sparc-python-tests.log`.
- **21 Raspberry Pi companion tests passed** on the recovery host.
- Project generation and structural validation passed: 19 Swift files, three
  Xcode targets, shared scheme, asset and privacy declarations.
- Result paths above are relative to `~/Documents/project/sparc APP`. The
  `.xcresult` bundles under `artifacts/ios/` include logs and screenshot
  attachments. Temporary logs alone are not the durable test evidence.
- **Published revision `8a761326` passed GitHub CI**. The completed
  [iOS workflow run 40384161469](https://github.com/hetptl1433/IOS-app/actions/runs/40384161469)
  passed 48 unit tests plus all 7 UI tests on a regular iPhone, and all 7 UI tests
  on a small iPhone. The [Python workflow](https://github.com/hetptl1433/IOS-app/actions/workflows/test.yml?query=branch%3Afeat%2Fnative-ios-controller)
  also passed its Python 3.11, 3.12 and 3.13 matrix, including the research suite,
  native project validation, and all 21 Raspberry Pi companion tests. This is
  completed evidence for that revision; later revisions have their own workflow
  results. See [QA commands and acceptance matrix](QA.md) for the physical checks
  that CI does not establish.

## Remaining integration and release work

1. Run the supplied read-only board inventory, target-side runtime/controller
   tests and physical iPhone + Banana Pi Pro acceptance matrix, including LAN
   pairing without Bluetooth, Local Network denial, setup-window expiry,
   schedule persistence/reboot, incorrect keys, setup cancellation and
   re-pairing. Where BLE/NetworkManager are installed, also test Bluetooth
   permissions and Wi-Fi password failure/recovery. Simulator tests
   cannot prove radio/router behavior.
2. Implement and authorize a real vehicle adapter, then connect the existing
   research planner to that adapter. The companion stores schedules and offers explicit virtual-car app-test mode.
   Production vehicle telemetry and commands still require an actual adapter. Live charging,
   savings and session history require that integration; the app's demo remains
   available independently.
3. Select the owner's Apple Developer team, confirm the bundle identifier and app
   privacy answers, create a signed archive, and perform TestFlight/App Store
   submission if requested. No signed distribution or store publication is
   established by a simulator build.

## Continue here

Open `ios/SPARC.xcodeproj` in Xcode and use the shared SPARC scheme. To repeat the
automated checks from the repository root:

```sh
python3 scripts/ios/generate_project.py --check
python3 scripts/ios/validate_project.py
python3 -m unittest discover -s tests -v
python3 -m pip install -r controller/requirements.txt
python3 -m unittest discover -s controller/tests -v
bash scripts/ios/test.sh
SPARC_SIMULATOR_KIND=small SPARC_TEST_SCOPE=ui bash scripts/ios/test.sh
```

See [iOS setup](../../ios/README.md), [Pi installation](../../controller/README.md)
and [protocol details](CONTROLLER_PROTOCOL.md). Preserve the approved Home design,
the permanent **Charge now** label, and the waiting default when extending it.
