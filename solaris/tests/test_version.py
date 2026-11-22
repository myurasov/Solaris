# Copyright 2026 Mikhail Yurasov <me@yurasov.me>
# SPDX-License-Identifier: Apache-2.0

"""Tests for solaris.tools.version."""

from __future__ import annotations

import json

import pytest

from solaris.tools import version as V


def test_parse_and_str():
    assert str(V.parse("1.2.3")) == "1.2.3"
    assert str(V.parse("v0.1.0")) == "0.1.0"
    assert V.parse("0.1.0") == V.Version(0, 1, 0)


@pytest.mark.parametrize("bad", ["1.2", "1.2.3.4", "x.y.z", "1.2.x", ""])
def test_parse_rejects_bad(bad):
    with pytest.raises(ValueError):
        V.parse(bad)


def test_compare():
    assert V.compare("1.0.0", "1.0.1") == -1
    assert V.compare("1.2.0", "1.2.0") == 0
    assert V.compare("2.0.0", "1.9.9") == 1


def test_bump_kind():
    assert V.bump_kind("1.0.0", "1.0.0") == "same"
    assert V.bump_kind("1.0.0", "2.0.0") == "major"
    assert V.bump_kind("1.0.0", "1.1.0") == "minor"
    assert V.bump_kind("1.0.0", "1.0.1") == "patch"
    assert V.bump_kind("1.1.0", "1.0.0") == "downgrade"


def test_framework_version_reads_real_pyproject():
    import solaris

    assert V.framework_version() == solaris.__version__


PACKS = ["aipack", "brain", "ai"]   # the default, a custom name, the pre-0.39 legacy name


def _write_manifest(project_dir, framework_version="0.1.0", plugins=None, pack="ai"):
    ai = project_dir / pack
    ai.mkdir(parents=True)
    (ai / "manifest.json").write_text(
        json.dumps(
            {
                "_comment": "do not edit",
                "schema_version": 1,
                "project": {"name": project_dir.name, "type": "web-service", "mode": "local"},
                "framework_version": framework_version,
                "plugins": plugins or [],
            },
            indent=2,
        ),
        encoding="utf-8",
    )


@pytest.mark.parametrize("pack", PACKS)
def test_manifest_roundtrip_and_set(tmp_path, pack):
    proj = tmp_path / "todo"
    _write_manifest(proj, "0.1.0", plugins=[{"name": "nvidia-isaac-lab", "version": "0.1.0"}], pack=pack)
    (proj / "source").mkdir()
    assert V.aipack_version(proj) == "0.1.0"
    assert V.plugin_recorded_version(proj, "nvidia-isaac-lab") == "0.1.0"
    assert V.plugin_recorded_version(proj, "absent") is None

    V.set_aipack_version(proj, "0.2.0")
    assert V.aipack_version(proj) == "0.2.0"
    # set must preserve other fields
    data = V.read_manifest(proj)
    assert data["project"]["name"] == "todo"
    assert data["plugins"][0]["name"] == "nvidia-isaac-lab"
    assert json.loads((proj / pack / "manifest.json").read_text(encoding="utf-8"))["framework_version"] == "0.2.0"
    assert sorted(p.name for p in proj.iterdir()) == sorted([pack, "source"])   # written in place, no new pack

    with pytest.raises(ValueError):
        V.set_aipack_version(proj, "not-a-version")


@pytest.mark.parametrize("pack", PACKS)
def test_set_plugin_version_records_a_copied_plugins_version(tmp_path, capsys, pack, monkeypatch):
    """The owner approved, 2026-11-17: a setter for a copied plugin's recorded version, so updating a copy
    (install-plugin, update-project) needs no hand edit of the manifest."""
    plugins_dir = tmp_path / "plugins"
    (plugins_dir / "autocode").mkdir(parents=True)
    (plugins_dir / "autocode" / "manifest.json").write_text(json.dumps({"name": "autocode", "version": "0.2.0"}),
                                                            encoding="utf-8")
    monkeypatch.setattr(V, "PLUGINS_DIR", plugins_dir)
    proj = tmp_path / "p"
    _write_manifest(proj, "0.48.0", plugins=[{"name": "autocode", "version": "0.1.0"},
                                             {"name": "reporting", "version": "0.4.2"},
                                             {"name": "browserctl", "mode": "link"}], pack=pack)
    # by default the plugin source's version
    assert V.main(["set-plugin", "--dir", str(proj), "--plugin", "autocode"]) == 0
    assert "autocode = 0.2.0" in capsys.readouterr().out
    assert V.plugin_recorded_version(proj, "autocode") == "0.2.0"
    # or a version given; the other entries and fields stay as they were
    assert V.main(["set-plugin", "--dir", str(proj), "--plugin", "reporting", "0.4.3"]) == 0
    data = V.read_manifest(proj)
    assert data["plugins"] == [{"name": "autocode", "version": "0.2.0"}, {"name": "reporting", "version": "0.4.3"},
                               {"name": "browserctl", "mode": "link"}]
    assert data["framework_version"] == "0.48.0" and data["_comment"] == "do not edit"
    capsys.readouterr()
    # refused: a linked plugin (it records no version), one not attached, a bad version, a source that is missing
    assert V.main(["set-plugin", "--dir", str(proj), "--plugin", "browserctl", "1.0.0"]) == 1
    assert "linked" in capsys.readouterr().out
    assert V.main(["set-plugin", "--dir", str(proj), "--plugin", "absent"]) == 1
    assert "not attached" in capsys.readouterr().out
    assert V.main(["set-plugin", "--dir", str(proj), "--plugin", "reporting", "x"]) == 1
    assert "not a MAJOR.MINOR.PATCH version" in capsys.readouterr().out
    assert V.main(["set-plugin", "--dir", str(proj), "--plugin", "reporting"]) == 1   # no plugins/reporting here
    assert "source" in capsys.readouterr().out
    assert V.read_manifest(proj)["plugins"][1] == {"name": "reporting", "version": "0.4.3"}


