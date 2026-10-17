# Copyright 2026 Mikhail Yurasov <me@yurasov.me>
# SPDX-License-Identifier: Apache-2.0

"""Tests for solaris.tools.read_first (the fail-safe read-first loader hook)."""

from __future__ import annotations

import io
import json
import os
import re
import time
from pathlib import Path

import pytest

from solaris.tools import read_first as R


@pytest.fixture(autouse=True)
def _no_session(monkeypatch, tmp_path_factory):
    # A test run inside a registered Claude Code session must still see orchestrator mode.
    monkeypatch.delenv(R._SESSION_ENV, raising=False)
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path_factory.mktemp("cache")))
    monkeypatch.delenv(R._BUDGET_ENV, raising=False)


def test_detect_ide_prefers_claude_when_both_present():
    # Claude Code can run inside Cursor, so both var families appear; Claude must win (it wants plain text).
    assert R.detect_ide(env={"CLAUDECODE": "1", "CURSOR_TRACE_ID": "x"}) == "claude"
    # any CLAUDE-prefixed var is enough, even without CLAUDECODE
    assert R.detect_ide(env={"CLAUDE_CODE_EXECPATH": "/x", "CURSOR_LAYOUT": "y"}) == "claude"
    assert R.detect_ide(env={"CURSOR_TRACE_ID": "x"}) == "cursor"
    assert R.detect_ide(env={}) == "unknown"


def test_render_full_includes_header_and_all_files(tmp_path):
    for rel in R.READ_FIRST:
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("body of " + rel + "\n", encoding="utf-8")
    out = R.render_full(repo_root=tmp_path)
    assert "SOLARIS READ-FIRST" in out
    for rel in R.READ_FIRST:
        assert ("----- " + rel + " -----") in out
        assert ("body of " + rel) in out


def test_render_full_tolerates_missing_files(tmp_path):
    # no files exist under tmp_path -> each is noted, never raises
    out = R.render_full(repo_root=tmp_path)
    assert out.count("open it directly") == len(R.READ_FIRST)


def test_emit_is_json_for_cursor_plain_for_others():
    buf = io.StringIO()
    R.emit("hello", "cursor", stream=buf)
    assert json.loads(buf.getvalue()) == {"additional_context": "hello"}

    buf = io.StringIO()
    R.emit("hello", "claude", stream=buf)
    assert buf.getvalue() == "hello"

    buf = io.StringIO()
    R.emit("hello", "unknown", stream=buf)
    assert buf.getvalue() == "hello"


def test_main_remind_vs_full(capsys, monkeypatch):
    monkeypatch.setattr(R, "heal_sync_conflicts", lambda: "")  # never sweep the real tree in tests
    assert R.main(["--remind"]) == 0
    out = capsys.readouterr().out
    assert "read-first" in out.lower()
    assert "SOLARIS READ-FIRST" not in out  # remind is the one-liner, not the full dump

    assert R.main([]) == 0
    assert "SOLARIS READ-FIRST" in capsys.readouterr().out


def test_render_full_respects_inline_budget():
    # Default (Claude-shaped) rendering fits the budget; the part-1 rules arrive whole.
    out = R.render_full()
    assert len(out) <= R._budget()
    for rel in (
        "solaris/rules/commits.rule.md",
        "solaris/rules/safety.rule.md",
    ):
        body = (R.REPO_ROOT / rel).read_text(encoding="utf-8")
        assert body in out, rel + " must be inlined whole"
    # Overflow degrades loudly, never silently.
    assert "TRUNCATED" in out or "POINTER" in out or len(out) < R._budget() // 2


def test_render_full_unbudgeted_is_complete():
    # Cursor path: a huge budget yields every file whole, no truncation markers in delimiters.
    out = R.render_full(budget=1_000_000)
    for rel in R.READ_FIRST:
        assert "\n----- " + rel + " -----\n" in out


def test_check_reports_budget(capsys):
    assert R.main(["--check"]) == 0
    out = capsys.readouterr().out
    assert "rendered payload" in out and ("OK (inline)" in out or "OVER BUDGET" in out)

def test_render_part2_inlines_subagents_rule_whole():
    # Part 2 (second SessionStart hook call) carries the subagents rule alone, whole and in budget.
    out = R.render_full(part=2)
    assert len(out) <= R._budget()
    assert "READ-FIRST, PART 2" in out
    assert R.READ_FIRST_2 == ("solaris/rules/subagents.rule.md",)
    for rel in R.READ_FIRST_2:
        body = (R.REPO_ROOT / rel).read_text(encoding="utf-8")
        assert body in out, rel + " must be inlined whole"
    # part 2 never re-lists part-1 files
    for rel in R.READ_FIRST:
        assert ("----- " + rel + " -----") not in out


def test_main_part2(capsys):
    assert R.main(["--part", "2"]) == 0
    out = capsys.readouterr().out
    assert "READ-FIRST, PART 2" in out and "PART 2" in out


def test_render_part3_inlines_economy_rule_whole():
    # Part 3 (third SessionStart hook call) carries the token-economy rule, whole and in budget.
    out = R.render_full(part=3)
    assert len(out) <= R._budget()
    assert "READ-FIRST, PART 3" in out
    for rel in R.READ_FIRST_3:
        body = (R.REPO_ROOT / rel).read_text(encoding="utf-8")
        assert body in out, rel + " must be inlined whole"
    # part 3 never re-lists earlier parts' files
    for rel in R.READ_FIRST + R.READ_FIRST_2:
        assert ("----- " + rel + " -----") not in out


