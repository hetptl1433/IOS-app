# Strategies: can anything beat the day-ahead plan in a single night?

The pitch setup throughout: a Cybertruck on an 11.5 kW Wall Connector, plugged in 20:00–07:00, adding
15, 30 or 80 kWh a night (the Average, Moderate and Heavy drivers), on Delivery Time-of-Day. Nights
2025-04-02 to 2026-10-14 unless noted. "$ a year" is saved against the default setup (standard
delivery, charging on plug-in) unless noted.

| Script | Question | Result file |
|---|---|---|
| `sweep.py` | 97 strategies, chosen on earlier nights and tested on 2026 | `results/sweep_*.txt`, `.json` |
| `tesla_modes.py` | SPARC's plan against both modes of Tesla's own scheduler | `results/tesla_modes.txt` |
| `free_switching.py` | Does switching every 5 minutes, which a smart plug allows, pay? | `results/free_switching.txt` |
| `five_minute_billing.py` | What if ComEd billed each 5-minute interval at its own price? | `results/five_minute_billing.txt` |
| `live_sensitivity.py` | Does reacting to live prices pay under any reasonable setting? | `results/live_sensitivity.txt` |
| `personal.py` | What is knowing the driver's habits worth? | `results/personal.txt` |

## The sweep (`sweep.py`)

The 97 strategies:
- fixed start times every half hour from 20:30 to 02:00 (12);
- Tesla-style "finish by" times from 03:00 to 07:00 (9);
- fixed price caps of 1–7 cents (8), and caps at a percentile of the night's day-ahead prices (5);
- the day-ahead plan with a penalty for hours whose price has recently strayed from day-ahead (5);
- the plan revised every 5 minutes with live prices, with four ways to extrapolate the latest
  deviation and five hysteresis settings (20), and six of those with the risk penalty;
- the plan using learned forecasts (Ridge or LightGBM, with or without weather), moved 25–100% of
  the way from day-ahead towards the forecast, with or without hysteresis (32).

Each strategy is ranked on the tuning nights (before 2026-01-01). Then the top 20 are scored on the
2026 test nights against the day-ahead plan, paired night by night, with one standard error.

- **Run 1**, tuned on 2025-04-02 to 2025-12-31: `results/sweep_2025-04-02_2026-01-01.txt`.
- **Run 2**, tuned on 2024-04-20 to 2025-12-31: `results/sweep_2024-04-20_2026-01-01.txt`. It was made
  in a second environment that had 2024 prices.

Run 1's top 20 are live-price variants and ridge-forecast blends (with and without weather). Run 2's
are all live-price variants. On the test nights none beats the day-ahead plan by a meaningful margin.
Run 2's top pick, for example, is −$3 ± 6, −$2 ± 9 and +$5 ± 9 a year against the plan.

The largest edges are a few dollars a year for the Heavy driver, all under two standard errors. The
closest is the 25% ridge blend, at +$2.14 ± 1.12 a year, or 1.9 standard errors. Perfect hindsight would
add $45, $68 and $89 on the same nights. Single-night timing is close to its ceiling: the plan gets about
90–94% of hindsight's saving.

## The other studies

- **Tesla's scheduler** (`tesla_modes.txt`). Against "start at" times from 21:00 to 01:00, the plan
  saves $9 to $108 a year more for the Average driver, $5 to $171 for Moderate and $14 to $59 for Heavy.
  Against "off-peak, finish by" 05:00, 06:00 and 07:00, it saves $10 to $164 more for Average and $8 to
  $200 for Moderate. For Heavy the margin is $13 against finish-by 06:00, $41 against 05:00 and $107
  against 07:00. A driver who picked Tesla's best setting with hindsight would still be $5–13 a year
  behind the plan. A common setting, such as starting at 11 p.m. or finishing by 7 a.m., is $51 to $200
  behind.
- **Free switching** (`free_switching.txt`). Live revision with no hysteresis, switching up to 4–7
  times a night, adds $1, $1 and $6 a year over the plan. Seeing each price 2 minutes sooner adds $2,
  $2 and $5. Fixed price caps (below 2 or 3 cents) switch 6–11 times a night and save $51–91 a year
  less than the plan.
- **5-minute billing** (`five_minute_billing.txt`). ComEd bills each kWh at its hour's average, so
  dips inside an hour cannot be captured. If each 5-minute interval were billed at its own price, the
  plan would save about the same ($366, $643, $1,228). Perfect hindsight's ceiling would rise by $53,
  $53 and $70 a year. The ceiling here keeps the 30-minute finish-early buffer when it can, which is
  why it reads $1,291 for Heavy where the backtest's hindsight reads $1,297.
- **Live-price settings** (`live_sensitivity.txt`, the Commuter and Big day example drivers on standard
  delivery). No hysteresis, strong hysteresis, no extrapolation, or seeing prices sooner all land
  within about 1 cent a night of the plan, except strong hysteresis, which costs 5–8 cents a night.
- **Knowing the driver** (`personal.txt`).
  - Departure time: if the plan aims to finish by 5 a.m. instead of 7 a.m., the Heavy driver saves
    $1,132 instead of $1,224. The others need too few hours for it to matter.
  - Charging only every few nights: with perfect prices, buying N nights' energy in the cheapest hours
    of those N nights lifts the ceiling from $393 (every night) to $421, $436 and $456 (every 2, 3 and
    7 nights) for the Average driver, and from $697 to $737, $757 and $776 for Moderate. That headroom
    is what `../battery_buffer` goes after with rules that only use what is known at the time.
