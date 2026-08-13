# lib/commands.sh — item resolution + the picker/get/add/list/remove/clean/update commands. Sourced by ./install.
# shellcheck shell=bash

# ── default (interactive picker) ──────────────────────────────────────

# Resolve a path that may be a symlink to its absolute target
resolve_path() {
  local path="$1"
  if [ -L "$path" ]; then
    local dir link_target
    dir="$(cd "$(dirname "$path")" && pwd)"
    link_target="$(readlink "$path")"
    # Resolve relative symlink from its parent dir
    (cd "$dir" && cd "$(dirname "$link_target")" && echo "$(pwd)/$(basename "$link_target")")
  elif [ -d "$path" ]; then
    (cd "$path" && pwd)
  else
    # Regular file: resolve its parent dir, keep the basename
    echo "$(cd "$(dirname "$path")" && pwd)/$(basename "$path")"
  fi
}

# Ask a yes/no question, or auto-confirm when assume_yes is 1. Returns 0 for yes.
prompt_yes() {
  local assume_yes="$1" prompt="$2"
  if [ "$assume_yes" = "1" ]; then
    return 0
  fi
  echo -en "$prompt"
  local ans
  read -r ans
  [ "$ans" = "y" ] || [ "$ans" = "Y" ]
}

# Locate an item by name. Echoes "type|path" (type is skill or plugin), returns 1 if not found.
find_item() {
  local want="$1" dir type resolved
  for dir in "$SKILLS_DIR:skill" "$PLUGINS_DIR:plugin"; do
    type="${dir##*:}"
    local base="${dir%:*}"
    [ -e "$base/$want" ] || continue
    resolved="$(resolve_path "$base/$want" 2>/dev/null)" || continue
    [ -d "$resolved" ] || continue
    echo "$type|$base/$want"
    return 0
  done
  return 1
}

# Expand a group name from groups.json into a newline-separated list of item names.
resolve_group() {
  local group="$1"
  if [ ! -f "$GROUPS_FILE" ]; then
    echo -e "${RED}No groups.json found at $GROUPS_FILE${RESET}" >&2
    return 1
  fi
  python3 - "$GROUPS_FILE" "$group" <<'PY'
import json, sys
path, group = sys.argv[1], sys.argv[2]
groups = json.load(open(path)).get("groups", {})
if group not in groups:
    sys.stderr.write(f"Unknown group: {group} (available: {', '.join(sorted(groups)) or 'none'})\n")
    sys.exit(1)
print("\n".join(groups[group]))
PY
}

# Install one item into the given scope. Shared by the picker and `get`.
install_item() {
  local name="$1" type="$2" path="$3" install_scope="$4"
  local target_skills_dir="$5" plugin_scope_flag="$6" assume_yes="$7" git_root="$8"

  if [ "$type" = "skill" ]; then
    local abs_path
    abs_path="$(resolve_path "$path")"
    ln -snf "$abs_path" "$target_skills_dir/$name"
    local display_target
    if [ "$install_scope" = "project" ]; then
      display_target="${target_skills_dir#$git_root/}"
    else
      # shellcheck disable=SC2088  # literal display label, not a path to expand
      display_target="~/.claude/skills"
    fi
    echo -e "  ${GREEN}✓${RESET} $name -> $display_target/$name  ${DIM}(skill)${RESET}"
    return
  fi

  local resolved
  resolved="$(resolve_path "$path")"
  if is_claude_plugin_dir "$resolved"; then
    ensure_marketplace
    local plugin_ref="$name@$MARKETPLACE_NAME"
    echo -e "  ${CYAN}→${RESET} $name  ${DIM}(plugin — $install_scope scope)${RESET}"
    if prompt_yes "$assume_yes" "    ${YELLOW}Run 'claude plugin install $plugin_scope_flag $plugin_ref'? [y/N]: ${RESET}"; then
      claude plugin install $plugin_scope_flag -y "$plugin_ref" || echo -e "    ${RED}Install failed — try: claude plugin install $plugin_scope_flag $plugin_ref${RESET}"
    fi
  elif [ -f "$resolved/notify-sound.sh" ]; then
    echo -e "  ${CYAN}→${RESET} $name  ${DIM}(codex helper)${RESET}"
    if prompt_yes "$assume_yes" "    ${YELLOW}Update ~/.codex/config.toml notify hook? [y/N]: ${RESET}"; then
      install_codex_notify "$resolved"
    fi
  elif [ -f "$resolved/awake.sh" ]; then
    echo -e "  ${CYAN}→${RESET} $name  ${DIM}(codex helper)${RESET}"
    if prompt_yes "$assume_yes" "    ${YELLOW}Update ~/.codex/hooks.json SessionStart/Stop hooks? [y/N]: ${RESET}"; then
      install_codex_awake "$resolved"
    fi
  else
    echo -e "  ${YELLOW}⚠${RESET} $name — no plugin manifest found, skipped"
  fi
}

