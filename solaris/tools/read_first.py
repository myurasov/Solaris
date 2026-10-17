# Copyright 2026 Mikhail Yurasov <me@yurasov.me>
# SPDX-License-Identifier: Apache-2.0

"""Session hook: auto-load the framework "read-first" set into the agent's context. Stdlib only.

AGENTS.md lists files the orchestrator must read at the start of every session (the orchestrator role,
the commit + safety rules, and the operating memory). Those are only *pointed to*, so loading them used to
depend on the agent choosing to read them - the exact step that gets skipped. This tool makes the load
deterministic by emitting their contents from a hook, so the harness (not the model) puts them in context.

Two modes:

- **no args** - full load, part 1 (the commit + safety rules, memory, orchestrator role). Print the
  concatenated read-first files under an authoritative header. Wired to the session-start hook (Claude
  Code ``SessionStart``; Cursor ``sessionStart``) so it fires once per session and again after a
  compaction / clear / resume.
- **``--part 2``** - full load, part 2 (the subagents rule); **``--part 3``** - full load, part 3 (the
  token-economy rule); **``--part 4``** - full load, part 4 (the interaction + YAGNI rules, plus the
  Syncthing conflict sweep below). Wired as additional session-start hook entries: the harness inline
  threshold applies per hook call, so splitting the set across calls multiplies the inline room without
  risking a spill.
- **``--check``** - print per-file sizes, the inline budget, and whether the rendered payload fits
  (the size assertion; run after growing any read-first file, especially ``.memory/instructions.md``).
- **``--remind``** - print a one-line forcing reminder that the set was loaded. Wired to Claude Code's
  ``UserPromptSubmit`` so the Hybrid model gets a cheap per-turn nudge on top of the once-per-session load.
  (Cursor's ``beforeSubmitPrompt`` cannot inject context - its output is only ``{continue, user_message}`` -
  so the per-prompt remind is Claude-only; Cursor relies on the ``sessionStart`` load alone.) It also runs
  the Syncthing conflict sweep below and puts its note in front of the reminder, so a conflict is repaired
  and reported on the next prompt; under Cursor the ``log_interaction`` prompt hook runs the sweep instead.

Output format is IDE-aware: Cursor hooks read a JSON object (``additional_context``); Claude Code hooks read
plain stdout. The tool detects the IDE from the environment and emits whichever the caller expects.

**Project mode.** A project master runs Claude Code at the Solaris root, so the orchestrator set above would
come back after every compaction, and none of the project's rules. When ``CLAUDE_CODE_SESSION_ID`` names a
session registry file (``${XDG_CACHE_HOME:-$HOME/.cache}/solaris/sessions/<id>.json``, written by
``session_clock --dir <project>``, at most 14 days old) whose ``pack`` folder exists, the parts carry the
project's set instead, in up to eight parts (``--part 5`` to ``--part 8`` print nothing in orchestrator
mode), each within the budget: part 1 the framework commit and safety rules; part 2 ``<pack>/card.md`` (the
project's hard-rules card, truncated if over the room left) and a generated block (the project, the clock
command, a MUST-READ list), always whole; then the pack's always-on rules (``<pack>/rules/*.rule.md``,
copied plugins' ``<pack>/plugins/<plugin>/*.rule.md``, a linked plugin's ``shared/*.rule.md``) and the
framework interaction rule, first-fit by decreasing size into part 2's rest and parts 3-8. A file larger
than an empty part gets a part of its own and is truncated to it; when eight parts are not enough, the
largest rules become pointers. Never ``solaris.agent.md`` or the framework ``.memory/instructions.md``.
``--remind`` then names the project and its card; ``--check --session <id>`` or ``--check --pack <path>``
shows the packing.

On a part-4 session start it also merges Syncthing ``*.sync-conflict-*`` copies of any ``.jsonl`` log
in a memory folder (framework ``.memory/`` and each project pack's ``.memory/``, each with its per-machine
``interactions/`` log folder, never ``__data/`` or ``__out/``) into the canonical file: it appends only the
missing lines under a non-blocking lock,
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
import shlex
import sys
import time
from datetime import datetime
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
    ".memory/instructions.md",
    "solaris/solaris.agent.md",
)
READ_FIRST_2 = (
    "solaris/rules/subagents.rule.md",
)
READ_FIRST_3 = (
    "solaris/rules/token-economy.rule.md",
)
# Part 1 is full with the commit and safety rules, so the interaction rule rides here.
READ_FIRST_4 = (
    "solaris/rules/interaction.rule.md",
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

# Parts 2-4 share one short header: every character here is room taken from the rule below it.
_HEADER_N = (
    "=== SOLARIS READ-FIRST, PART %d (auto-loaded by the read_first hook) ===\n"
    "Same authority as part 1: obey before acting. A file marked TRUNCATED or POINTER did not fit - "
    "read it yourself.\n"
)

_REMINDER = (
    "[Solaris read-first] The authoritative set (solaris.agent.md + the commit, safety, interaction, "
    "subagents, token-economy & YAGNI rules + .memory/instructions.md) was loaded at session start - "
    "follow it. Quick reminders: bare `ssh`/`open` are blocked, use the /tmp wrappers (`hss`, "
    "`nepo`); confirm before destructive / remote-mutating / outward actions; answer a direct "
    "question in the reply's first line; delegate per the subagents rule (posture default `auto` - "
    "follows the economy level) and honor the token-economy floor; `subagents:`/`economy:`/`yagni:`/"
    "`asap` in a prompt are per-request overrides; log the turn with `uv run -m solaris.tools.interactions "
    "add` (`--dir <project>` for project work)."
)

# Project mode: a session registered by session_clock --dir <project> gets the project's set instead.
_SESSION_ENV = "CLAUDE_CODE_SESSION_ID"
_SESSION_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_REGISTRY_MAX_AGE = 14 * 24 * 3600
PROJECT_PARTS = 8
PROJECT_FLOOR = ("solaris/rules/commits.rule.md", "solaris/rules/safety.rule.md")
PROJECT_RULES = ("solaris/rules/interaction.rule.md",)
CARD = "card.md"
_BLOCK_LABEL = "project session (generated by read_first)"
# Kept free in part 4, where the conflict-sweep note rides.
_NOTE_ROOM = 400
_MIN_TRUNCATED = 500
_ROLE = re.compile(r"^[a-z][a-z0-9-]*$")
_LINK_ROOT = re.compile(r"\*\*Plugin root:\*\*\s*`([^`]+)`")

_HEADER_PROJECT = (
    "=== SOLARIS READ-FIRST, PROJECT %s, PART %d (auto-loaded every session by the read_first hook) ===\n"
    "This session works on a project, so this is the project's read-first set, not the orchestrator's: "
    "obey it before acting; it overrides default behavior. A file marked TRUNCATED or POINTER did not fit "
    "the inline budget - read it yourself.\n"
)
_HEADER_PROJECT_N = (
    "=== SOLARIS READ-FIRST, PROJECT %s, PART %d (auto-loaded by the read_first hook) ===\n"
    "Same authority as part 1: obey before acting. A file marked TRUNCATED or POINTER did not fit - "
    "read it yourself.\n"
)
_CLAMP = "\n[... cut at the inline budget - open the files named above yourself]\n"

_REMINDER_PROJECT = (
    "[Solaris read-first] Project session: %(name)s (`%(slug)s`, pack `%(pack)s`). Its read-first set - the "
    "commit and safety rules, %(card)s, the pack and plugin rules and the interaction rule - was loaded at "
    "session start: follow it, and after a compaction re-read `%(pack)s/%(primary)s.agent.md`, "
    "`%(pack)s/instructions.md` and `%(pack)s/.memory/context.md`. Quick reminders: bare `ssh`/`open` are "
    "blocked, use the /tmp wrappers (`hss`, `nepo`); confirm before destructive / remote-mutating / outward "
    "actions; answer a direct question in the reply's first line; log the turn with `uv run -m "
    "solaris.tools.interactions add --dir %(dir)s`."
)


# Syncthing names the loser `stem.sync-conflict-YYYYMMDD-HHMMSS-DEVICE.ext`.
_CONFLICT_NAME = re.compile(
    r"^(?P<stem>.+)\.sync-conflict-\d{8}-\d{6}-[A-Za-z0-9]+(?P<ext>\.[^./]+)$"
)


def _memory_roots(repo_root: Path) -> list[Path]:
    """Memory dirs the conflict sweeper may touch: never walk __data/__out or the whole tree."""
    roots = [repo_root / ".memory"]
    # A pack of any name (ai/, aipack/) sits 2 to 4 levels under projects/: <slug>/<pack>,
    # <group>/<slug>/<pack> or <slug>/<repo>/<pack>, and embedded <group>/<slug>/<repo>/<pack>.
    # Keep .stglobalignore's conflict lines in step. The walk lists each level once, stops at the
    # packs and never enters __data/__out, so nothing in them is listed or swept.
    # A folder counts only if it holds manifest.json, so a .memory in a source/ repo is not swept.
    level = [repo_root / "projects"]
    for depth in range(1, 5):
        level = [d for p in level for d in p.glob("*/") if d.name not in ("__data", "__out")]
        if depth > 1:
            roots += [d / ".memory" for d in level if (d / "manifest.json").is_file()]
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
    Returns False and keeps every copy for the next sweep (session start or prompt) when the canonical file is missing
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
            # Per-machine logs have one writer each, so a copy there means two machines share a name.
            for folder in (root, root / "interactions"):
                for path in folder.glob("*sync-conflict-*"):
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
    header = _HEADER_N % part if part in (2, 3, 4) else _HEADER
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


def registry_dir(env: "dict | None" = None) -> Path:
    """Where session_clock registers sessions: ``${XDG_CACHE_HOME:-$HOME/.cache}/solaris/sessions`` (a
    relative ``XDG_CACHE_HOME`` is ignored, as the XDG spec says)."""
    env = os.environ if env is None else env
    base = env.get("XDG_CACHE_HOME") or ""
    if not os.path.isabs(base):
        base = os.path.join(env.get("HOME") or str(Path.home()), ".cache")
    return Path(base) / "solaris" / "sessions"


def load_registry(session: "str | None", env: "dict | None" = None) -> "dict | None":
    """The session's registry entry when it is usable (fresh, its pack a folder), else None. Never raises."""
    try:
        if not session or not _SESSION_ID.match(session):
            return None
        path = registry_dir(env) / (session + ".json")
        if time.time() - path.stat().st_mtime > _REGISTRY_MAX_AGE:
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or not isinstance(data.get("pack"), str):
            return None
        pack = Path(data["pack"])
        if not pack.is_absolute() or not pack.is_dir():
            return None
        return data
    except Exception:
        return None


