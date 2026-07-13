#!/bin/bash
# Resequenced 2026-07-12 (owner): hourly data sooner, QQQ dead last.
# 1) EOD for remaining basket tickers (resumable — done expirations skip)
# 2) hourly for the 8 basket tickers
# 3) QQQ EOD, then QQQ hourly (owner keeps the data, runs last)
set -u
EOD="XBI EEM EWZ TLT ARKK"
BASKET_HOURLY="GDX SLV XOP XBI EEM EWZ TLT ARKK"
for t in $EOD; do
  echo "=== EOD $t start $(date '+%F %T') ==="
  PYTHONPATH=. .venv/bin/python -m scripts.pull_spy_all \
      --symbol "$t" --out-dir data/options/all \
      --start-year 2017 --window-days 50 --strike-range 30 --workers 6
  PYTHONPATH=. .venv/bin/python -m scripts.pull_spy_all \
      --symbol "$t" --out-dir data/options/all --concat
done
echo "=== BASKET EOD DONE $(date '+%F %T') — starting hourly ==="
PYTHONPATH=. .venv/bin/python -m scripts.pull_intraday_basket \
    --symbols $BASKET_HOURLY --out-dir data/options/intraday \
    --years 7 --window-days 50 --strike-range 15 --interval 1h --workers 6
PYTHONPATH=. .venv/bin/python -m scripts.pull_intraday_basket \
    --symbols $BASKET_HOURLY --out-dir data/options/intraday --concat
echo "=== BASKET HOURLY DONE $(date '+%F %T') — QQQ dead last ==="
PYTHONPATH=. .venv/bin/python -m scripts.pull_spy_all \
    --symbol QQQ --out-dir data/options/all \
    --start-year 2017 --window-days 50 --strike-range 30 --workers 6
PYTHONPATH=. .venv/bin/python -m scripts.pull_spy_all \
    --symbol QQQ --out-dir data/options/all --concat
PYTHONPATH=. .venv/bin/python -m scripts.pull_intraday_basket \
    --symbols QQQ --out-dir data/options/intraday \
    --years 7 --window-days 50 --strike-range 15 --interval 1h --workers 6
PYTHONPATH=. .venv/bin/python -m scripts.pull_intraday_basket \
    --symbols QQQ --out-dir data/options/intraday --concat
echo "=== ALL DONE $(date '+%F %T') ==="
