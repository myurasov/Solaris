# Copyright 2026 Mikhail Yurasov <me@yurasov.me>
# SPDX-License-Identifier: Apache-2.0

"""Tests for solaris.tools.plugins: plugin dependencies, project types and their overlays (temporary trees only)."""

from __future__ import annotations

import json
import os
import stat

import pytest

from solaris.tools import plugins as T

INSTRUCTIONS = "# Instructions\n\nKeep it short.\n"

TYPE_HEAD = """---
type: competition
title: Contest
summary: One project per contest,
  ready to compete from the first hour.
defaults: {"mode": "local", "primary": "master", "roles": ["worker", "reviewer"]}
plugins: {"required": ["contest", "reporting"],
  "optional": [{"name": "sharing", "why": "share hosts"}, "cloud"]}
template: project-types/competition
questions: [{"key": "contest", "ask": "Contest URL or slug?", "required": true},
  {"key": "team-name", "ask": "Team name?", "default": "{{SLUG}}"},
  {"key": "notes", "ask": "Anything else?", "choices": ["none", "later"]}]
---
"""

TYPE_BODY = """
# Contest <!-- omit in toc -->

- [Structure](#structure)
- [Setup Steps](#setup-steps)
- [Hand-Off](#hand-off)

## Structure

One folder per experiment.

```text
## Not A Section
```

## Setup Steps

1. Sign in.
2. Read the rules.

### Details

More detail.

## Hand-Off

Run develop-project {{SLUG}}.
"""

STATUS = ('{"slug": "{{SLUG}}", "name": "{{NAME}}", "date": "{{DATE}}", "type": "{{TYPE}}", "mode": "{{MODE}}", '
          '"version": "{{FRAMEWORK_VERSION}}", "pack": "{{PACK}}", "primary": "{{PRIMARY}}", '
          '"description": "{{DESCRIPTION}}"}\n')
APPENDED = "## Contest\n\nContest: {{ANSWER_CONTEST}}\nTeam: {{ANSWER_TEAM_NAME}}\nNotes: [{{ANSWER_NOTES}}]\n" \
           "Lead: {{PRIMARY_TITLE}}\n"
LOGO = b"\xff\x00{{SLUG}}\x89"


@pytest.fixture
def root(tmp_path, monkeypatch):
    """A temporary Solaris root: plugins/, the core type folder and a few ai-pack template masters."""
    r = tmp_path / "solaris-root"
    (r / "plugins").mkdir(parents=True)
    (r / "solaris" / "templates" / "projects").mkdir(parents=True)
    masters = r / "solaris" / "templates" / "ai-pack" / "ai"
    for sub, name in (("rules", "safety.rule.md"), ("skills", "handover.skill.md"), ("info", "harnesses.md")):
        (masters / sub).mkdir(parents=True)
        (masters / sub / name).write_text("x\n", encoding="utf-8")
    monkeypatch.setattr(T, "REPO_ROOT", r)
    return r


def plugin(root, name, required=None, optional=None, deps=None, raw=None):
    """plugins/<name>/manifest.json with the given dependencies (or ``deps`` as the whole field, or ``raw`` text)."""
    folder = root / "plugins" / name
    folder.mkdir(parents=True, exist_ok=True)
    data = {"name": name, "version": "0.1.0"}
    if deps is not None:
        data["dependencies"] = deps
    elif required is not None or optional is not None:
        data["dependencies"] = {k: v for k, v in (("required", required), ("optional", optional)) if v is not None}
    (folder / "manifest.json").write_text(raw if raw is not None else json.dumps(data), encoding="utf-8")
    return folder


def write(path, text, mode=None):
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(text, bytes):
        path.write_bytes(text)
    else:
        path.write_text(text, encoding="utf-8")
    if mode is not None:
        path.chmod(mode)
    return path


def run(capsys, *argv):
    code = T.main([str(a) for a in argv])
    return code, capsys.readouterr().out


def run_json(capsys, *argv):
    code, out = run(capsys, *argv, "--json")
    return code, json.loads(out)


def graph(root):
    """alpha -> beta -> gamma (required); optional: delta (alpha, with why), epsilon (beta), ghost (absent),
    and alpha again from gamma (already attached, so never suggested)."""
    plugin(root, "alpha", required=["beta"], optional=[{"name": "delta", "why": "nicer  reports"}, "ghost"])
    plugin(root, "beta", required=[{"name": "gamma", "why": "renders"}], optional=["epsilon"])
    plugin(root, "gamma", optional=["alpha"])
    plugin(root, "delta")
    plugin(root, "epsilon")
    plugin(root, "zeta", required=["gamma"])


def contest(root, head=TYPE_HEAD, body=TYPE_BODY, overlay=True):
    """The contest plugin and its competition type: reporting needs render-kit; contest suggests browser."""
    pdir = plugin(root, "contest", optional=[{"name": "browser", "why": "read pages the CLI cannot"}])
    plugin(root, "reporting", required=["render-kit"])
    plugin(root, "render-kit")
    plugin(root, "sharing")
    plugin(root, "browser")
    write(pdir / "competition.project.md", head + body)
    if overlay:
        ov = pdir / "project-types" / "competition"
        write(ov / "{{PACK}}" / "instructions.append.md", APPENDED)
        write(ov / "{{PACK}}" / "contest-notes.append.md", "\n\nFirst notes for {{NAME}}.\n\n")
        write(ov / "reports" / "{{ANSWER_CONTEST}}" / "status.json", STATUS)
        write(ov / "tools" / "run.sh", "#!/bin/sh\necho {{SLUG}}\n", mode=0o755)
        write(ov / "data" / "logo.bin", LOGO)
        write(ov / ".DS_Store", b"junk")
    return pdir


def project(tmp_path, pack="aipack", **project_fields):
    """A project with an ai-pack manifest and the seeded instructions."""
    proj = tmp_path / "projects" / "demo"
    fields = {"name": "Demo", "slug": "demo", "type": "contest:competition", "mode": "local",
              "description": "A demo."}
    fields.update(project_fields)
    manifest = {"framework_version": "0.43.0", "project": {k: v for k, v in fields.items() if v is not None},
                "plugins": [], "created": "2026-10-08"}
    write(proj / pack / "manifest.json", json.dumps(manifest))
    write(proj / pack / "instructions.md", INSTRUCTIONS)
    return proj


def attach(proj, *entries, pack="aipack"):
    path = proj / pack / "manifest.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["plugins"] = [e if isinstance(e, dict) else {"name": e, "version": "0.1.0"} for e in entries]
    path.write_text(json.dumps(data), encoding="utf-8")


def answers(tmp_path, data):
    path = tmp_path / "answers.json"
    path.write_text(json.dumps(data) if not isinstance(data, str) else data, encoding="utf-8")
    return path


