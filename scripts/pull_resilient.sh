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

# CLOCK GATE. During US market hours (09:30-16:00 ET) ThetaData throttles bulk
# history ~40x — SPY greeks 39.8s vs <1s off-hours, pre-split AMZN 2020 chains
# exceed 90s (unpullable), and hammering it there just wedges the terminal (three
# false-unreachable watchdog restarts, zero progress). A probe-based gate leaked
# on brief fast blips. This is deterministic: park the pull 09:00-16:30 ET on
# weekdays (a buffer around the 09:30-16:00 session), no probing that could itself
# saturate the terminal. Then, off-hours, confirm the DATA path actually returns
# before pulling (blocks on a genuine wedge, tolerating off-hours slowness to 60s).
in_market_hours(){
  local dow hm
  dow=$(TZ=America/New_York date +%u)                 # 1=Mon .. 7=Sun
  hm=$((10#$(TZ=America/New_York date +%H%M)))         # HHMM as decimal (10# avoids octal)
  [ "$dow" -le 5 ] && [ "$hm" -ge 900 ] && [ "$hm" -le 1630 ]
}
wait_healthy(){
  local n=0
  while in_market_hours; do
    n=$((n+1))
    [ $((n % 5)) -eq 1 ] && log "market hours (ET) — pull parked; rechecking every 5m"
    sleep 300
  done
  # off-hours: make sure the data path genuinely responds before pulling
  until curl -s --max-time 60 \
          "$HOST/v3/option/history/greeks/eod?symbol=SPY&expiration=20240119&start_date=20240116&end_date=20240119&strike_range=2" \
          2>/dev/null | grep -q "[0-9]"; do
    log "off-hours but data path not responding — terminal wedged? sleeping 30s"
    sleep 30
  done
}

# expiration universe for a ticker (year>=2017, <= today) — the completeness target.
# CACHED to a file: the count is historical (expirations + today) so it's stable
# for the run, and the list call can transiently fail under pull load / market-hours
# contention. A cached-once good value avoids a poisonous 0 (which would make every
# ticker look permanently incomplete and spin the outer loop forever). On a cache
# miss we retry the list a few times before giving up with 0 for this call only
# (never cached) — the next call retries.
universe(){
  local t=$1 cache="$EOD_DIR/.universe_$t" n i
  if [ -s "$cache" ]; then cat "$cache"; return; fi
  for i in 1 2 3 4 5; do
    n=$(PYTHONPATH=. .venv/bin/python - "$t" <<'PY' 2>/dev/null
import sys, pandas as pd
from src.engine_v2.options.theta_client import ThetaClient
d = pd.Timestamp.today().normalize()
try:
    e = {x for x in ThetaClient(timeout=60).list_expirations(sys.argv[1])
         if x.year >= 2017 and x <= d}
    print(len(e))
except Exception:
    print(0)
PY
)
    if [ "${n:-0}" -gt 0 ]; then echo "$n" > "$cache"; echo "$n"; return; fi
    sleep 10
  done
  echo 0   # transient failure this call only; not cached, next call retries
}

# How much of a ticker's EOD universe we already hold (parquet + genuinely-empty).
eod_have(){
  local t=$1 cur emp
  cur=$(ls "$EOD_DIR"/${t}_*.parquet 2>/dev/null | wc -l | tr -d ' ')
  emp=$(ls "$EOD_DIR"/${t}_*.empty 2>/dev/null | wc -l | tr -d ' ')
  echo $((cur + emp))
}
# Complete = we hold the whole expiration universe. Returns 0/1 (shell truth).
eod_is_complete(){
  local t=$1 tgt; tgt=$(universe "$t"); tgt=${tgt:-0}
  [ "$tgt" -gt 0 ] && [ "$(eod_have "$t")" -ge "$tgt" ]
}
# One INVOCATION = up to 8 off-peak-gated passes. Concats + returns 0 only when the
# ticker is genuinely complete against its universe; otherwise returns 1 and the
# OUTER loop revisits it later. Nothing is ever declared done below its universe,
# and a partial is never concatenated — that was the AMZN-121/544 corruption.
pull_eod(){
  local t=$1 tgt have
  tgt=$(universe "$t"); tgt=${tgt:-0}
  for a in $(seq 1 8); do
    eod_is_complete "$t" && break
    wait_healthy
    PYTHONPATH=. .venv/bin/python -m scripts.pull_spy_all \
      --symbol "$t" --out-dir "$EOD_DIR" \
      --start-year 2017 --window-days 50 --strike-range 30 --workers $W --retry-timeouts || true
    have=$(eod_have "$t")
    log "EOD $t pass $a: $have / target $tgt"
  done
  if eod_is_complete "$t"; then
    wait_healthy
    PYTHONPATH=. .venv/bin/python -m scripts.pull_spy_all --symbol "$t" --out-dir "$EOD_DIR" --concat || true
    log "EOD $t COMPLETE: $(eod_have "$t")/$tgt"
    return 0
  fi
  log "EOD $t still incomplete: $(eod_have "$t")/$tgt — outer loop will revisit"
  return 1
}

# hourly universe is the last ~7 years of expirations; use the same target minus
# pre-2019 (hourly pulls --years 7). We accept convergence here (hourly has no
# per-day completeness anchor as clean as EOD) but still require a non-empty,
# stable-across-two-passes count and never below the prior best.
pull_hourly(){
  local t=$1 last=-1 cur stable=0
  for a in $(seq 1 12); do
    wait_healthy
    PYTHONPATH=. .venv/bin/python -m scripts.pull_intraday_basket \
      --symbols "$t" --out-dir "$INT_DIR" \
      --years 7 --window-days 50 --strike-range 15 --interval 1h --workers $W --retry-timeouts || true
    cur=$(ls "$INT_DIR"/${t}_*.parquet 2>/dev/null | wc -l | tr -d ' ')
    log "hourly $t pass $a: $cur files (was $last)"
    if [ "$cur" = "$last" ] && [ "$cur" -gt 0 ]; then
      stable=$((stable+1)); [ "$stable" -ge 2 ] && break   # 2 consecutive stable passes
    else
      stable=0
    fi
    last=$cur
  done
  wait_healthy
  PYTHONPATH=. .venv/bin/python -m scripts.pull_intraday_basket --symbols "$t" --out-dir "$INT_DIR" --concat || true
  log "hourly $t DONE: $cur files"
}

log "=== RESILIENT PULL START ==="
rm -f "$EOD_DIR"/.universe_* 2>/dev/null   # recompute completeness targets fresh each launch

# Priority EOD set (QQQ excluded — owner's "dead last" control, pulled after these).
# SPY/XBI/XOP are here for the wedge's timeout-gap expirations; they're already
# ~complete so eod_is_complete short-circuits them fast.
EOD_PRIORITY="AAPL AMZN NVDA META FB SPY XBI XOP"

# OUTER COMPLETION LOOP: revisit every not-yet-complete ticker until ALL are whole.
# This is what makes market-hours safe end-to-end: during the day the off-peak gate
# keeps passes from running, a ticker stays incomplete, the loop simply comes back;
# overnight the gate opens and tickers finish. "DONE" is only ever logged when the
# whole priority set is genuinely complete — so the supervisor never disarms on a
# gap, and nothing is left half-pulled.
round=0
while :; do
  round=$((round+1)); alldone=1
  for t in $EOD_PRIORITY; do
    if ! eod_is_complete "$t"; then
      pull_eod "$t" || true            # progress + COMPLETE/incomplete logged inside
      eod_is_complete "$t" || alldone=0
    fi
  done
  [ "$alldone" = 1 ] && break
  log "=== round $round: priority EOD not all complete — waiting 300s then revisiting ==="
  sleep 300
done
log "=== ALL PRIORITY EOD COMPLETE (round $round) ==="

# QQQ dead last (owner sequencing 2026-07-12), same completion discipline.
while ! eod_is_complete QQQ; do pull_eod QQQ || sleep 300; done
log "=== QQQ EOD COMPLETE ==="

# Hourly (TP-timing only; lower stakes than EOD, convergence-based). Includes the
# SPY hourly the wedge left partial and the straggler concats.
for t in AAPL AMZN NVDA META FB SPY QQQ; do pull_hourly "$t"; done
for t in XBI EEM EWZ TLT ARKK; do
  wait_healthy
  PYTHONPATH=. .venv/bin/python -m scripts.pull_intraday_basket --symbols "$t" --out-dir "$INT_DIR" --concat || true
done

log "=== leftover timeout markers (empty = clean) ==="
ls "$EOD_DIR"/*.timeout "$INT_DIR"/*.timeout 2>/dev/null || log "none"
log "=== RESILIENT PULL DONE ==="
