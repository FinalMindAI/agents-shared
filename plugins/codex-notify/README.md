# Codex Notify

macOS notifications with sound for Codex CLI turn completion.

## What it does

- Plays a sound and shows a macOS notification when Codex finishes a turn
- Uses the final assistant message as the notification body
- Uses the first user message as a short subtitle
- Clicking the notification activates your terminal app
- Reuses the same `NOTIFY_*` sound settings as the shared `notify` plugin

## Limitation

Codex's documented `notify` integration runs on turn completion. It is not the same as Claude Code's hook system, so this does not currently fire on "needs input" or permission prompts.

## Requirements

- macOS
- [terminal-notifier](https://github.com/julienXX/terminal-notifier): `brew install terminal-notifier`
- Codex CLI

## Install

Add this to `~/.codex/config.toml` with the absolute path to the script in this repo:

```toml
notify = ["/path/to/agents-shared/plugins/codex-notify/notify-sound.sh"]
```

## Configuration

Create `~/.codex/notify-config.sh` to override Codex-specific values. If you already use `~/.claude/notify-config.sh`, Codex will inherit those sound settings automatically and then apply any Codex-specific overrides.

```bash
# macOS sound name (see /System/Library/Sounds/)
NOTIFY_SOUND="Ping"

# Terminal app bundle ID to activate on click
# Auto-detected from $TERM_PROGRAM if empty
NOTIFY_TERMINAL_APP="com.mitchellh.ghostty"

# Notification group ID
NOTIFY_GROUP="codex-cli"

# Max subtitle length from the first user message
NOTIFY_SUBTITLE_MAX=72
```

### Supported terminals

| `$TERM_PROGRAM` | Bundle ID |
|---|---|
| ghostty | `com.mitchellh.ghostty` |
| iTerm.app | `com.googlecode.iterm2` |
| Apple_Terminal | `com.apple.Terminal` |
| WezTerm | `com.github.wez.wezterm` |
| vscode | `com.microsoft.VSCode` |
| Alacritty | `org.alacritty` |