def snapshot(folder):
    return {p.relative_to(folder).as_posix(): p.read_bytes() for p in sorted(folder.rglob("*")) if p.is_file()}


ANSWERS = {"contest": "titanic", "team-name": "{{SLUG}}-team", "SLUG": "demo", "PACK": "aipack", "PRIMARY": "master"}


# deps

def test_deps_required_transitive_and_optional(root, capsys):
    graph(root)
    code, rep = run_json(capsys, "deps", "alpha")
    assert code == 0 and rep["exit"] == 0 and rep["problems"] == []
    assert rep["attach"] == ["gamma", "beta", "alpha"]                 # dependencies first
    assert rep["required"] == [{"name": "gamma", "chain": ["alpha", "beta", "gamma"]},
                               {"name": "beta", "chain": ["alpha", "beta"]}]
    by_name = {o["name"]: o for o in rep["optional"]}
    assert sorted(by_name) == ["delta", "epsilon"]                     # alpha (from gamma) is attached anyway
    assert by_name["delta"] == {"name": "delta", "why": "nicer reports", "by": ["alpha"]}
    assert by_name["epsilon"]["by"] == ["beta"]
    assert rep["unavailable"] == [{"name": "ghost", "why": "", "by": ["alpha"]}]
    code, out = run(capsys, "deps", "alpha")
    assert code == 0
    assert "attach, dependencies first: gamma, beta, alpha" in out
    assert "  gamma - alpha > beta > gamma" in out and "  delta - nicer reports (alpha)" in out
    assert "optional, not under plugins/:" in out and "ghost" in out


def test_deps_several_roots_share_dependencies(root, capsys):
    graph(root)
    code, rep = run_json(capsys, "deps", "alpha", "zeta", "alpha")
    assert code == 0 and rep["plugins"] == ["alpha", "zeta"]
    assert rep["attach"] == ["gamma", "beta", "alpha", "zeta"]
    code, rep = run_json(capsys, "deps", "beta", "alpha")
    assert rep["attach"] == ["gamma", "beta", "alpha"]
    assert [r["name"] for r in rep["required"]] == ["gamma"]          # a named plugin is not listed as required
    code, out = run(capsys, "deps", "delta")
    assert code == 0 and "required: none" in out and "optional: none" in out


@pytest.mark.parametrize("edges, cycle", [
    ({"x": ["y"], "y": ["x"]}, "x > y > x"),
    ({"x": ["y"], "y": ["w"], "w": ["y"]}, "y > w > y"),
    ({"x": ["x"]}, "x > x"),
])
def test_deps_cycle_names_the_chain(root, capsys, edges, cycle):
    for name, required in edges.items():
        plugin(root, name, required=required)
    code, out = run(capsys, "deps", "x")
    assert code == 3
    assert f"dependency cycle: {cycle}" in out
    code, rep = run_json(capsys, "deps", "x")
    assert rep["exit"] == 3 and any(cycle in p for p in rep["problems"])


def test_deps_missing_required_dependency_names_the_chain(root, capsys):
    plugin(root, "top", required=["mid"])
    plugin(root, "mid", required=["nowhere"], optional=["also-nowhere"])
    code, rep = run_json(capsys, "deps", "top")
    assert code == 3
    assert rep["missing"] == [{"name": "nowhere", "chain": ["top", "mid", "nowhere"]}]
    assert rep["problems"] == ["missing required dependency: top > mid > nowhere (no plugins/nowhere/)"]
    assert rep["attach"] == ["mid", "top"]
    code, out = run(capsys, "deps", "nope")                              # a named plugin that is not there
    assert code == 1 and "no plugin 'nope' under plugins/ (known: mid, top)" in out
    code, rep = run_json(capsys, "deps", "nope")
    assert code == 1 and rep["missing"] == [] and rep["attach"] == []   # not a dependency: a problem only
    code, rep = run_json(capsys, "deps", "plugins/mid/")                # a plugins/ prefix and a slash are fine
    assert rep["plugins"] == ["mid"] and rep["missing"][0]["chain"] == ["mid", "nowhere"]


@pytest.mark.parametrize("deps, problem", [
    (["beta"], "dependencies must be an object"),
    ({"required": "beta"}, "dependencies.required must be a list"),
    ({"required": [42]}, "dependencies.required[0]: expected a plugin name or {name, why}, got 42"),
    ({"required": [{"why": "x"}]}, "dependencies.required[0]: expected a plugin name"),
    ({"required": ["../escape"]}, "dependencies.required[0]: expected a plugin name"),
    ({"optional": [{"name": "beta", "reason": "x"}]}, "dependencies.optional[0]: unknown key(s) reason"),
    ({"optional": [{"name": "beta", "why": 3}]}, "dependencies.optional[0]: why must be a string"),
    ({"requires": ["beta"]}, "dependencies: unknown key(s) requires"),
])
def test_deps_malformed_declarations(root, capsys, deps, problem):
    plugin(root, "beta")
    plugin(root, "alpha", deps=deps)
    code, out = run(capsys, "deps", "alpha")
    assert code == 3
    assert "plugins/alpha/manifest.json" in out and problem in out


def test_deps_comments_and_unreadable_manifests(root, capsys):
    plugin(root, "beta")
    plugin(root, "alpha", deps={"_comment": "fine", "required": [{"name": "beta", "why": "renders", "_note": "ok"}]})
    code, rep = run_json(capsys, "deps", "alpha")
    assert code == 0 and rep["attach"] == ["beta", "alpha"]
    plugin(root, "broken", raw="{not json")
    plugin(root, "uses-broken", required=["broken"])
    code, out = run(capsys, "deps", "uses-broken")
    assert code == 3                                                    # a dependency that cannot be read
    assert "plugins/broken/manifest.json: cannot read" in out and "(reached by uses-broken > broken)" in out
    code, out = run(capsys, "deps", "broken")                           # a named plugin that cannot be read
    assert code == 1 and "plugins/broken/manifest.json: cannot read" in out
    plugin(root, "listy", raw='["not", "an", "object"]')
    code, rep = run_json(capsys, "deps", "listy", "beta")
    assert code == 1 and "plugins/listy/manifest.json: not a JSON object (reached by listy)" in rep["problems"]


