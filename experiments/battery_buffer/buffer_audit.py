"""Where does the live top-up gain come from? Month by month, and the price paid for the extra kWh.

Fill-up alone against fill-up plus live top-ups (20th percentile), for the Average and Moderate drivers:
cents per kWh each month, the kWh the top-ups bought and their price, the nights with a top-up, and the
month's lowest billed hourly price. Then the nights whose top-ups cost least.

    python experiments/battery_buffer/buffer_audit.py 2025-04-02 2026-10-14
"""
from collections import defaultdict

import buffer
from buffer import Q20

if __name__ == "__main__":
    buffer.load(*buffer.arguments())
    for name in ("Average", "Moderate"):
        drive = buffer.DRIVES[name]
        base, _ = buffer.simulate(name, {"gamma": 3})
        totals, nights = defaultdict(lambda: defaultdict(float)), {}
        original = buffer.night

        def counted(day, *args):
            before = dict(buffer.STATS)
            cost, grid = original(day, *args)
            extra = buffer.STATS["top_up_kwh"] - before.get("top_up_kwh", 0.0)
            paid = buffer.STATS["top_up_cost"] - before.get("top_up_cost", 0.0)
            month = totals[day.strftime("%Y-%m")]
            month["extra_kwh"] += extra
            month["extra_cost"] += paid
            month["nights_with_extra"] += extra > 1e-9
            nights[day] = (extra, paid)
            return cost, grid

        buffer.STATS, buffer.night = defaultdict(float), counted
        live, _ = buffer.simulate(name, {"gamma": 3, "live": Q20})
        buffer.STATS, buffer.night = None, original
        print(f"\n{name}: month  nights  ¢/kWh fill-up  ¢/kWh +live  extra kWh  ¢/kWh extra  nights w/ extra"
              "  min hourly ¢")
        for month in sorted(base):
            b, o, t = base[month], live[month], totals[month]
            lows = [buffer.market.hourly[h] for d in buffer.days if d.strftime("%Y-%m") == month
                    and buffer.usable(buffer.session(d, drive), buffer.market) for h in buffer.session(d, 1).hours]
            count = sum(1 for d in buffer.days if d.strftime("%Y-%m") == month
                        and buffer.usable(buffer.session(d, drive), buffer.market))
            per_extra = 100 * t["extra_cost"] / t["extra_kwh"] if t["extra_kwh"] else float("nan")
            print(f"  {month}  {count:3d}  {100 * b[0] / b[1]:7.3f}  {100 * o[0] / o[1]:7.3f}  {t['extra_kwh']:8.0f}"
                  f"  {per_extra:7.2f}  {int(t['nights_with_extra']):3d}  {min(lows):7.2f}")
        cheapest = sorted(nights.items(), key=lambda kv: kv[1][1])[:8]
        print("  nights whose top-ups cost least: "
              + ", ".join(f"{d} {kwh:.0f} kWh ${paid:.2f}" for d, (kwh, paid) in cheapest))
