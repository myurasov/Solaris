# rev. 2

"""kaggle_live_plan: a live plan document for the owner of a Kaggle competition project.

The current submission and research plan (the Kaggle day's slots with status, prediction and decision rule, the
later slots and fallbacks, research with gates and compute, settled questions, compute notes and recent decisions)
beside live figures: the team's board place and the medal lines, the day's slots used, recent submissions, the
account's GPU week, lease ends and the coming schedule. The plan is edited by hand in a JSON file; this tool raises
its rev, writes the HTML and renders the PDF.

    python3 <plugin-dir>/tools/kaggle_live_plan.py                 raise the rev, write the HTML, render the PDF
    python3 <plugin-dir>/tools/kaggle_live_plan.py --no-render     the same, HTML only
    python3 <plugin-dir>/tools/kaggle_live_plan.py --keep-rev      the hourly rebuild: fresh live figures, same rev
    python3 <plugin-dir>/tools/kaggle_live_plan.py --out FILE      a preview elsewhere: HTML only, plan untouched

Run it from the project root (or pass --root), unsandboxed: it calls the Kaggle API read-only through the gateway
and renders with Chrome (about 10 s). It writes <root>/reports/html/<name>.html and, when the reporting plugin is
attached (<pack>/plugins/reporting/assets/render.sh), renders <root>/reports/<name>.pdf; without that plugin it
writes the HTML only.

Found or passed as flags:
  root        --root, else the folder holding <pack>/manifest.json (<pack> is ai or aipack) around the working
              directory, else the project of a copied overlay (<project>/<pack>/plugins/kaggle/tools/)
  plan        --plan, else <root>/submissions/live-plan.json
  slug        --slug, else the plan's "competition", else the only competition with saved leaderboard snapshots
              (kaggle_lb.py's store: <root>/__data/kaggle/<slug>/leaderboard/, or $KAGGLE_LB_DIR/<slug>/)
  team        --team, else the plan's "team": the team name on the board (without it the team's place is n/a)
  name        --name, else the plan's "name", else live-plan: the report name (a leading MMDD in the title dates
              the PDF's footer)
  timezone    --tz, else the plan's "timezone", else $TZ, else this machine's zone; every time shown is in it
  render env  --render-env, else the plan's "render_env", relative to the root: a shell file sourced before render.sh
              (one that puts node and Chrome on PATH, say); none by default
  schedule    the plan's "schedule", plus --clock FILE (else the plan's "clock", relative to the root): a Python
              file whose top-level DAILY and DATED list literals (the shapes below; fields after the second are
              ignored) are read with ast, never run; every schedule time is in the timezone above
  hosts       --hosts, else <pack>/.memory/hosts.json (the resource-sharing inventory; only name and gpus are
              read); without it the host table is left out
  lease ends  --leases, else <pack>/.memory/lease-ends.json: {"read": "<ISO UTC>", "ends": {"<host>": "<ISO UTC>"}}
  gateway     --gateway, else the kaggle.py beside this file; kaggle_share.py is taken from beside the gateway

Live data is read at build time and shown as n/a when a source fails: the newest full leaderboard snapshot, the
team's submissions (`competitions submissions <slug> --format json`; a description "<id>: <what>" fills the # and
What columns), the account's GPU week and sessions (`kaggle_share.py status --json`) and the lease ends.

Plan JSON. Text fields may carry <b>, <i> or <code>; any other markup in them, names and live data are escaped.
  {
    "rev": 1,                                  raised by every build unless --keep-rev or --out
    "name": "<MMDD>-<project>-live-plan",      optional, see name
    "title": "<MMDD> <Competition> - Live Plan",   optional H1, plain text (default: "<project name> - Live Plan")
    "subtitle": "Submissions and Research",    optional
    "intro": "...",                            optional paragraph under the title (a generic one by default)
    "competition": "<slug>", "team": "<team name>", "timezone": "<IANA zone>", "render_env": "<file>",
    "clock": "<file>",                         optional, see above
    "daily_slots": 5, "reset_utc": "00:00",    optional: the day's submission limit and its reset time in UTC
    "tz_label": "PT",                          optional name for the timezone in headers (default: its abbreviation)
    "phase": "Phase 1: <goal>",                the text before the first colon is the headline's value
    "terms": "...",                            optional glossary, appended to the intro
    "day": "<the Kaggle day: its window and slots>",
    "slots": [{"id", "what", "status", "prediction", "rule"}],   the reset's slots, in submission order
    "slot_rule": "...",                        when the later slots go in
    "later": [{"id", "what", "status", "rule"}],
    "fallbacks": "...",
    "research": [{"id", "what", "status", "gate", "compute"}],
    "verdicts": [{"id", "verdict", "what"}],
    "compute": ["<note>", ...],
    "schedule": {"daily": [["HH:MM", "<tag>"], ...], "dated": [["YYYY-MM-DD HH:MM", "<tag>"], ...]},   optional
    "labels": {"<tag>": "<label>"},            optional schedule labels (default: the tag, dashes as spaces)
    "schedule_note": "...",                    optional line above the schedule table
    "decisions": [{"when", "text"}]            newest first
  }
"""
import argparse
import ast
import glob
import gzip
import html
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

