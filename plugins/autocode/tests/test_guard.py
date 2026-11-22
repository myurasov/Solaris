"""Tests for shared/tools/guard.py (stdlib unittest, offline).

Every case runs the tool as its command line, in a temporary project folder, with a stand-in ``autocode`` program
first on PATH (it writes down how it was called) and no Autocode values in the environment: no real wake is ever
fired. Jobs are real processes of this machine (``sh``, ``sleep``), started and ended by the tool.

    python3 -m unittest discover -s plugins/autocode/tests -v
"""

import importlib.util
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
TOOL = os.path.join(os.path.dirname(HERE), "shared", "tools", "guard.py")

FAKE_AUTOCODE = """#!/bin/sh
# stands in for the autocode CLI: writes down its arguments, one call a line
printf '%s\\n' "$*" >> "$FAKE_LOG"
if [ -n "$FAKE_SLEEP" ]; then sleep "$FAKE_SLEEP"; fi
if [ -n "$FAKE_ERR" ]; then echo "$FAKE_ERR" >&2; fi
exit "${FAKE_EXIT:-0}"
"""


def load():
    spec = importlib.util.spec_from_file_location("guard", TOOL)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def wait_for(what, timeout=20.0, step=0.05):
    end = time.time() + timeout
    while time.time() < end:
        got = what()
        if got:
            return got
        time.sleep(step)
    raise AssertionError("timed out waiting")


class GuardCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="guard-test-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.project = os.path.join(self.tmp, "project")
        self.pack = os.path.join(self.project, "aipack")
        os.makedirs(os.path.join(self.pack, ".memory"))
        with open(os.path.join(self.pack, "manifest.json"), "w") as f:
            json.dump({"framework_version": "0.48.0", "project": {"name": "x"}}, f)
        self.state = os.path.join(self.pack, ".memory", "guard")
        self.bin = os.path.join(self.tmp, "bin")
        os.makedirs(self.bin)
        fake = os.path.join(self.bin, "autocode")
        with open(fake, "w") as f:
            f.write(FAKE_AUTOCODE)
        os.chmod(fake, 0o755)
        self.fake_log = os.path.join(self.tmp, "autocode-calls.txt")
        # a PATH without any real autocode, and no Autocode values: nothing here reaches a real server
        clean = [p for p in os.environ.get("PATH", "").split(os.pathsep)
                 if p and not os.path.exists(os.path.join(p, "autocode"))]
        self.env = {k: v for k, v in os.environ.items() if not k.startswith("AUTOCODE_")}
        self.env.update({
            "PATH": os.pathsep.join([self.bin] + clean),
            "HOME": os.path.join(self.tmp, "home"),
            "FAKE_LOG": self.fake_log,
            "AUTOCODE_GUARD_FIRE_GAP": "0",
            "AUTOCODE_GUARD_GRACE": "1",
        })
        os.makedirs(self.env["HOME"])
        self.pids = []
        self.addCleanup(self.kill_jobs)

    def kill_jobs(self):
        # only what these tests started, by the pids the tool gave then, and only while each is still that process
        g = load()
        for pid, token in self.pids:
            if token and g.proc_token(pid) == token:
                for kill in (os.killpg, os.kill):
                    try:
                        kill(pid, signal.SIGKILL)
                    except OSError:
                        pass

    def remember(self, name):
        rec = self.record(name) or {}
        g = load()
        for key in ("child_pid", "pid"):
            if rec.get(key):
                self.pids.append((rec[key], g.proc_token(rec[key])))
        return rec

    def run_tool(self, *args, env=None, cwd=None, timeout=30):
        e = dict(self.env)
        e.update(env or {})
        p = subprocess.run([sys.executable, TOOL] + list(args), cwd=cwd or self.project, env=e,
                           capture_output=True, text=True, timeout=timeout)
        return p.returncode, p.stdout, p.stderr

    def record(self, name):
        try:
            with open(os.path.join(self.state, "jobs", name + ".json")) as f:
                return json.load(f)
        except (OSError, ValueError):
            return None

    def edit(self, name, **changes):
        path = os.path.join(self.state, "jobs", name + ".json")
        with open(path) as f:
            rec = json.load(f)
        rec.update(changes)
        with open(path, "w") as f:
            json.dump(rec, f)

    def start(self, name, *cmd, wake=None, deadline="5m", env=None, more=()):
        args = ["job", "start", "--name", name, "--deadline", deadline] + list(more)
        if wake:
            args += ["--wake", wake]
        # the time limit is the proof that a start does not wait for its job
        got = self.run_tool(*(args + ["--"] + list(cmd)), env=env, timeout=15)
        self.remember(name)
        return got

    def gone(self, pgid):
        try:
            os.killpg(pgid, 0)
        except ProcessLookupError:
            return True
        except OSError:
            pass
        return False

    def ended(self, name, fired=True):
        def done():
            rec = self.record(name) or {}
            if rec.get("ended_at") is None:
                return None
            if fired and rec.get("wake") and rec.get("fired") is None:
                return None
            return rec
        return wait_for(done)

    def calls(self):
        try:
            with open(self.fake_log) as f:
                return f.read().splitlines()
        except OSError:
            return []


