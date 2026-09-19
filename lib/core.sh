# lib/core.sh — shared paths, colors, and low-level helpers. Sourced by ./install.
# shellcheck shell=bash

REPOS_DIR="$ROOT_DIR/repos"
SKILLS_DIR="$ROOT_DIR/skills"
PLUGINS_DIR="$ROOT_DIR/plugins"
MCP_DIR="$ROOT_DIR/mcp"
SOURCES_FILE="$ROOT_DIR/sources.json"
GROUPS_FILE="$ROOT_DIR/groups.json"
USER_SKILLS_DIR="$HOME/.claude/skills"
SHELL_DIR="$ROOT_DIR/shell"
ZSHRC="${ZDOTDIR:-$HOME}/.zshrc"

# Colors / escapes
BOLD='\033[1m'
DIM='\033[2m'
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
CYAN='\033[0;36m'
RED='\033[0;31m'
RESET='\033[0m'
HIDE_CURSOR='\033[?25l'
SHOW_CURSOR='\033[?25h'
ALT_SCREEN_ON='\033[?1049h'
ALT_SCREEN_OFF='\033[?1049l'
CLEAR_SCREEN='\033[2J\033[H'

# ── helpers ──────────────────────────────────────────────────────────

ensure_sources_file() {
  if [ ! -f "$SOURCES_FILE" ]; then
    echo '{}' > "$SOURCES_FILE"
  fi
}

# Extract description from SKILL.md YAML frontmatter
skill_description() {
  local skill_md="$1"
  sed -n '/^---$/,/^---$/p' "$skill_md" \
    | grep -A1 '^description:' \
    | tail -1 \
    | sed 's/^[[:space:]]*//' \
    | head -c 80
}

# Extract description from plugin.json
plugin_description() {
  local plugin_dir="$1"
  local manifest="$plugin_dir/.claude-plugin/plugin.json"
  if [ -f "$manifest" ]; then
    python3 - "$manifest" 2>/dev/null <<'PY' || true
import json, sys
print(json.load(open(sys.argv[1])).get('description', '')[:80])
PY
  fi
}

is_claude_plugin_dir() {
  local plugin_dir="$1"
  [ -f "$plugin_dir/.claude-plugin/plugin.json" ] || [ -f "$plugin_dir/hooks/hooks.json" ]
}

MARKETPLACE_NAME="agents-shared"
MARKETPLACE_READY=0

# The current Claude CLI installs plugins by `name@marketplace`, never by filesystem
# path, so this repo must be a registered marketplace before any plugin can install.
# Register it (or refresh the cache if already registered). Idempotent; the guard makes
# it run at most once per invocation.
ensure_marketplace() {
  [ "$MARKETPLACE_READY" -eq 1 ] && return 0
  if claude plugin marketplace list 2>/dev/null | grep -q "❯ $MARKETPLACE_NAME"; then
    claude plugin marketplace update "$MARKETPLACE_NAME" >/dev/null 2>&1 || true
  else
    claude plugin marketplace add "$ROOT_DIR" >/dev/null 2>&1 || true
  fi
  MARKETPLACE_READY=1
}

is_codex_plugin_dir() {
  local plugin_dir="$1"
  [ -f "$plugin_dir/notify-sound.sh" ] || [ -f "$plugin_dir/awake.sh" ]
}

install_codex_notify() {
  local plugin_dir="$1"
  local codex_home="${CODEX_HOME:-$HOME/.codex}"
  local config_file="$codex_home/config.toml"
  local script_path
  script_path="$(resolve_path "$plugin_dir/notify-sound.sh")"

  mkdir -p "$codex_home"
  python3 - "$config_file" "$script_path" <<'PY'
from pathlib import Path
import re, sys

config_path = Path(sys.argv[1])
script_path = sys.argv[2]
line = f'notify = ["{script_path}"]'
text = config_path.read_text() if config_path.exists() else ""

pattern = re.compile(r'(?m)^notify\s*=\s*\[.*\]\s*$')
if pattern.search(text):
    text = pattern.sub(line, text, count=1)
else:
    if text and not text.endswith("\n"):
        text += "\n"
    if text:
        text += "\n"
    text += line + "\n"

config_path.write_text(text)
PY
  echo -e "    ${GREEN}✓${RESET} updated ~/.codex/config.toml notify hook"
}

