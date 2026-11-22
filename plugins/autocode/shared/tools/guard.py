# rev. 1

"""The guard of an autocode session: open work, and background jobs that are watched (stdlib only).

  python3 guard.py check [--steps FILE ...] [--repeat 1h]
  python3 guard.py on TEXT... | off [TEXT...] | wait DURATION [TEXT...] | wait off | status [--json]
  python3 guard.py job start --name NAME --deadline 2h [--wake WAKE] [--log FILE] [--cwd DIR] -- COMMAND [ARG...]
  python3 guard.py job list [--json] | job stop NAME | job extend NAME DURATION | job done NAME

``check`` is the check of the session's idle guard wake: it exits 0, with one line per reason, when the session
should look (exit 0 wakes it), and 1 when nothing needs it (the run is skipped and costs nothing). Reasons: open
work (a line set with ``on``, or unticked ``- [ ]`` steps in a ``--steps`` file) and a job that needs a look. Open
work is held back while a ``wait`` lasts (``wait 40m`` before the turn ends on something that reports by itself; at
most 24h; it holds back the lines that were open when it was set, and the steps files altogether) and while a job
runs within its deadline, since its end wakes the session. Open work told a third time in a row with nothing
changed is told less and less often (after 20 minutes, 40, 80, then every 2 hours), until a line or a step
changes or the session uses the guard (on, off, wait, a job command). A check that cannot run exits 0 with the
reason, so a broken guard is seen.

``job start`` runs a command (its arguments as given: a shell line needs ``sh -c '...'``) detached from the turn,
for work that must outlive it: the command's exit code is written down, and the job's ``fire-only once`` wake (set
it first with autocode_wake) is fired with it. The guard tells what the wake cannot: a job that was lost at a
reboot or killed, one past its deadline (``job extend`` gives more time, ``job stop`` ends it and what it
started), one whose wake could not be fired, and the end of a job that has no wake. Each is told once per
``--repeat`` until ``job done NAME`` forgets the job. Ended jobs are forgotten after a week. Nothing is ever
signalled that cannot be proven to be the job's: where only a process group is left of a dead job, the guard names
it and leaves it to the session.

State is kept in <pack>/.memory/guard/ of the project in ``--dir`` (default: the current folder), or in the folder
``--state`` names (a session outside a project). Durations: 90s, 30m, 2h, 1d, 1h30m. Exit 0; 2 on a wrong call or
when the state cannot be found; 3 when the job named is not in the state the command needs.
"""
import argparse, errno, fcntl, hashlib, json, os, re, shlex, shutil, signal, socket, stat, subprocess, sys, time

NAME = re.compile(r'[A-Za-z0-9][A-Za-z0-9_.:-]{0,63}')
STEP = re.compile(r'\s*(?:[-*+]|\d+[.)]) \[ \]')
FENCE = re.compile(r'\s*(?:```|~~~)')
DURATION = re.compile(r'(\d+)([smhd])')
UNIT = {'s': 1, 'm': 60, 'h': 3600, 'd': 86400}
KEEP = 7 * 86400         # an ended job is forgotten after this
REPEAT = '1h'            # a job's trouble is told once per this
START_WAIT = 30          # seconds a new job has to say its pid
MAX_WAIT = 86400         # the longest wait
BACKOFF = 20 * 60        # unchanged open work, told a third time: after this, then twice as long each time
BACKOFF_MAX = 2 * 3600
ROOM = 1400              # characters of a check's reasons: autocode quotes the first 2000 of its output
LINE = 400               # the most of one reason
MAX_STEPS = 5 << 20      # bytes read of a steps file
FAR = 32503680000        # the year 3000: a time past it is no time


class Refused(Exception):
    def __init__(self, code, text):
        super().__init__(text)
        self.code = code


def duration(text):
    """Seconds of 90s, 30m, 2h, 1d or 1h30m."""
    text = str(text or '')
    if not re.fullmatch(r'(?:\d+[smhd])+', text):
        raise ValueError(f'not a duration: {text!r} (like 90s, 30m, 2h, 1d, 1h30m)')
    return sum(int(n) * UNIT[u] for n, u in DURATION.findall(text))


def some_time(text, what):
    """A duration that is more than nothing, for a call that needs one."""
    try:
        seconds = duration(text)
    except ValueError as e:
        raise Refused(2, f'{what}: {e}') from None
    if seconds <= 0:
        raise Refused(2, f'{what}: {text} is no time at all')
    return seconds


def knob(name, default):
    """Seconds from the environment, for what a test or a slow machine sets otherwise."""
    try:
        return max(float(os.environ.get(name, default)), 0.0)
    except ValueError:
        return float(default)


def grace():
    """Seconds a stopped command has after TERM."""
    return knob('AUTOCODE_GUARD_GRACE', 30)


def stamp(t):
    try:
        return time.strftime('%Y-%m-%d %H:%M %Z', time.localtime(t))
    except (OverflowError, OSError, ValueError, TypeError):
        return '?'


def clip(text):
    return text if len(text) <= LINE else text[:LINE - 3] + '...'


