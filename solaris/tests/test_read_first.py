# Copyright 2026 Mikhail Yurasov <me@yurasov.me>
# SPDX-License-Identifier: Apache-2.0

"""Tests for solaris.tools.read_first (the fail-safe read-first loader hook)."""

from __future__ import annotations

import io
import json
from pathlib import Path

from solaris.tools import read_first as R


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


def test_main_remind_vs_full(capsys):
    assert R.main(["--remind"]) == 0
    out = capsys.readouterr().out
    assert "read-first" in out.lower()
    assert "SOLARIS READ-FIRST" not in out  # remind is the one-liner, not the full dump

    assert R.main([]) == 0
    assert "SOLARIS READ-FIRST" in capsys.readouterr().out


def test_render_full_respects_inline_budget():
    # Default (Claude-shaped) rendering fits the budget; all always-on rules arrive whole.
    out = R.render_full()
    assert len(out) <= R._budget()
    for rel in (
        "solaris/rules/commits.rule.md",
        "solaris/rules/safety.rule.md",
        "solaris/rules/interaction.rule.md",
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


def test_render_part4_inlines_yagni_rule_whole():
    # Part 4 (fourth SessionStart hook call) carries the YAGNI rule, whole and in budget.
    out = R.render_full(part=4)
    assert len(out) <= R._budget()
    assert "READ-FIRST, PART 4" in out
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
    for argv in ([], ["--part", "2"], ["--part", "3"], ["--remind"]):
        assert R.main(argv) == 0
    assert calls == [] and note not in capsys.readouterr().out
    assert R.main(["--part", "4"]) == 0
    out = capsys.readouterr().out
    assert calls == [1] and out.startswith("[Solaris] " + note + "\n") and "READ-FIRST, PART 4" in out
    assert len(out) <= R._budget()
