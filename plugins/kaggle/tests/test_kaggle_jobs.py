"""Offline tests for shared/tools/kaggle_jobs.py (stdlib unittest; a fake `autocode` command, no server).

    python3 -m unittest discover -s plugins/kaggle/tests -p 'test_kaggle_jobs.py'

The fake stands in for the autocode CLI: it keeps the server's jobs in a file, answers `job list --json` and
`job set` as the owner's CLI does, and refuses both as an agent's shell does. No autocode server is touched.
"""

import json
import shutil
import subprocess
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from kaggle_standin import TOOLS, Project  # noqa: E402

REFUSAL = ("in an agent's shell the CLI has only me, fire and send (and the machine's own setup: info, serve, "
           "install, uninstall, plugin); in the conversation, use the autocode_* tools")
FAKE = r'''#!%s
import json, os, sys
here = os.path.dirname(os.path.abspath(__file__))
state_path = os.path.join(here, "state.json")
with open(state_path) as f:
    state = json.load(f)
args = sys.argv[1:]
with open(os.path.join(here, "calls.jsonl"), "a") as f:
    f.write(json.dumps(args) + "\n")
if args[:1] != ["job"]:
    sys.exit(2)
if state["mode"] == "agent":
    sys.stderr.write(%r + "\n")
    sys.exit(2)
if state["mode"] == "down":
    sys.stderr.write("autocode: cannot reach the server at http://127.0.0.1:1\n")
    sys.exit(1)
if args[1:] == ["list", "--json"]:
    print(json.dumps(list(state["jobs"].values()), indent=1))
    sys.exit(0)
if args[1:2] == ["set"]:
    name, rest, job = args[2], args[3:], {}
    flags = {"--host": "host", "--model": "model", "--variant": "variant", "--prompt": "prompt",
             "--trigger": "trigger", "--to": "to", "--description": "description", "--timeout": "timeout",
             "--run-usd": "run_usd", "--agent": "opencode_agent"}
    i = 0
    while i < len(rest):
        if rest[i] == "--keep-session":
            job["delete_session"] = False
            i += 1
        else:
            job[flags[rest[i]]] = rest[i + 1]
            i += 2
    if "run_usd" in job:
        job["run_usd"] = float(job["run_usd"])
    old = state["jobs"].get(name, {})
    # a set replaces the definition; the server keeps its own facts
    new = {"name": name, "variant": None, "to": None, "description": "", "timeout": "1h", "run_usd": None,
           "delete_session": True, **{k: old[k] for k in ("directory", "machine", "last_run") if k in old}, **job}
    if state.get("ignore") in new:
        new[state["ignore"]] = old.get(state["ignore"])
    state["jobs"][name] = new
    with open(state_path, "w") as f:
        json.dump(state, f)
    print("job " + name + " set")
    sys.exit(0)
sys.exit(2)
''' % (sys.executable, REFUSAL)

PROMPT = "You are an outside reviewer.\nRead prompt.md, then STATE.md.\nEnd with autocode_me action=finish."


