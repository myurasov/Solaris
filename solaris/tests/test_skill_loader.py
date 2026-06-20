# Copyright 2026 Mihail Yurasov <me@yurasov.me>
# SPDX-License-Identifier: Apache-2.0

"""Tests for solaris.tools.skill_loader (the fail-safe trigger-based skill-loader hook)."""

from __future__ import annotations

import io
import json

from solaris.tools import skill_loader as S

SAMPLE = (
    "---\n"
    'name: ad-hoc-task\n'
    'triggers: ["new task", "research X", "set up <host/thing>", "work on <project>"]\n'
    "summary: blah\n"
    "---\n"
    "\n# ad-hoc-task\n\nDo the thing.\n"
)


def test_parse_skill_extracts_name_triggers_body():
    sk = S.parse_skill(SAMPLE)
    assert sk["name"] == "ad-hoc-task"
    assert "new task" in sk["triggers"]
    assert sk["body"].startswith("# ad-hoc-task")
    assert "Do the thing." in sk["body"]


def test_parse_skill_rejects_non_frontmatter():
    assert S.parse_skill("# just markdown\n") is None
    assert S.parse_skill("---\nname: x\n---\nbody") is None  # no triggers


def test_trigger_to_regex_handles_placeholders():
    import re

    assert re.search(S.trigger_to_regex("new task"), "start a new task now", re.I)
    assert not re.search(S.trigger_to_regex("new task"), "newtask", re.I)
    # bare X placeholder -> one argument word required
    assert re.search(S.trigger_to_regex("research X"), "research papers", re.I)
    assert not re.search(S.trigger_to_regex("research X"), "research", re.I)
    # <...> span with internal spaces collapses to one placeholder
    assert re.search(S.trigger_to_regex("set up <host/thing>"), "set up the box", re.I)
    assert re.search(S.trigger_to_regex("work on <project>"), "work on tasks/foo", re.I)


def test_match_skills_returns_matching_only():
    skills = [
        {"name": "ad-hoc-task", "triggers": ["work on <project>", "new task"], "body": "B1"},
        {"name": "release", "triggers": ["do a release"], "body": "B2"},
    ]
    m = S.match_skills("lets work on tasks/2026-06-20-foo", skills)
    assert [s["name"] for s in m] == ["ad-hoc-task"]
    assert S.match_skills("please do a release now", skills)[0]["name"] == "release"
    assert S.match_skills("refactor this function", skills) == []


def test_antitriggers_suppress_match():
    skills = [
        {"name": "ad-hoc-task", "triggers": ["work on tasks/<slug>"], "antitriggers": [], "body": "A"},
        {"name": "develop-project", "triggers": ["work on <project>"],
         "antitriggers": ["tasks/<slug>"], "body": "D"},
    ]
    # a tasks/ path: ad-hoc-task matches; develop-project is suppressed by its antitrigger
    names = [s["name"] for s in S.match_skills("lets work on tasks/2026-06-20-foo", skills)]
    assert names == ["ad-hoc-task"]
    # a real project: develop-project matches (antitrigger does not fire)
    names = [s["name"] for s in S.match_skills("work on auth-service", skills)]
    assert names == ["develop-project"]


def test_parse_skill_reads_antitriggers():
    sample = (
        "---\nname: develop-project\n"
        'triggers: ["work on <project>"]\n'
        'antitriggers: ["tasks/<slug>"]\n---\nbody\n'
    )
    sk = S.parse_skill(sample)
    assert sk["antitriggers"] == ["tasks/<slug>"]
    # absent antitriggers -> empty list, still parses
    assert S.parse_skill(SAMPLE)["antitriggers"] == []


def test_real_develop_project_suppressed_on_tasks_path():
    skills = S.discover_skills()
    names = {s["name"] for s in S.match_skills("lets work on tasks/2026-06-20-foo", skills)}
    assert "ad-hoc-task" in names
    assert "develop-project" not in names


def test_render_full_then_reminder():
    skills = [{"name": "ad-hoc-task", "triggers": ["new task"], "body": "PROCEDURE BODY"}]
    text, fresh = S.render(skills, already=set())
    assert "SOLARIS SKILL: ad-hoc-task" in text
    assert "PROCEDURE BODY" in text
    assert fresh == ["ad-hoc-task"]

    text2, fresh2 = S.render(skills, already={"ad-hoc-task"})
    assert "PROCEDURE BODY" not in text2
    assert "Already loaded this session" in text2
    assert fresh2 == []


def test_session_state_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setattr(S.tempfile, "gettempdir", lambda: str(tmp_path))
    assert S.load_injected("sess-1") == set()
    S.save_injected("sess-1", {"ad-hoc-task", "release"})
    assert S.load_injected("sess-1") == {"ad-hoc-task", "release"}
    assert S.load_injected("other") == set()


def test_emit_is_json_for_cursor_plain_for_others():
    buf = io.StringIO()
    S.emit("hello", "cursor", stream=buf)
    assert json.loads(buf.getvalue()) == {"additional_context": "hello"}

    buf = io.StringIO()
    S.emit("hello", "claude", stream=buf)
    assert buf.getvalue() == "hello"

    buf = io.StringIO()
    S.emit("", "claude", stream=buf)
    assert buf.getvalue() == ""  # nothing to inject -> emit nothing


def test_main_refuses_cli_invocation():
    assert S.main(["--anything"]) == 2


def test_real_skills_discover_and_match():
    # against the actual repo skills: an ad-hoc phrasing must pull in ad-hoc-task
    skills = S.discover_skills()
    names = {s["name"] for s in skills}
    assert "ad-hoc-task" in names
    matched = S.match_skills("lets work on tasks/2026-06-20-myspark2-install-chrome-arm64", skills)
    assert any(s["name"] == "develop-project" or s["name"] == "ad-hoc-task" for s in matched)
