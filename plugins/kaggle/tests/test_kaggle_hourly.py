"""Offline tests for shared/tools/kaggle_hourly.py (stdlib unittest; a stand-in gateway, no network).

    python3 -m unittest discover -s plugins/kaggle/tests

The pass runs for real: kaggle_presubmit.py's reads (kaggle_forum.py, kaggle_lb.py) and kaggle_share.py status, all
through the stand-in gateway (kaggle_standin). The browsers check reads a made-up process table
(KAGGLE_HOURLY_PS_FILE), never the machine's.
"""

import json
import os
import sys
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
from kaggle_standin import SLUG, Project, kaggle_time, utc  # noqa: E402

ISO = "%Y-%m-%dT%H:%M:%SZ"


def chrome(profile, *flags):
    # a Chrome process on a profile folder; a helper process's flags include --type=
    return " ".join(["/opt/google/chrome/chrome", f"--user-data-dir={profile}", "--remote-debugging-port=0", *flags])


class Hourly(Project):
    def setUp(self):
        super().setUp()
        self.subs([])
        self.account([])
        # the browsers check reads this file instead of `ps`, and finds browserctl's profiles here
        self.ps_file, self.bhome = self.tmp / "ps.txt", self.tmp / "browserctl"
        self.ps([])
        for env in (self.env, os.environ):
            env.update(KAGGLE_HOURLY_PS_FILE=str(self.ps_file), BROWSERCTL_HOME=str(self.bhome))
            env.pop("BROWSERCTL_PROJECT", None)

    def ps(self, rows):
        # rows: (pid, parent pid, elapsed time as ps prints it, command), as `ps -o pid=,ppid=,etime=,command=` does
        self.ps_file.write_text("".join(f"{pid:>7} {ppid:>7} {up:>11} {cmd}\n" for pid, ppid, up, cmd in rows))

    def subs(self, rows, code=0):
        # rows: (ref, description, status, public score)
        out = json.dumps([{"ref": ref, "date": "2026-10-01 01:00:00", "description": what,
                           "status": f"SubmissionStatus.{status}", "publicScore": score}
                          for ref, what, status, score in rows])
        self.answer(f"competitions submissions {SLUG} --format json", out if rows else "No submissions found\n",
                    code=code)

    def account(self, kernels, code=0):
        # kernels: (ref, status, hours since its run started); what `kaggle.py --sdk account` prints
        doc = {"fetched_at": utc().strftime(ISO), "since": utc(12).strftime(ISO), "calls": 2, "errors": [],
               "quota": {"gpu": {"used": 3.5, "remaining": 26.5, "total": 30.0, "refresh": "2026-10-03T00:00:00"}},
               "kernels": [{"ref": r, "lastRunTime": kaggle_time(h), "status": s} for r, s, h in kernels]}
        self.answer("--sdk account", json.dumps(doc) if not code else "", code=code,
                    err="kaggle_sdk: 401 Unauthorized\n" if code else "")

    def our_kernel(self, ref="alice/k1"):
        # kaggle_share maps a kernel to this project through its kernel-metadata.json and the project's stamp
        k = self.root / "kernels" / ref.split("/")[1]
        k.mkdir(parents=True)
        (k / "kernel-metadata.json").write_text(json.dumps({"id": ref, "enable_gpu": True}))
        self.assertEqual(self.tool("kaggle_share.py", "stamp")[0], 0)

    def hourly(self, *args):
        return self.tool("kaggle_hourly.py", SLUG, "--gateway", str(self.gateway), *args)

    def line(self, out, check):
        return next(x for x in out.splitlines() if x.startswith(f"{check:<10} "))


