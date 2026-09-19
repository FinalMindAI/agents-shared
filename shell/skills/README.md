# skills

Audit where coding-agent skills live (canonical synced dir, `~/.claude/skills`, `~/.codex/skills`, `~/.grok/skills`, `~/.agents`, `~/.cursor`, project-local) and how they are linked; sync a skill into the canonical dir or link it into an agent's dir.

| File | Purpose |
|---|---|
| `skills.py` | Engine. `--json`, `--list`, `--issues`, `--agent`, `--cwd`, `info|sync|link <name>`. Interactive picker needs `fzf`. |
| `skills.zsh` | `skills` shell function; exports `SKILLS_PY` for the [audit-skills skill](../../skills/audit-skills/). |

## Install

```zsh
# ~/.zshrc  (or: ./install → pick "skills")
export SKILLS_CANONICAL_DIR=~/Dropbox/dev/agents/skills   # optional; this is the default
source /path/to/agents-shared/shell/skills/skills.zsh
```

## Usage

```
skills                    # fzf picker; ←/→ tabs: all / dropbox / cc / cdx / grok / local
skills --list --issues    # table of broken / unlinked / partial / canonical-only
skills --json             # for agents
skills info <name>
skills sync <name>        # copy into canonical dir and replace source with symlink
skills link <name>        # symlink from canonical dir into agent dirs
skills --version
```
