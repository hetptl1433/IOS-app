"""Import ComEd 5-minute prices fetched through Firecrawl (format=text) into the SPARC store.

Used once, where ComEd's API could not be reached directly: the same API URLs that
`python -m sparc fetch` requests were fetched through the Firecrawl scraping service, and this
reads the saved responses. It keeps responses whose source URL is the 5-minute feed with a date
range, and checks every label against the range asked for before importing.

    python experiments/data/import_firecrawl.py DB RESULTS_DIR
"""
import json
import re
import sys
from datetime import datetime
from pathlib import Path
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from sparc.clock import CT, SLOT, local  # noqa: E402
from sparc.store import Store  # noqa: E402

PAIR = re.compile(r"(\d{13}):(-?\d+(?:\.\d+)?)")


def main(db, results_dir):
    store = Store(db)
    seen = set()
    for path in sorted(Path(results_dir).glob("mcp-Firecrawl-firecrawl_scrape-*.txt")):
        try:
            result = json.loads(path.read_text())
        except ValueError:
            continue
        url = (result.get("metadata") or {}).get("sourceURL", "")
        query = {k: v[0] for k, v in parse_qs(urlparse(url).query).items()}
        if query.get("type") != "5minutefeed" or "datestart" not in query:
            continue
        if url in seen:
            continue
        seen.add(url)
        first = int(datetime.strptime(query["datestart"], "%Y%m%d%H%M").replace(tzinfo=CT).timestamp())
        last = int(datetime.strptime(query["dateend"], "%Y%m%d%H%M").replace(tzinfo=CT).timestamp())
        prices = {}
        for millis, price in PAIR.findall(result["rawHtml"]):
            millis = int(millis)
            if millis % (SLOT * 1000):
                raise SystemExit(f"{path.name}: off-grid label {millis}")
            start, value = millis // 1000, float(price)
            if prices.setdefault(start, value) != value:
                raise SystemExit(f"{path.name}: two prices for {local(start)}")
        outside = [t for t in prices if not first <= t <= last]
        expected = (last - first) // SLOT + 1
        status = (result.get("metadata") or {}).get("statusCode")
        warning = result.get("warning")
        print(f"{query['datestart'][:8]}-{query['dateend'][:8]}: {len(prices):5d}/{expected} intervals"
              f" ({100 * len(prices) / expected:5.1f}%), HTTP {status}"
              + (f", {len(outside)} outside range" if outside else "")
              + (f"\n    warning: {warning}" if warning else ""))
        store.add_five_minute(sorted(prices.items()))
    store.close()


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
