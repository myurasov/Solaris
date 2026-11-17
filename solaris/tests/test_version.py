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
