"""Offline tests for shared/tools/kaggle_scout.py (stdlib unittest; a stand-in gateway, no network).

    python3 -m unittest discover -s plugins/kaggle/tests -p 'test_kaggle_scout.py'

Every read goes to the stand-in gateway (kaggle_standin), which answers from a table: nothing reaches Kaggle.
"""

import json
import subprocess
import sys
import unittest
from datetime import timedelta
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
from kaggle_standin import TOOLS, Project, utc  # noqa: E402

GROUPS = ("general", "entered")
SORTS = ("latestDeadline", "recentlyCreated", "prize", "earliestDeadline")
NOTICE = ("Warning: Looks like you're using an outdated `kaggle` version (installed: 2.2.4), please consider "
          "upgrading to the latest version (2.2.5)\n")
URL = "https://www.kaggle.com/competitions/"


def key(group, sort):
    return f"competitions list --group {group} --sort-by {sort} --page-size 200 --format json"


def comp(slug, days, category="Featured", reward="50,000 Usd", teams=100, entered=False, **more):
    # a deadline some hours past the whole day, as Kaggle's 23:59 deadlines are
    deadline = (utc() + timedelta(days=days, hours=6)).strftime("%Y-%m-%dT%H:%M:%S")
    return {"ref": URL + slug, "deadline": deadline, "category": category, "reward": reward, "teamCount": teams,
            "userHasEntered": entered, "userRank": 0, **more}


class Scout(Project):
    def setUp(self):
        super().setUp()
        for g in GROUPS:
            for s in SORTS:
                self.listed(g, s, [])

    def listed(self, group, sort, rows, notice=""):
        self.answer(key(group, sort), notice + json.dumps(rows, indent=1))

    def scout(self, *args, cwd=None, tool=None):
        p = subprocess.run([sys.executable, str(tool or self.tools / "kaggle_scout.py"), *args, "--gateway",
                            str(self.gateway)], cwd=cwd or self.root, capture_output=True, text=True, env=self.env,
                           timeout=120)
        return p.returncode, p.stdout, p.stderr


