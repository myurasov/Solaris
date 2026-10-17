# Copyright 2026 Mikhail Yurasov <me@yurasov.me>
# SPDX-License-Identifier: Apache-2.0

"""Tests for solaris.tools.session_guard (the Stop and StopFailure hook), on temporary trees and fake payloads."""

from __future__ import annotations

import io
import json
import os
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from solaris.tools import session_clock as C
from solaris.tools import session_guard as G

REPO_ROOT = Path(__file__).resolve().parents[2]
PACK = {"project": {"name": "p", "slug": "p"}, "framework_version": "0.44.0"}
SID = "11111111-2222-3333-4444-555555555555"
REFUSAL = ("API Error: Opus 5.5's safeguards flagged this message (https://www.anthropic.com/legal/aup). This "
           "sometimes happens with safe, normal conversations.\n\nDetails: `[reasoning_extraction]`\n\nRequest ID: x")
CLOCK_TASK = {"id": "b1", "type": "shell", "status": "running", "description": "clock",
              "command": "cd ~/S && uv run -m solaris.tools.session_clock --dir projects/my/p --after X"}
HERE = socket.gethostname()


class Tree:
    """A Solaris root with one project, a temporary cache and the session's registry file."""

    def __init__(self, tmp_path):
        self.tmp = tmp_path
        self.root = tmp_path / "S"
        self.project = self.root / "projects" / "my" / "p"
        self.pack = self.project / "aipack"
        self.mem = self.pack / ".memory"
        self.mem.mkdir(parents=True)
        (self.pack / "manifest.json").write_text(json.dumps(PACK), encoding="utf-8")
        self.env = {"XDG_CACHE_HOME": str(tmp_path / "cache"), "HOME": str(tmp_path)}
        self.sessions = tmp_path / "cache" / "solaris" / "sessions"
        self.now = time.time()

    def register(self, updated=None):
        C.private_dirs(self.sessions)
        updated = self.now - 600 if updated is None else updated
        reg = {"schema": 1, "session": SID, "solaris": str(self.root), "dir": str(self.project),
               "pack": str(self.pack), "updated": C.iso(int(updated))}
        f = self.sessions / f"{SID}.json"
        f.write_text(json.dumps(reg), encoding="utf-8")
        os.utime(f, (updated, updated))
        return f

    def clock(self, **fields):
        data = {"schema": 1, "session": SID, "pid": 4242, "host": HERE,
                "started": C.iso(int(self.now - 900)), "wakes_at": C.iso(int(self.now - 300)), "state": "fired",
                "next_after": "2026-10-17T14:07:00-07:00", "cap": 0}
        data.update(fields)
        (self.mem / "clock.json").write_text(json.dumps(data), encoding="utf-8")

    def stop(self, tasks=(), active=False, text="Done for now.", **extra):
        payload = {"session_id": SID, "hook_event_name": "Stop", "stop_hook_active": active,
                   "last_assistant_message": text, "transcript_path": "", "cwd": str(self.root)}
        if tasks is not None:
            payload["background_tasks"] = list(tasks)
        payload.update(extra)
        return G.handle(payload, env=self.env, now=self.now)

    def failure(self, error, text, **extra):
        payload = {"session_id": SID, "hook_event_name": "StopFailure", "error": error,
                   "last_assistant_message": text}
        payload.update(extra)
        return G.handle(payload, env=self.env, now=self.now)

    def note(self, name, text, age):
        f = self.mem / name
        f.write_text(text, encoding="utf-8")
        os.utime(f, (self.now - age, self.now - age))
        return f

    def alerts(self):
        try:
            text = (self.mem / "alerts.jsonl").read_text(encoding="utf-8")
        except FileNotFoundError:
            return []
        return [json.loads(line) for line in text.splitlines()]

    def kinds(self):
        return [a["kind"] for a in self.alerts()]

    def clock_process(self, project=None):
        """A live process that looks like this project's clock, reaped as soon as it exits."""
        proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)", C.MODULE,
                                 "--dir", str(project or self.project)])
        threading.Thread(target=proc.wait, daemon=True).start()
        return proc


@pytest.fixture
def tree(tmp_path):
    t = Tree(tmp_path)
    t.register()
    return t