install_codex_awake() {
  local plugin_dir="$1"
  local codex_home="${CODEX_HOME:-$HOME/.codex}"
  local hooks_file="$codex_home/hooks.json"
  local script_path
  script_path="$(resolve_path "$plugin_dir/awake.sh")"

  mkdir -p "$codex_home"
  python3 - "$hooks_file" "$script_path" <<'PY'
from pathlib import Path
import json, sys

hooks_path = Path(sys.argv[1])
script_path = sys.argv[2]
start_cmd = f"{script_path} start"
stop_cmd = f"{script_path} stop"

if hooks_path.exists():
    data = json.loads(hooks_path.read_text())
else:
    data = {}

hooks = data.setdefault("hooks", {})
session_start = hooks.setdefault("SessionStart", [])
stop = hooks.setdefault("Stop", [])

def has_command(groups, command):
    for group in groups:
        for hook in group.get("hooks", []):
            if hook.get("type") == "command" and hook.get("command") == command:
                return True
    return False

if not has_command(session_start, start_cmd):
    session_start.append({
        "hooks": [
            {
                "type": "command",
                "command": start_cmd,
            }
        ]
    })

if not has_command(stop, stop_cmd):
    stop.append({
        "hooks": [
            {
                "type": "command",
                "command": stop_cmd,
            }
        ]
    })

hooks_path.write_text(json.dumps(data, indent=2) + "\n")
PY
  echo -e "    ${GREEN}✓${RESET} updated ~/.codex/hooks.json SessionStart/Stop hooks"
}

# Check if a skill is installed in ~/.claude/skills/
is_user_skill_installed() {
  local name="$1"
  [ -e "$USER_SKILLS_DIR/$name" ] || [ -L "$USER_SKILLS_DIR/$name" ]
}

# Check if a plugin is installed via claude plugin list
is_user_plugin_installed() {
  local name="$1"
  claude plugin list 2>/dev/null | grep -q "❯ $name" 2>/dev/null
}

# Returns "user", "project", "both", or "" for a skill
skill_install_level() {
  local name="$1"
  local user_installed=0 project_installed=0
  [ -e "$USER_SKILLS_DIR/$name" ] || [ -L "$USER_SKILLS_DIR/$name" ] && user_installed=1
  local git_root
  git_root="$(git -C "$PWD" rev-parse --show-toplevel 2>/dev/null)" || true
  if [ -n "$git_root" ] && [ "$git_root" != "$ROOT_DIR" ]; then
    local proj_skills="$git_root/.claude/skills"
    ([ -e "$proj_skills/$name" ] || [ -L "$proj_skills/$name" ]) && project_installed=1
  fi
  if [ "$user_installed" -eq 1 ] && [ "$project_installed" -eq 1 ]; then
    echo "both"
  elif [ "$user_installed" -eq 1 ]; then
    echo "user"
  elif [ "$project_installed" -eq 1 ]; then
    echo "project"
  fi
}

# Returns "user", "project", "both", or "" for a plugin
plugin_install_level() {
  local name="$1"
  local user_installed=0 project_installed=0
  claude plugin list --scope user 2>/dev/null | grep -q "❯ $name" 2>/dev/null && user_installed=1 || true
  local git_root
  git_root="$(git -C "$PWD" rev-parse --show-toplevel 2>/dev/null)" || true
  if [ -n "$git_root" ] && [ "$git_root" != "$ROOT_DIR" ]; then
    claude plugin list --scope project 2>/dev/null | grep -q "❯ $name" 2>/dev/null && project_installed=1 || true
  fi
  if [ "$user_installed" -eq 1 ] && [ "$project_installed" -eq 1 ]; then
    echo "both"
  elif [ "$user_installed" -eq 1 ]; then
    echo "user"
  elif [ "$project_installed" -eq 1 ]; then
    echo "project"
  fi
}

