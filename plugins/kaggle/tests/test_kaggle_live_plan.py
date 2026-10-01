"""Offline tests for shared/tools/kaggle_live_plan.py (stdlib unittest; stand-in gateway and kaggle_share, no network).

    python3 -m unittest discover -s plugins/kaggle/tests
"""

import contextlib
import gzip
import io
import json
import os
import shutil
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

sys.dont_write_bytecode = True  # keep __pycache__ out of the plugin folder
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "shared" / "tools"))
import kaggle_live_plan as P  # noqa: E402

SLUG = "demo-competition"
NOW = datetime(2026, 9, 28, 12, 0, tzinfo=timezone.utc)

# run the way the tool runs kaggle.py and kaggle_share.py: logs its arguments, prints <its name>.out
STAND_IN = """\
import json, os, sys
here, name = os.path.split(os.path.abspath(__file__))
with open(os.path.join(here, "calls.log"), "a") as f:
    f.write(json.dumps([name] + sys.argv[1:]) + "\\n")
with open(os.path.join(here, name + ".out"), "rb") as f:
    sys.stdout.buffer.write(f.read())
"""
SHARE_STATUS = {"quota": {"gpu": {"used": 12.5, "total": 30.0, "refresh": "2026-10-03T00:00:00Z"}},
                "gpu_hours_left": 17.5, "in_use": {"gpu": 1, "cpu": 0}, "cap": {"gpu": 2, "cpu": 5}}


def ctx(**kw):
    # what main() hands build(), with no live sources
    return {"board": None, "subs": [], "gpu": None, "hosts": None, "lease_read": "n/a", "events": [],
            "project": "Demo", "tzname": "UTC", "tz_label": "UTC", "css": "", "plan_rel": "submissions/live-plan.json",
            **kw}


def sub(sid, score, hours_ago=2):
    return {"t": NOW - timedelta(hours=hours_ago), "id": sid, "what": f"candidate {sid}", "score": score,
            "status": "COMPLETE"}


