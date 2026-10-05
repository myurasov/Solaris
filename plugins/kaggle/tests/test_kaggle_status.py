"""Offline tests for shared/tools/kaggle_status.py (stdlib unittest; a stand-in gateway, kaggle_share and ai_spend,
no network, no Chrome).

    python3 -m unittest discover -s plugins/kaggle/tests
"""

import contextlib
import gzip
import io
import json
import os
import re
import shutil
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


class Tmp(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp()).resolve()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        env = mock.patch.dict(os.environ, {}, clear=False)
        env.start()
        self.addCleanup(env.stop)
        os.environ.pop("KAGGLE_LB_DIR", None)
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
        # the plan: sorted by time, the old done item counted, free text last
        plan = page[page.index('id="plan"'):page.index('id="spending"')]
        whats = re.findall(r'<td class="lft">([^<]*)</td><td class="lft">[^<]*</td></tr>', plan)
        self.assertEqual(whats, ["Earlier run", "Train fold 3", "Submit the blend", "Decide on fold 4"])
        self.assertIn("1 done item older than three days is left out", plan)
        self.assertIn("Leaderboard snapshot", plan)
        # the spending: Claude n/a without a Solaris checkout, the ledger's categories, the budget
        spend = page[page.index('id="spending"'):page.index('id="resources"')]
        self.assertIn("Claude spend: n/a", spend)
        self.assertIn("cloud GPU", spend)
        self.assertIn("budget $40", spend)
        self.assertIn('<td class="lft"><b>Total</b></td><td>$12.50</td><td>$62.50</td><td>$62.50</td>', spend)
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
                  "schedule not an object": {"schedule": []}}
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
        self.assertIn(["kaggle.py", "competitions", "submissions", SLUG, "--format", "json"], calls)
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

    def test_claude_spend_from_the_solaris_checkout_above(self):
        sol = self.tmp / "sol"
        (sol / "solaris" / "tools").mkdir(parents=True)
        for f in ("solaris/__init__.py", "solaris/tools/__init__.py"):
            (sol / f).write_text("")
        (sol / "solaris" / "tools" / "ai_spend.py").write_text(
            "import json, os, sys\n"
            "open(os.path.join(os.path.dirname(__file__), 'args.json'), 'w').write(json.dumps(sys.argv[1:]))\n"
            f"print(json.dumps({{'rows': [{{'day': '{NOW.date()}', 'usd': 4.5}}, {{'day': '{NOW.date()}', "
            f"'usd': 1.5}}], 'budgets': [{{'daily_budget_usd': 20}}]}}))\n")
        # the project moves under the checkout
        (sol / "projects").mkdir()
        self.root = self.root.rename(sol / "projects" / "demo")
        self.status_file, self.page = self.root / "reports" / "status.json", self.root / "reports" / "html" / \
            "status.html"
        self.assertEqual(K.solaris_root(str(self.root)), str(sol))
        self.write_status(start=(NOW.date() - timedelta(days=3)).isoformat())
        # without uv on PATH it runs with this Python, in the checkout
        with mock.patch.dict(os.environ, {"PATH": str(self.tmp / "empty")}):
            code, _, err = self.run_tool("--offline")
        self.assertEqual((code, err), (0, ""))
        args = json.loads((sol / "solaris" / "tools" / "args.json").read_text())
        self.assertEqual(args, ["--dir", str(self.root), "--since", (NOW.date() - timedelta(days=3)).isoformat(),
                                "--json"])
        page = self.page.read_text()
        self.assertIn('<td class="lft">Claude (list-price estimate)</td><td>$6</td>', page)
        self.assertIn("Claude $6 of it today (list-price estimate)", page)
        self.assertIn("Claude limit $20", page)
        self.assertNotIn("Claude spend: n/a", page)


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
        code, out, _ = self.run_tool("--offline", render=True)
        self.assertEqual(code, 0)
        # the shorter charts leave the same gap: the first layout stays
        self.assertEqual(out.splitlines()[1:], [f"rendered {self.root / 'reports' / 'status.pdf'}",
                                                "layout check: 1 half-empty page(s), 0 runt(s)",
                                                "  page 2: 14% empty at the bottom (something moved to the next "
                                                "page)",
                                                "shorter charts: layout check: 1 half-empty page(s), 0 runt(s); kept "
                                                "the first layout"])
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