class PassTests(Hourly):
    def test_a_quiet_pass_exits_0_and_saves_no_check(self):
        code, out, err = self.hourly()
        self.assertEqual(code, 0, out + err)
        self.assertTrue(out.endswith("FLAGS: none: nothing needs a decision\n"), out)
        self.assertIn("no review recorded; triggers: no review yet", self.line(out, "review"))
        self.assertEqual(self.line(out, "board"), self.line(out, "board").split(";")[0] + "; #1 Alpha 0.9")
        self.assertEqual(self.line(out, "forum"), "forum      nothing unread")
        self.assertIn("no list before this one to compare", self.line(out, "notebooks"))
        self.assertEqual(self.line(out, "subs"), "subs       0 scoring now; no earlier pass to compare")
        self.assertIn("3.50 of 30.00 h used this week, 26.50 h left", self.line(out, "gpu"))
        self.assertEqual(self.line(out, "browsers"), "browsers   none running")
        # its check is not kept, so an --ack cannot record what nobody read
        self.assertFalse(self.store("presubmit", "seen.json").exists())
        self.assertEqual(sorted(json.loads(self.store("hourly", "last.json").read_text())),
                         ["at", "running", "schema", "submissions"])
        self.assertTrue(all(c["quiet"] == "1" for c in self.calls()))

    def test_scores_errors_and_ended_runs_flag_the_next_pass(self):
        self.our_kernel()
        self.subs([(55, "S1: base", "PENDING", "")])
        self.account([("alice/k1", "RUNNING", 1), ("bob/theirs", "QUEUED", 0.5)])
        code, out, _ = self.hourly()
        self.assertEqual(code, 0, out)
        self.assertEqual(self.line(out, "subs"), "subs       1 scoring now (S1: base); no earlier pass to compare")
        self.assertRegex(self.line(out, "kernels"), r"2 queued or running on the account; this project's: alice/k1 "
                                                    r"RUNNING 6\d min;")
        self.subs([(56, "S2: next", "ERROR", ""), (55, "S1: base", "COMPLETE", "0.912")])
        self.account([("alice/k1", "COMPLETE", 1)])
        code, out, _ = self.hourly()
        self.assertEqual(code, 10)
        self.assertEqual(self.line(out, "subs"), "subs       FLAG 0 scoring now; since the last pass: S1: base scored "
                                                 "0.912; S2: next ERROR")
        self.assertIn("ended since the last pass: alice/k1", self.line(out, "kernels"))
        self.assertIn("  - subs: a score landed or a submission errored: update the records and the plan", out)
        self.assertIn("  - kernels: a run ended: check its result, fetch its output, release its lease", out)
        # nothing new a third time
        self.assertEqual(self.hourly()[0], 0)

    def test_a_failed_read_is_a_flag_and_keeps_what_the_last_pass_saw(self):
        self.subs([(55, "S1: base", "PENDING", "")])
        self.assertEqual(self.hourly()[0], 0)
        self.subs([], code=1)
        self.board([], code=1)
        code, out, _ = self.hourly()
        self.assertEqual(code, 10)
        self.assertEqual(self.line(out, "subs"), "subs       FLAG not read")
        self.assertEqual(self.line(out, "board"), "board      FLAG not read")
        self.assertIn("  - board: the read failed: kaggle_lb.py snapshot failed (exit 1)", out)
        last = json.loads(self.store("hourly", "last.json").read_text())
        self.assertEqual(last["submissions"], {"55": {"status": "PENDING", "score": ""}})
        self.board([("Alpha", 0.9)])
        self.subs([(55, "S1: base", "COMPLETE", "0.9")])
        self.assertIn("S1: base scored 0.9", self.line(self.hourly()[1], "subs"))

    def test_an_incomplete_account_read_is_a_flag(self):
        self.account([], code=1)
        code, out, _ = self.hourly()
        self.assertEqual(code, 10)
        self.assertIn("`--sdk account` failed (exit 1)", self.line(out, "account"))