def _blocked(out):
    data = json.loads(out)
    assert data["decision"] == "block"
    return data["reason"]


def test_a_running_clock_lets_the_turn_end(tree):
    tree.clock(state="armed")
    assert tree.stop(tasks=[CLOCK_TASK]) == ""
    assert tree.stop(tasks=[dict(CLOCK_TASK, type="agent", command=None), CLOCK_TASK]) == ""
    assert tree.alerts() == []


def test_no_clock_blocks_with_the_exact_command(tree):
    tree.clock()
    tasks = [dict(CLOCK_TASK, status="completed"), {"id": "a1", "type": "agent", "status": "running"},
             dict(CLOCK_TASK, command="uv run -m solaris.tools.session_clock --dir projects/my/p --status"),
             dict(CLOCK_TASK, command="uv run -m solaris.tools.session_clock --dir projects/my/p --check")]
    reason = _blocked(tree.stop(tasks=tasks))
    cmd = (f"uv run --directory {tree.root} -m solaris.tools.session_clock --dir {tree.project} "
           "--after 2026-10-17T14:07:00-07:00 --cap 0")
    assert f"`{cmd}`" in reason and "project projects/my/p has no wake-up armed" in reason
    assert "background command, alone in its own call" in reason and "Pause only when the owner asked" in reason
    assert f"`uv run --directory {tree.root} -m solaris.tools.session_clock --forget`" in reason
    tree.clock(next_after="garbage", cap=25)   # an unusable --after is left out; the default cap is not written
    reason = _blocked(tree.stop(tasks=[]))
    assert f"--dir {tree.project}`" in reason and "--after" not in reason
    (tree.mem / "clock.json").unlink()
    assert f"--dir {tree.project}`" in _blocked(tree.stop(tasks=[]))
    assert tree.alerts() == []


def test_blocks_at_most_twice_in_a_row_then_alerts_once(tree):
    tree.clock()
    assert _blocked(tree.stop(active=False))
    assert _blocked(tree.stop(active=True))
    assert tree.stop(active=True) == ""   # the third end of the same turn is let through
    assert tree.kinds() == ["unarmed-turn-end"]
    alert = tree.alerts()[0]
    assert alert["session"] == SID and "after 2 reminders (clock.json: fired)" in alert["detail"]
    assert C.parse_time(alert["ts"]) == int(tree.now)
    assert tree.stop(active=True) == "" and len(tree.alerts()) == 1   # once per turn
    assert _blocked(tree.stop(active=False))   # a new turn counts again
    assert tree.stop(tasks=[CLOCK_TASK], active=True) == ""   # armed: the count resets
    assert _blocked(tree.stop(active=True)) and _blocked(tree.stop(active=True))
    assert tree.stop(active=True) == "" and len(tree.alerts()) == 2


def test_without_background_tasks_clock_json_decides(tree):
    live, stranger = tree.clock_process(), tree.clock_process(tree.tmp / "another-project")
    try:
        tree.clock(state="armed", pid=live.pid)
        assert tree.stop(tasks=None) == ""   # an older harness: armed, this session, this project, alive here
        tree.clock(state="armed", pid=stranger.pid)   # a live clock, but of another project (a reused pid)
        assert _blocked(tree.stop(tasks=None))
        tree.clock(state="armed", pid=live.pid, host="elsewhere")
        assert _blocked(tree.stop(tasks=None))
        tree.clock(state="fired", pid=live.pid)
        assert _blocked(tree.stop(tasks=None))
        tree.clock(state="armed", pid=live.pid)
        assert _blocked(tree.stop(tasks=[]))   # background_tasks present and empty: a detached clock wakes nothing
    finally:
        for proc in (live, stranger):
            proc.kill()
            proc.wait()
    tree.clock(state="armed", pid=live.pid)   # its process is gone
    assert _blocked(tree.stop(tasks=None))