cmd_default() {
  local all_names=() all_types=() all_descs=() all_paths=()

  # Collect skills from skills/
  if [ -d "$SKILLS_DIR" ]; then
    for item in "$SKILLS_DIR"/*; do
      local name
      name="$(basename "$item")"
      [ "$name" = "*" ] && continue
      # Must be a directory or a symlink to a directory
      local resolved
      resolved="$(resolve_path "$item" 2>/dev/null)" || continue
      [ ! -d "$resolved" ] && continue
      local desc=""
      if [ -f "$resolved/SKILL.md" ]; then
        desc="$(skill_description "$resolved/SKILL.md")"
      fi
      all_names+=("$name")
      all_types+=("skill")
      all_descs+=("$desc")
      all_paths+=("$item")
    done
  fi

  # Collect plugins from plugins/
  if [ -d "$PLUGINS_DIR" ]; then
    for item in "$PLUGINS_DIR"/*; do
      local name
      name="$(basename "$item")"
      [ "$name" = "*" ] && continue
      local resolved
      resolved="$(resolve_path "$item" 2>/dev/null)" || continue
      [ ! -d "$resolved" ] && continue
      local desc=""
      desc="$(plugin_description "$resolved" 2>/dev/null)"
      if [ -z "$desc" ] && [ -f "$resolved/README.md" ]; then
        desc="$(head -5 "$resolved/README.md" | grep -v '^#' | grep -v '^$' | head -1 | head -c 80)"
      fi
      all_names+=("$name")
      all_types+=("plugin")
      all_descs+=("$desc")
      all_paths+=("$item")
    done
  fi

  if [ ${#all_names[@]} -eq 0 ]; then
    echo -e "${YELLOW}No skills or plugins found in this repo.${RESET}"
    echo -e "Use ${BOLD}./install add <user/repo>${RESET} to add some from GitHub."
    exit 0
  fi

  # Build pipe-separated strings for multiselect
  local labels="" descs="" tags="" preselect=""
  for idx in "${!all_names[@]}"; do
    local name="${all_names[$idx]}"
    local type="${all_types[$idx]}"
    local desc="${all_descs[$idx]}"
    local path="${all_paths[$idx]}"
    local tag="$type"

    # Add source info to tag
    if [ -L "$path" ]; then
      local target
      target="$(readlink "$path" 2>/dev/null || echo "")"
      tag="$type  $target"
    fi

    # Check if already installed
    local install_level=""
    if [ "$type" = "skill" ]; then
      install_level="$(skill_install_level "$name")"
    elif [ "$type" = "plugin" ]; then
      install_level="$(plugin_install_level "$name")"
    fi
    if [ -n "$install_level" ]; then
      tag="$tag  $(installed_tag "$install_level")"
    fi

    [ -n "$labels" ] && labels="$labels|"
    [ -n "$descs" ] && descs="$descs|"
    [ -n "$tags" ] && tags="$tags|"
    [ -n "$preselect" ] && preselect="$preselect|"
    labels="$labels$name"
    descs="$descs$desc"
    tags="$tags$tag"
    preselect="${preselect}0"
  done

  echo ""
  echo -e "${BOLD}Select skills & plugins to install:${RESET}"
  echo ""

  local ms_result=""
  multiselect ms_result "$labels" "$descs" "$tags" "$preselect"

  # Check for cancel
  if [[ "$ms_result" == cancel* ]]; then
    echo ""
    echo "Cancelled."
    exit 0
  fi

  # Parse result into selected indices
  IFS='|' read -ra ms_selections <<< "$ms_result"
  local selected_indices=()
  for idx in "${!ms_selections[@]}"; do
    [ "${ms_selections[$idx]}" = "1" ] && selected_indices+=("$idx")
  done

  if [ ${#selected_indices[@]} -eq 0 ]; then
    echo ""
    echo "Nothing selected."
    exit 0
  fi

  # Determine install scope
  local install_scope=""
  local target_skills_dir=""
  local plugin_scope_flag=""
  local in_git_repo=0
  local git_root=""

  # Check if we're inside a git repo (other than agents-shared itself)
  git_root="$(git -C "$PWD" rev-parse --show-toplevel 2>/dev/null)" || true
  if [ -n "$git_root" ] && [ "$git_root" != "$ROOT_DIR" ]; then
    in_git_repo=1
  fi

  echo ""
  if [ "$in_git_repo" -eq 1 ]; then
    echo -e "${BOLD}Install to:${RESET}"
    echo -e "  ${GREEN}[1]${RESET} This project  ${DIM}($git_root/.claude/skills/)${RESET}"
    echo -e "  ${GREEN}[2]${RESET} User (global)  ${DIM}(~/.claude/skills/)${RESET}"
    echo ""
    echo -en "${BOLD}Scope [1]: ${RESET}"
    read -r scope_choice
    case "${scope_choice:-1}" in
      1)
        install_scope="project"
        target_skills_dir="$git_root/.claude/skills"
        plugin_scope_flag="--scope project"
        ;;
      2)
        install_scope="user"
        target_skills_dir="$USER_SKILLS_DIR"
        plugin_scope_flag="--scope user"
        ;;
      *)
        echo "Invalid choice."
        exit 1
        ;;
    esac
  else
    install_scope="user"
    target_skills_dir="$USER_SKILLS_DIR"
    plugin_scope_flag="--scope user"
  fi

  # Install selected items
  echo ""
  mkdir -p "$target_skills_dir"

  for idx in "${selected_indices[@]}"; do
    install_item \
      "${all_names[$idx]}" "${all_types[$idx]}" "${all_paths[$idx]}" \
      "$install_scope" "$target_skills_dir" "$plugin_scope_flag" 0 "$git_root"
  done

  echo ""
  echo -e "${GREEN}Done!${RESET} Installed to ${BOLD}$install_scope${RESET} scope."
}

# ── get (non-interactive) ────────────────────────────────────────────

# Install named items or a group without prompts — the entry point mise tasks call.
cmd_get() {
  local names=() scope_choice="" assume_yes=0 group=""
  while [ $# -gt 0 ]; do
    case "$1" in
      --scope) scope_choice="${2:-}"; shift 2 ;;
      --group) group="${2:-}"; shift 2 ;;
      --yes|-y) assume_yes=1; shift ;;
      -*) echo -e "${RED}Unknown flag: $1${RESET}"; exit 1 ;;
      *) names+=("$1"); shift ;;
    esac
  done

  if [ -n "$group" ]; then
    local expanded
    expanded="$(resolve_group "$group")" || exit 1
    while IFS= read -r n; do
      [ -n "$n" ] && names+=("$n")
    done <<< "$expanded"
  fi

  if [ ${#names[@]} -eq 0 ]; then
    echo -e "${RED}Usage: ./install get [--scope user|project] [--group <group>] [--yes] <name...>${RESET}"
    exit 1
  fi

  local install_scope target_skills_dir plugin_scope_flag git_root=""
  git_root="$(git -C "$PWD" rev-parse --show-toplevel 2>/dev/null)" || true
  case "${scope_choice:-user}" in
    user)
      install_scope="user"
      target_skills_dir="$USER_SKILLS_DIR"
      plugin_scope_flag="--scope user"
      ;;
    project)
      if [ -z "$git_root" ] || [ "$git_root" = "$ROOT_DIR" ]; then
        echo -e "${RED}--scope project requires being inside another git repo.${RESET}"
        exit 1
      fi
      install_scope="project"
      target_skills_dir="$git_root/.claude/skills"
      plugin_scope_flag="--scope project"
      ;;
    *)
      echo -e "${RED}Invalid scope: $scope_choice (use user or project)${RESET}"
      exit 1
      ;;
  esac

  mkdir -p "$target_skills_dir"
  echo ""
  local n entry type path
  for n in "${names[@]}"; do
    if ! entry="$(find_item "$n")"; then
      echo -e "  ${YELLOW}⚠${RESET} $n — not found, skipped"
      continue
    fi
    type="${entry%%|*}"
    path="${entry#*|}"
    install_item "$n" "$type" "$path" \
      "$install_scope" "$target_skills_dir" "$plugin_scope_flag" "$assume_yes" "$git_root"
  done
  echo ""
  echo -e "${GREEN}Done!${RESET} Installed to ${BOLD}$install_scope${RESET} scope."
}

# ── add ──────────────────────────────────────────────────────────────

cmd_add() {
  local slug="${1:-}"
  if [ -z "$slug" ]; then
    echo -e "${RED}Usage: ./install add <user/repo>${RESET}"
    echo "  e.g. ./install add garrytan/gstack"
    exit 1
  fi

  local user repo repo_dir
  user="$(echo "$slug" | cut -d/ -f1)"
  repo="$(echo "$slug" | cut -d/ -f2)"
  repo_dir="$REPOS_DIR/$user/$repo"

  # Clone if not already present
  if [ -d "$repo_dir/.git" ]; then
    echo -e "${DIM}Repo already cloned at repos/$user/$repo — pulling latest...${RESET}"
    (cd "$repo_dir" && git pull --ff-only 2>/dev/null || true)
  else
    echo -e "${CYAN}Cloning $slug...${RESET}"
    mkdir -p "$REPOS_DIR/$user"
    git clone "https://github.com/$slug.git" "$repo_dir"
  fi

  # Detect available items
  local skills=() plugins=() all_items=() item_types=()

  while IFS= read -r s; do
    [ -n "$s" ] && skills+=("$s")
  done < <(detect_skills "$repo_dir")

  while IFS= read -r p; do
    [ -n "$p" ] && plugins+=("$p")
  done < <(detect_plugins "$repo_dir")

  if [ ${#skills[@]} -eq 0 ] && [ ${#plugins[@]} -eq 0 ]; then
    echo -e "${YELLOW}No skills or plugins found in $slug.${RESET}"
    exit 0
  fi

  # Build combined item list
  for s in "${skills[@]+"${skills[@]}"}"; do
    all_items+=("$s")
    item_types+=("skill")
  done
  for p in "${plugins[@]+"${plugins[@]}"}"; do
    all_items+=("$p")
    item_types+=("plugin")
  done

  # Build pipe-separated strings for multiselect
  local labels="" descs="" tags="" preselect=""
  for idx in "${!all_items[@]}"; do
    local name="${all_items[$idx]}"
    local type="${item_types[$idx]}"
    local desc=""
    local display_name="$name"
    local tag="$type"

    if [ "$type" = "skill" ]; then
      if [[ "$name" == .:* ]]; then
        display_name="${name#.:}"
        [ -f "$repo_dir/SKILL.md" ] && desc="$(skill_description "$repo_dir/SKILL.md")"
      else
        [ -f "$repo_dir/$name/SKILL.md" ] && desc="$(skill_description "$repo_dir/$name/SKILL.md")"
      fi
      local sl
      sl="$(skill_install_level "$display_name")"
      if [ -n "$sl" ]; then
        tag="$tag  $(installed_tag "$sl")"
      elif [ -L "$SKILLS_DIR/$display_name" ] || [ -d "$SKILLS_DIR/$display_name" ]; then
        tag="$tag  $(installed_tag "project")"
      fi
    else
      local pl
      pl="$(plugin_install_level "$name")"
      if [ -n "$pl" ]; then
        tag="$tag  $(installed_tag "$pl")"
      elif [ -L "$PLUGINS_DIR/$name" ] || [ -d "$PLUGINS_DIR/$name" ]; then
        tag="$tag  $(installed_tag "project")"
      fi
    fi

    [ -n "$labels" ] && labels="$labels|"
    [ -n "$descs" ] && descs="$descs|"
    [ -n "$tags" ] && tags="$tags|"
    [ -n "$preselect" ] && preselect="$preselect|"
    labels="$labels$display_name"
    descs="$descs$desc"
    tags="$tags$tag"
    preselect="${preselect}0"
  done

  echo ""
  echo -e "${BOLD}Found ${#all_items[@]} item(s) in $slug. Select items to add:${RESET}"
  echo ""

  local ms_result=""
  multiselect ms_result "$labels" "$descs" "$tags" "$preselect"

  if [[ "$ms_result" == cancel* ]]; then
    echo ""
    echo "Cancelled."
    exit 0
  fi

  IFS='|' read -ra ms_selections <<< "$ms_result"
  local selected_indices=()
  for idx in "${!ms_selections[@]}"; do
    [ "${ms_selections[$idx]}" = "1" ] && selected_indices+=("$idx")
  done

  if [ ${#selected_indices[@]} -eq 0 ]; then
    echo ""
    echo "Nothing selected."
    exit 0
  fi

  # Install selected items
  echo ""
  ensure_sources_file
  local installed_skills=() installed_plugins=()

  for idx in "${selected_indices[@]}"; do
    local name="${all_items[$idx]}"
    local type="${item_types[$idx]}"

    if [ "$type" = "skill" ]; then
      local display_name="$name"
      local link_target

      if [[ "$name" == .:* ]]; then
        display_name="${name#.:}"
        # Root-level skill — link to the repo dir itself
        link_target="../repos/$user/$repo"
      else
        link_target="../repos/$user/$repo/$name"
      fi

      mkdir -p "$SKILLS_DIR"
      ln -snf "$link_target" "$SKILLS_DIR/$display_name"
      echo -e "  ${GREEN}✓${RESET} $display_name -> skills/$display_name  ${DIM}(skill)${RESET}"
      installed_skills+=("$display_name")
    else
      local link_target="../repos/$user/$repo/$name"
      # Don't overwrite existing non-symlink plugin dirs
      if [ -d "$PLUGINS_DIR/$name" ] && [ ! -L "$PLUGINS_DIR/$name" ]; then
        echo -e "  ${YELLOW}⚠${RESET} $name — existing plugin directory, skipped  ${DIM}(use remove first)${RESET}"
        continue
      fi
      mkdir -p "$PLUGINS_DIR"
      ln -snf "$link_target" "$PLUGINS_DIR/$name"
      echo -e "  ${GREEN}✓${RESET} $name -> plugins/$name  ${DIM}(plugin)${RESET}"
      installed_plugins+=("$name")
    fi
  done

  # Update sources.json
  local tmp
  tmp="$(mktemp)"
  local all_installed=()
  for s in "${installed_skills[@]+"${installed_skills[@]}"}"; do all_installed+=("$s"); done
  for p in "${installed_plugins[@]+"${installed_plugins[@]}"}"; do all_installed+=("$p"); done

  local items_json
  items_json=$(printf '%s\n' "${all_installed[@]}" | python3 -c "import sys,json; print(json.dumps([l.strip() for l in sys.stdin if l.strip()]))")

  python3 -c "
import json, sys
with open('$SOURCES_FILE') as f:
    data = json.load(f)
entry = data.get('$slug', {'items': []})
existing = set(entry.get('items', []))
new_items = json.loads('$items_json')
existing.update(new_items)
entry['items'] = sorted(existing)
entry['repo'] = 'https://github.com/$slug'
data['$slug'] = entry
with open('$tmp', 'w') as f:
    json.dump(data, f, indent=2)
    f.write('\n')
"
  mv "$tmp" "$SOURCES_FILE"

  local total=$(( ${#installed_skills[@]} + ${#installed_plugins[@]} ))
  echo ""
  echo -e "${GREEN}Done!${RESET} $total item(s) installed from ${BOLD}$slug${RESET}."

  # Run repo's setup script if present
  if [ -x "$repo_dir/setup" ]; then
    echo ""
    echo -en "${YELLOW}This repo has a setup script. Run it now? [y/N]: ${RESET}"
    read -r run_setup
    if [ "$run_setup" = "y" ] || [ "$run_setup" = "Y" ]; then
      (cd "$repo_dir" && ./setup)
    else
      echo -e "${DIM}Skipped. You can run it later: cd repos/$user/$repo && ./setup${RESET}"
    fi
  fi
}

# ── list ─────────────────────────────────────────────────────────────

cmd_list() {
  echo ""
  echo -e "${BOLD}Installed skills:${RESET}"
  local found=0
  if [ -d "$SKILLS_DIR" ]; then
    for item in "$SKILLS_DIR"/*; do
      [ ! -e "$item" ] && continue
      local name
      name="$(basename "$item")"
      [ "$name" = "*" ] && continue
      if [ -L "$item" ]; then
        local target
        target="$(readlink "$item" 2>/dev/null || echo "?")"
        echo -e "  ${GREEN}•${RESET} $name  ${DIM}-> $target${RESET}"
      elif [ -d "$item" ]; then
        echo -e "  ${GREEN}•${RESET} $name  ${DIM}(local)${RESET}"
      fi
      found=1
    done
  fi
  [ "$found" -eq 0 ] && echo -e "  ${DIM}(none)${RESET}"

  echo ""
  echo -e "${BOLD}Installed plugins:${RESET}"
  found=0
  if [ -d "$PLUGINS_DIR" ]; then
    for item in "$PLUGINS_DIR"/*; do
      [ ! -e "$item" ] && continue
      local name
      name="$(basename "$item")"
      [ "$name" = "*" ] && continue
      if [ -L "$item" ]; then
        local target
        target="$(readlink "$item" 2>/dev/null || echo "?")"
        echo -e "  ${GREEN}•${RESET} $name  ${DIM}-> $target${RESET}"
      elif [ -d "$item" ]; then
        echo -e "  ${GREEN}•${RESET} $name  ${DIM}(local)${RESET}"
      fi
      found=1
    done
  fi
  [ "$found" -eq 0 ] && echo -e "  ${DIM}(none)${RESET}"

  # Show sources
  if [ -f "$SOURCES_FILE" ]; then
    echo ""
    echo -e "${BOLD}Sources:${RESET}"
    python3 -c "
import json
with open('$SOURCES_FILE') as f:
    data = json.load(f)
for slug, info in sorted(data.items()):
    repo = info.get('repo', '?')
    items = ', '.join(info.get('items', []))
    print(f'  {slug}  ({items})')
    print(f'    {repo}')
" 2>/dev/null || true
  fi
  echo ""
}

# ── remove ───────────────────────────────────────────────────────────

cmd_remove() {
  local name="${1:-}"
  if [ -z "$name" ]; then
    echo -e "${RED}Usage: ./install remove <name>${RESET}"
    exit 1
  fi

  local removed=0

  # Check skills
  if [ -L "$SKILLS_DIR/$name" ]; then
    rm "$SKILLS_DIR/$name"
    echo -e "${GREEN}✓${RESET} Removed skill: $name"
    removed=1
  elif [ -d "$SKILLS_DIR/$name" ]; then
    echo -e "${YELLOW}⚠${RESET} $name is a local skill directory (not a symlink). Remove manually if intended."
  fi

  # Check plugins
  if [ -L "$PLUGINS_DIR/$name" ]; then
    rm "$PLUGINS_DIR/$name"
    echo -e "${GREEN}✓${RESET} Removed plugin: $name"
    removed=1
  elif [ -d "$PLUGINS_DIR/$name" ]; then
    echo -e "${YELLOW}⚠${RESET} $name is a local plugin directory (not a symlink). Remove manually if intended."
  fi

  if [ "$removed" -eq 0 ]; then
    echo -e "${RED}Not found: $name${RESET}"
    exit 1
  fi

  # Update sources.json
  if [ -f "$SOURCES_FILE" ]; then
    local tmp
    tmp="$(mktemp)"
    python3 -c "
import json
with open('$SOURCES_FILE') as f:
    data = json.load(f)
for slug in list(data.keys()):
    items = data[slug].get('items', [])
    if '$name' in items:
        items.remove('$name')
        data[slug]['items'] = items
    if not items:
        del data[slug]
with open('$tmp', 'w') as f:
    json.dump(data, f, indent=2)
    f.write('\n')
" 2>/dev/null
    mv "$tmp" "$SOURCES_FILE"
  fi
}

# ── clean ────────────────────────────────────────────────────────────

# Undo everything this repo installed into ~/.claude: every skill symlink whose
# target resolves back into this checkout, and every plugin from this repo's
# marketplace. Precisely scoped (skills from other repos are left alone) and
# reversible (re-run `./install setup <profile>`). Does not touch MCP servers,
# Codex hooks, or the marketplace registration.
cmd_clean() {
  echo ""
  echo -e "${BOLD}Cleaning agents-shared installs${RESET} ${DIM}(user scope)${RESET}"

  echo -e "\n${BOLD}Skills${RESET}"
  local removed=0
  if [ -d "$USER_SKILLS_DIR" ]; then
    for link in "$USER_SKILLS_DIR"/*; do
      [ -L "$link" ] || continue
      local tgt name
      tgt="$(readlink "$link" 2>/dev/null || echo "")"
      case "$tgt" in
        "$ROOT_DIR"/*)
          name="$(basename "$link")"
          rm "$link"
          echo -e "  ${GREEN}✓${RESET} removed $name  ${DIM}-> $tgt${RESET}"
          removed=1
          ;;
      esac
    done
  fi
  [ "$removed" -eq 0 ] && echo -e "  ${DIM}(none pointing into this repo)${RESET}"

  echo -e "\n${BOLD}Plugins${RESET}"
  local plugin_removed=0
  if [ -d "$PLUGINS_DIR" ]; then
    for dir in "$PLUGINS_DIR"/*/; do
      [ -d "$dir" ] || continue
      is_claude_plugin_dir "$dir" || continue
      local pname
      pname="$(basename "$dir")"
      is_user_plugin_installed "$pname" || continue
      if claude plugin uninstall "$pname@$MARKETPLACE_NAME" >/dev/null 2>&1 \
        || claude plugin uninstall "$pname" >/dev/null 2>&1; then
        echo -e "  ${GREEN}✓${RESET} uninstalled $pname"
        plugin_removed=1
      else
        echo -e "  ${RED}✘${RESET} failed to uninstall $pname — try: claude plugin uninstall $pname@$MARKETPLACE_NAME"
      fi
    done
  fi
  [ "$plugin_removed" -eq 0 ] && echo -e "  ${DIM}(none)${RESET}"

  echo -e "\n${DIM}Left in place: MCP servers (remove with 'claude mcp remove <name> -s user'), Codex hooks (~/.codex), and the '$MARKETPLACE_NAME' marketplace.${RESET}"
  echo ""
}

