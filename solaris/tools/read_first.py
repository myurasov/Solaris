# Copyright 2026 Mikhail Yurasov <me@yurasov.me>
# SPDX-License-Identifier: Apache-2.0

"""Session hook: auto-load the framework "read-first" set into the agent's context. Stdlib only.

AGENTS.md lists files the orchestrator must read at the start of every session (the orchestrator role,
the commit + safety rules, and the operating memory). Those are only *pointed to*, so loading them used to
depend on the agent choosing to read them - the exact step that gets skipped. This tool makes the load
deterministic by emitting their contents from a hook, so the harness (not the model) puts them in context.

Two modes:

- **no args** - full load, part 1 (rules + memory + orchestrator role). Print the concatenated read-first
  files under an authoritative header. Wired to the session-start hook (Claude Code ``SessionStart``;
  Cursor ``sessionStart``) so it fires once per session and again after a compaction / clear / resume.
- **``--part 2``** - full load, part 2 (the subagents rule); **``--part 3``** - full load, part 3 (the
  token-economy rule); **``--part 4``** - full load, part 4 (the YAGNI rule, plus the Syncthing
  conflict sweep below). Wired as additional session-start hook entries: the harness inline threshold
  applies per hook call, so splitting the set across calls multiplies the inline room without risking
  a spill.
- **``--check``** - print per-file sizes, the inline budget, and whether the rendered payload fits
  (the size assertion; run after growing any read-first file, especially ``.memory/instructions.md``).
- **``--remind``** - print a one-line forcing reminder that the set was loaded. Wired to Claude Code's
  ``UserPromptSubmit`` so the Hybrid model gets a cheap per-turn nudge on top of the once-per-session load.
  (Cursor's ``beforeSubmitPrompt`` cannot inject context - its output is only ``{continue, user_message}`` -
  so the per-prompt remind is Claude-only; Cursor relies on the ``sessionStart`` load alone.)

Output format is IDE-aware: Cursor hooks read a JSON object (``additional_context``); Claude Code hooks read
plain stdout. The tool detects the IDE from the environment and emits whichever the caller expects.

On a part-4 session start it also merges Syncthing ``*.sync-conflict-*`` copies of any ``.jsonl`` log
in a memory folder (framework ``.memory/`` and each project pack's ``.memory/``, never ``__data/`` or
``__out/``) into the canonical file: it appends only the missing lines under a non-blocking lock,
re-reads to verify them, and only then deletes the copies. A merge can bring back a line that was
deliberately removed from the canonical log on another device, since the copy still holds it. A group
it cannot merge safely (a missing, unreadable, locked or mid-line file) keeps its copies for the next
session start. Every copy left in place is reported by a one-line note (a count plus at most two paths)
prepended to part 4, the smallest payload, so the note cannot push part 1 past the inline budget.

Like the other hooks it is **fail-safe**: it never raises, always exits 0, and tolerates missing files / a
missing venv - a broken read-first load must never block the user's turn. It does not read stdin (avoiding
the blocking footgun); it keys only off argv and the environment.
"""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

try:
    import fcntl
except ImportError:  # no flock (Windows): the conflict sweep is skipped
    fcntl = None

REPO_ROOT = Path(__file__).resolve().parents[2]

# The AGENTS.md "Read first" set in INLINE PRIORITY order (not reading order): the always-on
# rules are small and operative, so they must always arrive whole; the operating memory and the
# orchestrator role are larger and degrade gracefully to a truncated head + a read-the-rest pointer.
# The set is split into four parts because Claude Code's inline threshold is PER HOOK INVOCATION:
# every part is wired as its own SessionStart hook, so each gets its own budget.
READ_FIRST = (
    "solaris/rules/commits.rule.md",
    "solaris/rules/safety.rule.md",
    "solaris/rules/interaction.rule.md",
    ".memory/instructions.md",
    "solaris/solaris.agent.md",
)
READ_FIRST_2 = (
    "solaris/rules/subagents.rule.md",
)
READ_FIRST_3 = (
    "solaris/rules/token-economy.rule.md",
)
READ_FIRST_4 = (
    "solaris/rules/yagni.rule.md",
)

# Claude Code persists hook stdout beyond 10,000 characters to a file, keeping only a small inline
# preview - which silently defeats the whole point of this hook (observed 2026-08-08: a 35.2KB
# payload arrived as a preview; agents then failed rule-recall canaries in the agent-bench runs;
# the 10k threshold is documented in the Claude Code hooks reference and applies per hook call).
# Stay comfortably under that: pack whole files first, truncate the first overflowing file at a
# paragraph boundary, and reduce the rest to must-read pointers. Override via env for tuning.
_DEFAULT_BUDGET = 9_500
_BUDGET_ENV = "SOLARIS_READ_FIRST_BUDGET"

