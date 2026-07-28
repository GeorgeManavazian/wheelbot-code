#!/bin/bash
# One tick of the live bot, fired every 5 minutes by systemd (wheelbot.timer).
# All market-hours gating is done here in ET, so the system clock can stay UTC.
#
# Replaces scripts/wheelbot_loop.sh, whose `while true` loop died on reboot with
# nothing to restart it. The other fix: the old loop ran `touch "$marker"`
# unconditionally after run_daily.py, which is what made 2026-07-24's total
# pull failure permanent. The marker is now written ONLY on exit 0.
set -uo pipefail
REPO="/home/ubuntu/etf-bot"
cd "$REPO" || exit 1
PY="$REPO/.venv-live/bin/python"
LOGDIR="$REPO/data/live/logs"
mkdir -p "$LOGDIR"

ET(){ TZ=America/New_York date "$@"; }
log(){ echo "$(ET '+%Y-%m-%d %H:%M:%S ET') $*" >> "$LOGDIR/tick.log"; }

DOW=$(ET +%u)
HM=$((10#$(ET +%H%M)))
TODAY=$(ET +%Y-%m-%d)
MARKER="$LOGDIR/.dailyran-$TODAY"

# --- weekly Schwab login reminder: warn at 6 days, once per day -------------
# The refresh token lapses at 7 days and re-auth is interactive by design.
# Without this the bot simply goes blind and every pull fails (see 2026-07-24).
TOKEN="$HOME/.schwab/token.json"
NAG="$LOGDIR/.tokennag-$TODAY"
if [ -f "$TOKEN" ] && [ ! -f "$NAG" ] && [ "$DOW" -le 5 ] && [ "$HM" -ge 1700 ]; then
  AGE=$(( ( $(date +%s) - $(stat -c %Y "$TOKEN") ) / 86400 ))
  if [ "$AGE" -ge 6 ]; then
    PYTHONPATH="$REPO" "$PY" -c "from live.alerts import send_alert; send_alert('Schwab login due', 'The Schwab refresh token is ${AGE}d old and lapses at 7 days. SSH into the VPS and re-run scripts/schwab/schwab_login.py, or the bot goes blind and every pull fails.')" >> "$LOGDIR/tick.log" 2>&1
    touch "$NAG"
    log "token nag sent (age ${AGE}d)"
  fi
fi

# --- EOD daily run: weekdays, 17:00-20:00 ET, once per day -----------------
# Fires on every tick inside the window; the marker stops the second success.
# That repetition IS the retry mechanism for a failed run.
if [ "$DOW" -le 5 ] && [ "$HM" -ge 1700 ] && [ "$HM" -le 2000 ] && [ ! -f "$MARKER" ]; then
  log "daily run start"
  PYTHONPATH="$REPO" "$PY" live/run_daily.py >> "$LOGDIR/$TODAY.log" 2>&1
  RC=$?
  log "daily run exit $RC"
  if [ "$RC" -eq 0 ]; then
    touch "$MARKER"          # ONLY on success -- a failed run must retry
    PYTHONPATH="$REPO" "$PY" -c "from live.sync import sync_state; sync_state('data/live', 'eod $TODAY')" >> "$LOGDIR/$TODAY.log" 2>&1
  fi
fi

# --- Intraday exit manager: weekdays 9:30-16:00 ET -------------------------
if [ "$DOW" -le 5 ] && [ "$HM" -ge 930 ] && [ "$HM" -le 1600 ]; then
  OUT=$(PYTHONPATH="$REPO" "$PY" live/run_intraday.py 2>&1)
  echo "$OUT" >> "$LOGDIR/intraday-$TODAY.log"
  # push only when a trade was actually booked -- not on the ~78 daily no-ops
  if echo "$OUT" | grep -qE "closed [1-9][0-9]* at TP"; then
    PYTHONPATH="$REPO" "$PY" -c "from live.sync import sync_state; sync_state('data/live', 'intraday $TODAY')" >> "$LOGDIR/intraday-$TODAY.log" 2>&1
  fi
fi

# --- Health check (dead-man's switch): weekdays after 20:15 ET -------------
if [ "$DOW" -le 5 ] && [ "$HM" -ge 2015 ]; then
  PYTHONPATH="$REPO" "$PY" live/run_health.py >> "$LOGDIR/health-$TODAY.log" 2>&1
fi

exit 0