def test_another_sessions_clock_holds_the_project_and_is_logged(tree):
    live = tree.clock_process()
    try:
        tree.clock(state="armed", pid=live.pid, session="other-session")
        assert tree.stop(tasks=[]) == ""   # not sent back: it would take the clock from the other session
        assert tree.stop(tasks=[], active=True) == ""
        assert tree.kinds() == ["unarmed-turn-end"]   # once per turn
        assert tree.alerts()[0]["detail"] == ("turn ended with no wake-up armed in this session; the project's clock "
                                              f"is held by session other-session on {HERE} (pid {live.pid})")
        tree.clock(state="armed", pid=live.pid, session=None)
        assert tree.stop(tasks=[]) == "" and "held by a session without an id" in tree.alerts()[-1]["detail"]
        tree.now += G.REPEAT_WINDOW + 1
        assert tree.failure("overloaded", "API Error: overloaded") == ""
        assert tree.alerts()[-1]["detail"].startswith("turn ended by overloaded with no wake-up armed")
        assert tree.alerts()[-1]["detail"].endswith(f"held by a session without an id on {HERE} (pid {live.pid})")
        tree.clock(state="armed", pid=live.pid, session="other-session")
    finally:
        live.kill()
        live.wait()
    assert _blocked(tree.stop(tasks=[]))   # that clock died
    later, long_ago = C.iso(int(tree.now + 600)), C.iso(int(tree.now - 3 * 3600))
    tree.clock(state="armed", session="other-session", host="elsewhere", wakes_at=later,
               started=C.iso(int(tree.now - 600)))
    assert tree.stop(tasks=[]) == "" and "on elsewhere" in tree.alerts()[-1]["detail"]
    tree.clock(state="armed", session="other-session", host="elsewhere", wakes_at=later, started=long_ago)
    assert _blocked(tree.stop(tasks=[]))   # started over 2 hours ago: no longer counts
    tree.clock(state="armed", session="other-session", host="elsewhere", wakes_at=C.iso(int(tree.now - 3600)),
               started=C.iso(int(tree.now - 4000)))
    assert _blocked(tree.stop(tasks=[]))   # over 30 minutes past its wake-up


def test_a_paused_project_is_left_alone_and_logged_once(tree):
    tree.clock(state="stopped")
    tree.note("handover-2026-10-17-1400.md", "# Handover\n\nPaused for the night.\n", age=60)
    assert tree.stop() == "" and tree.stop(active=True) == "" and tree.stop() == ""
    assert tree.kinds() == ["paused"]   # the first time this note is honoured only
    assert "paused by the handover note handover-2026-10-17-1400.md" in tree.alerts()[0]["detail"]
    # Syncthing conflict copies never count, whatever their name or age
    tree.note("handover-2026-10-19-0000.sync-conflict-20261019-000000-ABCDEFG.md", "conflict\n", age=1)
    assert tree.stop() == "" and tree.kinds() == ["paused"]
    # the newest note is picked by the time in its name, not by the file time
    tree.note("handover-2026-10-18-0900.md", "# Handover\n\nRESUMED AT 2026-10-18T10:00-07:00\n", age=7200)
    assert _blocked(tree.stop())   # resumed (any case); older by file time than the 1400 note, newer by name
    (tree.mem / "handover-2026-10-18-0900.md").unlink()
    tree.note("handover-2026-10-17-1400.md", "# Handover\n", age=3600)   # older than the last clock start
    assert _blocked(tree.stop())
    tree.note("handover-2026-10-20-0800.md", "# Handover\n", age=30)   # a new pause: logged again
    assert tree.stop() == "" and tree.kinds() == ["paused", "paused"]


def test_a_stop_newer_than_the_last_clock_start_is_a_pause(tree, monkeypatch, capsys):
    tree.clock(state="stopped", stop_reason="taken over by a clock of session x (pid 1)")
    assert _blocked(tree.stop())   # a takeover is no pause
    tree.clock(state="stopped", stop_reason=C.STOP_REASON, stopped_at=C.iso(int(tree.now - 3600)))
    assert _blocked(tree.stop())   # stopped before the last clock start
    monkeypatch.setenv("XDG_CACHE_HOME", tree.env["XDG_CACHE_HOME"])
    monkeypatch.setenv(C.SESSION_ENV, SID)
    assert C.main(["--dir", str(tree.project), "--stop"]) == 0   # the real --stop, now
    assert "counts the project as paused" in capsys.readouterr().out
    tree.now = time.time()
    assert tree.stop() == "" and tree.stop() == ""
    assert tree.kinds() == ["paused"]
    assert "paused by session_clock --stop at" in tree.alerts()[0]["detail"]
    assert f"(session {SID})" in tree.alerts()[0]["detail"]
    tree.register(updated=tree.now + 5)   # a clock started again after the stop
    tree.now += 10
    assert _blocked(tree.stop())


