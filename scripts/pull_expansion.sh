#!/bin/zsh
# Expansion pull for the backtest-universe study (2026-07-18). Priority-ordered:
# the diverse/weak/cyclical/rangey names the 9-trender backtest lacks come FIRST,
# so a partial run (sub lapses ~Jul 25, laptop leaves ~Jul 20) is still useful.
# Per ticker: pull (start-year 2024, trimmed sr15/wd25) -> concat. Resumable
# (pull_spy_all skips existing per-exp files). Parks during US market hours
# (ThetaData throttles ~40x then). Detached + caffeinated; survives sleep.
REPO="/Users/georgiemanavazian/Documents/Trading/code/etf-bot"
cd "$REPO" || exit 1
LOG="data/options/expansion_pull.log"
OUT="data/options/all"
SY=2024; SR=15; WD=25

# priority order: value/cyclical/weak + rangey ETFs first, growth/mega last
TICKERS=(
  WFC XOM CVX BAC C GM F VALE AA NEM FCX KMI OXY MO T VZ PFE CVS PARA AGNC
  KGC SLB HAL DVN COP MPC PSX BA DAL UAL CCL NCLH
  XLE XLF XLU XLI XLP KRE GLD USO SMH XLV XLB XLK IWM
  KO PEP WMT MCD DIS CMCSA INTC CSCO PYPL UBER SBUX NKE JPM GS CAT
  MSFT GOOGL TSLA AVGO ORCL CRM AMD NFLX
)

log(){ echo "$(date '+%m-%d %H:%M:%S %Z') $*" >> "$LOG"; }

in_market_hours(){   # weekday 09:30-16:00 ET -> park (Theta throttles)
  local dow=$(TZ=America/New_York date +%u)
  local hm=$((10#$(TZ=America/New_York date +%H%M)))
  [ "$dow" -le 5 ] && [ "$hm" -ge 930 ] && [ "$hm" -le 1600 ]
}

# never compete with the resilient pull for ThetaData's 4 slots (429 storm) --
# yield the whole terminal to it and wait.
yield_to_resilient(){
  while pgrep -f "pull_resilient.sh|pull_supervisor.sh" >/dev/null 2>&1; do
    log "resilient pull active — yielding, wait 5min"; sleep 300
  done
}

log "=== EXPANSION PULL START (${#TICKERS[@]} tickers, start-year $SY) ==="
for t in $TICKERS; do
  dest="data/options/${t:l}_greeks_eod_all.parquet"
  if [ -f "$dest" ]; then log "$t already concatted — skip"; continue; fi
  yield_to_resilient
  while in_market_hours; do log "$t: market hours — parked 5min"; sleep 300; done
  log "$t: pulling…"
  PYTHONPATH="$REPO" .venv/bin/python -m scripts.pull_spy_all --symbol "$t" \
    --out-dir "$OUT" --start-year $SY --strike-range $SR --window-days $WD --workers 3 \
    >> "$LOG" 2>&1
  PYTHONPATH="$REPO" .venv/bin/python -m scripts.pull_spy_all --symbol "$t" \
    --out-dir "$OUT" --concat >> "$LOG" 2>&1 \
    && log "$t: DONE -> $dest" || log "$t: concat failed (partial, will retry next run)"
done
log "=== EXPANSION PULL COMPLETE ==="
