"""Offline tests for shared/tools/kaggle_forum.py (stdlib unittest; a fake gateway and fake listings, no network).

    python3 -m unittest discover -s plugins/kaggle/tests
"""

import contextlib
import io
import json
import os
import shutil
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path
from unittest import mock

TOOLS = Path(__file__).resolve().parents[1] / "shared" / "tools"
sys.path.insert(0, str(TOOLS))
import kaggle_forum as F  # noqa: E402

SLUG = "demo-competition"
# what makes a folder an ai-pack, whatever its name: a manifest with framework_version and a project object
PACK_MANIFEST = json.dumps({"framework_version": "0.39.0", "project": {"name": "demo"}})


def row(tid, title, comments, date="2026-09-20T10:00:00.100000"):
    # a row of `competitions topics list --format json` (the CLI sends no author there)
    return {"id": tid, "title": title, "authorName": "", "commentCount": comments, "votes": 1, "postDate": date}


def cli_list(rows, next_page=None, notice=None):
    head = [notice] if notice else []
    tail = [f"Next Page Token = {next_page}"] if next_page else []
    return "\n".join(head + [json.dumps(rows, indent=2)] + tail) + "\n"


def comment(cid, author, date, content, replies=()):
    # a comment as the SDK read prints it, its replies nested
    return {"id": cid, "authorName": author, "postDate": date, "votes": 0, "content": content,
            "replies": list(replies)}


def count(comments):
    return sum(1 + count(c.get("replies") or []) for c in comments)


def sdk_topic(tid, title, comments, post="<p>Is X allowed?</p>"):
    """What `kaggle.py --sdk topic <id>` prints: the topic with its opening post, the comments with replies nested."""
    return json.dumps({"topic": {"id": tid, "title": title, "authorName": "Asker",
                                 "postDate": "2026-09-19T06:13:46.728000", "votes": 2,
                                 "commentCount": count(comments), "content": post},
                       "comments": comments}, indent=2) + "\n"


def old_topic_json(tid, title, comments):
    # what an earlier version saved from `forums topics show <id> --format json`: no opening post, comments flat
    return json.dumps({"topic": {"id": tid, "title": title, "authorName": "Asker", "commentCount": len(comments),
                                 "votes": 2, "postDate": "2026-09-19T06:13:46.728000"},
                       "comments": comments}, indent=2) + "\n"


# a thread: C1 <- C2 <- C4, then C3
C4 = comment(14, "Third", "2026-09-22T03:00:00.400000", "<p>Same here.</p>")
C2 = comment(12, "Asker", "2026-09-21T02:00:00.200000",
             '<p>Thanks, see <a href="https://example.org/doc">the doc</a>:</p><ul><li>one</li><li>two</li></ul>', [C4])
C1 = comment(11, "Host Person", "2026-09-20T01:00:00.100000", "<p>Yes, <b>allowed</b> &amp; fine.</p>", [C2])
C3 = comment(13, "Other", "2026-09-19T08:00:00.300000", "<p>Also asking.</p>")
POST = '<p>Is X allowed? See <a href="https://example.org/rules">the rules</a>.</p>'


class FakeGateway:
    """Stands in for `python3 kaggle.py ...`: topic list pages by -p, SDK topic reads by id."""

    def __init__(self, pages=None, topics=None, fail=()):
        self.pages, self.topics, self.fail, self.calls = pages or {}, topics or {}, dict(fail), []

    def __call__(self, cmd, cwd):
        args = cmd[2:]
        self.calls.append(args)
        key = " ".join(args)
        for part, (code, out) in self.fail.items():
            if part in key:
                return code, out
        if args[:3] == ["competitions", "topics", "list"]:
            return 0, self.pages[int(args[args.index("-p") + 1])]
        if args[:2] == ["--sdk", "topic"]:
            return 0, self.topics[int(args[2])]
        return 2, "unexpected command"


