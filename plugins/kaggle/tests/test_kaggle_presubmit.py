"""Offline tests for shared/tools/kaggle_presubmit.py (stdlib unittest; a stand-in gateway, no network).

    python3 -m unittest discover -s plugins/kaggle/tests

The check runs for real, with kaggle_forum.py and kaggle_lb.py reading through the stand-in gateway (kaggle_standin).
"""

import gzip
import json
import subprocess
import sys
import unittest
import zlib
from datetime import timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from kaggle_standin import SLUG, TOOLS, Project, kaggle_time, load  # noqa: E402

P = load(TOOLS / "kaggle_presubmit.py", "test_presubmit_tool")


class Check(Project):
    def check(self, *args):
        return self.tool("kaggle_presubmit.py", SLUG, "--gateway", str(self.gateway), *args)

    def ack(self):
        return self.tool("kaggle_presubmit.py", SLUG, "--ack")

    def reviewed(self):
        """A first check, read and recorded: the baseline the next check compares with."""
        self.assertEqual(self.check()[0], 10)
        self.assertEqual(self.ack()[0], 0)

    def config(self, **cfg):
        d = self.store("presubmit")
        d.mkdir(parents=True, exist_ok=True)
        (d / "config.json").write_text(json.dumps(cfg))

    def state(self, name):
        return json.loads(self.store("presubmit", name).read_text())


class FirstCheckTests(Check):
    def test_a_first_check_shows_the_last_day_and_needs_review(self):
        self.forum([{"id": 1, "title": "Old topic", "posted": 72, "comments": [("Ann", 70, "old reply"),
                                                                              ("Bob", 2, "fresh reply")]},
                    {"id": 2, "title": "New topic", "posted": 3, "post": "Fresh post"},
                    {"id": 3, "title": "Quiet topic", "posted": 72, "comments": [("Cy", 60, "an old one")]}])
        code, out, err = self.check()
        self.assertEqual(code, 10, err)
        self.assertIn("no review recorded yet", out)
        self.assertIn('NEW 2 "New topic" by Someone', out)
        self.assertIn("Fresh post", out)
        self.assertIn('+1 1 "Old topic": Bob', out)
        self.assertNotIn("old reply", out)
        self.assertNotIn("Quiet topic", out)
        self.assertIn("(no review yet: the last 24 h)", out)
        self.assertIn("[no review yet] forum; pages; notebooks; board", out)
        self.assertTrue(self.state("seen.json")["changes"])
        self.assertFalse(self.store("presubmit", "acked.json").exists())
        # monitoring reads only: every call unstamped, none a write
        self.assertTrue(all(c["quiet"] == "1" for c in self.calls()))
        writes = (["competitions", "submit"], ["kernels", "push"])
        self.assertFalse([c for c in self.calls() if c["args"][:2] in writes])

    def test_ack_records_the_check_and_a_check_with_nothing_new_exits_0(self):
        self.forum([{"id": 1, "title": "Topic", "posted": 2, "comments": [("Ann", 1, "a reply")]}])
        self.reviewed()
        acked, seen = self.state("acked.json"), self.state("seen.json")
        self.assertEqual((acked["seen_at"], acked["seen_id"]), (seen["at"], seen["id"]))
        code, out, _ = self.check()
        self.assertEqual(code, 0, out)
        self.assertIn("TRIGGERS: none of 10 matched", out)
        self.assertIn("FORUM: 0 new, 0 with new comments", out)
        self.assertEqual(self.ack()[1], f"review recorded: the check of {P.local(self.state('seen.json')['at'])}, in "
                                        f"__data/kaggle/{SLUG}/presubmit/acked.json\n")
        self.assertEqual(self.ack()[1], "the last check is recorded as reviewed already\n")

    def test_an_ack_needs_a_check(self):
        code, _, err = self.ack()
        self.assertEqual(code, 1)
        self.assertIn("no check to record", err)


