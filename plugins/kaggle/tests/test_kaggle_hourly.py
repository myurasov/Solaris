"""Offline tests for shared/tools/kaggle_hourly.py (stdlib unittest; a stand-in gateway, no network).

    python3 -m unittest discover -s plugins/kaggle/tests

The pass runs for real: kaggle_presubmit.py's reads (kaggle_forum.py, kaggle_lb.py) and kaggle_share.py status, all
through the stand-in gateway (kaggle_standin). The browsers check reads a made-up process table
(KAGGLE_HOURLY_PS_FILE), never the machine's.
"""

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from datetime import timedelta
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
from kaggle_standin import NOW, SLUG, TOOLS, Project, kaggle_time, load, utc  # noqa: E402

ISO = "%Y-%m-%dT%H:%M:%SZ"
# solaris.tools.ai_spend as the tests stand it in, in a checkout above the project: logs its arguments and prints
# detail.json for --detail, else today.json
FAKE_AI_SPEND = """\
import json, os, sys
here = os.path.dirname(os.path.abspath(__file__))
with open(os.path.join(here, "calls.jsonl"), "a") as f:
    f.write(json.dumps(sys.argv[1:]) + "\\n")
with open(os.path.join(here, "detail.json" if "--detail" in sys.argv else "today.json")) as f:
    sys.stdout.write(f.read())
"""
# the nvidia-brev plugin's cost ledger, as brev-run.skill.md's template starts it
BREV_HEAD = """# Brev Run-Cost Ledger

Every cloud instance this project creates gets a row: created/deleted timestamps (UTC),
rate, actual cost (billed while the instance exists, incl. setup/idle).

| Instance | Type | $/h | Created (UTC) | Deleted (UTC) | Hours | Cost | Purpose / outcome |
|---|---|---|---|---|---|---|---|
"""
BREV_TOTAL = "| **TOTAL** | | | | | **0** | **$0** | 0 closed instances; note any still-running ones here |\n"


