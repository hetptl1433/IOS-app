# SPARC iOS current status

Updated October 26, 2026. This file is a durable handoff for continuing the app
without relying on the earlier interrupted chat.

## Project location

- User's app checkout: `~/Documents/project/sparc APP`.
- Source repository: <https://github.com/hetptl1433/sparc>.
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
- Bluetooth discovery/pairing flow, setup-key verification, Wi-Fi provisioning,
  authenticated local-network reconnection, Keychain storage, cancellation and
  recovery UI, and forgetting a saved controller.
- Raspberry Pi companion with a setup window, encrypted/replay-protected protocol,
  NetworkManager Wi-Fi adapter, durable settings, installation instructions and
  service definition. Swift and Python share protocol test vectors.
- Unit/UI tests, screenshots captured by the UI suite, structural project checks,
  and configured GitHub Actions for regular/small iPhone simulators plus Python
  research and companion tests.
- Recovery fixes include the native font build error, overnight demo window
  placement, hiding a disabled demo schedule, and clearing unconfirmed live
  telemetry after an acknowledged command cannot be refreshed. Unchanged demo
  polling no longer invalidates the interface, and icon/tab controls have explicit
  hit areas. UI tests check fully visible controls, modal scope and large text.

## Verification evidence

- **55 native tests passed in one clean run: 48 unit tests and 7 UI tests**, on
  iPhone 17 Pro / iOS 26.5 with Xcode 27. Result bundle:
  `artifacts/ios/QA-regular-complete.xcresult`.
- **All 7 UI scenarios have passing small-iPhone evidence** across the full run
  and the corrected tab regression. `artifacts/ios/QA-small-complete.xcresult`
  contains six passing scenarios and the earlier tab failure;
  `artifacts/ios/QA-small-tabs.xcresult` confirms that scenario passes after its
  hit-area correction. A fresh combined small-device run is being saved separately
  as `artifacts/ios/QA-small-final.xcresult`; verify its summary before treating it
  as a clean full-suite pass.
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
- Remote checks are configured, with exact results available in the branch's
  [iOS workflow](https://github.com/hetptl1433/sparc/actions/workflows/ios.yml?query=branch%3Afeat%2Fnative-ios-controller)
  and [Python workflow](https://github.com/hetptl1433/sparc/actions/workflows/test.yml?query=branch%3Afeat%2Fnative-ios-controller).
  These local results do not establish a remote CI pass. See
  [QA commands and acceptance matrix](QA.md).

## Remaining integration and release work

1. Run the physical iPhone + Raspberry Pi acceptance matrix, including Bluetooth
   and Local Network permission denial, incorrect keys/passwords, setup
   cancellation, Wi-Fi failure/recovery, restarts, and re-pairing. Simulator tests
   cannot prove radio/router behavior.
2. Implement and authorize a real vehicle adapter, then connect the existing
   research planner to that adapter. The companion currently stores schedules but
   explicitly reports vehicle telemetry/commands unavailable. Live charging,
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
