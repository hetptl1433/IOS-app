"""The best buffer rule: fill-up, live top-ups, and skipping planned hours whose live price spikes.

    python experiments/battery_buffer/buffer_skip.py [FIRST] [LAST]    (default 2024-04-20 2026-10-14)

Skip thresholds were chosen on 2025 nights. $ a year against the nightly day-ahead plan by calendar year,
± one standard error from resampling months; then each rule against the one it adds to.
"""
import buffer
from buffer import Q20

S80 = ("q", 80, 0.0)
VARIANTS = [
    ("nightly plan", {}),
    ("fill-up", {"gamma": 3}),
    ("fill-up + live q20", {"gamma": 3, "live": Q20}),
    ("fill-up + skip q80", {"gamma": 3, "skip": S80}),
    ("fill-up + live q20 + skip q80", {"gamma": 3, "live": Q20, "skip": S80}),
    ("fill-up + live q20 + skip q90", {"gamma": 3, "live": Q20, "skip": ("q", 90, 0.0)}),
    ("fill-up + live q20 + skip q95+1", {"gamma": 3, "live": Q20, "skip": ("q", 95, 1.0)}),
    ("floor 10%: + live q20 + skip q80", {"gamma": 3, "live": Q20, "skip": S80, "floor": 0.1}),
]
INCREMENTS = [("fill-up + live q20", "fill-up", "live q20, added to fill-up"),
              ("fill-up + skip q80", "fill-up", "skip q80, added to fill-up"),
              ("fill-up + live q20 + skip q80", "fill-up + live q20", "skip q80, added to fill-up+live"),
              ("fill-up + live q20 + skip q90", "fill-up + live q20", "skip q90, added to fill-up+live"),
              ("fill-up + live q20 + skip q95+1", "fill-up + live q20", "skip q95+1, added to fill-up+live")]

if __name__ == "__main__":
    buffer.load(*buffer.arguments())
    buffer.report(buffer.run(VARIANTS), VARIANTS, INCREMENTS)
