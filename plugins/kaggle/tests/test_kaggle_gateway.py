"""Offline tests for shared/tools/kaggle.py, the gateway (stdlib unittest; a stand-in CLI, no network, no uv).

    python3 -m unittest discover -s plugins/kaggle/tests

Each test builds a project whose .venv-kaggle already holds a stand-in `kaggle` executable that logs
every run, so the gateway's context detection and its two hooks (the activity stamp and the
leaderboard tee) run for real without installing anything.
"""

import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

sys.dont_write_bytecode = True  # keep __pycache__ out of the plugin folder
TOOLS = Path(__file__).resolve().parents[1] / "shared" / "tools"
GATEWAY = TOOLS / "kaggle.py"
SLUG = "demo-competition"
BOARD = json.dumps([{"teamId": 1, "teamName": "Alpha", "submissionDate": "2026-09-25T00:00:57", "score": "0.512"},
                    {"teamId": 2, "teamName": "Beta", "submissionDate": "2026-09-25T01:10:00", "score": "0.498"}],
                   indent=2) + "\n"
CSV = ("Rank,TeamId,TeamName,LastSubmissionDate,Score,SubmissionCount,TeamMemberUserNames\n"
       '1,1,Alpha,"2026-09-25 00:00:57",0.512,5,alpha_user\n2,2,Beta,"2026-09-25 01:10:00",0.498,3,beta_user\n')
FAKE_CLI = """\
#!{python}
import json, os, sys, zipfile
a = sys.argv[1:]
with open({log!r}, "a") as f:
    f.write(json.dumps(a) + "\\n")
if a == ["--version"]:
    print("Kaggle CLI 2.2.4")
elif a[:2] in (["competitions", "leaderboard"], ["c", "leaderboard"]) and "failing-board" in a:
    print("403 - Forbidden")
    sys.exit(3)
elif a[:2] in (["competitions", "leaderboard"], ["c", "leaderboard"]) and "--download" in a:
    # like the CLI: <-p folder>/<slug>.zip, dated by the server's clock
    z = os.path.join(a[a.index("-p") + 1], a[2] + ".zip")
    os.makedirs(os.path.dirname(z), exist_ok=True)
    with zipfile.ZipFile(z, "w") as f:
        f.writestr(a[2] + "-publicleaderboard-2026-09-27T22:54:20.csv", {csv!r})
    os.utime(z, (1700000000, 1700000000))
elif a[:2] in (["competitions", "leaderboard"], ["c", "leaderboard"]):
    sys.stdout.write({board!r})
else:
    print("ARGS " + json.dumps(a))
    sys.exit(int(os.environ.get("FAKE_EXIT", "0")))
"""
# stands in for `uv run ... kaggle <args>` at the framework root: the stand-in CLI gets the arguments after "kaggle"
FAKE_UV = """\
#!{python}
import runpy, sys
a = sys.argv[1:]
sys.argv = [{cli!r}, *a[a.index("kaggle") + 1:]]
runpy.run_path({cli!r}, run_name="__main__")
"""


