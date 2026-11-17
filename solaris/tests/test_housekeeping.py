# Copyright 2026 Mikhail Yurasov <me@yurasov.me>
# SPDX-License-Identifier: Apache-2.0

"""Tests for solaris.tools.housekeeping (project sizes, tidy and prune), on temporary project trees."""

from __future__ import annotations

import errno
import json
import os
import shutil
import time

import pytest

from solaris.tools import housekeeping as H

PACK = {"project": {"name": "x", "slug": "x"}, "framework_version": "0.41.0"}
DAY = 86400
REAL_CHANGED = H._changed


@pytest.fixture(autouse=True)
def mtime_only(monkeypatch):
    # most tests back-date files with os.utime, which cannot move ctime back: judge them by mtime alone
    monkeypatch.setattr(H, "_changed", lambda st: st.st_mtime)


@pytest.fixture(autouse=True)
def no_processes(monkeypatch):
    # the machine's own processes stay out of the tests; a test that needs some sets its own
    monkeypatch.setattr(H, "_processes", lambda: [])


def _write(path, text="x"):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _age(path, days):
    """Set the mtime of ``path`` and of everything below it (symlinks themselves included) to ``days`` ago."""
    t = time.time() - days * DAY
    if path.is_dir() and not path.is_symlink():
        for dirpath, dirnames, filenames in os.walk(path, topdown=False):
            for name in filenames + dirnames:
                os.utime(os.path.join(dirpath, name), (t, t), follow_symlinks=False)
    os.utime(path, (t, t), follow_symlinks=False)
    return path


def _month(path):
    return time.strftime("%Y-%m", time.gmtime(os.lstat(path).st_mtime))


def _config(root, **cfg):
    _write(root / "aipack" / "housekeeping.json", json.dumps(cfg))


def _main(root, capsys, *args):
    code = H.main(["--dir", str(root), *args])
    return code, capsys.readouterr().out


def _json(root, capsys, *args):
    code = H.main(["--dir", str(root), *args, "--json"])
    return code, json.loads(capsys.readouterr().out)


def _log(root):
    path = root / "aipack" / ".memory" / H.LOG
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def _snapshot(root):
    """Every entry under ``root`` with its type, size and mtime."""
    seen = {}
    for dirpath, dirnames, filenames in os.walk(root):
        for name in dirnames + filenames:
            st = os.lstat(os.path.join(dirpath, name))
            seen[os.path.relpath(os.path.join(dirpath, name), root)] = (st.st_mode, st.st_size, st.st_mtime_ns)
    return seen


def _held(res, key="prune_held"):
    return {h["path"]: h["held"] for h in res[key]}


@pytest.fixture
def proj(tmp_path):
    """A project root with an ai-pack whose .memory/ holds only canonical files."""
    root = tmp_path / "proj"
    _write(root / "aipack" / "manifest.json", json.dumps(PACK))
    for name in ("context.md", "directions.md", "hosts.json", "config.json", "interactions/box.jsonl"):
        _age(_write(root / "aipack" / ".memory" / name), 30)
    _write(root / "AGENTS.md")
    _write(root / "README.md")
    (root / "source").mkdir()
    return root


def test_report_and_dry_runs_change_nothing(proj, capsys):
    mem = proj / "aipack" / ".memory"
    _age(_write(mem / "old-notes.md"), 40)
    _write(mem / "jobs" / "run-1" / "out.txt")
    _age(mem / "jobs" / "run-1", 10)
    _write(proj / "__out" / "tmp" / ".disposable")
    _age(proj / "__out" / "tmp", 3)
    _config(proj, prune=[{"glob": "__out/*", "older_than_days": 1}])
    before = _snapshot(proj)
    for args in ([], ["report"], ["tidy"], ["prune"]):
        code, out = _main(proj, capsys, *args)
        assert code == 3, out
    assert _snapshot(proj) == before
    assert not (mem / H.LOG).exists()


def test_report_text_sections(proj, capsys):
    mem = proj / "aipack" / ".memory"
    _age(_write(mem / "old-notes.md"), 40)
    _write(proj / "__out" / "tmp" / ".disposable")
    _age(proj / "__out" / "tmp", 3)
    _write(proj / "tools" / "watch.py")
    _config(proj, budget_gb={"__out": 1})
    code, out = _main(proj, capsys)
    lines = out.splitlines()
    assert code == 3
    assert lines[0].startswith(f"housekeeping report: {proj} (pack aipack; config aipack/housekeeping.json")
    for head in ("Sizes:", "Budgets:", "Untidy: 1 to move", "Prune candidates: 1", "Layout notes: 1"):
        assert any(line.startswith(head) for line in lines), head
    assert "  ok    __out: " in out
    assert "  aipack/.memory/old-notes.md -> aipack/.memory/archive/" in out
    assert "[.disposable]" in out and "tools/watch.py: loose file in tools/" in out
    assert lines[-1].startswith("Total: ") and lines[-1].endswith(
        "exit 3: attention: untidy items, prune candidates")


def test_tidy_archives_memory_leftovers_by_month(proj, capsys):
    mem = proj / "aipack" / ".memory"
    runbook = _age(_write(mem / "runbook-0930.md", "abc"), 60)
    _write(mem / "compactions" / "a.json")
    _age(mem / "compactions", 45)
    _age(_write(mem / "hosts.json.bak-1"), 50)
    others = (".DS_Store", "notes.sync-conflict-20260930-151157-AAAAAAA.md", "spend.jsonl", "spec-v0.md")
    for name in others:
        _age(_write(mem / name), 50)
    (mem / "jobs").mkdir()
    kept = ("context.md", "directions.md", "hosts.json", "config.json", "interactions", "jobs", *others)
    code, res = _json(proj, capsys, "tidy")
    moved = {m["path"]: m["to"] for m in res["untidy"]}
    assert code == 3
    assert moved == {
        "aipack/.memory/runbook-0930.md": f"aipack/.memory/archive/{_month(runbook)}/runbook-0930.md",
        "aipack/.memory/compactions": f"aipack/.memory/archive/{_month(mem / 'compactions')}/compactions",
        "aipack/.memory/hosts.json.bak-1": f"aipack/.memory/archive/{_month(mem / 'hosts.json.bak-1')}/"
                                           "hosts.json.bak-1"}
    assert all(m["reason"] == "not on the .memory allowlist" for m in res["untidy"])
    code, out = _main(proj, capsys, "tidy", "--apply")
    assert code == 0, out
    assert (proj / moved["aipack/.memory/runbook-0930.md"]).read_text() == "abc"
    assert (proj / moved["aipack/.memory/compactions"] / "a.json").exists()
    assert not runbook.exists()
    for name in kept:
        assert os.path.lexists(mem / name), name
    log = _log(proj)
    assert {(e["path"], e["to"]) for e in log} == set(moved.items())
    for e in log:
        assert e["action"] == "move" and e["ts"].endswith("Z") and isinstance(e["bytes"], int) and e["rule"]
    assert _main(proj, capsys, "tidy")[0] == 0


def test_memory_allow_extends_the_allowlist(proj, capsys):
    mem = proj / "aipack" / ".memory"
    _age(_write(mem / "hosts.json.bak-1"), 50)
    _age(_write(mem / "PAUSED"), 50)
    _config(proj, memory_allow=["hosts.json.bak-*", "PAUSED"])
    code, res = _json(proj, capsys, "tidy")
    assert code == 0 and res["untidy"] == [] and res["untidy_held"] == []


def test_newest_handover_stays_older_ones_are_archived(proj, capsys):
    mem = proj / "aipack" / ".memory"
    _age(_write(mem / "handover-2026-09-01.md"), 30)
    _age(_write(mem / "handover-b.md"), 20)
    _age(_write(mem / "handover-c.md"), 10)
    _age(_write(mem / "handover-runtime.json"), 40)
    code, res = _json(proj, capsys, "tidy")
    assert {m["path"]: m["reason"] for m in res["untidy"]} == {
        "aipack/.memory/handover-2026-09-01.md": "older handover note",
        "aipack/.memory/handover-b.md": "older handover note",
        "aipack/.memory/handover-runtime.json": "not on the .memory allowlist"}
    assert _main(proj, capsys, "tidy", "--apply")[0] == 0
    assert (mem / "handover-c.md").exists() and not (mem / "handover-b.md").exists()
    assert _main(proj, capsys, "tidy")[0] == 0


