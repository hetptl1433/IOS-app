"""Import ComEd hourly tables fetched by a Firecrawl browser-session script into the SPARC store.

Used once, with import_firecrawl.py, where ComEd could not be reached directly. Each saved result
holds stdout lines of {"day": "YYYYMMDD", "status": 200, "body": base64}; the bodies go through the
same parser as `python -m sparc fetch`.

    python experiments/data/import_tables.py DB RESULTS_DIR
"""
import base64
import json
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from sparc.comed import ComEdError, parse_hourly_table  # noqa: E402
from sparc.store import Store  # noqa: E402


def main(db, results_dir):
    store = Store(db)
    done = store.fetched("hourly")
    imported, rejected, failed = 0, [], []
    for path in sorted(Path(results_dir).glob("mcp-Firecrawl-firecrawl_interact-*.txt")):
        stdout = json.loads(path.read_text()).get("stdout", "")
        for line in stdout.splitlines():
            if not line.startswith("{"):
                continue
            record = json.loads(line)
            day = datetime.strptime(record["day"], "%Y%m%d").date()
            if record["status"] != 200:
                failed.append(f"{day} (HTTP {record['status']})")
                continue
            try:
                rows = parse_hourly_table(base64.b64decode(record["body"]), day)
            except ComEdError as exc:
                rejected.append(f"{day}: {exc}")
                continue
            store.add_hourly(rows)
            if day.isoformat() not in done:
                store.mark_fetched("hourly", day.isoformat())
                done.add(day.isoformat())
                imported += 1
    store.close()
    print(f"imported {imported} new days; {len(done)} days stored in total")
    for item in rejected:
        print("  rejected", item)
    for item in failed:
        print("  failed", item)


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