def load(path):
    spec = importlib.util.spec_from_file_location(f"gw_{abs(hash(str(path)))}", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


GW = load(GATEWAY)


class Env(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp()).resolve()
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.log = self.tmp / "calls.jsonl"
        self.state = self.tmp / "state"
        self.env = {k: v for k, v in os.environ.items() if not k.startswith(("KAGGLE_", "FAKE_"))}
        self.env.update(KAGGLE_SHARE_DIR=str(self.state), PYTHONDONTWRITEBYTECODE="1")

    def project(self, name="proj", pack="aipack"):
        root = self.tmp / name
        (root / pack).mkdir(parents=True)
        (root / pack / "manifest.json").write_text("{}\n")
        env = root / GW.ENV_DIR
        (env / "bin").mkdir(parents=True)
        (env / "pyvenv.cfg").write_text("home = test\n")
        (env / "bin" / "python").symlink_to(sys.executable)
        cli = env / "bin" / "kaggle"
        cli.write_text(FAKE_CLI.format(python=sys.executable, log=str(self.log), board=BOARD, csv=CSV))
        cli.chmod(0o755)
        # the stamp the gateway expects, so it uses this venv as installed
        (env / GW.STAMP).write_text("\n".join([*GW.REQS, str(env.resolve())]) + "\n")
        return root

    def overlay(self, root, pack="aipack", tools=None):
        """A copied install: the gateway plus the given tool files (name -> source text)."""
        d = root / pack / "plugins" / "kaggle" / "tools"
        d.mkdir(parents=True)
        shutil.copyfile(GATEWAY, d / "kaggle.py")
        for name, text in (tools or {}).items():
            (d / name).write_text(textwrap.dedent(text))
        return d / "kaggle.py"

    def run_gw(self, args, cwd, gateway=GATEWAY, **env):
        p = subprocess.run([sys.executable, str(gateway), *args], cwd=cwd, capture_output=True, text=True,
                           env={**self.env, **env}, timeout=60)
        return p.returncode, p.stdout, p.stderr

    def cli_runs(self):
        return [json.loads(line) for line in self.log.read_text().splitlines()] if self.log.exists() else []

    def stamps(self):
        return [json.loads(p.read_text()) for p in sorted((self.state / "activity").glob("*.json"))]

    def saved(self, root, slug=SLUG):
        index = root / "__data" / "kaggle" / slug / "leaderboard" / "index.jsonl"
        return [json.loads(line) for line in index.read_text().splitlines()] if index.exists() else []


class ContextTests(Env):
    def where(self, cwd, gateway=GATEWAY):
        old = os.getcwd()
        os.chdir(cwd)
        try:
            return (GW if gateway == GATEWAY else load(gateway)).find_context()
        finally:
            os.chdir(old)

    def test_project_roots_for_both_pack_names(self):
        for pack in ("ai", "aipack"):
            root = self.project(f"p-{pack}", pack)
            (root / "src" / "deep").mkdir(parents=True)
            self.assertEqual(self.where(root / "src" / "deep"), root)

    def test_project_wins_over_task_notes_inside_it(self):
        root = self.project()
        (root / "research").mkdir()
        (root / "research" / "notes.md").write_text("made with the ad-hoc-task skill\n")
        self.assertEqual(self.where(root / "research"), root)

    def test_task_folder_and_nothing(self):
        task = self.tmp / "task"
        (task / "sub").mkdir(parents=True)
        (task / "notes.md").write_text("Started with the ad-hoc-task skill\n")
        self.assertEqual(self.where(task / "sub"), task)
        (self.tmp / "none").mkdir()
        self.assertIsNone(self.where(self.tmp / "none"))

    def test_copied_overlay_finds_its_project_from_anywhere(self):
        (self.tmp / "elsewhere").mkdir()
        for pack in ("ai", "aipack"):
            root = self.project(f"c-{pack}", pack)
            self.assertEqual(self.where(self.tmp / "elsewhere", self.overlay(root, pack)), root)

    def test_overlay_shape_outside_a_pack_does_not_count(self):
        d = self.tmp / "other" / "plugins" / "kaggle" / "tools"
        d.mkdir(parents=True)
        (self.tmp / "other" / "manifest.json").write_text("{}\n")
        shutil.copyfile(GATEWAY, d / "kaggle.py")
        (self.tmp / "elsewhere").mkdir()
        self.assertIsNone(self.where(self.tmp / "elsewhere", d / "kaggle.py"))


class StampTests(Env):
    def test_each_call_is_stamped_with_command_words_only(self):
        root = self.project()
        self.assertEqual(self.run_gw(["--version"], root)[:2], (0, "Kaggle CLI 2.2.4\n"))
        self.run_gw(["competitions", "files", SLUG, "--page-size", "5"], root)
        (s,) = self.stamps()
        self.assertEqual((s["root"], s["calls"], s["command"]), (str(root), 2, "competitions files"))

    def test_quiet_calls_are_not_stamped(self):
        root = self.project()
        self.assertEqual(self.run_gw(["--version"], root, KAGGLE_SHARE_QUIET="1")[0], 0)
        self.assertEqual(self.stamps(), [])

    def test_unwritable_state_never_blocks_the_command(self):
        root = self.project()
        blocker = self.tmp / "a-file"
        blocker.write_text("")
        code, out, err = self.run_gw(["--version"], root, KAGGLE_SHARE_DIR=str(blocker / "state"))
        self.assertEqual((code, out), (0, "Kaggle CLI 2.2.4\n"))
        self.assertIn("activity stamp skipped", err)


class TeeTests(Env):
    def test_leaderboard_read_passes_through_and_is_saved_once(self):
        root = self.project()
        code, out, err = self.run_gw(["competitions", "leaderboard", SLUG, "--show", "--format", "json"], root)
        self.assertEqual((code, out), (0, BOARD))
        self.assertIn("saved", err)
        self.assertEqual([(e["source"], e["rows"], e["partial"]) for e in self.saved(root)], [("gateway", 2, False)])
        self.assertEqual(len(self.cli_runs()), 1)

    def test_alias_and_short_flag(self):
        root = self.project()
        self.assertEqual(self.run_gw(["c", "leaderboard", SLUG, "-s"], root)[:2], (0, BOARD))
        self.assertEqual(len(self.saved(root)), 1)

    def test_failed_read_keeps_its_exit_code_and_saves_nothing(self):
        root = self.project()
        code, out, _ = self.run_gw(["competitions", "leaderboard", "failing-board", "--show"], root)
        self.assertEqual((code, out), (3, "403 - Forbidden\n"))
        self.assertFalse((root / "__data").exists())
        self.assertEqual(len(self.cli_runs()), 1)

    def test_switch_off_and_other_commands_are_untouched(self):
        root = self.project()
        self.assertEqual(self.run_gw(["competitions", "leaderboard", SLUG, "--show"], root,
                                     KAGGLE_LB_RECORD="0")[:2], (0, BOARD))
        self.assertEqual(self.run_gw(["competitions", "leaderboard", SLUG, "--download", "-p", "x"], root,
                                     KAGGLE_LB_RECORD="0")[0], 0)
        code, out, _ = self.run_gw(["competitions", "files", SLUG], root, FAKE_EXIT="5")
        self.assertEqual((code, out), (5, f'ARGS ["competitions", "files", "{SLUG}"]\n'))
        self.assertFalse((root / "__data").exists())
        self.assertEqual(len(self.cli_runs()), 3)

    def test_a_downloaded_board_is_saved(self):
        root = self.project()
        code, out, err = self.run_gw(["competitions", "leaderboard", SLUG, "--download", "-p", "lb"], root)
        self.assertEqual((code, out), (0, ""))
        self.assertIn("download saved", err)
        self.assertEqual([(e["source"], e["rows"], e["fetched_at"]) for e in self.saved(root)],
                         [("gateway", 2, "2026-09-27T22:54:20Z")])
        self.assertEqual(len(self.cli_runs()), 1)

    def test_snapshot_through_the_gateway_is_saved_once_and_not_stamped(self):
        root = self.project()
        p = subprocess.run([sys.executable, str(TOOLS / "kaggle_lb.py"), "snapshot", SLUG], cwd=root,
                           capture_output=True, text=True, env=self.env, timeout=60)
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual([e["source"] for e in self.saved(root)], ["snapshot"])
        self.assertEqual(self.stamps(), [])


class HookFailureTests(Env):
    def check_command_still_runs(self, tools, expect_err):
        root = self.project()
        gw = self.overlay(root, tools=tools)
        code, out, err = self.run_gw(["competitions", "leaderboard", SLUG, "--show"], self.tmp, gateway=gw)
        self.assertEqual((code, out), (0, BOARD))
        self.assertIn(expect_err, err)
        self.assertEqual(len(self.cli_runs()), 1)
        self.assertFalse((root / "__data").exists())

    def test_missing_hook_tools(self):
        self.check_command_still_runs({}, "skipped")

    def test_hook_tools_that_fail_to_import(self):
        broken = "raise RuntimeError('broken on purpose')\n"
        self.check_command_still_runs({"kaggle_share.py": broken, "kaggle_lb.py": broken}, "broken on purpose")

    def test_hook_tools_that_exit_on_import(self):
        leaving = "import sys\nsys.exit(7)\n"
        self.check_command_still_runs({"kaggle_share.py": leaving, "kaggle_lb.py": leaving}, "SystemExit")

    def test_a_tee_failing_after_the_cli_ran_does_not_run_it_again(self):
        root = self.project()
        gw = self.overlay(root, tools={"kaggle_lb.py": """\
            import subprocess
            def leaderboard_read(argv):
                return True
            def tee_leaderboard(cmd, argv, root):
                subprocess.run([*cmd, *argv], stdout=subprocess.DEVNULL)
                raise RuntimeError("failed after the run")
            """})
        code, _, err = self.run_gw(["competitions", "leaderboard", SLUG, "--show"], root, gateway=gw)
        self.assertEqual(code, 1)
        self.assertIn("failed after the run", err)
        self.assertEqual(len(self.cli_runs()), 1)


class FrameworkRootTests(Env):
    def test_a_read_at_the_framework_root_is_saved_only_to_a_named_store(self):
        bindir = self.tmp / "bin"
        bindir.mkdir()
        cli = self.tmp / "cli.py"
        cli.write_text(FAKE_CLI.format(python=sys.executable, log=str(self.log), board=BOARD, csv=CSV))
        uv = bindir / "uv"
        uv.write_text(FAKE_UV.format(python=sys.executable, cli=str(cli)))
        uv.chmod(0o755)
        path = {"PATH": f"{bindir}{os.pathsep}{os.environ.get('PATH', '')}"}
        bare = self.tmp / "bare"
        bare.mkdir()
        args = ["competitions", "leaderboard", SLUG, "--show"]
        code, out, err = self.run_gw(args, bare, **path)
        self.assertEqual((code, out), (0, BOARD))
        self.assertIn("leaderboard read not saved", err)
        store = self.tmp / "store"
        code, out, err = self.run_gw(args, bare, KAGGLE_LB_DIR=str(store), **path)
        self.assertEqual((code, out), (0, BOARD))
        self.assertEqual(len((store / SLUG / "index.jsonl").read_text().splitlines()), 1)
        self.assertEqual(len(self.cli_runs()), 2)
        self.assertEqual(self.stamps(), [])


if __name__ == "__main__":
    unittest.main()