@pytest.mark.parametrize("event", ["Stop", "StopFailure"])
def test_a_refusal_never_blocks_and_alerts_once(tree, event):
    tree.clock()
    if event == "Stop":
        assert tree.stop(text=REFUSAL) == "" and tree.stop(text=REFUSAL, active=True) == ""
    else:
        assert tree.failure("invalid_request", REFUSAL) == "" and tree.failure("invalid_request", REFUSAL) == ""
    assert tree.kinds() == ["classifier-stop", "unarmed-turn-end"]   # the same refused turn counts once
    detail = tree.alerts()[0]["detail"]
    assert "safeguards flagged this message" in detail and "[reasoning_extraction]" in detail
    assert "\n" not in detail and "https://" not in detail and detail.startswith(event)
    assert "refusal" in tree.alerts()[1]["detail"]
    tree.now += G.REPEAT_WINDOW + 1   # the next refused turn
    assert tree.stop(text=REFUSAL) == "" and len(tree.alerts()) == 4


@pytest.mark.parametrize("text", [
    "API Error: Claude Opus 5.5 can't help with this. Start a new session to continue.",
    "API Error: Claude Code can’t respond to your last message with Opus 5.5.",
])
def test_other_refusal_wordings(tree, text):
    tree.clock(state="armed")
    assert tree.stop(text=text, tasks=[CLOCK_TASK]) == ""   # never sent back
    assert tree.kinds() == ["classifier-stop"]
    tree.now += G.REPEAT_WINDOW + 1
    assert tree.failure("invalid_request", text) == ""
    assert tree.kinds().count("classifier-stop") == 2 and "api-error" not in tree.kinds()


def test_what_is_not_a_refusal(tree, tmp_path):
    tree.clock()
    transcript = tmp_path / "t.jsonl"
    transcript.write_text(json.dumps({"type": "assistant", "message": {"stop_reason": "end_turn"}}) + "\n",
                          encoding="utf-8")
    kaggle = "API Error from Kaggle: submission refusal (1 pending submission)."
    assert _blocked(tree.stop(text=kaggle, transcript_path=str(transcript)))
    assert _blocked(tree.stop(text="The safety classifier stopped worker j221; I re-ran it on Sonnet."))
    assert tree.alerts() == []
    assert tree.failure("unknown", "The API refused: stop reason refusal") == ""   # StopFailure text is an error
    assert tree.failure("refusal", "") == ""
    assert tree.kinds()[0] == "classifier-stop" and tree.kinds().count("classifier-stop") == 2


def test_a_refusal_seen_in_the_transcript(tree, tmp_path):
    tree.clock(state="armed")
    transcript = tmp_path / "t.jsonl"
    lines = [{"type": "user", "message": {"role": "user", "content": "go"}},
             {"type": "assistant", "isSidechain": True, "message": {"stop_reason": "end_turn"}},
             {"type": "assistant", "message": {"model": "<synthetic>", "stop_reason": "refusal"}},
             {"type": "system", "subtype": "informational"}]
    transcript.write_text("\n".join(json.dumps(x) for x in lines) + "\n", encoding="utf-8")
    assert tree.stop(text="", transcript_path=str(transcript), tasks=[CLOCK_TASK]) == ""
    # an API error worded some other way still counts when the transcript says refusal
    assert tree.stop(text="API Error: something new", transcript_path=str(transcript), tasks=[CLOCK_TASK]) == ""
    assert tree.kinds() == ["classifier-stop", "classifier-stop"]
    lines.append({"type": "assistant", "message": {"stop_reason": "end_turn"}})
    transcript.write_text("\n".join(json.dumps(x) for x in lines) + "\n", encoding="utf-8")
    assert _blocked(tree.stop(text="", transcript_path=str(transcript), active=True))


