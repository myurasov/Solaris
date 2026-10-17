# Copyright 2026 Mikhail Yurasov <me@yurasov.me>
# SPDX-License-Identifier: Apache-2.0

"""Tests for solaris.tools.session_clock (the wake clock), on synthetic schedules and a fake clock."""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

import pytest

from solaris.tools import session_clock as C

T0 = int(datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc).timestamp())   # on the hour
MIN = 60
PACK = {"project": {"name": "x", "slug": "x"}, "framework_version": "0.40.0"}
REPO_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(autouse=True)
def _private_cache(tmp_path, monkeypatch):
    # the registry and the lock files go to a temporary cache, and no test inherits a real session id
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    monkeypatch.delenv(C.SESSION_ENV, raising=False)


class Clock:
    """A fake wall clock: sleeping moves it forward, and ``jumps`` can add a lost stretch (a machine asleep)."""

    def __init__(self, t):
        self.t = t
        self.naps = []
        self.jumps = []

    def now(self):
        return self.t

    def sleep(self, s):
        assert s > 0
        self.naps.append(s)
        self.t += s + (self.jumps.pop(0) if self.jumps else 0)


@pytest.fixture
def clock(monkeypatch):
    c = Clock(T0)
    monkeypatch.setattr(C, "_now", c.now)
    monkeypatch.setattr(C, "_sleep", c.sleep)
    return c


def _schedule(tmp_path, events):
    f = tmp_path / "schedule.json"
    f.write_text(json.dumps(events) if not isinstance(events, str) else events, encoding="utf-8")
    return f


def _at(t):
    return datetime.fromtimestamp(t, timezone.utc).isoformat()


def _run(capsys, *argv):
    code = C.main([str(a) for a in argv])
    return code, capsys.readouterr().out


def _next(flag, target, t, cap=""):
    """The printed next-command line."""
    return (f"session_clock: next: uv run --directory {REPO_ROOT} -m solaris.tools.session_clock {flag} "
            f"{Path(target).resolve()} --after {C.iso(t)}" + (f" --cap {cap}" if cap else ""))


def _project(tmp_path, events=None, name="proj"):
    project = tmp_path / name
    (project / "aipack" / ".memory").mkdir(parents=True)
    (project / "aipack" / "manifest.json").write_text(json.dumps(PACK), encoding="utf-8")
    if events is not None:
        _schedule(project / "aipack" / ".memory", events)
    return project


def _clock_json(project):
    return json.loads((project / "aipack" / ".memory" / "clock.json").read_text(encoding="utf-8"))


def _fake_clock_process(*extra):
    """A live process whose command line names the clock module, reaped as soon as it exits."""
    proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)", C.MODULE, *extra])
    threading.Thread(target=proc.wait, daemon=True).start()
    return proc


def test_sleeps_until_the_event_then_prints_it(tmp_path, clock, capsys):
    f = _schedule(tmp_path, [{"name": "reset", "at": _at(T0 + 10 * MIN), "note": "extra keys are ignored"},
                             {"name": "later", "at": _at(T0 + 20 * MIN)}])
    code, out = _run(capsys, "--schedule", f)
    assert code == 0
    assert clock.t == T0 + 10 * MIN and max(clock.naps) <= C.NAP   # short naps on the wall clock
    assert out.splitlines() == [f"session_clock: due reset at {C.iso(T0 + 10 * MIN)}",
                                _next("--schedule", f, T0 + 10 * MIN)]
    code, out = _run(capsys, "--schedule", f, "--after", C.iso(T0 + 10 * MIN))   # the next start
    assert code == 0 and clock.t == T0 + 20 * MIN and "due later at" in out


def test_cap_ends_the_sleep_with_a_re_arm(tmp_path, clock, capsys):
    f = _schedule(tmp_path, [{"name": "far", "at": _at(T0 + 3 * 60 * MIN)}])
    code, out = _run(capsys, "--schedule", f)
    assert code == 0 and clock.t == T0 + 25 * MIN   # default cap: 25 minutes
    assert "re-arm" in out and "next is far at" in out and "(in 2h35m)" in out
    assert out.splitlines()[-1] == _next("--schedule", f, T0)   # the same --after as this start
    code, out = _run(capsys, "--schedule", f, "--cap", "5")
    assert code == 0 and clock.t == T0 + 30 * MIN and "5-minute cap reached" in out
    assert out.splitlines()[-1] == _next("--schedule", f, T0 + 25 * MIN, cap="5")


