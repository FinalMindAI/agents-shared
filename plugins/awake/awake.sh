#!/bin/bash
# Prevent Mac from sleeping while any agent session is active.
# Uses Amphetamine.app and a shared counter file for multi-session tracking.
#
# Usage:
#   awake.sh start   — register a new session, start Amphetamine if needed
#   awake.sh stop    — unregister a session, end Amphetamine when all done

set -u

ACTION="${1:-}"
LOCK_DIR="${AWAKE_STATE_DIR:-$HOME/.claude/.awake}"
COUNTER_FILE="$LOCK_DIR/sessions"
LOCKFILE="$LOCK_DIR/lock"

mkdir -p "$LOCK_DIR"

# Portable file-based locking (no flock on macOS by default)
acquire_lock() {
  local attempts=0
  while ! mkdir "$LOCKFILE" 2>/dev/null; do
    attempts=$((attempts + 1))
    if [ "$attempts" -gt 50 ]; then
      echo "awake: could not acquire lock" >&2
      exit 1
    fi
    sleep 0.1
  done
}

release_lock() {
  rmdir "$LOCKFILE" 2>/dev/null
}

read_count() {
  if [ -f "$COUNTER_FILE" ]; then
    cat "$COUNTER_FILE"
  else
    echo 0
  fi
}

write_count() {
  echo "$1" > "$COUNTER_FILE"
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
    acquire_lock
    count=$(read_count)
    count=$((count + 1))
    write_count "$count"
    release_lock
    if [ "$count" -eq 1 ]; then
      amphetamine_start
    fi
    ;;
  stop)
    acquire_lock
    count=$(read_count)
    if [ "$count" -gt 0 ]; then
      count=$((count - 1))
    fi
    write_count "$count"
    release_lock
    if [ "$count" -eq 0 ]; then
      amphetamine_stop
    fi
    ;;
  status)
    echo "Active sessions: $(read_count)"
    ;;
  reset)
    acquire_lock
    write_count 0
    release_lock
    amphetamine_stop
    echo "awake: reset — all sessions cleared, Amphetamine session ended"
    ;;
  *)
    echo "Usage: awake.sh {start|stop|status|reset}" >&2
    exit 1
    ;;
esac