class TestMarker(GuardCase):
    def test_nothing_open_skips_the_run(self):
        code, out, err = self.run_tool("check")
        self.assertEqual((code, out), (1, ""), err)

    def test_open_work_wakes_and_off_ends_it(self):
        code, out, _ = self.run_tool("on", "finish", "the", "report")
        self.assertEqual(code, 0)
        self.assertIn("finish the report", out)
        self.assertTrue(os.path.isdir(self.state))
        code, out, _ = self.run_tool("check")
        self.assertEqual(code, 0)
        self.assertIn("open work: finish the report", out)
        self.assertEqual(self.run_tool("off")[0], 0)
        self.assertEqual(self.run_tool("check")[0], 1)

    def test_lines_are_kept_apart_and_one_can_end(self):
        self.run_tool("on", "the report")
        self.run_tool("on", "the upload")
        self.run_tool("on", "the report")
        out = self.run_tool("check")[1]
        self.assertEqual(out.count("the report"), 1)
        self.assertIn("the upload", out)
        self.assertEqual(self.run_tool("off", "the", "report")[0], 0)
        code, out, _ = self.run_tool("check")
        self.assertEqual(code, 0)
        self.assertNotIn("the report", out)
        self.assertIn("the upload", out)
        # a line that is not there is said, and nothing changes
        self.assertEqual(self.run_tool("off", "no such line")[0], 3)
        self.assertEqual(self.run_tool("check")[0], 0)

    def test_open_steps_in_a_file(self):
        steps = os.path.join(self.pack, ".memory", "todo.md")
        with open(steps, "w") as f:
            f.write("- [x] done\n- [ ] first\n  - [ ] nested\ntext - [ ] not a step\n"
                    "* [ ] star\n+ [ ] plus\n1. [ ] numbered\n2) [x] ticked\n"
                    "```\n- [ ] in a code block\n```\n~~~\n- [ ] in another\n~~~\n")
        code, out, _ = self.run_tool("check", "--steps", "aipack/.memory/todo.md")
        self.assertEqual(code, 0)
        self.assertIn("5 open steps in aipack/.memory/todo.md", out)
        with open(steps, "w") as f:
            f.write("- [x] done\n- [x] first\n")
        self.assertEqual(self.run_tool("check", "--steps", "aipack/.memory/todo.md")[0], 1)

    def test_a_missing_steps_file_wakes(self):
        code, out, _ = self.run_tool("check", "--steps", "aipack/.memory/none.md")
        self.assertEqual(code, 0)
        self.assertIn("aipack/.memory/none.md", out)
        self.assertIn("cannot be read", out)
        # what is no plain file is not read at all (a pipe would never end)
        code, out, _ = self.run_tool("check", "--steps", "aipack/.memory")
        self.assertEqual(code, 0)
        self.assertIn("cannot be read", out)
        fifo = os.path.join(self.tmp, "fifo")
        os.mkfifo(fifo)
        code, out, _ = self.run_tool("check", "--steps", fifo, timeout=10)
        self.assertEqual(code, 0)
        self.assertIn("cannot be read", out)

    def test_wait_holds_open_work_back_until_its_time(self):
        self.run_tool("on", "the report")
        code, out, _ = self.run_tool("wait", "30m", "a", "build", "runs")
        self.assertEqual(code, 0)
        self.assertIn("until", out)
        self.assertEqual(self.run_tool("check")[0], 1)
        # the time passed: open work counts again
        path = os.path.join(self.state, "open.json")
        with open(path) as f:
            data = json.load(f)
        data["wait_until"] = time.time() - 5
        with open(path, "w") as f:
            json.dump(data, f)
        code, out, _ = self.run_tool("check")
        self.assertEqual(code, 0)
        self.assertIn("the report", out)
        # taken back by hand
        self.run_tool("wait", "30m")
        self.assertEqual(self.run_tool("check")[0], 1)
        self.assertEqual(self.run_tool("wait", "off")[0], 0)
        self.assertEqual(self.run_tool("check")[0], 0)

    def test_off_ends_the_wait_too(self):
        self.run_tool("wait", "30m")
        self.run_tool("off")
        self.run_tool("on", "the report")
        self.assertEqual(self.run_tool("check")[0], 0)

    def test_a_wait_covers_what_was_open_when_it_was_set(self):
        self.run_tool("on", "first")
        self.run_tool("wait", "2h")
        self.run_tool("on", "second")
        code, out, _ = self.run_tool("check")
        self.assertEqual(code, 0)
        self.assertIn("second", out)
        self.assertNotIn("first", out)
        self.run_tool("off", "second")
        self.assertEqual(self.run_tool("check")[0], 1)

    def test_a_wait_has_an_end(self):
        for bad in ("25h", "0s", "soon"):
            code, _, err = self.run_tool("wait", bad)
            self.assertEqual(code, 2, bad)
            self.assertIn("wait", err)
        self.assertEqual(self.run_tool("wait", "24h")[0], 0)

    def test_open_work_told_again_and_again_with_nothing_changed_backs_off(self):
        steps = os.path.join(self.pack, ".memory", "todo.md")
        with open(steps, "w") as f:
            f.write("- [ ] first\n- [ ] second\n")
        check = ("check", "--steps", "aipack/.memory/todo.md")
        self.assertEqual(self.run_tool(*check)[0], 0)
        code, out, _ = self.run_tool(*check)
        self.assertEqual(code, 0)
        self.assertNotIn("nothing changed", out)
        # the third time in a row waits
        self.assertEqual(self.run_tool(*check)[0], 1)
        path = os.path.join(self.state, "told.json")
        with open(path) as f:
            told = json.load(f)
        told["open"]["at"] = time.time() - 21 * 60
        with open(path, "w") as f:
            json.dump(told, f)
        code, out, _ = self.run_tool(*check)
        self.assertEqual(code, 0)
        self.assertIn("nothing changed", out)
        self.assertEqual(self.run_tool(*check)[0], 1)
        # a step was ticked: the work goes on, and so does the guard
        with open(steps, "w") as f:
            f.write("- [x] first\n- [ ] second\n")
        self.assertEqual(self.run_tool(*check)[0], 0)
        self.assertEqual(self.run_tool(*check)[0], 0)
        self.assertEqual(self.run_tool(*check)[0], 1)
        # one ticked and another added: as many open as before, and still a change
        with open(steps, "w") as f:
            f.write("- [x] first\n- [x] second\n- [ ] third\n")
        self.assertEqual(self.run_tool(*check)[0], 0)
        # and marking work resets it too
        self.run_tool("on", "the report")
        self.assertEqual(self.run_tool(*check)[0], 0)

    def test_open_work_beyond_the_prompts_room_points_at_status(self):
        for n in range(30):
            self.run_tool("on", "open line number %02d with some words to fill it up to a fair length" % n)
        code, out, _ = self.run_tool("check")
        self.assertEqual(code, 0)
        self.assertLess(len(out), 1950)
        self.assertIn("status lists it", out)
        self.assertNotIn("next check", out)
        self.run_tool("off")
        self.run_tool("on", "x" * 2500)
        code, out, _ = self.run_tool("check")
        self.assertEqual(code, 0)
        self.assertLess(len(out), 1000)
        self.assertIn("...", out)

    def test_words_after_two_dashes_stay_in_the_line(self):
        self.run_tool("on", "fix", "the", "parser", "--", "then", "the", "docs")
        out = self.run_tool("status")[1]
        self.assertIn("then the docs", out)


