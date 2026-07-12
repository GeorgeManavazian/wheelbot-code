#!/bin/bash
# Sequential per-ticker bulk pull of option greeks/eod chains (2017+) before the
# ThetaData STANDARD subscription lapses ~2026-07-25.
set -u
TICKERS="GDX SLV XOP XBI EEM EWZ TLT ARKK QQQ"
for t in $TICKERS; do
  echo "=== $t start $(date '+%F %T') ==="
  PYTHONPATH=. .venv/bin/python -m scripts.pull_spy_all \
      --symbol "$t" --out-dir data/options/all \
      --start-year 2017 --window-days 50 --strike-range 30 --workers 6
  PYTHONPATH=. .venv/bin/python -m scripts.pull_spy_all \
      --symbol "$t" --out-dir data/options/all --concat
  echo "=== $t done $(date '+%F %T') ==="
done
echo "=== ALL DONE $(date '+%F %T') ==="
