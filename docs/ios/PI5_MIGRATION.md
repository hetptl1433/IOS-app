# Move the iPhone connection to a replacement Pi

## Background

On November 11, 2026, the replacement-flow update was signed, installed in place
on the iPhone and launched as **0.2.0, build 10** from this project. A Pi 5
checkout recovered from GitHub at `708ca30` (0.1.0, build 1) predates the
Tesla and shared-control features and is historical only; do not build from it.

## Select the replacement Pi

The replacement Pi has now been prepared as **SPARC-8672**, device ID
`8672d7f9-4338-4a6d-9b24-26689b21abad`, on host `sparc-pi5`. Select
**SPARC-8672** on the iPhone. The original controller remains **SPARC-D0D4**.
The identity helper preserves its original identity privately and changes only
the replacement's controller ID; rerunning it does not generate another ID.

Copying the SD card preserves schedules and app state. It does not copy the
Bluetooth radio: iOS assigns the Pi 5 a different peripheral identifier, while
the iPhone's existing Keychain pairing still selects the original Pi. Keep
reconnection pinned to that saved radio so both powered-on controllers cannot
receive commands intended for one another.

Before connecting the phone, give the replacement controller a distinct
`deviceId` in its private `identity.json`, preserving its setup key, settings and
other state. Restart its controller service to publish the new `SPARC-XXXX`
Bluetooth name and open its setup window. Do this on the replacement Pi only.
The controller screen shows the matching four-character identifier. The Pi 5's
LAN hostname is `sparc-pi5.local`; its authenticated app endpoint uses port 8766.
Port 8767 serves the Pi's own screen on loopback and is not an iPhone endpoint.

On the iPhone:

1. Open **Settings → Manage home controller**.
2. Choose **Replace controller** if the connected screen is visible, or
   **Search for controllers** from the connection screen.
3. Select the new `SPARC-XXXX` shown on the Pi 5. The original radio is labeled
   **Saved controller** when it appears in the list.
4. Complete pairing, then choose **Use home controller**. The app saves the
   replacement identity and radio only after authenticated pairing and ping.

Cancelling or failing setup leaves the previous pairing intact. An existing
installed version can also scan and select the new controller; the replacement
button and saved-radio label make that route clearer in the updated app.

No fixed IP address or automatic discovery fallback is required. For a LAN-only
pairing, explicitly pair the new controller at `sparc-pi5.local` with its setup
key. Do not convert the old saved identity into a LAN connection to the new Pi.

A physical iPhone must complete the Bluetooth selection. A Mac build or
Simulator cannot rewrite the pairing in the installed iPhone's Keychain or
prove its physical Bluetooth connection.
