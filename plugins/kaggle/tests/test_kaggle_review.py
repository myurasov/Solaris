"""Offline tests for shared/tools/kaggle_review.py (stdlib unittest; a fake `opencode` command, no network).

    python3 -m unittest discover -s plugins/kaggle/tests -p 'test_kaggle_review.py'

The fake stands in for OpenCode: it logs how it was started (folder, files, environment names, the private
config, a hash of the key it was given, the message on its stdin), refuses a key that is not the session's, and
prints the events and the session export a test sets. On request it also writes the key into its own home, as
OpenCode's log and database can, starts a child of its own, or kills the runner. No model is ever called.
"""

import fcntl
import hashlib
import json
import os
import shutil
import signal
import subprocess
import sys
import time
import unittest
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from kaggle_standin import Project  # noqa: E402

KEY = "sk-test-0123456789abcdef-REVIEW"
OTHER_SECRET = "another-secret-value"
SHA = hashlib.sha256(KEY.encode()).hexdigest()
MODEL = "hub/vendor/reviewer-1"
# the session's OpenCode config: JSONC, with comments, trailing commas, and "//" inside a string
SESSION_CONFIG = """{
  // the owner's providers
  "provider": {
    "hub": {
      "npm": "@ai-sdk/openai-compatible",
      "name": "Model Hub",
      "options": {
        "baseURL": "https://hub.example/v1", // the endpoint
        "apiKey": "%s",
        "headers": {"X-Extra": "%s"},
        "timeout": 600000,
      },
      "models": {
        "vendor/reviewer-1": {"name": "reviewer-1", "limit": {"context": 1000, "output": 100},
                              "variants": {"high": {"reasoningEffort": "high"}, "max": {"reasoningEffort": "max"}}},
        "vendor/reviewer-2": {"name": "reviewer-2"},
        "vendor/other": {"name": "other"},
      },
    },
  },
  /* the session's own model */
  "model": "hub/vendor/master-model",
}
"""
FAKE = r'''#!%s
import hashlib, json, os, signal, subprocess, sys, time
here = os.path.dirname(os.path.abspath(__file__))
with open(os.path.join(here, "fake.json")) as f:
    ctl = json.load(f)
args = sys.argv[1:]
key = os.environ.get("KAGGLE_REVIEW_KEY", "")
sha = hashlib.sha256(key.encode()).hexdigest()


def log(rec):
    with open(os.path.join(here, "calls.jsonl"), "a") as f:
        f.write(json.dumps(rec) + "\n")


if args[:2] == ["session", "export"]:
    log({"cmd": "export", "sid": args[-1], "cwd": os.getcwd(), "key_sha256": sha,
         "own_group": os.getpgid(0) == os.getpid()})
    if ctl.get("export") is None:
        sys.exit(1)
    sys.stdout.write("a notice line\n" + json.dumps(ctl["export"]))
    sys.exit(0)
if args[:1] != ["run"]:
    sys.exit(2)
cwd = os.getcwd()
files = sorted(os.path.relpath(os.path.join(d, n), cwd) for d, _dirs, names in os.walk(cwd) for n in names)
up, parents = cwd, []
while os.path.dirname(up) != up:
    parents.append(up)
    up = os.path.dirname(up)
with open(os.path.join(os.environ["XDG_CONFIG_HOME"], "opencode", "opencode.jsonc")) as f:
    config = f.read()
real = sha == ctl["key_sha256"]
child = None
if real and ctl.get("child"):
    # something OpenCode starts itself, as its private server: it must end with the reviewer
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"]).pid
log({"cmd": "run", "args": args[1:], "prompt": sys.stdin.read(), "cwd": cwd, "env": sorted(os.environ),
     "pwd": os.environ.get("PWD"), "home": os.environ.get("HOME"), "tmpdir": os.environ.get("TMPDIR"),
     "xdg": {k: v for k, v in os.environ.items() if k.startswith("XDG_")}, "key_sha256": sha, "files": files,
     "config": config, "in_git": any(os.path.exists(os.path.join(d, ".git")) for d in parents),
     "pid": os.getpid(), "child": child, "own_group": os.getpgid(0) == os.getpid(),
     "home_mode": os.stat(os.path.dirname(os.environ["HOME"])).st_mode & 0o777})
sid = "ses_fake"
if not real:
    if ctl.get("accept_any_key"):
        print(json.dumps({"type": "text", "sessionID": sid, "part": {"id": "p0", "type": "text", "text": "OK"}}))
        sys.exit(0)
    err = ctl.get("gate_error") or {"type": "provider.auth", "status": 401, "message": "The credential is invalid."}
    print(json.dumps({"type": "error", "sessionID": sid, "error": err}))
    sys.exit(1)
if ctl.get("leak"):
    print(json.dumps({"type": "error", "sessionID": sid,
                      "error": {"type": "provider.error", "status": 400, "message": "bad key " + key}}))
    sys.stderr.write("debug: the key is " + key + "\n")
if ctl.get("leak_home"):
    # what OpenCode keeps of its own: a log, a database, its state, cache and temporary files
    for var, name in (("XDG_DATA_HOME", "opencode/log/dev.log"), ("XDG_DATA_HOME", "opencode/opencode.db"),
                      ("XDG_STATE_HOME", "opencode/state.json"), ("XDG_CACHE_HOME", "opencode/cache.bin"),
                      ("HOME", ".local/trace.txt"), ("TMPDIR", "opencode/scratch.txt")):
        path = os.path.join(os.environ[var], name)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            f.write("Authorization: Bearer " + key + "\n")
sys.stdout.flush()
sys.stderr.flush()
if ctl.get("kill_runner_for") and ctl["kill_runner_for"] in " ".join(args):
    os.kill(os.getppid(), signal.SIGKILL)
    time.sleep(20)
    sys.exit(1)
time.sleep(ctl.get("sleep", 0))
for ev in ctl.get("events", []):
    print(json.dumps(ev))
sys.stdout.flush()
sys.exit(ctl.get("code", 0))
''' % sys.executable


def text(pid, words, sid="ses_fake"):
    return {"type": "text", "sessionID": sid, "part": {"id": pid, "type": "text", "text": words}}


EVENTS = [
    {"type": "step_start", "timestamp": 1, "sessionID": "ses_fake",
     "part": {"id": "p1", "messageID": "m1", "type": "step-start"}},
    {"type": "reasoning", "sessionID": "ses_fake", "part": {"id": "p2", "type": "reasoning", "text": "thinking"}},
    text("p3", "VERDICT: TAKE WITH"),
    text("p3", "VERDICT: TAKE WITH FIXES"),  # the same part sent again, whole
    text("p9", "a child session's words", sid="ses_child"),
    text("p4", "1. high: the split leaks."),
]
EXPORT = {"info": {"id": "ses_fake"}, "messages": [
    {"type": "user", "text": "the questions"},
    {"type": "assistant", "id": "m1", "cost": 0.1,
     "tokens": {"input": 1000, "output": 200, "reasoning": 50, "cache": {"read": 300, "write": 20}},
     "content": [{"type": "tool", "name": "read", "state": {"input": {"filePath": "STATE.md"}}},
                 {"type": "tool", "name": "read", "state": {"input": {"filePath": "/etc/hostname"},
                                                            "error": {"type": "permission.rejected"}}},
                 {"type": "text", "text": "VERDICT: TAKE WITH FIXES"}]},
    {"type": "assistant", "id": "m2", "cost": 0.0578,
     "tokens": {"input": 500, "output": 100, "reasoning": 0, "cache": {"read": 0, "write": 0}}, "content": []},
]}


