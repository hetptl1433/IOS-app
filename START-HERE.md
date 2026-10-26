# Start here: SPARC iPhone app

The app checkout belongs at `~/Documents/project/sparc APP`. The sibling `sparc`
folder is a different project.

Open [ios/SPARC.xcodeproj](ios/SPARC.xcodeproj) in Xcode, select the **SPARC**
scheme and an iPhone Simulator, then press Run. Simulator builds need no signing
account. Running on a physical iPhone needs your development team in the app
target's Signing & Capabilities.

The app opens in the approved waiting demo. Home always says **Charge now**.
Use the profile button for Settings and the Raspberry Pi connection flow.

- [Current status and remaining integration](docs/ios/STATUS.md)
- [iPhone setup and features](ios/README.md)
- [QA results, commands and hardware checklist](docs/ios/QA.md)
- [Raspberry Pi installation](controller/README.md)
- [Controller protocol](docs/ios/CONTROLLER_PROTOCOL.md)

## Source map

- `ios/SPARC/SPARCApp.swift`: app entry and refresh lifecycle.
- `ios/SPARC/Sources/UI/`: screens, theme and vehicle rendering.
- `ios/SPARC/Sources/Core/`: schedules, state and demo persistence.
- `ios/SPARC/Sources/Connectivity/`: Bluetooth setup, encrypted LAN requests and
  Keychain pairing.
- `ios/SPARCTests/` and `ios/SPARCUITests/`: native unit and interface checks.
- `controller/`: Raspberry Pi companion and its tests.
- `sparc/` and `tests/`: the existing Python research/planning application.

The native app and setup companion are implemented. Physical radio/router
acceptance, the real vehicle adapter/planner connection, and signed distribution
remain separate integration/release steps, detailed in the status document.
