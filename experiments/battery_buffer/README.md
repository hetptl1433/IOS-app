# Battery buffer: using the Cybertruck's battery across nights

Every strategy in `../strategies` buys each night exactly what the next day's drive will use, in that
night's cheapest hours, and none of them beats the day-ahead plan. A 123 kWh battery driven 15 or 30 kWh
a day doesn't have to refill every night. These studies let it buy more on cheap nights and less on dear
ones. All decisions use only what was known at the time.

The setup is the pitch scenario: a Cybertruck (123 kWh, 90% charging efficiency) on an 11.5 kW Wall
Connector, plugged in 20:00–07:00 every night, Delivery Time-of-Day, driving 15, 30 or 80 kWh a day
(Average, Moderate, Heavy). The battery is never planned below 20% after a day's drive, nor above 90%
after charging. The simulation is in `buffer.py`.

## The rule

1. **Fill up on cheap nights.** At plug-in, compare tonight's cheap-hours price (the mean day-ahead price
   plus delivery of the hours a normal night's charge needs) with the last 30 nights'. With `rank`
   between 0 for the cheapest and 1 for the dearest, add
   `minimum + (most - minimum) × (1 − rank)³`. Here `minimum` is what keeps tomorrow above the floor and
   `most` is what reaches the 90% cap. The day-ahead planner picks the hours, as every night.
2. **Top up when a live price comes in low.** Every 5 minutes, estimate the current hour's price from
   its posted 5-minute prices. If it plus delivery is below the 20th percentile of the last 30 nights'
   cheap-hours prices, charge now, up to the cap.
3. **Skip a planned hour whose live price spikes.** If a planned slot's live estimate plus delivery is
   above the 80th percentile, skip it, as long as tonight still covers the floor.

The settings (cube, 30 nights, 20th and 80th percentiles, 90% cap) were chosen on Apr–Dec 2025 nights.
2024 and 2026 are out of sample.

## Results

Against the default setup (standard delivery, charging on plug-in), on the 554 nights of the app's
tiers run, 2025-04-02 to 2026-10-14 (`results/buffer_headline.txt`):

| Driver | Day-ahead plan | Battery buffer | Perfect hindsight, one night at a time |
|---|---|---|---|
| Average, 15 kWh a day | $355 (54% cheaper) | **$410 (62%)** | $393 |
| Moderate, 30 kWh a day | $643 (51%) | **$697 (56%)** | $697 |
| Heavy, 80 kWh a day | $1,224 (41%) | **$1,233 (41%)** | $1,297 |

By year, against the nightly plan, ± one standard error from resampling months
(`results/buffer_skip_2024-04-20.txt`; 2025 here is the whole calendar year):

| Driver | 2024 (Apr 20–Dec) | 2025 | 2026 (Jan–Oct 14) | 2026 without the storm |
|---|---|---|---|---|
| Average | +$29 ± 7 | +$49 ± 10 | +$75 ± 30 | +$55 ± 14 |
| Moderate | +$34 ± 11 | +$51 ± 12 | +$74 ± 24 | +$58 ± 15 |
| Heavy | +$7 ± 2 | +$10 ± 2 | +$12 ± 3 | +$12 ± 2 |

Each part adds something in every year. Dollars a year in 2024, 2025 and 2026, from
`results/buffer_skip_2024-04-20.txt`, "each rule against the one it adds to":

| Part | Average | Moderate | Heavy |
|---|---|---|---|
| fill-up, against the nightly plan | +19, +33, +35 | +19, +31, +24 | +3, +4, +4 |
| live top-ups, added to fill-up | +7, +14, +34 | +11, +16, +41 | +3, +5, +6 |
| skipping spikes, added to both | +3, +3, +6 | +4, +4, +9 | +1, +2, +3 |

## Checks

- **No lookahead.** At plug-in a rule sees tonight's day-ahead prices (published the afternoon
  before) and earlier nights' prices. During the night it sees each 5-minute price 7 minutes after its
  interval starts. The bill uses ComEd's settled hourly price, which a live decision never sees.
- **Robust to its settings.** These all stay within about $15 a year of each other, well inside the
  standard errors (`results/buffer_live_*.txt`):
  - live thresholds at the 10th, 20th or 30th percentile, or 0.5 cents lower;
  - a 60-night look-back, an 80% cap, or a square instead of a cube;
  - top-ups that only add to the plan, or that also wait for the newest 5-minute price.

  Skipping helps whether the threshold is the 80th, 90th or 95th percentile.