class Review(Project):
    KEY = KEY

    def setUp(self):
        super().setUp()
        self.session_config = self.tmp / "session" / "opencode.jsonc"
        self.session_config.parent.mkdir()
        self.session_config.write_text(SESSION_CONFIG % (json.dumps(self.KEY)[1:-1], OTHER_SECRET))
        bin_dir = self.tmp / "bin"
        bin_dir.mkdir()
        self.fake = bin_dir / "opencode"
        self.fake.write_text(FAKE)
        self.fake.chmod(0o755)
        self.control()
        self.scratch = self.tmp / "tmp"
        self.scratch.mkdir()
        # what a calling session carries and a reviewer must never get
        self.env.update(TMPDIR=str(self.scratch), AUTOCODE_TOKEN="autocode-token", AUTOCODE_URL="http://x",
                        OPENCODE_SESSION_ID="ses_caller", HF_TOKEN="hf-token", KAGGLE_API_TOKEN="kaggle-token")
        for name, words in {"prompt.md": 'Attack this plan. Say "TAKE" or "REJECT".\n',
                            "research/plan.md": "# Plan\nShip the base.\n",
                            "source/train.py": "print('train')\n"}.items():
            self.write(name, words)

    # ---- fixtures

    def control(self, **changes):
        ctl = {"key_sha256": hashlib.sha256(self.KEY.encode()).hexdigest(), "events": EVENTS, "export": EXPORT,
               **changes}
        (self.fake.parent / "fake.json").write_text(json.dumps(ctl))

    def write(self, name, words):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(words)
        return path

    def settings(self, **values):
        (self.root / "aipack" / "defaults.json").write_text(json.dumps(values))

    def package(self, name="1122-plan-review", ready="2026-11-22", files="research/plan.md\n\n# the code\nsource\n"):
        folder = self.root / "__out" / "review" / name
        for part in ("prompt.md", "STATE.md", "files.txt", "READY"):
            (folder / part).unlink(missing_ok=True)
        self.write(f"__out/review/{name}/prompt.md", "Attack the day's plan.\n")
        self.write(f"__out/review/{name}/STATE.md", "# State\nWe are at 0.84.\n")
        if files is not None:
            self.write(f"__out/review/{name}/files.txt", files)
        if ready is not None:
            self.write(f"__out/review/{name}/READY", ready + "\n")
        return folder

    def command(self, *args, model=MODEL):
        extra = ("--model", model) if model else ()
        return [sys.executable, str(self.tools / "kaggle_review.py"), *args, *extra, "--opencode", str(self.fake),
                "--session-config", str(self.session_config)]

    def review(self, *args, model=MODEL):
        p = subprocess.run(self.command(*args, model=model), cwd=self.root, capture_output=True, text=True,
                           env=self.env, timeout=120)
        return p.returncode, p.stdout, p.stderr

    def start(self, *args):
        """A run in the background, once its reviewer has started: (the runner, the reviewer's log line)."""
        p = subprocess.Popen(self.command(*args), cwd=self.root, env=self.env, stdout=subprocess.PIPE,
                             stderr=subprocess.PIPE, text=True)
        self.addCleanup(lambda: p.poll() is None and p.kill())
        end = time.time() + 30
        while not self.real_runs() and time.time() < end and p.poll() is None:
            time.sleep(0.05)
        self.assertTrue(self.real_runs(), "the reviewer did not start")
        return p, self.real_runs()[-1]

    def gone(self, pid, seconds=10):
        """Whether a process has ended, waiting a little for it."""
        end = time.time() + seconds
        while time.time() < end:
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                return True
            try:
                with open(f"/proc/{pid}/stat") as f:
                    if f.read().rsplit(")", 1)[1].split()[0] == "Z":
                        return True  # ended; only its parent has not collected it yet
            except (OSError, IndexError):
                pass
            time.sleep(0.05)
        return False

    def fake_calls(self, cmd="run"):
        log = self.fake.parent / "calls.jsonl"
        calls = [json.loads(x) for x in log.read_text().splitlines()] if log.exists() else []
        return [c for c in calls if c["cmd"] == cmd]

    def real_runs(self):
        sha = hashlib.sha256(self.KEY.encode()).hexdigest()
        return [c for c in self.fake_calls() if c["key_sha256"] == sha]

    def gate_runs(self):
        sha = hashlib.sha256(self.KEY.encode()).hexdigest()
        return [c for c in self.fake_calls() if c["key_sha256"] != sha]

    def ledger(self):
        path = self.root / "aipack" / ".memory" / "spend.jsonl"
        return [json.loads(x) for x in path.read_text().splitlines()] if path.exists() else []

    def out(self, label, name="review.md"):
        return self.root / "__out" / "review" / label / name

    def data(self, *parts):
        return self.root / "__data" / "kaggle" / "review" / Path(*parts)

    def assert_key_nowhere(self, *outputs):
        """The key is in no file of the test tree but the session's own config, and in no output: not as it is,
        and not as JSON text writes it."""
        forms = {self.KEY, json.dumps(self.KEY)[1:-1], json.dumps(self.KEY, ensure_ascii=False)[1:-1]}
        for words in outputs:
            for form in forms:
                self.assertNotIn(form, words)
        for path in self.tmp.rglob("*"):
            if path.is_file() and not path.is_symlink() and path != self.session_config:
                data = path.read_bytes()
                for form in forms:
                    self.assertNotIn(form.encode(), data, f"the key is in {path}")

    def assert_nothing_left(self):
        """No throwaway folder and no record of a run in progress."""
        self.assertEqual(list(self.scratch.iterdir()), [])
        self.assertEqual(list(self.data("running").glob("*")), [])


