# SPARC controller protocol v1

The native iOS app and `controller/sparc_controller` share this contract. The default Virtual Car provides labeled simulated measurements; enabling real ComEd pricing does not turn simulated energy into vehicle data. Separately authorized Tesla setup and explicit `activate_connection` switch Home to real manual controls, pause the virtual campaign and retain its records. Tesla snapshots use explicit availability flags and cached readings; only an explicit Tesla refresh reads vehicle data. See [Tesla setup](../tesla-charging-setup.md). Automatic price planning remains virtual-only. Charging history is kept per car: the virtual car's sessions and the Tesla charges SPARC starts are recorded and served separately (see [Charging history](#charging-history)).

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

Public information contains `version:1`, `deviceId` (UUID), `bootId` (new random UUID each service process), `name`, `serverTime` (Unix seconds), `capabilities`, and `vehicleAdapterAvailable:false`. App-test mode advertises an `app-test` capability. A Bluetooth attribute value holds at most 512 bytes, so the Bluetooth info characteristic lists only the capabilities setup needs (`pair`, `ping`, `wifi.provision`, `app-test`, `pairing-code`); `GET /v1/info` lists them all. Public information contains no secret and does not establish whether a trusted snapshot is virtual. An attacker can forge discovery information and cause a failed connection, but cannot authenticate a response without the setup key. A previously paired device ID must match on reconnect.

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

Tesla operations (`tesla_authorize`, `tesla_complete_authorization`, `tesla_refresh`, `tesla_select_vehicle`, `tesla_disconnect`, `tesla_command`) report `connections.tesla`. One Pi may hold several Tesla accounts. `tesla.accounts` lists `{id,label,authorized,vehicleCount,error?}`, and `tesla.vehicles` is one list across accounts, each car tagged with `accountId`; a car shared by two accounts appears once. Completing sign-in for an account already on the Pi renews it and keeps its cars; any other account is added. `tesla.accountId` names the account that owns the selected car, and charging commands use that account. `tesla_disconnect` takes `{}` to remove every account or `{accountId}` to remove one. The Pi refuses to remove the account in use while Home charges with the Tesla; switch to the virtual car first. Older Pis omit `accounts`; the app then shows a single account. An account that fails to answer is marked with `error` without hiding the other accounts' cars.

A weekly schedule has `targetPercent`, `departureMinutes`, `days`, `enabled`, `mode` (`smart` or `fixed`), and `fixedStartMinutes`. At least one day is required while enabled. A one-time departure has `targetPercent` and `departure`. Configuration persistence alone does not issue a Tesla command. The research planner's existing timezone and tariff logic remain the future adapter's responsibility.

## Charging history

`get_history` takes `{cursor?, limit?, car?, vehicleId?}`. `limit` is 1–8 (the app asks for 4 over Bluetooth, 8 over LAN). `car` is `"virtual"` (the default) or `"tesla"`; `vehicleId` is allowed only with `"tesla"` and narrows the page to that car, otherwise every Tesla on the Pi is included. Omitting `car` keeps older apps on the virtual car, so they never receive a Tesla row. The page is `{schemaVersion:1, generatedAt, timezone:"America/Chicago", costScope:"electricity_supply", benchmark, car, week, sessions, nextCursor}`. `car` echoes the request (`{kind:"virtual"}` or `{kind:"tesla", vehicleId}`); older controllers omit it and reject the `car` field. `week` is the Chicago Monday-to-Sunday week of that car only, with seven `days`.

Every session carries `car`. Virtual sessions are unchanged apart from `car:{kind:"virtual"}`: `simulated:true`, `source:"ComEd"`, a `departure`, and costs compared with `benchmark:"immediate_same_energy"` (charging the same measured energy continuously from the session start).

Tesla sessions (`car:{kind:"tesla", vehicleId}`, page `benchmark:"none"`) are real charges that SPARC started:

