"""Offline tests for shared/tools/kaggle_sdk.py, the SDK reads (stdlib unittest; fake api and client objects, no network).

    python3 -m unittest discover -s plugins/kaggle/tests

The Kaggle packages are never imported: each read gets a fake signed-in api, and two stand-in modules
take the place of the SDK types the notebook search builds its requests from.
"""

import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import textwrap
import types
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace as NS
from unittest import mock

sys.dont_write_bytecode = True  # keep __pycache__ out of the plugin folder
TOOLS = Path(__file__).resolve().parents[1] / "shared" / "tools"
sys.path.insert(0, str(TOOLS))
import kaggle_sdk as S  # noqa: E402

SLUG = "demo-competition"
WHEN = datetime(2026, 9, 25, 0, 0, 57, 303000)


class Bag:
    """Stands in for an SDK request type: a plain attribute holder."""


def module(name, **attrs):
    m = types.ModuleType(name)
    vars(m).update(attrs)
    return m


SDK_TYPES = {
    "kagglesdk.search.types.search_api_service":
        module("search_api_service", ListEntitiesFilters=Bag, ListEntitiesRequest=Bag),
    "kagglesdk.search.types.search_enums": module("search_enums", DocumentType=NS(KERNEL="KERNEL")),
}


def http_error(status, message, reason=""):
    # like requests' HTTPError: the message, and the response it came with
    e = Exception(message)
    e.response = NS(status_code=status, reason=reason)
    return e


def kernel(ref, votes=1):
    # what kernels_list returns, with more fields than the read keeps
    return NS(ref=ref, title=f"Title of {ref}", last_run_time=WHEN, total_votes=votes, author="someone", id=9)


def doc(ref, score=None, linked=False):
    """A notebook search document as the SDK reads it: a missing score is 0.0, and the slug has no owner."""
    owner, name = ref.split("/")
    return NS(slug=name, owner_user=NS(user_name=owner, display_name="Someone"), owner_organization=None,
              kernel_document=NS(has_linked_submission=linked, best_public_score=score or 0.0))


class FakeSearch:
    """The SDK's search client: pages for the competition query by page token, then single-lookup results."""

    def __init__(self, pages=None, lookups=None, fail=None):
        self.pages = pages or {"": ([], "")}
        self.lookups = lookups or {}
        self.fail = fail  # which call (1 = the first) raises a 429
        self.queries = []

    def list_entities(self, req):
        f = req.filters
        self.queries.append((f.query, req.page_token, req.page_size, f.document_types))
        if self.fail == len(self.queries):
            raise http_error(429, "slow down")
        if f.query == SLUG:
            docs, token = self.pages[req.page_token]
        else:
            docs, token = self.lookups.get(f.query, []), ""
        return NS(documents=docs, next_page_token=token)


class FakeApi:
    """A signed-in KaggleApi: kernels_list pages, the search client, and forums_topic_show."""

    def __init__(self, kernels=(), search=None, topic=None, comments=(), fail_page=None):
        self.kernels = list(kernels)
        self.search = search or FakeSearch()
        self.topic, self.comments = topic, list(comments)
        self.fail_page = fail_page
        self.list_calls = []

    def kernels_list(self, **kw):
        self.list_calls.append(kw)
        if kw["page"] == self.fail_page:
            raise http_error(403, "Permission denied")
        start = (kw["page"] - 1) * kw["page_size"]
        return self.kernels[start:start + kw["page_size"]]

    def build_kaggle_client(self):
        return contextlib.nullcontext(NS(search=NS(search_api_client=self.search)))

    def forums_topic_show(self, topic_id):
        # the CLI's library prints notices like this one on stdout
        print("Warning: Looks like you're using an outdated `kaggle` version")
        return self.topic, self.comments, ""


def listed(ref, hours_ago):
    # one of our kernels as kernels_list returns it, last run hours ago: naive UTC, as the SDK reads Kaggle's times
    return NS(ref=ref, title=f"Title of {ref}", total_votes=0,
              last_run_time=datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=hours_ago))