PACKS = ("ai", "aipack")
HERE = os.path.dirname(os.path.abspath(__file__))
E = html.escape
NA = '<span class="pending">n/a</span>'
TZ = timezone.utc  # set in main()

# used only when the reporting plugin is not attached
FALLBACK_CSS = """
:root { --ink-1: #333; --ink-2: #555; --ink-muted: #888; --ink-heading: #111; --fs-sub: 0.9em; }
body { font: 10pt/1.4 system-ui, sans-serif; color: #222; margin: 2em auto; max-width: 64em; }
table { border-collapse: collapse; width: 100%; margin: 0.4em 0 0.8em 0; }
th, td { border-bottom: 1px solid #ddd; padding: 2pt 4pt; text-align: right; vertical-align: top; }
th.lft, td.lft { text-align: left; }
.h1-subtitle { font-size: 0.6em; font-weight: normal; }
"""


def find_root(start=None):
    """The project root around start (the folder holding <pack>/manifest.json), else a copied overlay's project."""
    d = os.path.abspath(start or os.getcwd())
    while True:
        if pack_of(d):
            return d
        if os.path.dirname(d) == d:
            break
        d = os.path.dirname(d)
    # a copied overlay sits at <project>/<pack>/plugins/kaggle/tools/
    plugins = os.path.dirname(os.path.dirname(HERE))
    pack = os.path.dirname(plugins)
    if os.path.basename(plugins) == "plugins" and os.path.basename(pack) in PACKS and pack_of(os.path.dirname(pack)):
        return os.path.dirname(pack)
    return None


def pack_of(root):
    return next((p for p in PACKS if os.path.isfile(os.path.join(root, p, "manifest.json"))), None)


def local_zone():
    tz = (os.environ.get("TZ") or "").lstrip(":")
    if tz:
        return tz
    path = os.path.realpath("/etc/localtime")
    return path.split("/zoneinfo/", 1)[1] if "/zoneinfo/" in path else "UTC"


def rich(v):
    """Plan text for the page: escaped, but for the <b>, <i> and <code> tags it may carry."""
    return re.sub(r"&lt;(/?)(b|i|code)&gt;", r"<\1\2>", E("" if v is None else str(v)))


def fmt(dt, f="%b %-d %-I:%M %p"):
    return dt.astimezone(TZ).strftime(f)


def iso(text):
    # no offset means UTC, as Kaggle and the plugin's tools write times
    t = datetime.fromisoformat(str(text).strip().replace("Z", "+00:00"))
    return t if t.tzinfo else t.replace(tzinfo=timezone.utc)


def sh(cmd, root, timeout=180):
    try:
        return subprocess.run(cmd, capture_output=True, encoding="utf-8", errors="replace", timeout=timeout, cwd=root,
                              env=dict(os.environ, KAGGLE_SHARE_QUIET="1")).stdout
    except Exception:
        return ""


def lb_dir(root, slug):
    base = os.environ.get("KAGGLE_LB_DIR")
    return os.path.join(os.path.expanduser(base), slug) if base else os.path.join(root, "__data", "kaggle", slug, "leaderboard")


