#!/bin/bash
# One tick of the universe-wide IV accrual pull, fired every 5 minutes by
# systemd (wheelbot-iv.timer). All market-hours gating is done here in ET, so
# the system clock can stay UTC.
#
# WHY ITS OWN UNIT (2026-08-07, owner-approved). This started as a block inside
# scripts/wheelbot_tick.sh. But wheelbot.service is Type=oneshot and
# wheelbot.timer is OnCalendar=*:0/5, so systemd never overlaps runs: a tick
# longer than 5 minutes absorbs the next trigger. The 15:20 tick would run
# intraday -> chain snapshot (~7 min) -> accrual (~5 min) and end ~15:32, so the
# intraday take-profit manager next fired ~15:35 instead of 15:25 and 15:30 --
# on every accrual retry tick. That is a trading-behavior change arriving
# without touching a trading file, which is exactly what this feature's design
# exists to prevent. A separate timer runs the pull concurrently with the
# trading tick and cannot delay it.
#
# The gates below mirror the ones the runner re-checks internally; the shell
# gate just avoids spawning a Python process 288 times a day for nothing.
#
#   * GATED on .chainsnap-$TODAY: trading's ~12-chain pull owns the window and
#     the API budget first; this only ever runs on its leftovers.
#   * Marker written ONLY on exit 0, so a failed pull retries on every
#     remaining in-window tick.
#   * ALWAYS exits 0. wheelbot-iv.service deliberately carries no OnFailure=;
#     an IV outage must never alert-storm or look like a failed unit.
set -uo pipefail
# WHEELBOT_REPO / WHEELBOT_FAKE_* are testability hooks
# (live/tests/test_iv_tick_script.py) -- unset in production, so every default
# below is byte-identical to the deployed behavior.
REPO="${WHEELBOT_REPO:-/home/ubuntu/etf-bot}"
cd "$REPO" || exit 0
PY="$REPO/.venv-live/bin/python"
LOGDIR="$REPO/data/live/logs"
mkdir -p "$LOGDIR"

ET(){ TZ=America/New_York date "$@"; }
log(){ echo "$(ET '+%Y-%m-%d %H:%M:%S ET') $*" >> "$LOGDIR/tick.log"; }
py(){ PYTHONPATH="$REPO" "$PY" "$@"; }

DOW="${WHEELBOT_FAKE_DOW:-$(ET +%u)}"
HM="${WHEELBOT_FAKE_HM:-$((10#$(ET +%H%M)))}"
TODAY="${WHEELBOT_FAKE_TODAY:-$(ET +%Y-%m-%d)}"
SNAPMARKER="$LOGDIR/.chainsnap-$TODAY"
IVMARKER="$LOGDIR/.ivaccrual-$TODAY"

# --- Universe-wide IV accrual: weekdays 15:20-15:55 ET, once per day --------
# 547 chains (~5 min) so the whole universe accrues an IV observation, not just
# the ~12 gate-passers -- without this the forward series gets ~5-6 obs/ticker/
# year against MIN_RANK_OBS=150 and any purchased history decays to NEUTRAL
# about a year after launch.
if [ "$DOW" -le 5 ] && [ "$HM" -ge 1520 ] && [ "$HM" -le 1555 ] \
   && [ -f "$SNAPMARKER" ] && [ ! -f "$IVMARKER" ]; then
  log "iv accrual start"
  py live/run_iv_accrual.py >> "$LOGDIR/iv-$TODAY.log" 2>&1
  RC=$?
  log "iv accrual exit $RC"
  [ "$RC" -eq 0 ] && touch "$IVMARKER"
fi

# Never nonzero: the runner's exit code gates ONLY its own marker (above).
exit 0
