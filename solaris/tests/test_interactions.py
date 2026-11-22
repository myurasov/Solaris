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
    monkeypatch.delenv("OPENCODE_SESSION_ID", raising=False)  # no self-compaction check against a real server
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


def _stdin(monkeypatch, **fields):
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(fields)))


def test_add_records_an_optional_trigger_that_show_json_prints(repo, monkeypatch, capsys):
    base = ["add", "--project", "demo", "--prompt", "p", "--request", "r", "--outcome", "o"]
    assert I.main(base + ["--trigger", "clock"]) == 0
    assert I.main(base) == 0  # no trigger given: no key, the line is the plain five fields
    _stdin(monkeypatch, project="demo", trigger=" Peer ", prompt="p", request="r", outcome="o")
    assert I.main(["add", "--stdin"]) == 0  # --stdin carries it too, folded to lower case
    _stdin(monkeypatch, project="demo", trigger="peer", prompt="p", request="r", outcome="o")
    assert I.main(["add", "--stdin", "--trigger", "worker"]) == 0  # the flag wins
    _stdin(monkeypatch, project="demo", trigger=None, prompt="p", request="r", outcome="o")
    assert I.main(["add", "--stdin", "--trigger", "OWNER"]) == 0
    lines = _lines(repo / ".memory" / "interactions" / "box1.jsonl")
    assert [line.get("trigger") for line in lines] == ["clock", None, "peer", "worker", "owner"]
    assert lines[1] == {"ts": "2026-10-02T12:00:00Z", "project": "demo", "prompt": "p", "request": "r",
                        "outcome": "o"}
    capsys.readouterr()
    assert I.main(["show", "--json", "--last", "0"]) == 0
    shown = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert [e.get("trigger") for e in shown] == ["clock", None, "peer", "worker", "owner"]


def test_add_refuses_an_unknown_trigger(repo, monkeypatch, capsys):
    base = ["add", "--project", "demo", "--prompt", "p", "--request", "r", "--outcome", "o"]
    assert I.main(base + ["--trigger", "cron"]) == 2
    err = capsys.readouterr().err
    assert err.count("\n") == 1 and "--trigger must be one of owner, clock, worker, peer" in err
    for bad in ("", "user", 3, ["owner"]):
        _stdin(monkeypatch, project="demo", trigger=bad, prompt="p", request="r", outcome="o")
        assert I.main(["add", "--stdin"]) == 2, bad
    assert not (repo / ".memory" / "interactions").exists()


def test_add_refuses_an_empty_or_dash_project(repo, monkeypatch, capsys):
    for project in ("-", " - ", "", "   "):
        assert I.main(["add", "--project", project, "--prompt", "p", "--request", "r", "--outcome", "o"]) == 2
        err = capsys.readouterr().err
        assert err.count("\n") == 1 and "names nothing" in err and "solaris for framework work" in err, project
    _stdin(monkeypatch, project="-", prompt="p", request="r", outcome="o")
    assert I.main(["add", "--stdin", "--dir", str(repo / "projects" / "my" / "demo")]) == 2
    # "-" as a flag value never reaches a log, and neither does the stdin one
    assert not (repo / ".memory" / "interactions").exists()
    assert not (repo / "projects" / "my" / "demo" / "aipack" / ".memory" / "interactions").exists()
    # a real name still logs
    assert I.main(["add", "--project", "solaris", "--prompt", "p", "--request", "r", "--outcome", "o"]) == 0


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


# --------------------------------------------------------------------------- a manager's lines (add --manager)