class Tmp(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp()).resolve()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.d = self.tmp / "store" / SLUG
        self.gateway = self.tmp / "kaggle.py"
        self.gateway.write_text("# stand-in gateway\n")
        env = mock.patch.dict(os.environ, {}, clear=False)
        env.start()
        self.addCleanup(env.stop)
        os.environ.pop(F.ENV_DIR, None)

    def listing(self, run, **kw):
        return F.take_listing(SLUG, self.d, gateway=self.gateway, root=self.tmp, run=run, pause=0, **kw)

    def browser_page(self, name, page, rows):
        f = self.tmp / name
        url = f"https://www.kaggle.com/competitions/{SLUG}/discussion?sort=recent-comments" + (
            f"&page={page}" if page > 1 else "")
        f.write_text(json.dumps({"url": url, "n": len(rows), "pag": [], "topics": rows}))
        return f

    def state(self, entries):
        self.d.mkdir(parents=True, exist_ok=True)
        (self.d / F.STATE).write_text(json.dumps(entries))


PAGES = {1: cli_list([row(i, f"Topic {i}", i % 3) for i in range(100, 120)], 2,
                     notice="Warning: Looks like you're using an outdated `kaggle` version"),
         2: cli_list([row(i, f"Topic {i}", i % 3) for i in range(120, 140)], 3),
         3: cli_list([row(i, f"Topic {i}", i % 3) for i in range(140, 145)])}


class ParseTests(unittest.TestCase):
    def test_topic_list_after_notices_with_the_next_page(self):
        rows, nxt = F.parse_listing(PAGES[1])
        self.assertEqual((len(rows), nxt), (20, 2))
        self.assertEqual(rows[1], {"id": 101, "title": "Topic 101", "comments": 2, "votes": 1,
                                   "posted": "2026-09-20T10:00:00.100000"})

    def test_empty_forum_and_unreadable_output(self):
        self.assertEqual(F.parse_listing("No topics found\n"), ([], None))
        with self.assertRaises(F.ForumError):
            F.parse_listing("403 - Forbidden\n")

    def test_strip_html_keeps_paragraphs_lists_links_and_entities(self):
        self.assertEqual(F.strip_html(C2["content"]), "Thanks, see the doc <https://example.org/doc>:\n- one\n- two")
        self.assertEqual(F.strip_html('<a href="https://x.org/a">https://x.org/a</a> &lt;b&gt;'),
                         "https://x.org/a <b>")
        self.assertEqual(F.strip_html('<img src="https://x.org/p.png" alt="plot">'), "[image <https://x.org/p.png>]")


class ListTests(Tmp):
    def test_walks_every_page_through_the_gateway(self):
        fake = FakeGateway(PAGES)
        path, listing = self.listing(fake)
        self.assertEqual((listing["topic_count"], listing["pages"], listing["complete"], listing["source"]),
                         (45, 3, True, "cli"))
        self.assertEqual([t["page"] for t in listing["topics"]][19:21], [1, 2])
        for n, args in enumerate(fake.calls, 1):
            self.assertEqual(args, ["competitions", "topics", "list", SLUG, "--sort-by", "recent", "--format", "json",
                                    "-p", str(n)])
        self.assertEqual(F.read_json(path, None), listing)
        self.assertEqual(path.parent, self.d / "listings")

    def test_a_failed_first_page_points_to_the_browser_path_and_saves_nothing(self):
        fake = FakeGateway(PAGES, fail={"-p 1": (1, "403 - Forbidden")})
        with self.assertRaises(F.ForumError) as cm:
            self.listing(fake)
        self.assertIn("--from", str(cm.exception))
        self.assertFalse((self.d / "listings").exists())

    def test_a_failed_later_page_is_saved_partial(self):
        path, listing = self.listing(FakeGateway(PAGES, fail={"-p 2": (1, "429 Too Many Requests")}))
        self.assertEqual((listing["topic_count"], listing["complete"]), (20, False))
        self.assertIn("page 2 failed", listing["note"])
        self.assertIn("-partial", path.name)
        self.assertEqual(self.listing(FakeGateway(PAGES), max_pages=2)[1]["note"], "stopped after 2 pages")

    def test_reads_are_marked_as_monitoring(self):
        # kaggle_share's activity stamp skips calls carrying KAGGLE_SHARE_QUIET=1
        with mock.patch.object(F.subprocess, "run") as run:
            run.return_value = mock.Mock(returncode=0, stdout=PAGES[3], stderr="")
            F._run(["gw"], self.tmp)
        self.assertEqual(run.call_args.kwargs["env"]["KAGGLE_SHARE_QUIET"], "1")


