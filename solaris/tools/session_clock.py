# Copyright 2026 Mikhail Yurasov <me@yurasov.me>
# SPDX-License-Identifier: Apache-2.0

"""Wake clock for agent sessions: sleep until the next scheduled event, print it and exit (stdlib only).

Session crons may not fire in some hosted harnesses, while a finished background command always wakes the
session: an agent starts this tool as a background command and takes its exit as the wake-up. It is one-shot and
never detaches. Start it as the harness's own tracked background command (in Claude Code, the Bash tool's
background option), alone in its call: never with ``&``, ``nohup`` or ``disown`` (the harness no longer tracks it,
so its exit wakes nothing) and never inside ``timeout`` (which kills it early and reports a false failure). The
schedule is a JSON list of events, by default ``<pack>/.memory/schedule.json`` (the pack is the project's ai-pack
folder, found by solaris.tools.pack):

- ``{"name": "kaggle-reset", "at": "2026-10-01T17:00:00-07:00"}`` fires once (the UTC offset is required);
- ``{"name": "hourly", "every": 60}`` fires every 60 minutes, counted from 00:00 UTC (so on the hour);
- ``{"name": "morning", "every": 1440, "at": "2026-10-02T08:57:00-07:00"}`` fires every 1440 minutes from ``at``.

Other keys (a note for the agent, say) are ignored, and the file is read once per start. No sleep runs past
``--cap`` minutes (default 25, below the 30 minutes after which Claude Code stops a background command in
unattended runs, ``-p`` and the SDK): at the cap the tool prints a ``re-arm`` line and exits, and the agent starts
it again. Where background commands have no time limit (Claude Code's interactive sessions: terminal, IDE
extension, desktop; since 2.1.288) a longer cap saves wake-ups: ``--cap 55`` still wakes the session at least
every 55 minutes, which keeps a one-hour prompt cache warm; ``--cap 0`` means no cap (one wake-up per event).
Keep the default in unattended runs and in any harness whose limit you have not checked.
Start every clock with the ``--after`` the previous one printed, so an event that came due while the agent was
busy fires at once instead of being skipped; a recurring event fires once for all the ticks it missed.

With ``--dir``, the clock keeps its state in ``<pack>/.memory/clock.json`` (replaced in one step): ``schema``,
``session`` (``CLAUDE_CODE_SESSION_ID`` or null), ``pid``, ``host``, ``started``, ``wakes_at`` (the earlier of the
next event and the cap), ``state`` (``armed`` while it sleeps, then ``fired``, ``re-arm`` or ``stopped``),
``next_after`` (the ``--after`` of the next start) and ``cap``. One clock runs per project: a start stops an older
clock of the same project on this machine and says so in one line. It signals the pid in clock.json only while
clock.json is ``armed`` and that process is a session_clock of this user whose ``--dir`` names this project. A clock
stopped that way says who took over; when another session did, it must not be re-armed while that session holds
the clock. ``--stop`` stops the project's clock and marks clock.json ``stopped``, which the session guard reads as a
pause until a clock starts again; ``--status`` prints clock.json and whether its process is alive; ``--check``
prints the next event and the start command without sleeping and without touching the project's clock (use it
to test a schedule from any session other than the one that runs the project). In a Claude Code session
(``CLAUDE_CODE_SESSION_ID`` set) each ``--dir`` start also records the session's project in
``${XDG_CACHE_HOME:-~/.cache}/solaris/sessions/<session id>.json`` (folders ``0700``), which the session guard hook
(solaris.tools.session_guard) and read_first's project mode read; registry files older than 14 days are deleted.
``--forget`` deletes this session's registry file and guard state, so a session that no longer works on the
project (or ran a clock by mistake) leaves project mode at once; it leaves the clock alone.

Run::

    uv run -m solaris.tools.session_clock --dir projects/<slug>
    uv run -m solaris.tools.session_clock --dir projects/<slug> --after 2026-10-01T17:00:00-07:00 --cap 55
    uv run -m solaris.tools.session_clock --schedule <file> --after 2026-10-01T17:00:00-07:00 --cap 25
    uv run -m solaris.tools.session_clock --dir projects/<slug> --check
    uv run -m solaris.tools.session_clock --dir projects/<slug> --status
    uv run -m solaris.tools.session_clock --dir projects/<slug> --stop
    uv run -m solaris.tools.session_clock --forget

Prints ``due <name> at <time>`` for each event due (every one at the earliest due time, or every one missed since
``--after``) or a ``re-arm`` line, and then ``next: <command>``: the exact command that starts the next clock.
Exit codes: 0 an event is due, the clock needs re-arming, or it was stopped (``--check``: an event is left;
``--stop`` and ``--forget``: done or nothing to do); 1 a missing or bad schedule or project, no event left to wait
for, a clock that runs on another machine (``--stop``), or a session registered for another project
(``--forget --dir``); 2 bad arguments. ``--status`` exits 0 when the clock is armed and its process alive, 3
otherwise.
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import math
import os
import re
import shlex
import signal
import socket
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

from solaris.tools import fileio
from solaris.tools import pack as P

SCHEDULE = Path(".memory") / "schedule.json"   # inside the pack folder
CLOCK = Path(".memory") / "clock.json"
CAP_MINUTES = 25   # below Claude Code's 30-minute limit on a background command in unattended runs
MAX_EVERY = 366 * 24 * 60   # minutes
NAP = 30.0   # seconds per sleep: the wall clock is read between naps, so a machine that sleeps still wakes on time
MODULE = "solaris.tools.session_clock"
SOLARIS_ROOT = Path(__file__).resolve().parents[2]
SESSION_ENV = "CLAUDE_CODE_SESSION_ID"
REGISTRY_DAYS = 14
GUARD_SUFFIX = ".guard.json"   # the session guard's count file, beside the registry file
STOP_REASON = "stopped by --stop"
STOP_WAIT = 5.0   # seconds an older clock gets to exit after SIGTERM
LOCK_WAIT = 10.0

_now = time.time   # tests swap the clock
_sleep = time.sleep


class ScheduleError(Exception):
    """The schedule is missing or invalid (the message names the file and the problem)."""


class Stopped(Exception):
    """A signal asked the clock to stop."""


def parse_time(value) -> int:
    """Epoch seconds of an ISO time with a UTC offset; ValueError for anything else."""
    if not isinstance(value, str):
        raise ValueError(value)
    dt = datetime.fromisoformat(value.strip())
    if dt.tzinfo is None:
        raise ValueError(value)
    return math.floor(dt.timestamp())   # whole seconds keep every tick exact


def parse_after(text: str) -> int:
    """Epoch seconds from ``--after``: epoch seconds, or an ISO time with a UTC offset; ValueError otherwise."""
    try:
        t = math.floor(float(text))
    except ValueError:
        t = parse_time(text)
    except OverflowError:   # inf
        raise ValueError(text) from None
    try:
        iso(t)
    except (ValueError, OverflowError, OSError):   # outside the years a clock can show
        raise ValueError(text) from None
    return t


def iso(t: float) -> str:
    """``t`` as an ISO time in the machine's zone, with its offset."""
    return datetime.fromtimestamp(t, timezone.utc).astimezone().isoformat()