_HEADER = (
    "=== SOLARIS READ-FIRST (auto-loaded every session by the read_first hook) ===\n"
    "These are the framework's authoritative read-first files (the AGENTS.md \"Read first\" set), "
    "loaded for you so you do not have to open them by hand. Obey them before acting; they override "
    "default behavior. A file marked TRUNCATED or POINTER below did not fit the inline budget - "
    "read the remainder/file yourself before relying on it. If a file is shown empty/missing, open "
    "it yourself.\n"
)

_HEADER_2 = (
    "=== SOLARIS READ-FIRST, PART 2 (auto-loaded every session by the read_first hook) ===\n"
    "Continuation of the authoritative read-first set (split across hook calls to stay inline). "
    "Same authority as part 1: obey before acting. A file marked TRUNCATED or POINTER did not fit - "
    "read it yourself before relying on it.\n"
)

_HEADER_3 = (
    "=== SOLARIS READ-FIRST, PART 3 (auto-loaded every session by the read_first hook) ===\n"
    "Continuation of the authoritative read-first set (split across hook calls to stay inline). "
    "Same authority as part 1: obey before acting. A file marked TRUNCATED or POINTER did not fit - "
    "read it yourself before relying on it.\n"
)

_HEADER_4 = (
    "=== SOLARIS READ-FIRST, PART 4 (auto-loaded every session by the read_first hook) ===\n"
    "Continuation of the authoritative read-first set (split across hook calls to stay inline). "
    "Same authority as part 1: obey before acting. A file marked TRUNCATED or POINTER did not fit - "
    "read it yourself before relying on it.\n"
)

_REMINDER = (
    "[Solaris read-first] The authoritative set (solaris.agent.md + the commit, safety, interaction, "
    "subagents, token-economy & YAGNI rules + .memory/instructions.md) was loaded at session start - "
    "follow it. Quick reminders: bare `ssh`/`open` are blocked, use the /tmp wrappers (`hss`, "
    "`nepo`); confirm before destructive / remote-mutating / outward actions; answer a direct "
    "question in the reply's first line; delegate per the subagents rule (posture default `auto` - "
    "follows the economy level) and honor the token-economy floor; `subagents:`/`economy:`/`yagni:`/"
    "`asap` in a prompt are per-request overrides; log the turn to .memory/interactions.jsonl (UTC ts)."
)


# Syncthing names the loser `stem.sync-conflict-YYYYMMDD-HHMMSS-DEVICE.ext`.
_CONFLICT_NAME = re.compile(
    r"^(?P<stem>.+)\.sync-conflict-\d{8}-\d{6}-[A-Za-z0-9]+(?P<ext>\.[^./]+)$"
)


def _memory_roots(repo_root: Path) -> list[Path]:
    """Memory dirs the conflict sweeper may touch: never walk __data/__out or the whole tree."""
    roots = [repo_root / ".memory"]
    # projects/<slug>/ai, projects/<group>/<slug>/<pack> (ai/ or a renamed pack such as aipack/)
    # and embedded projects/<group>/<slug>/<repo>/ai. Keep .stglobalignore's conflict lines in step.
    # Glob lists only the wildcard levels, so __data/__out are never listed; matches in them are dropped.
    # A match counts only if its pack holds manifest.json, so a .memory in a source/ repo is not swept.
    for pattern in ("projects/*/ai/.memory", "projects/*/*/*/.memory", "projects/*/*/*/ai/.memory"):
        for path in repo_root.glob(pattern):
            bulk = {"__data", "__out"} & set(path.relative_to(repo_root).parts)
            if not bulk and (path.parent / "manifest.json").is_file():
                roots.append(path)
    return [p for p in roots if p.is_dir()]


def canonical_conflict_target(path: Path) -> "Path | None":
    """Return the pre-conflict path for a Syncthing conflict copy, or None if the name does not match."""
    m = _CONFLICT_NAME.match(path.name)
    if not m:
        return None
    return path.with_name(m.group("stem") + m.group("ext"))


def _jsonl_lines(blob: bytes) -> list[bytes]:
    # Split bytes on b"\n" only: str.splitlines also breaks at U+2028/U+2029/U+0085 inside JSON strings.
    return [line for line in blob.split(b"\n") if line]