class ChangeTests(Check):
    def test_what_changed_on_each_source_with_hosts_first(self):
        self.forum([{"id": 1, "title": "Welcome", "posted": 72, "author": "Host Hana"}])
        self.reviewed()
        self.config(host_topics=[1])
        self.forum([{"id": 1, "title": "Welcome", "posted": 72, "author": "Host Hana",
                     "comments": [("Host Hana", 0.5, "We fixed the metric. See <a href=\"https://x.org/n\">notes</a>.")]},
                    {"id": 2, "title": "My run", "posted": 1, "author": "Pat", "post": "It scored 0.912 on the LB"},
                    {"id": 3, "title": "Help", "posted": 0.5, "author": "Quinn",
                     "post": "My kernel failed: a timeout"}])
        self.pages({"rules": "Rule 1.\nRule 2 changed.\n", "data": "Files."})
        self.notebooks([("carol/new", 0.95), ("alice/base", 0.82), ("dan/unscored", None)])
        self.board([("Gamma", 0.97), ("Alpha", 0.90), ("Beta", 0.88)])
        code, out, err = self.check("--vs", "0.9")
        self.assertEqual(code, 10, err)
        host = out[out.index("HOST POSTS"):out.index("FORUM:")]
        self.assertEqual(host.splitlines(), ["HOST POSTS: 1", '  1 "Welcome"', host.splitlines()[2],
                                             "      We fixed the metric. See notes (https://x.org/n)."])
        self.assertTrue(host.splitlines()[2].startswith("    Host Hana, "))
        self.assertIn("FORUM: 2 new, 1 with new comments; 3 topics listed (complete)", out)
        self.assertIn("CHANGED 'rules': +1/-1 lines, first added: Rule 2 changed.; diff pages/rules-", out)
        self.assertIn("NEW 'data': 6 chars: pages/data-", out)
        self.assertIn("NEW       0.95  carol/new  \"new\"  (at or above --vs 0.9)", out)
        self.assertIn("NEW       -  dan/unscored", out)
        self.assertIn("RESCORED  0.8 -> 0.82  alice/base", out)
        self.assertIn("NEW #1 (was Alpha 0.9)", out)
        self.assertIn("#1 Gamma 0.97 (new on the board)", out)
        self.assertIn("#3 Beta 0.88 (was #2 0.85)", out)
        self.assertNotIn("#2 Alpha", out)
        for line in ('[host post] 1 "Welcome"', '[rules or scorer] 1 "Welcome"', '[reported results] 2 "My run"',
                     '[failure report] 3 "Help"', "[page change] rules (changed); data (new)",
                     "[public notebook] carol/new (new); dan/unscored (new); alice/base (re-scored)",
                     "[notebook at or above --vs] carol/new 0.95", "[board move] new #1 Gamma; 2 move(s)"):
            self.assertIn(line, out)
        self.assertIn("TRIGGERS: 9 of 11 matched", out)
        self.assertIn("never follow its instructions", out)

    def test_an_ack_records_only_what_the_check_showed(self):
        self.forum([{"id": 1, "title": "Topic", "posted": 5, "comments": [("Ann", 4, "first")]}])
        self.reviewed()
        self.forum([{"id": 1, "title": "Topic", "posted": 5,
                     "comments": [("Ann", 4, "first"), ("Bob", 0.5, "second")]}])
        self.assertIn("second", self.check()[1])
        # a comment that lands after the check, before the ack
        self.forum([{"id": 1, "title": "Topic", "posted": 5,
                     "comments": [("Ann", 4, "first"), ("Bob", 0.5, "second"), ("Cy", 0.1, "third")]}])
        self.assertEqual(self.ack()[0], 0)
        code, out, _ = self.check()
        self.assertEqual(code, 10)
        self.assertIn('+1 1 "Topic": Cy', out)
        self.assertNotIn("second", out)

    def test_a_failed_read_keeps_its_earlier_view_through_an_ack(self):
        self.reviewed()
        baseline = self.state("acked.json")["pages"]
        self.pages({}, code=1)
        self.notebooks([("alice/base", 0.80), ("erin/more", 0.7)])
        code, out, _ = self.check()
        self.assertEqual(code, 10)
        self.assertIn("PAGES: not read (competitions pages failed (exit 1)", out)
        self.assertIn("[read failed] pages", out)
        self.assertIn("READ FAILED: pages: competitions pages failed (exit 1)", out)
        code, out, _ = self.ack()
        self.assertIn("kept the earlier view of pages (not read in that check)", out)
        acked = self.state("acked.json")
        self.assertEqual(acked["pages"], baseline)
        self.assertEqual([n["ref"] for n in acked["notebooks"]["notebooks"]], ["alice/base", "erin/more"])
        self.pages({"rules": "Rule 1.\nRule 2.\nRule 3.\n"})
        self.assertIn("CHANGED 'rules': +1/-0 lines, first added: Rule 3.", self.check()[1])

    def test_a_topic_from_an_earlier_forum_watch_counts_as_read(self):
        # kaggle_forum 0.3 saved topics without the opening post; one read before is not fetched again
        forum = self.store("forum")
        (forum / "topics").mkdir(parents=True)
        (forum / "state.json").write_text(json.dumps({"5": {"title": "Old", "comments": 1}}))
        (forum / "topics" / "5.json").write_text(json.dumps(
            {"topic": {"id": 5, "title": "Old", "authorName": "Ann", "commentCount": 1},
             "comments": [{"authorName": "Bob", "postDate": kaggle_time(80), "content": "<p>c</p>"}]}))
        self.forum([{"id": 5, "title": "Old", "posted": 90, "listed": 1, "unfetchable": True}])
        self.reviewed()
        code, out, _ = self.check()
        self.assertEqual(code, 0, out)
        self.assertNotIn("Old", out)

    def test_a_notebook_listed_unscored_triggers_once_it_has_a_score(self):
        self.notebooks([("alice/base", 0.80), ("dan/late", None)])
        self.reviewed()
        self.notebooks([("alice/base", 0.80), ("dan/late", 0.91)])
        code, out, _ = self.check("--vs", "0.9")
        self.assertEqual(code, 10, out)
        self.assertIn("RESCORED  - -> 0.91  dan/late", out)
        self.assertIn("[notebook at or above --vs] dan/late 0.91", out)

    def test_comments_listed_but_not_fetched_yet_show(self):
        self.forum([{"id": 1, "title": "Topic", "posted": 5, "comments": [("Ann", 4, "first")]}])
        self.reviewed()
        self.forum([{"id": 1, "title": "Topic", "posted": 5, "listed": 3, "unfetchable": True}])
        code, out, _ = self.check()
        self.assertEqual(code, 10)
        self.assertIn('+0 (+2 not fetched yet) 1 "Topic"', out)
        self.assertIn("note: some topics could not be fetched (kaggle_forum.py exit 1)", out)


