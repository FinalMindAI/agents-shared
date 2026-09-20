#!/usr/bin/env python3
"""Tests for thread listing and resume lookup."""

from __future__ import annotations

import json
import os
import sqlite3
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import threads as th  # noqa: E402


CLAUDE_ID = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
CLAUDE_ID_2 = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaab"
CODEX_ID = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
GROK_ID = "cccccccc-cccc-cccc-cccc-cccccccccccc"


def _write_claude(root: Path, session_id: str, title: str, cwd: str, mtime: int) -> None:
    proj = root / "projects" / "-Users-demo"
    proj.mkdir(parents=True, exist_ok=True)
    path = proj / f"{session_id}.jsonl"
    records = [
        {"type": "user", "cwd": cwd, "message": {"content": "hello"}},
        {"type": "custom-title", "customTitle": title},
    ]
    path.write_text("\n".join(json.dumps(r) for r in records) + "\n", encoding="utf-8")
    os.utime(path, (mtime, mtime))


def _write_codex(root: Path, session_id: str, name: str, cwd: str, updated_ms: int) -> None:
    root.mkdir(parents=True, exist_ok=True)
    db_path = root / "state_2.sqlite"
    conn = sqlite3.connect(db_path)
    conn.execute(
        "CREATE TABLE IF NOT EXISTS threads ("
        "id TEXT, cwd TEXT, archived INTEGER, source TEXT, "
        "updated_at_ms INTEGER, title TEXT, name TEXT, first_user_message TEXT)"
    )
    conn.execute(
        "INSERT INTO threads VALUES (?, ?, 0, 'cli', ?, '', ?, '')",
        (session_id, cwd, updated_ms, name),
    )
    conn.commit()
    conn.close()


def _write_grok(root: Path, session_id: str, title: str, cwd: str) -> None:
    path = root / "sessions" / "hash" / session_id / "summary.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "generated_title": title,
                "last_active_at": "2026-08-29T15:00:00+00:00",
                "info": {"id": session_id, "cwd": cwd},
            }
        ),
        encoding="utf-8",
    )


class FindThreadTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.claude = self.root / "claude"
        self.codex = self.root / "codex"
        self.grok = self.root / "grok"
        _write_claude(self.claude, CLAUDE_ID, "ship-pr", "/tmp/demo", 1_700_000_000)
        _write_claude(self.claude, CLAUDE_ID_2, "other-work", "/tmp/other", 1_700_000_100)
        _write_codex(self.codex, CODEX_ID, "fix-auth", "/tmp/auth", 1_700_000_200_000)
        _write_grok(self.grok, GROK_ID, "design-review", "/tmp/design")
        self.env = mock.patch.dict(
            "os.environ",
            {
                "CLAUDE_CONFIG_DIR": str(self.claude),
                "CODEX_HOME": str(self.codex),
                "GROK_HOME": str(self.grok),
            },
        )
        self.env.start()

    def tearDown(self) -> None:
        self.env.stop()
        self.tmp.cleanup()

    def _rows(self) -> list[dict]:
        return th.collect(None, None, None, limit=10_000)

    def test_find_by_full_id(self) -> None:
        row = th.find_thread(CODEX_ID, self._rows())
        self.assertEqual(row["agent"], "codex")
        self.assertEqual(row["id"], CODEX_ID)
        self.assertEqual(row["resume_argv"][0], "cdx")

    def test_find_by_id_prefix(self) -> None:
        row = th.find_thread("cccc", self._rows())
        self.assertEqual(row["id"], GROK_ID)
        self.assertEqual(row["resume_argv"][0], "grok")

    def test_find_by_alias_title(self) -> None:
        row = th.find_thread("ship-pr", self._rows())
        self.assertEqual(row["id"], CLAUDE_ID)
        self.assertEqual(row["resume_argv"], ["cc", "--resume", CLAUDE_ID])

    def test_find_by_alias_is_case_insensitive(self) -> None:
        row = th.find_thread("FIX-AUTH", self._rows())
        self.assertEqual(row["id"], CODEX_ID)

    def test_find_by_unique_title_prefix(self) -> None:
        row = th.find_thread("design", self._rows())
        self.assertEqual(row["id"], GROK_ID)

    def test_ambiguous_id_prefix_raises(self) -> None:
        with self.assertRaises(th.AmbiguousThreadError) as ctx:
            th.find_thread("aaaa", self._rows())
        self.assertEqual(len(ctx.exception.matches), 2)

    def test_missing_raises(self) -> None:
        with self.assertRaises(th.MissingThreadError):
            th.find_thread("no-such-thread", self._rows())

    def test_cli_find_prints_single_json_object(self) -> None:
        from io import StringIO

        buf = StringIO()
        with mock.patch("sys.stdout", buf):
            code = th.main(["--find", "fix-auth"])
        self.assertEqual(code, 0)
        payload = json.loads(buf.getvalue())
        self.assertIsInstance(payload, dict)
        self.assertEqual(payload["id"], CODEX_ID)
        self.assertEqual(payload["agent"], "codex")

    def test_cli_find_respects_agent_alias(self) -> None:
        from io import StringIO

        buf = StringIO()
        with mock.patch("sys.stdout", buf):
            code = th.main(["--agent", "cdx", "--find", "bbbb"])
        self.assertEqual(code, 0)
        payload = json.loads(buf.getvalue())
        self.assertEqual(payload["id"], CODEX_ID)

        err = StringIO()
        with mock.patch("sys.stdout", StringIO()), mock.patch("sys.stderr", err):
            code = th.main(["--agent", "cc", "--find", "fix-auth"])
        self.assertEqual(code, 1)
        self.assertIn("No thread matching", err.getvalue())


