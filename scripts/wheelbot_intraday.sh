#!/bin/zsh
# Intraday exit-only run, fired every ~15 min by launchd. run_intraday.py self-gates
# to market hours (9:30-4 ET weekdays) and is a sub-second no-op otherwise, so this
# stays cheap even though launchd wakes it around the clock. Data-only paper.
REPO="/Users/georgiemanavazian/Documents/Trading/code/etf-bot"
cd "$REPO" || exit 1
LOGDIR="$REPO/data/live/logs"; mkdir -p "$LOGDIR"
LOG="$LOGDIR/intraday-$(date +%Y-%m-%d).log"

echo "=== $(date '+%Y-%m-%d %H:%M:%S %Z') run_intraday ===" >> "$LOG"
caffeinate -i env PYTHONPATH="$REPO" "$REPO/.venv-live/bin/python" "$REPO/live/run_intraday.py" >> "$LOG" 2>&1
echo "exit: $?" >> "$LOG"