def _one_line(value, fallback: str) -> str:
    text = " ".join(str(value).split())[:80] if isinstance(value, str) else ""
    return text or fallback


def _epoch(value) -> "float | None":
    try:
        dt = datetime.fromisoformat(str(value).strip())
        return dt.timestamp() if dt.tzinfo is not None else None
    except Exception:
        return None


def project_context(pack, project_dir=None, updated=None) -> dict:
    """What project mode needs about a pack: its folders, names, primary persona and registry time."""
    pack = Path(pack)
    try:
        manifest = json.loads((pack / "manifest.json").read_text(encoding="utf-8"))
    except Exception:
        manifest = {}
    manifest = manifest if isinstance(manifest, dict) else {}
    project = manifest.get("project") if isinstance(manifest.get("project"), dict) else {}
    agents = manifest.get("agents") if isinstance(manifest.get("agents"), dict) else {}
    primary = agents.get("primary")
    primary = primary if isinstance(primary, str) and _ROLE.match(primary) else "engineer"
    root = Path(project_dir) if isinstance(project_dir, str) and project_dir else pack.parent
    slug = _one_line(project.get("slug"), root.name)
    return {"pack": pack, "dir": root, "slug": slug, "name": _one_line(project.get("name"), slug),
            "primary": primary, "since": _epoch(updated) if updated is not None else None}


