"""Offline tests for shared/tools/kaggle_watch.py (stdlib unittest; a stand-in gateway, no network).

    python3 -m unittest discover -s plugins/kaggle/tests -p 'test_kaggle_watch.py'

The gateway here answers each call with the next entry of a list (the last one again after that), so a
submission can go from pending to scored between two reads. Nothing reaches Kaggle.
"""

import json
import signal
import subprocess
import sys
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from kaggle_standin import SLUG, TOOLS, Project, load  # noqa: E402

REF = "58711656"
# answers call N with entry N of sequence.json beside it, and the last entry from then on; logs each call
SEQUENCE = """\
import json, os, sys
here = os.path.dirname(os.path.abspath(__file__))
args = sys.argv[1:]
log = os.path.join(here, "calls.jsonl")
with open(log, "a") as f:
    f.write(json.dumps({"args": args, "quiet": os.environ.get("KAGGLE_SHARE_QUIET")}) + "\\n")
with open(log) as f:
    n = sum(1 for _ in f) - 1
with open(os.path.join(here, "sequence.json")) as f:
    seq = json.load(f)
r = seq[min(n, len(seq) - 1)]
sys.stdout.write(r.get("out", ""))
sys.stderr.write(r.get("err", ""))
sys.exit(r.get("code", 0))
"""


def sub(ref=REF, status="PENDING", score=None, error=None, description="061: the base",
        date="2026-11-22T08:27:33Z"):
    return {"ref": ref, "date": date, "description": description, "status": status, "publicScore": score,
            "errorDescription": error}


def listing(*subs):
    return {"out": json.dumps({"competition": SLUG, "fetched_at": "2026-11-22T09:00:00Z", "submissions": list(subs)})}


OLDER = sub(ref="58700001", status="COMPLETE", score="0.80", description="060: before")
PENDING = listing(sub(), OLDER)
SCORED = listing(sub(status="COMPLETE", score="0.84512"), OLDER)
RATE = {"code": 1, "err": "kaggle_sdk: 429 Too Many Requests: slow down\n"}
BROKEN = {"code": 1, "err": "kaggle_sdk: 503 Service Unavailable: try later\n"}


class Watch(Project):
    def setUp(self):
        super().setUp()
        # the copy polls fast: no lower bound on --every, and a short wait before the confirming read
        tool = self.tools / "kaggle_watch.py"
        text = tool.read_text()
        for old, new in (("EVERY, MIN_EVERY = 180.0, 20.0\n", "EVERY, MIN_EVERY = 180.0, 0.0\n"),
                         ("\nCONFIRM = 30.0\n", "\nCONFIRM = 0.02\n")):
            self.assertEqual(text.count(old), 1, old)
            text = text.replace(old, new)
        tool.write_text(text)
        self.gateway.write_text(SEQUENCE)

    def answers(self, *seq):
        (self.gateway.parent / "sequence.json").write_text(json.dumps(list(seq)))

    def watch(self, *args, every="0.02"):
        return self.tool("kaggle_watch.py", SLUG, *args, "--every", every, "--gateway", str(self.gateway))

    def beat(self, name=REF):
        return json.loads(self.store("watch", f"{name}.json").read_text())