def test_main_part3(capsys):
    assert R.main(["--part", "3"]) == 0
    assert "READ-FIRST, PART 3" in capsys.readouterr().out


def test_render_part4_inlines_interaction_and_yagni_rules_whole():
    # Part 4 (fourth SessionStart hook call) carries the interaction and YAGNI rules, whole and in budget.
    out = R.render_full(part=4)
    assert len(out) <= R._budget()
    assert "READ-FIRST, PART 4" in out
    assert R.READ_FIRST_4 == ("solaris/rules/interaction.rule.md", "solaris/rules/yagni.rule.md")
    for rel in R.READ_FIRST_4:
        body = (R.REPO_ROOT / rel).read_text(encoding="utf-8")
        assert body in out, rel + " must be inlined whole"
    # part 4 never re-lists earlier parts' files
    for rel in R.READ_FIRST + R.READ_FIRST_2 + R.READ_FIRST_3:
        assert ("----- " + rel + " -----") not in out


def test_main_part4(capsys, monkeypatch):
    monkeypatch.setattr(R, "heal_sync_conflicts", lambda: "")  # never sweep the real tree in tests
    assert R.main(["--part", "4"]) == 0
    assert "READ-FIRST, PART 4" in capsys.readouterr().out


def test_check_covers_all_parts(capsys):
    assert R.main(["--check"]) == 0
    out = capsys.readouterr().out
    for n in (1, 2, 3, 4):
        assert ("part %d rendered payload" % n) in out
    assert "OVER BUDGET" not in out


def test_every_framework_rule_arrives_whole_in_exactly_one_part():
    # A rebalance must never drop a rule or leave one truncated: each solaris/rules/*.rule.md sits in one
    # part and is inlined whole there, within the budget.
    parts = {1: R.READ_FIRST, 2: R.READ_FIRST_2, 3: R.READ_FIRST_3, 4: R.READ_FIRST_4}
    placed = [rel for files in parts.values() for rel in files if rel.endswith(".rule.md")]
    rules = sorted("solaris/rules/" + p.name for p in (R.REPO_ROOT / "solaris" / "rules").glob("*.rule.md"))
    assert sorted(placed) == rules
    for part, files in parts.items():
        out = R.render_full(part=part)
        assert len(out) <= R._budget()
        for rel in files:
            if rel.endswith(".rule.md"):
                body = (R.REPO_ROOT / rel).read_text(encoding="utf-8")
                assert "\n----- " + rel + " -----\n" + body in out, rel + " must be inlined whole"


# Dummy Syncthing device IDs only: never a real device's ID in this public repo.
_CP = "interactions.sync-conflict-20260930-151208-AAAAAAA.jsonl"


def _memory(root, canonical, copy, rel=".memory", manifest=False):
    """A memory dir under ``root`` with a canonical log (None: missing) and one conflict copy."""
    mem = root / rel
    mem.mkdir(parents=True)
    if manifest:
        (mem.parent / "manifest.json").write_text("{}\n", encoding="utf-8")  # marks a project pack
    if canonical is not None:
        (mem / "interactions.jsonl").write_bytes(canonical)
    (mem / _CP).write_bytes(copy)
    return mem


def test_canonical_conflict_target():
    p = Path("/x/.memory/interactions.sync-conflict-20260930-151208-AAAAAAA.jsonl")
    assert R.canonical_conflict_target(p) == Path("/x/.memory/interactions.jsonl")
    p = Path("/x/.memory/instructions.sync-conflict-20260930-151157-BBBBBBB.md")
    assert R.canonical_conflict_target(p) == Path("/x/.memory/instructions.md")
    assert R.canonical_conflict_target(Path("/x/.memory/interactions.jsonl")) is None


def test_heal_sync_conflicts_unions_jsonl_and_leaves_other(tmp_path):
    mem = _memory(tmp_path, b'{"ts": "a"}\n{"ts": "b"}\n', b'{"ts": "b"}\n{"ts": "c"}\n')
    (mem / "interactions.jsonl").chmod(0o644)
    (mem / "instructions.md").write_text("main\n", encoding="utf-8")
    leftover = mem / "instructions.sync-conflict-20260930-151157-BBBBBBB.md"
    leftover.write_text("other\n", encoding="utf-8")
    # A tree that must not be walked:
    bulky = tmp_path / "projects" / "my" / "kaggle" / "__out"
    bulky.mkdir(parents=True)
    (bulky / _CP).write_text('{"ts": "nope"}\n', encoding="utf-8")

    note = R.heal_sync_conflicts(tmp_path)
    assert (mem / "interactions.jsonl").read_bytes() == b'{"ts": "a"}\n{"ts": "b"}\n{"ts": "c"}\n'
    assert (mem / "interactions.jsonl").stat().st_mode & 0o777 == 0o644  # appended in place
    assert not (mem / _CP).exists()
    assert leftover.exists()
    assert note == ("1 Syncthing conflict copy left in memory folders, review before deleting: "
                    ".memory/instructions.sync-conflict-20260930-151157-BBBBBBB.md")
    assert (bulky / _CP).exists()