def test_setters_rerender_the_pack_readme(tmp_path, capsys, monkeypatch):
    """The orchestrator's report, 2026-11-17: the pack README lists each plugin's version and the framework version
    from the manifest; version set and set-plugin re-render the managed files at once (revs ff, which never
    overwrites a project's own edit) and record their baseline, so no step order leaves them stale."""
    from solaris.tools import revs as R
    tpl = tmp_path / "tpl"
    (tpl / "ai").mkdir(parents=True)
    (tpl / "AGENTS.md").write_text("_Rev. 1_\n\n# ag\n", encoding="utf-8")
    (tpl / "ai" / "engineer.agent.md").write_text("_Rev. 1_\n\n# dev\n", encoding="utf-8")
    (tpl / "ai" / "README.md").write_text("_Rev. 1_\n\n# {{NAME}} on {{FRAMEWORK_VERSION}}\n\n{{PLUGINS}}\n",
                                          encoding="utf-8")
    plugins_dir = tmp_path / "plugins"
    (plugins_dir / "autocode" / "shared").mkdir(parents=True)
    (plugins_dir / "autocode" / "manifest.json").write_text(json.dumps({"name": "autocode", "version": "0.2.0"}),
                                                            encoding="utf-8")
    monkeypatch.setattr(V, "PLUGINS_DIR", plugins_dir)
    monkeypatch.setattr(R, "TEMPLATE_DIR", tpl)
    monkeypatch.setattr(R, "PLUGINS_DIR", plugins_dir)
    proj = tmp_path / "p"
    _write_manifest(proj, "0.48.0", plugins=[{"name": "autocode", "version": "0.1.0"}], pack="aipack")
    R.fast_forward(proj, template_dir=tpl, plugins_dir=plugins_dir)
    readme = proj / "aipack" / "README.md"
    assert "`autocode` 0.1.0" in readme.read_text(encoding="utf-8")
    assert V.main(["set-plugin", "--dir", str(proj), "--plugin", "autocode"]) == 0
    assert "`autocode` 0.2.0" in readme.read_text(encoding="utf-8")
    assert V.main(["set", "--dir", str(proj), "0.48.2"]) == 0
    assert "on 0.48.2" in readme.read_text(encoding="utf-8")
    assert "README" in capsys.readouterr().out
    rows = {r["rel"]: r["verdict"] for r in R.classify(proj, template_dir=tpl, plugins_dir=plugins_dir)}
    assert set(rows.values()) == {"in-sync"}
    # a project's own README edit stays; the setter says the file needs a merge, and still records the version
    readme.write_text("_Rev. 1_\n\n# my own words\n", encoding="utf-8")
    capsys.readouterr()
    assert V.main(["set-plugin", "--dir", str(proj), "--plugin", "autocode", "0.2.1"]) == 0
    out = capsys.readouterr().out
    assert "my own words" in readme.read_text(encoding="utf-8")
    assert "aipack/README.md" in out and "merge" in out
    assert V.plugin_recorded_version(proj, "autocode") == "0.2.1"
    # its baseline was not moved: it still shows as a conflict, so no later ff overwrites the edit
    rows = {r["rel"]: r["verdict"] for r in R.classify(proj, template_dir=tpl, plugins_dir=plugins_dir)}
    assert rows["aipack/README.md"] == "conflict"
    R.fast_forward(proj, template_dir=tpl, plugins_dir=plugins_dir)
    assert "my own words" in readme.read_text(encoding="utf-8")


def _write_migration(mig_dir, to_v, from_v, title="t", breaking=False, revertible=True):
    mig_dir.mkdir(parents=True, exist_ok=True)
    (mig_dir / f"{to_v}.md").write_text(
        f"---\n"
        f"to_version: {to_v}\n"
        f"from_version: {from_v}\n"
        f'title: "{title}"\n'
        f"breaking: {str(breaking).lower()}\n"
        f"revertible: {str(revertible).lower()}\n"
        f"---\n\n## Summary\nbody\n",
        encoding="utf-8",
    )


def test_frontmatter_parsing(tmp_path):
    _write_migration(tmp_path, "0.2.0", "0.1.0", title="rename thing", breaking=True)
    fm = V.migration_frontmatter(tmp_path / "0.2.0.md")
    assert fm["to_version"] == "0.2.0"
    assert fm["from_version"] == "0.1.0"
    assert fm["title"] == "rename thing"
    assert fm["breaking"] is True
    assert fm["revertible"] is True


def test_frontmatter_tolerates_rev_marker(tmp_path):
    # a rev-stamped migration (e.g. 0.18.0.md) must not drop out of the chain
    _write_migration(tmp_path, "0.2.0", "0.1.0")
    raw = (tmp_path / "0.2.0.md").read_text(encoding="utf-8")
    (tmp_path / "0.2.0.md").write_text("_Rev. 1_\n\n" + raw, encoding="utf-8")
    fm = V.migration_frontmatter(tmp_path / "0.2.0.md")
    assert fm["to_version"] == "0.2.0"


def test_scan_and_find_chain(tmp_path):
    mig = tmp_path / "migrations"
    _write_migration(mig, "0.2.0", "0.1.0")
    _write_migration(mig, "0.3.0", "0.2.0")
    # README/template are ignored
    (mig / "README.md").write_text("readme", encoding="utf-8")
    (mig / "template.md").write_text("---\nto_version: 0.0.0\nfrom_version: 0.0.0\n---\n", encoding="utf-8")

    rows = V.scan_migrations(mig)
    assert {r["to_version"] for r in rows} == {"0.2.0", "0.3.0"}

    chain = V.find_chain("0.1.0", "0.3.0", mig)
    assert [r["to_version"] for r in chain] == ["0.2.0", "0.3.0"]

    assert V.find_chain("0.1.0", "0.1.0", mig) == []   # same
    assert V.find_chain("0.3.0", "0.1.0", mig) == []   # downgrade
    assert V.find_chain("0.2.0", "0.3.0", mig)[0]["to_version"] == "0.3.0"


def test_find_chain_stops_on_gap(tmp_path):
    mig = tmp_path / "migrations"
    _write_migration(mig, "0.2.0", "0.1.0")
    # gap: nothing links 0.2.0 -> 0.4.0
    chain = V.find_chain("0.1.0", "0.4.0", mig)
    assert [r["to_version"] for r in chain] == ["0.2.0"]


def test_find_chain_spans_patch_gaps(tmp_path):
    # a patch release between migration points (0.2.1) must not break the chain: a project
    # at 0.2.0 still needs the 0.3.0 migration even though it declares from_version 0.2.1
    mig = tmp_path / "migrations"
    _write_migration(mig, "0.3.0", "0.2.1")
    _write_migration(mig, "0.4.0", "0.3.0")
    chain = V.find_chain("0.2.0", "0.4.0", mig)
    assert [r["to_version"] for r in chain] == ["0.3.0", "0.4.0"]
    # and a project already past a migration point does not get it again
    assert [r["to_version"] for r in V.find_chain("0.3.0", "0.4.0", mig)] == ["0.4.0"]


