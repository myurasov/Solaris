"""Offline tests for shared/tools/kaggle_submit.py (stdlib unittest; a stand-in gateway, no network).

    python3 -m unittest discover -s plugins/kaggle/tests

Every submit goes to the stand-in gateway (kaggle_standin), which logs it: nothing reaches Kaggle.
"""

import fcntl
import json
import subprocess
import sys
import unittest
from datetime import timedelta
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
from kaggle_standin import SLUG, Project  # noqa: E402

KERNEL = "alice/demo-kernel"
SUCCESS = "100%|##########| 1.00k/1.00k\n4 submissions remaining today.\nSuccessfully submitted to Demo Competition"
# what the gateway prints when Kaggle refuses a submit: its own line (kaggle.py SUBMIT_HOOK), then the CLI's
REFUSED = ("kaggle gateway: Kaggle refused the submit (400 FAILED_PRECONDITION): Submission not allowed: Your team "
           "already has 1 pending submissions\n")
CLI_400 = ("400 Client Error: Bad Request for url: https://api.kaggle.com/v1/competitions.CompetitionApiService/"
           "CreateCodeSubmission\n")


class Submit(Project):
    def setUp(self):
        super().setUp()
        self.answer(f"kernels status {KERNEL}", f'{KERNEL} has status "KernelWorkerStatus.COMPLETE"\n')
        self.answer(f"competitions submit {SLUG}", SUCCESS)

    def reviewed(self):
        """A recent pre-submit check, read and recorded."""
        self.assertEqual(self.tool("kaggle_presubmit.py", SLUG, "--gateway", str(self.gateway))[0], 10)
        self.assertEqual(self.tool("kaggle_presubmit.py", SLUG, "--ack")[0], 0)

    def record(self, name="s1", **fields):
        rec = {"competition": SLUG, "file": "submission.csv", "message": "S1: the base", "kernel": KERNEL, "version": 3,
               "notes": "kept as it is", **fields}
        path = self.root / "submissions" / name / "record.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({k: v for k, v in rec.items() if v is not None}))
        return path

    def submit(self, path, *args):
        return self.tool("kaggle_submit.py", str(path.relative_to(self.root)), "--gateway", str(self.gateway), *args)

    def submits(self):
        return [c for c in self.calls() if c["args"][:2] == ["competitions", "submit"]]