| Field | Meaning |
| --- | --- |
| `vehicleName` | The car's name when the charge started. |
| `start` / `date` | When Tesla accepted SPARC's `charge_start`. `departure` is `null`. |
| `end`, `endReason` | `null` while `status:"active"`. Otherwise `limit_reached` (status `complete`), `stopped` (SPARC's Stop, or the car reported Stopped), `unplugged`, `no_power` (two readings in a row without power), or `not_observed` (three failed reads in a row, 24 hours of reads, the Tesla removed, or another car selected). |
| `initialBatteryPercent`, `batteryPercent`, `targetBatteryPercent` | From the car's own readings; `targetBatteryPercent` is `null` when the car did not report its limit. |
| `energyKWh`, `energySource:"vehicle_reported"` | Sum of increases in the car's `charge_energy_added` counter across readings; a drop means the car started a new counter and only the new value is added. This is energy added to the battery, so the wall supplied more. `null` when the car did not report the counter in at least two readings. `energyPartial:true` when SPARC could not take a final reading, so the true amount may be higher. |
| `costUSD`, `costFinal`, `unpricedEnergyKWh`, `provisional` | ComEd supply cost of `energyKWh`. Energy between two readings is placed from the earlier reading at the car's reported charging power. `null` when energy or an hour's price is unknown. |
| `baselineCostUSD`, `savingsUSD`, `savingsPercent`, `targetReachedByDeadline` | Always `null`: a manual charge has no shifted baseline, so no savings are claimed. |
| `simulated:false`, `source:"ComEd"`, `lastObservedAt` | Real data; prices from ComEd; time of the latest reading. |

For Tesla weeks, a session counts as priced when its supply cost is known and as unpriced when its energy or price is missing.

**When the Pi reads the Tesla.** Every Fleet API `vehicle_data` response below HTTP 500 is billed, including the 408 an asleep car returns, and nothing here wakes the car. A session opens only when SPARC's own `charge_start` (Home Charge now/Resume or `tesla_command`) is accepted. It is fed by reads SPARC already makes: the read before Start, the read after each command, and explicit refresh, vehicle selection and activation. While the session is open the Pi adds one `charge_state` read every 15 minutes, counted from the latest read. It stops reading at the first reading that shows the charge ended, after three failed reads, after 96 reads, or when the Tesla is removed. With no open session it makes no reads. A 6-hour charge costs about 24 reads (about $0.05 at $1 per 500). Tesla sessions are stored in `history/tesla/` beside, never inside, the virtual ledger.

## Virtual car

App-test mode enables the virtual car by default. Its authenticated snapshot has `virtualCar:true`, `controlOnly:false` and `pluggedIn:true`. The model uses a 100 kWh battery, 11.5 kW AC charging at 90% efficiency, and elapsed charging time. Pause and waiting report zero power. Energy, cost and modeled savings accumulate only during charging; the battery stops at the selected target. State is persisted, and service downtime adds no energy. Example rates are 7¢/kWh from 01:00–05:00 America/Chicago and 21¢ otherwise. Smart virtual schedules prefer the example lower-cost hours while accounting for the target and departure; fixed schedules use the selected start and a 30-minute ready buffer. These are virtual calculations, not utility data or the production research optimizer.

Turning the virtual car off returns `virtualCar:false`, `controlOnly:true`, an unplugged state and zero numeric placeholders; the app hides those placeholders and charging commands are rejected until it is enabled. Schedules remain editable. `virtualCar` and `controlOnly` are optional snapshot fields for older controllers. The app treats only authenticated non-virtual adapter data as live telemetry. Local Demo mode has its own virtual car and toggle preference; controller mode reads the Pi's setting and updates it through an encrypted request.

## LAN-only deployment

`--lan-only` disables Bluetooth registration and Wi-Fi provisioning. The existing `--no-ble` option also remains supported. Neither mode changes Netplan, systemd-networkd, NetworkManager, routes, or an active SSH session. The supplied default systemd unit uses LAN-only mode. Optional Bonjour advertising is best effort; direct private-IP access remains available if Avahi is absent. An HTTP listener failure remains fatal so systemd can restart the service. BLE setup is available separately when a working BlueZ adapter exists; NetworkManager provisioning additionally requires an explicitly selected Wi-Fi interface managed by NetworkManager. See [controller installation](../../controller/README.md) for Armbian inspection and deployment commands.

## Verification and references

The public deterministic vectors in `controller/fixtures/wire-v1.json` are consumed by both Swift XCTest and Python unittest. They include known fake keys, nonce bytes, HKDF outputs, and both encrypted directions. They must never be used as device credentials.

Implementation references: [Apple CryptoKit](https://developer.apple.com/documentation/cryptokit/chachapoly), [Core Bluetooth maximum write length](https://developer.apple.com/documentation/corebluetooth/cbperipheral/maximumwritevaluelength(for:)), [local network privacy](https://developer.apple.com/documentation/technotes/tn3179-understanding-local-network-privacy), [BlueZ GATT D-Bus API](https://bluez.readthedocs.io/en/latest/gatt-api/), [NetworkManager D-Bus API](https://networkmanager.dev/docs/api/latest/spec.html), and [Raspberry Pi network configuration](https://www.raspberrypi.com/documentation/computers/configuration.html).

## Electricity rate history

Authenticated `get_rates` takes `{day?: "YYYY-MM-DD"}` and returns a single
Chicago calendar day. Only today and the previous seven calendar days are
accepted, with no extra fields. The response is `{schemaVersion:1, source:"ComEd",
timezone:"America/Chicago", day, generatedAt, currentSupplyCents, observedAt,
hours:[{start, supplyCents, status}]}`. `start` is an ISO-8601 instant; DST days
contain 23 or 25 actual hours. Normal days contain 24. Rates are cents/kWh and
can be negative. Missing rates are null, never zero.

Statuses are `settled` (ComEd's hourly table), `observed` (12 cached five-minute
samples), `provisional` (an incomplete past hour), `current` (fresh current-hour
average), `dayAhead` (a future estimate), or `unavailable`. An old current quote
is not replaced with a future estimate. This read uses the existing cached feed,
performs no extra network request, and does not modify charging/accounting.
The same route works over LAN and Bluetooth within the 16-KiB frame limit.
Loopback QA: `GET /api/rates?day=2026-11-12` on port 8767.

The iPhone adds the selected residential Time-of-Day distribution tariff for
its rate display only. The default class requested by the owner is single-family
with electric heat; the class is visible and editable. Supply-only session costs,
savings, and the virtual planner remain unchanged. See [rate display](RATES.md)
for the dated tariff scope and verification.

## Combined savings and Calendar windows

`get_savings` accepts `{kind:"simulation"}` (the installed historical replay) or `{kind:"recorded"}` (the durable virtual-car ledger). Both use schemaVersion 1, fixed_supply_standard_delivery benchmark, electricity_supply_distribution_estimate costScope, per-night costs/three gains, weighted summary and explicit missing-price counts. They are separate sources. See [combined savings](COMBINED_SAVINGS.md).

`set_sleep_windows` accepts exactly enabled, windows and targetPercent over paired encrypted LAN/Bluetooth. Each of at most 14 windows has an opaque id and absolute start/departure. Calendar state is owned by one client, checked for overlap/expiry and restricted to the virtual car. `get_settings.sleepCalendar` reports acknowledgement without event names. See [Calendar contract](APPLE_SLEEP_CALENDAR.md).
