"""Offline tests for shared/tools/kaggle_status.py (stdlib unittest; a stand-in gateway, kaggle_share and autocode,
no network, no Chrome). The tests never run this machine's autocode: a test that reads the agent's spend names a
stand-in for the command (KAGGLE_HOURLY_AUTOCODE), and every other test runs outside an autocode session.

    python3 -m unittest discover -s plugins/kaggle/tests
"""

import contextlib
import gzip
import importlib.util
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
import zlib
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

sys.dont_write_bytecode = True  # keep __pycache__ out of the plugin folder
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "shared" / "tools"))
import kaggle_status as K  # noqa: E402

SLUG = "demo-competition"
TEAM = "Our Team"
NOW = datetime.now(timezone.utc).replace(microsecond=0)
# what makes a folder an ai-pack, whatever its name: a manifest with framework_version and a project object
PACK_MANIFEST = json.dumps({"framework_version": "0.41.0", "project": {"name": "Demo"}})
# run the way the tool runs kaggle.py and kaggle_share.py: logs its arguments, prints <its name>.out (a leaderboard
# read prints <its name>.lb.out)
STAND_IN = """\
import json, os, sys
here, name = os.path.split(os.path.abspath(__file__))
with open(os.path.join(here, "calls.log"), "a") as f:
    f.write(json.dumps([name] + sys.argv[1:]) + "\\n")
out = os.path.join(here, name + (".lb.out" if "leaderboard" in sys.argv else ".out"))
with open(out, "rb") as f:
    sys.stdout.buffer.write(f.read())
"""
# the `autocode` command as the tests stand it in: logs its arguments, then answers `me --json` from me.json beside it
# (its text, what it writes on stderr and its exit code)
FAKE_AUTOCODE = """\
import json, os, sys
here = os.path.dirname(os.path.abspath(__file__))
with open(os.path.join(here, "calls.jsonl"), "a") as f:
    f.write(json.dumps({"args": sys.argv[1:], "cwd": os.getcwd()}) + "\\n")
with open(os.path.join(here, "me.json")) as f:
    reply = json.load(f)
sys.stdout.write(reply.get("out", ""))
sys.stderr.write(reply.get("err", ""))
sys.exit(reply.get("code", 0))
"""
SHARE_STATUS = {"quota": {"gpu": {"used": 12.5, "total": 30.0, "refresh": "2026-10-03T00:00:00Z"}},
                "gpu_hours_left": 17.5, "in_use": {"gpu": 1, "cpu": 0}, "cap": {"gpu": 2, "cpu": 5}}
SECTIONS = ["state", "board", "plan", "spending", "resources", "questions", "suggestions"]
IDEAS = """# Improvements

- **2026-10-01 - `tools/a.py`: the first idea.**
  Why: a reason.
- A bullet without a bold title.
- **2026-10-02 - The second idea, its title
  running over two lines.** Why: more.

```
- **not an entry: inside a fence**
```

- **2026-10-03 - The third <script>idea</script>.**
"""


def iso(t):
    return t.strftime("%Y-%m-%dT%H:%M:%SZ")


def day(d, f="%a %b %-d"):
    return (NOW + timedelta(days=d)).strftime(f)


class Frozen(datetime):
    """The tool's clock, stopped at NOW, so two builds give the same bytes."""

    @classmethod
    def now(cls, tz=None):
        return NOW.astimezone(tz) if tz else NOW.replace(tzinfo=None)


class Tmp(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp()).resolve()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        env = mock.patch.dict(os.environ, {}, clear=False)
        env.start()
        self.addCleanup(env.stop)
        # outside an autocode session, whatever session runs the tests
        for name in ("KAGGLE_LB_DIR", "AUTOCODE_SESSION", K.AUTOCODE_ENV):
            os.environ.pop(name, None)
        self.addCleanup(setattr, K, "TZ", K.TZ)
        self.root = self.tmp / "proj"
        self.mem = self.root / "ai" / ".memory"
        self.mem.mkdir(parents=True)
        (self.root / "ai" / "manifest.json").write_text(PACK_MANIFEST)
        (self.root / "reports").mkdir()
        self.status_file = self.root / "reports" / "status.json"
        self.page = self.root / "reports" / "html" / "status.html"
        self.store = self.root / "__data" / "kaggle" / SLUG / "leaderboard"
        gw = self.tmp / "gw"
        gw.mkdir()
        self.gateway, self.share = gw / "kaggle.py", gw / "kaggle_share.py"
        for f in (self.gateway, self.share):
            f.write_text(STAND_IN)
        self.calls = gw / "calls.log"
        self.gateway_says("[]")
        self.share_says(json.dumps(SHARE_STATUS))

    def gateway_says(self, out, board=None):
        Path(f"{self.gateway}.out").write_text(out)
        if board is not None:
            Path(f"{self.gateway}.lb.out").write_text(board)

    def share_says(self, out):
        Path(f"{self.share}.out").write_text(out)

    def autocode(self, cost=None, agent=None, registered=True, code=0, err="", out=None):
        """Build as an autocode session whose `autocode me --json` answers with this agent and cost."""
        d = self.tmp / "autocode"
        d.mkdir(exist_ok=True)
        (d / "autocode.py").write_text(FAKE_AUTOCODE)
        agent = {"id": "demo:master", "description": "Works the demo competition.", "status": "working",
                 "model": "anthropic/claude-opus-5-5", **(agent or {})}
        agent["cost"] = {"today_usd": 12.5, "total_usd": 340.25, "runs_today_usd": 1.5, "runs_total_usd": 20,
                         "daily_usd": 50, "left_usd": 37.5, **(cost or {})}
        doc = {"registered": registered, "session": "ses_test", **({"agent": agent} if registered else {}),
               "server": {"id": "m", "time": iso(NOW), "timezone": "UTC"}}
        (d / "me.json").write_text(json.dumps({"out": json.dumps(doc, indent=2) if out is None else out,
                                               "code": code, "err": err}))
        os.environ.update({K.AUTOCODE_ENV: str(d / "autocode.py"), "AUTOCODE_SESSION": "ses_test",
                           "AUTOCODE_TOKEN": "tok-0123456789abcdef"})

    def autocode_calls(self):
        log = self.tmp / "autocode" / "calls.jsonl"
        return [json.loads(x) for x in log.read_text().splitlines()] if log.exists() else []

    def receipt(self, ago, raw=None, status="quiet", **more):
        """An hourly receipt as kaggle_hourly.py saves it, of the pass `ago` before now, with these reads in "raw"."""
        at = NOW - ago
        d = self.root / "__data" / "kaggle" / SLUG / "hourly"
        d.mkdir(parents=True, exist_ok=True)
        slot = at.strftime("%Y%m%dT%HZ")
        doc = {"schema": 1, "slug": SLUG, "slot": slot, "at": iso(at), "status": status, "reasons": [], "held": [],
               **({"raw": {"at": iso(at), **raw}} if raw is not None else {}), **more}
        (d / f"{slot}.json").write_text(json.dumps(doc))
        return d / f"{slot}.json"

    def write_status(self, **fields):
        st = {"rev": 3, "competition": SLUG, "team": TEAM, "timezone": "UTC", "phase": "Phase 1: start", **fields}
        self.status_file.write_text(json.dumps(st, indent=1) + "\n")
        return st

    def snapshot(self, at, scores, partial=False):
        """One saved board as kaggle_lb.py saves it: (team name, score[, team id]) in rank order; our team's id is 1."""
        self.store.mkdir(parents=True, exist_ok=True)
        rows = [{"rank": i + 1, "team_id": x[2] if len(x) > 2 else 1 if x[0] == TEAM else zlib.crc32(x[0].encode()),
                 "team_name": x[0], "score": x[1]} for i, x in enumerate(scores)]
        snap = {"schema": 1, "slug": SLUG, "fetched_at": iso(at), "partial": partial, "rows": rows}
        stem = iso(at).replace("-", "").replace(":", "") + ("-partial" if partial else "")
        with gzip.open(self.store / f"{stem}.json.gz", "wt", encoding="utf-8") as f:
            json.dump(snap, f)

    def boards(self, n=6, hours=12, newest_ago=2.0):
        """n boards, hours apart, the newest newest_ago hours old: we climb from 9th to 4th of 12; higher is better."""
        for k in range(n):
            at = NOW - timedelta(hours=newest_ago + (n - 1 - k) * hours)
            ours = 0.60 + 0.05 * k
            teams = [(f"Team {i}", round(0.95 - 0.04 * i + 0.01 * k, 3)) for i in range(1, 12)]
            rows = sorted(teams + [(TEAM, round(ours, 3))], key=lambda x: -x[1])
            self.snapshot(at, [(name, f"{s:.3f}") for name, s in rows])

    def ledger(self, lines):
        (self.mem / "spend.jsonl").write_text("".join(x if isinstance(x, str) else json.dumps(x) + "\n"
                                                      for x in lines))

    def full_project(self):
        """Every source: boards, a cost ledger, hosts, lease ends, improvements and a full status JSON."""
        self.boards()
        today = NOW.date()
        self.ledger([{"day": (today - timedelta(days=d)).isoformat(), "category": "cloud GPU", "usd": 10 + d,
                      "what": f"rental day {d}"} for d in range(5)]
                    + [{"day": today.isoformat(), "category": "model API", "usd": 2.5, "what": "label calls"}])
        (self.mem / "hosts.json").write_text(json.dumps([{"name": "gpu-1", "gpus": "1x A100 80 GB"},
                                                         {"name": "gpu-2", "gpus": "2x H100 80 GB"}]))
        (self.mem / "lease-ends.json").write_text(json.dumps({"read": iso(NOW), "ends": {
            "gpu-1": iso(NOW + timedelta(hours=30)), "old-3": iso(NOW - timedelta(hours=5))}}))
        (self.mem / "improvements.md").write_text(IDEAS)

        def day(d):
            return (NOW + timedelta(days=d)).strftime("%Y-%m-%d")

        return self.write_status(
            summary="We climbed to <b>4th</b>.", notes=["Trust the CV."], owner_actions=["Approve a lease."],
            plan=[{"when": day(-9), "what": "Old done item", "status": "done"},
                  {"when": day(-1) + " 09:00", "what": "Earlier run", "status": "done"},
                  {"when": day(1), "what": "Submit the blend", "status": "pending"},
                  {"when": day(0) + " 23:00", "what": "Train fold 3", "status": "in progress"},
                  {"when": "after the blend scores", "what": "Decide on fold 4", "status": "pending"}],
            schedule={"daily": [["08:00", "lb-snapshot"]]}, labels={"lb-snapshot": "Leaderboard snapshot"},
            daily_budget_usd=40,
            resources=[{"name": "gpu-1", "kind": "machine", "from": "Oct 1", "cost": 2.5, "status": "busy",
                        "uses": [{"when": "Oct 3", "what": "fold 2"}]},
                       {"name": "Kaggle account", "kind": "account", "what": "Submissions", "cost": "free",
                        "status": "in use", "uses": ["five a day"]}],
            questions=["Keep gpu-1?", {"text": "Use the extra data?", "since": "Oct 3", "note": "license"}],
            suggestions=[{"kind": "framework", "text": "Show the model."}, {"kind": "procedure", "text": "CV first."},
                         {"kind": "resources", "text": "One more GPU."}, "A plain suggestion."])

    def run_tool(self, *args, render=False):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = K.main(["--root", str(self.root), "--gateway", str(self.gateway),
                           *([] if render else ["--no-render"]), *args])
        return code, out.getvalue(), err.getvalue()

    def calls_made(self):
        return [json.loads(x) for x in self.calls.read_text().splitlines()] if self.calls.exists() else []

    def tree(self):
        return {str(p.relative_to(self.root)): (p.stat().st_size, p.stat().st_mtime_ns)
                for p in self.root.rglob("*")}