def test_symlinked_private_plugin_counts_and_hidden_folders_do_not(root, capsys):
    real = root / "plugins" / ".repos" / "private" / "priv"
    plugin(root, ".repos/private/priv", optional=["alpha"])
    (root / "plugins" / "priv").symlink_to(real)
    plugin(root, "alpha", required=["priv"])
    (root / "plugins" / "stray-folder").mkdir()                       # no manifest: not a plugin
    (root / "plugins" / ".gitignore").write_text("*\n", encoding="utf-8")
    assert T.plugin_names() == ["alpha", "priv"]
    code, rep = run_json(capsys, "deps", "alpha")
    assert code == 0 and rep["attach"] == ["priv", "alpha"]
    write(real / "tool.project.md", TYPE_HEAD.replace("competition", "tool") + TYPE_BODY)
    assert "priv:tool" in T.plugin_type_names()


# check

def test_check_names_missing_required_dependencies_in_order(root, tmp_path, capsys):
    graph(root)
    proj = project(tmp_path)
    attach(proj, "alpha")
    code, out = run(capsys, "check", "--dir", proj)
    assert code == 3
    assert "attached: alpha" in out
    assert out.index("  gamma - alpha > beta > gamma") < out.index("  beta - alpha > beta")
    assert "result: 2 problem(s)" in out
    code, rep = run_json(capsys, "check", "--dir", proj)
    assert rep["exit"] == 3 and rep["pack"] == "aipack"
    assert [(m["name"], m["present"]) for m in rep["missing"]] == [("gamma", True), ("beta", True)]


def test_check_passes_and_lists_optional_suggestions(root, tmp_path, capsys):
    graph(root)
    proj = project(tmp_path)
    attach(proj, "alpha", "beta", {"name": "gamma", "mode": "link"}, "delta", "old-plugin")
    code, rep = run_json(capsys, "check", "--dir", proj)
    assert code == 0 and rep["missing"] == [] and rep["problems"] == []
    assert rep["attached"] == ["alpha", "beta", "gamma", "delta", "old-plugin"]   # link mode counts as attached
    assert [o["name"] for o in rep["suggest"]] == ["epsilon"]                     # delta is attached already
    assert [o["name"] for o in rep["unavailable"]] == ["ghost"]
    assert rep["unchecked"] == ["old-plugin"]                                     # its source is gone
    code, out = run(capsys, "check", "--dir", proj)
    assert code == 0
    assert "  epsilon - no reason given (beta)" in out
    assert "not checked (no source under plugins/): old-plugin" in out
    assert "result: ok" in out


def test_check_required_dependency_not_under_plugins(root, tmp_path, capsys):
    plugin(root, "top", required=["nowhere"])
    proj = project(tmp_path)
    attach(proj, "top")
    code, rep = run_json(capsys, "check", "--dir", proj)
    assert code == 3 and rep["missing"] == [{"name": "nowhere", "chain": ["top", "nowhere"], "present": False}]
    code, out = run(capsys, "check", "--dir", proj)
    assert "nowhere - top > nowhere (not under plugins/: acquire it first)" in out
    attach(proj, "top", "nowhere")                                    # attached, only its source is missing
    assert run(capsys, "check", "--dir", proj)[0] == 0


def test_check_flags_attached_plugins_with_broken_declarations(root, tmp_path, capsys):
    plugin(root, "beta")
    plugin(root, "alpha", deps=["beta"])                                # not an object
    proj = project(tmp_path)
    attach(proj, "alpha", "beta")
    code, out = run(capsys, "check", "--dir", proj)
    assert code == 3 and "plugins/alpha/manifest.json: dependencies must be an object" in out
    plugin(root, "alpha", raw="{not json")                              # a manifest that does not parse
    code, rep = run_json(capsys, "check", "--dir", proj)
    assert code == 3 and any("plugins/alpha/manifest.json: cannot read" in p for p in rep["problems"])


def test_check_cycle_is_an_error(root, tmp_path, capsys):
    plugin(root, "x", required=["y"])
    plugin(root, "y", required=["x"])
    proj = project(tmp_path)
    attach(proj, "x", "y")
    code, out = run(capsys, "check", "--dir", proj)
    assert code == 3 and "dependency cycle: x > y > x" in out


def test_check_needs_a_project_with_a_pack(root, tmp_path, capsys):
    code, out = run(capsys, "check", "--dir", tmp_path / "missing")
    assert code == 1 and "not found" in out
    (tmp_path / "empty").mkdir()
    code, out = run(capsys, "check", "--dir", tmp_path / "empty")
    assert code == 1 and "no ai-pack" in out


# types and type

def test_types_lists_core_and_plugin_types(root, capsys):
    core = root / "solaris" / "templates" / "projects"
    write(core / "cli-tool.md", "_Rev. 2_\n\n# Project type: cli-tool <!-- omit in toc -->\n\n- [Shape](#shape)\n\n"
                                "A small command-line tool, uv-based. More text\non a second line.\n\n## Shape\n\nx\n")
    write(core / "README.md", "# Not a type\n")
    contest(root)
    write(root / "plugins" / "contest" / "broken.project.md", "no frontmatter here\n")
    code, data = run_json(capsys, "types")
    assert code == 0
    assert data["core"] == [{"name": "cli-tool", "kind": "core", "file": "solaris/templates/projects/cli-tool.md",
                             "title": "cli-tool", "summary": "A small command-line tool, uv-based.",
                             "valid": True, "problems": []}]
    plugins = {t["name"]: t for t in data["plugin"]}
    assert sorted(plugins) == ["contest:broken", "contest:competition"]
    assert plugins["contest:competition"]["valid"] and plugins["contest:competition"]["title"] == "Contest"
    assert not plugins["contest:broken"]["valid"]
    code, out = run(capsys, "types")
    assert code == 0
    assert "cli-tool" in out and "A small command-line tool, uv-based." in out
    assert "contest:competition  Contest: One project per contest, ready to compete from the first hour." in out
    assert "contest:broken" in out and "INVALID" in out and "plugins type contest:broken" in out


