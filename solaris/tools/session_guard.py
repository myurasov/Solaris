# Copyright 2026 Mikhail Yurasov <me@yurasov.me>
# SPDX-License-Identifier: Apache-2.0

"""Claude Code Stop and StopFailure hook: a project session never goes idle unnoticed (stdlib only).

A session is a project session when solaris.tools.session_clock ``--dir`` recorded it in the session registry,
``${XDG_CACHE_HOME:-~/.cache}/solaris/sessions/<session id>.json``. For any other session, and for a subagent's
turn (a payload with an ``agent_id``), this hook does nothing; ``session_clock --forget`` ends project mode. It reads
the hook's JSON on stdin and, at each turn end of a project session, asks whether a wake-up is armed:

- **Armed:** one of the session's ``background_tasks`` is a running shell task that runs session_clock; when the
  payload has no ``background_tasks`` (StopFailure, older harnesses), the pack's ``clock.json`` is armed, names this
  session, and its process is a live session_clock for this project here.
- **Paused** (an ``unarmed`` turn end is fine): the pause marker read_first uses (the newest
  ``<pack>/.memory/handover-*.md`` by the timestamp in its name, Syncthing conflict copies skipped, with no "resumed
  at" line and newer than the registry's ``updated``), or a ``session_clock --stop`` newer than ``updated``. A
  ``paused`` alert is logged the first time a given note or stop is honoured.
- **Held elsewhere:** another session's armed clock holds the project (alive here and running this project, or on
  another machine until the earlier of 30 minutes after its ``wakes_at`` and 2 hours after its ``started``). The
  turn is not sent back, so two sessions never take the clock from each other, but the turn end is logged.

A Stop that is unarmed, not paused, not held elsewhere and not a refusal is sent back (``{"decision": "block"}``)
with a reason that names the project and gives the exact command that starts its clock, at most twice per turn (a
count in ``<session id>.guard.json`` beside the registry, reset at a new turn or when armed). Every other unarmed
turn end that is not paused appends one ``unarmed-turn-end`` alert per turn, saying why: the block cap, the API
error that ended the turn (StopFailure, transient errors included), a refusal, or the session holding the clock.

A **safety-classifier refusal** is never sent back (a new request would be refused again) and appends a
``classifier-stop`` alert, once per refused turn (the same text within a minute counts once). It is told by the
text: "safeguards flagged", "safety classifier", "can't help with this. Start a new session", "can't respond to
your last message", or, on StopFailure only, the word "refusal" or the error ``refusal``. On Stop the text must
start with "API Error" (a model may write about the classifier), and then the transcript's last main-thread
assistant entry with ``stop_reason: refusal`` also counts (or alone, when the message is empty). StopFailure errors
other than a refusal append ``api-error``, except the transient ``rate_limit``, ``overloaded`` and ``server_error``.

Alerts go to ``<pack>/.memory/alerts.jsonl``, one JSON object a line: ``{"ts", "kind", "session", "detail"}``. The
hook never fails a turn: on any problem (unreadable or late stdin, a missing registry or pack, an unwritable state
file) it exits 0 and prints nothing. No network; it runs well under a second.

Wiring (``.claude/settings.json``)::

    "Stop": [{"hooks": [{"type": "command", "timeout": 10,
               "command": "uv run --directory \\"${CLAUDE_PROJECT_DIR:-.}\\" -m solaris.tools.session_guard"}]}],
    "StopFailure": [... the same ...]
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import select
import shlex
import socket
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from solaris.tools import fileio
from solaris.tools import session_clock as C

READ_TIMEOUT = 2.0   # seconds to wait for the payload
MAX_PAYLOAD = 16 * 1024 * 1024
MAX_BLOCKS = 2
TRANSIENT = frozenset({"rate_limit", "overloaded", "server_error"})
REPEAT_WINDOW = 60   # seconds within which the same refusal, or a second alert of one turn, counts once
PEER_GRACE = 30 * 60   # another machine's armed clock counts this long after its wake-up...
PEER_MAX = 2 * 3600    # ...and never longer than this after its start
TRANSCRIPT_TAIL = 256 * 1024
SHELL_TYPES = frozenset({"shell", "bash", "local_bash"})
LIVE_STATUSES = frozenset({"running", "pending"})
# Claude Code's refusal wordings: the safeguards notice, and the short forms it shows for other refusals.
_CLAUDE_REFUSAL = re.compile(r"safeguards?\s+flagged|safety\s+classifier|can['’]t\s+help\s+with\s+this\.?\s+start\s+a"
                             r"\s+new\s+session|can['’]t\s+respond\s+to\s+your\s+last\s+message", re.I)
_REFUSAL_WORD = re.compile(r"\brefusal\b", re.I)


def read_payload(stream=None, timeout: float = READ_TIMEOUT) -> dict:
    """The hook's JSON object from stdin; {} on a tty, a late or oversized payload, or anything not an object."""
    stream = sys.stdin if stream is None else stream
    try:
        if stream is None or stream.isatty():
            return {}
    except (AttributeError, ValueError, OSError):
        return {}
    try:
        fd = stream.fileno()
    except (AttributeError, ValueError, OSError):
        fd = None
    try:
        if fd is None:
            raw = stream.read(MAX_PAYLOAD + 1)
            if isinstance(raw, bytes):
                raw = raw.decode("utf-8", "replace")
        else:
            chunks, size, deadline = [], 0, time.monotonic() + timeout
            while True:
                left = deadline - time.monotonic()
                if left <= 0 or not select.select([fd], [], [], left)[0]:
                    return {}
                chunk = os.read(fd, 65536)
                if not chunk:
                    break
                chunks.append(chunk)
                size += len(chunk)
                if size > MAX_PAYLOAD:
                    return {}
            raw = b"".join(chunks).decode("utf-8", "replace")
        if len(raw) > MAX_PAYLOAD:
            return {}
        data = json.loads(raw)
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _iso(t: float) -> str:
    return datetime.fromtimestamp(t, timezone.utc).astimezone().isoformat(timespec="seconds")