class TestState(GuardCase):
    def test_state_folder_named_by_hand_needs_no_pack(self):
        elsewhere = os.path.join(self.tmp, "elsewhere")
        os.makedirs(elsewhere)
        state = os.path.join(self.tmp, "my-state")
        code, _, err = self.run_tool("--state", state, "on", "x", cwd=elsewhere)
        self.assertEqual(code, 0, err)
        self.assertTrue(os.path.exists(os.path.join(state, "open.json")))
        self.assertEqual(self.run_tool("--state", state, "check", cwd=elsewhere)[0], 0)
        # the project's own state is another one
        self.assertEqual(self.run_tool("check")[0], 1)
        # a state folder that is not there is more likely mistyped than new: said, not created
        typo = os.path.join(self.tmp, "my-stat")
        code, out, _ = self.run_tool("--state", typo, "check", cwd=elsewhere)
        self.assertEqual(code, 0)
        self.assertIn("does not exist", out)
        self.assertFalse(os.path.exists(typo))
        afile = os.path.join(self.tmp, "a-file")
        with open(afile, "w") as f:
            f.write("x")
        code, out, _ = self.run_tool("--state", afile, "check", cwd=elsewhere)
        self.assertEqual(code, 0)
        self.assertIn("is not a folder", out)
        # any other command makes it
        self.assertEqual(self.run_tool("--state", typo, "status", cwd=elsewhere)[0], 0)
        self.assertEqual(self.run_tool("--state", typo, "check", cwd=elsewhere)[0], 1)

    def test_dir_names_the_project_before_or_after_the_command(self):
        self.run_tool("on", "x")
        self.assertEqual(self.run_tool("--dir", self.project, "check", cwd=self.tmp)[0], 0)
        self.assertEqual(self.run_tool("check", "--dir", self.project, cwd=self.tmp)[0], 0)

    def test_a_check_that_cannot_run_wakes_with_the_reason(self):
        # no pack here and no state named: a guard that skipped quietly would never be seen again
        code, out, _ = self.run_tool("check", cwd=self.tmp)
        self.assertEqual(code, 0)
        self.assertIn("could not run", out)
        self.assertIn("ai-pack", out)
        # a check called wrongly says so too: any other exit is a failed check, which nobody reads
        code, out, _ = self.run_tool("check", "--nope")
        self.assertEqual(code, 0)
        self.assertIn("could not run", out)
        self.assertIn("--nope", out)
        # other commands fail plainly
        code, _, err = self.run_tool("on", "x", cwd=self.tmp)
        self.assertEqual(code, 2)
        self.assertIn("ai-pack", err)

    def test_status_shows_the_marker_the_jobs_and_the_last_check(self):
        self.run_tool("on", "the report")
        self.run_tool("check")
        code, out, _ = self.run_tool("status")
        self.assertEqual(code, 0)
        self.assertIn("the report", out)
        self.assertIn("last check", out)
        self.assertIn("woke", out)
        self.assertIn("no jobs", out)
        data = json.loads(self.run_tool("status", "--json")[1])
        self.assertEqual([r["text"] for r in data["open"]], ["the report"])
        self.assertEqual(data["jobs"], [])
        self.assertTrue(data["last_check"]["woke"])