def test_type_parses_json_values_continued_on_indented_lines(root, capsys):
    contest(root)
    code, t = run_json(capsys, "type", "contest:competition")
    assert code == 0 and t["valid"] and t["problems"] == []
    assert (t["name"], t["kind"], t["plugin"], t["type"]) == ("contest:competition", "plugin", "contest", "competition")
    assert t["file"] == "plugins/contest/competition.project.md"
    assert t["summary"] == "One project per contest, ready to compete from the first hour."   # folded
    assert t["defaults"] == {"mode": "local", "primary": "master", "roles": ["worker", "reviewer"]}
    assert t["plugins"] == {"required": [{"name": "contest", "why": ""}, {"name": "reporting", "why": ""}],
                            "optional": [{"name": "sharing", "why": "share hosts"}, {"name": "cloud", "why": ""}]}
    assert [q["key"] for q in t["questions"]] == ["contest", "team-name", "notes"]
    assert t["questions"][0]["required"] is True and t["questions"][1]["required"] is False
    assert [q["placeholder"] for q in t["questions"]] == ["{{ANSWER_CONTEST}}", "{{ANSWER_TEAM_NAME}}",
                                                          "{{ANSWER_NOTES}}"]
    assert t["questions"][1]["default"] == "{{SLUG}}" and t["questions"][2]["choices"] == ["none", "later"]
    # the providing plugin and the required ones, each after its own dependencies
    assert [a["name"] for a in t["attach"]] == ["contest", "render-kit", "reporting"]
    assert {s["name"]: s["by"] for s in t["suggest"]} == {"sharing": ["contest:competition"], "browser": ["contest"]}
    assert [u["name"] for u in t["unavailable"]] == ["cloud"]
    assert t["template"] == "project-types/competition"
    assert {(o["action"], o["target"]) for o in t["overlay"]} == {
        ("append", "{{PACK}}/instructions.md"), ("append", "{{PACK}}/contest-notes.md"),
        ("create", "reports/{{ANSWER_CONTEST}}/status.json"), ("create", "tools/run.sh"),
        ("create", "data/logo.bin")}                                    # .DS_Store is skipped
    assert t["structure"].startswith("One folder per experiment.") and "## Not A Section" in t["structure"]
    assert t["setup_steps"].startswith("1. Sign in.") and "### Details" in t["setup_steps"]
    assert t["hand_off"] == "Run develop-project {{SLUG}}."
    code, out = run(capsys, "type", "contest:competition")
    assert code == 0 and out.rstrip().endswith("valid")
    assert "attach, dependencies first: contest, render-kit, reporting" in out
    assert "  contest (required): Contest URL or slug? -> {{ANSWER_CONTEST}}" in out
    assert "sections: Structure, Setup Steps, Hand-Off" in out


def test_type_default_overlay_folder_and_core_types(root, capsys):
    contest(root, head=TYPE_HEAD.replace("template: project-types/competition\n", ""))
    code, t = run_json(capsys, "type", "contest:competition")
    assert code == 0 and t["template"] == "project-types/competition" and t["overlay"]
    plugin(root, "bare")
    write(root / "plugins" / "bare" / "plain.project.md",
          "---\ntype: plain\ntitle: Plain\nsummary: No overlay.\n---\n\n## Structure\n\nx\n\n## Setup Steps\n\n"
          "None.\n\n## Hand-Off\n\nDone.\n")
    code, t = run_json(capsys, "type", "bare:plain")
    assert code == 0 and t["template"] is None and t["overlay"] == [] and t["questions"] == []
    assert [a["name"] for a in t["attach"]] == ["bare"]
    write(root / "solaris" / "templates" / "projects" / "cli-tool.md", "# Project type: cli-tool\n\nA tool.\n")
    code, t = run_json(capsys, "type", "cli-tool")
    assert code == 0 and t == {"name": "cli-tool", "kind": "core", "file": "solaris/templates/projects/cli-tool.md",
                               "title": "cli-tool", "summary": "A tool.", "valid": True, "problems": []}


def test_type_not_found(root, capsys):
    contest(root)
    code, out = run(capsys, "type", "nope")
    assert code == 1 and "no project type 'nope'" in out and "contest:competition" in out
    code, out = run(capsys, "type", "contest")
    assert code == 1 and "plugin contest provides contest:competition" in out
    code, out = run(capsys, "type", "contest:nope")
    assert code == 1 and "no project type 'contest:nope'" in out
    code, out = run(capsys, "type", "ghost:competition")
    assert code == 1 and "no plugin 'ghost' under plugins/" in out
    assert run(capsys, "type", "contest:../contest/competition")[0] == 1


def _without_section(body, title):
    head, _, rest = body.partition(f"## {title}\n")
    return head + rest[rest.find("\n## ") + 1:] if "\n## " in rest else head


@pytest.mark.parametrize("edit, problem", [
    (lambda h, b: (h.replace("title:", "owner: me\ntitle:"), b), "unknown frontmatter key(s) owner"),
    (lambda h, b: (h, _without_section(b, "Hand-Off")), "missing section ## Hand-Off"),
    (lambda h, b: (h, b.replace("1. Sign in.\n2. Read the rules.\n\n### Details\n\nMore detail.\n", "")),
     "section ## Setup Steps is empty"),
    (lambda h, b: (h, b + "\n## Structure\n\nAgain.\n"), "section ## Structure appears more than once"),
    (lambda h, b: (h.replace('"reporting"]', '"reporting", "nowhere"]'), b),
     "plugins.required: no plugin 'nowhere' under plugins/"),
    (lambda h, b: (h.replace("project-types/competition", "project-types/nope"), b),
     "template: no overlay folder plugins/contest/project-types/nope"),
    (lambda h, b: (h.replace("project-types/competition", "../elsewhere"), b),
     "must be a folder under project-types/ in the plugin"),
    (lambda h, b: (h.replace("template: project-types/competition", "template: ."), b),
     "template: '.' must be a folder under project-types/ in the plugin"),
    (lambda h, b: (h.replace("template: project-types/competition", "template: shared"), b),
     "template: 'shared' must be a folder under project-types/ in the plugin"),
    (lambda h, b: (h.replace("template: project-types/competition", "template: project-types"), b),
     "template: 'project-types' must be a folder under project-types/ in the plugin"),
    (lambda h, b: (h.replace("template: project-types/competition", "template: project-types/../shared"), b),
     "must be a folder under project-types/ in the plugin"),
    (lambda h, b: (h.replace('{"key": "notes", "ask": "Anything else?", "choices": ["none", "later"]}]',
                             '{"key": "notes", "ask": "Anything else?"'), b),
     "questions: expected a JSON list (it does not parse as JSON"),
    (lambda h, b: (h.replace("type: competition", "type: contest"), b),
     "type: 'contest' does not match the file name (competition.project.md)"),
    (lambda h, b: (h.replace("title: Contest\n", ""), b), "title: required"),
    (lambda h, b: (h.replace('"key": "notes"', '"key": "Notes"'), b), "key must be lower case"),
    (lambda h, b: (h.replace('"key": "notes"', '"key": "team_name"'), b),
     "gives the same placeholder {{ANSWER_TEAM_NAME}} as 'team-name'"),
    (lambda h, b: (h.replace('"ask": "Team name?"', '"ask": ""'), b), "question 'team-name': ask must be"),
    (lambda h, b: (h.replace('"required": true', '"required": "yes"'), b), "required must be true or false"),
    (lambda h, b: (h.replace('"mode": "local"', '"mode": "cloud"'), b), "defaults.mode must be one of"),
    (lambda h, b: (h.replace('"mode": "local"', '"group": "my"'), b), "defaults: unknown key(s) group"),
    (lambda h, b: (h.replace('["worker", "reviewer"]', '["worker", "master"]'), b),
     "defaults.roles must not name the primary persona"),
    (lambda h, b: (h.replace('"primary": "master"', '"primary": "Lead"'), b), "defaults.primary must be a role"),
    (lambda h, b: (h.replace("plugins: {", "plugins: {\"needs\": [], "), b), "plugins: unknown key(s) needs"),
    (lambda h, b: (h.replace("  ready to compete", " ready to compete"), b), "expected 'key: value'"),
    (lambda h, b: (h.replace("---\ntype:", "---\n  stray\ntype:"), b), "an indented line with no key above it"),
    (lambda h, b: (h.replace("title: Contest", "title: Contest\ntitle: Again"), b), "duplicate key 'title'"),
    (lambda h, b: (h[4:], b), "no frontmatter"),
])
def test_type_invalid_definitions(root, capsys, edit, problem):
    head, body = edit(TYPE_HEAD, TYPE_BODY)
    contest(root, head=head, body=body)
    code, out = run(capsys, "type", "contest:competition")
    assert code == 3 and "INVALID" in out
    assert problem in out
    code, t = run_json(capsys, "type", "contest:competition")
    assert code == 3 and not t["valid"] and any(problem in p for p in t["problems"])