# Build a green "✓ installed (level)" tag fragment
installed_tag() {
  local level="$1"
  case "$level" in
    both)    printf '\033[0;32m✓ installed (user+project)\033[0m' ;;
    user)    printf '\033[0;32m✓ installed (user)\033[0m' ;;
    project) printf '\033[0;32m✓ installed (project)\033[0m' ;;
  esac
}

# Detect skills in a repo (directories containing SKILL.md)
detect_skills() {
  local repo_dir="$1"
  local skills=()
  for dir in "$repo_dir"/*/; do
    if [ -f "$dir/SKILL.md" ]; then
      local name
      name="$(basename "$dir")"
      [ "$name" = "node_modules" ] && continue
      skills+=("$name")
    fi
  done
  # Also check repo root for SKILL.md — but only if no subdirectory skills found
  # (repos like gstack put a duplicate SKILL.md at root for the browse skill)
  if [ -f "$repo_dir/SKILL.md" ] && [ ${#skills[@]} -eq 0 ]; then
    local root_name
    root_name="$(basename "$repo_dir")"
    skills+=(".:$root_name")
  fi
  printf '%s\n' "${skills[@]+"${skills[@]}"}"
}

# Detect plugins in a repo (Claude plugins or Codex helper directories)
detect_plugins() {
  local repo_dir="$1"
  local plugins=()
  for dir in "$repo_dir"/*/; do
    if is_claude_plugin_dir "$dir" || is_codex_plugin_dir "$dir"; then
      plugins+=("$(basename "$dir")")
    fi
  done
  printf '%s\n' "${plugins[@]+"${plugins[@]}"}"
}

# ── shell helpers (shell/<name>/<name>.zsh, sourced from ~/.zshrc) ────
#
# Each helper defines a function `<name>` that answers `<name> --version`
# with "<name> X.Y.Z"; the repo copy's version is the `# version:` header of
# its entry file. Status is probed by running `<name> --version` in the
# user's interactive zsh, so it doesn't matter where or how they source it —
# a copy in their own dotfiles counts the same as one installed by us.
# The tagged ~/.zshrc line only exists so install/remove are idempotent.

shell_entry() { echo "$SHELL_DIR/$1/$1.zsh"; }
shell_marker() { echo "# agents-shared:shell:$1"; }
expand_home() { local p="$1"; p="${p/#\~/$HOME}"; echo "${p/#\$HOME/$HOME}"; }

# "1.2.3" from a file's `# version:` header, or "" if none
file_version() {
  [ -f "$1" ] && sed -n 's/^# version:[[:space:]]*//p' "$1" | head -1
}

shell_description() {
  local readme="$SHELL_DIR/$1/README.md"
  [ -f "$readme" ] && grep -v '^#' "$readme" | grep -v '^$' | head -1 | head -c 80
}

# Is the tagged source line present in ~/.zshrc?
shell_managed() {
  [ -f "$ZSHRC" ] && grep -qF "$(shell_marker "$1")" "$ZSHRC" 2>/dev/null
}

# Version the live shell reports for `<name> --version`, or "" if the
# function isn't available (or doesn't answer in that format).
shell_live_version() {
  command -v zsh >/dev/null 2>&1 || return 0
  zsh -ic "$1 --version" 2>/dev/null </dev/null \
    | sed -n "s/^$1 \([0-9][0-9A-Za-z.+-]*\).*$/\1/p" | head -1
}