class SourceTests(Hourly):
    def test_unread_topics_and_notebooks_high_on_the_list_are_flags(self):
        self.forum([{"id": 7, "title": "A topic", "posted": 2}])
        many = [(f"user{i}/nb{i}", round(0.9 - i / 100, 2)) for i in range(20)]
        self.notebooks(many, hours_ago=2)
        code, out, _ = self.hourly()
        self.assertEqual(code, 10)
        self.assertEqual(self.line(out, "forum"), "forum      FLAG 1 new, 0 changed, 0 missing topics not shown and "
                                                  "committed yet")
        self.assertIn("show demo-competition --new, log what matters, then commit", out)
        # a new notebook low on the list is listed, not flagged
        self.notebooks([*many, ("zed/low", 0.1)], hours_ago=1)
        code, out, _ = self.hourly()
        self.assertIn("1 new, 0 re-scored since the list before: zed/low 0.1 (#21, new)", self.line(out, "notebooks"))
        self.assertNotIn("FLAG", self.line(out, "notebooks"))
        self.notebooks([("top/new", 0.95), *many], hours_ago=0.5)
        code, out, _ = self.hourly()
        self.assertIn("FLAG 1 new, 0 re-scored", self.line(out, "notebooks"))
        self.assertIn("  - notebooks: 1 new or re-scored in the top 15", out)
        # or at or better than --vs, wherever it stands
        self.notebooks([("top/new", 0.95), *many, ("late/strong", 0.97)], hours_ago=0.2)
        code, out, _ = self.hourly("--vs", "0.96")
        self.assertIn("  - notebooks: 1 new or re-scored in the top 15 or at or above 0.96", out)

    def test_a_host_post_or_page_change_since_the_review_is_a_flag(self):
        self.tool("kaggle_presubmit.py", SLUG, "--gateway", str(self.gateway))
        self.tool("kaggle_presubmit.py", SLUG, "--ack")
        self.assertEqual(self.hourly()[0], 0)
        self.pages({"rules": "Rule 1.\nRule 2, changed.\n"})
        code, out, _ = self.hourly()
        self.assertEqual(code, 10)
        self.assertIn("page change: rules (changed)", self.line(out, "review"))
        self.assertIn("  - review: a host post or a page change: run kaggle_presubmit.py demo-competition, read it, "
                      "decide, then --ack", out)

    def test_live_plan_staleness(self):
        self.assertNotIn("live plan", self.hourly()[1])
        plan = self.root / "submissions" / "live-plan.json"
        plan.parent.mkdir()
        plan.write_text(json.dumps({"rev": 3, "name": "demo-plan"}))
        page = self.root / "reports" / "html" / "demo-plan.html"
        out = self.hourly()[1]
        self.assertEqual(self.line(out, "live plan"), "live plan  FLAG reports/html/demo-plan.html not built yet")
        page.parent.mkdir(parents=True)
        page.write_text("<html></html>")
        old = time.time() - 600
        os.utime(plan, (old, old))
        self.assertEqual(self.line(self.hourly()[1], "live plan"),
                         "live plan  reports/html/demo-plan.html built 0 min ago, rev 3")
        # a build saves the raised rev into the plan a moment after the page: not a change
        os.utime(page, (old, old))
        os.utime(plan, (old + 2, old + 2))
        self.assertNotIn("FLAG", self.line(self.hourly()[1], "live plan"))
        os.utime(plan, None)
        self.assertIn("the plan changed after the last build", self.hourly()[1])
        stale = time.time() - 3 * 3600
        os.utime(plan, (stale - 60, stale - 60))
        os.utime(page, (stale, stale))
        self.assertIn("live plan: the page is 3.0 h old: rebuild it (kaggle_live_plan.py --keep-rev)", self.hourly()[1])

    def test_bad_arguments(self):
        for args in (["not a slug"], [SLUG, "--vs", "x"]):
            self.assertEqual(self.tool("kaggle_hourly.py", *args)[0], 2, args)


