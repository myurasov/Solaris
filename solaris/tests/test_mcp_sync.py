"""Tests for solaris.tools.mcp_sync."""

from __future__ import annotations

import json

from solaris.tools import mcp_sync as M


def _write(path, servers, comment=False):
    path.parent.mkdir(parents=True, exist_ok=True)
    doc = {"mcpServers": servers}
    if comment:
        doc = {"_comment": "do not edit", "mcpServers": servers}
    path.write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")


PW = {"playwright": {"command": "npx", "args": ["@playwright/mcp@latest"]}}


def test_in_sync_ignores_comment_and_formatting(tmp_path, capsys):
    claude, cursor, _ = M._paths(tmp_path)
    _write(claude, PW, comment=True)
    _write(cursor, PW, comment=False)
    assert M.cmd_check(tmp_path) == 0
    assert "in sync" in capsys.readouterr().out


def test_drift_detected(tmp_path, capsys):
    claude, cursor, _ = M._paths(tmp_path)
    _write(claude, PW)
    _write(cursor, {**PW, "nvbugs": {"type": "http", "url": "https://example/mcp"}})
    assert M.cmd_check(tmp_path) == 1
    out = capsys.readouterr().out
    assert "DRIFT" in out and "nvbugs" in out


def test_missing_files(tmp_path, capsys):
    assert M.cmd_check(tmp_path) == 1                       # neither exists
    assert "neither" in capsys.readouterr().out
    claude, _, _ = M._paths(tmp_path)
    _write(claude, PW)
    assert M.cmd_check(tmp_path) == 1                       # cursor missing
    assert ".cursor/mcp.json is missing" in capsys.readouterr().out


def test_sync_writes_both(tmp_path, capsys):
    claude, cursor, example = M._paths(tmp_path)
    _write(example, PW, comment=True)
    assert M.cmd_sync(tmp_path, "example") == 0
    assert M.servers(json.loads(claude.read_text())) == PW
    assert M.servers(json.loads(cursor.read_text())) == PW
    # runtime files drop the _comment
    assert "_comment" not in json.loads(claude.read_text())
    assert M.cmd_check(tmp_path) == 0


def test_main_default_is_check(tmp_path):
    claude, cursor, _ = M._paths(tmp_path)
    _write(claude, PW)
    _write(cursor, PW)
    assert M.main(["--dir", str(tmp_path)]) == 0
