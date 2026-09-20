# threads / resume

List and resume recent Claude Code, Codex, and Grok threads from the shell.

| File | Purpose |
|---|---|
| `threads.py` | Listing engine. Reads Claude (`~/.claude/projects`), Codex, and Grok session stores. `--json`, `--list`, `--pick`, `--find`, `--since`, `--agent`, `--here`, `--page/--limit`. |
| `threads.zsh` | `threads` and `resume` shell functions (functions so `cd` sticks). |
| `test_threads.py` / `test_resume.sh` | Tests. |

## Install

```zsh
# ~/.zshrc  (or: ./install → pick "threads")
export THREADS_CLAUDE_CMD=cc   # optional: your claude wrapper alias (default: claude)
export THREADS_CODEX_CMD=cdx   # optional (default: codex)
source /path/to/agents-shared/shell/threads/threads.zsh
```

Sourcing exports `THREADS_PY`, which the [threads skill](../../skills/threads/) uses.

## Usage

```
threads                      # picker: cd into the thread's dir and resume it
threads --since 2h           # picker, filtered (today | yesterday | 30m | 7d | YYYY-MM-DD)
threads --json --here        # JSON for agents/scripts
resume <id-or-alias>         # newest match across agents
resume cdx <id>              # scope to codex (cc|claude|cdx|codex|grok)
resume                       # newest thread started in $PWD (or cmux surface binding)
```

## Tests

```
python3 shell/threads/test_threads.py
zsh shell/threads/test_resume.sh
```
