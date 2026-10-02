# Copyright 2026 Mikhail Yurasov <me@yurasov.me>
# SPDX-License-Identifier: Apache-2.0

"""Tests for solaris.tools.fileio (atomic file replacement)."""

from __future__ import annotations

import os

import pytest

from solaris.tools import fileio as F


def _temps(folder):
    return [p for p in folder.iterdir() if p.name.startswith(F.TMP_PREFIX)]


def test_replaces_content_and_keeps_the_mode(tmp_path):
    target = tmp_path / "tool.sh"
    target.write_text("old\n", encoding="utf-8")
    target.chmod(0o755)
    F.write_text_atomic(target, "new\n")
    assert target.read_text(encoding="utf-8") == "new\n"
    assert target.stat().st_mode & 0o777 == 0o755
    assert _temps(tmp_path) == []


def test_new_file_gets_the_umask_default(tmp_path):
    umask = os.umask(0o022)
    try:
        F.write_text_atomic(tmp_path / "new.json", "{}\n")
    finally:
        os.umask(umask)
    assert (tmp_path / "new.json").stat().st_mode & 0o777 == 0o644


def test_writes_through_a_symlink(tmp_path):
    real = tmp_path / "real.md"
    real.write_text("old\n", encoding="utf-8")
    link = tmp_path / "link.md"
    link.symlink_to(real)
    F.write_text_atomic(link, "new\n")
    assert link.is_symlink() and real.read_text(encoding="utf-8") == "new\n"


def test_a_failed_write_leaves_the_target_and_no_temp(tmp_path, monkeypatch):
    target = tmp_path / "manifest.json"
    target.write_text("keep\n", encoding="utf-8")

    def fail(src, dst):
        raise OSError("disk full")

    monkeypatch.setattr(F.os, "replace", fail)
    with pytest.raises(OSError):
        F.write_text_atomic(target, "lost\n")
    assert target.read_text(encoding="utf-8") == "keep\n" and _temps(tmp_path) == []