def browser_rows(ids, dated=True):
    return [{"id": i, "pinned": False, "title": f"Topic {i} Some Author", "comments": 1, "votes": 0,
             "when": "Posted 3h ago" if dated else None, "last_by": None} for i in ids]


class BrowserListingTests(Tmp):
    def test_pages_go_in_url_order_and_rows_without_a_date_are_dropped(self):
        featured = browser_rows([205, 206], dated=False)  # the featured strip repeats topics, undated
        p1 = self.browser_page("p1.json", 1, featured + browser_rows(range(200, 220)))
        p2 = self.browser_page("p2.json", 2, browser_rows([219, 220, 221]))  # 219 moved while paging
        _path, listing = self.listing(None, files=[p2, p1], total_pages=2)
        ids = [t["id"] for t in listing["topics"]]
        self.assertEqual(ids, list(range(200, 222)))
        self.assertEqual((listing["complete"], listing["duplicates"], listing["source"]), (True, 1, "browser"))
        self.assertEqual(listing["topics"][0]["when"], "Posted 3h ago")

    def test_a_browser_listing_is_complete_only_with_every_page(self):
        self.state({str(t): {"title": f"Topic {t}", "comments": 1} for t in (300, 320, 345)})
        p1 = self.browser_page("p1.json", 1, browser_rows(range(300, 320)))
        p2 = self.browser_page("p2.json", 2, browser_rows(range(320, 340)))
        p3 = self.browser_page("p3.json", 3, browser_rows([340, 341]))
        # a skipped middle page, even with the page count: partial, and nothing is reported missing
        path, listing = self.listing(None, files=[p1, p3], total_pages=3)
        self.assertEqual((listing["complete"], listing["note"]), (False, "page 2 of 3 not given"))
        self.assertTrue(path.name.endswith("-browser-partial.json"))
        self.assertEqual(F.run_diff(self.d, path)[3], [])
        # no page count: a short last page proves nothing
        self.assertFalse(self.listing(None, files=[p1, p2, p3])[1]["complete"])
        # every page given: only now is the topic absent from all of them missing
        path, listing = self.listing(None, files=[p3, p1, p2], total_pages=3)
        self.assertTrue(listing["complete"])
        self.assertEqual([k for k, _s in F.run_diff(self.d, path)[3]], ["345"])

    def test_the_shipped_extractor_sits_beside_the_tool(self):
        js = (TOOLS / "kaggle_forum_list.js").read_text()
        self.assertTrue(js.startswith("// rev. "))
        for field in ("topics", "comments", "when", "last_by"):
            self.assertIn(field, js)


