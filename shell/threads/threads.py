#!/usr/bin/env python3
"""List recent Claude, Codex, and Grok threads. Interactive pick lives in zsh."""

from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import shutil
import sqlite3
import subprocess
import sys
import termios
import time
import tty
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, TextIO

try:
    from select import select as _select
except ImportError:  # pragma: no cover
    _select = None


AGENT_ALIASES = {
    "claude": "claude",
    "cc": "claude",
    "codex": "codex",
    "cdx": "codex",
    "code": "codex",
    "grok": "grok",
    "gx": "grok",
}
RESUME = {
    "claude": ("cc", "--resume"),
    "codex": ("cdx", "resume"),
    "grok": ("grok", "--resume"),
}
LABEL = {"claude": "cc", "codex": "cdx", "grok": "grok"}
UUID_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)
SINCE_RE = re.compile(r"^(\d+)\s*([smhdw])$")
CODEX_TOP_LEVEL = {"cli", "vscode"}
DEFAULT_LIMIT = 20
FIND_LIMIT = 10_000
ID_PREFIX_RE = re.compile(r"^[0-9a-fA-F-]{4,}$")
PROGRESS_WIDTH = 20
PROGRESS_DELAY_S = 0.08


class ThreadLookupError(Exception):
    def __init__(self, query: str, matches: list[dict[str, Any]] | None = None) -> None:
        self.query = query
        self.matches = matches or []
        super().__init__(query)


class MissingThreadError(ThreadLookupError):
    pass


class AmbiguousThreadError(ThreadLookupError):
    pass


def _home() -> Path:
    return Path.home()


def _iso(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).astimezone().isoformat(timespec="seconds")


def _rel(ms: int, now_ms: int) -> str:
    delta = max(0, now_ms - ms) / 1000
    if delta < 45:
        return "just now"
    if delta < 90:
        return "1m ago"
    if delta < 3600:
        return f"{int(delta / 60)}m ago"
    if delta < 5400:
        return "1h ago"
    if delta < 86400:
        return f"{int(delta / 3600)}h ago"
    if delta < 172800:
        return "1d ago"
    return f"{int(delta / 86400)}d ago"


_COMMAND_TAG_RE = re.compile(r"</?command-(?:message|name)>")


def _one_line(value: Any, limit: int = 80) -> str:
    text = _COMMAND_TAG_RE.sub(" ", str(value or ""))
    text = " ".join(text.split())
    if len(text) <= limit:
        return text
    return text[: limit - 1] + "…"


def _parse_since(raw: str | None) -> int | None:
    if not raw:
        return None
    s = raw.strip().lower()
    now = datetime.now().astimezone()
    if s in {"today"}:
        start = datetime.combine(now.date(), datetime.min.time(), tzinfo=now.tzinfo)
        return int(start.timestamp() * 1000)
    if s in {"yesterday"}:
        start = datetime.combine(now.date() - timedelta(days=1), datetime.min.time(), tzinfo=now.tzinfo)
        return int(start.timestamp() * 1000)
    m = SINCE_RE.fullmatch(s.replace(" ", ""))
    if m:
        n = int(m.group(1))
        unit = m.group(2)
        seconds = {"s": 1, "m": 60, "h": 3600, "d": 86400, "w": 604800}[unit]
        return int((now - timedelta(seconds=n * seconds)).timestamp() * 1000)
    for fmt in ("%Y-%m-%d", "%Y-%m-%dT%H:%M", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            dt = datetime.strptime(s, fmt)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=now.tzinfo)
            return int(dt.timestamp() * 1000)
        except ValueError:
            continue
    raise SystemExit(
        f"Could not parse --since {raw!r}. Use today, yesterday, 2h, 30m, 7d, or YYYY-MM-DD."
    )


def _normalize_agents(values: list[str] | None) -> set[str] | None:
    if not values:
        return None
    out: set[str] = set()
    for raw in values:
        for part in raw.split(","):
            key = part.strip().lower()
            if not key:
                continue
            agent = AGENT_ALIASES.get(key)
            if agent is None:
                raise SystemExit(
                    f"Unknown agent {part!r}. Use claude/cc, codex/cdx/code, or grok."
                )
            out.add(agent)
    return out or None


def _row(
    agent: str,
    session_id: str,
    title: str,
    cwd: str,
    updated_ms: int,
) -> dict[str, Any]:
    prefix = RESUME[agent]
    return {
        "agent": agent,
        "id": session_id,
        "title": _one_line(title, 200) or "(untitled)",
        "cwd": cwd or "",
        "updated_at_ms": updated_ms,
        "updated_at": _iso(updated_ms),
        "resume": " ".join((*prefix, session_id)),
        "resume_argv": [*prefix, session_id],
    }


def _is_tty(stream: Any) -> bool:
    checker = getattr(stream, "isatty", None)
    return callable(checker) and bool(checker())


