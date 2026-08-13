# lib/mcp.sh — MCP server subsystem + the `mcp` command. Sourced by ./install.
# shellcheck shell=bash

# ── mcp (Model Context Protocol servers) ─────────────────────────────
#
# MCP servers are defined in mcp/*.group.json (group = filename stem). stdio
# servers run from repo-local vendored runtimes (node_modules/.bin, .venv/bin)
# via the mcp/with-env wrapper, which loads secrets from .env at launch — so no
# token ever lands in ~/.claude.json. http servers register as a plain URL.
# All servers install at USER scope so they resolve from any cwd.

# Install the vendored runtimes: node deps (pnpm) + python deps (uv venv).
mcp_deps() {
  echo ""
  if [ -f "$ROOT_DIR/package.json" ]; then
    echo -e "${CYAN}→${RESET} pnpm install (node MCP runtimes)"
    (cd "$ROOT_DIR" && pnpm install --config.confirmModulesPurge=false)
  fi
  if [ -f "$MCP_DIR/requirements.txt" ]; then
    echo -e "${CYAN}→${RESET} uv venv + install (python MCP runtimes)"
    [ -d "$ROOT_DIR/.venv" ] || uv venv "$ROOT_DIR/.venv"
    uv pip install --python "$ROOT_DIR/.venv/bin/python" -r "$MCP_DIR/requirements.txt"
  fi
  echo -e "${GREEN}✓${RESET} MCP runtimes installed."
}

# Auto-install runtimes on first use if either vendored tree is missing.
mcp_ensure_deps() {
  if { [ -f "$ROOT_DIR/package.json" ] && [ ! -d "$ROOT_DIR/node_modules" ]; } \
     || { [ -f "$MCP_DIR/requirements.txt" ] && [ ! -d "$ROOT_DIR/.venv" ]; }; then
    echo -e "${DIM}Vendored MCP runtimes not found — installing them first...${RESET}"
    mcp_deps
  fi
}

# Load every manifest server as a JSON array of {name, group, spec} on stdout —
# the single source all the mcp_* readers below share.
mcp_load() {
  python3 - "$MCP_DIR" <<'PY'
import glob, json, os, sys
mcp_dir = sys.argv[1]
out = []
for path in sorted(glob.glob(os.path.join(mcp_dir, "*.group.json"))):
    group = os.path.basename(path)[:-len(".group.json")]
    for name, spec in json.load(open(path)).get("servers", {}).items():
        out.append({"name": name, "group": group, "spec": spec})
json.dump(out, sys.stdout)
PY
}

# One `claude mcp list` call → {"installed":[...], "connected":[...]} JSON. It
# health-checks every server (the one slow call), so readers share a single pass.
mcp_status() {
  python3 <<'PY'
import json, subprocess, sys
installed, connected = [], []
try:
    out = subprocess.run(["claude", "mcp", "list"], capture_output=True, text=True, timeout=30).stdout
    for line in out.splitlines():
        if ": " in line and " - " in line:
            name = line.split(":", 1)[0].strip()
            installed.append(name)
            if line.rstrip().endswith("Connected"):
                connected.append(name)
except Exception:
    pass
json.dump({"installed": installed, "connected": connected}, sys.stdout)
PY
}

# Expand a group name ("__all__" for every manifest) into server names.
mcp_group_names() {
  python3 - "$(mcp_load)" "$1" <<'PY'
import json, sys
servers = json.loads(sys.argv[1])
group = sys.argv[2]
for s in servers:
    if group in ("__all__", s["group"]):
        print(s["name"])
PY
}

mcp_list() {
  echo ""
  echo -e "${BOLD}MCP servers (mcp/*.group.json):${RESET}"
  local status servers
  status="$(mcp_status)"; servers="$(mcp_load)"
  python3 - "$servers" "$status" <<'PY'
import json, sys
servers = json.loads(sys.argv[1])
installed = set(json.loads(sys.argv[2])["installed"])
GREEN, DIM, RESET = "\033[0;32m", "\033[2m", "\033[0m"
group = None
for s in servers:
    if s["group"] != group:
        group = s["group"]
        print(f"\n  {group}:")
    name, spec = s["name"], s["spec"]
    kind = spec.get("kind", "stdio")
    rt = spec.get("runtime", "http" if kind == "http" else "")
    tag = f"{GREEN}✓ registered (user){RESET}" if name in installed else f"{DIM}not registered{RESET}"
    desc = spec.get("_desc", "")
    print(f"    {name}  {DIM}({kind}/{rt}){RESET}  {tag}")
    if desc:
        print(f"        {DIM}{desc}{RESET}")
PY
  echo ""
  echo -e "  ${DIM}Register: ./install mcp get <name...>  ·  --group <g>  ·  --all${RESET}"
  echo ""
}

