"""The most negative billed hours: real, or a data error? ComEd's published hourly price against the 5-minute feed.

    python experiments/battery_buffer/negative_prices.py [FIRST] [LAST]
"""
import buffer
from buffer import HOUR, SLOT
from sparc.clock import local  # buffer has put the repository on the import path

if __name__ == "__main__":
    buffer.load(*buffer.arguments())
    market = buffer.market
    low = sorted(market.hourly.items(), key=lambda kv: kv[1])[:25]
    for hour, price in low:
        five = [market.five_minute.get(t) for t in range(hour, hour + HOUR, SLOT)]
        print(local(hour).strftime("%Y-%m-%d %a %H:%M"), f"billed {price:7.2f}",
              f"published {market.published.get(hour, float('nan')):7.2f}",
              f"5-min mean {market.means.get(hour, float('nan')):7.2f}",
              f"day-ahead {market.day_ahead.get(hour, float('nan')):6.2f}",
              "5-min:", " ".join(f"{p:.0f}" if p is not None else "-" for p in five))