def load(path: Path) -> list:
    """[(name, at, every)] from a schedule file: ``at`` in epoch seconds or None, ``every`` in seconds or None.
    ScheduleError with a clean message when the file is missing or invalid."""
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        raise ScheduleError(f"{path} not found: write a JSON list of events there (see --help)") from None
    except (OSError, UnicodeDecodeError) as exc:
        raise ScheduleError(f"{path}: cannot read it ({exc})") from None
    try:
        data = json.loads(text)
    except ValueError as exc:
        raise ScheduleError(f"{path}: not valid JSON ({exc})") from None
    if not isinstance(data, list):
        raise ScheduleError(f"{path}: must hold a JSON list of events, not a {type(data).__name__}")
    events = []
    for i, ev in enumerate(data, 1):
        where = f"{path}: event {i}"
        if not isinstance(ev, dict):
            raise ScheduleError(f"{where} is not an object")
        name = ev.get("name")
        if not isinstance(name, str) or not name.strip():
            raise ScheduleError(f"{where} needs a name (a non-empty string)")
        where += f" ({name.strip()})"
        at = every = None
        if "at" in ev:
            try:
                at = parse_time(ev["at"])
            except ValueError:
                raise ScheduleError(f"{where}: at must be an ISO time with a UTC offset, such as "
                                    f"2026-10-01T17:00:00-07:00, not {ev['at']!r}") from None
        if "every" in ev:
            n = ev["every"]
            whole = isinstance(n, int) and not isinstance(n, bool) or isinstance(n, float) and n.is_integer()
            if not whole or not 1 <= n <= MAX_EVERY:
                raise ScheduleError(f"{where}: every must be a whole number of minutes from 1 to {MAX_EVERY} "
                                    f"(a year), not {n!r}")
            every = int(n) * 60
        if at is None and every is None:
            raise ScheduleError(f"{where} needs at (a time) or every (minutes)")
        events.append((name.strip(), at, every))
    return events


