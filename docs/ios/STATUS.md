# SPARC iOS current status

Updated November 13, 2026. This file is a durable handoff for continuing the app.

## November 13: TestFlight beta review submitted

**0.2.0 (13) is Waiting for Review** in App Store Connect. It was added to the
external **SPARC beta** group with automatic tester notification enabled. One
external tester was added and the TestFlight link was emailed. Apple approval
has not been granted; installation is not yet available to external testers.

Public invitation: <https://testflight.apple.com/join/MC9FHqug>. Testers need
their own compatible SPARC Pi; there is no standalone preview.

Apple's review notes now use the requested [iCloud walkthrough](https://www.icloud.com/iclouddrive/06fZQ7TDLTzWI4PkkkHBqBKIg).
The shared file is view/download only for anyone with its link. Its public
Download button was tested without signing in, and the downloaded 1,343,435-byte
MP4 exactly matched the original SHA-256. The 18-second video is explicitly a
sequence of actual physical-iPhone screen captures, not a continuous recording.
It excludes Tesla account details and Calendar event data.

The **152 physical-iPhone tests** and live plug/scheduler results below remain
the QA evidence; real Tesla charging, native Health/Clock schedule mirroring,
two-Pi manual switching, and the separate 6 PM Pi update remain unverified or
pending as detailed in the QA report. No commit or Git push was made.

## November 12: TestFlight preparation, 0.2.0 (13)

Release **0.2.0 (13)** (`com.hetpatel.sparc`) was archived and uploaded using
Xcode's GUI; App Store Connect is processing it. No review submission, approval or
tester availability is claimed. Testers need their own Pi, with no preview mode.
Command-line export failed with **No Accounts** and no App Store provisioning
profile before the GUI upload succeeded.

**152 physical-iPhone tests passed at 18:26 Eastern**, before the final layout-only fix.
The corrected Release app built, installed and visibly kept the quick selector
below the Dynamic Island after reconnection. VStack/GeometryReader changes alone
failed; the final correction compensates only the measured 20pt-root/54pt-status-
bar inset deficit. Diagnostics were removed. This supersedes the open header
finding below.

Shared development-code auto-pairing is **DEBUG-only**; fresh Release testers enter
their own Pi key/code. The final signature verifies. The app bundle had no
`.json`, `.env`, `.key`, `.pem` or `.db` files; bounded binary scans found no
private-key/JWT/Bearer patterns or temporary layout diagnostics. This was not a
complete security audit.

The original Home/Sleep Calendar link is restored: Pi enabled, **14 windows**,
outlet **off**, following **on**. Native Health/Clock schedule mirroring remains
unimplemented. An **18-second, clearly labeled actual screen-capture sequence**
was uploaded to Google Drive with explicit authorization; sharing awaits the
owner's browser sign-in. No commits or pushes. Earlier hardware results and
limits remain in [the QA report](QA_2026-11-12.md).

## November 12: editable Plan source and real plug start/stop QA

The installed app now offers **Manual / Calendar event** on Plan and the schedule
page. Manual disconnects Calendar with the Pi and confirms canonical state before
unlocking the editor. A real phone edit from target 80% to 81% was saved and read
back on the Pi, then restored to 80%. The original Home/Sleep Calendar link was
restored with 14 windows. **152 iPhone tests passed**, zero failures; final build
and installation succeeded. No Pi code update was required for this fix.

Real Charge now, Pause and End actions made the verified Shelly relay go on, off
and off, respectively (4–13 seconds observed). Resume returned to the price-based
plan rather than forcing charging; its misleading label/toast now says **Resume
plan / Smart plan resumed**. Following is left **on** as requested, outlet **off**,
and automatic scheduling restored. Battery naturally advanced from 15% to 16%
during the virtual tests.

Calendar events are explicitly distinguished from Health Sleep/Wind Down and
Clock wake alarms. Native automatic Sleep/Clock mirroring is not implemented or
verified. The header overlap remains a visual finding; ineffective layout
experiments were reverted. The separate 6 PM update remains pending.
See [the updated QA report](QA_2026-11-12.md) for results, failures and limits.
Nothing committed or pushed.

