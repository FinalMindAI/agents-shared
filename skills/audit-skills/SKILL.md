---
name: audit-skills
description: >
  Audit where coding-agent skills live (Dropbox canonical, user dirs, this
  repo) and how they are linked. Use when the user runs /audit-skills, asks
  which skills are local vs user vs Dropbox, wants to sync a skill into
  Dropbox, or asks "where is this skill?"
argument-hint: "[name | --issues | --json]"
---

# audit-skills

Map the user's skill layout. Do not run the interactive picker.

## Command

```bash
python3 "${SKILLS_PY:-$HOME/.zsh/includes/zsh_ai_skills.py}" --json [filters]
```

Always pass `--json` from an agent. Humans run `skills` in a terminal (fzf,
same picker as `q`). ←/→ switch location tabs (`all` first, then dropbox / cc /
cdx / grok / local). Enter = info, `ctrl-s` = sync, `ctrl-l` = link, `ctrl-o` =
open.

## Filters (map `$ARGUMENTS`)

| User says | Flag |
|---|---|
| (none) | `--json` (full catalog) |
| issues / unlinked / broken | `--issues` |
| a skill name | `info <name> --json` |
| this repo only | `--scope local` |
| user / dropbox | `--scope user` / `--scope dropbox` |

```bash
python3 "$HOME/.zsh/includes/zsh_ai_skills.py" --json --issues
python3 "$HOME/.zsh/includes/zsh_ai_skills.py" info threads --json
```

## Output

Each skill: `{name, status, description, canonical, locations[]}`.

Statuses: `linked`, `partial`, `unlinked`, `external`, `local`, `dropbox-only`, `broken`, `mixed`.

Show a compact table (status, name, dropbox yes/no, user agents, local). If they
want to sync or inspect one, tell them to run `skills` (or `skills info <name>`)
in their terminal. Do not `sync`/`link` from this session unless they explicitly
ask you to, and then pass `--yes`.
