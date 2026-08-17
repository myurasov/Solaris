# Copyright 2026 Mikhail Yurasov <me@yurasov.me>
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


def test_plugins_materialize_under_ai_plugins(tmp_path):
    # 0.28.0+: plugin shared files live under ai/plugins/<name>/; a pack that still has the
    # legacy ai/<name>/ dir (pre-migration) classifies against that until it is moved.
    tpl = tmp_path / "tpl"
    plugins = tmp_path / "plugins"
    _wmd(tpl / "AGENTS.md", "# ag\n\nx\n", 1)
    _wmd(tpl / "ai" / "engineer.agent.md", "# dev\n\ny\n", 1)
    _wmd(plugins / "myplug" / "shared" / "a.rule.md", "# a\n\nrule\n", 1)
    manifest = json.dumps({"plugins": [{"name": "myplug", "version": "0.1.0"}], "revisions": {}})

    # fresh project (no plugin dir yet) -> new home, never the legacy one
    fresh = tmp_path / "fresh"
    (fresh / "ai").mkdir(parents=True)
    (fresh / "ai" / "manifest.json").write_text(manifest, encoding="utf-8")
    rels = {rel for _, _, rel in R.materialized_map(fresh, template_dir=tpl, plugins_dir=plugins)}
    assert "ai/plugins/myplug/a.rule.md" in rels and "ai/myplug/a.rule.md" not in rels

    # a plugin named like a pack-owned dir never treats ai/rules/ as its legacy overlay
    _wmd(plugins / "rules" / "shared" / "r.rule.md", "# r\n\nrule\n", 1)
    clash = tmp_path / "clash"
    _wmd(clash / "ai" / "rules" / "pack.rule.md", "# pack\n\nrule\n", 1)
    (clash / "ai" / "manifest.json").write_text(json.dumps({
        "plugins": [{"name": "rules", "version": "0.1.0"}], "revisions": {}}), encoding="utf-8")
    rels = {rel for _, _, rel in R.materialized_map(clash, template_dir=tpl, plugins_dir=plugins)}
    assert "ai/plugins/rules/r.rule.md" in rels and "ai/rules/r.rule.md" not in rels

    # legacy project (only ai/<name>/ exists) -> legacy home until migrated
    legacy = tmp_path / "legacy"
    _wmd(legacy / "ai" / "myplug" / "a.rule.md", "# a\n\nrule\n", 1)
    (legacy / "ai" / "manifest.json").write_text(manifest, encoding="utf-8")
    rels = {rel for _, _, rel in R.materialized_map(legacy, template_dir=tpl, plugins_dir=plugins)}
    assert "ai/myplug/a.rule.md" in rels and "ai/plugins/myplug/a.rule.md" not in rels

    # migrated project (both dirs somehow present) -> new home wins
    both = tmp_path / "both"
    _wmd(both / "ai" / "myplug" / "a.rule.md", "# a\n\nrule\n", 1)
    _wmd(both / "ai" / "plugins" / "myplug" / "a.rule.md", "# a\n\nrule\n", 1)
    (both / "ai" / "manifest.json").write_text(manifest, encoding="utf-8")
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


def test_materialized_map_covers_pack_rules_and_skills(tmp_path):
    # The pack's always-on rules and skill stubs sync per file like the engineer agent.
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
    (proj / "ai").mkdir(parents=True)
    mpath = proj / "ai" / "manifest.json"
    mpath.write_text(json.dumps({
        "project": {"name": "P", "slug": "p", "type": "t", "mode": "local"},
        "plugins": [], "revisions": {},
    }), encoding="utf-8")
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
    (proj / "ai").mkdir(parents=True)
    (proj / "ai" / "manifest.json").write_text(json.dumps({
        "project": {"name": "Proj X", "slug": "proj-x", "type": "t", "mode": "local",
                    "description": "Does X for Y."},
        "plugins": [{"name": "myplug", "version": "0.2.0"}, {"name": "lp", "mode": "link"}],
        "revisions": {},
    }), encoding="utf-8")
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
    block = R._skills_block(
        {"plugins": [{"name": "myplug", "version": "1.0"}, {"name": "lnk", "mode": "link"}]},
        proj, template_dir=tpl, plugins_dir=plugins)
    assert '- **init** - "init project", "onboard me" ([`skills/init.skill.md`](skills/init.skill.md))' in block
    assert '- **do-thing** - "do the thing" ([`plugins/myplug/do.skill.md`](plugins/myplug/do.skill.md))' in block
    assert '- **local** - "local dance"' in block
