# Copyright 2026 Mikhail Yurasov <me@yurasov.me>
# SPDX-License-Identifier: Apache-2.0

"""Tests for solaris.tools.pack (finding a project's ai-pack folder, whatever its name)."""

from __future__ import annotations

import json
import os

import pytest

from solaris.tools import pack as P

PACK = {"project": {"name": "Todo", "slug": "todo"}, "framework_version": "0.39.0"}
PLUGIN = {"name": "reporting", "version": "0.4.0", "description": "a plugin manifest, not a pack"}


def _manifest(folder, data):
    folder.mkdir(parents=True, exist_ok=True)
    text = data if isinstance(data, str) else json.dumps(data)
    (folder / "manifest.json").write_text(text, encoding="utf-8")
    return folder


def test_names():
    assert (P.DEFAULT, P.LEGACY) == ("aipack", "ai")


def test_no_pack(tmp_path):
    assert P.find_pack(tmp_path) is None                      # an empty project
    (tmp_path / "source").mkdir()
    (tmp_path / "README.md").write_text("x\n", encoding="utf-8")
    assert P.find_pack(tmp_path) is None                      # folders, but no manifest in any
    _manifest(tmp_path, PACK)                                 # a manifest at the root is not a child pack
    assert P.find_pack(tmp_path) is None
    assert P.find_pack(tmp_path / "missing") is None          # no such folder
    assert P.find_pack(tmp_path / "README.md") is None        # a file, not a folder
    with pytest.raises(P.PackError, match="no ai-pack"):
        P.require_pack(tmp_path)


@pytest.mark.parametrize("name", ["aipack", "ai", "brain", "my pack"])
def test_one_pack_under_any_plain_name(tmp_path, name):
    _manifest(tmp_path / name, PACK)
    (tmp_path / "source").mkdir()
    _manifest(tmp_path / "reporting", PLUGIN)
    assert P.find_pack(tmp_path) == tmp_path / name
    assert P.require_pack(tmp_path) == tmp_path / name


def test_several_packs_are_an_error(tmp_path):
    _manifest(tmp_path / "aipack", PACK)
    _manifest(tmp_path / "ai", PACK)
    with pytest.raises(P.PackError, match=r"more than one ai-pack \(ai, aipack\)"):
        P.find_pack(tmp_path)
    with pytest.raises(P.PackError, match="more than one ai-pack"):
        P.require_pack(tmp_path)


def test_hidden_folders_are_never_packs(tmp_path):
    _manifest(tmp_path / ".aipack", PACK)
    _manifest(tmp_path / ".memory", PACK)
    assert P.find_pack(tmp_path) is None
    _manifest(tmp_path / "aipack", PACK)
    assert P.find_pack(tmp_path) == tmp_path / "aipack"       # nor do they make a real pack ambiguous


def test_plugin_and_other_manifests_are_ignored(tmp_path):
    _manifest(tmp_path / "reporting", PLUGIN)
    _manifest(tmp_path / "no-project", {"framework_version": "0.39.0"})
    _manifest(tmp_path / "project-not-an-object", {"framework_version": "0.39.0", "project": "todo"})
    _manifest(tmp_path / "no-version", {"project": {"name": "x"}})
    _manifest(tmp_path / "a-list", [PACK])
    assert P.find_pack(tmp_path) is None
    assert not P.is_pack_manifest(tmp_path / "reporting" / "manifest.json")
    _manifest(tmp_path / "aipack", PACK)
    assert P.find_pack(tmp_path) == tmp_path / "aipack"
    assert P.is_pack_manifest(tmp_path / "aipack" / "manifest.json")


def test_unreadable_manifests_are_ignored(tmp_path):
    _manifest(tmp_path / "broken", "{not json")
    (tmp_path / "binary").mkdir()
    (tmp_path / "binary" / "manifest.json").write_bytes(b"\xff\xfe\x00garbage")
    (tmp_path / "dir" / "manifest.json").mkdir(parents=True)    # a folder named manifest.json
    assert not P.is_pack_manifest(tmp_path / "missing" / "manifest.json")
    assert P.find_pack(tmp_path) is None
    with pytest.raises(P.PackError, match="no ai-pack"):
        P.require_pack(tmp_path)
    _manifest(tmp_path / "aipack", PACK)                         # a broken leftover never makes it ambiguous
    assert P.require_pack(tmp_path) == tmp_path / "aipack"


@pytest.mark.skipif(not hasattr(os, "geteuid") or os.geteuid() == 0, reason="root can read any file")
def test_permission_denied_manifest_is_ignored(tmp_path):
    manifest = _manifest(tmp_path / "aipack", PACK) / "manifest.json"
    manifest.chmod(0)
    try:
        assert P.find_pack(tmp_path) is None
    finally:
        manifest.chmod(0o644)
