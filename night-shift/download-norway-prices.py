#!/usr/bin/env python3
"""Fetch and normalize the open, original-date Norwegian research price history.

Python 3.9+; standard library only. To rebuild without network access:
  python3 night-shift/download-norway-prices.py --source norway-price-data/source/prices.csv

The source has 24 rows on every local date, including clock-change dates. Those
dates are omitted rather than inventing a missing or duplicated hourly price.
"""

import argparse
import csv
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal, InvalidOperation
import hashlib
import io
import json
from pathlib import Path
import urllib.request
from zoneinfo import ZoneInfo


ROOT = Path(__file__).resolve().parents[1]
SOURCE_URL = "https://zenodo.org/api/records/8423312/files/prices.csv/content"
RECORD_URL = "https://zenodo.org/records/8423312"
PAPER_URL = "https://pmc.ncbi.nlm.nih.gov/articles/PMC10618698/"
SOURCE_MD5 = "ac85e79c173f690d4fb027941848269e"
OSLO = ZoneInfo("Europe/Oslo")
UTC = timezone.utc
AREAS = ("NO1", "NO3", "NO4", "NO5")
FIELDNAMES = ("area", "hour_utc", "hour_local", "price_nok_kwh", "published_assumed_utc")


def utc_iso(value):
    return value.astimezone(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def clock_change(day):
    """A local date has a transition if its next midnight changes UTC offset."""
    start = datetime.combine(day, time(), OSLO)
    end = datetime.combine(day + timedelta(days=1), time(), OSLO)
    return start.utcoffset() != end.utcoffset()


def normalize(raw, first_date, last_date):
    source_rows = list(csv.DictReader(io.StringIO(raw.decode("utf-8-sig"))))
    expected_columns = {"Price_area", "Date", "Hour", "Price_NOK_MWh", "Price_NOK_kWh"}
    if not source_rows or set(source_rows[0]) != expected_columns:
        raise ValueError("Unexpected source CSV schema")
    seen = set()
    daily_hours = {}
    result = []
    excluded_dates = {}
    source_dates = []
    raw_prices = []
    for row in source_rows:
        area = row["Price_area"]
        day = date.fromisoformat(row["Date"])
        hour = int(row["Hour"])
        if area not in {"NO1", "NO2", "NO3", "NO4", "NO5"} or not 1 <= hour <= 24:
            raise ValueError("Unexpected source area or hour")
        key = area, day, hour
        if key in seen:
            raise ValueError("Duplicate source area/date/hour: %r" % (key,))
        seen.add(key)
        daily_hours.setdefault((area, day), set()).add(hour)
        source_dates.append(day)
        try:
            nok_mwh = Decimal(row["Price_NOK_MWh"])
        except InvalidOperation as error:
            raise ValueError("Invalid source price") from error
        if not nok_mwh.is_finite():
            raise ValueError("Nonfinite source price")
        if area not in AREAS or day < first_date or day > last_date:
            continue
        if clock_change(day):
            excluded_dates[day.isoformat()] = (
                "Source normalizes every day to 24 hours: spring missing hour is averaged; "
                "autumn second repeated hour is omitted. Entire local transition date excluded."
            )
            continue
        # The source's 1..24 hour labels identify the successive hourly periods
        # of a local date; label 1 maps to its midnight-to-01:00 period.
        local = datetime.combine(day, time(hour=hour - 1), OSLO)
        publication = datetime.combine(day - timedelta(days=1), time(hour=13), OSLO)
        price = nok_mwh / Decimal(1000)
        raw_prices.append(price)
        result.append({
            "area": area,
            "hour_utc": utc_iso(local),
            "hour_local": local.isoformat(timespec="seconds"),
            "price_nok_kwh": format(price, "f"),
            "published_assumed_utc": utc_iso(publication),
        })
    if any(hours != set(range(1, 25)) for hours in daily_hours.values()):
        raise ValueError("Source has an incomplete local day")
    source_first, source_last = min(source_dates), max(source_dates)
    if first_date < source_first or last_date > source_last:
        raise ValueError("Requested dates extend beyond the published source history")
    # Fail if a source area/day is missing, instead of silently filling a gap.
    day = first_date
    while day <= last_date:
        for area in AREAS:
            if (area, day) not in daily_hours:
                raise ValueError("Missing source day: %s %s" % (area, day))
        day += timedelta(days=1)
    result.sort(key=lambda row: (row["area"], row["hour_utc"]))
    if not result:
        raise ValueError("No hourly prices selected")
    return result, excluded_dates, {
        "rowCount": len(source_rows),
        "firstDate": source_first.isoformat(),
        "lastDate": source_last.isoformat(),
        "areas": sorted({row["Price_area"] for row in source_rows}),
    }, raw_prices


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, help="Use the checksum-verified source CSV offline")
    parser.add_argument("--output-dir", type=Path, default=ROOT / "norway-price-data")
    parser.add_argument("--start-date", type=date.fromisoformat, default=date(2019, 7, 1))
    parser.add_argument("--end-date", type=date.fromisoformat, default=date(2021, 8, 7))
    args = parser.parse_args()
    if args.start_date > args.end_date:
        parser.error("start-date must be on or before end-date")
    if args.source:
        raw = args.source.read_bytes()
    else:
        request = urllib.request.Request(SOURCE_URL, headers={"User-Agent": "SPARC-Norway-history/1.0"})
        with urllib.request.urlopen(request, timeout=90) as response:
            raw = response.read()
    if hashlib.md5(raw).hexdigest() != SOURCE_MD5:
        raise ValueError("Downloaded prices.csv does not match the pinned Zenodo record checksum")
    rows, excluded_dates, source_coverage, prices = normalize(raw, args.start_date, args.end_date)
    output_dir = args.output_dir
    source_dir = output_dir / "source"
    source_dir.mkdir(parents=True, exist_ok=True)
    source_path = source_dir / "prices.csv"
    source_path.write_bytes(raw)
    hourly_path = output_dir / "hourly.csv"
    with hourly_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDNAMES, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
    provenance = {
        "title": "Norwegian historical hourly day-ahead spot prices for Dataset 1 replay",
        "dataset": "Norwegian hourly residential electricity demand data with consumer characteristics during the European energy crisis",
        "authors": ["Matthias Hofmann", "Sigurd Bjarghov", "Stian Nessa"],
        "doi": "10.5281/zenodo.8423312",
        "license": "CC BY 4.0",
        "licenseUrl": "https://creativecommons.org/licenses/by/4.0/",
        "licenseEvidence": "Zenodo record metadata: metadata.license.id = cc-by-4.0",
        "sources": [
            {"label": "Published research dataset, Zenodo record 8423312", "url": RECORD_URL},
            {"label": "Original prices.csv download", "url": SOURCE_URL},
            {"label": "Data article, sections 3.2.6 and 4.2", "url": PAPER_URL},
            {"label": "Machine-readable dataset metadata and license", "url": "https://zenodo.org/api/records/8423312"},
        ],
        "attribution": "Hofmann, Matthias; Bjarghov, Sigurd; Nessa, Stian (2023). Norwegian hourly residential electricity demand data with consumer characteristics during the European energy crisis (v3). Zenodo. https://doi.org/10.5281/zenodo.8423312. CC BY 4.0. Normalized for SPARC; DST transition dates omitted.",
        "costScope": "Historical day-ahead spot energy prices as provided by the research dataset, in NOK/kWh. No household grid fees, supplier markup, taxes, VAT adjustments, subsidies or fixed charges are added. These are a spot energy comparison, not a reconstruction of household bills.",
        "currency": "NOK",
        "unit": "NOK/kWh",
        "timezone": "Europe/Oslo",
        "sourceFields": ["Price_area", "Date", "Hour", "Price_NOK_MWh", "Price_NOK_kWh"],
        "priceConversion": "Price_NOK_MWh / 1000. The source Price_NOK_kWh field is rounded to two decimal places and is not used.",
        "hourInterpretation": "Source Hour labels 1..24 identify the successive hourly periods of the local date; Hour 1 is interpreted as 00:00–01:00. Entire clock-change dates are omitted.",
        "publishedAssumption": {
            "localTime": "13:00 Europe/Oslo on the previous local date",
            "description": "Model assumption for when the next-day price curve can be used; the source CSV has no historical publication timestamps. It is not an observed publication-time series.",
            "field": "published_assumed_utc",
        },
        "coverage": {"firstDate": args.start_date.isoformat(), "lastDate": args.end_date.isoformat(), "areas": list(AREAS), "rows": len(rows)},
        "sourceCoverage": source_coverage,
        "excludedDates": excluded_dates,
        "sourceChecksum": {"md5": SOURCE_MD5, "sha256": hashlib.sha256(raw).hexdigest()},
        "normalizedChecksum": {"sha256": hashlib.sha256(hourly_path.read_bytes()).hexdigest()},
        "sourceBytes": len(raw),
        "normalizedPriceRange": {"minimumNokKwh": float(min(prices)), "maximumNokKwh": float(max(prices))},
        "qualityPolicy": "Preserve negative spot prices. Reject missing/nonfinite prices, duplicate area/date/hour keys and incomplete days. No interpolation, date remapping, foreign prices or fallback price series are used.",
    }
    (output_dir / "provenance.json").write_text(json.dumps(provenance, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print("Wrote %s: %s hourly prices for %s..%s; excluded %s DST dates." % (hourly_path, len(rows), args.start_date, args.end_date, len(excluded_dates)))


if __name__ == "__main__":
    main()