@pytest.mark.parametrize("error, lasting", [("rate_limit", False), ("overloaded", False), ("server_error", False),
                                            ("billing_error", True), ("authentication_failed", True),
                                            ("max_output_tokens", True), ("", True)])
def test_stop_failure_alerts(tree, error, lasting):
    text = "API Error: something went wrong\nmore"
    tree.clock()
    assert tree.failure(error, text) == ""   # unarmed: logged, transient errors included
    assert tree.kinds() == (["api-error"] if lasting else []) + ["unarmed-turn-end"]
    assert tree.alerts()[-1]["detail"] == (f"turn ended by {error or 'an API error'} with no wake-up armed: "
                                           "API Error: something went wrong")
    if lasting:
        assert tree.alerts()[0]["detail"] == f"{error or 'unknown'}: API Error: something went wrong"
    (tree.mem / "alerts.jsonl").unlink()
    live = tree.clock_process()
    try:
        tree.clock(state="armed", pid=live.pid)
        tree.now += G.REPEAT_WINDOW + 1
        assert tree.failure(error, text) == ""   # armed: only a lasting error is logged
        assert tree.kinds() == (["api-error"] if lasting else [])
    finally:
        live.kill()
        live.wait()


def test_stop_failure_is_logged_once_per_turn(tree):
    tree.clock()
    assert tree.failure("rate_limit", "x") == "" and tree.failure("rate_limit", "x") == ""
    assert tree.kinds() == ["unarmed-turn-end"]
    tree.now += G.REPEAT_WINDOW + 1
    assert tree.failure("overloaded", "y") == ""
    assert tree.kinds() == ["unarmed-turn-end", "unarmed-turn-end"]
    tree.note("handover-2026-10-17-1400.md", "# Handover\n", age=1)
    tree.now += G.REPEAT_WINDOW + 1
    assert tree.failure("rate_limit", "x") == "" and tree.kinds()[-1] == "paused"


def test_a_subagent_turn_is_ignored(tree):
    tree.clock()
    assert tree.stop(agent_id="a1b2c3") == ""
    assert tree.stop(text=REFUSAL, agent_id="a1b2c3") == ""
    assert tree.failure("invalid_request", REFUSAL, agent_id="a1b2c3") == ""
    assert tree.failure("billing_error", "x", agent_id="a1b2c3") == ""
    assert tree.alerts() == [] and not (tree.sessions / f"{SID}{C.GUARD_SUFFIX}").exists()
    assert _blocked(tree.stop(agent_id=""))   # an empty agent id is the main session


def test_no_registry_means_no_project_session(tree, monkeypatch, capsys):
    tree.clock()
    monkeypatch.setenv("XDG_CACHE_HOME", tree.env["XDG_CACHE_HOME"])
    monkeypatch.setenv(C.SESSION_ENV, SID)
    assert C.main(["--forget"]) == 0 and "no longer runs" in capsys.readouterr().out
    assert tree.stop(tasks=[]) == "" and tree.failure("invalid_request", REFUSAL) == ""
    assert tree.failure("billing_error", "x") == "" and tree.alerts() == []
    assert G.handle({"hook_event_name": "Stop", "session_id": "../../etc"}, env=tree.env) == ""
    assert G.handle({"hook_event_name": "Stop"}, env=tree.env) == ""


def test_a_stale_or_broken_registry_is_ignored(tree):
    tree.clock()
    reg = tree.register(updated=tree.now - 15 * 86400)
    (tree.sessions / f"{SID}{C.GUARD_SUFFIX}").write_text("{}", encoding="utf-8")
    assert tree.stop() == "" and not reg.exists() and not (tree.sessions / f"{SID}{C.GUARD_SUFFIX}").exists()
    for bad in ("not json", json.dumps([1]), json.dumps({"session": "someone-else"}),
                json.dumps({"session": SID, "updated": "yesterday", "pack": str(tree.pack)})):
        reg.write_text(bad, encoding="utf-8")
        assert tree.stop() == ""
    tree.register()
    tree.pack.rename(tree.project / "gone")   # the pack moved away
    assert tree.stop() == ""


