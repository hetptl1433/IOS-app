# Tesla charging setup

Connect the iPhone to the Pi locally, then open Settings → Tesla.
The Pi screen offers the same Tesla setup under Settings.

1. Connect Tesla and approve the requested vehicle-data and charging permissions
   on Tesla's website. SPARC does not collect your Tesla password.
2. Refresh vehicles and select your car. Use **Add another Tesla account** to
   connect each account that has a car you charge here. Accounts have individual
   Remove controls; cars are grouped by their account.
3. Open Pair SPARC key on the phone, or scan the Pi's key QR code. Approve the key
   in the Tesla app for the selected vehicle.
4. With the car awake, refresh Tesla. SPARC verifies the selected vehicle, key,
   signed local connection and timestamped vehicle reading.
5. Choose the car under **Charging with**, then confirm. This pauses the virtual campaign and preserves
   its battery state, completed sessions and raw logs. Activation itself sends no
   start/stop command.

Home and Charge then offer real Start, Stop and charge-limit commands. Each
command is confirmed only after Tesla accepts it. Refresh reads observed vehicle
state; ordinary app refreshes use cached Tesla readings and do not poll the car.
Asleep/offline vehicles show unavailable or older data rather than fabricated
charging measurements. SPARC does not automatically wake the car.

Tesla controls in this version are **manual**. The real-price optimizer still
covers the virtual-car experiment. The installed history code records charges
SPARC starts, under the individual Tesla car, using the car's reported energy
counter and ComEd supply prices when available. Unknown energy or cost stays
unknown; no Tesla savings or automatic departure guarantee is inferred from
virtual results. Charges started outside SPARC are not imported.

**Charging with** lists the virtual car and every returned Tesla. Choosing another
Tesla goes through the virtual car first and sends no start/stop command.
Removing the active car's account also moves SPARC to the virtual car first.
Switching back to Virtual Car does not stop Tesla or resume a paused campaign.

The Pi uses Tesla's official `tesla-http-proxy` v0.4.1 (commit
`49977a18fd68567501d59e16a6c9e4a8b9348544`) on 127.0.0.1:4443, with verified
localhost TLS. The existing enrolled command key stays on the Pi. The proxy
installer verifies its staged executable checksum and supports rollback.

Reference: [Tesla vehicle commands](https://developer.tesla.com/docs/fleet-api/endpoints/vehicle-commands),
[virtual keys](https://developer.tesla.com/docs/fleet-api/virtual-keys/overview),
[official proxy](https://github.com/teslamotors/vehicle-command).

Charging records are under Settings → Charging → Charging history. Removing the
old Records tab changes navigation only; saved sessions and Savings remain.

## November 12, 2026 verification

Installed and checked on the real iPhone `00008120-001C45880A12601E` through
iPhone Mirroring and on `sparc-pi5` through Raspberry Pi Connect. The owner ran
the Pi sudo installation and entered their password. Nothing was committed or
pushed, and no simulator or Tesla/Apple credential sign-in was used.

Actual successful summaries:

```text
Pi full suite: Ran 266 tests in 2.375s / OK (skipped=8)
Pi Tesla suite: Ran 19 tests / OK
Pi connections-service suite: Ran 22 tests / OK
Pi multi-phone BLE suite: Ran 15 tests / OK
iPhone build: TEST BUILD SUCCEEDED
iPhone units: Executed 125 tests, with 0 failures / TEST EXECUTE SUCCEEDED
iPhone install: App installed (com.hetpatel.sparc)
Pi service after installation: active
```

All requested Pi tests passed by name:

- `test_several_accounts_list_every_car_and_commands_go_through_the_owning_account`
- `test_signing_in_again_to_an_account_renews_it_without_a_duplicate_or_losing_its_car`
- `test_removing_one_account_keeps_the_others_and_the_next_sign_in_reuses_the_first_file`
- `test_one_failing_account_does_not_hide_the_others_and_a_shared_car_is_listed_once`
- `test_a_rejected_sign_in_for_another_account_leaves_the_connected_one_working`
- `test_a_pi_set_up_before_accounts_keeps_its_sign_in_and_can_add_another`
- `test_tesla_account_in_use_cannot_be_removed_and_one_account_can_be_removed_alone`

Focused discovery initially failed with `_FailedTest` /
`ModuleNotFoundError: No module named 'sparc_controller'`. Use this from
`~/sparc-qa/stage` (substitute the other test filename as needed):

```sh
PYTHONPATH=controller python3 -m unittest discover -s controller/tests -p test_tesla.py -v
```

The owner-created backup is
`/var/backups/sparc-controller/source-20261112T152636Z-before-tesla-accounts.tgz`.
All ten installed files matched staging, and staging SHA-256 matched the Mac
checkout. `/api/state` and Tesla refresh answered; `connections.tesla.accounts`
contained one authorized existing account, `primary`, with `vehicleCount: 0` and
`vehicles: []`. The phone showed that account, its Remove control and
**Add another Tesla account**. **Charging with** showed selected Virtual
Cybertruck and Tesla → Pick your car. The live virtual `charge_now`, `pause`,
`resume`, and `end` requests all returned `ok: true`; active mode was checked as
virtual before each. The final virtual snapshot remained complete at 80%, so
below-target charging transitions were not demonstrated.

The existing Tesla account returned no cars. Multiple real accounts, grouped
real cars, real-car switching/removal, key approval, actual Tesla charging and
per-car Tesla history were **not verified on real cars**. These behaviors have
unit coverage; no Tesla start/stop command was sent. Simultaneous use by two
physical phones and the UI test suite were not run.

Saved `sparc-pi5` discovery and encrypted Bluetooth reconnection passed. Repeated
iPhone scans did not find `het-pi`, despite its active service, Bluetooth=true,
one active BlueZ advertisement and the expected service UUID. Pairing and
switching between two Pis remain **unverified**.

The first iPhone build in this QA pass failed after an incorrect optional-chain
edit to the fixture's nonoptional date; that edit was reverted. The history
session had already fixed the nullable departure access. See
[status and retained evidence](ios/STATUS.md#november-12-multi-account-qa-and-installation)
for that failure, the successful retry and the local test result bundle.
