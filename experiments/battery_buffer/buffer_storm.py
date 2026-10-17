"""Gain over the nightly plan across the whole period, with and without the January 2026 storm nights.

    python experiments/battery_buffer/buffer_storm.py 2025-04-02 2026-10-14
"""
import buffer
from buffer import Q20

VARIANTS = [("fill-up", {"gamma": 3}), ("fill-up + live q20", {"gamma": 3, "live": Q20}),
            ("fill-up + live q20 + skip q80", {"gamma": 3, "live": Q20, "skip": ("q", 80, 0.0)})]

if __name__ == "__main__":
    buffer.load(*buffer.arguments())
    for skip, title in ((frozenset(), "all nights"), (buffer.STORM, "without Jan 25-29, 2026")):
        print(f"{title} ({buffer.FIRST} to {buffer.LAST}): $ a year saved against the nightly plan")
        for label, options in VARIANTS:
            row = []
            for name, drive in buffer.DRIVES.items():
                base, _ = buffer.simulate(name, {}, skip)
                months, _ = buffer.simulate(name, options, skip)
                row.append(f"{name} ${(buffer.cents(base) - buffer.cents(months)) * drive / buffer.EFFICIENCY * 365 / 100:+.0f}")
            print(f"  {label:<32}", "  ".join(row))