def test_cli_current(capsys):
    rc = V.main(["current"])
    assert rc == 0
    assert capsys.readouterr().out.strip() == V.framework_version()


@pytest.mark.parametrize("pack", PACKS)
def test_cli_check_exit_codes(tmp_path, capsys, pack):
    proj = tmp_path / "p"
    _write_manifest(proj, V.framework_version(), pack=pack)
    assert V.main(["check", "--dir", str(proj)]) == 0      # match

    V.set_aipack_version(proj, "0.0.1")
    assert V.main(["check", "--dir", str(proj)]) == 1      # behind -> migrate

    V.set_aipack_version(proj, "9.9.9")
    assert V.main(["check", "--dir", str(proj)]) == 2      # ahead -> downgrade

    capsys.readouterr()
    assert V.main(["set", "--dir", str(proj), "0.3.0"]) == 0
    assert f"{pack}/manifest.json" in capsys.readouterr().out
    assert V.main(["aipack", "--dir", str(proj)]) == 0
    assert capsys.readouterr().out.strip() == "0.3.0"


def test_cli_reports_a_missing_or_ambiguous_pack_cleanly(tmp_path, capsys):
    proj = tmp_path / "p"
    (proj / "source").mkdir(parents=True)
    (proj / "reporting").mkdir()
    (proj / "reporting" / "manifest.json").write_text('{"name": "reporting", "version": "0.4.0"}',
                                                      encoding="utf-8")   # a plugin manifest is not a pack
    (proj / ".aipack").mkdir()
    (proj / ".aipack" / "manifest.json").write_text(
        json.dumps({"project": {"name": "p"}, "framework_version": "0.1.0"}), encoding="utf-8")   # hidden
    for cmd in (["check"], ["aipack"], ["chain"], ["check-plugins"], ["set", "0.2.0"],
                ["plugin", "--plugin", "x"]):
        assert V.main([cmd[0], "--dir", str(proj), *cmd[1:]]) == 1
        captured = capsys.readouterr()
        assert "no ai-pack" in captured.out and not captured.err      # one clean line, no traceback
    assert V.main(["check", "--dir", str(tmp_path / "nope")]) == 1
    assert "no ai-pack" in capsys.readouterr().out
    _write_manifest(proj, "0.1.0", pack="ai")
    _write_manifest(proj, "0.1.0", pack="aipack")
    assert V.main(["check", "--dir", str(proj)]) == 1
    assert "more than one ai-pack (ai, aipack)" in capsys.readouterr().out
    with pytest.raises(V.P.PackError):
        V.read_manifest(proj)


def test_project_version_roundtrip_and_bump(tmp_path):
    proj = tmp_path / "p"
    proj.mkdir()
    # missing file is a clean, actionable error
    with pytest.raises(FileNotFoundError):
        V.project_version(proj)
    assert V.set_project_version(proj, "0.1.0") == "0.1.0"
    assert (proj / ".version").read_text() == "0.1.0\n"
    assert V.project_version(proj) == "0.1.0"
    # tolerant read (whitespace, leading v) normalizes on the way out
    (proj / ".version").write_text(" v1.2.3 \n")
    assert V.project_version(proj) == "1.2.3"
    assert V.bump_project_version(proj, "patch") == "1.2.4"
    assert V.bump_project_version(proj, "minor") == "1.3.0"
    assert V.bump_project_version(proj, "major") == "2.0.0"
    with pytest.raises(ValueError):
        V.bump_project_version(proj, "huge")
    with pytest.raises(ValueError):
        V.set_project_version(proj, "1.2")


def test_cli_project_commands(tmp_path, capsys):
    proj = tmp_path / "p"
    proj.mkdir()
    assert V.main(["project", "--dir", str(proj)]) == 1          # missing -> clean error, exit 1
    assert ".version" in capsys.readouterr().out
    assert V.main(["project-set", "--dir", str(proj), "1.0.0"]) == 0
    capsys.readouterr()
    assert V.main(["project", "--dir", str(proj)]) == 0
    assert capsys.readouterr().out.strip() == "1.0.0"
    assert V.main(["project-bump", "--dir", str(proj), "minor"]) == 0
    assert capsys.readouterr().out.strip() == "1.1.0"
    assert V.main(["project-set", "--dir", str(proj), "not-a-version"]) == 1


def test_cli_project_error_paths_stay_clean(tmp_path, capsys):
    # a typo'd --dir must give a one-line error + exit 1, never a traceback
    missing = tmp_path / "does-not-exist"
    assert V.main(["project-set", "--dir", str(missing), "0.1.0"]) == 1
    assert "does-not-exist" in capsys.readouterr().out
    empty = tmp_path / "empty"
    empty.mkdir()
    assert V.main(["project-bump", "--dir", str(empty), "patch"]) == 1
    assert ".version" in capsys.readouterr().out


# --------------------------------------------------------------------------- update (no framework migration)

def _md(path, body, rev=1):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"_Rev. {rev}_\n\n{body}\n", encoding="utf-8")


def _plugin(plugins_dir, name, version, files=(), migrations=(), requires=()):
    root = plugins_dir / name
    (root / "shared").mkdir(parents=True, exist_ok=True)
    data = {"name": name, "version": version}
    if requires:
        data["dependencies"] = {"required": list(requires)}
    (root / "manifest.json").write_text(json.dumps(data), encoding="utf-8")
    for rel, body in files:
        _md(root / "shared" / rel, body)
    for to_v in migrations:
        (root / "migrations").mkdir(exist_ok=True)
        (root / "migrations" / f"{to_v}.md").write_text(f"---\nto_version: {to_v}\n---\n\n# steps\n", encoding="utf-8")


def _tree(root):
    return {p.relative_to(root).as_posix(): p.read_bytes() for p in sorted(root.rglob("*")) if p.is_file()}