def test_recent_items_stay(proj, capsys):
    mem = proj / "aipack" / ".memory"
    _write(mem / "scratch.md")
    deep = _write(mem / "jobs" / "run-2" / "deep" / "new.txt")
    _age(mem / "jobs" / "run-2", 10)
    os.utime(deep)   # one fresh file deep inside makes the whole folder recent
    code, res = _json(proj, capsys, "tidy")
    assert code == 0 and res["untidy"] == []
    assert _held(res, "untidy_held") == {"aipack/.memory/scratch.md": "recent",
                                         "aipack/.memory/jobs/run-2": "recent"}
    assert _main(proj, capsys, "tidy", "--apply")[0] == 0
    assert (mem / "scratch.md").exists() and deep.exists()
    assert not (mem / H.LOG).exists()
    _config(proj, keep_recent_hours=0)
    code, res = _json(proj, capsys, "tidy")
    assert code == 3 and len(res["untidy"]) == 2


def test_job_scratch_moves_to_out_jobs(proj, capsys):
    mem = proj / "aipack" / ".memory"
    _write(mem / "jobs" / "run-1" / "out.txt", "data")
    _age(mem / "jobs" / "run-1", 5)
    _age(_write(mem / "jobs" / "run-1-status.md"), 5)
    _write(proj / "__out" / "jobs" / "run-1" / "other.txt")
    code, res = _json(proj, capsys, "tidy")
    assert code == 3
    assert [(m["path"], m["to"], m["reason"], m["dir"]) for m in res["untidy"]] == [
        ("aipack/.memory/jobs/run-1", "__out/jobs/run-1-2", "job scratch", True)]
    assert _main(proj, capsys, "tidy", "--apply")[0] == 0
    assert (proj / "__out" / "jobs" / "run-1-2" / "out.txt").read_text() == "data"
    assert (proj / "__out" / "jobs" / "run-1" / "other.txt").exists()
    assert (mem / "jobs" / "run-1-status.md").exists() and not (mem / "jobs" / "run-1").exists()
    assert [(e["action"], e["path"], e["to"], e["bytes"], e["rule"]) for e in _log(proj)] == [
        ("move", "aipack/.memory/jobs/run-1", "__out/jobs/run-1-2", 4, "job scratch")]


def test_tidy_never_overwrites(proj, capsys):
    mem = proj / "aipack" / ".memory"
    old = _age(_write(mem / "old.md", "third"), 40)
    month = _month(old)
    _write(mem / "archive" / month / "old.md", "first")
    _write(mem / "archive" / month / "old-2.md", "second")
    code, res = _json(proj, capsys, "tidy")
    assert [m["to"] for m in res["untidy"]] == [f"aipack/.memory/archive/{month}/old-3.md"]
    assert _main(proj, capsys, "tidy", "--apply")[0] == 0
    assert [(mem / "archive" / month / n).read_text() for n in ("old.md", "old-2.md", "old-3.md")] == [
        "first", "second", "third"]


def test_tidy_without_memory_has_nothing_to_do(tmp_path, capsys):
    _write(tmp_path / "p" / "brain" / "manifest.json", json.dumps(PACK))
    code, res = _json(tmp_path / "p", capsys, "tidy")
    assert code == 0 and res["pack"] == "brain" and res["untidy"] == [] and res["reason"] == "nothing to do"


def test_prune_by_rule_and_age(proj, capsys):
    out = proj / "__out"
    for name, days in (("old", 10), ("mid", 3)):
        _write(out / name / "f.bin", "x" * 100)
        _age(out / name, days)
    _write(out / "fresh" / "f.bin")
    _age(_write(out / "old.npy", "y" * 10), 20)
    _config(proj, prune=[{"glob": "__out/*", "older_than_days": 7, "note": "run outputs"}])
    code, res = _json(proj, capsys, "prune")
    rule = "__out/* older than 7 days"
    assert code == 3
    assert [(c["path"], c["rule"], c["bytes"], c["dir"], c["how"]) for c in res["prune"]] == [
        ("__out/old", rule, 100, True, None), ("__out/old.npy", rule, 10, False, None)]
    assert res["prune"][0]["age_days"] >= 9.9
    assert _held(res) == {"__out/mid": "newer than 7 days", "__out/fresh": "recent"}
    code, text = _main(proj, capsys, "prune", "--apply")
    assert code == 0, text
    assert not (out / "old").exists() and not (out / "old.npy").exists()
    assert (out / "mid").exists() and (out / "fresh").exists()
    log = _log(proj)
    assert [(e["action"], e["path"], e["bytes"], e["rule"]) for e in log] == [
        ("delete", "__out/old", 100, rule), ("delete", "__out/old.npy", 10, rule)]
    assert all(e["ts"].endswith("Z") and "error" not in e and "how" not in e for e in log)
    assert _main(proj, capsys, "prune")[0] == 0


def test_prune_archive_with_pack_placeholder(proj, capsys):
    mem = proj / "aipack" / ".memory"
    _write(mem / "archive" / "2026-01" / "a.md")
    _age(mem / "archive" / "2026-01", 90)
    _age(_write(mem / "loose.md"), 90)
    _config(proj, prune=[{"glob": "{pack}/.memory/archive/*", "older_than_days": 30},
                         {"glob": "{pack}/.memory/*", "older_than_days": 30}])
    code, res = _json(proj, capsys, "prune")
    assert [c["path"] for c in res["prune"]] == ["aipack/.memory/archive/2026-01"]
    held = _held(res)
    assert held["aipack/.memory/loose.md"] == held["aipack/.memory/context.md"] == \
        "outside __data/, __out/ and the archive"


def test_disposable_and_keep_markers(proj, capsys):
    out, data = proj / "__out", proj / "__data"
    _write(out / "scratch" / ".disposable")
    _write(out / "scratch" / "big.bin", "z" * 50)
    _age(out / "scratch", 2)
    _write(data / "cache" / ".disposable")
    _write(data / "cache" / "sub" / ".keep")
    _age(data / "cache", 2)
    _write(out / "kept" / ".keep")
    _age(out / "kept", 30)
    _write(out / "under" / ".keep")
    _write(out / "under" / "inner" / ".disposable")
    _age(out / "under", 30)
    _write(out / "a" / ".disposable")
    _write(out / "a" / "b" / ".disposable")
    _age(out / "a", 2)
    _write(proj / "research" / "tmp" / ".disposable")
    _age(proj / "research" / "tmp", 30)
    _write(out / "new" / ".disposable")
    _config(proj, prune=[{"glob": "__out/kept", "older_than_days": 1}])
    code, res = _json(proj, capsys, "prune")
    assert code == 3
    assert {c["path"]: c["rule"] for c in res["prune"]} == {"__out/scratch": ".disposable", "__out/a": ".disposable"}
    assert _held(res) == {"__data/cache": ".keep", "__out/kept": ".keep", "__out/under/inner": ".keep",
                          "__out/new": "recent"}
    assert _main(proj, capsys, "prune", "--apply")[0] == 0
    assert not (out / "scratch").exists() and not (out / "a").exists()
    for kept in (data / "cache", out / "kept", out / "under" / "inner", out / "new", proj / "research" / "tmp"):
        assert kept.exists(), kept


def test_protect_globs(proj, capsys):
    for name in ("kaggle", "other"):
        _write(proj / "__data" / name / "f")
        _age(proj / "__data" / name, 30)
    _write(proj / "__out" / "a" / "b" / "f")
    _age(proj / "__out" / "a", 30)
    _write(proj / "__out" / "c" / "f")
    _age(proj / "__out" / "c", 30)
    _config(proj, prune=[{"glob": "__data/*", "older_than_days": 1}, {"glob": "__out/**", "older_than_days": 1}],
            protect=["__data/kaggle", "__out/a/b", "__out/c*"])
    code, res = _json(proj, capsys, "prune")
    assert [c["path"] for c in res["prune"]] == ["__data/other"]
    held = _held(res)
    assert held["__data/kaggle"] == held["__out/a"] == held["__out/a/b"] == held["__out/c"] == "protected"
    assert held["__out"] == "outside __data/, __out/ and the archive"
    assert _main(proj, capsys, "prune", "--apply")[0] == 0
    assert (proj / "__data" / "kaggle" / "f").exists() and (proj / "__out" / "a" / "b" / "f").exists()
    assert not (proj / "__data" / "other").exists()


