# rev. 3

"""kaggle_hourly: one scripted hourly pass of the read-only Kaggle checks, printed as flags.

An agent working a competition checks it every hour. This tool runs those reads
in one call and prints one line per check, marked FLAG where something needs a
decision, so the agent reads a few lines instead of every source. It wakes
nothing and schedules nothing: the agent runs it at its hourly pass, woken by
its in-session clock (no daemon or host scheduler).

    python3 <plugin-dir>/tools/kaggle_hourly.py <slug> [--vs SCORE] [--status FILE]

Run it from the project root or task folder. The reads go through the tools
beside this file and the gateway (--gateway; default the kaggle.py beside this
file), read-only and unstamped (KAGGLE_SHARE_QUIET=1):

  review     kaggle_presubmit.py's reads and comparison, not kept as a check
             (so --ack still records only a check the agent ran and read): the
             forum check, the competition pages, the public notebooks and a
             board snapshot. FLAG: a host post or a page change since the last
             review; the other triggers are listed.
  board      the newest full snapshot: teams and the #1
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
             the page missing, the JSON changed after the last build, or the
             page more than 2 hours old; a project still on
             submissions/live-plan.json without the JSON: the live plan is
             retired; in a git work tree, a file a build writes (the page,
             its HTML, the shorter-chart try) that git would not ignore
             (`git check-ignore`). A task folder without the JSON is not
             checked
  browsers   one `ps` read: this project's browserctl browsers and their age,
             orphaned report-render Chromes (their render.js gone) and a count
             of the other browsers, never flagged. FLAG: an orphaned render
             Chrome, or a browser of this project's up more than 2 hours
  A read that failed is a FLAG too.

The first pass has no earlier one to compare submissions and kernel runs with.
State: <context>/__data/kaggle/<slug>/hourly/last.json (the submissions and this
project's running kernels at the last pass). Exit codes: 0 nothing needs a
decision, 10 a FLAG was raised, 1 error, 2 bad usage. Stdlib only.
"""

import argparse
import importlib.util
import os
import re
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

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


class HourlyError(Exception):
    pass


def load_tool(name):
    """A tool module from this folder (kaggle_presubmit), loaded by path."""
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


def read_submissions(P, slug, gw, root):
    """The team's submissions, newest first as the CLI lists them: ([{key, status, score, what}], error)."""
    code, out, err = _run([sys.executable, str(gw), "competitions", "submissions", slug, "--format", "json"], root)
    if code != 0:
        return None, f"competitions submissions failed (exit {code}): {P.tail(err or out)}"
    # CLI 2.2.4 prints this instead of an empty JSON list
    rows = [] if "No submissions found" in out else P.first_json(out, "[")
    if not isinstance(rows, list):
        return None, f"competitions submissions printed no list: {P.tail(out)}"
    subs = []
    for r in rows:
        if isinstance(r, dict):
            subs.append({"key": str(r.get("ref") or f"{r.get('date')} {r.get('description')}"),
                         "status": str(r.get("status") or "").split(".")[-1].upper(),
                         "score": str(r.get("publicScore") or "").strip(),
                         "what": P.one_line(r.get("description") or r.get("fileName") or "?", 40)})
    return subs, None


def read_share(P, gw, root):
    """kaggle_share.py status --json: (the sharing view, error)."""
    code, out, err = _run([sys.executable, str(TOOLS / "kaggle_share.py"), "status", "--json", "--gateway", str(gw)],
                          root)
    view = P.first_json(out, "{") if code == 0 else None
    if not isinstance(view, dict) or not isinstance(view.get("projects"), dict):
        return None, f"kaggle_share.py status failed (exit {code}): {P.tail(err or out)}"
    return view, None


def status_page(P, root, status_path, now, given=False):
    """(line, flag or None) for the status page (kaggle_status.py), or None for a folder that needs none: a task
    folder without a status JSON, unless --status named one. In a git work tree, a page file git would not ignore
    is a flag too."""
    got = page_state(P, root, status_path, now, given)
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


def page_state(P, root, status_path, now, given):
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