class PaginateTest(unittest.TestCase):
    def test_page_slices_and_reports_has_more(self) -> None:
        rows = [{"id": str(i)} for i in range(5)]
        page1, meta1 = th.paginate(rows, page_size=2, page=1)
        self.assertEqual([r["id"] for r in page1], ["0", "1"])
        self.assertEqual(meta1["total"], 5)
        self.assertEqual(meta1["pages"], 3)
        self.assertTrue(meta1["has_more"])

        page2, meta2 = th.paginate(rows, page_size=2, page=2)
        self.assertEqual([r["id"] for r in page2], ["2", "3"])
        self.assertTrue(meta2["has_more"])

        page3, meta3 = th.paginate(rows, page_size=2, page=3)
        self.assertEqual([r["id"] for r in page3], ["4"])
        self.assertFalse(meta3["has_more"])
        self.assertEqual(meta3["page"], 3)

    def test_offset_matches_page(self) -> None:
        rows = [{"id": str(i)} for i in range(5)]
        by_page, _ = th.paginate(rows, page_size=2, page=2)
        by_offset, meta = th.paginate(rows, page_size=2, offset=2)
        self.assertEqual(by_page, by_offset)
        self.assertEqual(meta["offset"], 2)

    def test_empty_past_last_page(self) -> None:
        rows = [{"id": "a"}]
        page, meta = th.paginate(rows, page_size=2, page=9)
        self.assertEqual(page, [])
        self.assertEqual(meta["total"], 1)
        self.assertFalse(meta["has_more"])

    def test_rejects_bad_page_and_offset(self) -> None:
        rows = [{"id": "a"}]
        with self.assertRaises(SystemExit):
            th.paginate(rows, page_size=2, page=0)
        with self.assertRaises(SystemExit):
            th.paginate(rows, page_size=0, page=1)
        with self.assertRaises(SystemExit):
            th.paginate(rows, page_size=2, offset=-1)

    def test_move_pick_crosses_pages(self) -> None:
        self.assertEqual(
            th._move_pick("down", idx=1, page=0, page_len=2, pages=3, page_size=2),
            (0, 1),
        )
        self.assertEqual(
            th._move_pick("up", idx=0, page=1, page_len=2, pages=3, page_size=2),
            (1, 0),
        )
        self.assertEqual(
            th._move_pick("n", idx=1, page=0, page_len=2, pages=3, page_size=2),
            (0, 1),
        )
        self.assertEqual(
            th._move_pick("n", idx=0, page=2, page_len=1, pages=3, page_size=2),
            (0, 2),
        )
        self.assertEqual(
            th._move_pick("p", idx=0, page=0, page_len=2, pages=3, page_size=2),
            (0, 0),
        )
        self.assertEqual(
            th._move_pick("end", idx=0, page=0, page_len=2, pages=3, page_size=2),
            (1, 2),
        )
        self.assertIsNone(
            th._move_pick("x", idx=0, page=0, page_len=2, pages=3, page_size=2)
        )


