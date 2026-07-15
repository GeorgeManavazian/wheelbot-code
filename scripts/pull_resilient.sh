#!/bin/bash
# Added 2026-07-15: single resilient pull, supersedes pull_new_tickers.sh +
# retry_timeouts.sh (both retired — they raced tickers through a down terminal).
#
# Two failure modes hit on 2026-07-14/15, both handled here:
#  (1) terminal fully down during a watchdog restart -> pull_spy_all raises on
#      is_up() and bash (set -u, no -e) skipped to the next ticker, pulling ZERO.
#      -> FIX: wait_healthy() gates before every ticker; convergence retry re-runs
#         a ticker (resumable, existing files skip) until its file count stops
#         growing, so a mid-ticker restart fills the gap instead of skipping.
#  (2) heavy gRPC path wedged while the light health probe still answered 200
#      (masked a 5h AAPL stall). -> handled by pull_watchdog2.sh (progress-based,
#      keys on file mtime, can't be fooled by a light probe). Run it alongside.
#
# workers=4 (terminal max concurrent is 4; the old --workers 6 oversubscribed).
set -u
cd /Users/georgiemanavazian/Documents/Trading/code/etf-bot
HOST=http://127.0.0.1:25503
EOD_DIR=data/options/all
INT_DIR=data/options/intraday
W=4

log(){ echo "[$(date '+%F %T')] $*"; }

wait_healthy(){
  local n=0
  until [ "$(curl -s --max-time 30 -o /dev/null -w '%{http_code}' \
              "$HOST/v3/option/list/expirations?symbol=SPY")" = "200" ]; do
    n=$((n+1)); log "terminal not healthy (wait $n) — sleeping 20s"; sleep 20
  done
}

# convergence pull: re-run until per-ticker file count stops growing (max 6 tries)
pull_eod(){
  local t=$1 last=-1 cur
  for a in 1 2 3 4 5 6; do
    wait_healthy
    PYTHONPATH=. .venv/bin/python -m scripts.pull_spy_all \
      --symbol "$t" --out-dir "$EOD_DIR" \
      --start-year 2017 --window-days 50 --strike-range 30 --workers $W --retry-timeouts || true
    cur=$(ls "$EOD_DIR"/${t}_*.parquet 2>/dev/null | wc -l | tr -d ' ')
    log "EOD $t try $a: $cur files (was $last)"
    [ "$cur" = "$last" ] && [ "$cur" -gt 0 ] && break
    last=$cur
  done
  wait_healthy
  PYTHONPATH=. .venv/bin/python -m scripts.pull_spy_all --symbol "$t" --out-dir "$EOD_DIR" --concat || true
  log "EOD $t DONE: $cur files"
}

pull_hourly(){
  local t=$1 last=-1 cur
  for a in 1 2 3 4 5 6; do
    wait_healthy
    PYTHONPATH=. .venv/bin/python -m scripts.pull_intraday_basket \
      --symbols "$t" --out-dir "$INT_DIR" \
      --years 7 --window-days 50 --strike-range 15 --interval 1h --workers $W --retry-timeouts || true
    cur=$(ls "$INT_DIR"/${t}_*.parquet 2>/dev/null | wc -l | tr -d ' ')
    log "hourly $t try $a: $cur files (was $last)"
    [ "$cur" = "$last" ] && [ "$cur" -gt 0 ] && break
    last=$cur
  done
  wait_healthy
  PYTHONPATH=. .venv/bin/python -m scripts.pull_intraday_basket --symbols "$t" --out-dir "$INT_DIR" --concat || true
  log "hourly $t DONE: $cur files"
}

log "=== RESILIENT PULL START ==="

# 1) new single-stock tickers (AAPL resumes from its 88 partial) + FB (Meta pre-2022)
for t in AAPL AMZN NVDA META FB; do pull_eod "$t"; done
for t in AAPL AMZN NVDA META FB; do pull_hourly "$t"; done

# 2) finish the SPY hourly the wedge killed (~2022-07); resumable, convergence fills it
pull_hourly SPY

# 3) EOD timeout-marker gaps left by the wedge (2025-03 + 2026-04 expiries)
for t in SPY XBI XOP; do pull_eod "$t"; done

# 4) straggler hourly concats (pulled earlier, never concatenated)
for t in XBI EEM EWZ TLT ARKK; do
  wait_healthy
  PYTHONPATH=. .venv/bin/python -m scripts.pull_intraday_basket --symbols "$t" --out-dir "$INT_DIR" --concat || true
done

# 5) QQQ dead last (owner sequencing 2026-07-12)
pull_eod QQQ
pull_hourly QQQ

log "=== leftover timeout markers (empty = clean) ==="
ls "$EOD_DIR"/*.timeout "$INT_DIR"/*.timeout 2>/dev/null || log "none"
log "=== RESILIENT PULL DONE ==="
