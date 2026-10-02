# Copyright 2026 Mikhail Yurasov <me@yurasov.me>
# SPDX-License-Identifier: Apache-2.0

"""Interaction logs with one file per machine (stdlib only).

Every meaningful turn is logged as one ``{ts, project, prompt, request, outcome}`` JSON line. Each machine writes
only its own file - ``.memory/interactions/<machine>.jsonl`` for the framework master log, and
``<pack>/.memory/interactions/<machine>.jsonl`` for a project (the pack is the project's ai-pack folder, found by
solaris.tools.pack) - so a checkout synced between machines (Syncthing) never has two writers on one file, and a
log can never become a conflict copy. The single ``interactions.jsonl`` that older versions kept beside that
folder stays as read-only history and is read together with the per-machine files.

Run::

    uv run -m solaris.tools.interactions add --project <name> --prompt TEXT --request TEXT --outcome TEXT [--dir <project>]
    uv run -m solaris.tools.interactions add --stdin [--dir <project>] <<'EOF'
    {"project": "<name>", "prompt": "...", "request": "...", "outcome": "..."}
    EOF
    uv run -m solaris.tools.interactions show [--dir <project>] [--last N] [--since ISO] [--project NAME] [--machine NAME] [--json]
    uv run -m solaris.tools.interactions who [--dir <project>] [--minutes N]
    uv run -m solaris.tools.interactions machine

``add`` stamps ``ts`` from the clock (UTC, ``Z`` suffix) and appends the line to this machine's framework file and,
with ``--dir``, the identical line to the project's file (``--stdin`` reads the fields from a JSON object, which
avoids shell quoting; flags win). ``show`` merges the history file and every machine's file by ``ts`` (``--last 0``
prints everything). ``who`` lists each log file's latest entry. ``machine`` prints this machine's name:
``SOLARIS_MACHINE`` when set, else the macOS LocalHostName or the short host name, lowercased, with anything
outside ``[a-z0-9-]`` turned into ``-``.

Exit codes: 0 ok; 1 an error (no ai-pack, an unwritable log); 2 bad arguments; 3 (``who`` only) another machine
logged within ``--minutes`` (default 60), so the log's project may be in use there - as of the last sync.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import socket
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from solaris.tools import pack as P

REPO_ROOT = Path(__file__).resolve().parents[2]
FOLDER = "interactions"  # the per-machine files, inside a .memory folder
HISTORY = "interactions.jsonl"  # the single log older versions kept; read-only history now
FIELDS = ("project", "prompt", "request", "outcome")
ACTIVE_MINUTES = 60
_EPOCH = datetime.min.replace(tzinfo=timezone.utc)

_now = time.time  # tests swap the clock


def _clean(name: str) -> str:
    return re.sub(r"[^a-z0-9-]+", "-", name.strip().lower()).strip("-")


def machine_name(env: "dict | None" = None) -> str:
    """This machine's log name: SOLARIS_MACHINE, else the macOS LocalHostName, else the short host name."""
    env = os.environ if env is None else env
    name = _clean(env.get("SOLARIS_MACHINE", ""))
    if not name and sys.platform == "darwin":
        # LocalHostName is the name the owner set; the kernel host name can follow DHCP or reverse DNS.
        try:
            out = subprocess.run(["scutil", "--get", "LocalHostName"], capture_output=True, text=True, timeout=2)
            name = _clean(out.stdout) if out.returncode == 0 else ""
        except (OSError, subprocess.SubprocessError):
            name = ""
    if not name:
        name = _clean(socket.gethostname().split(".")[0])
    return name or "unknown"


def framework_memory(repo_root: "Path | None" = None) -> Path:
    return Path(repo_root or REPO_ROOT) / ".memory"


def project_memory(project_dir) -> Path:
    """The project's ``<pack>/.memory``; PackError when the folder holds no ai-pack."""
    return P.require_pack(project_dir) / ".memory"


def machine_log(memory, machine: "str | None" = None) -> Path:
    """This (or the named) machine's log file inside a ``.memory`` folder."""
    return Path(memory) / FOLDER / ((machine or machine_name()) + ".jsonl")


def log_files(memory) -> "list[tuple[str, Path]]":
    """(machine, path) for every log in a ``.memory`` folder: the history file as machine ``-``, then each
    machine's file. Syncthing conflict copies are skipped (the session-start sweep merges them)."""
    memory = Path(memory)
    out = []
    history = memory / HISTORY
    if history.is_file():
        out.append(("-", history))
    folder = memory / FOLDER
    if folder.is_dir():
        for path in sorted(folder.glob("*.jsonl")):
            if path.is_file() and ".sync-conflict-" not in path.name:
                out.append((path.stem, path))
    return out