def iso(t):
    return t.strftime(ISO)


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
            # the status check's git read finds no work tree above the temp folder, nor the user's git config or
            # global excludes
            for k in [k for k in env if k.startswith("GIT_")]:
                del env[k]
            env.update(GIT_CEILING_DIRECTORIES=str(self.tmp), GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM="1",
                       XDG_CONFIG_HOME=str(self.tmp / "xdg"))
        # a fresh status page, as kaggle_status.py leaves it (without the reporting plugin the HTML is the page)
        self.status_json, self.status_html = self.root / "reports" / "status.json", self.root / "reports" / "html" / \
            "status.html"
        self.status_html.parent.mkdir(parents=True)
        self.status_json.write_text(json.dumps({"rev": 1, "competition": SLUG}))
        self.status_html.write_text("<html></html>")

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
        self.assertIn("\nFLAGS: none: nothing needs a decision\nSTATUS (paste into the status message)\n", out)
        self.assertIn("no review recorded; triggers: no review yet", self.line(out, "review"))
        self.assertEqual(self.line(out, "board"), self.line(out, "board").split(";")[0] + "; #1 Alpha 0.9")
        self.assertEqual(self.line(out, "forum"), "forum      nothing unread")
        self.assertIn("no list before this one to compare", self.line(out, "notebooks"))
        self.assertEqual(self.line(out, "subs"), "subs       0 scoring now; no earlier pass to compare")
        self.assertIn("3.50 of 30.00 h used this week, 26.50 h left", self.line(out, "gpu"))
        self.assertEqual(self.line(out, "browsers"), "browsers   none running")
        # without a Solaris checkout above the project the AI figures are n/a, never a flag
        self.assertEqual(self.line(out, "agents"), "agents     n/a: no Solaris checkout above the project "
                                                   "(solaris.tools.ai_spend)")
        self.assertEqual(self.line(out, "spend"), "spend      Claude n/a (no Solaris checkout above the project); no "
                                                  "cost ledger (spend.jsonl)")
        self.assertEqual(out.splitlines()[-1], "for you:  0 open questions; 0 owner actions; 0 FLAG lines")
        # its check is not kept, so an --ack cannot record what nobody read
        self.assertFalse(self.store("presubmit", "seen.json").exists())
        last = json.loads(self.store("hourly", "last.json").read_text())
        self.assertEqual(sorted(last), ["at", "board", "reads", "running", "schema", "submissions"])
        self.assertEqual(sorted(last["reads"]), ["board", "notebooks"])
        self.assertTrue(all(c["quiet"] == "1" for c in self.calls()))

    def test_scores_errors_and_ended_runs_flag_the_next_pass(self):
        # a page built ten minutes ago
        old = time.time() - 600
        os.utime(self.status_json, (old - 1, old - 1))
        os.utime(self.status_html, (old, old))
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
        # the page cannot show the new score yet
        self.assertIn("  - status: the page was built before the newest score landed (S1: base 0.912): rebuild it "
                      "(kaggle_status.py --keep-rev)", out)
        self.assertIn("stopped:  kernels ended: alice/k1; claims n/a", out)
        # once it is rebuilt, nothing is new a third time
        self.status_html.write_text("<html></html>")
        code, out, _ = self.hourly()
        self.assertEqual(code, 0, out)
        self.assertIn("stopped:  kernels ended: none;", out)

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

    def test_status_page_staleness(self):
        status, page = self.status_json, self.status_html
        status.write_text(json.dumps({"rev": 3}))
        old = time.time() - 600
        os.utime(status, (old, old))
        self.assertEqual(self.line(self.hourly()[1], "status"),
                         "status     reports/html/status.html built 0 min ago, rev 3")
        page.unlink()
        out = self.hourly()[1]
        self.assertEqual(self.line(out, "status"), "status     FLAG reports/html/status.html not built yet")
        self.assertIn("  - status: never built: build it (kaggle_status.py)", out)
        page.write_text("<html></html>")
        # a build saves the raised rev into the JSON a moment after the page: not a change
        os.utime(page, (old, old))
        os.utime(status, (old + 2, old + 2))
        self.assertNotIn("FLAG", self.line(self.hourly()[1], "status"))
        os.utime(status, None)
        code, out, _ = self.hourly()
        self.assertEqual(code, 10)
        self.assertIn("  - status: the status JSON changed after the last build: rebuild the page (kaggle_status.py)",
                      out)
        stale = time.time() - 3 * 3600
        os.utime(status, (stale - 60, stale - 60))
        os.utime(page, (stale, stale))
        self.assertIn("  - status: the page is 3.0 h old: rebuild it (kaggle_status.py --keep-rev)", self.hourly()[1])
        # with the reporting plugin attached the PDF is the page the owner reads
        assets = self.root / "aipack" / "plugins" / "reporting" / "assets"
        assets.mkdir(parents=True)
        (assets / "render.sh").write_text("")
        self.assertEqual(self.line(self.hourly()[1], "status"), "status     FLAG reports/status.pdf not built yet")
        (self.root / "reports" / "status.pdf").write_bytes(b"%PDF")
        self.assertEqual(self.line(self.hourly()[1], "status"), "status     reports/status.pdf built 0 min ago, rev 3")

    def test_a_missing_or_unreadable_status_json_and_the_retired_live_plan(self):
        self.status_json.write_text("{not json")
        out = self.hourly()[1]
        self.assertEqual(self.line(out, "status"), "status     FLAG reports/status.json is not a status JSON")
        self.assertIn("  - status: unreadable: fix the status JSON, then rebuild the page (kaggle_status.py)", out)
        self.status_json.unlink()
        code, out, _ = self.hourly()
        self.assertEqual(code, 10)
        self.assertEqual(self.line(out, "status"), "status     FLAG reports/status.json missing")
        self.assertIn("  - status: no status page: write reports/status.json, then build it (kaggle_status.py)", out)
        plan = self.root / "submissions" / "live-plan.json"
        plan.parent.mkdir()
        plan.write_text(json.dumps({"rev": 9}))
        out = self.hourly()[1]
        self.assertEqual(self.line(out, "status"),
                         "status     FLAG submissions/live-plan.json and no reports/status.json")
        self.assertIn("  - status: live plan retired: move to reports/status.json", out)
        # --status names another JSON, relative to the working folder
        (self.root / "other.json").write_text(json.dumps({"rev": 4}))
        self.assertEqual(self.line(self.hourly("--status", "other.json")[1], "status"),
                         "status     reports/html/status.html built 0 min ago, rev 4")
        self.assertEqual(self.line(self.hourly("--status", "none.json")[1], "status"),
                         "status     FLAG none.json missing")

    @unittest.skipUnless(shutil.which("git"), "git is not installed")
    def test_a_page_file_git_would_not_ignore_is_a_flag(self):
        def git(*args):
            subprocess.run(["git", "-C", str(self.root), *args], env=self.env, capture_output=True, check=True)

        quiet = "status     reports/html/status.html built 0 min ago, rev 1"
        # outside a git work tree there is nothing to check
        self.assertEqual(self.line(self.hourly()[1], "status"), quiet)
        git("init", "-q")
        code, out, _ = self.hourly()
        self.assertEqual(code, 10)
        self.assertEqual(self.line(out, "status"), "status     FLAG reports/html/status.html built 0 min ago, rev 1; "
                                                   "not ignored by git: reports/status.pdf, reports/html/status.html, "
                                                   "reports/status-short.pdf, reports/html/status-short.html")
        self.assertIn("  - status: keep the page out of git: add reports/status*.pdf and reports/html/status*.html to "
                      "the project's .gitignore (and git rm --cached any already committed)", out)
        # the page's own names miss the shorter-chart try a killed build can leave
        gitignore = self.root / ".gitignore"
        gitignore.write_text("reports/status.pdf\nreports/html/status.html\n")
        self.assertTrue(self.line(self.hourly()[1], "status").endswith(
            "rev 1; not ignored by git: reports/status-short.pdf, reports/html/status-short.html"))
        gitignore.write_text("reports/status*.pdf\nreports/html/status*.html\n")
        code, out, _ = self.hourly()
        self.assertEqual((code, self.line(out, "status")), (0, quiet))
        # a page already committed stays in git whatever .gitignore says
        git("add", "-f", "reports/html/status.html")
        line = self.line(self.hourly()[1], "status")
        self.assertTrue(line.endswith("rev 1; not ignored by git: reports/html/status.html"), line)
        # and a flag about the page itself comes first
        (self.root / "reports" / "html" / "status.html").unlink()
        out = self.hourly()[1]
        self.assertIn("  - status: never built: build it (kaggle_status.py); keep the page out of git: add ", out)

    def test_a_task_folder_needs_no_status_page(self):
        H, P = self.module("kaggle_hourly"), self.module("kaggle_presubmit")
        task = self.tmp / "task"
        task.mkdir()
        (task / "notes.md").write_text("made with the ad-hoc-task skill\n")
        self.assertIsNone(H.status_page(P, task, task / "reports" / "status.json", P.utc_now()))
        line, flag = H.status_page(P, task, task / "reports" / "status.json", P.utc_now(), given=True)
        self.assertTrue(line.endswith("status.json missing") and flag.startswith("no status page"), (line, flag))

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


def block_of(out):
    """The STATUS block of a pass's text output: {field: text}."""
    tail = out.split("STATUS (paste into the status message)\n", 1)[1].splitlines()
    return {x[:9].rstrip()[:-1]: x[10:] for x in tail}