def test_cap_0_sleeps_until_the_event(tmp_path, clock, capsys):
    f = _schedule(tmp_path, [{"name": "far", "at": _at(T0 + 3 * 60 * MIN)}])
    code, out = _run(capsys, "--schedule", f, "--cap", "0")
    assert code == 0 and clock.t == T0 + 3 * 60 * MIN and max(clock.naps) <= C.NAP
    assert "re-arm" not in out and out.splitlines() == [f"session_clock: due far at {C.iso(T0 + 3 * 60 * MIN)}",
                                                        _next("--schedule", f, T0 + 3 * 60 * MIN, cap="0")]


def test_every_counts_from_midnight_utc_or_from_at():
    hourly = ("hourly", None, 60 * MIN)
    assert C.occurrence(None, 60 * MIN, T0, T0) == T0 + 60 * MIN   # strictly after --after
    assert C.occurrence(None, 60 * MIN, T0 + 1, T0 + 1) == T0 + 60 * MIN
    anchored = C.parse_time(_at(T0 + 7 * MIN))
    assert C.occurrence(anchored, 60 * MIN, T0, T0) == T0 + 7 * MIN    # first at the anchor
    assert C.occurrence(anchored, 60 * MIN, T0 + 7 * MIN, T0 + 7 * MIN) == T0 + 67 * MIN
    assert C.occurrence(anchored, 60 * MIN, T0 - 600 * MIN, T0 - 600 * MIN) == T0 + 7 * MIN   # none before it
    assert C.due([hourly], T0, T0) == [(T0 + 60 * MIN, "hourly")]


def test_events_at_the_same_time_fire_together(tmp_path, clock, capsys):
    f = _schedule(tmp_path, [{"name": "b-reset", "at": _at(T0 + 60 * MIN)}, {"name": "a-hourly", "every": 60},
                             {"name": "other", "at": _at(T0 + 61 * MIN)}])
    code, out = _run(capsys, "--schedule", f, "--cap", "90")
    assert code == 0 and clock.t == T0 + 60 * MIN
    assert [line.split(" at ")[0] for line in out.splitlines()[:2]] == ["session_clock: due a-hourly",
                                                                       "session_clock: due b-reset"]
    assert "other" not in out


def test_after_catches_up_on_missed_events_at_once(tmp_path, clock, capsys):
    # the agent was busy for three hours: the one-off fires, and the half-hourly tick fires once, for its latest
    f = _schedule(tmp_path, [{"name": "deadline", "at": _at(T0 - 60 * MIN)}, {"name": "pass", "every": 30},
                             {"name": "handled", "at": _at(T0 - 3 * 60 * MIN)}])
    code, out = _run(capsys, "--schedule", f, "--after", T0 - 3 * 60 * MIN)   # epoch seconds work too
    assert code == 0 and clock.naps == []
    assert out.splitlines() == [f"session_clock: due deadline at {C.iso(T0 - 60 * MIN)}",
                                f"session_clock: due pass at {C.iso(T0)}",
                                _next("--schedule", f, T0)]


def test_a_machine_that_slept_wakes_with_everything_due(tmp_path, clock, capsys):
    f = _schedule(tmp_path, [{"name": "first", "at": _at(T0 + 10 * MIN)}, {"name": "second", "at": _at(T0 + 40 * MIN)}])
    clock.jumps = [60 * MIN]   # the first nap loses an hour
    code, out = _run(capsys, "--schedule", f)
    assert code == 0 and len(clock.naps) == 1
    assert "due first" in out and "due second" in out and "re-arm" not in out


def test_dir_reads_the_pack_schedule_whatever_the_pack_is_called(tmp_path, clock, capsys):
    project = tmp_path / "proj"
    (project / "brain" / ".memory").mkdir(parents=True)
    (project / "brain" / "manifest.json").write_text(json.dumps(PACK), encoding="utf-8")
    code, out = _run(capsys, "--dir", project)
    assert code == 1 and "brain/.memory/schedule.json not found" in out
    _schedule(project / "brain" / ".memory", [{"name": "soon", "every": 5}])
    code, out = _run(capsys, "--dir", project)
    assert code == 0 and "due soon at" in out and clock.t == T0 + 5 * MIN
    code, out = _run(capsys, "--dir", tmp_path / "nowhere")
    assert code == 1 and "--dir must be a project root" in out
    code, out = _run(capsys, "--dir", tmp_path)
    assert code == 1 and "no ai-pack" in out


