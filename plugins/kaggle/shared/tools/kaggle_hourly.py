# rev. 5

"""kaggle_hourly: one scripted hourly pass of the read-only Kaggle checks, printed as flags.

An agent working a competition checks it every hour. This tool runs those reads
in one call and prints one line per check, marked FLAG where something needs a
decision, so the agent reads a few lines instead of every source, then a status
block to paste into its status message. It wakes nothing and schedules nothing:
the agent runs it at its hourly pass, woken by its in-session clock (no daemon or
host scheduler).

    python3 <plugin-dir>/tools/kaggle_hourly.py <slug> [--vs SCORE] [--status FILE] [--jump N%|N] [--json]

Run it from the project root or task folder. The reads go through the tools
beside this file and the gateway (--gateway; default the kaggle.py beside this
file), read-only and unstamped (KAGGLE_SHARE_QUIET=1):

  review     kaggle_presubmit.py's reads and comparison, not kept as a check
             (so --ack still records only a check the agent ran and read): the
             forum check, the competition pages, the public notebooks and a
             board snapshot. FLAG: a host post or a page change since the last
             review; the other triggers are listed.
  board      the newest full snapshot: teams and the #1. FLAG: a jump since
             the snapshot the last pass saw (else the one before): a team in
             the top 10 improved by at least --jump (default 2% of its earlier
             score; a plain number is in score units), or entered the top 10
             from below place 50 (or from off the board)
  notebooks  new or re-scored public notebooks since the list before. FLAG:
             one in the top 15 of the list, or at or better than --vs
  forum      topics the forum watch found and has not had shown and committed
             (kaggle_forum.py show, then commit). FLAG: any
  subs       `competitions submissions <slug>`: those still scoring. FLAG: a
             score landed, or a submission errored, since the last pass
  kernels    `kaggle_share.py status --json`: queued and running kernels, this
             project's and the others'. FLAG: a run of this project's ended
             since the last pass
  gpu        the account's GPU week, and this project's lease hours against
             its budget
  status     the project's status page (kaggle_status.py): the status JSON
             (--status, default reports/status.json) and its page,
             reports/status.pdf when the reporting plugin is attached, else
             reports/html/status.html. FLAG: the JSON missing or unreadable,
             the page missing, the JSON changed after the last build, the page
             built before the newest score landed, or more than 2 hours old; a
             project still on submissions/live-plan.json without the JSON: the
             live plan is retired; in a git work tree, a file a build writes
             (the page, its HTML, the shorter-chart try) that git would not
             ignore (`git check-ignore`). A task folder without the JSON is not
             checked
  git        the project's own repos: its root when that is its own git work
             tree, and its source/ when that is one (a project whose code repo
             sits there): the commits on each branch that its upstream does not
             have (git's own record of the remote; nothing is fetched). FLAG:
             the oldest waits over an hour (push per the project's rules). No
             upstream: said, not flagged
  agents     `solaris.tools.ai_spend --detail` since the last pass (the last
             hour on a first pass), run as kaggle_status.py runs ai_spend: the
             master's responses with their models and its effort mix (responses
             per level, always shown), the workers and the hook runs. FLAG: a
             worker or the master on a model outside the allowed list
             (claude-opus-5-5, claude-sonnet-5-5, claude-haiku-*; the pack's
             "kaggle.allowed_models" replaces it), a failed hook run, and the
             master at a level other than the pack's "kaggle.master_effort"
             when the pack sets one (the effort is the owner's choice per
             session: no level is assumed, so unset means no effort flag)
  spend      the day's spend, each against its own limit, no total: Claude
             (`ai_spend --today`) against "ai.daily_budget_usd", each category
             of the cost ledger <pack>/.memory/spend.jsonl, and Brev (the
             nvidia-brev plugin's <pack>/.memory/brev-costs.md: each closed
             instance's cost spread over its life, and each instance its
             TOTAL row notes as running or stopped at its $/h rate from its
             ISO start time) against "brev.daily_limit_usd". FLAG: a limit
             reached; with a limit, a running note it cannot count
  browsers   one `ps` read: this project's browserctl browsers and their age,
             orphaned report-render Chromes (their render.js gone) and a count
             of the other browsers, never flagged. FLAG: an orphaned render
             Chrome, or a browser of this project's up more than 2 hours
  A read that failed is a FLAG too. A Kaggle read answered with HTTP 429 (Too
  Many Requests, a rate limit, or 429 beside HTTP, Client Error or status; a bare
  429 is not one) stops the pass's Kaggle reads: one `rate-limited` FLAG line
  names the read and the checks skipped after it, instead of a FLAG per read.

The git, agents and spend lines need an ai-pack (a project, not a task folder);
agents and spend also a Solaris checkout above the project. Settings come from
<pack>/.memory/config.json, else <pack>/defaults.json; days are the owner's
("owner.timezone", as ai_spend reads it).

Then the STATUS block, one line each, n/a where the tool cannot tell: pick
(today's slots used of the status JSON's "daily_slots", pending submissions,
the status JSON's "pick" text), score (our best public score, the medal lines
of the newest board snapshot), rank, compute (the machines of
<pack>/.memory/hosts.json, the nearest booking end in lease-ends.json, the
machines other projects share with this one from resource-sharing's seen list
with their nearest planned end, the Kaggle GPU week), stopped (this project's kernel runs that ended since the
last pass; claims live on the hosts), spend (the spend line), for you (the
status JSON's open questions and owner actions, and the number of FLAG lines).
--json prints the whole pass, block included, as one JSON object.

The first pass has no earlier one to compare submissions and kernel runs with.
State: <context>/__data/kaggle/<slug>/hourly/last.json (the submissions with the
time each score was first seen, this project's running kernels, the board
snapshot the pass saw, and the board snapshot and notebook list it read fresh,
which kaggle_presubmit.py reuses while they are young). Exit codes: 0 nothing
needs a decision, 10 a FLAG was raised, 1 error, 2 bad usage. Stdlib only.
"""

import argparse
import fnmatch
import importlib.util
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

TOOLS = Path(__file__).resolve().parent
EXIT_FLAG = 10
# a notebook this high in the score-ordered list is worth a look; a status page older than this is stale
NB_TOP = 15
STALE_HOURS = 2.0
# a build saves the JSON's raised rev just after writing the page: a JSON newer by less is no edit
PLAN_SLACK = 60
# the files a status build writes, with the shorter-chart try a killed build can leave: they hold private operations
# data, so in a git work tree git must ignore each
PAGE_FILES = ("reports/status.pdf", "reports/html/status.html", "reports/status-short.pdf",
              "reports/html/status-short.html")
GIT_HINT = ("keep the page out of git: add reports/status*.pdf and reports/html/status*.html to the project's "
            ".gitignore (and git rm --cached any already committed)")