def _merge_jsonl(canonical: Path, copies: list[Path]) -> bool:
    """Append the copies' lines missing from ``canonical``; delete the copies only after a verified merge.

    The canonical file is only appended to (one O_APPEND write under a non-blocking flock), so a log
    line another session appends meanwhile is never lost, and the file keeps its inode and mode.
    Returns False and keeps every copy for the next session start when the canonical file is missing
    or locked, when any file is mid-line or cannot be read, or when the re-read lacks a copy line.
    """
    try:
        fd = os.open(canonical, os.O_WRONLY | os.O_APPEND)  # no O_CREAT: a missing canonical skips
    except OSError:
        return False
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)  # raises if another session holds it
        base = canonical.read_bytes()
        blobs = [copy.read_bytes() for copy in copies]
        if base and not base.endswith(b"\n"):
            return False  # a writer may be mid-line
        if any(blob and not blob.endswith(b"\n") for blob in blobs):
            return False  # a copy caught mid-write would merge a broken line
        have = set(_jsonl_lines(base))
        add: list[bytes] = []
        for blob in blobs:
            for line in _jsonl_lines(blob):
                if line not in have:
                    have.add(line)
                    add.append(line)
        if add:
            data = b"\n".join(add) + b"\n"
            if os.write(fd, data) != len(data):
                return False
            os.fsync(fd)
        # Re-read through the path: if a new canonical was swapped in since the open, this check fails.
        after = set(_jsonl_lines(canonical.read_bytes()))
        if any(line not in after for blob in blobs for line in _jsonl_lines(blob)):
            return False
        for copy in copies:
            copy.unlink(missing_ok=True)
        return True
    except OSError:
        return False
    finally:
        os.close(fd)


def heal_sync_conflicts(repo_root: Path = REPO_ROOT) -> str:
    """Merge ``.jsonl`` Syncthing conflict copies in memory dirs; return a note on any copy left.

    Fail-safe: any error returns an empty string, and without fcntl the sweep is skipped. Walks only
    memory dirs so a session start cannot scan ``__data/`` / ``__out/``. The note is a count plus at
    most two paths, so it stays small whatever is left.
    """
    if fcntl is None:
        return ""
    try:
        found: list[Path] = []
        grouped: dict[Path, list[Path]] = {}
        for root in _memory_roots(repo_root):
            for path in root.glob("*sync-conflict-*"):
                if not path.is_file():
                    continue
                found.append(path)
                target = canonical_conflict_target(path)
                if target is not None and target.suffix == ".jsonl":
                    grouped.setdefault(target, []).append(path)
        for canonical, copies in grouped.items():
            _merge_jsonl(canonical, sorted(copies))
        left = sorted(p for p in found if p.exists())
        if not left:
            return ""
        shown = ", ".join(str(p.relative_to(repo_root)) for p in left[:2])
        return "%d Syncthing conflict cop%s left in memory folders, review before deleting: %s%s" % (
            len(left), "y" if len(left) == 1 else "ies", shown, ", ..." if len(left) > 2 else "")
    except Exception:
        return ""


def migrate_legacy_memory(repo_root: Path = REPO_ROOT) -> None:
    """Auto-rename a legacy framework ``memory/`` to ``.memory/`` on first access (Solaris 0.19).

    Mirrors the 0.18.0 ai-pack ``ai/memory`` -> ``ai/.memory`` move: a pure rename, private files
    untouched. Fail-safe - any problem is ignored and the old path simply keeps working until fixed by
    hand.
    """
    try:
        legacy, new = repo_root / "memory", repo_root / ".memory"
        if legacy.is_dir() and not new.exists():
            legacy.rename(new)
    except Exception:
        pass


def detect_ide(env: "dict | None" = None) -> str:
    """Best-effort IDE detection from the environment.

    Claude is checked first and on purpose: Claude Code can run *inside* Cursor (the
    ``anthropic.claude-code`` extension), so both ``CLAUDE*`` and ``CURSOR*`` vars appear at once. When
    Claude is the harness it wants plain stdout, not Cursor's JSON, so any Claude signal wins. We accept any
    ``CLAUDE``-prefixed var (not just ``CLAUDECODE``) so detection holds even if that one is absent.
    """
    env = os.environ if env is None else env
    if any(k.startswith("CLAUDE") for k in env):
        return "claude"
    if any(k.startswith("CURSOR") for k in env):
        return "cursor"
    return "unknown"


def _budget() -> int:
    try:
        return max(2_000, int(os.environ.get(_BUDGET_ENV, _DEFAULT_BUDGET)))
    except Exception:
        return _DEFAULT_BUDGET


def _truncate_at_boundary(text: str, limit: int) -> str:
    """Head of ``text`` cut at the last paragraph (fallback: line) boundary within ``limit``."""
    if len(text) <= limit:
        return text
    head = text[:limit]
    cut = head.rfind("\n\n")
    if cut < limit // 2:
        cut = head.rfind("\n")
    return head[: cut if cut > 0 else limit]


