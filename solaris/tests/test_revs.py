# Copyright 2026 Mihail Yurasov <me@yurasov.me>
# SPDX-License-Identifier: Apache-2.0

"""Tests for solaris.tools.revs."""

from __future__ import annotations

import json

from solaris.tools import revs as R


def test_md_marker_roundtrip_and_hash_excludes_rev():
    base = "# Title\n\nsome body\n"
    a = R.set_rev(base, ".md", 1)
    b = R.set_rev(base, ".md", 7)
    assert R.read_rev(a, ".md") == 1
    assert R.read_rev(b, ".md") == 7
    assert b.startswith("_Rev. 7_")
    # rev bump must NOT change the content hash
    assert R.content_hash(a, ".md") == R.content_hash(b, ".md")
    # a real content change must change the hash
    assert R.content_hash(a, ".md") != R.content_hash(R.set_rev("# Title\n\nedited\n", ".md", 1), ".md")


def test_py_marker():
    t = R.set_rev("x = 1\n", ".py", 3)
    assert t.startswith("# rev. 3")
    assert R.read_rev(t, ".py") == 3
    new, rev = R.bump_text(t, ".py")
    assert rev == 4 and R.read_rev(new, ".py") == 4
    assert R.content_hash(t, ".py") == R.content_hash(new, ".py")


def test_json_marker():
    t = json.dumps({"name": "x", "k": 2})
    t1 = R.set_rev(t, ".json", 1)
    assert json.loads(t1)["_rev"] == 1
    assert list(json.loads(t1))[0] == "_rev"  # _rev is the first field
    t2 = R.set_rev(t1, ".json", 9)
    assert R.read_rev(t2, ".json") == 9
    assert R.content_hash(t1, ".json") == R.content_hash(t2, ".json")  # rev excluded
    assert R.content_hash(t2, ".json") != R.content_hash(R.set_rev(json.dumps({"name": "y"}), ".json", 9), ".json")


def test_bump_from_unmarked_starts_at_one():
    new, rev = R.bump_text("# Doc\n\nbody\n", ".md")
    assert rev == 1


def _wmd(path, body, rev):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(R.set_rev(body, ".md", rev), encoding="utf-8")


def test_classify_verdicts(tmp_path):
    tpl = tmp_path / "tpl"
    plugins = tmp_path / "plugins"
    proj = tmp_path / "proj"

    # master template
    _wmd(tpl / "AGENTS.md", "# AG\n\nsame\n", 2)
    _wmd(tpl / "ai" / "engineer.agent.md", "# dev\n\nNEW master\n", 2)
    # plugin master
    _wmd(plugins / "myplug" / "shared" / "up.rule.md", "# up\n\nmaster v1\n", 1)
    _wmd(plugins / "myplug" / "shared" / "conf.rule.md", "# conf\n\nmaster P\n", 2)
    _wmd(plugins / "myplug" / "shared" / "gone.rule.md", "# gone\n\nm\n", 1)

    # project copies
    _wmd(proj / "AGENTS.md", "# AG\n\nsame\n", 2)                  # identical -> in-sync
    base_dev_body = "# dev\n\nOLD\n"
    _wmd(proj / "ai" / "engineer.agent.md", base_dev_body, 1)     # untouched vs baseline -> fast-forward
    _wmd(proj / "ai" / "myplug" / "up.rule.md", "# up\n\nuser improved\n", 3)   # user rev>master -> merge-up
    _wmd(proj / "ai" / "myplug" / "conf.rule.md", "# conf\n\nuser Q\n", 1)      # both changed -> conflict
    # gone.rule.md intentionally missing in project -> missing

    baseline = {
        "AGENTS.md": {"rev": 2, "hash": R.content_hash(R.set_rev("# AG\n\nsame\n", ".md", 2), ".md")},
        "ai/engineer.agent.md": {"rev": 1, "hash": R.content_hash(R.set_rev(base_dev_body, ".md", 1), ".md")},
        "ai/myplug/up.rule.md": {"rev": 1, "hash": R.content_hash(R.set_rev("# up\n\nmaster v1\n", ".md", 1), ".md")},
        "ai/myplug/conf.rule.md": {"rev": 1, "hash": R.content_hash(R.set_rev("# conf\n\nbase\n", ".md", 1), ".md")},
        "ai/myplug/gone.rule.md": {"rev": 1, "hash": "deadbeef"},
    }
    (proj / "ai").mkdir(parents=True, exist_ok=True)
    (proj / "ai" / "manifest.json").write_text(json.dumps({
        "plugins": [{"name": "myplug", "version": "0.1.0"}],
        "revisions": baseline,
    }), encoding="utf-8")

    rows = {r["rel"]: r["verdict"] for r in R.classify(proj, template_dir=tpl, plugins_dir=plugins)}
    assert rows["AGENTS.md"] == "in-sync"
    assert rows["ai/engineer.agent.md"] == "fast-forward"
    assert rows["ai/myplug/up.rule.md"] == "merge-up"
    assert rows["ai/myplug/conf.rule.md"] == "conflict"
    assert rows["ai/myplug/gone.rule.md"] == "missing"