# a browser of this project's up longer than this is worth a look: stop it unless a running task needs it
BROWSER_HOURS = 2.0
# classes of review triggers that need a decision now, not only before the next submission
URGENT = ("host", "pages")
SCORING_DONE = ("COMPLETE", "ERROR")
# a board jump: a team in the top JUMP_TOP that improved by --jump, or came from below place JUMP_FROM
JUMP_DEFAULT, JUMP_TOP, JUMP_FROM = ("%", 2.0), 10, 50
# commits waiting longer than this for a push are a flag
UNPUSHED_HOURS = 1.0
# the models agents may run on (the kaggle rule's models by job), as fnmatch patterns; a pack replaces them with
# MODELS_KEY
ALLOWED_MODELS = ("claude-opus-5-5", "claude-sonnet-5-5", "claude-haiku-*")
MODELS_KEY, BREV_KEY, ZONE_KEY = "kaggle.allowed_models", "brev.daily_limit_usd", "owner.timezone"
# the master's effort is the owner's choice per session: only a level the pack sets here is checked
EFFORT_KEY = "kaggle.master_effort"
# a model id's family and version without a date, [1m] or provider prefix, as ai_spend prices it
MODEL_RE = re.compile(r"claude-[a-z]+-\d{1,2}(?:-\d{1,2})?(?!\d)")
# the agents check on a first pass looks back this far
FIRST_HOURS = 1.0
# the Brev ledger: a time in a cell (UTC unless it names a zone), its cost (the first dollar amount), and the hourly
# rate a running or stopped instance's note gives
BREV_TIME = re.compile(r"\d{4}-\d\d-\d\d[T ]\d\d:\d\d(?::\d\d(?:\.\d+)?)?(?:Z|[+-]\d\d:?\d\d)?")
BREV_USD = re.compile(r"\$\s*(\d[\d,]*(?:\.\d+)?)")
BREV_RATE = re.compile(r"\$\s*(\d[\d,]*(?:\.\d+)?)\s*(?:/\s*h(?:our|r)?\b|(?:per|an)\s+hour\b)", re.I)
BLOCK = ("pick", "score", "rank", "compute", "stopped", "spend", "for you")
NA = "n/a"


class HourlyError(Exception):
    pass


def load_tool(name):
    """A tool module from this folder (kaggle_presubmit, kaggle_status), loaded by path."""
    path = TOOLS / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"kaggle_hourly_{name}", path)
    if spec is None or spec.loader is None:
        raise HourlyError(f"cannot load {path}")
    mod = importlib.util.module_from_spec(spec)
    # keep __pycache__ out of the plugin folder
    sys.dont_write_bytecode = True
    try:
        spec.loader.exec_module(mod)
    except Exception as e:
        raise HourlyError(f"cannot load {path}: {e}") from None
    return mod


def _run(cmd, cwd, timeout=600):
    # KAGGLE_SHARE_QUIET: a monitoring read, so kaggle_share's activity stamp skips it
    try:
        p = subprocess.run(cmd, cwd=str(cwd), stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                           encoding="utf-8", errors="replace", timeout=timeout,
                           env={**os.environ, "KAGGLE_SHARE_QUIET": "1"})
    except subprocess.TimeoutExpired:
        return 124, "", "timed out"
    return p.returncode, p.stdout, p.stderr


def ago(t, now):
    s = max(0.0, (now - t).total_seconds())
    return f"{s / 60:.0f} min" if s < 5400 else f"{s / 3600:.1f} h"


def usd(v):
    return f"${v:,.2f}"


def shared_hosts(path):
    # the hosts other projects share with this one, from resource-sharing's seen list (as of the last
    # `hostclaims.py shared --ack`); [] when there is none or it cannot be read
    try:
        doc = json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return []
    hosts = doc.get("hosts") if isinstance(doc, dict) else None
    if not isinstance(hosts, dict):
        return []
    return [h for _, h in sorted(hosts.items()) if isinstance(h, dict)]


def shared_part(shared, now, P):
    # "N shared machines from <owners> (names); nearest shared end ..." for the compute field
    names = [str(h.get("name") or "?") for h in shared]
    owners = sorted({str(h.get("owner") or h.get("project") or "?") for h in shared})
    text = (plural(len(names), "shared machine") + " from " + ", ".join(owners) + " (" + ", ".join(names[:4])
            + (f" +{len(names) - 4} more" if len(names) > 4 else "") + ")")
    ends = []
    for h in shared:
        try:
            t = datetime.fromisoformat(str(h.get("planned_end")).replace("Z", "+00:00"))
        except ValueError:
            continue
        if t.tzinfo is None:
            t = t.replace(tzinfo=timezone.utc)
        if t > now:
            ends.append((t, str(h.get("name") or "?")))
    if ends:
        t, n = min(ends)
        text += f"; nearest shared end {n} {P.local(t)} ({(t - now).total_seconds() / 3600:.0f} h left)"
    return text

def plural(n, word):
    return f"{n} {word}{'' if n == 1 else 's'}"


def read_submissions(P, slug, gw, root, run=_run):
    """The team's submissions, newest first as the CLI lists them: ([{key, status, score, what, date}], error)."""
    code, out, err = run([sys.executable, str(gw), "competitions", "submissions", slug, "--format", "json"], root)
    if code != 0:
        return None, f"competitions submissions failed (exit {code}): {P.tail(err or out)}"
    # CLI 2.2.4 prints this instead of an empty JSON list
    rows = [] if "No submissions found" in out else P.first_json(out, "[")
    if not isinstance(rows, list):
        return None, f"competitions submissions printed no list: {P.tail(out)}"
    subs = []
    for r in rows:
        if isinstance(r, dict):
            when = P.parse_time(r.get("date"))
            subs.append({"key": str(r.get("ref") or f"{r.get('date')} {r.get('description')}"),
                         "status": str(r.get("status") or "").split(".")[-1].upper(),
                         "score": str(r.get("publicScore") or "").strip(),
                         "what": P.one_line(r.get("description") or r.get("fileName") or "?", 40),
                         "date": P.iso(when) if when else None})
    return subs, None


def read_share(P, gw, root, run=_run):
    """kaggle_share.py status --json: (the sharing view, error)."""
    code, out, err = run([sys.executable, str(TOOLS / "kaggle_share.py"), "status", "--json", "--gateway", str(gw)],
                         root)
    view = P.first_json(out, "{") if code == 0 else None
    if not isinstance(view, dict) or not isinstance(view.get("projects"), dict):
        return None, f"kaggle_share.py status failed (exit {code}): {P.tail(err or out)}"
    return view, None


def status_page(P, root, status_path, now, given=False, scored=None):
    """(line, flag or None) for the status page (kaggle_status.py), or None for a folder that needs none: a task
    folder without a status JSON, unless --status named one. In a git work tree, a page file git would not ignore
    is a flag too. scored: (when the newest score landed, what it is), to flag a page built before it."""
    got = page_state(P, root, status_path, now, given, scored)
    loose = not_ignored(root) if got else None
    if not loose:
        return got
    line, flag = got
    return (f"{line}; not ignored by git: {', '.join(P._rel(root / x) for x in loose)}",
            "; ".join(x for x in (flag, GIT_HINT) if x))


def not_ignored(root, paths=PAGE_FILES):
    """Those of paths (relative to root) that git would not ignore, a tracked one included; None outside a git work
    tree or when git does not run."""
    try:
        code, out, _err = _run(["git", "-C", str(root), "check-ignore", "--", *paths], root, timeout=60)
    except OSError:
        return None
    # check-ignore prints the ignored paths and exits 0 for some, 1 for none, 128 outside a work tree
    if code not in (0, 1):
        return None
    ignored = set(out.splitlines())
    return [p for p in paths if p not in ignored]