class RunTests(Review):
    def test_a_review_is_written_with_its_record_and_one_ledger_line(self):
        code, out, err = self.review("r1", "prompt.md", "research/plan.md", "--variant", "high")
        self.assertEqual(code, 0, out + err)
        self.assertEqual(self.out("r1").read_text(), "VERDICT: TAKE WITH FIXES\n\n1. high: the split leaks.\n")
        last = out.strip().splitlines()[-1]
        self.assertTrue(last.startswith("kaggle_review r1: review written, 2 text part(s), 1820 tokens in"), last)
        self.assertIn("$0.1578 (opencode estimate)", last)
        self.assertTrue(last.endswith("-> __out/review/r1/review.md"), last)
        day = datetime.now().astimezone().date().isoformat()
        self.assertEqual(self.ledger(), [{
            "day": day, "category": "outside reviews", "usd": 0.1578,
            "what": "opencode review r1 (hub/vendor/reviewer-1, high; opencode estimate)"}])
        rec = json.loads(self.out("r1", "run.json").read_text())
        self.assertEqual((rec["exit"], rec["opencode_exit"], rec["steps"], rec["denied_calls"]), (0, 0, 2, 1))
        self.assertEqual((rec["day"], rec["id"], rec["package"], rec["ready"], rec["review"]),
                         (day, "r1", None, None, "__out/review/r1/review.md"))
        self.assertEqual(rec["tokens"], {"input": 1500, "output": 300, "reasoning": 50, "cache_read": 300,
                                         "cache_write": 20})
        self.assertEqual(rec["tools"], {"read": 2})
        # the read of a path outside the reviewer's folder is named, and warned about
        self.assertEqual(rec["outside_paths"], ["/etc/hostname"])
        self.assertIn("WARNING: the reviewer pointed a tool at 1 path(s) outside its folder", err)

    def test_the_prompt_comes_in_on_stdin_never_on_the_command_line(self):
        code, out, err = self.review("r1", "prompt.md", "research/plan.md", "--variant", "high")
        self.assertEqual(code, 0, out + err)
        run = self.real_runs()[0]
        self.assertEqual(run["args"], ["--standalone", "--thinking", "-m", MODEL + "#high", "--agent", "review",
                                       "--format", "json", "--title", "review r1"])
        # as the file has it, quotes and all
        self.assertEqual(run["prompt"], 'Attack this plan. Say "TAKE" or "REJECT".\n')

    def test_the_reviewer_works_on_copies_in_a_folder_outside_every_repository(self):
        (self.root / ".git").mkdir()
        self.write("AGENTS.md", "Project rules for the team's own agents.\n")
        self.write("__out/review/r1/STATE.md", "# State\n")
        code, out, err = self.review("r1", "prompt.md", "__out/review/r1/STATE.md", "research/plan.md", "source")
        self.assertEqual(code, 0, out + err)
        run = self.real_runs()[0]
        # a file of the review's own folder lands at the top; the others keep their project paths
        self.assertEqual(run["files"], ["STATE.md", "research/plan.md", "source/train.py"])
        self.assertEqual(self.out("r1", "copies.txt").read_text().split(), run["files"])
        self.assertFalse(run["in_git"])
        self.assertTrue(run["cwd"].startswith(str(self.scratch / "kaggle-review-r1-")), run["cwd"])
        self.assertEqual(run["pwd"], run["cwd"])
        # the export ran in the reviewer's folder too
        self.assertEqual(self.fake_calls("export")[0]["cwd"], run["cwd"])
        # the folder is gone after the run
        self.assert_nothing_left()

    def test_opencode_gets_a_home_of_its_own_for_the_run_and_its_own_process_group(self):
        code, out, err = self.review("r1", "prompt.md", "research/plan.md")
        self.assertEqual(code, 0, out + err)
        for run in self.fake_calls():
            # nothing of the caller's home, config or sessions: every folder OpenCode writes to is in one
            # throwaway folder, apart from the reviewer's and outside the project
            home = Path(run["home"]).parent
            self.assertTrue(home.name.startswith("kaggle-review-home-"), home)
            self.assertEqual(home.parent, self.scratch)
            self.assertEqual(run["xdg"], {"XDG_CONFIG_HOME": str(home / "config"), "XDG_DATA_HOME": str(home / "data"),
                                          "XDG_STATE_HOME": str(home / "state"),
                                          "XDG_CACHE_HOME": str(home / "cache")})
            self.assertEqual((run["tmpdir"], run["home_mode"]), (str(home / "tmp"), 0o700))
            self.assertNotEqual(Path(run["cwd"]), home)
            self.assertNotIn(home, Path(run["cwd"]).parents)
            self.assertTrue(run["own_group"])
        # the gate and the review each had their own, and none is left; the project keeps no OpenCode state
        self.assertEqual(len({c["home"] for c in self.fake_calls()}), 2)
        self.assert_nothing_left()
        self.assertEqual(sorted(p.name for p in self.data().iterdir()), [".lock", "gate", "gates.json", "running"])

    def test_the_child_gets_the_key_and_no_other_variable_of_the_session(self):
        code, out, err = self.review("r1", "prompt.md", "research/plan.md")
        self.assertEqual(code, 0, out + err)
        run = self.real_runs()[0]
        self.assertIn("KAGGLE_REVIEW_KEY", run["env"])
        for name in run["env"]:
            self.assertFalse(name.startswith(("AUTOCODE", "OPENCODE")) or name in ("HF_TOKEN", "KAGGLE_API_TOKEN",
                                                                                    "KAGGLE_SHARE_DIR"), name)
        # reading the session back needs no key, and gets none
        export = self.fake_calls("export")[0]
        self.assertNotEqual(export["key_sha256"], run["key_sha256"])
        self.assertTrue(export["own_group"])
        self.assert_key_nowhere(out, err)

    def test_the_key_is_cut_from_what_opencode_echoes(self):
        self.control(leak=True)
        code, out, err = self.review("r1", "prompt.md", "research/plan.md")
        self.assertEqual(code, 0, out + err)
        self.assertIn("[key removed]", self.out("r1", "events.jsonl").read_text())
        self.assertIn("[key removed]", self.out("r1", "stderr.log").read_text())
        self.assertIn("the key was echoed into events.jsonl, stderr.log and cut from there", err)
        self.assert_key_nowhere(out, err)

    def test_what_opencode_writes_into_its_own_home_does_not_stay(self):
        self.control(leak_home=True)
        code, out, err = self.review("r1", "prompt.md", "research/plan.md")
        self.assertEqual(code, 0, out + err)
        self.assert_nothing_left()
        self.assert_key_nowhere(out, err)
        # also after a run that failed
        self.control(leak_home=True, leak=True, code=1)
        code, out, err = self.review("r2", "prompt.md", "research/plan.md")
        self.assertEqual(code, 1, out + err)
        self.assert_nothing_left()
        self.assert_key_nowhere(out, err)

    def test_the_private_config_denies_every_tool_but_reads(self):
        code, out, err = self.review("r1", "prompt.md", "research/plan.md")
        self.assertEqual(code, 0, out + err)
        cfg = json.loads(self.real_runs()[0]["config"])
        rules = cfg["permissions"]
        self.assertEqual(rules[0], {"action": "*", "resource": "*", "effect": "deny"})
        self.assertEqual([r["action"] for r in rules if r["effect"] == "allow"], ["read", "glob", "grep", "list"])
        # after the allows, the last word on each of these is a deny
        last = {r["action"]: r["effect"] for r in rules}
        for tool in ("edit", "write", "shell", "bash", "webfetch", "websearch", "task", "subagent",
                     "external_directory"):
            self.assertEqual(last[tool], "deny", tool)
        self.assertEqual(cfg["agents"]["review"]["permissions"], rules)
        self.assertEqual(cfg["default_agent"], "review")
        self.assertEqual(cfg["experimental"]["policies"], [
            {"action": "provider.use", "resource": "*", "effect": "deny"},
            {"action": "provider.use", "resource": "hub", "effect": "allow"}])
        self.assertEqual((cfg["websearch"], cfg["share"], cfg["update"], cfg["snapshots"]),
                         (False, "disabled", "disable", False))
        # the session's provider, with the one model, the key as a placeholder and no other secret
        hub = cfg["provider"]["hub"]
        self.assertEqual(list(cfg["provider"]), ["hub"])
        self.assertEqual(list(hub["models"]), ["vendor/reviewer-1"])
        self.assertEqual(hub["options"], {"baseURL": "https://hub.example/v1", "apiKey": "{env:KAGGLE_REVIEW_KEY}",
                                          "timeout": 600000})
        self.assertEqual(cfg["model"], MODEL)
        self.assertNotIn(OTHER_SECRET, self.real_runs()[0]["config"])

    def test_a_review_has_a_step_limit(self):
        self.assertEqual(self.review("r1", "prompt.md", "research/plan.md")[0], 0)
        self.assertEqual(json.loads(self.real_runs()[0]["config"])["agents"]["review"]["steps"], 150)
        self.assertEqual(json.loads(self.out("r1", "run.json").read_text())["step_limit"], 150)
        # the fake's session took two steps: at a limit of two the review may be cut short, and says so
        self.settings(**{"kaggle.review.max_steps": 2})
        code, out, err = self.review("r2", "prompt.md", "research/plan.md")
        self.assertEqual(code, 0, out + err)
        self.assertEqual(json.loads(self.real_runs()[1]["config"])["agents"]["review"]["steps"], 2)
        self.assertIn("WARNING: the reviewer reached its step limit (2, kaggle.review.max_steps)", err)
        self.settings(**{"kaggle.review.max_steps": 0})
        self.assertEqual(self.review("r3", "prompt.md", "research/plan.md")[0], 2)

    def test_an_env_placeholder_in_the_session_config_is_resolved(self):
        self.session_config.write_text(SESSION_CONFIG % ("{env:HUB_KEY}", OTHER_SECRET))
        self.assertEqual(self.review("r1", "prompt.md", "research/plan.md")[0], 4)
        self.assertEqual(self.fake_calls(), [])
        self.env["HUB_KEY"] = KEY
        code, out, err = self.review("r1", "prompt.md", "research/plan.md")
        self.assertEqual(code, 0, out + err)
        self.assertEqual(self.real_runs()[0]["key_sha256"], SHA)
        self.assertNotIn("HUB_KEY", self.real_runs()[0]["env"])

    def test_settings_name_the_reviewer_the_category_and_the_day(self):
        self.settings(**{"kaggle.review.model": "hub/vendor/reviewer-1", "kaggle.review.variant": "max",
                         "kaggle.review.category": "outside reviews", "owner.timezone": "Pacific/Kiritimati"})
        code, out, err = self.review("r1", "prompt.md", "research/plan.md", model=None)
        self.assertEqual(code, 0, out + err)
        self.assertIn(MODEL + "#max", self.real_runs()[0]["args"])
        line = self.ledger()[0]
        self.assertEqual(line["category"], "outside reviews")
        # the owner's day, not this machine's or UTC's
        from zoneinfo import ZoneInfo
        self.assertEqual(line["day"], datetime.now(ZoneInfo("Pacific/Kiritimati")).date().isoformat())
        self.assertEqual(line["what"], "opencode review r1 (hub/vendor/reviewer-1, max; opencode estimate)")

    def test_out_names_the_review_file(self):
        code, out, err = self.review("r1", "prompt.md", "research/plan.md", "--out", "reviews/first/sol.md")
        self.assertEqual(code, 0, out + err)
        self.assertTrue((self.root / "reviews" / "first" / "sol.md").read_text().startswith("VERDICT"))
        self.assertTrue((self.root / "reviews" / "first" / "run.json").is_file())
        self.assertFalse(self.out("r1").exists())

    def test_a_second_run_of_the_same_review_is_refused_while_the_first_holds_it(self):
        self.assertEqual(self.review("--gate")[0], 0)
        self.out("r1").parent.mkdir(parents=True)
        with open(self.out("r1", ".lock"), "w") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            code, out, _err = self.review("r1", "prompt.md", "research/plan.md")
        self.assertEqual(code, 1)
        self.assertIn("FAILED (another run of this review holds __out/review/r1/.lock); nothing run", out)
        self.assertEqual(self.real_runs(), [])


