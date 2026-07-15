#!/bin/bash
# Added 2026-07-15, rev2: REACHABILITY watchdog.
# History: v1 used a light list_expirations probe and slept through a heavy-path
# wedge. rev1 used file-mtime progress — but that conflates two very different
# things: (a) a genuine local terminal wedge/death [restart fixes it] vs (b) US
# market-hours throttling / the pull intentionally sleeping at its off-peak gate
# [restart does NOTHING, just thrashes]. During market hours or the pull's
# off-peak wait, files legitimately stop for hours; a progress watchdog would
# restart every 15 min for no reason.
# rev2: only restart when the terminal is genuinely UNREACHABLE — process dead, or
# the light list probe fails repeatedly (terminal hung/crashed at the HTTP layer).
# Heavy-path slowness is left alone: it's market-hours throttle, and the pull's
# own off-peak gate (wait_healthy, SPY barometer) handles that correctly.
set -u
cd /Users/georgiemanavazian/Documents/Trading/code/etf-bot
TT=/Users/georgiemanavazian/ThetaTerminal
JAR=202607101.jar
HOST=http://127.0.0.1:25503
CHECK=120
FAILS_TO_RESTART=6     # consecutive unreachable checks (~12 min) before restarting.
                       # High on purpose: during market hours the pull's 4 workers
                       # fill all 4 terminal slots with slow 40s+ requests, so a
                       # light probe transiently can't get a slot and times out —
                       # that's SATURATION, not a wedge, and a restart just kills
                       # in-flight requests. Only a genuine multi-minute dead
                       # terminal stays unreachable this long.
PROBE_TIMEOUT=45       # generous so a queued-but-alive probe isn't called dead
log(){ echo "[$(date '+%F %T')] watchdog2: $*"; }

reachable(){   # light liveness: does the HTTP layer answer at all? (not a data probe)
  [ "$(curl -s --max-time $PROBE_TIMEOUT -o /dev/null -w '%{http_code}' \
        "$HOST/v3/option/list/expirations?symbol=SPY" 2>/dev/null)" = "200" ]
}

restart_terminal(){
  log "restarting terminal (unreachable)"
  pkill -f "$JAR" 2>/dev/null; sleep 5
  (cd "$TT" && nohup /opt/homebrew/opt/openjdk@21/bin/java \
      -XX:+IgnoreUnrecognizedVMOptions -Dtd.logDir=/tmp \
      --sun-misc-unsafe-memory-access=allow --enable-native-access=ALL-UNNAMED \
      -jar "lib/$JAR" --config "$TT/config.toml" --dotenv-dir "$TT" \
      > terminal_direct.log 2>&1 &)
  sleep 60
  log "terminal restarted"
}

log "started (reachability mode; restart after ${FAILS_TO_RESTART} unreachable checks)"
fails=0
while pgrep -f "pull_resilient.sh" > /dev/null; do
  if ! pgrep -f "$JAR" > /dev/null; then
    log "terminal process dead"; restart_terminal; fails=0; sleep $CHECK; continue
  fi
  if reachable; then
    fails=0
  else
    fails=$((fails+1))
    log "terminal unreachable (light probe fail ${fails}/${FAILS_TO_RESTART})"
    if [ "$fails" -ge "$FAILS_TO_RESTART" ]; then
      restart_terminal; fails=0
    fi
  fi
  sleep $CHECK
done
log "pull_resilient.sh gone — exiting"