def page_state(P, root, status_path, now, given, scored=None):
    """(line, flag or None) for the status JSON and its page, or None for a folder that needs none (see
    status_page)."""
    try:
        pack = P.pack_of(root)
    except P.PresubmitError:
        pack = None
    if not status_path.is_file():
        old = root / "submissions" / "live-plan.json"
        if old.is_file() and not given:
            return f"{P._rel(old)} and no {P._rel(status_path)}", "live plan retired: move to reports/status.json"
        if pack or given:
            return (f"{P._rel(status_path)} missing",
                    "no status page: write reports/status.json, then build it (kaggle_status.py)")
        return None
    status = P.read_json(status_path, None)
    if not isinstance(status, dict):
        return (f"{P._rel(status_path)} is not a status JSON",
                "unreadable: fix the status JSON, then rebuild the page (kaggle_status.py)")
    # the PDF is what the owner reads; without the reporting plugin the tool writes the HTML only
    reporting = pack is not None and (pack / "plugins" / "reporting" / "assets" / "render.sh").is_file()
    page = root / "reports" / "status.pdf" if reporting else root / "reports" / "html" / "status.html"
    if not page.is_file():
        return f"{P._rel(page)} not built yet", "never built: build it (kaggle_status.py)"
    built = datetime.fromtimestamp(page.stat().st_mtime, timezone.utc)
    line = f"{P._rel(page)} built {ago(built, now)} ago, rev {status.get('rev')}"
    if status_path.stat().st_mtime > page.stat().st_mtime + PLAN_SLACK:
        return line, "the status JSON changed after the last build: rebuild the page (kaggle_status.py)"
    if scored and built < scored[0]:
        return line, f"the page was built before the newest score landed ({scored[1]}): rebuild it " \
                     "(kaggle_status.py --keep-rev)"
    if (now - built).total_seconds() > STALE_HOURS * 3600:
        return line, f"the page is {ago(built, now)} old: rebuild it (kaggle_status.py --keep-rev)"
    return line, None


def etime_seconds(text):
    """Seconds from an elapsed time as `ps` prints it, [[dd-]hh:]mm:ss, else None."""
    m = re.fullmatch(r"(?:(?:(\d+)-)?(\d+):)?(\d+):(\d+)", text.strip())
    if not m:
        return None
    days, hours, mins, secs = (int(x or 0) for x in m.groups())
    return ((days * 24 + hours) * 60 + mins) * 60 + secs


def read_processes(P, root):
    """The process table from one `ps` read: ([(pid, parent pid, seconds up, command)], error). KAGGLE_HOURLY_PS_FILE
    names a file of that ps output to read instead (the tests)."""
    fake = os.environ.get("KAGGLE_HOURLY_PS_FILE")
    if fake:
        try:
            out = Path(fake).read_text(encoding="utf-8", errors="replace")
        except OSError as e:
            return None, f"cannot read {fake}: {e.strerror or e}"
    else:
        try:
            code, out, err = _run(["ps", "-A", "-ww", "-o", "pid=,ppid=,etime=,command="], root, timeout=60)
        except OSError as e:
            return None, f"ps did not run: {e.strerror or e}"
        if code != 0:
            return None, f"ps failed (exit {code}): {P.tail(err or out)}"
    procs = []
    for row in out.splitlines():
        cols = row.split(None, 3)
        up = etime_seconds(cols[2]) if len(cols) == 4 and cols[0].isdigit() and cols[1].isdigit() else None
        if up is not None:
            procs.append((int(cols[0]), int(cols[1]), up, cols[3].rstrip()))
    return procs, None


def sanitize_id(raw):
    # browserctl.py's project and profile ids: lowercase, each run of other characters one "-", none at either end
    return re.sub(r"[^a-z0-9-]+", "-", str(raw).strip().lower()).strip("-")


def browser_project(P, root):
    """This project's id in browserctl, as browserctl.py derives it when run in root: $BROWSERCTL_PROJECT, else the
    ai-pack's project slug or name, else the name of the nearest folder at or above root with a .git, else root's."""
    env = sanitize_id(os.environ.get("BROWSERCTL_PROJECT") or "")
    if env:
        return env
    try:
        pack = P.pack_of(root)
    except P.PresubmitError:
        pack = None
    manifest = P.read_json(pack / "manifest.json", None) if pack else None
    project = manifest.get("project") if isinstance(manifest, dict) else None
    if isinstance(project, dict):
        name = sanitize_id(project.get("slug") or project.get("name") or "")
        if name:
            return name
    return sanitize_id(next((d for d in (root, *root.parents) if (d / ".git").exists()), root).name)


def browsers(P, root, now):
    """(line, flag or None) for the browsers running on this machine (see browser_line)."""
    procs, err = read_processes(P, root)
    if err:
        return "not read", f"the read failed: {err}"
    # browserctl.py keeps its profiles in $BROWSERCTL_HOME, else ~/.solaris/browserctl
    bhome = Path(os.path.expanduser(os.environ.get("BROWSERCTL_HOME") or "~/.solaris/browserctl"))
    return browser_line(procs, now, Path(os.path.expanduser("~")), bhome, browser_project(P, root))


def browser_line(procs, now, home, bhome, project):
    """(line, flag or None) for the browsers in procs (see read_processes): this project's browserctl profiles
    (<bhome>/profiles/<project>/<profile>) with their age, the report renderer's Chromes (a profile under
    <home>/.solaris/tmp/render-*) whose render.js is gone, and a count of the others, which are never flagged."""
    commands = {pid: cmd for pid, _ppid, _up, cmd in procs}
    render = f"--user-data-dir={home / '.solaris' / 'tmp'}/render-"
    ours = re.compile(re.escape(f"--user-data-dir={bhome / 'profiles' / project}/") + r"([^/\s]+)") if project else None
    mine, orphans, rendering, others = [], [], 0, 0
    for pid, ppid, up, cmd in procs:
        # only a browser's main process counts; its helpers carry --type=
        if "--user-data-dir=" not in cmd or "--type=" in cmd:
            continue
        m = ours.search(cmd) if ours else None
        if render in cmd:
            # a live render's Chrome is a child of render.js; an orphan was re-parented to init or systemd --user
            if "render.js" in commands.get(ppid, ""):
                rendering += 1
            else:
                orphans.append(f"pid {pid}, up {ago(now - timedelta(seconds=up), now)}")
        elif m:
            mine.append((m.group(1), up, "--headless" in cmd))
        else:
            others += 1
    parts = []
    if mine:
        parts.append("this project: " + ", ".join(f"{name} up {ago(now - timedelta(seconds=up), now)}"
                                                  + (" (headless)" if headless else "")
                                                  for name, up, headless in sorted(mine)))
    if orphans:
        parts.append(f"{len(orphans)} orphaned report-render Chrome{'s' if len(orphans) > 1 else ''} ("
                     + "; ".join(orphans[:4]) + (f"; +{len(orphans) - 4} more" if len(orphans) > 4 else "") + ")")
    if rendering:
        parts.append(f"{rendering} report render{'s' if rendering > 1 else ''} running")
    if others:
        parts.append(f"{others} other browser{'s' if others > 1 else ''} not this project's")
    long_up = any(up > BROWSER_HOURS * 3600 for _name, up, _headless in mine)
    flag = ("stop what no running task needs: browserctl.py stop --profile <name>; the reporting plugin's render.sh "
            "--reap closes an orphaned render Chrome") if orphans or long_up else None
    return "; ".join(parts) or "none running", flag


def pack_setting(P, pack, key):
    """(value, the file that sets it) of a pack setting: <pack>/.memory/config.json wins over <pack>/defaults.json;
    (None, None) where neither sets it."""
    for f in (pack / ".memory" / "config.json", pack / "defaults.json"):
        cfg = P.read_json(f, None)
        if isinstance(cfg, dict) and key in cfg:
            return cfg[key], f
    return None, None


def owner_day(P, pack, now):
    """The start of the owner's day holding now: "owner.timezone" in the pack's settings, else this machine's
    zone."""
    name, _src = pack_setting(P, pack, ZONE_KEY)
    try:
        tz = ZoneInfo(name) if isinstance(name, str) and name else None
    except (ValueError, OSError):
        tz = None
    t = now.astimezone(tz) if tz else now.astimezone()
    return t.replace(hour=0, minute=0, second=0, microsecond=0)


