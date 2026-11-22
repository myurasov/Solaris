# rev. 2

"""Autocode wake specs from a project's Solaris schedule (stdlib only).

  python3 wakes.py --dir <project> [--zone ZONE] [--local PREFIX ...] [--skip-prefix P ...] [--json]
  python3 wakes.py --dir <project> --show NAME

Reads the project's <pack>/.memory/schedule.json (the session_clock schedule: a JSON list of events with a name,
an ``at`` time with its UTC offset, an optional ``every`` in minutes counted from ``at`` or from 00:00 UTC, an
optional note, and an optional ``zone``) and prints one autocode wake per event:

- a recurring event as a cron trigger in UTC, which keeps the instants it fires at today (its stored offset is
  fixed, so it never moved with daylight saving: an event tied to a clock elsewhere, such as a daily reset at
  00:00 UTC, stays put);
- a recurring event tied to people's local time as a cron trigger in their zone, at the local time it was written
  for, so it stays at 9:00 when the clocks change: an event with a ``zone`` of its own (an IANA name), or one
  whose name starts with a --local prefix (then the zone is --zone);
- a one-time event still ahead as ``at <ISO time>``; past ones are left out.

A period that cron cannot express falls back to ``every <N>m`` (counted from when the wake is set; the line says
so). A recurring event whose start is still ahead gets its cron all the same, and the line says that the cron runs
before that start too. An event name that is no wake name (a wake name takes letters, digits and - _ . :, and
``guard`` is the idle guard's) gets one, and the line says which. The "next" time of each line is the trigger's
own next run. --zone is the zone those times are shown in and the zone of --local events: pass the server's
(autocode_me action=status names it); without it, this machine's. --skip-prefix leaves out events whose names
start with a prefix (events another agent owns). Each wake carries a short text that points back to the whole
note, which --show prints. A last line names the recurring events that were written with a local offset and come
out in UTC. --json prints the wakes as one list (hints then go to stderr). The schedule is checked as
session_clock checks it, and what that refuses is refused here, with the event's name. Nothing is changed: the
agent sets the wakes with its autocode tools. Exit 0, or 2 when the project, its pack, the schedule or a zone
cannot be read.
"""
import argparse, json, os, re, shlex, sys
from datetime import datetime, timedelta, timezone

WAKE_NAME = re.compile(r'[A-Za-z0-9._:-]{1,255}')
RESERVED = ('guard',)        # the idle guard's wake
MAX_EVERY = 366 * 24 * 60    # minutes, as session_clock has it


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


def zone_of(name):
    """The time zone of an IANA name, or of UTC; ValueError when there is none."""
    if name == 'UTC':
        return timezone.utc
    try:
        from zoneinfo import ZoneInfo
    except ImportError:
        raise ValueError(f'zone {name}: zones other than UTC need Python 3.9 or later') from None
    try:
        return ZoneInfo(name)
    except Exception:  # noqa: BLE001 - a missing zone and a bad name fail in different ways
        raise ValueError(f'no such time zone: {name!r} (an IANA name such as Europe/Berlin, or UTC)') from None


def local_zone(env=None):
    """This machine's zone as an IANA name; UTC when it cannot be told."""
    env = os.environ if env is None else env
    names = [str(env.get('TZ') or '').lstrip(':')]
    try:
        with open('/etc/timezone', encoding='utf-8') as f:
            names.append(f.read().strip())
    except OSError:
        pass
    link = os.path.realpath('/etc/localtime')
    if 'zoneinfo/' in link:
        names.append(link.split('zoneinfo/', 1)[1])
    for name in names:
        if not name:
            continue
        try:
            zone_of(name)
            return name
        except ValueError:
            continue
    return 'UTC'


def shown(t, tz):
    try:
        return t.astimezone(tz).strftime('%b %-d %-I:%M %p %Z')
    except (OverflowError, ValueError):
        return t.isoformat()   # a time at the calendar's edge


def by_the_hour(every):
    """Whether an event's cron names hours, so that its local time moves when the clocks change."""
    return every == 10080 or (every > 60 and every % 60 == 0 and 1440 % every == 0)


