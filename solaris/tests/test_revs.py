# Copyright 2026 Mikhail Yurasov <me@yurasov.me>
# SPDX-License-Identifier: Apache-2.0

"""Tests for solaris.tools.revs."""

from __future__ import annotations

import json

import pytest

from solaris.tools import pack as P
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


def _pack(proj, name="ai", **fields):
    """Make proj/<name>/ the project's pack: an ai-pack manifest (framework_version + project) plus fields."""
    data = {"framework_version": "0.39.0", "project": {}, "plugins": [], "revisions": {}, **fields}
    path = proj / name / "manifest.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


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
    _pack(proj, plugins=[{"name": "myplug", "version": "0.1.0"}], revisions=baseline)

    rows = {r["rel"]: r["verdict"] for r in R.classify(proj, template_dir=tpl, plugins_dir=plugins)}
    assert rows["AGENTS.md"] == "in-sync"
    assert rows["ai/engineer.agent.md"] == "fast-forward"
    assert rows["ai/myplug/up.rule.md"] == "merge-up"
    assert rows["ai/myplug/conf.rule.md"] == "conflict"
    assert rows["ai/myplug/gone.rule.md"] == "missing"


def test_plugins_materialize_under_ai_plugins(tmp_path):
    # 0.28.0+: plugin shared files live under ai/plugins/<name>/; a pack that still has the
    # legacy ai/<name>/ dir (pre-migration) classifies against that until it is moved.
    tpl = tmp_path / "tpl"
    plugins = tmp_path / "plugins"
    _wmd(tpl / "AGENTS.md", "# ag\n\nx\n", 1)
    _wmd(tpl / "ai" / "engineer.agent.md", "# dev\n\ny\n", 1)
    _wmd(plugins / "myplug" / "shared" / "a.rule.md", "# a\n\nrule\n", 1)
    attached = [{"name": "myplug", "version": "0.1.0"}]

    # fresh project (no plugin dir yet) -> new home, never the legacy one
    fresh = tmp_path / "fresh"
    _pack(fresh, plugins=attached)
    rels = {rel for _, _, rel in R.materialized_map(fresh, template_dir=tpl, plugins_dir=plugins)}
    assert "ai/plugins/myplug/a.rule.md" in rels and "ai/myplug/a.rule.md" not in rels

    # a plugin named like a pack-owned dir never treats ai/rules/ as its legacy overlay
    _wmd(plugins / "rules" / "shared" / "r.rule.md", "# r\n\nrule\n", 1)
    clash = tmp_path / "clash"
    _wmd(clash / "ai" / "rules" / "pack.rule.md", "# pack\n\nrule\n", 1)
    _pack(clash, plugins=[{"name": "rules", "version": "0.1.0"}])
    rels = {rel for _, _, rel in R.materialized_map(clash, template_dir=tpl, plugins_dir=plugins)}
    assert "ai/plugins/rules/r.rule.md" in rels and "ai/rules/r.rule.md" not in rels

    # legacy project (only ai/<name>/ exists) -> legacy home until migrated
    legacy = tmp_path / "legacy"
    _wmd(legacy / "ai" / "myplug" / "a.rule.md", "# a\n\nrule\n", 1)
    _pack(legacy, plugins=attached)
    rels = {rel for _, _, rel in R.materialized_map(legacy, template_dir=tpl, plugins_dir=plugins)}
    assert "ai/myplug/a.rule.md" in rels and "ai/plugins/myplug/a.rule.md" not in rels

    # migrated project (both dirs somehow present) -> new home wins
    both = tmp_path / "both"
    _wmd(both / "ai" / "myplug" / "a.rule.md", "# a\n\nrule\n", 1)
    _wmd(both / "ai" / "plugins" / "myplug" / "a.rule.md", "# a\n\nrule\n", 1)
    _pack(both, plugins=attached)
    rels = {rel for _, _, rel in R.materialized_map(both, template_dir=tpl, plugins_dir=plugins)}
    assert "ai/plugins/myplug/a.rule.md" in rels and "ai/myplug/a.rule.md" not in rels