class Tmp(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        env = mock.patch.dict(os.environ, {}, clear=False)
        env.start()
        self.addCleanup(env.stop)
        os.environ.pop("KAGGLE_LB_DIR", None)
        self.addCleanup(setattr, P, "TZ", P.TZ)
        (self.tmp / "ai").mkdir()
        (self.tmp / "ai" / "manifest.json").write_text(json.dumps({"project": {"name": "Demo"}}))
        (self.tmp / "submissions").mkdir()
        self.plan_file = self.tmp / "submissions" / "live-plan.json"
        self.page = self.tmp / "reports" / "html" / "live-plan.html"
        tools = self.tmp / "tools"
        tools.mkdir()
        self.gateway, self.share = tools / "kaggle.py", tools / "kaggle_share.py"
        for f in (self.gateway, self.share):
            f.write_text(STAND_IN)
        self.calls = tools / "calls.log"
        self.gateway_says("[]")
        self.share_says(json.dumps(SHARE_STATUS))

    def gateway_says(self, out):
        Path(f"{self.gateway}.out").write_bytes(out if isinstance(out, bytes) else out.encode())

    def share_says(self, out):
        Path(f"{self.share}.out").write_text(out)

    def write_plan(self, **fields):
        plan = {"rev": 3, "competition": SLUG, "team": "Our Team", "timezone": "UTC", "phase": "Phase 1: start",
                **fields}
        self.plan_file.write_text(json.dumps(plan, indent=1) + "\n")

    def snapshot(self, scores):
        d = self.tmp / "__data" / "kaggle" / SLUG / "leaderboard"
        d.mkdir(parents=True, exist_ok=True)
        rows = [{"rank": i + 1, "team_name": f"Team {i + 1}", "score": s} for i, s in enumerate(scores)]
        with gzip.open(d / "20260928T110000Z.json.gz", "wt", encoding="utf-8") as f:
            json.dump({"fetched_at": "2026-09-28T11:00:00Z", "rows": rows}, f)

    def run_tool(self, *args):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = P.main(["--root", str(self.tmp), "--gateway", str(self.gateway), "--no-render", *args])
        return code, out.getvalue(), err.getvalue()


class EscapeTests(unittest.TestCase):
    def test_plan_text_is_escaped_but_b_i_and_code_survive(self):
        plan = {"rev": 2, "phase": "Phase 2: <b>tune</b> the <script>x()</script>", "day": "Oct 1, a<b",
                "slots": [{"id": "S1", "what": "<script>alert(1)</script>", "status": "<i>ready</i>",
                           "prediction": "LB a<b", "rule": "<code>x > y</code> & <b onclick=x>no</b>"}],
                "decisions": [{"when": "Oct 1", "text": "use List<int>"}], "compute": ["<img src=x onerror=y>"],
                "intro": "<b>Plan</b> <script>z()</script>", "tz_label": "<i>PT</i><script>"}
        page = P.build(plan, NOW, ctx(tz_label=plan["tz_label"]))
        self.assertNotIn("<script>", page)
        self.assertNotIn("<img", page)
        for text in ("&lt;script&gt;alert(1)&lt;/script&gt;", "LB a&lt;b", "Oct 1, a&lt;b", "use List&lt;int&gt;",
                     "<b>tune</b>", "<i>ready</i>", "<code>x &gt; y</code> &amp; &lt;b onclick=x&gt;no</b>",
                     "<b>Plan</b> &lt;script&gt;z()&lt;/script&gt;", "When (<i>PT</i>&lt;script&gt;)"):
            self.assertIn(text, page)

    def test_live_figures_are_escaped(self):
        board = {"n": 3, "when": NOW, "me": None, "top": {"team_name": "<img src=x onerror=y>", "score": "0.9"},
                 "gold": (1, "0.9"), "silver": (1, "0.9"), "bronze": (1, "0.9"), "level": 1,
                 "rules": ("gold = top 10%", "top 20% and 40%"), "high": True}
        subs = [dict(sub("<b>S1</b>", "0.5"), what="<script>alert(1)</script>")]
        page = P.build({"rev": 1}, NOW, ctx(board=board, subs=subs))
        self.assertNotIn("<script>", page)
        self.assertNotIn("<img", page)
        self.assertIn("&lt;img src=x onerror=y&gt;", page)
        self.assertIn("best submission &lt;b&gt;S1&lt;/b&gt;", page)


class BestSubmissionTests(Tmp):
    def best(self, scores):
        self.snapshot(scores)
        b = P.board(self.tmp, SLUG, "Team 2")
        page = P.build({"rev": 1}, NOW, ctx(board=b, subs=[sub("S1", "0.31"), sub("S2", "0.29"), sub("S3", "")]))
        return b["high"], page

    def test_lower_is_better_board_picks_the_lowest_score(self):
        high, page = self.best(["0.25", "0.28", "0.33", "0.40"])
        self.assertFalse(high)
        self.assertIn("best submission S2", page)

    def test_higher_is_better_board_picks_the_highest_score(self):
        high, page = self.best(["0.40", "0.33", "0.28", "0.25"])
        self.assertTrue(high)
        self.assertIn("best submission S1", page)


class BuildTests(Tmp):
    def test_rev_rises_in_place_and_keep_rev_leaves_the_plan_alone(self):
        text = '{"rev": 3,\n   "competition": "%s", "timezone": "UTC",\n   "phase": "Phase 1: start"}\n' % SLUG
        self.plan_file.write_text(text)
        code, out, _ = self.run_tool()
        self.assertEqual((code, out.split(":")[0]), (0, "Rev. 4"))
        self.assertEqual(self.plan_file.read_text(), text.replace('"rev": 3', '"rev": 4'))
        self.assertIn("Rev. 4</span>", self.page.read_text())
        self.assertEqual(self.run_tool("--keep-rev")[0], 0)
        self.assertEqual(self.plan_file.read_text(), text.replace('"rev": 3', '"rev": 4'))

    def test_failed_build_keeps_the_last_page_and_the_rev(self):
        self.write_plan()
        self.assertEqual(self.run_tool()[0], 0)
        page = self.page.read_bytes()
        broken ={"string inside slots": {"slots": ["S1 only"]}, "missing clock file": {"clock": "tools/none.py"},
                  "rev not a number": {"rev": "three"}}
        for case, fields in broken.items():
            self.write_plan(**fields)
            plan = self.plan_file.read_bytes()
            code, _, err = self.run_tool()
            self.assertEqual(code, 2, case)
            self.assertTrue(err.startswith("kaggle_live_plan: ") and err.count("\n") == 1, (case, err))
            self.assertEqual(self.page.read_bytes(), page, case)
            self.assertEqual(self.plan_file.read_bytes(), plan, case)
        self.assertEqual(sorted(os.listdir(self.page.parent)), ["live-plan.html"])
        self.assertEqual(sorted(os.listdir(self.plan_file.parent)), ["live-plan.json"])

    def test_slips_that_still_build(self):
        (self.tmp / "clock.py").write_text('DAILY = [("08:00", "lb-snapshot", "python3 lb.py")]\nDATED = []\n')
        self.write_plan(phase=None, clock="clock.py")
        self.share_says(json.dumps(dict(SHARE_STATUS, gpu_hours_left="17.5",
                                        quota={"gpu": {"used": "12.5", "total": "30", "refresh": None}})))
        self.assertEqual(self.run_tool()[0], 0)
        page = self.page.read_text()
        self.assertIn("Lb snapshot", page)
        self.assertIn("12.5 of 30 h", page)
        self.assertIn("17.5 h left", page)


class GatewayTests(Tmp):
    def test_no_submissions_found_is_an_empty_list(self):
        self.gateway_says("No submissions found\n")
        self.assertEqual(P.submissions(str(self.gateway), str(self.tmp), SLUG), [])
        self.write_plan()
        self.assertEqual(self.run_tool()[0], 0)
        page = self.page.read_text()
        self.assertIn("No submissions yet.", page)
        self.assertIn("0 of 5 used", page)
        self.assertNotIn("did not answer", page)

    def test_failed_or_unreadable_answers_are_none(self):
        for out in ("", "401 - Unauthorized\n", "Warning: outdated [x] {y}\n"):
            self.gateway_says(out)
            self.assertIsNone(P.submissions(str(self.gateway), str(self.tmp), SLUG), out)

    def test_output_is_read_as_utf8_with_replacement(self):
        self.gateway_says(b'[{"date": "2026-09-28 10:00:00", "description": "S1: caf\xe9", "publicScore": "0.5",'
                          b' "status": "SubmissionStatus.COMPLETE"}]\n')
        (s,) = P.submissions(str(self.gateway), str(self.tmp), SLUG)
        self.assertEqual((s["id"], s["what"], s["score"], s["status"]), ("S1", "caf" + chr(0xFFFD), "0.5", "COMPLETE"))

    def test_gateway_is_passed_to_the_share_call(self):
        self.write_plan()
        self.assertEqual(self.run_tool()[0], 0)
        calls = [json.loads(line) for line in self.calls.read_text().splitlines()]
        self.assertIn(["kaggle_share.py", "status", "--json", "--gateway", str(self.gateway)], calls)
        self.assertIn(["kaggle.py", "competitions", "submissions", SLUG, "--format", "json"], calls)
        self.assertIn("17.5 h left", self.page.read_text())


if __name__ == "__main__":
    unittest.main()