def occurrence(at: "int | None", every: "int | None", after: float, now: float) -> "int | None":
    """When an event fires next: its first time after ``after``, or for a recurring event that has ticked since
    ``after``, its latest tick by ``now`` (missed ticks fire once). None for a one-off event that is past."""
    if every is None:
        return at if at > after else None
    base = at or 0   # no at: ticks count from 00:00 UTC
    first = base if after < base else base + (math.floor((after - base) / every) + 1) * every
    if first > now:
        return first
    return base + math.floor((now - base) / every) * every


def due(events: list, after: float, now: float) -> "list[tuple[int, str]]":
    """(time, name) of what fires next, earliest first: every event already due by ``now``, else every event at
    the earliest upcoming time. Empty when nothing is left."""
    upcoming = sorted((t, name) for name, at, every in events
                      if (t := occurrence(at, every, after, now)) is not None)
    if not upcoming:
        return []
    late = [(t, name) for t, name in upcoming if t <= now]
    return late or [(t, name) for t, name in upcoming if t == upcoming[0][0]]


def _span(seconds: float) -> str:
    minutes = max(0, math.ceil(seconds / 60))
    return f"{minutes // 60}h{minutes % 60:02d}m" if minutes >= 60 else f"{minutes}m"


def run(events: list, after: float, cap: float) -> "tuple[str, list]":
    """Sleep until something is due or ``cap`` seconds pass (0: no cap): ("due", [(time, name)...]) or
    ("re-arm", [the next (time, name)])."""
    started = _now()
    while True:
        now = _now()
        fire = due(events, after, now)
        if fire[0][0] <= now:
            return "due", fire
        if cap and now >= started + cap:
            return "re-arm", fire[:1]
        _sleep(min(fire[0][0] - now, started + cap - now, NAP) if cap else min(fire[0][0] - now, NAP))


def command(flag: str, target, after: "str | None" = None, cap: float = CAP_MINUTES,
            root=SOLARIS_ROOT) -> str:
    """The exact shell command that starts a clock: ``flag`` is ``--dir`` or ``--schedule``."""
    parts = ["uv", "run", "--directory", str(root), "-m", MODULE, flag, str(target)]
    if after:
        parts += ["--after", after]
    if cap != CAP_MINUTES:
        parts += ["--cap", f"{cap:g}"]
    return " ".join(shlex.quote(p) for p in parts)


def cache_dir(env=None) -> Path:
    """``${XDG_CACHE_HOME:-~/.cache}/solaris`` (raises when there is no home folder to fall back on)."""
    env = os.environ if env is None else env
    base = env.get("XDG_CACHE_HOME") or ""
    root = Path(base) if base and os.path.isabs(base) else Path(env.get("HOME") or Path.home()) / ".cache"
    return root / "solaris"


def valid_session(sid) -> bool:
    return isinstance(sid, str) and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", sid) is not None


def registry_path(sid, env=None) -> "Path | None":
    """The session's registry file, or None when ``sid`` is no usable session id."""
    return cache_dir(env) / "sessions" / f"{sid}.json" if valid_session(sid) else None


def guard_state_path(sid, env=None) -> "Path | None":
    """The session guard's count file beside the registry, or None."""
    reg = registry_path(sid, env)
    return reg.with_name(f"{sid}{GUARD_SUFFIX}") if reg else None


