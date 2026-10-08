# rev. 2

"""kaggle_status: the single status page of a Kaggle competition project.

The one page the owner reads (it replaces the live plan and the phase progress reports): the current state, the
leaderboard progress of the team and the top teams, the plan and timeline, the spending, the resources and what they
are used for, the open questions and the suggestions. The master edits reports/status.json by hand; this tool adds
the live figures, raises the JSON's rev, writes the HTML and renders the PDF.

    python3 <plugin-dir>/tools/kaggle_status.py                 raise the rev, write the HTML, render the PDF
    python3 <plugin-dir>/tools/kaggle_status.py --keep-rev      the hourly rebuild: fresh live figures, same rev
    python3 <plugin-dir>/tools/kaggle_status.py --no-render     the same, HTML only
    python3 <plugin-dir>/tools/kaggle_status.py --offline       no Kaggle calls: the saved board snapshots only
    python3 <plugin-dir>/tools/kaggle_status.py --out FILE      a preview: HTML only, offline, the project untouched

Run it from the project root (or pass --root), unsandboxed: it reads the Kaggle API through the gateway and renders
with Chrome (about 10 s). It writes <root>/reports/html/status.html and, when the reporting plugin is attached
(<pack>/plugins/reporting/assets/render.sh), renders <root>/reports/status.pdf: a living document, so each build
stops at the render and its layout check. When the check finds a half-empty page (a chart that did not fit moved on),
the page is rendered once more with shorter charts (reports/status-short.pdf beside it) and the one with fewer such
pages kept. The page carries private operations data (machines, spend): the project keeps it out of git with
reports/status*.pdf and reports/html/status*.html in .gitignore, which also cover the shorter-chart try a killed build
can leave (the next build removes it); it reaches the owner through file sync.

Found or passed as flags:
  root        --root, else the nearest folder up from the working directory (never the home folder or one above it)
              with exactly one child folder <pack> holding an ai-pack manifest.json (<pack> is the project's ai-pack
              folder: default aipack/, ai/ in older projects, any name; two such folders are an error), else the
              project of a copied overlay (<project>/<pack>/plugins/kaggle/tools/)
  status      --status, else <root>/reports/status.json
  slug        --slug, else the JSON's "competition", else the only competition with saved leaderboard snapshots
              (kaggle_lb.py's store: <root>/__data/kaggle/<slug>/leaderboard/, or $KAGGLE_LB_DIR/<slug>/)
  team        --team, else the JSON's "team": the team name on the board (without it the team's place is n/a)
  timezone    --tz, else the JSON's "timezone", else $TZ, else this machine's zone; every time shown is in it
  gateway     --gateway, else the kaggle.py beside this file; kaggle_share.py is taken from beside the gateway
  render env  the JSON's "render_env", relative to the root: a shell file sourced before render.sh (one that puts
              node and Chrome on PATH, say)
  schedule    the JSON's "schedule", plus its "clock" (relative to the root): a Python file whose top-level DAILY and
              DATED list literals are read with ast, never run; every schedule time is in the timezone above
  hosts       <pack>/.memory/hosts.json (the resource-sharing inventory, a list or one under "hosts": name and gpus
              are read)
  lease ends  <pack>/.memory/lease-ends.json: {"read": "<ISO UTC>", "ends": {"<host>": "<ISO UTC>"}}
  costs       <pack>/.memory/spend.jsonl, one JSON line per cost other than Claude Code:
              {"day": "YYYY-MM-DD", "category": "cloud GPU", "usd": 1.5, "what": "<what it paid for>"}
  Claude      the project's Claude Code spend per day from solaris.tools.ai_spend (list-price estimates), run in the
              Solaris checkout found above the root (the nearest folder holding solaris/tools/ai_spend.py); n/a
              without one
  ideas       the newest entries of <pack>/.memory/improvements.md: the bold title of each top-level "- **...**"

Live data is read at build time and shown as n/a when a source fails: the newest full leaderboard snapshot (first a
fresh read through kaggle_lb.py when the newest saved one is over 30 minutes old), the team's submissions
(`competitions submissions <slug> --format json`; a description "<id>: <what>" names the submission) and the
account's GPU week and sessions (`kaggle_share.py status --json`). --offline and a preview (--out) skip all three
Kaggle reads and draw from the saved snapshots: they never call the gateway (which can create or rebuild the
project's Kaggle venv).

Status JSON. Every field is optional. Text fields may carry <b>, <i> or <code>; any other markup in them, names
and live data are escaped.
  {
    "rev": 1,                                  raised by every build unless --keep-rev or --out
    "title": "<Competition>",                  the H1, plain text (default: the project name)
    "subtitle": "Project Status",              under the title, before the rev
    "intro": "...",                            the paragraph under the title (a generic one by default)
    "competition": "<slug>", "team": "<team name>", "timezone": "<IANA zone>", "tz_label": "PT",
    "render_env": "<file>", "clock": "<file>", see above
    "daily_slots": 5, "reset_utc": "00:00",    the day's submission limit and its reset time in UTC
    "start": "YYYY-MM-DD",                     the project's first day, for the spend totals (default: the first
                                               saved board or cost)
    "phase": "Phase 2: <goal>",                the text before the first colon is the headline's value
    "summary": "...",                          the state in a few plain sentences, under the headline table
    "notes": ["...", ...],
    "owner_actions": ["...", ...],             what the owner must do (none: nothing)
    "plan": [{"when": "YYYY-MM-DD HH:MM", "what": "...", "status": "done"}],   when: a day or a time, else any
                                               text; done items older than three days are counted, not listed
    "schedule": {"daily": [["HH:MM", "<tag>"], ...], "dated": [["YYYY-MM-DD HH:MM", "<tag>"], ...]},
    "labels": {"<tag>": "<label>"},            schedule labels (default: the tag, dashes as spaces)
    "daily_budget_usd": 60,                    the whole day's spend budget, drawn on the spending chart
    "resources": [{"name": "...", "kind": "machine", "what": "...", "from": "...", "until": "...",
                   "cost": "$2.10 an hour", "status": "...", "uses": [{"when": "Oct 3", "what": "..."}]}],
                                               a host from hosts.json or lease-ends.json joins its row by name
    "questions": ["...", {"text": "...", "since": "...", "note": "..."}],
    "suggestions": [{"kind": "framework|procedure|resources", "text": "..."}],
    "top_teams": 8                             teams drawn beside ours, the best on the newest board
  }

A minimal example:
  {"competition": "my-competition", "team": "My Team", "timezone": "America/Los_Angeles",
   "phase": "Phase 1: a first honest baseline",
   "plan": [{"when": "2026-10-06 09:00", "what": "Submit the baseline", "status": "pending"}]}

Exit codes: 0 built (and rendered when the plugin is attached), 1 the render failed, 2 bad usage or a status JSON
that does not build (nothing written then). Stdlib only.
"""
import argparse
import ast
import glob
import gzip
import html
import json
import math
import os
import re
import shutil
import subprocess
import sys
from datetime import date, datetime, time as dtime, timedelta, timezone
from zoneinfo import ZoneInfo

HERE = os.path.dirname(os.path.abspath(__file__))
E = html.escape
NA = '<span class="pending">n/a</span>'
TZ = timezone.utc  # set in main()
NAME = "status"
# the shorter-chart try renders beside the page: the page's .gitignore patterns (reports/status*.pdf,
# reports/html/status*.html) cover its files too, and a build first removes any a killed build left
SHORT = NAME + "-short"
# a saved board newer than this is not read again; at most this many boards are drawn, spread over the history
FRESH_MINUTES = 30
MAX_BOARDS = 200
# teams drawn beside ours, days of spending bars, improvement titles listed
TOP_TEAMS = 8
SPEND_DAYS = 14
IDEAS = 5
# plan items with such a status are done; older ones are counted, not listed
DONE_RE = re.compile(r"\s*(done|complete|finished|cancel|dropped|skipped|superseded|retired)", re.I)
# color-blind-safe series colors (the Okabe-Ito palette); team lines skip its orange, too close to the gold line
COLORS = ("#0072b2", "#e69f00", "#009e73", "#d55e00", "#56b4e9", "#cc79a7", "#7f7f7f", "#8c6d31")
TEAM_COLORS = ("#0072b2", "#009e73", "#d55e00", "#56b4e9", "#cc79a7", "#7f7f7f", "#8c6d31", "#2b8c8c")
MEDALS = (("gold", "#c9a227"), ("silver", "#8c959d"), ("bronze", "#b0703c"))
GRID, AXIS = "#e4e3df", "#555555"
# chart heights in a 760-wide viewBox: the normal layout, and the shorter one tried when a chart that did not fit
# left a half-empty page
HEIGHTS = {"score": (236, 176), "rank": (132, 100), "spend": (168, 124)}