## November 12: fresh QA pass

Real iPhone: **149 tests passed**, zero failures; build succeeded. Pi staging:
**294 run, OK (skipped=8)**. The eight missing-Node display cases were exercised on
the Mac; its complete display suite passed **9 tests** without skips. Both Pi
services are active; plug connected/verified, outlet and following off. Calendar
has 14 windows. Savings arithmetic and live request guards passed. The live
controller differs from the tested staged Python sources only by the pending
`replay_baselines.py` module; the live replay has no 6 PM comparison rows.

Home and the rate page/chart were checked in Mirroring. Remaining manual phone
checks were interrupted by iPhone use; no success is claimed for those checks.
See [the current QA report](QA_2026-11-12.md) for evidence, findings and limits.
No source or production configuration changes, commit or push in this pass.

## November 12: plug connection verified and settings cleanup

This supersedes the pending plug-install note below. The Shelly was reachable and
verified; the live controller was missing `followVirtual`. The owner completed
`install-plug.sh` after entering sudo themselves. Backup:
`/home/hetpi/sparc-qa/backup-plug-20261112-170627`.

The fresh plug probe returned HTTP 200 and verified the meter at 125.3 V. Real
outlet on/off commands both returned HTTP 200 with matching device read-back.
With the existing virtual car already charging, enabling following automatically
turned the outlet on; disabling it turned the outlet off. The same on/off flow
was then confirmed using the real iPhone's switch over Bluetooth. The outlet and
following were left **off**. No virtual battery or schedule was changed. This
checks relay operation, not actual vehicle charging or meter accuracy under load.
Automatic-off when virtual charging stops remains covered by unit tests rather
than this live check.

Smart Plug now has one connection status, compact outlet/power readings and a
shorter **Follow virtual car** switch. Setup explanations moved behind its info
button; redundant selected-car text was removed from connection settings. Older
controllers show an update hint instead of an unsupported switch. Rate notes are
shorter and no longer incorrectly say all savings are supply-only. Historical
replay source remains in savings information and record details.

The cleaned app built, **149 real-iPhone unit tests passed with zero failures**,
and installation succeeded. Mirroring confirmed Connected, Outlet off and the
new layout. Project validation and `git diff --check` passed. Evidence is under
ignored `artifacts/verification/20261112-plug-cleanup/`. The 6 PM installer was not
run in this pass. Nothing committed or pushed.

## November 12 (night): smart plug follows the virtual car (demo)

Settings → Smart Plug shows "Virtual car charges through this plug" only when the plug is verified and the virtual car is active. When it's on, the Pi switches the real outlet on while the virtual car charges and off when it stops. The check runs every 10 seconds, and the Pi only switches when the outlet isn't already in the right state. The manual outlet buttons are replaced by "Following the virtual car". If the plug isn't connected and verified, the setting does nothing: the Pi makes no switch attempts and shows no error. Turning the demo off, or leaving the virtual car, switches the outlet off once, and only if the demo turned it on.

Saved as `smartPlug.followVirtual` (older settings files load as off). Real-iPhone unit suite: **149 tests, 0 failures**, and the app is installed. Staged real-Pi suite: **294 run, OK (skipped=8)**. That includes the demo tests, after a fix: the first test draft left out the required `mode` when saving. The live Pi needs `sudo bash ~/sparc-qa/install-plug.sh`, which backs up the five files, installs them, restarts the controller, and waits for `followVirtual` in `/api/state`. The outlet has not been switched by the demo on real hardware yet, and the toggle has not been seen on screen. Nothing is committed.

## November 12 (later): percent-only savings and hourly comparisons

Savings lead with one number, the percent saved: on the Savings card, the Home tile,
the virtual car's Past 15 days rows and the Pi's Savings screen. A loss reads "N% more"
in the warning colour. Dollars sit one tap away in the ⓘ "How it's calculated" sheet
and in each night's detail sheet.

