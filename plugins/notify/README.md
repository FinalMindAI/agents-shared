# Notify Plugin

macOS notifications with sound, topic summarization, and terminal activation for Claude Code, plus Codex-aware labeling when the same helper is used from Codex CLI.

## Features

- Plays a sound and shows a macOS notification on Stop and Notification hooks
- Summarizes the session topic using Haiku (cached per session)
- Clicking the notification activates your terminal app
- Auto-detects your terminal from `$TERM_PROGRAM`
- Detects Codex payloads and titles notifications as `Codex` instead of `Claude Code`

## Requirements

- macOS
- [terminal-notifier](https://github.com/julienXX/terminal-notifier): `brew install terminal-notifier`
- Claude CLI (for topic summarization)

## Install

```bash
claude plugin add /path/to/claude-code-shared/plugins/notify
```

Or test locally:

```bash
claude --plugin-dir /path/to/claude-code-shared/plugins/notify
```

## Configuration

Create `~/.claude/notify-config.sh` to override defaults:

```bash
# macOS sound name (see /System/Library/Sounds/)
NOTIFY_SOUND="Ping"

# Terminal app bundle ID to activate on click
# Auto-detected from $TERM_PROGRAM if empty
NOTIFY_TERMINAL_APP="com.mitchellh.ghostty"

# Enable Haiku topic summarization (true/false)
NOTIFY_TOPIC_ENABLED=true

# Model for topic summarization
NOTIFY_TOPIC_MODEL="haiku"

# Notification group ID
NOTIFY_GROUP="claude-code"
```

### Supported terminals (auto-detected)

| `$TERM_PROGRAM` | Bundle ID |
|---|---|
| ghostty | `com.mitchellh.ghostty` |
| iTerm.app | `com.googlecode.iterm2` |
| Apple_Terminal | `com.apple.Terminal` |
| WezTerm | `com.github.wez.wezterm` |
| vscode | `com.microsoft.VSCode` |
| Alacritty | `org.alacritty` |

## Sticky notifications

To make notifications persist until dismissed:

**System Settings > Notifications > terminal-notifier > Alerts**