class OutcomeTests(Watch):
    def test_a_pending_submission_is_polled_until_it_is_scored(self):
        self.answers(PENDING, PENDING, SCORED)
        code, out, err = self.watch("--ref", REF)
        self.assertEqual((code, out), (0, f"{REF} 0.84512 COMPLETE\n"), err)
        calls = self.calls()
        self.assertEqual(len(calls), 3)
        # each read is the one read-only call, with no activity stamp
        self.assertEqual(calls[0], {"args": ["--sdk", "submissions", SLUG, "--page-size", "100", "--json"],
                                    "quiet": "1"})
        # a change of state is told once, not every poll
        self.assertEqual(err.count(": pending"), 1)
        beat = self.beat()
        self.assertEqual((beat["state"], beat["exit"], beat["polls"], beat["public_score"], beat["next"]),
                         ("scored", 0, 3, "0.84512", None))
        self.assertEqual((beat["slug"], beat["ref"], beat["status"]), (SLUG, REF, "COMPLETE"))

    def test_an_errored_submission_exits_3_with_kaggles_text(self):
        self.answers(PENDING, listing(sub(status="ERROR", error="Notebook  Threw\nException")))
        code, out, _err = self.watch("--ref", REF)
        self.assertEqual((code, out), (3, f"{REF} ERROR: Notebook Threw Exception\n"))
        self.assertEqual((self.beat()["state"], self.beat()["exit"], self.beat()["error"]),
                         ("errored", 3, "Notebook Threw Exception"))
        # a state that is neither waiting nor COMPLETE ends the watch the same way
        self.answers(listing(sub(status="SubmissionStatus.CANCELLED")))
        code, out, _err = self.watch("--ref", REF)
        self.assertEqual((code, out), (3, f"{REF} CANCELLED: Kaggle gave no error text\n"))

    def test_a_score_without_a_public_value_is_still_scored(self):
        self.answers(listing(sub(status="SubmissionStatus.COMPLETE")))
        self.assertEqual(self.watch("--ref", REF)[:2], (0, f"{REF} - COMPLETE\n"))

    def test_the_limit_ends_a_watch_of_a_pending_submission(self):
        self.answers(PENDING)
        code, out, _err = self.watch("--ref", REF, "--limit", "0.004")
        self.assertEqual(code, 4)
        self.assertTrue(out.startswith(f"{REF} still pending after 0.004 min: the last read was clean; last status "
                                       "PENDING at 20"), out)
        self.assertEqual((self.beat()["state"], self.beat()["exit"]), ("limit", 4))
        self.assertGreaterEqual(len(self.calls()), 2)

    def test_a_submission_missing_from_two_clean_reads_is_not_listed(self):
        self.answers(listing(OLDER))
        code, out, _err = self.watch("--ref", REF)
        self.assertEqual(code, 5)
        self.assertEqual(out, f"{REF} is not listed: two clean reads did not show it among the team's newest 100 "
                         "submissions\n")
        self.assertEqual(len(self.calls()), 2)
        self.assertEqual((self.beat()["state"], self.beat()["exit"]), ("not listed", 5))

    def test_a_submission_that_shows_up_on_the_second_read_is_watched(self):
        self.answers(listing(OLDER), SCORED)
        self.assertEqual(self.watch("--ref", REF)[0], 0)

    def test_a_failed_read_or_a_429_is_never_not_listed(self):
        self.answers(RATE, BROKEN, {"out": "not json"}, SCORED)
        code, out, err = self.watch("--ref", REF)
        self.assertEqual((code, out), (0, f"{REF} 0.84512 COMPLETE\n"), err)
        self.assertEqual(len(self.calls()), 4)
        self.assertIn(": rate-limited (exit 1: kaggle_sdk: 429 Too Many Requests: slow down)", err)
        self.assertIn(": read failed (exit 1: kaggle_sdk: 503 Service Unavailable: try later)", err)
        # reads that never work end at the limit, as "still pending", never as "not listed"
        self.answers(RATE)
        code, out, _err = self.watch("--ref", REF, "--limit", "0.004")
        self.assertEqual(code, 4)
        self.assertIn("the last read failed (exit 1: kaggle_sdk: 429 Too Many Requests: slow down); last status "
                      "never seen", out)
        self.assertEqual((self.beat()["state"], self.beat()["error"][:6]), ("limit", "exit 1"))

    def test_a_message_names_the_newest_submission_with_that_description(self):
        twin = sub(ref="58600000", status="COMPLETE", score="0.1", description="061:  the base",
                   date="2026-11-20T10:00:00Z")
        self.answers(listing(sub(status="COMPLETE", score="0.84512"), OLDER, twin))
        code, out, err = self.watch("--message", " 061: the  base ")
        # the line says which submission was watched: its ref, and when it was sent
        self.assertEqual((code, out), (0, f"{REF} 0.84512 COMPLETE (by message: the newest of 2 with this "
                                       "description, sent 2026-11-22T08:27:33Z)\n"), err)
        names = [p.name for p in self.store("watch").iterdir()]
        self.assertEqual(len(names), 1)
        self.assertRegex(names[0], r"^message-[0-9a-f]{10}\.json$")
        beat = json.loads(self.store("watch", names[0]).read_text())
        self.assertEqual((beat["ref"], beat["message"]), (REF, " 061: the  base "))
        # part of a description is not a match
        self.answers(listing(sub(), OLDER))
        code, out, _err = self.watch("--message", "061:", "--name", "poll061")
        self.assertEqual(code, 5)
        self.assertIn('the submission "061:" is not listed', out)
        self.assertEqual(self.beat("poll061")["exit"], 5)

    def test_the_newest_is_by_kaggles_date_not_by_the_place_in_the_list(self):
        old = sub(ref="58600000", status="COMPLETE", score="0.1", date="2026-11-20T10:00:00Z")
        new = sub(status="ERROR", error="Out of memory", date="2026-11-22T08:27:33Z")
        self.answers(listing(old, OLDER, new))
        code, out, _err = self.watch("--message", "061: the base")
        self.assertEqual((code, out), (3, f"{REF} ERROR: Out of memory (by message: the newest of 2 with this "
                                       "description, sent 2026-11-22T08:27:33Z)\n"))

    def test_an_earlier_submission_with_the_message_is_named_as_what_it_is(self):
        # right after a submit the new row may not be listed yet: the line shows the ref and date that were read
        self.answers(listing(sub(ref="58600000", status="COMPLETE", score="0.1", date="2026-11-20T10:00:00Z")))
        code, out, _err = self.watch("--message", "061: the base")
        self.assertEqual((code, out), (0, "58600000 0.1 COMPLETE (by message: the newest of 1 with this "
                                       "description, sent 2026-11-20T10:00:00Z)\n"))

    def test_the_choice_is_made_again_at_every_read(self):
        old = sub(ref="58600000", date="2026-11-20T10:00:00Z")
        self.answers(listing(old), listing(sub(status="COMPLETE", score="0.84512"),
                                           sub(ref="58600000", status="COMPLETE", score="0.1",
                                               date="2026-11-20T10:00:00Z")))
        code, out, err = self.watch("--message", "061: the base")
        self.assertEqual(code, 0, err)
        self.assertTrue(out.startswith(f"{REF} 0.84512 COMPLETE (by message: the newest of 2 "), out)

    def test_two_pending_submissions_with_the_message_cannot_be_told_apart(self):
        self.answers(listing(sub(), sub(ref="58600000", date="2026-11-22T08:20:00Z"), OLDER))
        code, out, _err = self.watch("--message", "061: the base", "--name", "twins")
        self.assertEqual((code, out), (2, '2 submissions with the description "061: the base" are still pending '
                                       f"(refs {REF}, 58600000): name one with --ref\n"))
        self.assertEqual((self.beat("twins")["state"], self.beat("twins")["exit"]), ("ambiguous", 2))
        self.assertEqual(len(self.calls()), 1)
        # with the ref it is no question, and one of them pending beside a scored one is none either
        self.answers(listing(sub(status="COMPLETE", score="0.5"), sub(ref="58600000")))
        self.assertEqual(self.watch("--ref", REF)[:2], (0, f"{REF} 0.5 COMPLETE\n"))
        self.assertEqual(self.watch("--message", "061: the base")[0], 0)


