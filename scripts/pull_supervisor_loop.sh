#!/bin/bash
# Added 2026-07-15: hourly loop wrapper around pull_supervisor.sh. A real file
# (not an inline `bash -c` string) so it's not fragile to pattern-matched kills.
# Runs the one-shot supervisor check every hour; exits when the check returns 42
# (pull complete). Launch under: nohup caffeinate -is bash scripts/pull_supervisor_loop.sh &
set -u
REPO=/Users/georgiemanavazian/Documents/Trading/code/etf-bot
SUP=$REPO/scripts/pull_supervisor.sh
SUPLOG=$REPO/data/options/supervisor.log
while true; do
  bash "$SUP"
  if [ $? -eq 42 ]; then
    echo "[$(date '+%F %T')] supervisor loop: pull complete — exiting" >> "$SUPLOG"
    break
  fi
  sleep 3600
done
