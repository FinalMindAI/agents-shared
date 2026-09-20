#!/usr/bin/env python3
"""Audit local / user / Dropbox coding-agent skills. Interactive sync lives here."""

from __future__ import annotations

import argparse
import json
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, TextIO


SKIP_NAMES = {".system", "codex-primary-runtime"}
AGENT_ALIASES = {
    "claude": "claude",
    "cc": "claude",
    "codex": "codex",
    "cdx": "codex",
    "code": "codex",
    "grok": "grok",
    "gx": "grok",
    "agents": "agents",
    "cursor": "cursor",
    "commands": "commands",
}
AGENT_LABEL = {
    "claude": "cc",
    "codex": "cdx",
    "grok": "grok",
    "agents": "ag",
    "cursor": "cur",
    "commands": "cmd",
}
DEFAULT_LINK_AGENTS = ("claude", "grok", "agents")
ISSUE_STATUSES = {"broken", "partial", "unlinked", "dropbox-only", "mixed"}
# Tab keys (first is default). Labels follow AGENT_LABEL plus locations.
TAB_KEYS = (
    "all",
    "dropbox",
    "claude",
    "codex",
    "grok",
    "agents",
    "cursor",
    "commands",
    "local",
)
TAB_LABEL = {
    "all": "all",
    "dropbox": "dropbox",
    "claude": "cc",
    "codex": "cdx",
    "grok": "grok",
    "agents": "ag",
    "cursor": "cur",
    "commands": "cmd",
    "local": "local",
}


def _home() -> Path:
    return Path.home()


def dropbox_skills() -> Path:
    """Canonical skills dir (synced folder). Override with SKILLS_CANONICAL_DIR."""
    raw = os.environ.get("SKILLS_CANONICAL_DIR") or "~/Dropbox/dev/agents/skills"
    return Path(raw).expanduser().resolve()


def user_roots() -> dict[str, Path]:
    home = _home()
    return {
        "claude": home / ".claude/skills",
        "codex": home / ".codex/skills",
        "grok": home / ".grok/skills",
        "agents": home / ".agents/skills",
        "cursor": home / ".cursor/skills",
        "commands": home / ".claude/commands",
    }


def git_root(start: Path) -> Path:
    cur = start.resolve()
    for directory in (cur, *cur.parents):
        if (directory / ".git").exists():
            return directory
    return cur


def local_roots(cwd: Path) -> list[tuple[str, Path]]:
    root = git_root(cwd)
    found: list[tuple[str, Path]] = []
    seen: set[Path] = set()
    chain: list[Path] = []
    cur = cwd.resolve()
    while True:
        chain.append(cur)
        if cur == root or cur.parent == cur:
            break
        cur = cur.parent
    for directory in chain:
        for agent, rel in (
            ("claude", ".claude/skills"),
            ("codex", ".codex/skills"),
            ("grok", ".grok/skills"),
            ("agents", ".agents/skills"),
            ("cursor", ".cursor/skills"),
            ("commands", ".claude/commands"),
        ):
            path = directory / rel
            resolved = path.resolve() if path.exists() else path
            if resolved in seen or not path.is_dir():
                continue
            seen.add(resolved)
            found.append((agent, path))
    return found


def _one_line(value: Any, limit: int = 80) -> str:
    text = " ".join(str(value or "").split())
    if len(text) <= limit:
        return text
    return text[: limit - 1] + "…"


def parse_frontmatter(text: str) -> dict[str, str]:
    if not text.startswith("---"):
        return {}
    rest = text.split("\n", 1)[1] if "\n" in text else ""
    end = rest.find("\n---")
    if end < 0:
        return {}
    data: dict[str, str] = {}
    key: str | None = None
    acc: list[str] = []
    for line in rest[:end].splitlines():
        stripped = line.strip()
        if key and (line.startswith("  ") or line.startswith("\t")):
            acc.append(stripped)
            continue
        if ":" in line and not line.startswith((" ", "\t")):
            if key:
                data[key] = " ".join(acc).strip()
            raw_key, _, raw_val = line.partition(":")
            key = raw_key.strip()
            value = raw_val.strip().strip('"').strip("'")
            acc = [] if value in {"", ">", "|"} else [value]
        elif key and stripped:
            acc.append(stripped)
    if key:
        data[key] = " ".join(acc).strip()
    return data