def git_line(P, root, now):
    """(line, flag or None) for the commits not yet pushed in the project's own repos: its root when that is its own
    git work tree, and its source/ when that is one (a project whose code repo sits there). None where git does not
    run or neither folder is in a work tree. The upstream is git's own record of the remote: nothing is fetched."""
    root = Path(root)

    def git(where, *args):
        try:
            code, out, _err = _run(["git", "-C", str(where), *args], root, timeout=60)
        except OSError:
            return None
        return out.strip() if code == 0 else None

    parts, late, outside = [], [], False
    for where, name in ((root, ""), (root / "source", "source/")):
        top = git(where, "rev-parse", "--show-toplevel") if where.is_dir() else None
        if top is None:
            continue
        if Path(top).resolve() != where.resolve():
            # a source/ inside the root's repo is that repo; a root inside another repo has none of its own
            outside = outside or where == root
            continue
        label = f"{name}: " if name else ""
        branch = git(where, "symbolic-ref", "--short", "-q", "HEAD")
        up = git(where, "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}") if branch else None
        if not branch:
            parts.append(f"{label}a detached HEAD: unpushed commits not counted")
            continue
        if not up:
            parts.append(f"{label}{branch} has no upstream branch: unpushed commits not counted")
            continue
        stamps = [int(x) for x in (git(where, "log", "--format=%ct", "@{u}..HEAD") or "").split() if x.isdigit()]
        if not stamps:
            parts.append(f"{label}{branch}: every commit pushed to {up}")
            continue
        oldest = datetime.fromtimestamp(min(stamps), timezone.utc)
        parts.append(f"{label}{branch}: {plural(len(stamps), 'commit')} not pushed to {up}, the oldest from "
                     f"{P.local(oldest)} ({ago(oldest, now)} ago)")
        if (now - oldest).total_seconds() > UNPUSHED_HOURS * 3600:
            late.append(name or "the project root")
    if not parts:
        return ("no git repository of the project's own (its root or source/): nothing to check", None) if outside \
            else None
    flag = (f"commits unpushed for over an hour ({', '.join(late)}): push per the project's rules (where none allows "
            "a push, ask the owner)") if late else None
    return "; ".join(parts), flag


def allowed_models(P, pack):
    """(the allowed model patterns, a problem with the pack's setting or None)."""
    value, src = pack_setting(P, pack, MODELS_KEY)
    if value is None:
        return ALLOWED_MODELS, None
    if isinstance(value, list) and value and all(isinstance(x, str) and x.strip() for x in value):
        return tuple(x.strip() for x in value), None
    return ALLOWED_MODELS, f'{MODELS_KEY} in {P._rel(src)} must be a list of model names (* allowed): fix it'


def expected_effort(P, pack):
    """(the master's effort level the pack expects, else None; a problem with the setting or None). The effort is the
    owner's choice per session, so no level is assumed: without the setting no effort is checked."""
    value, src = pack_setting(P, pack, EFFORT_KEY)
    if value is None:
        return None, None
    if isinstance(value, str) and value.strip():
        return value.strip(), None
    return None, f"{EFFORT_KEY} in {P._rel(src)} must be an effort level, as the transcripts record it: fix it"


def model_ok(model, allowed):
    """Whether a model id matches an allowed pattern, as written or by its family (a date, [1m] or a provider
    prefix aside)."""
    names = {str(model), str(model).lower()}
    m = MODEL_RE.search(str(model).lower())
    if m:
        names.add(m.group(0))
    return any(fnmatch.fnmatchcase(n, pat) for n in names for pat in allowed)


def counts(c):
    return ", ".join(f"{k} {v}" for k, v in c.items()) or "none recorded"


def take_detail(v):
    """The "detail" object of an ai_spend report, checked for the fields the agents line reads."""
    d = v["detail"]
    main, workers, hooks = d["main"], d["workers"], d["hooks"]
    if not (isinstance(main.get("models"), dict) and isinstance(main.get("effort"), dict) and isinstance(workers, list)
            and isinstance(hooks.get("failures"), list)):
        raise ValueError("not an ai_spend detail report")
    return d


def agents_line(P, S, root, pack, since, now):
    """(line, flag or None) of who answered for the project since the last pass (ai_spend --detail). The master's
    effort mix is always shown; it is judged only against a level the pack sets (EFFORT_KEY)."""
    if not S.solaris_root(str(root)):
        return f"{NA}: no Solaris checkout above the project (solaris.tools.ai_spend)", None
    allowed, problem = allowed_models(P, pack)
    expected, bad_effort = expected_effort(P, pack)
    d = S.ai_spend(str(root), ["--since", since.isoformat(), "--detail"], take_detail)
    if d is None:
        return "not read", "the read failed: solaris.tools.ai_spend --detail gave no report"
    main, workers, hooks = d["main"], d["workers"], d["hooks"]
    turns = int(main.get("turns") or 0)
    by_model = {}
    for w in workers:
        by_model[str(w.get("model"))] = by_model.get(str(w.get("model")), 0) + 1
    line = (f"since {P.local(since)}: master {plural(turns, 'response')} "
            f"({counts(main['models'])}; effort {counts(main['effort'])}"
            + (f"; expected {expected}" if expected else "") + f"); {plural(len(workers), 'worker')}"
            + (f" ({counts(by_model)})" if workers else "")
            + f"; hooks {plural(int(hooks.get('runs') or 0), 'run')}, {int(hooks.get('failed') or 0)} failed")
    flags = [x for x in (problem, bad_effort) if x]
    off = [w for w in workers if not model_ok(w.get("model"), allowed)]
    if off:
        shown = "; ".join(f"{w.get('model')} (session {w.get('session')}, agent {w.get('agent') or '?'})"
                          for w in off[:3]) + (f"; +{len(off) - 3} more" if len(off) > 3 else "")
        flags.append(f"{plural(len(off), 'worker')} on a model outside the allowed list: {shown}; pass the model on "
                     "every launch")
    off_main = [m for m in main["models"] if not model_ok(m, allowed)]
    if off_main:
        flags.append(f"the master answered on {', '.join(off_main)}, outside the allowed list")
    # a response without a recorded effort is never judged
    other = {e: n for e, n in main["effort"].items() if str(e).strip().lower() != expected.lower()} if expected else {}
    if other:
        flags.append(f"the master ran at another effort than the pack's {EFFORT_KEY} ({expected}): {counts(other)} of "
                     f"{plural(turns, 'response')}; tell the owner, whose choice the effort is")
    failed = int(hooks.get("failed") or 0)
    if failed:
        last = hooks["failures"][-1] if hooks["failures"] else {}
        flags.append(f"{plural(failed, 'hook run')} failed (newest: {last.get('event') or '?'} at "
                     f"{P.local(P.parse_time(last.get('ts')))}: {P.one_line(last.get('text'), 80)}): fix the hook")
    return line, "; ".join(flags) or None


def take_today(v):
    """(Claude's spend today, the daily budget or None, the start of the owner's day or None, problems) of an
    ai_spend --today report."""
    spent = sum(float(r["usd"]) for r in v["rows"])
    budgets = [b for b in v.get("budgets") or [] if isinstance(b, dict)]
    limit = float(budgets[0]["daily_budget_usd"]) if budgets else None
    if budgets and isinstance(budgets[0].get("today_usd"), (int, float)):
        spent = float(budgets[0]["today_usd"])
    return spent, limit, v.get("since"), [str(x) for x in v.get("problems") or []]


