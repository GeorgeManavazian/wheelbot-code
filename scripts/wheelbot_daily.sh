#!/bin/zsh
# Daily paper-trading run, invoked by the launchd agent (weekdays 5pm ET).
# Data-only paper trading. Logs to data/live/logs/. Pops a desktop notification
# when the 7-day Schwab login is due — the ONE manual step.
REPO="/Users/georgiemanavazian/Documents/Trading/code/etf-bot"
cd "$REPO" || exit 1
LOGDIR="$REPO/data/live/logs"; mkdir -p "$LOGDIR"
LOG="$LOGDIR/$(date +%Y-%m-%d).log"
TOKEN="$HOME/.schwab/token.json"

# Knobs (N, capital) live in data/live/config.json now -- run_daily.py reads it.

# weekly-login reminder: warn if the Schwab token is >= 6 days old
if [ -f "$TOKEN" ]; then
  AGE_DAYS=$(( ( $(date +%s) - $(stat -f %m "$TOKEN") ) / 86400 ))
  if [ "$AGE_DAYS" -ge 6 ]; then
    osascript -e 'display notification "Re-run schwab_login.py — token expires in ~1 day" with title "Wheel Bot: weekly Schwab login due"' 2>/dev/null
    echo "[WARN] Schwab token is ${AGE_DAYS}d old — re-login soon (schwab_login.py)" >> "$LOG"
  fi
fi

echo "=== $(date '+%Y-%m-%d %H:%M:%S %Z') run_daily (N/capital from config.json) ===" >> "$LOG"
PYTHONPATH="$REPO" "$REPO/.venv-live/bin/python" "$REPO/live/run_daily.py" >> "$LOG" 2>&1
RC=$?
echo "exit: $RC" >> "$LOG"
# on failure (e.g. lapsed token), nudge the desktop
if [ "$RC" -ne 0 ]; then
  osascript -e 'display notification "run_daily failed — check the log (maybe re-login?)" with title "Wheel Bot: run failed"' 2>/dev/null
fi
exit $RC