def test_fast_forward_and_baseline(tmp_path):
    tpl = tmp_path / "tpl"
    plugins = tmp_path / "plugins"
    proj = tmp_path / "proj"
    _wmd(tpl / "AGENTS.md", "# ag\n\nX\n", 1)
    _wmd(tpl / "ai" / "engineer.agent.md", "# dev\n\nY\n", 1)
    # project: AGENTS.md missing; engineer present and identical (in-sync)
    _wmd(proj / "ai" / "engineer.agent.md", "# dev\n\nY\n", 1)
    (proj / "ai" / "manifest.json").write_text(json.dumps({"plugins": [], "revisions": {}}), encoding="utf-8")

    res = R.fast_forward(proj, template_dir=tpl, plugins_dir=plugins)
    applied = dict(res["applied"])
    assert applied.get("AGENTS.md") == "missing" and (proj / "AGENTS.md").exists()
    assert applied.get("ai/engineer.agent.md") == "in-sync"
    assert res["skipped"] == []

    man = json.loads((proj / "ai" / "manifest.json").read_text())
    assert set(man["revisions"]) == {"AGENTS.md", "ai/engineer.agent.md"}
    # idempotent: re-running classifies everything in-sync
    assert all(r["verdict"] == "in-sync" for r in R.classify(proj, template_dir=tpl, plugins_dir=plugins))


def test_classify_renders_template_placeholders(tmp_path):
    # a placeholder-bearing master is substituted from the manifest before comparison
    tpl = tmp_path / "tpl"
    plugins = tmp_path / "plugins"
    proj = tmp_path / "proj"
    _wmd(tpl / "AGENTS.md", "# {{NAME}}\n\nproject {{NAME}} ({{TYPE}}, {{MODE}})\n", 1)
    _wmd(tpl / "ai" / "engineer.agent.md", "# {{NAME}} dev\n\nv{{FRAMEWORK_VERSION}}\n", 1)
    _wmd(proj / "AGENTS.md", "# Todo\n\nproject Todo (web-service, local)\n", 1)
    _wmd(proj / "ai" / "engineer.agent.md", "# Todo dev\n\nv0.2.0\n", 1)
    (proj / "ai" / "manifest.json").write_text(json.dumps({
        "project": {"name": "Todo", "slug": "todo", "type": "web-service", "mode": "local"},
        "framework_version": "0.2.0", "plugins": [], "revisions": {},
    }), encoding="utf-8")
    rows = {r["rel"]: r["verdict"] for r in R.classify(proj, template_dir=tpl, plugins_dir=plugins)}
    assert rows["AGENTS.md"] == "in-sync"
    assert rows["ai/engineer.agent.md"] == "in-sync"


def test_plugin_ledger_is_separate_from_framework(tmp_path):
    # framework masters at the FRAMEWORK_GLOBS paths
    fw = tmp_path / "solaris" / "templates" / "ai-pack"
    _wmd(fw / "AGENTS.md", "# ag\n\nx\n", 1)
    _wmd(fw / "ai" / "engineer.agent.md", "# eng\n\ny\n", 1)
    # a plugin with its own shared files
    plug = tmp_path / "plugins" / "myplug"
    _wmd(plug / "shared" / "a.rule.md", "# a\n\nrule a\n", 1)
    _wmd(plug / "shared" / "b.skill.md", "# b\n\nskill b\n", 2)

    fw_ledger = tmp_path / "solaris" / "revisions.json"
    R.rebuild_ledger(repo_root=tmp_path, path=fw_ledger)
    for pd in R.plugin_dirs(tmp_path):
        R.rebuild_plugin_ledger(pd)

    # framework ledger holds ONLY framework masters - never plugin keys
    fw_keys = set(json.loads(fw_ledger.read_text())["files"])
    assert fw_keys == {"solaris/templates/ai-pack/AGENTS.md", "solaris/templates/ai-pack/ai/engineer.agent.md"}
    assert not any("plugin" in k for k in fw_keys)

    # the plugin keeps its own ledger, keyed relative to the plugin
    pl = plug / "revisions.json"
    assert pl.exists()
    assert set(json.loads(pl.read_text())["files"]) == {"shared/a.rule.md", "shared/b.skill.md"}

    # status (framework + plugins) is clean right after a rebuild...
    assert R.status(repo_root=tmp_path, path=fw_ledger) == []
    # ...and flags a plugin shared file edited without a rev bump (reported repo-relative)
    (plug / "shared" / "a.rule.md").write_text(R.set_rev("# a\n\nrule a EDITED\n", ".md", 1), encoding="utf-8")
    assert R.status(repo_root=tmp_path, path=fw_ledger) == ["plugins/myplug/shared/a.rule.md"]
