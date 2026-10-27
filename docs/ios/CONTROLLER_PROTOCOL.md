# SPARC controller protocol v1

The native iOS app and `controller/sparc_controller` share this contract. Live vehicle values must originate from a vehicle adapter. The explicit `--app-test` mode instead provides a labeled virtual car, starting plugged in at 40%, for testing charging controls and schedules. It never sends vehicle commands. Without either mode or a real adapter, telemetry and vehicle commands explicitly fail.

## Discovery and transports

| Item | Value |
| --- | --- |
| BLE primary service | `7C49B5A0-8D22-4FB8-A3A8-6D7703100001` |
| Read-only public device information | `7C49B5A0-8D22-4FB8-A3A8-6D7703100002` |
| Request characteristic, write with response | `7C49B5A0-8D22-4FB8-A3A8-6D7703100003` |
| Response characteristic, notify | `7C49B5A0-8D22-4FB8-A3A8-6D7703100004` |
| Bonjour service | `_sparc._tcp` |
| Default HTTP LAN port | `8766` |
| Public boot challenge | `GET /v1/info` |
| Authenticated exchange | `POST /v1/exchange` |

The HTTP listener rejects public client addresses. iOS also restricts manually entered destinations to private IPv4, loopback, and `.local` names and never follows redirects. HTTP is only a transport: sensitive requests and all successful responses use the application encryption below. There is no plaintext credential or command endpoint.