# used only when the reporting plugin is not attached
FALLBACK_CSS = """
.viz-root { --ink-1: #222; --ink-2: #555; --ink-muted: #888; --ink-heading: #111; --fs-sub: 0.9em; --accent: #6a1b9a; }
body { font: 10pt/1.4 system-ui, sans-serif; color: #222; margin: 2em auto; max-width: 64em; }
table { border-collapse: collapse; width: 100%; margin: 0.4em 0 0.8em 0; }
th, td { border-bottom: 1px solid #ddd; padding: 2pt 4pt; text-align: center; vertical-align: top; }
th.lft, td.lft { text-align: left; }
.h1-subtitle { font-size: 0.6em; font-weight: normal; }
figure { margin: 1em 0; }
figcaption { font-size: 0.85em; color: #888; font-style: italic; text-align: center; }
.diagram-key { display: flex; flex-wrap: wrap; gap: 10pt; font-size: 0.85em; color: #555; }
.diagram-key span { display: inline-flex; align-items: center; gap: 4pt; }
.sw { width: 7pt; height: 7pt; display: inline-block; }
"""
STYLE = """
.updated { color: var(--ink-1); font-size: var(--fs-sub); margin: -4pt 0 4pt 0; }
.pending { color: var(--ink-muted); }
.note { color: var(--ink-2); font-size: var(--fs-sub); }
section li { margin: 0.2em 0; }
section ul, section ol { margin: 0.4em 0 0.6em 1.6em; padding: 0; }
section h3 { font-size: 8.5pt; font-weight: 600; color: var(--ink-heading); margin: 8pt 0 2pt 0; break-after: avoid; }
table.compact th, table.compact td { padding: 1.6pt 4pt; }
.nw { white-space: nowrap; }
svg .me { stroke: var(--accent); }
svg text.me { fill: var(--accent); stroke: none; font-weight: 600; }
svg .now { stroke: var(--accent); }
.diagram-key .ln { display: inline-block; width: 13pt; height: 0; border-top: 1.4pt solid; }
.diagram-key .ln.dash { border-top-style: dashed; }
.diagram-key .ln.me { border-top: 2.4pt solid var(--accent); }
"""


class PackError(Exception):
    """A folder with more than one ai-pack."""


def home_or_above(d):
    # the home folder, a folder above it, or /: the walk up never reads these (on macOS ~/Desktop can raise
    # privacy prompts, and an automounted /home is slow)
    if os.path.dirname(d) == d:
        return True
    home = os.path.expanduser("~")
    homes = (os.path.abspath(home), os.path.realpath(home)) if os.path.isabs(home) else ()
    return any(h == d or h.startswith(d + os.sep) for h in homes)


def find_root(start=None):
    """The project root around start (a folder with an ai-pack, see pack_of), else a copied overlay's project."""
    d = os.path.abspath(start or os.getcwd())
    while not home_or_above(d):
        if pack_of(d):
            return d
        d = os.path.dirname(d)
    # a copied overlay sits at <project>/<pack>/plugins/kaggle/tools/; a project root at home or above is never listed
    plugins = os.path.dirname(os.path.dirname(HERE))
    pack = os.path.dirname(plugins)
    if (os.path.basename(plugins) == "plugins" and not home_or_above(os.path.dirname(pack)) and is_pack(pack)
            and pack_of(os.path.dirname(pack)) == os.path.basename(pack)):
        return os.path.dirname(pack)
    return None


def is_pack(d):
    # an ai-pack manifest carries framework_version and a project object; a plugin's has neither
    try:
        with open(os.path.join(d, "manifest.json"), encoding="utf-8") as f:
            m = json.load(f)
    except (OSError, ValueError):
        return False
    return isinstance(m, dict) and "framework_version" in m and isinstance(m.get("project"), dict)


def pack_of(root):
    """The name of root's ai-pack folder: its one direct child folder holding an ai-pack manifest.json, else None."""
    try:
        packs = sorted(n for n in os.listdir(root) if not n.startswith(".") and is_pack(os.path.join(root, n)))
    except OSError:
        return None
    if len(packs) > 1:
        raise PackError(f"{root}: more than one ai-pack ({', '.join(packs)})")
    return packs[0] if packs else None