class RootTests(unittest.TestCase):
    def test_root_and_pack_for_any_pack_name_and_from_a_copied_install(self):
        tmp = Path(tempfile.mkdtemp()).resolve()
        self.addCleanup(shutil.rmtree, tmp, True)
        (tmp / "elsewhere").mkdir()
        for pack in ("ai", "aipack", "mypack"):
            proj = tmp / f"p-{pack}"
            tools = proj / pack / "plugins" / "kaggle" / "tools"
            tools.mkdir(parents=True)
            (proj / pack / "manifest.json").write_text(PACK_MANIFEST)
            self.assertEqual((K.find_root(str(tools)), K.pack_of(str(proj))), (str(proj), pack))
            self.assertEqual(K.project_name(str(proj), pack), "Demo")
            # a copied install finds its project from its own place
            with mock.patch.object(K, "HERE", str(tools)):
                self.assertEqual(K.find_root(str(tmp / "elsewhere")), str(proj))
        odd = tmp / "odd"
        for name, text in (("plugin", '{"name": "kaggle"}'), (".hidden", PACK_MANIFEST), ("one", PACK_MANIFEST),
                           ("two", PACK_MANIFEST)):
            (odd / name).mkdir(parents=True)
            (odd / name / "manifest.json").write_text(text)
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            self.assertEqual(K.main(["--root", str(odd), "--no-render"]), 2)
        self.assertEqual(err.getvalue(), f"kaggle_status: {odd}: more than one ai-pack (one, two)\n")


class PageTests(Tmp):
    def test_every_section_in_order_with_the_charts_offline(self):
        self.full_project()
        code, out, err = self.run_tool("--offline")
        self.assertEqual((code, err), (0, ""), out + err)
        self.assertEqual(out, f"Rev. 4: {self.page}\n")
        page = self.page.read_text()
        self.assertEqual(re.findall(r'<h2 id="([a-z]+)">', page), SECTIONS)
        self.assertEqual(re.findall(r'<li><a href="#([a-z]+)">', page), SECTIONS)
        # the score, rank, timeline and spending charts, numbered in order
        self.assertEqual(page.count("<svg "), 4)
        self.assertEqual(re.findall(r"Figure (\d)\. ", page), ["1", "2", "3", "4"])
        self.assertIn('aria-label="Public score over time"', page)
        self.assertIn('class="me"', page)
        self.assertIn("0.850 (#4)", page)
        self.assertIn("stroke-dasharray", page)
        # the headline: our place, the move in a day, the medal lines (12 teams: places 1, 2 and 4), the spend
        self.assertIn("<td>4 of 12</td>", page)
        self.assertIn("up 2 places since", page)
        self.assertIn("Gold, silver and bronze at places 1, 2 and 4", page)
        self.assertIn("we hold a bronze place, 0.070 short of silver", page)
        self.assertIn("n/a (offline build)", page)
        self.assertIn("<td>$12.50</td>", page)
        self.assertIn("1. Approve a lease.", page)
        self.assertIn("<p>We climbed to <b>4th</b>.</p>", page)
        # the plan: sorted by time, the old done item counted on its day, free text last
        plan = page[page.index('id="plan"'):page.index('id="spending"')]
        whats = re.findall(r'<td class="lft">([^<]*)</td><td class="lft">[^<]*</td></tr>', plan)
        self.assertEqual(whats, ["Earlier run", "Train fold 3", "Submit the blend", "Decide on fold 4"])
        old = (NOW - timedelta(days=9)).strftime("%a %b %-d")
        self.assertIn(f'<td class="lft"><span class="nw">{old}</span></td><td>1</td></tr>', plan)
        self.assertIn("Leaderboard snapshot", plan)
        # the spending: the AI agent n/a outside an autocode session, the ledger's categories, the budget
        spend = page[page.index('id="spending"'):page.index('id="resources"')]
        self.assertIn("AI agent spend: n/a (not an autocode session: no AUTOCODE_SESSION in the environment).", spend)
        self.assertIn('<td class="lft">AI agent (autocode)</td><td><span class="pending">n/a</span></td>', spend)
        self.assertIn("the cost ledger (no autocode spend report)", page)
        self.assertIn("cloud GPU", spend)
        self.assertIn("budget $40", spend)
        self.assertIn('<td class="lft"><b>Total</b></td><td>$12.50</td><td>$62.50</td><td>$62.50</td>', spend)
        # the five largest costs may split across pages, two rows held on each side
        self.assertIn('<h3>Largest Costs in the Last 7 Days</h3><table class="split compact">', spend)
        self.assertEqual(spend.count('<tbody style="break-inside:avoid">'), 2)
        # the resources: a JSON row joined with its host and lease, a host and a lease of their own
        res = page[page.index('id="resources"'):page.index('id="questions"')]
        self.assertIn("1x A100 80 GB", res)
        self.assertIn("(30 h left)", res)
        self.assertIn('<b class="nw">gpu-2</b>', res)
        self.assertIn("in the host list", res)
        self.assertIn('<b class="nw">old-3</b>', res)
        self.assertIn("lease ended", res)
        self.assertIn("Oct 3: fold 2", res)
        self.assertIn("$2.50", res)
        self.assertIn("Use the extra data? (open since Oct 3; license)", page)
        # the suggestions and the newest improvement titles, newest first
        sug = page[page.index('id="suggestions"'):]
        self.assertIn("Resources needed", sug)
        self.assertIn("A plain suggestion.", sug)
        titles = re.findall(r"<li>(2026-10-0\d[^<]*(?:<code>[^<]*</code>[^<]*)?)</li>", sug)
        self.assertEqual(titles, ["2026-10-03 - The third &lt;script&gt;idea&lt;/script&gt;.",
                                  "2026-10-02 - The second idea, its title running over two lines.",
                                  "2026-10-01 - <code>tools/a.py</code>: the first idea."])
        # offline: no Kaggle call at all
        self.assertEqual(self.calls_made(), [])

    def test_text_is_escaped_but_b_i_and_code_survive(self):
        self.snapshot(NOW - timedelta(hours=1), [("<img src=x onerror=y>", "0.9"), (TEAM, "0.8")])
        self.ledger([{"day": NOW.date().isoformat(), "category": "<script>c()</script>", "usd": 3,
                      "what": "<script>w()</script>"}])
        (self.mem / "hosts.json").write_text(json.dumps([{"name": "<b>h</b>", "gpus": "<script>g()</script>"}]))
        self.write_status(
            title="T <script>t()</script>", phase="Phase 2: <b>tune</b> the <script>x()</script>",
            summary="<i>ok</i> <script>s()</script>", notes=["<code>a > b</code> & <b onclick=x>no</b>"],
            owner_actions=["<img src=x onerror=y>"], tz_label="<i>PT</i><script>",
            plan=[{"when": "2026-10-01", "what": "<script>p()</script>", "status": "<i>ready</i>"}],
            resources=[{"name": "<script>r()</script>", "uses": [{"when": "<b>now</b>", "what": "<img src=x>"}]}],
            questions=["<script>q()</script>"], suggestions=[{"kind": "<script>k()</script>", "text": "<b>s</b>"}])
        self.assertEqual(self.run_tool("--offline")[0], 0)
        page = self.page.read_text()
        self.assertNotIn("<script>", page)
        self.assertNotIn("<img", page)
        for text in ("<b>tune</b>", "<i>ok</i> &lt;script&gt;s()&lt;/script&gt;",
                     "<code>a &gt; b</code> &amp; &lt;b onclick=x&gt;no</b>", "&lt;img src=x onerror=y&gt;",
                     "When (<i>PT</i>&lt;script&gt;)", "<i>ready</i>", "<b>now</b>: &lt;img src=x&gt;",
                     "&lt;script&gt;w()&lt;/script&gt;", "&lt;b&gt;h&lt;/b&gt;", "<title>T &lt;script&gt;t()"):
            self.assertIn(text, page)

    def test_missing_or_broken_sources_show_na(self):
        self.write_status(team=None)
        code, _, err = self.run_tool("--offline")
        self.assertEqual((code, err), (0, ""))
        page = self.page.read_text()
        self.assertEqual(re.findall(r'<h2 id="([a-z]+)">', page), SECTIONS)
        self.assertIn('<td class="lft">Public place</td><td><span class="pending">n/a</span></td>', page)
        self.assertIn("No saved board snapshot (set the JSON's \"team\")", page)
        self.assertIn("no saved full leaderboard snapshot for demo-competition", page)
        self.assertIn("The cost ledger (ai/.memory/spend.jsonl) does not exist yet", page)
        self.assertIn("No resources listed yet.", page)
        self.assertIn("Improvement suggestions (ai/.memory/improvements.md): <span", page)
        self.assertNotIn("<svg", page)
        # broken files: an unreadable board, a partial and an empty one, a bad ledger line, a host list that is
        # no list, a lease file that is no JSON
        self.store.mkdir(parents=True)
        (self.store / "20260101T000000Z.json.gz").write_bytes(b"not gzip")
        self.snapshot(NOW - timedelta(hours=3), [("Team 1", "0.9")], partial=True)
        self.snapshot(NOW - timedelta(hours=2), [])
        self.ledger(["{bad json\n", {"day": "2026-13-01", "usd": 1}, {"day": NOW.date().isoformat(), "usd": "x"},
                     {"day": NOW.date().isoformat(), "category": "", "usd": 1.25}])
        (self.mem / "hosts.json").write_text('{"name": "not a list"}')
        (self.mem / "lease-ends.json").write_text("not json")
        (self.root / "clock.py").write_text("DAILY = [oops\n")
        self.write_status(clock="clock.py")
        code, _, err = self.run_tool("--offline")
        self.assertEqual(code, 0, err)
        self.assertIn("the schedule: n/a (SyntaxError", err)
        page = self.page.read_text()
        self.assertIn("no saved full leaderboard snapshot", page)
        self.assertIn("3 lines of the cost ledger could not be read", page)
        self.assertIn('<td class="lft">other</td><td>$1.25</td>', page)
        self.assertIn("Host list (ai/.memory/hosts.json): <span", page)
        self.assertIn("the schedule or its clock file could not be read", page)

    def test_out_leaves_the_project_untouched(self):
        self.full_project()
        # old boards: a build would take a fresh one, a preview does not
        for i, f in enumerate(sorted(self.store.iterdir())):
            f.rename(f.with_name(f"2020010{i + 1}T000000Z.json.gz"))
        before = self.tree()
        preview = self.tmp / "preview" / "status.html"
        code, out, _ = self.run_tool("--out", str(preview))
        self.assertEqual((code, out), (0, f"Rev. 3: {preview}\n"))
        self.assertEqual(self.tree(), before)
        self.assertIn("Rev. 3</span>", preview.read_text())
        self.assertEqual(self.calls_made(), [])

    def test_a_build_removes_the_shorter_chart_try_a_killed_build_left(self):
        self.write_status()
        html = self.page.parent
        html.mkdir(parents=True)
        left = [self.root / "reports" / "status-short.pdf", html / "status-short.html",
                html / "status-short.4242.tmp.html"]
        for f in left:
            f.write_text("left by a killed build")
        # a preview leaves the project untouched
        self.assertEqual(self.run_tool("--out", str(self.tmp / "preview.html"))[0], 0)
        self.assertTrue(all(f.exists() for f in left))
        with mock.patch.object(K.os, "replace", wraps=os.replace) as replace:
            self.assertEqual(self.run_tool("--offline")[0], 0)
        self.assertEqual(sorted(os.listdir(html)), ["status.html"])
        self.assertEqual(sorted(os.listdir(self.root / "reports")), ["html", "status.json"])
        # a temp file keeps the extension last, so the page's .gitignore patterns would cover one a killed build left
        temps = [os.path.relpath(c.args[0], self.root) for c in replace.call_args_list]
        self.assertEqual([re.sub(r"\.\d+\.", ".<pid>.", t) for t in temps],
                         ["reports/html/status.<pid>.tmp.html", "reports/status.<pid>.tmp.json"])

    def test_rev_rises_in_place_and_keep_rev_keeps_it(self):
        text = '{"rev": 3,\n   "competition": "%s", "timezone": "UTC",\n   "phase": "Phase 1: start"}\n' % SLUG
        self.status_file.write_text(text)
        code, out, _ = self.run_tool("--offline")
        self.assertEqual((code, out.split(":")[0]), (0, "Rev. 4"))
        self.assertEqual(self.status_file.read_text(), text.replace('"rev": 3', '"rev": 4'))
        self.assertIn("Rev. 4</span>", self.page.read_text())
        self.assertEqual(self.run_tool("--offline", "--keep-rev")[0], 0)
        self.assertEqual(self.status_file.read_text(), text.replace('"rev": 3', '"rev": 4'))
        self.assertIn("Rev. 4</span>", self.page.read_text())
        # a JSON without a rev starts at 1, even with --keep-rev
        self.status_file.write_text('{"competition": "%s"}' % SLUG)
        self.assertEqual(self.run_tool("--offline", "--keep-rev")[1].split(":")[0], "Rev. 1")

    def test_a_json_that_does_not_build_keeps_the_last_page_and_the_rev(self):
        self.write_status()
        self.assertEqual(self.run_tool("--offline")[0], 0)
        page = self.page.read_bytes()
        broken = {"plan not a list": {"plan": "S1 only"}, "item not an object": {"plan": [3]},
                  "rev not a number": {"rev": "three"}, "bad start": {"start": "soon"},
                  "schedule not an object": {"schedule": []}, "outlook not an object": {"outlook": ["0.45"]},
                  "outlook lines not a list": {"outlook": {"lines": "0.45"}},
                  "outlook line not an object": {"outlook": {"lines": [0.45]}},
                  "lineage not a list": {"lineage": {"entry": "1"}}, "track not an object": {"tracks": [None]}}
        for case, fields in broken.items():
            self.write_status(**fields)
            st = self.status_file.read_bytes()
            code, _, err = self.run_tool("--offline")
            self.assertEqual(code, 2, case)
            self.assertTrue(err.startswith("kaggle_status: cannot build from ") and err.count("\n") == 1, (case, err))
            self.assertEqual(self.page.read_bytes(), page, case)
            self.assertEqual(self.status_file.read_bytes(), st, case)
        self.assertEqual(sorted(os.listdir(self.page.parent)), ["status.html"])
        self.assertEqual(sorted(os.listdir(self.status_file.parent)), ["html", "status.json"])
        for text, why in (("[1, 2]", "is not a JSON object"), ("{no", "cannot read the status JSON")):
            self.status_file.write_text(text)
            code, _, err = self.run_tool()
            self.assertEqual(code, 2)
            self.assertIn(why, err)
        self.status_file.unlink()
        self.assertIn("cannot read the status JSON", self.run_tool()[2])


