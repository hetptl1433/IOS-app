# SPARC controller protocol v1

The native iOS app and `controller/sparc_controller` share this contract. All live values must originate from a vehicle adapter on the Pi. Until that adapter is installed, pairing, Wi-Fi configuration, ping, and persisted app configuration work; telemetry and vehicle commands explicitly fail.

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

Public information contains `version:1`, `deviceId` (UUID), `bootId` (new random UUID each service process), `name`, `serverTime` (Unix seconds), `capabilities`, and `vehicleAdapterAvailable:false`. It contains no secret. An attacker can forge discovery information and cause a failed connection, but cannot authenticate a response without the setup key. A previously paired device ID must match on reconnect.

BLE frames are a four-byte unsigned big-endian length followed by UTF-8 envelope JSON. Maximum envelope size is 16,384 bytes. iOS splits writes using the negotiated `maximumWriteValueLength(for:.withResponse)`, acknowledges every write, and waits for the complete authenticated notification frame. The Pi sends conservative 20-byte notification chunks. Incomplete incoming frames expire after 15 seconds; invalid sizes and trailing bytes are rejected. Only one exchange per phone is in flight. Long Wi-Fi activation is acknowledged through the response characteristic rather than blocking an ATT write.

## Encryption and replay handling

Each controller generates a 32-byte cryptographically random setup key on explicit initialization. The owner copies its 64 hexadecimal characters into the app locally. BLE pairing verifies possession through an encrypted request and response. The app retains a candidate only in memory until an authenticated LAN ping succeeds, then stores the credential in Keychain using `kSecAttrAccessibleWhenUnlockedThisDeviceOnly`.

Use HKDF-SHA256, output length 32 bytes, input key material equal to the decoded setup key, salt equal to UTF-8 `deviceId`, and info equal to `SPARC/1/c2d` for phone-to-device or `SPARC/1/d2c` for device-to-phone. Encrypt with ChaCha20-Poly1305 and a fresh random 12-byte nonce. The authenticated additional data is the following exact UTF-8 string, with newline separators and no trailing newline:

```text
SPARC/1
<deviceId>
<bootId>
<c2d or d2c>
```

The envelope is `{ "v": 1, "box": "<base64 of nonce || ciphertext || 16-byte tag>" }`. This matches CryptoKit's `ChaChaPoly.SealedBox.combined`. Do not reuse a nonce under a directional key. Production callers never supply a nonce; deterministic nonces exist only in the public fixture tests.

The request plaintext contains `id` (fresh UUID), `timestamp` (Unix seconds), `operation` (string), and `body` (object). The Pi accepts timestamps within 90 seconds of its own clock. The app derives a clock offset from public device information so a first-boot Pi does not need internet time before provisioning. Every accepted request UUID is cached through its entire acceptance window. Live replay entries are never evicted to make space: the service fails closed at 4096 entries. A new `bootId` binds all messages to a new service boot. A stale boot challenge requires reconnecting; invalid ciphertext receives no BLE response. iOS never retries a mutating message automatically.

Responses contain matching `id`, `ok`, and either `body` or `error:{code,message}`. iOS authenticates ciphertext and checks the request ID before accepting success. Unknown fields may be added, but a new protocol version requires explicit support. JSON dates use ISO 8601 UTC strings; decoders accept seconds and fractional seconds. Percentages are integers from 50 to 100, schedule minutes are 0–1439, and days use ISO weekdays Monday=1 through Sunday=7.

## Operations

| Operation | Body | Behavior |
| --- | --- | --- |
| `pair` | `{}` | BLE only, during local setup window; returns device ID and LAN hostname. |
| `wifi.provision` | `{ssid,password}` | BLE only, during setup window; activates personal Wi-Fi through NetworkManager D-Bus and returns hostname after the new connection is active. On failure, deletes only the attempted new profile and tries to restore the previous connection. |
| `ping` | `{}` | Returns authenticated device ID, connection status, and adapter availability. |
| `get_settings` | `{}` | Returns `{weekly,today}`; expired overrides are omitted as `null`. |
| `set_weekly` | Weekly schedule object | Atomically stores the routine on the Pi. Returns `storedOnly:true` until an active planner adapter is attached. |
| `set_today` | `{today:object-or-null}` | Stores or clears one departure without changing the weekly routine. Departure must fall within the next 48 hours. |
| `get_snapshot` | `{}` | Currently unavailable; a vehicle adapter must supply real telemetry. |
| `charge_now`, `pause`, `resume`, `end` | `{}` | Currently unavailable; never reports fictitious command success. |

A weekly schedule has `targetPercent`, `departureMinutes`, `days`, `enabled`, `mode` (`smart` or `fixed`), and `fixedStartMinutes`. At least one day is required while enabled. A one-time departure has `targetPercent` and `departure`. Configuration persistence alone does not issue a Tesla command. The research planner's existing timezone and tariff logic remain the future adapter's responsibility.

## Verification and references

The public deterministic vectors in `controller/fixtures/wire-v1.json` are consumed by both Swift XCTest and Python unittest. They include known fake keys, nonce bytes, HKDF outputs, and both encrypted directions. They must never be used as device credentials.

Implementation references: [Apple CryptoKit](https://developer.apple.com/documentation/cryptokit/chachapoly), [Core Bluetooth maximum write length](https://developer.apple.com/documentation/corebluetooth/cbperipheral/maximumwritevaluelength(for:)), [local network privacy](https://developer.apple.com/documentation/technotes/tn3179-understanding-local-network-privacy), [BlueZ GATT D-Bus API](https://bluez.readthedocs.io/en/latest/gatt-api/), [NetworkManager D-Bus API](https://networkmanager.dev/docs/api/latest/spec.html), and [Raspberry Pi network configuration](https://www.raspberrypi.com/documentation/computers/configuration.html).