class RefusalTests(Submit):
    def test_an_incomplete_record_is_refused_without_a_kaggle_call(self):
        self.reviewed()
        cases = {"message": dict(message=None), "file": dict(file=" "), "one line": dict(message="a\nb"),
                 "version": dict(version=None), "kernel ref": dict(kernel="no-slash"),
                 "version without kernel": dict(kernel=None, file="missing.csv")}
        for case, fields in cases.items():
            code, out, _ = self.submit(self.record(**fields), "--go", "--review", "keep")
            self.assertEqual(code, 3, case)
            self.assertIn("REFUSED:", out, case)
        self.assertFalse([c for c in self.calls() if c["args"][0] in ("kernels", "competitions")
                          and c["args"][1] in ("status", "submit")])
        path = self.record()
        path.write_text("{not json")
        code, _, err = self.submit(path)
        self.assertEqual(code, 1)
        self.assertIn("cannot read the record", err)

    def test_the_pre_submit_check_must_be_fresh_and_acked(self):
        path = self.record()
        code, out, _ = self.submit(path)
        self.assertEqual(code, 3)
        self.assertIn("no pre-submit check yet: run kaggle_presubmit.py", out)
        self.tool("kaggle_presubmit.py", SLUG, "--gateway", str(self.gateway))
        code, out, _ = self.submit(path)
        self.assertEqual(code, 3)
        self.assertIn("no review recorded yet", out)
        self.tool("kaggle_presubmit.py", SLUG, "--ack")
        # something new after the ack: the next check finds it, and the submit waits for its review
        self.notebooks([("alice/base", 0.80), ("bo/new", 0.9)])
        self.tool("kaggle_presubmit.py", SLUG, "--gateway", str(self.gateway))
        code, out, _ = self.submit(path)
        self.assertEqual(code, 3)
        self.assertIn("found something new that no review recorded", out)
        self.tool("kaggle_presubmit.py", SLUG, "--ack")
        self.assertEqual(self.submit(path)[0], 0)
        # too old a check: a submit right before a fresh one only
        mod = self.module("kaggle_submit")
        seen = json.loads(self.store("presubmit", "seen.json").read_text())
        late = mod.load_tool("kaggle_presubmit").parse_time(seen["at"]) + timedelta(minutes=45)
        self.assertEqual(mod.review_problem(None, False), None)
        code, out = self.in_process(mod, [str(path)], now=late)
        self.assertEqual(code, 3)
        self.assertIn("min old (limit 30)", out)
        self.assertEqual(self.in_process(mod, [str(path), "--fresh", "60"], now=late)[0], 0)
        self.assertEqual(self.submits(), [])

    def in_process(self, mod, argv, now):
        import contextlib
        import io
        import os
        out = io.StringIO()
        old = os.getcwd()
        os.chdir(self.root)
        try:
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
                code = mod.main([*argv, "--gateway", str(self.gateway)], now=now)
        finally:
            os.chdir(old)
        return code, out.getvalue()

    def test_a_kernel_run_that_is_not_complete_is_refused(self):
        self.reviewed()
        for answer, why in ((f'{KERNEL} has status "KernelWorkerStatus.RUNNING"\n', "the kernel run is RUNNING"),
                            ("404 - Not Found\n", f"kernels status {KERNEL} failed")):
            self.answer(f"kernels status {KERNEL}", answer, code=0 if "RUNNING" in answer else 1)
            code, out, _ = self.submit(self.record(), "--go", "--review", "keep")
            self.assertEqual(code, 3)
            self.assertIn(why, out)
        self.assertEqual(self.submits(), [])

    def test_go_needs_a_one_line_review(self):
        self.reviewed()
        path = self.record()
        for args, why in (([], "--go needs --review"), (["--review", " "], "--review is empty"),
                          (["--review", "a\nb"], "--review must be one line"),
                          (["--review", "x" * 501], "--review is 501 characters")):
            code, out, _ = self.submit(path, "--go", *args)
            self.assertEqual(code, 3, args)
            self.assertIn(why, out)
        self.assertEqual(self.submits(), [])