@pytest.fixture
def release(tmp_path, monkeypatch):
    """A made-up Solaris tree one patch release ahead of its project: the framework went 0.48.4 -> 0.48.5 (a rule
    changed, a skill is new), the copied autocode plugin 0.4.1 -> 0.5.0 (a file changed, one is new, a migration
    on the way), the copied reporting plugin did not move, and browserctl is linked. Every master is released:
    the framework ledger and each plugin's ledger hold it as it is."""
    from solaris.tools import revs as R
    root, proj = tmp_path / "root", tmp_path / "projects" / "demo"
    tpl, plugins_dir = root / "solaris" / "templates" / "ai-pack", root / "plugins"
    _md(tpl / "AGENTS.md", "# {{NAME}}")
    _md(tpl / "ai" / "engineer.agent.md", "# dev")
    _md(tpl / "ai" / "README.md", "# {{NAME}} on {{FRAMEWORK_VERSION}}\n\n{{PLUGINS}}")
    _md(tpl / "ai" / "rules" / "economy.rule.md", "# economy\n\nold")
    _md(tpl / "ai" / "skills" / "init.skill.md", "# init\n\nthe template stub")
    _plugin(plugins_dir, "autocode", "0.4.1", files=[("autocode.rule.md", "# rule\n\nold")])
    _plugin(plugins_dir, "reporting", "0.4.2", files=[("reporting.rule.md", "# reporting")], migrations=["0.2.0"])
    _plugin(plugins_dir, "browserctl", "0.2.4", files=[("browserctl.rule.md", "# browser")])
    monkeypatch.setattr(V, "PLUGINS_DIR", plugins_dir)
    monkeypatch.setattr(V, "MIGRATIONS_DIR", root / "solaris" / "migrations")
    monkeypatch.setattr(V, "framework_version", lambda: "0.48.5")
    monkeypatch.setattr(R, "REPO_ROOT", root)
    monkeypatch.setattr(R, "LEDGER_PATH", root / "solaris" / "revisions.json")
    monkeypatch.setattr(R, "TEMPLATE_DIR", tpl)
    monkeypatch.setattr(R, "PLUGINS_DIR", plugins_dir)
    _write_manifest(proj, "0.48.4", pack="aipack", plugins=[{"name": "autocode", "version": "0.4.1"},
                                                            {"name": "reporting", "version": "0.4.2"},
                                                            {"name": "browserctl", "mode": "link"}])
    (proj / "aipack" / "instructions.md").write_text("# how we work here\n", encoding="utf-8")
    (proj / "aipack" / ".memory").mkdir()
    (proj / "aipack" / ".memory" / "context.md").write_text("private\n", encoding="utf-8")
    R.fast_forward(proj, tpl, plugins_dir)
    R.record_baseline(proj, tpl, plugins_dir)
    # the release: masters move on, and their ledgers with them
    _md(tpl / "ai" / "rules" / "economy.rule.md", "# economy\n\nnew", rev=2)
    _md(tpl / "ai" / "skills" / "handover.skill.md", "# handover")
    _plugin(plugins_dir, "autocode", "0.5.0", files=[("autocode-start.skill.md", "# start")],
            migrations=["0.3.0", "0.5.0"])
    _md(plugins_dir / "autocode" / "shared" / "autocode.rule.md", "# rule\n\nnew", rev=2)
    (plugins_dir / "autocode" / "migrations" / "README.md").write_text("# about\n", encoding="utf-8")
    R.rebuild_all(root)
    return {"proj": proj, "root": root, "tpl": tpl, "plugins": plugins_dir,
            "migrations": root / "solaris" / "migrations", "R": R}


CHANGED = ["aipack/manifest.json", "aipack/README.md", "aipack/rules/economy.rule.md",
           "aipack/skills/handover.skill.md (new)", "aipack/plugins/autocode/autocode-start.skill.md (new)",
           "aipack/plugins/autocode/autocode.rule.md"]
CHECKS = ["version check", "version check-plugins", "plugins check", "revs classify", "agents --check",
          "plugin files"]


def _listed(out, heading):
    """The indented lines under the report line that starts with ``heading``."""
    lines = out.splitlines()
    start = next(i for i, line in enumerate(lines) if line.startswith(heading))
    rows = []
    for line in lines[start + 1:]:
        if not line.startswith("  "):
            break
        rows.append(line.strip())
    return rows


def _update(capsys, proj, *flags):
    code = V.main(["update", "--dir", str(proj), *flags])
    return code, capsys.readouterr().out


def test_update_dry_run_prints_the_plan_and_changes_nothing(release, capsys):
    proj = release["proj"]
    before = _tree(proj)
    code, out = _update(capsys, proj, "--dry-run")
    assert code == 0 and _tree(proj) == before
    assert "(dry run): framework 0.48.4 -> 0.48.5 (patch)" in out
    assert "plugin autocode: 0.4.1 -> 0.5.0" in out and "plugin reporting: 0.4.2 (no new version)" in out
    assert "plugin browserctl: linked, live source 0.2.4" in out
    assert sorted(_listed(out, "files would change (6;")) == sorted(CHANGED)
    assert _listed(out, "steps for the project's session") == ["plugins/autocode/migrations/0.5.0.md"]
    assert "checks (on a scratch copy):" in out and "FAILED" not in out
    assert [line.split(":")[0] for line in _listed(out, "checks (on a scratch copy):")] == CHECKS
    assert "result: dry run, the project is unchanged; every check would pass" in out


def test_update_dry_run_works_on_a_scratch_copy_without_memory_and_removes_it(release, capsys, monkeypatch):
    proj = release["proj"]
    memory = proj / "aipack" / ".memory"
    (memory / "credentials.md").write_text("TOP-SECRET-KEY\n", encoding="utf-8")
    (memory / "elsewhere").symlink_to(release["root"])   # a link inside .memory/ is never looked at
    deep = proj / "aipack" / "plugins" / "autocode" / ".memory"   # left out at any depth
    deep.mkdir()
    (deep / "state.json").write_text('{"token": "TOP-SECRET-KEY"}', encoding="utf-8")
    cache = proj / "aipack" / "plugins" / "autocode" / "__pycache__"
    cache.mkdir()
    (cache / "x.pyc").write_bytes(b"\0")
    seen = {}
    real = V.apply_update

    def spy(target, plan):
        seen["target"], seen["files"] = target, _tree(target)
        return real(target, plan)

    monkeypatch.setattr(V, "apply_update", spy)
    before = _tree(proj)
    code, out = _update(capsys, proj, "--dry-run")
    assert code == 0 and _tree(proj) == before
    target = seen["target"]
    assert target != proj and target.parent.name.startswith("solaris-update-")
    assert not str(target).startswith(str(proj.parent.parent))   # in the system's temp folder, not in the tree
    # the copy holds the root AGENTS.md and the pack, and nothing of any .memory/ or cache
    wanted = {rel for rel in before if ".memory/" not in rel and "__pycache__" not in rel}
    assert set(seen["files"]) == wanted and "AGENTS.md" in wanted and "aipack/manifest.json" in wanted
    assert not any(b"TOP-SECRET-KEY" in data for data in seen["files"].values())
    # and it is gone when the run ends
    assert not target.exists() and not target.parent.exists()


