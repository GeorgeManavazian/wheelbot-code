#!/bin/bash
# Launch the dashboard against the LIVE state the bot is writing on the VPS.
#
# The bot no longer runs on this Mac, so data/live/ here is frozen at cutover
# (2026-07-28) and will never update again. The real store is pushed to the
# private wheelbot-state repo after every EOD run and every intraday run that
# books a trade. This clones/pulls that repo and points the dashboard at it via
# WHEELBOT_STATE_DIR (see live/paths.py).
#
# Auth: uses your existing gh/git credentials -- the VPS's PAT is NOT copied here.
#
#   ./scripts/dashboard.sh
set -uo pipefail
REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_DIR" || exit 1

STATE_DIR="$REPO_DIR/data/live-synced"
REMOTE="https://github.com/GeorgeManavazian/wheelbot-state.git"

if [ -d "$STATE_DIR/.git" ]; then
  echo "pulling latest bot state..."
  # the VPS force-pushes this mirror, so a plain pull can conflict on rewritten
  # history. Hard-reset to the remote: this clone is read-only by design and has
  # nothing local worth keeping.
  git -C "$STATE_DIR" fetch --quiet origin main && \
  git -C "$STATE_DIR" reset --quiet --hard origin/main
else
  echo "cloning bot state for the first time..."
  git clone --quiet "$REMOTE" "$STATE_DIR"
fi

if [ ! -d "$STATE_DIR/accounts" ]; then
  echo "ERROR: $STATE_DIR has no accounts/ -- the sync repo looks empty."
  echo "Check the VPS: ssh -i ~/.ssh/wheelbot.key ubuntu@129.80.185.142"
  exit 1
fi

LAST=$(git -C "$STATE_DIR" log -1 --format='%cr -- %s' 2>/dev/null)
echo "bot state: $LAST"

export WHEELBOT_STATE_DIR="$STATE_DIR"
exec "$REPO_DIR/.venv-live/bin/python" -m streamlit run dashboard/app.py