def test_heal_sync_conflicts_project_memory(tmp_path):
    mem = _memory(tmp_path, b'{"ts": "p1"}\n', b'{"ts": "p2"}\n', "projects/my/demo/ai/.memory",
                  manifest=True)
    (mem / "context.sync-conflict-20260928-224430-BBBBBBB.md").write_text("x\n", encoding="utf-8")
    note = R.heal_sync_conflicts(tmp_path)
    assert (mem / "interactions.jsonl").read_bytes() == b'{"ts": "p1"}\n{"ts": "p2"}\n'
    assert not (mem / _CP).exists()
    # A merged copy is not reported; the other leftover counts once, with its path.
    assert note.startswith("1 Syncthing conflict copy left") and "ai/.memory/context.sync" in note


def test_heal_covers_renamed_and_embedded_packs_but_not_data(tmp_path):
    demo = tmp_path / "projects" / "my" / "demo"
    swept = ["aipack/.memory", "repo/ai/.memory"]
    skipped = ["__data/.memory", "__out/ai/.memory"]
    for rel in swept + skipped:
        _memory(demo, b'{"ts": "a"}\n', b'{"ts": "loser"}\n', rel, manifest=True)
    assert R.heal_sync_conflicts(tmp_path) == ""
    for rel in swept:
        assert (demo / rel / "interactions.jsonl").read_bytes() == b'{"ts": "a"}\n{"ts": "loser"}\n'
        assert not (demo / rel / _CP).exists()
    for rel in skipped:
        assert (demo / rel / "interactions.jsonl").read_bytes() == b'{"ts": "a"}\n'
        assert (demo / rel / _CP).exists()


def test_heal_sweeps_a_project_memory_only_beside_a_manifest(tmp_path):
    # A .memory in a project's source/ repo is not a pack's; any folder holding manifest.json is.
    demo = tmp_path / "projects" / "my" / "demo"
    pack = _memory(demo, b'{"ts": "a"}\n', b'{"ts": "loser"}\n', "anyname/.memory", manifest=True)
    repo = _memory(demo, b'{"ts": "a"}\n', b'{"ts": "loser"}\n', "source/.memory")
    assert R.heal_sync_conflicts(tmp_path) == ""  # source/.memory is not walked, so not reported
    assert (pack / "interactions.jsonl").read_bytes() == b'{"ts": "a"}\n{"ts": "loser"}\n'
    assert not (pack / _CP).exists()
    assert (repo / "interactions.jsonl").read_bytes() == b'{"ts": "a"}\n' and (repo / _CP).exists()


def test_heal_covers_ungrouped_and_embedded_packs_of_any_name(tmp_path):
    # projects/<slug>/<pack> and embedded projects/<group>/<slug>/<repo>/<pack>, here aipack/:
    # swept beside a manifest.json, left alone without one.
    projects = tmp_path / "projects"
    swept = ["demo/aipack/.memory", "my/demo/repo/aipack/.memory"]
    skipped = ["bare/aipack/.memory", "my/bare/repo/aipack/.memory"]
    for rel in swept + skipped:
        _memory(projects, b'{"ts": "a"}\n', b'{"ts": "loser"}\n', rel, manifest=rel in swept)
    assert sorted(R._memory_roots(tmp_path)) == sorted(projects / rel for rel in swept)
    assert R.heal_sync_conflicts(tmp_path) == ""  # a folder without a manifest is not walked
    for rel in swept:
        assert (projects / rel / "interactions.jsonl").read_bytes() == b'{"ts": "a"}\n{"ts": "loser"}\n'
        assert not (projects / rel / _CP).exists()
    for rel in skipped:
        assert (projects / rel / "interactions.jsonl").read_bytes() == b'{"ts": "a"}\n'
        assert (projects / rel / _CP).exists()


def test_memory_roots_on_the_real_tree_keep_every_folder_found_before():
    # List only, never sweep the real tree: every memory folder the earlier patterns (ai/ packs
    # ungrouped and embedded, any pack grouped) found is still listed, and none twice.
    old = ("projects/*/ai/.memory", "projects/*/*/*/.memory", "projects/*/*/*/ai/.memory")
    before = [R.REPO_ROOT / ".memory"] + [
        p for pattern in old for p in R.REPO_ROOT.glob(pattern)
        if (p.parent / "manifest.json").is_file()
        and not {"__data", "__out"} & set(p.relative_to(R.REPO_ROOT).parts)]
    roots = R._memory_roots(R.REPO_ROOT)
    assert {p for p in before if p.is_dir()} <= set(roots)
    assert len(roots) == len(set(roots))


def test_heal_merges_bytes_split_on_newline_only(tmp_path):
    # A non-UTF-8 byte and raw U+2028/U+2029/U+0085 inside JSON strings must survive byte for byte;
    # str.splitlines would cut those entries apart and dedup would then join two of them.
    base = ('{"p": "x\u2028", "o": "same"}\n{"p": "y\u2029\u0085", "o": "same"}\n'.encode("utf-8")
            + b'{"p": "caf\xe9"}\n')
    mem = _memory(tmp_path, base, b'{"p": "caf\xe9"}\n{"ts": "loser"}\n')
    R.heal_sync_conflicts(tmp_path)
    assert (mem / "interactions.jsonl").read_bytes() == base + b'{"ts": "loser"}\n'
    assert not (mem / _CP).exists()


