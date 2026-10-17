"""How often the charger switches on, and what the top-ups bought and the skips avoided, for the best rule.

    python experiments/battery_buffer/buffer_stats.py 2025-04-02 2026-10-14
"""
from collections import defaultdict

import buffer
from buffer import Q20

VARIANTS = [("nightly plan", {}), ("fill-up", {"gamma": 3}),
            ("fill-up + live q20 + skip q80", {"gamma": 3, "live": Q20, "skip": ("q", 80, 0.0)})]

if __name__ == "__main__":
    buffer.load(*buffer.arguments())
    print(f"Nights {buffer.FIRST} to {buffer.LAST}; prices include delivery")
    for name, drive in buffer.DRIVES.items():
        nights = sum(1 for d in buffer.days if buffer.usable(buffer.session(d, drive), buffer.market))
        for label, options in VARIANTS:
            buffer.STATS = st = defaultdict(float)
            months, _ = buffer.simulate(name, options)
            print(f"{name:<9}{label:<32} starts/night {st['starts'] / nights:.2f}  avg {buffer.cents(months):.2f}¢"
                  f"  top-ups {st['top_up_kwh']:.0f} kWh at {100 * st['top_up_cost'] / max(st['top_up_kwh'], 1e-9):.2f}¢"
                  f"  skipped {st['skipped_kwh']:.0f} kWh worth {100 * st['skipped_cost'] / max(st['skipped_kwh'], 1e-9):.2f}¢")
    buffer.STATS = None