class SearchFilterTest(unittest.TestCase):
    def setUp(self) -> None:
        self.rows = [
            {
                "agent": "claude",
                "title": "ship-pr",
                "cwd": "/tmp/demo",
                "id": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
            },
            {
                "agent": "codex",
                "title": "fix-auth",
                "cwd": "/tmp/auth",
                "id": "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb",
            },
            {
                "agent": "grok",
                "title": "design-review",
                "cwd": "/tmp/design",
                "id": "cccccccc-cccc-cccc-cccc-cccccccccccc",
            },
        ]

    def test_empty_query_is_identity(self) -> None:
        self.assertEqual(th.filter_rows(self.rows, ""), self.rows)
        self.assertEqual(th.filter_rows(self.rows, "   "), self.rows)

    def test_title_and_dir_and_agent(self) -> None:
        self.assertEqual([r["id"][:4] for r in th.filter_rows(self.rows, "ship")], ["aaaa"])
        self.assertEqual([r["id"][:4] for r in th.filter_rows(self.rows, "cc demo")], ["aaaa"])
        self.assertEqual([r["id"][:4] for r in th.filter_rows(self.rows, "cdx")], ["bbbb"])
        self.assertEqual([r["id"][:4] for r in th.filter_rows(self.rows, "grok design")], ["cccc"])

    def test_tokens_are_anded(self) -> None:
        self.assertEqual(th.filter_rows(self.rows, "ship auth"), [])
        self.assertEqual([r["id"][:4] for r in th.filter_rows(self.rows, "fix auth")], ["bbbb"])

    def test_id_prefix(self) -> None:
        self.assertEqual([r["id"][:4] for r in th.filter_rows(self.rows, "cccc")], ["cccc"])

    def test_printable_char(self) -> None:
        self.assertEqual(th._printable_char("s"), "s")
        self.assertEqual(th._printable_char("/"), "/")
        self.assertIsNone(th._printable_char("up"))
        self.assertIsNone(th._printable_char("esc"))
        self.assertIsNone(th._printable_char("\x03"))


class ClipboardTest(unittest.TestCase):
    def test_uses_first_available_command(self) -> None:
        with mock.patch.object(
            th.shutil, "which", side_effect=lambda name: name == "pbcopy"
        ), mock.patch.object(th.subprocess, "run") as run:
            self.assertTrue(th._copy_to_clipboard(CODEX_ID))
        run.assert_called_once_with(
            ("pbcopy",),
            input=CODEX_ID,
            text=True,
            stdout=th.subprocess.DEVNULL,
            stderr=th.subprocess.DEVNULL,
            check=True,
        )

    def test_reports_no_supported_command(self) -> None:
        with mock.patch.object(th.shutil, "which", return_value=None):
            self.assertFalse(th._copy_to_clipboard(CODEX_ID))


