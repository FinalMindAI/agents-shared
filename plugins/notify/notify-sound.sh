#!/bin/bash
# Shared notification hook for Claude Code and Codex CLI.
# Claude Code sends JSON on stdin. Codex appends a legacy JSON payload as the last argv item.
# Clicking the notification activates your terminal app.
# To make sticky: System Settings > Notifications > terminal-notifier > Alerts

set -u

# Guard against recursive hook calls
if [ -n "${CLAUDE_NOTIFY_RUNNING:-}" ] || [ -n "${CODEX_NOTIFY_RUNNING:-}" ]; then
  exit 0
fi
export CLAUDE_NOTIFY_RUNNING=1
export CODEX_NOTIFY_RUNNING=1

# Resolve plugin root (directory containing this script)
PLUGIN_ROOT="$(cd "$(dirname "$0")" && pwd)"

# Load defaults, then user overrides
source "$PLUGIN_ROOT/config.defaults.sh"
if [ -f "$HOME/.claude/notify-config.sh" ]; then
  source "$HOME/.claude/notify-config.sh"
fi
if [ -f "$HOME/.codex/notify-config.sh" ]; then
  source "$HOME/.codex/notify-config.sh"
fi

# Auto-detect terminal app bundle ID if not set
if [ -z "$NOTIFY_TERMINAL_APP" ]; then
  case "${TERM_PROGRAM:-}" in
    ghostty)      NOTIFY_TERMINAL_APP="com.mitchellh.ghostty" ;;
    iTerm.app)    NOTIFY_TERMINAL_APP="com.googlecode.iterm2" ;;
    Apple_Terminal) NOTIFY_TERMINAL_APP="com.apple.Terminal" ;;
    WezTerm)      NOTIFY_TERMINAL_APP="com.github.wez.wezterm" ;;
    vscode)       NOTIFY_TERMINAL_APP="com.microsoft.VSCode" ;;
    Alacritty)    NOTIFY_TERMINAL_APP="org.alacritty" ;;
    tmux)         NOTIFY_TERMINAL_APP="com.mitchellh.ghostty" ;; # common default
    *)            NOTIFY_TERMINAL_APP="com.apple.Terminal" ;;
  esac
fi

# Check for terminal-notifier
if ! command -v terminal-notifier &>/dev/null; then
  echo "notify plugin: terminal-notifier not found. Install with: brew install terminal-notifier" >&2
  exit 1
fi

# Parse hook input from stdin for Claude, or last argv for Codex.
INPUT="$(cat)"
ARG_PAYLOAD="${*: -1}"

if [ -n "$INPUT" ]; then
  SOURCE="claude"
elif [ -n "$ARG_PAYLOAD" ]; then
  INPUT="$ARG_PAYLOAD"
  SOURCE="codex"
else
  exit 0
fi

read_json() {
  local expr="$1"
  python3 -c "import json,sys; data=json.loads(sys.stdin.read()); value=$expr; print('' if value is None else value)" <<<"$INPUT" 2>/dev/null
}

MESSAGE="$(read_json "data.get('message') or data.get('last-assistant-message') or 'Needs your attention'")"
TYPE="$(read_json "data.get('notification_type') or data.get('hook_event_name') or data.get('type') or ''")"
SESSION_ID="$(read_json "data.get('session_id', '')")"
TRANSCRIPT="$(read_json "data.get('transcript_path', '')")"
CLIENT="$(read_json "data.get('client', '')")"
FIRST_INPUT_MESSAGE="$(python3 -c "import json,sys; data=json.loads(sys.stdin.read()); msgs=data.get('input-messages') or []; print(msgs[0] if msgs else '')" <<<"$INPUT" 2>/dev/null)"

# Cache directory for generated topics
if [ "$SOURCE" = "codex" ]; then
  CACHE_DIR="${CODEX_HOME:-$HOME/.codex}/.topic-cache"
else
  CACHE_DIR="$HOME/.claude/.topic-cache"
fi
mkdir -p "$CACHE_DIR"

TOPIC=""

# Check cache first
if [ -n "$SESSION_ID" ] && [ -f "$CACHE_DIR/$SESSION_ID" ]; then
  TOPIC=$(cat "$CACHE_DIR/$SESSION_ID")
fi

# Generate topic if not cached and enabled.
# Claude can summarize from transcript; Codex falls back to the first input message.
if [ -z "$TOPIC" ] && [ "$NOTIFY_TOPIC_ENABLED" = "true" ] && [ -n "$TRANSCRIPT" ] && [ -f "$TRANSCRIPT" ]; then
  # Extract first real user message (skip meta entries, commands, and tool results)
  FIRST_MSG=$(python3 -c "
import json
with open('$TRANSCRIPT') as f:
    for line in f:
        d = json.loads(line)
        if d.get('type') != 'user' or d.get('isMeta'):
            continue
        msg = d.get('message', {})
        content = msg.get('content', '') if isinstance(msg, dict) else str(msg)
        # Skip tool-result messages (content is a list of tool_result dicts)
        if isinstance(content, list):
            continue
        if not isinstance(content, str):
            continue
        text = content.strip()
        # Skip slash commands and empty messages
        if not text or text.startswith('<command-name>'):
            continue
        print(text[:200])
        break
" 2>/dev/null)

  if [ -n "$FIRST_MSG" ]; then
    # Try to generate a short topic with Haiku via claude CLI
    TOPIC=$(echo "$FIRST_MSG" | env -u CLAUDECODE CLAUDE_NOTIFY_RUNNING=1 claude -p --model "$NOTIFY_TOPIC_MODEL" "Summarize the following user message in 5 words or fewer as a short topic title. Output ONLY the title, nothing else." 2>/dev/null | head -1)

    # Fall back to truncated first message
    if [ -z "$TOPIC" ]; then
      TOPIC="${FIRST_MSG:0:60}"
    fi

    # Cache the topic
    if [ -n "$SESSION_ID" ] && [ -n "$TOPIC" ]; then
      echo "$TOPIC" > "$CACHE_DIR/$SESSION_ID"
    fi
  fi
fi

if [ -z "$TOPIC" ] && [ -n "$FIRST_INPUT_MESSAGE" ]; then
  TOPIC="${FIRST_INPUT_MESSAGE:0:60}"
fi

# Event type as part of title
if [ "$SOURCE" = "codex" ] || [ -n "$CLIENT" ]; then
  TITLE="Codex"
  if [ -n "$CLIENT" ]; then
    TITLE="Codex (${CLIENT})"
  fi
  case "$TYPE" in
    agent-turn-complete) TITLE="$TITLE - Done" ;;
  esac
else
  case "$TYPE" in
    permission_prompt) TITLE="Claude Code — Permission" ;;
    idle_prompt)       TITLE="Claude Code — Input" ;;
    auth_success)      TITLE="Claude Code — Auth" ;;
    Stop)              TITLE="Claude Code — Done" ;;
    *)                 TITLE="Claude Code" ;;
  esac
fi

ARGS=(-title "$TITLE" -message "$MESSAGE" -sound "$NOTIFY_SOUND" -activate "$NOTIFY_TERMINAL_APP" -group "$NOTIFY_GROUP")

if [ -n "$TOPIC" ]; then
  ARGS+=(-subtitle "$TOPIC")
fi

terminal-notifier "${ARGS[@]}"