def test_update_brings_a_pack_to_the_current_release(release, capsys):
    proj, R = release["proj"], release["R"]
    private = (proj / "aipack" / ".memory" / "context.md").read_bytes()
    code, out = _update(capsys, proj)
    assert code == 0
    assert "framework 0.48.4 -> 0.48.5 (patch)" in out and "plugin autocode: 0.4.1 -> 0.5.0" in out
    # every file it changed is named, from the project root, and nothing else changed
    assert sorted(_listed(out, "files changed (6;")) == sorted(CHANGED)
    assert _listed(out, "steps for the project's session") == ["plugins/autocode/migrations/0.5.0.md"]
    assert [line.split(":")[0] for line in _listed(out, "checks:")] == CHECKS
    assert "FAILED" not in out and "result: updated, every check passed" in out
    assert "no longer shipped" not in out and "needs a merge" not in out
    assert V.aipack_version(proj) == "0.48.5"
    assert V.plugin_recorded_version(proj, "autocode") == "0.5.0"
    assert V.read_manifest(proj)["plugins"][2] == {"name": "browserctl", "mode": "link"}
    assert "on 0.48.5" in (proj / "aipack" / "README.md").read_text(encoding="utf-8")
    assert "`autocode` 0.5.0" in (proj / "aipack" / "README.md").read_text(encoding="utf-8")
    assert "new" in (proj / "aipack" / "plugins" / "autocode" / "autocode.rule.md").read_text(encoding="utf-8")
    assert (proj / "aipack" / ".memory" / "context.md").read_bytes() == private
    # the checks by hand
    assert V.main(["check", "--dir", str(proj)]) == 0 and V.main(["check-plugins", "--dir", str(proj)]) == 0
    assert {r["verdict"] for r in R.classify(proj, release["tpl"], release["plugins"])} == {"in-sync"}
    baseline = V.read_manifest(proj)["revisions"]
    assert baseline["aipack/rules/economy.rule.md"]["rev"] == 2 and "aipack/skills/handover.skill.md" in baseline
    # a second run finds nothing to do, and writes nothing: the manifest is the same file, not a new one
    capsys.readouterr()
    after, manifest = _tree(proj), (proj / "aipack" / "manifest.json").stat()
    code, out = _update(capsys, proj)
    assert code == 0
    assert "framework 0.48.5 (no new version)" in out and "files changed: none" in out
    assert "steps for the project's session" not in out and "result: nothing to update, every check passed" in out
    assert _tree(proj) == after
    again = (proj / "aipack" / "manifest.json").stat()
    assert (again.st_ino, again.st_mtime_ns) == (manifest.st_ino, manifest.st_mtime_ns)


def test_update_stops_before_any_change_when_a_file_needs_a_merge(release, capsys):
    proj = release["proj"]
    # the project edited a managed file: both sides changed
    _md(proj / "aipack" / "rules" / "economy.rule.md", "# economy\n\nthe project's own words")
    # and raised a plugin file above its master
    _md(proj / "aipack" / "plugins" / "reporting" / "reporting.rule.md", "# reporting\n\nimproved here", rev=5)
    before = _tree(proj)
    for flags in ([], ["--dry-run"]):
        code, out = _update(capsys, proj, *flags)
        assert code == V.REFUSED == 3
        assert "needs a merge (conflict): aipack/rules/economy.rule.md" in out
        assert "needs a merge (merge-up): aipack/plugins/reporting/reporting.rule.md" in out
        assert "stopped, nothing changed:" in out and "resolve, then run this again:" in out
        assert "the update-project skill (solaris/skills/update-project.skill.md)" in out
        assert "install-plugin" not in out and "unreleased" not in out   # only the cures that apply
        assert "files changed" not in out and "checks" not in out
        assert _tree(proj) == before


def test_update_never_replaces_a_skill_the_project_filled_in(release, capsys):
    proj, R = release["proj"], release["R"]
    # the project filled the stub in and its baseline records that: revs ff would put the template back
    _md(proj / "aipack" / "skills" / "init.skill.md", "# init\n\nour hosts and our steps")
    R.record_baseline(proj, release["tpl"], release["plugins"])
    rows = {r["rel"]: r["verdict"] for r in R.classify(proj, release["tpl"], release["plugins"])}
    assert rows["aipack/skills/init.skill.md"] == "fast-forward"
    before = _tree(proj)
    code, out = _update(capsys, proj)
    assert code == 3
    assert "a skill the project fills in would be replaced: aipack/skills/init.skill.md" in out
    assert _tree(proj) == before


def test_update_never_copies_an_unreleased_master(release, capsys):
    """Review B, finding 1: a master edited with no revision bump and no version bump classifies fast-forward, and
    a new one missing. Neither is copied: a master that does not match its ledger entry is work in progress."""
    proj, root, plugins_dir, R = release["proj"], release["root"], release["plugins"], release["R"]
    before = _tree(proj)
    rule = plugins_dir / "autocode" / "shared" / "autocode.rule.md"
    _md(rule, "# rule\n\nHALF-WRITTEN", rev=2)                                  # edited, same revision
    _md(plugins_dir / "autocode" / "shared" / "new.skill.md", "# new")           # new, in no ledger
    _md(release["tpl"] / "ai" / "rules" / "economy.rule.md", "# economy\n\nnewer", rev=3)   # bumped, no ledger yet
    for flags in ([], ["--dry-run"]):
        code, out = _update(capsys, proj, *flags)
        assert code == 3 and _tree(proj) == before
        assert ("unreleased master: plugins/autocode/shared/autocode.rule.md (differs from its entry in "
                "plugins/autocode/revisions.json)") in out
        assert "unreleased master: plugins/autocode/shared/new.skill.md (no entry in plugins/autocode/revisions.json)" \
            in out
        assert ("unreleased master: solaris/templates/ai-pack/ai/rules/economy.rule.md (differs from its entry in "
                "solaris/revisions.json)") in out
        assert "work in progress in this Solaris checkout" in out and "never rebuild a ledger" in out
        assert "HALF-WRITTEN" not in "".join(data.decode() for data in _tree(proj).values())
    # a master the project already holds as it is counts too: nothing of an unreleased file is written again
    (proj / "aipack" / "plugins" / "autocode" / "autocode.rule.md").write_bytes(rule.read_bytes())
    assert _update(capsys, proj)[0] == 3
    (proj / "aipack" / "plugins" / "autocode" / "autocode.rule.md").write_bytes(
        before["aipack/plugins/autocode/autocode.rule.md"])
    # a plugin with no ledger at all cannot be told released
    (plugins_dir / "autocode" / "revisions.json").unlink()
    code, out = _update(capsys, proj)
    assert code == 3 and "(no readable ledger plugins/autocode/revisions.json)" in out
    # once whoever edits them has released them (revisions bumped, ledgers rebuilt), the update runs
    _md(rule, "# rule\n\nfinished", rev=3)
    R.rebuild_all(root)
    code, out = _update(capsys, proj)
    assert code == 0 and "aipack/plugins/autocode/new.skill.md (new)" in out
    assert "finished" in (proj / "aipack" / "plugins" / "autocode" / "autocode.rule.md").read_text(encoding="utf-8")