def read_text(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


def project_name(root, pack):
    try:
        return json.loads(read_text(os.path.join(root, pack, "manifest.json")))["project"]["name"]
    except Exception:
        return os.path.basename(root)


def local_zone():
    tz = (os.environ.get("TZ") or "").lstrip(":")
    if tz:
        return tz
    path = os.path.realpath("/etc/localtime")
    return path.split("/zoneinfo/", 1)[1] if "/zoneinfo/" in path else "UTC"


# text and time

def rich(v):
    """JSON text for the page: escaped, but for the <b>, <i> and <code> tags it may carry."""
    return re.sub(r"&lt;(/?)(b|i|code)&gt;", r"<\1\2>", E("" if v is None else str(v)))


def fmt(dt, f="%b %-d %-I:%M %p"):
    return dt.astimezone(TZ).strftime(f)


def iso(text):
    # no offset means UTC, as Kaggle and the plugin's tools write times
    t = datetime.fromisoformat(str(text).strip().replace("Z", "+00:00"))
    return t if t.tzinfo else t.replace(tzinfo=timezone.utc)


def num(v):
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def score(r):
    return num(r.get("score"))


def usd(v):
    return f"${v:,.0f}" if abs(v) >= 100 or v == int(v) else f"${v:,.2f}"


def places(*texts):
    # as many decimals as the board shows
    return max((len(str(t).split(".")[1]) for t in texts if "." in str(t)), default=3)


def sh(cmd, cwd, timeout=180):
    try:
        return subprocess.run(cmd, capture_output=True, encoding="utf-8", errors="replace", timeout=timeout, cwd=cwd,
                              env=dict(os.environ, KAGGLE_SHARE_QUIET="1")).stdout
    except Exception:
        return ""


def warn(msg):
    print(f"kaggle_status: {msg}", file=sys.stderr)


def safe(what, fn, *args, default=None):
    """fn(*args), or default when it fails: a failed source shows n/a, never stops the build."""
    try:
        return fn(*args)
    except Exception as e:
        warn(f"{what}: n/a ({type(e).__name__}: {e})")
        return default


# the leaderboard: kaggle_lb.py's saved snapshots

def lb_dir(root, slug):
    base = os.environ.get("KAGGLE_LB_DIR")
    if base:
        return os.path.join(os.path.expanduser(base), slug)
    return os.path.join(root, "__data", "kaggle", slug, "leaderboard")


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


def higher_is_better(rows):
    # a board sorted best first tells the metric's direction
    vals = [v for v in (score(r) for r in rows) if v is not None]
    return not (len(vals) > 1 and vals[0] < vals[-1])


def newest_saved(root, slug):
    """When the newest full snapshot was saved, by its file name (the UTC time of the read), else None."""
    try:
        names = sorted(f for f in os.listdir(lb_dir(root, slug)) if f.endswith(".json.gz") and "partial" not in f)
        return datetime.strptime(names[-1][:16], "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc) if names else None
    except (OSError, ValueError):
        return None


def refresh_board(root, slug, gateway, now):
    """A fresh board snapshot through kaggle_lb.py when the newest saved one is older than FRESH_MINUTES; an error
    text when that read failed, else None."""
    last = newest_saved(root, slug)
    if last and (now - last).total_seconds() < FRESH_MINUTES * 60:
        return None
    try:
        r = subprocess.run([sys.executable, os.path.join(HERE, "kaggle_lb.py"), "snapshot", slug, "--gateway", gateway],
                           cwd=root, capture_output=True, encoding="utf-8", errors="replace", timeout=900,
                           env=dict(os.environ, KAGGLE_SHARE_QUIET="1"))
    except Exception as e:
        return f"the fresh board read failed: {e}"
    if r.returncode != 0:
        return "the fresh board read failed: " + " ".join((r.stderr or r.stdout).split())[-200:]
    return None


def load_boards(root, slug, cap=MAX_BOARDS):
    """The saved full boards, oldest first: [{"t", "rows" in rank order, "n", "saved"}], at most cap of them spread
    evenly over the history (the newest always); "saved" counts all of them. [] without any."""
    if not slug:
        return []
    d = lb_dir(root, slug)
    try:
        # a file name starts with the UTC time of its read; a partial read says so in its name
        names = sorted(f for f in os.listdir(d) if f.endswith(".json.gz") and "partial" not in f)
    except OSError:
        return []
    saved = len(names)
    if len(names) > cap:
        names = [names[round(i * (len(names) - 1) / (cap - 1))] for i in range(cap)]
    boards = []
    for name in names:
        try:
            with gzip.open(os.path.join(d, name), "rt", encoding="utf-8") as f:
                snap = json.load(f)
            rows = [r for r in snap["rows"] if isinstance(r, dict)]
            for i, r in enumerate(rows):
                r.setdefault("rank", None)
                r["rank"] = r["rank"] or i + 1
            rows.sort(key=lambda r: r["rank"])
            t = iso(snap["fetched_at"])
        except Exception:
            continue
        if rows and not snap.get("partial"):
            boards.append({"t": t, "rows": rows, "n": len(rows), "saved": saved})
    return sorted(boards, key=lambda b: b["t"])


def team_id(boards, team):
    """Our team's id on the newest board that lists the team name, else None (a renamed team keeps its id)."""
    for b in reversed(boards if team else []):
        for r in b["rows"]:
            if r.get("team_name") == team:
                return r.get("team_id")
    return None


def finder(boards, team):
    tid = team_id(boards, team)
    if tid is not None:
        return lambda rows: next((r for r in rows if r.get("team_id") == tid), None)
    return lambda rows: next((r for r in rows if team and r.get("team_name") == team), None)


def board_now(boards, team):
    """The newest board for the headline: size, time, our row and a day before, the #1, the medal lines; None
    without boards."""
    if not boards:
        return None
    ours = finder(boards, team)
    last = boards[-1]
    rows, n = last["rows"], last["n"]

    def at(place):
        return rows[min(place, n) - 1]

    before = next((b for b in reversed(boards) if b["t"] <= last["t"] - timedelta(hours=24)), None)
    return dict(n=n, when=last["t"], me=ours(rows), was=ours(before["rows"]) if before else None,
                was_when=before["t"] if before else None, top=rows[0], lines=[(p, at(p).get("score"))
                                                                             for p in medal_places(n)],
                rules=medal_rules(n), high=higher_is_better(rows), count=len(boards), saved=last["saved"])


def progress(boards, team, top):
    """The leaderboard over time: the top teams of the newest board (ours left out) and, per board, its time, size,
    medal places and scores, our rank and score, and the top teams' scores."""
    ours = finder(boards, team)
    me_now = ours(boards[-1]["rows"])
    leaders = [r for r in boards[-1]["rows"] if r is not me_now][:top]
    ids = [r.get("team_id") for r in leaders]
    series = []
    for b in boards:
        rows, n = b["rows"], b["n"]
        by_id = {r.get("team_id"): r for r in rows}
        me = ours(rows)
        series.append({"t": b["t"], "n": n,
                       "medals": [(p, score(rows[min(p, n) - 1])) for p in medal_places(n)],
                       "me": (me["rank"], score(me), str(me.get("score"))) if me else None,
                       "top": [score(by_id[i]) if i in by_id else None for i in ids]})
    return {"leaders": leaders, "series": series, "high": higher_is_better(boards[-1]["rows"]),
            "me": me_now}


# the Kaggle reads, through the gateway

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
    for r in rows if isinstance(rows, list) else []:
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


# local sources

def load_schedule(st, clock):
    daily, dated = [], []
    if clock:
        tree = ast.parse(read_text(clock))
        val = {t.targets[0].id: ast.literal_eval(t.value) for t in tree.body if isinstance(t, ast.Assign)
               and isinstance(t.targets[0], ast.Name) and t.targets[0].id in ("DAILY", "DATED")}
        daily, dated = list(val.get("DAILY", [])), list(val.get("DATED", []))
    sch = st.get("schedule") or {}
    return daily + list(sch.get("daily", [])), dated + list(sch.get("dated", []))


def upcoming(daily, dated, now, n=8):
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


def read_ledger(path):
    """([(day, category, usd, what)], lines that could not be read) of the cost ledger; (None, 0) without one."""
    try:
        lines = read_text(path).splitlines()
    except OSError:
        return None, 0
    out, bad = [], 0
    for line in lines:
        if not line.strip():
            continue
        try:
            d = json.loads(line)
            day = date.fromisoformat(str(d["day"])[:10])
            cost = num(d["usd"])
            if cost is None:
                raise ValueError("no amount")
        except Exception:
            bad += 1
            continue
        out.append((day, " ".join(str(d.get("category") or "other").split()) or "other", cost,
                    " ".join(str(d.get("what") or "").split())))
    return out, bad


def solaris_root(root):
    """The Solaris checkout holding the project: the nearest folder at or above it with solaris/tools/ai_spend.py."""
    d = os.path.abspath(root)
    while not home_or_above(d):
        if os.path.isfile(os.path.join(d, "solaris", "tools", "ai_spend.py")):
            return d
        d = os.path.dirname(d)
    return None


def ai_spend(root, args, parse):
    """parse(the JSON report of solaris.tools.ai_spend --dir <root> <args> --json), run in the Solaris checkout above
    the root: through uv when it is on PATH, else with this Python. The first run whose report parse takes (parse
    raises on one it cannot use) gives the result; None without a checkout or when every run fails. kaggle_hourly.py
    runs ai_spend through this too."""
    sol = solaris_root(root)
    if not sol:
        return None
    argv = ["-m", "solaris.tools.ai_spend", "--dir", os.path.abspath(root), *args, "--json"]
    uv = shutil.which("uv")
    cmds = ([[uv, "run", "--directory", sol, *argv]] if uv else []) + [[sys.executable, *argv]]
    for cmd in cmds:
        raw = sh(cmd, sol, timeout=300)
        try:
            return parse(json.loads(raw[raw.find("{"):raw.rfind("}") + 1]))
        except Exception:
            continue
    return None


def claude_spend(root, since):
    """{"days": {day: usd}, "limit": the daily limit or None} of the project's Claude Code spend (list-price
    estimates from solaris.tools.ai_spend); None without a Solaris checkout or when it fails."""
    def parse(v):
        days = {}
        for r in v["rows"]:
            day = date.fromisoformat(r["day"])
            days[day] = days.get(day, 0.0) + float(r["usd"])
        limit = next((num(b.get("daily_budget_usd")) for b in v.get("budgets") or [] if isinstance(b, dict)), None)
        return {"days": days, "limit": limit}

    return ai_spend(root, ["--since", since.isoformat()], parse)


def read_hosts(path):
    """The host list's entries with a name, else None. The list may stand alone or sit under "hosts", as
    hostclaims.py reads it."""
    try:
        hosts = json.loads(read_text(path))
    except Exception:
        return None
    if isinstance(hosts, dict):
        hosts = hosts.get("hosts")
    return [h for h in hosts if isinstance(h, dict) and h.get("name")] if isinstance(hosts, list) else None


def read_leases(path):
    """({host: lease end}, when they were read); ({}, None) without the file."""
    try:
        lc = json.loads(read_text(path))
        ends = lc.get("ends") or {}
    except Exception:
        return {}, None
    out = {}
    for k, v in ends.items() if isinstance(ends, dict) else ():
        try:
            out[str(k)] = iso(v)
        except (TypeError, ValueError):
            pass
    try:
        read = iso(lc["read"])
    except Exception:
        read = None
    return out, read


def read_ideas(path, n=IDEAS):
    """The bold titles of the newest top-level "- **...**" entries of improvements.md, newest first; None when the
    file cannot be read."""
    try:
        text = read_text(path)
    except (OSError, UnicodeDecodeError):
        return None
    titles, block, fence = [], None, False

    def close():
        m = re.match(r"- \*\*(.+?)\*\*", " ".join(block or []), re.S)
        if m:
            titles.append(" ".join(m.group(1).split()))

    for line in text.splitlines():
        if line.lstrip().startswith("```"):
            fence = not fence
        if fence or line.lstrip().startswith("```"):
            continue
        if line.startswith((" ", "\t")) and line.strip() and block is not None:
            block.append(line.strip())
            continue
        close()
        block = [line] if line.startswith("- ") else None
    close()
    return titles[-n:][::-1]


# charts: static inline SVG in a 760-wide viewBox

def nice_ticks(lo, hi, most=7):
    """At most `most` round ticks covering lo..hi, the tightest such, and the decimals they need."""
    if hi - lo < 1e-12:
        pad = abs(lo) * 0.05 or 0.5
        lo, hi = lo - pad, hi + pad
    mag = 10 ** math.floor(math.log10((hi - lo) / (most - 1)))
    for m in (1, 2, 2.5, 5, 10, 20, 25, 50):
        step = m * mag
        a, b = math.floor(lo / step + 1e-9) * step, math.ceil(hi / step - 1e-9) * step
        if round((b - a) / step) <= most - 1:
            break
    ticks = [a + i * step for i in range(round((b - a) / step) + 1)]
    return ticks, max(0, -math.floor(math.log10(step) + 1e-9) + (1 if m in (2.5, 25) else 0))


def time_ticks(t0, t1, most=8):
    """(time, label) ticks between t0 and t1: midnights over a span of days, else hours (a midnight shows its day)."""
    span = (t1 - t0).total_seconds() / 3600
    out = []
    if span > 36:
        step = next((d for d in (1, 2, 3, 7, 14, 28) if span / 24 / d <= most), 56)
        d = t0.astimezone(TZ).date()
        while True:
            t = datetime.combine(d, dtime(), tzinfo=TZ)
            if t > t1:
                return out
            if t >= t0:
                out.append((t, t.strftime("%b %-d")))
            d += timedelta(days=step)
    step = next((h for h in (1, 2, 3, 6, 12) if span / h <= most), 24)
    t = t0.astimezone(TZ).replace(minute=0, second=0, microsecond=0)
    while t <= t1:
        if t >= t0 and t.hour % step == 0:
            out.append((t, t.strftime("%b %-d") if t.hour == 0 else t.strftime("%-I %p")))
        t += timedelta(hours=1)
    return out


def path_d(pts):
    """An SVG path through the points, broken where a point is None."""
    d, pen = [], False
    for p in pts:
        if p is None:
            pen = False
            continue
        d.append(f"{'L' if pen else 'M'}{p[0]:.1f},{p[1]:.1f}")
        pen = True
    return "".join(d)


def right_labels(x, labels, lo, hi, gap=10.0):
    """Labels [(y, text, fill, class)] at the right edge, moved apart by gap and kept within lo..hi."""
    labels = sorted(labels, key=lambda lab: lab[0])
    ys = []
    for y, *_ in labels:
        ys.append(max(y, ys[-1] + gap) if ys else max(y, lo))
    for i in range(len(ys) - 1, -1, -1):
        ys[i] = min(ys[i], hi if i == len(ys) - 1 else ys[i + 1] - gap)
    return [text(x, y, s, fill=fill, cls=cls) for y, (_, s, fill, cls) in zip(ys, labels)]


def svg_open(w, h, label):
    return (f'<svg viewBox="0 0 {w} {h}" width="100%" role="img" aria-label="{E(label)}" '
            f'xmlns="http://www.w3.org/2000/svg">')


def text(x, y, s, anchor="start", size=9, fill=AXIS, cls=""):
    c = f' class="{cls}"' if cls else f' fill="{fill}"'
    return f'<text x="{x:.1f}" y="{y:.1f}" font-size="{size}" text-anchor="{anchor}"{c}>{s}</text>'


def hline(x0, x1, y, color=GRID, width=0.8, dash=""):
    d = f' stroke-dasharray="{dash}"' if dash else ""
    return f'<line x1="{x0:.1f}" x2="{x1:.1f}" y1="{y:.1f}" y2="{y:.1f}" stroke="{color}" stroke-width="{width}"{d}/>'


def vline(x, y0, y1, color=GRID, width=0.8, dash="", cls=""):
    d = f' stroke-dasharray="{dash}"' if dash else ""
    c = f' class="{cls}"' if cls else f' stroke="{color}"'
    return f'<line x1="{x:.1f}" x2="{x:.1f}" y1="{y0:.1f}" y2="{y1:.1f}"{c} stroke-width="{width}"{d}/>'


def score_chart(pg, H=HEIGHTS["score"][0]):
    """Score over time: ours (accent, thick, the latest value labelled), the top teams (thin, muted) and the medal
    lines (dashed)."""
    series, high = pg["series"], pg["high"]
    W, L, R, T, B = 760, 46, 92, 8, 18
    pw, ph = W - L - R, H - T - B
    t0, t1 = series[0]["t"], series[-1]["t"]
    if t1 - t0 < timedelta(hours=1):
        t0, t1 = t0 - timedelta(hours=12), t1 + timedelta(hours=12)

    def X(t):
        return L + pw * (t - t0).total_seconds() / (t1 - t0).total_seconds()

    mine = [s["me"][1] for s in series if s["me"] and s["me"][1] is not None]
    medal = [[s["medals"][k][1] for s in series] for k in range(3)]
    gold = [v for v in medal[0] if v is not None]
    bronze = [v for v in medal[2] if v is not None]
    tops = [v for s in series for v in s["top"] if v is not None]
    # the range spans us, the medal lines and the best teams; older, weaker scores of the top teams are clipped
    if high:
        lo, hi = min(mine + bronze), max(mine + tops + gold)
    else:
        lo, hi = min(mine + tops + gold), max(mine + bronze)
    ticks, dec = nice_ticks(lo, hi)
    y0, y1 = ticks[0], ticks[-1]

    def Y(v):
        return T + ph * (1 - (v - y0) / (y1 - y0))

    out = [svg_open(W, H, "Public score over time"),
           f'<defs><clipPath id="clip-score"><rect x="{L}" y="{T}" width="{pw}" height="{ph}"/></clipPath></defs>']
    for v in ticks:
        out += [hline(L, L + pw, Y(v)), text(L - 5, Y(v) + 3, f"{v:.{dec}f}", "end")]
    for t, lab in time_ticks(t0, t1):
        out += [vline(X(t), T, T + ph, "#f0efeb"), text(X(t), H - 5, lab, "middle")]
    out.append('<g clip-path="url(#clip-score)" fill="none" stroke-linejoin="round" stroke-linecap="round">')
    for i in range(len(pg["leaders"])):
        pts = [(X(s["t"]), Y(s["top"][i])) if s["top"][i] is not None else None for s in series]
        out.append(f'<path d="{path_d(pts)}" stroke="{TEAM_COLORS[i % len(TEAM_COLORS)]}" stroke-width="1" '
                   f'stroke-opacity="0.6"/>')
    for k, (_, color) in enumerate(MEDALS):
        pts = [(X(s["t"]), Y(v)) if v is not None else None for s, v in zip(series, medal[k])]
        out.append(f'<path d="{path_d(pts)}" stroke="{color}" stroke-width="1.3" stroke-dasharray="5 3"/>')
    me = [(X(s["t"]), Y(s["me"][1])) if s["me"] and s["me"][1] is not None else None for s in series]
    out.append(f'<path class="me" d="{path_d(me)}" stroke-width="2.6"/></g>')
    labels = []
    for k, (name, color) in enumerate(MEDALS):
        last = next((v for v in reversed(medal[k]) if v is not None), None)
        if last is not None:
            labels.append((Y(last) + 3, name, color, ""))
    last = next((p for p in reversed(me) if p), None)
    if last:
        rank, _, shown = next(s["me"] for s in reversed(series) if s["me"] and s["me"][1] is not None)
        out.append(f'<circle cx="{last[0]:.1f}" cy="{last[1]:.1f}" r="2.8" class="me" fill="none" stroke-width="2"/>')
        labels.append((last[1] + 3, f"{E(shown)} (#{rank:,})", "", "me"))
    out += right_labels(L + pw + 6, labels, T + 6, T + ph + 3)
    out.append("</svg>")
    return "".join(out)


def rank_chart(pg, H=HEIGHTS["rank"][0]):
    """Our place over time (log scale, first place at the top) against the medal places and the field size."""
    series = pg["series"]
    if not any(s["me"] for s in series):
        return None
    W, L, R, T, B = 760, 46, 92, 8, 18
    pw, ph = W - L - R, H - T - B
    t0, t1 = series[0]["t"], series[-1]["t"]
    if t1 - t0 < timedelta(hours=1):
        t0, t1 = t0 - timedelta(hours=12), t1 + timedelta(hours=12)

    def X(t):
        return L + pw * (t - t0).total_seconds() / (t1 - t0).total_seconds()

    worst = max([s["n"] for s in series] + [s["me"][0] for s in series if s["me"]])
    top = math.log10(max(worst, 10))

    def Y(r):
        return T + ph * math.log10(max(r, 1)) / top

    out = [svg_open(W, H, "Our place over time")]
    for v in (1, 3, 10, 30, 100, 300, 1000, 3000, 10000, 30000, 100000):
        if v <= worst:
            out += [hline(L, L + pw, Y(v)), text(L - 5, Y(v) + 3, f"{v:,}", "end")]
    for t, lab in time_ticks(t0, t1):
        out += [vline(X(t), T, T + ph, "#f0efeb"), text(X(t), H - 5, lab, "middle")]
    out.append('<g fill="none" stroke-linejoin="round">')
    out.append(f'<path d="{path_d([(X(s["t"]), Y(s["n"])) for s in series])}" stroke="#9a9a9a" stroke-width="1" '
               f'stroke-dasharray="1.5 2"/>')
    for k, (_, color) in enumerate(MEDALS):
        out.append(f'<path d="{path_d([(X(s["t"]), Y(s["medals"][k][0])) for s in series])}" stroke="{color}" '
                   f'stroke-width="1.3" stroke-dasharray="5 3"/>')
    me = [(X(s["t"]), Y(s["me"][0])) if s["me"] else None for s in series]
    out.append(f'<path class="me" d="{path_d(me)}" stroke-width="2.6"/></g>')
    last = series[-1]
    labels = [(Y(last["n"]) + 3, f"{last['n']:,} teams", "#777777", "")]
    labels += [(Y(last["medals"][k][0]) + 3, f"{name} {last['medals'][k][0]:,}", color, "")
               for k, (name, color) in enumerate(MEDALS)]
    pos = next((p for p in reversed(me) if p), None)
    if pos:
        rank = next(s["me"][0] for s in reversed(series) if s["me"])
        out.append(f'<circle cx="{pos[0]:.1f}" cy="{pos[1]:.1f}" r="2.8" class="me" fill="none" stroke-width="2"/>')
        labels.append((pos[1] + 3, f"#{rank:,}", "", "me"))
    out += right_labels(L + pw + 6, labels, T + 6, T + ph + 3, gap=9.5)
    out.append("</svg>")
    return "".join(out)


def spend_chart(days, cats, data, budget, limit, H=HEIGHTS["spend"][0]):
    """Daily stacked bars, one color per category, each bar's total on top; the budget and the Claude limit dashed."""
    W, L, R, T, B = 760, 46, 92, 12, 18
    pw, ph = W - L - R, H - T - B
    totals = [sum(data.get(d, {}).values()) for d in days]
    ticks, dec = nice_ticks(0, max(totals + [budget or 0, limit or 0, 1.0]), most=5)
    ymax = ticks[-1]

    def Y(v):
        return T + ph * (1 - v / ymax)

    out = [svg_open(W, H, "Spend per day")]
    for v in ticks:
        out += [hline(L, L + pw, Y(v)), text(L - 5, Y(v) + 3, f"${v:,.{dec}f}", "end")]
    slot = pw / len(days)
    bw = min(slot * 0.62, 40)
    step = 1 if slot >= 34 else 2
    for i, d in enumerate(days):
        x = L + slot * i + (slot - bw) / 2
        y = Y(0)
        for k, c in enumerate(cats):
            v = data.get(d, {}).get(c, 0.0)
            if v > 0:
                h = ph * v / ymax
                out.append(f'<rect x="{x:.1f}" y="{y - h:.1f}" width="{bw:.1f}" height="{h:.1f}" '
                           f'fill="{COLORS[k % len(COLORS)]}"/>')
                y -= h
        if totals[i] > 0:
            out.append(text(x + bw / 2, y - 3, usd(totals[i]), "middle", size=8.5))
        if i % step == 0 or i == len(days) - 1:
            out.append(text(x + bw / 2, H - 5, d.strftime("%b %-d"), "middle"))
    labels = []
    for v, dash, name in ((budget, "6 3", "budget"), (limit, "2 2", "Claude limit")):
        if v:
            out.append(hline(L, L + pw, Y(v), "#333333", 1.1, dash))
            labels.append((Y(v) + 3, f"{name} {usd(v)}", "#333333", ""))
    out += right_labels(L + pw + 6, labels, T + 6, T + ph + 3)
    out.append("</svg>")
    return "".join(out)


def timeline_chart(marks, resets, now, days=7):
    """The next days: plan items by their number (done gray, in progress blue, the rest orange), the Kaggle day
    resets (dashed) and now."""
    W, H, L, R, T = 760, 66, 8, 8, 14
    start = datetime.combine(now.astimezone(TZ).date(), dtime(), tzinfo=TZ)
    end = start + timedelta(days=days)

    def X(t):
        return L + (W - L - R) * (t - start).total_seconds() / (end - start).total_seconds()

    out = [svg_open(W, H, "The next days")]
    for i in range(days + 1):
        t = start + timedelta(days=i)
        out.append(vline(X(t), T - 2, H - 4, "#d9d8d4"))
        if i < days:
            out.append(text((X(t) + X(t + timedelta(days=1))) / 2, 9, t.strftime("%a %b %-d"), "middle"))
    for t in resets:
        if start <= t <= end:
            out.append(vline(X(t), T, H - 4, "#9a9a9a", 0.8, "3 2"))
    out.append(vline(X(now), T - 2, H - 4, width=1.6, cls="now"))
    rows = []
    for n, t, status in marks:
        if not start <= t <= end:
            continue
        x = X(t)
        lane = next((i for i, last in enumerate(rows) if x - last >= 15), len(rows))
        if lane == len(rows):
            rows.append(x)
        rows[lane] = x
        y = H - 12 - 15 * min(lane, 2)
        color = "#9a9a9a" if DONE_RE.match(status) else "#0072b2" if re.match(r"\s*in.progress", status, re.I) \
            else "#e69f00"
        out.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="6.5" fill="{color}"/>')
        out.append(text(x, y + 3, str(n), "middle", size=8, fill="#ffffff"))
    out.append("</svg>")
    return "".join(out)


def key(items):
    """A legend under a chart: (swatch kind, color or class, label) with kind "line", "dash", "me" or "box"."""
    out = []
    for kind, color, label in items:
        if kind == "box":
            sw = f'<span class="sw" style="background:{color}"></span>'
        elif kind == "me":
            sw = '<span class="ln me"></span>'
        else:
            sw = f'<span class="ln{" dash" if kind == "dash" else ""}" style="border-top-color:{color}"></span>'
        out.append(f"<span>{sw}{label}</span>")
    return f'<div class="diagram-key">{"".join(out)}</div>'


def short(name, width=24):
    name = " ".join(str(name or "").split())
    return E(name if len(name) <= width else name[:width - 1] + "…")


# the page

def table(head, rows, widths, cls="", lft=()):
    L = ' class="lft"'
    th = "".join(f'<th style="width:{w}%"{L if i in lft else ""}>{h}</th>'
                 for i, (h, w) in enumerate(zip(head, widths)))
    body = "".join("<tr>" + "".join(f'<td{L if i in lft else ""}>{c}</td>' for i, c in enumerate(r)) + "</tr>"
                   for r in rows)
    return f'<table class="{cls}"><thead><tr>{th}</tr></thead><tbody>{body}</tbody></table>'


class Figures:
    """Figure numbers in document order."""

    def __init__(self):
        self.n = 0

    def __call__(self, svg, caption, legend=""):
        self.n += 1
        return f"<figure>{svg}{legend}<figcaption>Figure {self.n}. {caption}</figcaption></figure>"


def when_of(v):
    """(time in the page's zone, has a time of day) of a plan item's "when", else (None, False)."""
    s = str(v or "").strip()
    try:
        if re.fullmatch(r"\d{4}-\d\d-\d\d", s):
            return datetime.combine(date.fromisoformat(s), dtime(), tzinfo=TZ), False
        t = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None, False
    return (t.replace(tzinfo=TZ) if t.tzinfo is None else t.astimezone(TZ)), True


def kaggle_day(st, now):
    h, m = map(int, str(st.get("reset_utc", "00:00")).split(":"))
    now_u = now.astimezone(timezone.utc)
    start = now_u.replace(hour=h, minute=m, second=0, microsecond=0)
    return start - timedelta(days=1) if start > now_u else start


def spend_view(st, ctx, today):
    """The spend by day and category: (days drawn, categories, {day: {category: usd}}, the totals rows, the start)."""
    ledger, claude = ctx["ledger"], ctx["claude"]
    data, cats = {}, {}
    if claude:
        for d, v in claude["days"].items():
            data.setdefault(d, {})["Claude"] = v
    for d, cat, v, _ in ledger or []:
        name = cat if cat != "Claude" else "Claude (ledger)"
        data.setdefault(d, {})[name] = data.get(d, {}).get(name, 0.0) + v
        cats[name] = cats.get(name, 0.0) + v
    order = (["Claude"] if claude else []) + sorted(cats, key=lambda c: -cats[c])
    start = ctx["start"]
    # the bars start at the first day with a cost, at most SPEND_DAYS back
    first = max(min([d for d, v in data.items() if any(v.values())] or [today]), start,
                today - timedelta(days=SPEND_DAYS - 1))
    first = min(first, today)
    days = [first + timedelta(days=i) for i in range((today - first).days + 1)]

    def total(c, lo):
        return sum(v.get(c, 0.0) for d, v in data.items() if lo <= d <= today)

    rows = []
    week = today - timedelta(days=6)
    for c in order:
        rows.append([E(c) + (" (list-price estimate)" if c == "Claude" else ""), usd(total(c, today)),
                     usd(total(c, week)), usd(total(c, start))])
    if not claude:
        rows.insert(0, ["Claude (list-price estimate)", NA, NA, NA])

    def every(lo):
        return sum(total(c, lo) for c in order)

    rows.append(["<b>Total</b>", usd(every(today)), usd(every(week)), usd(every(start))])
    return days, order, data, rows, every


def headline(st, now, ctx):
    b, subs, gq, tzl = ctx["board"], ctx["subs"], ctx["gpu"], ctx["tzl"]
    offline = '<span class="pending">n/a (offline build)</span>' if ctx["offline"] else NA
    phase = str(st.get("phase") or "")
    head, sep, rest = phase.partition(":")
    phase_val, phase_note = (head.strip(), rest.strip()) if sep and len(head) <= 24 else ("", phase)
    rows = [["Phase", rich(phase_val), rich(phase_note[:1].upper() + phase_note[1:])]]

    me = b and b["me"]
    if me:
        move = ""
        if b["was"]:
            d = b["was"]["rank"] - me["rank"]
            move = (f"; {'up' if d > 0 else 'down'} {abs(d):,} place{'s' if abs(d) != 1 else ''} since "
                    f"{fmt(b['was_when'])}" if d else f"; same place as at {fmt(b['was_when'])}")
        rows.append(["Public place", f"{me['rank']:,} of {b['n']:,}",
                     f"Board snapshot {fmt(b['when'])} {tzl}{move}"])
    else:
        why = "the team is not on the newest board" if b else "no saved board snapshot"
        rows.append(["Public place", NA, f"{why.capitalize()}" + ("" if ctx["team"] else
                                                                  " (set the JSON's \"team\")")])
    scored = [s for s in subs or [] if num(s["score"]) is not None]
    best = (min if b and not b["high"] else max)(scored, key=lambda s: num(s["score"]), default=None)
    top_note = f"#1 on the board: {E(str(b['top'].get('score')))} ({short(b['top'].get('team_name'), 40)})" if b else NA
    rows.append(["Best public score", E(str(me["score"])) if me else NA,
                 (f"Best submission {E(best['id'] or best['what'][:24])}; " if best else "") + top_note])
    if b:
        (g, gs), (s, ss), (br, bs) = b["lines"]
        zone = ""
        if me:
            mv = score(me)
            names = ("gold", "silver", "bronze")
            got = next((k for k, p in enumerate((g, s, br)) if me["rank"] <= p), None)
            target = (got - 1) if got else 2 if got is None else None
            if got == 0:
                zone = "; we hold a gold place"
            elif target is not None and mv is not None and num(b["lines"][target][1]) is not None:
                line = b["lines"][target][1]
                gap = abs(num(line) - mv)
                zone = (f"; we hold a {names[got]} place, " if got else "; ") + \
                    f"{gap:.{places(line, me['score'])}f} short of {names[target]}"
        rows.append(["Medal lines", f"{E(str(gs))} / {E(str(ss))} / {E(str(bs))}",
                     f"Gold, silver and bronze at places {g:,}, {s:,} and {br:,} ({b['rules'][0]}; silver and "
                     f"bronze {b['rules'][1]}){zone}"])
    else:
        rows.append(["Medal lines", NA, NA])

    kday = kaggle_day(st, now)
    today = [x for x in subs or [] if x["t"] >= kday]
    pending = [x["id"] or x["what"][:24] for x in subs or [] if x["status"] in ("PENDING", "RUNNING")]
    rows.append(["Kaggle day slots", f"{len(today)} of {rich(st.get('daily_slots', 5))} used"
                 if subs is not None else offline,
                 f"Current Kaggle day: {fmt(kday)} to {fmt(kday + timedelta(days=1))} {tzl}"
                 + (f"; scoring now: {', '.join(map(E, pending))}" if pending else "")])
    if subs:
        latest = "; ".join(f"{E(x['id'] or x['what'][:24])} {E(x['score']) or E(x['status'].lower())} "
                           f"({fmt(x['t'])})" for x in subs[:3])
        newest = subs[0]
        rows.append(["Latest submissions", E(newest["score"]) or E(newest["status"].lower()), latest])
    else:
        rows.append(["Latest submissions", "none yet" if subs is not None else offline,
                     "" if subs is not None else "The Kaggle API was not read" if ctx["offline"]
                     else "The Kaggle API did not answer"])
    quota = gq and gq["used"] is not None and gq["total"] is not None
    sess = gq and gq["cap"] and E(f"; sessions in use: GPU {gq['in_use'].get('gpu', 0)}/{gq['cap'].get('gpu', '?')}, "
                                  f"CPU {gq['in_use'].get('cpu', 0)}/{gq['cap'].get('cpu', '?')}")
    refresh = ""
    if quota and gq["refresh"]:
        try:
            refresh = f", resets {fmt(iso(gq['refresh']), '%a %b %-d %-I:%M %p')}"
        except ValueError:
            pass
    rows.append(["Kaggle GPU week", f"{gq['used']:.1f} of {gq['total']:.0f} h" if quota else
                 (offline if gq is None else NA),
                 (f"{gq['left']:.1f} h left{refresh}" + (sess or "")) if quota and gq["left"] is not None else
                 "The Kaggle API was not read" if ctx["offline"] else ""])

    sp = ctx["spend"]
    if sp:
        every, claude, budget = sp["every"], ctx["claude"], num(st.get("daily_budget_usd"))
        d0, week = ctx["today"], ctx["today"] - timedelta(days=6)
        notes = [f"{usd(every(week))} in the last 7 days"]
        if claude:
            notes.append(f"Claude {usd(claude['days'].get(d0, 0.0))} of it today (list-price estimate)")
        else:
            notes.append("Claude spend n/a")
        if budget:
            notes.append(f"budget {usd(budget)} a day")
        if claude and claude["limit"]:
            notes.append(f"Claude limit {usd(claude['limit'])} a day")
        rows.append(["Spend today", usd(every(d0)), "; ".join(notes)])
    else:
        rows.append(["Spend today", NA, NA])
    todo = [rich(x) for x in st.get("owner_actions") or []]
    rows.append(["For the owner", f"{len(todo)} to do" if todo else "Nothing",
                 "; ".join(f"{i}. {x}" for i, x in enumerate(todo, 1)) if todo else "Nothing waits on the owner"])
    out = [table(["Metric", "Value", "Note"], rows, [17, 15, 68], "headline", lft=(0, 2))]
    if st.get("summary"):
        out.append(f"<p>{rich(st['summary'])}</p>")
    notes = [rich(x) for x in st.get("notes") or []]
    if notes:
        out.append("<ul>" + "".join(f"<li>{x}</li>" for x in notes) + "</ul>")
    return "".join(out)


def board_section(ctx, fig):
    pg, b = ctx["progress"], ctx["board"]
    if not pg:
        return f"<p>{NA}: no saved full leaderboard snapshot" + (f" for {E(ctx['slug'])}" if ctx["slug"] else
                                                                   " (no competition slug)") + ".</p>"
    parts = []
    short_charts = ctx.get("compact", False)
    chart = safe("the score chart", score_chart, pg, HEIGHTS["score"][short_charts])
    drawn = any(s["me"] for s in pg["series"])
    if chart:
        items = [("me", "", f"Our team ({short(ctx['team'], 30)})" if ctx["team"] else "Our team")] if drawn else []
        items += [("line", TEAM_COLORS[i % len(TEAM_COLORS)], f"#{r['rank']} {short(r.get('team_name'), 20)}")
                  for i, r in enumerate(pg["leaders"])]
        items += [("dash", c, f"{n} line") for n, c in MEDALS]
        span = f"{fmt(pg['series'][0]['t'], '%b %-d')} to {fmt(pg['series'][-1]['t'], '%b %-d')}"
        boards = f"{b['count']} of {b['saved']}" if b["saved"] > b["count"] else str(b["count"])
        parts.append(fig(chart, f"Public score of our team and the top {len(pg['leaders'])} teams, with the medal "
                                f"lines, from {boards} saved boards, {span}; "
                                f"{'higher' if pg['high'] else 'lower'} is better.", key(items)))
    else:
        parts.append(f"<p>Score chart: {NA}</p>")
    if drawn:
        chart = safe("the rank chart", rank_chart, pg, HEIGHTS["rank"][short_charts])
        parts.append(fig(chart, "Our place over time on a log scale, first place at the top, against the medal "
                                "places and the number of teams (dotted).") if chart else f"<p>Rank chart: {NA}</p>")
    else:
        parts.append(f"<p>Our place over time: {NA} (the team is not on the saved boards"
                     + ("" if ctx["team"] else "; set the JSON's \"team\"") + ").</p>")
    return "".join(parts)


def plan_section(st, now, ctx, fig):
    items = []
    for i, x in enumerate(st.get("plan") or []):
        x = x if isinstance(x, dict) else {"what": x}
        t, has_time = when_of(x.get("when"))
        items.append((t, has_time, i, x))
    items.sort(key=lambda it: (it[0] is None, it[0] or now, it[2]))
    recent = now - timedelta(days=3)
    shown = [it for it in items if not DONE_RE.match(str(it[3].get("status") or "")) or it[0] is None
             or it[0] >= recent]
    hidden = len(items) - len(shown)
    parts = []
    tzl = ctx["tzl"]
    if shown:
        marks = [(n, t + (timedelta(hours=12) if not has_time else timedelta()), str(x.get("status") or ""))
                 for n, (t, has_time, _, x) in enumerate(shown, 1) if t]
        first = kaggle_day(st, now)
        resets = [first + timedelta(days=i) for i in range(9)]
        chart = safe("the timeline", timeline_chart, marks, resets, now) if marks else None
        if chart:
            parts.append(fig(chart, "The next seven days: plan items by their number in the table (gray done, blue "
                                    "in progress, orange to do), Kaggle day resets (dashed) and now (solid)."))
        rows = []
        for n, (t, has_time, _, x) in enumerate(shown, 1):
            when = rich(x.get("when")) if not t else fmt(t, "%a %b %-d %-I:%M %p") if has_time else \
                t.strftime("%a %b %-d")
            rows.append([str(n), f'<span class="nw">{when}</span>', rich(x.get("what")), rich(x.get("status"))])
        parts.append(table(["#", f"When ({tzl})", "What", "Status"], rows, [5, 19, 60, 16], "split compact",
                           lft=(2, 3)))
        if hidden:
            parts.append(f'<p class="note">{hidden} done item{"s" if hidden != 1 else ""} older than three days '
                         f'{"are" if hidden != 1 else "is"} left out (they stay in the status JSON).</p>')
    else:
        parts.append("<p>No plan items yet.</p>")
    if ctx["events"]:
        labels = st.get("labels") or {}
        parts.append("<h3>Coming Schedule</h3>" + table([f"When ({tzl})", "Event"], [
            [fmt(t, "%a %b %-d %-I:%M %p"), E(labels.get(g) or str(g).replace("-", " ").capitalize())]
            for t, g in ctx["events"]], [30, 70], "split compact", lft=(1,)))
    elif ctx["events"] is None:
        parts.append(f"<h3>Coming Schedule</h3><p>{NA} (the schedule or its clock file could not be read)</p>")
    return "".join(parts)


def spend_section(st, ctx, fig):
    sp = ctx["spend"]
    if not sp:
        return f"<p>{NA}</p>"
    days, cats, data, rows = sp["days"], sp["cats"], sp["data"], sp["rows"]
    budget, claude = num(st.get("daily_budget_usd")), ctx["claude"]
    start = ctx["start"]
    # the totals first: they read first, and a short table ahead of the chart leaves less of a gap when the chart
    # moves to the next page
    parts = [table(["Category", "Today", "Last 7 days", f"Since {start:%b %-d}"], rows, [40, 20, 20, 20],
                   "split compact", lft=(0,))]
    notes = []
    if not claude:
        notes.append("Claude spend: n/a (no Solaris checkout found above the project, or ai_spend failed).")
    if ctx["ledger"] is None:
        notes.append(f"The cost ledger ({E(ctx['ledger_rel'])}) does not exist yet: other costs show none.")
    if ctx["ledger_bad"]:
        notes.append(f"{ctx['ledger_bad']} line{'s' if ctx['ledger_bad'] != 1 else ''} of the cost ledger could not "
                     f"be read.")
    if notes:
        parts.append(f'<p class="note">{" ".join(notes)}</p>')
    if cats:
        chart = safe("the spending chart", spend_chart, days, cats, data, budget, claude and claude["limit"],
                     HEIGHTS["spend"][ctx.get("compact", False)])
        if chart:
            items = [("box", COLORS[k % len(COLORS)], E(c) + (" (list-price estimate)" if c == "Claude" else ""))
                     for k, c in enumerate(cats)]
            items += [("dash", "#333333", f"daily budget {usd(budget)}")] if budget else []
            parts.append(fig(chart, f"Spend per day by category, {days[0]:%b %-d} to {days[-1]:%b %-d}"
                                    + ("; the Claude part is a list-price estimate." if claude else "."), key(items)))
    else:
        parts.append("<p>No costs recorded yet.</p>")
    week = ctx["today"] - timedelta(days=6)
    big = sorted((x for x in ctx["ledger"] or [] if week <= x[0] <= ctx["today"]), key=lambda x: -x[2])[:5]
    if big:
        parts.append("<h3>Largest Costs in the Last 7 Days</h3>" + table(
            ["Day", "Category", "What", "Cost"],
            [[f'<span class="nw">{d:%a %b %-d}</span>', E(c), E(w), usd(v)] for d, c, v, w in big],
            [14, 18, 56, 12], "compact", lft=(1, 2)))
    return "".join(parts)


def lease_text(end, now):
    # one unbroken piece: a wrapped "(52 h left)" would be a runt
    left = (end - now).total_seconds() / 3600
    return f'<span class="nw">{fmt(end)} ' + (f"({left:.0f} h left)" if left > 0 else "(ended)") + "</span>"


def resource_section(st, now, ctx):
    hosts, ends, tzl = ctx["hosts"] or [], ctx["ends"], ctx["tzl"]
    by_host = {str(h["name"]): h for h in hosts}
    rows, seen = [], set()

    def cost(v):
        return usd(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else rich(v)

    def uses(v):
        if isinstance(v, list):
            return "; ".join((f"{rich(u.get('when'))}: {rich(u.get('what'))}" if u.get("when") else rich(u.get("what")))
                             if isinstance(u, dict) else rich(u) for u in v)
        return rich(v)

    for r in st.get("resources") or []:
        r = r if isinstance(r, dict) else {"name": r}
        name = str(r.get("name") or "")
        seen.add(name)
        h, end = by_host.get(name), ends.get(name)
        until = lease_text(end, now) if end else rich(r.get("until"))
        period = " to ".join(x for x in (rich(r.get("from")), until) if x)
        status = rich(r.get("status")) or ("lease ended" if end and end < now else "leased" if end else "")
        rows.append([f'<b class="nw">{rich(name)}</b>' if h or end else f"<b>{rich(name)}</b>",
                     rich(r.get("kind")) or ("machine" if h or end else ""),
                     rich(r.get("what")) or (E(str(h.get("gpus") or "")) if h else ""), period, cost(r.get("cost")),
                     status, uses(r.get("uses"))])
    extra = [n for n in by_host if n not in seen] + sorted(n for n in ends if n not in seen and n not in by_host)
    for name in extra:
        h, end = by_host.get(name), ends.get(name)
        rows.append([f'<b class="nw">{E(name)}</b>', "machine", E(str(h.get("gpus") or "")) if h else "",
                     lease_text(end, now) if end else NA, "",
                     "lease ended" if end and end < now else "leased" if end else "in the host list", ""])
    if not rows:
        return "<p>No resources listed yet.</p>" + ("" if ctx["hosts"] is not None else
                                                   f'<p class="note">Host list ({E(ctx["hosts_rel"])}): {NA}.</p>')
    out = [table(["Name", "Kind", "What it is", f"From and until ({tzl})", "Cost", "Status", "Used for"], rows,
                 [12, 7, 24, 17, 9, 10, 21], "split compact", lft=(0, 2, 3, 6))]
    notes = []
    if ctx["hosts"] is None:
        notes.append(f"Host list ({E(ctx['hosts_rel'])}): {NA}.")
    if ends:
        notes.append(f"Lease ends read {fmt(ctx['lease_read']) if ctx['lease_read'] else 'at an unknown time'}.")
    if notes:
        out.append(f'<p class="note">{" ".join(notes)}</p>')
    return "".join(out)


def questions_section(st):
    qs = []
    for q in st.get("questions") or []:
        if isinstance(q, dict):
            extra = [x for x in (f"open since {rich(q['since'])}" if q.get("since") else "", rich(q.get("note")))
                     if x]
            qs.append(rich(q.get("text") or q.get("question")) + (f" ({'; '.join(extra)})" if extra else ""))
        else:
            qs.append(rich(q))
    return "<ol>" + "".join(f"<li>{q}</li>" for q in qs) + "</ol>" if qs else "<p>No open questions.</p>"


def suggestion_section(st, ctx):
    kinds = {"framework": "Framework", "procedure": "Procedure", "resources": "Resources needed",
             "resource": "Resources needed"}
    rows = []
    for s in st.get("suggestions") or []:
        s = s if isinstance(s, dict) else {"text": s}
        kind = str(s.get("kind") or "").strip()
        rows.append([kinds.get(kind.lower(), rich(kind.capitalize()) or "Other"), rich(s.get("text"))])
    out = [table(["Kind", "Suggestion"], rows, [18, 82], "split compact", lft=(0, 1)) if rows else
           "<p>No suggestions in the status JSON.</p>"]
    ideas = ctx["ideas"]
    if ideas:
        # a title's `code` spans show as code
        items = (re.sub(r"`([^`]+)`", r"<code>\1</code>", E(t)) for t in ideas)
        out.append(f"<h3>Newest Entries in {E(ctx['ideas_rel'])}</h3><ul>"
                   + "".join(f"<li>{t}</li>" for t in items) + "</ul>")
    elif ideas is None:
        out.append(f'<p class="note">Improvement suggestions ({E(ctx["ideas_rel"])}): {NA}.</p>')
    return "".join(out)


def build(st, now, ctx):
    """The status page as one HTML document."""
    fig = Figures()
    tzl = ctx["tzl"]
    sections = [
        ("state", "Current State", headline(st, now, ctx)),
        ("board", "Leaderboard Progress", board_section(ctx, fig)),
        ("plan", "Plan and Timeline", plan_section(st, now, ctx, fig)),
        ("spending", "Spending", spend_section(st, ctx, fig)),
        ("resources", "Resources", resource_section(st, now, ctx)),
        ("questions", "Open Questions", questions_section(st)),
        ("suggestions", "Suggestions", suggestion_section(st, ctx)),
    ]
    rev, project = E(str(st.get("rev") or 0)), ctx["project"]
    title = str(st.get("title") or project)
    subtitle = str(st.get("subtitle") or "Project Status")
    intro = rich(st["intro"]) if st.get("intro") else (
        f"The single status page of {E(project)}: its state, board, plan, spending and resources in one place. It is "
        f"rebuilt at every hourly pass and on every plan change; the revision number rises with each change of the "
        f"status JSON. All times are {tzl} ({E(ctx['tzname'])}).")
    b = ctx["board"]
    sources = [f"{E(ctx['status_rel'])} (kept by the master)",
               f"{b['saved']} saved board snapshots, the newest {fmt(b['when'])}" if b else "no saved board snapshot",
               "no Kaggle API reads (offline build)" if ctx["offline"] else
               f"the submissions and the GPU week read at {fmt(now)}",
               "the cost ledger and the session records (list-price estimates)", "the host list and lease ends",
               "the improvement suggestions"]
    head = f'''<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="report-date" content="{now.astimezone(TZ):%Y-%m-%d}">
<title>{E(title)} — {E(subtitle)} — Rev. {rev}</title>
{ctx["css"]}<style>{STYLE}</style>
</head>
<body class="viz-root">
<main{' data-layout="compact"' if ctx.get("compact") else ""}>

<h1>{E(title)}<br><span class="h1-subtitle">{E(subtitle)} — Rev. {rev}</span></h1>

<p class="updated">Updated {fmt(now, "%Y-%m-%d %-I:%M %p %Z")} — rebuilt at every hourly pass and on every plan
change</p>

<p class="sub">{intro}</p>

<div class="toc" style="--toc-rows: {-(-len(sections) // 3)};">
<ol class="toc-list">
''' + "".join(f'<li><a href="#{a}">{t}</a></li>\n' for a, t, _ in sections) + "</ol>\n</div>\n"
    body = "".join(f'\n<section>\n<h2 id="{a}">{t}</h2>\n{s}\n</section>\n' for a, t, s in sections)
    foot = (f'\n<p class="foot">Sources: {"; ".join(sources)}. Built by kaggle_status.py (the kaggle plugin).</p>\n'
            "\n</main>\n</body>\n</html>\n")
    return head + body + foot


# files and the render

def render(root, pack, env_file, tzname, name=NAME):
    """Render reports/<name>.pdf through the reporting plugin: (ok, message with the layout check, half-empty
    pages it found)."""
    script = os.path.join(root, pack, "plugins", "reporting", "assets", "render.sh")
    cmd = ["bash", "-c", ('. "$1" && ' if env_file else "") + 'exec sh "$2" "$3"', "render", env_file or "", script,
           name]
    started = datetime.now().timestamp()
    try:
        r = subprocess.run(cmd, cwd=root, capture_output=True, text=True, timeout=300, env=dict(os.environ, TZ=tzname))
    except Exception as e:
        return False, f"RENDER FAILED: {e}", 0
    pdf = os.path.join(root, "reports", name + ".pdf")
    if r.returncode == 0 and os.path.exists(pdf) and os.path.getmtime(pdf) >= started - 5:
        check = [x for x in r.stdout.splitlines() if x.startswith(("layout check", "  page ", "  runt"))]
        gaps = re.search(r"layout check: (\d+) half-empty", r.stdout)
        return True, "\n".join(["rendered " + pdf, *check]), int(gaps.group(1)) if gaps else 0
    # the cause can sit above a long Node stack trace
    return False, "RENDER FAILED: " + (r.stderr or r.stdout)[-1500:], 0


def render_shorter(root, pack, env_file, tzname, page, gaps):
    """After a render that left half-empty pages: render the page with shorter charts beside it and keep that one
    when it leaves fewer; (kept, message)."""
    html_alt = os.path.join(root, "reports", "html", SHORT + ".html")
    pdf_alt = os.path.join(root, "reports", SHORT + ".pdf")
    try:
        write_atomic(html_alt, page)
        ok, msg, left = render(root, pack, env_file, tzname, SHORT)
        if not ok:
            return False, "shorter charts: " + msg
        check = msg.splitlines()[1:2] or ["no layout check"]
        if left >= gaps:
            return False, f"shorter charts: {check[0]}; kept the first layout"
        os.replace(pdf_alt, os.path.join(root, "reports", NAME + ".pdf"))
        os.replace(html_alt, os.path.join(root, "reports", "html", NAME + ".html"))
        return True, "kept shorter charts: " + "\n".join(msg.splitlines()[1:])
    finally:
        for f in (html_alt, pdf_alt):
            if os.path.exists(f):
                os.remove(f)


def drop_short(root):
    """Remove the shorter-chart try a killed build left behind: its PDF, its HTML and that HTML's temp file."""
    html_dir = os.path.join(root, "reports", "html")
    files = [os.path.join(root, "reports", SHORT + ".pdf"), os.path.join(html_dir, SHORT + ".html")]
    for f in files + glob.glob(os.path.join(glob.escape(html_dir), SHORT + ".*.tmp.html")):
        try:
            if os.path.isfile(f) or os.path.islink(f):
                os.remove(f)
        except OSError as e:
            warn(f"cannot remove the leftover {f}: {e}")


def write_atomic(path, text):
    """Write text through a temp file in the same folder and os.replace: a failed write keeps the old file whole."""
    path = os.path.realpath(path)
    # the extension stays last, so a temp file a killed build leaves matches the same .gitignore patterns
    stem, ext = os.path.splitext(path)
    tmp = f"{stem}.{os.getpid()}.tmp{ext}"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(text)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)


def save_rev(path, rev):
    """Set the JSON's top-level "rev", changing only that number when it has one (the master's formatting stays)."""
    text = read_text(path)
    want = json.loads(text)
    want["rev"] = rev
    for m in re.finditer(r'"rev"\s*:\s*(-?\d+|null)', text):
        new = text[:m.start(1)] + str(rev) + text[m.end(1):]
        if json.loads(new) == want:
            break
    else:
        new = json.dumps(want, indent=2, ensure_ascii=False) + "\n"
    write_atomic(path, new)


def check_shape(st):
    """Raise ValueError naming the first field of the wrong type."""
    for k in ("notes", "owner_actions", "plan", "resources", "questions", "suggestions"):
        if st.get(k) is not None and not isinstance(st[k], list):
            raise ValueError(f'"{k}" must be a list')
    for k in ("schedule", "labels"):
        if st.get(k) is not None and not isinstance(st[k], dict):
            raise ValueError(f'"{k}" must be an object')
    for k in ("plan", "resources", "suggestions", "questions"):
        for x in st.get(k) or []:
            if not isinstance(x, (dict, str)):
                raise ValueError(f'each item of "{k}" must be an object or a text')
    if st.get("start") is not None:
        date.fromisoformat(str(st["start"]))
    int(st.get("rev") or 0)


def main(argv=None):
    global TZ
    ap = argparse.ArgumentParser(description="Build a Kaggle project's status page: HTML, then a PDF through the "
                                             "reporting plugin when attached.")
    ap.add_argument("--keep-rev", action="store_true", help="rebuild under the same rev (fresh live figures)")
    ap.add_argument("--no-render", action="store_true", help="write the HTML only")
    ap.add_argument("--offline", action="store_true", help="no Kaggle calls: draw from the saved board snapshots")
    ap.add_argument("--out", help="write a preview HTML here instead: offline (no Kaggle call), no render, the "
                                  "project untouched")
    ap.add_argument("--root", help="project root (default: found around the working directory)")
    ap.add_argument("--status", help="status JSON (default: <root>/reports/status.json)")
    ap.add_argument("--slug", help="competition slug (default: the JSON's, else the only one with saved snapshots)")
    ap.add_argument("--team", help="team name on the leaderboard (default: the JSON's \"team\")")
    ap.add_argument("--tz", help="timezone for every time shown (default: the JSON's, else $TZ or this machine's)")
    ap.add_argument("--gateway", help="Kaggle gateway (default: the kaggle.py beside this file)")
    a = ap.parse_args(argv)

    try:
        root = os.path.abspath(a.root) if a.root else find_root()
        pack = pack_of(root) if root else None
    except PackError as e:
        warn(str(e))
        return 2
    if not root:
        warn("no project here - run from a project root or pass --root")
        return 2
    status_path = os.path.abspath(a.status or os.path.join(root, "reports", "status.json"))
    try:
        st = json.loads(read_text(status_path))
    except (OSError, ValueError) as e:
        warn(f"cannot read the status JSON {status_path}: {e}")
        return 2
    if not isinstance(st, dict):
        warn(f"the status JSON {status_path} is not a JSON object")
        return 2
    # the raised rev is saved only once the page is written
    bump = not a.out and (not a.keep_rev or not st.get("rev"))
    # a preview reads offline too: a gateway call can create or rebuild the project's Kaggle venv
    offline = a.offline or bool(a.out)

    tzname = a.tz or st.get("timezone") or local_zone()
    try:
        TZ = ZoneInfo(tzname)
    except Exception:
        warn(f"unknown timezone {tzname!r}, using UTC")
        tzname, TZ = "UTC", timezone.utc
    now = datetime.now(timezone.utc)
    slug = a.slug or st.get("competition") or find_slug(root)
    team = a.team or st.get("team")
    gateway = os.path.abspath(a.gateway) if a.gateway else os.path.join(HERE, "kaggle.py")
    share = os.path.join(os.path.dirname(gateway), "kaggle_share.py")
    if not os.path.isfile(share):
        share = os.path.join(HERE, "kaggle_share.py")
    mem = os.path.join(root, pack, ".memory") if pack else os.path.join(root, ".memory")

    def rel(p):
        return os.path.relpath(p, root)

    if not slug:
        warn("no competition slug (pass --slug or set the JSON's \"competition\"): the board and submissions show n/a")

    out = os.path.abspath(a.out) if a.out else os.path.join(root, "reports", "html", NAME + ".html")
    out_dir = os.path.dirname(out)
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
        check_shape(st)
        if bump:
            st["rev"] = int(st.get("rev") or 0) + 1
        top = max(0, min(int(st.get("top_teams") if st.get("top_teams") is not None else TOP_TEAMS), 12))
        clock = os.path.join(root, st["clock"]) if st.get("clock") else None
        sched = safe("the schedule", load_schedule, st, clock)
        events = safe("the schedule", upcoming, *sched, now) if sched else None
    except Exception as e:
        # a bad hand edit in the status JSON: the last page and the JSON's rev stay as they were
        warn(f"cannot build from {status_path}, nothing written: {type(e).__name__}: {e}")
        return 2
    # the build starts here: clear what a killed one left (a preview touches nothing in the project)
    if not a.out:
        drop_short(root)

    if slug and not offline:
        err = safe("the board", refresh_board, root, slug, gateway, now)
        if err:
            warn(err)
    boards = safe("the board", load_boards, root, slug, default=[])
    online = not offline and slug
    ledger, ledger_bad = safe("the cost ledger", read_ledger, os.path.join(mem, "spend.jsonl"), default=(None, 0))
    today = now.astimezone(TZ).date()
    firsts = [x[0] for x in ledger or []] + ([boards[0]["t"].astimezone(TZ).date()] if boards else [])
    start = date.fromisoformat(str(st["start"])) if st.get("start") else min(
        firsts, default=today - timedelta(days=SPEND_DAYS - 1))
    start = min(start, today)
    claude = safe("the Claude spend", claude_spend, root, start)
    ends, lease_read = safe("the lease ends", read_leases, os.path.join(mem, "lease-ends.json"), default=({}, None))
    ctx = dict(
        board=safe("the board", board_now, boards, team),
        progress=safe("the board", progress, boards, team, top) if boards else None,
        subs=safe("the submissions", submissions, gateway, root, slug) if online else None,
        gpu=safe("the GPU week", gpu_week, share, gateway, root) if not offline else None,
        offline=offline, slug=slug, team=team, ledger=ledger, ledger_bad=ledger_bad, claude=claude, start=start,
        today=today, hosts=safe("the host list", read_hosts, os.path.join(mem, "hosts.json")), ends=ends,
        lease_read=lease_read, ideas=safe("the improvements", read_ideas, os.path.join(mem, "improvements.md")),
        events=events, project=project_name(root, pack) if pack else os.path.basename(root), tzname=tzname,
        tzl=rich(st.get("tz_label") or now.astimezone(TZ).strftime("%Z")), css=css,
        status_rel=rel(status_path), ledger_rel=rel(os.path.join(mem, "spend.jsonl")),
        hosts_rel=rel(os.path.join(mem, "hosts.json")), ideas_rel=rel(os.path.join(mem, "improvements.md")))
    sp = safe("the spending", spend_view, st, ctx, today)
    ctx["spend"] = dict(zip(("days", "cats", "data", "rows", "every"), sp)) if sp else None
    try:
        page = build(st, now, ctx)
    except Exception as e:
        warn(f"cannot build from {status_path}, nothing written: {type(e).__name__}: {e}")
        return 2
    os.makedirs(out_dir, exist_ok=True)
    write_atomic(out, page)
    if bump:
        try:
            save_rev(status_path, st["rev"])
        except Exception as e:
            warn(f"wrote {out} but could not save rev {st['rev']} in {status_path}: {e}")
            return 2
    print(f"Rev. {st.get('rev') or 0}: {out}")
    if a.out or a.no_render:
        return 0
    if not reporting:
        print("the reporting plugin is not attached: HTML only")
        return 0
    env_file = st.get("render_env")
    # joined to the root: bash's "." looks a bare name up on PATH first
    env_file = os.path.join(root, os.path.expanduser(env_file)) if env_file else None
    ok, msg, gaps = render(root, pack, env_file, tzname)
    print(msg)
    if ok and gaps:
        # a chart that did not fit left a gap: the same page with shorter charts often fits
        try:
            print(render_shorter(root, pack, env_file, tzname, build(st, now, dict(ctx, compact=True)), gaps)[1])
        except Exception as e:
            print(f"shorter charts: not tried ({type(e).__name__}: {e})")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