def test_type_template_symlinked_out_of_project_types(root, capsys):
    pdir = contest(root)
    ov = pdir / "project-types" / "competition"
    ov.rename(pdir / "shared-overlay")
    ov.symlink_to(pdir / "shared-overlay")                            # project-types/competition -> ../shared-overlay
    for head in (TYPE_HEAD, TYPE_HEAD.replace("template: project-types/competition\n", "")):
        contest(root, head=head, overlay=False)
        code, t = run_json(capsys, "type", "contest:competition")
        assert code == 3
        assert "template: plugins/contest/project-types/competition leads out of the plugin's project-types/ " \
               "through a symlink" in t["problems"]
    ov.unlink()                                                        # a symlink that stays inside is fine
    (pdir / "shared-overlay").rename(pdir / "project-types" / "real")
    ov.symlink_to(pdir / "project-types" / "real")
    code, t = run_json(capsys, "type", "contest:competition")
    assert code == 0 and t["overlay"], t["problems"]
    contest(root, head=TYPE_HEAD.replace("project-types/competition", "./project-types/competition/"), overlay=False)
    code, t = run_json(capsys, "type", "contest:competition")
    assert code == 0 and t["template"] == "project-types/competition"


def test_type_parses_crlf_files(root, capsys):
    contest(root, head=TYPE_HEAD.replace("\n", "\r\n"), body=TYPE_BODY.replace("\n", "\r\n"))
    code, t = run_json(capsys, "type", "contest:competition")
    assert code == 0, t["problems"]
    assert [q["key"] for q in t["questions"]] == ["contest", "team-name", "notes"]
    assert t["summary"] == "One project per contest, ready to compete from the first hour."
    assert t["hand_off"] == "Run develop-project {{SLUG}}."


def test_type_unterminated_frontmatter_is_one_problem(root, capsys):
    contest(root, head=TYPE_HEAD[:-4], body="")
    code, t = run_json(capsys, "type", "contest:competition")
    assert code == 3 and t["problems"] == ["unterminated frontmatter: no closing --- line", "type: required",
                                           "title: required", "summary: required"]


@pytest.mark.parametrize("name, text, problem", [
    ("notes/{{ANSWER_NOPE}}.md", "x\n", "{{ANSWER_NOPE}} names no question"),
    ("notes/a.md", "Hello {{SLUGG}}\n", "{{SLUGG}} is not a placeholder apply-type fills"),
    ("{{PACK}}/README.md", "x\n", "writes {{PACK}}/README.md, a file the framework manages"),
    ("AGENTS.append.md", "x\n", "writes AGENTS.md, a file the framework manages"),
    ("{{PACK}}/rules/safety.rule.md", "x\n", "writes {{PACK}}/rules/safety.rule.md, a file the framework"),
    ("{{PACK}}/{{PRIMARY}}.agent.md", "x\n", "a file the framework manages"),
    ("{{PACK}}/plugins/contest/x.md", "x\n", "a file the framework manages"),
    # the default macOS filesystem ignores case: these land on the managed files too
    ("agents.append.md", "x\n", "writes agents.md, a file the framework manages"),
    ("{{PACK}}/readme.append.md", "x\n", "writes {{PACK}}/readme.md, a file the framework manages"),
    ("{{PACK}}/Plugins/kit/extra.md", "x\n", "writes {{PACK}}/Plugins/kit/extra.md, a file the framework manages"),
    ("{{PACK}}/Rules/Safety.Rule.md", "x\n", "writes {{PACK}}/Rules/Safety.Rule.md, a file the framework manages"),
    ("{{PACK}}/MANIFEST.json", "{}\n", "a file the framework manages"),
    ("{{PACK}}/Engineer.agent.md", "x\n", "a file the framework manages"),
    ("aipack/x.md", "x\n", "name the ai-pack folder {{PACK}}, not aipack"),
    ("AIPack/x.md", "x\n", "name the ai-pack folder {{PACK}}, not AIPack"),
    # an overlay file that can never apply: its path is another one's
    ("{{PACK}}/Instructions.md", "x\n", "overlay {{PACK}}/instructions.append.md: writes {{PACK}}/instructions.md, "
                                        "where {{PACK}}/Instructions.md writes too"),
    ("{{PACK}}/contest-notes.md/b.txt", "x\n",
     "overlay {{PACK}}/contest-notes.md/b.txt: writes {{PACK}}/contest-notes.md/b.txt, where "
     "{{PACK}}/contest-notes.append.md"),
    ("{{PACK}}/bin.append.md", b"\xff\xfe", "an .append.md file must be UTF-8 text"),
    ("reports/limits.json", '{"budget": {{ANSWER_NOTES}}}\n', "not valid JSON with its placeholders inside strings"),
])
def test_type_invalid_overlays(root, capsys, name, text, problem):
    pdir = contest(root)
    write(pdir / "project-types" / "competition" / name, text)
    code, t = run_json(capsys, "type", "contest:competition")
    assert code == 3 and any(problem in p for p in t["problems"]), t["problems"]


def test_type_required_plugin_with_broken_dependencies(root, capsys):
    contest(root)
    plugin(root, "reporting", required=["render-kit", "nowhere"])
    code, t = run_json(capsys, "type", "contest:competition")
    assert code == 3
    assert "plugins: missing required dependency: reporting > nowhere (no plugins/nowhere/)" in t["problems"]
    plugin(root, "render-kit", required=["reporting"])
    code, t = run_json(capsys, "type", "contest:competition")
    assert any("dependency cycle: reporting > render-kit > reporting" in p for p in t["problems"])