def span(seconds):
    m = int(max(seconds, 0) // 60)
    if m < 60:
        return f'{m} min'
    if m < 1440:
        return f'{m // 60} h {m % 60} min'
    return f'{m // 1440} d {m % 1440 // 60} h'


def number(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def sane(v):
    """A time that can be one."""
    return number(v) and 0 <= v < FAR


def find_pack(project):
    try:
        names = sorted(os.listdir(project))
    except OSError:
        names = []
    for name in names:
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
    raise FileNotFoundError(f'no ai-pack under {os.path.abspath(project)}: name the project with --dir, '
                            'or a state folder with --state')


def state_dir(a):
    if a.state:
        return os.path.abspath(a.state)
    return os.path.join(os.path.abspath(find_pack(a.dir or '.')), '.memory', 'guard')


class Lock:
    """One writer at a time: the tool, a job's supervisor and a check may run together."""

    def __init__(self, state):
        self.path = os.path.join(state, 'lock')

    def __enter__(self):
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        self.f = open(self.path, 'w')
        fcntl.flock(self.f, fcntl.LOCK_EX)
        return self

    def __exit__(self, *exc):
        fcntl.flock(self.f, fcntl.LOCK_UN)
        self.f.close()


def read(path, default=None):
    try:
        with open(path, encoding='utf-8') as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def write(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = f'{path}.{os.getpid()}.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=1, ensure_ascii=False)
    os.replace(tmp, path)


def marker(state):
    data = read(os.path.join(state, 'open.json'), {})
    if not isinstance(data, dict):
        data = {}
    data['open'] = [r for r in data.get('open') or [] if isinstance(r, dict) and isinstance(r.get('text'), str)
                    and r['text']]
    for r in data['open']:
        if not sane(r.get('since')):
            r.pop('since', None)
    # a wait longer than the longest was not set by this tool
    if not (sane(data.get('wait_until')) and sane(data.get('wait_set_at'))
            and data['wait_until'] - time.time() <= MAX_WAIT):
        for key in ('wait_until', 'wait_set_at', 'wait_text'):
            data.pop(key, None)
    return data


def told_of(state):
    """What the checks said and when: per job, and for the open work."""
    data = read(os.path.join(state, 'told.json'), {})
    if not isinstance(data, dict):
        data = {}
    jobs_told = data.get('jobs') if isinstance(data.get('jobs'), dict) else {}
    data['jobs'] = {k: v for k, v in jobs_told.items()
                    if isinstance(v, dict) and isinstance(v.get('what'), str) and sane(v.get('at'))}
    said = data.get('open')
    if not (isinstance(said, dict) and isinstance(said.get('key'), str) and isinstance(said.get('n'), int)
            and sane(said.get('at'))):
        data.pop('open', None)
    return data


def forget_told(state, job=None):
    """Under the lock, when the session used the guard: the open work counts as new, and so does the job named."""
    told = told_of(state)
    told.pop('open', None)
    if job is not None:
        told['jobs'].pop(job, None)
    write(os.path.join(state, 'told.json'), told)


def machine():
    return socket.gethostname().split('.')[0]


def boot_id():
    """What names this boot of the machine; '' where the system does not say."""
    try:
        with open('/proc/sys/kernel/random/boot_id') as f:
            return f.read().strip()
    except OSError:
        return ''


def proc_token(pid):
    """What tells a live process from a later one with its pid: when it started. '' when it is gone."""
    if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0:
        return ''
    if os.path.isdir('/proc/self'):
        try:
            with open(f'/proc/{pid}/stat') as f:
                data = f.read()
            # the name may hold spaces and brackets: the fields come after the last one
            fields = data[data.rindex(')') + 2:].split()
            return '' if fields[0] == 'Z' else fields[19]
        except (OSError, ValueError, IndexError):
            return ''
    try:
        # the same words whatever the caller's language is
        out = subprocess.run(['ps', '-o', 'lstart=', '-p', str(pid)], capture_output=True, text=True, timeout=5,
                             env=dict(os.environ, LC_ALL='C'))
        return out.stdout.strip() if out.returncode == 0 else ''
    except (OSError, subprocess.SubprocessError):
        return ''


def alive(pid, token):
    """Whether the process written down still runs. Without its token nobody can tell, so: no."""
    return bool(token) and proc_token(pid) == token


def members(pgid):
    """The live processes of a process group; None where the system does not say."""
    if not os.path.isdir('/proc/self'):
        return None
    out = []
    for name in os.listdir('/proc'):
        if not name.isdigit():
            continue
        try:
            with open(f'/proc/{name}/stat') as f:
                data = f.read()
            fields = data[data.rindex(')') + 2:].split()
            if fields[0] != 'Z' and int(fields[2]) == pgid:
                out.append(int(name))
        except (OSError, ValueError, IndexError):
            continue
    return out


def group_alive(pgid):
    try:
        os.killpg(pgid, 0)
    except ProcessLookupError:
        return False
    except OSError:
        return True
    return True


def signal_group(pgid, sig):
    try:
        os.killpg(pgid, sig)
    except OSError:
        pass


def end_group(pgid, limit):
    """TERM, then KILL after the grace, for a process group: whether it is gone."""
    signal_group(pgid, signal.SIGTERM)
    end = time.time() + limit
    while group_alive(pgid) and time.time() < end:
        time.sleep(0.1)
    if group_alive(pgid):
        signal_group(pgid, signal.SIGKILL)
        end = time.time() + 5
        while group_alive(pgid) and time.time() < end:
            time.sleep(0.1)
    return not group_alive(pgid)


def others(pgid):
    """Whether anything of a process group still runs, its ended leader apart."""
    live = members(pgid)
    return group_alive(pgid) if live is None else bool(live)


def leftover(rec):
    """The process group of a dead job's command when its leader is gone and members still run, else None."""
    pgid = rec.get('child_pid')
    if not pgid or proc_token(pgid):
        # no command; or a process has that number: the leader, or a later one, and then the group is gone
        return None
    return pgid if others(pgid) else None


def half_started(rec):
    """Its supervisor wrote itself down and died before it wrote its command down: the command may run."""
    return bool(rec.get('pid')) and 'child_pid' not in rec


def half_text(rec):
    return (f'its supervisor was killed as it started its command, which may run: look for it (pgrep -fa '
            f'{shlex.quote(rec["cmd"][0])}) and end it if it does.')


def left_text(pgid):
    return (f'processes of its group {pgid} may still run (pgrep -l -g {pgid} lists them); that they are its '
            f'cannot be proven from here, so end them by hand if they are (kill -- -{pgid}).')


def job_path(state, key):
    return os.path.join(state, 'jobs', key + '.json')


def bad(rec):
    """Why a job record cannot be used, or None."""
    if not isinstance(rec, dict):
        return 'not a record'
    cmd = rec.get('cmd')
    if not isinstance(cmd, list) or not cmd or not all(isinstance(c, str) for c in cmd):
        return 'no command'
    if not sane(rec.get('started_at')):
        return 'no start time'
    for key in ('machine', 'log'):
        if not isinstance(rec.get(key), str):
            return f'no {key}'
    for key in ('deadline_at', 'ended_at'):
        if rec.get(key) is not None and not sane(rec[key]):
            return f'{key} is no time'
    for key in ('pid', 'child_pid', 'exit'):
        if rec.get(key) is not None and (not isinstance(rec[key], int) or isinstance(rec[key], bool)):
            return f'{key} is no number'
    return None


def load_job(state, key):
    """(record, problem) of a job by its file: one of the two is None; both when there is no such job."""
    path = job_path(state, key)
    if not os.path.exists(path):
        return None, None
    try:
        with open(path, encoding='utf-8') as f:
            rec = json.load(f)
    except (OSError, ValueError):
        return None, 'not JSON'
    problem = bad(rec)
    if problem:
        return None, problem
    # the file names the job, whatever the record says
    rec['name'] = key
    return rec, None


def jobs(state):
    """Every job of the state, by its file: (key, record or None, problem or None), oldest first."""
    folder = os.path.join(state, 'jobs')
    try:
        names = sorted(n for n in os.listdir(folder) if n.endswith('.json') and '.sync-conflict-' not in n)
    except OSError:
        return []
    found = [(n[:-5],) + load_job(state, n[:-5]) for n in names]
    return sorted(found, key=lambda j: (j[1] or {}).get('started_at') or 0)


def job_state(rec, now):
    """ended, stopped, elsewhere (another machine's), lost (a reboot), starting, running or died."""
    if rec.get('ended_at') is not None:
        return 'stopped' if rec.get('stopped') else 'ended'
    if rec.get('machine') != machine():
        return 'elsewhere'
    boot = boot_id()
    if rec.get('boot') and boot and rec['boot'] != boot:
        return 'lost'
    if not rec.get('pid'):
        return 'starting' if 0 <= now - rec['started_at'] < START_WAIT else 'died'
    return 'running' if alive(rec['pid'], rec.get('pid_token')) else 'died'


def late(rec, state, now):
    return bool(rec.get('deadline_at')) and now > rec['deadline_at'] and state in ('running', 'starting', 'elsewhere')


def firing(rec, state):
    """Its end is written down and its supervisor is still at its wake."""
    return (state == 'ended' and bool(rec.get('wake')) and rec.get('fired') is None
            and rec.get('machine') == machine() and alive(rec.get('pid'), rec.get('pid_token')))


def label(rec, state, now):
    if state in ('ended', 'stopped'):
        text = f'{state}, exit {rec.get("exit")}' if rec.get('exit') is not None else f'{state}, no exit code'
        if firing(rec, state):
            text += ', firing its wake'
        elif state == 'ended' and rec.get('wake') and rec.get('fired') is not True:
            text += ', its wake not fired'
        return text
    if state == 'elsewhere':
        text = f'on {rec.get("machine")} (not checked from here)'
    elif state == 'lost':
        text = 'lost (the machine restarted)'
    elif state == 'died':
        text = 'died (no exit code)'
    else:
        text = state
    return text + (', past its deadline' if late(rec, state, now) else '')


def trouble(rec, state, now):
    """(what, line) the guard says about a job, or None when there is nothing to tell."""
    name, log = rec['name'], rec.get('log')
    if state == 'ended':
        if rec.get('wake') and (rec.get('fired') is True or firing(rec, state)):
            return None
        text = f'job {name} ended with exit {rec.get("exit")}'
        if rec.get('error'):
            text += f' ({rec["error"]})'
        if rec.get('wake'):
            why = rec.get('fire_error') or 'its supervisor was gone'
            text += f'; its wake {rec["wake"]} could not be fired ({why})'
        return 'ended', f'{text}; log {log}. Then: job done {name}'
    if state == 'lost':
        return 'lost', (f'job {name} was lost: the machine restarted since it started ({stamp(rec["started_at"])}); '
                        f'log {log}. Start it again, or: job done {name}')
    if state == 'died':
        text = f'job {name} is gone without an exit code (killed?), started {stamp(rec["started_at"])}; log {log}.'
        if alive(rec.get('child_pid'), rec.get('child_token')):
            return 'died', (f'{text} Its command still runs as pid {rec["child_pid"]}, unwatched: job stop {name} '
                            'ends it')
        if half_started(rec):
            return 'died', f'{text} And {half_text(rec)} Then: job done {name}'
        pgid = leftover(rec)
        if pgid:
            return 'died', f'{text} But {left_text(pgid)} Then: job done {name}'
        return 'died', f'{text} Start it again, or: job done {name}'
    if late(rec, state, now):
        over = span(now - rec['deadline_at'])
        if state == 'elsewhere':
            return 'late', (f'job {name} on {rec.get("machine")} is past its deadline by {over}; it cannot be '
                            'checked from here')
        return 'late', (f'job {name} is past its deadline by {over} and still running (pid {rec.get("pid")}): '
                        f'job extend {name} DURATION, or job stop {name}')
    return None


def open_steps(path):
    """(how many, a digest) of the unticked steps of a file: list items with [ ], outside code blocks."""
    if not stat.S_ISREG(os.stat(path).st_mode):
        raise OSError(errno.EINVAL, 'not a plain file')
    with open(path, 'rb') as f:
        text = f.read(MAX_STEPS).decode('utf-8', 'replace')
    n, fenced, digest = 0, False, hashlib.sha1()
    for line in text.splitlines():
        if FENCE.match(line):
            fenced = not fenced
        elif not fenced and STEP.match(line):
            n += 1
            digest.update(line.strip().encode('utf-8', 'replace') + b'\n')
    return n, digest.hexdigest()[:12]


def check(state, steps, repeat, tool):
    """(reasons, lines): why the session should look, as lines for its prompt."""
    now = time.time()
    with Lock(state):
        told = told_of(state)
        found = jobs(state)
        mark = marker(state)
        # what there is to say of the jobs: (key, what, line), each kind once per `repeat`
        about, held = [], False
        for key, rec, problem in found:
            if rec is None:
                about.append((key, 'damaged', f'the job record {job_path(state, key)} cannot be read ({problem}): '
                                              f'look at it, then: job done {key}'))
                continue
            st = job_state(rec, now)
            if st in ('running', 'starting') and not late(rec, st, now):
                held = True
            said = trouble(rec, st, now)
            if st in ('ended', 'stopped') and now - rec['ended_at'] > KEEP and (said is None or key in told['jobs']):
                os.remove(job_path(state, key))
                told['jobs'].pop(key, None)
                continue
            if said:
                about.append((key,) + said)
        waiting = (mark.get('wait_until') or 0) > now
        covered = mark.get('wait_set_at') or 0
        open_work, parts = [], []
        for r in mark['open']:
            since = r.get('since', now)
            # a wait covers what was open when it was set
            if held or (waiting and since <= covered):
                continue
            open_work.append(clip(f'open work: {r["text"]}') + f' (since {stamp(since)}, {span(now - since)})')
            parts.append(r['text'])
        for path in steps:
            try:
                n, digest = open_steps(path)
            except OSError as e:
                about.append((f'steps:{path}', 'unreadable', f'the steps file {path} cannot be read '
                                                             f'({e.strerror or e}): fix the guard wake\'s check'))
                continue
            if n and not held and not waiting:
                open_work.append(f'{n} open step{"" if n == 1 else "s"} in {path}')
                parts.append(f'{path}={digest}')
        keys = {key for key, _, _ in about}
        told['jobs'] = {k: v for k, v in told['jobs'].items() if k in keys}
        about = [a for a in about
                 if not (a[0] in told['jobs'] and told['jobs'][a[0]]['what'] == a[1]
                         and now - told['jobs'][a[0]]['at'] < repeat)]
        # open work told again and again with nothing changed: less and less often
        again = None
        if open_work:
            mark_key = hashlib.sha1('\n'.join(sorted(parts)).encode('utf-8', 'replace')).hexdigest()
            before = told.get('open') or {}
            times = before['n'] + 1 if before.get('key') == mark_key else 1
            if times > 2:
                gap = min(BACKOFF * 2 ** (times - 3), BACKOFF_MAX)
                if now - before['at'] < gap:
                    open_work = []
                else:
                    again = (f'(told {times} times with nothing changed: the next time comes later. Tick or end '
                             f'what is done, or wait.)')
        elif not (held or waiting):
            told.pop('open', None)
        # what fits the prompt is said, and only that counts as told
        said_jobs, said_open, used = [], [], 0
        for key, what, line in about:
            line = clip(line)
            if said_jobs and used + len(line) + 3 > ROOM:
                break
            said_jobs.append((key, what, line))
            used += len(line) + 3
        for line in open_work:
            if (said_jobs or said_open) and used + len(line) + 3 > ROOM:
                break
            said_open.append(line)
            used += len(line) + 3
        for key, what, _ in said_jobs:
            told['jobs'][key] = {'what': what, 'at': now}
        if said_open:
            told['open'] = {'key': mark_key, 'n': times, 'at': now}
        write(os.path.join(state, 'told.json'), told)
    reasons = [line for _, _, line in said_jobs] + said_open
    if not reasons:
        return [], []
    lines = [f'guard: {len(reasons)} thing{"" if len(reasons) == 1 else "s"} to look at']
    lines += ['- ' + line for line in reasons]
    if again and said_open:
        lines.append(again)
    if len(about) > len(said_jobs):
        lines.append(f'({len(about) - len(said_jobs)} more about jobs: they come with the next check.)')
    if len(open_work) > len(said_open):
        lines.append(f'({len(open_work) - len(said_open)} more of the open work: status lists it.)')
    lines.append(f'The commands: {tool} status | off | wait DURATION | job done NAME | job stop NAME | '
                 'job extend NAME DURATION')
    return reasons, lines


def fire(wake, text):
    """Fire the session's wake through the autocode command: (fired, why not)."""
    exe = shutil.which('autocode')
    if not exe:
        home = os.path.join(os.environ.get('AUTOCODE_HOME') or os.path.join(os.path.expanduser('~'), '.autocode'),
                            'bin', 'autocode')
        exe = home if os.path.exists(home) else None
    if not exe:
        return False, 'the autocode command is not on the PATH'
    gap = knob('AUTOCODE_GUARD_FIRE_GAP', 10)
    why = ''
    # a server that restarts is back in seconds: three tries
    for attempt in range(3):
        if attempt:
            time.sleep(gap)
        try:
            p = subprocess.run([exe, 'fire', 'me', wake, '--text', text], stdin=subprocess.DEVNULL,
                               capture_output=True, text=True, timeout=60)
        except (OSError, subprocess.SubprocessError) as e:
            why = str(e)
            continue
        if p.returncode == 0:
            return True, ''
        why = ' '.join((p.stderr or p.stdout or '').split())[-300:] or f'autocode fire: exit {p.returncode}'
    return False, why


def exited(child):
    """Whether the command has ended. Where the system allows, it is not reaped yet: its number, which is its
    group's too, then stays its own until what is left of the group was dealt with."""
    if hasattr(os, 'waitid') and os.path.isdir('/proc/self'):
        try:
            return os.waitid(os.P_PID, child.pid, os.WEXITED | os.WNOHANG | os.WNOWAIT) is not None
        except ChildProcessError:
            return True
    return child.poll() is not None


def supervise(state, name, run):
    """Runs a job's command, writes its end down and fires its wake. One process per start, detached."""
    path = job_path(state, name)

    def mine():
        # the record of this start: gone once the job was forgotten, another's once it was started again
        rec, _ = load_job(state, name)
        return rec if rec and rec.get('run') == run else None

    held = {'child': None, 'stop': None, 'session': False}

    def stop(signum, frame):
        # job stop says with a signal of its own that the session stops it; any other stop comes from outside
        if signum == signal.SIGUSR1:
            held['session'] = True
        if held['stop'] is None:
            held['stop'] = time.time()
            if held['child'] is not None:
                signal_group(held['child'].pid, signal.SIGTERM)

    for sig in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP, signal.SIGUSR1):
        signal.signal(sig, stop)
    # it says that it is there before the command starts: a start that was given up meanwhile runs nothing
    with Lock(state):
        rec = mine()
        if rec:
            rec.update(pid=os.getpid(), pid_token=proc_token(os.getpid()))
            write(path, rec)
    if not rec:
        return 2
    known = dict(rec)
    log = open(rec['log'], 'ab', buffering=0)

    def say(text):
        log.write(f'[guard] {text}\n'.encode('utf-8', 'replace'))

    say(f'job {name} started {stamp(time.time())}: {" ".join(shlex.quote(c) for c in rec["cmd"])}')
    error, child, code = None, None, 127
    # the command is started and written down in one go, under the lock: it never runs unrecorded
    with Lock(state):
        rec = mine()
        if not rec:
            return 2
        if held['stop'] is not None:
            error, code = 'stopped before its command started', 143
        else:
            try:
                # a process group of its own, so that a stop reaches everything it started
                child = held['child'] = subprocess.Popen(rec['cmd'], cwd=rec.get('cwd') or None,
                                                         stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                                                         start_new_session=True)
            except OSError as e:
                error = f'{rec["cmd"][0]}: {e.strerror or e}'
        rec.update(child_pid=child.pid if child else None, child_token=proc_token(child.pid) if child else '')
        write(path, rec)
        known = dict(rec)
    if child:
        if held['stop'] is not None:
            signal_group(child.pid, signal.SIGTERM)
        while not exited(child):
            if held['stop'] is not None and time.time() - held['stop'] > grace():
                signal_group(child.pid, signal.SIGKILL)
            time.sleep(0.1)
        if held['stop'] is not None:
            # a stop ends what the command started too: what took no TERM gets KILL when the grace is over
            while others(child.pid) and time.time() - held['stop'] < grace():
                time.sleep(0.1)
            if others(child.pid):
                signal_group(child.pid, signal.SIGKILL)
                end = time.time() + 5
                while others(child.pid) and time.time() < end:
                    time.sleep(0.1)
        rc = child.wait()
        # reaped: its number may be another's from here on
        held['child'] = None
        code = rc if rc >= 0 else 128 - rc
    stopped = False
    with Lock(state):
        rec = mine()
        if rec is None and not os.path.exists(path):
            # its record went while it ran (a file removed, a sync): back it comes, with its end
            rec = dict(known)
        if rec:
            stopped = held['session'] or bool(rec.get('stop_requested'))
            rec.update(ended_at=time.time(), exit=code, stopped=stopped)
            if error:
                rec['error'] = error
            write(path, rec)
            known = dict(rec)
    say(f'job {name} ended {stamp(time.time())}: exit {code}' + (f' ({error})' if error else ''))
    # stopped by the session itself: it knows, and its wake stays for a new start
    if known.get('wake') and not stopped:
        fired, why = fire(known['wake'], f'job {name} ended: exit {code}; log {known["log"]}')
        with Lock(state):
            rec = mine()
            if rec:
                rec.update(fired=fired, fire_error=why)
                write(path, rec)
        say(f'wake {known["wake"]} fired' if fired else f'wake {known["wake"]} could not be fired: {why}')
    return 0


def job_start(state, a, command, tool):
    if not NAME.fullmatch(a.name or ''):
        raise Refused(2, 'a job name takes letters, digits and - _ . : (at most 64, a letter or digit first)')
    if not command:
        raise Refused(2, 'no command: put it after --, like: job start --name build --deadline 1h -- make all')
    limit = some_time(a.deadline, '--deadline')
    cwd = os.path.abspath(a.cwd or '.')
    if not os.path.isdir(cwd):
        raise Refused(2, f'--cwd {cwd} is not a folder')
    log = os.path.abspath(a.log) if a.log else os.path.join(state, 'jobs', a.name + '.log')
    try:
        os.makedirs(os.path.dirname(log), exist_ok=True)
        errors = open(log, 'ab')
    except OSError as e:
        raise Refused(2, f'--log {log}: {e.strerror or e}') from None
    now = time.time()
    path = job_path(state, a.name)
    run = os.urandom(6).hex()
    with errors:
        with Lock(state):
            old, _ = load_job(state, a.name)
            if old:
                st = job_state(old, now)
                if st in ('running', 'starting'):
                    raise Refused(3, f'job {a.name} is still running (pid {old.get("pid")}): stop it first '
                                     f'({tool} job stop {a.name})')
                if st == 'elsewhere':
                    raise Refused(3, f'job {a.name} is written down as running on {old.get("machine")}: forget it '
                                     f'there, or here once you know it is over ({tool} job done {a.name})')
                if st == 'died':
                    # no second copy beside a command that still runs
                    if alive(old.get('child_pid'), old.get('child_token')):
                        raise Refused(3, f'the command of job {a.name} still runs as pid {old["child_pid"]}, '
                                         f'unwatched: {tool} job stop {a.name} ends it')
                    if half_started(old):
                        raise Refused(3, f'job {a.name} is gone, and {half_text(old)} Then: {tool} job done '
                                         f'{a.name}')
                    pgid = leftover(old)
                    if pgid:
                        raise Refused(3, f'job {a.name} is gone, but {left_text(pgid)} Then: {tool} job done '
                                         f'{a.name}')
            write(path, {'name': a.name, 'run': run, 'cmd': command, 'cwd': cwd, 'log': log, 'wake': a.wake or None,
                         'machine': machine(), 'boot': boot_id(), 'started_at': now, 'deadline_at': now + limit,
                         'pid': None, 'ended_at': None})
            forget_told(state, a.name)
        # detached from this command and its turn: no pipe of ours stays open in it
        sup = None
        try:
            sup = subprocess.Popen([sys.executable, os.path.abspath(__file__), '--state', state, '_supervise',
                                    a.name, run],
                                   stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=errors,
                                   start_new_session=True, close_fds=True)
            end = time.time() + knob('AUTOCODE_GUARD_START_WAIT', 10)
            while time.time() < end and sup.poll() is None:
                rec, _ = load_job(state, a.name)
                if rec and rec.get('run') == run and rec.get('pid'):
                    break
                time.sleep(0.05)
        except OSError:
            pass
        # the supervisor says that it is there before it starts the command, under the same lock: either it has
        # by now, or it finds no record when it comes to it, and runs nothing
        with Lock(state):
            rec, _ = load_job(state, a.name)
            there = bool(rec and rec.get('run') == run and rec.get('pid'))
            if not there and rec and rec.get('run') == run:
                os.remove(path)
        if not there:
            what = 'nothing of it runs'
            if sup is not None:
                try:
                    sup.wait(timeout=grace() + 15)
                except subprocess.TimeoutExpired:
                    what = f'its supervisor (pid {sup.pid}) has not ended yet, look at it'
            raise Refused(2, f'job {a.name} could not be started ({what}); see {log}')
        # a moment for its command's pid, for the line below
        end = time.time() + 5
        while 'child_pid' not in rec and time.time() < end:
            time.sleep(0.05)
            again, _ = load_job(state, a.name)
            if not (again and again.get('run') == run):
                break
            rec = again
    command_pid = f' (its command: pid {rec["child_pid"]})' if rec.get('child_pid') else ''
    print(f'job {a.name} started: pid {rec["pid"]}{command_pid}, log {log}')
    print(f'deadline {stamp(rec["deadline_at"])}: past it the guard says so')
    if a.wake:
        print(f'its end fires the wake {a.wake} with the exit code: that wake must exist (autocode_wake action=set '
              f'name={a.wake} trigger="fire-only once")')
    else:
        print('no wake: the guard tells its end at the next idle check')
    return 0


def job_stop(state, name, tool):
    path = job_path(state, name)
    now = time.time()
    with Lock(state):
        rec, problem = load_job(state, name)
        if problem:
            raise Refused(3, f'the record of job {name} cannot be read ({problem}): {tool} job done {name} removes it')
        if not rec:
            raise Refused(3, f'no job {name}')
        st = job_state(rec, now)
        if st == 'starting':
            raise Refused(3, f'job {name} is starting: try again in a moment')
        orphan = st == 'died' and alive(rec.get('child_pid'), rec.get('child_token'))
        if not orphan:
            if st == 'died' and half_started(rec):
                raise Refused(3, f'job {name} is gone, and {half_text(rec)} Then: {tool} job done {name}')
            pgid = leftover(rec) if st == 'died' else None
            if pgid:
                raise Refused(3, f'job {name} is gone, and {left_text(pgid)} Then: {tool} job done {name}')
            if st != 'running':
                raise Refused(3, f'job {name} is not running here ({label(rec, st, now)})')
            rec['stop_requested'] = True
            write(path, rec)
        forget_told(state, name)
    if orphan:
        # its supervisor is gone: the command and what it started are ended from here
        if not end_group(rec['child_pid'], grace()):
            raise Refused(3, f'the command of job {name} (pid {rec["child_pid"]}) did not end')
        with Lock(state):
            now_there, _ = load_job(state, name)
            if now_there and now_there.get('run') == rec.get('run'):
                now_there.update(ended_at=time.time(), exit=None, stopped=True)
                write(path, now_there)
        print(f'job {name}: its command (pid {rec["child_pid"]}) ended. Then: {tool} job done {name}')
        return 0
    try:
        os.kill(rec['pid'], signal.SIGUSR1)
    except OSError:
        pass
    end = time.time() + grace() + 15
    while time.time() < end:
        now_there, _ = load_job(state, name)
        rec = now_there or rec
        if rec.get('ended_at') is not None:
            break
        time.sleep(0.1)
    if rec.get('ended_at') is None:
        raise Refused(3, f'job {name} did not end in time (pid {rec.get("pid")}); look at {rec.get("log")}')
    print(f'job {name} stopped (exit {rec.get("exit")}).')
    if rec.get('wake'):
        print(f'Its wake {rec["wake"]} was not fired and is still set: cancel it (autocode_wake action=cancel '
              f'name={rec["wake"]}), or start the job again.')
    return 0


def job_extend(state, name, text):
    more = some_time(text, 'job extend')
    path = job_path(state, name)
    with Lock(state):
        rec, problem = load_job(state, name)
        if not rec:
            raise Refused(3, f'the record of job {name} cannot be read ({problem})' if problem else f'no job {name}')
        if rec.get('ended_at') is not None:
            raise Refused(3, f'job {name} has ended')
        rec['deadline_at'] = time.time() + more
        write(path, rec)
        forget_told(state, name)
    print(f'job {name}: deadline {stamp(rec["deadline_at"])}')
    return 0


def job_done(state, name, tool):
    path = job_path(state, name)
    now = time.time()
    with Lock(state):
        rec, problem = load_job(state, name)
        if not rec and not problem:
            raise Refused(3, f'no job {name}')
        if rec:
            st = job_state(rec, now)
            if st == 'starting':
                raise Refused(3, f'job {name} is starting: try again in a moment')
            if st == 'running':
                raise Refused(3, f'job {name} is still running (pid {rec.get("pid")}): {tool} job stop {name} '
                                 'ends it')
            if st == 'died' and alive(rec.get('child_pid'), rec.get('child_token')):
                raise Refused(3, f'the command of job {name} still runs as pid {rec["child_pid"]}: {tool} job stop '
                                 f'{name} ends it')
        died = bool(rec) and st == 'died'
        half = died and half_started(rec)
        pgid = leftover(rec) if died and not half else None
        os.remove(path)
        forget_told(state, name)
    if rec and st == 'elsewhere':
        print(f'job {name} forgotten here; whether it still runs on {rec.get("machine")} was not checked')
    else:
        print(f'job {name} forgotten' + (f' (its log stays: {rec.get("log")})' if rec else ' (a damaged record)'))
    if half:
        print(f'Mind: {half_text(rec)}')
    if pgid:
        print(f'Mind: {left_text(pgid)}')
    return 0


def rows(state):
    now = time.time()
    out = []
    for key, rec, problem in jobs(state):
        if rec is None:
            out.append({'name': key, 'state': 'damaged', 'label': f'damaged record ({problem})', 'late': False,
                        'exit': None, 'started_at': None, 'ended_at': None, 'deadline_at': None, 'wake': None,
                        'fired': None, 'machine': None, 'pid': None, 'child_pid': None,
                        'log': job_path(state, key), 'cmd': None})
            continue
        st = job_state(rec, now)
        out.append({'name': key, 'state': st, 'label': label(rec, st, now), 'late': late(rec, st, now),
                    'exit': rec.get('exit'), 'started_at': rec.get('started_at'), 'ended_at': rec.get('ended_at'),
                    'deadline_at': rec.get('deadline_at'), 'wake': rec.get('wake'), 'fired': rec.get('fired'),
                    'machine': rec.get('machine'), 'pid': rec.get('pid'), 'child_pid': rec.get('child_pid'),
                    'log': rec.get('log'), 'cmd': rec.get('cmd')})
    return out


def job_lines(found):
    if not found:
        return ['no jobs']
    lines = []
    for r in found:
        parts = [r['name'], r['label']]
        if r['state'] == 'damaged':
            lines.append(' | '.join(parts + [r['log']]))
            continue
        parts.append(f'started {stamp(r["started_at"])}')
        if r['deadline_at'] and r['ended_at'] is None:
            parts.append(f'deadline {stamp(r["deadline_at"])}')
        parts.append(f'wake {r["wake"]}' if r['wake'] else 'no wake')
        parts.append(f'log {r["log"]}')
        lines.append(' | '.join(parts))
    return lines


def status(state, as_json):
    now = time.time()
    mark = marker(state)
    last = read(os.path.join(state, 'check.json'))
    last = last if isinstance(last, dict) and sane(last.get('at')) else None
    found = rows(state)
    waiting = (mark.get('wait_until') or 0) > now
    if as_json:
        print(json.dumps({'state': state, 'open': mark['open'],
                          'wait_until': mark.get('wait_until') if waiting else None,
                          'wait_text': mark.get('wait_text') if waiting else None,
                          'jobs': found, 'last_check': last}, indent=1, ensure_ascii=False))
        return 0
    print('open work: ' + ('none' if not mark['open'] else ''))
    for r in mark['open']:
        print(f'- {r["text"]} (since {stamp(r.get("since", now))})')
    if waiting:
        why = f': {mark["wait_text"]}' if mark.get('wait_text') else ''
        print(f'waiting until {stamp(mark["wait_until"])}{why} (what was open when it was set is held back)')
    print('jobs:')
    for line in job_lines(found):
        print('- ' + line)
    if last:
        said = f'woke with {len(last.get("reasons") or [])} reason(s)' if last.get('woke') else 'nothing to tell'
        print(f'last check {stamp(last["at"])} ({span(now - last["at"])} ago): {said}')
    else:
        print('last check: none yet (is the guard wake set, with this tool as its check?)')
    return 0


def parser():
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument('--dir', default=argparse.SUPPRESS, help='the project folder (default: the current one)')
    common.add_argument('--state', default=argparse.SUPPRESS, help='the state folder, in place of the project\'s')
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument('--dir', default=None, help='the project folder (default: the current one)')
    ap.add_argument('--state', default=None, help='the state folder, in place of <pack>/.memory/guard/')
    sub = ap.add_subparsers(dest='command', metavar='{check,on,off,wait,status,job}')
    sub.required = True
    c = sub.add_parser('check', parents=[common], help='the guard wake\'s check: 0 with reasons, 1 when nothing')
    c.add_argument('--steps', action='append', default=[], metavar='FILE',
                   help='a file whose unticked [ ] list items are open work')
    c.add_argument('--repeat', default=REPEAT, help='tell a job\'s trouble once per this (default 1h)')
    c = sub.add_parser('on', parents=[common], help='mark open work')
    c.add_argument('text', nargs='*')
    c = sub.add_parser('off', parents=[common], help='end the open work, or one line of it')
    c.add_argument('text', nargs='*')
    c = sub.add_parser('wait', parents=[common], help='hold open work back for a time, at most 24h (or: wait off)')
    c.add_argument('time')
    c.add_argument('text', nargs='*')
    c = sub.add_parser('status', parents=[common], help='the open work, the jobs and the last check')
    c.add_argument('--json', action='store_true')
    job = sub.add_parser('job', parents=[common], help='background jobs that are watched')
    jsub = job.add_subparsers(dest='job_command')
    jsub.required = True
    c = jsub.add_parser('start', parents=[common], help='run a command detached, after --')
    c.add_argument('--name', required=True)
    c.add_argument('--deadline', required=True, help='how long it may run before the guard says so')
    c.add_argument('--wake', help='the fire-only once wake its end fires')
    c.add_argument('--log', help='where its output goes (default: in the state folder)')
    c.add_argument('--cwd', help='its folder (default: the current one)')
    c = jsub.add_parser('list', parents=[common], help='the jobs and their state')
    c.add_argument('--json', action='store_true')
    for name, text in (('stop', 'end a running job and what it started'), ('done', 'forget a job that is not running')):
        c = jsub.add_parser(name, parents=[common], help=text)
        c.add_argument('name')
    c = jsub.add_parser('extend', parents=[common], help='a new deadline, counted from now')
    c.add_argument('name')
    c.add_argument('time')
    c = sub.add_parser('_supervise', parents=[common])
    c.add_argument('name')
    c.add_argument('run')
    return ap


def command_of(argv):
    """The command a call names, with its second word for job: its first words that are no options."""
    found, value = [], False
    for word in argv:
        if value:
            value = False
        elif word == '--':
            break
        elif word in ('--dir', '--state'):
            value = True
        elif not word.startswith('-'):
            found.append(word)
            if found[0] != 'job' or len(found) == 2:
                break
    return found


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    given = list(argv)
    named = command_of(argv)
    command = []
    # only a job's command comes after --; elsewhere the words stay where they are
    if named == ['job', 'start'] and '--' in argv:
        cut = argv.index('--')
        argv, command = argv[:cut], argv[cut + 1:]
    try:
        a = parser().parse_args(argv)
    except SystemExit as e:
        if named[:1] == ['check'] and e.code not in (0, None):
            # any exit but 0 and 1 is a failed check, which the session never sees
            print(f'guard: the check could not run: it was called wrongly ({" ".join(given)})')
            return 0
        raise
    me = os.path.abspath(__file__)
    rel = os.path.relpath(me)
    tool = 'python3 ' + shlex.quote(me if rel.startswith('..') else rel)
    if a.state:
        tool += ' --state ' + shlex.quote(a.state)
    elif a.dir and a.dir != '.':
        tool += ' --dir ' + shlex.quote(a.dir)
    try:
        state = state_dir(a)
    except OSError as e:
        if a.command == 'check':
            print(f'guard: the check could not run: {e}')
            return 0
        print(f'guard: {e}', file=sys.stderr)
        return 2
    if a.command == 'check':
        if a.state and not os.path.isdir(state):
            # a mistyped folder would hide every mark for good: said, not created
            if os.path.exists(state):
                print(f'guard: the check could not run: --state {state} is not a folder.')
            else:
                print(f'guard: the check could not run: the state folder {state} does not exist. Is --state '
                      f'mistyped? Any other command of the guard creates it ({tool} status).')
            return 0
        try:
            reasons, lines = check(state, a.steps, duration(a.repeat), tool)
            write(os.path.join(state, 'check.json'), {'at': time.time(), 'woke': bool(reasons), 'reasons': reasons})
        except Exception as e:  # noqa: BLE001 - a guard that fails quietly is never seen again
            print(f'guard: the check could not run: {e}')
            return 0
        print('\n'.join(lines), end='\n' if lines else '')
        return 0 if reasons else 1
    try:
        if a.command == '_supervise':
            return supervise(state, a.name, a.run)
        os.makedirs(state, exist_ok=True)
        if a.command == 'status':
            return status(state, a.json)
        if a.command == 'job':
            if a.job_command == 'start':
                return job_start(state, a, command, tool)
            if a.job_command == 'list':
                found = rows(state)
                print(json.dumps(found, indent=1, ensure_ascii=False) if a.json else '\n'.join(job_lines(found)))
                return 0
            if a.job_command == 'stop':
                return job_stop(state, a.name, tool)
            if a.job_command == 'extend':
                return job_extend(state, a.name, a.time)
            return job_done(state, a.name, tool)
        text = ' '.join(a.text).strip()
        path = os.path.join(state, 'open.json')
        with Lock(state):
            mark = marker(state)
            if a.command == 'on':
                text = text or 'unfinished work'
                if text not in [r['text'] for r in mark['open']]:
                    mark['open'].append({'text': text, 'since': time.time()})
                said = f'guard on: {text} ({len(mark["open"])} open)'
            elif a.command == 'off':
                if text:
                    if text not in [r['text'] for r in mark['open']]:
                        raise Refused(3, f'no open line {text!r}; the lines: '
                                         + ('; '.join(r['text'] for r in mark['open']) or 'none'))
                    mark['open'] = [r for r in mark['open'] if r['text'] != text]
                    said = f'guard: ended {text} ({len(mark["open"])} open)'
                else:
                    mark = {'open': []}
                    said = 'guard off: no open work'
            elif a.time == 'off':
                for key in ('wait_until', 'wait_set_at', 'wait_text'):
                    mark.pop(key, None)
                said = 'guard: no wait'
            else:
                seconds = some_time(a.time, 'wait')
                if seconds > MAX_WAIT:
                    raise Refused(2, f'wait: at most 24h (got {a.time}). Work that rests longer is not open: end it '
                                     'with off, and mark it again when it goes on')
                now = time.time()
                mark.update(wait_until=now + seconds, wait_set_at=now, wait_text=text)
                said = (f'guard waits until {stamp(mark["wait_until"])}: what is open now is not told before then '
                        '(work marked later is)')
            write(path, mark)
            forget_told(state)
        print(said)
        return 0
    except Refused as e:
        print(f'guard: {e}', file=sys.stderr)
        return e.code
    except OSError as e:
        print(f'guard: {e}', file=sys.stderr)
        return 2


if __name__ == '__main__':
    sys.exit(main())