class HeartbeatTests(Watch):
    def test_a_running_watch_says_it_is_alive_and_a_stopped_one_says_so(self):
        self.answers(PENDING)
        p = subprocess.Popen([sys.executable, str(self.tools / "kaggle_watch.py"), SLUG, "--ref", REF, "--every",
                              "0.05", "--gateway", str(self.gateway)], cwd=self.root, env=self.env,
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            beat, end = {}, time.time() + 30
            while beat.get("polls", 0) < 2 and time.time() < end:
                time.sleep(0.05)
                try:
                    beat = self.beat()
                except (OSError, ValueError):
                    continue
            self.assertGreaterEqual(beat.get("polls", 0), 2)
            self.assertEqual((beat["exit"], beat["state"], beat["status"], beat["pid"]),
                             (None, "pending", "PENDING", p.pid))
            self.assertRegex(beat["next"], r"^20\d\d-\d\d-\d\dT\d\d:\d\d:\d\dZ$")
            p.send_signal(signal.SIGTERM)
            out, err = p.communicate(timeout=30)
        finally:
            if p.poll() is None:
                p.kill()
                p.communicate()
        self.assertEqual((p.returncode, out), (143, ""))
        self.assertIn("stopped by signal 15", err)
        self.assertEqual((self.beat()["state"], self.beat()["exit"], self.beat()["next"]), ("stopped", 143, None))


class UsageTests(Watch):
    def run_original(self, *args, cwd=None):
        p = subprocess.run([sys.executable, str(TOOLS / "kaggle_watch.py"), *args], cwd=cwd or self.root,
                           capture_output=True, text=True, env=self.env, timeout=60)
        return p.returncode, p.stdout, p.stderr

    def test_bad_usage_exits_2(self):
        cases = {"no submission": [SLUG], "both": [SLUG, "--ref", REF, "--message", "x"],
                 "a fast poll": [SLUG, "--ref", REF, "--every", "5"], "no limit": [SLUG, "--ref", REF, "--limit", "0"],
                 "a bad slug": ["a/b", "--ref", REF], "an empty message": [SLUG, "--message", " "],
                 "a bad name": [SLUG, "--ref", REF, "--name", "a/b"]}
        for name, args in cases.items():
            with self.subTest(name):
                self.assertEqual(self.run_original(*args)[0], 2)
        self.assertIn("--every is at least 20 seconds", self.run_original(SLUG, "--ref", REF, "--every", "5")[2])
        self.assertEqual(self.calls(), [])

    def test_two_ai_packs_are_bad_usage_not_a_crash(self):
        (self.root / "second").mkdir()
        (self.root / "second" / "manifest.json").write_text((self.root / "aipack" / "manifest.json").read_text())
        code, _out, err = self.run_original(SLUG, "--ref", REF, "--gateway", str(self.gateway))
        self.assertEqual(code, 2)
        self.assertIn("more than one ai-pack (aipack, second)", err)

    def test_an_error_of_the_tools_own_exits_1_in_one_line(self):
        # the heartbeat cannot be written: its folder's place is taken by a file
        self.store().mkdir(parents=True)
        self.store("watch").write_text("in the way")
        self.answers(SCORED)
        code, out, err = self.watch("--ref", REF)
        self.assertEqual((code, out), (1, ""))
        self.assertIn("kaggle_watch: the watch itself failed (", err)
        self.assertEqual(len(err.strip().splitlines()), 1)

    def test_outside_a_project_or_task_it_stops(self):
        code, _out, err = self.run_original(SLUG, "--ref", REF, "--gateway", str(self.gateway), cwd=self.tmp)
        self.assertEqual(code, 2)
        self.assertIn("no project or task folder here", err)
        code, _out, err = self.run_original(SLUG, "--ref", REF, "--gateway", str(self.tmp / "none.py"))
        self.assertEqual(code, 2)
        self.assertIn("gateway not found", err)

    def test_it_runs_from_a_task_folder(self):
        task = self.tmp / "task"
        task.mkdir()
        (task / "notes.md").write_text("# Notes\n\nStarted with the ad-hoc-task skill.\n")
        self.answers(SCORED)
        code, out, err = self.run_original(SLUG, "--ref", REF, "--gateway", str(self.gateway), cwd=task)
        self.assertEqual((code, out), (0, f"{REF} 0.84512 COMPLETE\n"), err)
        self.assertTrue((task / "__data" / "kaggle" / SLUG / "watch" / f"{REF}.json").is_file())


class LoopTests(Watch):
    """The loop itself, on a clock the test moves: the waits between reads."""

    def run_loop(self, reads, every=180.0, limit=3600.0, ref=REF, message=None):
        m = load(TOOLS / "kaggle_watch.py", f"watch_loop_{id(self)}")  # the tool as shipped, its real waits
        clock, waits, beats, said = [1000.0], [], [], []
        reads = list(reads)

        def read():
            r = reads.pop(0) if len(reads) > 1 else reads[0]
            return r if isinstance(r, tuple) else (r, None, False)

        def sleep(seconds):
            waits.append(seconds)
            clock[0] += seconds

        code, line = m.watch(read, ref, message, every, limit, lambda **f: beats.append(f), said.append,
                             sleep=sleep, now=lambda: clock[0])
        return code, line, waits, beats, said

    def test_failed_reads_back_off_and_a_clean_read_resets_the_wait(self):
        fail, limited = (None, "exit 1: boom", False), (None, "exit 1: 429", True)
        code, _line, waits, beats, said = self.run_loop(
            [fail, limited, fail, fail, [sub()], [sub()], fail, [sub(status="COMPLETE", score="1.0")]])
        self.assertEqual(code, 0)
        self.assertEqual(waits, [360.0, 720.0, 900.0, 900.0, 180.0, 180.0, 360.0])
        self.assertEqual([b["state"] for b in beats],
                         ["read failed", "rate-limited", "read failed", "read failed", "pending", "pending",
                          "read failed", "scored"])
        self.assertEqual(len(said), 5)

    def test_a_long_poll_interval_is_never_cut_by_the_back_off(self):
        code, _line, waits, _beats, _said = self.run_loop(
            [(None, "x", False), (None, "x", False), [sub(status="COMPLETE")]], every=1200.0, limit=7200.0)
        self.assertEqual((code, waits), (0, [1200.0, 1200.0]))

    def test_the_last_read_is_at_the_limit(self):
        code, line, waits, beats, _said = self.run_loop([[sub()]], every=180.0, limit=400.0)
        self.assertEqual(code, 4)
        self.assertEqual(waits, [180.0, 180.0, 40.0])
        self.assertIn("still pending after 6.66667 min", line)
        self.assertEqual(beats[-1]["polls"], 4)
        self.assertEqual((beats[-1]["state"], beats[-1]["exit"], beats[-1]["next"]), ("limit", 4, None))

    def test_the_confirming_read_comes_soon(self):
        code, _line, waits, _beats, _said = self.run_loop([[OLDER]])
        self.assertEqual((code, waits), (5, [30.0]))
        # a failed read in between does not count as a clean one
        code, _line, waits, _beats, _said = self.run_loop([[OLDER], (None, "x", False), [OLDER]])
        self.assertEqual((code, waits), (5, [30.0, 360.0]))
        # and a read that lists it starts the count again
        code, _line, _waits, _beats, _said = self.run_loop([[OLDER], [sub()], [OLDER], [sub(status="COMPLETE")]])
        self.assertEqual(code, 0)

    def test_a_failed_read_never_counts_toward_not_listed(self):
        fail = (None, "exit 1: boom", False)
        # one clean read without it, however many failed ones around it, is not "not listed"
        for reads in ([fail, [OLDER], [sub(status="COMPLETE")]], [fail, fail, fail, [OLDER], [sub(status="COMPLETE")]],
                      [[OLDER], fail, fail, [sub(status="COMPLETE")]]):
            code, _line, _waits, _beats, _said = self.run_loop(reads)
            self.assertEqual(code, 0, reads)
        code, _line, waits, _beats, _said = self.run_loop([fail, [OLDER], [sub(status="COMPLETE")]])
        self.assertEqual(waits, [360.0, 30.0])
        # failed reads alone end at the limit, never as "not listed"
        code, line, _waits, _beats, _said = self.run_loop([fail], limit=3600.0)
        self.assertEqual(code, 4)
        self.assertIn("the last read failed (exit 1: boom); last status never seen", line)


if __name__ == "__main__":
    unittest.main()