class Jobs(Project):
    def setUp(self):
        super().setUp()
        bin_dir = self.tmp / "bin"
        bin_dir.mkdir()
        self.autocode = bin_dir / "autocode"
        self.autocode.write_text(FAKE)
        self.autocode.chmod(0o755)
        # the server: two jobs of this project, one of another project
        self.server(jobs=[
            self.job("kaggle:demo:master:review-astra", trigger="fire-only", run_usd=30, to="kaggle:demo:master"),
            self.job("kaggle:demo:master:review-argon", trigger='cron "0 7 * * *" America/Los_Angeles', run_usd=2,
                     timeout="50m", delete_session=False, next="2026-11-23T15:00:00.000Z",
                     last_run={"id": "x", "state": "succeeded", "started": "2026-11-22T15:00:00.068Z"}),
            self.job("kaggle:other:master:review-astra", directory=str(self.tmp / "other"), run_usd=5),
        ])
        # the plugin's templates sit beside tools/ in a copied install
        shutil.copytree(TOOLS.parent / "jobs", self.tools.parent / "jobs")

    def job(self, name, **fields):
        return {"name": name, "description": f"Daily review ({name})", "host": "box:demo:a487b5d0",
                "machine": "box", "directory": str(self.root), "online": True, "model": "hub/vendor/reviewer-1",
                "model_name": "Reviewer 1", "variant": "max", "prompt": PROMPT, "trigger": "fire-only", "next": None,
                "to": "kaggle:demo:master", "timeout": "1h", "run_usd": 30, "delete_session": True,
                "created_by": None, "last_run": None, "created_at": "2026-11-18T08:41:42.661Z",
                "updated_at": "2026-11-18T08:50:32.196Z", **fields}

    def server(self, jobs=None, mode="owner", **more):
        path = self.autocode.parent / "state.json"
        state = json.loads(path.read_text()) if path.exists() else {"jobs": {}}
        if jobs is not None:
            state["jobs"] = {j["name"]: j for j in jobs}
        state.update(mode=mode, **more)
        path.write_text(json.dumps(state))

    def server_job(self, name):
        return json.loads((self.autocode.parent / "state.json").read_text())["jobs"][name]

    def cli_calls(self):
        log = self.autocode.parent / "calls.jsonl"
        return [json.loads(x) for x in log.read_text().splitlines()] if log.exists() else []

    def jobs(self, *args):
        return self.tool("kaggle_jobs.py", *args, "--autocode", str(self.autocode))

    def file(self, stem):
        return self.root / "aipack" / "jobs" / f"{stem}.json"

    def put(self, stem, **fields):
        job = {"name": stem, "host": "box:demo:a487b5d0", "model": "hub/vendor/reviewer-1", "variant": "max",
               "prompt": PROMPT, "trigger": "fire-only", "to": "kaggle:demo:master", "description": "A job",
               "timeout": "1h", "run_usd": 30, "keep_session": False, **fields}
        self.file(stem).parent.mkdir(parents=True, exist_ok=True)
        self.file(stem).write_text(json.dumps(job, indent=1))
        return job


class BackupTests(Jobs):
    def test_backup_writes_this_projects_jobs_with_the_fields_of_a_job(self):
        code, out, err = self.jobs("backup")
        self.assertEqual(code, 0, err)
        self.assertEqual(out.splitlines(), [
            "backup: kaggle:demo:master:review-argon -> aipack/jobs/review-argon.json (written)",
            "backup: kaggle:demo:master:review-astra -> aipack/jobs/review-astra.json (written)"])
        self.assertEqual(sorted(p.name for p in self.file("x").parent.iterdir()),
                         ["review-argon.json", "review-astra.json"])
        text = self.file("review-argon").read_text()
        job = json.loads(text)
        # exactly a job's own fields, in order; none of the server's facts (machine, next, last run, dates)
        self.assertEqual(list(job), ["name", "host", "model", "variant", "prompt", "trigger", "to", "description",
                                     "timeout", "run_usd", "keep_session"])
        self.assertEqual(job, {
            "name": "kaggle:demo:master:review-argon", "host": "box:demo:a487b5d0",
            "model": "hub/vendor/reviewer-1", "variant": "max", "prompt": PROMPT,
            "trigger": 'cron "0 7 * * *" America/Los_Angeles', "to": "kaggle:demo:master",
            "description": "Daily review (kaggle:demo:master:review-argon)", "timeout": "50m", "run_usd": 2,
            "keep_session": True})
        self.assertTrue(text.endswith("\n"))
        # nothing but the list was asked of the server
        self.assertEqual(self.cli_calls(), [["job", "list", "--json"]])

    def test_a_second_backup_changes_nothing_and_a_changed_job_updates_its_file(self):
        self.assertEqual(self.jobs("backup")[0], 0)
        self.assertIn("review-astra.json (unchanged)", self.jobs("backup")[1])
        self.server(jobs=[self.job("kaggle:demo:master:review-astra", run_usd=5, prompt="New words.")])
        code, out, _err = self.jobs("backup", "review-astra")
        self.assertEqual(code, 0)
        self.assertEqual(out, "backup: kaggle:demo:master:review-astra -> aipack/jobs/review-astra.json "
                         "(updated: prompt, run_usd)\n")
        self.assertEqual(json.loads(self.file("review-astra").read_text())["run_usd"], 5)

    def test_a_job_is_named_in_full_or_by_its_last_part(self):
        code, out, _err = self.jobs("backup", "kaggle:other:master:review-astra")
        self.assertEqual(code, 0)
        self.assertIn("kaggle:other:master:review-astra -> aipack/jobs/review-astra.json (written)", out)
        # the short name means this project's job; its file name is taken, so the whole name is used
        code, out, _err = self.jobs("backup", "review-astra")
        self.assertEqual(code, 0)
        self.assertIn("kaggle:demo:master:review-astra -> aipack/jobs/kaggle.demo.master.review-astra.json", out)
        code, _out, err = self.jobs("backup", "none")
        self.assertEqual(code, 2)
        self.assertIn("no job none on the server (this project's: kaggle:demo:master:review-argon, "
                      "kaggle:demo:master:review-astra)", err)

    def test_a_file_that_holds_the_job_under_a_short_name_is_the_one_updated(self):
        self.put("morning", name="review-astra", run_usd=1)
        code, out, _err = self.jobs("backup", "review-astra")
        self.assertEqual(code, 0)
        self.assertIn("kaggle:demo:master:review-astra -> aipack/jobs/morning.json (updated: description, run_usd)",
                      out)
        self.assertEqual(json.loads(self.file("morning").read_text())["name"], "kaggle:demo:master:review-astra")
        self.assertFalse(self.file("review-astra").exists())

    def test_no_job_of_this_project_writes_nothing(self):
        self.server(jobs=[self.job("kaggle:other:master:x", directory=str(self.tmp / "other"))])
        code, out, _err = self.jobs("backup")
        self.assertEqual((code, out), (0, "backup: no job on the server runs in this project's folder; nothing "
                                       "written\n"))
        self.assertFalse(self.file("x").parent.exists())