def test_add_manager_marks_the_line_in_both_logs(repo, monkeypatch, capsys):
    project = repo / "projects" / "my" / "demo"
    base = ["add", "--project", "demo", "--prompt", "p", "--request", "r", "--outcome", "o", "--dir", str(project)]
    assert I.main(base + ["--manager"]) == 0
    assert I.main(base) == 0  # no flag: no key, the line is as it always was
    _stdin(monkeypatch, project="demo", manager=True, prompt="p", request="r", outcome="o")
    assert I.main(["add", "--stdin", "--dir", str(project)]) == 0  # --stdin carries the mark too
    _stdin(monkeypatch, project="demo", manager=False, prompt="p", request="r", outcome="o")
    assert I.main(["add", "--stdin", "--dir", str(project), "--manager"]) == 0  # the flag wins
    _stdin(monkeypatch, project="demo", manager=False, trigger="peer", prompt="p", request="r", outcome="o")
    assert I.main(["add", "--stdin", "--dir", str(project)]) == 0
    framework = _lines(repo / ".memory" / "interactions" / "box1.jsonl")
    pack_log = _lines(project / "aipack" / ".memory" / "interactions" / "box1.jsonl")
    assert framework == pack_log  # the same line, mark included, in both logs
    assert [line.get("manager") for line in framework] == [True, None, True, True, None]
    assert framework[0] == {"ts": "2026-10-02T12:00:00Z", "project": "demo", "prompt": "p", "request": "r",
                            "outcome": "o", "manager": True}
    assert framework[4]["trigger"] == "peer" and "manager" not in framework[4]
    capsys.readouterr()
    assert I.main(["show", "--dir", str(project), "--last", "0"]) == 0
    shown = capsys.readouterr().out.splitlines()
    assert ["(manager)" in line for line in shown] == [True, False, True, True, False]
    assert "[demo] (manager) r" in shown[0] and "[demo] r" in shown[1]
    assert I.main(["show", "--dir", str(project), "--last", "1", "--json"]) == 0
    assert "manager" not in json.loads(capsys.readouterr().out)


def test_add_refuses_a_manager_mark_that_is_not_true_or_false(repo, monkeypatch, capsys):
    for bad in ("yes", 1, 0, "true", ["x"]):
        _stdin(monkeypatch, project="demo", manager=bad, prompt="p", request="r", outcome="o")
        assert I.main(["add", "--stdin"]) == 2, bad
        assert "manager must be true or false" in capsys.readouterr().err
    assert not (repo / ".memory" / "interactions").exists()


def _project_logs(repo, **files):
    logs = repo / "projects" / "my" / "demo" / "aipack" / ".memory" / "interactions"
    logs.mkdir(exist_ok=True)
    for machine, lines in files.items():
        (logs / f"{machine}.jsonl").write_text("".join(json.dumps(line) + "\n" for line in lines), encoding="utf-8")
    return str(repo / "projects" / "my" / "demo")


def test_who_does_not_count_a_managers_line_as_work_on_the_project(repo, capsys):
    # box2 is where a manager session runs: it only logs its own actions about the project
    project = _project_logs(
        repo, box1=[{"ts": "2026-10-02T11:58:00Z", "project": "demo"}],
        box2=[{"ts": "2026-10-02T08:00:00Z", "project": "demo"},
              {"ts": "2026-10-02T11:50:00Z", "project": "demo", "manager": True}])
    assert I.main(["who", "--dir", project]) == 0
    out = capsys.readouterr().out
    assert "may be in use" not in out
    line = next(row for row in out.splitlines() if row.startswith("box2"))
    assert "2026-10-02T11:50:00Z" in line and line.endswith("(manager)")   # still listed, labelled
    assert "(manager)" not in next(row for row in out.splitlines() if row.startswith("box1"))


def test_who_still_flags_work_logged_before_a_managers_line(repo, capsys):
    # real work on box2 ten minutes ago, then the manager there logged a line of its own
    project = _project_logs(
        repo, box1=[{"ts": "2026-10-02T11:58:00Z", "project": "demo"}],
        box2=[{"ts": "2026-10-02T11:50:00Z", "project": "demo"},
              {"ts": "2026-10-02T11:55:00Z", "project": "demo", "manager": True}])
    assert I.main(["who", "--dir", project]) == 3
    out = capsys.readouterr().out
    assert "box2 logged 10 min ago: this project may be in use there" in out   # the work line's age
    assert I.main(["who", "--dir", project, "--minutes", "8"]) == 0   # the work is outside the window
    capsys.readouterr()
    # a file that holds only a manager's lines never makes the project look in use
    project = _project_logs(repo, box2=[{"ts": "2026-10-02T11:59:00Z", "project": "demo", "manager": True}])
    assert I.main(["who", "--dir", project]) == 0


def test_who_treats_unmarked_and_oddly_marked_lines_as_work(repo, capsys):
    # old lines carry no mark; a mark that is not the JSON true is not one
    for mark in ({}, {"manager": False}, {"manager": "true"}, {"manager": 1}):
        project = _project_logs(repo, box2=[{"ts": "2026-10-02T11:50:00Z", "project": "demo", **mark}])
        assert I.main(["who", "--dir", project]) == 3, mark
        assert "(manager)" not in capsys.readouterr().out