class FieldTests(Tmp):
    """The outlook, the best-score lineage, the work tracks, the machines' health and the plan's rollup."""

    def preview(self, name="preview.html", tool=K):
        out = self.tmp / name
        with mock.patch.object(tool, "datetime", Frozen), contextlib.redirect_stdout(io.StringIO()), \
                contextlib.redirect_stderr(io.StringIO()) as err:
            code = tool.main(["--root", str(self.root), "--gateway", str(self.gateway), "--out", str(out)])
        self.assertEqual((code, err.getvalue()), (0, ""))
        return out.read_text()

    def section(self, page, name):
        return page[page.index(f'id="{name}"'):page.index(f'id="{SECTIONS[SECTIONS.index(name) + 1]}"')]

    def head_tool(self):
        """The tool as git's HEAD holds it, loaded from a copy in the temporary folder; the test is skipped where git
        or HEAD's copy is missing (a plugin installed without the repository)."""
        here = Path(K.__file__).resolve()

        def git(*args):
            r = subprocess.run(["git", "-C", str(here.parent), *args], capture_output=True, text=True, timeout=60)
            if r.returncode:
                raise OSError(r.stderr.strip())
            return r.stdout

        try:
            top = Path(git("rev-parse", "--show-toplevel").strip()).resolve()
            text = git("show", f"HEAD:{here.relative_to(top).as_posix()}")
        except (OSError, ValueError, subprocess.SubprocessError) as e:
            self.skipTest(f"no copy of kaggle_status.py at git's HEAD: {e}")
        copy = self.tmp / "head" / "kaggle_status.py"
        copy.parent.mkdir()
        copy.write_text(text)
        spec = importlib.util.spec_from_file_location("kaggle_status_head", copy)
        tool = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(tool)
        self.addCleanup(setattr, tool, "TZ", tool.TZ)
        return tool

    def test_without_the_new_fields_the_page_is_as_git_head_builds_it(self):
        # the closed old items are hidden by both tools: HEAD's may count them in a note, this one in a table
        def rollup_out(page):
            page = re.sub(r'<p class="note">\d+ done items? older than three days (are|is) left out \(they stay in the '
                          r'status JSON\)\.</p>', "", page)
            return re.sub(r"<h3>Earlier Plan Items</h3>.*?</table>", "", page, flags=re.S)

        def hidden(page):
            m = re.search(r"(\d+) done items? older than three days", page)
            return int(m.group(1)) if m else sum(map(int, re.findall(
                r'<tr><td class="lft"><span class="nw">[^<]*</span></td><td>(\d+)</td></tr>', page)))

        costs = re.compile(r"(<h3>Largest Costs in the Last 7 Days</h3>)(<table.*?</table>)", re.S)

        def costs_unsplit(page):
            # the one markup change allowed outside the plan: the costs table may split now, its first and last two
            # rows held together
            return costs.sub(lambda m: m.group(1) + re.sub(r"</?tbody[^>]*>", "", m.group(2)).replace(
                '<table class="split compact">', '<table class="compact">', 1), page)

        def agent_out(page):
            # the other change allowed: the AI spend is the autocode agent's, no longer Claude Code's from ai_spend.
            # Neither tool has a figure here (no Solaris checkout above, no autocode session): only the words differ
            for old, new in (
                    ("Claude (list-price estimate)", "AI agent (autocode)"),
                    ("Claude spend n/a", "AI agent spend n/a (not an autocode session: no AUTOCODE_SESSION in the "
                                         "environment)"),
                    ("Claude spend: n/a (no Solaris checkout found above the project, or ai_spend failed).",
                     "AI agent spend: n/a (not an autocode session: no AUTOCODE_SESSION in the environment)."),
                    ("the cost ledger and the session records (list-price estimates)",
                     "the cost ledger (no autocode spend report)"),
                    # a quiet hour takes no turn, so the page is rebuilt at each hour-close, not at each pass
                    ("rebuilt at every hourly pass and on every plan", "rebuilt at every hour-close and on every plan")):
                page = page.replace(old, new)
            return re.sub(r"(Spend per day by category), ([A-Z][a-z]{2} \d+ to [A-Z][a-z]{2} \d+)\.",
                          r"\1 from the cost ledger, \2; the AI agent's spend is not drawn.", page)

        head = self.head_tool()
        st = self.full_project()
        st["plan"] += [{"when": (NOW - timedelta(days=d)).strftime("%Y-%m-%d %H:%M"), "what": f"Closed {d} days ago",
                        "status": s} for d, s in ((5, "superseded by 4"), (6, "dropped"), (12, "Cancelled"),
                                                  (20, "finished"), (21, "skipped"), (4, "retired"))]
        seen = {"hosts": {"lord/gpu-9": {"key": "lord/gpu-9", "name": "gpu-9", "owner": "lord",
                                         "folder": "projects/my/lord"}}}
        # old items neither tool counts: no status, an empty one, unknown ones, one naming a later step
        still = [{"when": (NOW - timedelta(days=d)).strftime("%Y-%m-%d %H:%M"), "what": f"Open {d} days ago",
                  **({"status": s} if s is not None else {})}
                 for d, s in ((5, None), (6, ""), (7, "partly done: 12 of 20"), (8, "provisioning; then onboarding"),
                              (9, "141 / 142 test runs; 143 next"), (15, "Submitted 5 PM"))]
        # as it is; with the new fields empty; with them null; with a seen list whose owners keep no health file;
        # with older items still open
        for extra, with_seen in (({}, False), ({"outlook": {"at": iso(NOW), "lines": []}, "lineage": [],
                                                "tracks": []}, False),
                                 ({"outlook": None, "lineage": None, "tracks": None}, False), ({}, True),
                                 ({"plan": st["plan"] + still}, True)):
            self.status_file.write_text(json.dumps(dict(st, **extra), indent=1))
            if with_seen:
                (self.mem / "resource-sharing-seen.json").write_text(json.dumps(seen))
            before, after = self.preview("head.html", head), self.preview()
            self.assertEqual(costs_unsplit(rollup_out(after)), agent_out(costs_unsplit(rollup_out(before))),
                             (extra, with_seen))
            self.assertEqual((hidden(after), hidden(before)), (7, 7))
            table = costs.search(after).group(2)
            self.assertTrue(table.startswith('<table class="split compact">'), table)
            self.assertEqual(table.count('<tbody style="break-inside:avoid">'), 2)
            for h3 in ("Outlook", "Work Tracks", "Best-Score Lineage", "Machine Health"):
                self.assertNotIn(f"<h3>{h3}", after)

    def test_outlook_tracks_and_lineage_in_the_current_state(self):
        self.boards()
        at = NOW - timedelta(hours=30)
        steps = [{"entry": "220", "score": "0.432", "origin": "public", "what": "a public notebook"},
                 {"entry": "228", "score": "0.440", "gain": "+0.008", "origin": "public", "what": "a public set"},
                 {"entry": "231", "score": "0.444", "origin": "own", "what": "our <b>reranker</b><script>"},
                 {"entry": "236", "score": "0.451", "gain": "+0.007", "origin": "Public ", "what": "a fork"}]
        self.write_status(
            summary="Steady.",
            outlook={"at": iso(at), "lines": [
                {"when": "in 3 days", "score": "0.45-0.47", "rank": "#40-80", "basis": "two <i>own</i> runs"},
                {"when": "2026-11-30", "score": "0.46", "rank": "#50", "basis": "the trend"},
                {"when": "final", "score": "0.46-0.49", "rank": "#30-90", "basis": "the shake-up"}, "a plain line"]},
            tracks=[{"name": "LoRA", "state": "paused", "last_gain": day(-10, "%Y-%m-%d"), "note": "no GPU"},
                    {"name": "Reranker", "state": "running", "last_gain": None, "note": "first run"},
                    {"name": "Blend", "state": "running", "last_gain": iso(NOW), "note": ""}, "Search"],
            lineage=steps)
        state = self.section(self.preview(), "state")
        # after the headline table and the summary, in this order
        heads = re.findall(r"<h3>([^<(]*)", state)
        self.assertEqual([h.split(" as of")[0] for h in heads], ["Outlook", "Work Tracks", "Best-Score Lineage"])
        self.assertLess(state.index("<p>Steady.</p>"), state.index("<h3>Outlook"))
        # the outlook: as of its time, aged; "in N days" dated from it
        self.assertIn(f"<h3>Outlook as of {at.strftime('%b %-d %-I:%M %p')} UTC (30 h old)</h3>", state)
        self.assertIn(f'<td><span class="nw">In 3 days ({(at + timedelta(days=3)).strftime("%a %b %-d")})</span>'
                      f'</td><td>0.45-0.47</td><td>#40-80</td><td class="lft">two <i>own</i> runs</td>', state)
        self.assertIn('<span class="nw">Mon Nov 30</span></td><td>0.46</td>', state)
        self.assertIn('<span class="nw">Final</span></td><td>0.46-0.49</td><td>#30-90</td>', state)
        self.assertIn('<td class="lft">a plain line</td>', state)
        # the tracks: the last gain and how long ago
        self.assertIn(f'<td class="lft"><b>LoRA</b></td><td>paused</td><td><span class="nw">{day(-10)} (10 days '
                      f'ago)</span></td><td class="lft">no GPU</td>', state)
        self.assertIn("<td>running</td><td>none yet</td>", state)
        self.assertIn(f'<span class="nw">{day(0)} (today)</span>', state)
        self.assertIn('<td class="lft"><b>Search</b></td>', state)
        # the lineage: a missing gain is the step from the entry before, the gains summed by origin
        self.assertIn("<p><b>Gains since entry 220 (0.432):</b> own work +0.004 in 1 step; public material +0.015 in "
                      "2 steps.</p>", state)
        self.assertIn('<tr><td>231</td><td>0.444</td><td>+0.004</td><td>Own work</td><td class="lft">our <b>reranker'
                      '</b>&lt;script&gt;</td></tr>', state)
        self.assertIn("<tr><td>236</td><td>0.451</td><td>+0.007</td><td>Public material</td>", state)
        self.assertIn("<tr><td>220</td><td>0.432</td><td></td><td>Public material</td>", state)
        # a long chain: the newest steps listed, all of them summed; no own step says so
        chain = [{"entry": str(i), "score": f"{0.30 + 0.01 * i:.2f}", "origin": "public"} for i in range(13)]
        self.write_status(lineage=chain)
        state = self.section(self.preview(), "state")
        self.assertIn("own work none; public material +0.12 in 12 steps.", state)
        self.assertIn("The newest 10 of 13 steps; the gains above count them all.", state)
        self.assertEqual(len(re.findall(r"<td>Public material</td>", state)), 10)
        self.assertNotIn("<td>2</td><td>0.32</td>", state)
        self.assertIn("<td>3</td><td>0.33</td>", state)

    def test_machine_health_from_this_project_and_the_sharing_project(self):
        # a Solaris tree: this project and the one that shares its machines with it
        sol = self.tmp / "sol"
        (sol / "projects" / "my").mkdir(parents=True)
        self.root = self.root.rename(sol / "projects" / "my" / "guest")
        self.mem, self.status_file = self.root / "ai" / ".memory", self.root / "reports" / "status.json"
        lord = sol / "projects" / "my" / "lord" / "aipack"
        (lord / ".memory").mkdir(parents=True)
        (lord / "manifest.json").write_text(PACK_MANIFEST)
        self.write_status()
        checked = NOW - timedelta(minutes=10)
        (self.mem / "host-health.json").write_text(json.dumps({"schema": 1, "checked": iso(checked), "hosts": {
            "gpu-1": {"health": "ok", "gpus": 8, "gpus_visible": 8, "busy": 0.97, "xid_new": 0, "temp_max_c": 71,
                      "notes": [], "checked": iso(checked)},
            "gpu-2": {"health": "bad", "gpus": 8, "gpus_visible": 6, "busy": 0.5, "xid_new": 2,
                      "notes": ["GPU 6 <script>fenced"]},
            "big-2": {"health": "ok", "gpus": 1, "gpus_visible": 1, "busy": 0.9, "checked": iso(NOW - timedelta(
                hours=9))}}}))
        (lord / ".memory" / "host-health.json").write_text(json.dumps({"schema": 1, "checked": iso(NOW), "hosts": {
            "big-1": {"health": "warn", "gpus": 1, "gpus_visible": 1, "busy": 0.12, "notes": ["hot"],
                      "checked": iso(NOW - timedelta(hours=5))},
            "big-2": {"health": "warn", "gpus": 1, "gpus_visible": 1, "busy": 0.05, "xid_new": 1,
                      "checked": iso(NOW)}}}))
        seen = {"acked": iso(NOW), "hosts": {
            "lord/big-1": {"key": "lord/big-1", "name": "big-1", "owner": "lord", "folder": "projects/my/lord"},
            # this project calls the owner's big-2 lord/big-2; this project's own record of it is older
            "lord/big-2": {"key": "lord/big-2", "name": "big-2", "owner": "lord", "folder": "projects/my/lord"},
            "lord/big-3": {"key": "lord/big-3", "name": "big-3", "owner": "lord", "folder": "projects/my/lord"},
            "far/x-1": {"key": "far/x-1", "name": "x-1", "owner": "far", "folder": "../../outside"}}}
        (self.mem / "resource-sharing-seen.json").write_text(json.dumps(seen))
        page = self.preview()
        res = self.section(page, "resources")
        self.assertIn("<h3>Machine Health</h3>", res)
        rows = re.findall(r"<tr><td class=\"lft\"><b class=\"nw\">([^<]*)</b></td><td>([^<]*)</td>(.*?)</tr>", res)
        # this project's file in its order, then the shared machines by owner and name
        self.assertEqual([(n, o) for n, o, _ in rows], [("gpu-1", "this project"), ("gpu-2", "this project"),
                                                        ("big-2", "lord"), ("x-1", "far"), ("big-1", "lord"),
                                                        ("big-3", "lord")])
        cells = {n: rest for n, _, rest in rows}
        # the note: the thresholds crossed as the check words them, else the readings
        self.assertEqual(cells["gpu-1"], f'<td>ok</td><td>97%</td><td>8 of 8</td><td><span class="nw">'
                                         f'{checked.strftime("%b %-d %-I:%M %p")}</span></td><td class="lft">hottest '
                                         f'GPU 71 °C</td>')
        self.assertIn('<td><b>bad</b></td><td>50%</td><td><b>6 of 8</b></td>', cells["gpu-2"])
        self.assertIn('<td class="lft">GPU 6 &lt;script&gt;fenced</td>', cells["gpu-2"])
        # the newer record wins
        self.assertIn('<td><b>warn</b></td><td>5%</td>', cells["big-2"])
        self.assertIn('<td class="lft">1 new Xid error</td>', cells["big-2"])
        self.assertIn("<td><b>warn</b></td><td>12%</td><td>1 of 1</td>", cells["big-1"])
        self.assertIn("(5 h old)</span>", cells["big-1"])
        for name in ("big-3", "x-1"):
            self.assertIn('<td><span class="pending">n/a</span></td>', cells[name])
            self.assertIn("not in its health file", cells[name])
        self.assertIn("the host list, lease ends and host health;", page)
        # a guest without a file of its own still sees the owner's; an unreadable file is named
        (self.mem / "host-health.json").unlink()
        (lord / ".memory" / "host-health.json").write_text("{not json")
        res = self.section(self.preview(), "resources")
        self.assertIn("<h3>Machine Health</h3>", res)
        self.assertIn("No resources listed yet.", res)
        self.assertIn("Host health (projects/my/lord/aipack/.memory/host-health.json): <span", res)
        (lord / ".memory" / "host-health.json").unlink()
        (self.mem / "host-health.json").write_text(json.dumps({"hosts": []}))
        res = self.section(self.preview(), "resources")
        self.assertIn("Host health (ai/.memory/host-health.json): <span", res)
        self.assertEqual(res.count("not in its health file"), 4)

    def test_a_status_closes_an_item_only_with_a_closed_word_first_and_no_later_step(self):
        for s in ("done", "Done.", "<b>done</b>: no-go at 9 PM", "finished", "complete", "Completed 9 AM", "closed",
                  "dropped from the day", "canceled", "Cancelled", "cancel", "skipped", "superseded by 3",
                  "replaced by 4", "retired", "abandoned", "merged into 5", "rejected by the host", "NO-GO: alone",
                  "no go", "n/a", "N/A now"):
            self.assertTrue(K.closed(s), s)
        for s in (None, "", "  ", "partly done: 12 of 20", "provisioning; then onboarding", "done-ish", "doneness",
                  "141 / 142 test runs; 143 next", "done (review 2); review 3 next", "done, pending the score",
                  "complete; to do: the README", "superseded; waiting for 9", "Submitted 5 PM", "read 0.451: new best",
                  "started", "WIP", "TBD", "not done", "nogood", "n/applicable"):
            self.assertFalse(K.closed(s), s)

    def test_the_plan_lists_the_last_days_and_counts_only_older_closed_items(self):
        (self.mem / "config.json").write_text(json.dumps({"kaggle.status_plan_days": 1}))
        plan = [{"when": day(-30, "%Y-%m-%d"), "what": "Stale but open", "status": "<b>Pending</b> a reply"},
                {"when": day(-20, "%Y-%m-%d 08:00"), "what": "Old run", "status": "done"},
                {"when": day(-20, "%Y-%m-%d 09:00"), "what": "Old submit", "status": "Submitted 9 AM"},
                {"when": day(-20, "%Y-%m-%d 10:00"), "what": "Old no-go", "status": "NO-GO: alone 1 / 1"},
                {"when": day(-9, "%Y-%m-%d"), "what": "Nine days back", "status": "superseded by 3"},
                {"when": day(-9, "%Y-%m-%d 12:00"), "what": "No status"},
                {"when": day(-8, "%Y-%m-%d"), "what": "Empty status", "status": ""},
                {"when": day(-7, "%Y-%m-%d"), "what": "Partly", "status": "partly done: 12 of 20"},
                {"when": day(-7, "%Y-%m-%d 06:00"), "what": "Machine", "status": "provisioning; then onboarding"},
                {"when": day(-6, "%Y-%m-%d"), "what": "Test runs", "status": "141 / 142 test runs; 143 next"},
                {"when": day(-6, "%Y-%m-%d 06:00"), "what": "Review", "status": "done (review 2); review 3 next"},
                {"when": day(-5, "%Y-%m-%d"), "what": "Five days back", "status": "<i>Cancelled</i>"},
                {"when": day(-4, "%Y-%m-%d"), "what": "Not needed", "status": "n/a now"},
                {"when": day(-4, "%Y-%m-%d 06:00"), "what": "Refused", "status": "rejected by the host"},
                {"when": day(-2, "%Y-%m-%d 23:59"), "what": "Two days back", "status": "read 0.45"},
                {"when": day(-1, "%Y-%m-%d"), "what": "Yesterday", "status": "done"},
                {"when": day(1, "%Y-%m-%d"), "what": "Tomorrow", "status": "to do"},
                {"when": "weekly", "what": "Weekly review", "status": "standing"}]
        self.write_status(plan=plan)
        text = self.status_file.read_text()
        self.assertEqual(self.run_tool("--offline")[0], 0)
        # never rewritten: only the rev rises
        self.assertEqual(self.status_file.read_text(), text.replace('"rev": 3', '"rev": 4'))
        page = self.section(self.page.read_text(), "plan")
        whats = re.findall(r'<td class="lft">([^<]*)</td><td class="lft">', page)
        # every older item that is not closed stays listed: open, empty, unknown or naming a later step
        self.assertEqual(whats, ["Stale but open", "Old submit", "No status", "Empty status", "Partly", "Machine",
                                 "Test runs", "Review", "Two days back", "Yesterday", "Tomorrow", "Weekly review"])
        rows = re.findall(r'<tr><td class="lft"><span class="nw">([^<]*)</span></td><td>(\d+)</td></tr>', page)
        d9 = NOW.date() - timedelta(days=9)
        d20 = NOW.date() - timedelta(days=20)
        mon9, mon20 = d9 - timedelta(days=d9.weekday()), d20 - timedelta(days=d20.weekday())
        # by day for the week before the cut, then by week from Monday, newest first
        self.assertEqual(rows, [(day(-4), "2"), (day(-5), "1"), (f"{mon9:%a %b %-d} to {d9:%a %b %-d}", "1"),
                                (f"{mon20:%a %b %-d} to {mon20 + timedelta(days=6):%a %b %-d}", "2")])
        self.assertIn(f"The closed items before {day(-1)}, counted by day and then by week; the status JSON keeps "
                      f"them in full.", page)
        # nothing since the cut and nothing open
        self.write_status(plan=[plan[i] for i in (1, 3, 4)])
        self.assertEqual(self.run_tool("--offline")[0], 0)
        page = self.section(self.page.read_text(), "plan")
        self.assertIn(f"<p>No plan items since {day(-1)}.</p><h3>Earlier Plan Items</h3>", page)
        self.assertNotIn("<svg", page)

    def test_the_plan_days_setting(self):
        pack = self.mem.parent
        self.assertEqual(K.plan_days(str(self.root), "ai"), 3)
        (pack / "defaults.json").write_text(json.dumps({"kaggle.status_plan_days": 5}))
        self.assertEqual(K.plan_days(str(self.root), "ai"), 5)
        (self.mem / "config.json").write_text(json.dumps({"kaggle.status_plan_days": 0}))
        self.assertEqual(K.plan_days(str(self.root), "ai"), 0)
        for bad in ("two", -1, 1.5, True):
            (self.mem / "config.json").write_text(json.dumps({"kaggle.status_plan_days": bad}))
            err = io.StringIO()
            with contextlib.redirect_stderr(err):
                self.assertEqual(K.plan_days(str(self.root), "ai"), 3, bad)
            self.assertIn('"kaggle.status_plan_days" must be a whole number of days', err.getvalue())
        # 0: today only
        (self.mem / "config.json").write_text(json.dumps({"kaggle.status_plan_days": 0}))
        self.write_status(plan=[{"when": day(-1, "%Y-%m-%d 12:00"), "what": "Yesterday", "status": "done"},
                                {"when": day(0, "%Y-%m-%d 00:00"), "what": "Today", "status": "done"}])
        page = self.section(self.preview(), "plan")
        self.assertEqual(re.findall(r'<td class="lft">([^<]*)</td><td class="lft">', page), ["Today"])
        self.assertIn(f'<span class="nw">{day(-1)}</span></td><td>1</td></tr>', page)