# Register named servers (or a --group / --all) at user scope via claude mcp add.
mcp_get() {
  local force=0 group="" names=()
  while [ $# -gt 0 ]; do
    case "$1" in
      --force) force=1; shift ;;
      --group) group="${2:-}"; shift 2 ;;
      --all) group="__all__"; shift ;;
      -*) echo -e "${RED}Unknown flag: $1${RESET}"; return 1 ;;
      *) names+=("$1"); shift ;;
    esac
  done

  if [ -n "$group" ]; then
    while IFS= read -r n; do [ -n "$n" ] && names+=("$n"); done < <(mcp_group_names "$group")
  fi

  if [ ${#names[@]} -eq 0 ]; then
    echo -e "${RED}Usage: ./install mcp get [--group <g>|--all] [--force] <name...>${RESET}"
    return 1
  fi

  mcp_ensure_deps

  echo ""
  local status servers
  status="$(mcp_status)"; servers="$(mcp_load)"
  python3 - "$ROOT_DIR" "$MCP_DIR" "$servers" "$status" "$force" "${names[@]}" <<'PY'
import json, os, subprocess, sys
root, mcp_dir = sys.argv[1], sys.argv[2]
servers = {s["name"]: s["spec"] for s in json.loads(sys.argv[3])}
installed = set(json.loads(sys.argv[4])["installed"])
force = sys.argv[5] == "1"
targets = sys.argv[6:]
wrapper = os.path.join(mcp_dir, "with-env")

def local_bin(spec):
    rt = spec.get("runtime")
    if rt == "node":
        return os.path.join(root, "node_modules", ".bin", spec["bin"])
    if rt == "python":
        return os.path.join(root, ".venv", "bin", spec["bin"])
    return None

GREEN, YELLOW, DIM, RESET = "\033[0;32m", "\033[0;33m", "\033[2m", "\033[0m"
rc = 0
for name in targets:
    spec = servers.get(name)
    if not spec:
        print(f"  {YELLOW}⚠{RESET} {name} — not in any mcp/*.group.json, skipped"); rc = 1; continue
    if name in installed and not force:
        print(f"  {DIM}={RESET} {name} — already registered (use --force to re-add)"); continue
    if force:
        # Unconditional: `mcp list` health-checks can time out and under-report installs,
        # so never gate the force-remove on the parsed set.
        subprocess.run(["claude", "mcp", "remove", name, "-s", "user"], capture_output=True, text=True)

    kind = spec.get("kind", "stdio")
    if kind == "http":
        cmd = ["claude", "mcp", "add", name, "-s", "user", "--transport", "http", spec["url"]]
        for k, v in spec.get("headers", {}).items():
            cmd += ["-H", f"{k}: {v}"]
    else:
        lb = local_bin(spec)
        if not lb or not os.path.exists(lb):
            print(f"  {YELLOW}⚠{RESET} {name} — vendored binary missing ({lb}); run ./install mcp deps"); rc = 1; continue
        cmd = ["claude", "mcp", "add", name, "-s", "user"]
        # Manifest `env` = committed, machine-agnostic config (ports, flags) → stored
        # in ~/.claude.json via -e. Secrets and per-machine values (AWS_PROFILE, file
        # paths) instead live in .env and load via mcp/with-env at launch.
        for k, v in spec.get("env", {}).items():
            cmd += ["-e", f"{k}={v}"]
        cmd += ["--", wrapper, lb] + spec.get("args", [])

    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode == 0:
        print(f"  {GREEN}✓{RESET} {name} — registered ({kind}, user scope)")
        prereq = spec.get("_prereq")
        if prereq:
            print(f"      {DIM}next: {prereq}{RESET}")
    else:
        print(f"  {YELLOW}⚠{RESET} {name} — add failed: {(res.stderr or res.stdout).strip()}"); rc = 1

sys.exit(rc)
PY
  local status=$?
  echo ""
  if [ "$status" -eq 0 ]; then
    echo -e "${GREEN}Done!${RESET} Complete any per-server auth, then run ${BOLD}/mcp${RESET} in Claude Code to connect."
  fi
  return "$status"
}

# Run each server's `auth` recipe: check, fire the interactive login if stale,
# re-verify. Named servers, or --all / --group for every server.
mcp_auth() {
  local group="" names=()
  while [ $# -gt 0 ]; do
    case "$1" in
      --group) group="${2:-}"; shift 2 ;;
      --all) group="__all__"; shift ;;
      -*) echo -e "${RED}Unknown flag: $1${RESET}"; return 1 ;;
      *) names+=("$1"); shift ;;
    esac
  done
  if [ -n "$group" ]; then
    while IFS= read -r n; do [ -n "$n" ] && names+=("$n"); done < <(mcp_group_names "$group")
  fi
  [ ${#names[@]} -eq 0 ] && { echo -e "${RED}Usage: ./install mcp auth [--group g|--all] <name...>${RESET}"; return 1; }

  # Load .env so $AWS_PROFILE etc. resolve for check/login argv (literals only;
  # op:// refs aren't needed for the CLI auth commands).
  if [ -f "$ROOT_DIR/.env" ]; then set -a; . "$ROOT_DIR/.env"; set +a; fi

  echo ""
  # Interactive logins inherit the terminal (browser device-auth), so no capture here.
  local status servers
  status="$(mcp_status)"; servers="$(mcp_load)"
  python3 - "$servers" "$status" "${names[@]}" <<'PY'
import json, os, subprocess, sys
# Connection state is the source of truth: never fire an interactive login for a
# server Claude Code already reports Connected (a per-CLI check can false-negative,
# e.g. `az account show` fails for a subscription-less account whose MCP still works).
servers = {s["name"]: s["spec"] for s in json.loads(sys.argv[1])}
connected = set(json.loads(sys.argv[2])["connected"])
targets = sys.argv[3:]
GREEN, YELLOW, CYAN, DIM, RESET = "\033[0;32m", "\033[0;33m", "\033[0;36m", "\033[2m", "\033[0m"
xp = os.path.expandvars

def check_ok(argv):
    try:
        return subprocess.run([xp(a) for a in argv], capture_output=True, text=True).returncode == 0
    except FileNotFoundError as e:
        print(f"      {YELLOW}missing tool: {e.filename}{RESET}"); return False

rc = 0
for name in targets:
    spec = servers.get(name)
    auth = spec.get("auth") if spec else None
    if not spec:
        print(f"  {YELLOW}⚠{RESET} {name} — not in any manifest"); rc = 1; continue
    if name in connected:
        print(f"  {GREEN}✓{RESET} {name} — already connected"); continue
    if not auth:
        print(f"  {DIM}·{RESET} {name} — not connected; token comes from .env (check the value)"); continue
    if auth.get("type") == "manual":
        print(f"  {CYAN}→{RESET} {name} — manual step:")
        print(f"      {DIM}{auth['instructions']}{RESET}"); continue

    check, login = auth.get("check"), auth.get("login")
    if check and check_ok(check):
        print(f"  {GREEN}✓{RESET} {name} — authenticated (run /mcp to connect)"); continue
    if not login:
        print(f"  {YELLOW}⚠{RESET} {name} — not authenticated, no login recipe"); rc = 1; continue
    print(f"  {CYAN}→{RESET} {name} — running: {' '.join(xp(a) for a in login)}")
    subprocess.run([xp(a) for a in login])
    if check:
        ok = check_ok(check)
        print(f"  {GREEN}✓{RESET} {name} — authenticated" if ok
              else f"  {YELLOW}⚠{RESET} {name} — still not authenticated (see the server's _prereq)")
        if not ok:
            rc = 1
sys.exit(rc)
PY
  local status=$?
  echo ""
  echo -e "${DIM}Run ${BOLD}/mcp${RESET}${DIM} in Claude Code to (re)connect after auth.${RESET}"
  return "$status"
}

# One-shot: install runtimes, register everything, run every auth recipe.
mcp_setup() {
  mcp_ensure_deps
  echo ""
  mcp_get --all
  echo ""
  mcp_auth --all
}

cmd_mcp() {
  case "${1:-list}" in
    deps)     shift; mcp_deps ;;
    list|"")  mcp_list ;;
    get)      shift; mcp_get "$@" ;;
    auth)     shift; mcp_auth "$@" ;;
    setup)    shift; mcp_setup "$@" ;;
    -h|--help|help)
      echo "Usage: ./install mcp [list|deps|get|auth|setup] ..."
      echo "  list                                 Show servers + registration status"
      echo "  deps                                 Install vendored runtimes (pnpm + uv)"
      echo "  get [--group g|--all] [--force] <n>  Register servers at user scope"
      echo "  auth [--group g|--all] <n>           Run each server's login/auth recipe"
      echo "  setup                                deps + get --all + auth --all (one shot)"
      ;;
    *) echo -e "${RED}Unknown mcp subcommand: $1${RESET} (try: list, deps, get, auth, setup)"; return 1 ;;
  esac
}