def test_prune_refuses_symlinks_git_and_paths_outside(proj, tmp_path, capsys):
    outside = tmp_path / "outside"
    _write(outside / "precious.txt", "keep me")
    _age(outside, 30)
    (proj / "__out").mkdir()
    os.symlink(outside, proj / "__out" / "link")
    _write(proj / "__out" / "real" / "f")
    os.symlink(outside / "precious.txt", proj / "__out" / "real" / "ln")
    _age(proj / "__out" / "real", 30)
    _age(proj / "__out" / "link", 30)
    _write(proj / "__out" / "repo" / ".git" / "HEAD")
    _age(proj / "__out" / "repo", 30)
    _write(proj / "source" / "old" / "f")
    _age(proj / "source" / "old", 30)
    _config(proj, prune=[{"glob": "__out/*", "older_than_days": 1}, {"glob": "source/*", "older_than_days": 1},
                         {"glob": "__out/link/*", "older_than_days": 1}])
    code, res = _json(proj, capsys, "prune")
    assert [c["path"] for c in res["prune"]] == ["__out/real"]
    assert _held(res) == {"__out/link": "symlink", "__out/link/precious.txt": "symlink",
                          "__out/repo": "holds .git", "source/old": "outside __data/, __out/ and the archive"}
    assert _main(proj, capsys, "prune", "--apply")[0] == 0
    assert (outside / "precious.txt").read_text() == "keep me"
    assert (proj / "__out" / "link").is_symlink() and not (proj / "__out" / "real").exists()
    assert (proj / "__out" / "repo" / ".git" / "HEAD").exists() and (proj / "source" / "old").exists()


def test_double_star_skips_symlinked_folders_and_hidden_names(proj, capsys):
    out = proj / "__out"
    _write(out / "a" / "f")
    os.symlink(out, out / "a" / "loop")
    os.symlink(out, out / "loop2")
    _write(out / ".cache" / "f")
    _age(out, 30)
    _config(proj, prune=[{"glob": "__out/**", "older_than_days": 1}])
    code, res = _json(proj, capsys, "prune")
    assert [c["path"] for c in res["prune"]] == ["__out/a"]
    held = _held(res)
    assert held["__out/loop2"] == "symlink"
    assert not [p for p in held if ".cache" in p or "loop/" in p]
    assert _main(proj, capsys, "prune", "--apply")[0] == 0
    assert (out / ".cache" / "f").exists() and (out / "loop2").is_symlink() and not (out / "a").exists()


class _OtherDevice:
    """A stat result that claims another file system."""

    def __init__(self, st):
        self._st = st

    def __getattr__(self, name):
        return self._st.st_dev + 1 if name == "st_dev" else getattr(self._st, name)


def test_prune_refuses_mount_points(proj, capsys, monkeypatch):
    _write(proj / "__out" / "vol" / "f")
    _age(proj / "__out" / "vol", 30)
    _config(proj, prune=[{"glob": "__out/*", "older_than_days": 1}])
    target, real = os.path.join(os.path.realpath(proj), "__out", "vol"), os.lstat
    monkeypatch.setattr(H.os, "lstat", lambda p, *a, **k: _OtherDevice(real(p, *a, **k)) if os.fspath(p) == target
                        else real(p, *a, **k))
    code, res = _json(proj, capsys, "prune")
    assert code == 0 and res["prune"] == [] and _held(res) == {"__out/vol": "mount point"}


def test_a_failed_log_write_is_an_error(proj, capsys):
    shutil.rmtree(proj / "aipack" / ".memory")
    _write(proj / "aipack" / ".memory", "not a folder")
    _write(proj / "__out" / "old" / "f")
    _age(proj / "__out" / "old", 30)
    _config(proj, prune=[{"glob": "__out/*", "older_than_days": 1}])
    code, out = _main(proj, capsys, "prune", "--apply")
    assert code == 1 and "__out/old: not logged" in out and not (proj / "__out" / "old").exists()
    assert out.splitlines()[-1].endswith("exit 1: 1 action(s) failed")


def test_symlinked_data_root_is_never_walked(proj, tmp_path, capsys):
    big = tmp_path / "big"
    _write(big / "old" / "f")
    _write(big / "old" / ".disposable")
    _age(big, 30)
    os.symlink(big, proj / "__data")
    _config(proj, prune=[{"glob": "__data/*", "older_than_days": 1}])
    code, res = _json(proj, capsys, "prune")
    assert code == 0 and res["prune"] == []
    assert _held(res) == {"__data": "symlink: not walked", "__data/old": "symlink"}
    assert _main(proj, capsys, "prune", "--apply")[0] == 0
    assert (big / "old" / "f").exists()


@pytest.mark.skipif(os.geteuid() == 0, reason="root reads every folder")
def test_unreadable_tree_is_held(proj, capsys):
    locked = proj / "__out" / "old" / "locked"
    _write(locked / "f")
    _age(proj / "__out" / "old", 30)
    _config(proj, prune=[{"glob": "__out/*", "older_than_days": 1}])
    locked.chmod(0)
    try:
        code, res = _json(proj, capsys, "prune")
    finally:
        locked.chmod(0o755)
    assert code == 0 and res["prune"] == []
    assert _held(res) == {"__out/old": "unreadable parts"}
    assert res["unreadable"] == ["__out/old/locked"]


def test_budgets_and_exit_codes(proj, capsys):
    _write(proj / "__out" / "f", "x" * 2000)
    _config(proj, budget_gb={"project": 1, "__out": 1e-6})
    code, res = _json(proj, capsys)
    budgets = {b["name"]: b for b in res["budgets"]}
    assert code == 3 and res["reason"] == "attention: over budget"
    assert budgets["__out"]["over"] and budgets["__out"]["bytes"] == 2000 and budgets["__out"]["budget_bytes"] == 1073
    assert not budgets["project"]["over"] and budgets["project"]["bytes"] == res["sizes"]["total_bytes"]
    code, out = _main(proj, capsys, "tidy")
    assert code == 0, out   # tidy counts only its own items
    _config(proj, budget_gb={"__out": 1})
    code, out = _main(proj, capsys)
    assert code == 0 and out.splitlines()[-1].endswith("exit 0: nothing to do")


def test_json_shape(proj, capsys):
    code, res = _json(proj, capsys)
    assert code == res["exit"] == 0
    assert set(res) == {"project", "pack", "command", "apply", "config", "elapsed_s", "sizes", "budgets", "untidy",
                        "untidy_held", "prune", "prune_held", "layout", "live", "unreadable", "applied", "failed",
                        "exit", "reason"}
    assert res["live"] == {"jobs": [], "scripts": 0, "processes": 0, "complete": True, "note": ""}
    assert (res["command"], res["pack"], res["apply"]) == ("report", "aipack", False)
    assert res["config"] == {"file": None, "keep_recent_hours": 24}
    assert res["project"] == os.path.realpath(proj)
    entries = {e["path"]: e for e in res["sizes"]["entries"]}
    assert set(entries) == {"AGENTS.md", "README.md", "aipack", "source"}
    assert entries["AGENTS.md"] == {"path": "AGENTS.md", "dir": False, "bytes": 1, "files": 1}
    assert entries["aipack"]["dir"] is True and entries["source"]["bytes"] == 0
    assert res["sizes"]["total_bytes"] == sum(e["bytes"] for e in entries.values())
    assert res["budgets"] == [] and res["layout"] == [] and res["prune"] == [] and res["untidy"] == []
    code, res = _json(proj, capsys, "prune")
    assert res["sizes"] is res["budgets"] is res["untidy"] is res["layout"] is None and res["prune"] == []