class HostBadgeTests(Check):
    def badge(self, tid, hosts):
        """The forum's host badge on topic tid's read (the SDK read's host flag): true for the authors in hosts."""
        key = f"--sdk topic {tid}"
        doc = json.loads(self.table[key]["out"])
        todo = [doc["topic"], *doc["comments"]]
        while todo:
            m = todo.pop()
            m["host"] = m["authorName"] in hosts
            todo += m.get("replies") or []
        self.answer(key, json.dumps(doc))

    def test_the_badge_is_trusted_and_a_badged_author_missing_from_the_config_is_named(self):
        self.forum([{"id": 1, "title": "Welcome", "posted": 72, "author": "Host Hana"}])
        self.reviewed()
        self.config(hosts=["host hana"])
        self.forum([{"id": 1, "title": "Welcome", "posted": 72, "author": "Host Hana",
                     "comments": [("Nia Newhost", 0.5, "Pretrained weights are allowed.")]},
                    {"id": 2, "title": "Data question", "posted": 1, "author": "Pat", "post": "Which files?",
                     "comments": [("Nia Newhost", 0.4, "Use train.csv only."), ("Pat", 0.3, "Thanks")]},
                    {"id": 3, "title": "Host notes", "posted": 0.5, "author": "Odo Host", "post": "Notes."},
                    {"id": 4, "title": "Chat", "posted": 0.2, "author": "Quinn", "post": "Hi."}])
        for tid in (1, 2, 3, 4):
            self.badge(tid, {"Host Hana", "Nia Newhost", "Odo Host"})
        code, out, err = self.check()
        self.assertEqual(code, 10, err)
        host = out[out.index("HOST POSTS"):out.index("FORUM:")].splitlines()
        self.assertEqual(host[:2], ["HOST POSTS: 3", '  WARNING: the forum badges these authors as hosts, and '
                                    'config.json "hosts" does not name them (add them): Nia Newhost, Odo Host'])
        for text in ("Pretrained weights are allowed.", "Use train.csv only.", "Notes."):
            self.assertIn(f"      {text}", host)
        self.assertIn("    new topic by Odo Host, ", "\n".join(host))
        self.assertNotIn("Thanks", "\n".join(host))
        self.assertNotIn("Chat", "\n".join(host))
        (hits,) = [x for x in out.splitlines() if x.startswith("  [host post] ")]
        self.assertEqual(sorted(hits[len("  [host post] "):].split("; ")),
                         ['1 "Welcome"', '2 "Data question"', '3 "Host notes"'])
        # once the config names them, no warning
        self.config(hosts=["Host Hana", "Nia Newhost", "Odo Host"])
        self.assertNotIn("WARNING", self.check()[1])

    def test_badges_tell_host_posts_apart_without_a_config(self):
        self.forum([{"id": 4, "title": "Ruling", "posted": 2, "author": "Hana", "post": "Rule change."},
                    {"id": 5, "title": "Question", "posted": 2, "author": "Pat", "post": "Why?"}])
        # a read without the badge flag (an SDK without the author type): host posts are not told apart
        out = self.check()[1]
        self.assertIn('HOST POSTS: no hosts named (config.json "hosts", "host_topics")', out)
        # no post carries an author type: the mark is unknown, which is not "no host posted"
        self.assertIn('HOST POSTS: no hosts named (config.json "hosts", "host_topics") and host mark: unknown (the '
                      "forum read gives no author type): host posts are not told apart. Judge them by content\n", out)
        self.assertNotIn("[host post]", out)
        # the topics are still pending, so the next check reads them again, now with the badge
        for tid in (4, 5):
            self.badge(tid, {"Hana"})
        out = self.check()[1]
        self.assertIn("HOST POSTS: 1\n", out)
        self.assertIn("(add them): Hana\n", out)
        self.assertNotIn("no hosts named", out)
        self.assertIn('[host post] 4 "Ruling"\n', out)


    def host_line(self, out):
        (line,) = [x for x in out.splitlines() if x.startswith("HOST POSTS")]
        return line

    def test_without_an_author_type_the_mark_is_unknown_never_none(self):
        self.forum([{"id": 1, "title": "Welcome", "posted": 72, "author": "Pat"}])
        self.reviewed()
        self.config(hosts=["Host Hana"])
        topics = [{"id": 1, "title": "Welcome", "posted": 72, "author": "Pat",
                   "comments": [("Quinn", 0.5, "Is this allowed?")]}]
        self.forum(topics)
        # hosts are named, none of them posted, and the read gives no author type: not "none new"
        code, out, err = self.check()
        self.assertEqual(code, 10, err)
        self.assertEqual(self.host_line(out), "HOST POSTS: host mark: unknown (the forum read gives no author type); "
                                              "none new by the hosts config.json names. A host it does not name is "
                                              "not told apart: judge by content")
        self.assertNotIn("HOST POSTS: none new", out)
        mod = self.module("kaggle_presubmit")
        self.assertEqual(mod.check(SLUG, self.root, gateway=self.gateway, save=False)["host_mark"], "unknown")
        # a named host found by name still counts, mark or no mark
        topics[0]["comments"].append(("Host Hana", 0.2, "Yes."))
        self.forum(topics)
        self.assertEqual(self.host_line(self.check()[1]), "HOST POSTS: 1")
        # once the read carries author types, a count of none is a real none
        self.forum([dict(topics[0], comments=topics[0]["comments"][:1])])
        self.badge(1, set())
        code, out, _ = self.check()
        self.assertEqual(self.host_line(out), "HOST POSTS: none new")
        self.assertEqual(mod.check(SLUG, self.root, gateway=self.gateway, save=False)["host_mark"], "known")

    def test_a_forum_without_posts_has_no_mark_to_miss(self):
        mod = self.module("kaggle_presubmit")
        res = mod.check(SLUG, self.root, gateway=self.gateway, save=False)
        self.assertIsNone(res["host_mark"])
        self.assertEqual(self.host_line("\n".join(mod.report(res, SLUG, res["cfg"]))),
                         'HOST POSTS: no hosts named (config.json "hosts", "host_topics"): host posts are not told '
                         "apart")