class BrowserTests(Hourly):
    def render(self, name):
        # the reporting plugin's renderer starts Chrome on a profile under ~/.solaris/tmp/render-*/
        return Path(os.path.expanduser("~")) / ".solaris" / "tmp" / f"render-{name}" / "profile-1"

    def test_an_orphaned_render_chrome_and_a_long_running_browser_are_flags(self):
        ours = self.bhome / "profiles" / "demo"
        self.ps([(1, 0, "40-01:00:00", "/sbin/init"),
                 (3438989, 1, "1-09:23:31", chrome(self.render("a"), "--headless=new")),
                 (3438990, 3438989, "1-09:23:30", chrome(self.render("a"), "--type=renderer")),
                 (500, 1, "03:05:00", chrome(ours / "kaggle")),
                 (501, 500, "03:04:59", chrome(ours / "kaggle", "--type=gpu-process")),
                 (600, 1, "2-00:00:00", chrome(self.bhome / "profiles" / "other" / "default", "--headless=new"))])
        code, out, _ = self.hourly()
        self.assertEqual(code, 10, out)
        self.assertEqual(self.line(out, "browsers"), "browsers   FLAG this project: kaggle up 3.1 h; 1 orphaned "
                                                     "report-render Chrome (pid 3438989, up 33.4 h); 1 other browser "
                                                     "not this project's")
        self.assertIn("  - browsers: stop what no running task needs: browserctl.py stop --profile <name>; the "
                      "reporting plugin's render.sh --reap closes an orphaned render Chrome", out)

    def test_a_live_render_a_young_browser_and_other_browsers_are_not_flags(self):
        self.ps([(700, 1, "00:12", "node /x/plugins/reporting/shared/assets/render.js html/a.html a.pdf"),
                 (701, 700, "00:11", chrome(self.render("b"), "--headless=new")),
                 (702, 701, "00:11", chrome(self.render("b"), "--type=renderer")),
                 (800, 1, "10:00", chrome(self.bhome / "profiles" / "demo" / "kaggle", "--headless=new")),
                 (801, 800, "09:59", chrome(self.bhome / "profiles" / "demo" / "kaggle", "--type=utility")),
                 (900, 1, "9-00:00:00", chrome(self.bhome / "profiles" / "other" / "default")),
                 (901, 1, "9-00:00:00", "/usr/lib/slack/slack --user-data-dir=/home/u/.config/Slack")])
        code, out, _ = self.hourly()
        self.assertEqual(code, 0, out)
        self.assertEqual(self.line(out, "browsers"), "browsers   this project: kaggle up 10 min (headless); 1 report "
                                                     "render running; 2 other browsers not this project's")
        # a process table that cannot be read is a flag like any failed read
        self.ps_file.unlink()
        code, out, _ = self.hourly()
        self.assertEqual(code, 10)
        self.assertEqual(self.line(out, "browsers"), "browsers   FLAG not read")
        self.assertIn(f"  - browsers: the read failed: cannot read {self.ps_file}", out)

    def test_elapsed_times(self):
        H = self.module("kaggle_hourly")
        for text, secs in (("23:31", 1411), ("09:23:31", 33811), ("1-09:23:31", 120211), ("06-01:15:54", 522954)):
            self.assertEqual(H.etime_seconds(text), secs, text)
        for text in ("", "31", "1-23:31", "1:2:3:4", "ELAPSED"):
            self.assertIsNone(H.etime_seconds(text), text)

    def test_this_projects_id_is_the_one_browserctl_derives(self):
        H, P = self.module("kaggle_hourly"), self.module("kaggle_presubmit")
        # the ai-pack's project slug, else its name
        self.assertEqual(H.browser_project(P, self.root), "demo")
        (self.root / "aipack" / "manifest.json").write_text(
            json.dumps({"framework_version": "0.39.0", "project": {"slug": "My_Comp.2026", "name": "demo"}}))
        self.assertEqual(H.browser_project(P, self.root), "my-comp-2026")
        with mock.patch.dict(os.environ, BROWSERCTL_PROJECT="Other Project"):
            self.assertEqual(H.browser_project(P, self.root), "other-project")
        # no ai-pack, as in an ad-hoc task: the nearest git root's name
        task = self.tmp / "Git Root" / "tasks" / "t1"
        task.mkdir(parents=True)
        (self.tmp / "Git Root" / ".git").mkdir()
        self.assertEqual(H.browser_project(P, task), "git-root")


if __name__ == "__main__":
    unittest.main()
