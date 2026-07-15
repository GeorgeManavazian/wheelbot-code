#!/bin/bash
# Added 2026-07-14: keeps ThetaTerminal healthy while the pull chains run.
# Failure mode seen same day: terminal loses upstream gRPC and 429s everything
# until restarted (restart procedure = memory note thetaterminal-restart).
# Exits itself once both pull chains are done.
set -u
cd /Users/georgiemanavazian/Documents/Trading/code/etf-bot
TT=/Users/georgiemanavazian/ThetaTerminal
JAR=202607101.jar
fails=0
log(){ echo "[$(date '+%F %T')] watchdog: $*"; }

restart_terminal(){
  pkill -f "$JAR" 2>/dev/null; sleep 5
  (cd "$TT" && nohup /opt/homebrew/opt/openjdk@21/bin/java \
      -XX:+IgnoreUnrecognizedVMOptions -Dtd.logDir=/tmp \
      --sun-misc-unsafe-memory-access=allow --enable-native-access=ALL-UNNAMED \
      -jar "lib/$JAR" --config "$TT/config.toml" --dotenv-dir "$TT" \
      > terminal_direct.log 2>&1 &)
  sleep 60
  fails=0
  log "terminal restarted"
}

log "started"
while true; do
  if ! pgrep -f "pull_new_tickers.sh|retry_timeouts.sh" > /dev/null; then
    log "pull chains finished — exiting"
    break
  fi
  if ! pgrep -f "$JAR" > /dev/null; then
    log "terminal process dead — restarting"
    restart_terminal
    continue
  fi
  code=$(curl -s --max-time 90 -o /dev/null -w "%{http_code}" \
      "http://127.0.0.1:25503/v3/option/list/expirations?symbol=SPY" || echo 000)
  if [ "$code" = "200" ]; then
    fails=0
  else
    fails=$((fails+1))
    log "health probe $code (streak $fails/3)"
    if [ "$fails" -ge 3 ]; then
      log "terminal unhealthy (wedged) — restarting"
      restart_terminal
    fi
  fi
  sleep 120
done
