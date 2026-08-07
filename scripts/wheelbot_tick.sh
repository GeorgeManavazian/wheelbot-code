#!/bin/bash
# One tick of the live bot, fired every 5 minutes by systemd (wheelbot.timer).
# All market-hours gating is done here in ET, so the system clock can stay UTC.
#
# Group D rewrite (owner decisions 2026-08-01):
#   D6  the health check runs FIRST -- nothing that can hang or abort later
#       may starve the watchdog.
#   D9  every block records failure in FAIL; the tick exits nonzero so
#       systemd (and D7's OnFailure alert unit) can see a bad tick.
#   D1  the intraday manager's EXIT CODE is checked (a bare-traceback crash
#       used to slip the case-sensitive ERROR grep with exit 0).
#   D10 split markers: .dailyran = the trading day completed; .synced = the
#       mirror caught up. A failed push retries ALONE on later ticks -- the
#       547-name day is never re-run because GitHub hiccupped.
#   D5  the token nag is eligible on EVERY tick (weekends included) and its
#       marker is written only when the alert was DELIVERED (run_notify's
#       exit-0-means-delivered contract, D3).
#   D14 a heartbeat.json is refreshed every tick and rides the normal syncs,
#       so the off-VPS GitHub freshness check can judge liveness.
#
# Alert logic lives in live/run_notify.py, not in `python -c` strings here --
# alerting is the code that has to work on the worst day, so it gets to be
# readable and testable.
set -uo pipefail
# WHEELBOT_REPO / WHEELBOT_FAKE_* / WHEELBOT_TOKEN_PATH are testability hooks
# (live/tests/test_tick_script.py) -- unset in production, so every default
# below is byte-identical to the deployed behavior.
REPO="${WHEELBOT_REPO:-/home/ubuntu/etf-bot}"
cd "$REPO" || exit 1
PY="$REPO/.venv-live/bin/python"
LOGDIR="$REPO/data/live/logs"
mkdir -p "$LOGDIR"

ET(){ TZ=America/New_York date "$@"; }
log(){ echo "$(ET '+%Y-%m-%d %H:%M:%S ET') $*" >> "$LOGDIR/tick.log"; }
py(){ PYTHONPATH="$REPO" "$PY" "$@"; }

DOW="${WHEELBOT_FAKE_DOW:-$(ET +%u)}"
HM="${WHEELBOT_FAKE_HM:-$((10#$(ET +%H%M)))}"
TODAY="${WHEELBOT_FAKE_TODAY:-$(ET +%Y-%m-%d)}"
MARKER="$LOGDIR/.dailyran-$TODAY"
SYNCED="$LOGDIR/.synced-$TODAY"
FAIL=0

# --- Health check (dead-man's switch) FIRST (D6) ----------------------------
# Sub-second no-op before 23:45 ET; deliberately AFTER the 23:30 EOD window
# close so it never records a gap a later retry tick could still fill.
if [ "$DOW" -le 5 ] && [ "$HM" -ge 2345 ]; then
  py live/run_health.py >> "$LOGDIR/health-$TODAY.log" 2>&1 || FAIL=1
fi

# --- Failed-alert spool retry (D3): cheap file check, every tick ------------
py live/run_notify.py retry-spool >> "$LOGDIR/tick.log" 2>&1 || true

# --- Schwab login nag (D5): every tick, any day -----------------------------
# MUST NOT key on token.json's mtime (schwab-py rewrites it constantly);
# tokenage reads creation_timestamp. Marker only when the alert was DELIVERED
# (exit 0) -- an undelivered nag retries next tick.
TOKEN="${WHEELBOT_TOKEN_PATH:-$HOME/.schwab/token.json}"
NAG="$LOGDIR/.tokennag-$TODAY"
# D5b: no [ -f "$TOKEN" ] gate -- a MISSING token file is exactly what the
# nag must be able to report (run_notify owns fresh-install-vs-ran-before).
if [ ! -f "$NAG" ]; then
  if py live/run_notify.py token-age "$TOKEN" >> "$LOGDIR/tick.log" 2>&1; then
    touch "$NAG"
    log "token nag sent"
  fi
fi

# --- Intraday exit manager: weekdays 9:30-16:00 ET (D1) ---------------------
if [ "$DOW" -le 5 ] && [ "$HM" -ge 930 ] && [ "$HM" -le 1600 ]; then
  OUT=$(py live/run_intraday.py 2>&1)
  IRC=$?
  echo "$OUT" >> "$LOGDIR/intraday-$TODAY.log"

  # D1: the exit code is the contract; the text grep (now case-insensitive,
  # Traceback included) is belt-and-braces for partial per-account errors.
  IERR="$LOGDIR/.intradayerr-$TODAY"
  if [ "$IRC" -ne 0 ] || echo "$OUT" | grep -qiE "ERROR|Traceback"; then
    FAIL=1
    if [ ! -f "$IERR" ]; then
      if py live/run_notify.py intraday-errors "$TODAY" "$LOGDIR/intraday-$TODAY.log" \
        >> "$LOGDIR/tick.log" 2>&1; then
        touch "$IERR"
        log "intraday error alert sent"
      fi
    fi
  fi

  # push only when a trade was actually booked -- not on the ~78 daily no-ops.
  # D10: the push result is CHECKED; a booked-but-unpushed trade alerts.
  if echo "$OUT" | grep -qE "closed [1-9][0-9]* at TP"; then
    if ! py -c "import sys; from live.sync import sync_state; sys.exit(0 if sync_state('data/live', 'intraday $TODAY') else 1)" \
        >> "$LOGDIR/intraday-$TODAY.log" 2>&1; then
      FAIL=1
      ISYERR="$LOGDIR/.intradaysyncerr-$TODAY"
      if [ ! -f "$ISYERR" ]; then
        py live/run_notify.py sync-failed "$TODAY" >> "$LOGDIR/tick.log" 2>&1 \
          && touch "$ISYERR"
      fi
      log "intraday sync FAILED (alerted)"
    fi
  fi
