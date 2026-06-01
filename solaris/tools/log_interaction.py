"""Prompt-submit hook: append one line to the interaction log. Stdlib only.

Wired from ``.claude/settings.json`` (UserPromptSubmit) and ``.cursor/hooks.json`` (beforeSubmitPrompt).
It is **fail-safe**: it never raises, never blocks the turn, always exits 0, prints nothing, and tolerates
missing dirs / a missing venv. Routing: if the cwd is inside ``projects/<slug>/`` (and that ai-setup
exists), the entry goes to that project's ``ai/memory/interactions.jsonl``; otherwise to the framework
``memory/interactions.jsonl``.
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


def route(cwd: "str | Path", repo_root: Path = REPO_ROOT) -> Path:
    """Return the interaction-log path for a given cwd."""
    projects = (Path(repo_root) / "projects").resolve()
    try:
        rel = Path(cwd).resolve().relative_to(projects)
        slug = rel.parts[0]
        if (Path(repo_root) / "projects" / slug / "ai").exists():
            return Path(repo_root) / "projects" / slug / "ai" / "memory" / "interactions.jsonl"
    except (ValueError, IndexError, OSError):
        pass
    return Path(repo_root) / "memory" / "interactions.jsonl"


def append(log_path: Path, entry: dict) -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with open(log_path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry, ensure_ascii=True) + "\n")


def main() -> int:
    try:
        payload = read_payload(sys.stdin)
        entry = build_entry(payload)
        append(route(entry["cwd"]), entry)
    except Exception:
        pass  # fail-safe: a logging hook must never break the user's turn
    return 0


if __name__ == "__main__":
    sys.exit(main())