class ListTests(Jobs):
    def test_list_compares_the_files_with_the_server(self):
        self.assertEqual(self.jobs("backup", "review-argon")[0], 0)
        self.put("review-astra", name="review-astra", run_usd=5, prompt="Other words.", description="")
        self.put("worker", name="worker")
        code, out, err = self.jobs("list")
        self.assertEqual(code, 0, err)
        lines = out.splitlines()
        self.assertEqual(lines[0], "3 job file(s) in aipack/jobs; 2 job(s) on the server run in this project's folder")
        self.assertEqual(lines[1], "review-argon  kaggle:demo:master:review-argon  same; trigger cron \"0 7 * * *\" "
                         "America/Los_Angeles; next 2026-11-23T15:00:00.000Z; last run succeeded")
        # the description, left empty in the file, is the server's to choose: not a difference
        self.assertEqual(lines[2], "review-astra  kaggle:demo:master:review-astra  differs: prompt, run_usd; "
                         "trigger fire-only")
        self.assertEqual(lines[3], "worker        worker  pack only; trigger fire-only")
        self.assertEqual(len(lines), 4)
        self.file("review-argon").unlink()
        self.assertIn("-             kaggle:demo:master:review-argon  server only (kaggle_jobs.py backup "
                      "review-argon)", self.jobs("list")[1])
        doc = json.loads(self.jobs("list", "--json")[1])
        self.assertEqual((doc["pack"], doc["server"]), ("aipack/jobs", "read"))
        self.assertEqual([(j["file"], j["state"]) for j in doc["jobs"]],
                         [("review-astra", "differs"), ("worker", "pack only"), (None, "server only")])

    def test_in_an_agents_shell_list_shows_the_files_and_says_so(self):
        self.put("review-astra", name="kaggle:demo:master:review-astra")
        self.server(mode="agent")
        code, out, _err = self.jobs("list")
        self.assertEqual(code, 0)
        self.assertEqual(out.splitlines(), [
            "1 job file(s) in aipack/jobs; the server's jobs were not read: this is an agent's shell: its autocode "
            "CLI has only me, fire and send",
            "review-astra  kaggle:demo:master:review-astra  not compared; trigger fire-only"])
        self.server(mode="down")
        self.assertIn("were not read: `autocode job list --json` failed (exit 1): autocode: cannot reach the server",
                      self.jobs("list")[1])
        code, out, _err = self.tool("kaggle_jobs.py", "list", "--autocode", str(self.tmp / "none"))
        self.assertEqual(code, 0)
        self.assertIn("the autocode command is not there", out)

    def test_a_file_that_is_not_json_stops_the_run(self):
        self.file("broken").parent.mkdir(parents=True)
        self.file("broken").write_text("{not json")
        code, _out, err = self.jobs("list")
        self.assertEqual(code, 2)
        self.assertIn("broken.json is not JSON", err)