def test_parse_frontmatter_values():
    fm = T.parse_frontmatter('_Rev. 2_\n\n---\ntitle: "Quoted: yes"\ncount: 3\nlist: [1,\n  2]\nplain: one\n'
                             "    two\n# a comment\nempty:\nlater:\n  {\"a\": [true,\n     null]}\n---\nbody\n")
    assert fm.problems == []
    assert fm.values == {"title": "Quoted: yes", "count": 3, "list": [1, 2], "plain": "one two", "empty": "",
                         "later": {"a": [True, None]}}
    assert set(fm.errors) == {"plain", "empty"} and fm.texts["list"] == "[1, 2]"
    assert fm.body == "body\n"


def test_body_sections_skip_fenced_headings():
    body = "# Title\n\nintro\n\n## One\n\na\n\n~~~\n## Fenced\n~~~\n\n### Sub\n\nb\n\n## Two <!-- note -->\n\nc\n"
    assert T.body_sections(body) == [("One", "a\n\n~~~\n## Fenced\n~~~\n\n### Sub\n\nb"), ("Two", "c")]


# apply-type

def test_apply_type_renders_placeholders_and_appends(root, tmp_path, capsys):
    contest(root)
    proj = project(tmp_path)
    ans = answers(tmp_path, dict(ANSWERS, colour="blue"))
    code, out = run(capsys, "apply-type", "contest:competition", "--dir", proj, "--answers", ans)
    assert code == 0, out
    assert "note: not asked by contest:competition, ignored: colour" in out
    for line in ("append aipack/instructions.md", "create aipack/contest-notes.md",
                 "create reports/titanic/status.json", "create tools/run.sh", "create data/logo.bin",
                 "applied contest:competition: 4 created, 1 appended, 0 unchanged"):
        assert line in out
    # appended after a blank line; the unanswered optional question renders empty; answers fill placeholders
    assert (proj / "aipack" / "instructions.md").read_text(encoding="utf-8") == (
        INSTRUCTIONS + "\n## Contest\n\nContest: titanic\nTeam: demo-team\nNotes: []\nLead: Master\n")
    # an .append.md file whose target is missing creates it (NAME comes from the project's manifest)
    assert (proj / "aipack" / "contest-notes.md").read_text(encoding="utf-8") == "First notes for Demo.\n"
    assert json.loads((proj / "reports" / "titanic" / "status.json").read_text(encoding="utf-8")) == {
        "slug": "demo", "name": "Demo", "date": "2026-10-08", "type": "contest:competition", "mode": "local",
        "version": "0.43.0", "pack": "aipack", "primary": "master", "description": "A demo."}
    assert (proj / "tools" / "run.sh").read_text(encoding="utf-8") == "#!/bin/sh\necho demo\n"
    assert os.stat(proj / "tools" / "run.sh").st_mode & stat.S_IXUSR
    assert (proj / "data" / "logo.bin").read_bytes() == LOGO              # binary: copied as it is
    assert not (proj / ".DS_Store").exists()
    # a second run finds everything in place and writes nothing
    before = snapshot(proj)
    code, out = run(capsys, "apply-type", "contest:competition", "--dir", proj, "--answers", ans)
    assert code == 0 and "applied contest:competition: 0 created, 0 appended, 5 unchanged" in out
    assert snapshot(proj) == before


def test_apply_type_escapes_values_in_json_files(root, tmp_path, capsys):
    contest(root)
    proj = project(tmp_path)
    tricky = 'Say "hi" \\ then\ttab, naïve'
    code, out = run(capsys, "apply-type", "contest:competition", "--dir", proj,
                    "--answers", answers(tmp_path, dict(ANSWERS, DESCRIPTION=tricky)))
    assert code == 0, out
    assert json.loads((proj / "reports" / "titanic" / "status.json").read_text(encoding="utf-8"))["description"] \
        == tricky                                                         # still valid JSON, value intact
    assert "naïve" in (proj / "reports" / "titanic" / "status.json").read_text(encoding="utf-8")


def test_apply_type_executable_files_follow_the_umask(root, tmp_path, capsys):
    contest(root)
    proj = project(tmp_path)
    old = os.umask(0o077)
    try:
        code, out = run(capsys, "apply-type", "contest:competition", "--dir", proj,
                        "--answers", answers(tmp_path, ANSWERS))
    finally:
        os.umask(old)
    assert code == 0, out
    assert stat.S_IMODE(os.stat(proj / "tools" / "run.sh").st_mode) == 0o700
    assert stat.S_IMODE(os.stat(proj / "data" / "logo.bin").st_mode) == 0o600


def test_apply_type_dry_run_writes_nothing(root, tmp_path, capsys):
    contest(root)
    proj = project(tmp_path)
    before = snapshot(proj)
    code, out = run(capsys, "apply-type", "contest:competition", "--dir", proj,
                    "--answers", answers(tmp_path, ANSWERS), "--dry-run")
    assert code == 0
    assert "create reports/titanic/status.json" in out and "append aipack/instructions.md" in out
    assert "dry run, nothing written: 4 to create, 1 to append, 0 unchanged" in out
    assert snapshot(proj) == before


def test_apply_type_refuses_to_overwrite_and_writes_nothing(root, tmp_path, capsys):
    contest(root)
    proj = project(tmp_path)
    write(proj / "reports" / "titanic" / "status.json", "{}\n")
    before = snapshot(proj)
    code, out = run(capsys, "apply-type", "contest:competition", "--dir", proj, "--answers", answers(tmp_path, ANSWERS))
    assert code == 3
    assert "refused: reports/titanic/status.json exists and differs (a created file never overwrites one)" in out
    assert "nothing written" in out and snapshot(proj) == before          # not even the append happened
    # the same content already there is no overwrite
    rendered = STATUS.replace("{{SLUG}}", "demo").replace("{{NAME}}", "Demo").replace("{{DATE}}", "2026-10-08") \
        .replace("{{TYPE}}", "contest:competition").replace("{{MODE}}", "local").replace("{{PACK}}", "aipack") \
        .replace("{{FRAMEWORK_VERSION}}", "0.43.0").replace("{{PRIMARY}}", "master") \
        .replace("{{DESCRIPTION}}", "A demo.")
    write(proj / "reports" / "titanic" / "status.json", rendered)
    code, out = run(capsys, "apply-type", "contest:competition", "--dir", proj, "--answers", answers(tmp_path, ANSWERS))
    assert code == 0 and "unchanged reports/titanic/status.json" in out