@pytest.mark.parametrize("events, problem", [
    ("{not json", "not valid JSON"),
    ({"name": "x", "every": 5}, "must hold a JSON list"),
    (["x"], "event 1 is not an object"),
    ([{"every": 5}], "needs a name"),
    ([{"name": " "}], "needs a name"),
    ([{"name": "x"}], "needs at (a time) or every (minutes)"),
    ([{"name": "x", "at": "2026-10-01T17:00:00"}], "UTC offset"),
    ([{"name": "x", "at": "tomorrow"}], "UTC offset"),
    ([{"name": "x", "at": None}], "UTC offset"),
    ([{"name": "x", "every": 0}], "whole number of minutes"),
    ([{"name": "x", "every": 1.5}], "whole number of minutes"),
    ([{"name": "x", "every": "60"}], "whole number of minutes"),
    ([{"name": "x", "every": True}], "whole number of minutes"),
    ([{"name": "x", "every": 10 ** 9}], "whole number of minutes"),
    ([{"name": "ok", "every": 60}, {"name": "x", "evry": 5}], "event 2 (x) needs at"),
])
def test_bad_schedules_are_clean_errors(tmp_path, clock, capsys, events, problem):
    code, out = _run(capsys, "--schedule", _schedule(tmp_path, events))
    assert code == 1 and problem in out and out.startswith("session_clock: ") and clock.naps == []


def test_missing_schedule_and_nothing_left(tmp_path, clock, capsys):
    code, out = _run(capsys, "--schedule", tmp_path / "none.json")
    assert code == 1 and "none.json not found" in out
    f = _schedule(tmp_path, [{"name": "past", "at": _at(T0 - MIN)}])
    code, out = _run(capsys, "--schedule", f)
    assert code == 1 and "no event left to wait for" in out
    assert _run(capsys, "--schedule", _schedule(tmp_path, []))[0] == 1


@pytest.mark.parametrize("argv", [
    ["--cap", "-5"], ["--cap", "nan"], ["--cap", "inf"], ["--cap", "soon"], ["--stop"], ["--status"],
    ["--stop", "--status"],
    ["--after", "2026-10-01T12:00:00"], ["--after", "yesterday"], ["--after", "inf"], ["--after", "1e30"],
])
def test_bad_arguments_exit_2(tmp_path, clock, argv):
    f = _schedule(tmp_path, [{"name": "x", "every": 5}])
    with pytest.raises(SystemExit) as exc:
        C.main(["--schedule", str(f), *argv])
    assert exc.value.code == 2


def test_dir_or_schedule_is_required(tmp_path, clock):
    with pytest.raises(SystemExit) as exc:
        C.main([])
    assert exc.value.code == 2
    with pytest.raises(SystemExit) as exc:
        C.main(["--dir", str(tmp_path), "--schedule", str(tmp_path / "s.json")])
    assert exc.value.code == 2


def test_clock_json_follows_the_clock(tmp_path, clock, capsys, monkeypatch):
    project = _project(tmp_path, [{"name": "hourly", "every": 60}])
    seen, runs = [], []

    def sleep(s):   # keeps clock.json as the first nap of each run sees it
        if len(seen) <= len(runs):
            seen.append(_clock_json(project))
        clock.sleep(s)

    monkeypatch.setattr(C, "_sleep", sleep)
    runs.append(_run(capsys, "--dir", project))   # next event at T0 + 60: the 25-minute cap comes first
    armed = seen[0]
    assert armed == {"schema": 1, "session": None, "pid": os.getpid(), "host": socket.gethostname(),
                     "started": C.iso(T0), "wakes_at": C.iso(T0 + 25 * MIN), "state": "armed",
                     "next_after": C.iso(T0), "cap": 25}
    assert _clock_json(project) == dict(armed, state="re-arm")
    assert runs[0][1].splitlines()[-1] == _next("--dir", project, T0)
    runs.append(_run(capsys, "--dir", project, "--after", C.iso(T0), "--cap", "0"))   # no cap: wakes at the event
    assert seen[1]["wakes_at"] == C.iso(T0 + 60 * MIN) and seen[1]["cap"] == 0 and seen[1]["state"] == "armed"
    assert _clock_json(project)["state"] == "fired" and _clock_json(project)["next_after"] == C.iso(T0 + 60 * MIN)
    assert runs[1][1].splitlines() == [f"session_clock: due hourly at {C.iso(T0 + 60 * MIN)}",
                                       _next("--dir", project, T0 + 60 * MIN, cap="0")]
    assert not (tmp_path / "cache" / "solaris" / "sessions").exists()   # no session id: no registry