class DiffTests(Tmp):
    def listed(self, counts, complete=True):
        listing = F.build_listing(SLUG, [[F.normalize(row(t, f"Topic {t}", n)) for t, n in counts.items()]],
                                  source="cli", complete=complete)
        with F.locked(self.d):
            return F.save_listing(self.d, listing)

    def test_new_changed_missing_then_commit_marks_gone_and_back(self):
        self.state({"1": {"title": "Topic 1", "comments": 2}, "2": {"title": "Topic 2", "comments": 1},
                    "3": {"title": "Topic 3", "comments": 0}})
        path = self.listed({1: 2, 2: 3, 4: 0})
        _listing, new, changed, missing = F.run_diff(self.d, path)
        self.assertEqual(([t["id"] for t in new], [t["id"] for t, _s, _w in changed], [k for k, _s in missing]),
                         ([4], [2], ["3"]))
        pending = F.load_pending(self.d)
        self.assertEqual((pending["new"], pending["changed"], pending["missing"]), ([4], [2], [3]))
        (self.d / "topics").mkdir()
        for tid, n in ((2, 3), (4, 0)):
            comments = [comment(i, "A", f"2026-09-2{i}T00:00:00", "x") for i in range(n)]
            (self.d / "topics" / f"{tid}.json").write_text(sdk_topic(tid, f"Topic {tid}", comments))
        # nothing was shown yet, so a plain commit records nothing
        self.assertEqual(F.commit(self.d), ([], [], []))
        text = F.show_topics(self.d, SLUG)
        self.assertIn("MISSING  3  Topic 3", text)
        done, gone, skipped = F.commit(self.d)
        self.assertEqual((done, gone, skipped), ([4, 2], [3], []))
        state = F.load_state(self.d)
        self.assertEqual((state["2"]["comments"], state["2"]["newest_comment"]), (3, "2026-09-22T00:00:00"))
        self.assertIn("gone", state["3"])
        self.assertEqual(F.load_pending(self.d)["missing"], [])
        # a gone topic is not reported again, and comes back as changed when listed again
        self.assertEqual(F.run_diff(self.d, self.listed({1: 2, 2: 3, 4: 0}))[3], [])
        _l, _n, changed, _m = F.run_diff(self.d, self.listed({1: 2, 2: 3, 3: 0, 4: 0}))
        self.assertEqual([(t["id"], why) for t, _s, why in changed], [(3, "back")])

    def test_a_partial_listing_reports_nothing_missing(self):
        self.state({"1": {"comments": 0}, "9": {"comments": 0}})
        self.assertEqual(F.run_diff(self.d, self.listed({1: 0}, complete=False))[3], [])

    def test_state_from_before_this_tool_is_read(self):
        # {comments, last_seen, title}, as an earlier hourly script wrote it
        self.state({"5": {"comments": 1, "last_seen": "2026-09-28T20:45-07:00", "title": "Topic 5"}})
        _l, new, changed, _m = F.run_diff(self.d, self.listed({5: 1}))
        self.assertEqual((new, changed), ([], []))
        (self.d / "topics").mkdir()
        (self.d / "topics" / "5.json").write_text(sdk_topic(5, "Topic 5", [C3, C1]))
        text = F.render_topic(self.d, 5, SLUG, F.load_state(self.d)["5"], new_only=True)
        # without dates in the state, the newest comments beyond the count read then are the new ones
        self.assertIn("3 new since the last read", text)
        self.assertIn("NEW Asker", text)
        self.assertIn("NEW Host Person", text)
        self.assertNotIn("Other", text)


def fake_topics():
    return {7: sdk_topic(7, "A question", [C1, C3], POST), 8: sdk_topic(8, "Quiet", [], "<p>Nothing yet.</p>")}