def test_update_copies_a_released_master_under_an_unchanged_version(release, capsys):
    """Files sync by revision, not by version: a master released as a new revision reaches the project though no
    version moved, and the report says which files changed."""
    proj, root = release["proj"], release["root"]
    assert _update(capsys, proj)[0] == 0
    _md(release["plugins"] / "reporting" / "shared" / "reporting.rule.md", "# reporting\n\nclearer", rev=2)
    release["R"].rebuild_all(root)
    code, out = _update(capsys, proj)
    assert code == 0
    assert "framework 0.48.5 (no new version)" in out and "plugin reporting: 0.4.2 (no new version)" in out
    assert sorted(_listed(out, "files changed (2;")) == ["aipack/manifest.json",
                                                         "aipack/plugins/reporting/reporting.rule.md"]
    assert "result: updated, every check passed" in out
    assert "clearer" in (proj / "aipack" / "plugins" / "reporting" / "reporting.rule.md").read_text(encoding="utf-8")


def test_update_stops_when_the_framework_has_a_migration_to_run(release, capsys, monkeypatch):
    proj = release["proj"]
    before = _tree(proj)
    # a minor step with no migration file needs none: it runs (the dry run shows it)
    monkeypatch.setattr(V, "framework_version", lambda: "0.49.0")
    code, out = _update(capsys, proj, "--dry-run")
    assert code == 0 and "framework 0.48.4 -> 0.49.0 (minor)" in out
    # with a migration on the way it stops, naming the file and the skill
    _write_migration(release["migrations"], "0.49.0", "0.48.0")
    for flags in ([], ["--dry-run"]):
        code, out = _update(capsys, proj, *flags)
        assert code == 3
        assert "framework 0.48.4 -> 0.49.0 has 1 migration(s) to run: solaris/migrations/0.49.0.md" in out
        assert "update-project skill" in out
    # a pack newer than the framework stops too
    monkeypatch.setattr(V, "framework_version", lambda: "0.48.3")
    code, out = _update(capsys, proj)
    assert code == 3 and "newer than this checkout's 0.48.3" in out
    assert _tree(proj) == before


def test_update_stops_on_a_plugin_it_cannot_bring_up(release, capsys):
    import shutil
    proj, plugins_dir = release["proj"], release["plugins"]
    before = _tree(proj)
    # the source is older than the copy
    _plugin(plugins_dir, "reporting", "0.4.0")
    code, out = _update(capsys, proj)
    assert code == 3
    assert "plugin reporting: the ai-pack records 0.4.2, newer than the source's 0.4.0" in out
    assert "the install-plugin skill (solaris/skills/install-plugin.skill.md)" in out
    # the new version requires a plugin that is not attached
    _plugin(plugins_dir, "reporting", "0.4.2")
    _plugin(plugins_dir, "autocode", "0.5.0", requires=["resource-sharing"])
    _plugin(plugins_dir, "resource-sharing", "0.4.1")
    code, out = _update(capsys, proj)
    assert code == 3 and "a required plugin dependency is not attached: autocode > resource-sharing" in out
    _plugin(plugins_dir, "autocode", "0.5.0")
    # a source that is gone, for a copy and for a link
    shutil.rmtree(plugins_dir / "reporting")
    shutil.rmtree(plugins_dir / "browserctl")
    code, out = _update(capsys, proj, "--dry-run")
    assert code == 3
    assert "plugin reporting: no source with a version under plugins/reporting/" in out
    assert "plugin browserctl: no source with a version under plugins/browserctl/" in out
    assert _tree(proj) == before


def test_update_stops_part_way_when_a_file_changes_under_it(release, capsys, monkeypatch):
    """Review B, finding 2: a managed file edited after the plan was made (the project's live session, a file
    sync) is skipped by revs ff. The baseline must not record the edit, or the next run would overwrite it."""
    proj, R = release["proj"], release["R"]
    readme = proj / "aipack" / "README.md"
    real = V.set_aipack_version

    def set_and_edit(project_dir, version):
        real(project_dir, version)
        _md(readme, "# edited by the project's own session")   # between the first revs ff and the second

    monkeypatch.setattr(V, "set_aipack_version", set_and_edit)
    code, out = _update(capsys, proj)
    assert code == 1
    assert _listed(out, "changed while the update ran, needs a merge") == ["aipack/README.md (conflict)"]
    assert "the baseline was not recorded" in out
    assert "result: stopped part way: 1 file(s) need a merge (update-project section 1), then run this again" in out
    assert "revs classify: FAILED - aipack/README.md (conflict)" in out
    assert "edited by the project's own session" in readme.read_text(encoding="utf-8")
    rows = {r["rel"]: r["verdict"] for r in R.classify(proj, release["tpl"], release["plugins"])}
    assert rows["aipack/README.md"] == "conflict"   # not fast-forward: no later run may replace the edit
    # the next run stops before any change and names the file; the edit stays
    monkeypatch.setattr(V, "set_aipack_version", real)
    before = _tree(proj)
    code, out = _update(capsys, proj)
    assert code == 3 and "needs a merge (conflict): aipack/README.md" in out
    assert _tree(proj) == before and "edited by the project's own session" in readme.read_text(encoding="utf-8")


def test_update_stops_at_the_first_revs_ff_that_skips_a_file(release, capsys, monkeypatch):
    proj, R = release["proj"], release["R"]
    rule = proj / "aipack" / "rules" / "economy.rule.md"
    real = V._managed_state
    calls = []

    def state_and_edit(project_dir):
        out = real(project_dir)
        if not calls:   # right after the plan, before the first revs ff
            _md(rule, "# economy\n\nthe project's own words")
        calls.append(1)
        return out

    monkeypatch.setattr(V, "_managed_state", state_and_edit)
    code, out = _update(capsys, proj)
    assert code == 1
    assert _listed(out, "changed while the update ran, needs a merge") == ["aipack/rules/economy.rule.md (conflict)"]
    assert "the project's own words" in rule.read_text(encoding="utf-8")
    assert V.aipack_version(proj) == "0.48.4" and V.plugin_recorded_version(proj, "autocode") == "0.4.1"
    rows = {r["rel"]: r["verdict"] for r in R.classify(proj, release["tpl"], release["plugins"])}
    assert rows["aipack/rules/economy.rule.md"] == "conflict"
    assert "version check: FAILED" in out   # the versions were not set: the update stopped before them


