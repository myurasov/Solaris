# Copyright 2026 Mikhail Yurasov <me@yurasov.me>
# SPDX-License-Identifier: Apache-2.0

"""Tests for solaris.tools.read_first (the fail-safe read-first loader hook)."""

from __future__ import annotations

import io
import json

from solaris.tools import read_first as R


def test_detect_ide_prefers_claude_when_both_present():
    # Claude Code can run inside Cursor, so both var families appear; Claude must win (it wants plain text).
    assert R.detect_ide(env={"CLAUDECODE": "1", "CURSOR_TRACE_ID": "x"}) == "claude"
    # any CLAUDE-prefixed var is enough, even without CLAUDECODE
    assert R.detect_ide(env={"CLAUDE_CODE_EXECPATH": "/x", "CURSOR_LAYOUT": "y"}) == "claude"
    assert R.detect_ide(env={"CURSOR_TRACE_ID": "x"}) == "cursor"
    assert R.detect_ide(env={}) == "unknown"


def test_render_full_includes_header_and_all_files(tmp_path):
    for rel in R.READ_FIRST:
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("body of " + rel + "\n", encoding="utf-8")
    out = R.render_full(repo_root=tmp_path)
    assert "SOLARIS READ-FIRST" in out
    for rel in R.READ_FIRST:
        assert ("----- " + rel + " -----") in out
        assert ("body of " + rel) in out


def test_render_full_tolerates_missing_files(tmp_path):
    # no files exist under tmp_path -> each is noted, never raises
    out = R.render_full(repo_root=tmp_path)
    assert out.count("open it directly") == len(R.READ_FIRST)


def test_emit_is_json_for_cursor_plain_for_others():
    buf = io.StringIO()
    R.emit("hello", "cursor", stream=buf)
    assert json.loads(buf.getvalue()) == {"additional_context": "hello"}

    buf = io.StringIO()
    R.emit("hello", "claude", stream=buf)
    assert buf.getvalue() == "hello"

    buf = io.StringIO()
    R.emit("hello", "unknown", stream=buf)
    assert buf.getvalue() == "hello"


def test_main_remind_vs_full(capsys):
    assert R.main(["--remind"]) == 0
    out = capsys.readouterr().out
    assert "read-first" in out.lower()
    assert "SOLARIS READ-FIRST" not in out  # remind is the one-liner, not the full dump

    assert R.main([]) == 0
    assert "SOLARIS READ-FIRST" in capsys.readouterr().out


def test_render_full_respects_inline_budget():
    # Default (Claude-shaped) rendering fits the budget; all always-on rules arrive whole.
    out = R.render_full()
    assert len(out) <= R._budget()
    for rel in (
        "solaris/rules/commits.rule.md",
        "solaris/rules/safety.rule.md",
        "solaris/rules/interaction.rule.md",
    ):
        body = (R.REPO_ROOT / rel).read_text(encoding="utf-8")
        assert body in out, rel + " must be inlined whole"
    # Overflow degrades loudly, never silently.
    assert "TRUNCATED" in out or "POINTER" in out or len(out) < R._budget() // 2


def test_render_full_unbudgeted_is_complete():
    # Cursor path: a huge budget yields every file whole, no truncation markers in delimiters.
    out = R.render_full(budget=1_000_000)
    for rel in R.READ_FIRST:
        assert "\n----- " + rel + " -----\n" in out


def test_check_reports_budget(capsys):
    assert R.main(["--check"]) == 0
    out = capsys.readouterr().out
    assert "rendered payload" in out and ("OK (inline)" in out or "OVER BUDGET" in out)

def test_render_part2_inlines_both_rules_whole():
    # Part 2 (second SessionStart hook call) carries the subagents + YAGNI rules, whole and in budget.
    out = R.render_full(part=2)
    assert len(out) <= R._budget()
    assert "READ-FIRST, PART 2" in out
    for rel in R.READ_FIRST_2:
        body = (R.REPO_ROOT / rel).read_text(encoding="utf-8")
        assert body in out, rel + " must be inlined whole"
    # part 2 never re-lists part-1 files
    for rel in R.READ_FIRST:
        assert ("----- " + rel + " -----") not in out


def test_main_part2(capsys):
    assert R.main(["--part", "2"]) == 0
    out = capsys.readouterr().out
    assert "READ-FIRST, PART 2" in out and "PART 2" in out


def test_render_part3_inlines_economy_rule_whole():
    # Part 3 (third SessionStart hook call) carries the token-economy rule, whole and in budget.
    out = R.render_full(part=3)
    assert len(out) <= R._budget()
    assert "READ-FIRST, PART 3" in out
    for rel in R.READ_FIRST_3:
        body = (R.REPO_ROOT / rel).read_text(encoding="utf-8")
        assert body in out, rel + " must be inlined whole"
    # part 3 never re-lists earlier parts' files
    for rel in R.READ_FIRST + R.READ_FIRST_2:
        assert ("----- " + rel + " -----") not in out


def test_main_part3(capsys):
    assert R.main(["--part", "3"]) == 0
    assert "READ-FIRST, PART 3" in capsys.readouterr().out


def test_check_covers_all_parts(capsys):
    assert R.main(["--check"]) == 0
    out = capsys.readouterr().out
    for n in (1, 2, 3):
        assert ("part %d rendered payload" % n) in out
    assert "OVER BUDGET" not in out