class BlockTests(Hourly):
    def test_the_status_block_from_the_reads_and_the_pack(self):
        P = self.module("kaggle_presubmit")
        kday = NOW.replace(hour=0, minute=0, second=0)
        self.answer(f"competitions submissions {SLUG} --format json", json.dumps([
            {"ref": 57, "date": (kday + (NOW - kday) / 2).strftime("%Y-%m-%d %H:%M:%S"), "description": "S2: next",
             "status": "SubmissionStatus.PENDING", "publicScore": ""},
            {"ref": 55, "date": (kday - timedelta(hours=30)).strftime("%Y-%m-%d %H:%M:%S"), "description": "S1: base",
             "status": "SubmissionStatus.COMPLETE", "publicScore": "0.85"}]))
        self.board([("Alpha", 0.95), ("Beta", 0.9), ("Our Team", 0.85), ("Delta", 0.8),
                    *((f"T{i}", round(0.7 - i / 100, 2)) for i in range(8))])
        self.status_json.write_text(json.dumps({
            "rev": 2, "competition": SLUG, "team": "Our Team", "daily_slots": 5,
            "pick": "S3 tonight, medium confidence",
            "questions": ["Raise the GPU budget?", {"text": "Merge with another team?", "since": "Oct 1"}],
            "owner_actions": ["Accept the new rules"]}))
        self.status_html.write_text("<html></html>")
        mem = self.root / "aipack" / ".memory"
        mem.mkdir()
        (mem / "hosts.json").write_text(json.dumps({"hosts": [{"name": "gpu-1"}, {"name": "gpu-2"}]}))
        end = NOW + timedelta(hours=30)
        (mem / "lease-ends.json").write_text(json.dumps({"read": iso(NOW), "ends": {
            "gpu-1": iso(end), "gpu-2": iso(NOW - timedelta(hours=5))}}))
        code, out, _ = self.hourly()
        self.assertEqual(code, 0, out)
        block = block_of(out)
        self.assertEqual(list(block), ["pick", "score", "rank", "compute", "stopped", "spend", "for you"])
        self.assertEqual(block["pick"], f"1 of 5 slots used today, the next reset {P.local(kday + timedelta(days=1))}; "
                                        "1 pending (S2: next); pick: S3 tonight, medium confidence")
        self.assertRegex(block["score"],
                         r"^0\.85; gold 0\.95 \(#1\), silver 0\.9 \(#2\), bronze 0\.8 \(#4\) \(board .+\)$")
        self.assertRegex(block["rank"], r"^#3 of 12 teams \(board .+\)$")
        self.assertTrue(block["compute"].startswith(
            f"2 machines (gpu-1, gpu-2); nearest booking end gpu-1 {P.local(end)} (30 h left); Kaggle GPU week: "
            "3.50 of 30.00 h used this week"), block["compute"])
        self.assertEqual(block["stopped"], "kernels n/a (no earlier pass); claims n/a (they live on the hosts: "
                                           "hostclaims.py status)")
        self.assertEqual(block["spend"], "Claude n/a (no Solaris checkout above the project); no cost ledger "
                                         "(spend.jsonl)")
        self.assertEqual(block["for you"], "2 open questions: 1. Raise the GPU budget?; 2. Merge with another team?; "
                                           "1 owner action: 1. Accept the new rules; 0 FLAG lines")
        # --json prints the same pass as one object
        code, out, _ = self.hourly("--json")
        doc = json.loads(out)
        self.assertEqual((code, doc["slug"], doc["flags"]), (0, SLUG, 0))
        self.assertEqual(list(doc["block"]), list(block))

        def same(b):
            # this pass read the board again: only the snapshot's time may differ
            return {k: re.sub(r"\(board [^)]*\)", "(board)", v) for k, v in b.items() if k != "stopped"}

        self.assertEqual(same(doc["block"]), same(block))
        self.assertEqual(doc["block"]["stopped"], "kernels ended: none; claims n/a (they live on the hosts: "
                                                  "hostclaims.py status)")
        self.assertIn({"check": "subs", "line": "1 scoring now (S2: next); nothing landed since the last pass",
                       "flag": None}, doc["checks"])

    def test_what_the_block_cannot_tell_is_na(self):
        self.status_json.write_text(json.dumps({"rev": 1, "team": "Nobody"}))
        self.status_html.write_text("<html></html>")
        self.subs([(55, "S1: base", "COMPLETE", "0.88")])
        block = block_of(self.hourly()[1])
        self.assertEqual(block["rank"], "n/a (the team is not on the board)")
        self.assertRegex(block["score"], r"^0\.88 \(our best submission; the team is not on the board\); gold ")
        self.assertTrue(block["pick"].startswith("0 slots used today (limit n/a: the status JSON's daily_slots)"))
        self.assertEqual(block["compute"].split("; ")[:2], ["machines n/a (no hosts.json)",
                                                             "booking ends n/a (no lease-ends.json)"])