def private_dirs(folder: Path) -> None:
    """Create ``folder`` and each missing parent with mode 0700."""
    missing, p = [], Path(folder)
    while not p.exists():
        missing.append(p)
        if p.parent == p:
            break
        p = p.parent
    for d in reversed(missing):
        try:
            d.mkdir(mode=0o700)
        except FileExistsError:
            continue
        os.chmod(d, 0o700)


def prune_registry(folder: Path, now: float) -> None:
    """Delete the files in the registry folder last written over REGISTRY_DAYS ago."""
    for f in folder.iterdir():
        try:
            if f.is_file() and now - f.stat().st_mtime > REGISTRY_DAYS * 86400:
                f.unlink()
        except OSError:
            pass


def write_registry(sid, project: Path, pack: Path, now: float, env=None) -> None:
    """Record that session ``sid`` runs ``project`` (Interface 4); best effort, never fails the clock."""
    try:
        path = registry_path(sid, env)
        if path is None:
            return
        data = {"schema": 1, "session": sid, "solaris": str(SOLARIS_ROOT), "dir": str(project), "pack": str(pack),
                "updated": iso(math.floor(now))}
        private_dirs(path.parent)
        prune_registry(path.parent, time.time())
        fileio.write_text_atomic(path, json.dumps(data) + "\n")
    except Exception:
        pass


def paused_note(pack: Path, since: "float | None" = None) -> "Path | None":
    """The pause marker, read the way read_first reads it: the newest ``handover-*.md`` by the timestamp in its file
    name (Syncthing conflict copies skipped), when it has no "resumed at" line (any case) and was written after
    ``since`` (the registry's ``updated``, when known). None when the project is not paused."""
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


