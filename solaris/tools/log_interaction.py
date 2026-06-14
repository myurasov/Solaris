# Copyright 2026 Mihail Yurasov <me@yurasov.me>
# SPDX-License-Identifier: Apache-2.0

"""Prompt-submit hook: append the raw user prompt to the framework interaction log. Stdlib only.

Wired from ``.claude/settings.json`` (UserPromptSubmit) and ``.cursor/hooks.json`` (beforeSubmitPrompt),
always with **no arguments** and a JSON payload on stdin. In that hook context it is **fail-safe**: it never
raises, never blocks the turn, always exits 0, prints nothing, and tolerates missing dirs / a missing venv.

It is **not a CLI** and must never be called by hand. As a guard, if it is invoked with any arguments (or
interactively with no piped payload) it prints a one-line notice and exits non-zero instead of blocking on
``stdin.read()`` - the agent authors the full ``{ts, project, prompt, request, outcome}`` entries itself.

The hook records only the raw *prompt* (the interpreted request and the outcome are unknown at submit time)
as a ``{ts, cwd, ide, prompt}`` backstop line, and always to the **framework master log**
``memory/interactions.jsonl`` - the complete prompt stream, including project (handed-off) work, because
"hand off" does not change the cwd. The agent additionally authors the full ``{ts, project, prompt, request,
outcome}`` entry (``prompt`` the raw prompt, ``request`` its interpretation) into this master log and into
each touched project's ``ai/memory/interactions.jsonl``; so the master mixes these backstop lines with the
agent's full entries, and this hook guarantees a prompt is never lost.
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
    """The framework master interaction log; the hook always writes here.

    Routing by cwd is deliberately *not* done: "hand off" to a project does not change the cwd, so a
    cwd-based rule would both miss handed-off turns and split the master stream. This log is the complete
    prompt stream; the full {ts, project, prompt, request, outcome} entries are authored by the agent.
    """
    return Path(repo_root) / "memory" / "interactions.jsonl"


def append(log: Path, entry: dict) -> None:
    log.parent.mkdir(parents=True, exist_ok=True)
    with open(log, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry, ensure_ascii=True) + "\n")


_NOT_A_CLI = (
    "solaris.tools.log_interaction is the prompt-submit HOOK (it reads a JSON payload on stdin); "
    "it is not a command-line tool and takes no arguments.\n"
    "Do not call it by hand. To record an interaction, append the authoritative "
    "{ts, project, prompt, request, outcome} line yourself to BOTH the project's "
    "ai/memory/interactions.jsonl and the framework master memory/interactions.jsonl."
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
        payload = read_payload(sys.stdin)
        entry = build_entry(payload)
        append(log_path(), entry)
    except Exception:
        pass  # fail-safe: a logging hook must never break the user's turn
    return 0


if __name__ == "__main__":
    sys.exit(main())