class RateLimitTests(Hourly):
    def test_a_429_stops_the_passs_kaggle_reads_with_one_line(self):
        self.answer(f"competitions leaderboard {SLUG} --show --format json --page-size 200",
                    "429 - Too Many Requests\n", code=1)
        code, out, _ = self.hourly()
        self.assertEqual(code, 10, out)
        self.assertEqual(self.line(out, "rate-limited"),
                         "rate-limited FLAG Kaggle answered HTTP 429 (Too Many Requests) to the board read; skipped "
                         "after it: subs, kernels, gpu")
        self.assertEqual([x for x in out.splitlines() if x.startswith("  - ")],
                         ["  - rate-limited: Kaggle is rate limiting the account: wait a few minutes before the next "
                          "Kaggle read (the skipped checks run at the next pass)"])
        for check in ("board", "subs", "kernels", "gpu"):
            self.assertFalse([x for x in out.splitlines() if x.startswith(f"{check:<10} ")], check)
        calls = [" ".join(c["args"]) for c in self.calls()]
        self.assertFalse([c for c in calls if c.startswith(("competitions submissions", "--sdk account"))], calls)
        block = block_of(out)
        self.assertEqual(block["pick"], "slots n/a (submissions rate limited)")
        self.assertIn("Kaggle GPU week n/a (rate limited)", block["compute"])
        self.assertTrue(block["stopped"].startswith("kernels n/a (rate limited)"))
        # an earlier read answered 429: every read after it waits for the next pass
        self.answer(f"competitions pages {SLUG} list {SLUG} --content --format json",
                    "429 Client Error: Too Many Requests for url: https://www.kaggle.com/api/v1/pages\n", code=1)
        code, out, _ = self.hourly()
        self.assertEqual(code, 10)
        self.assertEqual(self.line(out, "rate-limited"), "rate-limited FLAG Kaggle answered HTTP 429 (Too Many "
                                                         "Requests) to the pages read; skipped after it: notebooks, "
                                                         "board, subs, kernels, gpu")
        self.assertIn("review     ", out)
        self.assertNotIn("pages      ", out)

    def test_a_bare_429_is_no_rate_limit(self):
        # a full board of exactly 429 teams: kaggle_lb.py prints "429 rows" (a rate limit to the old matcher)
        self.board([(f"T{i:03d}", round(0.9 - i / 10000, 4)) for i in range(429)])
        code, out, _ = self.hourly()
        self.assertEqual(code, 0, out)
        self.assertNotIn("rate-limited", out)
        self.assertTrue(self.line(out, "board").startswith("board      429 teams, snapshot "))
        for check in ("subs", "kernels", "gpu"):
            self.assertTrue([x for x in out.splitlines() if x.startswith(f"{check:<10} ")], check)
        # a read that crashes through line 429 is a failed read, not a rate limit: the reads after it still run
        self.answer(f"competitions pages {SLUG} list {SLUG} --content --format json", "",
                    err='Traceback (most recent call last):\n  File "/x/kaggle.py", line 429, in main\n'
                        "ValueError: boom\n", code=1)
        code, out, _ = self.hourly()
        self.assertEqual(code, 10, out)
        self.assertNotIn("rate-limited", out)
        self.assertEqual(self.line(out, "pages"), "pages      FLAG not read")
        self.assertIn("  - pages: the read failed: competitions pages failed (exit 1): Traceback (most recent call "
                      'last): File "/x/kaggle.py", line 429, in main ValueError: boom', out)
        for check in ("board", "subs", "kernels"):
            self.assertTrue([x for x in out.splitlines() if x.startswith(f"{check:<10} ")], check)


class JumpTests(Hourly):
    @staticmethod
    def teams(**moved):
        """60 teams 0.01 apart, best first, with the scores moved names."""
        rows = {f"T{i:02d}": round(0.9 - i / 100, 2) for i in range(60)}
        rows.update(moved)
        return sorted(rows.items(), key=lambda x: -x[1])

    def test_a_top_team_jump_is_a_flag(self):
        self.board(self.teams())
        self.assertEqual(self.hourly()[0], 0)
        # T55 comes from #56 to #1, T05 gains 2.1% (and keeps #6 behind it), T02 gains only 1%
        self.board(self.teams(T05=0.868, T55=0.95, T02=0.8888))
        code, out, _ = self.hourly()
        self.assertEqual(code, 10, out)
        line = self.line(out, "board")
        self.assertIn("; jump since ", line)
        self.assertTrue(line.endswith(": T55 #56 -> #1 (0.95); T05 #6 -> #6, 0.85 -> 0.868, +2.1%"), line)
        self.assertIn("  - board: a top team jumped: refresh the top-teams research and record a decision within a "
                      "day", out)
        # the next pass compares with the snapshot this one saw
        self.assertEqual(self.hourly()[0], 0)
        H, P = self.module("kaggle_hourly"), self.module("kaggle_presubmit")
        first, moved = H.full_snapshots(P, self.root, SLUG)[:2]
        self.assertEqual(H.jump_moves(P, self.root, SLUG, moved, first, ("%", 5.0)), ["T55 #56 -> #1 (0.95)"])
        self.assertEqual(len(H.jump_moves(P, self.root, SLUG, moved, first, ("abs", 0.001))), 3)


