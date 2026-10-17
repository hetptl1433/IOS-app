"""Fill-up on cheap nights, and live top-ups when a posted price comes in unusually low, year by year.

    python experiments/battery_buffer/buffer_live.py [FIRST] [LAST]    (default 2024-04-20 2026-10-14)

Rules were chosen on Apr-Dec 2025 nights; 2024 and 2026 are out of sample. $ a year against the nightly
day-ahead plan, ± one standard error from resampling months; "lowest" is the battery's lowest level after
a day's drive. See buffer.py for the rules.
"""
import buffer
from buffer import Q20

VARIANTS = [
    ("nightly plan", {}),
    ("fill-up", {"gamma": 3}),
    ("fill-up + live q20", {"gamma": 3, "live": Q20}),
    ("fill-up + live q20-0.5", {"gamma": 3, "live": ("q", 20, 0.5)}),
    ("fill-up + live q10", {"gamma": 3, "live": ("q", 10, 0.0)}),
    ("fill-up + live q30", {"gamma": 3, "live": ("q", 30, 0.0)}),
    ("fill-up + live q20 add-only", {"gamma": 3, "live": Q20, "add_only": True}),
    ("fill-up + live q20 confirm", {"gamma": 3, "live": Q20, "confirm": True}),
    ("fill-up + live q20 back60", {"gamma": 3, "live": Q20, "back": 60}),
    ("fill-up g2 + live q20", {"gamma": 2, "live": Q20}),
    ("fill-up 80% + live q20", {"gamma": 3, "live": Q20, "top": 0.8}),
    ("fill-up 80%", {"gamma": 3, "top": 0.8}),
    ("floor 10%: fill-up", {"gamma": 3, "floor": 0.1}),
    ("floor 10%: fill-up + live q20", {"gamma": 3, "live": Q20, "floor": 0.1}),
    ("nightly + live q20", {"live": Q20}),
]
INCREMENTS = [(label, "fill-up", label.replace("fill-up + ", "") + ", added to fill-up")
              for label in ("fill-up + live q20", "fill-up + live q20-0.5", "fill-up + live q10", "fill-up + live q30")]

if __name__ == "__main__":
    buffer.load(*buffer.arguments())
    buffer.report(buffer.run(VARIANTS), VARIANTS, INCREMENTS)