@pytest.mark.parametrize("change, problem", [
    ({"contest": None}, "question 'contest' is required and has no answer"),
    ({"contest": "  "}, "question 'contest' is required and has no answer"),
    ({"contest": "../../etc"}, "not a plain path inside the project"),
    ({"PACK": "ai"}, "PACK is 'ai' in the answers, but the project's ai-pack folder is aipack"),
    ({"team-name": "{{NOPE}}-{{DESCRIPTION}}"}, None),
])
def test_apply_type_refusals_on_answers(root, tmp_path, capsys, change, problem):
    contest(root)
    proj = project(tmp_path)
    data = {k: v for k, v in dict(ANSWERS, **change).items() if v is not None}
    before = snapshot(proj)
    code, out = run(capsys, "apply-type", "contest:competition", "--dir", proj, "--answers", answers(tmp_path, data))
    if problem is None:                       # an unknown placeholder in an answer stays as written
        assert code == 0 and "Team: {{NOPE}}-A demo." in (proj / "aipack" / "instructions.md").read_text()
        return
    assert code == 3 and problem in out and "nothing written" in out
    assert snapshot(proj) == before


def test_apply_type_placeholder_without_a_value(root, tmp_path, capsys):
    contest(root)
    proj = project(tmp_path, description=None)
    code, out = run(capsys, "apply-type", "contest:competition", "--dir", proj, "--answers", answers(tmp_path, ANSWERS))
    assert code == 3 and "reports/{{ANSWER_CONTEST}}/status.json: no value for {{DESCRIPTION}}" in out
    data = dict(ANSWERS, DESCRIPTION="", **{"team-name": "{{MODE}} {{DESCRIPTION}}"})
    proj2 = tmp_path / "second"
    proj2.mkdir()
    code, out = run(capsys, "apply-type", "contest:competition", "--dir", project(proj2, description=None),
                    "--answers", answers(tmp_path, data))
    assert code == 0, out                     # an empty value is a value


def test_apply_type_managed_and_colliding_targets(root, tmp_path, capsys):
    pdir = plugin(root, "kit")
    write(pdir / "lab.project.md",
          '---\ntype: lab\ntitle: Lab\nsummary: A lab.\nquestions: [{"key": "role", "ask": "Role?"},\n'
          '  {"key": "page", "ask": "Page?"}]\n---\n\n## Structure\n\nx\n\n## Setup Steps\n\nNone.\n\n'
          '## Hand-Off\n\nDone.\n')
    ov = pdir / "project-types" / "lab"
    write(ov / "{{PACK}}" / "{{ANSWER_ROLE}}.agent.md", "brief\n")
    write(ov / "notes" / "{{ANSWER_PAGE}}.md", "page\n")
    write(ov / "notes" / "fixed.md", "fixed\n")
    proj = project(tmp_path)
    ok = {"role": "worker", "page": "first", "PRIMARY": "master"}
    code, out = run(capsys, "apply-type", "kit:lab", "--dir", proj, "--answers", answers(tmp_path, ok))
    assert code == 0 and "create aipack/worker.agent.md" in out
    proj2 = project(tmp_path / "two")
    bad = {"role": "master", "page": "fixed", "PRIMARY": "master"}
    code, out = run(capsys, "apply-type", "kit:lab", "--dir", proj2, "--answers", answers(tmp_path, bad))
    assert code == 3
    assert "writes aipack/master.agent.md, a file the framework manages" in out
    assert "notes/{{ANSWER_PAGE}}.md writes notes/fixed.md, which collides with notes/fixed.md" in out
    write(proj2 / "notes", "a file where a folder should be\n")
    code, out = run(capsys, "apply-type", "kit:lab", "--dir", proj2,
                    "--answers", answers(tmp_path, dict(bad, role="worker", page="other")))
    assert code == 3 and "notes exists and is not a folder" in out


LAB = ('---\ntype: lab\ntitle: Lab\nsummary: A lab.\n'
       'questions: [{"key": "x", "ask": "X?"}, {"key": "y", "ask": "Y?"},\n'
       '  {"key": "file", "ask": "File?"}, {"key": "role", "ask": "Role?"}, {"key": "dir", "ask": "Folder?"}]\n---\n\n'
       '## Structure\n\nx\n\n## Setup Steps\n\nNone.\n\n## Hand-Off\n\nDone.\n')


def lab(root):
    """kit:lab, whose overlay paths are made of answers, so only apply-type can see what they name."""
    pdir = plugin(root, "kit")
    write(pdir / "lab.project.md", LAB)
    ov = pdir / "project-types" / "lab"
    write(ov / "notes" / "{{ANSWER_X}}.md", "x\n")
    write(ov / "notes" / "{{ANSWER_Y}}.md", "y\n")
    write(ov / "{{ANSWER_FILE}}.append.md", "more\n")
    write(ov / "{{PACK}}" / "{{ANSWER_ROLE}}.agent.md", "brief\n")
    write(ov / "{{PACK}}" / "{{ANSWER_DIR}}" / "x.md", "x\n")
    return pdir


LAB_OK = {"x": "Foo", "y": "bar", "file": "notes-index", "role": "worker", "dir": "notes", "PRIMARY": "master"}


@pytest.mark.parametrize("change, problem", [
    ({"y": "foo"}, "notes/{{ANSWER_Y}}.md writes notes/foo.md, which collides with notes/Foo.md from "
                   "notes/{{ANSWER_X}}.md (names that differ only in case are one file where the filesystem ignores "
                   "case)"),
    ({"file": "agents"}, "{{ANSWER_FILE}}.append.md: writes agents.md, a file the framework manages"),
    ({"file": "AGENTS"}, "{{ANSWER_FILE}}.append.md: writes AGENTS.md, a file the framework manages"),
    ({"role": "MASTER"}, "writes aipack/MASTER.agent.md, a file the framework manages"),
    ({"role": "Engineer"}, "writes aipack/Engineer.agent.md, a file the framework manages"),
    ({"dir": "Plugins"}, "writes aipack/Plugins/x.md, a file the framework manages"),
    ({"dir": "SKILLS", "file": "x"}, None),                            # not a master's name: a project-local skill
])
def test_apply_type_targets_compare_case_folded(root, tmp_path, capsys, change, problem):
    lab(root)
    assert run(capsys, "type", "kit:lab")[0] == 0                      # only the answers make these paths
    proj = project(tmp_path)
    before = snapshot(proj)
    code, out = run(capsys, "apply-type", "kit:lab", "--dir", proj,
                    "--answers", answers(tmp_path, dict(LAB_OK, **change)))
    if problem is None:
        assert code == 0 and (proj / "aipack" / "SKILLS" / "x.md").exists(), out
        return
    assert code == 3 and problem in out and "nothing written" in out
    assert snapshot(proj) == before