def read_brev(path):
    """(closed, running, bad) from the nvidia-brev plugin's cost ledger, or (None, [], 0) without the file. The ledger
    is the Markdown table brev-run.skill.md keeps (Instance | Type | $/h | Created (UTC) | Deleted (UTC) | Hours | Cost
    | Purpose / outcome), a row per instance appended at its deletion, then a TOTAL row whose outcome cell notes the
    instances still running or stopped.
    closed: [(created, deleted, usd)] of the instance rows: a time is the first ISO time in its cell (UTC unless it
    names a zone), a cost the first dollar amount in its cell; a row that cost nothing is skipped.
    running: [(start or None, stop or None, usd an hour)] of the TOTAL row's notes (see open_notes).
    bad: the instance rows that could not be read."""
    try:
        text = Path(path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None, [], 0
    rows, bad, cols, total = [], 0, None, ""
    for raw in text.splitlines():
        s = raw.strip()
        if not s.startswith("|"):
            cols = None
            continue
        cells = [c.strip() for c in s.strip("|").split("|")]
        low = [c.lower().strip("*_ ") for c in cells]
        if cols is None:
            at = [next((i for i, c in enumerate(low) if c.startswith(w)), None) for w in ("created", "deleted", "cost")]
            if None not in at:
                outcome = next((i for i, c in enumerate(low) if "outcome" in c or c.startswith("purpose")), None)
                cols = (*at, outcome)
            continue
        if all(re.fullmatch(r":?-+:?", c) for c in cells if c):
            continue
        if low[0].startswith("total"):
            total = cells[cols[3]] if cols[3] is not None and cols[3] < len(cells) else cells[-1]
            continue
        m = BREV_USD.search(cells[cols[2]]) if cols[2] < len(cells) else None
        cost = float(m.group(1).replace(",", "")) if m else None
        # a row that cost nothing (an instance that never ran, often without a deletion time) places nothing
        if cost == 0:
            continue
        times = [BREV_TIME.search(cells[i]) if i < len(cells) else None for i in cols[:2]]
        created, deleted = (parse_utc(t.group(0)) if t else None for t in times)
        if created and deleted and cost is not None:
            rows.append((created, deleted, cost))
        else:
            bad += 1
    return rows, open_notes(total, rows), bad


def open_notes(cell, closed):
    """[(start or None, stop or None, usd an hour)] of the instances a TOTAL row's outcome cell notes as running or
    stopped. The ledger's template asks for each one's rate and start time, and for a stopped one when it stopped:
    each clause (split at ";" and at the end of a sentence) naming a rate in $/h is one instance, its earliest ISO
    time the start and the first ISO time after "stopped" the stop. A clause that says the instance was deleted or is
    gone, or whose start is a closed row's creation (within 5 minutes), is left out: its row counts it."""
    found = []
    for clause in re.split(r";|(?<=[.!?])\s+", cell or ""):
        rate = BREV_RATE.search(clause)
        low = clause.lower()
        if not rate or any(w in low for w in ("delet", "gone", "see its row")):
            continue
        times = [(m.start(), parse_utc(m.group(0))) for m in BREV_TIME.finditer(clause)]
        times = [(p, t) for p, t in times if t]
        at = low.find("stopp")
        stop = next((t for p, t in times if p > at), None) if at >= 0 else None
        begin = min((t for _p, t in times if t is not stop), default=None)
        if begin and any(abs((c - begin).total_seconds()) <= 300 for c, _d, _u in closed):
            continue
        # a stopped note whose stop time is missing is unplaced, like one without a start
        found.append((begin if at < 0 or stop else None, stop, float(rate.group(1).replace(",", ""))))
    return found


def parse_utc(text):
    try:
        t = datetime.fromisoformat(text.replace("Z", "+00:00").replace(" ", "T"))
    except ValueError:
        return None
    return t if t.tzinfo else t.replace(tzinfo=timezone.utc)


def brev_spent(closed, running, start, now):
    """(the Brev spend between start and now, the running notes it could not count). A closed instance's cost is
    spread evenly over its life; a running or stopped one bills its rate from its start to now or to its stop (the
    storage a stopped one keeps billing is not counted). A note without a start is not counted, unless it stopped
    before start."""
    total, lost = 0.0, 0
    for created, deleted, cost in closed:
        life = (deleted - created).total_seconds()
        if life <= 0:
            total += cost if start <= deleted <= now else 0.0
            continue
        part = (min(deleted, now) - max(created, start)).total_seconds()
        total += cost * part / life if part > 0 else 0.0
    for begin, stop, rate in running:
        if begin is None:
            lost += 0 if stop and stop <= start else 1
            continue
        part = (min(stop or now, now) - max(begin, start)).total_seconds()
        total += rate * part / 3600 if part > 0 else 0.0
    return total, lost


def positive(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool) and v > 0 and v == v and v != float("inf")


def spend_line(P, S, root, pack, now):
    """(line, flag or None) of the day's spend: Claude against its daily budget, each category of the cost ledger,
    Brev against its daily limit; each on its own, no total."""
    mem = pack / ".memory"
    parts, flags = [], []
    start = None
    if not S.solaris_root(str(root)):
        parts.append(f"Claude {NA} (no Solaris checkout above the project)")
    else:
        got = S.ai_spend(str(root), ["--today"], take_today)
        if got is None:
            parts.append("Claude not read")
            flags.append("the read failed: solaris.tools.ai_spend --today gave no report")
        else:
            spent, limit, since, problems = got
            start = P.parse_time(since)
            parts.append(f"Claude {usd(spent)} today" + (f" of {usd(limit)}" if limit else " (no daily budget set)"))
            if limit and spent >= limit:
                flags.append(f"Claude spend reached its daily budget ({usd(spent)} of {usd(limit)}): economize and "
                             "tell the owner")
            flags += [f"ai_spend: {x}" for x in problems]
    start = start or owner_day(P, pack, now)
    day = start.date()
    ledger, bad = S.read_ledger(str(mem / "spend.jsonl"))
    if ledger is None:
        parts.append("no cost ledger (spend.jsonl)")
    else:
        cats = {}
        for d, cat, cost, _what in ledger:
            if d == day:
                cats[cat] = cats.get(cat, 0.0) + cost
        shown = ", ".join(f"{c} {usd(v)}" for c, v in sorted(cats.items(), key=lambda x: -x[1]))
        parts.append(f"ledger today: {shown or 'nothing'}" + (f" ({plural(bad, 'line')} unreadable)" if bad else ""))
    limit, src = pack_setting(P, pack, BREV_KEY)
    if limit is not None and not positive(limit):
        flags.append(f"{BREV_KEY} in {P._rel(src)} must be a positive number of US dollars: fix it")
        limit = None
    rows, running, bad = read_brev(mem / "brev-costs.md")
    if rows is not None or limit:
        spent, lost = brev_spent(rows or [], running, start, now)
        if rows is None:
            source = "no brev-costs.md yet"
        else:
            placed = len(running) - sum(1 for b, _s, _r in running if b is None)
            source = "brev-costs.md" + (f", with {plural(placed, 'running or stopped instance')} from its TOTAL row"
                                        if placed else "")
            source += f"; {plural(bad, 'row')} unreadable" if bad else ""
            source += f"; {plural(lost, 'running note')} not counted (no ISO start or stop time)" if lost else ""
        parts.append(f"Brev {usd(spent)} today" + (f" of {usd(limit)}" if limit else "") + f" ({source})")
        if limit and spent >= limit:
            flags.append(f"Brev spend reached its daily limit ({usd(spent)} of {usd(limit)}): tell the owner before "
                         "more paid work")
        if limit and lost:
            flags.append(f"{plural(lost, 'running Brev instance')} in brev-costs.md could not be counted against the "
                         "limit: note each running or stopped instance in its TOTAL row with its $/h rate and ISO "
                         "start time (and stop time)")
    return "; ".join(parts), "; ".join(flags) or None


def full_snapshots(P, root, slug):
    """The names of the saved full board snapshots, oldest first."""
    d = P.lb_dir(root, slug)
    return P.by_time(d, (n for n in P._files(d, "*.json.gz") if "partial" not in n))


def jump_moves(P, root, slug, newest, base, jump):
    """The top teams of snapshot newest that jumped since snapshot base: improved by at least jump (("%", pct of its
    earlier score) or ("abs", score units)), or came from below place JUMP_FROM or from off the board."""
    d = P.lb_dir(root, slug)
    _snap, rows = P.board_rows(d / newest)
    _old, old = P.board_rows(d / base)
    if not rows or not old:
        return []
    sign = P.direction(rows)
    by_id = {r.get("team_id"): (i, r) for i, r in enumerate(old, 1) if r.get("team_id") is not None}
    by_name = {r.get("team_name"): (i, r) for i, r in enumerate(old, 1)}
    moves = []
    for i, r in enumerate(rows[:JUMP_TOP], 1):
        place, o = by_id.get(r.get("team_id")) or by_name.get(r.get("team_name")) or (None, None)
        name, rank = P.one_line(r.get("team_name"), 30), r.get("rank") or i
        was = (o.get("rank") or place) if o else None
        if o is None or was > JUMP_FROM:
            moves.append(f"{name} {'new on the board' if o is None else f'#{was}'} -> #{rank} ({r.get('score')})")
            continue
        now_s, was_s = P.num(r.get("score")), P.num(o.get("score"))
        if now_s is None or was_s is None:
            continue
        gain = sign * (now_s - was_s)
        need = jump[1] / 100 * abs(was_s) if jump[0] == "%" else jump[1]
        if gain > 1e-12 and gain >= need - 1e-12:
            pct = f", {100 * gain / abs(was_s):+.1f}%" if was_s else ""
            moves.append(f"{name} #{was} -> #{rank}, {o.get('score')} -> {r.get('score')}{pct}")
    return moves


def landed(P, s, prev, first, now):
    """When submission s's score was first seen: kept from the last pass while the score is the same; on a first
    pass its submission time (the score landed after it); else this pass."""
    if not (s["status"] == "COMPLETE" and s["score"]):
        return None
    if isinstance(prev, dict) and prev.get("status") == "COMPLETE" and prev.get("score") == s["score"]:
        return prev.get("landed") or s.get("date")
    return s.get("date") if first else P.iso(now)


def run_pass(P, slug, root, *, gateway=None, vs=None, status=None, now=None, jump=JUMP_DEFAULT):
    """One hourly pass with the kaggle_presubmit module P: ([(check, line, flag or None)], the state for last.json,
    its path, the status block {field: text})."""
    root = Path(root)
    gw = P.find_gateway(gateway)
    now = now or P.utc_now()
    S = load_tool("kaggle_status")
    state_path = root / "__data" / "kaggle" / P.check_slug(slug) / "hourly" / "last.json"
    last = P.read_json(state_path, None)
    last = last if isinstance(last, dict) else {}
    try:
        pack = P.pack_of(root)
    except P.PresubmitError:
        pack = None
    status_path = Path(status).resolve() if status else root / "reports" / "status.json"
    st = P.read_json(status_path, None)
    st = st if isinstance(st, dict) else None
    lines = []

    def add(check, line, flag=None):
        lines.append((check, line, flag))

    # every Kaggle read of the pass goes through this: after an HTTP 429 the rest are skipped
    reads = P.Limiter(_run)
    try:
        res = P.check(slug, root, gateway=gw, now=now, vs=vs, save=False, run=reads)
    except P.PresubmitError as e:
        res = None
        add("review", "not run", f"the pre-submit reads did not run: {e}")
    # what the last pass saw; a part whose read fails now keeps it for the next pass
    keep = dict(last)
    keep["reads"] = {}
    if res:
        view, hits = res["view"], res["hits"]
        urgent = [f"{name}: " + "; ".join(hits[k][:4]) for k, name in P.TRIGGERS if k in URGENT and hits[k]]
        others = [name for k, name in P.TRIGGERS if k not in URGENT and k != "failed" and hits[k]]
        since = (f"since the review recorded {P.local(res['base_at'])}" if res["base_at"] else "no review recorded")
        add("review", f"{since}; triggers: {', '.join(others) or 'none'}"
            + (f"; {' | '.join(urgent)}" if urgent else ""),
            f"a host post or a page change: run kaggle_presubmit.py {slug}, read it, decide, then --ack"
            if urgent else None)
        for src in P.SOURCES:
            # a read the rate limit stopped is named once, in the rate-limited line
            if not view[src].get("ok") and src != reads.hit and src not in reads.skipped:
                add(src, "not read", f"the read failed: {view[src].get('error')}")
        b = view["board"]
        if b.get("ok"):
            lead = b["top"][0] if b["top"] else {}
            line = (f"{b['teams']:,} teams, snapshot {P.local(b['fetched_at'])}; #1 "
                    f"{P.one_line(lead.get('team_name'), 30)} {lead.get('score')}")
            add("board", *board_jump(P, root, slug, b["file"], last.get("board"), jump, line))
            keep["board"] = b["file"]
            # a read of this pass's own, for kaggle_presubmit.py to reuse while it is young
            if not b.get("note"):
                keep["reads"]["board"] = b["file"]
        n = view["notebooks"]
        if n.get("ok"):
            add("notebooks", *notebook_line(P, root, slug, n, vs, res["sign"]))
            keep["reads"]["notebooks"] = n["file"]
        if view["forum"].get("ok"):
            pending = P.read_json(P.forum_dir(root, slug) / "pending.json", None) or {}
            topics = [len(pending.get(k) or []) if isinstance(pending, dict) else 0
                      for k in ("new", "changed", "missing")]
            unread = "{} new, {} changed, {} missing topics not shown and committed yet".format(*topics)
            forum = P._rel(TOOLS / "kaggle_forum.py")
            add("forum", unread if any(topics) else "nothing unread",
                f"read them: python3 {forum} show {slug} --new, log what matters, then commit" if any(topics) else None)
    reads.check = "subs"
    subs, err = read_submissions(P, slug, gw, root, reads)
    if err:
        # under a rate limit the read is named in the rate-limited line instead
        if not reads.hit:
            add("subs", "not read", f"the read failed: {err}")
    else:
        before = last.get("submissions")
        before = before if isinstance(before, dict) else None
        keep["submissions"] = {}
        for s in subs:
            entry = {"status": s["status"], "score": s["score"]}
            seen = landed(P, s, (before or {}).get(s["key"]), before is None, now)
            if seen:
                entry["landed"] = seen
            keep["submissions"][s["key"]] = entry
        add("subs", *submission_line(subs, before))
    reads.check = "kernels"
    share, err = read_share(P, gw, root, reads)
    ended = None
    ours = set()
    if err:
        if not reads.hit:
            add("kernels", "not read", f"the read failed: {err}")
    else:
        ours = {n for n, p in share["projects"].items() if isinstance(p, dict) and p.get("root") == str(root.resolve())}
        keep["running"] = sorted({r["ref"] for r in share.get("running") or [] if r.get("project") in ours})
        before = last.get("running")
        add("kernels", *kernel_line(P, share, ours, before if isinstance(before, list) else None, now))
        if isinstance(before, list):
            ended = sorted(set(before) - set(keep["running"]))
        add("gpu", gpu_line(P, share, ours))
        if share.get("errors"):
            add("account", P.one_line("; ".join(map(str, share["errors"])), 200),
                "the account read was incomplete: see kaggle_share.py status")
    page = status_page(P, root, status_path, now, given=bool(status), scored=newest_score(P, keep, subs))
    if page:
        add("status", *page)
    spend = None
    if pack:
        git = git_line(P, root, now)
        if git:
            add("git", *git)
        at = P.parse_time(last.get("at"))
        since = at if at and at <= now else now - timedelta(hours=FIRST_HOURS)
        add("agents", *agents_line(P, S, root, pack, since, now))
        spend = spend_line(P, S, root, pack, now)
        add("spend", *spend)
    add("browsers", *browsers(P, root, now))
    if reads.hit:
        # the account read feeds the kernels and the gpu lines
        named = [{"kernels": "kernels, gpu"}.get(x, x) for x in reads.skipped]
        add("rate-limited", f"Kaggle answered HTTP 429 (Too Many Requests) to the {reads.hit} read; skipped after it: "
                            f"{', '.join(named) or 'none'}",
            "Kaggle is rate limiting the account: wait a few minutes before the next Kaggle read (the skipped checks "
            "run at the next pass)")
    flags = sum(1 for _c, _l, f in lines if f)
    block = status_block(P, S, root, pack, st, now, slug=slug, subs=subs, share=share, ours=ours, ended=ended,
                         spend=spend, flags=flags, limited=bool(reads.hit))
    return lines, dict(keep, schema=1, at=P.iso(now)), state_path, block


def board_jump(P, root, slug, newest, seen, jump, line):
    """(line, flag or None) for the board: line, and the top teams' jumps between the snapshot the last pass saw
    (else the one before newest) and newest."""
    names = full_snapshots(P, root, slug)
    if newest not in names or seen == newest:
        return line, None
    older = names[:names.index(newest)]
    base = seen if seen in older else (older[-1] if older else None)
    if base is None:
        return line, None
    moves = jump_moves(P, root, slug, newest, base, jump)
    if not moves:
        return line, None
    snap, _rows = P.board_rows(P.lb_dir(root, slug) / base)
    since = P.local((snap or {}).get("fetched_at"))
    return (line + f"; jump since {since}: " + "; ".join(moves[:4]) + (f" (+{len(moves) - 4} more)" if len(moves) > 4
                                                                       else ""),
            "a top team jumped: refresh the top-teams research and record a decision within a day")


def newest_score(P, keep, subs):
    """(when the newest score was first seen, what it is) from the submissions as last seen, else None."""
    known = keep.get("submissions") if isinstance(keep.get("submissions"), dict) else {}
    what = {s["key"]: f"{s['what']} {s['score']}" for s in subs or []}
    seen = [(P.parse_time(v.get("landed")), k) for k, v in known.items() if isinstance(v, dict) and v.get("landed")]
    seen = [(t, k) for t, k in seen if t]
    if not seen:
        return None
    t, k = max(seen)
    return t, what.get(k) or f"submission {k} {known[k].get('score')}"


def status_block(P, S, root, pack, st, now, *, slug, subs, share, ours, ended, spend, flags, limited):
    """The STATUS block: {field: text} for BLOCK, n/a where the tool cannot tell."""
    why = "rate limited" if limited else "not read"
    out = {}
    # pick: the Kaggle day's slots, the pending submissions and the master's pick
    parts = []
    if subs is None:
        parts.append(f"slots {NA} (submissions {why})")
    else:
        try:
            kday = S.kaggle_day(st or {}, now)
        except (ValueError, TypeError, AttributeError):
            kday = now.replace(hour=0, minute=0, second=0, microsecond=0)
        used = [s for s in subs if (P.parse_time(s.get("date")) or kday - timedelta(days=1)) >= kday]
        limit = (st or {}).get("daily_slots")
        limit = limit if isinstance(limit, int) and not isinstance(limit, bool) and limit > 0 else None
        parts.append((f"{len(used)} of {limit} slots used today" if limit else
                      f"{len(used)} slots used today (limit {NA}: the status JSON's daily_slots)")
                     + f", the next reset {P.local(kday + timedelta(days=1))}")
        pending = [s for s in subs if s["status"] not in SCORING_DONE]
        parts.append(f"{len(pending)} pending" + (f" ({', '.join(s['what'] for s in pending[:3])})" if pending else ""))
    pick = (st or {}).get("pick")
    if isinstance(pick, str) and pick.strip():
        parts.append(f"pick: {P.one_line(pick, 200)}")
    out["pick"] = "; ".join(parts)
    # score and rank: the newest full board snapshot
    names = full_snapshots(P, root, slug)
    snap, rows = P.board_rows(P.lb_dir(root, slug) / names[-1]) if names else (None, None)
    if not rows:
        out["score"] = out["rank"] = f"{NA} (no full board snapshot saved)"
    else:
        n, when = len(rows), P.local(snap.get("fetched_at"))
        team = (st or {}).get("team")
        me = next((r for r in rows if team and r.get("team_name") == team), None)
        medals = ", ".join(f"{name} {rows[min(place, n) - 1].get('score')} (#{place})"
                           for name, place in zip(("gold", "silver", "bronze"), S.medal_places(n)))
        if me:
            ours_score = f"{me.get('score')}"
            out["rank"] = f"#{me.get('rank')} of {n:,} teams (board {when})"
        else:
            reason = "the team is not on the board" if team else "set the status JSON's team"
            sign = P.direction(rows)
            scored = [x for x in subs or [] if P.num(x["score"]) is not None]
            best = max(scored, key=lambda x: sign * P.num(x["score"]), default=None)
            ours_score = f"{best['score']} (our best submission; {reason})" if best else f"{NA} ({reason})"
            out["rank"] = f"{NA} ({reason})"
        out["score"] = f"{ours_score}; {medals} (board {when})"
    # compute: the machines, the nearest booking end, the Kaggle GPU week
    parts = []
    if pack:
        mem = pack / ".memory"
        hosts = S.read_hosts(str(mem / "hosts.json"))
        if hosts is None:
            parts.append(f"machines {NA} (no hosts.json)")
        else:
            names_ = [str(h["name"]) for h in hosts]
            parts.append(plural(len(names_), "machine") + (f" ({', '.join(names_[:4])}"
                                                            + (f" +{len(names_) - 4} more" if len(names_) > 4 else "")
                                                            + ")" if names_ else ""))
        ends, _read = S.read_leases(str(mem / "lease-ends.json"))
        coming = sorted((t, h) for h, t in ends.items() if t > now)
        if coming:
            t, h = coming[0]
            parts.append(f"nearest booking end {h} {P.local(t)} ({(t - now).total_seconds() / 3600:.0f} h left)")
        else:
            parts.append("every booking in lease-ends.json has ended" if ends else f"booking ends {NA} (no "
                                                                                    "lease-ends.json)")
        shared = shared_hosts(mem / "resource-sharing-seen.json")
        if shared:
            parts.append(shared_part(shared, now, P))
    else:
        parts.append(f"machines {NA} (no ai-pack)")
    parts.append(f"Kaggle GPU week: {gpu_line(P, share, ours)}" if share else f"Kaggle GPU week {NA} ({why})")
    out["compute"] = "; ".join(parts)
    # stopped: this project's kernel runs that ended; claims live on the hosts
    kernels = (f"kernels {NA} ({why})" if share is None else f"kernels {NA} (no earlier pass)" if ended is None
               else "kernels ended: " + (", ".join(ended) or "none"))
    out["stopped"] = f"{kernels}; claims {NA} (they live on the hosts: hostclaims.py status)"
    out["spend"] = spend[0] if spend else f"{NA} (no ai-pack)"
    # for you: the status JSON's open questions and owner actions, and the flags
    parts = []
    if st is None:
        parts.append(f"questions {NA} (no status JSON)")
    else:
        for key, word in (("questions", "open question"), ("owner_actions", "owner action")):
            items = [x for x in st.get(key) or [] if x] if isinstance(st.get(key), list) else []
            texts = [P.one_line(x.get("text") or x.get("question") if isinstance(x, dict) else x, 80) for x in items]
            parts.append(plural(len(texts), word) + (": " + "; ".join(f"{i}. {t}" for i, t in enumerate(texts, 1))
                                                    if texts else ""))
    parts.append(plural(flags, "FLAG line"))
    out["for you"] = "; ".join(parts)
    return {field: out[field] for field in BLOCK}


def notebook_line(P, root, slug, n, vs, sign):
    """(line, flag) for the public notebooks: what changed since the list before this pass's."""
    d = P.lb_dir(root, slug, "notebooks")
    lists = P.by_time(d, P._files(d, "*.json.gz"))
    older = lists[:lists.index(n["file"])] if n["file"] in lists else []
    prev = P.nb_list(P.read_gz(d / older[-1])) if older else None
    if prev is None:
        return f"{len(n['notebooks'])} listed; no list before this one to compare", None
    fresh, rescored = P.notebook_changes(prev, n["notebooks"])
    if not fresh and not rescored:
        return f"{len(n['notebooks'])} listed; no new or re-scored notebook since the list before", None
    place = {x["ref"].lower(): i for i, x in enumerate(n["notebooks"], 1)}
    shown = [f"{x['ref']} {P.fmt_score(x['score'])} (#{place[x['ref'].lower()]}, new)" for x in fresh]
    shown += [f"{x['ref']} {P.fmt_score(was)} -> {P.fmt_score(x['score'])} (#{place[x['ref'].lower()]})"
              for x, was in rescored]
    hot = [x for x in [*fresh, *(x for x, _ in rescored)]
           if place[x["ref"].lower()] <= NB_TOP or P.at_or_better(x["score"], vs, sign)]
    line = f"{len(fresh)} new, {len(rescored)} re-scored since the list before: " + "; ".join(shown[:6]) + (
        f" (+{len(shown) - 6} more)" if len(shown) > 6 else "")
    flag = (f"{len(hot)} new or re-scored in the top {NB_TOP}" + (f" or at or above {vs:g}" if vs is not None
                                                                             else "")) if hot else None
    return line, flag


def submission_line(subs, before):
    """(line, flag) for the submissions: those scoring now, and what landed or errored since the last pass."""
    scoring = [s for s in subs if s["status"] not in SCORING_DONE]
    line = f"{len(scoring)} scoring now" + (f" ({', '.join(s['what'] for s in scoring[:3])})" if scoring else "")
    if before is None:
        return line + "; no earlier pass to compare", None
    was = {k: v for k, v in before.items() if isinstance(v, dict)}
    landed_ = [s for s in subs if s["status"] == "COMPLETE" and s["score"]
               and not (was.get(s["key"], {}).get("status") == "COMPLETE" and was.get(s["key"], {}).get("score"))]
    errored = [s for s in subs if s["status"] == "ERROR" and was.get(s["key"], {}).get("status") != "ERROR"]
    news = [f"{s['what']} scored {s['score']}" for s in landed_] + [f"{s['what']} ERROR" for s in errored]
    if not news:
        return line + "; nothing landed since the last pass", None
    return line + "; since the last pass: " + "; ".join(news[:5]), (
        "a score landed or a submission errored: update the records and the plan")


def kernel_line(P, share, ours, before, now):
    """(line, flag) for the queued and running kernels; a run of this project's that ended since the last pass."""
    running = share.get("running") or []
    mine = [r for r in running if r.get("project") in ours]
    parts = []
    for r in mine[:4]:
        started = P.parse_time(r.get("last_run"))
        parts.append(f"{r['ref']} {r.get('status')}" + (f" {ago(started, now)}" if started else ""))
    line = (f"{len(running)} queued or running on the account; this project's: " + (", ".join(parts) or "none"))
    if before is None:
        return line + "; no earlier pass to compare", None
    ended = sorted(set(before) - {r["ref"] for r in mine})
    if not ended:
        return line, None
    return line + f"; ended since the last pass: {', '.join(ended)}", (
        "a run ended: check its result, fetch its output, release its lease")


def gpu_line(P, share, ours):
    g = (share.get("quota") or {}).get("gpu") or {}
    used, total, left = P.num(g.get("used")), P.num(g.get("total")), P.num(share.get("gpu_hours_left"))
    week = (f"{used:.2f} of {total:.2f} h used this week" if used is not None and total is not None
            else "account quota unknown") + (f", {left:.2f} h left" if left is not None else "")
    if g.get("refresh"):
        week += f" (resets {P.local(g['refresh'])})"
    mine = [p for n, p in share["projects"].items() if n in ours]
    if mine:
        week += "; this project's leases {:.2f} of {:.2f} h".format(sum(P.num(p.get("gpu_hours")) or 0 for p in mine),
                                                                    sum(P.num(p.get("gpu_budget")) or 0 for p in mine))
    return week


def jump_arg(text):
    """--jump: "N%" of the team's earlier score, or N in score units."""
    s = str(text).strip()
    pct = s.endswith("%")
    try:
        v = float(s[:-1] if pct else s)
    except ValueError:
        v = -1.0
    if not 0 < v < float("inf"):
        raise argparse.ArgumentTypeError(f"not a jump: {text!r} (N% of the earlier score, or a score difference)")
    return ("%" if pct else "abs", v)


def main(argv=None):
    p = argparse.ArgumentParser(prog="kaggle_hourly.py", description="One hourly pass of the read-only Kaggle checks: "
                                "a line per check, FLAG where a decision is needed, then the status block.")
    p.add_argument("slug", help="competition slug")
    p.add_argument("--vs", help="flag new or re-scored public notebooks at or better than this score")
    p.add_argument("--status", help="the status page's JSON (default: <root>/reports/status.json)")
    p.add_argument("--jump", type=jump_arg, default=JUMP_DEFAULT, metavar="N%|N",
                   help=f"flag a top-{JUMP_TOP} team that improved by at least this much since the last pass's board "
                        "snapshot: N%% of its earlier score, or N in score units (default 2%%)")
    p.add_argument("--json", action="store_true", help="print the pass, status block included, as one JSON object")
    p.add_argument("--gateway", help="the kaggle.py gateway to call (default: the one beside this file)")
    a = p.parse_args(argv)
    try:
        P = load_tool("kaggle_presubmit")
    except HourlyError as e:
        print(f"kaggle_hourly: {e}", file=sys.stderr)
        return 1
    if not P.SLUG_RE.fullmatch(a.slug):
        p.error(f"not a competition slug: {a.slug!r}")
    try:
        vs = None if a.vs is None else P.score_arg(a.vs)
    except argparse.ArgumentTypeError as e:
        p.error(f"argument --vs: {e}")
    try:
        root = P.find_root()
        if root is None:
            raise HourlyError("no project or task folder here: run from one")
        lines, state, state_path, block = run_pass(P, a.slug, root, gateway=a.gateway, vs=vs, status=a.status,
                                                   jump=a.jump)
        P.write_json(state_path, state)
    except (HourlyError, P.PresubmitError) as e:
        print(f"kaggle_hourly: {e}", file=sys.stderr)
        return 1
    flags = [(check, flag) for check, _l, flag in lines if flag]
    if a.json:
        print(json.dumps({"slug": a.slug, "at": state["at"], "note": P.DISCLAIMER,
                          "checks": [{"check": c, "line": line, "flag": f} for c, line, f in lines],
                          "flags": len(flags), "block": block}, indent=1, ensure_ascii=False))
        return EXIT_FLAG if flags else 0
    print(f"HOURLY {a.slug}, {P.local(P.utc_now())}")
    print(P.DISCLAIMER)
    for check, line, flag in lines:
        print(f"{check:<10} {'FLAG ' if flag else ''}{line}")
    print(f"FLAGS: {len(flags)}" if flags else "FLAGS: none: nothing needs a decision")
    for check, flag in flags:
        print(f"  - {check}: {flag}")
    print("STATUS (paste into the status message)")
    for field in BLOCK:
        print(f"{field + ':':<9} {block[field]}")
    return EXIT_FLAG if flags else 0


if __name__ == "__main__":
    sys.exit(main())