class BoardWalkTests(Check):
    KEY = f"competitions leaderboard {SLUG} --show --format json --page-size 200"

    def rows(self, teams):
        return json.dumps([{"teamId": zlib.crc32(name.encode()) % 10 ** 6 + 1, "teamName": name,
                            "submissionDate": "2026-09-30T00:00:00", "score": str(score)} for name, score in teams])

    def two_pages(self, first, second):
        """A board the CLI serves on two pages: the first names the token of the second."""
        self.answer(self.KEY, "Next Page Token = T1\n" + self.rows(first))
        self.answer(self.KEY + " --page-token T1", self.rows(second))

    def board_line(self, out):
        lines = out.splitlines()
        i = next(n for n, x in enumerate(lines) if x.startswith("BOARD"))
        return lines[i], lines[i + 1] if lines[i + 1].startswith("  note:") else None

    def test_max_pages_is_passed_on_to_the_board_walk(self):
        self.two_pages([("Alpha", 0.90), ("Beta", 0.85)], [("Gamma", 0.80), ("Delta", 0.75)])
        code, out, err = self.check()
        self.assertEqual(code, 10, err)
        self.assertTrue(self.board_line(out)[0].startswith("BOARD: 4 teams, "), out)
        whole = sorted(p.name for p in self.store("leaderboard").glob("*.json.gz"))
        # the walk stops after one page: the read is partial, and the check goes by the whole board before it
        code, out, err = self.check("--max-pages", "1")
        line, note = self.board_line(out)
        self.assertTrue(line.startswith("BOARD: 4 teams, "), out)
        self.assertEqual(note, f"  note: this read was partial: the newest full snapshot is {whole[-1]}")
        calls = [" ".join(c["args"]) for c in self.calls() if c["args"][:2] == ["competitions", "leaderboard"]]
        self.assertEqual(calls, [self.KEY, self.KEY + " --page-token T1", self.KEY])
        partial = [p.name for p in self.store("leaderboard").glob("*partial*")]
        self.assertEqual(len(partial), 1)
        for bad in (["--max-pages", "0"], ["--max-pages", "x"], ["--ack", "--max-pages", "3"]):
            self.assertEqual(self.tool("kaggle_presubmit.py", SLUG, *bad)[0], 2, bad)

    def test_an_unreliable_read_is_passed_by_for_the_newest_reliable_snapshot(self):
        # Kaggle serves the first page again as the second: a whole read, with duplicates, so its ranks are off
        garbled = [("Gamma", 0.97), ("Alpha", 0.90)]
        self.two_pages(garbled, garbled)
        code, out, err = self.check()
        self.assertEqual(code, 10, err)
        self.assertEqual(self.board_line(out)[0],
                         "BOARD: not read (no full snapshot to compare: the read was unreliable)")
        self.assertIn("[read failed] board", out)
        # a clean read, reviewed
        self.board([("Alpha", 0.90), ("Beta", 0.85)])
        self.reviewed()
        clean = sorted(p.name for p in self.store("leaderboard").glob("*.json.gz") if "unreliable" not in p.name)
        # the next garbled read changes nothing: the board is the reliable one, and the check says why
        self.two_pages(garbled, garbled)
        code, out, err = self.check()
        line, note = self.board_line(out)
        self.assertIn("#1 Alpha 0.9", line)
        self.assertEqual(note, "  note: this read was unreliable (its pages repeated teams, so its ranks cannot be "
                               f"trusted): the newest reliable snapshot is {clean[-1]}")
        self.assertNotIn("[board move]", out)
        self.assertNotIn("Gamma", out)

    def test_a_snapshot_saved_before_the_flag_is_unreliable_by_its_duplicates(self):
        d = self.store("leaderboard")
        d.mkdir(parents=True)
        rows = [{"rank": 1, "team_id": 1, "team_name": "Alpha", "score": "0.9"}]
        for name, extra in (("20260928T100000Z.json.gz", {}), ("20260928T110000Z.json.gz", {"duplicates": 3}),
                            ("20260928T120000Z.json.gz", {"unreliable": True})):
            with gzip.open(d / name, "wt", encoding="utf-8") as f:
                json.dump({"fetched_at": "2026-09-28T10:00:00Z", "partial": False, "rows": rows, **extra}, f)
        self.assertEqual([bool(P.board_view(d, n)) for n in sorted(p.name for p in d.iterdir())],
                         [True, False, False])
        self.assertEqual([P.unreliable(x) for x in ({}, {"duplicates": None}, {"unreliable": False, "duplicates": 0},
                                                    {"duplicates": 1}, {"unreliable": True})],
                         [False, False, False, True, True])