def test_heal_keeps_copies_when_a_read_fails(tmp_path, monkeypatch):
    # Missing canonical (Syncthing between its renames): nothing is created and the copy stays.
    mem = _memory(tmp_path, None, b'{"ts": "loser"}\n')
    assert R.heal_sync_conflicts(tmp_path).startswith("1 Syncthing conflict copy left")
    assert not (mem / "interactions.jsonl").exists() and (mem / _CP).exists()
    # Unreadable copy: the canonical is untouched and the copy stays.
    (mem / "interactions.jsonl").write_bytes(b'{"ts": "a"}\n')
    real = Path.read_bytes

    def deny(self):
        if self.name == _CP:
            raise PermissionError(self)
        return real(self)

    monkeypatch.setattr(Path, "read_bytes", deny)
    R.heal_sync_conflicts(tmp_path)
    assert real(mem / "interactions.jsonl") == b'{"ts": "a"}\n' and (mem / _CP).exists()


def test_heal_keeps_an_append_made_during_the_merge(tmp_path, monkeypatch):
    from solaris.tools import log_interaction as L

    mem = _memory(tmp_path, b'{"ts": "a"}\n', b'{"ts": "loser"}\n')
    canon = mem / "interactions.jsonl"
    real = Path.read_bytes

    def racing(self):
        out = real(self)
        if self == canon and b"during" not in out:
            L.append(canon, {"ts": "during"})  # another session logs right after the sweep's read
        return out

    monkeypatch.setattr(Path, "read_bytes", racing)
    R.heal_sync_conflicts(tmp_path)
    assert real(canon) == b'{"ts": "a"}\n{"ts": "during"}\n{"ts": "loser"}\n'
    assert not (mem / _CP).exists()


def test_heal_skips_when_busy_mid_line_or_without_fcntl(tmp_path, monkeypatch):
    mem = _memory(tmp_path, b'{"ts": "a"}\n', b'{"ts": "loser"}\n')
    canon = mem / "interactions.jsonl"
    with open(canon, "ab") as held:
        R.fcntl.flock(held.fileno(), R.fcntl.LOCK_EX)  # another session's sweep holds the lock
        R.heal_sync_conflicts(tmp_path)
    assert canon.read_bytes() == b'{"ts": "a"}\n' and (mem / _CP).exists()
    # No trailing newline: a writer may be mid-line, so the group waits for the next session start.
    canon.write_bytes(b'{"ts": "a"}\n{"ts": "par')
    R.heal_sync_conflicts(tmp_path)
    assert canon.read_bytes() == b'{"ts": "a"}\n{"ts": "par' and (mem / _CP).exists()
    with open(canon, "ab") as fh:
        fh.write(b'tial"}\n')
    with monkeypatch.context() as m:
        m.setattr(R, "fcntl", None)  # no flock (Windows): the sweep is skipped
        assert R.heal_sync_conflicts(tmp_path) == ""
    assert (mem / _CP).exists()
    R.heal_sync_conflicts(tmp_path)
    assert canon.read_bytes() == b'{"ts": "a"}\n{"ts": "partial"}\n{"ts": "loser"}\n'
    assert not (mem / _CP).exists()


def test_heal_keeps_copies_when_a_copy_is_cut_off_mid_line(tmp_path):
    # Syncthing caught a copy mid-write: nothing of its group is merged, so no broken line lands.
    mem = _memory(tmp_path, b'{"ts": "a"}\n', b'{"ts": "b"}\n{"ts": "par')
    whole = mem / "interactions.sync-conflict-20260930-151209-BBBBBBB.jsonl"
    whole.write_bytes(b'{"ts": "c"}\n')
    assert R.heal_sync_conflicts(tmp_path).startswith("2 Syncthing conflict copies left")
    assert (mem / "interactions.jsonl").read_bytes() == b'{"ts": "a"}\n'
    assert (mem / _CP).read_bytes() == b'{"ts": "b"}\n{"ts": "par' and whole.exists()


def test_heal_keeps_copies_when_the_verify_fails(tmp_path, monkeypatch):
    # Syncthing swaps in a new canonical after the open: the append lands in the old inode, the
    # re-read through the path lacks the copy's line, so the copy is kept.
    mem = _memory(tmp_path, b'{"ts": "a"}\n', b'{"ts": "loser"}\n')
    canon = mem / "interactions.jsonl"
    real = R.os.write

    def swap_then_write(fd, data):
        (mem / "incoming").write_bytes(b'{"ts": "remote"}\n')
        R.os.replace(mem / "incoming", canon)
        return real(fd, data)

    monkeypatch.setattr(R.os, "write", swap_then_write)
    R.heal_sync_conflicts(tmp_path)
    assert canon.read_bytes() == b'{"ts": "remote"}\n' and (mem / _CP).exists()