def session_project(session: "str | None" = None, env: "dict | None" = None) -> "dict | None":
    """The project context of this session (``CLAUDE_CODE_SESSION_ID`` unless given), or None."""
    env = os.environ if env is None else env
    data = load_registry(env.get(_SESSION_ENV) if session is None else session, env)
    if data is None:
        return None
    try:
        return project_context(data["pack"], data.get("dir"), data.get("updated"))
    except Exception:
        return None


def _label(path, repo_root: Path) -> str:
    """``path`` relative to the Solaris root when it lies under it (the session's folder), else absolute."""
    p = os.path.normpath(os.path.abspath(path))
    for root in (str(repo_root), os.path.realpath(repo_root)):
        root = os.path.normpath(os.path.abspath(root))
        if p.startswith(root + os.sep):
            return p[len(root) + 1:]
    return p


def paused_handover(pack: Path, since: "float | None" = None) -> "Path | None":
    """The newest handover note when the project is paused on purpose: no "resumed at" line, and newer than
    the registry's ``updated`` (when known)."""
    try:
        notes = sorted(p for p in (Path(pack) / ".memory").glob("handover-*.md")
                       if "sync-conflict" not in p.name and p.is_file())
        if not notes:
            return None
        newest = notes[-1]
        if "resumed at" in newest.read_text(encoding="utf-8", errors="replace").lower():
            return None
        if since is not None and newest.stat().st_mtime <= since:
            return None
        return newest
    except Exception:
        return None