class AlreadyThereTests(Review):
    def test_a_review_that_is_there_is_not_run_again(self):
        self.assertEqual(self.review("r1", "prompt.md", "research/plan.md")[0], 0)
        code, out, _err = self.review("r1", "prompt.md", "research/plan.md")
        self.assertEqual(code, 0)
        self.assertIn("kaggle_review r1: __out/review/r1/review.md exists already; not run again", out)
        self.assertEqual(len(self.real_runs()), 1)
        self.assertEqual(len(self.ledger()), 1)

    def test_a_forced_run_keeps_the_earlier_review_under_a_dated_name(self):
        self.assertEqual(self.review("r1", "prompt.md", "research/plan.md")[0], 0)
        day = datetime.now().astimezone().date().isoformat()
        self.control(events=[text("p1", "VERDICT: REJECT")])
        code, out, err = self.review("r1", "prompt.md", "research/plan.md", "--force")
        self.assertEqual(code, 0, out + err)
        self.assertIn(f"the earlier review is kept as __out/review/r1/review-{day}.md", err)
        self.assertEqual(self.out("r1").read_text(), "VERDICT: REJECT\n")
        self.assertTrue(self.out("r1", f"review-{day}.md").read_text().startswith("VERDICT: TAKE WITH FIXES"))
        kept = json.loads(self.out("r1", f"run-{day}.json").read_text())
        self.assertEqual((kept["exit"], kept["review"]), (0, f"__out/review/r1/review-{day}.md"))
        # and a third run overwrites neither
        self.assertEqual(self.review("r1", "prompt.md", "research/plan.md", "--force")[0], 0)
        self.assertEqual(self.out("r1", f"review-{day}-2.md").read_text(), "VERDICT: REJECT\n")
        self.assertTrue(self.out("r1", f"run-{day}-2.json").is_file())
        self.assertEqual(len(self.real_runs()), 3)

    def test_a_review_file_without_the_record_of_a_good_run_is_not_taken_as_the_review(self):
        cases = {"no record": None, "a failed run's": {"exit": 1, "id": "r1", "review": "__out/review/r1/review.md"},
                 "another review's": {"exit": 0, "id": "r9", "review": "__out/review/r1/review.md"}}
        for n, (name, record) in enumerate(cases.items(), 1):
            with self.subTest(name):
                shutil.rmtree(self.out("r1").parent, ignore_errors=True)
                self.write("__out/review/r1/review.md", "Words somebody left here.\n")
                if record:
                    self.write("__out/review/r1/run.json", json.dumps(record))
                code, out, err = self.review("r1", "prompt.md", "research/plan.md")
                self.assertEqual(code, 0, out + err)
                self.assertEqual(len(self.real_runs()), n)
                self.assertTrue(self.out("r1").read_text().startswith("VERDICT"))
                kept = [p for p in self.out("r1").parent.glob("review-*.md")]
                self.assertEqual([p.read_text() for p in kept], ["Words somebody left here.\n"])

    def test_a_package_folder_used_again_the_next_day_is_reviewed_again(self):
        self.package("plan", ready="2026-11-22")
        code, out, err = self.review("--package", "__out/review/plan", "--day", "2026-11-22")
        self.assertEqual(code, 0, out + err)
        rec = json.loads(self.out("plan", "run.json").read_text())
        self.assertEqual((rec["ready"], rec["package"]), ("2026-11-22", "__out/review/plan"))
        # the same day again: the review is there
        code, out, _err = self.review("--package", "__out/review/plan", "--day", "2026-11-22")
        self.assertEqual(code, 0)
        self.assertIn("__out/review/plan/review.md exists already; not run again (it reviewed the package of "
                      "2026-11-22; --force runs it again)", out)
        self.assertEqual(len(self.real_runs()), 1)
        # the next day the master wrote a new state and READY into the same folder
        self.package("plan", ready="2026-11-23")
        self.control(events=[text("p1", "VERDICT: REJECT (day two)")])
        code, out, err = self.review("--package", "__out/review/plan", "--day", "2026-11-23")
        self.assertEqual(code, 0, out + err)
        self.assertIn("kaggle_review plan: review written", out)
        self.assertEqual(len(self.real_runs()), 2)
        self.assertEqual(self.out("plan").read_text(), "VERDICT: REJECT (day two)\n")
        # the first day's review keeps its day in its name, with its record
        self.assertTrue(self.out("plan", "review-2026-11-22.md").read_text().startswith("VERDICT: TAKE WITH FIXES"))
        self.assertEqual(json.loads(self.out("plan", "run-2026-11-22.json").read_text())["ready"], "2026-11-22")
        self.assertEqual(json.loads(self.out("plan", "run.json").read_text())["ready"], "2026-11-23")
        self.assertIn("the earlier review is kept as __out/review/plan/review-2026-11-22.md", err)
        # yesterday's READY is still refused for today
        self.package("plan", ready="2026-11-23")
        self.assertEqual(self.review("--package", "__out/review/plan", "--day", "2026-11-24")[0], 3)
        self.assertEqual(len(self.real_runs()), 2)

    def test_without_a_day_a_changed_ready_is_a_new_package_too(self):
        self.package("plan", ready="first")
        self.assertEqual(self.review("--package", "__out/review/plan")[0], 0)
        self.assertEqual(self.review("--package", "__out/review/plan")[0], 0)
        self.assertEqual(len(self.real_runs()), 1)
        self.package("plan", ready="second")
        self.assertEqual(self.review("--package", "__out/review/plan")[0], 0)
        self.assertEqual(len(self.real_runs()), 2)
        self.assertEqual(len(list(self.out("plan").parent.glob("review-*.md"))), 1)