Settings → Savings comparison (charging section, virtual car only) picks the phone's
baseline: the fixed-price plan (default, **67%**), hourly pricing charging right away at
6 PM (**38%**, 13 nights) or hourly pricing charging at plug-in (**27%**). See
[other comparisons](COMBINED_SAVINGS.md#other-comparisons). The Pi screen keeps the
fixed-price plan.

Real-iPhone unit suite: **148 tests, 0 failures**; the Debug build is installed on the
phone. Checked through Mirroring: the Savings card read "67% saved"; its ⓘ sheet listed
$107.57 → $35.96, $71.61 saved; Records rows read "58% saved" and so on; the setting
switched the card to "27% saved" (· vs hourly) at plug-in and to a dash at 6 PM, then
back to 67%. The Home tile was not checked on screen (Mirroring would not scroll).
Pages pushed from Settings (Records, Savings comparison and the rest) show no back
button; switching tabs returns to Settings. That was not changed here.
Pi staged suite: **290 run, OK (skipped=8)**. The Pi update (touchscreen percent,
6 PM tool and augmented replay) is staged in `~/sparc-qa`. It goes live when the owner
runs `sudo bash ~/sparc-qa/install-6pm.sh`; until then the 6 PM option shows a dash.
UI tests were not rerun. No commit or push.

## November 12: combined savings, Calendar and quick car profiles

The top car name now uses a compact native menu, with immediate app-wide profile
selection. Home, Charge, Plan, Savings and Records follow that car. Viewing Tesla
data/history works without charging-key approval; real charging controls retain
all existing activation and readiness checks. There are no separate car pickers
inside Savings or Records.

Combined savings compare fixed supply + standard distribution against hourly
supply + TOD distribution + SPARC timing, with three contributions that sum once.
The confirmed 15-night replay (Oct 28–Nov 11) is stored on the Pi and shown under
the virtual car's Past 15 days and Records. Fourteen priced nights give **$107.57
baseline vs $35.96, a $71.61 / 66.6% difference**. One DST night remains unpriced.
Record details retain Historical replay provenance; recorded Pi history is not
rewritten. Supply/distribution estimates exclude other bill charges.

Current real-iPhone result: **147 tests, 0 failures, TEST EXECUTE SUCCEEDED**.
Controller: **285 run, OK (skipped=8)** locally and in real-Pi staging. Owner
installed the controller update; service active, source files compared with
staging. Live replay and recorded APIs both verified. The phone's combined
Savings display was checked via Mirroring. Compact-menu selection, CYR’s read-only battery data and separate empty Savings/Records were checked through Mirroring. The owner granted Calendar access and approved the daily Sleep event. The Pi confirmed Calendar enabled with 14 stored windows, 10pm bedtime, 7am wake and a 6:30am ready target; battery remained 80%. Manual propagation of a later Calendar edit has not yet been checked.

The final display recovery update is installed: `RECOVERY_INSTALLED`,
`FINAL_ASSETS_MATCH`, and both services `active`. The Calendar plan was also
checked after relaunching the installed phone app.

See [combined savings](COMBINED_SAVINGS.md), [Calendar behavior and limits](APPLE_SLEEP_CALENDAR.md),
and [profile edge-case coverage](CAR_PROFILE_QA.md). Logs, failures, report,
hourly audit and the final xcresult are retained under ignored
`artifacts/verification/20261112-savings-calendar-profiles/`. No commit or push.

## November 12: combined rate and hourly history

Home's Rate now now shows hourly supply plus Time-of-Day delivery and opens a
full-screen breakdown/history page. The owner selected single-family with
electric heat; the class can be changed on that page. The Pi serves retained
hourly rates over the existing encrypted connection. Actual observations,
partial hours, future day-ahead estimates and missing prices stay distinct.
The verified November–December 2026 delivery tariff includes its applicable
adjustments and expires outside that verified period. This is a display subtotal;
the original rate-page version left savings and planning supply-only; the newer combined-savings work above supersedes that limit.

Real-iPhone unit suite: **132 passed, zero failures**. Pi suite: **271 run,
OK (skipped=8)**. Both app and Pi update installed; the owner entered Pi sudo.
The live phone showed 4.489¢/kWh = 2.200¢ supply + 2.289¢ morning delivery.
Full page, chart selection and loading yesterday's rows were checked through
iPhone Mirroring. See [rates, sources, limits and verification](RATES.md).

## November 12: multi-account QA and installation

This verification supersedes the earlier November 12 notes below. Work used the
`feat/native-ios-controller` checkout in
`~/Library/Mobile Documents/com~apple~CloudDocs/Documents/project/sparc APP`.
Nothing was committed or pushed. No simulator, Tesla sign-in or Apple sign-in was
used, and the owner entered the Pi sudo password themselves.

- **Real iPhone:** `build-for-testing` returned `TEST BUILD SUCCEEDED` for
  `00008120-001C45880A12601E`. `test-without-building -only-testing:SPARCTests`
  returned `Executed 125 tests, with 0 failures` and `TEST EXECUTE SUCCEEDED`.
  All seven new multi-account store tests passed. `devicectl` returned
  `App installed` for `com.hetpatel.sparc`.
- **Pi staging:** Python 3.13 in `~/sparc-qa/stage` returned
  `Ran 266 tests in 2.375s` / `OK (skipped=8)` (258 passed, 8 skipped).
  Focused Tesla tests returned 19 OK, connections-service tests 22 OK, and
  multi-phone BLE tests 15 OK. All seven requested multi-account tests were
  observed passing by name; see [Tesla setup](../tesla-charging-setup.md).
- **Test-import failure resolved:** focused discovery without an import path
  returned `_FailedTest` and
  `ModuleNotFoundError: No module named 'sparc_controller'`. Run focused tests
  from the stage root with `PYTHONPATH=controller`. The full suite previously
  hid this because other test modules insert the controller directory into
  `sys.path`; no test implementation was changed for this issue.
- **Build failure retained:** an attempted fixture edit incorrectly optional-chained
  the nonoptional `first.date`. The first build returned
  `cannot use optional chaining on non-optional value of type 'Date'` and
  `TEST BUILD FAILED`. That edit was reverted. The history session had already
  corrected the optional `first.departure` access. The successful final build
  uses that source with no lasting fixture change from this QA pass.
- **Pi installation:** the history session had stopped with its handoff before
  installation. SHA-256 matched all ten staged install files to the Mac source.
  The owner ran the backup/install/restart command; output was `active` and
  `BACKUP /var/backups/sparc-controller/source-20261112T152636Z-before-tesla-accounts.tgz`.
  Afterwards `cmp` returned `MATCH` for all ten installed files.
- **Live Pi API:** `/api/state` answered and Tesla refresh returned an `accounts`
  array containing the existing authorized `primary` account, label
  `Tesla account 1`, `vehicleCount: 0`, `vehicles: []`, `error: null`, and
  `activeMode: virtual`. Each of `charge_now`, `pause`, `resume`, and `end`
  returned `ok: true`, with the matching accepted operation. The mode was
  checked as virtual before each command. Final snapshot was
  `state: complete`, `virtualCar: true`, `batteryPercent: 80`; the control
  record held `operation: end`, sequence 6. This checks command acceptance;
  it does not demonstrate charging transitions below the target battery.
- **iPhone Mirroring:** Settings → Tesla displayed `Tesla account 1`, its Remove
  control, `No cars on this account`, and `Add another Tesla account`.
  The Virtual Car screen's **Charging with** displayed selected Virtual
  Cybertruck at 80% and Tesla → Pick your car. Saved `sparc-pi5` discovery and
  encrypted Bluetooth reconnection succeeded; leaving a scan also recovered
  the saved connection.

**Not verified / unresolved:** repeated physical-iPhone scans found only
`sparc-pi5`, even after the owner reported `het-pi` ready. `het-pi` was online,
its app-test service was active, its API reported Bluetooth true, BlueZ returned
`ActiveInstances: y 1`, and its source used the expected SPARC service UUID.
Pairing and switching between both Pis therefore remain unverified; no new
pairing was made with `het-pi`. One malformed diagnostic pipeline on `het-pi`
returned `JSONDecodeError: Expecting value: line 1 column 1 (char 0)`; the
independent Bluetooth API/property and UUID checks above subsequently succeeded. Simultaneous use by two real phones was not
performed. Multiple real Tesla accounts, car grouping, switching between real
cars, account removal, key approval, real charging and Tesla history remain
unverified because the existing account returns no vehicles. No Tesla start/stop
commands or credential entry were performed. The UI test suite was not run.

Local, git-ignored evidence is in
`artifacts/verification/20261112-tesla-accounts/`: the failed and successful build
logs, real-iPhone test/install logs, and
`Test-SPARC-2026.11.12_09-25-41--0600.xcresult`. Pi raw test logs remain at
`/tmp/sparc-qa-{tesla,connections,full,multi-phone}.log` on `sparc-pi5`.

## Earlier November 12: Tesla made owner-friendly

The Tesla screen (Settings › Tesla) is now Connect → Connected, with **Switch
account** and **Remove**. Switch account opens Tesla's sign-in without Safari's
saved Tesla login, so a different account can be chosen; the old "Reconnect"
silently reused the same account. With one car on the account, SPARC picks it;
approving SPARC in the Tesla app is one button, re-checked when the owner comes
back. "Virtual key", "verification" and provider wording are gone from these
screens, and Pi/Tesla errors are reworded (`TeslaMessage.friendly`).

**Charging with** (Tesla screen, Virtual Car screen, and Settings once a Tesla
car is chosen) switches between the virtual car and the Tesla after one
confirmation; switching never starts or stops charging. Removing Tesla or
switching account while the Tesla is active moves charging to the virtual car
first. History and Savings get a Virtual/Tesla picker once a Tesla car exists:
virtual sessions stay with the virtual car; the Pi does not record Tesla
sessions yet, and the Tesla side says so. No Pi changes were needed.

Checked on the real iPhone (build 11, Pi over Bluetooth): 114/114 unit tests
passed on the device, and the screens were walked through iPhone Mirroring.
The Tesla account on the Pi still returns no cars; the owner needs to switch to
the Tesla account that owns the car. The UI test suite was not re-run.

## November 6 source and release status

Current app source is **0.2.0 (build 2)**. Normal launches use the paired controller
or show an offline state; Demo mode is no longer available in normal Settings.
Only the car is simulated. Prices and charging history come from the Pi's ComEd
feeds. Saved history, weekly supply-only comparisons and the finite nightly
schedule remain visible with stale labels while the phone is away.

The app now sends a persistent public installation ID in encrypted LAN/Bluetooth
requests, refreshes foreground controller state every four seconds after each
completed poll, and preserves unsaved schedule/car edits when another phone
updates the controller. Developer Settings edits observed simulated battery and
plug state. The Pi remains authoritative for accepted command order and pairing
registration. Bluetooth continuously frames the shared notification stream and
ignores authenticated responses for other request IDs.

The generic iPhone Release build, signed archive and distribution export all
succeeded. The same archive uploaded successfully to App Store Connect app
**SPARCCharge (6831693360)** on November 6 at **16:48:16 UTC**; the uploader then
reported package processing. Apple Delivery UUID:
`70342c70-bf7e-45a7-b057-e12885ee0bd4`. A public App Store release was not performed.
External TestFlight review/group setup and a sharing link are separate steps.

No app tests, simulator runs or physical-iPhone actions were performed for these
multi-phone changes, at the owner's request. Earlier test results below describe
earlier revisions and do not establish runtime acceptance of this update.
Archive, IPA, upload receipt and logs are retained in the task workspace under
`multi-app/verification/testflight/`. The stopped manual-import experiment was not
copied into this source.

The remaining sections retain the earlier October 27 history; their Demo/default
battery descriptions are historical and are superseded by this update.

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

- User's app checkout: `~/Library/Mobile Documents/com~apple~CloudDocs/Documents/project/sparc APP`.
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
