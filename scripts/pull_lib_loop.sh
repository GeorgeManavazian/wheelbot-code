#!/bin/zsh
# Full-universe library pull, caffeinated. Loops the concurrent driver until the
# universe is complete -- if a run dies (network blip, laptop wake), the next
# iteration resumes (already-concatted tickers skip instantly). No ThetaTerminal.
REPO="/Users/georgiemanavazian/Documents/Trading/code/etf-bot"
cd "$REPO" || exit 1
LOG="data/options/pull_lib.log"
while true; do
  echo "=== $(date '+%Y-%m-%d %H:%M:%S %Z') universe-lib pull pass ===" >> "$LOG"
  PYTHONPATH="$REPO" "$REPO/.venv-live/bin/python" scripts/pull_universe_lib.py --workers 4 >> "$LOG" 2>&1
  grep -q "UNIVERSE PULL COMPLETE: 547/547" "$LOG" && { echo "ALL DONE" >> "$LOG"; break; }
  echo "=== pass ended, resuming in 30s ===" >> "$LOG"
  sleep 30
done