def _progress_bar(pct: int, width: int = PROGRESS_WIDTH) -> str:
    pct = max(0, min(100, pct))
    filled = min(width, max(0, pct * width // 100))
    return "█" * filled + "░" * (width - filled)


def format_progress(done: int, total: int, label: str = "loading") -> str:
    if total <= 0:
        pct = 100
        done = 0
        total = 0
    else:
        pct = min(100, int(done * 100 / total))
    return f"{label}  {_progress_bar(pct)}  {pct}%  {done}/{total}"


class Progress:
    """TTY progress line. Hidden unless work lasts longer than PROGRESS_DELAY_S."""

    def __init__(self, stream: TextIO | None, *, enabled: bool | None = None) -> None:
        self.stream = stream
        if enabled is None:
            enabled = _is_tty(stream)
        self.enabled = bool(enabled and stream)
        self.started = time.monotonic()
        self.shown = False

    def update(self, done: int, total: int, label: str = "loading") -> None:
        if not self.enabled or self.stream is None:
            return
        if not self.shown:
            if done < total and (time.monotonic() - self.started) < PROGRESS_DELAY_S:
                return
            self.shown = True
        self.stream.write("\r\x1b[2K" + format_progress(done, total, label))
        self.stream.flush()

    def finish(self) -> None:
        if not self.shown or self.stream is None:
            return
        self.stream.write("\r\x1b[2K")
        self.stream.flush()
        self.shown = False


def _public_row(row: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in row.items() if not key.startswith("_")}


def _needs_hydrate(row: dict[str, Any]) -> bool:
    return row.get("_hydrated") is False


def _jsonl_records(path: Path, *, head_bytes: int = 0, tail_bytes: int = 0) -> list[dict[str, Any]]:
    try:
        size = path.stat().st_size
        with path.open("rb") as handle:
            if head_bytes and size > head_bytes and not tail_bytes:
                data = handle.read(head_bytes)
            elif tail_bytes and size > tail_bytes:
                handle.seek(-tail_bytes, os.SEEK_END)
                handle.readline()
                data = handle.read()
            else:
                data = handle.read(max(head_bytes, tail_bytes) or size)
    except OSError:
        return []
    records: list[dict[str, Any]] = []
    for line in data.decode("utf-8", "replace").splitlines():
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            records.append(value)
    return records


def _user_text(record: dict[str, Any]) -> str:
    if record.get("type") != "user":
        return ""
    message = record.get("message")
    content: Any = None
    if isinstance(message, dict):
        content = message.get("content")
    elif isinstance(message, str):
        content = message
    if content is None:
        content = record.get("content")
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                if item.get("type") in {"tool_result", "tool_use", "image"}:
                    continue
                text = item.get("text")
                if isinstance(text, str):
                    parts.append(text)
        return "\n".join(parts).strip()
    return ""


def _claude_title_and_cwd(path: Path) -> tuple[str, str]:
    head = _jsonl_records(path, head_bytes=64_000)
    tail = _jsonl_records(path, tail_bytes=64_000)
    cwd = ""
    title = ""
    first_user = ""
    for record in head + tail:
        if not cwd:
            raw = record.get("cwd")
            if isinstance(raw, str) and raw.startswith("/"):
                cwd = raw
        kind = record.get("type")
        if kind == "ai-title":
            value = record.get("aiTitle") or record.get("title")
            if isinstance(value, str) and value.strip():
                title = value.strip()
        elif kind == "custom-title":
            value = record.get("customTitle") or record.get("title") or record.get("summary")
            if isinstance(value, str) and value.strip():
                title = value.strip()
        elif kind == "summary" and not title:
            value = record.get("summary") or record.get("title")
            if isinstance(value, str) and value.strip():
                title = value.strip()
        elif kind == "last-prompt" and not title:
            value = record.get("lastPrompt")
            if isinstance(value, str) and value.strip():
                title = value.strip()
        if not first_user:
            text = _user_text(record)
            if text:
                first_user = text
    if not cwd:
        cwd = _claude_slug_cwd(path)
    return (title or first_user or "(untitled)", cwd)


def _claude_slug_cwd(path: Path) -> str:
    slug = path.parent.name
    if slug.startswith("-"):
        return "/" + slug[1:].replace("-", "/")
    return ""


def _claude_cwd_quick(path: Path) -> str:
    for record in _jsonl_records(path, head_bytes=16_000):
        raw = record.get("cwd")
        if isinstance(raw, str) and raw.startswith("/"):
            return raw
    return _claude_slug_cwd(path)


def _claude_paths() -> list[Path]:
    projects = Path(os.environ.get("CLAUDE_CONFIG_DIR", _home() / ".claude")) / "projects"
    if not projects.is_dir():
        return []
    try:
        files = projects.glob("*/*.jsonl")
    except OSError:
        return []
    return [
        path
        for path in files
        if not path.is_symlink() and path.is_file() and UUID_RE.fullmatch(path.stem)
    ]


def _claude_stub(
    path: Path,
    since_ms: int | None,
    here: str | None,
) -> dict[str, Any] | None:
    try:
        updated_ms = int(path.stat().st_mtime * 1000)
    except OSError:
        return None
    if since_ms is not None and updated_ms < since_ms:
        return None
    cwd = _claude_cwd_quick(path) if here else _claude_slug_cwd(path)
    if here and os.path.normpath(cwd or "") != here:
        return None
    row = _row("claude", path.stem, "", cwd, updated_ms)
    row["_path"] = str(path)
    row["_hydrated"] = False
    return row


def _sqlite(path: Path) -> sqlite3.Connection | None:
    if not path.is_file():
        return None
    try:
        conn = sqlite3.connect(f"file:{path.resolve().as_posix()}?mode=ro", uri=True)
        conn.execute("SELECT 1")
        return conn
    except sqlite3.Error:
        try:
            return sqlite3.connect(str(path))
        except sqlite3.Error:
            return None


def _codex_state_db() -> Path | None:
    home = Path(os.environ.get("CODEX_HOME", _home() / ".codex"))
    best: tuple[int, Path] | None = None
    try:
        children = home.iterdir()
    except OSError:
        return None
    for path in children:
        m = re.fullmatch(r"state_(\d+)\.sqlite", path.name)
        if m and path.is_file() and not path.is_symlink():
            ver = int(m.group(1))
            if best is None or ver > best[0]:
                best = (ver, path)
    return None if best is None else best[1]


def _codex_source_ok(source: Any) -> bool:
    if isinstance(source, str) and source in CODEX_TOP_LEVEL:
        return True
    if isinstance(source, str) and source.startswith("{"):
        return False
    return False


def list_codex(since_ms: int | None, here: str | None) -> list[dict[str, Any]]:
    db_path = _codex_state_db()
    if db_path is None:
        return []
    conn = _sqlite(db_path)
    if conn is None:
        return []
    rows: list[dict[str, Any]] = []
    try:
        cols = {row[1] for row in conn.execute("PRAGMA table_info(threads)")}
        if not {"id", "cwd", "archived", "source"}.issubset(cols):
            return []
        updated = "updated_at_ms" if "updated_at_ms" in cols else "updated_at"
        title_expr = "title" if "title" in cols else "''"
        name_expr = "name" if "name" in cols else "''"
        first_expr = "first_user_message" if "first_user_message" in cols else "''"
        query = (
            f"SELECT id, cwd, {updated}, source, {title_expr}, {name_expr}, {first_expr} "
            "FROM threads WHERE archived = 0"
        )
        for session_id, cwd, raw_updated, source, title, name, first in conn.execute(query):
            if not isinstance(session_id, str) or not UUID_RE.fullmatch(session_id):
                continue
            if not _codex_source_ok(source):
                continue
            if isinstance(raw_updated, int):
                updated_ms = raw_updated if raw_updated > 10_000_000_000 else raw_updated * 1000
            elif isinstance(raw_updated, float):
                updated_ms = int(raw_updated if raw_updated > 10_000_000_000 else raw_updated * 1000)
            else:
                continue
            if since_ms is not None and updated_ms < since_ms:
                continue
            stored_cwd = cwd if isinstance(cwd, str) else ""
            if here and os.path.normpath(stored_cwd or "") != here:
                continue
            label = name or title or first or "(untitled)"
            row = _row("codex", session_id, label, stored_cwd, updated_ms)
            row["_hydrated"] = True
            rows.append(row)
    except sqlite3.Error:
        return []
    finally:
        conn.close()
    return rows


def _grok_paths() -> list[Path]:
    root = Path(os.environ.get("GROK_HOME", _home() / ".grok")) / "sessions"
    if not root.is_dir():
        return []
    try:
        summaries = root.glob("*/*/summary.json")
    except OSError:
        return []
    return [path for path in summaries if not path.is_symlink()]


def _grok_from_summary(path: Path, since_ms: int | None, here: str | None) -> dict[str, Any] | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    info = data.get("info") if isinstance(data.get("info"), dict) else {}
    session_id = info.get("id") or path.parent.name
    if not isinstance(session_id, str) or not session_id:
        return None
    cwd = info.get("cwd") if isinstance(info.get("cwd"), str) else ""
    if here and os.path.normpath(cwd or "") != here:
        return None
    stamp = data.get("last_active_at") or data.get("updated_at")
    updated_ms = 0
    if isinstance(stamp, str):
        try:
            updated_ms = int(datetime.fromisoformat(stamp.replace("Z", "+00:00")).timestamp() * 1000)
        except ValueError:
            updated_ms = 0
    if not updated_ms:
        try:
            updated_ms = int(path.stat().st_mtime * 1000)
        except OSError:
            return None
    if since_ms is not None and updated_ms < since_ms:
        return None
    title = (
        data.get("generated_title")
        or data.get("session_summary")
        or data.get("last_turn_summary")
        or "(untitled)"
    )
    row = _row("grok", session_id, str(title), cwd, updated_ms)
    row["_path"] = str(path)
    row["_hydrated"] = True
    return row


def _grok_stub(path: Path, since_ms: int | None, here: str | None) -> dict[str, Any] | None:
    if here:
        return _grok_from_summary(path, since_ms, here)
    try:
        updated_ms = int(path.stat().st_mtime * 1000)
    except OSError:
        return None
    if since_ms is not None and updated_ms < since_ms:
        return None
    row = _row("grok", path.parent.name, "", "", updated_ms)
    row["_path"] = str(path)
    row["_hydrated"] = False
    return row


def hydrate_row(row: dict[str, Any]) -> dict[str, Any]:
    if not _needs_hydrate(row):
        return row
    raw_path = row.get("_path")
    path = Path(raw_path) if isinstance(raw_path, str) else None
    if row["agent"] == "claude" and path is not None:
        title, cwd = _claude_title_and_cwd(path)
        row["title"] = _one_line(title, 200) or "(untitled)"
        if cwd:
            row["cwd"] = cwd
    elif row["agent"] == "grok" and path is not None:
        loaded = _grok_from_summary(path, None, None)
        if loaded is not None:
            row["id"] = loaded["id"]
            row["title"] = loaded["title"]
            row["cwd"] = loaded["cwd"]
            row["resume"] = loaded["resume"]
            row["resume_argv"] = loaded["resume_argv"]
    row["_hydrated"] = True
    return row


def hydrate_rows(
    rows: list[dict[str, Any]],
    progress: Progress | None = None,
    label: str = "loading",
) -> None:
    pending = [row for row in rows if _needs_hydrate(row)]
    total = len(pending)
    if total == 0:
        return
    for i, row in enumerate(pending, 1):
        hydrate_row(row)
        if progress is not None:
            progress.update(i, total, label)
    if progress is not None:
        progress.finish()


def _dedupe_sort(rows: list[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
    rows.sort(key=lambda item: item["updated_at_ms"], reverse=True)
    seen: set[tuple[str, str]] = set()
    out: list[dict[str, Any]] = []
    for row in rows:
        key = (row["agent"], row["id"])
        if key in seen:
            continue
        seen.add(key)
        out.append(row)
        if len(out) >= limit:
            break
    return out


def index_threads(
    agents: set[str] | None,
    since_ms: int | None,
    here: str | None,
    limit: int,
    progress: Progress | None = None,
) -> list[dict[str, Any]]:
    wanted = agents or {"claude", "codex", "grok"}
    claude_files = _claude_paths() if "claude" in wanted else []
    grok_files = _grok_paths() if "grok" in wanted else []
    do_codex = "codex" in wanted
    total = len(claude_files) + len(grok_files) + (1 if do_codex else 0)
    done = 0
    rows: list[dict[str, Any]] = []
    for path in claude_files:
        stub = _claude_stub(path, since_ms, here)
        if stub is not None:
            rows.append(stub)
        done += 1
        if progress is not None:
            progress.update(done, total, "scanning")
    if do_codex:
        rows.extend(list_codex(since_ms, here))
        done += 1
        if progress is not None:
            progress.update(done, total, "scanning")
    for path in grok_files:
        stub = _grok_stub(path, since_ms, here)
        if stub is not None:
            rows.append(stub)
        done += 1
        if progress is not None:
            progress.update(done, total, "scanning")
    if progress is not None:
        progress.finish()
    return _dedupe_sort(rows, limit)


def collect(
    agents: set[str] | None,
    since_ms: int | None,
    here: str | None,
    limit: int,
    progress: Progress | None = None,
) -> list[dict[str, Any]]:
    rows = index_threads(agents, since_ms, here, limit, progress=progress)
    hydrate_rows(rows, progress=progress, label="loading")
    return [_public_row(row) for row in rows]


def paginate(
    rows: list[dict[str, Any]],
    *,
    page_size: int,
    page: int = 1,
    offset: int | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    if page_size < 1:
        raise SystemExit("--limit must be >= 1")
    if offset is None:
        if page < 1:
            raise SystemExit("--page must be >= 1")
        offset = (page - 1) * page_size
    elif offset < 0:
        raise SystemExit("--offset must be >= 0")
    total = len(rows)
    pages = max(1, (total + page_size - 1) // page_size) if total else 1
    sliced = rows[offset : offset + page_size]
    shown_page = offset // page_size + 1
    return sliced, {
        "page": shown_page,
        "page_size": page_size,
        "pages": pages,
        "total": total,
        "offset": offset,
        "has_more": offset + len(sliced) < total,
    }


def _id_key(value: str) -> str:
    return value.lower().replace("-", "")


def find_thread(query: str, rows: list[dict[str, Any]]) -> dict[str, Any]:
    q = (query or "").strip()
    if not q:
        raise MissingThreadError(query)
    q_lower = q.lower()
    q_id = _id_key(q)

    exact_id = [
        row
        for row in rows
        if row["id"].lower() == q_lower or _id_key(row["id"]) == q_id
    ]
    if len(exact_id) == 1:
        return exact_id[0]
    if len(exact_id) > 1:
        raise AmbiguousThreadError(query, exact_id)

    if ID_PREFIX_RE.fullmatch(q):
        prefix = [
            row
            for row in rows
            if row["id"].lower().startswith(q_lower) or _id_key(row["id"]).startswith(q_id)
        ]
        if len(prefix) == 1:
            return prefix[0]
        if len(prefix) > 1:
            raise AmbiguousThreadError(query, prefix)

    exact_title = [row for row in rows if str(row.get("title") or "").lower() == q_lower]
    if len(exact_title) == 1:
        return exact_title[0]
    if len(exact_title) > 1:
        raise AmbiguousThreadError(query, exact_title)

    title_prefix = [
        row for row in rows if str(row.get("title") or "").lower().startswith(q_lower)
    ]
    if len(title_prefix) == 1:
        return title_prefix[0]
    if len(title_prefix) > 1:
        raise AmbiguousThreadError(query, title_prefix)

    title_sub = [row for row in rows if q_lower in str(row.get("title") or "").lower()]
    if len(title_sub) == 1:
        return title_sub[0]
    if len(title_sub) > 1:
        raise AmbiguousThreadError(query, title_sub)

    raise MissingThreadError(query)


def _dir_label(cwd: str) -> str:
    if not cwd:
        return "?"
    return Path(cwd).name or cwd


def _format_table(rows: list[dict[str, Any]], now_ms: int, start_index: int = 1) -> str:
    if not rows:
        return "No threads found."
    last = start_index + len(rows) - 1
    width = max(2, len(str(last)))
    lines = [f"{'#'.ljust(width)}  AGENT  WHEN      DIR                  TITLE"]
    for i, row in enumerate(rows):
        num = str(start_index + i).rjust(width)
        agent = LABEL[row["agent"]].ljust(5)
        when = _rel(row["updated_at_ms"], now_ms).ljust(9)
        directory = _dir_label(row["cwd"]).ljust(20)[:20]
        title = _one_line(row["title"], 72)
        lines.append(f"{num}  {agent}  {when} {directory} {title}")
    return "\n".join(lines)


def _format_page_footer(meta: dict[str, Any], shown: int, *, command: str = "threads --list") -> str:
    if meta["total"] == 0 or meta["pages"] <= 1:
        return ""
    line = f"page {meta['page']}/{meta['pages']}  ({shown} of {meta['total']})"
    if meta["has_more"]:
        line += f"  next: {command} --page {meta['page'] + 1}"
    elif meta["page"] > 1:
        line += f"  prev: {command} --page {meta['page'] - 1}"
    return line


def _read_byte(fd: int) -> str:
    data = os.read(fd, 1)
    if not data:
        return ""
    return data.decode("latin1")


def _read_key(fd: int) -> str:
    # Unbuffered os.read: a buffered TextIO.read(1) slurps the rest of
    # ESC-[A into Python's buffer, then select() on the fd times out.
    ch = _read_byte(fd)
    if ch != "\x1b":
        return ch
    rest = ""
    while _select is not None and _select([fd], [], [], 0.03)[0]:
        rest += _read_byte(fd)
        if len(rest) >= 8:
            break
    last = rest[-1:] if rest else ""
    if last in "ABCD" and rest[:1] in {"[", "O"}:
        return {"A": "up", "B": "down", "C": "right", "D": "left"}[last]
    if rest.startswith("[5"):
        return "pageup"
    if rest.startswith("[6"):
        return "pagedown"
    if rest in {"[H", "OH"}:
        return "home"
    if rest in {"[F", "OF"}:
        return "end"
    return "esc"


def _page_count(n: int, page_size: int) -> int:
    if n <= 0:
        return 1
    return max(1, (n + page_size - 1) // page_size)


def _move_pick(
    key: str,
    *,
    idx: int,
    page: int,
    page_len: int,
    pages: int,
    page_size: int,
) -> tuple[int, int] | None:
    """Return new (idx, page) for a movement key, or None if it isn't one."""
    if page_len <= 0:
        return idx, page
    if key in {"up", "k"}:
        if idx > 0:
            return idx - 1, page
        if page > 0:
            return page_size - 1, page - 1
        return page_len - 1, page
    if key in {"down", "j"}:
        if idx + 1 < page_len:
            return idx + 1, page
        if page + 1 < pages:
            return 0, page + 1
        return 0, page
    if key in {"n", "N", "right", "pagedown", " "}:
        if page + 1 < pages:
            return 0, page + 1
        return idx, page
    if key in {"p", "P", "left", "pageup"}:
        if page > 0:
            return min(idx, page_size - 1), page - 1
        return idx, page
    if key == "home":
        return 0, 0
    if key == "end":
        return page_size - 1, pages - 1
    return None


def _erase_drawn(tty_out: TextIO, drawn: int) -> int:
    if not drawn:
        return 0
    tty_out.write(f"\x1b[{drawn}A")
    for _ in range(drawn):
        tty_out.write("\x1b[2K\r\n")
    tty_out.write(f"\x1b[{drawn}A")
    tty_out.flush()
    return 0


def _hydrate_next(rows: list[dict[str, Any]], start: int, end: int) -> int:
    for i in range(start, end):
        if _needs_hydrate(rows[i]):
            hydrate_row(rows[i])
            return i + 1
    return end


def _wait_key(fd: int, prefetch: list[dict[str, Any]]) -> str:
    i = 0
    n = len(prefetch)
    while True:
        if _select is None:
            return _read_key(fd)
        ready = _select([fd], [], [], 0.05)[0]
        if ready:
            return _read_key(fd)
        if i < n:
            if _needs_hydrate(prefetch[i]):
                hydrate_row(prefetch[i])
            i += 1
            continue
        return _read_key(fd)


def _search_blob(row: dict[str, Any]) -> str:
    agent = str(row.get("agent") or "")
    return " ".join(
        [
            LABEL.get(agent, agent),
            agent,
            _dir_label(str(row.get("cwd") or "")),
            str(row.get("cwd") or ""),
            str(row.get("title") or ""),
            str(row.get("id") or ""),
        ]
    ).lower()


def filter_rows(rows: list[dict[str, Any]], query: str) -> list[dict[str, Any]]:
    parts = [part for part in query.lower().split() if part]
    if not parts:
        return rows
    return [row for row in rows if all(part in _search_blob(row) for part in parts)]


def _printable_char(key: str) -> str | None:
    if len(key) != 1:
        return None
    if 32 <= ord(key) <= 126:
        return key
    return None


def _copy_to_clipboard(value: str) -> bool:
    commands = (
        ("pbcopy",),
        ("wl-copy",),
        ("xclip", "-selection", "clipboard"),
        ("xsel", "--clipboard", "--input"),
        ("clip.exe",),
    )
    for command in commands:
        if shutil.which(command[0]) is None:
            continue
        try:
            subprocess.run(
                command,
                input=value,
                text=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=True,
            )
        except (OSError, subprocess.CalledProcessError):
            continue
        return True
    return False


def _pick(
    rows: list[dict[str, Any]],
    now_ms: int,
    page_size: int = DEFAULT_LIMIT,
    start_page: int = 0,
) -> dict[str, Any] | None:
    try:
        tty_in = open("/dev/tty", "r", encoding="utf-8", errors="replace")
        tty_out = open("/dev/tty", "w", encoding="utf-8", errors="replace")
    except OSError as exc:
        raise SystemExit(
            f"No TTY for interactive pick ({exc}). Re-run with --json or --list, or use `threads` in a terminal."
        ) from exc
    fd = tty_in.fileno()
    old = termios.tcgetattr(fd)
    idx = 0
    page = max(0, start_page)
    pages = _page_count(len(rows), page_size)
    if page >= pages:
        page = pages - 1
    drawn = 0
    page_progress = Progress(tty_out, enabled=True)
    searching = False
    query = ""
    notice = ""
    try:
        tty.setraw(fd)
        tty_out.write("\x1b[?25l")
        while True:
            view = filter_rows(rows, query) if searching else rows
            pages = _page_count(len(view), page_size)
            if page >= pages:
                page = max(0, pages - 1)
            start = page * page_size
            page_rows = view[start : start + page_size]
            page_n = len(page_rows)
            if not searching and page_n == 0:
                return None
            if page_n == 0:
                idx = 0
            elif idx >= page_n:
                idx = page_n - 1
            pending = [row for row in page_rows if _needs_hydrate(row)]
            if pending:
                drawn = _erase_drawn(tty_out, drawn)
                hydrate_rows(pending, progress=page_progress, label="loading")
            if drawn:
                tty_out.write(f"\x1b[{drawn}A")
            if searching:
                nmatch = len(view)
                noun = "match" if nmatch == 1 else "matches"
                typed = _one_line(query, 40)
                if pages > 1:
                    header = (
                        f"search: {typed}█  page {page + 1}/{pages}  {nmatch} {noun}  "
                        f"↑/↓  enter  esc\r\n"
                    )
                else:
                    header = (
                        f"search: {typed}█  {nmatch} {noun}  ↑/↓  enter  esc\r\n"
                    )
            elif pages > 1:
                header = (
                    f"page {page + 1}/{pages}  ↑/↓ pick  ←/→ n/p page  "
                    f"s search  c copy id  1-9  enter  q"
                )
            else:
                header = "↑/↓ or 1-9 pick  s search  c copy id  enter resume  q cancel"
            if not searching and notice:
                header += f"  · {notice}"
            header += "\r\n"
            tty_out.write("\x1b[2K" + header)
            if page_n == 0:
                tty_out.write("\x1b[2KNo matches.\r\n")
                new_drawn = 2
            else:
                for i, row in enumerate(page_rows):
                    marker = "▸" if i == idx else " "
                    num = f"{i + 1}" if i < 9 else " "
                    agent = LABEL[row["agent"]].ljust(5)
                    when = _rel(row["updated_at_ms"], now_ms).ljust(9)
                    directory = _dir_label(row["cwd"]).ljust(16)[:16]
                    title = _one_line(row["title"], 56)
                    line = f"{marker} {num}  {agent}  {when} {directory} {title}"
                    if i == idx:
                        line = f"\x1b[7m{line}\x1b[0m"
                    tty_out.write("\x1b[2K" + line + "\r\n")
                new_drawn = page_n + 1
            if drawn > new_drawn:
                extra = drawn - new_drawn
                for _ in range(extra):
                    tty_out.write("\x1b[2K\r\n")
                tty_out.write(f"\x1b[{extra}A")
            drawn = new_drawn
            tty_out.flush()
            key = _wait_key(fd, view[start + page_size : start + page_size * 2])
            if key == "\x03":
                return None
            if searching:
                if key == "esc":
                    if query:
                        query = ""
                    else:
                        selected = page_rows[idx] if page_rows else None
                        searching = False
                        if selected is not None:
                            pos = rows.index(selected)
                            page = pos // page_size
                            idx = pos % page_size
                        else:
                            page = 0
                            idx = 0
                    continue
                if key in {"\x7f", "\x08"}:
                    query = query[:-1]
                    page = 0
                    idx = 0
                    continue
                if key == "\x15":
                    query = ""
                    page = 0
                    idx = 0
                    continue
                if key in {"\r", "\n"}:
                    if page_rows:
                        return _public_row(page_rows[idx])
                    continue
                if key in {"up", "down", "k", "j", "pageup", "pagedown", "home", "end"}:
                    moved = _move_pick(
                        key,
                        idx=idx,
                        page=page,
                        page_len=max(page_n, 1),
                        pages=pages,
                        page_size=page_size,
                    )
                    if moved is not None:
                        idx, page = moved
                        if idx >= page_size:
                            idx = page_size - 1
                        if idx < 0:
                            idx = 0
                    continue
                typed = _printable_char(key)
                if typed is not None:
                    query += typed
                    page = 0
                    idx = 0
                continue
            if key in {"q", "Q", "esc"}:
                return None
            if key in {"c", "C"}:
                thread_id = str(page_rows[idx]["id"])
                notice = (
                    f"copied {thread_id}"
                    if _copy_to_clipboard(thread_id)
                    else "copy failed (no clipboard command)"
                )
                continue
            notice = ""
            if key in {"s", "S", "/"}:
                rest = [row for row in rows if _needs_hydrate(row)]
                if rest:
                    drawn = _erase_drawn(tty_out, drawn)
                    hydrate_rows(rest, progress=page_progress, label="loading")
                searching = True
                query = ""
                page = 0
                idx = 0
                continue
            if key in {"\r", "\n"}:
                return _public_row(page_rows[idx])
            if key in "123456789":
                choice = int(key) - 1
                if choice < page_n:
                    return _public_row(page_rows[choice])
                continue
            moved = _move_pick(
                key,
                idx=idx,
                page=page,
                page_len=page_n,
                pages=pages,
                page_size=page_size,
            )
            if moved is None:
                continue
            idx, page = moved
            if idx >= page_size:
                idx = page_size - 1
            if idx < 0:
                idx = 0
    finally:
        tty_out.write("\x1b[?25h")
        _erase_drawn(tty_out, drawn)
        termios.tcsetattr(fd, termios.TCSADRAIN, old)
        tty_in.close()
        tty_out.close()


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="threads",
        description="List recent Claude (cc), Codex (cdx), and Grok threads.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Print JSON and skip the picker (for agents / scripts).",
    )
    parser.add_argument("--list", action="store_true", help="Print a table and skip the picker.")
    parser.add_argument(
        "--pick",
        action="store_true",
        help="Interactive picker; print the chosen thread as JSON on stdout.",
    )
    parser.add_argument(
        "--since",
        metavar="WHEN",
        help="Only threads newer than this: today, yesterday, 2h, 30m, 7d, YYYY-MM-DD.",
    )
    parser.add_argument(
        "--agent",
        action="append",
        dest="agents",
        metavar="AGENT",
        help="Filter: claude/cc, codex/cdx/code, grok. Repeatable or comma-separated.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help=f"Page size (default {DEFAULT_LIMIT}).",
    )
    parser.add_argument(
        "--page",
        type=int,
        default=1,
        help="1-based page (default 1). Combine with --limit.",
    )
    parser.add_argument(
        "--offset",
        type=int,
        default=None,
        help="Skip this many rows. Cannot combine with --page.",
    )
    parser.add_argument("--here", action="store_true", help="Only threads whose cwd is the current directory.")
    parser.add_argument("--cwd", metavar="DIR", help="Only threads whose cwd is DIR.")
    parser.add_argument(
        "--find",
        metavar="QUERY",
        help="Find one thread by id, id prefix, or title/alias. Print JSON (for `resume`).",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    since_ms = _parse_since(args.since)
    agents = _normalize_agents(args.agents)
    here = None
    if args.here or args.cwd:
        here = os.path.normpath(os.path.abspath(os.path.expanduser(args.cwd or os.getcwd())))
    page_size = args.limit if args.limit is not None else DEFAULT_LIMIT
    if page_size < 1:
        raise SystemExit("--limit must be >= 1")
    if args.offset is not None and args.page != 1:
        raise SystemExit("Use --page or --offset, not both.")
    if args.page < 1:
        raise SystemExit("--page must be >= 1")
    show_progress = (not args.json) and _is_tty(sys.stderr)
    progress = Progress(sys.stderr, enabled=show_progress)
    pool = index_threads(agents, since_ms, here, FIND_LIMIT, progress=progress)
    now_ms = int(datetime.now().timestamp() * 1000)
    if args.find:
        try:
            chosen = find_thread(args.find, pool)
        except MissingThreadError:
            hydrate_rows(pool, progress=progress, label="loading")
            try:
                chosen = find_thread(args.find, pool)
            except MissingThreadError:
                sys.stderr.write(
                    f"No thread matching {args.find!r}. Try `threads --since 7d` or a longer id prefix.\n"
                )
                return 1
            except AmbiguousThreadError as exc:
                hydrate_rows(exc.matches, progress=progress, label="loading")
                sys.stderr.write(
                    f"{len(exc.matches)} threads match {args.find!r}. Be more specific "
                    "(longer id prefix, exact title, or `--agent cc|cdx|grok`).\n"
                )
                sys.stderr.write(_format_table(exc.matches, now_ms) + "\n")
                return 2
        except AmbiguousThreadError as exc:
            hydrate_rows(exc.matches, progress=progress, label="loading")
            sys.stderr.write(
                f"{len(exc.matches)} threads match {args.find!r}. Be more specific "
                "(longer id prefix, exact title, or `--agent cc|cdx|grok`).\n"
            )
            sys.stderr.write(_format_table(exc.matches, now_ms) + "\n")
            return 2
        hydrate_row(chosen)
        json.dump(_public_row(chosen), sys.stdout, ensure_ascii=False)
        sys.stdout.write("\n")
        return 0
    rows, meta = paginate(
        pool,
        page_size=page_size,
        page=args.page,
        offset=args.offset,
    )
    if args.json or (args.list and not args.pick):
        if not rows and meta["total"] > 0:
            sys.stderr.write(
                f"Page {meta['page']} is empty ({meta['total']} threads, "
                f"{meta['pages']} pages). Try `--page {meta['pages']}`.\n"
            )
            return 1
        hydrate_rows(rows, progress=progress, label="loading")
        public = [_public_row(row) for row in rows]
        if args.json:
            json.dump(public, sys.stdout, indent=2, ensure_ascii=False)
            sys.stdout.write("\n")
            footer = _format_page_footer(meta, len(public), command="threads --json")
            if footer:
                sys.stderr.write(footer + "\n")
        else:
            sys.stdout.write(_format_table(public, now_ms, start_index=meta["offset"] + 1) + "\n")
            footer = _format_page_footer(meta, len(public))
            if footer:
                sys.stdout.write(footer + "\n")
        return 0
    if not pool:
        where = f" since {args.since}" if args.since else ""
        sys.stderr.write(
            f"No threads{where}. Try a wider window (`threads --since 7d`) or drop the filter.\n"
        )
        return 1
    if args.pick or sys.stdin.isatty():
        start_page = meta["page"] - 1
        start = start_page * page_size
        hydrate_rows(pool[start : start + page_size], progress=progress, label="loading")
        chosen = _pick(pool, now_ms, page_size=page_size, start_page=start_page)
        if chosen is None:
            return 1
        if args.pick:
            json.dump(chosen, sys.stdout, ensure_ascii=False)
            sys.stdout.write("\n")
            return 0
        cwd = chosen.get("cwd") or ""
        resume = chosen["resume"]
        if cwd:
            sys.stdout.write(f"cd {shlex.quote(cwd)} && {resume}\n")
        else:
            sys.stdout.write(f"{resume}\n")
        return 0
    sys.stderr.write(
        "Not a TTY. Pass --json (agents) or --list, or run `threads` in a terminal.\n"
    )
    hydrate_rows(rows, progress=progress, label="loading")
    public = [_public_row(row) for row in rows]
    sys.stdout.write(_format_table(public, now_ms, start_index=meta["offset"] + 1) + "\n")
    footer = _format_page_footer(meta, len(public))
    if footer:
        sys.stdout.write(footer + "\n")
    return 1


if __name__ == "__main__":
    sys.exit(main())