class GitTests(Hourly):
    def git(self, *args, hours_ago=0.0, where=None):
        when = f"@{int(time.time() - hours_ago * 3600)} +0000"
        env = dict(self.env, GIT_AUTHOR_DATE=when, GIT_COMMITTER_DATE=when)
        subprocess.run(["git", "-c", "user.name=Test", "-c", "user.email=test@example.com", "-C",
                        str(where or self.root), *args], env=env, capture_output=True, check=True)

    def pushed_repo(self, where, remote):
        """A repo in where with one commit pushed to a bare remote: its main tracks origin/main."""
        where.mkdir(parents=True, exist_ok=True)
        subprocess.run(["git", "init", "-q", "--bare", str(remote)], env=self.env, capture_output=True, check=True)
        self.git("init", "-q", "-b", "main", where=where)
        self.git("commit", "-q", "--allow-empty", "-m", "one", hours_ago=3, where=where)
        self.git("remote", "add", "origin", str(remote), where=where)
        self.git("push", "-q", "-u", "origin", "main", where=where)

    @unittest.skipUnless(shutil.which("git"), "git is not installed")
    def test_commits_not_pushed_for_over_an_hour_are_a_flag(self):
        # outside a git work tree there is no git line
        self.assertNotIn("\ngit        ", self.hourly()[1])
        (self.root / ".gitignore").write_text("reports/status*.pdf\nreports/html/status*.html\n__data/\n")
        self.git("init", "-q", "-b", "main")
        self.git("add", ".gitignore")
        self.git("commit", "-q", "-m", "one", hours_ago=3)
        code, out, _ = self.hourly()
        self.assertEqual((code, self.line(out, "git")),
                         (0, "git        main has no upstream branch: unpushed commits not counted"))
        remote = self.tmp / "remote.git"
        subprocess.run(["git", "init", "-q", "--bare", str(remote)], env=self.env, capture_output=True, check=True)
        self.git("remote", "add", "origin", str(remote))
        self.git("push", "-q", "-u", "origin", "main")
        self.assertEqual(self.line(self.hourly()[1], "git"), "git        main: every commit pushed to origin/main")
        self.git("commit", "-q", "--allow-empty", "-m", "two", hours_ago=0.5)
        code, out, _ = self.hourly()
        self.assertEqual(code, 0, out)
        self.assertRegex(self.line(out, "git"),
                         r"^git        main: 1 commit not pushed to origin/main, the oldest from .+ \(30 min ago\)$")
        self.git("commit", "-q", "--allow-empty", "-m", "three", hours_ago=2)
        code, out, _ = self.hourly()
        self.assertEqual(code, 10)
        self.assertRegex(self.line(out, "git"), r"^git        FLAG main: 2 commits not pushed to origin/main, the "
                                                r"oldest from .+ \(2\.0 h ago\)$")
        # the flag leaves the push to the project's rules
        self.assertIn("  - git: commits unpushed for over an hour (the project root): push per the project's rules "
                      "(where none allows a push, ask the owner)", out)

    @unittest.skipUnless(shutil.which("git"), "git is not installed")
    def test_a_code_repo_in_source_is_checked_too(self):
        # the project root is no repo; its code repo sits in source/
        source = self.root / "source"
        self.pushed_repo(source, self.tmp / "source.git")
        self.assertEqual(self.line(self.hourly()[1], "git"),
                         "git        source/: main: every commit pushed to origin/main")
        self.git("commit", "-q", "--allow-empty", "-m", "two", hours_ago=2, where=source)
        code, out, _ = self.hourly()
        self.assertEqual(code, 10, out)
        self.assertRegex(self.line(out, "git"), r"^git        FLAG source/: main: 1 commit not pushed to origin/main, "
                                                r"the oldest from .+ \(2\.0 h ago\)$")
        self.assertIn("  - git: commits unpushed for over an hour (source/): push per the project's rules (where none "
                      "allows a push, ask the owner)", out)
        # a root that is a repo of its own as well: both are read
        (self.root / ".gitignore").write_text("reports/status*.pdf\nreports/html/status*.html\n__data/\nsource/\n")
        self.git("init", "-q", "-b", "main")
        self.assertRegex(self.line(self.hourly()[1], "git"),
                         r"^git        FLAG main has no upstream branch: unpushed commits not counted; source/: main: "
                         r"1 commit not pushed to origin/main, the oldest from .+ \(2\.0 h ago\)$")

    @unittest.skipUnless(shutil.which("git"), "git is not installed")
    def test_a_root_inside_another_repo_has_none_of_its_own(self):
        # a synced copy inside a larger repo, say: git may look above the project here
        for env in (self.env, os.environ):
            env["GIT_CEILING_DIRECTORIES"] = str(self.tmp.parent)
        self.git("init", "-q", "-b", "main", where=self.tmp)
        code, out, _ = self.hourly()
        self.assertEqual(self.line(out, "git"), "git        no git repository of the project's own (its root or "
                                                "source/): nothing to check")
        self.assertNotIn("  - git:", out)


