# Copyright 2026 Mikhail Yurasov <me@yurasov.me>
# SPDX-License-Identifier: Apache-2.0

"""Wake clock for agent sessions: sleep until the next scheduled event, print it and exit (stdlib only).

Session crons may not fire in some hosted harnesses, while a finished background command always wakes the
session: an agent starts this tool as a background command and takes its exit as the wake-up. It is one-shot and
never detaches. The schedule is a JSON list of events, by default ``<pack>/.memory/schedule.json`` (the pack is
the project's ai-pack folder, found by solaris.tools.pack):

- ``{"name": "kaggle-reset", "at": "2026-10-01T17:00:00-07:00"}`` fires once (the UTC offset is required);
- ``{"name": "hourly", "every": 60}`` fires every 60 minutes, counted from 00:00 UTC (so on the hour);
- ``{"name": "morning", "every": 1440, "at": "2026-10-02T08:57:00-07:00"}`` fires every 1440 minutes from ``at``.

Other keys (a note for the agent, say) are ignored, and the file is read once per start. No sleep runs past
``--cap`` minutes (default 25, below the 30 minutes after which Claude Code stops a background command): at the cap
the tool prints a ``re-arm`` line and exits, and the agent starts it again. Start every clock with the ``--after``
the previous one printed, so an event that came due while the agent was busy fires at once instead of being
skipped; a recurring event fires once for all the ticks it missed.

Run::

    uv run -m solaris.tools.session_clock --dir projects/<slug>
    uv run -m solaris.tools.session_clock --schedule <file> --after 2026-10-01T17:00:00-07:00 --cap 25

Prints ``due <name> at <time>`` for each event due (every one at the earliest due time, or every one missed since
``--after``) and then ``next: --after <time>``; or a single ``re-arm`` line. Exit codes: 0 an event is due or the
clock needs re-arming; 1 a missing or bad schedule, or no event left to wait for; 2 bad arguments.
"""

from __future__ import annotations

import argparse
import json
import math
import time
from datetime import datetime, timezone
from pathlib import Path

from solaris.tools import pack as P

SCHEDULE = Path(".memory") / "schedule.json"   # inside the pack folder
CAP_MINUTES = 25   # below Claude Code's 30-minute limit on a background command
MAX_EVERY = 366 * 24 * 60   # minutes
NAP = 30.0   # seconds per sleep: the wall clock is read between naps, so a machine that sleeps still wakes on time

_now = time.time   # tests swap the clock
_sleep = time.sleep


class ScheduleError(Exception):
    """The schedule is missing or invalid (the message names the file and the problem)."""


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
    """Sleep until something is due or ``cap`` seconds pass: ("due", [(time, name)...]) or ("re-arm", [the
    next (time, name)])."""
    started = _now()
    while True:
        now = _now()
        fire = due(events, after, now)
        if fire[0][0] <= now:
            return "due", fire
        if now >= started + cap:
            return "re-arm", fire[:1]
        _sleep(min(fire[0][0] - now, started + cap - now, NAP))


def main(argv: "list[str] | None" = None) -> int:
    parser = argparse.ArgumentParser(prog="solaris.tools.session_clock", description=__doc__.splitlines()[0])
    where = parser.add_mutually_exclusive_group(required=True)
    where.add_argument("--dir", help="project root: read <pack>/.memory/schedule.json")
    where.add_argument("--schedule", type=Path, help="schedule file to read instead")
    parser.add_argument("--after", metavar="TIME",
                        help="due time of the last event handled (ISO with offset, or epoch seconds); default: now")
    parser.add_argument("--cap", type=float, default=CAP_MINUTES, metavar="MINUTES",
                        help=f"longest sleep before a re-arm (default {CAP_MINUTES}, below the harness's limit "
                             "on background commands)")
    args = parser.parse_args(argv)
    if not math.isfinite(args.cap) or args.cap <= 0:
        parser.error(f"--cap must be a positive number of minutes, not {args.cap}")
    after = math.floor(_now())
    if args.after is not None:
        try:
            after = parse_after(args.after)
        except ValueError:
            parser.error(f"--after {args.after!r} is neither an ISO time with a UTC offset nor epoch seconds")
    path = args.schedule
    try:
        if path is None:
            if not Path(args.dir).is_dir():
                raise ScheduleError(f"{args.dir} not found: --dir must be a project root (embedded mode: the "
                                    "repo root)")
            path = P.require_pack(Path(args.dir)) / SCHEDULE
        events = load(path)
    except (ScheduleError, P.PackError) as exc:
        print(f"session_clock: {exc}")
        return 1
    if not due(events, after, _now()):
        print(f"session_clock: no event left to wait for in {path} after {iso(after)} (every one-off event is "
              "past and none recurs)")
        return 1
    kind, fire = run(events, after, args.cap * 60)
    if kind == "re-arm":
        t, name = fire[0]
        print(f"session_clock: re-arm: {args.cap:g}-minute cap reached; next is {name} at {iso(t)} (in "
              f"{_span(t - _now())}); start again with --after {iso(after)}")
        return 0
    for t, name in fire:
        print(f"session_clock: due {name} at {iso(t)}")
    print(f"session_clock: next: --after {iso(fire[-1][0])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