class RateLimitTests(Check):
    def test_a_429_stops_the_reads_after_it(self):
        self.reviewed()
        baseline = self.state("acked.json")
        self.answer(f"competitions pages {SLUG} list {SLUG} --content --format json",
                    "429 Client Error: Too Many Requests for url: https://www.kaggle.com/api/v1/pages\n", code=1)
        before = len(self.calls())
        code, out, _ = self.check()
        self.assertEqual(code, 10)
        self.assertIn("RATE LIMITED: Kaggle answered HTTP 429 (Too Many Requests) to the pages read; not read after "
                      "it: notebooks, board. Wait a few minutes before the next Kaggle read, then run the check again",
                      out)
        calls = [" ".join(c["args"]) for c in self.calls()[before:]]
        self.assertFalse([c for c in calls if c.startswith(("--sdk notebooks", "competitions leaderboard"))], calls)
        self.assertIn("[read failed] pages; notebooks; board", out)
        self.assertIn("NOTEBOOKS: not read (kaggle_lb.py notebooks failed (exit 75): not read: Kaggle answered HTTP "
                      "429 (Too Many Requests) to the pages read)", out)
        # what was not read keeps the view the last review recorded
        self.assertIn("kept the earlier view of pages, notebooks, board", self.ack()[1])
        acked = self.state("acked.json")
        self.assertEqual([acked[k] for k in ("pages", "notebooks", "board")],
                         [baseline[k] for k in ("pages", "notebooks", "board")])


