# threads / resume — list and resume recent Claude, Codex, and Grok threads.
# version: 1.0.0
#
#   source /path/to/agents-shared/shell/threads/threads.zsh   # in ~/.zshrc
#
#   threads                  # interactive picker -> cd + resume
#   threads --json [...]     # machine-readable (what the /threads skill uses)
#   resume <id-or-alias>     # skip the picker
#   resume cdx <id>          # scope lookup to one agent
#   resume                   # newest thread whose cwd is $PWD
#
# Functions (not scripts) so the `cd` sticks in the calling shell.
# Listing engine: threads.py next to this file; exported as $THREADS_PY so
# the Claude Code /threads skill can find it.

export THREADS_PY="${${(%):-%x}:A:h}/threads.py"
THREADS_ZSH_VERSION="$(sed -n 's/^# version:[[:space:]]*//p' "${${(%):-%x}:A}" | head -1)"

# Launch commands per agent. Override before sourcing if you use wrappers
# (e.g. THREADS_CLAUDE_CMD=cc for `alias cc="claude --permission-mode auto"`).
: "${THREADS_CLAUDE_CMD:=claude}"
: "${THREADS_CODEX_CMD:=codex}"
: "${THREADS_GROK_CMD:=grok}"

# Sets `reply` to python argv for looking up one thread.
# Usage: resume [<cc|cdx|grok>] <id-or-alias>
_ai_resume_py_args() {
    reply=()
    if (( $# == 0 )); then
        return 1
    fi
    if (( $# >= 2 )) && [[ $1 == (cc|claude|cdx|codex|code|grok|gx) ]]; then
        reply=(--agent "$1" --find "$2")
        return 0
    fi
    reply=(--find "$1")
}

_ai_launch_thread() {
    local row="$1"
    local cwd agent id
    [[ -n "$row" ]] || return 1
    cwd="$(print -r -- "$row" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("cwd") or "")')"
    agent="$(print -r -- "$row" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("agent") or "")')"
    id="$(print -r -- "$row" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("id") or "")')"
    if [[ -n "$cwd" && -d "$cwd" ]]; then
        cd "$cwd" || return
    elif [[ -n "$cwd" ]]; then
        print -u2 -- "resume: cwd $cwd is missing; resuming from $PWD"
    fi
    # eval so the *_CMD vars may be aliases or multi-word commands.
    case "$agent" in
        claude) eval "$THREADS_CLAUDE_CMD --resume ${(q)id}" ;;
        codex)  eval "$THREADS_CODEX_CMD resume ${(q)id}" ;;
        grok)   eval "$THREADS_GROK_CMD --resume ${(q)id}" ;;
        *)
            print -u2 -- "resume: unknown agent ${agent:-empty}. Re-run with --json and pick the id yourself."
            return 1
            ;;
    esac
}

threads() {
    if [[ "$1" == --version ]]; then
        print -- "threads $THREADS_ZSH_VERSION"
        return 0
    fi
    local -a pass=()
    local a json=0 list=0 pick=0
    for a in "$@"; do
        case "$a" in
            --json|--find) json=1 ;;
            --list) list=1 ;;
            --pick) pick=1 ;;
        esac
        pass+=("$a")
    done
    if (( json || list || pick )) || [[ "$1" == -h || "$1" == --help ]]; then
        python3 "$THREADS_PY" "${pass[@]}"
        return
    fi
    local row
    row="$(python3 "$THREADS_PY" --pick "${pass[@]}")" || return $?
    _ai_launch_thread "$row"
}

resume() {
    if [[ "$1" == --version ]]; then
        print -- "resume $THREADS_ZSH_VERSION"
        return 0
    fi
    if [[ "$1" == -h || "$1" == --help ]]; then
        print -- "Usage: resume [<cc|cdx|grok>] <id-or-alias>"
        print -- "       resume                 # newest thread whose cwd is \$PWD"
        print -- "       resume -i | --pick     # interactive picker (same as threads)"
        return 0
    fi
    if [[ "$1" == -i || "$1" == --pick ]]; then
        threads
        return
    fi
    if (( $# == 0 )); then
        local row
        # Inside a cmux pane, prefer the session that was running in *this* surface
        # over "newest in $PWD" — two sessions in one dir would otherwise collide.
        if [[ -n "$CMUX_SURFACE_ID" ]] && command -v cmux >/dev/null; then
            row="$(cmux surface resume get --json 2>/dev/null | python3 -c '
import json, sys
try:
    b = json.load(sys.stdin).get("resume_binding") or {}
except Exception:
    b = {}
agent, cid = b.get("kind"), b.get("checkpoint_id")
if agent in ("claude", "codex", "grok") and cid:
    print(json.dumps({"agent": agent, "id": cid, "cwd": b.get("cwd") or ""}))
')"
            if [[ -n "$row" ]]; then
                _ai_launch_thread "$row"
                return
            fi
        fi
        row="$(python3 "$THREADS_PY" --json --here --limit 1 2>/dev/null \
            | python3 -c 'import json,sys; rows=json.load(sys.stdin); print(json.dumps(rows[0]) if rows else "")')" || return $?
        if [[ -z "$row" ]]; then
            print -u2 -- "resume: no threads started in $PWD; opening the picker."
            threads
            return
        fi
        _ai_launch_thread "$row"
        return
    fi
    local -a py_args
    _ai_resume_py_args "$@" || return 1
    py_args=("${reply[@]}")
    local row
    row="$(python3 "$THREADS_PY" "${py_args[@]}")" || return $?
    _ai_launch_thread "$row"
}
