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


def test_route_framework_vs_project(tmp_path):
    (tmp_path / "memory").mkdir()
    # cwd outside projects -> framework log
    assert L.route(tmp_path, repo_root=tmp_path) == tmp_path / "memory" / "interactions.jsonl"

    proj_ai = tmp_path / "projects" / "todo" / "ai"
    proj_ai.mkdir(parents=True)
    cwd = tmp_path / "projects" / "todo" / "src"
    cwd.mkdir(parents=True)
    assert L.route(cwd, repo_root=tmp_path) == tmp_path / "projects" / "todo" / "ai" / "memory" / "interactions.jsonl"

    # a projects/<slug> without an ai/ falls back to the framework log
    bare = tmp_path / "projects" / "bare"
    bare.mkdir(parents=True)
    assert L.route(bare, repo_root=tmp_path) == tmp_path / "memory" / "interactions.jsonl"


def test_append_creates_parent_and_writes_jsonl(tmp_path):
    log = tmp_path / "memory" / "interactions.jsonl"
    L.append(log, {"ts": "t", "prompt": "a"})
    L.append(log, {"ts": "t", "prompt": "b"})
    lines = log.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    assert json.loads(lines[0])["prompt"] == "a"
    assert json.loads(lines[1])["prompt"] == "b"