def test_sweep_note_rides_on_part4_within_budget(tmp_path, monkeypatch, capsys):
    # Many unmerged copies with long names still give a count plus at most two paths.
    stamp = ".sync-conflict-20261001-101500-BBBBBBB"
    for i in range(12):
        mem = tmp_path / "projects" / "my" / ("a-project-with-a-long-slug-%02d" % i) / "ai" / ".memory"
        mem.mkdir(parents=True)
        (mem.parent / "manifest.json").write_text("{}\n", encoding="utf-8")
        (mem / ("context" + stamp + ".md")).write_text("x\n", encoding="utf-8")
    note = R.heal_sync_conflicts(tmp_path)
    assert note.startswith("12 Syncthing conflict copies left") and note.count(stamp) == 2
    calls = []
    monkeypatch.setattr(R, "heal_sync_conflicts", lambda: calls.append(1) or note)
    monkeypatch.setattr(R, "detect_ide", lambda env=None: "claude")
    for argv in ([], ["--part", "2"], ["--part", "3"]):
        assert R.main(argv) == 0
    assert calls == [] and note not in capsys.readouterr().out
    assert R.main(["--part", "4"]) == 0
    out = capsys.readouterr().out
    assert calls == [1] and out.startswith("[Solaris] " + note + "\n") and "READ-FIRST, PART 4" in out
    assert len(out) <= R._budget()
    # The per-prompt reminder sweeps too, so a mid-session conflict surfaces on the next prompt.
    assert R.main(["--remind"]) == 0
    out = capsys.readouterr().out
    assert calls == [1, 1] and out.startswith("[Solaris] " + note + "\n") and R._REMINDER in out


def test_heal_sweeps_per_machine_log_folders(tmp_path):
    # A copy of a per-machine log (two machines sharing a name) is merged like any other log.
    for rel in (".memory", "projects/my/demo/aipack/.memory"):
        mem = tmp_path / rel
        logs = mem / "interactions"
        logs.mkdir(parents=True)
        if rel != ".memory":
            (mem.parent / "manifest.json").write_text("{}\n", encoding="utf-8")
        (logs / "box1.jsonl").write_bytes(b'{"ts": "a"}\n')
        (logs / "box1.sync-conflict-20261002-101500-AAAAAAA.jsonl").write_bytes(b'{"ts": "a"}\n{"ts": "b"}\n')
    assert R.heal_sync_conflicts(tmp_path) == ""
    for rel in (".memory", "projects/my/demo/aipack/.memory"):
        logs = tmp_path / rel / "interactions"
        assert (logs / "box1.jsonl").read_bytes() == b'{"ts": "a"}\n{"ts": "b"}\n'
        assert sorted(p.name for p in logs.iterdir()) == ["box1.jsonl"]


# Project mode: a session registered by session_clock --dir <project> gets the project's set.

_SID = "0a1b2c3d-4e5f-test-session"

_LINK = """<!-- Linked plugin: managed by install-plugin (link mode). Machine-local development pointer. -->

# Linked Plugin: linked

This project uses the **linked** plugin in **link mode**: nothing is copied into `aipack/plugins/linked/`; the live
plugin source is loaded directly. On every turn, treat the plugin as if it were materialized here:

- **Plugin root:** `%s` - relative to this ai-pack's root, the directory **above**
  `aipack/` (not to this file). Shared files are in `shared/` there; the live version is in its `manifest.json`.
- Load each `shared/*.rule.md` as always-on; treat each `shared/*.skill.md` as trigger-invoked.
"""


def _write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _huge(tag, paragraphs=100):
    return "\n\n".join("%s paragraph %d %s" % (tag, i, "x" * 200) for i in range(paragraphs)) + "\n"


def _solaris(root):
    """A temporary Solaris root (framework rules, orchestrator files, a plugin) holding one project."""
    for name in ("commits", "safety", "interaction", "subagents", "token-economy", "yagni"):
        _write(root / "solaris" / "rules" / (name + ".rule.md"), "# %s\nFW-%s body\n" % (name, name.upper()))
    _write(root / ".memory" / "instructions.md", "FRAMEWORK-MEMORY\n")
    _write(root / "solaris" / "solaris.agent.md", "ORCHESTRATOR-ROLE\n")
    _write(root / "plugins" / "linked" / "shared" / "linked.rule.md", "LINKED-RULE\n")
    _write(root / "plugins" / "linked" / "shared" / "linked.skill.md", "LINKED-SKILL\n")
    pack = root / "projects" / "my" / "demo" / "aipack"
    _write(pack / "manifest.json", json.dumps({
        "framework_version": "0.44.0", "project": {"name": "Demo Project", "slug": "demo"},
        "agents": {"primary": "master"},
        "plugins": [{"name": "copied", "version": "1.0.0"}, {"name": "linked", "mode": "link"}]}))
    _write(pack / "rules" / "small.rule.md", "SMALL-RULE\n")
    _write(pack / "rules" / "huge.rule.md", _huge("HUGE"))
    _write(pack / "plugins" / "copied" / "copied.rule.md", "COPIED-RULE\n")
    _write(pack / "plugins" / "copied" / "copied.skill.md", "COPIED-SKILL\n")
    _write(pack / "plugins" / "linked.link.md", _LINK % "../../../plugins/linked/")
    _write(pack / ".memory" / "context.md", "ctx\n")
    return pack


