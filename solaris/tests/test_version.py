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


def _write_manifest(project_dir, framework_version="0.1.0", plugins=None):
    ai = project_dir / "ai"
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


def test_manifest_roundtrip_and_set(tmp_path):
    proj = tmp_path / "todo"
    _write_manifest(proj, "0.1.0", plugins=[{"name": "nvidia-isaac-lab", "version": "0.1.0"}])
    assert V.aipack_version(proj) == "0.1.0"
    assert V.plugin_recorded_version(proj, "nvidia-isaac-lab") == "0.1.0"
    assert V.plugin_recorded_version(proj, "absent") is None

    V.set_aipack_version(proj, "0.2.0")
    assert V.aipack_version(proj) == "0.2.0"
    # set must preserve other fields
    data = V.read_manifest(proj)
    assert data["project"]["name"] == "todo"
    assert data["plugins"][0]["name"] == "nvidia-isaac-lab"

    with pytest.raises(ValueError):
        V.set_aipack_version(proj, "not-a-version")


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


def test_cli_current(capsys):
    rc = V.main(["current"])
    assert rc == 0
    assert capsys.readouterr().out.strip() == V.framework_version()


def test_cli_check_exit_codes(tmp_path, capsys):
    proj = tmp_path / "p"
    _write_manifest(proj, V.framework_version())
    assert V.main(["check", "--dir", str(proj)]) == 0      # match

    V.set_aipack_version(proj, "0.0.1")
    assert V.main(["check", "--dir", str(proj)]) == 1      # behind -> migrate

    V.set_aipack_version(proj, "9.9.9")
    assert V.main(["check", "--dir", str(proj)]) == 2      # ahead -> downgrade