def render_full(repo_root: Path = REPO_ROOT, budget: "int | None" = None, part: int = 1) -> str:
    """Header + read-first files packed into the inline budget.

    Files are taken in inline-priority order: whole while they fit; the first file that
    overflows is truncated at a paragraph boundary with an explicit marker; every later file
    becomes a one-line MUST-READ pointer. This keeps the payload inline in Claude Code (large
    hook stdout is otherwise spilled to a barely-previewed file) while degrading loudly, never
    silently. ``part`` selects which slice of the set to render (the budget is per hook call,
    so each part is wired as its own SessionStart hook).
    """
    files = {2: READ_FIRST_2, 3: READ_FIRST_3, 4: READ_FIRST_4}.get(part, READ_FIRST)
    header = {2: _HEADER_2, 3: _HEADER_3, 4: _HEADER_4}.get(part, _HEADER)
    budget = _budget() if budget is None else budget
    remaining = budget - len(header)
    parts = [header]
    truncated = False

    def _pointer(rel: str) -> str:
        return "\n----- " + rel + " (POINTER - read this file yourself NOW) -----\n"

    for i, rel in enumerate(files):
        delim = "\n----- " + rel + " -----\n"
        try:
            body = (Path(repo_root) / rel).read_text(encoding="utf-8")
        except Exception:
            body = "(could not read this file - open it directly)\n"
        # Worst-case room the files after this one still need (each degrades to a pointer line);
        # counting it here guarantees pointers can never push the payload past the budget.
        reserve = sum(len(_pointer(r)) for r in files[i + 1:])
        if truncated or len(delim) + len(body) + reserve > remaining:
            if not truncated and remaining - reserve > len(delim) + 500:
                head = _truncate_at_boundary(body, remaining - reserve - len(delim) - 120)
                parts.append("\n----- " + rel + " (TRUNCATED - read the rest yourself) -----\n")
                parts.append(head)
                parts.append("\n[... truncated at inline budget - open " + rel + " for the rest]\n")
                remaining = reserve
            else:
                parts.append(_pointer(rel))
                remaining -= len(_pointer(rel))
            truncated = True
            continue
        parts.append(delim)
        parts.append(body)
        remaining -= len(delim) + len(body)
    return "".join(parts)


def check(repo_root: Path = REPO_ROOT) -> str:
    """Size assertion for humans/CI: per-file sizes, the budget, and every part's rendered payload size."""
    lines = ["read_first check: budget=%d per part (%s)" % (_budget(), _BUDGET_ENV)]
    for part, files in ((1, READ_FIRST), (2, READ_FIRST_2), (3, READ_FIRST_3), (4, READ_FIRST_4)):
        for rel in files:
            try:
                n = len((Path(repo_root) / rel).read_text(encoding="utf-8"))
            except Exception:
                n = -1
            lines.append("  %8d  %s" % (n, rel))
        rendered = render_full(repo_root, part=part)
        ok = len(rendered) <= _budget()
        lines.append("  part %d rendered payload: %d bytes -> %s"
                     % (part, len(rendered), "OK (inline)" if ok else "OVER BUDGET"))
    return "\n".join(lines)


def emit(text: str, ide: str, stream=None) -> None:
    """Print context in the shape the calling IDE's hook expects.

    Cursor reads a JSON object with ``additional_context``; Claude Code (and unknown callers) read plain
    stdout. We default to plain text so an unrecognized harness still gets the content verbatim. ``stream``
    is resolved at call time (not bound as a default) so test capture / redirection of stdout still works.
    """
    if stream is None:
        stream = sys.stdout
    if ide == "cursor":
        stream.write(json.dumps({"additional_context": text}))
    else:
        stream.write(text)


def main(argv: "list[str] | None" = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    try:
        if "--check" in argv:
            print(check())
            return 0
        remind = "--remind" in argv
        part = 1
        if "--part" in argv:
            part = 4 if "4" in argv else (3 if "3" in argv else (2 if "2" in argv else 1))
        if not remind and part == 1:
            migrate_legacy_memory()  # session start: pick up a pre-0.19 checkout's memory/ folder
        ide = detect_ide()
        # Cursor carries hook context as JSON without spilling large payloads to a file, so it
        # keeps the full set; the inline budget exists for Claude Code's stdout-persist behavior.
        full_budget = 1_000_000 if ide == "cursor" else None
        text = _REMINDER if remind else render_full(budget=full_budget, part=part)
        if not remind and part == 4:
            # The sweep and its note ride on part 4, the smallest payload: part 1 sits near the
            # budget, and past 10,000 characters the harness stops inlining it.
            note = heal_sync_conflicts()
            if note:
                text = "[Solaris] " + note + "\n" + text
        emit(text, ide)
    except Exception:
        pass  # fail-safe: a context-loading hook must never break the user's turn
    return 0


if __name__ == "__main__":
    sys.exit(main())
