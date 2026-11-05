"""Bundle original Norway sessions with open, historical Norwegian spot prices."""
from __future__ import annotations

import csv
import hashlib
import importlib.util
import json
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
AREAS = {"ASK": "NO1", "BAR": "NO1", "BAR_2": "NO1", "KRO": "NO1",
         "OSL_1": "NO1", "OSL_2": "NO1", "OSL_S": "NO1", "OSL_T": "NO1",
         "BER": "NO5", "BOD": "NO4", "TRO": "NO3", "TRO_R": "NO3"}
LABELS = {"NO1": "NO1 · Eastern Norway", "NO3": "NO3 · Central Norway",
          "NO4": "NO4 · Northern Norway", "NO5": "NO5 · Western Norway"}
LOCATIONS = {"ASK": "Asker", "BAR": "Bærum", "BAR_2": "Bærum · site 2", "BER": "Bergen",
             "BOD": "Bodø", "KRO": "Krokkleiva", "OSL_1": "Oslo · site 1", "OSL_2": "Oslo · site 2",
             "OSL_S": "Oslo southeast", "OSL_T": "Oslo Tveita", "TRO": "Trondheim", "TRO_R": "Trondheim Risvollan"}


def epoch(value):
    return int(datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp())


def build(root=ROOT):
    spec = importlib.util.spec_from_file_location("residential_import", root / "night-shift/build-residential-data.py")
    imported = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(imported)
    residential = imported.prepare(root)
    rows = []
    for values in residential["rows"]:
        row = dict(zip(residential["columns"], values))
        if row["site"] not in AREAS:
            raise ValueError(f"No verified Norwegian price-area mapping: {row['site']}")
        row.update(area=AREAS[row["site"]], start=row["sourceStart"], end=row["sourceEnd"])
        rows.append(row)

    source = root / "norway-price-data/hourly.csv"
    provenance = json.loads((root / "norway-price-data/provenance.json").read_text())
    prices = {area: [] for area in LABELS}
    seen = set()
    with source.open(newline="") as stream:
        for item in csv.DictReader(stream):
            area, hour = item["area"], epoch(item["hour_utc"])
            if area not in prices:
                continue
            key = area, hour
            if key in seen:
                raise ValueError(f"Duplicate Norwegian price hour: {key}")
            seen.add(key)
            prices[area].append([hour, float(item["price_nok_kwh"]), epoch(item["published_assumed_utc"])])
    for area in prices:
        prices[area].sort()
        if not prices[area]:
            raise ValueError(f"No historical prices for {area}")
    price_first = min(datetime.fromtimestamp(values[0][0], timezone.utc).isoformat() for values in prices.values())
    coverage = provenance["coverage"]
    omitted = len(provenance.get("excludedDates", {}))
    provenance.update({
        "costScope": "Historical Norwegian wholesale spot energy in NOK. No grid fees, VAT, taxes, retailer markup or subsidies are added.",
        "priceCoverage": f"Original-date spot prices from {coverage['firstDate']} through {coverage['lastDate']}. Earlier charging sessions and missing-price windows are excluded. {omitted} daylight-saving transition dates are omitted because the source normalizes them to 24 hours.",
        "normalizedPriceSha256": hashlib.sha256(source.read_bytes()).hexdigest(),
        "chargingSources": residential["sources"],
        "chargingAttribution": "Åse Lekang Sørensen (2024). Data files: Electric vehicle charging dataset with 35,000 charging sessions from 12 residential locations in Norway. CC BY 4.0. Charging reports normalized and combined with historical price data for SPARC.",
        "areaMapping": AREAS,
        "areaMappingSource": "https://temakart.nve.no/tema/nettanlegg",
        "firstPriceUtc": price_first,
    })
    sources = provenance.get("sources", [])
    if not any(source.get("url") == "https://temakart.nve.no/tema/nettanlegg" for source in sources):
        sources.append({"label": "NVE electricity price-area map", "url": "https://temakart.nve.no/tema/nettanlegg"})
    provenance["sources"] = sources
    sources.extend([
        {"label": "Sørensen charging-session dataset · CC BY 4.0", "url": "https://zenodo.org/records/12730566"},
        {"label": "Charging-session methodology", "url": residential["methodology"]},
    ])
    return {"version": 1, "dataSource": "Norway Dataset1", "timezone": "Europe/Oslo", "currency": "NOK",
        "costScope": provenance["costScope"], "rows": rows, "prices": prices,
        "areas": [{"id": area, "label": label} for area, label in LABELS.items()],
        "locations": [{"id": site, "label": f"{label} ({site})"} for site, label in LOCATIONS.items()],
        "coverage": {"firstDate": rows[0]["startLocal"][:10], "lastDate": rows[-1]["startLocal"][:10]},
        "priceCoverage": provenance.get("coverage"), "provenance": provenance,
        "generatedAt": datetime.now(timezone.utc).isoformat()}


def main():
    data = build()
    target = ROOT / "night-shift/dist/norway-local-data.js"
    target.write_text("window.SPARC_NORWAY_LOCAL_DATA=" + json.dumps(data, ensure_ascii=True,
        allow_nan=False, separators=(",", ":")) + ";\n")
    print(f"Bundled {len(data['rows']):,} original sessions and {sum(map(len, data['prices'].values())):,} Norwegian area-price hours.")


if __name__ == "__main__":
    main()