class ReviewStatusTests(Check):
    def test_the_gate_kaggle_submit_applies(self):
        why, summary = P.review_status(SLUG, self.root)
        self.assertEqual((why.split(":")[0], summary), ("no pre-submit check yet", "no check yet"))
        self.check()
        self.assertIn("no review recorded yet", P.review_status(SLUG, self.root)[0])
        self.ack()
        self.assertEqual(P.review_status(SLUG, self.root)[0], None)
        late = P.parse_time(self.state("seen.json")["at"]) + timedelta(minutes=31)
        self.assertIn("min old (limit 30)", P.review_status(SLUG, self.root, now=late)[0])
        self.assertIsNone(P.review_status(SLUG, self.root, now=late, fresh_minutes=60)[0])
        # a check that found nothing new needs no new ack
        self.assertEqual(self.check()[0], 0)
        self.assertIsNone(P.review_status(SLUG, self.root)[0])
        self.notebooks([("alice/base", 0.80), ("fay/fresh", 0.5)])
        self.assertEqual(self.check()[0], 10)
        self.assertIn("found something new that no review recorded", P.review_status(SLUG, self.root)[0])


class InputTests(Check):
    def test_bad_arguments_and_config(self):
        for args in (["not a slug"], [SLUG, "--ack", "--vs", "1"], [SLUG, "--hours", "0"], [SLUG, "--vs", "nan"]):
            self.assertEqual(self.tool("kaggle_presubmit.py", *args)[0], 2, args)
        self.config(hosts="Hana")
        code, _, err = self.check()
        self.assertEqual(code, 1)
        self.assertIn('want {"hosts": ["<display name>", ...]', err)

    def test_no_project_here(self):
        p = subprocess.run([sys.executable, str(TOOLS / "kaggle_presubmit.py"), SLUG], cwd=self.tmp,
                           capture_output=True, text=True, env=self.env, timeout=60)
        self.assertEqual((p.returncode, p.stderr), (1, "kaggle_presubmit: no project or task folder here: run from "
                                                       "one\n"))