def test_who_on_the_framework_log_counts_a_manager_on_another_machine(repo, capsys):
    logs = repo / ".memory" / "interactions"
    logs.mkdir()
    (logs / "box2.jsonl").write_text('{"ts": "2026-10-02T11:50:00Z", "project": "demo", "manager": true}\n',
                                     encoding="utf-8")
    assert I.main(["who"]) == 3   # that session does use this checkout
    out = capsys.readouterr().out
    assert "(manager)" in out and "box2 logged 10 min ago: this checkout may be in use there" in out


def test_last_entry_can_leave_out_a_managers_lines(tmp_path):
    log = tmp_path / "box2.jsonl"
    filler = "".join(json.dumps({"ts": "2026-10-02T10:00:00Z", "manager": True, "i": i}) + "\n" for i in range(3000))
    log.write_text('{"ts": "2026-10-01T09:00:00Z", "i": "work"}\n' + filler, encoding="utf-8")
    assert I.last_entry(log)["i"] == 2999
    # the work line is far before the tail: the whole file is read to find it
    assert I.last_entry(log, work_only=True) == {"ts": "2026-10-01T09:00:00Z", "i": "work"}
    log.write_text(filler, encoding="utf-8")
    assert I.last_entry(log, work_only=True) is None
    logs = tmp_path / "interactions"
    logs.mkdir()
    (logs / "box2.jsonl").write_text(
        '{"ts": "2026-10-02T11:00:00Z"}\n{"ts": "2026-10-02T11:30:00Z", "manager": true}\n', encoding="utf-8")
    [row] = I.activity(tmp_path)
    assert row["manager"] and row["ts"].hour == 11 and row["ts"].minute == 30 and row["work_ts"].minute == 0


def test_a_managers_line_never_checks_the_projects_compaction_checkpoint(repo, monkeypatch):
    project = str(repo / "projects" / "my" / "demo")
    seen = []
    monkeypatch.setattr(I, "_compaction_note", lambda project_dir, name: seen.append(project_dir))
    base = ["add", "--project", "demo", "--prompt", "p", "--request", "r", "--outcome", "o", "--dir", project]
    assert I.main(base) == 0 and I.main(base + ["--manager"]) == 0
    assert seen == [project, None]   # the manager is not the project's session: its checkpoint is its own


def test_add_manager_needs_a_project_dir(repo, monkeypatch, capsys):
    """Review B, finding 16: without --dir the mark would change nothing, so it is refused before any write."""
    base = ["add", "--project", "demo", "--prompt", "p", "--request", "r", "--outcome", "o"]
    assert I.main(base + ["--manager"]) == 2
    err = capsys.readouterr().err
    assert err.count("\n") == 1 and "--manager marks a line about a project: give --dir <project>" in err
    _stdin(monkeypatch, project="demo", manager=True, prompt="p", request="r", outcome="o")
    assert I.main(["add", "--stdin"]) == 2
    assert not (repo / ".memory" / "interactions").exists()
    _stdin(monkeypatch, project="demo", manager=False, prompt="p", request="r", outcome="o")
    assert I.main(["add", "--stdin"]) == 0   # no mark asked for: a plain line, as ever
    assert "manager" not in _lines(repo / ".memory" / "interactions" / "box1.jsonl")[0]


def test_who_counts_a_work_line_with_no_readable_time_before_a_managers_line(repo, capsys):
    """Review B, finding 15: a hand-made work line with no ts used to count as of the file's latest line. A
    manager's line after it must not hide it."""
    project = _project_logs(
        repo, box2=[{"project": "demo", "request": "work, no ts"},
                    {"ts": "2026-10-02T11:50:00Z", "project": "demo", "manager": True}])
    assert I.main(["who", "--dir", project]) == 3
    assert "box2 logged 10 min ago: this project may be in use there" in capsys.readouterr().out
    # with a readable work line too, that one decides: the newest work is an hour and a half old
    project = _project_logs(
        repo, box2=[{"project": "demo", "request": "work, no ts"},
                    {"ts": "2026-10-02T10:30:00Z", "project": "demo"},
                    {"ts": "2026-10-02T11:50:00Z", "project": "demo", "manager": True}])
    assert I.main(["who", "--dir", project]) == 0