def test_sizes_count_hard_links_once_and_never_follow_symlinks(proj, tmp_path, capsys, monkeypatch):
    monkeypatch.setattr(H, "LARGE", 1000)
    _write(proj / "__data" / "set" / "a.bin", "x" * 5000)
    (proj / "__out").mkdir()
    os.link(proj / "__data" / "set" / "a.bin", proj / "__out" / "a-link.bin")
    _write(tmp_path / "huge.bin", "y" * 100000)
    os.symlink(tmp_path / "huge.bin", proj / "__out" / "huge-link")
    _write(proj / "__out" / "small" / "f", "z" * 10)
    code, res = _json(proj, capsys)
    entries = {e["path"]: e["bytes"] for e in res["sizes"]["entries"]}
    assert entries["__data"] == 5000 and entries["__out"] < 1000
    assert [e["path"] for e in res["sizes"]["large"]] == ["__data/set"]


def test_layout_notes(proj, capsys):
    _write(proj / "tools" / "README.md")
    _write(proj / "tools" / "watch.py")
    _write(proj / "tools" / "hourly" / "hourly.py")
    _write(proj / "notes.txt")
    _write(proj / ".venv" / "x")
    (proj / "paper").mkdir()
    (proj / "__scratch").mkdir()
    code, res = _json(proj, capsys)
    assert code == 0   # layout notes are report-only
    assert {n["path"] for n in res["layout"]} == {"tools/watch.py", "notes.txt", "paper"}
    _config(proj, root_allow=["paper", "*.txt"])
    assert [n["path"] for n in _json(proj, capsys)[1]["layout"]] == ["tools/watch.py"]
    embedded = {**PACK, "project": {**PACK["project"], "mode": "embedded"}}
    _write(proj / "aipack" / "manifest.json", json.dumps(embedded))
    assert _json(proj, capsys)[1]["layout"] == []
    workspaces = {**PACK, "project": {**PACK["project"], "workspaces": ["paper"]}}
    _write(proj / "aipack" / "manifest.json", json.dumps(workspaces))
    _config(proj)
    assert {n["path"] for n in _json(proj, capsys)[1]["layout"]} == {"tools/watch.py", "notes.txt"}


@pytest.mark.parametrize("text, message", [
    ("{not json", "is unreadable"),
    ("[]", "must hold a JSON object"),
    ('{"keep_recent": 1}', "unknown key(s) keep_recent"),
    ('{"keep_recent_hours": -1}', "keep_recent_hours must be a number >= 0"),
    ('{"budget_gb": {"__out": "big"}}', "budget_gb['__out'] must be a number"),
    ('{"prune": [{"glob": "__out/*"}]}', "must be an object with glob and older_than_days"),
    ('{"prune": [{"glob": "../x", "older_than_days": 1}]}', "must stay inside the project"),
    ('{"prune": [{"glob": "/tmp/*", "older_than_days": 1}]}', "must stay inside the project"),
    ('{"prune": [{"glob": "./", "older_than_days": 1}]}', "names the project root itself"),
    ('{"protect": "__data"}', "protect must be a list"),
    ('{"protect_names": ["__data/raw"]}', "must be a name, not a path"),
    ('{"protect_names": "raw"}', "protect_names must be a list"),
])
def test_bad_config_is_an_error(proj, capsys, text, message):
    _write(proj / "aipack" / H.CONFIG, text)
    code, out = _main(proj, capsys, "prune", "--apply")
    assert code == 1 and message in out


def test_comment_keys_are_allowed(proj, capsys):
    _config(proj, _comment="why", keep_recent_hours=12)
    code, res = _json(proj, capsys)
    assert code == 0 and res["config"] == {"file": "aipack/housekeeping.json", "keep_recent_hours": 12}


def test_bad_dir_and_usage(tmp_path, capsys):
    assert H.main(["--dir", str(tmp_path / "missing")]) == 1
    assert "not found" in capsys.readouterr().out
    assert H.main(["--dir", str(tmp_path)]) == 1
    assert "no ai-pack" in capsys.readouterr().out
    for argv in (["--dir", str(tmp_path), "report", "--apply"], ["--dir", str(tmp_path), "sweep"], ["tidy"]):
        with pytest.raises(SystemExit) as exc:
            H.main(argv)
        assert exc.value.code == 2


def test_a_fresh_copy_with_old_mtimes_counts_as_recent(proj, capsys, monkeypatch):
    monkeypatch.setattr(H, "_changed", REAL_CHANGED)
    _write(proj / "__out" / "copied" / "a.bin")
    _age(proj / "__out" / "copied", 60)   # like rsync -a: old mtimes, but a new ctime
    _config(proj, prune=[{"glob": "__out/*", "older_than_days": 7}])
    code, res = _json(proj, capsys, "prune")
    assert not res["prune"] and _held(res)["__out/copied"] == "recent"


def test_a_match_inside_a_recent_folder_is_held(proj, capsys):
    _age(_write(proj / "__out" / "run" / "ckpt-old.pt"), 30)
    _write(proj / "__out" / "run" / "ckpt-new.pt")   # the run is still writing
    _config(proj, prune=[{"glob": "__out/*/*.pt", "older_than_days": 7}])
    code, res = _json(proj, capsys, "prune")
    assert not res["prune"]
    assert _held(res)["__out/run/ckpt-old.pt"] == "inside a recent folder"


def test_tidy_holds_jobs_named_in_context_or_handover(proj, capsys):
    mem = proj / "aipack" / ".memory"
    for job in ("live-job", "done-job"):
        _write(mem / "jobs" / job / "out.txt")
        _age(mem / "jobs" / job, 10)
    _age(_write(mem / "context.md", "running: live-job on a remote host"), 30)
    code, res = _json(proj, capsys, "tidy")
    assert [m["path"] for m in res["untidy"]] == ["aipack/.memory/jobs/done-job"]
    assert _held(res, "untidy_held")["aipack/.memory/jobs/live-job"] == "named in context or handover"


def test_tidy_skips_dot_names_and_paused_and_matches_case_insensitively(proj, capsys):
    mem = proj / "aipack" / ".memory"
    for name in (".lock", "PAUSED", "Directions.md"):
        _age(_write(mem / name), 40)
    code, res = _json(proj, capsys, "tidy")
    assert not res["untidy"]


def test_tidy_holds_keep_trees(proj, capsys):
    mem = proj / "aipack" / ".memory"
    _write(mem / "jobs" / "kept" / ".keep")
    _age(mem / "jobs" / "kept", 10)
    code, res = _json(proj, capsys, "tidy")
    assert not res["untidy"] and _held(res, "untidy_held")["aipack/.memory/jobs/kept"] == ".keep"


def test_protect_matches_case_insensitively(proj, capsys):
    _age(_write(proj / "__data" / "Kaggle" / "big.bin"), 30)
    _config(proj, prune=[{"glob": "__data/*", "older_than_days": 7}], protect=["__data/kaggle"])
    code, res = _json(proj, capsys, "prune")
    assert not res["prune"] and _held(res)["__data/Kaggle"] == "protected"


def test_prune_rechecks_each_candidate_before_deleting(proj):
    _age(_write(proj / "__out" / "old" / "a.bin"), 30)
    done, failed = H.apply_prune(str(proj), str(proj / "aipack" / ".memory" / H.LOG),
                                 [{"path": "__out/old", "rule": "r", "bytes": 1}], lambda c: "recent")
    assert not done and "refused on recheck: recent" in failed[0]["error"]
    assert (proj / "__out" / "old" / "a.bin").exists()


def test_a_file_move_never_replaces_a_new_target(proj):
    src = _write(proj / "aipack" / ".memory" / "old.md", "old")
    dst = _write(proj / "aipack" / ".memory" / "archive" / "2026-01" / "old.md", "new")
    done, failed = H.apply_moves(str(proj), str(proj / "aipack" / ".memory" / H.LOG),
                                 [{"path": "aipack/.memory/old.md", "to": "aipack/.memory/archive/2026-01/old.md",
                                   "bytes": 3, "reason": "r"}])
    assert not done and failed and src.read_text() == "old" and dst.read_text() == "new"


