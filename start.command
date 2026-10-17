#!/bin/bash
# SPARC on a Mac: double-click this file in Finder (or run ./start.command in Terminal).
# The first time, it downloads ComEd prices and Chicago weather into runtime/sparc.db (about
# 20 minutes; run it again to resume if it stops). Then it opens the web app in your browser.
cd "$(dirname "$0")" || exit 1

PY=""
for candidate in python3.13 python3.12 python3.11 python3; do
  if command -v "$candidate" >/dev/null 2>&1 && "$candidate" -c 'import sys; sys.exit(sys.version_info < (3, 11))'; then
    PY="$candidate"
    break
  fi
done
if [ -z "$PY" ]; then
  echo "SPARC needs Python 3.11 or newer."
  echo "Install it from https://www.python.org/downloads/ (or: brew install python@3.12), then run this again."
  read -n 1 -s -r -p "Press any key to close."
  exit 1
fi

if [ ! -f runtime/sparc.db ]; then
  echo "First run: downloading prices from 2024-01-01 to today (about 20 minutes)..."
  "$PY" -m sparc fetch 2024-01-01 "$(date +%Y-%m-%d)" || echo "The download stopped early. Run this again to resume."
fi

echo "SPARC is running at http://127.0.0.1:8765. Close this window or press Ctrl+C to stop."
exec "$PY" -m sparc serve
