"""Offline tests for shared/tools/kaggle_share.py (stdlib unittest; fixture kernel lists, no network).

    python3 -m unittest discover -s plugins/kaggle/tests
"""

import contextlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

TOOLS = Path(__file__).resolve().parents[1] / "shared" / "tools"
sys.path.insert(0, str(TOOLS))
import kaggle_share as S  # noqa: E402

NOW = datetime(2026, 9, 28, 12, 0, tzinfo=timezone.utc)
# what makes a folder an ai-pack, whatever its name: a manifest with framework_version and a project object
PACK_MANIFEST = json.dumps({"framework_version": "0.39.0", "project": {"name": "demo"}})
# the quota as the account read prints it
QUOTA = {"gpu": {"used": 12.0, "remaining": 18.0, "total": 30.0, "refresh": "2026-10-03T00:00:00"},
         "tpu": {"used": 0.0, "remaining": 20.0, "total": 20.0, "refresh": "2026-10-03T00:00:00"}}


def at(hours_ago):
    return S.iso(NOW - timedelta(hours=hours_ago))


def kaggle_time(hours_ago):
    # the CLI's lastRunTime: naive UTC with microseconds
    return (NOW - timedelta(hours=hours_ago)).strftime("%Y-%m-%dT%H:%M:%S.123000")


def ctx(name, last=None, kernels=None):
    return {"name": name, "root": None, "kind": "project", "last": last, "calls": 1 if last else 0,
            "kernels": kernels or {}}


# a stand-in gateway run as a subprocess: the account read with no quota, the kernel list from a fixture file and
# every run complete
GATEWAY_STANDIN = """\
import json, sys
from datetime import datetime, timedelta, timezone
a = sys.argv[1:]
if a[:2] != ["--sdk", "account"]:
    sys.exit(2)
since = datetime.now(timezone.utc) - timedelta(hours=float(a[a.index("--hours") + 1]))
kernels = [dict(k, status="COMPLETE") for k in json.load(open({fixtures!r}))]
print(json.dumps(dict(since=since.strftime("%Y-%m-%dT%H:%M:%SZ"), quota=dict(), kernels=kernels, calls=2, errors=[])))
"""


class FakeKaggle:
    """Stands in for the gateway's account read (--sdk account), printing what kaggle_sdk.py prints from fixtures.

    fail names what fails: "quota", "list", a kernel ref (its status), or "read" (the whole read). The read's
    clock runs `late` seconds after the caller's.
    """

    def __init__(self, kernels=(), statuses=None, quota=None, fail=(), late=0):
        self.kernels, self.statuses = list(kernels), statuses or {}
        self.quota, self.fail, self.late, self.calls = quota or QUOTA, set(fail), late, []

    def __call__(self, cmd, cwd):
        args = cmd[2:]
        self.calls.append(args)
        if args[:2] != ["--sdk", "account"]:
            return 2, "unexpected command"
        if "read" in self.fail:
            return 1, "kaggle_sdk: not signed in to Kaggle: sign in first\n"
        since = NOW + timedelta(seconds=self.late) - timedelta(hours=float(args[args.index("--hours") + 1]))
        done = {args[i + 1] for i, a in enumerate(args) if a == "--done"}
        doc = {"fetched_at": S.iso(NOW), "since": S.iso(since), "quota": self.quota, "kernels": [], "calls": 2,
               "errors": []}
        if "quota" in self.fail:
            doc["quota"] = None
            doc["errors"].append("quota read failed: 403 Forbidden: Permission denied")
        if "list" in self.fail:
            doc["kernels"] = None
            doc["errors"].append("kernel list failed: 403 Forbidden: Permission denied")
            return 0, json.dumps(doc, indent=2)
        for k in self.kernels:
            row = dict(k, status=None)
            doc["kernels"].append(row)
            if S.parse_time(k["lastRunTime"]) < since or f"{k['ref'].lower()}={k['lastRunTime']}" in done:
                continue
            doc["calls"] += 1
            if k["ref"] in self.fail:
                doc["errors"].append(f"status read of {k['ref']} failed: 429 Too Many Requests: slow down")
            else:
                row["status"] = self.statuses.get(k["ref"], "COMPLETE")
        return 0, json.dumps(doc, indent=2)


