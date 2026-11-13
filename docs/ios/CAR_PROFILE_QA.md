# Car profiles and edge-case QA

The top car name opens a compact native menu. Selecting a profile updates the viewing context immediately and persists it per Pi. Every tab and Records follows that choice; Savings and Records have no independent car picker. Switching context resets page-local state so a detail sheet, date selection or previous car's data does not carry across.

Viewing a Tesla does not require its charging key. The app can show its data/history in a read-only profile while the Pi continues its existing charging provider. Enabling actual Tesla charging still requires the existing explicit activation and readiness checks. Merely choosing a profile sends no charge/start/stop command. A new profile never borrows another car's battery, artwork, history or savings while fetching data.

## Coverage

| Case | Verification |
|---|---|
| Instant choice, no mutation or activation | New iPhone unit test |
| Tesla without virtual-key approval | Read-only profile test; commands remain disabled |
| Rapid virtual/Tesla selections | Only last profile prepared; choice persists on relaunch |
| Removed or unknown vehicle | No wrong-car data; invalid selection rejected/falls back |
| Another phone changes selected Tesla | Viewed ID guards battery/history and command enablement |
| Controller switch | Profile setting and history cache scoped by Pi identity |
| Offline/failed refresh | Profile identity retained; error shown; no commands sent |
| Older controller/history API | Existing compatibility tests retained; saved history survives failed refresh |
| Stale cached data | Existing snapshot/history freshness tests and exact vehicle-ID guards |
| Several Tesla accounts, shared car, one failed account | Controller multi-account suite |
| Expired/rejected authorization, token rotation/save failure | Controller Tesla suite |
| Wrong vehicle, invalid charge payload, unready key | Controller rejects before actuation |
| Timeout/uncertain response | No blind retries of vehicle commands |
| Accepted command then failed local audit/read-back | Accepted result retained with warning |
| Pi process restart | Durable account, control and history tests |
| Bad key, tampering, nonce replay, wrong direction/boot | Existing encrypted-protocol tests |
| Missing prices, negative scheduling gain, DST | Combined-billing and rate-history tests |
| Calendar overlap/deletion/expiry/foreign phone | Calendar controller and iPhone tests |

This is targeted coverage, not proof that every possible hardware/account/network failure was exercised. Live start, stop and limit commands cannot be verified until Tesla key approval is complete. No Tesla or Apple sign-in was performed. No physical charger was actuated for QA. Two-Pi manual switching remains unverified from the earlier QA session.

## Runs and failures retained

- Controller: 285 run, OK (skipped=8), both local and real-Pi staging.
- Tesla adapter/history focus: 34 tests, OK.
- Current iPhone: 147 tests, 0 failures, `TEST EXECUTE SUCCEEDED` on physical device.
- An intermediate quick-profile run had 10 assertions fail: older-controller history selection/cache and legacy Tesla display name. Those regressions were fixed and the complete 147-test run passed afterward.
- Earlier new-test fixture import failure and a Swift view-grouping compile error were corrected; their original logs remain in ignored verification artifacts.
- Initial physical destination unavailable / locked messages were resolved through the owner's device and Mirroring. No Simulator fallback was used.

A Mirroring check found Results still following the charging provider in a read-only Tesla profile. The route was corrected, disabled Tesla controls were visibly dimmed, and a dedicated data-isolation regression passed in the full 147-test physical-iPhone run.

Live Mirroring confirmed the compact top menu, immediate CYR selection, read-only 80%/unplugged data, disabled charging controls without key approval, CYR-only empty Savings/Records and switching back to the virtual profile. Calendar linking was acknowledged by the Pi with 14 windows. The Pi display service was restarted to load the new page assets and returned active.

The final offline replay cache regression passed: results restore after restarting offline and never load under a different Pi. All 147 physical-iPhone tests passed. An earlier runner attempt failed because the real device was locked; after reconnecting Mirroring, the complete rerun passed.

Display reconnection QA used an isolated loopback service. The first check found a stale error after reconnection; recovery now listens for the existing successful controller heartbeat and the changed script has a content version. With that version verified in the page, stopping the service produced Retrying, and restoring it automatically returned the unchanged 66.6% / $71.61 report without a reload or button press. No real Pi outage was induced for this check.

Final display assets matched staging (`FINAL_ASSETS_MATCH`) after the owner completed installation (`RECOVERY_INSTALLED`). Both controller and display services returned `active`; Calendar remained enabled with 14 windows. The final installed phone app retained the Calendar plan after relaunch. A later Calendar-edit round trip and manual two-Pi switching remain unverified.

November 12 plug follow-up: the owner installed the missing plug update. A fresh read-only probe verified the Shelly. Both direct on/off commands and automatic following returned matching real-device states. The real iPhone toggle also enabled automatic outlet-on and disabled it back to off over Bluetooth. Following and the outlet were left off, and virtual battery/schedule settings were preserved. The cleaned UI passed all 149 real-iPhone unit tests and was installed; live Mirroring confirmed the compact status and toggle. See STATUS.md and ignored `artifacts/verification/20261112-plug-cleanup/`.
