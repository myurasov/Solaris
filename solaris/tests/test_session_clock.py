# Copyright 2026 Mikhail Yurasov <me@yurasov.me>
# SPDX-License-Identifier: Apache-2.0

"""Tests for solaris.tools.session_clock (the wake clock), on synthetic schedules and a fake clock."""

from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from solaris.tools import session_clock as C

T0 = int(datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc).timestamp())   # on the hour
MIN = 60
PACK = {"project": {"name": "x", "slug": "x"}, "framework_version": "0.40.0"}


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


def test_sleeps_until_the_event_then_prints_it(tmp_path, clock, capsys):
    f = _schedule(tmp_path, [{"name": "reset", "at": _at(T0 + 10 * MIN), "note": "extra keys are ignored"},
                             {"name": "later", "at": _at(T0 + 20 * MIN)}])
    code, out = _run(capsys, "--schedule", f)
    assert code == 0
    assert clock.t == T0 + 10 * MIN and max(clock.naps) <= C.NAP   # short naps on the wall clock
    assert out.splitlines() == [f"session_clock: due reset at {C.iso(T0 + 10 * MIN)}",
                                f"session_clock: next: --after {C.iso(T0 + 10 * MIN)}"]
    code, out = _run(capsys, "--schedule", f, "--after", C.iso(T0 + 10 * MIN))   # the next start
    assert code == 0 and clock.t == T0 + 20 * MIN and "due later at" in out


def test_cap_ends_the_sleep_with_a_re_arm(tmp_path, clock, capsys):
    f = _schedule(tmp_path, [{"name": "far", "at": _at(T0 + 3 * 60 * MIN)}])
    code, out = _run(capsys, "--schedule", f)
    assert code == 0 and clock.t == T0 + 25 * MIN   # default cap: 25 minutes
    assert "re-arm" in out and "next is far at" in out and "(in 2h35m)" in out
    assert out.strip().endswith(f"start again with --after {C.iso(T0)}")   # the same --after as this start
    code, out = _run(capsys, "--schedule", f, "--cap", "5")
    assert code == 0 and clock.t == T0 + 30 * MIN and "5-minute cap reached" in out


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
                                f"session_clock: next: --after {C.iso(T0)}"]


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
    ["--cap", "0"], ["--cap", "-5"], ["--cap", "nan"], ["--cap", "soon"],
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