class Tmp(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp()).resolve()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.base = self.tmp / "state"
        env = mock.patch.dict(os.environ, {}, clear=False)
        env.start()
        self.addCleanup(env.stop)
        os.environ.pop(S.ENV_DIR, None)

    def tree(self):
        """A small Solaris checkout: alpha (aipack) and beta (ai) with kernels, gamma (mypack) with only the plugin."""
        sol = self.tmp / "sol"
        (sol / "solaris").mkdir(parents=True)
        (sol / "solaris" / "solaris.agent.md").write_text("x")
        specs = {"my/alpha": ("aipack", {"source/kaggle/k1": {"id": "alice/alpha-gpu", "enable_gpu": True},
                                         "source/kaggle/k2": {"id": "alice/alpha-cpu", "enable_gpu": False},
                                         ".venv/x": {"id": "alice/hidden"}, "__out/y": {"id": "alice/local"}}),
                 "nv/beta": ("ai", {"kernels/b1": {"id": "Alice/Beta-GPU", "machine_shape": "NvidiaTeslaT4"}}),
                 "tmp/gamma": ("mypack", {}), "tmp/plain": ("ai", {})}
        for rel, (pack, kernels) in specs.items():
            root = sol / "projects" / rel
            (root / pack).mkdir(parents=True)
            (root / pack / "manifest.json").write_text(PACK_MANIFEST)
            for d, meta in kernels.items():
                (root / d).mkdir(parents=True)
                (root / d / "kernel-metadata.json").write_text(json.dumps(meta))
        (sol / "projects" / "tmp" / "gamma" / "mypack" / "plugins" / "kaggle").mkdir(parents=True)
        return sol

    def config(self, **raw):
        S.write_json(self.base / "sharing.json", raw)
        return S.load_config(self.base)