def test_registry_records_the_session_project(tmp_path, clock, capsys, monkeypatch):
    project = _project(tmp_path, [{"name": "soon", "every": 5}])
    sessions = tmp_path / "cache" / "solaris" / "sessions"
    monkeypatch.setenv(C.SESSION_ENV, "sess-1")
    assert _run(capsys, "--dir", project)[0] == 0
    reg = json.loads((sessions / "sess-1.json").read_text(encoding="utf-8"))
    assert reg == {"schema": 1, "session": "sess-1", "solaris": str(REPO_ROOT), "dir": str(project.resolve()),
                   "pack": str(project.resolve() / "aipack"), "updated": C.iso(T0)}
    assert _clock_json(project)["session"] == "sess-1"
    for folder in (tmp_path / "cache", tmp_path / "cache" / "solaris", sessions):
        assert folder.stat().st_mode & 0o777 == 0o700
    stale, fresh = sessions / "old.json", sessions / "other.json"
    for f in (stale, fresh):
        f.write_text("{}", encoding="utf-8")
    os.utime(stale, (time.time() - 15 * 86400,) * 2)
    assert _run(capsys, "--dir", project)[0] == 0
    assert not stale.exists() and fresh.exists()
    assert json.loads((sessions / "sess-1.json").read_text(encoding="utf-8"))["updated"] == C.iso(T0 + 5 * MIN)
    monkeypatch.setenv(C.SESSION_ENV, "../escape")   # not a usable file name: no registry, the clock still runs
    assert _run(capsys, "--dir", project)[0] == 0
    assert sorted(p.name for p in sessions.iterdir()) == ["other.json", "sess-1.json"]
    monkeypatch.setenv(C.SESSION_ENV, "sess-2")   # --schedule names no project
    assert _run(capsys, "--schedule", project / "aipack" / ".memory" / "schedule.json")[0] == 0
    assert not (sessions / "sess-2.json").exists()


def test_a_new_clock_stops_the_older_one_of_this_project_only(tmp_path, clock, capsys):
    project = _project(tmp_path, [{"name": "soon", "every": 5}])
    mem = project / "aipack" / ".memory"
    here = socket.gethostname()
    elsewhere = _fake_clock_process("--dir", str(tmp_path / "another-project"))   # a clock of another project
    relative = _fake_clock_process("--dir", "x")   # a relative --dir that names no such project
    old = _fake_clock_process("--dir", str(project))
    try:
        for pid in (elsewhere.pid, relative.pid):
            (mem / "clock.json").write_text(json.dumps({"pid": pid, "host": here, "state": "armed"}), encoding="utf-8")
            code, out = _run(capsys, "--dir", project)
            assert code == 0 and "stopped the older clock" not in out
        (mem / "clock.json").write_text(json.dumps({"pid": old.pid, "host": here, "state": "fired"}), encoding="utf-8")
        code, out = _run(capsys, "--dir", project)   # clock.json not armed: its pid is never signalled
        assert code == 0 and "stopped the older clock" not in out
        assert elsewhere.poll() is None and relative.poll() is None and old.poll() is None
        (mem / "clock.json").write_text(json.dumps({"schema": 1, "pid": old.pid, "host": here, "state": "armed"}),
                                        encoding="utf-8")
        code, out = _run(capsys, "--dir", project)
        assert code == 0 and out.splitlines()[0] == f"session_clock: stopped the older clock of this project (pid {old.pid})"
        assert old.wait(timeout=5) != 0
        data = _clock_json(project)
        assert data["pid"] == os.getpid() and data["state"] == "fired" and "stop_reason" not in data
        assert elsewhere.poll() is None and relative.poll() is None
    finally:
        for proc in (elsewhere, relative, old):
            proc.kill()