def _read(path: Path) -> str:
    try:
        return Path(path).read_text(encoding="utf-8")
    except Exception:
        return "(could not read this file - open it directly)\n"


def _link_rules(link: Path, pack: Path, repo_root: Path) -> list:
    """A linked plugin's ``shared/*.rule.md`` (install-plugin step 5: the "Plugin root" path is relative to
    the folder above the pack); the plugin under the Solaris root as a fallback; else the pointer file."""
    name = link.name[: -len(".link.md")]
    roots = []
    m = _LINK_ROOT.search(_read(link))
    if m:
        raw = Path(m.group(1).strip())
        roots.append(raw if raw.is_absolute() else pack.parent / raw)
    roots.append(Path(repo_root) / "plugins" / name)
    for root in roots:
        shared = Path(os.path.normpath(root)) / "shared"
        if shared.is_dir():
            return sorted(shared.glob("*.rule.md"))
    return [link]


def _session_block(ctx: dict, repo_root: Path) -> str:
    pack, proj = _label(ctx["pack"], repo_root), _label(ctx["dir"], repo_root)
    lines = [
        "This session works on project %s (`%s`): project dir `%s`, pack `%s`; you act as its primary persona, "
        "`%s`. The orchestrator's `solaris/solaris.agent.md` and the framework `.memory/instructions.md` are not "
        "part of this set (a pack never reads the framework memory)." % (
            ctx["name"], ctx["slug"], proj, pack, ctx["primary"]),
        "Clock: start `uv run -m solaris.tools.session_clock --dir %s` as a background command, alone in its "
        "call (pass `--after <time>` from the previous clock's `next:` line)." % shlex.quote(proj),
        "MUST-READ now, whole (not inlined here), and again after every compaction:",
        "- `%s/%s.agent.md` (your persona)" % (pack, ctx["primary"]),
        "- `%s/instructions.md` (the project's procedures, gotchas and standing rules)" % pack,
        "- `%s/.memory/context.md` (where the work stands)" % pack,
    ]
    note = paused_handover(ctx["pack"], ctx.get("since"))
    if note is not None:
        lines.append("- `%s` - PAUSED: the newest handover note has no \"resumed at\" line; read it first"
                     % _label(note, repo_root))
    return "\n".join(lines) + "\n"


def project_items(ctx: dict, repo_root: "Path | None" = None) -> dict:
    """The project set: ``floor`` (part 1), ``card`` (or None) and ``block`` (part 2), and ``rules`` - every
    always-on rule in priority order, smallest first (the largest become pointers first when 8 parts are
    not enough). Each item is (label, body)."""
    repo_root = REPO_ROOT if repo_root is None else Path(repo_root)
    pack = Path(ctx["pack"])
    card = pack / CARD
    files = sorted(pack.glob("rules/*.rule.md")) + sorted(pack.glob("plugins/*/*.rule.md"))
    for link in sorted(pack.glob("plugins/*.link.md")):
        files += _link_rules(link, pack, repo_root)
    files += [repo_root / rel for rel in PROJECT_RULES]
    seen, rules = set(), []
    for path in files:
        key = os.path.normpath(os.path.abspath(path))
        if key not in seen:
            seen.add(key)
            rules.append((_label(path, repo_root), _read(path)))
    rules.sort(key=lambda item: (len(item[1]), item[0]))
    return {"floor": [(rel, _read(repo_root / rel)) for rel in PROJECT_FLOOR],
            "card": (_label(card, repo_root), _read(card)) if card.is_file() else None,
            "block": (_BLOCK_LABEL, _session_block(ctx, repo_root)),
            "rules": rules}


