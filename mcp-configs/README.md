# MCP server configs

One `<name>.json` per MCP server. Each file is the server config passed to
`claude mcp add-json`, plus an optional `description` field (stripped before
install, used only for the picker).

Register / remove them with the `mise` front door:

```bash
mise up                    # register every server in this dir (user scope)
mise run up -- slack gmail  # register specific ones
mise down                  # remove every server in this dir
mise run down -- slack      # remove specific ones
```

Under the hood these call `./install mcp up|down`, and `./install get`
(and the `install:all` / group tasks) also register any MCP whose name
appears in a `groups.json` group.

## File format

`stdio` server:

```json
{
  "description": "What this server is",
  "type": "stdio",
  "command": "npx",
  "args": ["-y", "some-mcp-server"],
  "env": { "SOME_TOKEN": "${SOME_TOKEN}" }
}
```

`http` server:

```json
{
  "description": "What this server is",
  "type": "http",
  "url": "https://example.com/mcp"
}
```

## Rules

- **Secrets go through env vars** (`"${SLACK_BOT_TOKEN}"`), never inline. Claude
  Code expands `${VAR}` at runtime.
- Files shipped here are **templates** with `"command": "REPLACE_ME"`. Install
  refuses to register a config that still contains `REPLACE_ME` — fill in the
  real command/url first.
- Verify the package/server name against official docs before committing it.
