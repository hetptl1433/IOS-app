"""Build the offline ComEd bill-cost inputs from sparc-lite's unmodified CSVs.

Usage: python3 night-shift/build-comed-cost-data.py [--source /path/to/sparc-lite]
Five-minute prices are END stamped. Only twelve distinct, finite readings make
a billable hour. Calendar dates and forecast releases use America/Chicago.
"""
from __future__ import annotations

import argparse
import calendar
import csv
import hashlib
import json
import math
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
CT = ZoneInfo("America/Chicago")
FILES = ("car0001_zip60025_tesla-X-LR-2022.csv", "comed-electricity-cost-5min.csv",
         "comed-electricity-cost-monthly.csv", "comed-rate-components.csv")
RATE_FIELDS = ("fixed_price", "pea", "transmission", "distribution_facilities",
               "il_electricity_distribution", "renewable_portfolio_standard",
               "carbon_free_resource_adj", "zero_emission_standard", "energy_efficiency",
               "environmental_cost_recovery", "energy_transition_assistance", "coal_to_solar",
               "capacity_rate_usd_kw_month", "customer_charge_usd_month",
               "metering_charge_usd_month", "low_income_discount_recovery_usd_month")
STATE_TAX_TIERS = [[2000, .330], [48000, .319], [50000, .303], [400000, .297], [500000, .286],
                   [2000000, .270], [2000000, .254], [5000000, .233], [10000000, .207], [None, .202]]
GLENVIEW_CENTS = [.556, .366, .346, .345, .340, .338, .294, .206, .144, .082]
RIDER_TAX_SOURCE = "https://www.comed.com/cdn/assets/v3/assets/blt3ebb3fed6084be2a/bltb8485cefee950e7c/67b4f3aef14e110d1e948678/18_Rider_TAX.pdf?branch=prod_alias"
GLENVIEW_2026_SOURCE = "https://www.comed.com/cdn/assets/v3/assets/blt3ebb3fed6084be2a/blt86ebee5fe6ed02f8/6ad911e1630c34d7f475f5cd/2026_Ratebook.pdf#page=404"


def number(value):
    try:
        result = float(value)
        return result if math.isfinite(result) else None
    except (ValueError, TypeError):
        return None


