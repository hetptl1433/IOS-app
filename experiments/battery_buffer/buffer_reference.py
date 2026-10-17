"""Live rules measured against earlier nights' billed cheap-hour prices instead of their day-ahead ones.

    python experiments/battery_buffer/buffer_reference.py [FIRST] [LAST]
"""
import buffer
from buffer import Q20

S80 = ("q", 80, 0.0)
VARIANTS = [("nightly plan", {}), ("fill-up", {"gamma": 3}),
            ("fill-up + live q20", {"gamma": 3, "live": Q20}),
            ("fill-up + live q20 + skip q80", {"gamma": 3, "live": Q20, "skip": S80}),
            ("billed ref: + live q20", {"gamma": 3, "live": Q20, "ref": "billed"}),
            ("billed ref: + live q20 + skip q80", {"gamma": 3, "live": Q20, "skip": S80, "ref": "billed"}),
            ("billed ref: + live q30 + skip q80", {"gamma": 3, "live": ("q", 30, 0.0), "skip": S80, "ref": "billed"}),
            ("billed ref: + live q10 + skip q80", {"gamma": 3, "live": ("q", 10, 0.0), "skip": S80, "ref": "billed"})]

if __name__ == "__main__":
    buffer.load(*buffer.arguments())
    buffer.report(buffer.run(VARIANTS), VARIANTS)
