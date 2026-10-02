# rev. 1

"""kaggle_hourly: one scripted hourly pass of the read-only Kaggle checks, printed as flags.

An agent working a competition checks it every hour. This tool runs those reads
in one call and prints one line per check, marked FLAG where something needs a
decision, so the agent reads a few lines instead of every source. It wakes
nothing and schedules nothing: the agent runs it at its hourly pass (a host
scheduler only when the owner approved one).

    python3 <plugin-dir>/tools/kaggle_hourly.py <slug> [--vs SCORE] [--plan FILE]

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
  live plan  when the plan exists (--plan, default submissions/live-plan.json):
             its page reports/html/<name>.html. FLAG: missing, older than the
             plan, or more than 2 hours old
  A read that failed is a FLAG too.

The first pass has no earlier one to compare submissions and kernel runs with.
State: <context>/__data/kaggle/<slug>/hourly/last.json (the submissions and this
project's running kernels at the last pass). Exit codes: 0 nothing needs a
decision, 10 a FLAG was raised, 1 error, 2 bad usage. Stdlib only.
"""

import argparse
import importlib.util
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

TOOLS = Path(__file__).resolve().parent
EXIT_FLAG = 10
# a notebook this high in the score-ordered list is worth a look; a live plan page older than this is stale
NB_TOP = 15
STALE_HOURS = 2.0
PLAN_SLACK = 60
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


def live_plan(P, root, plan_path, now):
    """(line, flag or None) for the live plan's page, or None when there is no plan."""
    if not plan_path.is_file():
        return None
    plan = P.read_json(plan_path, None)
    name = plan.get("name") if isinstance(plan, dict) else None
    if not isinstance(plan, dict) or not isinstance(name or "", str):
        return f"{P._rel(plan_path)} is not a plan", "unreadable: fix the plan, then rebuild it (kaggle_live_plan.py)"
    page = root / "reports" / "html" / f"{name or 'live-plan'}.html"
    if not page.is_file():
        return f"{P._rel(page)} not built yet", "never built: build it (kaggle_live_plan.py)"
    built = datetime.fromtimestamp(page.stat().st_mtime, timezone.utc)
    line = f"{P._rel(page)} built {ago(built, now)} ago, rev {plan.get('rev')}"
    # a build saves the plan's raised rev just after the page: only a later edit makes the page stale
    if plan_path.stat().st_mtime > page.stat().st_mtime + PLAN_SLACK:
        return line, "the plan changed after the last build: rebuild it (kaggle_live_plan.py)"
    if (now - built).total_seconds() > STALE_HOURS * 3600:
        return line, f"the page is {ago(built, now)} old: rebuild it (kaggle_live_plan.py --keep-rev)"
    return line, None


def run_pass(P, slug, root, *, gateway=None, vs=None, plan=None, now=None):
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
    plan_line = live_plan(P, root, Path(plan) if plan else root / "submissions" / "live-plan.json", now)
    if plan_line:
        add("live plan", *plan_line)
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
    p.add_argument("--plan", help="the live plan JSON (default: <root>/submissions/live-plan.json)")
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
        lines, state, state_path = run_pass(P, a.slug, root, gateway=a.gateway, vs=vs, plan=a.plan)
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