def one_line(text, limit: int = 160) -> str:
    text = re.sub(r"\s*\(https?://[^)\s]*\)", "", str(text or ""))
    text = " ".join(text.split())
    return text if len(text) <= limit else text[:limit - 3].rstrip() + "..."


def _first_line(text: str, limit: int = 120) -> str:
    text = (text or "").strip()
    return one_line(text.splitlines()[0], limit) if text else "no message text"


def load_registry(sid, now: float, env=None) -> "dict | None":
    """The session's registry entry with ``since`` (its ``updated`` in epoch seconds), or None (a stale one is
    deleted)."""
    path = C.registry_path(sid, env)
    if path is None:
        return None
    try:
        mtime = path.stat().st_mtime
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or data.get("session") != sid:
        return None
    try:
        updated = C.parse_time(data.get("updated"))
    except ValueError:
        return None
    if now - max(updated, mtime) > C.REGISTRY_DAYS * 86400:
        for stale in (path, C.guard_state_path(sid, env)):
            try:
                stale.unlink()
            except OSError:
                pass
        return None
    if not all(isinstance(data.get(k), str) and data[k] for k in ("solaris", "dir", "pack")):
        return None
    return dict(data, since=updated)


def load_state(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def save_state(path: Path, state: dict) -> bool:
    try:
        fileio.write_text_atomic(path, json.dumps(state) + "\n")
        return True
    except OSError:
        return False


def append_alert(pack: Path, kind: str, sid, detail: str, now: float) -> None:
    line = json.dumps({"ts": _iso(now), "kind": kind, "session": sid, "detail": detail}, ensure_ascii=False)
    fd = os.open(Path(pack) / ".memory" / "alerts.jsonl", os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o644)
    try:
        os.write(fd, (line + "\n").encode("utf-8"))
    finally:
        os.close(fd)


def transcript_refused(path) -> bool:
    """True when the transcript's last main-thread assistant entry stopped on a refusal."""
    if not isinstance(path, str) or not path:
        return False
    try:
        with open(path, "rb") as fh:
            fh.seek(0, os.SEEK_END)
            fh.seek(max(0, fh.tell() - TRANSCRIPT_TAIL))
            tail = fh.read()
    except (OSError, ValueError):
        return False
    for raw in reversed(tail.splitlines()):
        if b'"assistant"' not in raw:
            continue
        try:
            entry = json.loads(raw)
        except ValueError:
            continue
        if not isinstance(entry, dict) or entry.get("type") != "assistant" or entry.get("isSidechain"):
            continue
        message = entry.get("message")
        return isinstance(message, dict) and message.get("stop_reason") == "refusal"
    return False


def is_refusal(event: str, text: str, error: str = "", transcript=None) -> bool:
    """A safety-classifier refusal (module docstring)."""
    head = (text or "").strip()
    if error == "refusal":
        return True
    if event == "StopFailure":
        return bool(_CLAUDE_REFUSAL.search(head) or _REFUSAL_WORD.search(head))
    if not head:
        return transcript_refused(transcript)
    if not head.lower().startswith("api error"):
        return False
    return bool(_CLAUDE_REFUSAL.search(head)) or transcript_refused(transcript)


def refusal_detail(event: str, text: str, error: str) -> str:
    category = re.search(r"Details:\s*`?\[([\w.-]+)\]", text or "")
    return (f"{event}" + (f" {error}" if error else "") + f": {_first_line(text, 160)}"
            + (f" [{category.group(1)}]" if category else ""))


def clock_task_running(tasks) -> bool:
    for task in tasks:
        if not isinstance(task, dict):
            continue
        cmd = task.get("command")
        if (str(task.get("type", "")).lower() in SHELL_TYPES and str(task.get("status", "")).lower() in LIVE_STATUSES
                and isinstance(cmd, str) and C.MODULE in cmd
                and not re.search(r"--(?:stop|status|check|forget)\b", cmd)):
            return True
    return False


def armed(payload: dict, clock: "dict | None", sid, project) -> bool:
    tasks = payload.get("background_tasks")
    if isinstance(tasks, list):
        return clock_task_running(tasks)
    return bool(clock and clock.get("state") == "armed" and clock.get("session") == sid
                and clock.get("host") == socket.gethostname() and C.is_clock_process(clock.get("pid"), project))


def holder(clock: "dict | None", sid, now: float, project) -> "str | None":
    """Who holds the project's clock when another session's armed clock does, else None."""
    if not clock or clock.get("state") != "armed" or clock.get("session") == sid:
        return None
    other, host, pid = clock.get("session"), clock.get("host"), clock.get("pid")
    if host == socket.gethostname():
        if not C.is_clock_process(pid, project):
            return None
    else:
        ends = []
        for key, extra in (("wakes_at", PEER_GRACE), ("started", PEER_MAX)):
            try:
                ends.append(C.parse_time(clock.get(key)) + extra)
            except ValueError:
                pass
        if not ends or min(ends) <= now:
            return None
    return f"{C._taker(other)} on {host} (pid {pid})"


def pause_marker(pack: Path, reg: dict, clock: "dict | None") -> "tuple[str, str] | None":
    """(key, detail) of the pause the project is in, or None."""
    note = C.paused_note(pack, reg["since"])
    if note is not None:
        return f"note:{note.name}", f"paused by the handover note {note.name}; this session ends turns unarmed"
    if not clock or clock.get("state") != "stopped" or clock.get("stop_reason") != C.STOP_REASON:
        return None
    stopped = clock.get("stopped_at")
    try:
        t = C.parse_time(stopped)
    except ValueError:
        try:
            t = (Path(pack) / C.CLOCK).stat().st_mtime
        except OSError:
            return None
        stopped = _iso(t)
    if t <= reg["since"]:
        return None
    by = clock.get("stopped_by_session")
    return (f"stop:{stopped}", f"paused by session_clock --stop at {stopped} ({C._taker(by)}); this session ends "
                               "turns unarmed")


def block_reason(reg: dict, clock: "dict | None") -> str:
    root, project = Path(reg["solaris"]), Path(reg["dir"])
    try:
        name = str(project.relative_to(root))
    except ValueError:
        name = project.name
    after = clock.get("next_after") if clock else None
    try:
        C.parse_time(after)
    except ValueError:
        after = None
    cap = clock.get("cap", C.CAP_MINUTES) if clock else C.CAP_MINUTES
    if isinstance(cap, bool) or not isinstance(cap, (int, float)) or not 0 <= cap < float("inf"):
        cap = C.CAP_MINUTES
    cmd = C.command("--dir", project, after=after, cap=cap, root=root)
    forget = " ".join(shlex.quote(p) for p in ("uv", "run", "--directory", str(root), "-m", C.MODULE, "--forget"))
    return (f"Session guard: project {name} has no wake-up armed, so this session would sit idle until someone "
            f"writes to it. Start its clock now as a background command, alone in its own call, with no &, nohup "
            f"or disown (the harness would not track it, so it wakes nothing) and no timeout wrapper: `{cmd}`. "
            f"Starting it stops any older clock of the project. Pause only when the owner asked for it: follow the "
            f"handover skill (its note, then `--stop`). If this session no longer works on the project: `{forget}`.")


def handle(payload: dict, env=None, now: "float | None" = None) -> str:
    """What the hook prints for ``payload`` ("" for nothing)."""
    env = os.environ if env is None else env
    now = time.time() if now is None else now
    event = payload.get("hook_event_name")
    if event not in ("Stop", "StopFailure") or payload.get("agent_id"):
        return ""
    sid = payload.get("session_id") or env.get(C.SESSION_ENV)
    reg = load_registry(sid, now, env)
    if reg is None:
        return ""
    pack, project = Path(reg["pack"]), Path(reg["dir"])
    if not (pack / ".memory").is_dir():
        return ""
    spath = C.guard_state_path(sid, env)
    state = load_state(spath)
    text = payload.get("last_assistant_message")
    text = text if isinstance(text, str) else ""
    error = payload.get("error")
    if isinstance(error, dict):
        error = error.get("type") or error.get("kind") or ""
    error = str(error or "").strip().lower()
    active = payload.get("stop_hook_active")
    last_alert = state.get("alerted_at")
    recent = isinstance(last_alert, (int, float)) and 0 <= now - last_alert < REPEAT_WINDOW
    if active is False or (event == "StopFailure" and active is not True and not recent):
        state.update(blocks=0, alerted=False)   # a new turn

    refused = is_refusal(event, text, error, payload.get("transcript_path"))
    if refused:
        digest = hashlib.sha1(text.encode("utf-8", "replace")).hexdigest()[:16]
        last = state.get("refusal") if isinstance(state.get("refusal"), dict) else {}
        at = last.get("at")
        if not (last.get("hash") == digest and isinstance(at, (int, float)) and 0 <= now - at < REPEAT_WINDOW):
            state["refusal"] = {"hash": digest, "at": now}
            save_state(spath, state)
            append_alert(pack, "classifier-stop", sid, refusal_detail(event, text, error), now)
    elif event == "StopFailure" and error not in TRANSIENT:
        append_alert(pack, "api-error", sid, f"{error or 'unknown'}: {_first_line(text, 160)}", now)

    clock = C.read_clock(pack)
    if armed(payload, clock, sid, project):
        if state.get("blocks") or state.get("alerted"):
            save_state(spath, dict(state, blocks=0, alerted=False))
        return ""
    pause = pause_marker(pack, reg, clock)
    if pause is not None:
        key, detail = pause
        if state.get("paused") != key:
            if save_state(spath, dict(state, blocks=0, paused=key)):
                append_alert(pack, "paused", sid, detail, now)
        return ""
    held = holder(clock, sid, now, project)
    blocks = state.get("blocks") if isinstance(state.get("blocks"), int) else 0
    # without a saved count the cap cannot hold: then the turn ends, and is logged below
    if (event == "Stop" and not refused and not held and blocks < MAX_BLOCKS
            and save_state(spath, dict(state, blocks=blocks + 1))):
        return json.dumps({"decision": "block", "reason": block_reason(reg, clock)})
    if not state.get("alerted"):
        if event == "StopFailure":
            why = (f"turn ended by {error or 'an API error'}" + (" (a safety-classifier refusal)" if refused else "")
                   + f" with no wake-up armed: {_first_line(text, 100)}"
                   + (f"; the project's clock is held by {held}" if held else ""))
        elif refused:
            why = "turn ended by a safety-classifier refusal with no wake-up armed"
        elif held:
            why = f"turn ended with no wake-up armed in this session; the project's clock is held by {held}"
        else:
            why = (f"turn ended with no wake-up armed after {blocks} reminder{'' if blocks == 1 else 's'}"
                   + (f" (clock.json: {one_line(clock.get('state'), 20)})" if clock else " (no clock.json)"))
        save_state(spath, dict(state, alerted=True, alerted_at=now))
        append_alert(pack, "unarmed-turn-end", sid, why, now)
    return ""


def main(argv: "list[str] | None" = None) -> int:
    try:
        out = handle(read_payload())
        if out:
            sys.stdout.write(out + "\n")
            sys.stdout.flush()
    except Exception:
        pass   # fail open: a guard must never break the turn
    return 0


if __name__ == "__main__":
    sys.exit(main())