class DetectionTests(Tmp):
    def test_projects_and_kernels_come_from_the_tree(self):
        contexts = S.gather(self.tree(), [])
        self.assertEqual(sorted(contexts), ["alpha", "beta", "gamma"])
        self.assertEqual(contexts["alpha"]["kernels"], {"alice/alpha-gpu": "gpu", "alice/alpha-cpu": "cpu"})
        self.assertEqual(contexts["beta"]["kernels"], {"alice/beta-gpu": "gpu"})
        self.assertEqual(contexts["gamma"]["kernels"], {})

    def test_stamp_keeps_the_command_words_only(self):
        root = self.tree() / "projects" / "my" / "alpha"
        p = S.stamp(root, ["kernels", "push", "-p", "SECRETDIR/x"], now=NOW, base=self.base)
        S.stamp(root, ["competitions", "submit", "slug", "-m", "SECRETNOTE"], now=NOW + timedelta(hours=1),
                base=self.base)
        s = json.loads(p.read_text())
        self.assertEqual((s["calls"], s["command"], s["kind"], s["name"]), (2, "competitions submit", "project",
                                                                            "alpha"))
        self.assertEqual((s["first"], s["last"]), (S.iso(NOW), S.iso(NOW + timedelta(hours=1))))
        self.assertNotIn("SECRET", p.read_text())
        self.assertNotIn("slug", p.read_text())
        self.assertEqual(p.parent, self.base / "activity")

    def test_an_sdk_read_is_stamped_as_sdk_and_its_read(self):
        for argv, command in ((["--sdk", "notebooks", "SECRETSLUG", "--max", "5"], "sdk notebooks"),
                              (["--sdk", "account", "--hours", "12"], "sdk account"), (["--sdk"], "sdk"),
                              (["--sdk", "--help"], "sdk"), (["kernels", "list", "--mine"], "kernels list")):
            self.assertEqual(S.command_of(argv), command)
        root = self.tree() / "projects" / "my" / "alpha"
        p = S.stamp(root, ["--sdk", "topic", "987654321"], now=NOW, base=self.base)
        self.assertEqual(json.loads(p.read_text())["command"], "sdk topic")
        self.assertNotIn("987654321", p.read_text())

    def test_monitoring_calls_are_not_stamped(self):
        root = self.tree() / "projects" / "my" / "alpha"
        os.environ[S.QUIET_ENV] = "1"
        self.assertIsNone(S.stamp(root, ["kernels", "status"], now=NOW, base=self.base))
        self.assertEqual(S.load_stamps(self.base), [])
        with mock.patch.object(S.subprocess, "run") as run:
            run.return_value = mock.Mock(returncode=0, stdout="[]", stderr="")
            os.environ.pop(S.QUIET_ENV)
            S._run(["gw", "quota"], self.tmp)
        self.assertEqual(run.call_args.kwargs["env"][S.QUIET_ENV], "1")

    def test_default_state_folder_is_not_the_credentials_folder(self):
        self.assertEqual(S.state_dir().parts[-2:], (".solaris", "kaggle"))
        os.environ[S.ENV_DIR] = str(self.tmp / "x")
        self.assertEqual(S.state_dir(), self.tmp / "x")

    def test_recent_gateway_calls_make_a_project_active(self):
        sol = self.tree()
        root = sol / "projects" / "my" / "alpha"
        S.stamp(root, ["kernels", "list"], now=NOW - timedelta(hours=23), base=self.base)
        contexts = S.gather(sol, S.load_stamps(self.base))
        cfg, state = S.load_config(self.base), S.load_state(self.base)
        view = S.build_view(cfg, contexts, None, state, [], NOW)
        self.assertTrue(view["projects"]["alpha"]["active"])
        self.assertIsNone(view["projects"]["beta"]["active"])
        later = S.build_view(cfg, contexts, None, state, [], NOW + timedelta(hours=2))
        self.assertIsNone(later["projects"]["alpha"]["active"])

    def test_a_stamp_outside_the_tree_is_a_context(self):
        task = self.tmp / "task"
        task.mkdir()
        (task / "notes.md").write_text("Skill: ad-hoc-task\n")
        S.stamp(task, ["kernels", "status"], now=NOW, base=self.base)
        contexts = S.gather(None, S.load_stamps(self.base))
        self.assertEqual(contexts["task"]["kind"], "task")

    def test_scan_checks_only_kernels_run_in_the_last_hours(self):
        fake = FakeKaggle([{"ref": "alice/a", "lastRunTime": kaggle_time(1)},
                           {"ref": "alice/b", "lastRunTime": kaggle_time(5)},
                           {"ref": "alice/c", "lastRunTime": kaggle_time(20)}], {"alice/a": "RUNNING"})
        acc = S.scan_account(Path("gw.py"), self.tmp, run=fake, now=NOW)
        self.assertEqual([(k["ref"], k["status"]) for k in acc["kernels"]],
                         [("alice/a", "RUNNING"), ("alice/b", "COMPLETE")])
        self.assertEqual(acc["calls"], 4)
        self.assertEqual(acc["quota"]["gpu"], {"used": 12.0, "remaining": 18.0, "total": 30.0,
                                               "refresh": "2026-10-03T00:00:00"})
        again = S.scan_account(Path("gw.py"), self.tmp, run=fake, now=NOW, cache=acc)
        self.assertEqual(again["calls"], 3)  # the finished run is not asked again
        self.assertEqual(again["kernels"], acc["kernels"])
        # one read each time, and it is told of the finished run
        read = ["--sdk", "account", "--hours", "12", "--page-size", "50"]
        self.assertEqual(fake.calls, [read, [*read, "--done", f"alice/b={kaggle_time(5)}"]])

    def test_the_reads_own_window_decides_what_is_recent(self):
        # the read starts 5 s after this clock: a run inside this window but not the read's has no status read,
        # and must not show as a run of unknown state
        edge = (NOW - timedelta(hours=12, seconds=-2)).strftime("%Y-%m-%dT%H:%M:%S.123000")
        fake = FakeKaggle([{"ref": "alice/edge", "lastRunTime": edge}], late=5)
        self.assertEqual(S.scan_account(Path("gw.py"), self.tmp, run=fake, now=NOW)["kernels"], [])

    def test_failed_reads_are_reported_not_fatal(self):
        kernels = [{"ref": "alice/a", "lastRunTime": kaggle_time(1)}]
        acc = S.scan_account(Path("gw.py"), self.tmp, run=FakeKaggle(kernels, fail={"list"}), now=NOW)
        self.assertFalse(acc["listed"])
        self.assertTrue(any("kernel list" in e for e in acc["errors"]))
        self.assertIsNotNone(acc["quota"])
        # a status the read could not get counts as a run of unknown state
        acc = S.scan_account(Path("gw.py"), self.tmp, run=FakeKaggle(kernels, fail={"quota", "alice/a"}), now=NOW)
        self.assertEqual((acc["quota"], acc["kernels"][0]["status"], len(acc["errors"])), (None, "UNKNOWN", 2))
        # the whole read failing: nothing listed, no quota
        acc = S.scan_account(Path("gw.py"), self.tmp, run=FakeKaggle(kernels, fail={"read"}), now=NOW)
        self.assertEqual((acc["listed"], acc["quota"], acc["kernels"], acc["calls"]), (False, None, [], 0))
        self.assertEqual(acc["errors"], ["`--sdk account` failed (exit 1): kaggle_sdk: not signed in to Kaggle: "
                                         "sign in first"])

    def test_kernel_ids_that_contain_insert_are_real(self):
        d = self.tmp / "k"
        d.mkdir()
        for kid, real in (("alice/insertion-sort-demo", True), ("Alice/Insert-Demo", True),
                          ("alice/INSERT_KERNEL_SLUG_HERE", False), ("INSERT_USERNAME_HERE/demo", False),
                          ("no-owner", False)):
            (d / "kernel-metadata.json").write_text(json.dumps({"id": kid}))
            if real:
                self.assertEqual(S.read_kernel(d), (kid.lower(), "cpu"))
            else:
                with self.assertRaises(S.ShareError):
                    S.read_kernel(d)
        (d / "kernel-metadata.json").write_text("[1]")
        with self.assertRaises(S.ShareError):
            S.read_kernel(d)

    def test_embedded_grouped_projects_are_found_without_a_stamp(self):
        sol = self.tree()
        repo = sol / "projects" / "my" / "delta" / "delta-repo"
        (repo / "ai").mkdir(parents=True)
        (repo / "ai" / "manifest.json").write_text(PACK_MANIFEST)
        (repo / "kaggle" / "k").mkdir(parents=True)
        (repo / "kaggle" / "k" / "kernel-metadata.json").write_text(json.dumps({"id": "alice/delta-gpu",
                                                                               "enable_gpu": True}))
        # a folder inside a project is never a project of its own
        nested = sol / "projects" / "my" / "alpha" / "vendor"
        (nested / "ai").mkdir(parents=True)
        (nested / "ai" / "manifest.json").write_text(PACK_MANIFEST)
        found = S.tree_projects(sol)
        self.assertIn(repo, found)
        self.assertNotIn(nested, found)
        self.assertEqual(S.gather(sol, [])["delta-repo"]["kernels"], {"alice/delta-gpu": "gpu"})

    def test_a_project_whose_pack_cannot_be_told_is_skipped_with_a_note(self):
        sol = self.tree()
        projects, beta, delta = sol / "projects", sol / "projects" / "nv" / "beta", sol / "projects" / "my" / "delta"
        # beta gets a copy of its pack (cp -r ai ai.bak); delta's only pack manifest is a merge conflict
        shutil.copytree(beta / "ai", beta / "ai.bak")
        (delta / "aipack").mkdir(parents=True)
        (delta / "aipack" / "manifest.json").write_text(f"<<<<<<< HEAD\n{PACK_MANIFEST}\n=======\n")
        # an embedded repo's own manifest.json that is not JSON but names no framework_version is no broken pack
        repo = projects / "my" / "ext" / "ext-repo"
        (repo / "ai").mkdir(parents=True)
        (repo / "ai" / "manifest.json").write_text(PACK_MANIFEST)
        (repo / "manifest.json").write_text('{"manifest_version": 3, // a browser extension\n}')
        err, out = io.StringIO(), io.StringIO()
        with contextlib.redirect_stderr(err), contextlib.redirect_stdout(out):
            found = S.tree_projects(sol)
            # account sharing goes on for the others
            code = S.main(["status", "--json", "--offline", "--solaris", str(sol), "--state", str(self.base)])
        self.assertEqual([p.relative_to(projects).as_posix() for p in found],
                         ["my/alpha", "my/ext/ext-repo", "tmp/gamma", "tmp/plain"])
        self.assertEqual((code, sorted(json.loads(out.getvalue())["projects"])), (0, ["alpha", "gamma"]))
        notes = [f"kaggle_share: {delta}: cannot read aipack/manifest.json: skipped",
                 f"kaggle_share: {beta}: more than one ai-pack (ai, ai.bak): skipped"]
        self.assertEqual(err.getvalue().splitlines(), notes * 2)
        # the tool's own lookup still stops at two packs
        with self.assertRaisesRegex(S.ShareError, r"beta: more than one ai-pack \(ai, ai\.bak\)$"):
            S.find_context(beta / "kernels")

    def test_context_for_any_pack_name_and_from_a_copied_install(self):
        (self.tmp / "elsewhere").mkdir()
        for pack in ("ai", "aipack", "mypack"):
            proj = self.tmp / f"p-{pack}"
            tools = proj / pack / "plugins" / "kaggle" / "tools"
            tools.mkdir(parents=True)
            (proj / pack / "manifest.json").write_text(PACK_MANIFEST)
            self.assertEqual((S.find_context(tools), S.pack_of(proj), S.context_kind(proj)),
                             (proj, proj / pack, "project"))
            self.assertTrue(S.has_kaggle_plugin(proj))
            # a copied install finds its project from its own place
            with mock.patch.object(S, "__file__", str(tools / "kaggle_share.py")):
                self.assertEqual(S.find_context(self.tmp / "elsewhere"), proj)
        # a plugin manifest and a hidden folder are no pack; two packs stop the walk with an error naming them
        odd = self.tmp / "odd"
        for name, text in (("plugin", '{"name": "kaggle"}'), (".hidden", PACK_MANIFEST), ("one", PACK_MANIFEST),
                           ("two", PACK_MANIFEST)):
            (odd / name / "plugins" / "kaggle").mkdir(parents=True)
            (odd / name / "manifest.json").write_text(text)
        with self.assertRaisesRegex(S.ShareError, r"odd: more than one ai-pack \(one, two\)$"):
            S.find_context(odd / "plugin")
        (odd / "two" / "manifest.json").unlink()
        self.assertEqual((S.pack_of(odd), S.context_kind(odd), S.has_kaggle_plugin(odd)), (odd / "one", "project", True))

    def test_scan_hours_from_sharing_json_reach_the_account_scan(self):
        now = S.now_utc()
        runs = [{"ref": f"alice/{r}", "lastRunTime": (now - timedelta(hours=h)).strftime("%Y-%m-%dT%H:%M:%S.123000")}
                for r, h in (("recent", 1), ("older", 20))]
        fixtures = self.tmp / "kernels.json"
        fixtures.write_text(json.dumps(runs))
        gw = self.tmp / "gw.py"
        gw.write_text(GATEWAY_STANDIN.format(fixtures=str(fixtures)))

        def checked(**raw):
            S.write_json(self.base / "sharing.json", raw)
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                code = S.main(["status", "--json", "--gateway", str(gw), "--solaris", "none", "--state",
                               str(self.base)])
            self.assertEqual(code, 0)
            return json.loads(out.getvalue())["recent"]

        self.assertEqual(checked(), 1)
        self.assertEqual(checked(scan_hours=24), 2)

    def test_running_kernels_map_to_projects_and_kinds(self):
        contexts = S.gather(self.tree(), [])
        account = {"at": S.iso(NOW), "listed": True, "kinds": {}, "kernels": [
            {"ref": "alice/alpha-gpu", "last_run": kaggle_time(1), "status": "RUNNING"},
            {"ref": "alice/beta-gpu", "last_run": kaggle_time(1), "status": "QUEUED"},
            {"ref": "alice/alpha-cpu", "last_run": kaggle_time(2), "status": "COMPLETE"},
            {"ref": "alice/elsewhere", "last_run": kaggle_time(1), "status": "RUNNING"}]}
        view = S.build_view(S.load_config(self.base), contexts, account, S.load_state(self.base), [], NOW)
        self.assertEqual(view["projects"]["alpha"]["used"], {"cpu": 0, "gpu": 1})
        self.assertEqual(view["projects"]["beta"]["used"], {"cpu": 0, "gpu": 1})
        self.assertEqual(view["other"]["unknown"], 1)
        self.assertEqual(view["in_use"], {"cpu": 0, "gpu": 2})
        self.assertIn("1 queued or running", view["projects"]["alpha"]["active"])
        self.assertIsNone(view["projects"]["gamma"]["active"])