def parse_ts(value) -> "datetime | None":
    """An aware datetime from a log ``ts`` (``Z``, an offset, or naive taken as UTC); None when unreadable."""
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        dt = datetime.fromisoformat(value.strip())
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def utc_stamp(t: "float | None" = None) -> str:
    return datetime.fromtimestamp(_now() if t is None else t, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def build_entry(project: str, prompt: str, request: str, outcome: str, t: "float | None" = None) -> dict:
    return {"ts": utc_stamp(t), "project": project, "prompt": prompt, "request": request, "outcome": outcome}


def append_line(path, entry: dict) -> None:
    """Append one JSON line in a single O_APPEND write, so local writers never interleave mid-line."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = (json.dumps(entry, ensure_ascii=True) + "\n").encode("ascii")
    fd = os.open(path, os.O_RDWR | os.O_APPEND | os.O_CREAT, 0o644)
    try:
        size = os.fstat(fd).st_size
        if size and os.pread(fd, 1, size - 1) != b"\n":
            data = b"\n" + data  # a writer died mid-line: start a fresh line instead of joining it
        view = memoryview(data)
        while view:
            view = view[os.write(fd, view):]
    finally:
        os.close(fd)


def read_entries(memory) -> "tuple[list[dict], int]":
    """Every entry in a ``.memory`` folder's logs, oldest first, each with a ``machine`` key, plus the number of
    lines that were not JSON objects. Entries without a readable ``ts`` sort first, in file order."""
    rows = []
    bad = 0
    for machine, path in log_files(memory):
        try:
            blob = path.read_bytes()
        except OSError:
            continue
        # Split on b"\n" only: str.splitlines also breaks at U+2028/U+2029/U+0085 inside JSON strings.
        for line in blob.split(b"\n"):
            if not line.strip():
                continue
            try:
                entry = json.loads(line)
            except ValueError:
                entry = None
            if not isinstance(entry, dict):
                bad += 1
                continue
            entry["machine"] = machine
            rows.append((parse_ts(entry.get("ts")) or _EPOCH, len(rows), entry))
    rows.sort(key=lambda r: (r[0], r[1]))
    return [r[2] for r in rows], bad


def last_entry(path) -> "dict | None":
    """The last JSON object in a log file, reading only its tail unless the tail holds none."""
    try:
        with open(path, "rb") as fh:
            size = fh.seek(0, os.SEEK_END)
            for chunk in (65536, None):
                start = size - chunk if chunk and size > chunk else 0
                fh.seek(start)
                lines = fh.read().split(b"\n")
                for line in reversed(lines[1:] if start else lines):  # a tail's first line may be cut
                    try:
                        entry = json.loads(line)
                    except ValueError:
                        continue
                    if isinstance(entry, dict):
                        return entry
                if not start:
                    break
    except OSError:
        pass
    return None


def activity(memory) -> "list[dict]":
    """Each log file's latest entry, newest first: [{machine, path, ts, project}] (ts falls back to the mtime)."""
    out = []
    for machine, path in log_files(memory):
        entry = last_entry(path) or {}
        ts = parse_ts(entry.get("ts"))
        if ts is None:
            try:
                ts = datetime.fromtimestamp(path.stat().st_mtime, timezone.utc)
            except OSError:
                ts = None
        out.append({"machine": machine, "path": path, "ts": ts, "project": entry.get("project")})
    out.sort(key=lambda r: r["ts"] or _EPOCH, reverse=True)
    return out


def _age(ts: "datetime | None", now: float) -> str:
    if ts is None:
        return "unknown"
    s = max(0, int(now - ts.timestamp()))
    if s < 3600:
        return f"{s // 60} min ago"
    if s < 2 * 86400:
        return f"{s // 3600} h ago"
    return f"{s // 86400} d ago"


def _rel(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(REPO_ROOT.resolve()))
    except ValueError:
        return str(path)


def _brief(entry: dict) -> str:
    text = entry.get("request") or ("prompt: " + str(entry.get("prompt") or ""))
    text = " ".join(str(text).split())
    if len(text) > 160:
        text = text[:157] + "..."
    project = entry.get("project") or entry.get("cwd") or "-"
    return f"{entry.get('ts', '-')}  {entry['machine']:<10} [{project}] {text}"


def _usage(msg: str) -> int:
    print(f"interactions: {msg}", file=sys.stderr)
    return 2


def _memory_for(a) -> Path:
    return project_memory(a.dir) if a.dir else framework_memory()


def cmd_add(a) -> int:
    fields = {}
    if a.stdin:
        if sys.stdin is None or sys.stdin.isatty():
            return _usage("--stdin needs a JSON object piped in (a heredoc works)")
        try:
            data = json.loads(sys.stdin.read() or "{}")
        except ValueError as e:
            return _usage(f"--stdin is not JSON ({e})")
        if not isinstance(data, dict):
            return _usage("--stdin needs a JSON object")
        fields.update({k: data[k] for k in FIELDS if k in data})
    for k in FIELDS:
        if getattr(a, k) is not None:
            fields[k] = getattr(a, k)
    missing = [k for k in FIELDS if not isinstance(fields.get(k), str)]
    if missing:
        return _usage("missing " + ", ".join("--" + k for k in missing))
    empty = [k for k in ("project", "request", "outcome") if not fields[k].strip()]
    if empty:
        return _usage("empty " + ", ".join("--" + k for k in empty))
    machine = machine_name()
    targets = [machine_log(framework_memory(), machine)]
    if a.dir:
        targets.append(machine_log(project_memory(a.dir), machine))
    entry = build_entry(**fields)
    for path in targets:
        try:
            append_line(path, entry)
        except OSError as e:
            print(f"interactions: cannot write {path}: {e}", file=sys.stderr)
            return 1
    print(f"logged {entry['ts']} ({machine}) -> " + ", ".join(_rel(p) for p in targets))
    return 0


def cmd_show(a) -> int:
    entries, bad = read_entries(_memory_for(a))
    if a.since:
        since = parse_ts(a.since)
        if since is None:
            return _usage(f"--since is not an ISO time: {a.since}")
        entries = [e for e in entries if (parse_ts(e.get("ts")) or _EPOCH) >= since]
    if a.project:
        entries = [e for e in entries if e.get("project") == a.project]
    if a.machine:
        entries = [e for e in entries if e.get("machine") == a.machine]
    if a.last > 0:
        entries = entries[-a.last:]
    for e in entries:
        print(json.dumps(e, ensure_ascii=False) if a.json else _brief(e))
    if bad:
        print(f"interactions: skipped {bad} line(s) that are not JSON objects", file=sys.stderr)
    return 0


def cmd_who(a) -> int:
    me = machine_name()
    now = _now()
    rows = activity(_memory_for(a))
    if not rows:
        print("no interaction log yet")
        return 0
    busy = []
    for r in rows:
        if r["machine"] == "-":
            label = "history file"
        else:
            label = r["machine"] + (" (this machine)" if r["machine"] == me else "")
        stamp = r["ts"].strftime("%Y-%m-%dT%H:%M:%SZ") if r["ts"] else "-"
        project = f"  [{r['project']}]" if r["project"] else ""
        print(f"{label:<24} {stamp}  {_age(r['ts'], now)}{project}")
        if r["machine"] not in ("-", me) and r["ts"] and now - r["ts"].timestamp() <= a.minutes * 60:
            busy.append(r)
    scope = "this project" if a.dir else "this checkout"
    for r in busy:
        print(f"{r['machine']} logged {_age(r['ts'], now)}: {scope} may be in use there (as of the last sync)")
    return 3 if busy else 0


def cmd_machine(a) -> int:
    print(machine_name())
    return 0


def main(argv: "list[str] | None" = None) -> int:
    parser = argparse.ArgumentParser(prog="solaris.tools.interactions", description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="cmd", required=True)
    add = sub.add_parser("add", help="log one turn in this machine's files")
    for k in FIELDS:
        add.add_argument("--" + k)
    add.add_argument("--stdin", action="store_true", help="read the fields from a JSON object on stdin")
    add.add_argument("--dir", help="project folder: also log the line in its ai-pack")
    show = sub.add_parser("show", help="print the merged log, oldest first")
    show.add_argument("--dir", help="project folder (default: the framework log)")
    show.add_argument("--last", type=int, default=20, help="newest N entries (0: all; default 20)")
    show.add_argument("--since", help="only entries at or after this ISO time")
    show.add_argument("--project", help="only entries for this project name")
    show.add_argument("--machine", help="only entries from this machine (- for the history file)")
    show.add_argument("--json", action="store_true", help="full JSON lines (with a machine key)")
    who = sub.add_parser("who", help="each log file's latest entry; exit 3 if another machine is active")
    who.add_argument("--dir", help="project folder (default: the framework log)")
    who.add_argument("--minutes", type=int, default=ACTIVE_MINUTES, help="activity window (default 60)")
    sub.add_parser("machine", help="print this machine's log name")
    a = parser.parse_args(argv)
    try:
        return {"add": cmd_add, "show": cmd_show, "who": cmd_who, "machine": cmd_machine}[a.cmd](a)
    except P.PackError as e:
        print(f"interactions: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