# ── update ───────────────────────────────────────────────────────────

cmd_update() {
  local slug="${1:-}"

  if [ -n "$slug" ]; then
    local user repo repo_dir
    user="$(echo "$slug" | cut -d/ -f1)"
    repo="$(echo "$slug" | cut -d/ -f2)"
    repo_dir="$REPOS_DIR/$user/$repo"
    if [ ! -d "$repo_dir/.git" ]; then
      echo -e "${RED}Repo not found: repos/$user/$repo${RESET}"
      exit 1
    fi
    echo -e "Updating ${BOLD}$slug${RESET}..."
    (cd "$repo_dir" && git pull --ff-only)
    # Re-run setup if present
    if [ -x "$repo_dir/setup" ]; then
      echo -en "${YELLOW}Run setup script? [y/N]: ${RESET}"
      read -r run_setup
      if [ "$run_setup" = "y" ] || [ "$run_setup" = "Y" ]; then
        (cd "$repo_dir" && ./setup)
      fi
    fi
  else
    # Update all repos
    if [ ! -d "$REPOS_DIR" ]; then
      echo -e "${DIM}No repos to update.${RESET}"
      exit 0
    fi
    for user_dir in "$REPOS_DIR"/*/; do
      [ ! -d "$user_dir" ] && continue
      for repo_dir in "$user_dir"*/; do
        [ ! -d "$repo_dir/.git" ] && continue
        local rel_path="${repo_dir#$REPOS_DIR/}"
        rel_path="${rel_path%/}"
        echo -e "Updating ${BOLD}$rel_path${RESET}..."
        (cd "$repo_dir" && git pull --ff-only 2>&1 | head -3)
      done
    done
  fi
  echo -e "${GREEN}Done.${RESET}"
}

