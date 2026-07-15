#!/bin/bash
# Added 2026-07-15: PROGRESS-BASED watchdog, replaces pull_watchdog.sh.
# The v1 watchdog probed the terminal with a light list_expirations call that
# kept answering 200 even while the bulk-data gRPC path was wedged — it slept
# through a 5h AAPL stall. This one keys on the ground truth: are pull output
# files still being written? If nothing new lands for STALL seconds while the
# pull is running, the terminal is wedged (or dead) -> restart it. The pull's
# convergence loop then resumes. Exits when the pull script exits.
set -u
cd /Users/georgiemanavazian/Documents/Trading/code/etf-bot
TT=/Users/georgiemanavazian/ThetaTerminal
JAR=202607101.jar
STALL=600          # 10 min with no new file = wedged
CHECK=120
EOD_DIR=data/options/all
INT_DIR=data/options/intraday
log(){ echo "[$(date '+%F %T')] watchdog2: $*"; }

newest_mtime(){   # epoch secs of most-recently-modified pull output, 0 if none
  find "$EOD_DIR" "$INT_DIR" -name '*.parquet' -type f -print0 2>/dev/null \
    | xargs -0 stat -f '%m' 2>/dev/null | sort -rn | head -1
}

restart_terminal(){
  log "restarting terminal"
  pkill -f "$JAR" 2>/dev/null; sleep 5
  (cd "$TT" && nohup /opt/homebrew/opt/openjdk@21/bin/java \
      -XX:+IgnoreUnrecognizedVMOptions -Dtd.logDir=/tmp \
      --sun-misc-unsafe-memory-access=allow --enable-native-access=ALL-UNNAMED \
      -jar "lib/$JAR" --config "$TT/config.toml" --dotenv-dir "$TT" \
      > terminal_direct.log 2>&1 &)
  sleep 60
  log "terminal restarted"
}

# Anchor progress to watchdog start, not absolute file age: pre-existing files
# are hours old and must not trigger a false restart. We alarm on "no NEWER file
# has appeared for STALL seconds," measured in wall-clock since we last saw one.
log "started (stall threshold ${STALL}s)"
prev_nm=$(newest_mtime); prev_nm=${prev_nm:-0}
last_progress=$(date +%s)
while pgrep -f "pull_resilient.sh" > /dev/null; do
  if ! pgrep -f "$JAR" > /dev/null; then
    log "terminal process dead"; restart_terminal
    last_progress=$(date +%s); sleep $CHECK; continue
  fi
  nm=$(newest_mtime); nm=${nm:-0}; now=$(date +%s)
  if [ "$nm" -gt "$prev_nm" ]; then
    prev_nm=$nm; last_progress=$now       # a newer file appeared = progress
  fi
  idle=$((now - last_progress))
  if [ "$idle" -ge "$STALL" ]; then
    log "no new pull file for ${idle}s (>= ${STALL}) — terminal wedged"
    restart_terminal
    last_progress=$(date +%s)
  fi
  sleep $CHECK
done
log "pull_resilient.sh gone — exiting"