# Status fragment for the picker/list:
#   ""                                  not available in the shell
#   "✓ v1.0.0"                          available, same version as repo
#   "✓ v0.9.0 → v1.0.0 available"       available, repo is newer
#   "✓ v1.1.0 (repo has v1.0.0)"        available, newer than repo
shell_status() {
  local name="$1"
  local repo_ver live_ver
  repo_ver="$(file_version "$(shell_entry "$name")")"
  live_ver="$(shell_live_version "$name")"
  [ -n "$live_ver" ] || return 0
  if [ "$live_ver" = "$repo_ver" ]; then
    printf '\033[0;32m✓ v%s\033[0m' "$live_ver"
  elif [ "$(printf '%s\n%s\n' "$live_ver" "$repo_ver" | sort -V | head -1)" = "$live_ver" ]; then
    printf '\033[0;33m✓ v%s → v%s available\033[0m' "$live_ver" "$repo_ver"
  else
    printf '\033[0;33m✓ v%s (repo has v%s)\033[0m' "$live_ver" "$repo_ver"
  fi
}

# Append/replace the tagged source line in ~/.zshrc
install_shell_helper() {
  local name="$1"
  local entry marker line
  entry="$(shell_entry "$name")"
  marker="$(shell_marker "$name")"
  line="source \"${entry/#$HOME/\$HOME}\"  $marker"
  touch "$ZSHRC"
  if shell_managed "$name"; then
    local tmp; tmp="$(mktemp)"
    grep -vF "$marker" "$ZSHRC" > "$tmp"; printf '%s\n' "$line" >> "$tmp"; mv "$tmp" "$ZSHRC"
    echo -e "  ${GREEN}✓${RESET} $name -> updated source line in ${ZSHRC/#$HOME/~}  ${DIM}(shell v$(file_version "$entry"))${RESET}"
  else
    printf '\n%s\n' "$line" >> "$ZSHRC"
    echo -e "  ${GREEN}✓${RESET} $name -> added to ${ZSHRC/#$HOME/~}  ${DIM}(shell v$(file_version "$entry"))${RESET}"
  fi
  local live_ver
  live_ver="$(shell_live_version "$name")"
  if [ -n "$live_ver" ] && [ "$live_ver" != "$(file_version "$entry")" ]; then
    echo -e "    ${YELLOW}⚠${RESET} your shell currently resolves \`$name\` to v$live_ver from elsewhere; whichever definition loads last wins."
  fi
}

remove_shell_helper() {
  local marker; marker="$(shell_marker "$1")"
  [ -f "$ZSHRC" ] && grep -qF "$marker" "$ZSHRC" || return 1
  local tmp; tmp="$(mktemp)"
  grep -vF "$marker" "$ZSHRC" > "$tmp"; mv "$tmp" "$ZSHRC"
}

# Detect skills in a repo (directories containing SKILL.md)
detect_skills() {
  local repo_dir="$1"
  local skills=()
  for dir in "$repo_dir"/*/; do
    if [ -f "$dir/SKILL.md" ]; then
      local name
      name="$(basename "$dir")"
      [ "$name" = "node_modules" ] && continue
      skills+=("$name")
    fi
  done
  # Also check repo root for SKILL.md — but only if no subdirectory skills found
  # (repos like gstack put a duplicate SKILL.md at root for the browse skill)
  if [ -f "$repo_dir/SKILL.md" ] && [ ${#skills[@]} -eq 0 ]; then
    local root_name
    root_name="$(basename "$repo_dir")"
    skills+=(".:$root_name")
  fi
  printf '%s\n' "${skills[@]+"${skills[@]}"}"
}

# Detect plugins in a repo (Claude plugins or Codex helper directories)
detect_plugins() {
  local repo_dir="$1"
  local plugins=()
  for dir in "$repo_dir"/*/; do
    if is_claude_plugin_dir "$dir" || is_codex_plugin_dir "$dir"; then
      plugins+=("$(basename "$dir")")
    fi
  done
  printf '%s\n' "${plugins[@]+"${plugins[@]}"}"
}