def test_update_dry_run_refuses_a_pack_that_holds_a_symlink(release, capsys, tmp_path):
    """Review B, finding 3: a link is copied as a link, and a write to the copy would go through it to the real
    file. So a dry run of such a pack is refused; the real run writes through the link, as revs ff does."""
    proj = release["proj"]
    outside = tmp_path / "outside"
    outside.mkdir()
    rule = proj / "aipack" / "rules" / "economy.rule.md"
    (outside / "economy.rule.md").write_bytes(rule.read_bytes())
    rule.unlink()
    rule.symlink_to(outside / "economy.rule.md")            # a managed file that is a link
    folder = proj / "aipack" / "plugins" / "autocode"
    folder.rename(outside / "autocode")
    folder.symlink_to(outside / "autocode")                 # managed files under a linked folder
    before = _tree(outside)
    code, out = _update(capsys, proj, "--dry-run")
    assert code == 1
    assert "the pack holds a symlink (aipack/plugins/autocode and 1 more): a dry run cannot copy it safely" in out
    assert "nothing was done" in out and "files would change" not in out
    assert _tree(outside) == before and rule.is_symlink() and folder.is_symlink()
    # the real run is not held up: it writes through the links
    code, out = _update(capsys, proj)
    assert code == 0 and "result: updated, every check passed" in out
    assert "new" in (outside / "economy.rule.md").read_text(encoding="utf-8") and rule.is_symlink()
    assert (outside / "autocode" / "autocode-start.skill.md").is_file()


def test_update_dry_run_of_a_pack_folder_that_is_itself_a_link(release, capsys, tmp_path):
    proj = release["proj"]
    home = tmp_path / "elsewhere" / "aipack"
    home.parent.mkdir()
    (proj / "aipack").rename(home)
    (proj / "aipack").symlink_to(home)
    before = _tree(home)
    code, out = _update(capsys, proj, "--dry-run")
    assert code == 0 and _tree(home) == before and (proj / "aipack").is_symlink()
    assert sorted(_listed(out, "files would change (6;")) == sorted(CHANGED)
    assert "result: dry run, the project is unchanged; every check would pass" in out
    code, out = _update(capsys, proj)
    assert code == 0 and V.aipack_version(proj) == "0.48.5" and (proj / "aipack").is_symlink()


def test_update_names_shared_files_that_revs_does_not_track(release, capsys):
    """Review B, finding 4: revs copies a plugin's .md, .py, .js, .sh and .css files and its marked .json files.
    A shared file of any other kind never reaches the pack through update, so a check names it."""
    proj, root, R = release["proj"], release["root"], release["R"]
    shared = release["plugins"] / "autocode" / "shared"
    (shared / "Dockerfile").write_text("FROM scratch\n", encoding="utf-8")
    (shared / "data").mkdir()
    (shared / "data" / "models.json").write_text('{"rows": []}', encoding="utf-8")    # JSON with no marker: data
    (shared / "jobs").mkdir()
    (shared / "jobs" / "plan.json").write_text('{"_rev": 1, "model": "small"}', encoding="utf-8")   # marked: tracked
    (shared / ".DS_Store").write_bytes(b"\0")
    (shared / "__pycache__").mkdir()
    (shared / "__pycache__" / "x.pyc").write_bytes(b"\0")
    R.rebuild_all(root)
    code, out = _update(capsys, proj)
    assert code == 1 and "result: updated, 1 check(s) FAILED" in out
    assert "aipack/plugins/autocode/jobs/plan.json (new)" in out          # the marked JSON came with revs ff
    line = next(row for row in _listed(out, "checks:") if row.startswith("plugin files"))
    assert line == ("plugin files: FAILED - not copied by revs (install-plugin copies them): "
                    "aipack/plugins/autocode/Dockerfile is missing (source plugins/autocode/shared/Dockerfile); "
                    "aipack/plugins/autocode/data/models.json is missing (source "
                    "plugins/autocode/shared/data/models.json)")
    home = proj / "aipack" / "plugins" / "autocode"
    assert (home / "jobs" / "plan.json").read_bytes() == (shared / "jobs" / "plan.json").read_bytes()
    assert not (home / "Dockerfile").exists() and not (home / ".DS_Store").exists()
    # copied by hand (install-plugin), the check passes; a copy that differs is named again
    (home / "Dockerfile").write_bytes((shared / "Dockerfile").read_bytes())
    (home / "data").mkdir()
    (home / "data" / "models.json").write_text('{"rows": [1]}', encoding="utf-8")
    code, out = _update(capsys, proj)
    assert code == 1
    assert ("plugin files: FAILED - not copied by revs (install-plugin copies them): "
            "aipack/plugins/autocode/data/models.json differs from plugins/autocode/shared/data/models.json") in out
    (home / "data" / "models.json").write_bytes((shared / "data" / "models.json").read_bytes())
    code, out = _update(capsys, proj)
    assert code == 0 and "plugin files: ok" in out
    # a linked plugin has no copy to compare
    (release["plugins"] / "browserctl" / "shared" / "Dockerfile").write_text("FROM scratch\n", encoding="utf-8")
    assert _update(capsys, proj)[0] == 0


def test_update_lists_a_file_a_plugin_dropped_before_the_baseline_forgets_it(release, capsys):
    """Review B, finding 5: revs baseline rebuilds the map from the current masters, so a file a plugin no longer
    ships leaves it without a word and stays in the pack (a dropped rule stays always-on)."""
    proj, root = release["proj"], release["root"]
    (release["plugins"] / "reporting" / "shared" / "reporting.rule.md").unlink()
    release["R"].rebuild_all(root)
    left = "aipack/plugins/reporting/reporting.rule.md"
    assert left in V.read_manifest(proj)["revisions"]
    heading = "no longer shipped by the framework or a plugin, still in the pack"
    code, out = _update(capsys, proj, "--dry-run")
    assert code == 0 and _listed(out, heading) == [left]
    code, out = _update(capsys, proj)
    assert code == 0 and _listed(out, heading) == [left] and "the project's own session removes them" in out
    assert (proj / left).is_file() and left not in V.read_manifest(proj)["revisions"]
    assert left not in out.split(heading)[0]   # not among the files changed: update removes nothing
    code, out = _update(capsys, proj)
    assert code == 0 and heading not in out   # told once: the baseline no longer names it


