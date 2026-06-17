# Copyright 2026 Mihail Yurasov <me@yurasov.me>
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
