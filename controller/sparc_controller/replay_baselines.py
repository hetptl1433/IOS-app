"""Hourly pricing, charging right away at 6 PM: a second baseline for the savings replay.

The replay compares against a fixed-price plan. This adds what each night's grid energy
would cost on ComEd hourly pricing if charging started right away at 6 PM Chicago time,
at the replay's charging power. It is priced twice, with standard delivery and with
Time-of-Day delivery, so the gain splits into delivery and timing without double
counting. Prices are ComEd's published real-time hourly prices (its billing prices). A
night with a missing price stays unpriced; nothing is filled in.

Before adding anything, the same method recomputes the replay's own arrival-time hourly
costs and stops if they don't match, so both baselines share one price source.

    python3 -m sparc_controller.replay_baselines REPLAY.json OUT.json
"""
from datetime import date, datetime, time, timedelta, timezone
import html
import json
import re
from pathlib import Path

from .billing import DEFAULT_CLASS, CHICAGO, cents
from .history import immediate_buckets

START_MINUTES = 18 * 60
KEY = "at6PM"
TOLERANCE_USD = 0.005


def table_prices(body, day):
    """{UTC hour ISO: real-time cents/kWh} from ComEd's hourly table for one date.

    Rows are matched by their hour-end label. Hours that start or end on an ambiguous or
    skipped Chicago wall time (daylight-saving changes) and hours without a real-time
    price are left out.
    """
    from .research.comed import _ROW, _cents
    text = re.sub(r"<[^>]*>", " ", html.unescape(body.decode("utf-8", "replace")))
    prices = {}
    for label, half, _, real_time in _ROW.findall(text):
        end = datetime.combine(day, time(int(label) % 12 + (12 if half.lower() == "p" else 0)), CHICAGO)
        start, price = end - timedelta(hours=1), _cents(real_time)
        clear = all(w.replace(fold=0).utcoffset() == w.replace(fold=1).utcoffset() for w in (start, end))
        if price is not None and clear:
            prices[start.astimezone(timezone.utc).isoformat()] = price
    return prices


def night_costs(day, energy, power, prices, start_minutes=START_MINUTES, delivery_class=DEFAULT_CLASS):
    """Hourly supply plus standard or Time-of-Day delivery, charging right away from the start."""
    begin = datetime.combine(day, time(start_minutes // 60, start_minutes % 60), CHICAGO)
    buckets = immediate_buckets(begin.astimezone(timezone.utc), energy, power)
    if not buckets or abs(sum(buckets.values()) - energy) > 1e-6:
        return None
    standard = timed = 0.0
    for hour, kwh in buckets.items():
        supply, flat, tod = prices.get(hour), cents(hour, delivery_class, True), cents(hour, delivery_class)
        if supply is None or flat is None or tod is None:
            return None
        standard += kwh * (supply + flat) / 100
        timed += kwh * (supply + tod) / 100
    return {"hourlyStandardCostUSD": round(standard, 6), "hourlyTODCostUSD": round(timed, 6)}


def augment(report, prices, start_minutes=START_MINUTES):
    """A copy of the replay with each night's 6 PM costs added under KEY."""
    assumptions = report["assumptions"]
    power, arrival = assumptions["powerKW"], assumptions["startMinutes"]
    result = json.loads(json.dumps(report))
    for night in result["nights"]:
        day = date.fromisoformat(night["date"])
        if night.get("hourlyStandardCostUSD") is not None:
            check = night_costs(day, night["energyKWh"], power, prices, arrival)
            if check is None or any(abs(check[k] - night[k]) > TOLERANCE_USD
                                    for k in ("hourlyStandardCostUSD", "hourlyTODCostUSD")):
                raise ValueError(f"{night['date']}: these prices don't reproduce the replay's hourly costs.")
        night[KEY] = night_costs(day, night["energyKWh"], power, prices, start_minutes)
    return result


def fetch_prices(first, last):
    """ComEd's real-time hourly prices for the Chicago dates first..last inclusive."""
    from urllib.parse import urlencode
    from .research.comed import TABLE_URL, Fetcher
    get, prices = Fetcher(), {}
    while first <= last:
        query = urlencode({"type": "pricingtabledual", "date": first.strftime("%Y%m%d")})
        prices.update(table_prices(get(f"{TABLE_URL}?{query}"), first))
        first += timedelta(days=1)
    return prices


def main(argv=None):
    import argparse
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("replay")
    parser.add_argument("out")
    args = parser.parse_args(argv)
    report = json.loads(Path(args.replay).read_text())
    prices = fetch_prices(date.fromisoformat(report["start"]), date.fromisoformat(report["end"]) + timedelta(days=1))
    result = augment(report, prices)
    Path(args.out).write_text(json.dumps(result, separators=(",", ":"), allow_nan=False))
    priced = [n[KEY] for n in result["nights"] if n[KEY] is not None]
    print(f"{len(priced)}/{len(result['nights'])} nights priced at 6 PM; arrival-time costs reproduced.")


if __name__ == "__main__":
    main()