class StopTests(Review):
    def test_a_stopped_runner_ends_its_reviewer_and_everything_it_started(self):
        for sig, code in ((signal.SIGTERM, 143), (signal.SIGHUP, 129), (signal.SIGINT, 130)):
            with self.subTest(sig.name):
                (self.fake.parent / "calls.jsonl").unlink(missing_ok=True)
                self.control(sleep=120, child=True, leak=True, leak_home=True)
                p, run = self.start(f"r-{sig.name}", "prompt.md", "research/plan.md")
                time.sleep(0.3)
                t0 = time.time()
                p.send_signal(sig)
                out, err = p.communicate(timeout=30)
                self.assertEqual(p.returncode, code, out + err)
                # well inside the three seconds a shell tool gives a command after TERM
                self.assertLess(time.time() - t0, 2.5)
                self.assertEqual(out.strip().splitlines()[-1], f"kaggle_review: stopped by signal {int(sig)}: the "
                                 "reviewer was ended and its folders removed")
                self.assertTrue(self.gone(run["pid"]), "the reviewer is still running")
                self.assertTrue(self.gone(run["child"]), "what the reviewer started is still running")
                self.assert_nothing_left()
                self.assert_key_nowhere(out, err)
                self.assertFalse(self.out(f"r-{sig.name}").exists())

    def test_a_reviewer_over_its_time_limit_is_ended_with_what_it_started(self):
        self.control(sleep=120, child=True)
        code, out, err = self.review("r1", "prompt.md", "research/plan.md", "--timeout", "1")
        self.assertEqual(code, 1, out + err)
        self.assertIn("FAILED (timed out after 1 s)", out)
        rec = json.loads(self.out("r1", "run.json").read_text())
        self.assertEqual((rec["timed_out"], rec["opencode_exit"]), (True, 124))
        run = self.real_runs()[0]
        self.assertTrue(self.gone(run["pid"]) and self.gone(run["child"]))
        self.assert_nothing_left()

    def test_what_the_reviewer_started_does_not_outlive_a_run_that_ended_well(self):
        self.control(child=True)
        code, out, err = self.review("r1", "prompt.md", "research/plan.md")
        self.assertEqual(code, 0, out + err)
        self.assertTrue(self.gone(self.real_runs()[0]["child"]))

    @unittest.skipUnless(sys.platform == "linux", "the kernel's tie to the runner is Linux's prctl")
    def test_a_runner_killed_outright_takes_its_reviewer_with_it_and_the_next_run_cleans_up(self):
        self.control(sleep=120, leak_home=True)
        p, run = self.start("r1", "prompt.md", "research/plan.md")
        time.sleep(0.3)
        p.kill()
        p.communicate(timeout=30)
        self.assertEqual(p.returncode, -signal.SIGKILL)
        # the kernel ends the reviewer; nobody was left to remove the folders
        self.assertTrue(self.gone(run["pid"]), "the reviewer outlived its runner")
        left = sorted(d.name for d in self.scratch.iterdir())
        self.assertEqual(len(left), 2, left)
        self.assertEqual(len(list(self.data("running").glob("*.json"))), 1)
        # the lock is free, and the next run of the tool removes what was left before it starts its own
        self.control()
        code, out, err = self.review("r2", "prompt.md", "research/plan.md")
        self.assertEqual(code, 0, out + err)
        self.assert_nothing_left()
        self.assert_key_nowhere(out, err)

    def test_folders_of_a_run_that_is_alive_or_of_another_machine_are_left_alone(self):
        m = self.module("kaggle_review")
        review_dir = self.data()
        mine = self.scratch / "kaggle-review-alive-x"
        other = self.scratch / "kaggle-review-elsewhere-x"
        stray = self.scratch / "not-a-run-folder"
        dead = self.scratch / "kaggle-review-dead-x"
        for d in (mine, other, stray, dead):
            d.mkdir()
        gone = subprocess.Popen([sys.executable, "-c", "pass"])
        gone.wait()
        records = {"alive": {"pid": os.getpid(), "born": m.born(os.getpid()), "host": m.socket.gethostname(),
                             "folders": [str(mine)]},
                   "elsewhere": {"pid": gone.pid, "born": "1", "host": "another-machine", "folders": [str(other)]},
                   "dead": {"pid": gone.pid, "born": "1", "host": m.socket.gethostname(),
                            "folders": [str(dead), str(stray), str(self.root)]}}
        for name, rec in records.items():
            path = review_dir / "running" / f"{name}.json"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(rec))
        m.sweep(review_dir)
        # only the dead run's own throwaway folder went: never a folder with another name, never the project
        self.assertEqual(sorted(d.name for d in self.scratch.iterdir()),
                         ["kaggle-review-alive-x", "kaggle-review-elsewhere-x", "not-a-run-folder"])
        self.assertTrue((self.root / "aipack").is_dir())
        self.assertEqual(sorted(p.stem for p in (review_dir / "running").glob("*.json")), ["alive", "elsewhere"])


class OtherKeyTests(Review):
    # a key that JSON text writes otherwise: a quote, a backslash, a letter outside ASCII
    KEY = 'sk-"quoted"\\back-é-0123456789'

    def test_such_a_key_is_cut_in_every_form(self):
        self.control(leak=True, leak_home=True)
        code, out, err = self.review("r1", "prompt.md", "research/plan.md")
        self.assertEqual(code, 0, out + err)
        self.assertEqual(self.real_runs()[0]["key_sha256"], hashlib.sha256(self.KEY.encode()).hexdigest())
        events = self.out("r1", "events.jsonl").read_text()
        self.assertIn("bad key [key removed]", events)
        self.assertIn("the key is [key removed]", self.out("r1", "stderr.log").read_text())
        self.assert_key_nowhere(out, err)