def test_budget_keys_must_be_paths_and_missing_paths_need_attention(proj, capsys):
    _config(proj, budget_gb={"__data/*": 1})
    code, out = _main(proj, capsys, "report")
    assert code == 1 and "must be a path" in out + capsys.readouterr().err
    _config(proj, budget_gb={"__nothing": 1})
    code, res = _json(proj, capsys, "report")
    assert code == H.ATTENTION
    assert res["budgets"][0]["measured"] is False and "missing" in res["budgets"][0]["note"]


def test_recreatable_rule_prunes_without_asking_and_logs_how(proj, capsys):
    data = proj / "__data" / "downloads"
    for name in ("set-a", "set-b"):
        _write(data / name / "part.bin", "x" * 20)
        _age(data / name, 5)
    _age(data, 5)
    how = "tools/fetch/fetch.sh <name> downloads it again"
    _config(proj, prune=[{"glob": "__data/downloads/*", "older_than_days": 2, "recreatable": True, "how": how}])
    code, out = _main(proj, capsys, "prune")
    rule = "__data/downloads/* older than 2 days, re-creatable"
    assert code == 3 and f"__data/downloads/set-a/  [{rule}]  how: {how}" in out
    code, res = _json(proj, capsys, "prune")
    assert {c["path"]: (c["rule"], c["how"]) for c in res["prune"]} == {
        "__data/downloads/set-a": (rule, how), "__data/downloads/set-b": (rule, how)}
    assert _held(res) == {}
    code, out = _main(proj, capsys, "prune", "--apply")
    assert code == 0 and f"__data/downloads/set-a  (how: {how})" in out
    assert not (data / "set-a").exists() and not (data / "set-b").exists()
    assert [(e["action"], e["path"], e["rule"], e["how"], e["bytes"]) for e in _log(proj)] == [
        ("delete", "__data/downloads/set-a", rule, how, 20), ("delete", "__data/downloads/set-b", rule, how, 20)]


def test_recreatable_marker_needs_a_how_line(proj, capsys):
    out = proj / "__out"
    _write(out / "feats" / ".recreatable", "python tools/feats/build.py rebuilds it (20 min)\nmore notes\n")
    _write(out / "feats" / "f.npy", "x" * 30)
    _age(out / "feats", 3)
    _write(out / "blank" / ".recreatable", "\n")
    _age(out / "blank", 3)
    _write(out / "both" / ".recreatable", "rebuild with make both")
    _write(out / "both" / ".disposable")
    _age(out / "both", 3)
    _write(out / "new" / ".recreatable", "rebuild")
    _write(proj / "research" / "x" / ".recreatable", "rebuild")
    _age(proj / "research" / "x", 30)
    code, res = _json(proj, capsys, "prune")
    assert {c["path"]: (c["rule"], c["how"]) for c in res["prune"]} == {
        "__out/feats": (".recreatable", "python tools/feats/build.py rebuilds it (20 min)"),
        "__out/both": (".recreatable", "rebuild with make both")}
    held = _held(res)
    assert held["__out/blank"].startswith("no how") and held["__out/new"] == "recent"
    assert _main(proj, capsys, "prune", "--apply")[0] == 0
    assert not (out / "feats").exists() and (out / "blank").exists() and (proj / "research" / "x").exists()
    assert {e["path"]: e["how"] for e in _log(proj)}["__out/feats"].startswith("python tools/feats/build.py")


def test_recreatable_data_keeps_every_guard(proj, capsys):
    data = proj / "__data" / "cache"
    for name in ("young", "kept", "guarded", "old"):
        _write(data / name / "f")
    _write(data / "kept" / ".keep")
    _age(data, 30)
    _age(data / "young", 3)
    _write(proj / "__data" / "busy" / "old" / "f")
    _age(proj / "__data" / "busy", 30)
    _write(proj / "__data" / "busy" / "new" / "f")   # the folder around a match is still being written
    _config(proj, protect=["__data/cache/guarded"],
            prune=[{"glob": "__data/cache/*", "older_than_days": 7, "recreatable": True, "how": "rebuild"},
                   {"glob": "__data/busy/*", "older_than_days": 7, "recreatable": True, "how": "rebuild"}])
    code, res = _json(proj, capsys, "prune")
    assert [c["path"] for c in res["prune"]] == ["__data/cache/old"]
    assert _held(res) == {"__data/cache/young": "newer than 7 days", "__data/cache/kept": ".keep",
                          "__data/cache/guarded": "protected", "__data/busy/old": "inside a recent folder",
                          "__data/busy/new": "recent"}


def test_never_delete_list_wins_over_rules_and_markers(proj, capsys):
    out = proj / "__out"
    _write(out / "Submissions" / "s1.csv")
    _write(out / "run-a" / "Submission.csv")
    _write(out / "run-b" / "shard0" / "traces" / "t.jsonl")
    _write(out / "run-c" / "train.log")
    _write(out / "run-d" / "logs" / "x.txt")
    _write(out / "run-e" / "out.npy")
    _write(out / "run-f" / "x.TRACE")
    _write(out / "scratch" / ".disposable")
    _write(out / "scratch" / "job.log.1")
    _age(out, 30)
    rules = [{"glob": "__out/*", "older_than_days": 1, "recreatable": True, "how": "rerun"},
             {"glob": "__out/Submissions/*", "older_than_days": 1}]
    _config(proj, prune=rules)
    code, res = _json(proj, capsys, "prune")
    assert [c["path"] for c in res["prune"]] == ["__out/run-e"]
    held = {h["path"]: (h["held"], h.get("entry")) for h in res["prune_held"]}
    never = {
        "__out/Submissions": ("never deleted: submission", "__out/Submissions"),
        "__out/Submissions/s1.csv": ("never deleted: submission", "__out/Submissions"),
        "__out/run-a": ("never deleted: submission", "__out/run-a/Submission.csv"),
        "__out/run-b": ("never deleted: raw trace", "__out/run-b/shard0/traces"),
        "__out/run-f": ("never deleted: raw trace", "__out/run-f/x.TRACE")}
    assert held == {**never, "__out/run-c": ("protected name", "__out/run-c/train.log"),
                    "__out/run-d": ("protected name", "__out/run-d/logs"),
                    "__out/scratch": ("protected name", "__out/scratch/job.log.1")}
    assert _main(proj, capsys, "prune", "--apply")[0] == 0
    assert not (out / "run-e").exists()
    assert all((out / n).exists() for n in ("Submissions", "run-a", "run-b", "run-c", "run-d", "run-f", "scratch"))
    # a project that drops the default protect_names prunes run logs by its rules; the never list stays
    _config(proj, prune=rules, protect_names=[])
    code, res = _json(proj, capsys, "prune")
    assert sorted(c["path"] for c in res["prune"]) == ["__out/run-c", "__out/run-d", "__out/scratch"]
    assert {h["path"]: (h["held"], h.get("entry")) for h in res["prune_held"]} == never


def test_protect_names_default_holds_raw_data_until_the_project_changes_it(proj, capsys):
    data = proj / "__data"
    _write(data / "raw" / "comp.zip")
    _write(data / "feats" / "raw_cols.npy")
    _age(data, 30)
    rule = {"glob": "__data/*", "older_than_days": 7, "recreatable": True, "how": "download the data again"}
    _config(proj, prune=[rule])
    code, res = _json(proj, capsys, "prune")
    assert res["prune"] == []
    assert {h["path"]: (h["held"], h.get("entry")) for h in res["prune_held"]} == {
        "__data/raw": ("protected name", "__data/raw"), "__data/feats": ("protected name", "__data/feats/raw_cols.npy")}
    _config(proj, prune=[rule], protect_names=["raw_*"])
    code, res = _json(proj, capsys, "prune")
    assert [c["path"] for c in res["prune"]] == ["__data/raw"] and _held(res) == {"__data/feats": "protected name"}
    assert _main(proj, capsys, "prune", "--apply")[0] == 0
    assert not (data / "raw").exists() and (data / "feats" / "raw_cols.npy").exists()
    assert _log(proj)[0]["how"] == "download the data again"