def _pointer(rel: str) -> str:
    return "\n----- " + rel + " (POINTER - read this file yourself NOW) -----\n"


def _whole(label: str, body: str) -> str:
    return "\n----- " + label + " -----\n" + body


def _fit(label: str, body: str, room: int) -> dict:
    """An entry for ``body`` within ``room``: whole, else truncated at a paragraph (when at least
    _MIN_TRUNCATED characters fit), else a pointer."""
    entry = {"label": label, "size": len(body), "state": "whole", "text": _whole(label, body)}
    if len(entry["text"]) <= room:
        return entry
    delim = "\n----- " + label + " (TRUNCATED - read the rest yourself) -----\n"
    tail = "\n[... truncated at inline budget - open " + label + " for the rest]\n"
    space = room - len(delim) - len(tail)
    if space >= _MIN_TRUNCATED:
        head = body[:space]
        # A paragraph break near the end, else a line break: a long section must not cost a whole part.
        cut = head.rfind("\n\n")
        if cut < space - 400:
            cut = head.rfind("\n")
        head = head[: cut if cut > 0 else space]
        entry.update(state="truncated", kept=len(head), text=delim + head + tail)
    else:
        entry.update(state="pointer", text=_pointer(label))
    return entry


def _first_fit(rules: list, pointers: list, rem: dict, cap, limit: int, force: bool = False) -> "dict | None":
    """First-fit decreasing of ``rules`` into the open parts (``rem``: part -> room left), opening parts
    3..``limit`` as needed: a file that fits an empty part opens the lowest free part it fits; a larger one
    gets a free part of its own (the roomiest, so not part 4 while another is free), truncated to it. Then
    the ``pointers`` lines. Returns {part: entries}, or None when ``limit`` parts are not enough (``force``
    puts what does not fit into the roomiest part instead; rendering cuts it at the budget)."""
    rem, placed = dict(rem), {}
    free = [k for k in range(3, limit + 1) if k not in rem]

    def place(label: str, body: "str | None") -> bool:
        need = len(_pointer(label) if body is None else _whole(label, body))
        k = next((k for k in sorted(rem) if rem[k] >= need), None)
        if k is None:
            fits = [j for j in free if cap(j) >= need]
            if fits or free:
                k = min(fits) if fits else max(free, key=lambda j: (cap(j), -j))
                free.remove(k)
                rem[k] = cap(k)
            elif force:
                k = max(sorted(rem), key=lambda j: rem[j])
            else:
                return False
        entry = (_fit(label, body, rem[k]) if body is not None
                 else {"label": label, "size": 0, "state": "pointer", "text": _pointer(label)})
        placed.setdefault(k, []).append(entry)
        rem[k] -= len(entry["text"])
        return True

    for label, body in sorted(rules, key=lambda item: (-len(item[1]), item[0])):
        if not place(label, body):
            return None
    for label, _ in pointers:
        if not place(label, None):
            return None
    return placed


def _project_header(ctx: dict, part: int) -> str:
    return (_HEADER_PROJECT if part == 1 else _HEADER_PROJECT_N) % (ctx["slug"], part)