class SplitTests(Tmp):
    def view(self, cfg, contexts, **kw):
        state = kw.pop("state", None) or S.load_state(self.base)
        return S.build_view(cfg, contexts, kw.pop("account", None), state, kw.pop("ledger", []), NOW, **kw)

    def three(self):
        return {n: ctx(n, last=at(1)) for n in ("alpha", "beta", "gamma")}

    def test_equal_shares_round_up_and_split_the_gpu_hours(self):
        v = self.view(S.load_config(self.base), self.three())
        for n in ("alpha", "beta", "gamma"):
            self.assertEqual(v["projects"][n]["share"], {"cpu": 2, "gpu": 1})
            self.assertAlmostEqual(v["projects"][n]["gpu_budget"], 10.0)

    def test_weights_override_detection(self):
        cfg = self.config(weights={"alpha": 3, "beta": 1})
        contexts = {"alpha": ctx("alpha"), "beta": ctx("beta", last=at(1)), "gamma": ctx("gamma", last=at(1))}
        v = self.view(cfg, contexts)
        self.assertEqual(v["projects"]["alpha"]["share"], {"cpu": 4, "gpu": 2})
        self.assertEqual(v["projects"]["beta"]["share"], {"cpu": 2, "gpu": 1})
        self.assertEqual(v["projects"]["gamma"]["share"], {"cpu": 0, "gpu": 0})
        self.assertAlmostEqual(v["projects"]["alpha"]["gpu_budget"], 22.5)
        self.assertAlmostEqual(v["projects"]["beta"]["gpu_budget"], 7.5)

    def test_only_caps_and_reserve(self):
        cfg = self.config(only="beta", caps={"beta": {"gpu": 1, "gpu_hours": 12}}, reserve={"gpu": 1})
        v = self.view(cfg, self.three())
        self.assertEqual(v["cap"], {"cpu": 5, "gpu": 1})
        self.assertEqual(v["projects"]["beta"]["share"], {"cpu": 5, "gpu": 1})
        self.assertAlmostEqual(v["projects"]["beta"]["gpu_budget"], 12.0)
        self.assertEqual(v["projects"]["alpha"]["share"], {"cpu": 0, "gpu": 0})
        ok, _, why = S.decide(self.view(cfg, self.three(), requester="alpha"), cfg, "alpha", "cpu")
        self.assertFalse(ok)
        self.assertIn("only to beta", why)

    def test_gpu_hours_this_week_come_from_the_ledger_and_open_leases(self):
        account = {"at": S.iso(NOW), "quota": QUOTA, "kernels": []}
        ledger = [{"project": "alpha", "kind": "gpu", "end": at(24), "hours": 4.0},
                  {"project": "alpha", "kind": "gpu", "end": at(24 * 5), "hours": 3.0},  # before the week began
                  {"project": "alpha", "kind": "cpu", "end": at(2), "hours": 9.0}]
        state = {"leases": {"l1": {"id": "l1", "project": "alpha", "kind": "gpu", "acquired": at(2),
                                   "expires": at(-10)}}, "waiters": {}}
        v = self.view(S.load_config(self.base), self.three(), account=account, ledger=ledger, state=state)
        self.assertEqual(v["week_start"], "2026-09-26T00:00:00Z")
        self.assertAlmostEqual(v["projects"]["alpha"]["gpu_hours"], 6.0)
        self.assertAlmostEqual(v["gpu_hours_left"], 18.0)