def _skill_md(path: Path) -> Path | None:
    if path.is_file() and path.suffix == ".md" and path.name != "README.md":
        return path
    candidate = path / "SKILL.md"
    if candidate.is_file():
        return candidate
    return None


def _read_meta(path: Path) -> dict[str, str]:
    md = _skill_md(path)
    if md is None:
        return {}
    try:
        return parse_frontmatter(md.read_text(encoding="utf-8", errors="replace")[:8000])
    except OSError:
        return {}


def _resolve(path: Path) -> Path | None:
    try:
        return path.resolve()
    except OSError:
        return None


def _under(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except (OSError, ValueError):
        return False


@dataclass
class Location:
    scope: str
    agent: str
    path: str
    kind: str
    target: str | None = None
    resolved: str | None = None
    broken: bool = False

    def label(self) -> str:
        base = AGENT_LABEL.get(self.agent, self.agent)
        if self.scope == "local":
            return f"repo:{base}"
        if self.scope == "dropbox":
            return "dropbox"
        return base


@dataclass
class Skill:
    name: str
    locations: list[Location] = field(default_factory=list)
    description: str = ""
    status: str = ""

    def dropbox(self) -> Location | None:
        for loc in self.locations:
            if loc.scope == "dropbox" and not loc.broken:
                return loc
        return None

    def user(self) -> list[Location]:
        return [loc for loc in self.locations if loc.scope == "user"]

    def local(self) -> list[Location]:
        return [loc for loc in self.locations if loc.scope == "local"]

    def canonical(self) -> Path | None:
        db = self.dropbox()
        if db and db.resolved:
            return Path(db.resolved)
        for loc in self.locations:
            if not loc.broken and loc.resolved:
                return Path(loc.resolved)
        return None

    def to_json(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "status": self.status,
            "description": self.description,
            "canonical": str(self.canonical()) if self.canonical() else None,
            "locations": [
                {
                    "scope": loc.scope,
                    "agent": loc.agent,
                    "path": loc.path,
                    "kind": loc.kind,
                    "target": loc.target,
                    "resolved": loc.resolved,
                    "broken": loc.broken,
                }
                for loc in self.locations
            ],
        }

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> "Skill":
        locations = [
            Location(
                scope=str(item.get("scope") or ""),
                agent=str(item.get("agent") or ""),
                path=str(item.get("path") or ""),
                kind=str(item.get("kind") or "dir"),
                target=item.get("target"),
                resolved=item.get("resolved"),
                broken=bool(item.get("broken")),
            )
            for item in data.get("locations") or []
            if isinstance(item, dict)
        ]
        return cls(
            name=str(data.get("name") or ""),
            locations=locations,
            description=str(data.get("description") or ""),
            status=str(data.get("status") or ""),
        )


def _location_from_path(scope: str, agent: str, path: Path) -> Location:
    kind = "file"
    target = None
    if path.is_symlink():
        kind = "symlink"
        try:
            target = str(Path(os.readlink(path)))
            if not Path(target).is_absolute():
                target = str((path.parent / target))
        except OSError:
            target = None
    elif path.is_dir():
        kind = "dir"
    resolved = _resolve(path)
    broken = path.is_symlink() and (resolved is None or not path.exists())
    return Location(
        scope=scope,
        agent=agent,
        path=str(path),
        kind=kind,
        target=target,
        resolved=str(resolved) if resolved else None,
        broken=broken,
    )


def _iter_entries(root: Path) -> Iterable[Path]:
    try:
        children = sorted(root.iterdir(), key=lambda item: item.name.lower())
    except OSError:
        return
    for child in children:
        if child.name.startswith(".") or child.name in SKIP_NAMES:
            continue
        if child.name == "README.md":
            continue
        yield child


def _add(skills: dict[str, Skill], name: str, loc: Location, meta: dict[str, str]) -> None:
    skill = skills.setdefault(name, Skill(name=name))
    skill.locations.append(loc)
    description = meta.get("description") or meta.get("name") or ""
    if description and not skill.description:
        skill.description = _one_line(description, 200)


def discover(cwd: Path) -> dict[str, Skill]:
    skills: dict[str, Skill] = {}
    db = dropbox_skills()
    if db.is_dir():
        for child in _iter_entries(db):
            _add(skills, child.name, _location_from_path("dropbox", "dropbox", child), _read_meta(child))
    for agent, root in user_roots().items():
        if not root.is_dir():
            continue
        for child in _iter_entries(root):
            name = child.stem if child.is_file() else child.name
            _add(skills, name, _location_from_path("user", agent, child), _read_meta(child))
    for agent, root in local_roots(cwd):
        for child in _iter_entries(root):
            name = child.stem if child.is_file() else child.name
            _add(skills, name, _location_from_path("local", agent, child), _read_meta(child))
    for skill in skills.values():
        skill.status = classify(skill, db)
    return skills


def classify(skill: Skill, db_root: Path) -> str:
    if any(loc.broken for loc in skill.locations):
        return "broken"
    db = skill.dropbox()
    user = skill.user()
    local = skill.local()
    db_path = Path(db.resolved) if db and db.resolved else None

    def points_at_db(loc: Location) -> bool:
        if not db_path or not loc.resolved:
            return False
        resolved = Path(loc.resolved)
        return resolved == db_path or _under(resolved, db_path)

    if db_path and user:
        linked = [loc for loc in user if points_at_db(loc)]
        others = [loc for loc in user if not points_at_db(loc)]
        if others and linked:
            return "partial"
        if others and not linked:
            if all(loc.kind == "symlink" for loc in others):
                return "external"
            return "unlinked"
        if linked and not others:
            return "linked"
    if db_path and not user:
        return "dropbox-only"
    if user and not db_path:
        if all(loc.kind == "symlink" for loc in user):
            return "external"
        return "unlinked"
    if local and not user and not db_path:
        return "local"
    if local and (user or db_path):
        return "mixed"
    return "unknown"


def _normalize_agents(values: list[str] | None) -> list[str]:
    if not values:
        return []
    out: list[str] = []
    for raw in values:
        for part in raw.split(","):
            key = part.strip().lower()
            if not key:
                continue
            agent = AGENT_ALIASES.get(key)
            if agent is None:
                raise SystemExit(
                    f"Unknown agent {part!r}. Use claude/cc, codex/cdx, grok, agents, cursor."
                )
            if agent not in out:
                out.append(agent)
    return out


def _filter_skills(
    skills: dict[str, Skill],
    *,
    query: str | None,
    status: str | None,
    issues: bool,
    scope: str | None,
    agents: list[str],
) -> list[Skill]:
    rows = list(skills.values())
    if query:
        needle = query.lower()
        rows = [
            skill
            for skill in rows
            if needle in skill.name.lower() or needle in skill.description.lower()
        ]
    if issues:
        rows = [skill for skill in rows if skill.status in ISSUE_STATUSES]
    if status:
        rows = [skill for skill in rows if skill.status == status]
    if scope:
        rows = [skill for skill in rows if any(loc.scope == scope for loc in skill.locations)]
    if agents:
        wanted = set(agents)
        rows = [skill for skill in rows if any(loc.agent in wanted for loc in skill.locations)]
    rank = {
        "broken": 0,
        "unlinked": 1,
        "partial": 2,
        "dropbox-only": 3,
        "mixed": 4,
        "external": 5,
        "local": 6,
        "linked": 7,
    }
    rows.sort(key=lambda skill: (rank.get(skill.status, 9), skill.name.lower()))
    return rows


def _loc_summary(skill: Skill) -> str:
    user = ",".join(loc.label() for loc in skill.user()) or "-"
    local = ",".join(loc.label() for loc in skill.local()) or "-"
    db = "yes" if skill.dropbox() else "no"
    return db, user, local


def format_table(rows: list[Skill]) -> str:
    if not rows:
        return "No skills matched. Drop --issues / --status, or pass a broader name."
    lines = ["#  STATUS        NAME                        DROPBOX  USER                 LOCAL"]
    for i, skill in enumerate(rows, 1):
        db, user, local = _loc_summary(skill)
        lines.append(
            f"{str(i).rjust(2)}  {skill.status:<12} {_one_line(skill.name, 27):<27} "
            f"{db:<8} {_one_line(user, 20):<20} {_one_line(local, 24)}"
        )
    return "\n".join(lines)


def format_info(skill: Skill) -> str:
    lines = [
        skill.name,
        f"status     {skill.status}",
        f"description {_one_line(skill.description, 160) or '(none)'}",
        f"canonical  {skill.canonical() or '-'}",
        "locations",
    ]
    for loc in skill.locations:
        mark = " BROKEN" if loc.broken else ""
        target = f" -> {loc.resolved or loc.target}" if loc.kind == "symlink" else ""
        lines.append(f"  {loc.scope:<8} {loc.label():<10} {loc.kind:<8} {loc.path}{target}{mark}")
    return "\n".join(lines)


def _confirm(prompt: str, *, yes: bool, tty_in: TextIO | None = None) -> bool:
    if yes:
        return True
    stream_in = tty_in
    stream_out: TextIO
    close_in = False
    close_out = False
    if stream_in is None:
        try:
            stream_in = open("/dev/tty", "r", encoding="utf-8", errors="replace")
            close_in = True
        except OSError:
            stream_in = sys.stdin
    try:
        stream_out = open("/dev/tty", "w", encoding="utf-8", errors="replace")
        close_out = True
    except OSError:
        stream_out = sys.stderr
    try:
        stream_out.write(f"{prompt} [y/N] ")
        stream_out.flush()
        answer = stream_in.readline()
        return answer.strip().lower() in {"y", "yes"}
    finally:
        if close_in:
            stream_in.close()
        if close_out:
            stream_out.close()


def _replace_with_symlink(path: Path, dest: Path) -> str:
    dest = dest.resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() or path.is_symlink():
        if path.is_symlink() or path.is_file():
            current = _resolve(path)
            if current == dest:
                return f"already linked {path}"
            path.unlink()
        else:
            shutil.rmtree(path)
    path.symlink_to(dest)
    return f"linked {path} -> {dest}"


def adopt_to_dropbox(skill: Skill, *, yes: bool) -> list[str]:
    dest = dropbox_skills() / skill.name
    if dest.exists():
        return [f"already in dropbox: {dest}"]
    source: Path | None = None
    move = False
    for loc in skill.user():
        path = Path(loc.path)
        if loc.kind == "dir" and path.is_dir() and not path.is_symlink():
            source = path
            move = True
            break
    if source is None:
        for loc in skill.local():
            path = Path(loc.path)
            if loc.kind == "dir" and path.is_dir() and not path.is_symlink():
                source = path
                move = False
                break
    if source is None:
        raise SystemExit(
            f"{skill.name} has no real directory to copy. Point a user/local dir at a real skill folder first."
        )
    action = "move" if move else "copy"
    if not _confirm(f"{action} {source} -> {dest}?", yes=yes):
        return ["cancelled"]
    dest.parent.mkdir(parents=True, exist_ok=True)
    if move:
        shutil.move(str(source), str(dest))
    else:
        shutil.copytree(source, dest)
    return [f"{action}d {source} -> {dest}"]


def link_skill(skill: Skill, agents: list[str], *, yes: bool, force: bool) -> list[str]:
    db = skill.dropbox()
    dest = Path(db.resolved) if db and db.resolved else dropbox_skills() / skill.name
    if not dest.exists():
        raise SystemExit(
            f"{skill.name} is not in Dropbox ({dest}). Run `skills sync {skill.name}` first."
        )
    if not agents:
        existing = [loc.agent for loc in skill.user()]
        agents = list(dict.fromkeys([*DEFAULT_LINK_AGENTS, *existing]))
        agents = [agent for agent in agents if agent != "commands"]
    roots = user_roots()
    notes: list[str] = []
    for agent in agents:
        root = roots.get(agent)
        if root is None:
            notes.append(f"skip unknown agent {agent}")
            continue
        path = root / skill.name
        loc = next((item for item in skill.user() if item.agent == agent), None)
        if loc and loc.kind == "symlink" and loc.resolved and Path(loc.resolved) != dest.resolve():
            if not force:
                notes.append(
                    f"skip {path} (symlink to {loc.resolved}; pass --force to replace)"
                )
                continue
        if (path.exists() or path.is_symlink()) and not (path.is_symlink() or path.is_file()):
            if not force and not _confirm(f"replace real directory {path} with a symlink to Dropbox?", yes=yes):
                notes.append(f"skip {path}")
                continue
        notes.append(_replace_with_symlink(path, dest))
    return notes


def sync_skill(skill: Skill, agents: list[str], *, yes: bool, force: bool) -> list[str]:
    notes: list[str] = []
    if skill.dropbox() is None:
        notes.extend(adopt_to_dropbox(skill, yes=yes))
        if notes[-1:] == ["cancelled"]:
            return notes
        skill.locations.append(
            _location_from_path("dropbox", "dropbox", dropbox_skills() / skill.name)
        )
    notes.extend(link_skill(skill, agents, yes=yes, force=force))
    return notes


def _open_path(path: Path) -> None:
    if sys.platform == "darwin":
        subprocess.call(["open", str(path)])
    else:
        subprocess.call(["xdg-open", str(path)])


def _script() -> Path:
    return Path(__file__).resolve()


def _self_cmd(*args: str) -> str:
    return " ".join(shlex.quote(part) for part in (sys.executable, str(_script()), *args))


def _state_dir() -> Path:
    raw = os.environ.get("SKILLS_FZF_STATE")
    if not raw:
        raise SystemExit("SKILLS_FZF_STATE is unset. Run `skills` from a terminal, not this helper flag.")
    path = Path(raw)
    if not path.is_dir():
        raise SystemExit(f"skills fzf state missing: {path}")
    return path


def _on_tab(skill: Skill, tab: str) -> bool:
    if tab == "all":
        return True
    if tab == "dropbox":
        return skill.dropbox() is not None
    if tab == "local":
        return bool(skill.local())
    return any(loc.scope == "user" and loc.agent == tab for loc in skill.locations)


def _tabs_for(rows: list[Skill]) -> list[str]:
    tabs = ["all"]
    for tab in TAB_KEYS:
        if tab == "all":
            continue
        if any(_on_tab(skill, tab) for skill in rows):
            tabs.append(tab)
    return tabs


def _tab_header(tabs: list[str], idx: int) -> str:
    parts: list[str] = []
    for i, tab in enumerate(tabs):
        label = TAB_LABEL.get(tab, tab)
        parts.append(f"[{label}]" if i == idx else label)
    return "←/→  " + "  ".join(parts) + "   enter:info  ^s:sync  ^l:link  ^o:open"


def format_fzf_lines(rows: list[Skill]) -> str:
    lines: list[str] = []
    for skill in rows:
        db, user, local = _loc_summary(skill)
        lines.append(
            "\t".join(
                [
                    skill.name,
                    f"{skill.status:<12}",
                    f"{skill.name:<28}",
                    f"{db:<8}",
                    f"{_one_line(user, 24):<24}",
                    _one_line(local, 24),
                ]
            )
        )
    return "\n".join(lines) + ("\n" if lines else "")


def _write_tab_lists(state: Path, rows: list[Skill], tabs: list[str]) -> None:
    for tab in tabs:
        filtered = [skill for skill in rows if _on_tab(skill, tab)]
        (state / f"list-{tab}").write_text(format_fzf_lines(filtered), encoding="utf-8")


def _load_catalog(state: Path) -> dict[str, Skill]:
    payload = json.loads((state / "catalog.json").read_text(encoding="utf-8"))
    skills = [Skill.from_json(item) for item in payload]
    return {skill.name: skill for skill in skills}


def _dump_catalog(state: Path, catalog: dict[str, Skill]) -> None:
    (state / "catalog.json").write_text(
        json.dumps([skill.to_json() for skill in catalog.values()], ensure_ascii=False),
        encoding="utf-8",
    )


def _current_tab(state: Path) -> str:
    tabs = (state / "tabs").read_text(encoding="utf-8").split()
    idx = int((state / "idx").read_text(encoding="utf-8") or "0")
    return tabs[idx % len(tabs)]


def fzf_print_list() -> int:
    state = _state_dir()
    tab = _current_tab(state)
    sys.stdout.write((state / f"list-{tab}").read_text(encoding="utf-8"))
    return 0


def fzf_transform(direction: str) -> int:
    state = _state_dir()
    tabs = (state / "tabs").read_text(encoding="utf-8").split()
    idx = int((state / "idx").read_text(encoding="utf-8") or "0")
    delta = 1 if direction == "right" else -1
    idx = (idx + delta) % len(tabs)
    (state / "idx").write_text(str(idx), encoding="utf-8")
    header = _tab_header(tabs, idx)
    sys.stdout.write(
        f"reload({_self_cmd('--fzf-list')})+change-header({header})"
    )
    return 0


def fzf_refresh() -> int:
    state = _state_dir()
    cwd = Path((state / "cwd").read_text(encoding="utf-8"))
    issues = (state / "issues").read_text(encoding="utf-8").strip() == "1"
    catalog = discover(cwd)
    _dump_catalog(state, catalog)
    rows = _filter_skills(
        catalog,
        query=None,
        status=None,
        issues=issues,
        scope=None,
        agents=[],
    )
    tabs = (state / "tabs").read_text(encoding="utf-8").split()
    _write_tab_lists(state, rows, tabs)
    return 0


def fzf_open(name: str) -> int:
    state = os.environ.get("SKILLS_FZF_STATE")
    if state:
        catalog = _load_catalog(Path(state))
        skill = catalog.get(name)
    else:
        skill = None
    if skill is None:
        catalog = discover(Path.cwd())
        skill = catalog.get(name)
    if skill is None:
        raise SystemExit(f"No skill named {name!r}.")
    path = skill.canonical()
    if path is None:
        raise SystemExit(f"{name} has no canonical path to open.")
    _open_path(path)
    return 0


def interactive(rows: list[Skill], catalog: dict[str, Skill], cwd: Path, *, issues: bool, query: str | None) -> int:
    if not rows:
        raise SystemExit("No skills matched. Drop --issues or pass a broader name.")
    fzf = shutil.which("fzf")
    if fzf is None:
        sys.stdout.write(format_table(rows) + "\n")
        sys.stderr.write(
            "fzf not found; printed --list. Install fzf (`brew install fzf`) — same picker `q` uses.\n"
        )
        return 1
    tabs = _tabs_for(rows)
    with tempfile.TemporaryDirectory(prefix="skills-fzf-") as tmp:
        state = Path(tmp)
        (state / "tabs").write_text(" ".join(tabs), encoding="utf-8")
        (state / "idx").write_text("0", encoding="utf-8")
        (state / "cwd").write_text(str(cwd), encoding="utf-8")
        (state / "issues").write_text("1" if issues else "0", encoding="utf-8")
        _dump_catalog(state, catalog)
        _write_tab_lists(state, rows, tabs)
        env = os.environ.copy()
        env["SKILLS_FZF_STATE"] = str(state)
        list_cmd = _self_cmd("--fzf-list")
        preview_cmd = _self_cmd("info", "{1}")
        sync_cmd = _self_cmd("sync", "{1}")
        link_cmd = _self_cmd("link", "{1}")
        open_cmd = _self_cmd("--fzf-open", "{1}")
        refresh_cmd = _self_cmd("--fzf-refresh")
        transform_left = _self_cmd("--fzf-transform", "left")
        transform_right = _self_cmd("--fzf-transform", "right")
        args = [
            fzf,
            "--ansi",
            "--reverse",
            "--height=80%",
            "--min-height=16",
            "--header-first",
            "--header",
            _tab_header(tabs, 0),
            "--prompt=skills> ",
            "--delimiter=\t",
            "--with-nth=2..",
            "--accept-nth=1",
            "--preview",
            preview_cmd,
            "--preview-window=right:55%:wrap",
            "--bind",
            "start:reload(" + list_cmd + ")",
            "--bind",
            "left:transform(" + transform_left + ")",
            "--bind",
            "right:transform(" + transform_right + ")",
            "--bind",
            "enter:execute(" + _self_cmd("info", "{1}") + " | less -R)",
            "--bind",
            "ctrl-s:execute(" + sync_cmd + ")+execute-silent(" + refresh_cmd + ")+reload(" + list_cmd + ")",
            "--bind",
            "ctrl-l:execute(" + link_cmd + ")+execute-silent(" + refresh_cmd + ")+reload(" + list_cmd + ")",
            "--bind",
            "ctrl-o:execute-silent(" + open_cmd + ")",
        ]
        if query:
            args.extend(["--query", query])
        result = subprocess.run(args, env=env, stdin=subprocess.DEVNULL)
        return 0 if result.returncode in {0, 130} else result.returncode


def _require(skills: dict[str, Skill], name: str) -> Skill:
    if name in skills:
        return skills[name]
    needle = name.lower()
    matches = [skill for skill in skills.values() if needle in skill.name.lower()]
    if len(matches) == 1:
        return matches[0]
    if matches:
        listed = ", ".join(skill.name for skill in matches[:12])
        raise SystemExit(f"ambiguous skill {name!r}: {listed}. Pass the exact name.")
    raise SystemExit(f"No skill named {name!r}. Run `skills --list` to see names.")


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="skills",
        description="Audit local, user, and Dropbox agent skills; sync or inspect one.",
    )
    parser.add_argument("command", nargs="?", help="info | sync | link. Default: interactive / list.")
    parser.add_argument("name", nargs="?", help="Skill name (or a search string).")
    parser.add_argument("--json", action="store_true", help="Print JSON (agents / scripts).")
    parser.add_argument("--list", action="store_true", help="Print a table and skip the picker.")
    parser.add_argument("--issues", action="store_true", help="Only broken / unlinked / partial / dropbox-only.")
    parser.add_argument(
        "--status",
        choices=["linked", "partial", "unlinked", "external", "local", "dropbox-only", "broken", "mixed"],
        help="Filter by computed status.",
    )
    parser.add_argument(
        "--scope",
        choices=["dropbox", "user", "local"],
        help="Only skills that appear in this scope.",
    )
    parser.add_argument(
        "--agent",
        action="append",
        dest="agents",
        help="Filter or (with sync/link) target: claude/cc, codex/cdx, grok, agents, cursor.",
    )
    parser.add_argument("--cwd", default=None, help="Project directory to scan for local skills.")
    parser.add_argument("--yes", action="store_true", help="Do not prompt for sync/link confirmation.")
    parser.add_argument("--force", action="store_true", help="Replace existing non-Dropbox symlinks / dirs.")
    parser.add_argument("--fzf-list", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--fzf-transform", choices=["left", "right"], help=argparse.SUPPRESS)
    parser.add_argument("--fzf-refresh", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--fzf-open", metavar="NAME", help=argparse.SUPPRESS)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    if args.fzf_list:
        return fzf_print_list()
    if args.fzf_transform:
        return fzf_transform(args.fzf_transform)
    if args.fzf_refresh:
        return fzf_refresh()
    if args.fzf_open:
        return fzf_open(args.fzf_open)

    cwd = Path(args.cwd).expanduser().resolve() if args.cwd else Path.cwd()
    command = (args.command or "").lower()
    name = args.name
    if command and command not in {"info", "sync", "link"}:
        name = args.command if not name else f"{args.command} {args.name}"
        command = ""

    state = os.environ.get("SKILLS_FZF_STATE")
    if command == "info" and state and name:
        try:
            skill = _require(_load_catalog(Path(state)), name)
            sys.stdout.write(format_info(skill) + "\n")
            return 0
        except (OSError, json.JSONDecodeError, SystemExit):
            pass

    catalog = discover(cwd)
    agents = _normalize_agents(args.agents)

    if command == "info":
        if not name:
            raise SystemExit("skills info needs a name. Try `skills info threads`.")
        skill = _require(catalog, name)
        if args.json:
            json.dump(skill.to_json(), sys.stdout, indent=2, ensure_ascii=False)
            sys.stdout.write("\n")
        else:
            sys.stdout.write(format_info(skill) + "\n")
        return 0

    if command in {"sync", "link"}:
        if not name:
            raise SystemExit(f"skills {command} needs a name. Try `skills {command} threads`.")
        skill = _require(catalog, name)
        if command == "sync":
            notes = sync_skill(skill, agents, yes=args.yes, force=args.force)
        else:
            notes = link_skill(skill, agents, yes=args.yes, force=args.force)
        sys.stdout.write("\n".join(notes) + "\n")
        return 0

    rows = _filter_skills(
        catalog,
        query=name,
        status=args.status,
        issues=args.issues,
        scope=args.scope,
        agents=agents,
    )
    if args.json:
        json.dump([skill.to_json() for skill in rows], sys.stdout, indent=2, ensure_ascii=False)
        sys.stdout.write("\n")
        return 0
    if args.list or not sys.stdin.isatty():
        counts = {
            "dropbox": sum(1 for skill in catalog.values() if skill.dropbox()),
            "user": sum(1 for skill in catalog.values() if skill.user()),
            "local": sum(1 for skill in catalog.values() if skill.local()),
            "issues": sum(1 for skill in catalog.values() if skill.status in ISSUE_STATUSES),
        }
        sys.stdout.write(
            f"{counts['dropbox']} dropbox  {counts['user']} user  "
            f"{counts['local']} local  {counts['issues']} issues\n"
        )
        sys.stdout.write(format_table(rows) + "\n")
        return 0
    rows = _filter_skills(
        catalog,
        query=None,
        status=args.status,
        issues=args.issues,
        scope=args.scope,
        agents=agents,
    )
    return interactive(rows, catalog, cwd, issues=args.issues, query=name)


if __name__ == "__main__":
    sys.exit(main())