class CliPageTest(FindThreadTest):
    def test_json_page_two(self) -> None:
        from io import StringIO

        page1 = StringIO()
        with mock.patch("sys.stdout", page1), mock.patch("sys.stderr", StringIO()):
            code = th.main(["--json", "--limit", "2", "--page", "1"])
        self.assertEqual(code, 0)
        first = json.loads(page1.getvalue())
        self.assertEqual(len(first), 2)

        page2 = StringIO()
        with mock.patch("sys.stdout", page2), mock.patch("sys.stderr", StringIO()):
            code = th.main(["--json", "--limit", "2", "--page", "2"])
        self.assertEqual(code, 0)
        second = json.loads(page2.getvalue())
        self.assertEqual(len(second), 2)
        self.assertNotEqual([r["id"] for r in first], [r["id"] for r in second])

        offset = StringIO()
        with mock.patch("sys.stdout", offset), mock.patch("sys.stderr", StringIO()):
            code = th.main(["--json", "--limit", "2", "--offset", "2"])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(offset.getvalue()), second)

    def test_list_footer_and_global_index(self) -> None:
        from io import StringIO

        buf = StringIO()
        with mock.patch("sys.stdout", buf), mock.patch("sys.stderr", StringIO()):
            code = th.main(["--list", "--limit", "2", "--page", "2"])
        self.assertEqual(code, 0)
        text = buf.getvalue()
        self.assertIn("page 2/2", text)
        self.assertRegex(text, r"(?m)^ 3  ")

    def test_json_stderr_footer_when_more_pages(self) -> None:
        from io import StringIO

        err = StringIO()
        with mock.patch("sys.stdout", StringIO()), mock.patch("sys.stderr", err):
            code = th.main(["--json", "--limit", "2", "--page", "1"])
        self.assertEqual(code, 0)
        self.assertIn("page 1/2", err.getvalue())
        self.assertIn("threads --json --page 2", err.getvalue())

    def test_empty_page_is_an_error(self) -> None:
        from io import StringIO

        err = StringIO()
        with mock.patch("sys.stdout", StringIO()), mock.patch("sys.stderr", err):
            code = th.main(["--list", "--limit", "2", "--page", "9"])
        self.assertEqual(code, 1)
        self.assertIn("Page 9 is empty", err.getvalue())

    def test_page_and_offset_together_fail(self) -> None:
        with self.assertRaises(SystemExit):
            th.main(["--json", "--page", "2", "--offset", "2"])

    def test_json_strips_private_keys_and_hydrates_titles(self) -> None:
        from io import StringIO

        buf = StringIO()
        with mock.patch("sys.stdout", buf), mock.patch("sys.stderr", StringIO()):
            code = th.main(["--json", "--limit", "10"])
        self.assertEqual(code, 0)
        payload = json.loads(buf.getvalue())
        self.assertGreaterEqual(len(payload), 1)
        for row in payload:
            self.assertNotIn("_path", row)
            self.assertNotIn("_hydrated", row)
        titles = {row["title"] for row in payload}
        self.assertIn("ship-pr", titles)
        self.assertIn("fix-auth", titles)

    def test_hydrate_is_idempotent_and_fills_claude_title(self) -> None:
        rows = th.index_threads(None, None, None, limit=10_000)
        claude = next(row for row in rows if row["agent"] == "claude" and row["id"] == CLAUDE_ID)
        self.assertTrue(th._needs_hydrate(claude))
        th.hydrate_row(claude)
        self.assertFalse(th._needs_hydrate(claude))
        self.assertEqual(claude["title"], "ship-pr")
        th.hydrate_row(claude)
        self.assertEqual(claude["title"], "ship-pr")


class ProgressTest(unittest.TestCase):
    def test_bar_fill(self) -> None:
        self.assertEqual(th._progress_bar(0, 10), "░░░░░░░░░░")
        self.assertEqual(th._progress_bar(100, 10), "██████████")
        self.assertEqual(th._progress_bar(50, 10), "█████░░░░░")

    def test_format_progress(self) -> None:
        text = th.format_progress(8, 20, "loading")
        self.assertIn("loading", text)
        self.assertIn("40%", text)
        self.assertIn("8/20", text)

    def test_hidden_until_delay(self) -> None:
        from io import StringIO

        buf = StringIO()
        progress = th.Progress(buf, enabled=True)
        progress.started = time.monotonic()
        progress.update(1, 10, "scanning")
        self.assertEqual(buf.getvalue(), "")
        progress.started = time.monotonic() - 1
        progress.update(2, 10, "scanning")
        self.assertIn("scanning", buf.getvalue())
        self.assertIn("20%", buf.getvalue())
        progress.finish()
        self.assertTrue(buf.getvalue().endswith("\r\x1b[2K"))

    def test_hydrate_next_skips_ready_rows(self) -> None:
        rows = [
            {"_hydrated": True},
            {"agent": "codex", "_hydrated": False, "title": "x"},
            {"_hydrated": False, "agent": "codex", "title": "y"},
        ]
        nxt = th._hydrate_next(rows, 0, 3)
        self.assertEqual(nxt, 2)
        self.assertFalse(th._needs_hydrate(rows[1]))


if __name__ == "__main__":
    unittest.main()