class AiTests(Hourly):
    """The agents and spend lines, from a stand-in ai_spend in a Solaris checkout above the project."""

    def setUp(self):
        super().setUp()
        self.sol = self.tmp / "solaris" / "tools"
        self.sol.mkdir(parents=True)
        for f in (self.tmp / "solaris" / "__init__.py", self.sol / "__init__.py"):
            f.write_text("")
        (self.sol / "ai_spend.py").write_text(FAKE_AI_SPEND)
        # no uv on PATH: kaggle_status.py's ai_spend runner falls back to this Python, in the checkout
        self.env["PATH"] = str(self.tmp / "no-bin")
        self.start = NOW - timedelta(hours=6)
        self.workers([("a1", "claude-sonnet-5-5"), ("b2", "claude-fable-5-1"), ("c3", "claude-haiku-4-5-20251001")])
        self.today(12.0, 10)
        self.mem = self.root / "aipack" / ".memory"
        self.mem.mkdir()

    def workers(self, workers, effort=None, failures=()):
        detail = {"main": {"turns": 12, "models": {"claude-opus-5-5": 12}, "effort": effort or {"max": 10, "high": 2}},
                  "workers": [{"session": "5e55a0b1", "agent": a, "first": "2026-10-07T18:01:00-07:00", "model": m,
                               "effort": "max", "usd": 1.0} for a, m in workers],
                  "hooks": {"runs": 9, "failed": len(failures), "failures": list(failures)}}
        (self.sol / "detail.json").write_text(json.dumps({"rows": [], "budgets": [], "detail": detail}))

    def today(self, spent, limit=None):
        # the stand-in's "today" starts six hours ago, so every cost below falls in a known window
        (self.sol / "today.json").write_text(json.dumps({
            "since": self.start.isoformat(), "rows": [{"day": str(self.start.date()), "usd": spent}],
            "budgets": [{"daily_budget_usd": limit, "today_usd": spent, "over": spent > limit}] if limit else [],
            "problems": []}))

    def config(self, settings):
        (self.mem / "config.json").write_text(json.dumps(settings))

    def ai_calls(self):
        return [json.loads(x) for x in (self.sol / "calls.jsonl").read_text().splitlines()]

    def test_agents_flags_a_worker_model_and_a_failed_hook(self):
        self.workers([("a1", "claude-sonnet-5-5"), ("b2", "claude-fable-5-1"), ("c3", "claude-haiku-4-5-20251001")],
                     failures=[{"ts": "2026-10-07T18:05:00-07:00", "event": "UserPromptSubmit",
                                "text": "Traceback: boom"}])
        code, out, _ = self.hourly()
        self.assertEqual(code, 10, out)
        line = self.line(out, "agents")
        self.assertIn(": master 12 responses (claude-opus-5-5 12; effort max 10, high 2); 3 workers "
                      "(claude-sonnet-5-5 1, claude-fable-5-1 1, claude-haiku-4-5-20251001 1); hooks 9 runs, 1 failed",
                      line)
        flag = next(x for x in out.splitlines() if x.startswith("  - agents: "))
        for part in ("1 worker on a model outside the allowed list: claude-fable-5-1 (session 5e55a0b1, agent b2); "
                     "pass the model on every launch",
                     "1 hook run failed (newest: UserPromptSubmit at ",
                     ": Traceback: boom): fix the hook"):
            self.assertIn(part, flag)
        # the effort mix is information: the pack sets no level, so none is judged
        self.assertNotIn("effort", flag)
        first = json.loads(self.store("hourly", "last.json").read_text())["at"]
        # the next pass asks from the last pass on; allowed models and working hooks are quiet, whatever the efforts
        self.workers([("a1", "claude-sonnet-5-5"), ("c3", "claude-haiku-4-5-20251001")], effort={"xhigh": 7, "max": 5})
        self.today(2.0)
        code, out, _ = self.hourly()
        self.assertEqual(code, 0, out)
        self.assertIn("(claude-opus-5-5 12; effort xhigh 7, max 5); 2 workers", self.line(out, "agents"))
        detail = [c for c in self.ai_calls() if "--detail" in c]
        self.assertEqual(detail[-1][:2], ["--dir", str(self.root)])
        since = detail[-1][detail[-1].index("--since") + 1]
        P = self.module("kaggle_presubmit")
        self.assertEqual(P.parse_time(since), P.parse_time(first))
        self.assertEqual(detail[-1][-2:], ["--detail", "--json"])
        self.assertIn(["--dir", str(self.root), "--today", "--json"], self.ai_calls())

    def test_the_master_effort_is_judged_only_against_the_packs_setting(self):
        self.workers([("a1", "claude-sonnet-5-5")], effort={"xhigh": 7, "max": 5})
        self.today(2.0)
        # unset: a mix of levels is shown, never flagged
        code, out, _ = self.hourly()
        self.assertEqual(code, 0, out)
        self.assertIn("(claude-opus-5-5 12; effort xhigh 7, max 5); 1 worker", self.line(out, "agents"))
        # set: the responses at another level are a flag
        self.config({"kaggle.master_effort": "xhigh"})
        code, out, _ = self.hourly()
        self.assertEqual(code, 10, out)
        self.assertIn("(claude-opus-5-5 12; effort xhigh 7, max 5; expected xhigh); 1 worker",
                      self.line(out, "agents"))
        self.assertIn("  - agents: the master ran at another effort than the pack's kaggle.master_effort (xhigh): "
                      "max 5 of 12 responses; tell the owner, whose choice the effort is", out)
        # every response at the level the pack sets (in any case) is quiet, whichever level that is
        self.config({"kaggle.master_effort": "Max"})
        self.workers([("a1", "claude-sonnet-5-5")], effort={"max": 12})
        self.assertEqual(self.hourly()[0], 0)
        self.config({"kaggle.master_effort": "high"})
        self.workers([("a1", "claude-sonnet-5-5")], effort={"high": 12})
        self.assertEqual(self.hourly()[0], 0)
        # a setting that names no level is a flag of its own, and judges nothing
        self.config({"kaggle.master_effort": 5})
        code, out, _ = self.hourly()
        self.assertEqual(code, 10, out)
        flag = next(x for x in out.splitlines() if x.startswith("  - agents: "))
        self.assertEqual(flag, "  - agents: kaggle.master_effort in aipack/.memory/config.json must be an effort "
                               "level, as the transcripts record it: fix it")

    def test_the_pack_sets_the_allowed_models(self):
        self.config({"kaggle.allowed_models": ["claude-*"]})
        out = self.hourly()[1]
        self.assertNotIn("outside the allowed list", out)
        self.config({"kaggle.allowed_models": "claude-opus-5-5"})
        out = self.hourly()[1]
        self.assertIn("kaggle.allowed_models in aipack/.memory/config.json must be a list of model names (* allowed): "
                      "fix it", out)
        self.assertIn("claude-fable-5-1 (session 5e55a0b1, agent b2)", out)

    def test_spend_against_each_limit_without_a_total(self):
        day = str(self.start.date())
        (self.mem / "spend.jsonl").write_text("".join(json.dumps(x) + "\n" for x in (
            {"day": day, "category": "cloud GPU", "usd": 3, "what": "a rental"},
            {"day": day, "category": "OpenCode", "usd": 1.25, "what": "review calls"},
            {"day": str(self.start.date() - timedelta(days=1)), "category": "cloud GPU", "usd": 50, "what": "old"}))
            + "not json\n")
        s = self.start

        def row(name, start, end, cost):
            return f"| {name} | 1x L4 | 4.79 | {iso(start)} | {iso(end)} (requested) | 1 | {cost} | a job |\n"

        # the TOTAL row still mentions an instance whose row has landed: its row counts it, the note does not
        total = (f"| **TOTAL** | | | | | **9** | **$145** | 3 closed instances. gone-one (8x H100, $43.20/h) stopped "
                 f"{iso(s - timedelta(hours=40))} and deleted {iso(s - timedelta(hours=20))} (see its row). |\n")
        (self.mem / "brev-costs.md").write_text(
            BREV_HEAD + row("half-today", s - timedelta(hours=2), s + timedelta(hours=2), "~$40")
            + row("all-today", s + timedelta(hours=1), s + timedelta(hours=3), "$6 (usage feed)")
            + row("yesterday", s - timedelta(hours=30), s - timedelta(hours=26), "about $99 at the stop")
            + "| broken | x | 1 | not a time | | | $3 | |\n"
            + f"| never-ran | x | 1 | {iso(s)} (create failed) | - | 0 | $0 | the type was refused |\n" + total)
        self.config({"brev.daily_limit_usd": 25})
        code, out, _ = self.hourly()
        self.assertEqual(code, 10, out)
        self.assertEqual(self.line(out, "spend"),
                         "spend      FLAG Claude $12.00 today of $10.00; ledger today: cloud GPU $3.00, OpenCode $1.25 "
                         "(1 line unreadable); Brev $26.00 today of $25.00 (brev-costs.md; 1 row unreadable)")
        self.assertIn("  - spend: Claude spend reached its daily budget ($12.00 of $10.00): economize and tell the "
                      "owner; Brev spend reached its daily limit ($26.00 of $25.00): tell the owner before more paid "
                      "work", out)
        self.assertEqual(block_of(out)["spend"], self.line(out, "spend")[len("spend      FLAG "):])
        # under the limits: no flag; a bad limit is one
        self.today(2.0)
        self.config({"brev.daily_limit_usd": 100})
        self.assertNotIn("  - spend:", self.hourly()[1])
        self.config({"brev.daily_limit_usd": "lots"})
        self.assertIn("brev.daily_limit_usd in aipack/.memory/config.json must be a positive number of US dollars",
                      self.hourly()[1])

    def test_a_running_brev_instance_counts_while_it_bills(self):
        s = self.start
        self.today(2.0)
        # nothing closed yet: one instance billing for two hours now, one stopped after an hour's run today, one
        # that stopped before today (no part today), as the TOTAL row notes them
        (self.mem / "brev-costs.md").write_text(BREV_HEAD + (
            f"| **TOTAL** | | | | | **0** | **$0** | 0 closed instances; big-run (8x H100, $40/h) running since "
            f"{iso(s + timedelta(hours=4))} for a long job; parked (1x L4, $2 per hour) created "
            f"{iso(s + timedelta(hours=1))}, stopped {iso(s + timedelta(hours=2))}, waits for the next run; "
            f"old (1x L4, $3/h) stopped {iso(s - timedelta(hours=3))} |\n"))
        self.config({"brev.daily_limit_usd": 50})
        before = time.time()
        code, out, _ = self.hourly()
        after = time.time()
        self.assertEqual(code, 10, out)
        m = re.fullmatch(r"spend      FLAG Claude \$2\.00 today \(no daily budget set\); no cost ledger "
                         r"\(spend\.jsonl\); Brev \$([\d.]+) today of \$50\.00 \(brev-costs\.md, with 2 running or "
                         r"stopped instances from its TOTAL row\)", self.line(out, "spend"))
        self.assertTrue(m, self.line(out, "spend"))
        # $40 an hour from its start to the pass, and $2 for the parked hour
        running = [(t - (s + timedelta(hours=4)).timestamp()) / 3600 for t in (before, after)]
        self.assertTrue(2 + 40 * running[0] - 0.01 <= float(m.group(1)) <= 2 + 40 * running[1] + 0.01, m.group(1))
        self.assertIn(f"  - spend: Brev spend reached its daily limit (${m.group(1)} of $50.00): tell the owner before "
                      "more paid work", out)
        # a running note without an ISO start time cannot be counted: said, and flagged under a limit
        (self.mem / "brev-costs.md").write_text(
            BREV_HEAD + "| **TOTAL** | | | | | | | mystery (4x L4, $9/h) running since this morning |\n")
        code, out, _ = self.hourly()
        self.assertEqual(code, 10, out)
        self.assertTrue(self.line(out, "spend").endswith(
            "Brev $0.00 today of $50.00 (brev-costs.md; 1 running note not counted (no ISO start or stop time))"))
        self.assertIn("  - spend: 1 running Brev instance in brev-costs.md could not be counted against the limit: "
                      "note each running or stopped instance in its TOTAL row with its $/h rate and ISO start time "
                      "(and stop time)", out)
        # without a limit it is said, not flagged
        self.config({})
        self.assertNotIn("  - spend:", self.hourly()[1])

    def test_an_ai_spend_that_gives_no_report_is_a_flag(self):
        (self.sol / "detail.json").write_text("Traceback (most recent call last): ...\n")
        (self.sol / "today.json").write_text("{}\n")
        code, out, _ = self.hourly()
        self.assertEqual(code, 10)
        self.assertEqual(self.line(out, "agents"), "agents     FLAG not read")
        self.assertIn("  - agents: the read failed: solaris.tools.ai_spend --detail gave no report", out)
        self.assertIn("  - spend: the read failed: solaris.tools.ai_spend --today gave no report", out)


