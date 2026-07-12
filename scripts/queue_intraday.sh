#!/bin/bash
# Wait for the EOD greeks pull to finish, then run the hourly OHLC pull.
# Both hammer the same local Theta Terminal; running them concurrently starves both.
set -u
echo "waiting for EOD pull (pull_basket.sh) to finish... $(date '+%F %T')"
while pgrep -f "pull_basket.sh" > /dev/null; do sleep 60; done
echo "EOD pull done, starting hourly pull $(date '+%F %T')"
PYTHONPATH=. .venv/bin/python -m scripts.pull_intraday_basket \
    --out-dir data/options/intraday --years 7 --window-days 50 \
    --strike-range 15 --interval 1h --workers 6
echo "hourly pull done, concatenating $(date '+%F %T')"
PYTHONPATH=. .venv/bin/python -m scripts.pull_intraday_basket \
    --out-dir data/options/intraday --concat
echo "=== ALL DONE $(date '+%F %T') ==="
