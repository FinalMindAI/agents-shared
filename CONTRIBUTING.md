# Contributing to agents-shared

How to add a **skill**, an **MCP server**, a **setup profile**, or **port a skill** from another repo. The `install` bash CLI (wrapped by `mise`) is the front door; everything below is data you add plus one install command.

## Add a skill

A skill is a directory under [`skills/`](skills/) containing a `SKILL.md` with YAML frontmatter.

1. Create `skills/<name>/SKILL.md`:
   ```markdown
   ---
   name: my-skill
   description: One line describing when to use it (this is what the model matches on).
   ---

   # My Skill
   Instructions the model follows when the skill triggers…
   ```
   Add any helper files (scripts, templates, a `workflows/*.mjs` sweep) alongside it.
2. (Optional) Add it to a bundle: list the name in a [`profiles.json`](profiles.json) profile and/or a [`groups.json`](groups.json) group.
3. Install it:
   ```bash
   ./install                     # interactive picker, or
   ./install get my-skill        # user scope (~/.claude/skills), or
   ./install get --scope project my-skill
   ```

Skills install as symlinks from `~/.claude/skills/<name>` → this repo, so edits are live without re-installing.

## Add an MCP server

MCP servers are **data** in `mcp/<group>.group.json` (the group is the filename stem, e.g. `personal`, `infra`). stdio servers run from repo-vendored runtimes via `mcp/with-env`; http servers register as a URL. Everything installs at **user scope** so it resolves from any cwd.

### 1. Add the manifest entry

Pick the shape by transport:

```jsonc
// stdio server (a local process Claude Code spawns)
"my-server": {
  "kind": "stdio",
  "runtime": "node",            // "node" → node_modules/.bin,  "python" → .venv/bin
  "bin": "my-mcp-binary",       // the console-script / bin name
  "args": ["--transport", "stdio"],
  "needs_env": ["MY_TOKEN"],    // secrets it reads (documentation; sourced from .env at launch)
  "env": { "PORT": "39271" },   // committed, machine-agnostic config → passed via `claude mcp add -e`
  "auth": {                     // optional; drives `./install mcp auth`
    "type": "cmd",              // run a login if a check fails …
    "check": ["mycli", "whoami"],
    "login": ["mycli", "login"]
  },
  "_desc": "What it does.",
  "_prereq": "One-time setup the user must do."
}
```

```jsonc
// http server (remote; Claude Code calls it directly, no local process)
"my-http": {
  "kind": "http",
  "url": "https://mcp.example.com/mcp",
  "headers": { "Authorization": "Bearer ${MY_HTTP_TOKEN}" },  // optional; see the header-secret caveat
  "needs_env": ["MY_HTTP_TOKEN"],
  "auth": { "type": "manual", "instructions": "Run /mcp in Claude Code and complete OAuth." },
  "_desc": "…", "_prereq": "…"
}
```

Field reference: `kind` (`stdio`|`http`), `runtime` (`node`|`python`, stdio only), `bin`, `args`, `url`, `headers`, `env` (non-secret config → stored in `~/.claude.json`), `needs_env` (secrets, documentation), `auth` (`{type:cmd, check, login}` or `{type:manual, instructions}`), `_desc`/`_prereq` (docs). Auth argv supports `$VAR` expansion from `.env`.

### 2. Vendor the runtime (stdio only)

Pin the package so it runs locally instead of `npx -y`/`uvx` pulling latest each launch:
- **node** → add to [`package.json`](package.json) `dependencies` (bin lands in `node_modules/.bin/`)
- **python** → add to [`mcp/requirements.txt`](mcp/requirements.txt) (console script lands in `.venv/bin/`)

Then: `./install mcp deps` (runs `pnpm install` + `uv` into the repo).

### 3. Secrets

`mcp/with-env` loads `.env` (gitignored) at launch, using each server's **own** env-var names — so **no token lands in `~/.claude.json`**. Copy [`.env.example`](.env.example) → `.env` and set values as either literals or 1Password references:
```
MY_TOKEN=op://Vault/my-server/token     # resolved via `op run` at launch
```
Rule of thumb: **secrets and per-machine values** (tokens, `AWS_PROFILE`, absolute paths) go in `.env`; **committed machine-agnostic constants** (ports, flags) go in the manifest `env` block.

> **http header-secret caveat:** http headers are sent by Claude Code directly, *not* through `mcp/with-env`, so a `headers` token (`${VAR}`) must be **exported in the shell that launches Claude Code** (e.g. `export MY_HTTP_TOKEN=$(op read …)` in your rc), not just in `.env`. See `github`/`langfuse-prod` for the pattern.

### 4. Register + connect

```bash
./install mcp get my-server        # register at user scope (idempotent; --force to re-add)
./install mcp auth my-server       # run its auth recipe (or print the manual step)
```
Then **restart Claude Code** (servers load at session start) and run `/mcp` to connect. `./install mcp list` shows registration status; `./install mcp setup` does deps + register-all + auth-all in one shot.

## Add a setup profile

[`profiles.json`](profiles.json) maps a name to a bundle of skills/plugins (`items`) and MCP servers (`mcp`). `extends` merges a base; `"*"` means everything available.
```jsonc
"alex": { "extends": "default", "items": ["my-skill"], "mcp": ["my-server"] }
```
Wire it to a task in [`mise.toml`](mise.toml) (`[tasks."setup:alex"]` → `install setup alex`) so `mise setup:alex` bootstraps it.

## Port a skill from another repo

Skills written for another org (e.g. the `mt-*` skills tuned for Modern Treasury) need **de-coupling** before they work here:

1. Copy `skills/<name>/` in (drop the `mt-` prefix; keep any `workflows/*.mjs`).
2. Rewrite infra assumptions for Quovy: **Buildkite → GitHub Actions** (`gh run`/`gh pr`), MT repo/branch conventions → Quovy's, and **`mcp__engineering__*` aggregator tool names → our standalone MCP tools** (`mcp__github__*`, `mcp__linear__*`, `mcp__gmail__*`, `mcp__slack__*`, …).
3. Drop MT-only dependencies (Snowflake, PagerDuty, cell-router) unless we have an equivalent.
4. Update the frontmatter `description`/triggers, verify against a real Quovy repo, and add it to a profile.

## Conventions

- One logical change per PR; feature branch → PR (work repos stop at PR for human review).
- Commit format: `<type>: <description>` (`feat`, `fix`, `refactor`, `docs`, `test`, `chore`).
- Never commit secrets — `.env`, `node_modules/`, and `.venv/` are gitignored; `pnpm-lock.yaml` **is** committed.
