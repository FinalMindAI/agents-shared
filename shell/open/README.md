# open shim

`open <number>` opens that GitHub PR in the browser via `gh pr view --web`; everything else falls through to the system `open`.

```zsh
# ~/.zshrc  (or: ./install → pick "open")
source /path/to/agents-shared/shell/open/open.zsh
```

```
open 1156        -> gh pr view 1156 --web
open .           -> /usr/bin/open .
command open 123 -> force the real open
```
