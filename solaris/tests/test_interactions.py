# Copyright 2026 Mikhail Yurasov <me@yurasov.me>
# SPDX-License-Identifier: Apache-2.0

"""Tests for solaris.tools.interactions (per-machine interaction logs)."""

from __future__ import annotations

import io
import json
import sys
from datetime import datetime, timezone

import pytest

from solaris.tools import interactions as I

NOW = datetime(2026, 10, 2, 12, 0, 0, tzinfo=timezone.utc).timestamp()


@pytest.fixture
def repo(tmp_path, monkeypatch):
    """A Solaris root with a framework .memory and one project; this machine is box1, the clock is NOW."""
    monkeypatch.setattr(I, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(I, "_now", lambda: NOW)
    monkeypatch.setenv("SOLARIS_MACHINE", "box1")
    (tmp_path / ".memory").mkdir()
    pack = tmp_path / "projects" / "my" / "demo" / "aipack"
    (pack / ".memory").mkdir(parents=True)
    (pack / "manifest.json").write_text(
        json.dumps({"project": {"name": "demo"}, "framework_version": "0.40.0"}), encoding="utf-8")
    return tmp_path


def _lines(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def test_machine_name_override_host_name_and_cleanup(monkeypatch):
    assert I.machine_name({"SOLARIS_MACHINE": "My Box.local"}) == "my-box-local"
    monkeypatch.setattr(I.sys, "platform", "linux")
    monkeypatch.setattr(I.socket, "gethostname", lambda: "Build-Box2.lan")
    assert I.machine_name({}) == "build-box2"
    monkeypatch.setattr(I.socket, "gethostname", lambda: "...")
    assert I.machine_name({}) == "unknown"


def test_add_writes_one_line_to_this_machines_framework_and_project_files(repo, capsys):
    project = repo / "projects" / "my" / "demo"
    argv = ["add", "--project", "demo", "--prompt", "hi", "--request", "r", "--outcome", "o", "--dir", str(project)]
    assert I.main(argv) == 0
    framework = repo / ".memory" / "interactions" / "box1.jsonl"
    pack_log = project / "aipack" / ".memory" / "interactions" / "box1.jsonl"
    want = {"ts": "2026-10-02T12:00:00Z", "project": "demo", "prompt": "hi", "request": "r", "outcome": "o"}
    assert _lines(framework) == [want] and _lines(pack_log) == [want]
    out = capsys.readouterr().out
    assert out.startswith("logged 2026-10-02T12:00:00Z (box1) -> ") and ".memory/interactions/box1.jsonl" in out


def test_add_reads_stdin_json_and_flags_win(repo, monkeypatch):
    entry = {"project": "solaris", "prompt": "it's \"quoted\"", "request": "r", "outcome": "o", "ts": "ignored"}
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(entry)))
    assert I.main(["add", "--stdin", "--outcome", "flag wins"]) == 0
    [line] = _lines(repo / ".memory" / "interactions" / "box1.jsonl")
    assert line == {"ts": "2026-10-02T12:00:00Z", "project": "solaris", "prompt": "it's \"quoted\"",
                    "request": "r", "outcome": "flag wins"}


def test_add_refuses_bad_input_without_writing(repo, monkeypatch, capsys):
    assert I.main(["add", "--project", "x"]) == 2
    assert I.main(["add", "--project", "x", "--prompt", "", "--request", " ", "--outcome", "o"]) == 2
    monkeypatch.setattr(sys, "stdin", io.StringIO("[1, 2]"))
    assert I.main(["add", "--stdin"]) == 2
    # A folder without an ai-pack fails before anything is written, so the two logs never disagree.
    argv = ["add", "--project", "x", "--prompt", "p", "--request", "r", "--outcome", "o", "--dir", str(repo)]
    assert I.main(argv) == 1
    assert not (repo / ".memory" / "interactions").exists()


def test_append_line_starts_a_fresh_line_after_a_cut_one(tmp_path):
    log = tmp_path / "interactions" / "box1.jsonl"
    log.parent.mkdir()
    log.write_bytes(b'{"ts": "a"}\n{"ts": "cut')
    I.append_line(log, {"ts": "b"})
    assert log.read_bytes() == b'{"ts": "a"}\n{"ts": "cut\n{"ts": "b"}\n'