def test_fast_forward_and_baseline(tmp_path):
    tpl = tmp_path / "tpl"
    plugins = tmp_path / "plugins"
    proj = tmp_path / "proj"
    _wmd(tpl / "AGENTS.md", "# ag\n\nX\n", 1)
    _wmd(tpl / "ai" / "engineer.agent.md", "# dev\n\nY\n", 1)
    # project: AGENTS.md missing; engineer present and identical (in-sync)
    _wmd(proj / "ai" / "engineer.agent.md", "# dev\n\nY\n", 1)
    _pack(proj)

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


def test_set_rev_places_marker_after_frontmatter():
    # GitHub only renders YAML frontmatter when it opens the file - the rev
    # marker must land after the closing ---, hash-neutral and idempotent.
    src = "---\nname: x\ntriggers: [\"y\"]\n---\n\n# T\n\nbody\n"
    stamped = R.set_rev(src, ".md", 3)
    assert stamped.startswith("---\n")
    assert "---\n_Rev. 3_\n\n# T" in stamped
    assert R.read_rev(stamped, ".md") == 3
    assert R.content_hash(stamped, ".md") == R.content_hash(src, ".md")
    assert R.set_rev(stamped, ".md", 3) == stamped
    # migrating a legacy marker-on-line-1 file keeps the hash too
    legacy = "_Rev. 3_\n\n" + src
    assert R.set_rev(legacy, ".md", 3) == stamped
    # no frontmatter: marker stays on line 1
    assert R.set_rev("# T\n\nbody\n", ".md", 1).startswith("_Rev. 1_\n")


def test_bump_keeps_the_final_newline(tmp_path, capsys):
    # a Markdown file with frontmatter lost its final newline on every bump
    skill = tmp_path / "x.skill.md"
    skill.write_text('---\nname: x\ntriggers: ["y"]\n---\n_Rev. 4_\n\n# T\n\nbody\n', encoding="utf-8")
    assert R.main(["bump", str(skill)]) == 0 and "rev 5" in capsys.readouterr().out
    assert skill.read_text(encoding="utf-8") == '---\nname: x\ntriggers: ["y"]\n---\n_Rev. 5_\n\n# T\n\nbody\n'
    assert R.bump_file(skill) == 6 and R.bump_file(skill) == 7
    assert skill.read_text(encoding="utf-8").endswith("\n\nbody\n")
    # every other form ends with exactly one newline too, with or without one before the bump
    cases = {"fm-only.md": "---\nname: x\n---\n", "fm-nonl.md": "---\nname: x\n---\n\nbody", "plain.md": "# T\n\nb",
             "tool.py": "x = 1\n", "run.sh": "#!/bin/sh\necho hi", "app.js": "const x = 1;\n", "s.css": "a { }"}
    for name, text in cases.items():
        path = tmp_path / name
        path.write_text(text, encoding="utf-8")
        before = R.content_hash(text, path.suffix)
        R.bump_file(path)
        after = path.read_text(encoding="utf-8")
        assert after.endswith("\n") and not after.endswith("\n\n"), name
        assert R.read_rev(after, path.suffix) == 1 and R.content_hash(after, path.suffix) == before, name