class LiveTests(Tmp):
    def test_online_reads_a_fresh_board_the_submissions_and_the_gpu_week(self):
        self.boards(newest_ago=2)
        # a Kaggle day that began six hours ago holds both submissions below
        self.write_status(reset_utc=(NOW - timedelta(hours=6)).strftime("%H:%M"))
        board = [{"teamId": 7, "teamName": "Fresh", "submissionDate": "2026-09-30T00:00:00", "score": "0.99"},
                 {"teamId": 1, "teamName": TEAM, "submissionDate": "2026-09-30T00:00:00", "score": "0.97"}]
        subs = [{"date": (NOW - timedelta(minutes=30)).strftime("%Y-%m-%d %H:%M:%S"), "description": "S7: blend",
                 "publicScore": "0.97", "status": "SubmissionStatus.COMPLETE"},
                {"date": (NOW - timedelta(minutes=5)).strftime("%Y-%m-%d %H:%M:%S"), "description": "S8: next",
                 "publicScore": None, "status": "SubmissionStatus.PENDING"}]
        self.gateway_says(json.dumps(subs), board=json.dumps(board))
        before = len(list(self.store.glob("*.json.gz")))
        code, out, err = self.run_tool()
        self.assertEqual(code, 0, err)
        calls = self.calls_made()
        self.assertIn(["kaggle.py", "competitions", "leaderboard", SLUG, "--show", "--format", "json", "--page-size",
                       "200"], calls)
        # the newest 100 submissions: the CLI's default page of 20 loses the best one after that many
        self.assertIn(["kaggle.py", "competitions", "submissions", SLUG, "--format", "json", "--page-size", "100"],
                      calls)
        self.assertIn(["kaggle_share.py", "status", "--json", "--gateway", str(self.gateway)], calls)
        # kaggle_lb.py saved the fresh read
        self.assertEqual(len(list(self.store.glob("*.json.gz"))), before + 1)
        page = self.page.read_text()
        self.assertIn("<td>2 of 2</td>", page)
        self.assertIn("2 of 5 used", page)
        self.assertIn("scoring now: S8", page)
        self.assertIn("S8 pending", page)
        self.assertIn("Best submission S7", page)
        self.assertIn("12.5 of 30 h", page)
        self.assertIn("17.5 h left", page)
        # a board younger than half an hour is not read again
        self.calls.unlink()
        self.assertEqual(self.run_tool()[0], 0)
        self.assertFalse(any("leaderboard" in c for c in self.calls_made()))

    def test_a_preview_makes_no_gateway_call(self):
        # what a build would read: a board over half an hour old, the submissions, the GPU week
        self.boards(newest_ago=2)
        self.write_status()
        self.gateway_says(json.dumps([{"date": (NOW - timedelta(minutes=30)).strftime("%Y-%m-%d %H:%M:%S"),
                                       "description": "S7: blend", "publicScore": "0.97",
                                       "status": "SubmissionStatus.COMPLETE"}]),
                          board=json.dumps([{"teamId": 1, "teamName": TEAM, "submissionDate": "2026-09-30T00:00:00",
                                             "score": "0.97"}]))
        preview = self.tmp / "preview.html"
        code, out, err = self.run_tool("--out", str(preview))
        self.assertEqual((code, out, err), (0, f"Rev. 3: {preview}\n", ""))
        # the stand-in gateway and kaggle_share.py log every call: none, as with --offline
        self.assertEqual(self.calls_made(), [])
        page = preview.read_text()
        self.assertIn("n/a (offline build)", page)
        self.assertIn("no Kaggle API reads (offline build)", page)
        self.assertNotIn("Best submission S7", page)
        # the saved boards still draw
        self.assertIn("<td>4 of 12</td>", page)

    def test_failed_reads_show_na(self):
        self.boards()
        self.write_status()
        self.gateway_says("401 - Unauthorized\n", board="403 - Forbidden\n")
        self.share_says("")
        code, _, err = self.run_tool()
        self.assertEqual(code, 0)
        self.assertIn("kaggle_status: the fresh board read failed", err)
        page = self.page.read_text()
        self.assertIn("The Kaggle API did not answer", page)
        self.assertIn('<td class="lft">Kaggle GPU week</td><td><span class="pending">n/a</span></td>', page)
        # the saved boards still draw
        self.assertIn("<td>4 of 12</td>", page)

    def spend_parts(self):
        page = self.page.read_text()
        row = re.search(r'<tr><td class="lft">Spend today</td><td>(.*?)</td><td class="lft">(.*?)</td></tr>', page)
        return page, row.group(1), row.group(2), page[page.index('id="spending"'):page.index('id="resources"')]

    def test_the_ai_agents_spend_comes_from_autocode(self):
        (self.mem / "config.json").write_text(json.dumps({"ai.daily_budget_usd": 30}))
        today = NOW.date()
        self.ledger([{"day": today.isoformat(), "category": "cloud GPU", "usd": 3, "what": "a rental"},
                     {"day": (today - timedelta(days=3)).isoformat(), "category": "cloud GPU", "usd": 5, "what": "x"}])
        self.write_status(daily_budget_usd=40, start=(today - timedelta(days=9)).isoformat())
        self.autocode()
        code, _, err = self.run_tool("--offline")
        self.assertEqual((code, err), (0, ""))
        # one read of this session's agent, in the project
        self.assertEqual(self.autocode_calls(), [{"args": ["me", "--json"], "cwd": str(self.root)}])
        page, value, note, spend = self.spend_parts()
        na = '<span class="pending">n/a</span>'
        # today's spend is the agent's and the ledger's; the week is the ledger's alone
        self.assertEqual(value, "$15.50")
        self.assertEqual(note, "AI agent $12.50 of it (autocode, $1.50 of that its job runs); cost ledger $8 in the "
                               "last 7 days; budget $40 a day; AI budget $30 a day; autocode's cap $50 a day, $37.50 "
                               "left")
        self.assertIn(f'<td class="lft">AI agent demo:master (autocode)</td><td>$12.50</td><td>{na}</td>'
                      "<td>$340 in total</td>", spend)
        self.assertIn('<td class="lft">cloud GPU</td><td>$3</td><td>$8</td><td>$8</td>', spend)
        self.assertIn(f'<td class="lft"><b>Total</b></td><td>$15.50</td><td>{na}</td>'
                      "<td>$348 with the agent's total</td>", spend)
        self.assertIn("AI agent: the spend of this session's autocode agent; its job runs are $1.50 of today's spend "
                      "and $20 of the total; autocode's daily cap is $50, $37.50 left. Autocode gives today's spend "
                      "and the total only, so the last 7 days are n/a and the chart shows the cost ledger alone. The "
                      "pack's daily AI budget is $30.", spend)
        # the bars are the ledger's: the agent has no per-day history
        self.assertIn("Spend per day by category from the cost ledger, ", spend)
        self.assertIn("; the AI agent's spend is not drawn.", spend)
        self.assertNotIn("AI agent", spend[spend.index("<svg"):spend.index("</svg>")])
        self.assertIn("the cost ledger and autocode's spend report", page)
        self.assertNotIn("Claude", page)
        # autocode without a cap of its own, an agent with no job runs, a pack without a budget
        (self.mem / "config.json").unlink()
        self.autocode(cost={"daily_usd": "off", "left_usd": None, "runs_today_usd": 0, "runs_total_usd": 0,
                            "total_usd": None})
        self.assertEqual(self.run_tool("--offline")[0], 0)
        page, value, note, spend = self.spend_parts()
        self.assertEqual(note, "AI agent $12.50 of it (autocode); cost ledger $8 in the last 7 days; budget $40 a day")
        self.assertIn("its job runs are $0 of today's spend and $0 of the total; autocode sets no daily cap.", spend)
        self.assertIn(f'(autocode)</td><td>$12.50</td><td>{na}</td><td>{na}</td>', spend)
        self.assertIn(f'<b>Total</b></td><td>$15.50</td><td>{na}</td><td>{na}</td>', spend)
        # a preview reads it too: it is no Kaggle call, and it changes nothing
        self.assertEqual(self.run_tool("--out", str(self.tmp / "p.html"))[0], 0)
        self.assertIn("AI agent $12.50 of it (autocode)", (self.tmp / "p.html").read_text())
        self.assertEqual(self.calls_made(), [])

    def test_without_a_report_the_ai_spend_is_n_a_with_the_reason(self):
        self.write_status()
        secret = "tok-0123456789abcdef"
        cases = [
            (lambda: None, "not an autocode session: no AUTOCODE_SESSION in the environment"),
            (lambda: self.autocode(code=3, err=f"cannot reach the server with {secret}\n", out=f"{secret}\n"),
             "`autocode me --json` gave no report (exit 3)"),
            (lambda: self.autocode(out="no JSON here"), "`autocode me --json` gave no report (exit 0)"),
            (lambda: self.autocode(registered=False), "this session is not registered as an autocode agent"),
            (lambda: self.autocode(agent={"description": "run of job demo:master:review"}),
             "this session is an autocode job run, not the master"),
            (lambda: self.autocode(cost={"today_usd": None}), "`autocode me --json` named no spend for today"),
            (lambda: self.autocode(cost={"today_usd": True}), "`autocode me --json` named no spend for today"),
            (lambda: (self.autocode(), os.environ.update({K.AUTOCODE_ENV: str(self.tmp / "no-such" / "autocode")})),
             "the autocode command did not run (No such file or directory)"),
        ]
        for prepare, why in cases:
            prepare()
            self.assertEqual(K.agent_spend(str(self.root)), (None, why))
            code, out, err = self.run_tool("--offline")
            self.assertEqual((code, err), (0, ""), why)
            page, value, note, spend = self.spend_parts()
            shown = K.E(why)
            self.assertEqual((value, note), ("$0", f"$0 in the last 7 days; AI agent spend n/a ({shown})"))
            self.assertIn(f"AI agent spend: n/a ({shown}).", spend)
            # what the command printed is never shown: a token it echoes stays out
            self.assertNotIn(secret, page + out + err)
        # a pack budget that is no number of dollars is not shown, with a word on stderr
        (self.mem / "config.json").write_text(json.dumps({"ai.daily_budget_usd": "plenty"}))
        code, _, err = self.run_tool("--offline")
        self.assertEqual((code, err), (0, 'kaggle_status: "ai.daily_budget_usd" must be a positive number of US '
                                          "dollars: not shown\n"))
        self.assertNotIn("AI budget", self.page.read_text())

    # what the hourly pass read, as its receipt keeps it: the document of the gateway's --sdk submissions read, and
    # what kaggle_share.py status passes on of the --sdk account read
    SUBS_DOC = {"competition": SLUG, "fetched_at": iso(NOW), "submissions": [
        {"ref": 12, "date": (NOW - timedelta(minutes=25)).strftime("%Y-%m-%dT%H:%M:%S.250000"), "status": "PENDING",
         "description": "S8: next", "publicScore": None, "errorDescription": None},
        {"ref": 11, "date": (NOW - timedelta(minutes=50)).strftime("%Y-%m-%dT%H:%M:%S.250000"), "status": "COMPLETE",
         "description": "S7: blend", "publicScore": "0.97", "errorDescription": None}]}
    ACCOUNT_DOC = {"fetched_at": iso(NOW), "calls": 2, "errors": [],
                   "quota": {"gpu": {"used": 12.5, "remaining": 17.5, "total": 30.0, "refresh": "2026-10-03T00:00:00"}}}

    def offline(self, *args):
        self.calls.unlink(missing_ok=True)
        code, _, err = self.run_tool("--offline", *args)
        self.assertEqual((code, err), (0, ""))
        # no Kaggle call, with or without a receipt
        self.assertEqual(self.calls_made(), [])
        page = self.page.read_text()
        rows = dict((k, (v, n)) for k, v, n in re.findall(
            r'<tr><td class="lft">([^<]*)</td><td>(.*?)</td><td class="lft">(.*?)</td></tr>', page))
        return page, rows

    def test_an_offline_build_takes_the_reads_of_the_last_hourly_pass(self):
        self.boards()
        # a Kaggle day that began six hours ago holds both submissions
        self.write_status(reset_utc=(NOW - timedelta(hours=6)).strftime("%H:%M"))
        ago = timedelta(minutes=20)
        self.receipt(ago, {"submissions": self.SUBS_DOC, "account": self.ACCOUNT_DOC})
        read = f"as of the hourly pass's read, {K.fmt(NOW - ago)} UTC"
        page, rows = self.offline()
        self.assertEqual(rows["Kaggle day slots"][0], "2 of 5 used")
        self.assertTrue(rows["Kaggle day slots"][1].endswith(f"; scoring now: S8; {read}"), rows["Kaggle day slots"])
        self.assertEqual(rows["Latest submissions"][0], "pending")
        self.assertRegex(rows["Latest submissions"][1], rf"^S8 pending \(.*\); S7 0\.97 \(.*\); {re.escape(read)}$")
        self.assertTrue(rows["Best public score"][1].startswith(f"Best submission S7 ({read}); #1 on the board"))
        self.assertEqual(rows["Kaggle GPU week"][0], "12.5 of 30 h")
        self.assertRegex(rows["Kaggle GPU week"][1],
                         rf"^17\.5 h left, resets .*; sessions in use: n/a; {re.escape(read)}$")
        self.assertNotIn("n/a (offline build)", page)
        self.assertIn("no Kaggle API reads in this build: the submissions and the GPU week as the hourly pass read "
                      f"them at {K.fmt(NOW - ago)}", page)
        # the same figures as a build that reads Kaggle itself gets from the CLI's rows and kaggle_share's view
        cli = [dict(r, status="SubmissionStatus." + r["status"], date=r["date"].replace("T", " ")[:19])
               for r in self.SUBS_DOC["submissions"]]
        self.gateway_says(json.dumps(cli), board=json.dumps([{"teamId": 1, "teamName": TEAM, "score": "0.97",
                                                             "submissionDate": "2026-09-30T00:00:00"}]))
        self.assertEqual(self.run_tool()[0], 0)
        live = dict((k, v) for k, v, _n in re.findall(
            r'<tr><td class="lft">([^<]*)</td><td>(.*?)</td><td class="lft">(.*?)</td></tr>', self.page.read_text()))
        for key in ("Kaggle day slots", "Latest submissions", "Kaggle GPU week"):
            self.assertEqual(live[key], rows[key][0], key)
        self.assertNotIn("the hourly pass", self.page.read_text())

    def test_an_offline_build_shows_n_a_for_what_the_receipt_does_not_carry(self):
        self.write_status()
        na = '<span class="pending">n/a (offline build)</span>'
        # no receipt, a receipt without the reads, a pass that still runs, a broken file: as before
        self.receipt(timedelta(minutes=5))
        self.receipt(timedelta(hours=1, minutes=5), status="running")
        (self.root / "__data" / "kaggle" / SLUG / "hourly" / "29991231T23Z.json").write_text("{broken")
        page, rows = self.offline()
        self.assertEqual([rows[k][0] for k in ("Kaggle day slots", "Latest submissions", "Kaggle GPU week")], [na] * 3)
        self.assertIn("no Kaggle API reads (offline build)", page)
        # the newest receipt with reads is over two hours old: not taken
        old = self.receipt(timedelta(hours=2, minutes=5), {"submissions": self.SUBS_DOC, "account": self.ACCOUNT_DOC})
        self.assertEqual(self.offline()[1]["Latest submissions"][0], na)
        old.unlink()
        # a pass that read the submissions only (and kept them as the CLI's list): the GPU week stays n/a
        ago = timedelta(hours=1, minutes=40)
        self.receipt(ago, {"submissions": self.SUBS_DOC["submissions"]})
        page, rows = self.offline()
        self.assertEqual((rows["Latest submissions"][0], rows["Kaggle GPU week"][0]), ("pending", na))
        self.assertEqual(rows["Kaggle GPU week"][1], "The Kaggle API was not read")
        self.assertIn("no Kaggle API reads in this build: the submissions as the hourly pass read them at "
                      f"{K.fmt(NOW - ago)}", page)
        # the account read without the submissions, a quota without the week's figures, an empty submissions list
        self.receipt(ago, {"account": self.ACCOUNT_DOC})
        page, rows = self.offline()
        self.assertEqual((rows["Latest submissions"][0], rows["Kaggle GPU week"][0]), (na, "12.5 of 30 h"))
        self.assertIn("the GPU week as the hourly pass read them at", page)
        # (a failed quota read leaves "quota" null in what the receipt keeps)
        self.receipt(ago, {"submissions": dict(self.SUBS_DOC, submissions=[]), "account": {"quota": None, "errors":
                                                                                           ["quota read failed"]}})
        page, rows = self.offline()
        self.assertEqual((rows["Kaggle day slots"][0], rows["Latest submissions"][0], rows["Kaggle GPU week"][0]),
                         ("0 of 5 used", "none yet", na))
        self.assertEqual(rows["Latest submissions"][1], f"As of the hourly pass's read, {K.fmt(NOW - ago)} UTC")
        # a preview is an offline build too, and reads the receipt without touching the project
        before = self.tree()
        preview = self.tmp / "preview.html"
        self.assertEqual(self.run_tool("--out", str(preview))[0], 0)
        self.assertIn("none yet", preview.read_text())
        self.assertEqual(self.tree(), before)
        # the two parsers the live reads and the receipt share
        self.assertEqual(K.sub_rows("not a list"), [])
        self.assertEqual([(x["id"], x["status"], x["score"]) for x in K.sub_rows(self.SUBS_DOC["submissions"])],
                         [("S8", "PENDING", ""), ("S7", "COMPLETE", "0.97")])
        self.assertEqual(K.gpu_view(dict(SHARE_STATUS))["left"], 17.5)
        self.assertEqual(K.gpu_view(self.ACCOUNT_DOC), {"used": 12.5, "total": 30.0, "left": 17.5,
                                                         "refresh": "2026-10-03T00:00:00", "in_use": {}, "cap": {}})


