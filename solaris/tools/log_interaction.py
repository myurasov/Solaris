"""Prompt-submit hook: append the raw user request to the framework interaction log. Stdlib only.

Wired from ``.claude/settings.json`` (UserPromptSubmit) and ``.cursor/hooks.json`` (beforeSubmitPrompt).
It is **fail-safe**: it never raises, never blocks the turn, always exits 0, prints nothing, and tolerates
missing dirs / a missing venv.

The hook records only the *request* (the outcome is unknown at submit time), and always to the **framework
master log** ``memory/interactions.jsonl`` - the complete request stream, including project (handed-off)
work, because "hand off" does not change the cwd. The agent additionally authors curated
``{ts, project, request, outcome}`` entries into this master log and into each touched project's
``ai/memory/interactions.jsonl``; this hook is the backstop that guarantees a request is never lost.
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
    record of requests; per-project request+outcome entries are authored by the agent.
    """
    return Path(repo_root) / "memory" / "interactions.jsonl"


def append(log: Path, entry: dict) -> None:
    log.parent.mkdir(parents=True, exist_ok=True)
    with open(log, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry, ensure_ascii=True) + "\n")


def main() -> int:
    try:
        payload = read_payload(sys.stdin)
        entry = build_entry(payload)
        append(log_path(), entry)
    except Exception:
        pass  # fail-safe: a logging hook must never break the user's turn
    return 0


if __name__ == "__main__":
    sys.exit(main())