def test_recreatable_rules_never_reach_the_pack(proj, capsys):
    mem = proj / "aipack" / ".memory"
    _write(mem / "archive" / "2026-01" / "a.md")
    _age(mem / "archive" / "2026-01", 90)
    _config(proj, prune=[{"glob": "{pack}/.memory/archive/*", "older_than_days": 30, "recreatable": True,
                          "how": "none"}])
    code, res = _json(proj, capsys, "prune")
    assert not res["prune"]
    assert _held(res) == {"aipack/.memory/archive/2026-01": "in the pack: re-creatable rules never reach it"}


@pytest.mark.parametrize("rule, message", [
    ({"recreatable": True}, "a recreatable rule needs how"),
    ({"recreatable": True, "how": "  "}, "a recreatable rule needs how"),
    ({"recreatable": True, "how": "line one\nline two"}, "how must be one line"),
    ({"recreatable": True, "how": "x" * 301}, "how must be one line of at most 300"),
    ({"how": "rebuild"}, 'how goes with "recreatable": true'),
    ({"recreatable": "yes", "how": "rebuild"}, "recreatable must be true or false"),
    ({"recreatable": True, "how": "x", "why": "y"}, "optional recreatable, how and note"),
])
def test_bad_recreatable_rules(proj, capsys, rule, message):
    _config(proj, prune=[{"glob": "__out/*", "older_than_days": 1, **rule}])
    code, out = _main(proj, capsys, "prune", "--apply")
    assert code == 1 and message in out


def _kit(root, rel, text, days=10):
    """A job kit: one script at ``rel`` (inside the project) with ``text``, its folder aged ``days``."""
    path = _write(root / rel, text)
    _age(path.parent, days)
    return path


def test_tidy_holds_a_job_folder_a_live_kit_still_calls(proj, capsys):
    mem = proj / "aipack" / ".memory"
    _kit(proj, "aipack/.memory/jobs/engine-reads/rx.sh", "#!/bin/sh\nssh host run\n")
    _kit(proj, "aipack/.memory/jobs/poller/poll.sh",
         "#!/bin/sh\nwhile true; do bash aipack/.memory/jobs/engine-reads/rx.sh; sleep 60; done\n")
    _kit(proj, "aipack/.memory/jobs/finished/run.sh", "echo done\n")
    _age(_write(mem / "context.md", "Running: poller (checks the host every minute)."), 30)
    code, out = _main(proj, capsys, "tidy")
    assert code == 3
    assert ("  held  aipack/.memory/jobs/engine-reads/  used by a live job: aipack/.memory/jobs/poller/poll.sh"
            in out)
    assert "Live jobs: 2 (2 scripts read, 0 processes)" in out
    assert "  aipack/.memory/jobs/poller  (named in context or handover)" in out
    assert "  aipack/.memory/jobs/engine-reads  (used by a live job)" in out
    code, res = _json(proj, capsys, "tidy")
    assert [m["path"] for m in res["untidy"]] == ["aipack/.memory/jobs/finished"]
    held = {h["path"]: (h["held"], h.get("by")) for h in res["untidy_held"]}
    assert held == {"aipack/.memory/jobs/engine-reads": ("used by a live job", "aipack/.memory/jobs/poller/poll.sh"),
                    "aipack/.memory/jobs/poller": ("named in context or handover", "aipack/.memory/context.md")}
    assert _main(proj, capsys, "tidy", "--apply")[0] == 0
    assert (mem / "jobs" / "engine-reads" / "rx.sh").exists() and not (mem / "jobs" / "finished").exists()


def test_live_jobs_chain_and_hold_prune_candidates(proj, capsys):
    # a recent job calls a kit by a variable path; the kit, live in turn, reads a data folder
    _write(proj / "__out" / "jobs" / "fresh" / "go.py", 'KIT = f"{JOBS}/kit-a/run.sh"\n')
    _kit(proj, "aipack/.memory/jobs/kit-a/run.sh", "python x.py --data __data/model-x/weights.bin\n")
    _kit(proj, "aipack/.memory/jobs/kit-a/sub/conf.yaml", "data: __data/model-y\n")
    _kit(proj, "aipack/.memory/jobs/kit-a/sub/deeper/conf.yaml", "data: __data/model-z\n")
    _write(proj / "__out" / "jobs" / "fresh" / "secrets.env", "DATA=__data/model-w\n")   # never read
    _age(proj / "aipack" / ".memory" / "jobs" / "kit-a", 10)
    for name in ("model-w", "model-x", "model-y", "model-z", "other"):
        _write(proj / "__data" / name / "weights.bin")
        _age(proj / "__data" / name, 30)
    _config(proj, prune=[{"glob": "__data/*", "older_than_days": 7, "recreatable": True, "how": "download"}])
    code, res = _json(proj, capsys, "prune")
    assert {j["path"]: j["why"] for j in res["live"]["jobs"]} == {"__out/jobs/fresh": "recent",
                                                                 "aipack/.memory/jobs/kit-a": "used by a live job"}
    assert sorted(c["path"] for c in res["prune"]) == ["__data/model-w", "__data/model-z", "__data/other"]
    held = {h["path"]: (h["held"], h.get("by")) for h in res["prune_held"]}
    assert held == {"__data/model-x": ("used by a live job", "aipack/.memory/jobs/kit-a/run.sh"),
                    "__data/model-y": ("used by a live job", "aipack/.memory/jobs/kit-a/sub/conf.yaml")}


def test_running_processes_hold_what_they_name(proj, capsys, monkeypatch):
    root = os.path.realpath(proj)
    _kit(proj, "__out/jobs/runner/go.py", "open('__data/set-b/x')\n")
    for name in ("set-a", "set-b", "set-c"):
        _write(proj / "__data" / name / "x")
        _age(proj / "__data" / name, 30)
    cmd = f"python {root}/__out/jobs/runner/go.py --in __data/set-a"
    monkeypatch.setattr(H, "_processes", lambda: [(4242, cmd), (77, "uv run x --dir /elsewhere/__data/set-c")])
    _config(proj, prune=[{"glob": "__data/*", "older_than_days": 7, "recreatable": True, "how": "download"}])
    code, res = _json(proj, capsys, "prune")
    assert res["live"]["processes"] == 2
    assert res["live"]["jobs"] == [{"path": "__out/jobs/runner", "why": "used by a process"}]
    held = {h["path"]: (h["held"], h.get("by")) for h in res["prune_held"]}
    assert held == {"__data/set-a": ("used by a process", "process 4242 (python): __data/set-a"),
                    "__data/set-b": ("used by a live job", "__out/jobs/runner/go.py"),
                    "__data/set-c": ("used by a process", "process 77 (uv): __data/set-c")}
    assert res["prune"] == []


def test_process_command_lines_are_never_printed(proj, capsys, monkeypatch):
    secret = "hunter2-secret"
    _kit(proj, "__out/jobs/fetch/run.sh", "echo\n")
    _write(proj / "__data" / "set" / "x")
    _age(proj / "__data" / "set", 30)
    monkeypatch.setattr(H, "_processes", lambda: [
        (11, f"/usr/local/bin/fetcher --token {secret} --out __data/set/x"),
        (12, f"/opt/x/run-$(id) -p{secret} __out/jobs/fetch/run.sh")])
    _config(proj, prune=[{"glob": "__data/*", "older_than_days": 7, "recreatable": True, "how": "download"}])
    for args in (("prune",), ("prune", "--json"), ("tidy",), ("tidy", "--json"), ("report", "--json")):
        code, out = _main(proj, capsys, *args)
        assert secret not in out and "--token" not in out, args
    code, res = _json(proj, capsys, "prune")
    assert [(h["held"], h["by"]) for h in res["prune_held"]] == [
        ("used by a process", "process 11 (fetcher): __data/set")]
    assert res["live"]["jobs"] == [{"path": "__out/jobs/fetch", "why": "used by a process"}]
    assert H._exe_name("/opt/x/run-$(id) -p1") == "run-id" and H._exe_name("") == "?"