def test_other_events_and_an_unwritable_state(tree):
    tree.clock()
    assert G.handle({"hook_event_name": "SubagentStop", "session_id": SID}, env=tree.env, now=tree.now) == ""
    assert G.handle({"hook_event_name": "UserPromptSubmit", "session_id": SID}, env=tree.env, now=tree.now) == ""
    assert tree.alerts() == []
    (tree.sessions / f"{SID}{C.GUARD_SUFFIX}").mkdir()   # the count cannot be kept: never block, but log the turn
    assert tree.stop() == ""
    assert tree.kinds() == ["unarmed-turn-end"] and "after 0 reminders" in tree.alerts()[0]["detail"]


def test_session_id_from_the_environment(tree):
    tree.clock()
    payload = {"hook_event_name": "Stop", "background_tasks": [], "stop_hook_active": False}
    assert _blocked(G.handle(payload, env=dict(tree.env, CLAUDE_CODE_SESSION_ID=SID), now=tree.now))


def test_the_pause_note_matches_read_first(tree):
    from solaris.tools import read_first
    if not hasattr(read_first, "paused_handover"):
        pytest.skip("read_first has no pause-note reader")
    cases = [("handover-2026-10-17-1400.md", "x\n", 60),
             ("handover-2026-10-18-0900.md", "Resumed At noon\n", 7200),
             ("handover-2026-10-19-0000.sync-conflict-20261019-000000-ABCDEFG.md", "x\n", 1),
             ("handover-2026-10-20-0000.md", "x\n", 5000)]
    for name, text, age in cases:
        tree.note(name, text, age)
        for since in (None, tree.now - 600):
            ours, theirs = C.paused_note(tree.pack, since), read_first.paused_handover(tree.pack, since)
            assert ours == theirs, (name, since)


@pytest.mark.parametrize("raw", ["", "   ", "{not json", "[1, 2]", "null", '"text"', "\x00\xff"])
def test_garbage_stdin_prints_nothing(tree, monkeypatch, capsys, raw):
    monkeypatch.setattr(sys, "stdin", io.StringIO(raw))
    assert G.main() == 0
    assert capsys.readouterr().out == ""


def test_main_reads_stdin_and_prints_the_block(tree, monkeypatch, capsys):
    tree.clock()
    monkeypatch.setenv("XDG_CACHE_HOME", tree.env["XDG_CACHE_HOME"])
    payload = {"session_id": SID, "hook_event_name": "Stop", "stop_hook_active": False, "background_tasks": []}
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(payload)))
    assert G.main() == 0
    assert _blocked(capsys.readouterr().out)


def _hook(tree, stdin_text=None, keep_open=False):
    env = dict(os.environ, **tree.env)
    env.pop("CLAUDE_CODE_SESSION_ID", None)
    proc = subprocess.Popen([sys.executable, "-m", "solaris.tools.session_guard"], cwd=REPO_ROOT, env=env,
                            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    started = time.monotonic()
    if keep_open:   # never write, never close: the hook must give up on its own
        try:
            proc.wait(timeout=20)
        finally:
            proc.kill()
        out, err = proc.stdout.read(), proc.stderr.read()
        proc.stdin.close()
    else:
        out, err = proc.communicate(stdin_text, timeout=20)
    return proc.returncode, out, err, time.monotonic() - started


def test_the_hook_as_a_process(tree):
    tree.clock()
    payload = {"session_id": SID, "hook_event_name": "Stop", "stop_hook_active": False, "background_tasks": []}
    code, out, err, _took = _hook(tree, json.dumps(payload))
    assert code == 0 and err == "" and _blocked(out.strip())
    code, out, err, _took = _hook(tree, json.dumps(dict(payload, background_tasks=[CLOCK_TASK])))
    assert code == 0 and out == "" and err == ""


def test_a_payload_that_never_ends_times_out_quietly(tree):
    code, out, err, took = _hook(tree, keep_open=True)   # stdin stays open and empty
    assert code == 0 and out == "" and err == ""
    assert took < G.READ_TIMEOUT + 10


def test_a_normal_stop_is_quick(tree):
    tree.clock(state="armed")
    runs = []
    for _ in range(5):   # the fastest run: a loaded machine slows some runs, not all
        started = time.perf_counter()
        tree.stop(tasks=[CLOCK_TASK])
        runs.append(time.perf_counter() - started)
    assert min(runs) < 0.25
