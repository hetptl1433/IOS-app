#!/bin/bash
# SPARC on a Mac: double-click this file in Finder (or run ./start.command in Terminal).
# It opens SPARC in your browser. The first time, it also makes SPARC's own Python environment
# in .venv (so the Test models page can install AI models with one click) and loads the prices:
# from comed-data/ in seconds when that folder is there, or from ComEd in about 20 minutes.
cd "$(dirname "$0")" || exit 1

if [ ! -x .venv/bin/python ]; then
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
  echo "First start: making SPARC's own Python environment in .venv ..."
  if ! "$PY" -m venv .venv; then
    echo "Could not make the .venv folder. Check that you can write to this folder, then run this again."
    read -n 1 -s -r -p "Press any key to close."
    exit 1
  fi
fi
PY=.venv/bin/python

if [ ! -f runtime/sparc.db ]; then
  if ls comed-data/hourly/*.csv >/dev/null 2>&1; then
    "$PY" -m sparc load
  else
    echo "First start: downloading prices from 2024-01-01 to today (about 20 minutes)..."
    "$PY" -m sparc fetch 2024-01-01 "$(date +%Y-%m-%d)" || echo "The download stopped early. Run this again to resume."
  fi
fi

echo "SPARC is running at http://127.0.0.1:8765. Keep this window open while you use it; close it to stop SPARC."
exec "$PY" -m sparc serve