For iOS 17 and later, the app declares `NSAllowsLocalNetworking` for `.local` names plus `NSExceptionDomains` CIDR exceptions for `10.0.0.0/8`, `172.16.0.0/12`, `192.168.0.0/16`, `169.254.0.0/16`, and `127.0.0.0/8`. Each range permits HTTP transport; application messages still require the authenticated encryption below. No broad arbitrary-load exception is enabled. These ranges match the app's accepted numeric addresses; numeric IPv6 entry is not supported. Apple's [local networking ATS documentation](https://developer.apple.com/documentation/bundleresources/information-property-list/nsapptransportsecurity/nsallowslocalnetworking) and [exception-domain documentation](https://developer.apple.com/documentation/bundleresources/information-property-list/nsapptransportsecurity/nsexceptiondomains) require explicit IP or CIDR exceptions on these OS versions. The structural validator checks the exact allowlist and rejects broader exceptions.

Public information contains `version:1`, `deviceId` (UUID), `bootId` (new random UUID each service process), `name`, `serverTime` (Unix seconds), `capabilities`, and `vehicleAdapterAvailable:false`. App-test mode advertises an `app-test` capability. Public information contains no secret and does not establish whether a trusted snapshot is virtual. An attacker can forge discovery information and cause a failed connection, but cannot authenticate a response without the setup key. A previously paired device ID must match on reconnect.

Bluetooth carries the same encrypted operations as LAN, including snapshots, commands and schedule changes. A saved Bluetooth pairing stores the peripheral UUID alongside the controller identity; legacy pairings without that field continue to use LAN. The app reconnects that peripheral, obtains a fresh boot challenge and verifies the expected identity before each exchange. Wi-Fi setup is optional and is not required for Bluetooth controls; the app shows it on the Status screen. Advertising remains active after the first-pairing window closes; only new `pair` requests are time limited.

BLE frames are a four-byte unsigned big-endian length followed by UTF-8 envelope JSON. Maximum envelope size is 16,384 bytes. iOS splits writes using the negotiated `maximumWriteValueLength(for:.withResponse)`, acknowledges every write, and waits for the complete authenticated notification frame. The Pi sends conservative 20-byte notification chunks. Incomplete incoming frames expire after 15 seconds; invalid sizes and trailing bytes are rejected. Only one exchange per phone is in flight. Long Wi-Fi activation is acknowledged through the response characteristic rather than blocking an ATT write.

## Encryption and replay handling

Each controller generates a 32-byte cryptographically random setup key on explicit initialization. The owner copies its 64 hexadecimal characters into the app locally. Either LAN or BLE pairing verifies possession through an encrypted request and response. The app retains a candidate only in memory until an authenticated ping succeeds over the chosen transport, then stores the credential in Keychain using `kSecAttrAccessibleWhenUnlockedThisDeviceOnly`. A failed or cancelled attempt never replaces an existing saved pairing.

For first-time LAN setup, both devices already use the same trusted local network. The owner enters a private IPv4 address or `.local` hostname and the setup key. The app obtains the public boot challenge, validates its version and UUIDs, derives the directional keys, and sends an encrypted `pair` request during the controller's local setup window. It authenticates the encrypted response, matches the request ID and the returned device ID, and verifies an encrypted LAN `ping` before saving. Public information alone never establishes trust. The app keeps the owner's validated destination rather than following a hostname supplied by discovery or a pairing response. Reconnection uses the saved device ID and key with `ping`, without requiring the setup window or BLE.

Use HKDF-SHA256, output length 32 bytes, input key material equal to the decoded setup key, salt equal to UTF-8 `deviceId`, and info equal to `SPARC/1/c2d` for phone-to-device or `SPARC/1/d2c` for device-to-phone. Encrypt with ChaCha20-Poly1305 and a fresh random 12-byte nonce. The authenticated additional data is the following exact UTF-8 string, with newline separators and no trailing newline:

```text
SPARC/1
<deviceId>
<bootId>
<c2d or d2c>
```

The envelope is `{ "v": 1, "box": "<base64 of nonce || ciphertext || 16-byte tag>" }`. This matches CryptoKit's `ChaChaPoly.SealedBox.combined`. Do not reuse a nonce under a directional key. Production callers never supply a nonce; deterministic nonces exist only in the public fixture tests.

The request plaintext contains `id` (fresh UUID), `timestamp` (Unix seconds), `operation` (string), and `body` (object). The Pi accepts timestamps within 90 seconds of its own clock. The app derives a clock offset from public device information so a first-boot Pi does not need internet time before provisioning. Every accepted request UUID is cached through its entire acceptance window. Live replay entries are never evicted to make space: the service fails closed at 4096 entries. A new `bootId` binds all messages to a new service boot. A stale boot challenge requires reconnecting; invalid ciphertext receives no BLE response. iOS never retries a mutating message automatically.

Responses contain matching `id`, `ok`, and either `body` or `error:{code,message}`. iOS authenticates ciphertext and checks the request ID before accepting success. Unknown fields may be added, but a new protocol version requires explicit support. JSON dates use ISO 8601 UTC strings; decoders accept seconds and fractional seconds. Charge targets are integers from 50 to 100 and battery percentages are 0–100; schedule minutes are 0–1439, and days use ISO weekdays Monday=1 through Sunday=7.

## Operations

| Operation | Body | Behavior |
| --- | --- | --- |
| `pair` | `{}` | LAN or BLE, during local setup window; returns authenticated device ID and LAN hostname. Does not rotate identity or change networking. |
| `status` | `{}` | Returns `uptimeSeconds`, `temperatureC` (or `null`), `bluetooth` (advertising), mode fields and `wifi`: `{connected,ssid,canSetUp}`, or `null` when this Pi keeps its own network settings. |
| `wifi.scan` | `{}` | BLE only, while Wi-Fi setup is open. Returns up to 20 `networks` as `{ssid,strength,secured}`, strongest first, one entry per name. The app lists secured networks only. |
| `wifi.provision` | `{ssid,password}` | BLE only, while Wi-Fi setup is open; activates personal Wi-Fi through NetworkManager D-Bus and returns `{host,connected:true}` after the new connection is active. On failure, deletes only the attempted new profile and tries to restore the previous connection. Unavailable in LAN-only mode. |
| `ping` | `{}` | Returns authenticated device ID, connection status, and adapter availability. |
| `get_settings` | `{}` | Returns `{weekly,today}`; app-test mode also returns `virtualCarEnabled`. Expired overrides are omitted as `null`. |
| `set_weekly` | Weekly schedule object | Atomically stores the routine on the Pi. Returns `storedOnly:true` until an active planner adapter is attached. |
| `set_today` | `{today:object-or-null}` | Stores or clears one departure without changing the weekly routine. Departure must fall within the next 48 hours. |
| `get_snapshot` | `{}` | Returns a labeled virtual snapshot in app-test mode; otherwise requires a vehicle adapter. |
| `charge_now`, `pause`, `resume`, `end` | `{}` | Control the virtual charge in app-test mode; otherwise require a real adapter acknowledgement. |
| `set_virtual_car` | `{enabled:boolean}` | App-test mode only. Changes the persisted virtual-car setting. Turning it on from off resets the car to plugged in at 40%; repeated enable requests do not reset an active session. |

Wi-Fi setup is open during the first ten minutes after the controller starts, and at any time NetworkManager reports the Pi offline (state below connected-local). The controller re-reads that state every 30 seconds and before each `status`, `wifi.scan` and `wifi.provision`. If the state cannot be read, setup stays closed outside the first ten minutes, so a D-Bus hiccup never lets a nearby phone replace a working network.

A weekly schedule has `targetPercent`, `departureMinutes`, `days`, `enabled`, `mode` (`smart` or `fixed`), and `fixedStartMinutes`. At least one day is required while enabled. A one-time departure has `targetPercent` and `departure`. Configuration persistence alone does not issue a Tesla command. The research planner's existing timezone and tariff logic remain the future adapter's responsibility.

## Virtual car

App-test mode enables the virtual car by default. Its authenticated snapshot has `virtualCar:true`, `controlOnly:false` and `pluggedIn:true`. The model uses a 100 kWh battery, 11.5 kW AC charging at 90% efficiency, and elapsed charging time. Pause and waiting report zero power. Energy, cost and modeled savings accumulate only during charging; the battery stops at the selected target. State is persisted, and service downtime adds no energy. Example rates are 7¢/kWh from 01:00–05:00 America/Chicago and 21¢ otherwise. Smart virtual schedules prefer the example lower-cost hours while accounting for the target and departure; fixed schedules use the selected start and a 30-minute ready buffer. These are virtual calculations, not utility data or the production research optimizer.

Turning the virtual car off returns `virtualCar:false`, `controlOnly:true`, an unplugged state and zero numeric placeholders; the app hides those placeholders and charging commands are rejected until it is enabled. Schedules remain editable. `virtualCar` and `controlOnly` are optional snapshot fields for older controllers. The app treats only authenticated non-virtual adapter data as live telemetry. Local Demo mode has its own virtual car and toggle preference; controller mode reads the Pi's setting and updates it through an encrypted request.

## LAN-only deployment

`--lan-only` disables Bluetooth registration and Wi-Fi provisioning. The existing `--no-ble` option also remains supported. Neither mode changes Netplan, systemd-networkd, NetworkManager, routes, or an active SSH session. The supplied default systemd unit uses LAN-only mode. Optional Bonjour advertising is best effort; direct private-IP access remains available if Avahi is absent. An HTTP listener failure remains fatal so systemd can restart the service. BLE setup is available separately when a working BlueZ adapter exists; NetworkManager provisioning additionally requires an explicitly selected Wi-Fi interface managed by NetworkManager. See [controller installation](../../controller/README.md) for Armbian inspection and deployment commands.

## Verification and references

The public deterministic vectors in `controller/fixtures/wire-v1.json` are consumed by both Swift XCTest and Python unittest. They include known fake keys, nonce bytes, HKDF outputs, and both encrypted directions. They must never be used as device credentials.

Implementation references: [Apple CryptoKit](https://developer.apple.com/documentation/cryptokit/chachapoly), [Core Bluetooth maximum write length](https://developer.apple.com/documentation/corebluetooth/cbperipheral/maximumwritevaluelength(for:)), [local network privacy](https://developer.apple.com/documentation/technotes/tn3179-understanding-local-network-privacy), [BlueZ GATT D-Bus API](https://bluez.readthedocs.io/en/latest/gatt-api/), [NetworkManager D-Bus API](https://networkmanager.dev/docs/api/latest/spec.html), and [Raspberry Pi network configuration](https://www.raspberrypi.com/documentation/computers/configuration.html).