def test_update_reports_a_check_that_fails_after_the_update(release, capsys):
    proj = release["proj"]
    (proj / "aipack" / "instructions.md").unlink()   # agents --check wants the shared instructions store
    code, out = _update(capsys, proj, "--dry-run")
    assert code == 1
    assert "agents --check: FAILED - aipack/instructions.md not found" in out
    assert "result: dry run, the project is unchanged; 1 check(s) would FAIL" in out
    assert V.aipack_version(proj) == "0.48.4"
    code, out = _update(capsys, proj)
    assert code == 1
    assert "agents --check: FAILED" in out and "result: updated, 1 check(s) FAILED" in out
    assert "version check: ok" in out and "revs classify: ok" in out
    assert V.aipack_version(proj) == "0.48.5"   # the update itself was done


def test_update_that_fails_part_way_says_so_and_a_second_run_completes_it(release, capsys, monkeypatch):
    proj = release["proj"]
    real = V.set_plugin_version

    def full_disk(project_dir, name, version=None):
        raise OSError("no space left on device")

    monkeypatch.setattr(V, "set_plugin_version", full_disk)
    code, out = _update(capsys, proj)
    assert code == 1
    assert "update: failed part way (no space left on device); the files written so far stay." in out
    assert "then run this again" in out and "result:" not in out
    assert V.aipack_version(proj) == "0.48.5" and V.plugin_recorded_version(proj, "autocode") == "0.4.1"
    monkeypatch.setattr(V, "set_plugin_version", real)
    code, out = _update(capsys, proj)
    assert code == 0 and "result: updated, every check passed" in out
    assert V.plugin_recorded_version(proj, "autocode") == "0.5.0"
    # the same trouble in a dry run touches nothing
    monkeypatch.setattr(V, "set_plugin_version", full_disk)
    (release["plugins"] / "autocode" / "manifest.json").write_text('{"name": "autocode", "version": "0.5.1"}',
                                                                   encoding="utf-8")
    before = _tree(proj)
    code, out = _update(capsys, proj, "--dry-run")
    assert code == 1 and "update: the dry run could not be made (no space left on device); nothing was done" in out
    assert _tree(proj) == before


def test_update_records_a_version_for_a_copy_that_has_none(release, capsys):
    proj = release["proj"]
    data = V.read_manifest(proj)
    del data["plugins"][0]["version"]
    V.write_manifest(proj, data)
    code, out = _update(capsys, proj)
    assert code == 0 and "plugin autocode: no version recorded -> 0.5.0" in out
    # no version to count from: every migration up to the source's version is named
    assert _listed(out, "steps for the project's session") == ["plugins/autocode/migrations/0.3.0.md",
                                                               "plugins/autocode/migrations/0.5.0.md"]
    assert V.plugin_recorded_version(proj, "autocode") == "0.5.0"


@pytest.mark.parametrize("entry", [{"version": "1.0.0"}, {"name": ""}, {"name": 7}, "reporting", None])
def test_update_names_a_plugins_entry_it_cannot_use(release, capsys, entry):
    """Review B, finding 7: an entry with no name made revs raise a TypeError. It is a reason to stop, named."""
    proj = release["proj"]
    data = V.read_manifest(proj)
    data["plugins"].append(entry)
    V.write_manifest(proj, data)
    before = _tree(proj)
    for flags in ([], ["--dry-run"]):
        code, out = _update(capsys, proj, *flags)
        assert code == 3 and _tree(proj) == before
        assert f"a plugins entry of aipack/manifest.json is not an object with a name: {entry!r}" in out
        assert "the install-plugin skill" in out


@pytest.mark.parametrize("change, said", [
    ({"framework_version": "soon"}, "not a MAJOR.MINOR.PATCH version"),
    ({"plugins": {"autocode": "0.4.1"}}, "cannot read the project's state"),
    ({"agents": {"primary": "Not A Role"}}, "agents.primary"),
])
def test_update_on_a_manifest_it_cannot_read_is_one_clean_line(release, capsys, change, said):
    proj = release["proj"]
    V.write_manifest(proj, {**V.read_manifest(proj), **change})
    before = _tree(proj)
    code = V.main(["update", "--dir", str(proj)])
    captured = capsys.readouterr()
    assert code == 1 and not captured.err and _tree(proj) == before
    assert captured.out.startswith("update: cannot read the project's state (") and said in captured.out
    assert captured.out.count("\n") == 1 and "nothing changed" in captured.out


def test_update_without_an_ai_pack_is_one_clean_line(tmp_path, capsys):
    (tmp_path / "source").mkdir()
    assert V.main(["update", "--dir", str(tmp_path)]) == 1
    captured = capsys.readouterr()
    assert "no ai-pack" in captured.out and not captured.err


def test_plugin_migrations_on_the_way(tmp_path):
    _plugin(tmp_path, "kit", "0.10.0", migrations=["0.2.0", "0.7.0", "0.10.0"])
    mig = tmp_path / "kit" / "migrations"
    (mig / "README.md").write_text("---\nto_version: 0.8.0\n---\n", encoding="utf-8")   # never a migration
    (mig / "0.9.0.md").write_text("# no frontmatter: the file name is the version\n", encoding="utf-8")
    paths = [f"plugins/kit/migrations/{v}.md" for v in ("0.2.0", "0.7.0", "0.9.0", "0.10.0")]
    assert V.plugin_migrations("kit", None, "0.10.0", tmp_path) == paths          # ordered as versions, not names
    assert V.plugin_migrations("kit", "0.2.0", "0.9.5", tmp_path) == paths[1:3]   # after 0.2.0, up to 0.9.5
    assert V.plugin_migrations("kit", "0.10.0", "0.10.0", tmp_path) == []
    assert V.plugin_migrations("absent", None, "1.0.0", tmp_path) == []
    # a file whose version cannot be read may be a step too: it is named, last, never dropped
    (mig / "notes.md").write_text("# not a version\n", encoding="utf-8")
    (mig / "next.md").write_text("---\nto_version: soon\n---\n", encoding="utf-8")
    unknown = ["plugins/kit/migrations/next.md (version unknown)", "plugins/kit/migrations/notes.md (version unknown)"]
    assert V.plugin_migrations("kit", "0.7.0", "0.10.0", tmp_path) == paths[2:] + unknown