class ReuseTests(Hourly):
    def presubmit(self, *args):
        before = len(self.calls())
        code, out, _ = self.tool("kaggle_presubmit.py", SLUG, "--gateway", str(self.gateway), *args)
        return code, out, [" ".join(c["args"]) for c in self.calls()[before:]]

    def test_the_presubmit_check_reuses_a_young_hourly_read(self):
        self.assertEqual(self.hourly()[0], 0)
        P = self.module("kaggle_presubmit")
        last = json.loads(self.store("hourly", "last.json").read_text())
        code, out, calls = self.presubmit()
        self.assertEqual(code, 10, out)
        self.assertRegex(out, r"\nREUSED: the last hourly pass's notebook list of .+ and board snapshot of .+ "
                              r"\(at most --fresh 20 min old\), not read again\n")
        self.assertFalse([c for c in calls if c.startswith(("competitions leaderboard", "--sdk notebooks"))], calls)
        self.assertTrue([c for c in calls if c.startswith("competitions pages")], calls)
        seen = json.loads(self.store("presubmit", "seen.json").read_text())["view"]
        self.assertEqual((seen["board"]["file"], seen["notebooks"]["file"]),
                         (last["reads"]["board"], last["reads"]["notebooks"]))
        # --fresh 0 reads both again, and so does a check after an hourly pass older than --fresh
        code, out, calls = self.presubmit("--fresh", "0")
        self.assertNotIn("REUSED", out)
        self.assertTrue([c for c in calls if c.startswith("competitions leaderboard")], calls)
        last["at"] = P.iso(P.utc_now() - timedelta(minutes=30))
        self.store("hourly", "last.json").write_text(json.dumps(last))
        self.assertNotIn("REUSED", self.presubmit()[1])
        self.assertIn("REUSED", self.presubmit("--fresh", "45")[1])
        self.assertEqual(self.tool("kaggle_presubmit.py", SLUG, "--ack", "--fresh", "5")[0], 2)