def test_apply_type_refuses_targets_that_leave_the_project(root, tmp_path, capsys):
    contest(root)
    proj = project(tmp_path)
    outside = tmp_path / "outside"
    write(outside / "instructions.md", "someone else's\n")
    (proj / "reports").symlink_to(outside)                             # a symlinked folder
    (proj / "aipack" / "instructions.md").unlink()
    (proj / "aipack" / "instructions.md").symlink_to(outside / "instructions.md")   # a symlinked file
    before, before_outside = snapshot(proj), snapshot(outside)
    code, out = run(capsys, "apply-type", "contest:competition", "--dir", proj, "--answers", answers(tmp_path, ANSWERS))
    assert code == 3 and "nothing written" in out
    assert "reports/titanic/status.json leads outside the project through a symlink" in out
    assert "aipack/instructions.md leads outside the project through a symlink" in out
    assert snapshot(proj) == before and snapshot(outside) == before_outside
    # links that stay inside the project are followed
    (proj / "reports").unlink()
    (proj / "data" / "reports").mkdir(parents=True)
    (proj / "reports").symlink_to(proj / "data" / "reports")
    (proj / "aipack" / "instructions.md").unlink()
    write(proj / "shared-instructions.md", INSTRUCTIONS)
    (proj / "aipack" / "instructions.md").symlink_to(proj / "shared-instructions.md")
    code, out = run(capsys, "apply-type", "contest:competition", "--dir", proj, "--answers", answers(tmp_path, ANSWERS))
    assert code == 0, out
    assert (proj / "data" / "reports" / "titanic" / "status.json").is_file()
    assert "Contest: titanic" in (proj / "shared-instructions.md").read_text(encoding="utf-8")
    assert snapshot(outside) == before_outside


def test_apply_type_optional_questions_take_their_defaults(root, tmp_path, capsys):
    contest(root)
    proj = project(tmp_path)
    plain = {k: v for k, v in ANSWERS.items() if k != "team-name"}
    code, out = run(capsys, "apply-type", "contest:competition", "--dir", proj, "--answers", answers(tmp_path, plain))
    assert code == 0, out
    text = (proj / "aipack" / "instructions.md").read_text(encoding="utf-8")
    assert "Team: demo\n" in text                                       # the default {{SLUG}}, filled
    assert "Notes: []\n" in text                                        # no default: empty
    for value, shown in ((None, "demo"), ("", "")):                     # null is no answer; "" is an answer
        proj2 = project(tmp_path / f"p{shown or 'empty'}")
        code, out = run(capsys, "apply-type", "contest:competition", "--dir", proj2,
                        "--answers", answers(tmp_path, dict(ANSWERS, **{"team-name": value})))
        assert code == 0 and f"Team: {shown}\n" in (proj2 / "aipack" / "instructions.md").read_text(encoding="utf-8")
    pdir = plugin(root, "kit")
    write(pdir / "dflt.project.md",
          '---\ntype: dflt\ntitle: Defaults\nsummary: Defaults.\nquestions: [{"key": "count", "ask": "Count?", '
          '"default": 3},\n  {"key": "flag", "ask": "Flag?", "default": true},\n  {"key": "lead", "ask": "Lead?", '
          '"required": true, "default": "{{SLUG}}"}]\n---\n\n## Structure\n\nx\n\n## Setup Steps\n\nNone.\n\n'
          '## Hand-Off\n\nDone.\n')
    write(pdir / "project-types" / "dflt" / "values.md", "{{ANSWER_COUNT}} {{ANSWER_FLAG}} {{ANSWER_LEAD}}\n")
    proj3 = project(tmp_path / "p3")
    code, out = run(capsys, "apply-type", "kit:dflt", "--dir", proj3, "--answers", answers(tmp_path, {}))
    assert code == 3 and "question 'lead' is required and has no answer" in out   # required: the default is no answer
    code, out = run(capsys, "apply-type", "kit:dflt", "--dir", proj3, "--answers", answers(tmp_path, {"lead": "me"}))
    assert code == 0 and (proj3 / "values.md").read_text(encoding="utf-8") == "3 true me\n"


def test_apply_type_append_target_must_be_a_text_file(root, tmp_path, capsys):
    contest(root)
    proj = project(tmp_path)
    (proj / "aipack" / "contest-notes.md").mkdir()
    code, out = run(capsys, "apply-type", "contest:competition", "--dir", proj, "--answers", answers(tmp_path, ANSWERS))
    assert code == 3 and "aipack/contest-notes.md exists and is not a readable text file to append to" in out


def test_apply_type_usage_and_input_errors(root, tmp_path, capsys):
    contest(root)
    proj = project(tmp_path)
    ans = answers(tmp_path, ANSWERS)
    assert run(capsys, "apply-type", "python-cli", "--dir", proj, "--answers", ans)[0] == 2
    code, out = run(capsys, "apply-type", "contest:competition", "--dir", tmp_path / "nowhere", "--answers", ans)
    assert code == 1 and "not found" in out
    code, out = run(capsys, "apply-type", "contest:competition", "--dir", proj, "--answers", tmp_path / "none.json")
    assert code == 1 and "cannot read" in out
    code, out = run(capsys, "apply-type", "contest:competition", "--dir", proj, "--answers", answers(tmp_path, "[1]"))
    assert code == 1 and "must hold a JSON object" in out
    write(root / "plugins" / "contest" / "competition.project.md", TYPE_HEAD + "\n## Structure\n\nx\n")
    code, out = run(capsys, "apply-type", "contest:competition", "--dir", proj, "--answers", ans)
    assert code == 3 and "refused: contest:competition is not a valid type" in out
    assert (proj / "aipack" / "instructions.md").read_text(encoding="utf-8") == INSTRUCTIONS


def test_apply_type_without_overlay_still_checks_dir_and_answers(root, tmp_path, capsys):
    contest(root, head=TYPE_HEAD.replace("template: project-types/competition\n", ""), overlay=False)
    proj = project(tmp_path)
    code, out = run(capsys, "apply-type", "contest:competition", "--dir", tmp_path / "nowhere",
                    "--answers", answers(tmp_path, ANSWERS))
    assert code == 1 and "not found" in out
    code, out = run(capsys, "apply-type", "contest:competition", "--dir", proj, "--answers", answers(tmp_path, {}))
    assert code == 3 and "question 'contest' is required and has no answer" in out
    code, out = run(capsys, "apply-type", "contest:competition", "--dir", proj, "--answers", answers(tmp_path, ANSWERS))
    assert code == 0 and "has no overlay: nothing to apply" in out
    assert (proj / "aipack" / "instructions.md").read_text(encoding="utf-8") == INSTRUCTIONS