class RenderTests(Tmp):
    def renderer(self, script):
        assets = self.root / "ai" / "plugins" / "reporting" / "assets"
        assets.mkdir(parents=True)
        (assets / "render.sh").write_text(script)
        (assets / "style.css").write_text("")

    def test_the_render_and_its_layout_check(self):
        # as render.sh does: reports/html/<name>.html to reports/<name>.pdf, then the layout check
        self.renderer('cp "reports/html/$1.html" "reports/$1.pdf"\necho "rendered reports/$1.pdf"\n'
                      'echo "layout check: 1 half-empty page(s), 0 runt(s)"\n'
                      'echo "  page 2: 14% empty at the bottom (something moved to the next page)"\n')
        self.write_status()
        # this stand-in PDF is no PDF, so its text cannot be read
        with mock.patch.object(K, "pdf_pages", side_effect=RuntimeError("pdftotext: not a PDF")):
            code, out, _ = self.run_tool("--offline", render=True)
        self.assertEqual(code, 0)
        # the shorter charts leave the same gap: the first layout stays (this page has no table a fit could shorten),
        # the check's finding stands, and the build says it could not name what moved
        self.assertEqual(out.splitlines()[1:], [f"rendered {self.root / 'reports' / 'status.pdf'}",
                                                "layout check: 1 half-empty page(s), 0 runt(s)",
                                                "  page 2: 14% empty at the bottom (something moved to the next "
                                                "page)",
                                                "shorter charts: layout check: 1 half-empty page(s), 0 runt(s); kept "
                                                "the first layout",
                                                # the last layout check of the output is the kept page's
                                                "the page as kept: layout check: 1 half-empty page(s), 0 runt(s)",
                                                "half-empty pages left, and what moved off each:",
                                                "  page 2: what moved to page 3 could not be named (pdftotext: not a "
                                                "PDF)"])
        self.assertEqual(sorted(os.listdir(self.root / "reports")), ["html", "status.json", "status.pdf"])
        self.assertEqual(os.listdir(self.page.parent), ["status.html"])
        page = self.page.read_text()
        self.assertNotIn("data-layout", page)
        self.assertIn('href="../../ai/plugins/reporting/assets/style.css"', page)
        self.assertIn(f'<meta name="report-date" content="{NOW.date()}">', page)
        (self.root / "ai" / "plugins" / "reporting" / "assets" / "render.sh").write_text(
            'echo "Chrome did not start" >&2\nexit 1\n')
        code, out, _ = self.run_tool("--offline", render=True)
        self.assertEqual(code, 1)
        self.assertIn("RENDER FAILED: Chrome did not start", out)

    def test_a_gap_is_tried_again_with_shorter_charts(self):
        # this renderer finds a half-empty page unless the charts are short
        self.renderer('cp "reports/html/$1.html" "reports/$1.pdf"\n'
                      'if grep -q \'data-layout="compact"\' "reports/html/$1.html"; then n=0; else n=1; fi\n'
                      'echo "layout check: $n half-empty page(s), 0 runt(s)"\n')
        self.boards()
        self.write_status()
        code, out, _ = self.run_tool("--offline", render=True)
        self.assertEqual(code, 0)
        self.assertEqual(out.splitlines()[-1], "kept shorter charts: layout check: 0 half-empty page(s), 0 runt(s)")
        page = self.page.read_text()
        self.assertIn('<main data-layout="compact">', page)
        self.assertIn('viewBox="0 0 760 176"', page)
        self.assertEqual((self.root / "reports" / "status.pdf").read_text(), page)
        self.assertEqual(sorted(os.listdir(self.root / "reports")), ["html", "status.json", "status.pdf"])
        self.assertEqual(os.listdir(self.page.parent), ["status.html"])

    # a renderer that logs each render and finds page 2 half-empty while the page holds more than `most` of `mark`
    GAP = ('cp "reports/html/$1.html" "reports/$1.pdf"\necho "$1" >> renders.log\n'
           'n=$(grep -o "{mark}" "reports/html/$1.html" | wc -l)\n'
           'if [ "$n" -gt {most} ]; then echo "layout check: 1 half-empty page(s), 0 runt(s)"\n'
           'echo "  page 2: 14% empty at the bottom (something moved to the next page)"\n'
           'else echo "layout check: 0 half-empty page(s), 0 runt(s)"; fi\n')
    HELD = "layout check: 1 half-empty page(s), 0 runt(s)"
    FITS = "layout check: 0 half-empty page(s), 0 runt(s)"

    def long_page(self):
        """A status whose tables a fit can shorten: 12 lineage steps, 8 coming events, 12 older closed plan items."""
        old = [{"when": (NOW - timedelta(days=10 + 7 * k)).strftime("%Y-%m-%d"), "what": f"Old item {k}",
                "status": "done"} for k in range(12)]
        return self.write_status(
            lineage=[{"entry": str(k), "score": f"0.{400 + k}", "origin": "own", "what": f"LinStep {k}"}
                     for k in range(12)], plan=old,
            schedule={"daily": [[f"{h:02d}:00", "tick"] for h in (2, 8, 14, 20)]}, labels={"tick": "Zzz event"})

    def renders(self):
        return (self.root / "renders.log").read_text().split()

    def test_a_gap_left_by_the_charts_is_tried_with_a_shorter_schedule(self):
        self.renderer(self.GAP.format(mark="Zzz event", most=6))
        self.boards()
        before = json.dumps(self.long_page(), indent=1) + "\n"
        # the Spending section moved to page 3: the charts and the schedule stand before it
        text = ["cover", "Demo\nEarlier Plan Items\nDay or week   Closed items\nComing Schedule\n",
                "Demo - Project Status\n4. Spending\nCategory    Today    Last 7 days   Since Oct 1\n"]
        with mock.patch.object(K, "pdf_pages", return_value=text):
            code, out, _ = self.run_tool("--offline", "--keep-rev", render=True)
        self.assertEqual(code, 0)
        self.assertEqual(out.splitlines()[2:], [
            self.HELD, "  page 2: 14% empty at the bottom (something moved to the next page)",
            f"shorter charts: {self.HELD}; kept the first layout",
            f"kept a shorter schedule (Earlier Plan Items: 8 rows, the oldest 5 folded into one; Coming Schedule: the "
            f"next 6 of 8 events): {self.FITS}"])
        # one render for the page, one for each fit tried: the lineage fits were not needed
        self.assertEqual(self.renders(), ["status", "status-short", "status-short"])
        page = self.page.read_text()
        self.assertEqual((page.count("Zzz event"), page.count("LinStep")), (6, 10))
        self.assertNotIn("data-layout", page)
        # the page says the schedule is cut, as it does for the other trims
        self.assertIn("<h3>Coming Schedule (the next 6 of 8 events)</h3>", page)
        self.assertNotIn("the page as kept", out)
        rows = re.search(r"<h3>Earlier Plan Items</h3>.*?<tbody>(.*?)</tbody>", page, re.S).group(1)
        self.assertEqual(rows.count("<tr>"), 8)
        self.assertRegex(rows, r'<span class="nw">Before \w{3} \w{3} \d+</span></td><td>5</td></tr>$')
        self.assertEqual((self.root / "reports" / "status.pdf").read_text(), page)
        # the trims are of this build's page only: the status JSON keeps every item
        self.assertEqual(self.status_file.read_text(), before)
        self.assertEqual(sorted(os.listdir(self.root / "reports")), ["html", "status.json", "status.pdf"])
        self.assertEqual(os.listdir(self.page.parent), ["status.html"])

    def test_a_fit_that_cannot_help_is_not_rendered_and_what_still_moved_is_named(self):
        self.boards()
        self.long_page()
        # the score chart moved: only what stands before it (the lineage) or the charts themselves can pull it back
        text = ["cover", "Demo\nWork Tracks\nTrack  State\nBest-Score Lineage\nEntry Score\n2. Leaderboard Progress\n",
                "Demo - Project Status\n0.95\nOur team  #1 Team 1\nFigure 1. Public score of our team and the top 8 "
                "teams, with the\nmedal lines; higher is better.\nFigure 2. Our place over time on a log scale\n"]
        self.renderer(self.GAP.format(mark="LinStep", most=5))
        with mock.patch.object(K, "pdf_pages", return_value=text):
            code, out, _ = self.run_tool("--offline", render=True)
        self.assertEqual(code, 0)
        self.assertEqual(out.splitlines()[4:], [
            f"shorter charts: {self.HELD}; kept the first layout",
            f"a shorter lineage (Best-Score Lineage: the newest 7 of 12 steps): {self.HELD}; kept the first layout",
            f"kept a shorter lineage (Best-Score Lineage: the newest 5 of 12 steps): {self.FITS}"])
        self.assertEqual(self.renders(), ["status"] + ["status-short"] * 3)
        page = self.page.read_text()
        self.assertEqual((page.count("LinStep"), page.count("Zzz event")), (5, 8))
        self.assertIn("<h3>Coming Schedule</h3>", page)
        self.assertIn("The newest 5 of 12 steps; the gains above count them all.", page)
        # no fit closes this gap: the first layout stays, and the output names the block and what its page holds
        (self.root / "renders.log").unlink()
        (self.root / "ai" / "plugins" / "reporting" / "assets" / "render.sh").write_text(
            self.GAP.format(mark="Leaderboard Progress", most=0))
        with mock.patch.object(K, "pdf_pages", return_value=text):
            code, out, _ = self.run_tool("--offline", render=True)
        self.assertEqual(code, 0)
        self.assertEqual(self.renders(), ["status"] + ["status-short"] * 3)
        # after a fit that was not kept, the kept page's own check comes last of the checks
        self.assertEqual([x for x in out.splitlines() if "layout check" in x][-1], f"the page as kept: {self.HELD}")
        self.assertEqual(out.splitlines()[-3:], [
            f"the page as kept: {self.HELD}",
            "half-empty pages left, and what moved off each:",
            "  page 2: the score chart did not fit and moved to page 3. Page 2 holds the Work Tracks table, the "
            "Best-Score Lineage table and the Leaderboard Progress heading: shorten one of them, or the text between "
            "them"])
        page = self.page.read_text()
        self.assertEqual((page.count("LinStep"), page.count("Zzz event")), (10, 8))
        self.assertNotIn("data-layout", page)

    def test_the_blocks_of_a_page_are_known_by_their_first_lines(self):
        text = """Demo — Project Status — Rev. 7                                   2 / 9
 Track                 State         Last gain        Note
Best-Score Lineage
   Entry      Score     Gain      Source     What
2. Leaderboard Progress
      0.50        Oct 4     Oct 11
   Figure 1. Public score of our team and the top 8 teams, with the medal lines, from 6 saved
                boards, Sep 27 to Nov 22; higher is better.
 Figure 2. Our place over time on a log scale, first place at the top.
3. Plan and Timeline
 Figure 3. The next seven days: plan items by their number in the table
  #     When (PT)     What           Status
Earlier Plan Items
Coming Schedule (the next 6 of 8 events)
4. Spending
 Category     Today     Last 7 days     Since Oct 1
Figure 4. Spend per day by category, Nov 9 to Nov 22.
Largest Costs in the Last 7 Days
5. Resources
 Name   Kind   What it is   From and until (PT)   Cost   Status   Used for
Machine Health
6. Open Questions
7. Suggestions
Newest Entries in aipack/.memory/improvements.md
"""
        self.assertEqual([k for k, _ in K.blocks_on(text)], [
            "lineage", "board", "score", "rank", "plan", "timeline", "items", "rollup", "events", "spending",
            "totals", "spend", "costs", "resources", "machines", "health", "questions", "suggestions", "ideas"])
        # the contents list of page 1 names every section on shared lines: no block starts there
        toc = "1. Current State      4. Spending      7. Suggestions\n2. Leaderboard Progress     5. Resources\n"
        page = toc + " Metric     Value     Note\nOutlook as of Nov 22 1:09 AM PT\nWork Tracks\n"
        self.assertEqual(K.blocks_on(page), [("headline", "the headline table"), ("outlook", "the Outlook table"),
                                             ("tracks", "the Work Tracks table")])
        self.assertEqual({k for k, _n, _p in K.BLOCKS} >= {b for _w, blocks, _o, _m in K.FITS for b in blocks}, True)
        self.assertEqual(K.gap_pages("rendered x\nlayout check: 2 half-empty page(s), 1 runt(s)\n  page 20: 31% empty "
                                     "at the bottom (something moved)\n  page 2: 14% empty at the bottom\n  runt, "
                                     'page 4: "word"'), [2, 20])
        with mock.patch.object(K, "pdf_pages", return_value=["one", "two\nComing Schedule\n", "three\n4. Spending\n",
                                                           "plain text\n"]):
            moved, why = K.moved_blocks("x.pdf", [2, 3, 4])
        self.assertEqual((moved, why), ({2: (("spending", "the Spending heading"),
                                             [("events", "the Coming Schedule table")]),
                                         3: (None, [("spending", "the Spending heading")]), 4: (None, [])}, None))
        self.assertEqual(K.moved_lines([2, 3], moved, None), [
            "  page 2: the Spending heading did not fit and moved to page 3. Page 2 holds the Coming Schedule table: "
            "shorten it, or the text after it",
            "  page 3: what moved to page 4 could not be named (page 4 opens with no block this tool knows)"])
        # a fit helps when one of its blocks stands before the block that moved; a chart also when it moved itself
        after = {2: (("score", "the score chart"), [])}
        self.assertEqual([K.fit_applies(blocks, own, after) for _w, blocks, own, _m in K.FITS],
                         [True, False, False, True, True])
        self.assertEqual([K.fit_applies(("events",), own, {2: (("events", "x"), [])}) for own in (False, True)],
                         [False, True])
        self.assertTrue(all(K.fit_applies(blocks, own, {}) and K.fit_applies(blocks, own, {2: (None, [])})
                            for _w, blocks, own, _m in K.FITS))

    def test_the_pdf_text_comes_from_pdftotext(self):
        bindir = self.tmp / "bin"
        bindir.mkdir()
        tool = bindir / "pdftotext"
        tool.write_text('#!/bin/sh\n[ "$1" = "-layout" ] && [ "$3" = "-" ] || exit 9\n'
                        'printf "page one of $2\\fpage two\\f"\n')
        tool.chmod(0o755)
        with mock.patch.dict(os.environ, {"PATH": str(bindir)}):
            self.assertEqual(K.pdf_pages("a.pdf"), ["page one of a.pdf", "page two", ""])
            tool.write_text("#!/bin/sh\necho 'Syntax Error: no PDF   here' >&2\nexit 1\n")
            with self.assertRaisesRegex(RuntimeError, "^pdftotext: Syntax Error: no PDF here$"):
                K.pdf_pages("a.pdf")
            self.assertEqual(K.moved_blocks("a.pdf", [2]), ({}, "pdftotext: Syntax Error: no PDF here"))
        with mock.patch.dict(os.environ, {"PATH": str(self.tmp / "empty")}):
            moved, why = K.moved_blocks("a.pdf", [2])
        self.assertEqual(moved, {})
        self.assertIn("pdftotext", why)
        self.assertEqual(K.moved_lines([2], moved, "pdftotext: not found"),
                         ["  page 2: what moved to page 3 could not be named (pdftotext: not found)"])
        self.assertEqual(K.moved_lines([5], {5: (("items", "the plan table"), [("items", "the plan table")])}, None),
                         ["  page 5: the plan table did not fit and moved to page 6. Shorten what stands before it on "
                          "page 5"])

    def test_without_the_reporting_plugin_the_html_only(self):
        self.write_status()
        code, out, _ = self.run_tool("--offline", render=True)
        self.assertEqual((code, out.splitlines()[1:]), (0, ["the reporting plugin is not attached: HTML only"]))
        self.assertIn(".viz-root {", self.page.read_text())


