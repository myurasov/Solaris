# Copyright 2026 Mikhail Yurasov <me@yurasov.me>
# SPDX-License-Identifier: Apache-2.0

"""solaris.tools.plugins against what ships: every plugin's dependencies resolve, public plugins and types name no
private plugin, every plugin-provided type is valid, and kaggle:competition applies to a pack made from the
template the way create-project makes one, with a result kaggle_status.py builds a page from."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from solaris.tools import plugins as T
from solaris.tools import version as V

REPO_ROOT = Path(__file__).resolve().parents[2]
PLUGINS = REPO_ROOT / "plugins"
TEMPLATE = REPO_ROOT / "solaris" / "templates" / "ai-pack"
KAGGLE_TYPE = "kaggle:competition"
STATUS_TOOL = PLUGINS / "kaggle" / "shared" / "tools" / "kaggle_status.py"


def _entries() -> list:
    """Every plugin folder under plugins/, plus every symlink there (a private checkout, present or not)."""
    return sorted(d for d in PLUGINS.iterdir()
                  if not d.name.startswith(".") and ((d / "manifest.json").is_file() or d.is_symlink()))


def _private() -> set:
    # symlinked or untracked plugin folders are private checkouts, as in test_plugins.py; without git only the
    # symlinks count
    try:
        out = subprocess.run(["git", "ls-files", "plugins"], cwd=REPO_ROOT, capture_output=True, text=True,
                             check=True).stdout
        tracked = {line.split("/")[1] for line in out.splitlines()}
    except (OSError, subprocess.CalledProcessError):
        tracked = None
    return {d.name for d in _entries() if d.is_symlink() or (tracked is not None and d.name not in tracked)}


ENTRIES = _entries()
PRIVATE = _private()
TYPES = T.plugin_type_names()


@pytest.mark.parametrize("plugin", ENTRIES, ids=lambda d: d.name)
def test_every_plugin_resolves(plugin):
    if not (plugin / "manifest.json").is_file():
        pytest.skip(f"{plugin.name} is a private plugin that is not on this machine")
    rep = T.deps_report([plugin.name])
    assert rep["exit"] == 0, rep["problems"]


@pytest.mark.parametrize("plugin", [d for d in ENTRIES if d.name not in PRIVATE], ids=lambda d: d.name)
def test_public_plugins_name_no_private_plugin(plugin):
    manifest = json.loads((plugin / "manifest.json").read_text(encoding="utf-8"))
    required, optional, problems = T.dependencies(manifest, f"plugins/{plugin.name}/manifest.json")
    assert problems == []
    assert not {d.name for d in required + optional} & PRIVATE


@pytest.mark.parametrize("name", TYPES)
def test_every_plugin_type_is_valid(name):
    t = T.load_type(name)
    assert t.valid, t.problems
    if t.plugin not in PRIVATE:
        assert not {d.name for d in t.required + t.optional} & PRIVATE


def _render(text: str, values: dict) -> str:
    for key, value in values.items():
        text = text.replace("{{" + key + "}}", value)
    return text


def _seed(project: Path, values: dict) -> None:
    """The project as create-project step 3 leaves it in local mode: the pack's manifest, the files revs never
    renders (instructions, spec, defaults, .memory/), the root CLAUDE.md and the source/ stub, placeholders filled.
    The managed files (AGENTS.md, the persona, the pack README, rules, skills, info) come later, from revs ff."""
    pack = values["PACK"]
    pairs = [(f"ai/{name}", f"{pack}/{name}") for name in ("manifest.json", "instructions.md", "spec.md",
                                                            "defaults.json")]
    pairs += [(f"ai/.memory/{f.name}", f"{pack}/.memory/{f.name}")
              for f in sorted((TEMPLATE / "ai" / ".memory").iterdir()) if f.is_file() and not f.name.startswith(".")]
    pairs += [("CLAUDE.md", "CLAUDE.md")]
    pairs += [(f"source/{f.name}", f"source/{f.name}") for f in sorted((TEMPLATE / "source").iterdir())
              if f.is_file() and not f.name.startswith(".")]
    for src, dst in pairs:
        target = project / dst
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(_render((TEMPLATE / src).read_text(encoding="utf-8"), values), encoding="utf-8")


def _files(project: Path) -> dict:
    return {p.relative_to(project).as_posix(): p.read_bytes() for p in sorted(project.rglob("*")) if p.is_file()}


@pytest.mark.skipif(KAGGLE_TYPE not in TYPES, reason="the kaggle plugin ships no competition type")
def test_kaggle_competition_applies_to_a_template_pack(tmp_path, capsys):
    values = {"SLUG": "demo-contest", "NAME": "Demo Contest", "TYPE": KAGGLE_TYPE, "MODE": "local",
              "DESCRIPTION": "A demo competition project.", "DATE": "2026-10-08",
              "FRAMEWORK_VERSION": V.framework_version(), "PACK": "aipack", "PRIMARY": "master",
              "PRIMARY_TITLE": "Master"}
    project = tmp_path / "demo-contest"
    _seed(project, values)
    t = T.load_type(KAGGLE_TYPE)
    # the required questions answered, the optional ones left out: each renders as its default, or empty
    answers = dict(values, **{q["key"]: "example-contest" if q["key"] == "competition" else f"example {q['key']}"
                              for q in t.questions if q["required"]})
    answers_file = tmp_path / "answers.json"
    answers_file.write_text(json.dumps(answers), encoding="utf-8")
    seeded = _files(project)
    argv = ["apply-type", KAGGLE_TYPE, "--dir", str(project), "--answers", str(answers_file)]

    assert T.main(argv) == 0, capsys.readouterr().out
    out = capsys.readouterr().out
    overlay = T.type_json(t)["overlay"]
    assert overlay, "the competition type has no overlay"
    for row in overlay:
        target = _render(row["target"], values)
        assert "{{" not in target, f"{row['source']} names its path from an answer: teach this test the answer"
        verb = "append" if row["action"] == "append" and target in seeded else "create"
        assert f"{verb} {target}\n" in out
        assert (project / target).is_file()
    after = _files(project)
    assert [rel for rel, data in after.items() if b"{{" in data] == []          # every placeholder filled
    status = json.loads(after["reports/status.json"])
    assert isinstance(status, dict) and status.get("competition", "example-contest") == "example-contest"
    assert after[f"{values['PACK']}/instructions.md"].startswith(seeded[f"{values['PACK']}/instructions.md"])

    # a second run finds everything in place
    assert T.main(argv) == 0
    assert "applied kaggle:competition: 0 created, 0 appended," in capsys.readouterr().out
    assert _files(project) == after

    # the plugins the type attaches satisfy each other's required dependencies
    manifest_path = project / values["PACK"] / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["plugins"] = [{"name": row["name"], "version": V.plugin_source_version(row["name"])}
                           for row in T.type_json(t)["attach"]]
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    rep = T.check(project)
    assert rep["exit"] == 0 and rep["pack"] == values["PACK"], rep

    # the status page builds from the overlay's reports/status.json: a preview, offline, the project untouched
    before = _files(project)
    page = tmp_path / "status.html"
    env = {k: v for k, v in os.environ.items() if not k.startswith("KAGGLE_")}
    run = subprocess.run([sys.executable, str(STATUS_TOOL), "--root", str(project), "--out", str(page), "--offline"],
                         cwd=tmp_path, env=env, capture_output=True, text=True, timeout=300)
    assert run.returncode == 0, run.stdout + run.stderr
    assert "<html" in page.read_text(encoding="utf-8").lower()
    assert _files(project) == before