- **The January 2026 storm.** On Jan 26–28, 2026, hourly real-time prices fell as low as −33 cents per
  kWh while day-ahead prices for the same hours were 25–98 cents. ComEd's published hourly prices confirm it, so it is real
  billing data (`results/negative_prices.txt`). Live top-ups bought heavily then
  (`results/buffer_audit.txt`). Without those nights the whole-period gain over the plan is $44, $45 and
  $9 a year instead of $54, $54 and $10 (`results/buffer_storm.txt`).
- **The floor holds.** "lowest" in the tables is the battery's lowest level after a day's drive. It never
  goes below the floor (20%, or 10% for the 10%-floor variants), so no night came up short.
- **Switching.** The charger turns on about 2.8 times a night for the Average and Moderate drivers,
  against 1.2–1.3 for the plan (`results/buffer_stats.txt`). That suits a smart switch or the car's API.
  The top-ups bought 3,414 kWh at 2.75 cents including delivery for the Average driver, and the skips
  avoided 360 kWh that would have cost 6.45 cents.
- **Against evcc's approach** (`results/buffer_evcc_*.txt`). The open-source charger software evcc
  charges in the cheapest hours and also whenever the price is below a fixed limit. With limits of 3 to
  6 cents including delivery, that does between −$33 and +$5 a year against the nightly plan. A fixed
  limit fills the battery and leaves no room for the next cheap night. Varying the target with the
  night's price is what makes the buffer pay.
- **A billed-price reference** for the live thresholds, instead of a day-ahead one, does no better
  (`results/buffer_reference_*.txt`).
- **How much is left** (`results/buffer_bound_*.txt`, linear programs over all nights).
  - With perfect knowledge of every future real-time price, the same battery limits would save $71, $98
    and $130 a year over the nightly plan for the Average driver (2024, 2025, 2026), and $78, $116 and
    $155 for Moderate. The rules get 41–58% of that.
  - Perfect knowledge of future *day-ahead* prices alone would be worth only $23–50 a year. So a
    next-night price forecast has little to add: the extra value is in real-time dips.
  - For the Heavy driver, 80 kWh a day leaves only about 6 kWh of room between the floor and the cap,
    so the battery can't buffer much.

## How it got here

The rules were found in steps:
1. `fillup_rules.py`: fill to the cap when tonight is in the cheapest part of the last 30 nights, and
   charge only the minimum when it is in the dearest. Also weekend fills and filling every k-th night.
   The percentile rules win, by $19 (Average) and $36 (Moderate) a year on 2026 test nights.
   `fillup_months.py` checks them month by month. Run 2 (`results/fillup_rules_2024-04-20.txt`) tunes
   on 2024–2025 instead.
2. `fillup_rules2.py`: the proportional rule above, with exponents 0.5–3 and look-backs of 14, 30 and
   60 nights. `fillup_marginal.py`: add each extra hour only while it is cheaper than a typical night.
   They do about the same, $32–34 a year for Average on test nights. Filling only at weekends or
   every few nights doesn't help, and some versions lose money.
3. `buffer_live.py`: live top-ups on top of the fill-up rule. `buffer_audit.py` and
   `negative_prices.py` check where the gain comes from.
4. `buffer_skip.py`: skipping planned hours whose live price spikes. `buffer_evcc.py`,
   `buffer_reference.py` and `buffer_bound.py` compare against other approaches and the ceiling.

| Script | Result files |
|---|---|
| `buffer.py` | the shared simulation |
| `fillup_rules.py`, `fillup_rules2.py`, `fillup_marginal.py`, `fillup_months.py` | `results/fillup_*` |
| `buffer_live.py`, `buffer_skip.py`, `buffer_evcc.py`, `buffer_reference.py`, `buffer_bound.py` | `results/buffer_<name>_<first night>.txt` |
| `buffer_headline.py`, `buffer_storm.py`, `buffer_stats.py`, `buffer_audit.py`, `negative_prices.py` | `results/buffer_*.txt`, `results/negative_prices.txt` |

Files ending `_2025-04-02` start on the first night of the app's backtest. Files ending `_2024-04-20`
were run in a second environment that had 2024 prices, with the same scripts.

```sh
python experiments/battery_buffer/buffer_skip.py 2025-04-02 2026-10-14   # about 30 s
python experiments/battery_buffer/buffer_bound.py 2025-04-02 2026-10-14  # needs scipy
```

## Limits

- The car is assumed plugged in every night from 8 p.m. to 7 a.m., driving the same distance every day.
  Irregular driving needs the floor checked against the next day's real plans.
- Holding the battery near 90% more often wears it slightly faster. That cost is not counted.
  Tesla's "daily" range for this battery goes up to 90%. An 80% cap gives up a few dollars a year
  (`results/buffer_live_*.txt`).
- The buffer rules exist as these scripts only. They are not yet strategies in the app, and they need
  5-minute control of the charger.