class LeaseTests(Tmp):
    def setUp(self):
        super().setUp()
        self.cfg = S.load_config(self.base)
        self.contexts = {"alpha": ctx("alpha", last=at(1)), "beta": ctx("beta", last=at(1))}

    def take(self, project, kind="gpu", cfg=None, account=None, **kw):
        return S.try_acquire(self.base, cfg or self.cfg, self.contexts, account, project, kind, now=NOW, **kw)

    def test_share_then_borrow_then_refuse_then_release(self):
        l1, why, _ = self.take("alpha")
        self.assertEqual((bool(l1), why), (True, "within its share"))
        l2, why, _ = self.take("alpha")
        self.assertTrue(l2["borrowed"])
        self.assertIn("borrowed", why)
        l3, why, view = self.take("beta")
        self.assertIsNone(l3)
        self.assertIn("all 2 GPU sessions are in use", why)
        self.assertEqual(view["in_use"]["gpu"], 2)
        S.release(self.base, ids=[l2["id"]], now=NOW + timedelta(hours=2))
        l4, why, _ = self.take("beta")
        self.assertEqual(why, "within its share")
        self.assertIsNone(self.take("alpha")[0])
        (entry,) = S.load_ledger(self.base)
        self.assertEqual((entry["project"], entry["borrowed"], entry["hours"], entry["how"]),
                         ("alpha", True, 2.0, "released"))

    def test_no_borrowing_while_another_project_uses_its_share(self):
        cfg = self.config(limits={"gpu": 4})
        self.assertTrue(self.take("beta", cfg=cfg)[0])
        self.assertTrue(self.take("alpha", cfg=cfg)[0])
        self.assertTrue(self.take("alpha", cfg=cfg)[0])
        lease, why, _ = self.take("alpha", cfg=cfg)
        self.assertIsNone(lease)
        self.assertIn("beta still use or wait", why)

    def test_a_waiting_project_blocks_borrowing(self):
        self.assertTrue(self.take("alpha")[0])
        S.set_waiter(self.base, {"id": "w1", "project": "beta", "kind": "gpu", "since": S.iso(NOW),
                                 "until": S.iso(NOW + timedelta(minutes=30)), "pid": os.getpid()})
        lease, why, _ = self.take("alpha")
        self.assertIsNone(lease)
        self.assertIn("beta", why)
        S.set_waiter(self.base, remove="w1")
        self.assertTrue(self.take("alpha")[0])

    def test_a_gone_waiter_is_dropped(self):
        S.set_waiter(self.base, {"id": "w1", "project": "beta", "kind": "gpu", "since": S.iso(NOW),
                                 "until": S.iso(NOW + timedelta(minutes=30)), "pid": 2 ** 22 + 12345})
        self.take("alpha")
        self.assertEqual(S.load_state(self.base)["waiters"], {})

    def test_caps_and_the_weekly_quota(self):
        cfg = self.config(caps={"alpha": {"gpu": 1}})
        self.assertTrue(self.take("alpha", cfg=cfg)[0])
        lease, why, _ = self.take("alpha", cfg=cfg)
        self.assertIn("capped at 1", why)
        spent = {"at": S.iso(NOW), "kernels": [], "quota": {"gpu": {"used": 30.0, "remaining": 0.0, "total": 30.0,
                                                                    "refresh": "2026-10-03T00:00:00"}}}
        lease, why, _ = self.take("beta", account=spent)
        self.assertIsNone(lease)
        self.assertIn("GPU hours left", why)
        self.assertTrue(self.take("beta", kind="cpu", account=spent)[0])

    def test_a_used_up_gpu_budget_is_over_share(self):
        self.base.mkdir(parents=True, exist_ok=True)
        with open(self.base / "ledger.jsonl", "a") as f:
            f.write(json.dumps({"project": "alpha", "kind": "gpu", "end": at(3), "hours": 15.5}) + "\n")
        self.assertTrue(self.take("beta", kind="gpu")[0])
        lease, why, _ = self.take("alpha")
        self.assertIsNone(lease)  # beta is using its share, so alpha cannot borrow hours
        self.assertIn("has used its share", why)

    def test_finished_and_expired_leases_close_themselves(self):
        a1 = self.take("alpha", kernel="alice/alpha-gpu")[0]
        b1 = self.take("beta", kind="cpu")[0]
        state = S.load_state(self.base)
        state["leases"][b1["id"]]["expires"] = at(-0.5)
        done = {"kernels": [{"ref": "alice/alpha-gpu", "last_run": kaggle_time(-0.25), "status": "COMPLETE"}]}
        closed = S.tidy(self.base, state, done, NOW + timedelta(hours=1))
        self.assertEqual({x["id"] for x, _h in closed}, {a1["id"], b1["id"]})
        hows = {e["project"]: (e["how"], e["hours"]) for e in S.load_ledger(self.base)}
        self.assertEqual(hows, {"alpha": ("finished", 1.0), "beta": ("expired", 0.5)})

    def test_an_older_finished_run_does_not_close_a_new_lease(self):
        self.take("alpha", kernel="alice/alpha-gpu")
        state = S.load_state(self.base)
        old = {"kernels": [{"ref": "alice/alpha-gpu", "last_run": kaggle_time(3), "status": "COMPLETE"}]}
        self.assertEqual(S.tidy(self.base, state, old, NOW), [])

    def test_a_run_on_a_slower_kaggle_clock_still_closes_the_lease(self):
        read = {"at": S.iso(NOW), "listed": True, "kinds": {}, "kernels": []}
        lease = self.take("alpha", kernel="alice/alpha-gpu", account=read)[0]
        self.assertNotIn("prior_unknown", lease)
        state = S.load_state(self.base)
        # Kaggle's clock is 5 minutes behind this machine's, so its run seems to start before the lease
        done = {"kernels": [{"ref": "alice/alpha-gpu", "last_run": kaggle_time(5 / 60), "status": "COMPLETE"}]}
        ((closed, _h),) = S.tidy(self.base, state, done, NOW + timedelta(hours=1))
        self.assertEqual(closed["id"], lease["id"])

    def test_without_an_account_read_at_acquire_only_a_later_run_closes_the_lease(self):
        # the read failed (not listed), or only an old cached read was at hand (--offline)
        failed = {"at": S.iso(NOW), "listed": False, "kinds": {}, "kernels": [], "errors": ["kernels list failed"]}
        old = {"at": at(2), "listed": True, "kinds": {}, "kernels": []}
        for account in (failed, old, None):
            lease = self.take("alpha", kernel="alice/alpha-gpu", account=account)[0]
            self.assertTrue(lease["prior_unknown"])
            self.assertNotIn("prior_run", lease)
            state = S.load_state(self.base)
            # a run that finished within the clock slack before the lease is not this lease's run
            before = {"kernels": [{"ref": "alice/alpha-gpu", "last_run": kaggle_time(5 / 60), "status": "COMPLETE"}]}
            self.assertEqual(S.tidy(self.base, state, before, NOW + timedelta(minutes=30)), [])
            after = {"kernels": [{"ref": "alice/alpha-gpu", "last_run": kaggle_time(-0.1), "status": "COMPLETE"}]}
            ((closed, _h),) = S.tidy(self.base, state, after, NOW + timedelta(hours=1))
            self.assertEqual(closed["id"], lease["id"])
            S.write_json(self.base / "state.json", state)

    def test_the_run_kaggle_showed_before_the_lease_never_closes_it(self):
        before = {"at": S.iso(NOW), "listed": True, "kinds": {}, "kernels": [
            {"ref": "alice/alpha-gpu", "last_run": kaggle_time(3 / 60), "status": "ERROR"}]}
        lease = self.take("alpha", kernel="alice/alpha-gpu", account=before)[0]
        self.assertEqual(lease["prior_run"], kaggle_time(3 / 60))
        state = S.load_state(self.base)
        self.assertEqual(S.tidy(self.base, state, before, NOW + timedelta(minutes=5)), [])
        after = {"kernels": [{"ref": "alice/alpha-gpu", "last_run": kaggle_time(-0.1), "status": "COMPLETE"}]}
        self.assertEqual(len(S.tidy(self.base, state, after, NOW + timedelta(hours=1))), 1)

    def test_a_lease_and_its_running_kernel_are_one_session(self):
        self.take("alpha", kernel="alice/alpha-gpu")
        account = {"kinds": {}, "kernels": [{"ref": "alice/alpha-gpu", "last_run": kaggle_time(0.1),
                                             "status": "RUNNING"}]}
        view = S.build_view(self.cfg, self.contexts, account, S.load_state(self.base), [], NOW)
        self.assertEqual(view["projects"]["alpha"]["used"]["gpu"], 1)
        self.assertEqual(view["running"][0]["kind"], "gpu")

    def test_release_needs_a_unique_match(self):
        self.take("alpha", kernel="alice/k1")
        self.take("alpha", kind="cpu", kernel="alice/k2")
        with self.assertRaises(S.ShareError):
            S.release(self.base, project="alpha")
        (one,) = S.release(self.base, project="alpha", kernel="ALICE/K1")
        self.assertEqual(one[0]["kernel"], "alice/k1")
        self.assertEqual(len(S.release(self.base, project="alpha", all_=True)), 1)
        with self.assertRaises(S.ShareError):
            S.release(self.base, ids=["nope"])