class ApplyTests(Jobs):
    def test_apply_sets_the_job_with_every_field_and_reads_it_back(self):
        self.put("review-astra", name="kaggle:demo:master:review-astra", run_usd=7.5, keep_session=True,
                 prompt="New words.", timeout="45m")
        code, out, err = self.jobs("apply", "review-astra")
        self.assertEqual(code, 0, err)
        self.assertEqual(out, "apply: kaggle:demo:master:review-astra set from aipack/jobs/review-astra.json\n")
        sets = [c for c in self.cli_calls() if c[:2] == ["job", "set"]]
        self.assertEqual(sets, [[
            "job", "set", "kaggle:demo:master:review-astra", "--host", "box:demo:a487b5d0", "--model",
            "hub/vendor/reviewer-1", "--variant", "max", "--prompt", "New words.", "--trigger", "fire-only", "--to",
            "kaggle:demo:master", "--description", "A job", "--timeout", "45m", "--run-usd", "7.5",
            "--keep-session"]])
        self.assertEqual(self.server_job("kaggle:demo:master:review-astra")["prompt"], "New words.")
        self.assertIn("review-astra  kaggle:demo:master:review-astra  same", self.jobs("list")[1])

    def test_empty_fields_are_left_out_of_the_call(self):
        self.put("plain", variant="", to="", description="", timeout="", run_usd=None, opencode_agent="review")
        code, out, err = self.jobs("apply", "plain")
        self.assertEqual(code, 0, out + err)
        self.assertEqual([c for c in self.cli_calls() if c[:2] == ["job", "set"]], [[
            "job", "set", "plain", "--host", "box:demo:a487b5d0", "--model", "hub/vendor/reviewer-1", "--prompt",
            PROMPT, "--trigger", "fire-only", "--agent", "review"]])

    def test_a_backup_applied_again_changes_nothing_on_the_server(self):
        before = self.server_job("kaggle:demo:master:review-argon")
        self.assertEqual(self.jobs("backup")[0], 0)
        code, out, err = self.jobs("apply", "kaggle:demo:master:review-argon", "review-astra")
        self.assertEqual(code, 0, out + err)
        after = self.server_job("kaggle:demo:master:review-argon")
        for field in ("host", "model", "variant", "prompt", "trigger", "to", "description", "timeout", "run_usd",
                      "delete_session", "directory"):
            self.assertEqual(after[field], before[field], field)
        self.assertEqual(len([c for c in self.cli_calls() if c[:2] == ["job", "set"]]), 2)

    def test_a_file_with_a_placeholder_or_a_missing_field_is_never_applied(self):
        self.put("template", prompt="Review {{PROJECT_DIR}} with {{REVIEW_MODEL}}.", model="{{REVIEW_MODEL}}")
        self.put("no-host", host="")
        self.put("bad-name", name="a job")
        cases = {"template": "still has placeholders: {{PROJECT_DIR}} (prompt); {{REVIEW_MODEL}} (model, prompt)",
                 "no-host": "no-host.json lacks host", "bad-name": "not a job name: 'a job'",
                 "none": "no job file for none (the pack has: bad-name, no-host, template)"}
        for name, words in cases.items():
            with self.subTest(name):
                code, _out, err = self.jobs("apply", name)
                self.assertEqual(code, 2)
                self.assertIn(words, err)
        self.assertEqual(self.cli_calls(), [])

    def test_in_an_agents_shell_backup_and_apply_stop_and_point_at_the_tool_call(self):
        self.put("review-astra", name="kaggle:demo:master:review-astra")
        self.server(mode="agent")
        for cmd in (["backup"], ["apply", "review-astra"]):
            with self.subTest(cmd[0]):
                code, out, err = self.jobs(*cmd)
                self.assertEqual((code, out), (3, ""))
                self.assertIn(f"kaggle_jobs: {cmd[0]} needs the owner's autocode CLI: a script in an agent's shell "
                              "can call only `autocode me`, `fire me` and `send`.", err)
                self.assertIn("`kaggle_jobs.py show NAME --tool` prints the arguments", err)
        self.assertEqual([c for c in self.cli_calls() if c[:2] == ["job", "set"]], [])

    def test_a_server_that_cannot_be_reached_or_does_not_take_the_job_exits_1(self):
        self.put("review-astra", name="kaggle:demo:master:review-astra", timeout="45m")
        self.server(mode="down")
        code, _out, err = self.jobs("apply", "review-astra")
        self.assertEqual(code, 1)
        self.assertIn("apply cannot read the server's jobs: `autocode job list --json` failed (exit 1)", err)
        self.assertEqual(self.jobs("backup")[0], 1)
        # a set the server did not take in full
        self.server(mode="owner", ignore="timeout")
        code, out, _err = self.jobs("apply", "review-astra")
        self.assertEqual(code, 1)
        self.assertIn("apply: kaggle:demo:master:review-astra set, but the server differs from "
                      "aipack/jobs/review-astra.json in: timeout", out)