def _register(pack, session=_SID, age_days=0.0, updated=None, raw=None):
    path = R.registry_dir() / (session + ".json")
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {"schema": 1, "session": session, "solaris": str(pack.parents[3]), "dir": str(pack.parent),
            "pack": str(pack), "updated": updated or "2026-10-17T13:00:00-07:00"}
    path.write_text(raw if raw is not None else json.dumps(data), encoding="utf-8")
    if age_days:
        t = time.time() - age_days * 86400
        os.utime(path, (t, t))
    return path


@pytest.fixture
def project(tmp_path, monkeypatch):
    """A registered project session at a temporary Solaris root; returns the pack folder."""
    pack = _solaris(tmp_path)
    monkeypatch.setattr(R, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(R, "heal_sync_conflicts", lambda: "")
    monkeypatch.setattr(R, "migrate_legacy_memory", lambda: None)
    monkeypatch.setattr(R, "detect_ide", lambda env=None: "claude")
    monkeypatch.setenv(R._SESSION_ENV, _SID)
    _register(pack)
    return pack


def _all_parts(capsys):
    outs = []
    for part in range(1, R.PROJECT_PARTS + 1):
        assert R.main([] if part == 1 else ["--part", str(part)]) == 0
        outs.append(capsys.readouterr().out)
    return outs


def _delim(rel):
    return "\n----- " + rel + " -----\n"


_LABEL = re.compile(r"^----- (.+?)(?: \((?:TRUNCATED|POINTER) - [^)]*\))? -----$", re.M)


def _labels(out):
    """The file labels of one part's output, in order."""
    return _LABEL.findall(out)


def test_orchestrator_main_is_render_full_and_parts_5_to_8_print_nothing(capsys, monkeypatch):
    monkeypatch.setattr(R, "heal_sync_conflicts", lambda: "")
    monkeypatch.setattr(R, "migrate_legacy_memory", lambda: None)
    for ide in ("claude", "cursor"):
        monkeypatch.setattr(R, "detect_ide", lambda env=None, ide=ide: ide)
        for part in (5, 6, 7, 8):
            assert R.main(["--part", str(part)]) == 0
            assert capsys.readouterr().out == ""
    monkeypatch.setattr(R, "detect_ide", lambda env=None: "claude")
    for part, argv in ((1, []), (2, ["--part", "2"]), (3, ["--part", "3"]), (4, ["--part", "4"])):
        assert R.main(argv) == 0
        assert capsys.readouterr().out == R.render_full(part=part)
    assert R.main(["--remind"]) == 0
    assert capsys.readouterr().out == R._REMINDER


def test_project_mode_loads_the_project_set_not_the_orchestrators(project, capsys):
    outs = _all_parts(capsys)
    joined = "".join(outs)
    assert all(len(out) <= R._budget() for out in outs)
    assert outs[0].startswith("=== SOLARIS READ-FIRST, PROJECT demo, PART 1")
    assert "SOLARIS READ-FIRST (auto-loaded" not in joined
    # Part 1 is the floor alone, whole; never the orchestrator role or the framework memory.
    commits = (project.parents[3] / "solaris/rules/commits.rule.md").read_text(encoding="utf-8")
    safety = (project.parents[3] / "solaris/rules/safety.rule.md").read_text(encoding="utf-8")
    assert outs[0].endswith(_delim("solaris/rules/commits.rule.md") + commits
                            + _delim("solaris/rules/safety.rule.md") + safety)
    assert _labels(outs[0]) == ["solaris/rules/commits.rule.md", "solaris/rules/safety.rule.md"]
    for marker in ("FRAMEWORK-MEMORY", "ORCHESTRATOR-ROLE", "FW-SUBAGENTS", "FW-TOKEN-ECONOMY", "FW-YAGNI"):
        assert marker not in joined
    # Part 2 starts with the generated block (no card): the project, the clock command, the MUST-READ list.
    assert _labels(outs[1])[0] == R._BLOCK_LABEL and "card.md" not in joined
    for text in ("project Demo Project (`demo`)", "`uv run -m solaris.tools.session_clock --dir projects/my/demo`",
                 "- `projects/my/demo/aipack/master.agent.md`", "- `projects/my/demo/aipack/instructions.md`",
                 "- `projects/my/demo/aipack/.memory/context.md`"):
        assert outs[1].count(text) == 1, text
    assert "PAUSED" not in joined
    # Pack rules, a copied plugin's rule, a linked plugin's rule and the interaction rule, once each.
    for rel, body in (("projects/my/demo/aipack/rules/small.rule.md", "SMALL-RULE\n"),
                      ("projects/my/demo/aipack/plugins/copied/copied.rule.md", "COPIED-RULE\n"),
                      ("plugins/linked/shared/linked.rule.md", "LINKED-RULE\n"),
                      ("solaris/rules/interaction.rule.md", "# interaction\nFW-INTERACTION body\n")):
        assert joined.count(_delim(rel) + body) == 1, rel
    assert "COPIED-SKILL" not in joined and "LINKED-SKILL" not in joined
    # The oversized rule gets part 3 to itself and keeps nearly all of it.
    assert _labels(outs[2]) == ["projects/my/demo/aipack/rules/huge.rule.md"]
    assert "(TRUNCATED - read the rest yourself)" in outs[2] and len(outs[2]) > R._budget() - 150
    assert outs[3:] == ["", "", "", "", ""]


def test_project_card_opens_part_2_and_the_reminder_names_it(project, capsys):
    assert R.main(["--remind"]) == 0
    out = capsys.readouterr().out
    assert "Demo Project" in out and "no hard-rules card yet (`projects/my/demo/aipack/card.md`)" in out
    card = "# Demo - Hard Rules Card\n\n- CARD-RULE\n"
    _write(project / "card.md", card)
    outs = _all_parts(capsys)
    assert _labels(outs[1])[:2] == ["projects/my/demo/aipack/card.md", R._BLOCK_LABEL]
    assert _delim("projects/my/demo/aipack/card.md") + card in outs[1]
    assert R.main(["--remind"]) == 0
    out = capsys.readouterr().out
    assert out.startswith("[Solaris read-first] Project session: Demo Project (`demo`")
    assert "the hard-rules card `projects/my/demo/aipack/card.md`" in out and len(out) < 1000
    assert R.main(["--check", "--pack", str(project)]) == 0
    assert "card: whole (%d characters)" % len(card) in capsys.readouterr().out


def test_an_oversized_card_is_truncated_and_the_block_stays_whole(project, capsys):
    _write(project / "card.md", _huge("CARD", 60))
    block = R._session_block(R.project_context(project), project.parents[3])
    outs = _all_parts(capsys)
    assert len(outs[1]) <= R._budget()
    assert _labels(outs[1])[:2] == ["projects/my/demo/aipack/card.md", R._BLOCK_LABEL]
    assert "card.md (TRUNCATED - read the rest yourself)" in outs[1] and _delim(R._BLOCK_LABEL) + block in outs[1]
    assert R.main(["--check", "--pack", str(project)]) == 0
    out = capsys.readouterr().out
    assert "card: TRUNCATED to " in out and "over the room left in part 2" in out


def test_large_rules_get_parts_of_their_own_and_the_rest_fill_first_fit(project, capsys):
    # Like a pack's subagents (11.8 KB) and token-economy (9.8 KB) rules: each gets a full part (never the
    # smaller part 4) and keeps about the budget; the mid-size rules fill part 2's rest, then part 4.
    _write(project / "rules" / "subagents.rule.md", _huge("SUB", 54))
    _write(project / "rules" / "economy.rule.md", _huge("ECO", 45))
    _write(project / "rules" / "mid.rule.md", _huge("MID", 30))
    _write(project / "rules" / "mid2.rule.md", _huge("MID2", 30))
    outs = _all_parts(capsys)
    assert all(len(out) <= R._budget() for out in outs)
    assert _labels(outs[2]) == ["projects/my/demo/aipack/rules/huge.rule.md"]
    big = {_labels(out)[0]: out for out in (outs[4], outs[5])}
    assert set(big) == {"projects/my/demo/aipack/rules/subagents.rule.md",
                        "projects/my/demo/aipack/rules/economy.rule.md"}
    for out in big.values():
        assert "(TRUNCATED - read the rest yourself)" in out and len(out) > R._budget() - 150
    assert "projects/my/demo/aipack/rules/mid.rule.md" in _labels(outs[1]) + _labels(outs[3])
    assert "projects/my/demo/aipack/rules/mid2.rule.md" in _labels(outs[1]) + _labels(outs[3])
    assert "SMALL-RULE" in outs[1] and outs[6:] == ["", ""]


def test_more_than_8_parts_make_the_largest_rules_pointers(project, capsys):
    # Seven mid-size rules need part 2's rest and parts 3-8, plus a part for the huge rule: 9 parts.
    for i in range(7):
        _write(project / "rules" / ("big%d.rule.md" % i), _huge("BIG%d" % i, 30))
    _write(project / "card.md", "CARD-RULE\n")
    outs = _all_parts(capsys)
    joined = "".join(outs)
    assert all(out and len(out) <= R._budget() for out in outs)
    for marker in ("CARD-RULE", "SMALL-RULE", "COPIED-RULE", "LINKED-RULE", "FW-INTERACTION"):
        assert joined.count(marker) == 1
    for i in range(7):
        assert joined.count(_delim("projects/my/demo/aipack/rules/big%d.rule.md" % i)) == 1
    assert joined.count("\n----- projects/my/demo/aipack/rules/huge.rule.md (POINTER") == 1
    assert R.main(["--check", "--pack", str(project)]) == 0
    out = capsys.readouterr().out
    assert "parts used: 8 of 8" in out
    assert "WARNING: the files need 9 parts, more than 8" in out and out.rstrip().endswith("huge.rule.md")
    # A tiny budget still never overflows a part.
    ctx = R.project_context(project)
    for part in range(1, R.PROJECT_PARTS + 1):
        assert len(R.render_project(ctx, budget=2_000, part=part)) <= 2_000


def test_project_part4_keeps_room_for_the_sweep_note(project, capsys, monkeypatch):
    # Rules sized to fill part 4 to its room: a note of a usual size still arrives whole, a longer one is cut.
    ctx = R.project_context(project)
    room4 = R._budget() - len(R._project_header(ctx, 4)) - R._NOTE_ROOM
    for i in range(2):
        rel = "projects/my/demo/aipack/rules/fill%d.rule.md" % i
        _write(project.parents[3] / rel, "y" * (room4 - len(_delim(rel)) - 1) + "\n")
    out = R.render_project(ctx, part=4)
    assert _labels(out) == ["projects/my/demo/aipack/rules/fill0.rule.md"]
    assert len(out) == R._budget() - R._NOTE_ROOM
    for note, whole in (("3 Syncthing conflict copies left in memory folders: " + "p" * 250, True),
                        ("n" * 3_000, False)):
        monkeypatch.setattr(R, "heal_sync_conflicts", lambda note=note: note)
        assert R.main(["--part", "4"]) == 0
        out = capsys.readouterr().out
        assert out.startswith("[Solaris] " + (note + "\n" if whole else note[:40])) and len(out) <= R._budget()
        assert "\n=== SOLARIS READ-FIRST, PROJECT demo, PART 4" in out


def test_stale_foreign_or_broken_registry_means_orchestrator_mode(project, capsys, monkeypatch):
    orchestrator = R.render_full(part=1)

    def foreign():  # only another session is registered
        _register(project, session="another")
        monkeypatch.setenv(R._SESSION_ENV, "someone-else")

    cases = [
        lambda: _register(project, age_days=15),  # older than 14 days
        lambda: _register(project, raw="{not json"),
        lambda: _register(project, raw=json.dumps({"pack": str(project / "gone")})),
        lambda: _register(project, raw=json.dumps({"pack": "projects/my/demo/aipack"})),  # not absolute
        foreign,
    ]
    for make in cases:
        monkeypatch.setenv(R._SESSION_ENV, _SID)
        make()
        assert R.main([]) == 0
        assert capsys.readouterr().out == orchestrator
        assert R.main(["--part", "6"]) == 0
        assert capsys.readouterr().out == ""
    assert R.load_registry("../" + _SID) is None and R.load_registry("") is None
    _register(project, age_days=13)
    assert R.load_registry(_SID)["pack"] == str(project)


def test_registry_honors_xdg_cache_home_and_home():
    assert R.registry_dir({"XDG_CACHE_HOME": "/c"}) == Path("/c/solaris/sessions")
    assert R.registry_dir({"XDG_CACHE_HOME": "", "HOME": "/h"}) == Path("/h/.cache/solaris/sessions")
    assert R.registry_dir({"XDG_CACHE_HOME": "rel", "HOME": "/h"}) == Path("/h/.cache/solaris/sessions")


def test_paused_project_lists_its_newest_handover_note(project, capsys):
    mem = project / ".memory"
    _write(mem / "handover-2026-10-01-0900.md", "old note, resumed at 2026-10-02 10:00\n")
    note = _write(mem / "handover-2026-10-17-1200.md", "# Handover\nnext steps\n")
    _register(project, updated="2026-10-17T11:00:00-07:00")
    t = R._epoch("2026-10-17T12:00:00-07:00")
    os.utime(note, (t, t))
    listed = "- `projects/my/demo/aipack/.memory/handover-2026-10-17-1200.md` - PAUSED"
    assert listed in "".join(_all_parts(capsys))
    # Written before the session's registry update: not a pause of this session.
    _register(project, updated="2026-10-17T13:00:00-07:00")
    assert "PAUSED" not in "".join(_all_parts(capsys))
    # Resumed: never listed, also without a registry (--check --pack).
    _register(project, updated="2026-10-17T11:00:00-07:00")
    note.write_text("# Handover\nresumed at 2026-10-17 12:30\n", encoding="utf-8")
    os.utime(note, (t, t))
    assert "PAUSED" not in "".join(_all_parts(capsys))
    assert R.paused_handover(project) is None


def test_link_mode_falls_back_to_the_plugin_folder_then_to_the_pointer(project, capsys):
    link = project / "plugins" / "linked.link.md"
    link.write_text("# Linked Plugin: linked\n(no plugin root line)\n", encoding="utf-8")
    assert _delim("plugins/linked/shared/linked.rule.md") + "LINKED-RULE\n" in "".join(_all_parts(capsys))
    gone = project / "plugins" / "gone.link.md"
    gone.write_text(_LINK % "../../../plugins/gone/", encoding="utf-8")
    joined = "".join(_all_parts(capsys))
    assert joined.count(_delim("projects/my/demo/aipack/plugins/gone.link.md")) == 1


def test_check_shows_the_project_packing(project, capsys, monkeypatch):
    for argv in (["--check", "--pack", str(project)], ["--check", "--session", _SID], ["--check", "--session"]):
        assert R.main(argv) == 0
        out = capsys.readouterr().out
        assert out.startswith("read_first check (project mode)") and "card: none" in out, argv
        assert "primary master" in out and "truncated to" in out
        for part in range(1, 7):
            assert ("part %d rendered payload" % part) in out
        assert "OVER BUDGET" not in out
    assert R.main(["--check", "--session", "no-such-session"]) == 0
    out = capsys.readouterr().out
    assert "orchestrator mode" in out and "part 4 rendered payload" in out
    assert R.main(["--check", "--pack", str(project / "missing")]) == 0
    assert "no pack folder" in capsys.readouterr().out


def test_project_mode_is_fail_safe(project, capsys, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("broken")

    monkeypatch.setattr(R, "plan_project", boom)
    assert R.main([]) == 0 and capsys.readouterr().out == ""