def test_a_process_names_a_project_by_its_dir(tmp_path):
    project = _project(tmp_path)
    assert C._runs_project(["python", "-m", C.MODULE, "--dir", str(project)], None, project)
    assert C._runs_project(["python", "-m", C.MODULE, f"--dir={project}"], None, project)
    assert C._runs_project(["python", "-m", C.MODULE, "--dir", "proj"], str(tmp_path), project)   # its own folder
    assert C._runs_project(["python", "-m", C.MODULE, "--dir", os.path.relpath(project, REPO_ROOT)], None, project)
    assert not C._runs_project(["python", "-m", C.MODULE, "--dir", "proj"], None, project)
    assert not C._runs_project(["python", "-m", C.MODULE, "--schedule", str(project)], None, project)
    assert not C._runs_project(["python", "-m", C.MODULE, "--dir"], None, project)


def test_only_a_clock_of_this_user_on_this_machine_is_stopped(tmp_path, clock, capsys):
    project = _project(tmp_path, [{"name": "soon", "every": 5}])
    mem = project / "aipack" / ".memory"
    other = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)", "--dir", str(project)])  # no clock
    try:
        for pid, host in ((other.pid, socket.gethostname()), (os.getpid(), socket.gethostname()),
                          (other.pid, "elsewhere")):
            (mem / "clock.json").write_text(json.dumps({"pid": pid, "host": host, "state": "armed"}),
                                            encoding="utf-8")
            code, out = _run(capsys, "--dir", project)
            assert code == 0 and "stopped the older clock" not in out and other.poll() is None
            assert ("names a clock on elsewhere" in out) == (host == "elsewhere")
    finally:
        other.kill()
        other.wait()


def test_stop_marks_a_pause(tmp_path, capsys, monkeypatch):
    project = _project(tmp_path, [{"name": "soon", "every": 5}])
    mem = project / "aipack" / ".memory"
    monkeypatch.setenv(C.SESSION_ENV, "sess-1")
    code, out = _run(capsys, "--dir", project, "--stop")   # no clock.json yet: the pause is still recorded
    assert code == 0 and "no armed clock for this project here" in out and "counts the project as paused" in out
    data = _clock_json(project)
    assert data["state"] == "stopped" and data["stop_reason"] == C.STOP_REASON
    assert data["stopped_by_session"] == "sess-1" and abs(C.parse_time(data["stopped_at"]) - time.time()) < 5
    live = _fake_clock_process("--dir", str(project))
    try:
        (mem / "clock.json").write_text(json.dumps({"pid": live.pid, "host": socket.gethostname(),
                                                    "state": "fired"}), encoding="utf-8")
        code, out = _run(capsys, "--dir", project, "--stop")   # not armed: never signalled
        assert code == 0 and "no armed clock" in out and live.poll() is None
        (mem / "clock.json").write_text(json.dumps({"pid": live.pid, "host": socket.gethostname(),
                                                    "state": "armed"}), encoding="utf-8")
        code, out = _run(capsys, "--dir", project, "--stop")
        assert code == 0 and out.startswith(f"session_clock: stopped the clock of this project (pid {live.pid})")
        assert live.wait(timeout=5) != 0
        assert _clock_json(project)["state"] == "stopped"
    finally:
        live.kill()
    (mem / "clock.json").write_text(json.dumps({"pid": 4242, "host": "elsewhere", "state": "armed"}),
                                    encoding="utf-8")
    code, out = _run(capsys, "--dir", project, "--stop")
    assert code == 1 and "runs on elsewhere" in out and _clock_json(project)["state"] == "armed"
    assert _run(capsys, "--dir", tmp_path / "nowhere", "--stop")[0] == 1