class ShowTests(Jobs):
    def test_show_prints_the_definition_and_tool_the_agents_call(self):
        job = self.put("review-astra", name="kaggle:demo:master:review-astra", _note="ignored", to="", variant="")
        code, out, _err = self.jobs("show", "review-astra")
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out), {k: v for k, v in job.items() if k != "_note"})
        code, out, err = self.jobs("show", "kaggle:demo:master:review-astra", "--tool")
        self.assertEqual(code, 0, err)
        self.assertEqual(json.loads(out), {
            "action": "job_set", "job": "review-astra", "model": "hub/vendor/reviewer-1", "prompt": PROMPT,
            "trigger": "fire-only", "run_usd": 30, "host": "box:demo:a487b5d0", "timeout": "1h",
            "description": "A job"})
        self.assertIn("the server names the job <your agent ID>:review-astra", err)
        # no server is asked
        self.assertEqual(self.cli_calls(), [])

    def test_the_tool_call_needs_a_cap_and_no_placeholder_but_no_host(self):
        self.put("own", host="", keep_session=True)
        call = json.loads(self.jobs("show", "own", "--tool")[1])
        self.assertNotIn("host", call)
        self.assertIs(call["keep_session"], True)
        self.put("no-cap", run_usd=None)
        code, _out, err = self.jobs("show", "no-cap", "--tool")
        self.assertEqual(code, 2)
        self.assertIn("has no run_usd", err)
        self.put("template", prompt="{{PACKAGE_FOLDER}}")
        self.assertEqual(self.jobs("show", "template", "--tool")[0], 2)
        self.assertEqual(self.jobs("show", "template")[0], 0)