class ListTests(Scout):
    def test_open_competitions_are_merged_from_every_read_and_sorted_by_days_left(self):
        self.listed("general", "latestDeadline", [comp("far-ahead", 240, "Playground", teams=2312),
                                                   comp("santa", 69, teams="1,946"), comp("closed", -3)], NOTICE)
        self.listed("general", "prize", [comp("santa", 69, teams=1946), comp("big-prize", 46, reward="400,000 Usd")])
        self.listed("general", "recentlyCreated", [comp("new-one", 10, "Playground", "Swag", teams="518")])
        self.listed("entered", "latestDeadline", [comp("ours", 12, entered="True"), comp("santa", 69),
                                                  comp("old-entry", -400, entered=True)])
        code, out, err = self.scout()
        self.assertEqual(code, 0, err)
        lines = out.splitlines()
        self.assertRegex(lines[0], r"^Open competitions: 5 \(8 list reads, read 20\d\d-\d\d-\d\dT\d\d:\d\d:\d\dZ\)$")
        self.assertEqual(lines[1].split(), ["ref", "days", "category", "reward", "teams", "entered"])
        rows = [ln.split() for ln in lines[2:]]
        self.assertEqual([r[0] for r in rows], ["new-one", "ours", "big-prize", "santa", "far-ahead"])
        self.assertEqual(rows[0], ["new-one", "10", "Playground", "Swag", "518", "no"])
        # entered: Kaggle's own mark, or a place in the entered group's list
        self.assertEqual(rows[1], ["ours", "12", "Featured", "50,000", "Usd", "100", "yes"])
        self.assertEqual(rows[3], ["santa", "69", "Featured", "50,000", "Usd", "1946", "yes"])
        self.assertEqual(rows[4][-1], "no")
        self.assertNotIn("closed", out)
        self.assertNotIn("old-entry", out)
        # eight read-only reads, none of them stamped as activity
        calls = self.calls()
        self.assertEqual(sorted(" ".join(c["args"]) for c in calls), sorted(key(g, s) for g in GROUPS for s in SORTS))
        self.assertEqual({c["quiet"] for c in calls}, {"1"})

    def test_min_days_drops_the_ones_that_end_sooner(self):
        self.listed("general", "latestDeadline", [comp("soon", 3), comp("later", 30), comp("edge", 14)])
        code, out, _err = self.scout("--min-days", "14")
        self.assertEqual(code, 0)
        self.assertIn("Open competitions with at least 14 days left: 2 ", out)
        self.assertEqual([ln.split()[0] for ln in out.splitlines()[2:]], ["edge", "later"])

    def test_the_json_document(self):
        self.listed("general", "latestDeadline", [comp("santa", 69, teams=1946)])
        code, out, _err = self.scout("--json")
        self.assertEqual(code, 0)
        doc = json.loads(out)
        self.assertEqual(sorted(doc), ["competitions", "details", "failed", "fetched_at", "reads"])
        self.assertEqual((doc["reads"], doc["failed"], doc["details"]), (8, [], {}))
        c = doc["competitions"][0]
        self.assertEqual({k: c[k] for k in ("ref", "url", "days_left", "category", "reward", "teams", "entered")},
                         {"ref": "santa", "url": URL + "santa", "days_left": 69, "category": "Featured",
                          "reward": "50,000 Usd", "teams": 1946, "entered": False})
        self.assertRegex(c["deadline"], r"^20\d\d-\d\d-\d\dT\d\d:\d\d:\d\dZ$")

    def test_a_failed_read_is_noted_and_the_rest_still_lists(self):
        self.listed("general", "latestDeadline", [comp("santa", 69)])
        self.answer(key("general", "prize"), code=1, err="429 Client Error: Too Many Requests\n")
        self.answer(key("entered", "prize"), "not a list")
        code, out, _err = self.scout()
        self.assertEqual(code, 0)
        self.assertIn("note: a list read failed (general/prize: exit 1: 429 Client Error: Too Many Requests)", out)
        self.assertIn("note: a list read failed (entered/prize: the read gave no JSON list)", out)
        self.assertEqual(out.splitlines()[2].split()[0], "santa")
        self.assertEqual(json.loads(self.scout("--json")[1])["failed"][0][:14], "general/prize:")

    def test_no_read_that_worked_exits_1(self):
        for g in GROUPS:
            for s in SORTS:
                self.answer(key(g, s), code=1, err="401 Unauthorized\n")
        code, out, err = self.scout()
        self.assertEqual(code, 1)
        self.assertIn("Open competitions: 0 ", out)
        self.assertIn("kaggle_scout: no read worked (general/latestDeadline: exit 1: 401 Unauthorized)", err)

    def test_kaggles_time_and_value_forms(self):
        m = self.module("kaggle_scout")
        for text in ("2027-01-14T23:59:00.817000", "2027-01-14 23:59:00", "2027-01-14T23:59:00Z"):
            self.assertEqual(m.parse_time(text).strftime("%Y-%m-%d %H:%M %z"), "2027-01-14 23:59 +0000", text)
        self.assertIsNone(m.parse_time("soon"))
        self.assertEqual([m.truthy(v) for v in (True, "True", "true", False, "False", None, 1)],
                         [True, True, True, False, False, False, False])
        self.assertEqual([m.count(v) for v in (518, "518", "1,946", None, "many")], [518, 518, 1946, None, None])
        self.assertEqual(m.list_rows("No competitions found\n"), [])

    def test_reads_leave_no_stamp_and_save_no_board(self):
        m = self.module("kaggle_scout")
        done = subprocess.CompletedProcess([], 0, "[]", "")
        with mock.patch.object(m.subprocess, "run", return_value=done) as run:
            self.assertEqual(m.call(self.gateway, ["competitions", "list"]), ("[]", None))
        env = run.call_args.kwargs["env"]
        self.assertEqual((env["KAGGLE_SHARE_QUIET"], env["KAGGLE_LB_RECORD"]), ("1", "0"))


