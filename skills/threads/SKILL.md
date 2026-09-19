---
name: threads
description: >
  List recent Claude (cc), Codex (cdx), and Grok threads, optionally filtered
  by relative time or date. Use when the user runs /threads, asks for recent
  threads/sessions, wants to resume a past conversation, or names a window
  like today, yesterday, 2h, 30m, 7d, or YYYY-MM-DD.
argument-hint: "[today | yesterday | 2h | 7d | YYYY-MM-DD | --page N | --json]"
---

# threads

List the user's recent coding-agent threads. Do not resume them from this
session — that nests a TUI inside the current agent.

## Command

```bash
python3 "${THREADS_PY:-$HOME/.zsh/includes/zsh_ai_threads.py}" --json [filters]
```

Always pass `--json`. Never run the picker (`threads` with no flags, or `--pick`).
Never run `resume`. The interactive CLI is for the user's own terminal: it cds
and resumes with `cc` / `cdx` / `grok`.

If they already have an id or title/alias, tell them to run this in their terminal:

```bash
resume <id-or-alias>
resume cdx <id-or-alias>
```

## Filters (map `$ARGUMENTS`)

| User says | Flag |
|---|---|
| (none) | `--limit 20` (page 1, default) |
| more / next / page 2 | `--page 2` (keep `--limit`) |
| page N | `--page N` |
| today / yesterday / 2h / 30m / 7d / YYYY-MM-DD | `--since <that>` |
| claude / cc | `--agent claude` |
| codex / cdx / code | `--agent codex` |
| grok | `--agent grok` |
| this repo only | `--here` |

Combine freely: `--json --since today --agent claude --here --page 2`.

If a page has exactly `--limit` rows, more pages may exist — re-run with `--page N+1`. Stderr prints `page 1/4  (20 of 73)  next: threads --json --page 2` when there is another page.

## Output

JSON array of `{agent, id, title, cwd, updated_at, resume}` for the current
page only. Show a compact table (agent, relative time, directory name, title).
If stderr reports another page and the user wants it, fetch `--page N`. If they
want to open one, tell them to run `threads` in their terminal (or
`threads --since …` with the same filter).