class AccountApi(FakeApi):
    """A signed-in KaggleApi for the account read: the quota, our kernels and each one's run state."""

    def __init__(self, kernels=(), states=None, quota=None, fail=(), fail_page=None):
        super().__init__(kernels, fail_page=fail_page)
        self.states, self.fail, self.asked = states or {}, set(fail), []
        self.quota = quota or NS(quota_refresh_time=datetime(2026, 10, 3), tpu_quota=None, gpu_quota=NS(
            time_used=timedelta(seconds=5477), total_time_allowed=timedelta(hours=30)))

    def quota_view(self):
        print("Warning: Looks like you're using an outdated `kaggle` version")
        if "quota" in self.fail:
            raise http_error(503, "try later")
        return self.quota

    def kernels_status(self, ref):
        self.asked.append(ref)
        if ref in self.fail:
            raise http_error(429, "slow down")
        return NS(status=NS(name=self.states.get(ref, "COMPLETE")), failure_message="")


def run(argv, api=None):
    out, err = io.StringIO(), io.StringIO()
    with mock.patch.object(S, "sign_in", return_value=api) as sign_in, \
            contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            code = S.main(argv)
        except SystemExit as e:
            code = e.code
    if code == 2:
        sign_in.assert_not_called()
    return code, out.getvalue(), err.getvalue()


def comment(cid, replies=(), when=WHEN):
    return NS(id=cid, author_name=f"Author {cid}", author_url=f"/user{cid}", post_date=when, votes=cid,
              content=f"<p>comment {cid}</p>", replies=list(replies))


def shown(cid, replies=(), when="2026-09-25T00:00:57.303000"):
    return {"id": cid, "authorName": f"Author {cid}", "postDate": when, "votes": cid,
            "content": f"<p>comment {cid}</p>", "replies": list(replies)}


class TopicTests(unittest.TestCase):
    topic = NS(id=5, title="A topic", url="/t/5", author_name="Alice", author_url="/alice", votes=3,
               post_date=WHEN, last_comment_date=WHEN, comment_count=4, forum_name="F", forum_id=1,
               content='<p>Opening <a href="https://example.com">post</a></p>')

    def test_topic_and_nested_replies_with_only_the_contract_fields(self):
        comments = [comment(1, [comment(2, [comment(3)])]), comment(4)]
        code, out, err = run(["topic", "5"], FakeApi(topic=self.topic, comments=comments))
        self.assertEqual((code, err), (0, ""))
        self.assertEqual(json.loads(out), {
            "topic": {"id": 5, "title": "A topic", "authorName": "Alice", "postDate": "2026-09-25T00:00:57.303000",
                      "votes": 3, "commentCount": 4, "content": '<p>Opening <a href="https://example.com">post</a></p>'},
            "comments": [shown(1, [shown(2, [shown(3)])]), shown(4)]})

    def test_dates_print_as_the_cli_json_prints_them(self):
        comments = [comment(1, when=datetime(2026, 9, 25, 7, 5, 0)), comment(2, when=None)]
        _, out, _ = run(["topic", "5"], FakeApi(topic=self.topic, comments=comments))
        self.assertEqual([c["postDate"] for c in json.loads(out)["comments"]], ["2026-09-25T07:05:00", None])

    def test_a_missing_topic_fails(self):
        self.assertEqual(run(["topic", "5"], FakeApi()), (1, "", "kaggle_sdk: topic 5 not found\n"))


