#!/bin/bash
# Prevent Mac from sleeping while any agent session is active.
# Uses Amphetamine.app and per-session marker files for multi-session tracking.
#
# Usage (hook stdin carries the session JSON):
#   awake.sh start   — register this session, start Amphetamine if it's the first
#   awake.sh stop    — unregister this session, end Amphetamine when none remain

set -u

ACTION="${1:-}"
STATE_DIR="${AWAKE_STATE_DIR:-$HOME/.claude/.awake}"
SESSIONS_DIR="$STATE_DIR/sessions.d"

mkdir -p "$SESSIONS_DIR"

# Markers are keyed by session id so repeat SessionStart events (resume, clear,
# compact) re-register the same session instead of inflating a counter that then
# never drains back to zero.
session_id() {
  local payload id
  payload=$(cat 2>/dev/null)
  id=$(printf '%s' "$payload" | sed -n 's/.*"session_id"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p')
  # Strip anything that could escape the marker directory.
  id=$(printf '%s' "$id" | tr -cd 'A-Za-z0-9._-')
  printf '%s' "${id:-default}"
}

active_count() {
  find "$SESSIONS_DIR" -type f -depth 1 2>/dev/null | wc -l | tr -d ' '
}

amphetamine_start() {
  if ! pgrep -xq "Amphetamine"; then
    echo "awake: Amphetamine is not running, skipping" >&2
    return 1
  fi
  osascript -e 'tell application "Amphetamine" to start new session' 2>/dev/null
}

amphetamine_stop() {
  if ! pgrep -xq "Amphetamine"; then
    return 0
  fi
  osascript -e 'tell application "Amphetamine" to end session' 2>/dev/null
}

case "$ACTION" in
  start)
    id=$(session_id)
    before=$(active_count)
    : > "$SESSIONS_DIR/$id"
    if [ "$before" -eq 0 ]; then
      amphetamine_start
    fi
    ;;
  stop)
    id=$(session_id)
    rm -f "$SESSIONS_DIR/$id"
    if [ "$(active_count)" -eq 0 ]; then
      amphetamine_stop
    fi
    ;;
  status)
    echo "Active sessions: $(active_count)"
    find "$SESSIONS_DIR" -type f -depth 1 -exec basename {} \; 2>/dev/null
    ;;
  reset)
    rm -f "$SESSIONS_DIR"/* 2>/dev/null
    amphetamine_stop
    echo "awake: reset — all sessions cleared, Amphetamine session ended"
    ;;
  *)
    echo "Usage: awake.sh {start|stop|status|reset}" >&2
    exit 1
    ;;
esac