class FailureTests(Review):
    def test_no_text_is_a_failure(self):
        self.control(events=EVENTS[:2])
        code, out, err = self.review("r1", "prompt.md", "research/plan.md")
        self.assertEqual(code, 1, out + err)
        self.assertIn("kaggle_review r1: FAILED (no review text)", out.strip().splitlines()[-1])
        self.assertFalse(self.out("r1").exists())
        self.assertFalse(self.out("r1", "review.partial.md").exists())
        # the run cost money all the same
        self.assertEqual(len(self.ledger()), 1)
        self.assertEqual(json.loads(self.out("r1", "run.json").read_text())["exit"], 1)
        # and the next call runs the review: a failed run is not "already there"
        self.control()
        self.assertEqual(self.review("r1", "prompt.md", "research/plan.md")[0], 0)
        self.assertEqual(len(self.real_runs()), 2)

    def test_a_failed_run_keeps_its_text_apart(self):
        self.write("__out/review/r1/review.md", "\n")  # what an earlier failed run could leave: no text
        self.control(code=1)
        code, out, err = self.review("r1", "prompt.md", "research/plan.md")
        self.assertEqual(code, 1, out + err)
        self.assertIn("FAILED (OpenCode exit 1)", out)
        self.assertIn("partial text in __out/review/r1/review.partial.md", out)
        self.assertFalse(self.out("r1").exists())
        self.assertTrue(self.out("r1", "review.partial.md").read_text().startswith("VERDICT"))

    def test_a_failed_export_leaves_the_cost_unknown(self):
        self.control(export=None)
        code, out, err = self.review("r1", "prompt.md", "research/plan.md")
        self.assertEqual(code, 0, out + err)
        self.assertIn("(cost unknown: the session export failed)", out)
        self.assertEqual(self.ledger()[0]["usd"], 0)
        self.assertIn("cost unknown: the session export failed", self.ledger()[0]["what"])

    def test_no_provider_or_key_runs_nothing(self):
        cases = {"no config": lambda: self.session_config.unlink(),
                 "not json": lambda: self.session_config.write_text('{"provider": ' + KEY),
                 "no provider": lambda: self.session_config.write_text('{"provider": {"else": {}}}'),
                 "no key": lambda: self.session_config.write_text(
                     '{"provider": {"hub": {"options": {}, "models": {"vendor/reviewer-1": {}}}}}')}
        for name, change in cases.items():
            with self.subTest(name):
                self.session_config.write_text(SESSION_CONFIG % (KEY, OTHER_SECRET))
                change()
                code, out, err = self.review("r1", "prompt.md", "research/plan.md")
                self.assertEqual(code, 4, out + err)
                self.assertNotIn(KEY, out + err)
        self.assertEqual(self.fake_calls(), [])
        self.assertEqual(self.ledger(), [])

    def test_a_pinned_endpoint_must_match(self):
        self.settings(**{"kaggle.review.base_url": "https://other.example/v1"})
        code, out, _err = self.review("r1", "prompt.md", "research/plan.md")
        self.assertEqual(code, 4)
        self.assertIn("is not https://other.example/v1 (kaggle.review.base_url); the key is not used", out)
        self.assertEqual(self.fake_calls(), [])
        self.settings(**{"kaggle.review.base_url": "https://hub.example/v1"})
        self.assertEqual(self.review("r1", "prompt.md", "research/plan.md")[0], 0)

    def test_bad_usage(self):
        self.write("long.md", "x" * 2001 + "\n")
        cases = {
            "no reviewer": (("r1", "prompt.md"), None, "no reviewer: pass --model"),
            "unknown model": (("r1", "prompt.md"), "hub/vendor/none", "unknown model hub/vendor/none"),
            "unknown variant": (("r1", "prompt.md", "--variant", "huge"), MODEL,
                                "has no variant huge; known: high, max"),
            "bad id": (("r 1", "prompt.md"), MODEL, "not a review ID"),
            "no prompt": (("r1", "none.md"), MODEL, "no such prompt file: none.md"),
            "missing file": (("r1", "prompt.md", "none.txt"), MODEL, "no such file: none.txt"),
            "long line": (("r1", "prompt.md", "long.md"), MODEL, "1 line(s) over 2000 characters"),
            "id with package": (("r1", "--package", "x"), MODEL, "--package takes no ID"),
            "nothing": ((), MODEL, "usage: kaggle_review.py ID PROMPT_FILE"),
        }
        for name, (args, model, words) in cases.items():
            with self.subTest(name):
                code, out, err = self.review(*args, model=model)
                self.assertEqual(code, 2, out + err)
                self.assertIn(words, out)
        self.assertEqual(self.fake_calls(), [])
        # the unknown model names what the session's config offers
        self.assertIn("vendor/other, vendor/reviewer-1, vendor/reviewer-2",
                      self.review("r1", "prompt.md", model="hub/vendor/none")[1])
        code, out, err = self.review("r1", "prompt.md", "long.md", "--allow-long")
        self.assertEqual(code, 0, out + err)

    def test_outside_a_project_it_stops(self):
        code, out, _err = self.tool("kaggle_review.py", "r1", "prompt.md", "--model", MODEL, "--root", str(self.tmp))
        self.assertEqual(code, 2)
        self.assertIn("no project here", out)


class CopyTests(Review):
    def test_private_and_instruction_files_inside_a_folder_are_left_out(self):
        self.write("source/AGENTS.md", "rules\n")
        self.write("source/.memory/notes.md", "private\n")
        self.write("source/.claude/settings.json", "{}\n")
        self.write("source/.agents/skills/x.md", "a skill\n")
        self.write("source/credentials.md", "a credentials file\n")
        self.write("source/.env", "TOKEN=1\n")
        self.write("source/auth.json", "{}\n")
        self.write("source/secrets.yaml", "a: b\n")
        self.write("source/hub.token", "t\n")
        self.write("source/sub/opencode.json", "{}\n")
        self.write("source/tokenizer.py", "# not a secret by its name\n")
        (self.root / "source" / "link.md").symlink_to(self.root / "aipack" / "manifest.json")
        code, out, err = self.review("r1", "prompt.md", "source")
        self.assertEqual(code, 0, out + err)
        self.assertEqual(self.real_runs()[0]["files"], ["source/tokenizer.py", "source/train.py"])
        left = json.loads(self.out("r1", "run.json").read_text())["left_out"]
        self.assertEqual(len(left), 11, left)
        self.assertIn("left out of the copies: ", err)
        self.assertIn("source/link.md (a symbolic link)", "; ".join(left))

    def test_a_private_or_outside_file_named_by_the_caller_stops_the_run(self):
        self.write("AGENTS.md", "rules\n")
        self.write("aipack/.memory/config.json", "{}\n")
        self.write("credentials.md", "a credentials file\n")
        self.write(".claude/settings.json", "{}\n")
        (self.tmp / "outside.md").write_text("not the project's\n")
        (self.root / "way-out").symlink_to(self.tmp / "outside.md")
        cases = {"AGENTS.md": "OpenCode would load it", "aipack/.memory/config.json": "inside a .memory folder",
                 "aipack/.memory": "a private or tool folder", "credentials.md": "named like a credentials file",
                 ".claude": "a private or tool folder", ".claude/settings.json": "inside a .claude folder",
                 "../outside.md": "not a file or folder inside the project",
                 str(self.tmp / "outside.md"): "not a file or folder inside the project",
                 "way-out": "not a file or folder inside the project", ".": "not a file or folder inside the project"}
        for name, words in cases.items():
            with self.subTest(name):
                code, out, err = self.review("r1", "prompt.md", name)
                self.assertEqual(code, 2, out + err)
                self.assertIn(words, out)
        self.assertEqual(self.fake_calls(), [])

    def test_two_files_at_one_place_stop_the_run(self):
        self.write("__out/review/r1/plan.md", "a copy\n")
        self.write("plan.md", "another\n")
        code, out, _err = self.review("r1", "prompt.md", "__out/review/r1/plan.md", "plan.md")
        self.assertEqual(code, 2)
        self.assertIn("two files would land at plan.md", out)
        # the same file named twice is one copy
        code, out, err = self.review("r2", "prompt.md", "research/plan.md", "./research/plan.md")
        self.assertEqual(code, 0, out + err)
        self.assertEqual(self.real_runs()[0]["files"], ["research/plan.md"])

    def test_too_much_to_copy_stops_the_run(self):
        self.settings(**{"kaggle.review.max_mb": 0.001})
        self.write("big.bin", "x" * 5000)
        code, out, _err = self.review("r1", "prompt.md", "big.bin")
        self.assertEqual(code, 2)
        self.assertIn("hold more than 0.001 MB (kaggle.review.max_mb)", out)
        self.assertEqual(self.real_runs(), [])
        self.assert_nothing_left()

    def test_a_temporary_folder_below_a_repository_or_a_config_folder_is_refused(self):
        for name in (".git", ".claude", ".agents", ".opencode", "AGENTS.md", "opencode.jsonc"):
            with self.subTest(name):
                above = self.tmp / f"above-{name.strip('.')}"
                (above / "tmp").mkdir(parents=True)
                (above / name).mkdir() if name.startswith(".") else (above / name).write_text("x")
                self.env["TMPDIR"] = str(above / "tmp")
                code, out, _err = self.review("r1", "prompt.md", "research/plan.md")
                self.assertEqual(code, 5)
                self.assertIn(f"is below {above}, which holds {name}", out)
                self.assertEqual(list((above / "tmp").iterdir()), [])
        self.assertEqual(self.fake_calls(), [])


