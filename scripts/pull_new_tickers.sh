#!/bin/bash
# Added 2026-07-14: EOD greeks + hourly OHLC for AAPL AMZN NVDA META (owner request).
# These four are OUTSIDE the pre-registered basket (2026-07-12 spec) — data pull only;
# any backtest on them before the basket run is exploratory and must be labeled as such.
# Sequencing: waits for the in-flight SPY hourly pull (concurrent pulls starve the
# terminal), then new tickers, then QQQ dead last (owner resequencing decision 2026-07-12).
set -u
NEW="AAPL AMZN NVDA META"

echo "waiting for in-flight intraday pull to finish... $(date '+%F %T')"
while pgrep -f "scripts.pull_intraday_basket" > /dev/null; do sleep 60; done

echo "=== SPY hourly concat $(date '+%F %T') ==="
PYTHONPATH=. .venv/bin/python -m scripts.pull_intraday_basket \
    --symbols SPY --out-dir data/options/intraday --concat
echo "=== straggler hourly concat (resumable-skipped tickers) $(date '+%F %T') ==="
PYTHONPATH=. .venv/bin/python -m scripts.pull_intraday_basket \
    --symbols XBI EEM EWZ TLT ARKK --out-dir data/options/intraday --concat

for t in $NEW; do
  echo "=== EOD $t start $(date '+%F %T') ==="
  PYTHONPATH=. .venv/bin/python -m scripts.pull_spy_all \
      --symbol "$t" --out-dir data/options/all \
      --start-year 2017 --window-days 50 --strike-range 30 --workers 6
  PYTHONPATH=. .venv/bin/python -m scripts.pull_spy_all \
      --symbol "$t" --out-dir data/options/all --concat
  echo "=== EOD $t done $(date '+%F %T') ==="
done

echo "=== NEW EOD DONE $(date '+%F %T') — starting new-ticker hourly ==="
PYTHONPATH=. .venv/bin/python -m scripts.pull_intraday_basket \
    --symbols $NEW --out-dir data/options/intraday \
    --years 7 --window-days 50 --strike-range 15 --interval 1h --workers 6
PYTHONPATH=. .venv/bin/python -m scripts.pull_intraday_basket \
    --symbols $NEW --out-dir data/options/intraday --concat

echo "=== NEW HOURLY DONE $(date '+%F %T') — QQQ dead last (owner sequencing) ==="
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