class PartTests(Tmp):
    def test_boards_skip_partial_empty_and_broken_reads_and_spread_over_the_history(self):
        for k in range(10):
            self.snapshot(NOW - timedelta(hours=10 - k), [("Team 1", "0.9"), (TEAM, f"0.{k}")])
        # at most cap boards, spread over the history: the first and the newest always
        picked = K.load_boards(str(self.root), SLUG, cap=4)
        self.assertEqual([b["rows"][1]["score"] for b in picked], ["0.0", "0.3", "0.6", "0.9"])
        self.snapshot(NOW - timedelta(minutes=3), [("Team 1", "0.9")], partial=True)
        self.snapshot(NOW - timedelta(minutes=2), [])
        (self.store / "20000101T000000Z.json.gz").write_bytes(b"broken")
        boards = K.load_boards(str(self.root), SLUG)
        self.assertEqual([b["rows"][1]["score"] for b in boards], [f"0.{k}" for k in range(10)])
        # a renamed team is followed by its id
        self.snapshot(NOW - timedelta(minutes=1), [("Renamed", "0.95", 1), ("Team 1", "0.9")])
        pg = K.progress(K.load_boards(str(self.root), SLUG), TEAM, 8)
        self.assertEqual([s["me"] for s in pg["series"]][-2:], [(2, 0.9, "0.9"), (1, 0.95, "0.95")])
        self.assertEqual([r["team_name"] for r in pg["leaders"]], ["Team 1"])
        self.assertTrue(pg["high"])

    def test_an_unreliable_board_is_never_drawn(self):
        def save(at, scores, name="", **more):
            rows = [{"rank": i + 1, "team_id": i + 1, "team_name": n, "score": s} for i, (n, s) in enumerate(scores)]
            stem = iso(at).replace("-", "").replace(":", "") + name
            with gzip.open(self.store / f"{stem}.json.gz", "wt", encoding="utf-8") as f:
                json.dump({"schema": 1, "slug": SLUG, "fetched_at": iso(at), "partial": False, "rows": rows, **more}, f)

        self.snapshot(NOW - timedelta(hours=3), [(TEAM, "0.9"), ("Team 1", "0.8")])
        self.snapshot(NOW - timedelta(hours=2), [(TEAM, "0.9"), ("Team 1", "0.85")])
        # two garbled reads after them: one saved before the flag (duplicates only), one flagged and named so
        save(NOW - timedelta(hours=1), [("Team 1", "0.85"), ("Ghost", "0.1"), (TEAM, "0.9")], duplicates=400)
        save(NOW - timedelta(minutes=10), [("Team 1", "0.85"), (TEAM, "0.9")], "-unreliable", unreliable=True,
             duplicates=200)
        boards = K.load_boards(str(self.root), SLUG)
        self.assertEqual([b["t"] for b in boards], [NOW - timedelta(hours=3), NOW - timedelta(hours=2)])
        # the headline and the charts go by the newest reliable board: we are first, not third
        b = K.board_now(boards, TEAM)
        self.assertEqual((b["me"]["rank"], b["when"], b["n"]), (1, NOW - timedelta(hours=2), 2))
        # with many boards the newest reliable one is still the last drawn, not lost in the spread
        self.assertEqual(K.load_boards(str(self.root), SLUG, cap=2)[-1]["t"], NOW - timedelta(hours=2))
        # a read named unreliable is not the fresh board: the next build reads the board again
        self.assertEqual(K.newest_saved(str(self.root), SLUG), NOW - timedelta(hours=1))
        self.write_status()
        board = [{"teamId": 1, "teamName": TEAM, "submissionDate": "2026-09-30T00:00:00", "score": "0.97"}]
        self.gateway_says("[]", board=json.dumps(board))
        self.assertEqual(self.run_tool()[0], 0)
        self.assertTrue(any("leaderboard" in c for c in self.calls_made()))
        self.assertIn("<td>1 of 1</td>", self.page.read_text())

    def test_the_plan_rollup_folds_its_oldest_rows_in_a_fit(self):
        cut = datetime(2026, 11, 20, tzinfo=timezone.utc)
        older = [(cut - timedelta(days=d), False, 0, {}) for d in (1, 2, 2, 9, 16, 23, 30, 30)]
        rows = K.plan_rollup(older, cut)
        self.assertEqual([n for _, n in rows], ["1", "2", "1", "1", "1", "2"])
        self.assertEqual(K.plan_rollup(older, cut, 6), rows)
        self.assertEqual(K.plan_rollup(older, cut, 1), rows)
        folded = K.plan_rollup(older, cut, 4)
        self.assertEqual(folded[:3], rows[:3])
        # the newest three rows, then one row for everything before the oldest of them
        self.assertEqual(folded[3], ['<span class="nw">Before Mon Nov 9</span>', "4"])
        self.assertEqual(sum(int(n) for _, n in folded), len(older))

    def test_the_host_list_alone_or_under_hosts(self):
        path = self.mem / "hosts.json"
        hosts = [{"name": "gpu-1", "gpus": "1x A100 80 GB"}, {"gpus": "no name"}, "not a host"]
        # hostclaims.py reads both forms
        for doc in (hosts, {"hosts": hosts, "defaults": {"user": "me"}}):
            path.write_text(json.dumps(doc))
            self.assertEqual(K.read_hosts(str(path)), [{"name": "gpu-1", "gpus": "1x A100 80 GB"}], doc)
        for doc in ({"name": "not a list"}, {"hosts": "gpu-1"}, 3):
            path.write_text(json.dumps(doc))
            self.assertIsNone(K.read_hosts(str(path)), doc)
        # the page joins the host to its row
        path.write_text(json.dumps({"hosts": hosts}))
        self.write_status()
        self.assertEqual(self.run_tool("--offline")[0], 0)
        res = self.page.read_text()
        self.assertIn('<b class="nw">gpu-1</b>', res)
        self.assertIn("1x A100 80 GB", res)
        self.assertNotIn("Host list (", res)

    def test_a_split_table_can_keep_two_rows_on_each_side_of_a_break(self):
        held = ' style="break-inside:avoid"'

        def groups(n):
            page = K.table(["N"], [[str(i)] for i in range(n)], [100], "split compact", keep=2)
            return (re.match(r'<table class="([^"]*)">', page).group(1),
                    [(h, re.findall(r"<td>(\d)</td>", body))
                     for h, body in re.findall(r"<tbody((?: [^>]*)?)>(.*?)</tbody>", page)])

        # too short to leave two rows on each side: it does not split
        self.assertEqual(groups(3), ("compact", [("", ["0", "1", "2"])]))
        self.assertEqual(groups(4), ("split compact", [(held, ["0", "1"]), (held, ["2", "3"])]))
        self.assertEqual(groups(5), ("split compact", [(held, ["0", "1"]), ("", ["2"]), (held, ["3", "4"])]))
        self.assertEqual(groups(6), ("split compact", [(held, ["0", "1"]), ("", ["2", "3"]), (held, ["4", "5"])]))
        # without keep, the table as before
        self.assertEqual(K.table(["N"], [["0"], ["1"]], [100], "split compact"),
                         '<table class="split compact"><thead><tr><th style="width:100%">N</th></tr></thead><tbody>'
                         '<tr><td>0</td></tr><tr><td>1</td></tr></tbody></table>')

    def test_improvement_titles_newest_first(self):
        path = self.mem / "improvements.md"
        path.write_text(IDEAS)
        self.assertEqual(K.read_ideas(str(path), 2), ["2026-10-03 - The third <script>idea</script>.",
                                                     "2026-10-02 - The second idea, its title running over two "
                                                     "lines."])
        self.assertIsNone(K.read_ideas(str(self.mem / "none.md")))

    def test_round_ticks_and_medal_places(self):
        for lo, hi, most, want, dec in ((0.19, 0.444, 7, [0.15, 0.2, 0.25, 0.3, 0.35, 0.4, 0.45], 2),
                                        (0, 46, 5, [0, 20, 40, 60], 0), (0, 1, 5, [0, 0.25, 0.5, 0.75, 1], 2),
                                        (120, 980, 7, [0, 200, 400, 600, 800, 1000], 0)):
            ticks, places = K.nice_ticks(lo, hi, most)
            self.assertEqual(([round(t, 9) for t in ticks], places), (want, dec), (lo, hi))
        self.assertEqual([K.medal_places(n) for n in (12, 150, 600, 2000)],
                         [(1, 2, 4), (10, 30, 60), (11, 50, 100), (14, 100, 200)])


if __name__ == "__main__":
    unittest.main()