def test_read_entries_merges_history_and_machines_by_time(repo):
    mem = repo / ".memory"
    logs = mem / "interactions"
    logs.mkdir()
    (mem / "interactions.jsonl").write_text(
        '{"ts": "2026-10-01T10:00:00+00:00", "prompt": "history"}\nnot json\n', encoding="utf-8")
    (logs / "box1.jsonl").write_text(
        '{"ts": "2026-10-01T09:00:00Z", "request": "first"}\n{"ts": "2026-10-01T11:00:00Z", "request": "third"}\n',
        encoding="utf-8")
    (logs / "box2.jsonl").write_text('{"ts": "2026-10-01T12:00:00Z", "request": "fourth"}\n', encoding="utf-8")
    (logs / "box2.sync-conflict-20261002-101500-AAAAAAA.jsonl").write_text(
        '{"ts": "2026-10-01T13:00:00Z", "request": "copy"}\n', encoding="utf-8")
    entries, bad = I.read_entries(mem)
    assert [(e["machine"], e.get("request") or e.get("prompt")) for e in entries] == [
        ("box1", "first"), ("-", "history"), ("box1", "third"), ("box2", "fourth")]
    assert bad == 1


def test_show_filters_and_prints_json(repo, capsys):
    logs = repo / ".memory" / "interactions"
    logs.mkdir()
    (logs / "box1.jsonl").write_text(
        '{"ts": "2026-10-01T09:00:00Z", "project": "a", "request": "one"}\n'
        '{"ts": "2026-10-01T11:00:00Z", "project": "b", "request": "two"}\n', encoding="utf-8")
    (logs / "box2.jsonl").write_text('{"ts": "2026-10-01T12:00:00Z", "project": "a", "request": "three"}\n',
                                     encoding="utf-8")
    assert I.main(["show", "--last", "2", "--json"]) == 0
    assert [json.loads(line)["request"] for line in capsys.readouterr().out.splitlines()] == ["two", "three"]
    assert I.main(["show", "--project", "a", "--since", "2026-10-01T10:00:00Z"]) == 0
    out = capsys.readouterr().out.splitlines()
    assert len(out) == 1 and "box2" in out[0] and "[a] three" in out[0]
    assert I.main(["show", "--since", "yesterday"]) == 2


def test_who_flags_another_machine_active_within_the_window(repo, capsys):
    logs = repo / "projects" / "my" / "demo" / "aipack" / ".memory" / "interactions"
    logs.mkdir()
    (logs / "box1.jsonl").write_text('{"ts": "2026-10-02T11:58:00Z", "project": "demo"}\n', encoding="utf-8")
    (logs / "box2.jsonl").write_text('{"ts": "2026-10-02T11:50:00Z", "project": "demo"}\n', encoding="utf-8")
    project = str(repo / "projects" / "my" / "demo")
    assert I.main(["who", "--dir", project]) == 3
    out = capsys.readouterr().out
    assert "box1 (this machine)" in out and "box2 logged 10 min ago: this project may be in use there" in out
    assert I.main(["who", "--dir", project, "--minutes", "5"]) == 0
    assert "may be in use" not in capsys.readouterr().out


def test_who_counts_a_recent_history_write_as_unknown_activity(repo, capsys):
    project = repo / "projects" / "my" / "demo"
    history = project / "aipack" / ".memory" / "interactions.jsonl"
    history.write_text('{"ts": "2026-10-02T11:55:00Z", "project": "demo"}\n', encoding="utf-8")
    assert I.main(["who", "--dir", str(project)]) == 3
    assert "history file written 5 min ago: a session on older instructions" in capsys.readouterr().out
    history.write_text('{"ts": "2026-10-01T11:55:00Z", "project": "demo"}\n', encoding="utf-8")
    assert I.main(["who", "--dir", str(project)]) == 0


def test_last_entry_prefers_the_newest_ts_over_a_merged_older_line(tmp_path):
    log = tmp_path / "box2.jsonl"
    log.write_text('{"ts": "2026-10-02T11:57:00Z", "i": 1}\n{"ts": "2026-09-30T08:00:00Z", "i": 2}\n',
                   encoding="utf-8")
    assert I.last_entry(log) == {"ts": "2026-10-02T11:57:00Z", "i": 1}


def test_last_entry_reads_the_tail_and_skips_a_broken_last_line(tmp_path):
    log = tmp_path / "box1.jsonl"
    filler = "".join(json.dumps({"ts": "x", "i": i}) + "\n" for i in range(3000))  # well past one tail chunk
    log.write_text(filler + '{"ts": "last"}\n{"ts": "bro', encoding="utf-8")
    assert I.last_entry(log) == {"ts": "last"}
    log.write_text('{"ts": "only"}\n', encoding="utf-8")
    assert I.last_entry(log) == {"ts": "only"}
    assert I.last_entry(tmp_path / "missing.jsonl") is None