def test_prune_holds_what_context_names_and_what_live_jobs_hold(proj, capsys):
    mem = proj / "aipack" / ".memory"
    for rel in ("__out/cache/feat-1", "__out/cache/feat-2", "__out/jobs/live-a/ckpt", "__out/jobs/done-b/ckpt"):
        _write(proj / rel / "part.npy")
    _age(proj / "__out", 30)
    _age(_write(mem / "context.md", "Next: read __out/cache/feat-1/part.npy; live-a is still running."), 30)
    _age(_write(mem / "handover-1.md", "old note naming __out/cache/feat-2"), 40)
    _age(_write(mem / "handover-2.md", "newest note"), 20)
    _config(proj, prune=[{"glob": "__out/cache/*", "older_than_days": 7, "recreatable": True, "how": "rebuild"},
                         {"glob": "__out/jobs/*/ckpt", "older_than_days": 7, "recreatable": True, "how": "rerun"}])
    code, res = _json(proj, capsys, "prune")
    assert sorted(c["path"] for c in res["prune"]) == ["__out/cache/feat-2", "__out/jobs/done-b/ckpt"]
    held = {h["path"]: (h["held"], h.get("by")) for h in res["prune_held"]}
    assert held == {"__out/cache/feat-1": ("named in context or handover", "aipack/.memory/context.md"),
                    "__out/jobs/live-a/ckpt": ("live job", "__out/jobs/live-a (named in context or handover)")}
    code, out = _main(proj, capsys, "prune")
    assert "  held  __out/cache/feat-1/  named in context or handover: aipack/.memory/context.md" in out


def test_an_incomplete_script_scan_holds_everything(proj, capsys, monkeypatch):
    monkeypatch.setattr(H, "SCAN_FILES", 1)
    _write(proj / "__out" / "jobs" / "fresh" / "a.sh", "echo a\n")
    _write(proj / "__out" / "jobs" / "fresh" / "b.sh", "echo b\n")
    _write(proj / "__data" / "old" / "x")
    _age(proj / "__data" / "old", 30)
    _age(_write(proj / "aipack" / ".memory" / "stray.md"), 30)
    _config(proj, prune=[{"glob": "__data/*", "older_than_days": 7, "recreatable": True, "how": "download"}])
    code, res = _json(proj, capsys, "prune")
    assert res["prune"] == [] and _held(res) == {"__data/old": "references not fully read"}
    assert res["live"]["complete"] is False and "every move and deletion held" in res["live"]["note"]
    code, res = _json(proj, capsys, "tidy")
    assert res["untidy"] == [] and _held(res, "untidy_held") == {"aipack/.memory/stray.md": "references not fully read"}
    code, out = _main(proj, capsys, "tidy")
    assert "every move and deletion held" in out.splitlines()[1]


def test_a_failed_process_list_holds_every_move_and_deletion(proj, capsys, monkeypatch):
    def fail():
        raise OSError("ps exit 1")
    monkeypatch.setattr(H, "_processes", fail)
    _write(proj / "__data" / "old" / "x")
    _age(proj / "__data" / "old", 30)
    _age(_write(proj / "aipack" / ".memory" / "stray.md"), 30)
    _config(proj, prune=[{"glob": "__data/*", "older_than_days": 7, "recreatable": True, "how": "download"}])
    note = "processes not read (ps exit 1): every move and deletion held"
    code, res = _json(proj, capsys, "prune")
    assert code == 0 and res["prune"] == [] and _held(res) == {"__data/old": "references not fully read"}
    assert res["live"]["processes"] is None and res["live"]["complete"] is False and res["live"]["note"] == note
    code, out = _main(proj, capsys, "prune", "--apply")
    assert code == 0 and f"Live jobs: 0 (0 scripts read, no process list); {note}" in out
    assert (proj / "__data" / "old" / "x").exists()
    code, out = _main(proj, capsys, "tidy", "--apply")
    assert code == 0 and (proj / "aipack" / ".memory" / "stray.md").exists()
    assert not (proj / "aipack" / ".memory" / H.LOG).exists()


@pytest.mark.skipif(shutil.which("ps") is None, reason="no ps")
def test_the_process_list_leaves_out_this_process(monkeypatch):
    monkeypatch.undo()
    procs = H._processes()
    assert procs and all(isinstance(pid, int) and cmd for pid, cmd in procs)
    assert os.getpid() not in {pid for pid, _ in procs} and os.getppid() not in {pid for pid, _ in procs}


def test_help_describes_recreatable_data_and_holds(capsys):
    with pytest.raises(SystemExit) as exc:
        H.main(["--help"])
    out = capsys.readouterr().out
    assert exc.value.code == 0
    for text in ('"recreatable": true', ".recreatable marker", "never deleted", "submission*", "held while in use",
                 "protect_names", '"raw", "raw-*"'):
        assert text in out, text


def _link(root, tmp_path, name):
    """Keep the data root ``name`` on "another drive": a folder outside the project, linked into its root."""
    target = tmp_path / "drive" / name
    target.mkdir(parents=True)
    os.symlink(target, root / name)
    return target


def _other_drive(monkeypatch, drive):
    """Make renames and hard links between ``drive`` and the rest fail like moves across file systems."""
    base, rename, link = os.path.realpath(drive) + os.sep, os.rename, os.link

    def on_drive(path):
        return (os.path.realpath(os.path.dirname(path)) + os.sep).startswith(base)

    def guarded(real):
        def call(src, dst, *args, **kwargs):
            if on_drive(src) != on_drive(dst):
                raise OSError(errno.EXDEV, "Invalid cross-device link")
            return real(src, dst, *args, **kwargs)
        return call
    monkeypatch.setattr(H.os, "rename", guarded(rename))
    monkeypatch.setattr(H.os, "link", guarded(link))


def test_linked_root_is_measured_budgeted_and_pruned(proj, tmp_path, capsys, monkeypatch):
    monkeypatch.setattr(H, "LARGE", 1000)
    out = _link(proj, tmp_path, "__out")
    _write(out / "old" / "f", "x" * 3000)
    _write(out / "marked" / "g", "y" * 10)
    _write(out / "marked" / ".disposable", "")
    _write(out / "jobs" / "run-9" / "run.sh", "echo")
    _age(out / "old", 30)
    _age(out / "marked", 30)
    # as on a real second drive: the link itself sits on the project's file system, its target on another
    spot, real = os.path.join(os.path.realpath(proj), "__out"), os.lstat
    monkeypatch.setattr(H.os, "lstat", lambda p, *a, **k: _OtherDevice(real(p, *a, **k)) if os.fspath(p) == spot
                        else real(p, *a, **k))
    _config(proj, linked_roots=["__out", "__data"], budget_gb={"__out": 1e-6, "project": 1},
            prune=[{"glob": "__out/old", "older_than_days": 1}])
    code, res = _json(proj, capsys)
    target = os.path.realpath(out)
    assert code == 3 and res["config"]["linked_roots"] == {"__out": target}
    entry = next(e for e in res["sizes"]["entries"] if e["path"] == "__out")
    assert entry == {"path": "__out", "dir": True, "bytes": 3014, "files": 4, "link": target}
    assert [e["path"] for e in res["sizes"]["large"]] == ["__out/old"]
    budgets = {b["name"]: b for b in res["budgets"]}
    assert budgets["__out"]["measured"] and budgets["__out"]["over"] and budgets["__out"]["bytes"] == 3014
    assert budgets["project"]["bytes"] == res["sizes"]["total_bytes"] > 3014
    assert res["live"]["jobs"] == [{"path": "__out/jobs/run-9", "why": "recent"}]
    assert sorted(c["path"] for c in res["prune"]) == ["__out/marked", "__out/old"] and _held(res) == {}
    code, text = _main(proj, capsys)
    assert f"__out/ -> {target}" in text and "not measured" not in text
    assert _main(proj, capsys, "prune", "--apply")[0] == 0
    assert not (out / "old").exists() and not (out / "marked").exists() and (out / "jobs" / "run-9").exists()
    assert (proj / "__out").is_symlink()
    assert sorted((e["action"], e["path"]) for e in _log(proj)) == [("delete", "__out/marked"),
                                                                    ("delete", "__out/old")]