def read_clock(pack: Path) -> "dict | None":
    try:
        data = json.loads((Path(pack) / CLOCK).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def write_clock(pack: Path, data: dict) -> bool:
    try:
        fileio.write_text_atomic(Path(pack) / CLOCK, json.dumps(data) + "\n")
        return True
    except OSError:
        return False


def _pid(value) -> "int | None":
    return value if isinstance(value, int) and not isinstance(value, bool) and value > 1 else None


def _process(pid: int) -> "tuple[int, list, str | None] | None":
    """(uid, arguments, working folder or None) of a live process, or None."""
    proc = Path(f"/proc/{pid}")
    if proc.is_dir():
        try:
            argv = (proc / "cmdline").read_bytes().decode("utf-8", "replace").split("\0")
            uid = proc.stat().st_uid
        except OSError:
            return None
        try:
            cwd = os.readlink(proc / "cwd")
        except OSError:
            cwd = None
        return uid, [a for a in argv if a], cwd
    try:
        out = subprocess.run(["ps", "-ww", "-o", "uid=", "-o", "args=", "-p", str(pid)], capture_output=True,
                             text=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        return None
    fields = out.stdout.strip().split(None, 1)
    if out.returncode != 0 or len(fields) != 2 or not fields[0].isdigit():
        return None
    return int(fields[0]), fields[1].split(), None


def _runs_project(argv: list, cwd: "str | None", project) -> bool:
    """True when the arguments' ``--dir`` names ``project`` (a relative one is read from the process's folder when
    known, and from the Solaris root, where the clock commands run)."""
    try:
        project = Path(project).resolve()
    except (OSError, RuntimeError, ValueError):
        return False
    for i, tok in enumerate(argv):
        value = argv[i + 1] if tok == "--dir" and i + 1 < len(argv) else tok[6:] if tok.startswith("--dir=") else ""
        if not value:
            continue
        p = Path(os.path.expanduser(value))
        for cand in [p] if p.is_absolute() else [Path(base) / p for base in (cwd, SOLARIS_ROOT) if base]:
            try:
                if cand.resolve() == project:
                    return True
            except (OSError, RuntimeError, ValueError):
                continue
    return False


def is_clock_process(pid, project=None) -> bool:
    """True when ``pid`` is a live session_clock run by this user on this machine (not a one-shot --stop, --status,
    --check or --forget), and, with ``project``, one whose ``--dir`` names that project."""
    pid = _pid(pid)
    if pid is None or pid == os.getpid():
        return False
    try:
        os.kill(pid, 0)
    except OSError:   # gone, or another user's
        return False
    found = _process(pid)
    if found is None:
        return False
    uid, argv, cwd = found
    if uid != os.getuid() or not any(MODULE in a for a in argv):
        return False
    if any(a in ("--stop", "--status", "--check", "--forget") for a in argv):
        return False
    return project is None or _runs_project(argv, cwd, project)


def stop_process(pid: int, project=None) -> bool:
    """SIGTERM a clock and wait for it to go (SIGKILL after STOP_WAIT seconds); True when it is gone."""
    for sig, wait in ((signal.SIGTERM, STOP_WAIT), (signal.SIGKILL, 1.0)):
        try:
            os.kill(pid, sig)
        except ProcessLookupError:
            return True
        except OSError:
            return False
        deadline = time.monotonic() + wait
        while time.monotonic() < deadline:
            if not is_clock_process(pid, project):
                return True
            time.sleep(0.05)
    return not is_clock_process(pid, project)


@contextlib.contextmanager
def pack_lock(pack: Path):
    """Hold this machine's lock for the project's clock (best effort: no lock when it cannot be had)."""
    fh = None
    try:
        import fcntl
        folder = cache_dir() / "locks"
        private_dirs(folder)
        fh = open(folder / f"clock-{hashlib.sha1(str(pack).encode()).hexdigest()[:16]}.lock", "a")
        deadline = time.monotonic() + LOCK_WAIT
        while True:
            try:
                fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() > deadline:
                    break
                time.sleep(0.05)
    except Exception:
        pass
    try:
        yield
    finally:
        if fh is not None:
            fh.close()


def _taker(sid) -> str:
    return f"session {sid}" if sid else "a session without an id"


def _replace_older(project: Path, pack: Path, me: int, sid: "str | None") -> "str | None":
    """Stop the project's older armed clock on this machine; the line to print, or None."""
    old = read_clock(pack)
    pid = _pid(old.get("pid")) if old else None
    if pid is None or pid in (me, os.getppid()) or old.get("state") != "armed":
        return None
    if old.get("host") != socket.gethostname():
        return (f"session_clock: clock.json names a clock on {old.get('host')} (pid {pid}); stop it there if it "
                "still runs")
    if not is_clock_process(pid, project):
        return None
    same = bool(sid) and sid == old.get("session")
    reason = (f"replaced by a newer clock of this session (pid {me})" if same
              else f"taken over by a clock of {_taker(sid)} (pid {me})")
    write_clock(pack, dict(old, state="stopped", stop_reason=reason, stopped_by_session=sid, taken_over=not same))
    gone = stop_process(pid, project)
    holder = "" if same or not old.get("session") else f", {_taker(old.get('session'))}"
    return (f"session_clock: stopped the older clock of this project (pid {pid}{holder})"
            + ("" if gone else "; it has not exited yet"))


def _finish(pack: "Path | None", me: int, **fields) -> None:
    """Update clock.json at exit, unless another clock owns it now."""
    if pack is None:
        return
    data = read_clock(pack)
    if data is not None and data.get("pid") == me:
        write_clock(pack, dict(data, **fields))


def _on_signal(signum, frame):
    raise Stopped(signum)


@contextlib.contextmanager
def _stoppable():
    """Turn SIGTERM and SIGHUP into Stopped while the clock runs."""
    old = {}
    for sig in (signal.SIGTERM, signal.SIGHUP):
        try:
            old[sig] = signal.signal(sig, _on_signal)
        except (ValueError, OSError):   # not the main thread
            pass
    try:
        yield
    finally:
        for sig, handler in old.items():
            try:
                signal.signal(sig, handler)
            except (ValueError, OSError):
                pass


def _project(dir_arg: str) -> "tuple[Path, Path]":
    if not Path(dir_arg).is_dir():
        raise ScheduleError(f"{dir_arg} not found: --dir must be a project root (embedded mode: the repo root)")
    project = Path(dir_arg).resolve()
    return project, P.require_pack(project)


def stop(project: Path, pack: Path, sid: "str | None") -> int:
    """``--stop``: stop the project's armed clock and mark clock.json stopped, which the session guard reads as a
    pause until a clock starts again."""
    data = read_clock(pack) or {}
    pid, host, state = _pid(data.get("pid")), data.get("host"), data.get("state")
    here = host == socket.gethostname()
    if data and not here and state == "armed":
        print(f"session_clock: the clock runs on {host} (pid {pid}): stop it there")
        return 1
    live = here and state == "armed" and is_clock_process(pid, project)
    marked = dict(data or {"schema": 1, "session": None, "host": socket.gethostname()}, state="stopped",
                  stop_reason=STOP_REASON, stopped_at=iso(math.floor(_now())), stopped_by_session=sid)
    marked.pop("taken_over", None)
    write_clock(pack, marked)
    pause = "; the session guard counts the project as paused until a clock starts again"
    if not live:
        print(f"session_clock: no armed clock for this project here; clock.json marked stopped{pause}")
    elif stop_process(pid, project):
        print(f"session_clock: stopped the clock of this project (pid {pid}){pause}")
    else:
        print(f"session_clock: asked the clock (pid {pid}) to stop; it has not exited yet{pause}")
    return 0


def status(project: Path, pack: Path) -> int:
    """``--status``: print clock.json and whether its process is alive; 0 armed and alive, 3 otherwise."""
    data = read_clock(pack)
    if data is None:
        print(f"session_clock: no readable clock.json in {pack / '.memory'}: no clock started for this project")
        return 3
    print(json.dumps(data, ensure_ascii=False))
    pid, host, state = data.get("pid"), data.get("host"), data.get("state")
    if host != socket.gethostname():
        alive, where = False, f"pid {pid} runs on {host}: cannot check it here"
    else:
        alive = is_clock_process(pid, project)
        where = f"pid {pid} " + ("alive" if alive else "not running")
    armed = state == "armed" and alive
    print(f"session_clock: {state}, {where}" + (f", wakes at {data.get('wakes_at')}" if armed else ""))
    return 0 if armed else 3


def forget(project: "Path | None") -> int:
    """``--forget``: delete this session's registry and guard state, which ends its project mode."""
    sid = os.environ.get(SESSION_ENV) or None
    if not valid_session(sid):
        print("session_clock: no usable CLAUDE_CODE_SESSION_ID here: no session to forget")
        return 0
    try:
        path = registry_path(sid)
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            runs = data.get("dir") if isinstance(data, dict) else None
        except (OSError, ValueError):
            runs = None
        if project is not None and runs and Path(runs) != project:
            print(f"session_clock: session {sid} runs {runs}, not {project}: nothing forgotten")
            return 1
        removed = []
        for f in (path, guard_state_path(sid)):
            try:
                f.unlink()
                removed.append(f.name)
            except FileNotFoundError:
                pass
    except Exception as exc:
        print(f"session_clock: cannot forget session {sid}: {exc}")
        return 1
    if not removed:
        print(f"session_clock: session {sid} is not registered: nothing to forget")
    else:
        print(f"session_clock: session {sid} no longer runs " + (runs or "a project") + ": its registry and guard "
              "state are deleted (the project's clock is untouched; --stop stops it)")
    return 0


def main(argv: "list[str] | None" = None) -> int:
    parser = argparse.ArgumentParser(prog="solaris.tools.session_clock", description=__doc__.splitlines()[0])
    where = parser.add_mutually_exclusive_group()
    where.add_argument("--dir", help="project root: read <pack>/.memory/schedule.json")
    where.add_argument("--schedule", type=Path, help="schedule file to read instead")
    action = parser.add_mutually_exclusive_group()
    action.add_argument("--stop", action="store_true",
                        help="stop the project's clock (needs --dir); the session guard counts the project as "
                             "paused until a clock starts again")
    action.add_argument("--status", action="store_true",
                        help="print clock.json and whether its process is alive (needs --dir); exit 0 armed and "
                             "alive, 3 otherwise")
    action.add_argument("--check", action="store_true",
                        help="print the next event and the command that starts the clock, then exit: no sleep, "
                             "and the project's clock, clock.json and the session registry stay as they are")
    action.add_argument("--forget", action="store_true",
                        help="delete this session's registry entry and guard state (CLAUDE_CODE_SESSION_ID): the "
                             "session leaves project mode; the clock is untouched")
    parser.add_argument("--after", metavar="TIME",
                        help="due time of the last event handled (ISO with offset, or epoch seconds); default: now")
    parser.add_argument("--cap", type=float, default=CAP_MINUTES, metavar="MINUTES",
                        help=f"longest sleep before a re-arm (default {CAP_MINUTES}, below the harness's limit "
                             "on background commands); 0: no cap, for harnesses whose background commands have "
                             "no time limit (Claude Code's interactive sessions)")
    args = parser.parse_args(argv)
    if args.forget and args.schedule is not None:
        parser.error("--forget takes no --schedule (an optional --dir must name the session's project)")
    if not args.forget and args.dir is None and args.schedule is None:
        parser.error("one of the arguments --dir --schedule is required")
    if (args.stop or args.status) and args.dir is None:
        parser.error("--stop and --status need --dir (the project root)")
    if not math.isfinite(args.cap) or args.cap < 0:
        parser.error(f"--cap must be 0 (no cap) or a positive number of minutes, not {args.cap}")
    after = math.floor(_now())
    if args.after is not None:
        try:
            after = parse_after(args.after)
        except ValueError:
            parser.error(f"--after {args.after!r} is neither an ISO time with a UTC offset nor epoch seconds")
    sid = os.environ.get(SESSION_ENV) or None
    if args.forget:
        return forget(Path(args.dir).resolve() if args.dir else None)
    path, project, pack = args.schedule, None, None
    try:
        if path is None:
            project, pack = _project(args.dir)
            if args.stop or args.status:
                with pack_lock(pack):
                    return stop(project, pack, sid) if args.stop else status(project, pack)
            path = pack / SCHEDULE
        events = load(path)
    except (ScheduleError, P.PackError) as exc:
        print(f"session_clock: {exc}")
        return 1
    start = _now()
    if not due(events, after, start):
        print(f"session_clock: no event left to wait for in {path} after {iso(after)} (every one-off event is "
              "past and none recurs)")
        return 1

    def next_line(t: float) -> str:
        target = ("--dir", project) if project is not None else ("--schedule", Path(path).resolve())
        return f"session_clock: next: {command(*target, after=iso(t), cap=args.cap)}"

    if args.check:
        for t, name in due(events, after, start):
            print(f"session_clock: check: {name} " + (f"is due now ({iso(t)})" if t <= start else
                                                      f"at {iso(t)} (in {_span(t - start)})"))
        print(next_line(after))
        return 0
    me, cap = os.getpid(), args.cap * 60
    with _stoppable():
        try:
            if pack is not None:
                first = due(events, after, start)[0][0]
                wakes = max(start, first if not cap else min(first, start + cap))
                with pack_lock(pack):
                    note = _replace_older(project, pack, me, sid)
                    write_clock(pack, {"schema": 1, "session": sid, "pid": me, "host": socket.gethostname(),
                                       "started": iso(math.floor(start)), "wakes_at": iso(math.floor(wakes)),
                                       "state": "armed", "next_after": iso(after),
                                       "cap": int(args.cap) if args.cap.is_integer() else args.cap})
                if note:
                    print(note, flush=True)
                if sid:
                    write_registry(sid, project, pack, start)
            kind, fire = run(events, after, cap)
        except Stopped:
            data = read_clock(pack) if pack is not None else None
            reason = data.get("stop_reason") if data and data.get("pid") == me else None
            if reason and data.get("taken_over"):
                print(f"session_clock: {reason}; do not re-arm while it holds the clock (check with --status); "
                      "re-arm with the next line only once it has stopped")
                print(next_line(after))
                return 0
            if reason:
                print(f"session_clock: {reason}; nothing to re-arm")
                return 0
            _finish(pack, me, state="stopped")
            print("session_clock: stopped by a signal (a kill, a timeout or the harness); re-arm with the next "
                  "line if this project still needs its clock")
            print(next_line(after))
            return 0
    if kind == "re-arm":
        t, name = fire[0]
        _finish(pack, me, state="re-arm", next_after=iso(after))
        print(f"session_clock: re-arm: {args.cap:g}-minute cap reached; next is {name} at {iso(t)} (in "
              f"{_span(t - _now())})")
        print(next_line(after))
        return 0
    _finish(pack, me, state="fired", next_after=iso(fire[-1][0]))
    for t, name in fire:
        print(f"session_clock: due {name} at {iso(t)}")
    print(next_line(fire[-1][0]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