def test_ledger_plugin_rebuilds_only_that_plugins_ledger(tmp_path, monkeypatch, capsys):
    # the whole tree is temporary: REPO_ROOT and the framework ledger path point into tmp_path
    monkeypatch.setattr(R, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(R, "LEDGER_PATH", tmp_path / "solaris" / "revisions.json")
    template = tmp_path / "solaris" / "templates" / "ai-pack" / "AGENTS.md"
    rules = {name: tmp_path / "plugins" / name / "shared" / "a.rule.md" for name in ("alpha", "beta", "gamma")}
    _wmd(template, "# ag\n\nx\n", 1)
    for name, path in rules.items():
        _wmd(path, f"# {name}\n\nrule\n", 1)
    R.rebuild_all(tmp_path)
    ledgers = {"fw": tmp_path / "solaris" / "revisions.json",
               **{name: tmp_path / "plugins" / name / "revisions.json" for name in rules}}

    def snapshot():
        return {key: path.read_text(encoding="utf-8") for key, path in ledgers.items()}

    def rev(key, rel):
        return json.loads(ledgers[key].read_text(encoding="utf-8"))["files"][rel]["rev"]

    # edits everywhere, each with its rev bump; only the named plugin's ledger may move
    _wmd(template, "# ag\n\nedited\n", 2)
    for name, path in rules.items():
        _wmd(path, f"# {name}\n\nedited\n", 2)
    before = snapshot()
    assert R.main(["ledger", "--plugin", "alpha"]) == 0
    assert "plugins/alpha/revisions.json rebuilt for 1 file(s)" in capsys.readouterr().out
    after = snapshot()
    assert rev("alpha", "shared/a.rule.md") == 2
    assert {k: v for k, v in after.items() if k != "alpha"} == {k: v for k, v in before.items() if k != "alpha"}
    # several at once; a plugins/ prefix and a trailing slash are fine
    assert R.main(["ledger", "--plugin", "plugins/beta/", "--plugin", "beta"]) == 0
    assert rev("beta", "shared/a.rule.md") == 2 and rev("gamma", "shared/a.rule.md") == 1
    assert rev("fw", "solaris/templates/ai-pack/AGENTS.md") == 1
    # an unknown name fails before any ledger is written
    before = snapshot()
    assert R.main(["ledger", "--plugin", "gamma", "--plugin", "nope"]) == 1
    out = capsys.readouterr().out
    assert "no plugin 'nope'" in out and "alpha, beta, gamma" in out and snapshot() == before
    # without --plugin, every ledger is rebuilt, the framework's included
    assert R.main(["ledger"]) == 0
    assert rev("gamma", "shared/a.rule.md") == 2 and rev("fw", "solaris/templates/ai-pack/AGENTS.md") == 2


def test_materialized_map_covers_pack_rules_and_skills(tmp_path):
    # The pack's always-on rules and skill stubs sync per file like the engineer agent.
    _pack(tmp_path)
    rels = {rel for _, _, rel in R.materialized_map(tmp_path)}
    assert "ai/rules/subagents.rule.md" in rels
    assert "ai/rules/token-economy.rule.md" in rels
    assert "ai/rules/yagni.rule.md" in rels
    assert "ai/skills/init.skill.md" in rels
    assert "ai/skills/refresh.skill.md" in rels
    assert "ai/info/model-tiers.md" in rels
    assert "ai/info/harnesses.md" in rels
    assert "ai/engineer.agent.md" in rels


def test_sh_js_css_markers_roundtrip():
    from solaris.tools import revs as R
    cases = [
        (".sh", "#!/bin/sh\necho hi\n", "# rev. 2"),
        (".js", "const x = 1;\n", "// rev. 2"),
        (".css", ".a { color: red; }\n", "/* rev. 2 */"),
    ]
    for ext, body, want_marker in cases:
        stamped = R.set_rev(body, ext, 2)
        assert R.read_rev(stamped, ext) == 2, ext
        assert want_marker in stamped, ext
        # content hash identical with and without the marker
        assert R.content_hash(stamped, ext) == R.content_hash(body, ext), ext
    # a shebang stays the first line so the script remains directly executable
    sh = R.set_rev("#!/bin/sh\necho hi\n", ".sh", 3)
    assert sh.splitlines()[0] == "#!/bin/sh"
    assert sh.splitlines()[1] == "# rev. 3"


def test_template_defaults_carry_rule_switch_keys():
    import json
    from solaris.tools.revs import TEMPLATE_DIR
    defaults = json.loads((TEMPLATE_DIR / "ai" / "defaults.json").read_text(encoding="utf-8"))
    for key in ("subagents.level", "economy.level", "yagni.enabled", "git.developer_branches",
                "git.feature_branches"):
        assert key in defaults, key
    assert defaults["git.developer_branches"] is True
    assert defaults["git.feature_branches"] is True


def test_plugins_block_renders_copied_linked_and_empty(tmp_path):
    assert R._plugins_block({"plugins": []}, tmp_path) == "- none attached yet"
    assert R._plugins_block({"plugins": None}, tmp_path) == "- none attached yet"
    # malformed entries (no name) are skipped, never rendered as `None`
    assert R._plugins_block({"plugins": [{"mode": "link"}, {"version": "1.0"}]}, tmp_path) == "- none attached yet"
    block = R._plugins_block({"plugins": [
        {"name": "aplug", "version": "0.1.1"},
        {"name": "lplug", "mode": "link"},
    ]}, tmp_path)
    assert "- `aplug` 0.1.1 - copied into `plugins/aplug/`" in block
    assert "`lplug` - linked" in block and "plugins/lplug.link.md" in block


def test_plugin_blocks_respect_legacy_pack_layout(tmp_path):
    # pre-0.28 pack: plugin files still under ai/<name>/ - rendered refs must point there
    tpl = tmp_path / "tpl"
    plugins = tmp_path / "plugins"
    proj = tmp_path / "proj"
    skill_body = '---\nname: do-thing\ntriggers: ["do the thing"]\n---\n\n# do\n'
    _wmd(plugins / "myplug" / "shared" / "do.skill.md", skill_body, 1)
    _wmd(proj / "ai" / "myplug" / "do.skill.md", skill_body, 1)
    _pack(proj)
    manifest = {"plugins": [{"name": "myplug", "version": "0.1.0"}]}
    assert "- `myplug` 0.1.0 - copied into `myplug/`" in R._plugins_block(manifest, proj)
    block = R._skills_block(manifest, proj, template_dir=tpl, plugins_dir=plugins)
    assert "([`myplug/do.skill.md`](myplug/do.skill.md))" in block
    # once migrated (ai/plugins/<name>/ exists), the new home wins again
    (proj / "ai" / "plugins" / "myplug").mkdir(parents=True)
    assert "copied into `plugins/myplug/`" in R._plugins_block(manifest, proj)


def test_readme_fast_forwards_after_plugin_attach(tmp_path):
    # baseline stability: a manifest-only plugin attach must re-classify the README as
    # fast-forward (the on-disk copy matches the baseline), never conflict, and ff re-renders it
    tpl = tmp_path / "tpl"
    plugins = tmp_path / "plugins"
    proj = tmp_path / "proj"
    _wmd(tpl / "AGENTS.md", "# ag\n\nx\n", 1)
    _wmd(tpl / "ai" / "engineer.agent.md", "# dev\n\ny\n", 1)
    _wmd(tpl / "ai" / "README.md", "# {{NAME}}\n\n{{PLUGINS}}\n", 1)
    _wmd(plugins / "myplug" / "shared" / "a.rule.md", "# a\n\nrule\n", 1)
    mpath = _pack(proj, project={"name": "P", "slug": "p", "type": "t", "mode": "local"})
    R.fast_forward(proj, template_dir=tpl, plugins_dir=plugins)
    assert "- none attached yet" in (proj / "ai" / "README.md").read_text(encoding="utf-8")

    m = json.loads(mpath.read_text(encoding="utf-8"))
    m["plugins"] = [{"name": "myplug", "version": "0.2.0"}]
    mpath.write_text(json.dumps(m), encoding="utf-8")
    rows = {r["rel"]: r["verdict"] for r in R.classify(proj, template_dir=tpl, plugins_dir=plugins)}
    assert rows["ai/README.md"] == "fast-forward"
    R.fast_forward(proj, template_dir=tpl, plugins_dir=plugins)
    text = (proj / "ai" / "README.md").read_text(encoding="utf-8")
    assert "- `myplug` 0.2.0 - copied into `plugins/myplug/`" in text


def test_materialized_map_includes_pack_readme(tmp_path):
    # the shipped template carries the generated pack README; it syncs like the engineer agent
    _pack(tmp_path)
    rels = {rel for _, _, rel in R.materialized_map(tmp_path)}
    assert "ai/README.md" in rels


def test_ff_materializes_readme_with_plugin_list(tmp_path):
    tpl = tmp_path / "tpl"
    plugins = tmp_path / "plugins"
    proj = tmp_path / "proj"
    _wmd(tpl / "AGENTS.md", "# ag\n\nx\n", 1)
    _wmd(tpl / "ai" / "engineer.agent.md", "# dev\n\ny\n", 1)
    _wmd(tpl / "ai" / "README.md",
         "# {{NAME}} - AI Pack\n\n{{DESCRIPTION}}\n\n{{PLUGINS}}\n\n{{WORKSPACES}}\n\n{{SKILLS}}\n", 1)
    _wmd(plugins / "myplug" / "shared" / "a.rule.md", "# a\n\nrule\n", 1)
    _wmd(plugins / "myplug" / "shared" / "do.skill.md",
         '---\nname: do-thing\ntriggers: ["do the thing", "run thing"]\n---\n\n# do\n', 1)
    _pack(proj, project={"name": "Proj X", "slug": "proj-x", "type": "t", "mode": "local",
                         "description": "Does X for Y."},
          plugins=[{"name": "myplug", "version": "0.2.0"}, {"name": "lp", "mode": "link"}])
    R.fast_forward(proj, template_dir=tpl, plugins_dir=plugins)
    text = (proj / "ai" / "README.md").read_text(encoding="utf-8")
    assert text.startswith("_Rev. 1_")
    assert "# Proj X - AI Pack" in text
    assert "**The project:** Does X for Y." in text
    assert "- `myplug` 0.2.0 - copied into `plugins/myplug/`" in text
    assert "`lp` - linked" in text and "{{PLUGINS}}" not in text
    assert "- `source/` - the default (and only) workspace" in text
    assert '- **do-thing** - "do the thing", "run thing" ([`plugins/myplug/do.skill.md`](plugins/myplug/do.skill.md))' in text
    assert "{{SKILLS}}" not in text and "{{WORKSPACES}}" not in text and "{{DESCRIPTION}}" not in text


def test_customized_stub_above_master_rev_never_fast_forwards(tmp_path):
    # a project-customized pack stub (rev bumped above master) must classify merge-up even
    # after a baseline re-record - a fast-forward here would clobber it with the older master
    tpl = tmp_path / "tpl"
    proj = tmp_path / "proj"
    _wmd(tpl / "AGENTS.md", "# ag\n\nx\n", 1)
    _wmd(tpl / "ai" / "engineer.agent.md", "# dev\n\ny\n", 1)
    _wmd(tpl / "ai" / "skills" / "init.skill.md", "# init\n\nstub\n", 8)
    custom = "# init\n\nproject-specific resources\n"
    _wmd(proj / "AGENTS.md", "# ag\n\nx\n", 1)
    _wmd(proj / "ai" / "engineer.agent.md", "# dev\n\ny\n", 1)
    _wmd(proj / "ai" / "skills" / "init.skill.md", custom, 9)
    _pack(proj)
    plugins = tmp_path / "plugins"
    R.record_baseline(proj, template_dir=tpl, plugins_dir=plugins)  # baseline == customized copy
    rows = {r["rel"]: r["verdict"] for r in R.classify(proj, template_dir=tpl, plugins_dir=plugins)}
    assert rows["ai/skills/init.skill.md"] == "merge-up"
    R.fast_forward(proj, template_dir=tpl, plugins_dir=plugins)
    text = (proj / "ai" / "skills" / "init.skill.md").read_text(encoding="utf-8")
    assert "project-specific resources" in text  # untouched by ff


def test_workspaces_block_variants():
    assert R._workspaces_block({"project": {"mode": "local"}}) == "- `source/` - the default (and only) workspace"
    assert R._workspaces_block({"project": {"mode": "embedded"}}).startswith("- this repo itself")
    multi = R._workspaces_block({"project": {"mode": "local", "workspaces": ["baseline", "experiments"]}})
    assert multi.splitlines() == ["- `source/` - the default workspace", "- `baseline/`", "- `experiments/`"]


def test_description_block_present_and_absent():
    assert "see [`spec.md`](spec.md)" in R._description_block({"project": {}})
    d = R._description_block({"project": {"description": "Does X for Y."}})
    assert d.startswith("**The project:** Does X for Y.")


def test_skills_block_lists_pack_and_plugin_skills(tmp_path):
    tpl = tmp_path / "tpl"
    plugins = tmp_path / "plugins"
    proj = tmp_path / "proj"
    _wmd(tpl / "ai" / "skills" / "init.skill.md",
         '---\nname: init\ntriggers: ["init project", "onboard me", "getting started"]\n---\n\n# init\n', 1)
    # multi-line dash form with "a" / "b" alternates: first alternate wins
    _wmd(plugins / "myplug" / "shared" / "do.skill.md",
         '---\nname: do-thing\ntriggers:\n  - "do the thing" / "run thing"\n---\n\n# do\n', 1)
    _wmd(proj / "ai" / "skills" / "local.skill.md",
         '---\nname: local\ntriggers: ["local dance"]\n---\n\n# local\n', 1)
    _pack(proj)
    block = R._skills_block(
        {"plugins": [{"name": "myplug", "version": "1.0"}, {"name": "lnk", "mode": "link"}]},
        proj, template_dir=tpl, plugins_dir=plugins)
    assert '- **init** - "init project", "onboard me" ([`skills/init.skill.md`](skills/init.skill.md))' in block
    assert '- **do-thing** - "do the thing" ([`plugins/myplug/do.skill.md`](plugins/myplug/do.skill.md))' in block
    assert '- **local** - "local dance"' in block


@pytest.mark.parametrize("name", ["aipack", "mypack", "ai"])
def test_any_pack_folder_name_syncs(tmp_path, name):
    # the pack is the child folder holding an ai-pack manifest, whatever its name: rel keys carry the
    # name, {{PACK}} renders to it, and rendered copies classify in-sync (the template keeps ai/)
    tpl = tmp_path / "solaris" / "templates" / "ai-pack"   # at the FRAMEWORK_GLOBS paths, for status
    plugins = tmp_path / "plugins"
    proj = tmp_path / "proj"
    _wmd(tpl / "AGENTS.md", "# {{NAME}}\n\nRead [`{{PACK}}/{{PRIMARY}}.agent.md`]({{PACK}}/{{PRIMARY}}.agent.md).\n", 3)
    _wmd(tpl / "ai" / "engineer.agent.md", "# dev\n\nShared store: `{{PACK}}/instructions.md`.\n", 2)
    _wmd(tpl / "ai" / "README.md", "# {{NAME}}\n\n{{PLUGINS}}\n\n{{SKILLS}}\n", 1)
    _wmd(tpl / "ai" / "rules" / "r.rule.md", "# r\n\nSwitches: `{{PACK}}/defaults.json`.\n", 1)
    _wmd(plugins / "myplug" / "shared" / "p.skill.md",
         '---\nname: p\ntriggers: ["do p"]\n---\n\n# p\n\nNotes go to `{{PACK}}/.memory/`.\n', 1)
    (proj / "source").mkdir(parents=True)
    (proj / "source" / "manifest.json").write_text('{"name": "web app"}', encoding="utf-8")   # not a pack
    mpath = _pack(proj, name, project={"name": "P", "slug": "p", "type": "t", "mode": "local"},
                  plugins=[{"name": "myplug", "version": "0.1.0"}])

    def verdicts():
        return {r["rel"]: r["verdict"] for r in R.classify(proj, template_dir=tpl, plugins_dir=plugins)}

    # a copy rendered by hand (as create-project does) and baselined is in-sync, not fast-forward
    (proj / "AGENTS.md").write_text(R.set_rev(
        f"# P\n\nRead [`{name}/engineer.agent.md`]({name}/engineer.agent.md).\n", ".md", 3), encoding="utf-8")
    R.record_baseline(proj, template_dir=tpl, plugins_dir=plugins)
    assert verdicts()["AGENTS.md"] == "in-sync"

    res = R.fast_forward(proj, template_dir=tpl, plugins_dir=plugins)
    rels = {"AGENTS.md", f"{name}/engineer.agent.md", f"{name}/README.md", f"{name}/rules/r.rule.md",
            f"{name}/plugins/myplug/p.skill.md"}
    assert {rel for rel, _v in res["applied"]} == rels and res["skipped"] == []
    for rel in rels:
        assert "{{" not in (proj / rel).read_text(encoding="utf-8"), rel
    assert f"`{name}/instructions.md`" in (proj / name / "engineer.agent.md").read_text(encoding="utf-8")
    assert f"`{name}/.memory/`" in (proj / name / "plugins" / "myplug" / "p.skill.md").read_text(encoding="utf-8")
    assert "[`plugins/myplug/p.skill.md`](plugins/myplug/p.skill.md)" in (proj / name / "README.md").read_text(
        encoding="utf-8")
    assert name == "ai" or not (proj / "ai").exists()   # nothing lands in a legacy ai/
    assert set(json.loads(mpath.read_text(encoding="utf-8"))["revisions"]) == rels

    # in-sync after ff and after a re-recorded baseline; a master edit still fast-forwards
    assert set(verdicts().values()) == {"in-sync"}
    assert set(R.record_baseline(proj, template_dir=tpl, plugins_dir=plugins)) == rels
    assert set(verdicts().values()) == {"in-sync"}
    _wmd(tpl / "ai" / "rules" / "r.rule.md", "# r\n\nSwitches live in `{{PACK}}/defaults.json`.\n", 2)
    assert verdicts()[f"{name}/rules/r.rule.md"] == "fast-forward"
    R.fast_forward(proj, template_dir=tpl, plugins_dir=plugins)
    assert set(verdicts().values()) == {"in-sync"}

    # status reads only the framework and plugin ledgers, never a project
    ledger = tmp_path / "solaris" / "revisions.json"
    R.rebuild_ledger(repo_root=tmp_path, path=ledger)
    assert R.status(repo_root=tmp_path, path=ledger) == []


def test_shipped_templates_render_the_pack_folder(tmp_path, capsys):
    # the shipped masters write {{PACK}}: ff into a pack named mypack writes mypack/ paths, and the
    # rendered copies stay in-sync across a baseline
    proj = tmp_path / "proj"
    plugins = tmp_path / "plugins"
    _pack(proj, "mypack", project={"name": "Todo", "slug": "todo", "type": "python-cli", "mode": "local"})
    R.fast_forward(proj, plugins_dir=plugins)
    rels = [r["rel"] for r in R.classify(proj, plugins_dir=plugins)]
    assert "mypack/engineer.agent.md" in rels and not [rel for rel in rels if rel.startswith("ai/")]
    agents_md = (proj / "AGENTS.md").read_text(encoding="utf-8")
    assert "mypack/engineer.agent.md" in agents_md and "mypack/instructions.md" in agents_md
    for rel in rels:
        assert "{{PACK}}" not in (proj / rel).read_text(encoding="utf-8"), rel
    assert R.main(["baseline", "--dir", str(proj)]) == 0
    assert str(proj / "mypack" / "manifest.json") in capsys.readouterr().out
    assert {r["verdict"] for r in R.classify(proj, plugins_dir=plugins)} == {"in-sync"}


def test_no_single_pack_is_a_clean_error(tmp_path, capsys):
    # a plugin manifest does not make a pack, nor does a hidden folder; two packs are ambiguous
    proj = tmp_path / "proj"
    (proj / "ai").mkdir(parents=True)
    (proj / "ai" / "manifest.json").write_text('{"name": "myplug", "version": "0.1.0"}', encoding="utf-8")
    _pack(proj, ".aipack")
    with pytest.raises(P.PackError):
        R.classify(proj)
    assert R.main(["classify", "--dir", str(proj)]) == 1
    assert "no ai-pack" in capsys.readouterr().out
    _pack(proj, "aipack")
    _pack(proj, "mypack")
    assert R.main(["ff", "--dir", str(proj)]) == 1
    assert "more than one ai-pack" in capsys.readouterr().out


def test_manifest_errors_name_the_file_plainly():
    # primary_role sees only the manifest, not its folder: no literal <pack>/ placeholder in its messages
    for agents in ([], {"roles": ["x"]}, {"primary": "Bad Name"}):
        with pytest.raises(ValueError, match=r"^ai-pack manifest\.json: "):
            R.primary_role({"agents": agents})


def test_legacy_ai_keys_in_a_renamed_pack(tmp_path):
    # a pack renamed by hand (aipack/) may still hold ai/... revisions keys: they classify exactly like
    # aipack/... keys (a key under the real name wins), and ff / baseline write the real name
    tpl = tmp_path / "tpl"
    plugins = tmp_path / "plugins"
    _wmd(tpl / "AGENTS.md", "# ag\n\nx\n", 1)
    _wmd(tpl / "ai" / "engineer.agent.md", "# dev\n\nNEW\n", 2)
    _wmd(tpl / "ai" / "rules" / "c.rule.md", "# c\n\nmaster\n", 2)
    _wmd(tpl / "ai" / "rules" / "u.rule.md", "# u\n\nmaster\n", 2)

    def base(body, rev):
        return {"rev": rev, "hash": R.content_hash(R.set_rev(body, ".md", rev), ".md")}

    def project(dirname, prefix):
        proj = tmp_path / dirname
        _wmd(proj / "AGENTS.md", "# ag\n\nx\n", 1)                            # in-sync
        _wmd(proj / "aipack" / "engineer.agent.md", "# dev\n\nOLD\n", 1)      # untouched since base: fast-forward
        _wmd(proj / "aipack" / "rules" / "c.rule.md", "# c\n\nedited\n", 1)   # both changed: conflict
        _wmd(proj / "aipack" / "rules" / "u.rule.md", "# u\n\nmine\n", 3)     # rev above master: merge-up
        return _pack(proj, "aipack", revisions={
            "AGENTS.md": base("# ag\n\nx\n", 1), f"{prefix}/engineer.agent.md": base("# dev\n\nOLD\n", 1),
            f"{prefix}/rules/c.rule.md": base("# c\n\nbase\n", 1), f"{prefix}/rules/u.rule.md": base("# u\n\nmaster\n", 1)})

    def rows(proj):
        return [(r["rel"], r["verdict"], r["base_rev"])
                for r in R.classify(proj, template_dir=tpl, plugins_dir=plugins)]

    legacy, real = tmp_path / "legacy", tmp_path / "real"
    legacy_manifest, real_manifest = project("legacy", "ai"), project("real", "aipack")
    m = json.loads(real_manifest.read_text(encoding="utf-8"))
    m["revisions"]["ai/engineer.agent.md"] = {"rev": 9, "hash": "stale"}   # loses to the real-name key
    real_manifest.write_text(json.dumps(m), encoding="utf-8")
    assert rows(legacy) == rows(real) == [
        ("AGENTS.md", "in-sync", 1), ("aipack/engineer.agent.md", "fast-forward", 1),
        ("aipack/rules/c.rule.md", "conflict", 1), ("aipack/rules/u.rule.md", "merge-up", 1)]

    # ff moves every key to the real name, keeping the bases of the files it skips
    real_keys = {"AGENTS.md", "aipack/engineer.agent.md", "aipack/rules/c.rule.md", "aipack/rules/u.rule.md"}
    R.fast_forward(legacy, template_dir=tpl, plugins_dir=plugins)
    assert set(json.loads(legacy_manifest.read_text(encoding="utf-8"))["revisions"]) == real_keys
    assert rows(legacy)[1:] == [("aipack/engineer.agent.md", "in-sync", 2),
                                ("aipack/rules/c.rule.md", "conflict", 1), ("aipack/rules/u.rule.md", "merge-up", 1)]
    # baseline records the real name and drops the stale legacy key
    R.record_baseline(real, template_dir=tpl, plugins_dir=plugins)
    assert set(json.loads(real_manifest.read_text(encoding="utf-8"))["revisions"]) == real_keys