class FetchShowTests(Tmp):
    def test_fetch_reads_each_topic_once_and_a_failed_read_keeps_the_file(self):
        fake = FakeGateway(topics=fake_topics())
        done, failed, left = F.fetch_topics([7, 8], self.d, self.gateway, self.tmp, run=fake, pause=0)
        self.assertEqual((done, failed, left), ([(7, 4), (8, 0)], [], []))
        self.assertEqual(fake.calls, [["--sdk", "topic", "7"], ["--sdk", "topic", "8"]])
        saved = json.loads((self.d / "topics" / "7.json").read_text())
        self.assertEqual((saved["topic"]["content"], saved["comments"][0]["replies"][0]["id"]), (POST, 12))
        self.assertEqual(sorted(p.name for p in (self.d / "topics").iterdir()), ["7.json", "8.json"])
        before = (self.d / "topics" / "7.json").read_text()
        reads = {"kaggle_sdk: 404 Not Found": FakeGateway(fail={"topic 7": (1, "kaggle_sdk: 404 Not Found: no topic")}),
                 "topic 8, not 7": FakeGateway(topics={7: sdk_topic(8, "Quiet", [])}),
                 "no opening post": FakeGateway(topics={7: old_topic_json(7, "A question", [])})}
        for why, gw in reads.items():
            done, failed, _ = F.fetch_topics([7], self.d, self.gateway, self.tmp, run=gw, pause=0)
            self.assertEqual((done, [t for t, _ in failed]), ([], [7]))
            self.assertIn(why, failed[0][1])
            self.assertEqual((self.d / "topics" / "7.json").read_text(), before)

    def test_a_rate_limit_stops_the_loop(self):
        fake = FakeGateway(topics=fake_topics(), fail={"topic 7": (1, "kaggle_sdk: 429 Too Many Requests: slow down")})
        done, failed, left = F.fetch_topics([7, 8], self.d, self.gateway, self.tmp, run=fake, pause=0)
        self.assertEqual((done, [t for t, _ in failed], left), ([], [7], [8]))
        self.assertEqual(len(fake.calls), 1)

    def test_show_prints_the_opening_post_and_the_reply_tree_with_new_comments_marked(self):
        F.fetch_topics([7, 8], self.d, self.gateway, self.tmp, run=FakeGateway(topics=fake_topics()), pause=0)
        text = F.render_topic(self.d, 7, SLUG)
        self.assertIn("never read before", text)
        self.assertIn(f"https://www.kaggle.com/competitions/{SLUG}/discussion/7", text)
        self.assertIn("\nIs X allowed? See the rules <https://example.org/rules>.\n", text)
        self.assertIn("-- 4 comments --", text)
        lines = text.splitlines()
        tree = ["* Host Person (2026-09-20 01:00) [0]", "  Yes, allowed & fine.", "    * Asker (2026-09-21 02:00) [0]",
                "      Thanks, see the doc <https://example.org/doc>:", "        * Third (2026-09-22 03:00) [0]",
                "          Same here.", "* Other (2026-09-19 08:00) [0]"]
        order = [lines.index(s) for s in tree]
        self.assertEqual(order, sorted(order))
        read = {"comments": 2, "newest_comment": "2026-09-20T01:00:00.100000"}
        text = F.render_topic(self.d, 7, SLUG, read)
        self.assertIn("2 new since the last read", text)
        self.assertIn("\n* Host Person (2026-09-20 01:00) [0]\n", text)
        self.assertIn("\n    * NEW Asker (2026-09-21 02:00) [0]\n", text)
        text = F.render_topic(self.d, 7, SLUG, read, new_only=True)
        self.assertIn("-- 4 comments, 2 new (only the new ones below) --", text)
        self.assertIn("\n    * NEW Asker (2026-09-21 02:00) [0] (reply to Host Person)\n", text)
        self.assertIn("\n        * NEW Third (2026-09-22 03:00) [0] (reply to Asker)\n", text)
        for old in ("Is X allowed?", "* Host Person", "Other"):
            self.assertNotIn(old, text)
        self.assertIn("-- 0 comments --", F.render_topic(self.d, 8, SLUG))

    def test_a_topic_an_earlier_version_saved_is_kept_until_a_fetch_reads_it_again(self):
        # the earlier fetch saved <id>.json without the opening post (comments flat) and the table view in <id>.txt
        flat = [{k: v for k, v in c.items() if k != "replies"} for c in (C1, C2, C4, C3)]
        old_json, old_txt = old_topic_json(7, "A question", flat), "Topic #7: A question\n\nIs X allowed?\n"
        (self.d / "topics").mkdir(parents=True)
        (self.d / "topics" / "7.json").write_text(old_json)
        (self.d / "topics" / "7.txt").write_text(old_txt)
        F.run_diff(self.d, self.listing(FakeGateway(pages={1: cli_list([row(7, "A question", 4)])}))[0])
        self.assertIn("==== 7: fetched by an earlier version (run fetch to read it again)", F.show_topics(self.d, SLUG))
        # nothing of it was shown: nothing is recorded, and it stays pending
        self.assertEqual((F.load_shown(self.d)["topics"], F.commit(self.d)), ({}, ([], [], [])))
        self.assertEqual(F.load_pending(self.d)["new"], [7])
        # commit by id still reads its flat comments
        self.assertEqual(F.read_entry(json.loads(old_json), "now")["newest_comment"], "2026-09-22T03:00:00.400000")
        bad = FakeGateway(fail={"topic 7": (1, "kaggle_sdk: 404 Not Found: no topic")})
        F.fetch_topics([7], self.d, self.gateway, self.tmp, run=bad, pause=0)
        self.assertEqual([(self.d / "topics" / f).read_text() for f in ("7.json", "7.txt")], [old_json, old_txt])
        # the next check fetches the pending topic again; the .txt is left as it was
        F.fetch_topics(F.load_pending(self.d)["new"], self.d, self.gateway, self.tmp,
                       run=FakeGateway(topics=fake_topics()), pause=0)
        text = F.show_topics(self.d, SLUG)
        self.assertIn("\nIs X allowed? See the rules <https://example.org/rules>.\n", text)
        self.assertIn("\n        * Third (2026-09-22 03:00) [0]\n", text)
        self.assertEqual(F.commit(self.d)[0], [7])
        self.assertEqual((self.d / "topics" / "7.txt").read_text(), old_txt)

    def test_a_check_between_show_and_commit_adds_nothing_unseen(self):
        # the agent shows topic 101 with one comment; a later check then lists 202 and a second comment on 101
        c1 = comment(1, "First", "2026-09-20T01:00:00.100000", "<p>first</p>")
        c2 = comment(2, "Second", "2026-09-21T01:00:00.100000", "<p>second</p>")
        before = FakeGateway(pages={1: cli_list([row(101, "Topic 101", 1)])},
                             topics={101: sdk_topic(101, "Topic 101", [c1])})
        F.run_diff(self.d, self.listing(before)[0])
        F.fetch_topics(F.load_pending(self.d)["new"], self.d, self.gateway, self.tmp, run=before, pause=0)
        self.assertIn("==== 101", F.show_topics(self.d, SLUG))
        after = FakeGateway(pages={1: cli_list([row(202, "Topic 202", 0), row(101, "Topic 101", 2)])},
                            topics={101: sdk_topic(101, "Topic 101", [c1, c2]), 202: sdk_topic(202, "Topic 202", [])})
        F.run_diff(self.d, self.listing(after)[0])
        self.assertEqual(F.load_pending(self.d)["new"], [202, 101])
        F.fetch_topics([202, 101], self.d, self.gateway, self.tmp, run=after, pause=0)
        done, gone, skipped = F.commit(self.d)
        self.assertEqual((done, gone, skipped), ([101], [], []))
        state = F.load_state(self.d)
        self.assertEqual(list(state), ["101"])
        self.assertEqual((state["101"]["comments"], state["101"]["newest_comment"]), (1, "2026-09-20T01:00:00.100000"))
        # 202 was never shown and 101 has a comment no one saw: both stay pending, and show marks that comment
        self.assertEqual(F.load_pending(self.d)["new"], [202, 101])
        text = F.show_topics(self.d, SLUG, new_only=True)
        self.assertIn("==== 202", text)
        self.assertIn("* NEW Second", text)
        self.assertNotIn("First", text)
        self.assertEqual(F.commit(self.d)[0], [202, 101])
        self.assertEqual(F.load_pending(self.d)["new"], [])

    def test_commit_of_given_ids_uses_the_shown_read_else_the_fetched_one(self):
        F.fetch_topics([7, 8], self.d, self.gateway, self.tmp, run=FakeGateway(topics=fake_topics()), pause=0)
        F.show_topics(self.d, SLUG, [8])
        self.assertEqual(F.commit(self.d, [7, 8, 9]), ([7, 8], [], [(9, "not fetched")]))
        self.assertEqual(F.load_state(self.d)["7"]["comments"], 4)
        self.assertEqual(F.load_shown(self.d)["topics"], {})