class DetailTests(Scout):
    def setUp(self):
        super().setUp()
        board = "teamId,teamName,submissionDate,score\n" + "".join(
            f"{i},Team {i},2026-11-20 10:00:00,{1 - i / 100:.2f}\n" for i in range(1, 21))
        self.answer("competitions leaderboard santa --show --csv", NOTICE + board)
        self.answer("competitions files santa --csv", "name,size,creationDate\n" + "".join(
            f"part{i}.csv,{i}MB,2026-11-01 00:00:00\n" for i in range(1, 13)))
        self.answer("competitions leaderboard gated --show --csv", "teamId,teamName,submissionDate,score\n")
        self.answer("competitions files gated --csv", code=1, err="403 Client Error: Forbidden for url\n")

    def test_the_top_of_the_board_and_the_files_of_each_named_competition(self):
        self.listed("general", "latestDeadline", [comp("santa", 69)])
        code, out, err = self.scout("--detail", "santa", URL + "gated", "santa")
        self.assertEqual(code, 0, err)
        tail = out[out.index("== santa"):].splitlines()
        self.assertEqual(tail[1], "board (20 rows read): 1. Team 1 0.99; 2. Team 2 0.98; 3. Team 3 0.97; "
                         "4. Team 4 0.96; 5. Team 5 0.95")
        self.assertEqual(tail[2], "files (12): " + "; ".join(f"part{i}.csv ({i}MB)" for i in range(1, 11))
                         + "; and 2 more")
        self.assertEqual(tail[3:], ["== gated", "board (0 rows read): no team yet",
                                    "files: not readable (exit 1: 403 Client Error: Forbidden for url)"])
        # the list reads, then two reads per competition named, each once
        self.assertEqual(len(self.calls()), 8 + 4)

    def test_no_list_reads_only_the_details(self):
        code, out, _err = self.scout("--detail", "santa", "--no-list", "--json")
        self.assertEqual(code, 0)
        doc = json.loads(out)
        self.assertEqual((doc["reads"], doc["competitions"]), (2, []))
        self.assertEqual(doc["details"]["santa"]["board"]["rows"], 20)
        self.assertEqual(doc["details"]["santa"]["board"]["top"][0], {"rank": 1, "team": "Team 1", "score": "0.99"})
        self.assertEqual(doc["details"]["santa"]["files"]["count"], 12)
        self.assertEqual([c["args"][:2] for c in self.calls()], [["competitions", "leaderboard"],
                                                               ["competitions", "files"]])
        self.assertNotIn("Open competitions", self.scout("--detail", "santa", "--no-list")[1])

    def test_details_that_cannot_be_read_exit_1_when_nothing_else_worked(self):
        code, out, err = self.scout("--detail", "unknown", "--no-list")
        self.assertEqual(code, 1)
        self.assertIn("board: not readable (exit 2: stand-in gateway: no answer for", out)
        self.assertIn("kaggle_scout: no read worked", err)


class PlaceTests(Scout):
    def test_it_runs_outside_a_project_as_the_gateway_does(self):
        self.listed("general", "latestDeadline", [comp("santa", 69)])
        task = self.tmp / "task"
        task.mkdir()
        (task / "notes.md").write_text("# Notes\n\nStarted with the ad-hoc-task skill.\n")
        for cwd in (self.tmp, task):
            code, out, err = self.scout(cwd=cwd, tool=TOOLS / "kaggle_scout.py")
            self.assertEqual(code, 0, err)
            self.assertIn("santa", out)
        # it wrote nothing in either place
        self.assertEqual(sorted(p.name for p in task.iterdir()), ["notes.md"])
        self.assertFalse((self.root / "__data").exists())

    def test_bad_usage_exits_2(self):
        for args in (["--detail", "a b"], ["--no-list"], ["--min-days", "-1"], ["--min-days", "x"], ["extra"]):
            with self.subTest(args):
                self.assertEqual(self.scout(*args)[0], 2)
        p = subprocess.run([sys.executable, str(self.tools / "kaggle_scout.py"), "--gateway", str(self.tmp / "no.py")],
                           capture_output=True, text=True, env=self.env)
        self.assertEqual(p.returncode, 2)
        self.assertIn("gateway not found", p.stderr)
        self.assertEqual(self.calls(), [])


if __name__ == "__main__":
    unittest.main()
