#!/bin/bash
# Added 2026-07-15: hourly supervisor (owner asked for an hourly "is the pull
# still running?" check). Sits ABOVE pull_watchdog2.sh: the watchdog only heals
# terminal wedges while the pull runs; if the pull bash process itself dies
# (crash, reboot, OOM) nothing relaunches it. This does. Idempotent + resumable:
# safe to run every hour, relaunches only when needed, exits the cron when done.
# Absolute paths throughout — cron/launchd run with a minimal environment.
set -u
REPO=/Users/georgiemanavazian/Documents/Trading/code/etf-bot
LOG=$REPO/data/options/pull_resilient.log
SUPLOG=$REPO/data/options/supervisor.log
cd "$REPO" || { echo "$(date '+%F %T') supervisor: cannot cd $REPO" >> "$SUPLOG"; exit 1; }

stamp(){ echo "[$(date '+%F %T')] supervisor: $*" >> "$SUPLOG"; }

# 1) pull finished cleanly? signal the hourly loop to stop (exit 42).
if [ -f "$LOG" ] && grep -q "RESILIENT PULL DONE" "$LOG"; then
  stamp "pull COMPLETE — signalling supervisor loop to stop"
  exit 42
fi

# 2) pull alive? heartbeat and leave it.
if pgrep -f "pull_resilient.sh" > /dev/null; then
  files=$(ls "$REPO"/data/options/all/*.parquet 2>/dev/null | wc -l | tr -d ' ')
  stamp "pull RUNNING (${files} EOD files on disk) — ok"
  # make sure the terminal watchdog is also up
  if ! pgrep -f "pull_watchdog2.sh" > /dev/null; then
    stamp "watchdog missing — relaunching it"
    nohup caffeinate -is bash "$REPO/scripts/pull_watchdog2.sh" \
      >> "$REPO/data/options/watchdog2.log" 2>&1 &
  fi
  exit 0
fi

# 3) pull is DOWN and not finished -> relaunch (resumable; existing files skip).
stamp "pull DOWN and unfinished — relaunching pull + watchdog"
nohup caffeinate -is bash "$REPO/scripts/pull_resilient.sh" >> "$LOG" 2>&1 &
sleep 2
nohup caffeinate -is bash "$REPO/scripts/pull_watchdog2.sh" \
  >> "$REPO/data/options/watchdog2.log" 2>&1 &
stamp "relaunched"
