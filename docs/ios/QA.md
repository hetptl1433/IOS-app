# iOS quality checks

SPARC's native app targets iPhone on iOS 17 or later. The checked-in Xcode project
has no third-party Swift packages and opens directly in Xcode. The approved Site
is the visual reference: pitch black, neutral glossy cards, dark green data,
compact Home schedule access, and the realistic Cybertruck cutout with a separate
charging-only white light bar.

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
| Wire protocol | A shared Python/Swift known-answer fixture; tampered tags, wrong key, boot and request identity; BLE frames split across arbitrary MTUs, maximum and oversized payloads, zero/trailing data, parser recovery, strict local addresses, invalid setup keys, forged/wrong-direction envelopes, credentials absent from serialized requests. |
| UI | All five tabs, six Home states, constant **Charge now** label, light-bar state, schedule sheet, today/weekly isolation, canceled drafts, saved weekly edits, start/pause/resume, controller setup entry, accessibility text, screenshots. |

[GitHub Actions for this branch](https://github.com/hetptl1433/sparc/actions/workflows/ios.yml?query=branch%3Afeat%2Fnative-ios-controller)
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
matrix on an actual iPhone and Raspberry Pi 4 with the companion service:

| Scenario | Expected result |
|---|---|
| First scan, Bluetooth allowed | Nearby advertised SPARC controllers appear; other BLE devices are filtered out. |
| Bluetooth off or permission denied | A clear recovery message and iPhone Settings entry; no successful pairing state. |
| Wrong setup key | Authenticated exchange fails; credentials and paired-controller storage remain unchanged. |
| Correct key, setup window open | Controller identity is verified before Wi-Fi details are sent. |
| Wrong Wi-Fi password or unavailable SSID | A recoverable failure; no false connected state. |
| Correct Wi-Fi, same LAN | The app verifies the paired controller over LAN before saving the pairing in Keychain. |
| Cancel, disconnect or timeout during setup | Pending operations finish once, the app can scan/retry, and a prior saved controller remains available. |
| Different Wi-Fi, denied local-network access, Pi offline | A recovery message; Home has no fabricated live telemetry. |
| Pi restart or repeated encrypted request | New boot challenge invalidates old messages; duplicate requests are rejected. |
| Forget controller | The key is removed from this iPhone; Pi Wi-Fi and schedules remain on the Pi. |
| Real vehicle adapter absent | Pairing/network setup works; vehicle data and control remain unavailable. |
| Reduce Motion on | Headlight is steady while charging; transitions do not pulse or scale. |

Real Tesla authorization/control and live session history require a Pi vehicle
adapter. This build does not report them as connected. For App Store/TestFlight,
select the signing team in Xcode and verify capabilities, app privacy answers,
real-device permissions, radio tests and an archive with that team's credentials.

## Evidence boundaries

Local verification on Xcode 27 / iOS 26.5 passed **55 tests in one clean iPhone
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