class HelperTests(unittest.TestCase):
    H = load(TOOLS / "kaggle_hourly.py", "test_hourly_helpers")

    def test_models_match_by_name_or_family(self):
        allowed = self.H.ALLOWED_MODELS
        for model in ("claude-opus-5-5", "claude-opus-5-5[1m]", "claude-sonnet-5-5", "claude-haiku-4-5-20251001",
                      "aws/anthropic/bedrock-claude-sonnet-5-5"):
            self.assertTrue(self.H.model_ok(model, allowed), model)
        for model in ("claude-fable-5-1", "claude-opus-5", "claude-sonnet-5", "azure/openai/gpt-6.1-sol", "None"):
            self.assertFalse(self.H.model_ok(model, allowed), model)

    def test_jump_arguments(self):
        self.assertEqual(self.H.jump_arg("2%"), ("%", 2.0))
        self.assertEqual(self.H.jump_arg("0.005"), ("abs", 0.005))
        for text in ("0", "-1%", "x", "%", "inf"):
            with self.assertRaises(Exception):
                self.H.jump_arg(text)

    def test_the_brev_ledger(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "brev-costs.md"
            path.write_text(BREV_HEAD + BREV_TOTAL)
            self.assertEqual(self.H.read_brev(path), ([], [], 0))
            self.assertEqual(self.H.read_brev(Path(d) / "missing.md"), (None, [], 0))
        P = load(TOOLS / "kaggle_presubmit.py", "test_hourly_helpers_p")
        t0 = P.parse_time("2026-10-07T00:00:00Z")
        rows = [(t0 - timedelta(hours=1), t0 + timedelta(hours=1), 10.0), (t0, t0, 2.0)]
        self.assertEqual(self.H.brev_spent(rows, [], t0, t0 + timedelta(hours=12)), (7.0, 0))
        self.assertEqual(self.H.brev_spent(rows, [], t0, t0 + timedelta(minutes=30)), (4.5, 0))
        # running and stopped instances bill their rate inside the window; one without a start is not counted, unless
        # it stopped before the window
        running = [(t0 + timedelta(hours=2), None, 6.0), (t0 - timedelta(hours=2), t0 + timedelta(hours=1), 4.0),
                   (None, t0 - timedelta(hours=1), 3.0), (None, None, 9.0)]
        total, lost = self.H.brev_spent(rows, running, t0, t0 + timedelta(hours=5))
        self.assertEqual((round(total, 6), lost), (7.0 + 18.0 + 4.0, 1))

    def test_the_running_notes_of_the_brev_total_row(self):
        P = load(TOOLS / "kaggle_presubmit.py", "test_hourly_helpers_q")

        def t(text):
            return P.parse_time(text)

        landed = t("2026-10-07T05:00:00Z")
        cell = ("3 closed instances. a (8x H100, $43.20/h) stopped 2026-10-06T16:18:22Z and deleted 2026-10-06T20:00Z "
                "(see its row). b ($4.79/h) running since 2026-10-07T08:00:00Z; c (1x L4, $2/hour) created "
                "2026-10-07T06:00Z, stopped 2026-10-07T07:30Z, waits for a rerun; d ($5/h) running since "
                "2026-10-07T05:00:00Z, its row landed; e ($9/h) running since this morning; f ($1 per hour) stopped")
        self.assertEqual(self.H.open_notes(cell, [(landed, t("2026-10-07T09:00:00Z"), 20.0)]),
                         [(t("2026-10-07T08:00:00Z"), None, 4.79),
                          (t("2026-10-07T06:00:00Z"), t("2026-10-07T07:30:00Z"), 2.0),
                          (None, None, 9.0), (None, None, 1.0)])
        self.assertEqual(self.H.open_notes("0 closed instances; note any still-running ones here", []), [])


if __name__ == "__main__":
    unittest.main()
