# Copyright 2026 Mihail Yurasov <me@yurasov.me>
# SPDX-License-Identifier: Apache-2.0

"""Session hook: auto-load the framework "read-first" set into the agent's context. Stdlib only.

AGENTS.md lists files the orchestrator must read at the start of every session (the orchestrator role,
the commit + safety rules, and the operating memory). Those are only *pointed to*, so loading them used to
depend on the agent choosing to read them - the exact step that gets skipped. This tool makes the load
deterministic by emitting their contents from a hook, so the harness (not the model) puts them in context.

Two modes:

- **no args** - full load. Print the concatenated read-first files under an authoritative header. Wired to
  the session-start hook (Claude Code ``SessionStart``; Cursor ``sessionStart``) so it fires once per
  session and again after a compaction / clear / resume.
- **``--remind``** - print a one-line forcing reminder that the set was loaded. Wired to Claude Code's
  ``UserPromptSubmit`` so the Hybrid model gets a cheap per-turn nudge on top of the once-per-session load.
  (Cursor's ``beforeSubmitPrompt`` cannot inject context - its output is only ``{continue, user_message}`` -
  so the per-prompt remind is Claude-only; Cursor relies on the ``sessionStart`` load alone.)

Output format is IDE-aware: Cursor hooks read a JSON object (``additional_context``); Claude Code hooks read
plain stdout. The tool detects the IDE from the environment and emits whichever the caller expects.

Like the other hooks it is **fail-safe**: it never raises, always exits 0, and tolerates missing files / a
missing venv - a broken read-first load must never block the user's turn. It does not read stdin (avoiding
the blocking footgun); it keys only off argv and the environment.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

# The AGENTS.md "Read first" set, in reading order, relative to the repo root.
READ_FIRST = (
    "solaris/solaris.agent.md",
    "solaris/rules/commits.rule.md",
    "solaris/rules/safety.rule.md",
    "memory/instructions.md",
)

_HEADER = (
    "=== SOLARIS READ-FIRST (auto-loaded every session by the read_first hook) ===\n"
    "These are the framework's authoritative read-first files (the AGENTS.md \"Read first\" set), "
    "loaded for you so you do not have to open them by hand. Obey them before acting; they override "
    "default behavior. If a file is shown empty/missing below, open it yourself.\n"
)

_REMINDER = (
    "[Solaris read-first] The authoritative set (solaris.agent.md + the commit & safety rules + "
    "memory/instructions.md) was loaded at session start - follow it. Quick reminders: bare `ssh`/`open` "
    "are blocked, use the /tmp wrappers (`hss`, `nepo`); confirm before destructive / remote-mutating / "
    "outward actions; log the turn to memory/interactions.jsonl."
)


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


def render_full(repo_root: Path = REPO_ROOT) -> str:
    """The header plus each read-first file, delimited by its path. Missing files are noted, not fatal."""
    parts = [_HEADER]
    for rel in READ_FIRST:
        parts.append("\n----- " + rel + " -----\n")
        try:
            parts.append((Path(repo_root) / rel).read_text(encoding="utf-8"))
        except Exception:
            parts.append("(could not read this file - open it directly)\n")
    return "".join(parts)


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
        remind = "--remind" in argv
        text = _REMINDER if remind else render_full()
        emit(text, detect_ide())
    except Exception:
        pass  # fail-safe: a context-loading hook must never break the user's turn
    return 0


if __name__ == "__main__":
    sys.exit(main())
