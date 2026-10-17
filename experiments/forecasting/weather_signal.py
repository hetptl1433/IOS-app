"""Is there any weather signal in the gap between the settled real-time price and the day-ahead price?

Correlations of each forecast input with the gap and with its size, and the gap split by weather surprise,
forecast temperature, wind and holidays. Nights 2025-01-02 to 2026-10-14.
"""
import statistics
import sys
from datetime import date, time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from shared import database  # noqa: E402
from sparc.clock import DAY, at  # noqa: E402
from sparc.forecast import INPUTS, samples  # noqa: E402
from sparc.market import Market  # noqa: E402
from sparc.store import Store  # noqa: E402

store = Store(database())
start, end = at(date(2025, 1, 1), time(0)) - DAY, at(date(2026, 10, 16), time(0))
market = Market(store.five_minute(start, end), store.day_ahead(start, end), store.published_real_time(start, end),
                store.weather(start, end))
data = samples(market, date(2025, 1, 2), date(2026, 10, 14), "weather")
names = INPUTS["weather"]
cols = {n: [r[i] for r in data.rows] for i, n in enumerate(names)}
gap = data.gaps
print(f"{len(gap):,} hourly forecasts; gap = settled real-time price minus the day-ahead price used")
print(f"gap: mean {statistics.mean(gap):.2f}, sd {statistics.pstdev(gap):.2f}, median {statistics.median(gap):.2f} cents")


def corr(x, y):
    mx, my = statistics.mean(x), statistics.mean(y)
    sx, sy = statistics.pstdev(x), statistics.pstdev(y)
    return sum((a - mx) * (b - my) for a, b in zip(x, y)) / (len(x) * sx * sy) if sx and sy else 0.0


print("\ncorrelation with the gap (and with |gap|, i.e. spikiness):")
for n in names:
    print(f"  {n:16} {corr(cols[n], gap):+.3f}   |gap| {corr(cols[n], [abs(g) for g in gap]):+.3f}")


def summary(label, picks):
    g = [gap[i] for i in picks]
    if not g:
        return
    big = sum(abs(x) > 5 for x in g) / len(g)
    print(f"  {label:42} n={len(g):6,}  mean gap {statistics.mean(g):+6.2f}  mean |gap| {statistics.mean(map(abs, g)):5.2f}"
          f"  share |gap|>5c {100 * big:5.1f}%")


idx = range(len(gap))
print("\nby weather surprise in the last 3 hours (observed minus day-ahead forecast):")
summary("much colder than forecast (< -2 C)", [i for i in idx if cols["temp_surprise"][i] < -2])
summary("about as forecast (-2..2 C)", [i for i in idx if -2 <= cols["temp_surprise"][i] <= 2])
summary("much warmer than forecast (> 2 C)", [i for i in idx if cols["temp_surprise"][i] > 2])
print("\nby forecast temperature:")
summary("very cold (heating > 25, below -7 C)", [i for i in idx if cols["heating_fc"][i] > 25])
summary("cold (heating 10-25)", [i for i in idx if 10 < cols["heating_fc"][i] <= 25])
summary("mild", [i for i in idx if cols["heating_fc"][i] <= 10 and cols["cooling_fc"][i] <= 6])
summary("hot (cooling > 6, above 24 C)", [i for i in idx if cols["cooling_fc"][i] > 6])
print("\nby wind forecast:")
summary("calm (< 10 km/h)", [i for i in idx if cols["wind_fc"][i] < 10])
summary("windy (> 25 km/h)", [i for i in idx if cols["wind_fc"][i] > 25])
print("\nholidays:")
summary("holiday", [i for i in idx if cols["holiday"][i] == 1])
summary("not a holiday", [i for i in idx if cols["holiday"][i] == 0])