class TestJobs(GuardCase):
    def test_a_job_ends_fires_its_wake_and_is_forgotten_when_acknowledged(self):
        code, out, err = self.start("build", "sh", "-c", "echo hello; exit 3", wake="build-done")
        self.assertEqual(code, 0, err)
        self.assertIn("job build started", out)
        self.assertIn("build-done", out)
        rec = self.ended("build")
        self.assertEqual((rec["exit"], rec["fired"]), (3, True))
        calls = self.calls()
        self.assertEqual(len(calls), 1)
        self.assertTrue(calls[0].startswith("fire me build-done --text "), calls)
        self.assertIn("exit 3", calls[0])
        with open(rec["log"]) as f:
            log = f.read()
        self.assertIn("hello", log)
        self.assertIn("exit 3", log)
        # its wake told the session: the guard has nothing to add
        self.assertEqual(self.run_tool("check")[0], 1)
        out = self.run_tool("job", "list")[1]
        self.assertIn("build", out)
        self.assertIn("ended, exit 3", out)
        self.assertEqual(self.run_tool("job", "done", "build")[0], 0)
        self.assertIsNone(self.record("build"))
        self.assertIn("no jobs", self.run_tool("job", "list")[1])

    def test_a_job_without_a_wake_is_told_by_the_guard_once_an_hour(self):
        self.assertEqual(self.start("quiet", "true")[0], 0)
        self.ended("quiet")
        self.assertEqual(self.calls(), [])
        code, out, _ = self.run_tool("check")
        self.assertEqual(code, 0)
        self.assertIn("job quiet ended with exit 0", out)
        self.assertIn("job done quiet", out)
        # said once: not again within the hour
        self.assertEqual(self.run_tool("check")[0], 1)
        self.assertEqual(self.run_tool("check", "--repeat", "0s")[0], 0)
        self.run_tool("job", "done", "quiet")
        self.assertEqual(self.run_tool("check", "--repeat", "0s")[0], 1)

    def test_a_wake_that_could_not_be_fired_is_told_by_the_guard(self):
        env = {"FAKE_EXIT": "1", "FAKE_ERR": "refused (not_found): no wake build-done"}
        self.assertEqual(self.start("build", "true", wake="build-done", env=env)[0], 0)
        rec = self.ended("build")
        self.assertIs(rec["fired"], False)
        # tried again before giving up
        self.assertEqual(len(self.calls()), 3)
        code, out, _ = self.run_tool("check")
        self.assertEqual(code, 0)
        self.assertIn("job build ended with exit 0", out)
        self.assertIn("build-done could not be fired", out)
        self.assertIn("no wake build-done", out)

    def test_without_the_autocode_command_the_fire_fails_and_is_told(self):
        env = {"PATH": os.pathsep.join(self.env["PATH"].split(os.pathsep)[1:])}
        self.assertEqual(self.start("build", "true", wake="build-done", env=env)[0], 0)
        rec = self.ended("build")
        self.assertIs(rec["fired"], False)
        self.assertIn("autocode", rec["fire_error"])
        self.assertEqual(self.run_tool("check")[0], 0)

    def test_a_command_that_cannot_start(self):
        self.assertEqual(self.start("bad", "/no/such/program", wake="w")[0], 0)
        rec = self.ended("bad")
        self.assertEqual(rec["exit"], 127)
        self.assertIn("/no/such/program", rec["error"])
        self.assertIn("exit 127", self.calls()[0])

    def test_a_running_job_holds_open_work_back_and_start_returns_at_once(self):
        code, out, err = self.start("long", "sleep", "60", wake="long-done")
        self.assertEqual(code, 0, err)
        rec = self.record("long")
        self.assertTrue(rec["pid"] and rec["child_pid"])
        self.run_tool("on", "the report")
        # its end wakes the session: no nudge while it runs
        self.assertEqual(self.run_tool("check")[0], 1)
        self.assertIn("running", self.run_tool("job", "list")[1])
        # the same name cannot start twice, and a running job cannot be acknowledged
        code, _, err = self.start("long", "sleep", "60")
        self.assertEqual(code, 3)
        self.assertIn("still running", err)
        self.assertEqual(self.run_tool("job", "done", "long")[0], 3)
        # stopped by the session itself: no wake is fired and the guard says nothing of it
        code, out, _ = self.run_tool("job", "stop", "long", timeout=60)
        self.assertEqual(code, 0)
        self.assertIn("long-done", out)
        rec = self.ended("long", fired=False)
        self.assertTrue(rec["stopped"])
        self.assertEqual(self.calls(), [])
        code, out, _ = self.run_tool("check")
        self.assertEqual(code, 0)
        self.assertIn("the report", out)
        self.assertNotIn("job long", out)
        self.assertFalse(load().proc_token(rec["child_pid"]))

    def test_a_job_that_was_killed_is_told(self):
        self.assertEqual(self.start("long", "sleep", "60", wake="long-done")[0], 0)
        rec = self.record("long")
        os.kill(rec["pid"], signal.SIGKILL)
        wait_for(lambda: not load().proc_token(rec["pid"]))
        code, out, _ = self.run_tool("check")
        self.assertEqual(code, 0)
        self.assertIn("job long is gone without an exit code", out)
        # its command was left behind
        self.assertIn("still runs as pid %d" % rec["child_pid"], out)
        self.assertIn("died", self.run_tool("job", "list")[1])
        self.assertEqual(self.calls(), [])
        # said once an hour
        self.assertEqual(self.run_tool("check")[0], 1)
        os.kill(rec["child_pid"], signal.SIGKILL)
        self.assertEqual(self.run_tool("job", "done", "long")[0], 0)

    def test_a_job_lost_at_a_reboot_is_told(self):
        self.assertEqual(self.start("long", "sleep", "60")[0], 0)
        rec = self.record("long")
        if not rec.get("boot"):
            self.skipTest("this machine names no boot")
        self.edit("long", boot="another-boot")
        code, out, _ = self.run_tool("check")
        self.assertEqual(code, 0)
        self.assertIn("job long was lost", out)
        self.assertIn("restarted", out)
        self.assertIn("lost", self.run_tool("job", "list")[1])
        self.edit("long", boot=rec["boot"])
        self.run_tool("job", "stop", "long", timeout=60)

    def test_a_job_past_its_deadline_is_told_until_it_is_extended(self):
        self.assertEqual(self.start("slow", "sleep", "60", deadline="1s")[0], 0)
        self.run_tool("on", "the report")
        time.sleep(1.3)
        code, out, _ = self.run_tool("check")
        self.assertEqual(code, 0)
        self.assertIn("job slow is past its deadline", out)
        self.assertIn("job extend slow", out)
        # an overdue job no longer holds open work back
        self.assertIn("the report", out)
        code, out, _ = self.run_tool("job", "extend", "slow", "1h")
        self.assertEqual(code, 0)
        self.assertEqual(self.run_tool("check", "--repeat", "0s")[0], 1)
        self.run_tool("job", "stop", "slow", timeout=60)

    def test_a_job_of_another_machine_is_not_judged_here(self):
        self.assertEqual(self.start("far", "sleep", "60")[0], 0)
        rec = self.record("far")
        self.edit("far", machine="another-machine")
        self.assertEqual(self.run_tool("check")[0], 1)
        self.assertIn("another-machine", self.run_tool("job", "list")[1])
        # its name is not taken over from here, and it is not stopped from here
        code, _, err = self.start("far", "true")
        self.assertEqual(code, 3)
        self.assertIn("another-machine", err)
        self.assertEqual(self.run_tool("job", "stop", "far")[0], 3)
        self.run_tool("on", "the report")
        self.assertEqual(self.run_tool("check")[0], 0)
        self.edit("far", machine=rec["machine"])
        self.assertEqual(self.run_tool("job", "stop", "far", timeout=60)[0], 0)

    def test_a_job_ended_from_outside_still_fires_its_wake(self):
        self.assertEqual(self.start("long", "sleep", "60", wake="long-done")[0], 0)
        rec = self.record("long")
        os.kill(rec["pid"], signal.SIGTERM)
        rec = self.ended("long")
        self.assertEqual((rec["exit"], rec["fired"], rec.get("stopped")), (143, True, False))
        self.assertIn("exit 143", self.calls()[0])

    def test_old_ended_jobs_are_forgotten(self):
        self.assertEqual(self.start("old", "true", wake="w")[0], 0)
        self.ended("old")
        self.edit("old", ended_at=time.time() - 8 * 86400)
        self.assertEqual(self.run_tool("check")[0], 1)
        self.assertIsNone(self.record("old"))

    def test_names_and_commands_are_checked(self):
        start = ("job", "start", "--deadline", "5m")
        for name in ("two words", "nl\n", "-x", ""):
            code, _, err = self.run_tool(*start, "--name", name, "--", "true")
            self.assertEqual(code, 2, repr(name))
            self.assertIn("name", err)
        code, _, err = self.run_tool(*start, "--name", "x")
        self.assertEqual(code, 2)
        self.assertIn("command", err)
        # a job that may run without end would hold the guard back without end
        code, _, err = self.run_tool("job", "start", "--name", "x", "--", "true")
        self.assertEqual(code, 2)
        self.assertIn("--deadline", err)
        for bad in ("soon", "0s"):
            code, _, err = self.run_tool("job", "start", "--name", "x", "--deadline", bad, "--", "true")
            self.assertEqual(code, 2, bad)
            self.assertIn(bad, err)
        code, _, err = self.run_tool(*start, "--name", "x", "--cwd", os.path.join(self.tmp, "none"), "--", "true")
        self.assertEqual(code, 2)
        self.assertIn("--cwd", err)
        # a start that fails leaves no job behind
        code, _, err = self.run_tool(*start, "--name", "x", "--log", self.tmp, "--", "true")
        self.assertEqual(code, 2)
        self.assertIn("no jobs", self.run_tool("job", "list")[1])
        self.assertEqual(self.run_tool("check")[0], 1)
        self.assertEqual(self.run_tool("job", "done", "none")[0], 3)
        self.assertEqual(self.run_tool("job", "extend", "none", "1h")[0], 3)

    def test_a_wrong_call_fails_plainly_though_a_word_of_it_is_check(self):
        wrong = [
            ("job", "start", "--name", "build", "--", "make", "check"),
            ("job", "start", "--name", "check", "--", "true"),
            ("job", "start", "--name", "x", "--deadline", "1h", "--bogus", "--", "make", "check"),
            ("job", "extend", "check"),
            ("--state", "check", "job", "extend"),
        ]
        for call in wrong:
            code, out, err = self.run_tool(*call)
            self.assertEqual(code, 2, call)
            self.assertNotIn("could not run", out + err)
        self.assertIn("no jobs", self.run_tool("job", "list")[1])
        # and the words of open work stay words
        self.assertEqual(self.run_tool("on", "check", "this")[0], 0)
        self.assertEqual(self.run_tool("on", "job", "start", "--", "later")[0], 0)
        out = self.run_tool("status")[1]
        self.assertIn("- check this", out)
        # (the dashes themselves go: they end the options)
        self.assertIn("- job start later", out)

    def test_a_stop_ends_what_the_command_left_behind(self):
        # the command starts a helper that takes no TERM, then waits
        script = "( trap '' TERM; exec sleep 600 ) & wait"
        self.assertEqual(self.start("forker", "sh", "-c", script, wake="w")[0], 0)
        rec = self.record("forker")
        time.sleep(0.3)
        code, out, _ = self.run_tool("job", "stop", "forker", timeout=60)
        self.assertEqual(code, 0)
        self.assertIn("stopped", out)
        self.assertTrue(self.gone(rec["child_pid"]), "a process of the stopped job still runs")
        self.assertTrue(self.ended("forker", fired=False)["stopped"])

    def test_a_command_left_without_its_supervisor_can_still_be_stopped(self):
        self.assertEqual(self.start("orphan", "sh", "-c", "trap '' TERM; sleep 600", wake="w")[0], 0)
        rec = self.record("orphan")
        time.sleep(0.3)
        os.kill(rec["pid"], signal.SIGKILL)
        wait_for(lambda: not load().proc_token(rec["pid"]))
        # not forgotten while its command runs, and no second copy of it is started
        code, _, err = self.run_tool("job", "done", "orphan")
        self.assertEqual(code, 3)
        self.assertIn("still runs", err)
        code, _, err = self.start("orphan", "sleep", "60")
        self.assertEqual(code, 3)
        self.assertIn("still runs", err)
        code, out, _ = self.run_tool("check")
        self.assertEqual(code, 0)
        self.assertIn("job stop orphan ends it", out)
        self.assertNotIn("Start it again", out)
        code, out, _ = self.run_tool("job", "stop", "orphan", timeout=60)
        self.assertEqual(code, 0)
        self.assertIn("ended", out)
        self.assertTrue(self.gone(rec["child_pid"]))
        # stopped by hand: the guard has nothing to tell, and the job can go
        self.assertEqual(self.run_tool("check", "--repeat", "0s")[0], 1)
        self.assertEqual(self.run_tool("job", "done", "orphan")[0], 0)

    def test_processes_left_by_a_dead_job_are_pointed_out(self):
        # the command leaves a helper running and ends; its supervisor was killed before that
        self.assertEqual(self.start("left", "sh", "-c", "sleep 300 & sleep 1")[0], 0)
        rec = self.record("left")
        pgid = rec["child_pid"]

        def sweep():
            try:
                os.killpg(pgid, signal.SIGKILL)
            except OSError:
                pass

        self.addCleanup(sweep)
        os.kill(rec["pid"], signal.SIGKILL)
        g = load()
        wait_for(lambda: not g.proc_token(rec["pid"]) and not g.proc_token(pgid))
        code, out, _ = self.run_tool("check")
        self.assertEqual(code, 0)
        self.assertIn("job left is gone without an exit code", out)
        self.assertIn("pgrep -l -g %d" % pgid, out)
        self.assertNotIn("Start it again", out)
        # nothing is signalled that cannot be proven to be the job's
        code, _, err = self.run_tool("job", "stop", "left")
        self.assertEqual(code, 3)
        self.assertIn("kill -- -%d" % pgid, err)
        self.assertFalse(self.gone(pgid))
        self.assertEqual(self.start("left", "true")[0], 3)
        code, out, _ = self.run_tool("job", "done", "left")
        self.assertEqual(code, 0)
        self.assertIn("pgrep -l -g %d" % pgid, out)
        sweep()

    def test_a_start_that_is_given_up_runs_nothing(self):
        # a supervisor that is slow to come up: every python started for it sleeps first
        slow = os.path.join(self.tmp, "slow")
        os.makedirs(slow)
        with open(os.path.join(slow, "sitecustomize.py"), "w") as f:
            f.write("import os, sys, time\nif '_supervise' in sys.argv:\n    time.sleep(3)\n")
        marker = os.path.join(self.tmp, "ran")
        env = {"PYTHONPATH": slow, "AUTOCODE_GUARD_START_WAIT": "1"}
        code, _, err = self.run_tool("job", "start", "--name", "slow", "--deadline", "5m", "--wake", "w", "--",
                                     "touch", marker, env=env, timeout=40)
        self.assertEqual(code, 2)
        self.assertIn("could not be started", err)
        self.assertIn("nothing of it runs", err)
        self.assertIn("no jobs", self.run_tool("job", "list")[1])
        self.assertFalse(os.path.exists(marker), "the command of a start that was reported as failed ran")
        self.assertEqual(self.calls(), [])
        # a supervisor without a record of its own start does nothing either
        code, _, _ = self.run_tool("_supervise", "slow", "no-such-run")
        self.assertEqual(code, 2)
        self.assertFalse(os.path.exists(marker))

    def test_a_record_removed_under_a_running_job_comes_back_with_its_end(self):
        self.assertEqual(self.start("gone", "sleep", "1", wake="gone-done")[0], 0)
        os.remove(os.path.join(self.state, "jobs", "gone.json"))
        rec = self.ended("gone")
        self.assertEqual((rec["exit"], rec["fired"]), (0, True))
        self.assertIn("fire me gone-done", self.calls()[0])

    def test_a_job_command_shows_the_work_goes_on(self):
        self.run_tool("on", "the report")
        self.assertEqual(self.run_tool("check")[0], 0)
        self.assertEqual(self.run_tool("check")[0], 0)
        self.assertEqual(self.run_tool("check")[0], 1)
        self.assertEqual(self.start("step", "true", wake="w")[0], 0)
        self.ended("step")
        code, out, _ = self.run_tool("check")
        self.assertEqual(code, 0)
        self.assertNotIn("nothing changed", out)

    def test_while_the_wake_is_being_fired_the_guard_waits(self):
        env = {"FAKE_SLEEP": "3"}
        self.assertEqual(self.start("slowfire", "true", wake="w", env=env)[0], 0)
        self.ended("slowfire", fired=False)
        self.assertIsNone(self.record("slowfire").get("fired"))
        # no false alarm: its wake is on its way
        self.assertEqual(self.run_tool("check")[0], 1)
        self.assertIn("firing its wake", self.run_tool("job", "list")[1])
        rec = self.ended("slowfire")
        self.assertIs(rec["fired"], True)
        self.assertEqual(self.run_tool("check")[0], 1)

    def test_a_job_forgotten_while_its_wake_is_fired_stays_forgotten(self):
        self.assertEqual(self.start("a", "true", wake="w", env={"FAKE_SLEEP": "2"})[0], 0)
        self.ended("a", fired=False)
        self.assertEqual(self.run_tool("job", "done", "a")[0], 0)
        wait_for(lambda: len(self.calls()) == 1)
        time.sleep(2.5)
        self.assertIsNone(self.record("a"))

    def test_a_new_start_is_not_written_over_by_the_old_ones_fire(self):
        env = {"FAKE_SLEEP": "2", "FAKE_EXIT": "1", "FAKE_ERR": "no wake"}
        self.assertEqual(self.start("b", "true", wake="w", env=env)[0], 0)
        self.ended("b", fired=False)
        self.assertEqual(self.start("b", "sleep", "60", wake="w")[0], 0)
        new = self.record("b")
        # the old run's three tries end
        wait_for(lambda: len(self.calls()) == 3)
        time.sleep(2.5)
        rec = self.record("b")
        self.assertEqual(rec["run"], new["run"])
        self.assertIsNone(rec.get("ended_at"))
        self.assertIsNone(rec.get("fired"))
        self.assertIn("running", self.run_tool("job", "list")[1])
        self.assertEqual(self.run_tool("job", "stop", "b", timeout=60)[0], 0)

    def test_a_record_that_cannot_be_read_is_told_and_can_be_removed(self):
        jobs = os.path.join(self.state, "jobs")
        os.makedirs(jobs)
        with open(os.path.join(jobs, "broken.json"), "w") as f:
            f.write("{ not json")
        with open(os.path.join(jobs, "odd.json"), "w") as f:
            json.dump({"name": "../victim", "cmd": ["true"], "started_at": "yesterday", "machine": "x"}, f)
        g = load()
        with open(os.path.join(jobs, "far.json"), "w") as f:
            json.dump({"name": "far", "cmd": ["true"], "log": "/dev/null", "machine": g.machine(),
                       "started_at": 1e30, "pid": None, "ended_at": None}, f)
        # started an hour from now by a clock that was set back: not "starting" for good
        with open(os.path.join(jobs, "ahead.json"), "w") as f:
            json.dump({"name": "ahead", "cmd": ["true"], "log": "/dev/null", "machine": g.machine(),
                       "boot": g.boot_id(), "started_at": time.time() + 3600, "pid": None, "ended_at": None}, f)
        # a copy that a file sync left is no job
        with open(os.path.join(jobs, "broken.sync-conflict-20261122-010101-ABCDEFG.json"), "w") as f:
            f.write("{ not json")
        victim = os.path.join(self.state, "victim.json")
        with open(victim, "w") as f:
            f.write("{}")
        code, out, _ = self.run_tool("check")
        self.assertEqual(code, 0)
        self.assertIn("broken.json", out)
        self.assertIn("odd.json", out)
        self.assertNotIn("sync-conflict", out)
        self.assertNotIn("could not run", out)
        # told once an hour, like a job's trouble
        self.assertEqual(self.run_tool("check")[0], 1)
        out = self.run_tool("job", "list")[1]
        self.assertIn("broken | damaged", out)
        self.assertIn("odd | damaged", out)
        self.assertIn("far | damaged", out)
        self.assertIn("ahead | died", out)
        self.assertEqual(self.run_tool("status")[0], 0)
        for name in ("broken", "odd", "far", "ahead"):
            self.assertEqual(self.run_tool("job", "done", name)[0], 0)
        self.assertIn("no jobs", self.run_tool("job", "list")[1])
        self.assertTrue(os.path.exists(victim))
        self.assertEqual(self.run_tool("check", "--repeat", "0s")[0], 1)

    def test_a_pid_without_its_token_is_nobodys_to_stop(self):
        victim = subprocess.Popen(["sleep", "60"], start_new_session=True)
        self.addCleanup(victim.wait)
        self.addCleanup(victim.kill)
        self.assertEqual(self.start("x", "true")[0], 0)
        self.ended("x")
        self.edit("x", ended_at=None, exit=None, pid=victim.pid, pid_token="", child_pid=victim.pid,
                  child_token="")
        self.assertIn("died", self.run_tool("job", "list")[1])
        self.assertEqual(self.run_tool("job", "stop", "x")[0], 3)
        time.sleep(0.3)
        self.assertIsNone(victim.poll(), "a process that was never the job's was signalled")

    def test_each_trouble_of_a_job_is_told_when_it_comes(self):
        env = {"FAKE_EXIT": "1", "FAKE_ERR": "no wake"}
        self.assertEqual(self.start("late", "sleep", "2", wake="w", deadline="1s", env=env)[0], 0)
        time.sleep(1.2)
        code, out, _ = self.run_tool("check")
        self.assertEqual(code, 0)
        self.assertIn("past its deadline", out)
        self.ended("late")
        # another matter than the one told a moment ago
        code, out, _ = self.run_tool("check")
        self.assertEqual(code, 0)
        self.assertIn("could not be fired", out)
        self.assertEqual(self.run_tool("check")[0], 1)

    def test_what_does_not_fit_the_prompt_is_told_the_next_time(self):
        for n in range(14):
            self.assertEqual(self.start("job-%02d" % n, "true")[0], 0)
        for n in range(14):
            self.ended("job-%02d" % n)
        self.run_tool("on", "the report")
        code, out, _ = self.run_tool("check")
        self.assertEqual(code, 0)
        self.assertLess(len(out), 1950)
        self.assertIn("more", out.splitlines()[-2] + out.splitlines()[-1])
        first = [n for n in range(14) if "job job-%02d ended" % n in out]
        self.assertTrue(0 < len(first) < 14, first)
        # the rest comes with the next check, not an hour later
        code, out, _ = self.run_tool("check")
        self.assertEqual(code, 0)
        second = [n for n in range(14) if "job job-%02d ended" % n in out]
        self.assertTrue(second and not set(first) & set(second), (first, second))

    def test_a_supervisor_killed_as_it_started_its_command_leaves_a_warning(self):
        # its own pid is written down, its command's is not: the command may run
        dead = subprocess.Popen(["true"])
        dead.wait()
        os.makedirs(os.path.join(self.state, "jobs"))
        g = load()
        with open(os.path.join(self.state, "jobs", "half.json"), "w") as f:
            json.dump({"name": "half", "run": "r", "cmd": ["sleep", "600"], "cwd": self.tmp, "log": "/dev/null",
                       "wake": None, "machine": g.machine(), "boot": g.boot_id(), "started_at": time.time() - 60,
                       "deadline_at": time.time() + 600, "pid": dead.pid, "pid_token": "long-ago",
                       "ended_at": None}, f)
        code, out, _ = self.run_tool("check")
        self.assertEqual(code, 0)
        self.assertIn("job half is gone without an exit code", out)
        self.assertIn("may run", out)
        self.assertNotIn("Start it again", out)
        for call in (("job", "start", "--name", "half", "--deadline", "5m", "--", "true"), ("job", "stop", "half")):
            code, _, err = self.run_tool(*call)
            self.assertEqual(code, 3, call)
            self.assertIn("may run", err)
        code, out, _ = self.run_tool("job", "done", "half")
        self.assertEqual(code, 0)
        self.assertIn("may run", out)
        # a start that went through always says which command it started
        self.assertEqual(self.start("whole", "sleep", "60")[0], 0)
        self.assertIn("child_pid", self.record("whole"))
        self.assertEqual(self.run_tool("job", "stop", "whole", timeout=60)[0], 0)

    def test_a_record_removed_while_its_job_is_stopped_comes_back_as_stopped(self):
        self.assertEqual(self.start("v", "sh", "-c", "trap '' TERM; sleep 600", wake="v-done")[0], 0)
        time.sleep(0.3)
        e = dict(self.env)
        stop = subprocess.Popen([sys.executable, TOOL, "job", "stop", "v"], cwd=self.project, env=e,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        time.sleep(0.4)
        os.remove(os.path.join(self.state, "jobs", "v.json"))
        out, err = stop.communicate(timeout=60)
        self.assertEqual(stop.returncode, 0, err)
        rec = self.ended("v", fired=False)
        self.assertTrue(rec["stopped"])
        time.sleep(0.5)
        self.assertEqual(self.calls(), [])
        self.assertIn("stopped", self.run_tool("job", "list")[1])

    def test_a_job_that_is_starting_says_so(self):
        os.makedirs(os.path.join(self.state, "jobs"))
        g = load()
        with open(os.path.join(self.state, "jobs", "new.json"), "w") as f:
            json.dump({"name": "new", "run": "r", "cmd": ["true"], "cwd": self.tmp, "log": "/dev/null",
                       "wake": None, "machine": g.machine(), "boot": g.boot_id(), "started_at": time.time(),
                       "deadline_at": time.time() + 60, "pid": None, "ended_at": None}, f)
        for command in ("stop", "done"):
            code, _, err = self.run_tool("job", command, "new")
            self.assertEqual(code, 3)
            self.assertIn("starting", err)

    def test_the_job_list_as_json(self):
        self.assertEqual(self.start("build", "true", wake="w")[0], 0)
        self.ended("build")
        rows = json.loads(self.run_tool("job", "list", "--json")[1])
        self.assertEqual([(r["name"], r["state"], r["exit"]) for r in rows], [("build", "ended", 0)])


class TestParts(unittest.TestCase):
    def test_durations(self):
        g = load()
        self.assertEqual(g.duration("90s"), 90)
        self.assertEqual(g.duration("10m"), 600)
        self.assertEqual(g.duration("1h30m"), 5400)
        self.assertEqual(g.duration("2d"), 172800)
        for bad in ("", "10", "soon", "1h30", "m", "-5m"):
            with self.assertRaises(ValueError):
                g.duration(bad)

    def test_this_process_has_a_token_and_a_gone_one_has_none(self):
        g = load()
        self.assertTrue(g.proc_token(os.getpid()))
        p = subprocess.Popen(["true"])
        p.wait()
        self.assertEqual(g.proc_token(p.pid), "")


if __name__ == "__main__":
    unittest.main()