class ConcurrencyTests(Tmp):
    def test_parallel_acquires_never_overbook_the_pool(self):
        S.write_json(self.base / "sharing.json", {"limits": {"gpu": 2}})
        cmd = [sys.executable, str(TOOLS / "kaggle_share.py"), "acquire", "--kind", "gpu", "--offline",
               "--solaris", "none", "--state", str(self.base)]
        procs = [subprocess.Popen([*cmd, "--project", f"p{i}"], cwd=self.tmp, stdout=subprocess.PIPE,
                                  stderr=subprocess.PIPE) for i in range(6)]
        codes = sorted(p.wait(timeout=60) for p in procs)
        for p in procs:
            p.stdout.close()
            p.stderr.close()
        self.assertEqual(codes, [0, 0, 3, 3, 3, 3])
        state = json.loads((self.base / "state.json").read_text())
        self.assertEqual(len(state["leases"]), 2)


class CommandTests(Tmp):
    def run_main(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = S.main([*argv, "--state", str(self.base)])
        return code, out.getvalue(), err.getvalue()

    def test_config_round_trip(self):
        code, out, _ = self.run_main("config", "--weight", "alpha=3", "--weight", "beta=1",
                                     "--cap", "beta:gpu=1", "--reserve", "gpu=1", "--note", "owner: favour alpha")
        self.assertEqual(code, 0)
        cfg = S.load_config(self.base)
        self.assertEqual((cfg["mode"], cfg["weights"], cfg["caps"], cfg["reserve"]["gpu"]),
                         ("weights", {"alpha": 3.0, "beta": 1.0}, {"beta": {"gpu": 1.0}}, 1.0))
        self.run_main("config", "--only", "beta")
        self.assertEqual(S.load_config(self.base)["only"], ["beta"])
        self.run_main("config", "--equal")
        self.assertEqual(S.load_config(self.base)["mode"], "auto")
        self.assertEqual(self.run_main("config", "--cap", "beta:tpu=1")[0], 1)

    def test_acquire_from_a_kernel_folder_then_status_and_release(self):
        sol = self.tree()
        kdir = sol / "projects" / "my" / "alpha" / "source" / "kaggle" / "k1"
        code, out, _ = self.run_main("acquire", "--offline", "--solaris", str(sol), "--path", str(kdir), "--json")
        self.assertEqual(code, 0)
        lease = json.loads(out)
        self.assertEqual((lease["project"], lease["kind"], lease["kernel"]), ("alpha", "gpu", "alice/alpha-gpu"))
        code, out, _ = self.run_main("status", "--offline", "--solaris", str(sol))
        self.assertEqual(code, 0)
        self.assertIn("Sessions in use: CPU 0/5, GPU 1/2", out)
        self.assertIn(lease["id"], out)
        code, out, _ = self.run_main("status", "--offline", "--solaris", str(sol), "--json")
        self.assertEqual(json.loads(out)["projects"]["alpha"]["used"]["gpu"], 1)
        code, out, _ = self.run_main("release", "--path", str(kdir))
        self.assertEqual(code, 0)
        self.assertIn("released", out)
        code, out, _ = self.run_main("ledger")
        self.assertIn("alpha", out)

    def test_two_projects_with_one_folder_name_keep_their_leases_apart(self):
        sol = self.tree()
        mine, twin = sol / "projects" / "my" / "alpha", sol / "projects" / "nv" / "alpha"
        (twin / "ai").mkdir(parents=True)
        (twin / "ai" / "manifest.json").write_text(PACK_MANIFEST)
        (twin / "k").mkdir()
        (twin / "k" / "kernel-metadata.json").write_text(json.dumps({"id": "alice/twin-gpu", "enable_gpu": True}))
        self.assertEqual(sorted(S.gather(sol, [])), ["alpha (my)", "alpha (nv)", "beta", "gamma"])
        code, out, _ = self.run_main("acquire", "--offline", "--solaris", str(sol), "--path", str(twin / "k"),
                                     "--json")
        self.assertEqual(code, 0)
        lease = json.loads(out)
        self.assertEqual((lease["project"], lease["root"]), ("alpha (nv)", str(twin)))
        code, out, _ = self.run_main("status", "--offline", "--solaris", str(sol), "--json")
        projects = json.loads(out)["projects"]
        self.assertEqual((projects["alpha (nv)"]["used"]["gpu"], projects["alpha (my)"]["used"]["gpu"]), (1, 0))
        # a lease stored under the bare folder name still follows its folder
        state = S.load_state(self.base)
        state["leases"][lease["id"]]["project"] = "alpha"
        S.write_json(self.base / "state.json", state)
        view = S.build_view(S.load_config(self.base), S.gather(sol, []), None, S.load_state(self.base), [], NOW)
        self.assertEqual(view["projects"]["alpha (nv)"]["used"]["gpu"], 1)
        # only its own folder releases it
        self.assertEqual(S.release(self.base, project="alpha", root=mine), [])
        ((closed, _h),) = S.release(self.base, project="alpha", root=twin)
        self.assertEqual(closed["id"], lease["id"])
        self.assertEqual(S.load_ledger(self.base)[0]["root"], str(twin))

    def test_acquire_refusal_exit_code(self):
        S.write_json(self.base / "sharing.json", {"only": ["beta"]})
        code, _, err = self.run_main("acquire", "--offline", "--solaris", "none", "--kind", "gpu",
                                     "--project", "alpha")
        self.assertEqual(code, S.EXIT_REFUSED)
        self.assertIn("refused", err)

    def test_stamp_command(self):
        proj = self.tmp / "p"
        (proj / "ai").mkdir(parents=True)
        (proj / "ai" / "manifest.json").write_text(PACK_MANIFEST)
        code, out, _ = self.run_main("stamp", "--path", str(proj))
        self.assertEqual(code, 0)
        self.assertEqual(S.load_stamps(self.base)[0]["root"], str(proj))


if __name__ == "__main__":
    unittest.main()