fi

# --- RTH chain snapshot: weekdays 15:20-15:55 ET, once per day (A16) --------
# Placed after the intraday block so a time-sensitive take-profit is never
# queued behind a ~7-minute pull. Marker only on exit 0 -- a failed pull
# retries on every remaining tick in the window (NOT a FAIL: retrying is the
# design; a whole-window failure surfaces via run_daily's gap path).
SNAPMARKER="$LOGDIR/.chainsnap-$TODAY"
if [ "$DOW" -le 5 ] && [ "$HM" -ge 1520 ] && [ "$HM" -le 1555 ] && [ ! -f "$SNAPMARKER" ]; then
  log "chain snapshot start"
  py live/run_chain_snapshot.py >> "$LOGDIR/$TODAY.log" 2>&1
  RC=$?
  log "chain snapshot exit $RC"
  [ "$RC" -eq 0 ] && touch "$SNAPMARKER"
fi

# --- Universe-wide IV accrual: weekdays 15:20-15:55 ET, once per day -------
# 547 chains (~5 min) so the whole universe accrues an IV observation, not just
# the ~12 gate-passers -- without this the forward series gets ~5-6 obs/ticker/
# year against MIN_RANK_OBS=150 and any purchased history decays to NEUTRAL
# about a year after launch.
#
# GATED on $SNAPMARKER: trading's ~12-chain pull owns the window and the API
# budget first; this only ever runs on its leftovers. Deliberately does NOT set
# FAIL -- an IV outage must never alert-storm or mark a trading day failed. The
# runner itself re-checks every one of these conditions; the shell gate just
# avoids spawning it 60 times a day for nothing.
IVMARKER="$LOGDIR/.ivaccrual-$TODAY"
if [ "$DOW" -le 5 ] && [ "$HM" -ge 1520 ] && [ "$HM" -le 1555 ] \
   && [ -f "$SNAPMARKER" ] && [ ! -f "$IVMARKER" ]; then
  log "iv accrual start"
  py live/run_iv_accrual.py >> "$LOGDIR/iv-$TODAY.log" 2>&1
  RC=$?
  log "iv accrual exit $RC"
  [ "$RC" -eq 0 ] && touch "$IVMARKER"
fi

# --- EOD daily run: weekdays, 17:00-23:30 ET, once per day ------------------
# Fires on every tick inside the window; the marker stops the second success.
# That repetition IS the retry mechanism for a failed run. The window runs
# late on purpose (a 20:30 re-login evening stays recoverable); run_daily
# derives its trading date from ET, so a late run still stamps the right day.
if [ "$DOW" -le 5 ] && [ "$HM" -ge 1700 ] && [ "$HM" -le 2330 ] && [ ! -f "$MARKER" ]; then
  log "daily run start"
  py live/run_daily.py >> "$LOGDIR/$TODAY.log" 2>&1
  RC=$?
  log "daily run exit $RC"
  if [ "$RC" -eq 0 ]; then
    touch "$MARKER"          # ONLY on success -- a failed run must retry
  else
    FAIL=1
  fi
fi

# --- Heartbeat (D14): refreshed every tick, BEFORE the EOD sync so the ------
# pushed mirror carries today's truth. Written atomically; *.tmp is
# gitignored so a crash mid-write stages nothing.
HB="$REPO/data/live/heartbeat.json"
DR=false; [ -f "$MARKER" ] && DR=true
SY=false; [ -f "$SYNCED" ] && SY=true
printf '{"tick_at": "%s", "today": "%s", "dailyran": %s, "synced": %s, "fail": %s}\n' \
  "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$TODAY" "$DR" "$SY" \
  "$([ "$FAIL" -eq 0 ] && echo false || echo true)" > "$HB.tmp" \
  && mv "$HB.tmp" "$HB"

# --- EOD state sync (D10): retry the PUSH alone until it lands --------------
# A failed push used to be attempted exactly once and the mirror could freeze
# for weeks while everything reported success.
if [ -f "$MARKER" ] && [ ! -f "$SYNCED" ]; then
  if py -c "import sys; from live.sync import sync_state; sys.exit(0 if sync_state('data/live', 'eod $TODAY') else 1)" \
      >> "$LOGDIR/$TODAY.log" 2>&1; then
    touch "$SYNCED"
  else
    FAIL=1
    SYERR="$LOGDIR/.syncerr-$TODAY"
    if [ ! -f "$SYERR" ]; then
      py live/run_notify.py sync-failed "$TODAY" >> "$LOGDIR/tick.log" 2>&1 \
        && touch "$SYERR"
    fi
    log "state sync FAILED (alerted)"
  fi
fi

exit $FAIL
