"""The headline numbers with the buffer rules: $ a year saved against the default setup.

The default setup is standard delivery and charging on plug-in, as in the tiers backtest
(experiments/scenarios/results/runs/20261016-214142-381e.json, nights 2025-04-02 to 2026-10-14).

    python experiments/battery_buffer/buffer_headline.py 2025-04-02 2026-10-14
"""
import json
import sys
from pathlib import Path

import buffer
from buffer import Q20

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from shared import TIERS_RUN  # noqa: E402

VARIANTS = [("nightly plan", {}), ("fill-up", {"gamma": 3}), ("fill-up + live q20", {"gamma": 3, "live": Q20}),
            ("fill-up + live q20 + skip q80", {"gamma": 3, "live": Q20, "skip": ("q", 80, 0.0)})]

if __name__ == "__main__":
    buffer.load(*buffer.arguments())
    run = json.loads(TIERS_RUN.read_text())
    default = {r["profile"]: r["default_cost"] for r in run["default_setup"]}
    backtest = {(r["profile"], r["strategy"]): r["saved_per_year"] for r in run["default_setup"]}
    print(f"Nights {buffer.FIRST} to {buffer.LAST}; $ a year saved against the default setup (% cheaper, ¢/kWh)")
    print(f"{'':<32}" + "".join(f"{name:>24}" for name in buffer.DRIVES))
    for strategy in ("Day-ahead plan", "Perfect hindsight"):
        print(f"{'backtest: ' + strategy:<32}" + "".join(f"{'$%.0f' % backtest[n, strategy]:>24}" for n in buffer.DRIVES))
    for label, options in VARIANTS:
        row = []
        for name, drive in buffer.DRIVES.items():
            months, _ = buffer.simulate(name, options)
            per_kwh = buffer.cents(months)
            per_night = per_kwh * drive / buffer.EFFICIENCY / 100
            row.append(f"${(default[name] - per_night) * 365:,.1f} ({1 - per_night / default[name]:.1%}, {per_kwh:.3f}¢)")
        print(f"{label:<32}" + "".join(f"{c:>24}" for c in row))
