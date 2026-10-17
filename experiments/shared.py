"""What the experiment scripts share: the repository on the import path, and where the data is.

The scripts read prices from $SPARC_DATA/sparc.db, or runtime/sparc.db in the repository, the
database `python -m sparc fetch` fills. Backtest runs they save go to <data>/experiment-runs,
apart from the web app's own runs.
"""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

DATA = Path(os.environ.get("SPARC_DATA", ROOT / "runtime"))
RUNS = DATA / "experiment-runs"
# The tiers run (2025-04-02 to 2026-10-14) behind the README's headline numbers.
TIERS_RUN = ROOT / "experiments" / "scenarios" / "results" / "runs" / "20261016-214142-381e.json"


def database() -> Path:
    """The price database, or a pointer to how to build it."""
    db = DATA / "sparc.db"
    if not db.exists():
        raise SystemExit(f"No price database at {db}. Build it first: python -m sparc fetch 2024-01-01 2026-10-16 "
                         "(see experiments/data/README.md).")
    return db
