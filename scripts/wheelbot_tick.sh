#!/bin/bash
# One tick of the live bot, fired every 5 minutes by systemd (wheelbot.timer).
# All market-hours gating is done here in ET, so the system clock can stay UTC.
#
# Replaces scripts/wheelbot_loop.sh, whose `while true` loop died on reboot with
# nothing to restart it. The other fix: the old loop ran `touch "$marker"`
# unconditionally after run_daily.py, which is what made 2026-07-24's total pull
# failure permanent. The marker is now written ONLY on exit 0.
#
# Alert logic lives in live/run_notify.py, not in `python -c` strings here --
# alerting is the code that has to work on the worst day, so it gets to be
# readable and testable.
set -uo pipefail
REPO="/home/ubuntu/etf-bot"
cd "$REPO" || exit 1
PY="$REPO/.venv-live/bin/python"
LOGDIR="$REPO/data/live/logs"
mkdir -p "$LOGDIR"

ET(){ TZ=America/New_York date "$@"; }
log(){ echo "$(ET '+%Y-%m-%d %H:%M:%S ET') $*" >> "$LOGDIR/tick.log"; }
py(){ PYTHONPATH="$REPO" "$PY" "$@"; }

DOW=$(ET +%u)
HM=$((10#$(ET +%H%M)))
TODAY=$(ET +%Y-%m-%d)
MARKER="$LOGDIR/.dailyran-$TODAY"

# --- weekly Schwab login reminder ------------------------------------------
# MUST NOT key on token.json's mtime: schwab-py rewrites that file on every
# access-token refresh, so its mtime is never more than minutes old and the
# pre-2026-07-29 nag could never fire -- the bot would have gone blind at the
# 7-day refresh-token lapse with no warning at all. live/tokenage.py reads the
# `creation_timestamp` field, which is the real issue time.
TOKEN="$HOME/.schwab/token.json"
NAG="$LOGDIR/.tokennag-$TODAY"
if [ -f "$TOKEN" ] && [ ! -f "$NAG" ] && [ "$DOW" -le 5 ] && [ "$HM" -ge 1700 ]; then
  if py live/run_notify.py token-age "$TOKEN" >> "$LOGDIR/tick.log" 2>&1; then
    touch "$NAG"
    log "token nag sent"
  fi
fi

# --- EOD daily run: weekdays, 17:00-23:30 ET, once per day -----------------
# Fires on every tick inside the window; the marker stops the second success.
# That repetition IS the retry mechanism for a failed run.
#
# The window runs late on purpose. The old loop had no upper bound at all, and
# the 20:00 cap this script originally shipped with turned a RECOVERABLE evening
# (owner re-runs the login at 20:30, the chain is still pullable) into a
# permanent, unbackfillable gap. run_daily.py derives its trading date from ET,
# so a late run still stamps the correct day.
if [ "$DOW" -le 5 ] && [ "$HM" -ge 1700 ] && [ "$HM" -le 2330 ] && [ ! -f "$MARKER" ]; then
  log "daily run start"
  py live/run_daily.py >> "$LOGDIR/$TODAY.log" 2>&1
  RC=$?
  log "daily run exit $RC"
  if [ "$RC" -eq 0 ]; then
    touch "$MARKER"          # ONLY on success -- a failed run must retry
    # A failed push used to be discarded here, so the mirror could freeze for
    # weeks while everything reported success and the dashboard quietly aged.
    if ! py -c "import sys; from live.sync import sync_state; sys.exit(0 if sync_state('data/live', 'eod $TODAY') else 1)" >> "$LOGDIR/$TODAY.log" 2>&1; then
      py live/run_notify.py sync-failed "$TODAY" >> "$LOGDIR/tick.log" 2>&1
      log "state sync FAILED (alerted)"
    fi
  fi
fi

# --- Intraday exit manager: weekdays 9:30-16:00 ET -------------------------
if [ "$DOW" -le 5 ] && [ "$HM" -ge 930 ] && [ "$HM" -le 1600 ]; then
  OUT=$(py live/run_intraday.py 2>&1)
  echo "$OUT" >> "$LOGDIR/intraday-$TODAY.log"

  # A dead exit engine prints output byte-identical to a quiet day ("0 TP
  # close(s)"), so it could be broken for weeks with zero signal. Alert once per
  # day if any account errored.
  IERR="$LOGDIR/.intradayerr-$TODAY"
  if echo "$OUT" | grep -q "ERROR" && [ ! -f "$IERR" ]; then
    py live/run_notify.py intraday-errors "$TODAY" "$LOGDIR/intraday-$TODAY.log" \
      >> "$LOGDIR/tick.log" 2>&1
    touch "$IERR"
    log "intraday error alert sent"
  fi

  # push only when a trade was actually booked -- not on the ~78 daily no-ops
  if echo "$OUT" | grep -qE "closed [1-9][0-9]* at TP"; then
    py -c "from live.sync import sync_state; sync_state('data/live', 'intraday $TODAY')" \
      >> "$LOGDIR/intraday-$TODAY.log" 2>&1
  fi
fi

# --- RTH chain snapshot: weekdays 15:20-15:55 ET, once per day (A16) -------
# The 17:00 daily run consumes THIS snapshot instead of pulling chains from
# the post-close book (measured 3-4x wider than tradeable). Placed after the
# intraday block so a time-sensitive take-profit is never queued behind a
# ~7-minute pull. Marker only on exit 0 -- a failed pull retries on every
# remaining tick in the window. If the whole window fails, run_daily records
# the day as a gap (owner decision 2026-07-31): no post-close fallback, ever.
SNAPMARKER="$LOGDIR/.chainsnap-$TODAY"
if [ "$DOW" -le 5 ] && [ "$HM" -ge 1520 ] && [ "$HM" -le 1555 ] && [ ! -f "$SNAPMARKER" ]; then
  log "chain snapshot start"
  py live/run_chain_snapshot.py >> "$LOGDIR/$TODAY.log" 2>&1
  RC=$?
  log "chain snapshot exit $RC"
  [ "$RC" -eq 0 ] && touch "$SNAPMARKER"
fi

# --- Health check (dead-man's switch): weekdays after 23:45 ET -------------
# Deliberately AFTER the 23:30 EOD window close -- an earlier cutoff would
# record a gap for a day that a later retry tick could still complete.
if [ "$DOW" -le 5 ] && [ "$HM" -ge 2345 ]; then
  py live/run_health.py >> "$LOGDIR/health-$TODAY.log" 2>&1
fi

exit 0