class HelperTests(unittest.TestCase):
    def test_notebook_changes_count_a_first_score_but_not_a_lost_one(self):
        old = [{"ref": "A/x", "score": 0.5}, {"ref": "b/y", "score": None}, {"ref": "c/z", "score": 0.4}]
        new = [{"ref": "a/x", "score": 0.6}, {"ref": "b/y", "score": 0.3}, {"ref": "c/z", "score": None},
               {"ref": "d/w", "score": None}]
        fresh, rescored = P.notebook_changes(old, new)
        self.assertEqual(([n["ref"] for n in fresh], [(n["ref"], was) for n, was in rescored]),
                         (["d/w"], [("a/x", 0.5), ("b/y", None)]))

    def test_plain_text_from_forum_html(self):
        self.assertEqual(P.plain('<p>One &amp; <a href="https://x.org">two</a></p><ul><li>a</li></ul>\x1b[31m'),
                         "One & two (https://x.org)\na\n[31m")

    def test_lower_is_better_boards(self):
        self.assertEqual(P.direction([{"score": "0.1"}, {"score": "0.5"}]), -1)
        self.assertTrue(P.at_or_better(0.09, 0.1, -1))
        self.assertFalse(P.at_or_better(0.11, 0.1, -1))

    def test_what_reads_as_a_rate_limit(self):
        # explicit wording only
        for code, out, err, own in (
                (1, "", "kaggle_lb: leaderboard read failed: exit 1: 429 - Too Many Requests", False),
                (1, "429 Client Error: Too Many Requests for url: https://x.org/a", "", False),
                (1, "", "kaggle_sdk: 429 Too Many Requests: slow down", False),
                (1, "", "urllib.error.HTTPError: HTTP Error 429: slow down", False),
                (1, "", "the API answered with status code 429", False),
                (1, "", "status: 429", False),
                (1, "", "Rate limit exceeded, retry later", False),
                (0, "", "rate limited: 3 topics left for the next check (1 2 3)", False),
                (0, "demo: 400 rows, 2 pages (partial: page 3 failed (exit 1: 429 - Too Many Requests)), Oct 08",
                 "", True)):
            self.assertTrue(P.rate_limited(code, out, err, own=own), (out, err))
        # a bare 429 is a count, a score, a line number or a path; third-party text of a read that worked is not
        # searched (the first four read as a rate limit before this matcher)
        for code, out, err, own in (
                (0, "my-comp: 429 rows, 3 pages (full), Oct 08 01:30 PDT; saved __data/x.json.gz", "", True),
                (1, "", 'Traceback (most recent call last):\n  File "/x/kaggle_forum.py", line 429, in main\n'
                        "KeyError: 7", False),
                (1, "read 429 topics before the error", "", False),
                (0, "demo: 429 rows, 2 pages (partial: page 3 failed (exit 1: bad token at line 429)), Oct 08",
                 "", True),
                (1, "", "saved to /data/run-429/x.json.gz; best 0.429; 4290 rows; HTTP 4290", False),
                (0, '[{"title": "Too Many Requests when submitting? HTTP 429"}]', "", False),
                (0, "demo: 400 rows (partial: page 3 failed (exit 1: 429))", "", False),
                (1, "", "an accurate limit of 429", False)):
            self.assertFalse(P.rate_limited(code, out, err, own=own), (out, err))

    def test_a_limiter_runs_nothing_after_a_429(self):
        ran = []

        def run(cmd, cwd):
            ran.append(cmd[0])
            return (1, "", "HTTP 429") if cmd[0] == "b" else (0, "ok", "")

        reads = P.Limiter(run)
        for check in ("a", "b", "c", "d"):
            reads.check = check
            code, _out, err = reads([check], ".")
        self.assertEqual((ran, reads.hit, reads.skipped), (["a", "b"], "b", ["c", "d"]))
        self.assertEqual((code, err),
                         (P.SKIPPED, "not read: Kaggle answered HTTP 429 (Too Many Requests) to the b read"))


if __name__ == "__main__":
    unittest.main()