def run_pass(P, slug, root, *, gateway=None, vs=None, status=None, now=None):
    """One hourly pass with the kaggle_presubmit module P: ([(check, line, flag or None)], the state for last.json,
    its path)."""
    root = Path(root)
    gw = P.find_gateway(gateway)
    now = now or P.utc_now()
    state_path = root / "__data" / "kaggle" / P.check_slug(slug) / "hourly" / "last.json"
    last = P.read_json(state_path, None)
    last = last if isinstance(last, dict) else {}
    lines = []

    def add(check, line, flag=None):
        lines.append((check, line, flag))

    try:
        res = P.check(slug, root, gateway=gw, now=now, vs=vs, save=False)
    except P.PresubmitError as e:
        res = None
        add("review", "not run", f"the pre-submit reads did not run: {e}")
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
            if not view[src].get("ok"):
                add(src, "not read", f"the read failed: {view[src].get('error')}")
        b = view["board"]
        if b.get("ok"):
            lead = b["top"][0] if b["top"] else {}
            add("board", f"{b['teams']:,} teams, snapshot {P.local(b['fetched_at'])}; #1 "
                         f"{P.one_line(lead.get('team_name'), 30)} {lead.get('score')}")
        n = view["notebooks"]
        if n.get("ok"):
            add("notebooks", *notebook_line(P, root, slug, n, vs, res["sign"]))
        if view["forum"].get("ok"):
            pending = P.read_json(P.forum_dir(root, slug) / "pending.json", None) or {}
            counts = [len(pending.get(k) or []) if isinstance(pending, dict) else 0
                      for k in ("new", "changed", "missing")]
            unread = "{} new, {} changed, {} missing topics not shown and committed yet".format(*counts)
            forum = P._rel(TOOLS / "kaggle_forum.py")
            add("forum", unread if any(counts) else "nothing unread",
                f"read them: python3 {forum} show {slug} --new, log what matters, then commit" if any(counts) else None)
    # what the last pass saw; a part whose read fails now keeps it for the next pass
    keep = dict(last)
    subs, err = read_submissions(P, slug, gw, root)
    if err:
        add("subs", "not read", f"the read failed: {err}")
    else:
        keep["submissions"] = {s["key"]: {"status": s["status"], "score": s["score"]} for s in subs}
        before = last.get("submissions")
        add("subs", *submission_line(subs, before if isinstance(before, dict) else None))
    share, err = read_share(P, gw, root)
    if err:
        add("kernels", "not read", f"the read failed: {err}")
    else:
        ours = {n for n, p in share["projects"].items() if isinstance(p, dict) and p.get("root") == str(root.resolve())}
        keep["running"] = sorted({r["ref"] for r in share.get("running") or [] if r.get("project") in ours})
        before = last.get("running")
        add("kernels", *kernel_line(P, share, ours, before if isinstance(before, list) else None, now))
        add("gpu", gpu_line(P, share, ours))
        if share.get("errors"):
            add("account", P.one_line("; ".join(map(str, share["errors"])), 200),
                "the account read was incomplete: see kaggle_share.py status")
    page = status_page(P, root, Path(status).resolve() if status else root / "reports" / "status.json", now,
                       given=bool(status))
    if page:
        add("status", *page)
    add("browsers", *browsers(P, root, now))
    return lines, dict(keep, schema=1, at=P.iso(now)), state_path


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
    landed = [s for s in subs if s["status"] == "COMPLETE" and s["score"]
              and not (was.get(s["key"], {}).get("status") == "COMPLETE" and was.get(s["key"], {}).get("score"))]
    errored = [s for s in subs if s["status"] == "ERROR" and was.get(s["key"], {}).get("status") != "ERROR"]
    news = [f"{s['what']} scored {s['score']}" for s in landed] + [f"{s['what']} ERROR" for s in errored]
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


def main(argv=None):
    p = argparse.ArgumentParser(prog="kaggle_hourly.py", description="One hourly pass of the read-only Kaggle checks: "
                                "a line per check, FLAG where a decision is needed.")
    p.add_argument("slug", help="competition slug")
    p.add_argument("--vs", help="flag new or re-scored public notebooks at or better than this score")
    p.add_argument("--status", help="the status page's JSON (default: <root>/reports/status.json)")
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
        lines, state, state_path = run_pass(P, a.slug, root, gateway=a.gateway, vs=vs, status=a.status)
        P.write_json(state_path, state)
    except (HourlyError, P.PresubmitError) as e:
        print(f"kaggle_hourly: {e}", file=sys.stderr)
        return 1
    print(f"HOURLY {a.slug}, {P.local(P.utc_now())}")
    print(P.DISCLAIMER)
    for check, line, flag in lines:
        print(f"{check:<10} {'FLAG ' if flag else ''}{line}")
    flags = [(check, flag) for check, _l, flag in lines if flag]
    print(f"FLAGS: {len(flags)}" if flags else "FLAGS: none: nothing needs a decision")
    for check, flag in flags:
        print(f"  - {check}: {flag}")
    return EXIT_FLAG if flags else 0


if __name__ == "__main__":
    sys.exit(main())