def test_leading_double_star_reaches_into_a_linked_root(proj, tmp_path, capsys):
    out = _link(proj, tmp_path, "__out")
    _write(out / "a" / "cache" / "f")
    _write(out / "b" / "keepme" / "f")
    _age(out / "a", 30)
    _age(out / "b", 30)
    _config(proj, linked_roots=["__out"], prune=[{"glob": "**/cache", "older_than_days": 1}])
    code, res = _json(proj, capsys, "prune")
    assert [c["path"] for c in res["prune"]] == ["__out/a/cache"]
    _config(proj, linked_roots=["__out"], prune=[{"glob": "__out/*", "older_than_days": 1}], protect=["**/keepme"])
    code, res = _json(proj, capsys, "prune")
    assert [c["path"] for c in res["prune"]] == ["__out/a"] and _held(res) == {"__out/b": "protected"}


def test_a_symlink_below_a_linked_root_and_an_unlisted_link_are_never_followed(proj, tmp_path, capsys):
    out = _link(proj, tmp_path, "__out")
    outside, big = tmp_path / "outside", tmp_path / "big"
    _write(outside / "precious.txt")
    _write(big / "old" / "f")
    os.symlink(outside, out / "link")
    os.symlink(big, proj / "__data")
    for path in (outside, big, out / "link"):
        _age(path, 30)
    _config(proj, linked_roots=["__out"], budget_gb={"__data": 1},
            prune=[{"glob": "__out/*", "older_than_days": 1}, {"glob": "__out/link/*", "older_than_days": 1},
                   {"glob": "__data/*", "older_than_days": 1}])
    code, res = _json(proj, capsys)
    assert res["prune"] == [] and next(e for e in res["sizes"]["entries"] if e["path"] == "__out")["files"] == 1
    assert _held(res) == {"__data": "symlink: not walked", "__data/old": "symlink", "__out/link": "symlink",
                          "__out/link/precious.txt": "symlink"}
    assert [(b["name"], b["measured"]) for b in res["budgets"]] == [("__data", False)]
    assert _main(proj, capsys, "prune", "--apply")[0] == 0
    assert (outside / "precious.txt").exists() and (big / "old" / "f").exists() and (out / "link").is_symlink()


def test_linked_roots_config_and_targets_are_checked(proj, tmp_path, capsys):
    _config(proj, linked_roots=["source"])
    code, out = _main(proj, capsys)
    assert code == 1 and "linked_roots entry 'source' must be one of __data, __out" in out
    _config(proj, linked_roots=["__out", "__data"])
    (proj / "__data").mkdir()
    assert _main(proj, capsys)[0] == 0   # listed, but a real folder or absent: nothing to follow
    os.symlink(tmp_path / "unplugged", proj / "__out")
    code, out = _main(proj, capsys)
    assert code == 1 and "linked root __out" in out and "is its drive mounted?" in out
    os.unlink(proj / "__out")
    os.symlink(proj / "source", proj / "__out")
    code, out = _main(proj, capsys, "prune", "--apply")
    assert code == 1 and "must point outside the project" in out
    os.unlink(proj / "__out")
    os.symlink(tmp_path, proj / "__out")
    assert _main(proj, capsys)[0] == 1   # a folder that holds the project is no data root either
    os.unlink(proj / "__out")
    (proj / "__data").rmdir()
    (tmp_path / "drive" / "sub").mkdir(parents=True)
    os.symlink(tmp_path / "drive", proj / "__data")
    os.symlink(tmp_path / "drive" / "sub", proj / "__out")
    code, out = _main(proj, capsys)
    assert code == 1 and "overlap" in out


def test_a_linked_root_changed_during_the_run_is_a_plain_symlink_again(proj, tmp_path):
    out = _link(proj, tmp_path, "__out")
    root = os.path.realpath(proj)
    linked = H.linked_roots(root, {"linked_roots": ["__out"]})
    assert linked == {"__out": os.path.realpath(out)}
    assert H._project_rel(root, os.path.realpath(out / "jobs" / "a.sh"), linked) == "__out/jobs/a.sh"
    assert H._project_rel(root, os.path.realpath(out / "jobs" / "a.sh")) == ""
    assert not H._symlink_on_path(root, "__out/old", linked) and H._symlink_on_path(root, "__out/old")
    elsewhere = tmp_path / "elsewhere"
    _age(_write(elsewhere / "old" / "f"), 30)
    os.unlink(proj / "__out")
    os.symlink(elsewhere, proj / "__out")
    assert H._symlink_on_path(root, "__out/old", linked)
    assert not H._inside(os.path.join(root, "__out/old"), root, linked)
    done, failed = H.apply_prune(root, str(proj / "aipack" / ".memory" / H.LOG),
                                 [{"path": "__out/old", "rule": "r", "bytes": 1}], None, linked)
    assert not done and "refused" in failed[0]["error"] and (elsewhere / "old" / "f").exists()


def test_tidy_moves_job_scratch_onto_a_linked_root_across_file_systems(proj, tmp_path, capsys, monkeypatch):
    out = _link(proj, tmp_path, "__out")
    _other_drive(monkeypatch, tmp_path / "drive")
    mem = proj / "aipack" / ".memory"
    _write(mem / "jobs" / "run-1" / "sub" / "out.txt", "data")
    os.symlink("out.txt", mem / "jobs" / "run-1" / "sub" / "ln")
    _age(mem / "jobs" / "run-1", 5)
    code, res = _json(proj, capsys, "tidy")   # not listed: the move would leave the project
    assert res["untidy"] == [] and _held(res, "untidy_held") == {"aipack/.memory/jobs/run-1": "outside the project"}
    _config(proj, linked_roots=["__out"])
    code, res = _json(proj, capsys, "tidy")
    assert [(m["path"], m["to"]) for m in res["untidy"]] == [("aipack/.memory/jobs/run-1", "__out/jobs/run-1")]
    assert _main(proj, capsys, "tidy", "--apply")[0] == 0
    moved = out / "jobs" / "run-1"
    assert (moved / "sub" / "out.txt").read_text() == "data" and os.readlink(moved / "sub" / "ln") == "out.txt"
    assert not (mem / "jobs" / "run-1").exists() and [p.name for p in (out / "jobs").iterdir()] == ["run-1"]
    assert [(e["action"], e["path"], e["to"]) for e in _log(proj)] == [
        ("move", "aipack/.memory/jobs/run-1", "__out/jobs/run-1")]


def test_a_move_across_file_systems_keeps_the_source_until_the_copy_matches(proj, tmp_path, monkeypatch):
    out = _link(proj, tmp_path, "__out")
    _other_drive(monkeypatch, tmp_path / "drive")
    root, log = os.path.realpath(proj), str(proj / "aipack" / ".memory" / H.LOG)
    linked = H.linked_roots(root, {"linked_roots": ["__out"]})
    src = _write(proj / "aipack" / ".memory" / "jobs" / "run-1" / "out.txt", "data")
    note = _write(proj / "aipack" / ".memory" / "note.md", "note")
    moves = [{"path": "aipack/.memory/jobs/run-1", "to": "__out/jobs/run-1", "bytes": 4, "reason": "job scratch"},
             {"path": "aipack/.memory/note.md", "to": "__out/note.md", "bytes": 4, "reason": "r"}]
    with monkeypatch.context() as patch:
        patch.setattr(H.filecmp, "cmp", lambda *a, **k: False)
        done, failed = H.apply_moves(root, log, moves, linked)
    assert not done and all("differs" in f["error"] for f in failed) and len(failed) == 2
    assert src.read_text() == "data" and note.read_text() == "note"
    assert list((out / "jobs").iterdir()) == [] and [p.name for p in out.iterdir()] == ["jobs"]
    os.mkfifo(proj / "aipack" / ".memory" / "jobs" / "run-1" / "pipe")
    done, failed = H.apply_moves(root, log, moves[:1], linked)
    assert not done and "special file" in failed[0]["error"] and src.exists()
    os.unlink(proj / "aipack" / ".memory" / "jobs" / "run-1" / "pipe")
    done, failed = H.apply_moves(root, log, moves, linked)
    assert not failed and [d["to"] for d in done] == ["__out/jobs/run-1", "__out/note.md"]
    assert (out / "jobs" / "run-1" / "out.txt").read_text() == "data" and (out / "note.md").read_text() == "note"
    assert not src.parent.exists() and not note.exists()
    assert sorted(p.name for p in out.iterdir()) == ["jobs", "note.md"]
