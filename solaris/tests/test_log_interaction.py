# Copyright 2026 Mikhail Yurasov <me@yurasov.me>
# SPDX-License-Identifier: Apache-2.0

"""Tests for solaris.tools.log_interaction (the fail-safe prompt-submit hook)."""

from __future__ import annotations

import io
import json

from solaris.tools import log_interaction as L


def test_read_payload_tolerates_garbage():
    assert L.read_payload(io.StringIO("")) == {}
    assert L.read_payload(io.StringIO("   ")) == {}
    assert L.read_payload(io.StringIO("not json")) == {}
    assert L.read_payload(io.StringIO('{"prompt": "hi"}')) == {"prompt": "hi"}


def test_build_entry_truncates_and_detects_ide():
    entry = L.build_entry({"prompt": "x" * 500, "cwd": "/tmp/foo"}, env={"CLAUDECODE": "1"})
    assert entry["prompt"].endswith("...") and len(entry["prompt"]) == 283
    assert entry["cwd"] == "/tmp/foo"
    assert entry["ide"] == "claude"
    assert "ts" in entry
    assert L.build_entry({}, env={"CURSOR_TRACE_ID": "x"})["ide"] == "cursor"
    assert L.build_entry({}, env={})["ide"] == "unknown"


def test_migrate_legacy_memory_renames_once(tmp_path):
    from solaris.tools import read_first as R

    (tmp_path / "memory").mkdir()
    (tmp_path / "memory" / "instructions.md").write_text("x", encoding="utf-8")
    R.migrate_legacy_memory(tmp_path)
    assert not (tmp_path / "memory").exists()
    assert (tmp_path / ".memory" / "instructions.md").read_text(encoding="utf-8") == "x"
    # idempotent, and never clobbers an existing .memory/
    (tmp_path / "memory").mkdir()
    R.migrate_legacy_memory(tmp_path)
    assert (tmp_path / "memory").exists()  # left alone: .memory/ already present


def test_log_path_is_always_framework(tmp_path, monkeypatch):
    monkeypatch.setenv("SOLARIS_MACHINE", "box1")
    (tmp_path / ".memory").mkdir()
    mine = tmp_path / ".memory" / "interactions" / "box1.jsonl"
    # the hook always targets this machine's file in the framework master log (cwd is irrelevant -
    # "hand off" never changes it)
    assert L.log_path(repo_root=tmp_path) == mine
    # even with projects present, whatever their pack folder is named (the default, a custom name, the
    # legacy ai/), it still routes to the framework master (the agent writes the project logs)
    for slug, pack in (("todo", "aipack"), ("lab", "brain"), ("old", "ai")):
        d = tmp_path / "projects" / "my" / slug / pack
        (d / ".memory").mkdir(parents=True)
        (d / "manifest.json").write_text(
            json.dumps({"project": {"name": slug}, "framework_version": "0.39.0"}), encoding="utf-8")
    assert L.log_path(repo_root=tmp_path) == mine


def test_cli_refusal_points_at_the_logging_command(capsys):
    assert L.main(["--anything"]) == 2
    err = capsys.readouterr().err
    assert "solaris.tools.interactions add" in err and "<pack>/.memory/interactions/" in err
    assert "ai/.memory" not in err


def test_main_logs_and_sweeps_unless_claude_does(tmp_path, monkeypatch):
    # Under Claude Code the read_first --remind hook sweeps (it can show the note); elsewhere this hook does.
    import os
    import sys

    from solaris.tools import read_first as R

    log = tmp_path / ".memory" / "interactions" / "box1.jsonl"
    monkeypatch.setattr(L, "log_path", lambda repo_root=None: log)
    calls = []
    monkeypatch.setattr(R, "heal_sync_conflicts", lambda *a, **k: calls.append(1) or "")
    for var, swept in (("CURSOR_TRACE_ID", True), ("CLAUDECODE", False)):
        for k in list(os.environ):
            if k.startswith(("CLAUDE", "CURSOR")):
                monkeypatch.delenv(k)
        monkeypatch.setenv(var, "1")
        monkeypatch.setattr(sys, "stdin", io.StringIO('{"prompt": "hi"}'))
        calls.clear()
        assert L.main([]) == 0
        assert calls == ([1] if swept else [])
    assert [json.loads(line)["prompt"] for line in log.read_text(encoding="utf-8").splitlines()] == ["hi", "hi"]


def test_append_creates_parent_and_writes_jsonl(tmp_path):
    log = tmp_path / ".memory" / "interactions.jsonl"
    L.append(log, {"ts": "t", "prompt": "a"})
    L.append(log, {"ts": "t", "prompt": "b"})
    lines = log.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    assert json.loads(lines[0])["prompt"] == "a"
    assert json.loads(lines[1])["prompt"] == "b"