def find_slug(root):
    """The only competition with saved leaderboard snapshots, else None."""
    base = os.environ.get("KAGGLE_LB_DIR")
    base = os.path.expanduser(base) if base else os.path.join(root, "__data", "kaggle")
    try:
        names = sorted(os.listdir(base))
    except OSError:
        return None
    found = [s for s in names if glob.glob(os.path.join(lb_dir(root, s), "*.json.gz"))]
    return found[0] if len(found) == 1 else None


def medal_places(n):
    """Last gold, silver and bronze places for n teams (Kaggle's progression table)."""
    if n < 100:
        return max(1, n // 10), max(1, n // 5), max(1, n * 2 // 5)
    if n < 250:
        return 10, n // 5, n * 2 // 5
    if n < 1000:
        return 10 + n // 500, 50, 100
    return 10 + n // 500, n // 20, n // 10


def medal_rules(n):
    """How the gold, and the silver and bronze, lines are set for n teams."""
    if n < 100:
        return "gold = top 10%", "top 20% and 40%"
    if n < 250:
        return "gold = top 10", "top 20% and 40%"
    if n < 1000:
        return "gold = top 10 plus one per 500 teams", "top 50 and 100"
    return "gold = top 10 plus one per 500 teams", "top 5% and 10%"


def num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def score(r):
    return num(r.get("score"))


def board(root, slug, team):
    """The newest full leaderboard snapshot, read for the medal lines and the team's place; None when missing."""
    if not slug:
        return None
    files = sorted(f for f in glob.glob(os.path.join(lb_dir(root, slug), "*.json.gz"))
                   if "partial" not in os.path.basename(f))
    if not files:
        return None
    try:
        with gzip.open(files[-1], "rt", encoding="utf-8") as f:
            d = json.load(f)
        rows = sorted(d["rows"], key=lambda r: r.get("rank") or 10 ** 9)
        when = iso(d["fetched_at"])
    except Exception:
        return None
    if not rows:
        return None
    n = len(rows)
    g, s, b = medal_places(n)
    at = lambda place: rows[min(place, n) - 1]
    # a board sorted best first tells the metric's direction
    high = (score(rows[0]) or 0) >= (score(rows[-1]) or 0)
    line = score(at(g))
    level = sum(1 for r in rows if line is not None and score(r) is not None
                and (score(r) >= line if high else score(r) <= line))
    me = next((r for r in rows if team and r.get("team_name") == team), None)
    return dict(n=n, when=when, me=me, top=rows[0], gold=(g, at(g)["score"]), silver=(s, at(s)["score"]),
                bronze=(b, at(b)["score"]), level=level, rules=medal_rules(n), high=high)


def submissions(gateway, root, slug):
    """The team's submissions, newest first; None when the Kaggle API does not answer."""
    if not slug:
        return None
    raw = sh([sys.executable, gateway, "competitions", "submissions", slug, "--format", "json"], root)
    try:
        rows = json.loads(raw[raw.find("["):raw.rfind("]") + 1])
    except Exception:
        # CLI 2.2.4 prints this instead of an empty JSON list
        return [] if "No submissions found" in raw else None
    out = []
    for r in rows:
        try:
            t = datetime.fromisoformat(str(r["date"])[:19]).replace(tzinfo=timezone.utc)
        except Exception:
            continue
        desc = (r.get("description") or "").strip()
        sid, sep, what = desc.partition(":")
        if not sep or len(sid.strip()) > 12:
            sid, what = "", desc
        out.append(dict(t=t, id=sid.strip(), what=what.strip() or desc, score=str(r.get("publicScore") or ""),
                        status=str(r.get("status", "")).split(".")[-1]))
    return sorted(out, key=lambda x: x["t"], reverse=True)


def gpu_week(share, gateway, root):
    """The account's GPU week and session use from kaggle_share.py, read through gateway; None when it fails."""
    raw = sh([sys.executable, share, "status", "--json", "--gateway", gateway], root)
    try:
        v = json.loads(raw[raw.find("{"):raw.rfind("}") + 1])
    except Exception:
        return None
    g = (v.get("quota") or {}).get("gpu") or {}
    return dict(used=num(g.get("used")), total=num(g.get("total")), left=num(v.get("gpu_hours_left")),
                refresh=g.get("refresh"), in_use=v.get("in_use") or {}, cap=v.get("cap") or {})


def load_schedule(plan, clock):
    daily, dated = [], []
    if clock:
        tree = ast.parse(open(clock, encoding="utf-8").read())
        val = {t.targets[0].id: ast.literal_eval(t.value) for t in tree.body if isinstance(t, ast.Assign)
               and isinstance(t.targets[0], ast.Name) and t.targets[0].id in ("DAILY", "DATED")}
        daily, dated = list(val.get("DAILY", [])), list(val.get("DATED", []))
    sch = plan.get("schedule") or {}
    return daily + list(sch.get("daily", [])), dated + list(sch.get("dated", []))


def upcoming(daily, dated, now, n=6):
    ev = []
    for dd in range(3):
        d = (now + timedelta(days=dd)).astimezone(TZ).date()
        # a clock row may carry more fields (a command, say): only the first two are read
        for hm, tag, *_ in daily:
            h, m = map(int, hm.split(":"))
            ev.append((datetime(d.year, d.month, d.day, h, m, tzinfo=TZ), tag))
    for s, tag, *_ in dated:
        ev.append((datetime.strptime(s, "%Y-%m-%d %H:%M").replace(tzinfo=TZ), tag))
    return [e for e in sorted(ev, key=lambda e: e[0]) if e[0] > now][:n]


def hosts_table(hosts_file, leases_file, now):
    """Rows of host, hardware, lease end and hours left, plus when the lease ends were read; (None, n/a) without hosts."""
    try:
        hosts = json.load(open(hosts_file, encoding="utf-8"))
    except Exception:
        return None, "n/a"
    ends, read = {}, "n/a"
    try:
        lc = json.load(open(leases_file, encoding="utf-8"))
        ends, read = lc.get("ends") or {}, fmt(iso(lc["read"]))
    except Exception:
        pass
    if not isinstance(ends, dict):
        ends = {}
    rows = []
    for h in hosts if isinstance(hosts, list) else []:
        if not isinstance(h, dict):
            continue
        end = ends.get(h.get("name"))
        try:
            t = iso(end) if end else None
        except ValueError:
            t = None
        left = (t - now).total_seconds() / 3600 if t else None
        rows.append([f'<span class="nw">{E(str(h.get("name", "")))}</span>', E(str(h.get("gpus", ""))),
                     fmt(t) if t else NA, f"{left:.0f} h" if left is not None else NA])
    return rows, read


def table(head, rows, widths, cls="", lft=()):
    L = ' class="lft"'
    th = "".join(f'<th style="width:{w}%"{L if i in lft else ""}>{h}</th>' for i, (h, w) in enumerate(zip(head, widths)))
    body = "".join("<tr>" + "".join(f'<td{L if i in lft else ""}>{c}</td>' for i, c in enumerate(r)) + "</tr>"
                   for r in rows)
    return f'<table class="{cls}"><thead><tr>{th}</tr></thead><tbody>{body}</tbody></table>'


def build(plan, now, ctx):
    """The live plan as one HTML page."""
    b, subs, gq, tzl = ctx["board"], ctx["subs"], ctx["gpu"], rich(ctx["tz_label"])
    h, m = map(int, str(plan.get("reset_utc", "00:00")).split(":"))
    now_u = now.astimezone(timezone.utc)
    kday = now_u.replace(hour=h, minute=m, second=0, microsecond=0)
    if kday > now_u:
        kday -= timedelta(days=1)
    today = [s for s in (subs or []) if s["t"] >= kday]
    pending = [s["id"] or s["what"][:24] for s in (subs or []) if s["status"] in ("PENDING", "RUNNING")]
    scored = [s for s in (subs or []) if score(s) is not None]
    best = (min if b and not b["high"] else max)(scored, key=score, default=None)
    snap = f"snapshot {fmt(b['when'])}" if b else ""

    phase = str(plan.get("phase") or "")
    head, sep, rest = phase.partition(":")
    phase_val, phase_note = (head.strip(), rest.strip()) if sep and len(head) <= 24 else ("", phase)
    me = b and b["me"]
    quota = gq and gq["used"] is not None and gq["total"] is not None
    sess = gq and gq["cap"] and E(f"; sessions in use: GPU {gq['in_use'].get('gpu', 0)}/{gq['cap'].get('gpu', '?')}, "
                                  f"CPU {gq['in_use'].get('cpu', 0)}/{gq['cap'].get('cpu', '?')}")
    refresh = ""
    if quota and gq["refresh"]:
        try:
            refresh = f", resets {fmt(iso(gq['refresh']), '%a %b %-d %-I:%M %p')}"
        except ValueError:
            pass
    head_rows = [
        ["Phase", rich(phase_val), rich(phase_note[:1].upper() + phase_note[1:])],
        ["Our public score", E(str(me["score"])) if me else NA,
         (f"Rank {E(str(me['rank']))} of {b['n']:,} teams ({snap})" if me else NA)
         + (f"; best submission {E(best['id'])}" if best and best["id"] else "")],
        ["Gold line", E(str(b["gold"][1])) if b else NA,
         f"Score at place {b['gold'][0]} ({b['rules'][0]}); {b['level']} teams at or above it, ties ranked by "
         f"submission time" if b else NA],
        ["Silver / bronze lines", f"{E(str(b['silver'][1]))} / {E(str(b['bronze'][1]))}" if b else NA,
         f"Places {b['silver'][0]} and {b['bronze'][0]} ({b['rules'][1]})" if b else NA],
        ["#1 on the board", E(str(b["top"]["score"])) if b else NA, E(str(b["top"].get("team_name", ""))) if b else NA],
        ["Kaggle day slots", f"{len(today)} of {rich(plan.get('daily_slots', 5))} used" if subs is not None else NA,
         f"Current Kaggle day: {fmt(kday)} to {fmt(kday + timedelta(days=1))} {tzl}"
         + (f"; scoring now: {', '.join(map(E, pending))}" if pending else "")],
        ["Kaggle GPU week", f"{gq['used']:.1f} of {gq['total']:.0f} h" if quota else NA,
         (f"{gq['left']:.1f} h left{refresh}" + (sess or "")) if quota and gq["left"] is not None else NA],
    ]
    parts = [table(["Metric", "Value", "Note"], head_rows, [18, 14, 68], "headline", lft=(0, 2))]

    slots = plan.get("slots") or []
    lead = rich(plan.get("day"))
    s = [f'<p>{lead + ". " if lead else ""}Slots in submission order:</p>' if slots else
         "<p>No slots planned yet.</p>"]
    if slots:
        s.append(table(["#", "Candidate", "Status", "Prediction", "Rule"],
                       [[f'<span class="nw">{rich(x.get("id"))}</span>', rich(x.get("what")), rich(x.get("status")),
                         rich(x.get("prediction")), rich(x.get("rule"))] for x in slots],
                       [6, 28, 20, 22, 24], "split", lft=(1, 2, 3, 4)))
    if plan.get("later") or plan.get("slot_rule"):
        s.append("<h3>Later Slots</h3>")
        if plan.get("slot_rule"):
            s.append(f'<p>{rich(plan["slot_rule"])}</p>')
        if plan.get("later"):
            s.append(table(["#", "Candidate", "Status", "When"],
                           [[f'<span class="nw">{rich(x.get("id"))}</span>', rich(x.get("what")), rich(x.get("status")),
                             rich(x.get("rule"))] for x in plan["later"]], [6, 38, 24, 32], "split", lft=(1, 2, 3)))
    if plan.get("fallbacks"):
        s.append(f'<p class="note">Fallbacks: {rich(plan["fallbacks"])}</p>')
    parts.append(("slots", "Submission Plan", "".join(s)))

    if plan.get("research"):
        parts.append(("research", "Research Plan", table(["Item", "What", "Status", "Gate or next step", "Compute"],
            [[f'<span class="nw">{rich(x.get("id"))}</span>', rich(x.get("what")), rich(x.get("status")),
              rich(x.get("gate")), rich(x.get("compute"))] for x in plan["research"]], [9, 31, 22, 20, 18], "split",
            lft=(0, 1, 2, 3, 4))))
    if plan.get("verdicts"):
        parts.append(("settled", "Settled Questions", table(["Item", "Verdict", "What"],
            [[f'<span class="nw">{rich(x.get("id"))}</span>', rich(x.get("verdict")), rich(x.get("what"))]
             for x in plan["verdicts"]], [12, 16, 72], "split", lft=(0, 2))))

    if subs:
        rec = table([f"Submitted ({tzl})", "#", "What", "Status", "Public"],
                    [[fmt(x["t"]), E(x["id"]), E(x["what"][:150]), E(x["status"].title()),
                      E(x["score"]) or ('<span class="pending">pending</span>'
                                        if x["status"] in ("PENDING", "RUNNING") else "")] for x in subs[:12]],
                    [15, 6, 57, 11, 11], "split compact", lft=(2,))
    elif subs is not None:
        rec = "<p>No submissions yet.</p>"
    else:
        rec = f"<p>{NA} (the Kaggle API did not answer at build time)</p>"
    parts.append(("recent", "Recent Submissions", rec))

    comp = "".join(f"<li>{rich(c)}</li>" for c in plan.get("compute") or [])
    hosts = ctx["hosts"]
    if comp or hosts:
        body = (f"<ul>{comp}</ul>" if comp else "") + (table(["Host", "Hardware", f"Lease ends ({tzl})", "Left"], hosts,
                                                             [16, 48, 22, 14], "", lft=(0, 1)) if hosts else "")
        parts.append(("compute", "Compute", body))
    labels = plan.get("labels") or {}
    if ctx["events"]:
        note = f'<p>{rich(plan["schedule_note"])}</p>' if plan.get("schedule_note") else ""
        parts.append(("schedule", "Schedule", note + table([f"When ({tzl})", "Event"],
            [[fmt(t, "%a %b %-d %-I:%M %p"), E(labels.get(g) or g.replace("-", " ").capitalize())]
             for t, g in ctx["events"]], [30, 70], "", lft=(1,))))
    if plan.get("decisions"):
        parts.append(("decisions", "Decisions", table([f"When ({tzl})", "Decision"],
            [[rich(x.get("when")), rich(x.get("text"))] for x in plan["decisions"]], [16, 84], "split", lft=(1,))))

    toc = [p for p in parts if isinstance(p, tuple)]
    rev, project = E(str(plan.get("rev") or 0)), ctx["project"]
    title = plan.get("title") or f"{project} — Live Plan"
    subtitle = plan.get("subtitle") or "Submissions and Research"
    intro = rich(plan["intro"]) if plan.get("intro") else (
        f"The current Kaggle submission and research plan of {E(project)}. It is rebuilt in place "
        f"whenever the plan changes and every hour for the live figures; the revision number rises with every "
        f"plan change. All times are {tzl} ({E(ctx['tzname'])}).")
    if plan.get("terms"):
        intro += f' Terms: {rich(plan["terms"])}'
    sources = [f"{E(ctx['plan_rel'])} (the plan, edited by hand)", f"the Kaggle leaderboard {snap or 'snapshot (n/a)'}",
               f"the team's Kaggle submissions and the account's GPU week, read at {fmt(now)}"]
    if hosts:
        sources.append(f"the lease ends (read {ctx['lease_read']})")
    if ctx["events"]:
        sources.append("the schedule")
    out = [f'''<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>{E(title)} — {E(subtitle)} — Rev. {rev}</title>
{ctx["css"]}<style>
.updated {{ color: var(--ink-1); font-size: var(--fs-sub); margin: -4pt 0 4pt 0; }}
.pending {{ color: var(--ink-muted); }}
.note {{ color: var(--ink-2); font-size: var(--fs-sub); }}
section li {{ margin: 0.2em 0; }}
section ul {{ margin: 0.4em 0 0.6em 1.6em; padding: 0; }}
section h3 {{ font-size: 8.5pt; font-weight: 600; color: var(--ink-heading); margin: 8pt 0 2pt 0; break-after: avoid; }}
table.compact th, table.compact td {{ padding: 1.6pt 4pt; }}
.nw {{ white-space: nowrap; }}
</style>
</head>
<body class="viz-root">
<main>

<h1>{E(title)}<br><span class="h1-subtitle">{E(subtitle)} — Rev. {rev}</span></h1>

<p class="updated">Updated {fmt(now, "%Y-%m-%d %-I:%M %p %Z")} — rebuilt on every plan change and every hour</p>

<p class="sub">{intro}</p>

<div class="toc" style="--toc-rows: {-(-len(toc) // 3)};">
<ol class="toc-list">
''' + "".join(f'<li><a href="#{a}">{t}</a></li>\n' for a, t, _ in toc) + """</ol>
</div>
""", parts[0]]
    for a, t, body in toc:
        out.append(f"\n<section>\n<h2 id=\"{a}\">{t}</h2>\n{body}\n</section>\n")
    out.append(f"""
<p class="foot">Sources: {", ".join(sources)}. Built by kaggle_live_plan.py (the kaggle plugin).</p>

</main>
</body>
</html>
""")
    return "".join(out)


def render(root, pack, name, env_file, tzname, started):
    """Render reports/<name>.pdf through the reporting plugin; (ok, message)."""
    script = os.path.join(root, pack, "plugins", "reporting", "assets", "render.sh")
    cmd = ["bash", "-c", ('. "$1" && ' if env_file else "") + 'exec sh "$2" "$3"', "render", env_file or "", script, name]
    try:
        r = subprocess.run(cmd, cwd=root, capture_output=True, text=True, timeout=300, env=dict(os.environ, TZ=tzname))
    except Exception as e:
        return False, f"RENDER FAILED: {e}"
    pdf = os.path.join(root, "reports", name + ".pdf")
    if r.returncode == 0 and os.path.exists(pdf) and os.path.getmtime(pdf) >= started - 5:
        return True, "rendered " + pdf
    # the cause can sit above a long Node stack trace
    return False, "RENDER FAILED: " + (r.stderr or r.stdout)[-1500:]


def write_atomic(path, text):
    """Write text through a temp file in the same folder and os.replace: a failed write keeps the old file whole."""
    path = os.path.realpath(path)
    tmp = f"{path}.{os.getpid()}.tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(text)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)


def save_rev(path, rev):
    """Set the plan file's top-level "rev", changing only that number when it has one (the owner's formatting stays)."""
    text = open(path, encoding="utf-8").read()
    want = json.loads(text)
    want["rev"] = rev
    for m in re.finditer(r'"rev"\s*:\s*(-?\d+|null)', text):
        new = text[:m.start(1)] + str(rev) + text[m.end(1):]
        if json.loads(new) == want:
            break
    else:
        new = json.dumps(want, indent=2, ensure_ascii=False) + "\n"
    write_atomic(path, new)


def main(argv=None):
    global TZ
    ap = argparse.ArgumentParser(description="Build a Kaggle project's live plan: HTML, then a PDF through the "
                                             "reporting plugin when attached.")
    ap.add_argument("--no-render", action="store_true", help="write the HTML only")
    ap.add_argument("--keep-rev", action="store_true", help="rebuild under the same rev (fresh live figures)")
    ap.add_argument("--out", help="write a preview HTML here instead: no render, the plan untouched")
    ap.add_argument("--root", help="project root (default: found around the working directory)")
    ap.add_argument("--plan", help="plan JSON (default: <root>/submissions/live-plan.json)")
    ap.add_argument("--slug", help="competition slug (default: the plan's, else the only one with saved snapshots)")
    ap.add_argument("--team", help="team name on the leaderboard (default: the plan's \"team\")")
    ap.add_argument("--name", help="report name (default: the plan's \"name\", else live-plan)")
    ap.add_argument("--tz", help="timezone for every time shown (default: the plan's, else $TZ or this machine's)")
    ap.add_argument("--render-env", help="shell file sourced before render.sh (default: the plan's \"render_env\")")
    ap.add_argument("--clock", help="Python file with DAILY and DATED schedule lists, read with ast")
    ap.add_argument("--hosts", help="host list (default: <pack>/.memory/hosts.json)")
    ap.add_argument("--leases", help="lease ends (default: <pack>/.memory/lease-ends.json)")
    ap.add_argument("--gateway", help="Kaggle gateway (default: the kaggle.py beside this file)")
    a = ap.parse_args(argv)

    root = os.path.abspath(a.root) if a.root else find_root()
    if not root:
        print("kaggle_live_plan: no project here - run from a project root or pass --root", file=sys.stderr)
        return 2
    pack = pack_of(root)
    plan_path = os.path.abspath(a.plan or os.path.join(root, "submissions", "live-plan.json"))
    try:
        plan = json.load(open(plan_path, encoding="utf-8"))
    except (OSError, ValueError) as e:
        print(f"kaggle_live_plan: cannot read the plan {plan_path}: {e}", file=sys.stderr)
        return 2
    if not isinstance(plan, dict):
        print(f"kaggle_live_plan: the plan {plan_path} is not a JSON object", file=sys.stderr)
        return 2
    # the raised rev is saved only once the page is written
    bump = not a.out and (not a.keep_rev or not plan.get("rev"))

    tzname = a.tz or plan.get("timezone") or local_zone()
    try:
        TZ = ZoneInfo(tzname)
    except Exception:
        print(f"kaggle_live_plan: unknown timezone {tzname!r}, using UTC", file=sys.stderr)
        tzname, TZ = "UTC", timezone.utc
    now = datetime.now(timezone.utc)
    slug = a.slug or plan.get("competition") or find_slug(root)
    team = a.team or plan.get("team")
    name = a.name or plan.get("name") or "live-plan"
    gateway = os.path.abspath(a.gateway) if a.gateway else os.path.join(HERE, "kaggle.py")
    share = os.path.join(os.path.dirname(gateway), "kaggle_share.py")
    if not os.path.isfile(share):
        share = os.path.join(HERE, "kaggle_share.py")
    mem = os.path.join(root, pack, ".memory") if pack else root
    hosts, lease_read = hosts_table(a.hosts or os.path.join(mem, "hosts.json"),
                                    a.leases or os.path.join(mem, "lease-ends.json"), now)
    clock = a.clock or (os.path.join(root, plan["clock"]) if plan.get("clock") else None)
    if not slug:
        print("kaggle_live_plan: no competition slug (pass --slug or set the plan's \"competition\"): "
              "board and submissions show n/a", file=sys.stderr)

    out = os.path.abspath(a.out) if a.out else os.path.join(root, "reports", "html", name + ".html")
    out_dir = os.path.dirname(out)
    os.makedirs(out_dir, exist_ok=True)
    assets = os.path.join(root, pack, "plugins", "reporting", "assets") if pack else ""
    reporting = bool(assets) and os.path.isfile(os.path.join(assets, "render.sh"))
    if reporting:
        css = f'<link rel="stylesheet" href="{E(os.path.relpath(os.path.join(assets, "style.css"), out_dir))}">\n'
        theme = os.path.join(root, "reports", "theme.css")
        if os.path.isfile(theme):
            css += f'<link rel="stylesheet" href="{E(os.path.relpath(theme, out_dir))}">\n'
    else:
        css = f"<style>{FALLBACK_CSS}</style>\n"

    try:
        if bump:
            plan["rev"] = int(plan.get("rev") or 0) + 1
        daily, dated = load_schedule(plan, clock)
        ctx = dict(board=board(root, slug, team), subs=submissions(gateway, root, slug),
                   gpu=gpu_week(share, gateway, root), hosts=hosts, lease_read=lease_read,
                   events=upcoming(daily, dated, now), project=project_name(root, pack), tzname=tzname,
                   tz_label=plan.get("tz_label") or now.astimezone(TZ).strftime("%Z"), css=css,
                   plan_rel=os.path.relpath(plan_path, root))
        page = build(plan, now, ctx)
    except Exception as e:
        # a bad hand edit in the plan or the clock: the last page and the plan's rev stay as they were
        print(f"kaggle_live_plan: cannot build from {plan_path}, nothing written: {type(e).__name__}: {e}",
              file=sys.stderr)
        return 2
    write_atomic(out, page)
    if bump:
        try:
            save_rev(plan_path, plan["rev"])
        except Exception as e:
            print(f"kaggle_live_plan: wrote {out} but could not save rev {plan['rev']} in {plan_path}: {e}",
                  file=sys.stderr)
            return 2
    print(f"Rev. {plan.get('rev') or 0}: {out}")
    if a.out or a.no_render:
        return 0
    if not reporting:
        print("the reporting plugin is not attached: HTML only")
        return 0
    env_file = a.render_env or plan.get("render_env")
    # joined to the root: bash's "." looks a bare name up on PATH first
    env_file = os.path.join(root, os.path.expanduser(env_file)) if env_file else None
    ok, msg = render(root, pack, name, env_file, tzname, now.timestamp())
    print(msg)
    return 0 if ok else 1


def project_name(root, pack):
    try:
        return json.load(open(os.path.join(root, pack, "manifest.json"), encoding="utf-8"))["project"]["name"]
    except Exception:
        return os.path.basename(root)


if __name__ == "__main__":
    sys.exit(main())