class PackageTests(Review):
    def test_a_package_is_reviewed_by_its_folder_name(self):
        self.package()
        (self.root / "__out" / "review" / "current").symlink_to("1122-plan-review")
        code, out, err = self.review("--package", "__out/review/current", "--day", "2026-11-22")
        self.assertEqual(code, 0, out + err)
        self.assertIn("kaggle_review 1122-plan-review: review written", out)
        self.assertTrue(self.out("1122-plan-review").is_file())
        run = self.real_runs()[0]
        self.assertEqual(run["prompt"], "Attack the day's plan.\n")
        # STATE.md at the top, the listed files at their project paths; not the prompt, the list or READY
        self.assertEqual(run["files"], ["STATE.md", "research/plan.md", "source/train.py"])

    def test_a_tag_gives_a_second_reviewer_its_own_folder(self):
        self.package()
        code, out, err = self.review("--package", "__out/review/1122-plan-review", "--tag", "two",
                                     model="hub/vendor/reviewer-2")
        self.assertEqual(code, 0, out + err)
        self.assertTrue(self.out("1122-plan-review-two").is_file())
        self.assertFalse(self.out("1122-plan-review").exists())
        self.assertIn("opencode review 1122-plan-review-two (hub/vendor/reviewer-2; ", self.ledger()[0]["what"])
        code, out, err = self.review("--package", "__out/review/1122-plan-review", "--id", "morning")
        self.assertEqual(code, 0, out + err)
        self.assertTrue(self.out("morning").is_file())

    def test_a_package_that_is_not_ready_exits_3_and_runs_nothing(self):
        folder = self.root / "__out" / "review" / "1122-plan-review"
        cases = {
            "no folder": (lambda: None, "inputs not ready: no package folder __out/review/1122-plan-review"),
            "no list": (lambda: self.package(files=None), "files.txt is missing or empty"),
            "empty list": (lambda: self.package(files="# nothing yet\n\n"), "files.txt names no file"),
            "no READY": (lambda: self.package(ready=None), "inputs not ready for 2026-11-22: "
                         "__out/review/1122-plan-review/READY is missing"),
            "yesterday's": (lambda: self.package(ready="2026-11-21"), "READY says 2026-11-21"),
            "a listed file is missing": (lambda: self.package(files="research/none.md\n"),
                                         "files.txt names research/none.md, which is not there"),
        }
        for name, (make, words) in cases.items():
            with self.subTest(name):
                shutil.rmtree(folder, ignore_errors=True)
                make()
                code, out, err = self.review("--package", "__out/review/1122-plan-review", "--day", "2026-11-22",
                                             "--tag", "two")
                self.assertEqual(code, 3, out + err)
                # the result line names the review, as a job reports it: exit 3: kaggle_review <ID>: inputs not ready
                last = out.strip().splitlines()[-1]
                self.assertTrue(last.startswith("kaggle_review 1122-plan-review-two: inputs not ready"), last)
                self.assertIn(words, last)
        self.assertEqual(self.fake_calls(), [])
        self.assertEqual(self.ledger(), [])

    def test_today_is_the_owners_day(self):
        self.settings(**{"owner.timezone": "UTC"})
        self.package(ready=datetime.now(timezone.utc).date().isoformat())
        code, out, err = self.review("--package", "__out/review/1122-plan-review", "--day", "today")
        self.assertEqual(code, 0, out + err)
        self.assertEqual(self.review("--package", "__out/review/1122-plan-review", "--day", "tomorrow")[0], 2)

    def test_without_a_day_ready_is_not_needed(self):
        self.package(ready=None)
        self.assertEqual(self.review("--package", "__out/review/1122-plan-review")[0], 0)


class GateTests(Review):
    def test_a_models_first_call_is_gated_and_later_ones_are_not(self):
        code, out, err = self.review("r1", "prompt.md", "research/plan.md")
        self.assertEqual(code, 0, out + err)
        gate, real = self.gate_runs(), self.real_runs()
        self.assertEqual((len(gate), len(real)), (1, 1))
        # the gate call comes first, in its own empty folder, with a one-line prompt
        self.assertEqual(self.fake_calls()[0], gate[0])
        self.assertEqual((gate[0]["files"], gate[0]["prompt"]), ([], "Reply with the single word OK."))
        self.assertIn(MODEL, gate[0]["args"])
        gates = json.loads(self.data("gates.json").read_text())
        self.assertEqual(gates[MODEL]["status"], 401)
        self.assertEqual(self.review("r2", "prompt.md", "research/plan.md", "--variant", "max")[0], 0)
        self.assertEqual(len(self.gate_runs()), 1)
        # another model has its own gate
        self.assertEqual(self.review("r3", "prompt.md", "research/plan.md", model="hub/vendor/reviewer-2")[0], 0)
        self.assertEqual(len(self.gate_runs()), 2)
        # a changed OpenCode binary is gated again
        os.utime(self.fake, ns=(1, 1))
        self.assertEqual(self.review("r4", "prompt.md", "research/plan.md")[0], 0)
        self.assertEqual(len(self.gate_runs()), 3)
        # the gate costs nothing: one ledger line per review
        self.assertEqual(len(self.ledger()), 4)

    def test_an_invalid_key_that_gets_an_answer_stops_everything(self):
        self.control(accept_any_key=True)
        code, out, err = self.review("r1", "prompt.md", "research/plan.md")
        self.assertEqual(code, 6, out + err)
        self.assertIn("the key gate failed for hub/vendor/reviewer-1: a call with an invalid key got an answer", out)
        self.assertEqual(self.real_runs(), [])
        self.assertEqual(self.ledger(), [])
        self.assertFalse(self.out("r1").exists())
        self.assertFalse(self.data("gates.json").exists())
        self.assert_nothing_left()

    def test_a_gate_call_that_fails_another_way_is_not_a_pass(self):
        self.control(gate_error={"type": "provider.error", "status": 500})
        code, out, err = self.review("r1", "prompt.md", "research/plan.md")
        self.assertEqual(code, 6, out + err)
        self.assertIn("ended with exit 1 without a refused key (errors: provider.error 500)", out)
        self.assertEqual(self.real_runs(), [])

    def test_the_gate_alone(self):
        code, out, err = self.review("--gate")
        self.assertEqual(code, 0, out + err)
        self.assertIn("key gate passed for hub/vendor/reviewer-1: a call with an invalid key was refused (401)", out)
        self.assertEqual((len(self.gate_runs()), len(self.real_runs())), (1, 0))
        # asked for, it runs again
        self.assertEqual(self.review("--gate")[0], 0)
        self.assertEqual(len(self.gate_runs()), 2)
        self.assert_nothing_left()
        self.assert_key_nowhere(out, err)