def test_status(tmp_path, capsys):
    project = _project(tmp_path, [{"name": "soon", "every": 5}])
    mem = project / "aipack" / ".memory"
    code, out = _run(capsys, "--dir", project, "--status")
    assert code == 3 and "no clock started" in out
    live = _fake_clock_process("--dir", str(project))
    stranger = _fake_clock_process("--dir", str(tmp_path / "another-project"))
    here = socket.gethostname()
    try:
        for data, want, says in (
                ({"pid": live.pid, "host": here, "state": "armed", "wakes_at": "W"}, 0, "armed, pid {} alive, wakes at W"),
                ({"pid": live.pid, "host": here, "state": "fired"}, 3, "fired, pid {} alive"),
                ({"pid": live.pid, "host": "elsewhere", "state": "armed"}, 3, "pid {} runs on elsewhere"),
                ({"pid": stranger.pid, "host": here, "state": "armed"}, 3, "pid {} not running"),
        ):
            (mem / "clock.json").write_text(json.dumps(data), encoding="utf-8")
            code, out = _run(capsys, "--dir", project, "--status")
            assert code == want and json.loads(out.splitlines()[0]) == data
            assert says.format(data["pid"]) in out.splitlines()[1]
    finally:
        for proc in (live, stranger):
            proc.kill()
            proc.wait()
    (mem / "clock.json").write_text(json.dumps({"pid": live.pid, "host": here, "state": "armed"}), encoding="utf-8")
    code, out = _run(capsys, "--dir", project, "--status")
    assert code == 3 and f"pid {live.pid} not running" in out


def test_check_touches_nothing(tmp_path, clock, capsys, monkeypatch):
    project = _project(tmp_path, [{"name": "soon", "every": 5}, {"name": "past", "at": _at(T0 - MIN)}])
    monkeypatch.setenv(C.SESSION_ENV, "sess-1")
    live = _fake_clock_process("--dir", str(project))
    try:
        (project / "aipack" / ".memory" / "clock.json").write_text(json.dumps(
            {"pid": live.pid, "host": socket.gethostname(), "state": "armed"}), encoding="utf-8")
        code, out = _run(capsys, "--dir", project, "--check")
        assert code == 0 and clock.naps == [] and live.poll() is None
        assert out.splitlines() == [f"session_clock: check: soon at {C.iso(T0 + 5 * MIN)} (in 5m)",
                                    _next("--dir", project, T0)]
        assert _clock_json(project) == {"pid": live.pid, "host": socket.gethostname(), "state": "armed"}
        assert not (tmp_path / "cache" / "solaris" / "sessions").exists()
        code, out = _run(capsys, "--dir", project, "--check", "--after", T0 - 2 * MIN, "--cap", "0")
        assert code == 0 and out.splitlines()[0] == f"session_clock: check: past is due now ({C.iso(T0 - MIN)})"
        assert out.splitlines()[-1] == _next("--dir", project, T0 - 2 * MIN, cap="0")
    finally:
        live.kill()
    f = _schedule(tmp_path, [{"name": "gone", "at": _at(T0 - MIN)}])
    assert _run(capsys, "--schedule", f, "--check")[0] == 1


def test_forget_ends_project_mode(tmp_path, clock, capsys, monkeypatch):
    project = _project(tmp_path, [{"name": "soon", "every": 5}])
    other = _project(tmp_path, [{"name": "soon", "every": 5}], name="other")
    sessions = tmp_path / "cache" / "solaris" / "sessions"
    code, out = _run(capsys, "--forget")
    assert code == 0 and "no usable CLAUDE_CODE_SESSION_ID" in out
    monkeypatch.setenv(C.SESSION_ENV, "sess-1")
    assert _run(capsys, "--dir", project)[0] == 0
    (sessions / f"sess-1{C.GUARD_SUFFIX}").write_text("{}", encoding="utf-8")
    code, out = _run(capsys, "--forget", "--dir", other)   # registered for another project: kept
    assert code == 1 and "nothing forgotten" in out and (sessions / "sess-1.json").exists()
    clock_before = _clock_json(project)
    code, out = _run(capsys, "--forget", "--dir", project)
    assert code == 0 and f"session sess-1 no longer runs {project.resolve()}" in out
    assert sorted(p.name for p in sessions.iterdir()) == [] and _clock_json(project) == clock_before
    code, out = _run(capsys, "--forget")
    assert code == 0 and "is not registered: nothing to forget" in out
    with pytest.raises(SystemExit) as exc:
        C.main(["--forget", "--schedule", str(project / "aipack" / ".memory" / "schedule.json")])
    assert exc.value.code == 2