def plan_project(ctx: dict, repo_root: "Path | None" = None, budget: "int | None" = None) -> dict:
    """Where every file of a project session goes (deterministic, so each hook call computes the same).

    Part 1 holds the framework floor; part 2 starts with the card and the generated block (the block always
    whole; a card over the room left is truncated); the rules fill part 2's rest and parts 3-8 first-fit by
    decreasing size, a file larger than an empty part getting a part of its own. When 8 parts are not
    enough, the lowest-priority rules become pointers. Returns {"parts": {1..8: entries}, "needed": the
    parts the files need without pointers, "pointers": the labels made pointers for room}.
    """
    budget = _budget() if budget is None else budget
    items = project_items(ctx, repo_root)

    def cap(k: int) -> int:
        return budget - len(_project_header(ctx, k)) - (_NOTE_ROOM if k == 4 else 0)

    parts = {k: [] for k in range(1, PROJECT_PARTS + 1)}
    room = cap(1)
    for i, (label, body) in enumerate(items["floor"]):
        later = sum(len(_pointer(rest)) for rest, _ in items["floor"][i + 1:])
        degraded = any(e["state"] != "whole" for e in parts[1])
        entry = _fit(label, body, -1 if degraded else room - later)
        parts[1].append(entry)
        room -= len(entry["text"])
    block = {"label": items["block"][0], "size": len(items["block"][1]), "state": "whole",
             "text": _whole(*items["block"])}
    room = cap(2) - len(block["text"])
    if items["card"] is not None:
        card = _fit(*items["card"], room)
        parts[2].append(card)
        room -= len(card["text"])
    parts[2].append(block)
    rules = items["rules"]
    whole = _first_fit(rules, [], {2: room}, cap, 99, force=True)
    needed = len({1, 2} | set(whole))
    placed = None
    for n in range(len(rules) + 1):
        keep, drop = rules[: len(rules) - n], rules[len(rules) - n:]
        placed = _first_fit(keep, drop, {2: room}, cap, PROJECT_PARTS)
        if placed is not None:
            break
    if placed is None:
        placed = _first_fit([], rules, {2: room}, cap, PROJECT_PARTS, force=True)
        drop = rules
    for k, entries in placed.items():
        parts[k] += entries
    return {"parts": parts, "needed": needed, "pointers": [label for label, _ in drop]}


def render_project(ctx: dict, repo_root: "Path | None" = None, budget: "int | None" = None,
                   part: int = 1, plan: "dict | None" = None) -> str:
    """One part of a project session's set; empty when the part holds nothing."""
    budget = _budget() if budget is None else budget
    plan = plan_project(ctx, repo_root, budget) if plan is None else plan
    entries = plan["parts"].get(part)
    if not entries:
        return ""
    text = _project_header(ctx, part) + "".join(e["text"] for e in entries)
    if len(text) > budget:  # only with absurdly many files or a tiny budget
        text = text[: budget - len(_CLAMP)] + _CLAMP
    return text


def remind_project(ctx: dict, repo_root: "Path | None" = None) -> str:
    repo_root = REPO_ROOT if repo_root is None else Path(repo_root)
    pack = _label(ctx["pack"], repo_root)
    card = ("the hard-rules card `%s/%s`" % (pack, CARD) if (Path(ctx["pack"]) / CARD).is_file()
            else "no hard-rules card yet (`%s/%s`)" % (pack, CARD))
    return _REMINDER_PROJECT % {"name": ctx["name"], "slug": ctx["slug"], "pack": pack, "card": card,
                                "primary": ctx["primary"], "dir": shlex.quote(_label(ctx["dir"], repo_root))}


def _arg(argv: list, flag: str) -> "str | None":
    """The value after ``flag`` in argv, or None."""
    if flag in argv:
        i = argv.index(flag)
        if i + 1 < len(argv) and not argv[i + 1].startswith("--"):
            return argv[i + 1]
    return None