class TemplateTests(Jobs):
    PLACEHOLDERS = {"PROJECT_DIR", "PACK", "TOOLS", "ZONE", "JOB_MODEL", "REVIEW_MODEL", "REVIEWER", "PACKAGE_FOLDER"}
    FILL = ("--set", "REVIEWER=argon", "--set", "REVIEW_MODEL=hub/vendor/reviewer-2", "--set",
            "JOB_MODEL=hub/vendor/small", "--set", "PACKAGE_FOLDER=__out/review/current")

    def test_the_plan_review_template_is_a_job_definition_with_its_placeholders(self):
        m = self.module("kaggle_jobs")
        text = (TOOLS.parent / "jobs" / "plan-review.json").read_text()
        doc = json.loads(text)
        self.assertEqual(list(doc)[0], "_rev")
        self.assertEqual([k for k in doc if not k.startswith("_")], list(m.FIELDS))
        self.assertEqual(set(m.PLACEHOLDER_RE.findall(text)), self.PLACEHOLDERS)
        # each run only starts the runner on the day's package and reports one line
        self.assertIn("python3 {{TOOLS}}/kaggle_review.py --package {{PACKAGE_FOLDER}} --day today --model "
                      "{{REVIEW_MODEL}} --tag {{REVIEWER}}", doc["prompt"])
        for line in ("exit 0: kaggle_review", "exit 3: kaggle_review", "exit 1: kaggle_review",
                     "autocode_me action=finish", "inputs not ready", "FAILED"):
            self.assertIn(line, doc["prompt"])
        # the job's own session is not the reviewer: its model is its own placeholder, the reviewer's goes to
        # the runner; and the hour is in a named zone
        self.assertEqual((doc["model"], doc["trigger"]), ("{{JOB_MODEL}}", 'cron "0 7 * * *" {{ZONE}}'))
        self.assertNotIn("{{JOB_MODEL}}", doc["prompt"])

    def test_new_fills_a_template_and_names_what_is_left(self):
        code, out, err = self.jobs("new", "review-argon", "--template", "plan-review", "--set", "REVIEWER=argon")
        self.assertEqual(code, 0, err)
        self.assertEqual(out, "new: aipack/jobs/review-argon.json written from the template plan-review; still to "
                         "fill: {{JOB_MODEL}}, {{PACKAGE_FOLDER}}, {{REVIEW_MODEL}}, {{ZONE}}\n")
        self.assertEqual(self.jobs("apply", "review-argon")[0], 2)
        self.assertEqual(self.jobs("new", "review-argon", "--template", "plan-review")[0], 2)
        code, out, err = self.jobs("new", "review-argon", "--template", "plan-review", "--force", *self.FILL,
                                   "--set", "ZONE=Europe/London")
        self.assertEqual((code, out), (0, "new: aipack/jobs/review-argon.json written from the template "
                                       "plan-review\n"), err)
        job = json.loads(self.file("review-argon").read_text())
        self.assertEqual(list(job), list(self.module("kaggle_jobs").FIELDS))
        # the job's session runs on its own model; the reviewer's model is the runner's
        self.assertEqual((job["name"], job["model"], job["run_usd"]), ("review-argon", "hub/vendor/small", 2))
        self.assertEqual(job["trigger"], 'cron "0 7 * * *" Europe/London')
        self.assertIn(f"From {self.root}, run exactly this command", job["prompt"])
        # the tools as this project has them: a copied plugin's, inside the pack
        self.assertIn("python3 aipack/plugins/kaggle/tools/kaggle_review.py --package __out/review/current --day "
                      "today --model hub/vendor/reviewer-2 --tag argon --timeout 1800", job["prompt"])
        self.assertNotIn("{{", json.dumps(job))
        # an agent sets it as its own job: no host is needed
        call = json.loads(self.jobs("show", "review-argon", "--tool")[1])
        self.assertEqual((call["job"], call["model"], call["timeout"]), ("review-argon", "hub/vendor/small", "50m"))
        self.assertNotIn("host", call)

    def test_new_takes_the_zone_from_the_packs_settings(self):
        (self.root / "aipack" / "defaults.json").write_text(json.dumps({"owner.timezone": "America/Los_Angeles"}))
        code, out, err = self.jobs("new", "review-argon", "--template", "plan-review", *self.FILL)
        self.assertEqual((code, out), (0, "new: aipack/jobs/review-argon.json written from the template "
                                       "plan-review\n"), err)
        job = json.loads(self.file("review-argon").read_text())
        self.assertEqual(job["trigger"], 'cron "0 7 * * *" America/Los_Angeles')
        self.assertIn("07:00 America/Los_Angeles", job["description"])
        # the private setting wins, and --set wins over both
        (self.root / "aipack" / ".memory").mkdir()
        (self.root / "aipack" / ".memory" / "config.json").write_text(json.dumps({"owner.timezone": "Asia/Tokyo"}))
        self.assertEqual(self.jobs("new", "a", "--template", "plan-review", *self.FILL)[0], 0)
        self.assertEqual(json.loads(self.file("a").read_text())["trigger"], 'cron "0 7 * * *" Asia/Tokyo')
        self.assertEqual(self.jobs("new", "b", "--template", "plan-review", *self.FILL, "--set", "ZONE=UTC")[0], 0)
        self.assertEqual(json.loads(self.file("b").read_text())["trigger"], 'cron "0 7 * * *" UTC')

    def test_a_linked_plugins_tools_are_named_by_their_full_path(self):
        # the tool run from the plugin's own folder, outside the project, as a linked plugin's is
        p = subprocess.run([sys.executable, str(TOOLS / "kaggle_jobs.py"), "new", "review-argon", "--template",
                            "plan-review", *self.FILL, "--set", "ZONE=UTC", "--root", str(self.root)],
                           capture_output=True, text=True, env=self.env)
        self.assertEqual(p.returncode, 0, p.stderr)
        job = json.loads(self.file("review-argon").read_text())
        self.assertIn(f"python3 {TOOLS}/kaggle_review.py --package __out/review/current", job["prompt"])
        self.assertTrue((TOOLS / "kaggle_review.py").is_file())

    def test_new_refuses_what_it_cannot_use(self):
        cases = {"an unknown template": (["x", "--template", "none"], "no template none"),
                 "a bad name": (["a/b", "--template", "plan-review"], "not a file name"),
                 "a bad pair": (["x", "--template", "plan-review", "--set", "REVIEWER"], "--set takes KEY=VALUE"),
                 "an unknown key": (["x", "--template", "plan-review", "--set", "MODEL=m"],
                                    "the template plan-review has no placeholder MODEL")}
        for name, (args, words) in cases.items():
            with self.subTest(name):
                code, _out, err = self.jobs("new", *args)
                self.assertEqual(code, 2)
                self.assertIn(words, err)
        self.assertFalse(self.file("x").exists())


class PlaceTests(Jobs):
    def test_outside_a_project_it_stops(self):
        code, _out, err = self.tool("kaggle_jobs.py", "list", "--root", str(self.tmp))
        self.assertEqual(code, 2)
        self.assertIn("no project here", err)


if __name__ == "__main__":
    unittest.main()
