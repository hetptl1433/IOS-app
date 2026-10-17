"""evcc's way, for comparison: the nightly plan, plus full-power charging whenever the price is below a fixed limit.

evcc (open-source charging software) charges in the cheapest hours to reach a target, and also whenever
the price is below a limit the user sets. Here the limit is 3 to 6 cents including delivery, filling up to
90%. Against the buffer rules on the same nights.

    python experiments/battery_buffer/buffer_evcc.py [FIRST] [LAST]
"""
import buffer
from buffer import Q20

VARIANTS = [("nightly plan", {}), ("fill-up", {"gamma": 3}),
            *[(f"evcc-style limit {c}¢", {"live": ("fixed", c)}) for c in (3, 4, 5, 6)],
            ("fill-up + live q20", {"gamma": 3, "live": Q20}),
            ("fill-up + live q20 + skip q80", {"gamma": 3, "live": Q20, "skip": ("q", 80, 0.0)})]

if __name__ == "__main__":
    buffer.load(*buffer.arguments())
    buffer.report(buffer.run(VARIANTS), VARIANTS)
