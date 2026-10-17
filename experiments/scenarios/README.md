# Scenarios: who saves how much

Which driver, plug-in window and delivery rate make price-aware charging worth the most. Every script
here runs SPARC's own backtest (`sparc.lab.run_backtest`) on nights 2025-04-02 to 2026-10-14, with
90 days of calibration before them. Savings are scaled to a year.

| Script | Question | Result file |
|---|---|---|
| `pitch_all_strategies.py` | Every strategy for the pitch setup (Cybertruck, 11.5 kW, 20:00–07:00, 15/30/80 kWh), on Time-of-Day delivery, against the default setup | `results/pitch_all_strategies.txt` |
| `plug_in_times.py` | The same three drivers plugging in at 8 p.m. or 11 p.m. | `results/plug_in_times.txt` |
| `night_window.py` | Every strategy for a 10 p.m. plug-in, leaving at 7 or 8 a.m., at 7.7 kW | `results/night_window.txt` |
| `cybertruck.py` | Nightly energy from 20 to 120 kWh, plugged in at 6 p.m. or 10 p.m. | `results/cybertruck.txt` |
| `afternoon_plug_in.py` | Plugged in from noon with 80–100 kWh to add | `results/afternoon_plug_in.txt` |
| `biggest.py` | The configuration with the largest yearly saving | `results/biggest.txt` |
| `worked_example.py` | One typical night hour by hour, for `results/how-it-works.html` | `results/worked_example.txt`, `../../results/example.js` |

The two command-line runs behind the main README's tables are saved too:
`results/cli_tiers.txt` (`python -m sparc backtest 2025-04-02 2026-10-14`) and
`results/cli_examples.txt` (`... --drivers examples`).

`results/runs/` holds the summaries of the 17 backtest runs made during the study, as the web app saved
them: the settings, every strategy's average cost, monthly costs, winners and forecast accuracy. They are
aggregates. The per-night CSV files that go with them are not included, because nightly costs are close
to a copy of ComEd's prices (see `../data/README.md`). `20261016-214142-381e.json` is the tiers run the
headline numbers come from, and other scripts read it.

## What they show

- **The pitch setup** (`pitch_all_strategies.txt`). Against the default setup (standard delivery,
  charging on plug-in), the day-ahead plan on Time-of-Day delivery saves **$355, $643 and $1,224 a
  year** (54%, 51% and 41% cheaper) for the Average, Moderate and Heavy drivers. Perfect hindsight would
  save $393, $697 and $1,297. Switching the delivery rate alone, still charging on plug-in, saves $165,
  $363 and $1,020.
- **Plug-in time matters more than strategy** (`plug_in_times.txt`). Plugging in at 8 p.m., the plan
  saves $190, $280 and $203 a year over charging on plug-in on the same Time-of-Day rate. Plugging in at
  11 p.m., it saves only $51, $66 and $27, because charging on plug-in at 11 p.m. already avoids the
  expensive evening.
- **The day-ahead plan is the strategy to beat.** Across the 154 driver-and-rate cases in `results/runs/`,
  it is the cheapest in 97 and tied within two standard errors in 52. In the other 5, revising it with
  live 5-minute prices beat it by 2 to 8 cents a night: one 10 p.m. plug-in with 60 kWh to add, and four
  noon plug-ins on standard delivery.
- **Big numbers need a bad baseline** (`afternoon_plug_in.txt`, `biggest.txt`). A car plugged in at
  noon that charges at once pays summer-afternoon prices and the 10.7-cent Time-of-Day peak delivery
  rate. It also sets next year's capacity charge (8,924 kWh a year charged in those risk hours). Waiting
  for the night "saves" up to $3,314 a year in `biggest.txt`: plugged in at 11 a.m. with 110 kWh to
  add. The saving is real only for someone who would otherwise charge at noon, so the pitch uses the
  evening plug-in.
