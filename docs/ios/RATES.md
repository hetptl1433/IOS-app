# Electricity rate display

The Home **Rate now** tile shows hourly ComEd supply plus the selected Time-of-Day
distribution charge. Tapping it opens a full-screen **Rate details** page. The
Plan rate card opens the same page. It includes the current breakdown, delivery
class selector, four delivery periods, a combined/supply/delivery chart and an
hour-by-hour table. Selecting a date loads that day's retained Pi data; up to
seven previous days are available. All times use America/Chicago, with separate
repeated hours on a fall-back day. Missing supply prices stay blank. Future
day-ahead estimates are distinguished from settled observations.

## Delivery estimate

At the owner's request the initial selection is **single-family with electric
heat**. Match it to the class on the ComEd bill; the app does not enroll anyone
in Delivery Time-of-Day. Changing the class updates the display and persists on
that phone.

The current calculation is explicitly valid for November–December 2026 billing
periods. The official source is ComEd's [2026 Schedule of Rates](https://www.comed.com/cdn/assets/v3/assets/blt3ebb3fed6084be2a/blt86ebee5fe6ed02f8/6ad911e1630c34d7f475f5cd/2026_Ratebook.pdf?branch=prod_alias),
read November 12: informational sheets 67/67.1 (periods and base DFC), 20 (IDUF),
9 (DRAF), 18 (class RBAF), 65 (TPAF), 62 (EDAF), and 56 (DG rebate adjustment).
The billed-charge calculation is `DFC × (IDUF + DRAF + EDAF + TPAF + RBAF) + DGRA`.
Individual adjustments are rounded to $0.00001/kWh as illustrated in ComEd's
[billed delivery-charge guide](https://www.comed.com/cdn/assets/v3/assets/blt3ebb3fed6084be2a/blt7904befea93c3525/695c1987487fe7e68ec05155/A_Guide_to_the_Retail_Customer_s_Billed_Delivery_Service_Charges.pdf?branch=prod_alias).
For the selected class the resulting delivery cents/kWh are:

| Period | Chicago time | Delivery cents/kWh |
| --- | --- | ---: |
| Overnight | 9pm–6am | 1.790 |
| Morning | 6am–1pm | 2.289 |
| Mid-day peak | 1pm–7pm | 5.999 |
| Evening | 7pm–9pm | 2.168 |

Other supply charges, capacity, IEDT, taxes and fixed monthly charges are outside
this displayed subtotal. It is not a complete bill rate. The app refuses to
extend this dated delivery tariff into an unverified period. Charging-session
costs, savings and planning still use their existing supply-only accounting;
this change does not rewrite historical costs or control real Tesla charging.
The research model's older March delivery assumptions were not changed.

## November 12 verification

- Real iPhone `00008120-001C45880A12601E`: test build succeeded; **132 unit tests,
  zero failures**, including seven new calculation, DST, real-controller JSON,
  encrypted Bluetooth routing and preference-persistence checks.
- Pi staging `~/sparc-qa/rates-stage`: **271 tests run, OK (skipped=8)**, including
  rate-history retention, negative/missing data, current-quote expiry, 23/25-hour
  days and encrypted LAN/Bluetooth dispatch without charging-setting mutations.
- The Mac's first full controller run could not open local test sockets. After
  allowing local sockets, seven errors remained because its bundled Python lacks
  `dbus_next`. The ten focused pricing checks and encrypted-route integration
  check passed there; the Pi's full suite is the acceptance result.
- The owner installed the six changed Pi files and entered sudo themselves.
  Service output: `active`. Backup:
  `/var/backups/sparc-controller/source-20261112T161822Z-before-rate-history.tgz`.
  All six installed files matched staging.
- Live `/api/rates?day=2026-11-12` returned 24 rows: 10 settled, one current,
  12 day-ahead estimates and one unavailable. At that check supply was 2.200¢.
- Through iPhone Mirroring, Home showed 4.5¢ and supply + delivery. Tapping opened
  the full page with **2.200 + 2.289 = 4.489¢/kWh**. Combined, Supply and Delivery
  chart selection worked; November 11 loaded its own rows. No Tesla commands,
  sign-in, commit or push were performed.

Local evidence is retained under `artifacts/verification/20261112-rates/`.
The updated UI-test expectation is checked in source but no UI automation was
run; screen interactions used iPhone Mirroring. Other delivery classes have
calculation coverage but were not matched to a real household bill.