def build(source: Path):
    rows = []
    with (source / FILES[0]).open(newline="") as stream:
        for r in csv.DictReader(stream):
            value = number(r["home_kwh"])
            if value is None or value < 0:
                raise ValueError("Daily home energy must be finite and nonnegative")
            datetime.strptime(r["date"], "%Y-%m-%d")
            rows.append([r["date"], value])
    rows.sort()
    if len(set(r[0] for r in rows)) != len(rows):
        raise ValueError("Duplicate daily charging dates")
    components = []
    with (source / FILES[3]).open(newline="") as stream:
        for r in csv.DictReader(line for line in stream if not line.startswith("#")):
            components.append(r)
    rates = {}
    with (source / FILES[2]).open(newline="") as stream:
        for r in csv.DictReader(stream):
            item = {key: number(r[key]) for key in RATE_FIELDS}
            if any(v is None for v in item.values()):
                raise ValueError(f"Invalid monthly rate {r['month']}")
            item["notes"] = r["notes"]
            item["calendarDays"] = calendar.monthrange(*map(int, r["month"].split("-")))[1]
            provenance = {}
            for component in sorted(set(c["component"] for c in components)):
                if component.startswith("chicago_"):
                    continue
                changes = sorted((c for c in components if c["component"] == component), key=lambda c: c["effective_month"])
                applicable = [c for c in changes if c["effective_month"] <= r["month"]]
                c = (applicable or changes)[-1 if applicable else 0]
                provenance[component] = {k: c[k] for k in ("effective_month", "status", "source", "unit")}
                if not applicable:
                    provenance[component]["backfilled"] = True
            item["provenance"] = provenance
            rates[r["month"]] = item
    buckets = defaultdict(list)
    with (source / FILES[1]).open(newline="") as stream:
        for r in csv.DictReader(stream):
            stamp = int(r["millisUTC"])
            if stamp % 300000:
                raise ValueError("Price timestamp is not on five-minute boundary")
            hour = ((stamp - 1) // 3600000) * 3600
            buckets[hour].append((stamp, number(r["hourly_price"]), number(r["hourly_price_forecast"])))
    hours = []
    complete, coherent = 0, 0
    for epoch, entries in sorted(buckets.items()):
        wall = datetime.fromtimestamp(epoch, CT)
        valid = len(entries) == 12 and len(set(e[0] for e in entries)) == 12 and all(e[1] is not None for e in entries)
        forecasts = set(e[2] for e in entries if e[2] is not None)
        forecast = next(iter(forecasts)) if len(forecasts) == 1 and all(e[2] is not None for e in entries) else None
        billed = round(sum(e[1] for e in entries) / 12, 10) if valid else None
        release = datetime.combine(wall.date() - timedelta(days=1), datetime.min.time()).replace(hour=17, tzinfo=CT)
        hours.append([epoch, wall.date().isoformat(), wall.hour, forecast, billed,
                      sum(e[1] is not None for e in entries), int(release.timestamp())])
        complete += valid
        coherent += forecast is not None
    return {
        "version": 1, "timezone": "America/Chicago", "rows": rows, "hours": hours, "rates": rates,
        "source": {
            "directory": "sparc-lite", "trace": FILES[0], "priceFile": FILES[1],
            "monthlyFile": FILES[2], "componentsFile": FILES[3],
            "sha256": {name: hashlib.sha256((source / name).read_bytes()).hexdigest() for name in FILES},
            "traceDays": len(rows), "chargingDays": sum(r[1] > 0 for r in rows),
            "homeKwh": round(sum(r[1] for r in rows), 6),
            "priceHours": len(hours), "completePriceHours": complete, "coherentForecastHours": coherent,
            "taxSource": "https://www.ilga.gov/commission/jcar/admincode/086/086005110A01100R.html",
            "taxSources": {
                "state": {"id": "illinois-residential-excise", "source": "Illinois Administrative Code §511.110",
                          "url": "https://www.ilga.gov/commission/jcar/admincode/086/086005110A01100R.html",
                          "revenueDepartmentUrl": "https://tax.illinois.gov/research/taxrates/excise.html",
                          "riderTaxUrl": RIDER_TAX_SOURCE, "basis": "monthly household kWh", "status": "verified",
                          "tiers": [{"kwh": width, "centsPerKwh": cents} for width, cents in STATE_TAX_TIERS]},
                "planComparison": {"source": "ComEd Hourly Pricing Billing FAQs", "url": "https://hourlypricing.comed.com/faqs/",
                                   "statement": "Residential taxes and fees use the same rates on fixed and hourly supply; supply, transmission and PEA differ.", "status": "verified"},
                "municipal": {"id": "glenview-electricity-use-tax", "source": "ComEd Rider TAX, Glenview municipal tax additions, Sheet 261",
                              "url": GLENVIEW_2026_SOURCE, "rider2025SourceUrl": RIDER_TAX_SOURCE,
                              "earlierSourceUrl": "https://www.icc.illinois.gov/downloads/public/filing/4/350172.pdf",
                              "basis": "monthly household kWh", "effectiveFrom": "2024-01-01", "verifiedThrough": "2026-11-05",
                              "latestSheetEffectiveFrom": "2026-10-01", "checkedThrough": "2026-11-05",
                              "tiers": [{"kwh": width, "centsPerKwh": GLENVIEW_CENTS[i]} for i, (width, _) in enumerate(STATE_TAX_TIERS)],
                              "collectionAdjustmentIncluded": True,
                              "note": "Same schedule on both supply plans; dates outside the verified source range are historical assumptions, not confirmed rate history."},
            },
            "billingSource": "https://hourlypricing.comed.com/faqs/",
            "capacitySource": "https://hourlypricing.comed.com/capacity-charge-updates/",
        },
        "limitations": [
            "Estimated EV costs using supplied rates, not a reconstruction of actual utility bills.",
            "Daily home_kwh has no connection timestamps; charging availability and power are assumptions. No extra charging loss is added.",
            "Hourly-plan transmission uses the supplied fixed-plan rate as a proxy; hourly-specific PEA and miscellaneous procurement charges are unavailable and omitted.",
            "Historical rates include backfills, placeholders and assumptions; monthly notes and component provenance are retained.",
            "The address is inside Glenview. Municipal electricity tax defaults to ComEd Rider TAX's Glenview usage tiers, already including its collection adjustment. The verified 2024, 2025 and October 2026 filings agree; pre-2024 dates carry that schedule as a flagged historical assumption. Separate flat municipal overrides are user assumptions, not verified tariffs. Franchise adjustments remain omitted.",
            "Monthly service and hourly capacity charges are account costs, allocated to EVs by EV kWh / (other household kWh + EV kWh). Capacity obligation is assumed, not inferred from this trace.",
            "Calendar months stand in for unknown billing-cycle boundaries. Selected calendar days, including zero-charge days, prorate account fees and other household energy; a final overnight spill day is included when needed.",
            "State tax uses monthly incremental Illinois excise tax T(other household kWh + EV kWh) - T(other household kWh), with the verified 2,000 kWh first tier.",
            "ComEd confirms residential taxes and fees use the same rates for fixed and hourly supply; each plan is dispatched explicitly to the applicable tax rule. Per-kWh excise tax is based on energy, not the dollar supply price, so cheaper or negative supply does not lower the tax rate.",
            "For partial-month selections, household usage is prorated while statutory monthly tax tiers remain whole; unknown usage outside the selection and actual billing cycles can change the tax estimate.",
            "Day-ahead forecasts are assumed released at 5 p.m. Chicago time on the prior date; unreleased hours use prior-day same-hour forecasts until publication. Actual prices are used only for billing.",
        ],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=ROOT.parents[1] / "sparc-lite")
    args = parser.parse_args()
    data = build(args.source)
    target = ROOT / "night-shift/dist/comed-cost-data.js"
    target.write_text("window.SPARC_COMED_COST_DATA=" + json.dumps(data, separators=(",", ":"), allow_nan=False) + ";\n")
    print(f"Bundled {len(data['rows'])} daily rows; {data['source']['completePriceHours']} complete billable hours; {len(data['rates'])} monthly rates.")


if __name__ == "__main__":
    main()