def check_project(argv: list, repo_root: "Path | None" = None) -> str:
    """``--check --session <id>`` / ``--check --pack <path>``: the project-mode packing, part by part."""
    repo_root = REPO_ROOT if repo_root is None else Path(repo_root)
    pack_arg = _arg(argv, "--pack")
    if pack_arg is not None:
        pack = Path(pack_arg).expanduser()
        if not pack.is_absolute() and not pack.is_dir():
            pack = repo_root / pack
        if not pack.is_dir():
            return "read_first check: no pack folder at %s" % pack_arg
        ctx = project_context(pack.absolute())
    else:
        session = _arg(argv, "--session") or os.environ.get(_SESSION_ENV, "")
        ctx = session_project(session)
        if ctx is None:
            return ("read_first check: session %r has no usable registry file in %s (missing, unreadable, "
                    "older than 14 days, or its pack is gone), so it runs in orchestrator mode:\n%s"
                    % (session, registry_dir(), check(repo_root)))
    budget = _budget()
    plan = plan_project(ctx, repo_root, budget)
    card_label = _label(Path(ctx["pack"]) / CARD, repo_root)
    card = next((e for e in plan["parts"][2] if e["label"] == card_label), None)
    if card is None:
        card_line = "none (%s)" % card_label
    elif card["state"] == "whole":
        card_line = "whole (%d characters)" % card["size"]
    else:
        card_line = "%s (%d characters, over the room left in part 2: the rest is a pointer; shorten the card)" % (
            "TRUNCATED to %d" % card["kept"] if card["state"] == "truncated" else "POINTER only", card["size"])
    lines = [
        "read_first check (project mode): budget=%d per part (%s), at most %d parts"
        % (budget, _BUDGET_ENV, PROJECT_PARTS),
        "  project %s (`%s`), dir %s, pack %s, primary %s" % (
            ctx["name"], ctx["slug"], _label(ctx["dir"], repo_root), _label(ctx["pack"], repo_root),
            ctx["primary"]),
        "  card: " + card_line,
    ]
    note = paused_handover(ctx["pack"], ctx["since"])
    if note is not None:
        lines.append("  paused: %s" % _label(note, repo_root))
    used = []
    for part, entries in sorted(plan["parts"].items()):
        for e in entries:
            state = "truncated to %d" % e["kept"] if e["state"] == "truncated" else e["state"]
            lines.append("  %8d  %-17s %s" % (e["size"], state, e["label"]))
        rendered = render_project(ctx, repo_root, budget, part, plan)
        if rendered:
            used.append(str(part))
        lines.append("  part %d rendered payload: %d bytes -> %s" % (
            part, len(rendered), "empty (prints nothing)" if not rendered
            else "OK (inline)" if len(rendered) <= budget else "OVER BUDGET"))
    lines.append("  parts used: %d of %d (%s)" % (len(used), PROJECT_PARTS, ", ".join(used)))
    if plan["needed"] > PROJECT_PARTS:
        lines.append("  WARNING: the files need %d parts, more than %d; the lowest-priority (largest) are "
                     "pointers: %s" % (plan["needed"], PROJECT_PARTS, ", ".join(plan["pointers"])))
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
            print(check_project(argv) if ("--session" in argv or "--pack" in argv) else check())
            return 0
        remind = "--remind" in argv
        value = _arg(argv, "--part")
        part = int(value) if value in [str(k) for k in range(1, PROJECT_PARTS + 1)] else 1
        if not remind and part == 1:
            migrate_legacy_memory()  # session start: pick up a pre-0.19 checkout's memory/ folder
        ide = detect_ide()
        # Cursor carries hook context as JSON without spilling large payloads to a file, so it
        # keeps the full set; the inline budget exists for Claude Code's stdout-persist behavior.
        full_budget = 1_000_000 if ide == "cursor" else None
        project = session_project()
        if project is not None:
            text = remind_project(project) if remind else render_project(project, budget=full_budget, part=part)
        elif remind or part <= 4:
            text = _REMINDER if remind else render_full(budget=full_budget, part=part)
        else:
            return 0  # parts 5-8 carry only a project session's files
        if remind or part == 4:
            # The sweep and its note ride on part 4, the smallest payload: part 1 sits near the
            # budget, and past 10,000 characters the harness stops inlining it. The per-prompt
            # remind repeats the sweep so a conflict that happens mid-session is caught too.
            note = heal_sync_conflicts()
            if note:
                line = "[Solaris] " + note + "\n"
                if project is not None and not remind:
                    # Project part 4 keeps _NOTE_ROOM free; a longer note is cut, never the rules.
                    room = (full_budget or _budget()) - len(text)
                    line = line if len(line) <= room else (line[: room - 4] + "...\n" if room > 40 else "")
                text = line + text
        if text:
            emit(text, ide)
    except Exception:
        pass  # fail-safe: a context-loading hook must never break the user's turn
    return 0


if __name__ == "__main__":
    sys.exit(main())