# a stand-in gateway run as a subprocess: topic list pages and topics from a fixture file
GATEWAY_STANDIN = """\
import json, sys
a = sys.argv[1:]
fx = json.load(open({fixtures!r}))
if a[:3] == ["competitions", "topics", "list"]:
    sys.stdout.write(fx["pages"][a[a.index("-p") + 1]])
elif a[:2] == ["--sdk", "topic"]:
    sys.stdout.write(fx["topics"][a[2]])
else:
    sys.exit(2)
"""


class CommandTests(Tmp):
    def run_main(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = F.main([*argv, "--dir", str(self.tmp / "store")])
        return code, out.getvalue(), err.getvalue()

    def test_check_show_and_commit_with_a_subprocess_gateway(self):
        fixtures = self.tmp / "fixtures.json"
        pages = {"1": cli_list([row(7, "A question", 4), row(8, "Quiet", 0)])}
        topics = {str(k): v for k, v in fake_topics().items()}
        fixtures.write_text(json.dumps({"pages": pages, "topics": topics}))
        self.gateway.write_text(GATEWAY_STANDIN.format(fixtures=str(fixtures)))
        code, out, err = self.run_main("check", SLUG, "--gateway", str(self.gateway), "--pause", "0")
        self.assertEqual(code, 0, err)
        self.assertIn("2 new, 0 changed, 0 missing", out)
        self.assertIn("fetched 7 (4 comments)", out)
        self.assertIn("next: python3", out)
        code, out, _ = self.run_main("show", SLUG)
        self.assertEqual(code, 0)
        self.assertTrue(out.startswith(F.DISCLAIMER))
        self.assertIn("==== 7  A question", out)
        self.assertIn("==== 8  Quiet", out)
        code, out, _ = self.run_main("commit", SLUG)
        self.assertIn("read: 2 topics recorded", out)
        code, out, _ = self.run_main("check", SLUG, "--gateway", str(self.gateway), "--pause", "0")
        self.assertIn("0 new, 0 changed, 0 missing", out)
        self.assertEqual(self.run_main("show", SLUG)[1], "nothing pending: pass topic ids to show\n")

    def test_default_store_is_under_the_project_root(self):
        proj = self.tmp / "proj"
        (proj / "aipack").mkdir(parents=True)
        (proj / "aipack" / "manifest.json").write_text(PACK_MANIFEST)
        (proj / "src").mkdir()
        self.assertEqual(F.find_root(proj / "src"), proj)
        self.assertEqual(F.store_dir(SLUG, root=proj), proj / "__data" / "kaggle" / SLUG / "forum")
        os.environ[F.ENV_DIR] = str(self.tmp / "env")
        self.assertEqual(F.store_dir(SLUG), self.tmp / "env" / SLUG)
        for bad in ("../x", "a/b", "", ".hidden"):
            with self.assertRaises(F.ForumError):
                F.store_dir(bad, directory=str(self.tmp))

    def test_project_root_for_any_pack_name_and_from_a_copied_install(self):
        (self.tmp / "elsewhere").mkdir()
        for pack in ("ai", "aipack", "mypack"):
            proj = self.tmp / f"p-{pack}"
            tools = proj / pack / "plugins" / "kaggle" / "tools"
            tools.mkdir(parents=True)
            (proj / pack / "manifest.json").write_text(PACK_MANIFEST)
            self.assertEqual((F.find_root(tools), F.pack_of(proj)), (proj, proj / pack))
            # a copied install finds its project from its own place
            with mock.patch.object(F, "__file__", str(tools / "kaggle_forum.py")):
                self.assertEqual(F.find_root(self.tmp / "elsewhere"), proj)
        # a plugin manifest and a hidden folder are no pack; two packs stop the walk with an error naming them
        odd = self.tmp / "odd"
        for name, text in (("plugin", '{"name": "kaggle"}'), (".hidden", PACK_MANIFEST), ("one", PACK_MANIFEST),
                           ("two", PACK_MANIFEST)):
            (odd / name).mkdir(parents=True)
            (odd / name / "manifest.json").write_text(text)
        with self.assertRaisesRegex(F.ForumError, r"odd: more than one ai-pack \(one, two\)$"):
            F.find_root(odd / "plugin")
        (odd / "two" / "manifest.json").unlink()
        self.assertEqual(F.pack_of(odd), odd / "one")

    def test_list_failure_exit_code_and_message(self):
        self.gateway.write_text(textwrap.dedent("""\
            import sys
            print("403 Client Error: Forbidden")
            sys.exit(1)
        """))
        code, _, err = self.run_main("list", SLUG, "--gateway", str(self.gateway), "--pause", "0")
        self.assertEqual(code, 1)
        self.assertIn("could not list the forum", err)


if __name__ == "__main__":
    unittest.main()
