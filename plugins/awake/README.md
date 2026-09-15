# Awake Plugin

Prevents your Mac from sleeping while any Claude Code or Codex session is active, using [Amphetamine](https://apps.apple.com/app/amphetamine/id937984704).

## How it works

- **Session markers** — One marker file per session id under `~/.claude/.awake/sessions.d/` tracks which agent sessions are running.
- **Start** — When the first session starts, an Amphetamine session is started via AppleScript.
- **SessionEnd** — When the last session ends, the Amphetamine session is ended.
- Multiple concurrent agent sessions are supported — Amphetamine stays active until all finish.

## Requirements

- [Amphetamine](https://apps.apple.com/app/amphetamine/id937984704) must be running (menu bar).

## Manual usage

```bash
# Start a keep-awake session
./awake.sh start

# Stop a keep-awake session
./awake.sh stop

# Check how many sessions are active
./awake.sh status

# Reset everything (end Amphetamine + clear markers)
./awake.sh reset
```

## Claude Code hooks

The plugin registers `SessionStart` and `SessionEnd` hooks automatically via `hooks/hooks.json`.

Note: bind teardown to `SessionEnd`, never `Stop` — `Stop` fires at the end of every assistant turn, which would end the Amphetamine session mid-work.

## Codex

Codex reads hooks from `hooks.json` in your Codex config directory, typically `~/.codex/hooks.json`.

Use the `codex-awake` wrapper there:

```json
{
  "hooks": {
    "SessionStart": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "/path/to/agents-shared/plugins/codex-awake/awake.sh start"
          }
        ]
      }
    ],
    "SessionEnd": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "/path/to/agents-shared/plugins/codex-awake/awake.sh stop"
          }
        ]
      }
    ]
  }
}
```
