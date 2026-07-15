#!/bin/bash
# Added 2026-07-14: waits for pull_new_tickers.sh to finish, then:
# 1) FB 2017-2022 chains — Meta traded under root FB until 2022-06; the META root
#    only starts 2021-07 (verified against terminal), so 2017+ history needs FB.
#    Separate root, separate parquet; FB/META stitching is an owner decision.
# 2) retries every timeout-marked window (terminal wedge ~19:23-20:21 left
#    .timeout markers that plain resume skips) — EOD gaps SPY/XBI/XOP + intraday.
# 3) SPY hourly completion: the wedge killed it at ~2022-07; the retry run
#    enumerates all expiries, skips existing files, re-concats.
set -u
echo "waiting for pull_new_tickers.sh chain... $(date '+%F %T')"
while pgrep -f "pull_new_tickers.sh" > /dev/null; do sleep 60; done

echo "=== FB (pre-rename Meta) EOD $(date '+%F %T') ==="
PYTHONPATH=. .venv/bin/python -m scripts.pull_spy_all \
    --symbol FB --out-dir data/options/all \
    --start-year 2017 --window-days 50 --strike-range 30 --workers 4
PYTHONPATH=. .venv/bin/python -m scripts.pull_spy_all \
    --symbol FB --out-dir data/options/all --concat
echo "=== FB hourly $(date '+%F %T') ==="
PYTHONPATH=. .venv/bin/python -m scripts.pull_intraday_basket \
    --symbols FB --out-dir data/options/intraday \
    --years 7 --window-days 50 --strike-range 15 --interval 1h --workers 4
PYTHONPATH=. .venv/bin/python -m scripts.pull_intraday_basket \
    --symbols FB --out-dir data/options/intraday --concat

echo "=== EOD timeout retries $(date '+%F %T') ==="
for t in SPY XBI XOP; do
  PYTHONPATH=. .venv/bin/python -m scripts.pull_spy_all \
      --symbol "$t" --out-dir data/options/all \
      --start-year 2017 --window-days 50 --strike-range 30 --workers 4 \
      --retry-timeouts
  PYTHONPATH=. .venv/bin/python -m scripts.pull_spy_all \
      --symbol "$t" --out-dir data/options/all --concat
done

echo "=== intraday timeout retries $(date '+%F %T') ==="
ALL="SPY GDX SLV XOP XBI EEM EWZ TLT ARKK QQQ AAPL AMZN NVDA META"
PYTHONPATH=. .venv/bin/python -m scripts.pull_intraday_basket \
    --symbols $ALL --out-dir data/options/intraday \
    --years 7 --window-days 50 --strike-range 15 --interval 1h --workers 4 \
    --retry-timeouts
PYTHONPATH=. .venv/bin/python -m scripts.pull_intraday_basket \
    --symbols $ALL --out-dir data/options/intraday --concat

echo "=== leftover markers (should be empty or genuinely dead) ==="
ls data/options/all/*.timeout data/options/intraday/*.timeout 2>/dev/null
echo "=== RETRY PASS DONE $(date '+%F %T') ==="