class SubmitTests(Submit):
    def test_the_check_only_run_prints_the_command_and_sends_nothing(self):
        self.reviewed()
        code, out, err = self.submit(self.record())
        self.assertEqual(code, 0, err)
        self.assertIn("kernel     latest run COMPLETE", out)
        self.assertIn(f"competitions submit {SLUG} -f submission.csv -m 'S1: the base' -k {KERNEL} -v 3\n", out)
        self.assertIn("CHECKS PASSED: nothing submitted", out)
        status = [c for c in self.calls() if c["args"][:2] == ["kernels", "status"]]
        self.assertEqual([c["quiet"] for c in status], ["1"])
        self.assertEqual(self.submits(), [])

    def test_go_submits_once_and_records_the_result(self):
        self.reviewed()
        path = self.record()
        code, out, err = self.submit(path, "--go", "--review", "a host post on the metric, read: keep S1")
        self.assertEqual(code, 0, err)
        self.assertIn("SUBMITTED: recorded in submissions/s1/record.json", out)
        (call,) = self.submits()
        self.assertEqual(call["args"], ["competitions", "submit", SLUG, "-f", "submission.csv", "-m", "S1: the base",
                                        "-k", KERNEL, "-v", "3"])
        # the submit is the project's own activity: no KAGGLE_SHARE_QUIET
        self.assertIsNone(call["quiet"])
        # the gateway's mark is on the submit call only
        self.assertEqual([c["gated"] for c in self.calls() if c["args"][:2] in (["kernels", "status"],
                                                                                ["competitions", "submit"])],
                         [None, "1"])
        rec = json.loads(path.read_text())
        self.assertEqual(rec["notes"], "kept as it is")
        (attempt,) = rec["attempts"]
        self.assertEqual((attempt["result"], attempt["exit"], attempt["review"]),
                         ("submitted", 0, "a host post on the metric, read: keep S1"))
        self.assertIn("Successfully submitted", attempt["output"])
        code, out, _ = self.submit(path, "--go", "--review", "again")
        self.assertEqual(code, 3)
        self.assertIn("already submitted at", out)
        self.assertEqual(len(self.submits()), 1)

    def test_an_unclear_result_is_never_retried_blindly(self):
        self.reviewed()
        path = self.record()
        for answer, code in (({"out": "", "err": "Read timed out\n", "code": 1}, 1),
                             ({"out": "Could not submit to competition\n", "code": 0}, 0)):
            self.answer(f"competitions submit {SLUG}", **answer)
            path = self.record(name=f"s-{code}")
            got, out, _ = self.submit(path, "--go", "--review", "keep")
            self.assertEqual(got, 4, out)
            self.assertIn(f"UNCLEAR (exit {code}): the submission may or may not have landed", out)
            # the read it names lists 100 rows: the default page of 20 loses an older submission
            self.assertIn(f"competitions submissions {SLUG} --page-size 100` and", out)
            self.assertIn(f"competitions submission-limits {SLUG}`", out)
            self.assertEqual(json.loads(path.read_text())["attempts"][0]["result"], "unclear")
        before = len(self.submits())
        code, out, _ = self.submit(path, "--go", "--review", "keep")
        self.assertEqual(code, 3)
        self.assertIn("ended 'unclear', so it may have landed", out)
        self.assertIn(f"read `competitions submissions {SLUG} --page-size 100` and", out)
        self.assertEqual(len(self.submits()), before)
        # after reading the submissions and the limits: --checked lets one more attempt through
        self.answer(f"competitions submit {SLUG}", SUCCESS)
        code, out, _ = self.submit(path, "--go", "--review", "not listed: submit again", "--checked")
        self.assertEqual(code, 0, out)
        attempts = json.loads(path.read_text())["attempts"]
        self.assertEqual([a["result"] for a in attempts], ["unclear", "submitted"])
        self.assertIn("checked", attempts[1])

    def test_a_submit_kaggle_refuses_is_recorded_refused_with_its_reason(self):
        self.reviewed()
        path = self.record()
        reason = "400 FAILED_PRECONDITION: Submission not allowed: Your team already has 1 pending submissions"
        self.answer(f"competitions submit {SLUG}", err=REFUSED + CLI_400, code=1)
        code, out, _ = self.submit(path, "--go", "--review", "keep")
        self.assertEqual(code, 5, out)
        self.assertIn(f"REFUSED BY KAGGLE: {reason}\nNothing landed; recorded in submissions/s1/record.json", out)
        self.assertNotIn("UNCLEAR", out)
        (attempt,) = json.loads(path.read_text())["attempts"]
        self.assertEqual((attempt["result"], attempt["exit"], attempt["reason"]), ("refused", 1, reason))
        # nothing landed: the next --go needs no --checked
        self.answer(f"competitions submit {SLUG}", SUCCESS)
        code, out, _ = self.submit(path, "--go", "--review", "the pending one scored: submit again")
        self.assertEqual(code, 0, out)
        self.assertEqual([a["result"] for a in json.loads(path.read_text())["attempts"]], ["refused", "submitted"])
        self.assertEqual(len(self.submits()), 2)

    def test_only_kaggles_named_refusal_reads_as_refused(self):
        self.reviewed()
        # a 4xx without Kaggle's message, a 5xx, and the gateway's line on a run that did not fail: may have landed
        for n, (err, exit_code) in enumerate(((CLI_400, 1), ("500 Server Error: Internal Server Error for url: x\n", 1),
                                              (REFUSED, 0))):
            self.answer(f"competitions submit {SLUG}", err=err, code=exit_code)
            path = self.record(name=f"u{n}")
            code, out, _ = self.submit(path, "--go", "--review", "keep")
            self.assertEqual(code, 4, out)
            self.assertIn("UNCLEAR", out)
            self.assertEqual(json.loads(path.read_text())["attempts"][0]["result"], "unclear")
        mod = self.module("kaggle_submit")
        self.assertEqual(mod.refusal(1, "x\n" + REFUSED + "kaggle gateway: Kaggle refused the submit (429): slow\n"),
                         "429: slow")
        self.assertIsNone(mod.refusal(1, REFUSED.replace("(400 ", "(500 ")))

    def test_only_statuses_where_nothing_was_created_read_as_refused(self):
        self.reviewed()
        mod = self.module("kaggle_submit")
        for status in (400, 401, 403, 404, 429):
            line = f"kaggle gateway: Kaggle refused the submit ({status} KIND): the reason\n"
            self.assertEqual(mod.refusal(1, line + CLI_400), f"{status} KIND: the reason")
        # a timeout, a submission that exists or was aborted, a cancelled call: it may have landed, so even a line
        # naming one of these (no gateway prints it) reads as unclear, and the next --go needs --checked
        for n, (status, kind) in enumerate(((408, "DEADLINE_EXCEEDED"), (409, "ALREADY_EXISTS"), (409, "ABORTED"),
                                            (499, "CANCELLED"), (422, "UNPROCESSABLE"))):
            err = f"kaggle gateway: Kaggle refused the submit ({status} {kind}): the reason\n{status} Client Error\n"
            self.assertIsNone(mod.refusal(1, err))
            self.answer(f"competitions submit {SLUG}", err=err, code=1)
            path = self.record(name=f"c{n}")
            code, out, _ = self.submit(path, "--go", "--review", "keep")
            self.assertEqual(code, 4, out)
            self.assertNotIn("REFUSED BY KAGGLE", out)
            self.assertEqual(json.loads(path.read_text())["attempts"][0]["result"], "unclear")
        code, out, _ = self.submit(path, "--go", "--review", "keep")
        self.assertEqual(code, 3)
        self.assertIn("ended 'unclear', so it may have landed", out)

    def test_an_interrupted_attempt_counts_as_unclear(self):
        self.reviewed()
        path = self.record(attempts=[{"at": "2026-10-01T10:00:00Z", "result": "submitting"}])
        code, out, _ = self.submit(path, "--go", "--review", "keep")
        self.assertEqual(code, 3)
        self.assertIn("ended 'submitting', so it may have landed", out)
        self.assertEqual(self.submits(), [])

    def test_landed_marks_the_unclear_attempt_as_submitted_so_no_second_submit_follows(self):
        self.reviewed()
        path = self.record()
        self.answer(f"competitions submit {SLUG}", out="", err="Read timed out\n", code=1)
        self.assertEqual(self.submit(path, "--go", "--review", "keep")[0], 4)
        calls = len(self.calls())
        # the submissions read lists it: record its ref
        code, out, err = self.submit(path, "--landed", "57824036")
        self.assertEqual((code, err), (0, ""))
        (attempt,) = json.loads(path.read_text())["attempts"]
        self.assertEqual(out.splitlines(), [
            "record     submissions/s1/record.json",
            f"MARKED LANDED: the attempt at {attempt['at']} (it ended 'unclear') now reads 'submitted', ref 57824036; "
            "recorded in submissions/s1/record.json. A new submission needs its own record"])
        self.assertEqual((attempt["result"], attempt["was"], attempt["ref"], attempt["exit"]),
                         ("submitted", "unclear", 57824036, 1))
        self.assertRegex(attempt["marked"], r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$")
        self.assertEqual(json.loads(path.read_text())["notes"], "kept as it is")
        # no Kaggle call, and no leftover temp file beside the record
        self.assertEqual(len(self.calls()), calls)
        self.assertEqual(sorted(p.name for p in path.parent.iterdir()), ["record.json", "record.json.lock"])
        # the record now blocks a second submit, even with --checked
        self.answer(f"competitions submit {SLUG}", SUCCESS)
        before = len(self.submits())
        for more in ([], ["--checked"]):
            code, out, _ = self.submit(path, "--go", "--review", "again", *more)
            self.assertEqual(code, 3)
            self.assertIn(f"already submitted at {attempt['at']}", out)
        self.assertEqual(len(self.submits()), before)
        # and a second --landed has nothing to mark
        code, out, _ = self.submit(path, "--landed", "57824099")
        self.assertEqual(code, 3)
        self.assertIn(f"the record already holds a submitted attempt (at {attempt['at']}, ref 57824036): nothing to "
                      "mark", out)
        self.assertEqual(json.loads(path.read_text())["attempts"][0]["ref"], 57824036)

    def test_landed_takes_the_last_attempt_that_may_have_landed(self):
        # an unclear attempt read as not listed, then a refusal: the unclear one is found landed after all
        attempts = [{"at": "2026-10-01T10:00:00Z", "result": "unclear"},
                    {"at": "2026-10-01T11:00:00Z", "result": "refused", "reason": "400: pending"}]
        path = self.record(attempts=attempts)
        self.assertEqual(self.submit(path, "--landed", "7")[0], 0)
        got = json.loads(path.read_text())["attempts"]
        self.assertEqual([(a["result"], a.get("ref"), a.get("was")) for a in got],
                         [("submitted", 7, "unclear"), ("refused", None, None)])
        # a run cut off while submitting may have landed too
        path = self.record(name="s2", attempts=[{"at": "2026-10-01T10:00:00Z", "result": "submitting"}])
        code, out, _ = self.submit(path, "--landed", "8")
        self.assertEqual(code, 0)
        self.assertIn("(it ended 'submitting') now reads 'submitted', ref 8", out)
        # it needs no gateway and no pre-submit check, and makes no call
        self.assertEqual(self.tool("kaggle_submit.py", str(self.record(name="s3", attempts=attempts)), "--landed",
                                   "9", "--gateway", str(self.tmp / "none.py"))[0], 0)
        self.assertEqual(self.calls(), [])

    def test_landed_refuses_a_record_without_an_attempt_that_may_have_landed(self):
        cases = {"no-attempts": None, "refused": [{"at": "2026-10-01T10:00:00Z", "result": "refused"}],
                 "not-a-list": {"at": "x"}}
        for name, attempts in cases.items():
            path = self.record(name=name, attempts=attempts)
            before = path.read_text()
            code, out, _ = self.submit(path, "--landed", "7")
            self.assertEqual(code, 3, name)
            self.assertIn("REFUSED:\n  - ", out)
            self.assertIn('"attempts" is not a list' if name == "not-a-list" else
                          "no attempt in the record may have landed", out)
            self.assertEqual(path.read_text(), before)
        path = self.record(name="usage", attempts=[{"at": "2026-10-01T10:00:00Z", "result": "unclear"}])
        before = path.read_text()
        for bad in (["--landed", "abc"], ["--landed", "0"], ["--landed", "-5"], ["--landed"],
                    ["--landed", "7", "--go"], ["--landed", "7", "--review", "x"], ["--landed", "7", "--checked"],
                    ["--landed", "7", "--fresh", "10"]):
            code, out, err = self.submit(path, *bad)
            self.assertEqual((code, out), (2, ""), bad)
        self.assertIn("--landed only records an attempt as landed", err)
        self.assertEqual(path.read_text(), before)
        self.assertEqual(self.submits(), [])

    def test_a_file_competition_uploads_the_file_beside_the_record(self):
        self.reviewed()
        path = self.record(kernel=None, version=None, file="preds.csv")
        code, out, _ = self.submit(path, "--go", "--review", "keep")
        self.assertEqual(code, 3)
        self.assertIn('"file" preds.csv is not a file with content beside the record', out)
        (path.parent / "preds.csv").write_text("id,y\n1,0\n")
        code, out, err = self.submit(path, "--go", "--review", "keep")
        self.assertEqual(code, 0, err)
        (call,) = self.submits()
        self.assertEqual(call["args"], ["competitions", "submit", SLUG, "-f", "submissions/s1/preds.csv", "-m",
                                        "S1: the base"])
        self.assertFalse([c for c in self.calls() if c["args"][:2] == ["kernels", "status"]])

    def go(self, path):
        cmd = [sys.executable, str(self.tools / "kaggle_submit.py"), str(path.relative_to(self.root)), "--gateway",
               str(self.gateway), "--go", "--review", "keep"]
        return subprocess.Popen(cmd, cwd=self.root, env=self.env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                text=True)

    def test_two_concurrent_go_runs_submit_once(self):
        self.reviewed()
        path = self.record()
        # a real status read takes seconds: the first run is still checking when the second starts
        self.answer(f"kernels status {KERNEL}", f'{KERNEL} has status "KernelWorkerStatus.COMPLETE"\n', sleep=1)
        runs = [self.go(path), self.go(path)]
        outs = [r.communicate(timeout=120)[0] for r in runs]
        self.assertEqual(sorted(r.returncode for r in runs), [0, 3], outs)
        self.assertEqual(len(self.submits()), 1)
        (attempt,) = json.loads(path.read_text())["attempts"]
        self.assertEqual(attempt["result"], "submitted")

    def test_a_run_is_refused_while_another_holds_the_record(self):
        self.reviewed()
        path = self.record()
        with open(f"{path}.lock", "a") as held:
            fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)
            for args in ([], ["--go", "--review", "keep"]):
                code, out, _ = self.submit(path, *args)
                self.assertEqual(code, 3, out)
                self.assertEqual(out, "REFUSED:\n  - another kaggle_submit.py run holds submissions/s1/record.json"
                                      ".lock and may be submitting this record now: let it end, then read the record "
                                      "before running again\n")
        self.assertFalse([c for c in self.calls() if c["args"][0] in ("kernels", "competitions")
                          and c["args"][1] in ("status", "submit")])
        self.assertNotIn("attempts", json.loads(path.read_text()))
        # the lock is free once its holder ends
        self.assertEqual(self.submit(path, "--go", "--review", "keep")[0], 0)
        self.assertEqual(len(self.submits()), 1)
        code, _, err = self.submit(path.with_name("missing.json"))
        self.assertEqual(code, 1)
        self.assertIn("cannot read the record", err)
        self.assertFalse(path.with_name("missing.json.lock").exists())
        # a filesystem without locks fails closed
        mod = self.module("kaggle_submit")
        with mock.patch.object(mod.fcntl, "flock", side_effect=OSError("no locks available")):
            with self.assertRaisesRegex(mod.SubmitError, r"cannot lock .*record\.json\.lock \(no locks available\): "
                                        "keep the record on a local disk"):
                mod.hold(path)

    def test_the_docstring_says_it_is_a_write(self):
        mod = self.module("kaggle_submit")
        self.assertIn("This is a Kaggle write", mod.__doc__)
        self.assertIn("owner's approval", mod.__doc__)
        self.assertIn("standing grant", mod.__doc__)
        doc = " ".join(mod.__doc__.split())
        for code in ("0 submitted", "3 refused by the checks", "4 unclear result", "5 refused by Kaggle"):
            self.assertIn(code, doc)
        # what decides between --landed and --checked after an unclear attempt
        self.assertIn("--landed <ref>", doc)
        self.assertIn('A read that failed (an error, HTTP 429, a timeout) or came back empty is not "not listed"', doc)
        self.assertIn("a clean read shows no row with the exact message in that time window", doc)


if __name__ == "__main__":
    unittest.main()
