# Claude Code Shared Skills & Plugins

A collection of shared skills, plugins, and notification helpers for Claude Code and Codex CLI.

## Quick start with mise

[mise](https://mise.jdx.dev) is the front door. It pins the one tool the installer needs (`python3`) and wraps the `install` CLI in named tasks:

```bash
mise setup                     # bootstrap the 'default' profile: skills/plugins + MCP + auth
mise setup:all                 # everything: every skill/plugin + every MCP server + auth
mise setup:ryan                # a named per-person profile (defined in profiles.json)

mise pick                      # interactive picker — hand-choose skills & plugins
mise run install:all           # install every first-party skill & plugin, no prompts
mise run install -- notify awake   # install specific items by name
mise run list                  # show what's installed
mise run update                # pull latest for cloned external repos
mise run add -- garrytan/gstack    # clone a GitHub repo and pick items

mise run mcp                       # list MCP servers + registration status
mise run mcp:deps                  # install the vendored MCP runtimes (pnpm + uv)
mise run mcp:get -- gmail datadog  # register specific MCP servers (user scope)
mise run mcp:all                   # register every MCP server from mcp/*.group.json
mise run mcp:auth -- aws-mcp       # run a server's auth recipe (aws sso login, …)
mise run mcp:setup                 # one shot: deps + register all + auth all
```

Setup profiles are data-driven — [`profiles.json`](profiles.json) defines named bundles of skills/plugins + MCP servers (`default`, `ryan`, `all`), each installable with `mise setup:<name>` (`extends` merges a base; `"*"` means everything available). Item-only groups for `./install get --group <name>` live in [`groups.json`](groups.json).

## Installing skills & plugins from GitHub

Run `./install` to pick from everything available and install to `~/.claude/`:

```bash
./install                         # interactive picker for all skills & plugins
./install add garrytan/gstack     # clone a GitHub repo and pick items to add
./install list                    # show what's installed
./install remove <name>           # remove a skill or plugin
./install update                  # pull latest for all repos
```

External repos are cloned to `repos/<user>/<repo>/` (gitignored) and symlinked into `skills/` and `plugins/`. This keeps upstream repos intact for easy `git pull` updates.

## First-party skills

### [convert-video-to-sop](skills/convert-video-to-sop/)

Convert screen-recording videos (single file or directory) into illustrated Standard Operating Procedures, output as Word + PDF. Local preprocessing (ffmpeg scene detection, tesseract OCR, classified frame diffs with adaptive densification) keeps token cost ~90% below naive frame-by-frame analysis; estimates cost and confirms with the user before any AI spend. Requires `ffmpeg`, `tesseract`; `pandoc` + LibreOffice for docx/PDF; `whisper-cpp` optional for narrated videos.

### [pr-create](skills/pr-create/)

Run tests, lint, typecheck, and format for the changed stack (TypeScript/pnpm, Python/ruff), create a `<type>/<desc>` branch, commit, and open a PR with a description built from the full diff. Stops at PR creation for human review (pair with `pr-babysit` to watch CI). Writes the commit and PR under your name — no AI attribution, no em dashes.

### [email-cleanup](skills/email-cleanup/)

Drain a drifted Gmail inbox back to single/double digits: fan out parallel bucket sweeps that label + archive noise (never deletes), then close the loop by authoring the Gmail filters that stop the refill. Uses the standalone `gmail` MCP server — since that server exposes `create_filter`/`create_label`, the skill creates filters and labels directly (with confirmation), not just paste-ready specs. Maintains a per-mailbox `email_cleanup_status.md` (taxonomy, keep-exceptions, never-filter list) so state carries across runs and machines. Invoke explicitly (`disable-model-invocation`).

## Plugins

### [notify](plugins/notify/)

macOS notifications with sound, topic summarization, and terminal activation when Claude Code finishes or needs input.

### [awake](plugins/awake/)

Starts an Amphetamine session when the first Claude Code or Codex session begins, and ends it when the last one stops.

### [codex-notify](plugins/codex-notify/)

macOS notifications with sound and terminal activation when Codex CLI finishes a turn.

### [codex-awake](plugins/codex-awake/)

Codex hook wrapper for the shared Amphetamine keep-awake helper.

## MCP servers

agents-shared is also the place to install and **run** MCP servers. Servers are defined as data in [`mcp/*.group.json`](mcp/) (the group is the filename stem — `personal`, `infra`), and the vendored runtimes they need are pinned **in this repo** rather than pulled fresh by `npx`/`uvx` on every launch:

- node servers → [`package.json`](package.json) (pnpm) → `node_modules/.bin/*`
- python servers → [`mcp/requirements.txt`](mcp/requirements.txt) (uv) → `.venv/bin/*`

```bash
./install mcp deps                 # install vendored runtimes (pnpm install + uv venv)
./install mcp list                 # servers + whether each is registered
./install mcp get gmail datadog    # register named servers at user scope
./install mcp get --all            # register everything
./install mcp get --force azure    # re-register (remove + add)
```

stdio servers register at **user scope** as `mcp/with-env <local-binary> [args…]`, so they resolve from any cwd. http servers (e.g. datadog) register as a plain URL with `--transport http`.

### Secrets — `.env` + 1Password

`mcp/with-env` loads secrets from a gitignored `.env` at launch, so **no token is ever written to `~/.claude.json`**. Copy [`.env.example`](.env.example) to `.env`; each value is either a literal or a 1Password reference resolved on launch:

```
SLACK_MCP_XOXP_TOKEN=op://Private/slack-mcp/xoxp-token   # resolved via `op run`
AWS_PROFILE=quovy-readonly                               # literal (not a secret)
```

If `op` is installed and `.env` contains any `op://` reference, the wrapper runs the server under `op run --no-masking` (masking is disabled because MCP stdio speaks JSON-RPC over stdout, which masking would corrupt); otherwise it sources the literal values. `op` must be unlocked when a server launches.

### After registering: connect

Registration is not connection. Each server carries an `auth` recipe in its manifest, and `./install mcp auth [--all|<name…>]` drives it: it treats Claude Code's **connection state as the source of truth** (never re-runs a login for an already-connected server), runs the CLI login for the rest, and re-verifies.

| Server | Auth | Automated by `mcp auth`? |
|--------|------|---------------------------|
| `aws-mcp` | `aws sso login --profile $AWS_PROFILE` | ✅ runs it (browser device-auth) |
| `azure`   | `az login` | ✅ runs it (skipped once connected — the MCP works even for subscription-less accounts) |
| `datadog` / `sentry` / `linear` | OAuth via `/mcp` in Claude Code | ❌ manual — Claude Code owns the handshake |
| `gmail`   | Google Desktop OAuth client + one mint run | ❌ manual — Google Cloud console |

So `./install mcp setup` gets you all the way for the CLI-auth servers; `datadog` and `gmail` still need the one manual step above, after which you run `/mcp` in Claude Code to connect.

`aws-mcp` and `azure` are also defined in the `FinalMindAI/quovy` repo's committed `.mcp.json` at **project** scope (which wins inside that repo); the user-scope copies here are the fallback everywhere else.

### Servers that need extra setup

Most servers are one OAuth click in `/mcp`. Two need a one-time setup you do yourself — the exact steps live in each server's `auth.instructions` in its manifest, so **`./install mcp auth <name>` prints them as a checklist** (the wizard is the single source of truth; the summaries below mirror it).

**`gmail`** — one-time Google OAuth (~2 min):
1. [console.cloud.google.com](https://console.cloud.google.com) → pick/create a project.
2. **APIs & Services → Library** → enable the **Gmail API**.
3. **APIs & Services → Credentials → Create Credentials → OAuth client ID** (configure the consent screen as **External** + add yourself as a **Test user** if prompted) → application type **Desktop app** → **Download JSON**.
4. Save it at `~/.gmail-mcp/gcp-oauth.keys.json` (matches `GMAIL_OAUTH_PATH` in `.env`).
5. Mint credentials: `node_modules/.bin/gmail-mcp auth` — opens a browser for `gmail.modify` + `gmail.settings.basic`, writes `~/.gmail-mcp/credentials.json`.
6. Restart Claude Code → `/mcp`.

**http header-secret servers (`langfuse-prod`, `github`)** — these authenticate with a token in an HTTP **header**, which is the one case `mcp/with-env` can't cover: Claude Code sends http headers itself, not through the stdio wrapper. So the token has to be **exported in the shell that launches Claude Code** (best via `op` in your shell rc). The manifest stores only a `${VAR}` reference in the header, never the token.

- **`langfuse-prod`** (Basic auth): Langfuse (`langfuse.quovy.com`) → **Settings → API Keys** → copy public + secret → `printf '%s' 'PUBLIC:SECRET' | base64` → `export LANGFUSE_PROD_MCP_AUTH=$(op read 'op://<vault>/langfuse-prod-mcp/credential')` in your rc → `./install mcp get --force langfuse-prod` → restart + `/mcp`.
- **`github`** (Bearer PAT): the hosted `api.githubcopilot.com` endpoint rejects Claude Code's OAuth dynamic client registration, so use a PAT. github.com → **Settings → Developer settings → Personal access tokens** → fine-grained token with the scopes you need → `export GITHUB_MCP_PAT=$(op read 'op://<vault>/github-mcp/token')` in your rc → `./install mcp get github` → restart + `/mcp`.

Once exported, `./install mcp list` still shows these as "not registered" until you register them, and `/mcp` shows them connected once the header token resolves.

## Usage

Install a plugin:

```bash
claude plugin add /path/to/claude-code-shared/plugins/<plugin-name>
```

Or test locally:

```bash
claude --plugin-dir /path/to/claude-code-shared/plugins/<plugin-name>
```

For Codex CLI, add the helper script to `~/.codex/config.toml`:

```toml
notify = ["/path/to/claude-code-shared/plugins/codex-notify/notify-sound.sh"]
```

For Codex session start/stop hooks, create `~/.codex/hooks.json`:

```json
{
  "hooks": {
    "SessionStart": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "/path/to/claude-code-shared/plugins/codex-awake/awake.sh start"
          }
        ]
      }
    ],
    "Stop": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "/path/to/claude-code-shared/plugins/codex-awake/awake.sh stop"
          }
        ]
      }
    ]
  }
}
```
