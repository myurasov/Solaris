# Copyright 2026 Mikhail Yurasov <me@yurasov.me>
# SPDX-License-Identifier: Apache-2.0

"""Prompt-submit hook: append the raw user prompt to the framework interaction log. Stdlib only.

Wired from ``.claude/settings.json`` (UserPromptSubmit) and ``.cursor/hooks.json`` (beforeSubmitPrompt),
always with **no arguments** and a JSON payload on stdin. In that hook context it is **fail-safe**: it never
raises, never blocks the turn, always exits 0, prints nothing, and tolerates missing dirs / a missing venv.

It is **not a CLI** and must never be called by hand. As a guard, if it is invoked with any arguments (or
interactively with no piped payload) it prints a one-line notice and exits non-zero instead of blocking on
``stdin.read()`` - the agent authors the full ``{ts, project, prompt, request, outcome}`` entries itself.

The hook records only the raw *prompt* (the interpreted request and the outcome are unknown at submit time)
as a ``{ts, cwd, ide, prompt}`` backstop line, and always to this machine's file in the **framework master
log**, ``.memory/interactions/<machine>.jsonl`` (one file per machine, so a checkout synced between machines
never has two writers on one file; see solaris.tools.interactions) - the complete prompt stream, including
project (handed-off) work, because "hand off" does not change the cwd. The agent additionally logs the full
``{ts, project, prompt, request, outcome}`` entry (``prompt`` the raw prompt, ``request`` its interpretation)
with ``uv run -m solaris.tools.interactions add``, which writes this master log and, for project work, the
project's ``<pack>/.memory/interactions/<machine>.jsonl`` (``<pack>`` is the project's ai-pack folder:
``aipack/`` by default, ``ai/`` in older projects); so the master mixes these backstop lines with the agent's
full entries, and this hook guarantees a prompt is never lost.

After logging, the hook merges any Syncthing conflict copies of memory-folder logs (the read_first sweep), so a
conflict is repaired on the next prompt rather than at the next session start. Under Claude Code the
``read_first --remind`` hook does that instead, because it can also show a note about copies it leaves.
"""

from __future__ import annotations

import datetime as _dt
import json
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


def read_payload(stream) -> dict:
    try:
        raw = stream.read()
        if not raw or not raw.strip():
            return {}
        data = json.loads(raw)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def detect_ide(env: "dict | None" = None) -> str:
    env = os.environ if env is None else env
    if env.get("CLAUDECODE") or env.get("CLAUDE_CODE"):
        return "claude"
    if any(k.startswith("CURSOR") for k in env):
        return "cursor"
    return "unknown"


def build_entry(payload: dict, env: "dict | None" = None) -> dict:
    prompt = payload.get("prompt") or payload.get("user_prompt") or ""
    if not isinstance(prompt, str):
        prompt = str(prompt)
    if len(prompt) > 280:
        prompt = prompt[:280] + "..."
    return {
        "ts": _dt.datetime.now(_dt.timezone.utc).isoformat(),
        "cwd": str(payload.get("cwd") or os.getcwd()),
        "ide": detect_ide(env),
        "prompt": prompt,
    }


def log_path(repo_root: Path = REPO_ROOT) -> Path:
    """This machine's file in the framework master interaction log; the hook always writes here.

    Routing by cwd is deliberately *not* done: "hand off" to a project does not change the cwd, so a
    cwd-based rule would both miss handed-off turns and split the master stream. This log is the complete
    prompt stream; the full {ts, project, prompt, request, outcome} entries are logged by the agent.
    """
    from solaris.tools import interactions as I  # imported lazily: the hook must survive a broken import
    return I.machine_log(I.framework_memory(repo_root))


def append(log: Path, entry: dict) -> None:
    from solaris.tools import interactions as I
    I.append_line(log, entry)


_NOT_A_CLI = (
    "solaris.tools.log_interaction is the prompt-submit HOOK (it reads a JSON payload on stdin); "
    "it is not a command-line tool and takes no arguments.\n"
    "Do not call it by hand. To record an interaction, log the authoritative "
    "{ts, project, prompt, request, outcome} line with\n"
    "  uv run -m solaris.tools.interactions add --project <name> --prompt ... --request ... --outcome ... "
    "[--dir <project>]\n"
    "which writes this machine's file in the framework master log and, with --dir, in the project's "
    "<pack>/.memory/interactions/ (its ai-pack folder: aipack/, or ai/ in older projects)."
)


def main(argv: "list[str] | None" = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    # Footgun guard: this module is a stdin hook, never a CLI. If it is called with
    # arguments (or interactively with no piped payload) fail fast with guidance
    # instead of blocking forever on stdin.read() (which once hung a session ~2min).
    if argv:
        print(_NOT_A_CLI, file=sys.stderr)
        return 2
    try:
        if sys.stdin is None or sys.stdin.isatty():
            print(_NOT_A_CLI, file=sys.stderr)
            return 2
    except Exception:
        pass
    try:
        # a legacy pre-0.19 memory/ must be renamed before this hook mkdirs a fresh .memory/
        # (which would orphan the old folder forever); read_first owns the migration logic
        from solaris.tools.read_first import migrate_legacy_memory
        migrate_legacy_memory()
        payload = read_payload(sys.stdin)
        entry = build_entry(payload)
        append(log_path(), entry)
    except Exception:
        pass  # fail-safe: a logging hook must never break the user's turn
    try:
        if detect_ide() != "claude":  # under Claude Code, read_first --remind sweeps and can show the note
            from solaris.tools.read_first import heal_sync_conflicts
            heal_sync_conflicts()
    except Exception:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