def test_no_home_folder_still_arms_the_clock(tmp_path, clock, capsys, monkeypatch):
    project = _project(tmp_path, [{"name": "soon", "every": 5}])

    def no_home(*_args):
        raise RuntimeError("Could not determine home directory")

    monkeypatch.delenv("XDG_CACHE_HOME")
    monkeypatch.delenv("HOME", raising=False)
    monkeypatch.setattr(Path, "home", staticmethod(no_home))
    monkeypatch.setenv(C.SESSION_ENV, "sess-1")   # the registry cannot be written: the clock runs anyway
    code, out = _run(capsys, "--dir", project)
    assert code == 0 and "due soon at" in out and _clock_json(project)["state"] == "fired"


def _real_clock(project, tmp_path, *extra, session=None):
    """Start the clock as its own process and wait until clock.json names it."""
    env = dict(os.environ, XDG_CACHE_HOME=str(tmp_path / "cache"))
    env.pop(C.SESSION_ENV, None)
    if session:
        env[C.SESSION_ENV] = session
    proc = subprocess.Popen([sys.executable, "-m", C.MODULE, "--dir", str(project), *extra], cwd=REPO_ROOT,
                            env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        try:
            if _clock_json(project).get("pid") == proc.pid:
                return proc
        except (OSError, ValueError):
            pass
        time.sleep(0.05)
    proc.kill()
    raise AssertionError(f"the clock never armed: {proc.communicate()}")


@pytest.mark.parametrize("how", ["stop", "newer", "signal"])
def test_a_stopped_clock_exits_cleanly_and_says_why(tmp_path, capsys, monkeypatch, how):
    project = _project(tmp_path, [{"name": "daily", "every": 1440}])
    proc = _real_clock(project, tmp_path, "--cap", "0", session="sess-a")
    try:
        if how == "stop":
            assert _run(capsys, "--dir", project, "--stop")[0] == 0
        elif how == "newer":   # the same session starts a newer clock
            monkeypatch.setenv(C.SESSION_ENV, "sess-a")
            _schedule(project / "aipack" / ".memory", [{"name": "now", "at": C.iso(time.time() - 60)}])
            code, out = _run(capsys, "--dir", project, "--after", C.iso(time.time() - 120))
            assert code == 0 and out.splitlines()[0] == f"session_clock: stopped the older clock of this project " \
                                                         f"(pid {proc.pid})"
        else:
            proc.terminate()   # what a timeout wrapper or a harness kill does
        out, err = proc.communicate(timeout=15)
    finally:
        proc.kill()
    assert proc.returncode == 0, err
    if how == "stop":
        assert out.strip() == "session_clock: stopped by --stop; nothing to re-arm"
        assert _clock_json(project)["state"] == "stopped"
    elif how == "newer":
        assert out.strip() == f"session_clock: replaced by a newer clock of this session (pid {os.getpid()}); " \
                              "nothing to re-arm"
        assert _clock_json(project)["pid"] == os.getpid()
    else:
        lines = out.splitlines()
        assert lines[0].startswith("session_clock: stopped by a signal") and len(lines) == 2
        assert lines[1].startswith("session_clock: next: uv run --directory ") and lines[1].endswith(" --cap 0")
        assert _clock_json(project)["state"] == "stopped"


@pytest.mark.parametrize("taker, named", [("sess-b", "session sess-b"), (None, "a session without an id")])
def test_a_clock_taken_over_by_another_session_names_the_taker(tmp_path, capsys, monkeypatch, taker, named):
    project = _project(tmp_path, [{"name": "daily", "every": 1440}])
    proc = _real_clock(project, tmp_path, "--cap", "0", session="sess-a")
    try:
        _schedule(project / "aipack" / ".memory", [{"name": "now", "at": C.iso(time.time() - 60)}])
        if taker:
            monkeypatch.setenv(C.SESSION_ENV, taker)
        code, out = _run(capsys, "--dir", project, "--after", C.iso(time.time() - 120))
        assert code == 0 and out.splitlines()[0] == (f"session_clock: stopped the older clock of this project "
                                                     f"(pid {proc.pid}, session sess-a)")
        out, err = proc.communicate(timeout=15)
    finally:
        proc.kill()
    lines = out.splitlines()
    assert proc.returncode == 0 and len(lines) == 2, err
    assert lines[0] == (f"session_clock: taken over by a clock of {named} (pid {os.getpid()}); do not re-arm while it "
                        "holds the clock (check with --status); re-arm with the next line only once it has stopped")
    assert lines[1].startswith("session_clock: next: uv run --directory ")
    assert _clock_json(project)["session"] == taker