def cron_for(every, anchor, tz=timezone.utc, zone='UTC'):
    """(trigger, note, fields) for an event every `every` minutes from `anchor` (an aware datetime), said in a
    zone; fields are the cron's (minutes, hours or None, weekday or None), None for a trigger that is no cron."""
    a = anchor.astimezone(tz)
    if 60 % every == 0:
        minutes = sorted({(a.minute + k * every) % 60 for k in range(60 // every)})
        return f'cron "{",".join(map(str, minutes))} * * * *" {zone}', '', (minutes, None, None)
    if every % 60 == 0 and 1440 % every == 0:
        step = every // 60
        hours = sorted({(a.hour + k * step) % 24 for k in range(24 // step)})
        return f'cron "{a.minute} {",".join(map(str, hours))} * * *" {zone}', '', ([a.minute], hours, None)
    if every == 10080:
        dow = (a.weekday() + 1) % 7  # cron: 0 is Sunday
        return f'cron "{a.minute} {a.hour} * * {dow}" {zone}', '', ([a.minute], [a.hour], dow)
    note = f'every {every} minutes counted from when the wake is set, not from {anchor.isoformat()}'
    return f'every {every}m', note, None


def wall(day, hour, minute, tz):
    """The instant of a wall time in a zone, as autocode's cron takes it: the first one when the clocks repeat
    it, the next minute that exists when they skip it."""
    naive = datetime(day.year, day.month, day.day, hour, minute)
    for k in range(181):
        t = naive + timedelta(minutes=k)
        there = t.replace(tzinfo=tz)
        if there.astimezone(timezone.utc).astimezone(tz).replace(tzinfo=None) == t:
            return there
    return naive.replace(tzinfo=tz)


def next_run(fields, tz, now):
    """When a cron of ours runs next after `now`."""
    minutes, hours, dow = fields
    # counted in UTC: two times of one zone compare by their clock faces, which an hour that comes twice confuses
    now = now.astimezone(timezone.utc)
    today = now.astimezone(tz).date()
    for d in range(9):
        day = today + timedelta(days=d)
        if dow is not None and (day.weekday() + 1) % 7 != dow:
            continue
        for hour in (range(24) if hours is None else hours):
            for minute in minutes:
                when = wall(day, hour, minute, tz).astimezone(timezone.utc)
                if when > now:
                    return when
    return now


def parse_at(value):
    """An aware datetime of an ISO time with a UTC offset (Z is one); ValueError for anything else."""
    if not isinstance(value, str):
        raise ValueError(value)
    text = value.strip()
    if text[-1:] == 'Z':
        text = text[:-1] + '+00:00'   # fromisoformat takes Z only from Python 3.11 on
    elif text[-1:] == 'z':
        raise ValueError(value)
    dt = datetime.fromisoformat(text)
    if dt.tzinfo is None:
        raise ValueError(value)
    return dt


def checked(events):
    """The schedule's events as (name, at, every, zone, note), refused where session_clock refuses them."""
    out = []
    for i, e in enumerate(events, 1):
        where = f'schedule.json: event {i}'
        if not isinstance(e, dict):
            raise ValueError(f'{where} is not an object')
        name = e.get('name')
        if not isinstance(name, str) or not name.strip():
            raise ValueError(f'{where} needs a name (a non-empty string)')
        name = name.strip()
        where += f' ({name})'
        at = every = None
        if 'at' in e:
            try:
                at = parse_at(e['at'])
            except (TypeError, ValueError):
                raise ValueError(f'{where}: at must be an ISO time with a UTC offset, such as '
                                 f'2026-10-01T17:00:00-07:00, not {e["at"]!r}') from None
            try:
                at.astimezone(timezone.utc) + timedelta(days=2)
                at.astimezone(timezone.utc) - timedelta(days=2)
            except OverflowError:
                raise ValueError(f'{where}: at {e["at"]!r} is at the calendar\'s edge, which no zone can count '
                                 'from') from None
        if 'every' in e:
            n = e['every']
            whole = isinstance(n, int) and not isinstance(n, bool) or isinstance(n, float) and n.is_integer()
            if not whole or not 1 <= n <= MAX_EVERY:
                raise ValueError(f'{where}: every must be a whole number of minutes from 1 to {MAX_EVERY} (a year), '
                                 f'not {n!r}')
            every = int(n)
        if at is None and every is None:
            raise ValueError(f'{where} needs at (a time) or every (minutes)')
        zone = e.get('zone')
        if zone is not None and not isinstance(zone, str):
            raise ValueError(f'{where}: zone must be a zone name such as Europe/Berlin, not {zone!r}')
        out.append((name, at, every, zone, ' '.join(str(e.get('note') or '').split())))
    return out


def wake_names(names):
    """A wake name for each event name, in order: its own where autocode takes it, else one made of it."""
    def own(name):
        return bool(WAKE_NAME.fullmatch(name)) and bool(name.strip('.')) and name not in RESERVED

    taken = set(RESERVED)
    for name in names:
        if own(name):
            taken.add(name)
    seen, out = set(), []
    for name in names:
        if own(name) and name not in seen:
            seen.add(name)
            out.append(name)
            continue
        base = re.sub(r'[^A-Za-z0-9._:-]+', '-', name).strip('-.')[:200] or 'event'
        wake, n = base, 1
        while wake in taken:
            n += 1
            wake = f'{base}-{n}'
        taken.add(wake)
        out.append(wake)
    return out


def load(project):
    pack = find_pack(project)
    with open(os.path.join(pack, '.memory', 'schedule.json'), encoding='utf-8') as f:
        events = json.load(f)
    if not isinstance(events, list):
        raise ValueError('schedule.json is not a JSON list')
    return pack, events


def specs(project, skip=(), now=None, zone=None, local=()):
    """(wakes, hints) of the project's schedule."""
    pack, events = load(project)
    now = now or datetime.now(timezone.utc)
    zone = zone or local_zone()
    show_tz = zone_of(zone)
    rel = os.path.relpath(os.path.abspath(__file__), os.path.abspath(project))
    out, moving = [], []
    for name, anchor, every, own_zone, note in checked(events):
        if any(name.startswith(p) for p in skip):
            continue
        if own_zone:
            zone_of(own_zone)   # a zone that does not exist is refused, used or not
        tied = own_zone or (zone if any(name.startswith(p) for p in local) else None)
        notes = []
        if every:
            if tied and anchor is None:
                notes.append('it has a zone and no at, so its local time is not known: counted from 00:00 UTC')
                tied = None
            start = anchor or datetime(1970, 1, 1, tzinfo=timezone.utc)
            tz = zone_of(tied) if tied else timezone.utc
            trigger, caveat, fields = cron_for(every, start, tz, tied or 'UTC')
            if caveat:
                notes.append(caveat)
            if fields and anchor is not None and anchor > now:
                notes.append(f'its start {anchor.isoformat()} is still ahead, and this cron runs before it too: set '
                             'the wake on that day if it must not')
            if not tied and by_the_hour(every) and start.utcoffset():
                moving.append(name)
            nxt = next_run(fields, tz, now) if fields else now + timedelta(minutes=every)
            kind = f'every {every} min'
        else:
            if anchor <= now:
                continue
            trigger, nxt, kind = f'at {anchor.isoformat()}', anchor, 'once'
        # --show=NAME, so that a name may start with a dash
        text = (f'Scheduled event "{name}" ({kind}): {note[:200]}{"..." if len(note) > 200 else ""} '
                f'Whole note: python3 {rel} --dir . --show={shlex.quote(name)}')
        out.append({'name': name, 'trigger': trigger, 'next': shown(nxt, show_tz), 'caveat': '; '.join(notes),
                    'text': text})
    for row, wake in zip(out, wake_names([r['name'] for r in out])):
        row['wake'] = wake
        if wake != row['name']:
            renamed = (f'the event "{row["name"]}" is set as the wake {wake} (a wake name takes letters, digits '
                       "and - _ . :, and guard is the idle guard's)")
            row['caveat'] = f'{row["caveat"]}; {renamed}' if row['caveat'] else renamed
    hints = []
    if moving:
        hints.append(f'{len(moving)} recurring event(s) were written with a local offset and come out as UTC crons: '
                     'they keep today\'s instants, so their local time moves by an hour when the clocks change. To '
                     'keep the local time instead, name them with --local PREFIX (and --zone), or give each a '
                     f'"zone" in the schedule: {", ".join(moving)}')
    return out, hints


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument('--dir', required=True, help='the project folder (holding the ai-pack)')
    ap.add_argument('--zone', help="the zone of the next times and of --local events (default: this machine's)")
    ap.add_argument('--local', action='append', default=[], metavar='PREFIX',
                    help='events with this name prefix keep their local time in --zone')
    ap.add_argument('--skip-prefix', action='append', default=[], help='leave out events with this name prefix')
    ap.add_argument('--json', action='store_true', help='one JSON list instead of lines')
    ap.add_argument('--show', metavar='NAME', help="print one event's whole entry")
    a = ap.parse_args(argv)
    try:
        if a.show:
            _, events = load(a.dir)
            hits = [e for e in events if isinstance(e, dict) and str(e.get('name')).strip() == a.show.strip()]
            print(json.dumps(hits, indent=1, ensure_ascii=False) if hits else f'no event named {a.show}')
            return 0
        rows, hints = specs(a.dir, a.skip_prefix, zone=a.zone, local=a.local)
    except (OSError, ValueError, OverflowError) as e:
        print(f'wakes: {e}', file=sys.stderr)
        return 2
    if a.json:
        print(json.dumps(rows, indent=1, ensure_ascii=False))
        for hint in hints:
            print(f'HINT {hint}', file=sys.stderr)
        return 0
    for r in rows:
        print(f'{r["wake"]} | {r["trigger"]} | next {r["next"]}' + (f' | NOTE {r["caveat"]}' if r['caveat'] else ''))
    print(f'{len(rows)} wake(s)')
    for hint in hints:
        print(f'HINT {hint}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
