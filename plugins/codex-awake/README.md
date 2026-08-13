# Codex Awake

Codex wrapper for the shared Amphetamine keep-awake helper in [`../awake`](../awake/).

## Install

Create `~/.codex/hooks.json` with the absolute path to this wrapper:

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
    "Stop": [
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

## Notes

- The wrapper delegates to `plugins/awake/awake.sh`, so Claude Code and Codex share the same session counter.
- Amphetamine must be running for the helper to start or end a keep-awake session.
