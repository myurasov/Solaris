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
            self.assertIn(f"competitions submissions {SLUG}` and", out)
            self.assertIn(f"competitions submission-limits {SLUG}`", out)
            self.assertEqual(json.loads(path.read_text())["attempts"][0]["result"], "unclear")
        before = len(self.submits())
        code, out, _ = self.submit(path, "--go", "--review", "keep")
        self.assertEqual(code, 3)
        self.assertIn("ended 'unclear', so it may have landed", out)
        self.assertEqual(len(self.submits()), before)
        # after reading the submissions and the limits: --checked lets one more attempt through
        self.answer(f"competitions submit {SLUG}", SUCCESS)
        code, out, _ = self.submit(path, "--go", "--review", "not listed: submit again", "--checked")
        self.assertEqual(code, 0, out)
        attempts = json.loads(path.read_text())["attempts"]
        self.assertEqual([a["result"] for a in attempts], ["unclear", "submitted"])
        self.assertIn("checked", attempts[1])

    def test_an_interrupted_attempt_counts_as_unclear(self):
        self.reviewed()
        path = self.record(attempts=[{"at": "2026-10-01T10:00:00Z", "result": "submitting"}])
        code, out, _ = self.submit(path, "--go", "--review", "keep")
        self.assertEqual(code, 3)
        self.assertIn("ended 'submitting', so it may have landed", out)
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


if __name__ == "__main__":
    unittest.main()