class NotebookTests(unittest.TestCase):
    def setUp(self):
        p = mock.patch.dict(sys.modules, SDK_TYPES)
        p.start()
        self.addCleanup(p.stop)

    def read(self, api, *flags):
        code, out, err = run(["notebooks", SLUG, *flags], api)
        self.assertEqual((code, err), (0, ""))
        return json.loads(out)

    def scores(self, res):
        return [n["score"] for n in res["notebooks"]]

    def test_scores_merge_by_ref_and_a_missing_score_stays_null(self):
        refs = ["ann/one", "bob/two", "cid/three", "dee/four", "eve/five"]
        docs = [doc("ANN/One", 0.512), doc("bob/two"), doc("cid/three", linked=True), doc("eve/five", 0.25, linked=True)]
        search = FakeSearch(pages={"": (docs, "")}, lookups={"dee/four": [doc("dee/four", 0.3)]})
        api = FakeApi([kernel(r) for r in refs], search)
        res = self.read(api)
        self.assertEqual(self.scores(res), [0.512, None, None, 0.3, 0.25])
        self.assertEqual((res["competition"], res["complete"], res["note"]), (SLUG, True, None))
        self.assertRegex(res["fetched_at"], r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$")
        self.assertEqual(res["notebooks"][0], {"ref": "ann/one", "title": "Title of ann/one",
                                               "lastRunTime": "2026-09-25T00:00:57.303000", "votes": 1, "score": 0.512})
        self.assertEqual(search.queries, [(SLUG, "", S.SEARCH_PAGE, ["KERNEL"]), ("dee/four", "", S.SEARCH_PAGE, ["KERNEL"])])
        self.assertEqual(api.list_calls, [{"page": 1, "page_size": 100, "competition": SLUG, "sort_by": "scoreDescending"}])

    def test_search_pages_stop_once_every_notebook_is_found(self):
        search = FakeSearch(pages={"": ([doc("ann/one", 0.5)], "t2"), "t2": ([doc("bob/two", 0.4)], "t3")})
        res = self.read(FakeApi([kernel("ann/one"), kernel("bob/two")], search))
        self.assertEqual((self.scores(res), res["complete"]), ([0.5, 0.4], True))
        self.assertEqual([q[:2] for q in search.queries], [(SLUG, ""), (SLUG, "t2")])

    def test_search_pages_are_capped_and_misses_looked_up_singly(self):
        pages = {"" if i == 0 else f"t{i}": ([], f"t{i + 1}") for i in range(10)}
        search = FakeSearch(pages=pages, lookups={"ann/one": [doc("ann/one", 0.5), doc("bob/two", 0.4)]})
        res = self.read(FakeApi([kernel("ann/one"), kernel("bob/two")], search))
        self.assertEqual((self.scores(res), res["complete"]), ([0.5, 0.4], True))
        # bob/two came with ann/one's lookup, so it needs none of its own
        self.assertEqual([q[0] for q in search.queries], [SLUG] * S.SEARCH_PAGES + ["ann/one"])

    def test_single_lookups_are_capped(self):
        refs = [f"user{i}/nb" for i in range(25)]
        search = FakeSearch(lookups={r: [doc(r, 0.1)] for r in refs})
        res = self.read(FakeApi([kernel(r) for r in refs], search))
        self.assertEqual([q[0] for q in search.queries], [SLUG, *refs[:S.LOOKUPS]])
        self.assertEqual(self.scores(res), [0.1] * 20 + [None] * 5)
        self.assertEqual((res["complete"], res["note"]), (False, "20 single lookups made, scores left unread: 5"))

    def test_a_notebook_the_search_cannot_find_is_noted(self):
        res = self.read(FakeApi([kernel("ann/one")], FakeSearch()))
        self.assertEqual(self.scores(res), [None])
        self.assertEqual((res["complete"], res["note"]), (False, "not found by the search, score unknown: ann/one"))

    def test_the_list_stops_at_max(self):
        api = FakeApi([kernel(f"u{i}/nb") for i in range(3)])
        res = self.read(api, "--max", "2")
        self.assertEqual([n["ref"] for n in res["notebooks"]], ["u0/nb", "u1/nb"])
        self.assertEqual([c["page_size"] for c in api.list_calls], [2])
        self.assertFalse(res["complete"])
        self.assertTrue(res["note"].startswith("the list stopped at --max 2"))

    def test_the_list_pages_by_100(self):
        refs = [f"u{i}/nb" for i in range(150)]
        search = FakeSearch(pages={"": ([doc(r, 0.2) for r in refs], "")})
        res = self.read(FakeApi([kernel(r) for r in refs], search), "--max", "1000")
        self.assertEqual((len(res["notebooks"]), res["complete"]), (150, True))
        api = FakeApi([kernel(r) for r in refs], search)
        res = self.read(api, "--max", "120")
        self.assertEqual((len(res["notebooks"]), res["note"]), (120, "the list stopped at --max 120"))
        self.assertEqual([(c["page"], c["page_size"]) for c in api.list_calls], [(1, 100), (2, 100)])

    def test_a_failed_first_list_page_fails_the_read(self):
        code, out, err = run(["notebooks", SLUG], FakeApi([kernel("ann/one")], fail_page=1))
        self.assertEqual((code, out, err), (1, "", "kaggle_sdk: 403 Forbidden: Permission denied\n"))

    def test_a_failed_later_list_page_keeps_what_was_read(self):
        refs = [f"u{i}/nb" for i in range(150)]
        search = FakeSearch(pages={"": ([doc(r, 0.2) for r in refs], "")})
        res = self.read(FakeApi([kernel(r) for r in refs], search, fail_page=2), "--max", "300")
        self.assertEqual((len(res["notebooks"]), res["complete"]), (100, False))
        self.assertEqual(res["note"], "notebook list page 2 failed: 403 Forbidden: Permission denied")

    def test_a_failed_search_call_ends_the_calls(self):
        for fail, step in ((1, "search page 1"), (2, "lookup of ann/one")):
            search = FakeSearch(fail=fail)
            res = self.read(FakeApi([kernel("ann/one"), kernel("bob/two")], search))
            self.assertEqual(len(search.queries), fail)
            self.assertEqual((self.scores(res), res["complete"]), ([None, None], False))
            self.assertEqual(res["note"], f"{step} failed, no scores read after it: 429 Too Many Requests: slow down")

    def test_no_notebooks_means_no_search(self):
        search = FakeSearch()
        res = self.read(FakeApi([], search))
        self.assertEqual((res["notebooks"], res["complete"], res["note"], search.queries), ([], True, None, []))


class AccountTests(unittest.TestCase):
    def read(self, api, *flags):
        code, out, err = run(["account", *flags], api)
        self.assertEqual((code, err), (0, ""))
        return json.loads(out)

    def states(self, res):
        return [k["status"] for k in res["kernels"]]

    def test_quota_kernels_and_the_run_states_of_recent_runs(self):
        api = AccountApi([listed("Ann/Running", 1), listed("ann/done", 5), listed("ann/old", 20)],
                         states={"Ann/Running": "RUNNING"})
        res = self.read(api)
        self.assertEqual(api.list_calls, [{"page": 1, "page_size": 50, "mine": True, "sort_by": "dateRun"}])
        self.assertEqual(api.asked, ["Ann/Running", "ann/done"])
        self.assertEqual(res["kernels"], [{"ref": k.ref, "lastRunTime": k.last_run_time.isoformat(), "status": s}
                                          for k, s in zip(api.kernels, ("RUNNING", "COMPLETE", None))])
        self.assertEqual(res["quota"], {"gpu": {"used": 1.52, "remaining": 28.48, "total": 30.0,
                                                "refresh": "2026-10-03T00:00:00"}})
        self.assertEqual((res["calls"], res["errors"]), (4, []))
        fetched, since = (datetime.strptime(res[k], S.ISO) for k in ("fetched_at", "since"))
        self.assertEqual(fetched - since, timedelta(hours=12))

    def test_hours_and_page_size_set_the_window_and_the_list(self):
        kernels = [listed("ann/a", 1), listed("ann/b", 5), listed("ann/c", 20)]
        api = AccountApi(kernels)
        self.assertEqual(self.states(self.read(api, "--hours", "24")), ["COMPLETE"] * 3)
        api = AccountApi(kernels)
        res = self.read(api, "--hours", "0", "--page-size", "2")
        self.assertEqual((self.states(res), res["calls"], api.asked), ([None, None], 2, []))
        self.assertEqual(api.list_calls[0]["page_size"], 2)

    def test_runs_named_done_are_not_read_again(self):
        kernels = [listed("ann/a", 1), listed("ann/b", 2), listed("ann/c", 3)]
        api = AccountApi(kernels)
        # b's run is known to have finished; c has run again since the run the caller knows
        res = self.read(api, "--done", f"ANN/B={kernels[1].last_run_time.isoformat()}",
                        "--done", "ann/c=2026-01-01T00:00:00")
        self.assertEqual((api.asked, self.states(res), res["calls"]),
                         (["ann/a", "ann/c"], ["COMPLETE", None, "COMPLETE"], 4))

    def test_status_reads_stop_at_the_first_25_recent_runs(self):
        kernels = [listed(f"ann/k{i}", 0.1 * i) for i in range(30)]
        api = AccountApi(kernels)
        res = self.read(api)
        self.assertEqual(api.asked, [k.ref for k in kernels[:S.STATUSES]])
        self.assertEqual((self.states(res)[24:], res["calls"]), (["COMPLETE"] + [None] * 5, 27))
        # a run named done keeps its place among the 25
        api = AccountApi(kernels)
        self.read(api, "--done", f"ann/k0={kernels[0].last_run_time.isoformat()}")
        self.assertEqual(api.asked, [k.ref for k in kernels[1:S.STATUSES]])

    def test_failed_calls_are_reported_and_the_read_goes_on(self):
        api = AccountApi([listed("ann/a", 1), listed("ann/b", 2)], fail={"quota", "ann/a"})
        res = self.read(api)
        self.assertEqual((res["quota"], self.states(res), res["calls"]), (None, [None, "COMPLETE"], 4))
        self.assertEqual(res["errors"], ["quota read failed: 503 Service Unavailable: try later",
                                         "status read of ann/a failed: 429 Too Many Requests: slow down"])

    def test_a_failed_list_leaves_the_kernels_null(self):
        api = AccountApi([listed("ann/a", 1)], fail_page=1)
        res = self.read(api)
        self.assertEqual((res["kernels"], res["calls"], api.asked), (None, 2, []))
        self.assertEqual(res["errors"], ["kernel list failed: 403 Forbidden: Permission denied"])
        self.assertIn("gpu", res["quota"])

    def test_quota_without_a_gpu_or_a_reset_time(self):
        tpu = NS(time_used=timedelta(0), total_time_allowed=timedelta(hours=20))
        res = self.read(AccountApi(quota=NS(quota_refresh_time=None, gpu_quota=None, tpu_quota=tpu)))
        self.assertEqual(res["quota"], {"tpu": {"used": 0.0, "remaining": 20.0, "total": 20.0, "refresh": None}})
        res = self.read(AccountApi(quota=NS(quota_refresh_time=None, gpu_quota=None, tpu_quota=None)))
        self.assertEqual((res["quota"], res["kernels"]), ({}, []))

    def test_bad_account_arguments_exit_2_with_usage(self):
        for argv in (["account", "--hours", "-1"], ["account", "--hours", "x"], ["account", "--hours", "nan"],
                     ["account", "--hours", "8761"], ["account", "--page-size", "0"],
                     ["account", "--page-size", "101"], ["account", "--done", "ann/a"],
                     ["account", "--done", "ann/a="], ["account", "--done", "nope=t"],
                     ["account", "--done", "a/b/c=t"], ["account", "extra"]):
            code, out, err = run(argv)
            self.assertEqual((code, out), (2, ""), argv)
            self.assertIn("usage:", err)
        a = S.parse(["account", "--hours", "0", "--page-size", "100", "--done", "Ann/A=2026-09-25T00:00:57.303000"])
        self.assertEqual((a.hours, a.page_size, a.done), (0.0, 100, [("ann/a", "2026-09-25T00:00:57.303000")]))
        a = S.parse(["account"])
        self.assertEqual((a.hours, a.page_size, a.done), (12, 50, []))
        self.assertEqual(S.parse(["account", "--hours", "8760", "--page-size", "1"]).hours, 8760.0)

    def test_a_failed_sign_in_fails_the_read(self):
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(S, "sign_in", side_effect=S.SdkError("not signed in to Kaggle: sign in first")), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = S.main(["account"])
        self.assertEqual((code, out.getvalue(), err.getvalue()),
                         (1, "", "kaggle_sdk: not signed in to Kaggle: sign in first\n"))


class ErrorTests(unittest.TestCase):
    def test_error_lines(self):
        self.assertEqual(S.error_text(http_error(429, "too\nmany")), "429 Too Many Requests: too many")
        self.assertEqual(S.error_text(http_error(599, "odd", reason="Odd")), "599 Odd: odd")
        self.assertEqual(S.error_text(ValueError("no network")), "no network")
        self.assertEqual(S.error_text(OSError()), "OSError")

    def test_bad_arguments_exit_2_with_usage(self):
        for argv in ([], ["topic"], ["topic", "abc"], ["topic", "0"], ["topic", "-3"], ["topic", "1.5"],
                     ["notebooks"], ["notebooks", "bad slug"], ["notebooks", "../up"], ["notebooks", "a/b"],
                     ["notebooks", SLUG, "--max", "0"], ["notebooks", SLUG, "--max", "1001"],
                     ["notebooks", SLUG, "--max", "x"], ["no-such-read"]):
            code, out, err = run(argv)
            self.assertEqual((code, out), (2, ""), argv)
            self.assertIn("usage:", err)

    def test_counts_at_the_bounds_are_accepted(self):
        self.assertEqual([S.parse(["notebooks", SLUG, "--max", m]).max for m in ("1", "1000")], [1, 1000])
        self.assertEqual(S.parse(["notebooks", SLUG]).max, 100)


class SignInTests(unittest.TestCase):
    def main(self, kaggle):
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.dict(sys.modules, {"kaggle": kaggle}), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = S.main(["topic", "5"])
        return code, out.getvalue(), err.getvalue()

    def test_not_signed_in_is_one_line(self):
        class Api:
            _authenticated = False

            def authenticate(self):
                print("Authentication required to call the Kaggle API.\n\nkaggle auth login")
                sys.exit(1)

        code, out, err = self.main(module("kaggle", api=Api()))
        self.assertEqual((code, out), (1, ""))
        self.assertRegex(err, r"^kaggle_sdk: not signed in to Kaggle: [^\n]*\n$")

    def test_the_clis_signed_in_api_is_used_as_is(self):
        topic = NS(id=5, title="T", author_name="A", post_date=None, votes=0, comment_count=0, content="")
        api = NS(_authenticated=True, authenticate=None, forums_topic_show=lambda topic_id: (topic, [], ""))
        code, out, _ = self.main(module("kaggle", api=api))
        self.assertEqual((code, json.loads(out)["topic"]["id"]), (0, 5))

    def test_missing_packages_point_at_the_gateway(self):
        code, out, err = self.main(None)
        self.assertEqual((code, out), (1, ""))
        self.assertRegex(err, r"^kaggle_sdk: [^\n]*run this through the gateway[^\n]*\n$")

    def test_the_gateway_beside_it_does_not_shadow_the_kaggle_package(self):
        # run as a script, the tool's own folder (which holds kaggle.py) comes first on sys.path
        with tempfile.TemporaryDirectory() as tmp:
            pkg = Path(tmp) / "kaggle"
            pkg.mkdir()
            (pkg / "__init__.py").write_text(textwrap.dedent("""\
                from types import SimpleNamespace as NS
                topic = NS(id=8, title="T", author_name="A", post_date=None, votes=0, comment_count=0, content="")
                api = NS(_authenticated=True, forums_topic_show=lambda topic_id: (topic, [], ""))
                print("a notice from the library")
                """))
            env = {**os.environ, "PYTHONPATH": tmp, "PYTHONDONTWRITEBYTECODE": "1"}
            p = subprocess.run([sys.executable, str(TOOLS / "kaggle_sdk.py"), "topic", "8"], cwd=tmp, env=env,
                               capture_output=True, text=True, timeout=60)
        self.assertEqual((p.returncode, p.stderr), (0, ""))
        self.assertEqual(json.loads(p.stdout), {"topic": {"id": 8, "title": "T", "authorName": "A", "postDate": None,
                                                          "votes": 0, "commentCount": 0, "content": ""},
                                                "comments": []})


if __name__ == "__main__":
    unittest.main()
