#!/bin/zsh
# In-session scheduler for the live paper bot. Replaces the launchd agents, which
# macOS TCC blocks from ~/Documents (exit 127 "can't open input file"). This runs
# in the user session (which HAS Documents access, same as the Theta pull), under
# caffeinate, checking every 5 min:
#   - the EOD daily run once per weekday after 5pm ET (marker-gated, no double-run)
#   - the intraday exit manager every pass during market hours (9:30-16:00 ET)
# Survives sleep (caffeinate); dies on reboot/logout -> relaunch it then.
REPO="/Users/georgiemanavazian/Documents/Trading/code/etf-bot"
cd "$REPO" || exit 1
mkdir -p data/live/logs
LOG="data/live/logs/loop.log"
ET(){ TZ=America/New_York date "$@"; }
log(){ echo "$(ET '+%Y-%m-%d %H:%M:%S ET') $*" >> "$LOG"; }

daily_if_due(){
  local today=$(ET +%Y-%m-%d) dow=$(ET +%u) hm=$((10#$(ET +%H%M)))
  local marker="data/live/logs/.dailyran-$today"
  [ "$dow" -le 5 ] || return          # weekday only
  [ "$hm" -ge 1700 ] || return        # after 5pm ET (data settled)
  [ -f "$marker" ] && return          # already ran today
  log "EOD daily run start"
  PYTHONPATH="$REPO" .venv-live/bin/python live/run_daily.py \
    >> "data/live/logs/$today.log" 2>&1
  log "EOD daily run exit $?"
  touch "$marker"
}

intraday_if_market(){
  local dow=$(ET +%u) hm=$((10#$(ET +%H%M)))
  { [ "$dow" -le 5 ] && [ "$hm" -ge 930 ] && [ "$hm" -le 1600 ]; } || return
  PYTHONPATH="$REPO" .venv-live/bin/python live/run_intraday.py \
    >> "data/live/logs/intraday-$(ET +%Y-%m-%d).log" 2>&1
}

log "=== wheelbot loop start ==="
while true; do
  daily_if_due
  intraday_if_market
  sleep 300
done
