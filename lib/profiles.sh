# lib/profiles.sh — named-profile resolution + `setup`. Sourced by ./install.
# shellcheck shell=bash

# ── setup (named profiles: skills/plugins + mcp in one shot) ─────────
#
# Profiles live in profiles.json. `./install setup <profile>` installs the
# profile's skill/plugin items at user scope, then registers + auths its MCP
# servers. Wired to `mise setup` / `mise setup:<name>` / `mise setup:all`.

# Print two lines for a profile: space-joined items, then space-joined mcp servers.
resolve_profile() {
  # Servers come from the shared mcp_load so "*" expansion uses the same manifest
  # reader as the mcp_* commands.
  python3 - "$ROOT_DIR" "$1" "$(mcp_load)" <<'PY'
import json, os, sys
root, profile = sys.argv[1], sys.argv[2]
all_mcp_servers = [s["name"] for s in json.loads(sys.argv[3])]
data = json.load(open(os.path.join(root, "profiles.json")))
profs = data.get("profiles", {})
if profile not in profs:
    sys.stderr.write(f"Unknown profile: {profile} (available: {', '.join(sorted(profs))})\n")
    sys.exit(1)

def all_items():
    out = []
    for d in ("skills", "plugins"):
        p = os.path.join(root, d)
        if os.path.isdir(p):
            out += [n for n in sorted(os.listdir(p)) if not n.startswith(".")]
    return out

def resolve(name, seen):
    if name in seen:
        return [], []
    seen.add(name)
    pr = profs[name]
    items, mcp = [], []
    base = pr.get("extends")
    if base and base in profs:
        bi, bm = resolve(base, seen)
        items += bi; mcp += bm
    items += pr.get("items", [])
    mcp += pr.get("mcp", [])
    return items, mcp

items, mcp = resolve(profile, set())
if "*" in items:
    items = all_items()
if "*" in mcp:
    mcp = all_mcp_servers

def dedupe(xs):
    seen, out = set(), []
    for x in xs:
        if x not in seen:
            seen.add(x); out.append(x)
    return out

print(" ".join(dedupe(items)))
print(" ".join(dedupe(mcp)))
PY
}

cmd_setup() {
  local profile="${1:-default}"
  local out items mcp
  out="$(resolve_profile "$profile")" || exit 1
  { IFS= read -r items; IFS= read -r mcp; } <<<"$out"

  echo ""
  echo -e "${BOLD}Setup profile: ${CYAN}$profile${RESET}"

  if [ -n "$items" ]; then
    echo -e "\n${BOLD}Skills & plugins${RESET} ${DIM}(user scope)${RESET}"
    # shellcheck disable=SC2086
    cmd_get --yes $items
  fi

  if [ -n "$mcp" ]; then
    echo -e "\n${BOLD}MCP servers${RESET}"
    # mcp_get runs mcp_ensure_deps itself, so no separate deps call needed here.
    # shellcheck disable=SC2086
    mcp_get $mcp
    echo ""
    # shellcheck disable=SC2086
    mcp_auth $mcp
  fi

  echo ""
  echo -e "${GREEN}Setup '$profile' complete.${RESET} Restart Claude Code + ${BOLD}/mcp${RESET} to connect any newly-registered servers."
}