class PoolTests(Review):
    def setUp(self):
        super().setUp()
        self.settings(**{"kaggle.review.pool": {"one": {"model": "hub/vendor/reviewer-1", "variant": "high"},
                                                "two": {"model": "hub/vendor/reviewer-2"}}})

    def test_the_pool_runs_every_member_on_the_same_input(self):
        code, out, err = self.review("r1", "prompt.md", "research/plan.md", "--pool", model=None)
        self.assertEqual(code, 0, out + err)
        self.assertEqual(out.strip().splitlines()[-1], "kaggle_review: pool done, exit codes: one 0, two 0")
        self.assertTrue(self.out("r1-one").is_file() and self.out("r1-two").is_file())
        models = sorted(c["args"][c["args"].index("-m") + 1] for c in self.real_runs())
        self.assertEqual(models, ["hub/vendor/reviewer-1#high", "hub/vendor/reviewer-2"])
        self.assertEqual(len(self.gate_runs()), 2)
        self.assertEqual(sorted(x["what"].split(" (")[0] for x in self.ledger()),
                         ["opencode review r1-one", "opencode review r1-two"])
        gates = json.loads(self.data("gates.json").read_text())
        self.assertEqual(sorted(gates), ["hub/vendor/reviewer-1", "hub/vendor/reviewer-2"])
        self.assert_nothing_left()

    def test_one_member_by_name(self):
        code, out, err = self.review("r1", "prompt.md", "research/plan.md", "--reviewer", "one", model=None)
        self.assertEqual(code, 0, out + err)
        self.assertTrue(self.out("r1-one").is_file())
        code, out, _err = self.review("r1", "prompt.md", "--reviewer", "three", model=None)
        self.assertEqual(code, 2)
        self.assertIn('no reviewer three in "kaggle.review.pool" (known: one, two)', out)
        self.assertEqual(self.review("r1", "prompt.md", "--reviewer", "one")[0], 2)
        self.assertEqual(self.review("r1", "prompt.md", "--pool", "--out", "x.md", model=None)[0], 2)

    def test_the_pools_worst_exit_code_wins(self):
        self.session_config.write_text(SESSION_CONFIG.replace('"vendor/reviewer-2": {"name": "reviewer-2"},', "")
                                       % (KEY, OTHER_SECRET))
        code, out, _err = self.review("r1", "prompt.md", "research/plan.md", "--pool", model=None)
        self.assertEqual(code, 2)
        self.assertIn("pool done, exit codes: one 0, two 2", out)

    def test_a_member_killed_by_a_signal_fails_the_pool(self):
        # the member's reviewer kills the member's runner outright, as an out-of-memory kill would
        self.control(kill_runner_for="reviewer-2")
        code, out, err = self.review("r1", "prompt.md", "research/plan.md", "--pool", model=None)
        self.assertEqual(code, 1, out + err)
        self.assertEqual(out.strip().splitlines()[-1],
                         "kaggle_review: pool done, exit codes: one 0, two -9 (killed by signal 9)")
        self.assertTrue(self.out("r1-one").is_file())
        self.assertFalse(self.out("r1-two").exists())

    def test_a_stopped_pool_stops_its_members(self):
        self.control(sleep=120)
        p = subprocess.Popen(self.command("r1", "prompt.md", "research/plan.md", "--pool", model=None), cwd=self.root,
                             env=self.env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        self.addCleanup(lambda: p.poll() is None and p.kill())
        end = time.time() + 30
        while len(self.real_runs()) < 2 and time.time() < end:
            time.sleep(0.05)
        self.assertEqual(len(self.real_runs()), 2)
        p.send_signal(signal.SIGTERM)
        out, err = p.communicate(timeout=30)
        self.assertEqual(p.returncode, 143, out + err)
        for run in self.real_runs():
            self.assertTrue(self.gone(run["pid"]), "a member's reviewer is still running")
        self.assert_nothing_left()


class UnitTests(Review):
    def test_jsonc_is_read_as_json(self):
        m = self.module("kaggle_review")
        doc = json.loads(m.strip_jsonc('{"a": "x // y", /* c */ "b": [1, 2,], // d\n "c": "q\\"//",}'))
        self.assertEqual(doc, {"a": "x // y", "b": [1, 2], "c": 'q"//'})

    def test_secret_names_are_dropped_and_token_counts_kept(self):
        m = self.module("kaggle_review")
        got = m.without_secrets({"apiKey": "k", "api_key": "k", "headers": {"A": "b"}, "authToken": "t",
                                 "Authorization": "x", "maxTokens": 5, "budgetTokens": 9, "baseURL": "u",
                                 "limit": {"output": 1, "secretThing": 2}, "list": [{"password": "p", "ok": 1}]})
        self.assertEqual(got, {"maxTokens": 5, "budgetTokens": 9, "baseURL": "u", "limit": {"output": 1},
                               "list": [{"ok": 1}]})

    def test_variants_in_both_config_forms(self):
        m = self.module("kaggle_review")
        self.assertEqual(m.variants_of({"variants": {"max": {}, "high": {}}}), ["high", "max"])
        self.assertEqual(m.variants_of({"variants": [{"id": "low"}, {"id": "high"}]}), ["high", "low"])
        self.assertIsNone(m.variants_of({"name": "x"}))

    def test_paths_outside_the_folder(self):
        m = self.module("kaggle_review")
        ctx = Path("/tmp/kaggle-review-x")
        self.assertEqual(m.outside(["STATE.md", "a/../b.md", "../up.md", "/etc/passwd", str(ctx / "in.md"),
                                    "/tmp/kaggle-review-xy/z"], ctx),
                         ["../up.md", "/etc/passwd", "/tmp/kaggle-review-xy/z"])

    def test_the_forms_a_key_takes_in_a_file(self):
        m = self.module("kaggle_review")
        self.assertEqual(m.key_forms("plain-key"), [b"plain-key"])
        forms = m.key_forms('a"b\\é')
        self.assertEqual(sorted(forms), sorted(['a"b\\é'.encode(), b'a\\"b\\\\\\u00e9', 'a\\"b\\\\é'.encode()]))
        self.assertEqual(forms, sorted(forms, key=len, reverse=True))


if __name__ == "__main__":
    unittest.main()
