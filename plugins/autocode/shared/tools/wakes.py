# rev. 1

"""Autocode wake specs from a project's Solaris schedule (stdlib only).

  python3 wakes.py --dir <project> [--skip-prefix P ...] [--json]
  python3 wakes.py --dir <project> --show NAME

Reads the project's <pack>/.memory/schedule.json (the session_clock schedule: a JSON list of events with a name,
an ``at`` time with its UTC offset, an optional ``every`` in minutes counted from ``at`` or from 00:00 UTC, and an
optional note) and prints one autocode wake per event: recurring events as cron triggers in UTC, which keeps the
instants they fire at today (their stored offsets are fixed, so they never moved with daylight saving: Kaggle's
00:00 UTC reset stays put); one-shot events still ahead as ``at <ISO time>``; past one-shots are left out. A period
that cron cannot express falls back to ``every <N>m`` (counted from when the wake is set; the line says so).
--skip-prefix leaves out events whose names start with a prefix (events another agent owns). Each line carries a
short wake text that points back to the whole note, which --show prints. Nothing is changed: the agent sets the
wakes with its autocode tools. Exit 0, or 2 when the project, its pack or the schedule cannot be read.
"""
import argparse, json, os, sys
from datetime import datetime, timedelta, timezone

PACIFIC_OFFSET_HINT = 'America/Los_Angeles'


def find_pack(project):
    for name in sorted(os.listdir(project)):
        path = os.path.join(project, name)
        if name.startswith('.') or not os.path.isdir(path):
            continue
        try:
            with open(os.path.join(path, 'manifest.json'), encoding='utf-8') as f:
                data = json.load(f)
        except (OSError, ValueError):
            continue
        if isinstance(data, dict) and 'framework_version' in data and isinstance(data.get('project'), dict):
            return path
    raise FileNotFoundError(f'no ai-pack under {project}')


def pacific(t):
    try:
        from zoneinfo import ZoneInfo
        return t.astimezone(ZoneInfo(PACIFIC_OFFSET_HINT)).strftime('%b %-d %-I:%M %p %Z')
    except Exception:  # noqa: BLE001 - a display hint only
        return t.astimezone(timezone.utc).strftime('%b %-d %H:%M UTC')


def cron_for(every, anchor):
    """(trigger, note) for an event every `every` minutes from `anchor` (an aware datetime)."""
    a = anchor.astimezone(timezone.utc)
    if every <= 0:
        return None, 'a period of 0 minutes'
    if 60 % every == 0:
        minutes = sorted({(a.minute + k * every) % 60 for k in range(60 // every)})
        return f'cron "{",".join(map(str, minutes))} * * * *" UTC', ''
    if every % 60 == 0 and 1440 % every == 0:
        step = every // 60
        hours = sorted({(a.hour + k * step) % 24 for k in range(24 // step)})
        return f'cron "{a.minute} {",".join(map(str, hours))} * * *" UTC', ''
    if every == 10080:
        dow = (a.weekday() + 1) % 7  # cron: 0 is Sunday
        return f'cron "{a.minute} {a.hour} * * {dow}" UTC', ''
    return f'every {every}m', f'every {every} minutes counted from when the wake is set, not from {a.isoformat()}'


def next_fire(every, anchor, now):
    if anchor > now:
        return anchor
    steps = int((now - anchor).total_seconds() // (every * 60)) + 1
    return anchor + timedelta(minutes=every * steps)


def load(project):
    pack = find_pack(project)
    with open(os.path.join(pack, '.memory', 'schedule.json'), encoding='utf-8') as f:
        events = json.load(f)
    if not isinstance(events, list):
        raise ValueError('schedule.json is not a JSON list')
    return pack, events


def specs(project, skip, now=None):
    pack, events = load(project)
    now = now or datetime.now(timezone.utc)
    rel = os.path.relpath(os.path.abspath(__file__), os.path.abspath(project))
    out = []
    for e in events:
        name = e.get('name')
        if not isinstance(name, str) or any(name.startswith(p) for p in skip):
            continue
        note = ' '.join(str(e.get('note') or '').split())
        try:
            anchor = datetime.fromisoformat(e['at']) if e.get('at') else None
        except (TypeError, ValueError):
            anchor = None
        every = e.get('every')
        if every:
            if anchor is None:
                anchor = datetime(1970, 1, 1, tzinfo=timezone.utc)
            trigger, caveat = cron_for(int(every), anchor)
            if trigger is None:
                continue
            nxt = next_fire(int(every), anchor, now)
            kind = f'every {int(every)} min'
        else:
            if anchor is None or anchor <= now:
                continue
            trigger, caveat, nxt, kind = f'at {anchor.isoformat()}', '', anchor, 'once'
        text = (f'Scheduled event "{name}" ({kind}): {note[:200]}{"..." if len(note) > 200 else ""} '
                f'Whole note: python3 {rel} --dir . --show {name}')
        out.append({'name': name, 'trigger': trigger, 'next': pacific(nxt), 'caveat': caveat, 'text': text})
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument('--dir', required=True, help='the project folder (holding the ai-pack)')
    ap.add_argument('--skip-prefix', action='append', default=[], help='leave out events with this name prefix')
    ap.add_argument('--json', action='store_true', help='one JSON list instead of lines')
    ap.add_argument('--show', metavar='NAME', help="print one event's whole entry")
    a = ap.parse_args(argv)
    try:
        if a.show:
            _, events = load(a.dir)
            hits = [e for e in events if e.get('name') == a.show]
            print(json.dumps(hits, indent=1, ensure_ascii=False) if hits else f'no event named {a.show}')
            return 0
        rows = specs(a.dir, a.skip_prefix)
    except (OSError, ValueError) as e:
        print(f'wakes: {e}', file=sys.stderr)
        return 2
    if a.json:
        print(json.dumps(rows, indent=1, ensure_ascii=False))
        return 0
    for r in rows:
        print(f'{r["name"]} | {r["trigger"]} | next {r["next"]}' + (f' | NOTE {r["caveat"]}' if r['caveat'] else ''))
    print(f'{len(rows)} wake(s)')
    return 0


if __name__ == '__main__':
    sys.exit(main())
