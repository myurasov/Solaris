# rev. 3

"""Tests for shared/tools/hostclaims.py (stdlib unittest).

The host side runs against temp folders (local-root mode); a simulate.json in
each claims folder stands in for the machine's readings (CPUs, RAM, disk, GPUs,
GPU processes, boot id, clock offset). Liveness uses real processes. One test
drives the ssh path through a fake ssh program that runs the command locally.

    python3 -m unittest discover -s plugins/resource-sharing/tests -v
"""

import ast
import importlib.util
import io
import json
import os
import shlex
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import redirect_stdout

HERE = os.path.dirname(os.path.abspath(__file__))
TOOL = os.path.join(os.path.dirname(HERE), "shared", "tools", "hostclaims.py")
_spec = importlib.util.spec_from_file_location("hostclaims", TOOL)
hc = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(hc)
with open(TOOL) as _f:
    SRC = _f.read()
T4 = {"name": "Tesla T4", "mem_total_gb": 15.0}


def load(path):
    with open(path) as f:
        return json.load(f)


def text(path):
    with open(path) as f:
        return f.read()


def sim(**kw):
    base = {"boot_id": "boot-1", "cpus": 8, "ram_total_gb": 64.0, "ram_used_gb": 0.0, "disk_total_gb": 1000.0,
            "disk_free_gb": 1000.0, "gpus": [dict(T4, index=0), dict(T4, index=1)]}
    base.update(kw)
    return base


def wait_for(fn, timeout=20.0, step=0.1):
    deadline = time.time() + timeout
    while time.time() < deadline:
        v = fn()
        if v:
            return v
        time.sleep(step)
    return fn()


class Base(unittest.TestCase):
    install_args = ("--launcher", "setsid", "--rule", "heartbeat_s=1", "--reserve-cores", "0", "--reserve-ram", "0",
                    "--share-with", "*")

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="hc-test-")
        self.cwd = os.getcwd()
        os.chdir(self.tmp)
        self.saved_env = dict((k, os.environ.pop(k)) for k in list(os.environ) if k.startswith("HOSTCLAIMS_"))
        os.environ["HOSTCLAIMS_SIMULATE"] = "1"
        self.root = os.path.join(self.tmp, "root")
        os.makedirs(self.root)
        self.write_sim(sim())
        self.procs = []
        self.cli("--agent", "own", "install", *self.install_args)

    def tearDown(self):
        for c in self.all_claims():
            for pid in (c.get("pid"), (c.get("launcher") or {}).get("pid")):
                if pid:
                    try:
                        os.killpg(pid, signal.SIGKILL)
                    except OSError:
                        pass
        for p in self.procs:
            try:
                p.kill()
                p.wait(5)
            except OSError:
                pass
        os.chdir(self.cwd)
        os.environ.pop("HOSTCLAIMS_SIMULATE", None)
        os.environ.update(self.saved_env)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def all_claims(self, root=None):
        d = os.path.join(root or self.root, "claims")
        out = []
        for n in sorted(os.listdir(d)) if os.path.isdir(d) else []:
            if n.endswith(".json") and not n.startswith("."):
                out.append(load(os.path.join(d, n)))
        return out

    def write_sim(self, s, root=None):
        with open(os.path.join(root or self.root, "simulate.json"), "w") as f:
            json.dump(s, f)

    def patch_sim(self, **kw):
        path = os.path.join(self.root, "simulate.json")
        s = load(path)
        s.update(kw)
        self.write_sim(s)

    def cli(self, *args, **kw):
        code = kw.get("code", 0)
        base = list(kw.get("base") or ["--local-root", self.root])
        # these calls run outside a project, where owner actions need the explicit confirmation
        if kw.get("as_owner", True):
            base.append("--as-owner")
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = hc.main(list(base) + ["--json"] + list(args))
        out = buf.getvalue()
        data = json.loads(out) if out.strip() else {}
        if code is not None:
            self.assertEqual(rc, code, out)
        return data

    def claim(self, agent, job, *extra, **kw):
        args = ["--agent", agent, "claim", "--job", job, "--cores", str(kw.pop("cores", 1)),
                "--ram", kw.pop("ram", "1G")] + list(extra)
        return self.cli(*args, **kw)

    def sleeper(self, seconds=60):
        p = subprocess.Popen(["sleep", str(seconds)])
        self.procs.append(p)
        return p

    def set_claim(self, cid, **fields):
        path = os.path.join(self.root, "claims", cid + ".json")
        c = load(path)
        c.update(fields)
        hc.write_json(path, c)

    def history(self, root=None):
        return hc.read_jsonl(os.path.join(root or self.root, "history.jsonl"))


class TestCapacity(Base):
    def test_class_default_shares_spread_then_fill(self):
        ids = [self.claim("a", "j%d" % i, "--gpu", "any")["claim"]["resources"]["gpus"][0] for i in range(4)]
        self.assertEqual([(g["index"], g["share"]) for g in ids], [(0, 0.5), (1, 0.5), (0, 0.5), (1, 0.5)])
        r = self.claim("a", "j9", "--gpu", "any", "--class", "P2", code=hc.NOFIT)
        self.assertIn("gpu", r["reasons"][0])

    def test_p0_takes_a_whole_gpu(self):
        self.claim("a", "small", "--class", "P2", "--gpu", "any")
        g = self.claim("b", "big", "--class", "P0", "--gpu", "any")["claim"]["resources"]["gpus"][0]
        self.assertEqual((g["index"], g["share"]), (1, 1.0))
        self.claim("b", "big2", "--class", "P0", "--gpu", "any", code=hc.NOFIT)
        g = self.claim("c", "small2", "--class", "P3", "--gpu", "any")["claim"]["resources"]["gpus"][0]
        self.assertEqual((g["index"], g["share"]), (0, 0.25))

    def test_gpu_memory_is_claimed_too(self):
        self.claim("a", "m1", "--gpu", "0:0.25:12G")
        r = self.claim("b", "m2", "--gpu", "0:0.5", code=hc.NOFIT)
        self.assertIn("GPU 0", r["reasons"][0])
        g = self.claim("b", "m3", "--gpu", "0:0.5:3G")["claim"]["resources"]["gpus"][0]
        self.assertEqual((g["index"], g["mem_gb"]), (0, 3.0))

    def test_unclaimed_gpu_process_counts_as_a_whole_gpu(self):
        self.patch_sim(gpu_procs=[{"pid": 424242, "gpu": 1, "mem_gb": 2.0}])
        self.claim("a", "x", "--gpu", "1", "--class", "P3", code=hc.NOFIT)
        self.assertEqual(self.claim("a", "y", "--gpu", "any", "--class", "P3")["claim"]["resources"]["gpus"][0]["index"], 0)
        st = self.cli("status")["hosts"]["local"]
        self.assertEqual([p["pid"] for p in st["unclaimed_gpu_procs"]], [424242])
        self.assertTrue([g for g in st["free"]["gpus"] if g["index"] == 1][0]["unclaimed"])

    def test_unclaimed_gpu_memory_without_a_process(self):
        gpus = [dict(T4, index=0), dict(T4, index=1, mem_used_gb=3.0)]
        self.patch_sim(gpus=gpus)
        self.claim("a", "x", "--gpu", "1", "--class", "P3", code=hc.NOFIT)
        gpus[1]["mem_used_gb"] = 0.5
        self.patch_sim(gpus=gpus)
        g = self.claim("a", "y", "--gpu", "1:1")["claim"]["resources"]["gpus"][0]
        self.assertAlmostEqual(g["mem_gb"], 14.5)
        st = self.cli("status")["hosts"]["local"]["free"]["gpus"][1]
        self.assertAlmostEqual(st["free_mem_gb"], 0.0)

    def test_gpu_process_of_a_claim_counts_only_on_its_gpu(self):
        c = self.claim("a", "x", "--gpu", "0:0.25")["claim"]
        self.patch_sim(gpu_procs=[{"pid": 1, "gpu": 0, "mem_gb": 2.0, "claim": c["id"]},
                                  {"pid": 2, "gpu": 1, "mem_gb": 2.0, "claim": c["id"]}])
        free = dict((g["index"], g) for g in self.cli("status")["hosts"]["local"]["free"]["gpus"])
        self.assertFalse(free[0]["unclaimed"])
        self.assertAlmostEqual(free[0]["free_share"], 0.75)
        self.assertTrue(free[1]["unclaimed"])

    def test_ram_counts_unclaimed_use_and_actual_use(self):
        self.cli("--agent", "own", "install", "--reserve-ram", "4G")
        self.patch_sim(ram_used_gb=20.0)
        c = self.claim("a", "r1", ram="30G")["claim"]
        self.claim("a", "r2", ram="12G", code=hc.NOFIT)
        self.claim("a", "r3", ram="10G")
        # the first job now uses 40G, more than it reserved
        self.patch_sim(ram_used_gb=60.0, claim_rss_gb={c["id"]: 40.0})
        self.assertLessEqual(self.cli("status")["hosts"]["local"]["free"]["ram_gb"], 0.0)
        self.claim("a", "r4", ram="1G", code=hc.NOFIT)

    def test_cores_are_disjoint_and_respect_the_reserve(self):
        self.cli("--agent", "own", "install", "--reserve-cores", "1")
        a = self.claim("a", "c1", cores=3)["claim"]["resources"]["cores"]
        b = self.claim("b", "c2", cores=4)["claim"]["resources"]["cores"]
        self.assertFalse(set(a) & set(b))
        self.claim("c", "c3", cores=1, code=hc.NOFIT)

    def test_unclaimed_cpu_load_hides_cores_and_busy_cores_are_avoided(self):
        self.patch_sim(cpu_busy={"0": 1.0, "1": 1.0, "2": 1.0, "3": 0.9})
        r = self.claim("a", "c1", cores=5, code=hc.NOFIT)
        self.assertIn("4 free", r["reasons"][0])
        self.assertEqual(self.claim("a", "c2", cores=4)["claim"]["resources"]["cores"], [4, 5, 6, 7])

    def test_what_if_does_not_count_a_dropped_claims_load_as_unclaimed(self):
        a = self.claim("a", "busy", cores=6)["claim"]
        self.patch_sim(cpu_busy=dict((str(c), 1.0) for c in a["resources"]["cores"]))
        st = hc.load_state(hc.Root(self.root), {}, hc.host_cfg(hc.Root(self.root)), load(os.path.join(self.root, "simulate.json")))
        claims = hc.classify_all(hc.read_claims(hc.Root(self.root)), st)
        hc.attach_claims(st, claims)
        self.assertEqual(hc.capacity(st, claims)["cores_free"], 2)
        self.assertEqual(hc.capacity(st, claims, exclude=set([a["id"]]))["cores_free"], 8)

    def test_disk_reservations_and_floor(self):
        self.claim("a", "d1", "--disk", "800G")
        self.claim("a", "d2", "--disk", "150G", code=hc.NOFIT)
        self.claim("a", "d3", "--disk", "100G")
        self.patch_sim(disk_free_gb=50.0)
        r = self.claim("a", "d4", code=hc.NOFIT)
        self.assertIn("floor", r["reasons"][0])


class TestParallel(Base):
    def spawn(self, agent, job, *extra):
        cmd = [sys.executable, TOOL, "--local-root", self.root, "--agent", agent, "--json", "claim", "--job", job,
               "--ram", "1G"] + list(extra)
        env = dict((k, v) for k, v in os.environ.items() if not k.startswith("HOSTCLAIMS_"))
        env["HOSTCLAIMS_SIMULATE"] = "1"
        return subprocess.run(cmd, cwd=self.tmp, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              universal_newlines=True, timeout=120)

    def test_parallel_gpu_claims_never_oversubscribe(self):
        with ThreadPoolExecutor(max_workers=24) as ex:
            res = list(ex.map(lambda i: self.spawn("p%d" % i, "g", "--cores", "1", "--gpu", "any"), range(24)))
        self.assertEqual(sorted(set(r.returncode for r in res)), [0, hc.NOFIT], [r.stderr for r in res][:3])
        self.assertEqual(sum(1 for r in res if r.returncode == 0), 4)
        claims = self.all_claims()
        for idx in (0, 1):
            self.assertLessEqual(sum(g["share"] for c in claims for g in c["resources"]["gpus"] if g["index"] == idx), 1.0)
        cores = [x for c in claims for x in c["resources"]["cores"]]
        self.assertEqual(len(cores), len(set(cores)))

    def test_parallel_core_claims_never_overlap(self):
        with ThreadPoolExecutor(max_workers=12) as ex:
            res = list(ex.map(lambda i: self.spawn("q%d" % i, "c", "--cores", "3"), range(12)))
        self.assertEqual(sum(1 for r in res if r.returncode == 0), 2)
        cores = [x for c in self.all_claims() for x in c["resources"]["cores"]]
        self.assertEqual(len(cores), 6)
        self.assertEqual(len(cores), len(set(cores)))


class TestIdempotency(Base):
    def test_claim_twice_returns_the_same_claim_and_refreshes_its_heartbeat(self):
        first = self.claim("a", "j")["claim"]
        five_min_ago = hc.iso(time.time() - 300)
        self.set_claim(first["id"], heartbeat=five_min_ago)
        again = self.claim("a", "j", "--gpu", "any")
        self.assertTrue(again["existing"])
        self.assertEqual(again["claim"]["id"], first["id"])
        self.assertNotEqual(again["claim"]["heartbeat"], five_min_ago)
        self.assertEqual(len(self.all_claims()), 1)
        self.assertNotEqual(self.claim("b", "j")["claim"]["id"], first["id"])

    def test_run_twice_starts_one_job(self):
        args = ("--agent", "a", "run", "--job", "r", "--cores", "1", "--ram", "1G", "--", "sleep", "30")
        one = self.cli(*args)
        two = self.cli(*args)
        self.assertTrue(two["existing"])
        self.assertEqual(one["claim"]["id"], two["claim"]["id"])
        self.assertEqual(len(os.listdir(os.path.join(self.root, "run"))), 1)

    def test_run_of_a_job_that_just_ended_needs_rerun(self):
        args = ("--agent", "a", "run", "--job", "q", "--cores", "1", "--ram", "1G", "--", "true")
        first = self.cli(*args)
        done = os.path.join(first["claim"]["run_dir"], "done.json")
        self.assertTrue(wait_for(lambda: not self.all_claims()))
        again = self.cli(*args)
        self.assertIn("finished", again)
        self.assertEqual(len(os.listdir(os.path.join(self.root, "run"))), 1)
        self.assertTrue(os.path.exists(done))
        third = self.cli(*(args[:-2] + ("--rerun", "--", "true")))
        self.assertFalse(third["existing"])


class TestRun(Base):
    def run_job(self, *extra, **kw):
        cmd = kw.get("cmd", ["sh", "-c", "echo CVD=[$CUDA_VISIBLE_DEVICES] OMP=$OMP_NUM_THREADS ID=$HOSTCLAIMS_CLAIM_ID"])
        r = self.cli("--agent", kw.get("agent", "a"), "run", "--job", kw.get("job", "r"), "--cores", "2", "--ram", "1G",
                     *extra, "--", *cmd)
        c = r["claim"]
        done = os.path.join(c["run_dir"], "done.json")
        self.assertTrue(wait_for(lambda: os.path.exists(done) and not self.all_claims(), 30), "job did not finish")
        return c, load(done)

    def test_env_pinning_done_marker_and_release(self):
        extra_done = os.path.join(self.tmp, "markers", "r.done.json")
        c, done = self.run_job("--gpu", "1", "--done-file", extra_done)
        log = text(os.path.join(c["run_dir"], "job.log"))
        self.assertIn("CVD=[1]", log)
        self.assertIn("OMP=2", log)
        self.assertIn("ID=" + c["id"], log)
        self.assertEqual((done["exit_code"], done["reason"]), (0, "finished"))
        self.assertEqual(load(extra_done)["claim"], c["id"])
        ends = [e for e in self.history() if e["event"] == "end"]
        self.assertEqual((ends[-1]["id"], ends[-1]["exit_code"], ends[-1]["reason"]), (c["id"], 0, "finished"))
        self.assertFalse(os.path.exists(os.path.join(c["run_dir"], "hostclaims.py")))
        self.assertTrue(os.path.exists(os.path.join(c["run_dir"], "wrapper.log")))

    def test_cpu_only_claims_see_no_gpu(self):
        c, _ = self.run_job()
        self.assertIn("CVD=[]", text(os.path.join(c["run_dir"], "job.log")))

    def test_failed_job_reports_its_exit_code(self):
        _, done = self.run_job(cmd=["sh", "-c", "exit 3"])
        self.assertEqual((done["exit_code"], done["reason"]), (3, "failed"))

    def test_heartbeat_is_refreshed_while_the_job_lives(self):
        r = self.cli("--agent", "a", "run", "--job", "hb", "--cores", "1", "--ram", "1G", "--", "sleep", "5")
        cid = r["claim"]["id"]
        path = os.path.join(self.root, "claims", cid + ".json")
        self.assertTrue(wait_for(lambda: load(path).get("pid")))
        self.set_claim(cid, heartbeat="2020-01-01T00:00:00Z")
        self.assertTrue(wait_for(lambda: load(path)["heartbeat"] != "2020-01-01T00:00:00Z", 5))
        self.assertEqual(self.cli("status")["hosts"]["local"]["claims"][0]["state"], "live")

    def test_release_of_a_running_job_needs_stop(self):
        r = self.cli("--agent", "a", "run", "--job", "s", "--cores", "1", "--ram", "1G", "--", "sleep", "60")
        cid = r["claim"]["id"]
        self.assertTrue(wait_for(lambda: self.all_claims()[0].get("pid")))
        self.cli("--agent", "b", "release", "--claim", cid, code=hc.DENIED)
        self.cli("--agent", "a", "release", "--job", "s", code=hc.DENIED)
        self.assertTrue(self.cli("--agent", "a", "release", "--job", "s", "--stop")["stopping"])
        done = os.path.join(r["claim"]["run_dir"], "done.json")
        self.assertTrue(wait_for(lambda: os.path.exists(done), 20))
        self.assertEqual(load(done)["reason"], "stopped")


class TestClaimThenRun(Base):
    def test_run_starts_inside_an_earlier_bare_claim(self):
        first = self.claim("a", "two-step", "--gpu", "1", cores=2)["claim"]
        r = self.cli("--agent", "a", "run", "--job", "two-step", "--cores", "1", "--ram", "1G", "--gpu", "1", "--",
                     "sh", "-c", "echo CVD=[$CUDA_VISIBLE_DEVICES]")
        self.assertEqual(r["claim"]["id"], first["id"])
        done = os.path.join(r["claim"]["run_dir"], "done.json")
        self.assertTrue(wait_for(lambda: os.path.exists(done) and not self.all_claims(), 20))
        self.assertIn("CVD=[1]", text(os.path.join(r["claim"]["run_dir"], "job.log")))

    def test_stop_a_claimed_process_and_bad_pids(self):
        p = self.sleeper()
        self.claim("a", "adopted", "--pid", str(p.pid))
        self.cli("--agent", "a", "release", "--job", "adopted", code=hc.DENIED)
        self.assertTrue(self.cli("--agent", "a", "release", "--job", "adopted", "--stop")["released"])
        self.assertEqual(p.wait(10), -signal.SIGTERM)
        self.assertEqual([e["reason"] for e in self.history() if e["event"] == "end"], ["stopped"])
        self.assertIn("no running process", self.claim("a", "ghost", "--pid", str(p.pid), code=hc.ERROR)["error"])


class TestStale(Base):
    def old(self, cid, minutes=16):
        self.set_claim(cid, heartbeat=hc.iso(time.time() - minutes * 60))

    def test_dead_pid_and_old_heartbeat_moves_to_stale(self):
        p = self.sleeper()
        c = self.claim("a", "x", "--pid", str(p.pid))["claim"]
        p.kill()
        p.wait()
        self.old(c["id"], 14)
        self.assertEqual(self.cli("reap")["hosts"]["local"]["reaped"], [])
        self.old(c["id"], 16)
        r = self.cli("reap")["hosts"]["local"]
        self.assertEqual([x["id"] for x in r["reaped"]], [c["id"]])
        moved = load(os.path.join(self.root, "stale", c["id"] + ".json"))
        self.assertIn("heartbeat", moved["stale"]["reason"])
        self.assertEqual(moved["job"], "x")
        self.assertEqual(self.all_claims(), [])
        self.assertEqual([e["id"] for e in self.history() if e["event"] == "stale"], [c["id"]])

    def test_a_live_process_keeps_an_old_claim(self):
        p = self.sleeper()
        c = self.claim("a", "x", "--pid", str(p.pid))["claim"]
        self.old(c["id"], 60)
        self.assertEqual(self.cli("reap")["hosts"]["local"]["reaped"], [])

    def test_changed_boot_id_is_stale_at_once(self):
        p = self.sleeper()
        c = self.claim("a", "x", "--pid", str(p.pid))["claim"]
        self.patch_sim(boot_id="boot-2")
        r = self.cli("reap")["hosts"]["local"]
        self.assertEqual([x["id"] for x in r["reaped"]], [c["id"]])
        self.assertIn("reboot", r["reaped"][0]["reason"])

    def test_bare_claim_goes_stale_after_15_minutes(self):
        c = self.claim("a", "x")["claim"]
        self.patch_sim(clock_offset_s=14 * 60)
        self.assertEqual(self.cli("reap")["hosts"]["local"]["reaped"], [])
        self.patch_sim(clock_offset_s=16 * 60)
        self.assertEqual([x["id"] for x in self.cli("reap")["hosts"]["local"]["reaped"]], [c["id"]])

    def test_orphan_is_flagged_and_kept(self):
        r = self.cli("--agent", "a", "run", "--job", "o", "--cores", "1", "--ram", "1G", "--", "sleep", "60")
        cid = r["claim"]["id"]
        c = wait_for(lambda: [x for x in self.all_claims() if x.get("pid") and (x.get("launcher") or {}).get("pid")])[0]
        os.kill(c["launcher"]["pid"], signal.SIGKILL)
        self.assertTrue(wait_for(lambda: hc.proc_start(c["launcher"]["pid"]) is None, 10))
        self.old(cid, 30)
        st = self.cli("status")["hosts"]["local"]
        self.assertEqual(st["claims"][0]["state"], "orphan")
        reap = self.cli("reap")["hosts"]["local"]
        self.assertEqual(([x["id"] for x in reap["orphans"]], reap["reaped"]), ([cid], []))
        os.killpg(c["pid"], signal.SIGKILL)
        self.assertTrue(wait_for(lambda: hc.proc_start(c["pid"]) is None, 10))
        self.assertEqual([x["id"] for x in self.cli("reap")["hosts"]["local"]["reaped"]], [cid])


class TestYield(Base):
    def test_rules(self):
        pid = lambda: str(self.sleeper().pid)
        p2 = self.claim("a", "explore", "--class", "P2", "--gpu", "any", "--pid", pid())["claim"]["id"]
        p1 = self.claim("a", "keep", "--class", "P1", "--gpu", "any", "--pid", pid())["claim"]["id"]
        young = self.cli("--agent", "b", "yield", "--claim", p2, "--class", "P0", code=hc.DENIED)
        self.assertIn("younger", young["error"])
        self.patch_sim(clock_offset_s=25 * 60)
        self.cli("--agent", "b", "yield", "--claim", p1, "--class", "P0", code=hc.DENIED)
        self.cli("--agent", "b", "yield", "--claim", p2, "--class", "P2", code=hc.DENIED)
        self.cli("--agent", "b", "yield", "--claim", p2, "--class", "P3", code=hc.DENIED)
        ok = self.cli("--agent", "b", "yield", "--claim", p2, "--class", "P1", "--reason", "slot due")
        self.assertEqual(ok["yield"]["by_agent"], "b")
        self.assertTrue(self.cli("--agent", "c", "yield", "--claim", p2, "--class", "P0")["existing"])
        borrowed = self.claim("a", "extra", "--class", "P2", "--borrowed", "--pid", pid())["claim"]["id"]
        self.patch_sim(clock_offset_s=50 * 60)
        self.cli("--agent", "b", "yield", "--claim", borrowed, "--class", "P2", "--borrowed", code=hc.DENIED)
        self.cli("--agent", "b", "yield", "--claim", borrowed, "--class", "P2")

    def test_request_is_acknowledged_and_the_job_checkpoints(self):
        job = "trap 'echo checkpoint saved; exit 0' TERM; while :; do sleep 0.2; done"
        r = self.cli("--agent", "a", "run", "--job", "y", "--class", "P2", "--gpu", "any", "--cores", "1", "--ram", "1G",
                     "--", "sh", "-c", job)
        cid, rd = r["claim"]["id"], r["claim"]["run_dir"]
        self.assertTrue(wait_for(lambda: self.all_claims()[0].get("pid")))
        self.patch_sim(clock_offset_s=25 * 60)
        self.cli("--agent", "b", "yield", "--claim", cid, "--class", "P0", "--job", "deadline")
        self.assertTrue(wait_for(lambda: os.path.exists(os.path.join(rd, "done.json")), 20))
        done = load(os.path.join(rd, "done.json"))
        self.assertEqual((done["reason"], done["exit_code"]), ("yielded", 0))
        self.assertIn("checkpoint saved", text(os.path.join(rd, "job.log")))
        events = [e["event"] for e in self.history()]
        self.assertIn("yield-request", events)
        self.assertIn("yield-ack", events)
        self.assertEqual([e["reason"] for e in self.history() if e["event"] == "end"], ["yielded"])

    def test_a_job_that_ignores_the_request_is_killed_after_the_grace(self):
        r = self.cli("--agent", "a", "run", "--job", "stubborn", "--class", "P3", "--grace-min", "0.03", "--cores", "1",
                     "--ram", "1G", "--", "sh", "-c", "trap '' TERM; while :; do sleep 0.2; done")
        cid, rd = r["claim"]["id"], r["claim"]["run_dir"]
        self.assertTrue(wait_for(lambda: self.all_claims()[0].get("pid")))
        self.patch_sim(clock_offset_s=25 * 60)
        self.cli("--agent", "b", "yield", "--claim", cid, "--class", "P0")
        self.assertTrue(wait_for(lambda: os.path.exists(os.path.join(rd, "done.json")), 30))
        done = load(os.path.join(rd, "done.json"))
        self.assertEqual((done["reason"], done["signal"], done["exit_code"]), ("yielded", "SIGKILL", 137))

    def test_refusal_lists_the_smallest_set_to_ask(self):
        pid = lambda: str(self.sleeper().pid)
        a = self.claim("a", "p3", "--class", "P3", "--gpu", "0:0.5", "--pid", pid())["claim"]["id"]
        b = self.claim("a", "p2", "--class", "P2", "--gpu", "0:0.5", "--pid", pid())["claim"]["id"]
        self.claim("c", "p1", "--class", "P1", "--gpu", "1:1", "--pid", pid())
        r = self.claim("b", "urgent", "--class", "P0", "--gpu", "any", code=hc.NOFIT)
        self.assertEqual(r["yield_candidates"], [])
        self.patch_sim(clock_offset_s=25 * 60)
        r = self.claim("b", "urgent", "--class", "P0", "--gpu", "any", code=hc.NOFIT)
        self.assertEqual(sorted(r["yield_candidates"]), sorted([a, b]))
        r = self.claim("b", "half", "--class", "P1", "--gpu", "any", code=hc.NOFIT)
        self.assertEqual(r["yield_candidates"], [a])


class TestStatusTags(Base):
    def tag(self):
        return self.cli("status")["hosts"]["local"]["tag"]

    def test_tags(self):
        self.assertEqual(self.tag(), "free")
        c = self.claim("a", "x", "--class", "P2", "--gpu", "any")["claim"]
        self.assertEqual(self.tag(), "partly-busy")
        self.claim("a", "y", "--class", "P0", "--gpu", "1")
        self.claim("a", "z", "--gpu", "0:0.75")
        self.assertEqual(self.tag(), "busy")
        self.assertEqual(load(os.path.join(self.root, "status.json"))["tag"], "busy")
        self.cli("--agent", "a", "release", "--claim", c["id"])
        self.assertEqual(self.tag(), "partly-busy")

    def test_unclaimed_use_and_draining(self):
        self.patch_sim(gpu_procs=[{"pid": 5, "gpu": 0, "mem_gb": 1.0}])
        self.assertEqual(self.tag(), "partly-busy")
        self.patch_sim(gpu_procs=[])
        self.cli("--agent", "own", "install", "--mode", "draining")
        self.assertEqual(self.tag(), "busy")
        self.assertIn("draining", self.claim("a", "x", code=hc.NOFIT)["reasons"][0])


class TestUsage(Base):
    def test_ledger_window_and_live_claims(self):
        now = time.time()
        hist = os.path.join(self.root, "history.jsonl")
        gpu = [{"index": 0, "name": "Tesla T4", "share": 0.5}]
        hc.append_jsonl(hist, {"event": "end", "agent": "a", "job": "j1", "start": hc.iso(now - 3 * 3600),
                               "end": hc.iso(now - 3600), "gpus": gpu, "cores": 4})
        hc.append_jsonl(hist, {"event": "stale", "agent": "b", "job": "j2", "start": hc.iso(now - 30 * 3600),
                               "end": hc.iso(now - 20 * 3600), "gpus": [dict(gpu[0], share=1.0)], "cores": 2})
        hc.append_jsonl(hist, {"event": "end", "agent": "b", "job": "old", "start": hc.iso(now - 50 * 3600),
                               "end": hc.iso(now - 40 * 3600), "gpus": gpu, "cores": 2})
        c = self.claim("a", "live", "--gpu", "1:0.25", cores=2)["claim"]
        self.set_claim(c["id"], created=hc.iso(now - 3600))
        with open(os.path.join(self.tmp, "policy.json"), "w") as f:
            json.dump({"weights": {"a": 3, "b": 1}}, f)
        u = self.cli("--policy", os.path.join(self.tmp, "policy.json"), "usage")
        a, b = u["agents"]["a"], u["agents"]["b"]
        self.assertAlmostEqual(a["gpu_hours"]["Tesla T4"], 0.5 * 2 + 0.25 * 1, places=2)
        self.assertAlmostEqual(a["cpu_hours"], 4 * 2 + 2 * 1, places=2)
        self.assertAlmostEqual(b["gpu_hours"]["Tesla T4"], 4.0, places=2)
        self.assertAlmostEqual(b["cpu_hours"], 8.0, places=2)
        self.assertEqual((a["claims"], b["claims"]), (2, 1))
        self.assertEqual((a["due_share"], b["due_share"]), (0.75, 0.25))
        week = self.cli("usage", "--window", "7d")["agents"]["b"]
        self.assertAlmostEqual(week["gpu_hours"]["Tesla T4"], 10 * 1.0 + 10 * 0.5, places=2)


class TestPool(Base):
    def pool(self, *args, **kw):
        return self.cli("pool", *args, "--file", os.path.join(self.tmp, "coord", "pools.json"), **kw)

    def test_caps_capacity_idempotency_and_ownership(self):
        self.pool("define", "--agent", "own", "--name", "sessions", "--capacity", "5", "--cap", "*=3", "--borrow-max-hours", "2")
        for i in range(3):
            self.pool("acquire", "--agent", "a", "--name", "sessions", "--id", "k%d" % i)
        self.assertTrue(self.pool("acquire", "--agent", "a", "--name", "sessions", "--id", "k0")["existing"])
        self.assertIn("cap", self.pool("acquire", "--agent", "a", "--name", "sessions", "--id", "k3",
                                       code=hc.DENIED)["error"])
        self.pool("acquire", "--agent", "b", "--name", "sessions", "--id", "m0")
        self.pool("acquire", "--agent", "b", "--name", "sessions", "--id", "m1")
        self.assertIn("full", self.pool("acquire", "--agent", "b", "--name", "sessions", "--id", "m2",
                                        code=hc.NOFIT)["error"])
        self.pool("release", "--agent", "a", "--name", "sessions", "--id", "m0", code=hc.DENIED)
        self.assertTrue(self.pool("release", "--agent", "b", "--name", "sessions", "--id", "m0")["released"])
        show = self.pool("show")["pools"][0]
        self.assertEqual((show["used"], show["agents"]["a"]["used"], show["agents"]["b"]["used"]), (4, 3, 1))

    def test_borrowing_only_while_others_hold_none(self):
        self.pool("define", "--agent", "own", "--name", "gpu", "--capacity", "3", "--cap", "*=1", "--borrow-max-hours", "2")
        self.pool("acquire", "--agent", "a", "--name", "gpu", "--id", "g0")
        self.pool("acquire", "--agent", "a", "--name", "gpu", "--id", "g1", "--borrow", code=hc.DENIED)
        h = self.pool("acquire", "--agent", "a", "--name", "gpu", "--id", "g1", "--borrow", "--hours", "1.5")["hold"]
        self.assertTrue(h["borrowed"])
        self.pool("release", "--agent", "a", "--name", "gpu", "--id", "g1")
        self.pool("acquire", "--agent", "b", "--name", "gpu", "--id", "b0")
        self.assertIn("cannot borrow", self.pool("acquire", "--agent", "a", "--name", "gpu", "--id", "g2", "--borrow",
                                                 "--hours", "1", code=hc.DENIED)["error"])

    def test_budget_window_and_hold_expiry(self):
        path = os.path.join(self.tmp, "budget.json")
        t0 = 1_900_000_000.0
        req = {"op": "pool", "file": path}
        hc.host_op(dict(req, action="define", agent="own", defs={"gpu-hours": {"capacity": 30, "caps": {"*": 15}, "window_hours": 168}}))
        r = hc.host_op(dict(req, action="acquire", name="gpu-hours", agent="a", id="k1", amount=10, now=t0))
        self.assertTrue(r["ok"], r)
        hc.host_op(dict(req, action="release", name="gpu-hours", agent="a", id="k1", actual=12, now=t0 + 3600))
        r = hc.host_op(dict(req, action="acquire", name="gpu-hours", agent="a", id="k2", amount=4, now=t0 + 7200))
        self.assertEqual((r["ok"], r["code"]), (False, hc.DENIED))
        show = hc.host_op(dict(req, action="show", now=t0 + 170 * 3600))["pools"][0]
        self.assertEqual(show["used"], 0)
        r = hc.host_op(dict(req, action="acquire", name="gpu-hours", agent="a", id="k3", amount=14, hours=1,
                            now=t0 + 170 * 3600))
        self.assertTrue(r["ok"], r)
        self.assertEqual(hc.host_op(dict(req, action="show", now=t0 + 172 * 3600))["pools"][0]["holds"], [])

    def test_preset(self):
        preset = os.path.join(self.tmp, "preset.json")
        with open(preset, "w") as f:
            json.dump({"_rev": 1, "pools": {"x": {"capacity": 2, "caps": {"*": 1}}, "y": {"capacity": 9}}}, f)
        names = [p["name"] for p in self.pool("define", "--agent", "own", "--preset", preset)["pools"]]
        self.assertEqual(names, ["x", "y"])


class TestUninstall(Base):
    def test_refusals_then_removal(self):
        c = self.claim("a", "x")["claim"]
        self.cli("--agent", "own", "uninstall", code=hc.DENIED)
        self.cli("--agent", "a", "release", "--claim", c["id"])
        self.cli("--agent", "guest", "uninstall", code=hc.DENIED)
        self.cli("--agent", "own", "pool", "define", "--file", os.path.join(self.root, "pools", "p.json"), "--name", "s", "--capacity", "1")
        self.cli("--agent", "a", "pool", "acquire", "--file", os.path.join(self.root, "pools", "p.json"), "--name", "s",
                 "--id", "h")
        self.assertIn("pool", self.cli("--agent", "own", "uninstall", code=hc.DENIED)["error"])
        self.cli("--agent", "a", "pool", "release", "--file", os.path.join(self.root, "pools", "p.json"), "--name", "s",
                 "--id", "h")
        open(os.path.join(self.root, "notes.txt"), "w").close()
        self.assertIn("did not create", self.cli("--agent", "own", "uninstall", code=hc.DENIED)["error"])
        os.unlink(os.path.join(self.root, "notes.txt"))
        self.assertTrue(self.cli("--agent", "own", "uninstall")["removed"])
        self.assertFalse(os.path.exists(self.root))


class TestOwnership(Base):
    def test_only_the_owner_changes_the_host(self):
        self.cli("--agent", "guest", "install", "--mode", "draining", code=hc.DENIED)
        self.cli("--agent", "guest", "lease", "--set", "kind=free", "--set", "planned_end=+48h", code=hc.DENIED)
        lease = self.cli("--agent", "own", "lease", "--set", "kind=free", "--set", "planned_end=+48h")["lease"]
        self.assertEqual(lease["kind"], "free")
        self.cli("--agent", "own", "install", "--owner", "heir")
        self.cli("--agent", "own", "install", "--mode", "shared", code=hc.DENIED)
        self.cli("--agent", "heir", "install", "--mode", "shared")

    def test_new_host_is_installed_only_by_its_inventory_owner(self):
        root = os.path.join(self.tmp, "r2")
        inv = os.path.join(self.tmp, "inv.json")
        with open(inv, "w") as f:
            json.dump([{"name": "h", "target": "local", "root": root, "owner": "p"}], f)
        self.cli("--agent", "q", "install", base=["--hosts", inv], code=hc.DENIED)
        self.assertFalse(os.path.exists(root))
        r = self.cli("--agent", "p", "install", "--launcher", "setsid", base=["--hosts", inv])
        self.assertEqual(r["hosts"]["h"]["host"]["owner"], "p")

    def test_share_with_admits_only_named_projects(self):
        self.cli("--agent", "own", "install", "--share-with", "b")
        self.claim("b", "x")
        self.claim("own", "y")
        self.assertIn("not shared", self.claim("c", "z", code=hc.NOFIT)["reasons"][0])
        self.cli("--agent", "own", "install", "--share-with", "*")
        self.claim("c", "z")


class TestLeaseKinds(Base):
    def test_none_free_and_paid(self):
        self.claim("a", "n")
        self.cli("--agent", "own", "lease", "--set", "kind=free", "--set", "planned_end=+1h")
        r = self.claim("a", "f", "--hours", "2")
        self.assertIn("owner must renew", r["warnings"][0])
        self.cli("--agent", "own", "lease", "--set", "kind=paid", code=hc.USAGE)
        self.cli("--agent", "own", "lease", "--set", "kind=paid", "--set", "usd_per_hour=2.5")
        self.assertIn("--hours", self.claim("a", "p1", code=hc.NOFIT)["reasons"][0])
        self.assertIn("planned end", self.claim("a", "p2", "--hours", "2", code=hc.NOFIT)["reasons"][0])
        self.claim("a", "p3", "--hours", "0.5")
        self.cli("--agent", "own", "lease", "--set", "kind=none")
        self.assertEqual(self.cli("lease")["lease"], {"kind": "none"})


class TestRequests(Base):
    def setUp(self):
        Base.setUp(self)
        self.cli("--agent", "own", "lease", "--set", "kind=paid", "--set", "planned_end=+1h", "--set", "usd_per_hour=3",
                 "--set", "gpu_type=Tesla T4", "--set", "instance=box-1")

    def extend(self, job, code=0):
        return self.cli("--agent", "guest", "extend", "--job", job, "--hours", "3", "--cores", "2", "--ram", "4G",
                        "--gpus", "1", "--new-usd-per-hour", "6", code=code)

    def fit(self, job):
        return self.cli("--agent", "guest", "fit", "--job", job, "--hours", "3", "--cores", "2", "--ram", "4G",
                        "--gpus", "1", "--new-usd-per-hour", "6")["recommendation"]

    def test_request_approve(self):
        self.assertEqual(self.fit("long")["action"], "reuse-with-extension")
        r = self.extend("long")["request"]
        self.assertAlmostEqual(r["extra_hours"], 2.25, places=1)
        self.assertAlmostEqual(r["est_extra_usd"], 6.75, places=1)
        self.assertEqual((r["state"], r["owner"]), ("pending", "own"))
        self.assertTrue(self.extend("long")["existing"])
        st = self.cli("status")["hosts"]["local"]
        self.assertEqual([q["id"] for q in st["requests"] if q["state"] == "pending"], [r["id"]])
        self.assertEqual(self.fit("long")["action"], "wait-for-owner")
        self.cli("--agent", "guest", "approve", "--request", r["id"], code=hc.DENIED)
        self.claim("guest", "long", "--hours", "3", code=hc.NOFIT)
        q = self.cli("--agent", "own", "approve", "--request", r["id"])["request"]
        self.assertEqual(q["state"], "approved")
        self.claim("guest", "long", "--hours", "3")
        self.cli("--agent", "own", "decline", "--request", r["id"], code=hc.DENIED)

    def test_decline_then_launch_new(self):
        r = self.extend("other")["request"]
        self.cli("--agent", "own", "decline", "--request", r["id"], "--reason", "deleting tonight")
        rec = self.fit("other")
        self.assertEqual(rec["action"], "launch-new")

    def test_timeout_then_launch_new(self):
        r = self.extend("slow")["request"]
        self.assertAlmostEqual(hc.parse_iso(r["expires"]) - hc.parse_iso(r["created"]), 120 * 60, delta=2)
        self.patch_sim(clock_offset_s=121 * 60)
        st = self.cli("status")["hosts"]["local"]
        self.assertEqual([q["state"] for q in st["requests"]], ["expired"])
        self.assertIn("expired", self.cli("--agent", "own", "approve", "--request", r["id"], code=hc.DENIED)["error"])
        self.assertEqual(self.fit("slow")["action"], "launch-new")
        self.assertEqual(self.cli("reap")["hosts"]["local"]["expired_requests"], [])

    def test_maintenance_and_objection(self):
        m = self.cli("--agent", "guest", "request", "--type", "maintenance", "--reason", "GPU 1 driver fell off")["request"]
        self.assertEqual((m["type"], m["owner"]), ("maintenance", "own"))
        self.cli("--agent", "guest", "request", "--type", "objection", "--reason", "x", code=hc.USAGE)
        o = self.cli("--agent", "guest", "request", "--type", "objection", "--reason", "sweep resumes at night",
                     "--until", "+6h")["request"]
        self.cli("--agent", "guest", "approve", "--request", o["id"], code=hc.DENIED)
        self.cli("--agent", "own", "approve", "--request", o["id"])
        self.cli("--agent", "own", "decline", "--request", m["id"])
        self.assertTrue(self.cli("status")["hosts"]["local"]["keep_until"])


class TestFitRanking(unittest.TestCase):
    fp = hc.FIT_DEFAULTS
    now = 1_900_000_000.0

    def row(self, name, kind="none", end_h=None, rate=0.0, start=0.0, **kw):
        lease = {"kind": kind}
        if end_h is not None:
            lease["planned_end"] = hc.iso(self.now + end_h * 3600)
        if kind == "paid":
            lease["usd_per_hour"] = rate
        r = {"name": name, "ok": True, "installed": True, "now": self.now, "lease": lease, "start_in_min": start,
             "fits_now": start == 0, "hw_reasons": [], "reason_kinds": [], "contention": 0.0, "requests": []}
        r.update(kw)
        return r

    def rank(self, rows, hours=3.0, usd=12.0):
        return hc.rank_fit(rows, hours, self.fp, {"wait_min": 35.0, "usd": usd, "usd_per_hour": 3.0})

    def test_reuse_as_is_wins_and_prefers_less_contention(self):
        table, rec = self.rank([self.row("busy-gpu", contention=0.5), self.row("idle-gpu"),
                                self.row("paid-short", "paid", end_h=1, rate=3)])
        self.assertEqual((rec["action"], rec["host"]), ("reuse", "idle-gpu"))
        self.assertEqual([e["verdict"] for e in table], ["as-is", "as-is", "extension"])

    def test_extension_only_for_a_really_good_fit(self):
        rec = self.rank([self.row("paid", "paid", end_h=2, rate=3)])[1]
        self.assertEqual((rec["action"], rec["host"]), ("reuse-with-extension", "paid"))
        self.assertAlmostEqual(rec["extra_hours"], 1.25)
        self.assertEqual(self.rank([self.row("paid", "paid", end_h=2, rate=6)], usd=12.0)[1]["action"], "launch-new")
        self.assertEqual(self.rank([self.row("paid", "paid", end_h=2, rate=3, start=30)])[1]["action"], "launch-new")
        self.assertEqual(self.rank([self.row("paid", "paid", end_h=-1, rate=1)], hours=3)[1]["action"], "launch-new")

    def test_waiting_and_refusals(self):
        self.assertEqual(self.rank([self.row("later", start=60)])[1]["action"], "reuse")
        rec = self.rank([self.row("much-later", start=120)])[1]
        self.assertEqual(rec["action"], "launch-new")
        self.assertIn("much-later", rec["why"])
        pend = self.row("paid", "paid", end_h=2, rate=3, requests=[{"id": "r1", "state": "pending"}])
        self.assertEqual(self.rank([pend])[1]["action"], "wait-for-owner")
        declined = self.row("paid", "paid", end_h=2, rate=3, requests=[{"id": "r1", "state": "declined"}])
        table, rec = self.rank([declined])
        self.assertEqual((table[0]["verdict"], rec["action"]), ("refused", "launch-new"))

    def test_other_rows_are_listed_not_chosen(self):
        rows = [{"name": "down", "ok": False, "error": "no route"}, {"name": "bare", "ok": True, "installed": False},
                self.row("wrong-gpu", hw_reasons=["gpu: 0 matching GPU(s)"]),
                self.row("private", reason_kinds=["mode"], reasons=["not shared"]),
                self.row("free-lease", "free", end_h=1)]
        table, rec = self.rank(rows)
        self.assertEqual([e["verdict"] for e in table], ["unreachable", "not-installed", "no-match", "not-admitted", "as-is"])
        self.assertEqual((rec["action"], rec["host"]), ("reuse", "free-lease"))
        self.assertIn("extended", rec["why"])

    def test_new_instance_estimate(self):
        new = hc.new_instance(self.fp, 2.0, "A100", usd=None, wait=None, stage_min=10)
        self.assertEqual((new["wait_min"], new["usd"]), (45.0, None))
        fp = dict(self.fp, new_instance=dict(self.fp["new_instance"], usd_per_hour={"a100": 2.0}))
        new = hc.new_instance(fp, 2.0, "NVIDIA A100 80GB", stage_min=0)
        self.assertAlmostEqual(new["usd"], 2.0 * (32 + 120 + 7) / 60.0, places=2)


class TestFitIntegration(Base):
    def test_fake_inventory(self):
        hosts = []
        for name, lease, gpus in (("owned-busy", {}, [dict(T4, index=0, mem_used_gb=5.0)]),
                                  ("paid-short", {"kind": "paid", "planned_end": "+1h", "usd_per_hour": 1.0},
                                   [dict(T4, index=0)]),
                                  ("big-gpu", {}, [{"index": 0, "name": "NVIDIA A100", "mem_total_gb": 80.0}])):
            root = os.path.join(self.tmp, name)
            os.makedirs(root)
            self.write_sim(sim(gpus=gpus), root)
            hosts.append({"name": name, "target": "local", "root": root, "owner": "own"})
        inv = os.path.join(self.tmp, "inv.json")
        with open(inv, "w") as f:
            json.dump(hosts, f)
        for h in hosts:
            self.cli("--agent", "own", "install", "--host", h["name"], "--launcher", "setsid", "--share-with", "*",
                     base=["--hosts", inv])
        self.cli("--agent", "own", "lease", "--host", "paid-short", "--set", "kind=paid", "--set", "planned_end=+1h",
                 "--set", "usd_per_hour=1", base=["--hosts", inv])
        out = self.cli("--agent", "g", "fit", "--gpu-type", "T4", "--gpus", "1", "--hours", "2", "--cores", "1",
                       "--ram", "1G", "--new-usd-per-hour", "3", base=["--hosts", inv])
        verdicts = dict((e["host"], e["verdict"]) for e in out["hosts"])
        self.assertEqual(verdicts, {"owned-busy": "busy", "paid-short": "extension", "big-gpu": "no-match"})
        self.assertEqual((out["recommendation"]["action"], out["recommendation"]["host"]),
                         ("reuse-with-extension", "paid-short"))
        self.assertIn("extend --host paid-short", out["recommendation"]["extend_cmd"])


class TestMixedInventory(Base):
    def test_hosts_without_hostclaims_are_skipped_not_errors(self):
        bare = os.path.join(self.tmp, "bare")
        inv = os.path.join(self.tmp, "inv.json")
        with open(inv, "w") as f:
            json.dump([{"name": "ready", "target": "local", "root": self.root, "owner": "own"},
                       {"name": "bare", "target": "local", "root": bare, "owner": "own"}], f)
        base = ["--hosts", inv, "--agent", "own"]
        self.assertFalse(self.cli("status", base=base)["hosts"]["bare"]["installed"])
        self.assertFalse(self.cli("reap", base=base)["hosts"]["bare"]["installed"])
        self.cli("claim", "--host", "ready", "--job", "j", "--cores", "1", "--ram", "1G", base=base)
        self.assertIn("own", self.cli("usage", base=base)["agents"])
        actions = dict((r["host"], r["recommendation"]["action"]) for r in self.cli("audit", base=base)["hosts"])
        self.assertEqual(actions, {"ready": "keep", "bare": "check"})
        self.assertFalse(os.path.exists(bare))


class TestDiscovery(Base):
    def project(self, rel, pack, slug, share_with, hosts):
        root = os.path.join(self.tmp, "sol", "projects", *rel.split("/"))
        os.makedirs(os.path.join(root, pack, ".memory"))
        with open(os.path.join(root, pack, "manifest.json"), "w") as f:
            json.dump({"name": slug}, f)
        with open(os.path.join(root, pack, ".memory", "resource-sharing.json"), "w") as f:
            json.dump({"project": slug, "share_with": share_with}, f)
        entries = []
        for h in hosts:
            hroot = os.path.join(self.tmp, "hosts", slug + "-" + h["name"])
            os.makedirs(hroot)
            self.write_sim(sim(), hroot)
            entries.append(dict(h, target="local", root=hroot))
        with open(os.path.join(root, pack, ".memory", "hosts.json"), "w") as f:
            json.dump(entries, f)
        return root

    def setUp(self):
        Base.setUp(self)
        # a shares with b and c; c shares with a (mutual); b shares with nobody (one way from a)
        self.a = self.project("my/proj-a", "ai", "proj-a", ["proj-b", "proj-c", "proj-gone"],
                              [{"name": "gpu-1"}, {"name": "borrowed", "owner": "proj-x"}])
        self.b = self.project("nv/team/proj-b", "aipack", "proj-b", [], [{"name": "gpu-1"}])
        self.c = self.project("my/proj-c", "ai", "proj-c", ["proj-a"], [{"name": "cpu-1"}])

    def inv(self, project):
        buf = io.StringIO()
        with redirect_stdout(buf):
            ctx = hc.Ctx(hc.build_parser().parse_args(["--project", project, "status"]))
            inv = ctx.inventory()
        return ctx, dict((h["name"], (h["project"], h["owner"])) for h in inv)

    def test_one_way_and_mutual_sharing(self):
        ctx, b = self.inv(self.b)
        self.assertEqual(ctx.agent, "proj-b")
        self.assertEqual(b, {"gpu-1": ("proj-b", "proj-b"), "proj-a/gpu-1": ("proj-a", "proj-a")})
        ctx, a = self.inv(self.a)
        self.assertEqual(a, {"gpu-1": ("proj-a", "proj-a"), "borrowed": ("proj-a", "proj-x"),
                             "cpu-1": ("proj-c", "proj-c")})
        self.assertTrue(any("proj-gone" in n for n in ctx.notes))
        _, c = self.inv(self.c)
        self.assertEqual(c, {"cpu-1": ("proj-c", "proj-c"), "gpu-1": ("proj-a", "proj-a")})

    def test_owner_installs_and_guests_claim_and_ask(self):
        own = ["--project", self.a]
        self.cli("install", "--host", "gpu-1", "--launcher", "setsid", base=own)
        host = load(os.path.join(self.tmp, "hosts", "proj-a-gpu-1", "host.json"))
        self.assertEqual((host["owner"], host["share_with"]), ("proj-a", ["proj-b", "proj-c", "proj-gone"]))
        guest = ["--project", self.b]
        self.cli("install", "--host", "proj-a/gpu-1", base=guest, code=hc.DENIED)
        self.cli("claim", "--host", "proj-a/gpu-1", "--job", "j", "--cores", "1", "--ram", "1G", base=guest)
        self.cli("lease", "--host", "proj-a/gpu-1", "--set", "kind=free", base=guest, code=hc.DENIED)
        self.cli("--agent", "proj-d", "claim", "--host", "proj-a/gpu-1", "--job", "j", "--cores", "1", "--ram", "1G",
                 base=guest, code=hc.NOFIT)
        st = self.cli("status", "--host", "proj-a/gpu-1", base=guest)["hosts"]["proj-a/gpu-1"]
        self.assertEqual((st["owner"], st["project"]), ("proj-a", "proj-a"))


class TestAudit(Base):
    ap = hc.AUDIT_DEFAULTS
    now = 1_900_000_000.0

    def row(self, kind="paid", idle_min=0, live=0, end_h=10, **kw):
        lease = {"kind": kind, "planned_end": hc.iso(self.now + end_h * 3600), "usd_per_hour": 2.0}
        r = {"ok": True, "installed": True, "now": self.now, "lease": lease, "idle_s": idle_min * 60,
             "live_claims": [{"id": "c%d" % i, "eta": None} for i in range(live)], "requests": [], "keep_until": None}
        r.update(kw)
        return r

    def test_recommendations(self):
        rec = lambda **kw: hc.audit_recommend(self.row(**kw), self.ap)["action"]
        self.assertEqual(rec(idle_min=45), "delete")
        self.assertEqual(rec(idle_min=10), "keep")
        self.assertEqual(rec(idle_min=45, live=1), "keep")
        self.assertEqual(rec(end_h=-1), "delete")
        self.assertEqual(rec(idle_min=45, requests=[{"id": "r", "type": "extension", "agent": "g", "extra_hours": 2}]),
                         "extend")
        self.assertEqual(rec(idle_min=45, keep_until=hc.iso(self.now + 3600)), "keep")
        self.assertEqual(rec(kind="free", idle_min=25 * 60), "release")
        self.assertEqual(rec(kind="free", idle_min=60), "keep")
        self.assertEqual(rec(kind="free", live=1, end_h=20), "extend")
        self.assertEqual(rec(kind="none", idle_min=10000), "keep")
        self.assertEqual(hc.audit_recommend({"ok": False, "error": "timeout", "lease": {"kind": "paid"}}, self.ap)["action"],
                         "check")

    def test_owner_audit_on_a_host(self):
        self.cli("--agent", "own", "lease", "--set", "kind=paid", "--set", "planned_end=+5h", "--set", "usd_per_hour=2",
                 "--set", "started=-", code=hc.USAGE)
        self.cli("--agent", "own", "lease", "--set", "kind=paid", "--set", "planned_end=+5h", "--set", "usd_per_hour=2")
        c = self.claim("guest", "j", "--gpu", "any", "--hours", "1")["claim"]
        self.cli("--agent", "guest", "release", "--claim", c["id"])
        out = self.cli("--agent", "own", "audit")["hosts"][0]
        self.assertEqual(out["recommendation"]["action"], "keep")
        self.assertIn("guest", out["result"]["usage"])
        self.patch_sim(clock_offset_s=2 * 3600)
        out = self.cli("--agent", "own", "audit")["hosts"][0]
        self.assertEqual(out["recommendation"]["action"], "delete")
        self.assertAlmostEqual(out["result"]["cost_so_far_usd"], 4.0, delta=0.1)
        self.assertEqual(self.cli("--agent", "guest", "audit")["hosts"], [])


class TestTransport(Base):
    def test_ssh_path_with_a_fake_ssh(self):
        home = os.path.join(self.tmp, "home")
        root = os.path.join(home, ".solaris", "claims")
        os.makedirs(root)
        self.write_sim(sim(), root)
        fake = os.path.join(self.tmp, "fake-ssh")
        with open(fake, "w") as f:
            f.write('#!/bin/sh\nwhile [ $# -gt 0 ]; do case "$1" in -o|-i|-p|-l|-F|-J) shift 2;; -*) shift;; *) break;; '
                    'esac; done\nshift\nexec /bin/sh -c "$*"\n')
        os.chmod(fake, 0o755)
        inv = os.path.join(self.tmp, "inv.json")
        with open(inv, "w") as f:
            json.dump([{"name": "far", "target": "user@far", "opts": ["-i", "~/.ssh/key", "-o", "HostKeyAlias=x"]}], f)
        base = ["--hosts", inv, "--ssh", fake, "--agent", "own"]
        old = os.environ.get("HOME")
        os.environ["HOME"] = home
        try:
            r = self.cli("install", "--launcher", "setsid", base=base)["hosts"]["far"]
            self.assertIn(r["root"], (root, os.path.realpath(root)))
            c = self.cli("claim", "--job", "j", "--cores", "2", "--ram", "1G", "--gpu", "1", base=base)["claim"]
            self.assertEqual(c["resources"]["gpus"][0]["index"], 1)
            st = self.cli("status", base=base)["hosts"]["far"]
            self.assertEqual(st["claims"][0]["id"], c["id"])
            self.cli("pool", "define", "--file", "far:~/pools/k.json", "--name", "s", "--capacity", "1", base=base)
            self.cli("pool", "acquire", "--file", "far:~/pools/k.json", "--name", "s", "--id", "h", base=base)
            self.assertTrue(os.path.exists(os.path.join(home, "pools", "k.json")))
            self.cli("status", base=["--hosts", inv, "--ssh", "/nonexistent/ssh"], code=hc.ERROR)
            down = os.path.join(self.tmp, "down-ssh")
            with open(down, "w") as f:
                f.write("#!/bin/sh\necho 'ssh: connect to host far port 22: Connection timed out' >&2\nexit 255\n")
            os.chmod(down, 0o755)
            r = self.cli("status", base=["--hosts", inv, "--ssh", down], code=hc.UNREACHABLE)["hosts"]["far"]
            self.assertIn("timed out", r["error"])
        finally:
            os.environ["HOME"] = old


class TestHardening(Base):
    def test_watcher_never_brings_back_a_released_claim(self):
        cid = "a--gone--20260101T000000Z-0000"
        rd = os.path.join(self.root, "run", cid)
        os.makedirs(rd)
        with open(os.path.join(rd, "job.sh"), "w") as f:
            f.write("#!/bin/sh\nexec true\n")
        self.assertEqual(hc.wrap_main(self.root, cid), 1)
        self.assertEqual(self.all_claims(), [])
        self.assertIn("is gone", text(os.path.join(rd, "wrapper.log")))

    def test_yield_needs_admission(self):
        pid = str(self.sleeper().pid)
        target = self.claim("b", "x", "--class", "P3", "--pid", pid)["claim"]["id"]
        self.cli("--agent", "own", "install", "--share-with", "b")
        self.patch_sim(clock_offset_s=25 * 60)
        self.assertIn("may not ask", self.cli("--agent", "stranger", "yield", "--claim", target, "--class", "P0",
                                              code=hc.DENIED)["error"])
        self.cli("--agent", "own", "yield", "--claim", target, "--class", "P0")

    def test_pending_yields_count_before_new_victims(self):
        self.claim("a", "p3a", "--class", "P3", "--gpu", "0:1", "--pid", str(self.sleeper().pid))
        self.claim("a", "p3b", "--class", "P3", "--gpu", "1:1", "--pid", str(self.sleeper().pid))
        self.patch_sim(clock_offset_s=25 * 60)
        first = self.claim("b", "urgent", "--class", "P0", "--gpu", "any", code=hc.NOFIT)
        self.assertEqual(len(first["yield_candidates"]), 1)
        self.cli("--agent", "b", "yield", "--claim", first["yield_candidates"][0], "--class", "P0")
        again = self.claim("b", "urgent", "--class", "P0", "--gpu", "any", code=hc.NOFIT)
        self.assertEqual((again["yield_candidates"], again["yields_pending"]), ([], first["yield_candidates"]))

    def test_stop_always_sends_term(self):
        r = self.cli("--agent", "a", "run", "--job", "quiet", "--yield-signal", "none", "--cores", "1", "--ram", "1G",
                     "--", "sh", "-c", "trap 'exit 0' TERM; while :; do sleep 0.2; done")
        self.assertTrue(wait_for(lambda: self.all_claims()[0].get("pid")))
        self.cli("--agent", "a", "release", "--job", "quiet", "--stop")
        done = os.path.join(r["claim"]["run_dir"], "done.json")
        self.assertTrue(wait_for(lambda: os.path.exists(done), 15))
        self.assertEqual((load(done)["reason"], load(done)["exit_code"]), ("stopped", 0))

    def test_a_failed_done_marker_write_still_releases(self):
        # a folder where done.json should go makes the write fail for any user, root included
        r = self.cli("--agent", "a", "run", "--job", "ro", "--cores", "1", "--ram", "1G", "--",
                     "sh", "-c", "mkdir \"$HOSTCLAIMS_RUN_DIR/done.json\"")
        rd = r["claim"]["run_dir"]
        self.assertTrue(wait_for(lambda: not self.all_claims(), 20))
        self.assertEqual([e["reason"] for e in self.history() if e["event"] == "end"], ["finished"])
        self.assertTrue(os.path.isdir(os.path.join(rd, "done.json")))
        self.assertIn("could not write done.json", text(os.path.join(rd, "wrapper.log")))

    def test_stop_without_a_watcher_waits_for_the_process(self):
        p = subprocess.Popen(["sh", "-c", "trap '' TERM; sleep 60"])
        self.procs.append(p)
        time.sleep(0.3)
        self.claim("a", "stubborn", "--pid", str(p.pid))
        r = self.cli("--agent", "a", "release", "--job", "stubborn", "--stop")
        self.assertTrue(r["stopping"])
        self.assertEqual(len(self.all_claims()), 1)

    def test_claims_folder_is_bound_to_its_machine(self):
        self.patch_sim(machine_id="m-1")
        self.cli("--agent", "own", "install")
        self.patch_sim(machine_id="m-2")
        self.assertIn("another machine", self.cli("status", code=hc.DENIED)["hosts"]["local"]["error"])
        self.claim("a", "x", code=hc.DENIED)
        self.cli("--agent", "guest", "install", code=hc.DENIED)
        self.cli("--agent", "own", "install")
        self.assertEqual(self.cli("status")["hosts"]["local"]["tag"], "free")
        self.assertIn("rebind", [e["event"] for e in self.history()])

    def test_default_root_on_a_network_filesystem_is_refused(self):
        home = os.path.join(self.tmp, "nfs-home")
        root = os.path.join(home, ".solaris", "claims")
        os.makedirs(root)
        self.write_sim(sim(fs_type="nfs4"), root)
        old = os.environ.get("HOME")
        os.environ["HOME"] = home
        try:
            r = hc.host_op({"op": "install", "agent": "own"}, SRC)
        finally:
            os.environ["HOME"] = old
        self.assertEqual((r["ok"], r["code"]), (False, hc.DENIED))
        self.assertIn("network filesystem", r["error"])
        self.assertFalse(os.path.exists(os.path.join(root, "host.json")))

    def test_pool_rules(self):
        f = os.path.join(self.tmp, "p.json")
        self.cli("--agent", "own", "pool", "define", "--file", f, "--name", "s", "--capacity", "2", "--cap", "*=1",
                 "--borrow-max-hours", "2")
        self.cli("--agent", "guest", "pool", "define", "--file", f, "--name", "s", "--capacity", "9", code=hc.DENIED)
        self.cli("pool", "define", "--file", f, "--name", "t", "--capacity", "1", code=hc.USAGE)
        self.cli("--agent", "a", "pool", "acquire", "--file", f, "--name", "s", "--id", "h0")
        self.cli("--agent", "a", "pool", "acquire", "--file", f, "--name", "s", "--id", "h1", "--borrow", "--hours", "1")
        self.cli("--agent", "a", "pool", "acquire", "--file", f, "--name", "s", "--id", "h1", "--hours", "100",
                 code=hc.DENIED)
        self.cli("--agent", "a", "pool", "acquire", "--file", f, "--name", "s", "--id", "h1", "--hours", "1.5")

    def test_requests_and_audit_edges(self):
        self.cli("--agent", "own", "lease", "--set", "kind=paid", "--set", "planned_end=+1h", "--set", "usd_per_hour=3")
        self.cli("--agent", "bad name", "request", "--type", "maintenance", "--reason", "x", code=hc.USAGE)
        m = self.cli("--agent", "guest", "request", "--type", "maintenance", "--reason", "reboot please")["request"]
        rec = self.cli("--agent", "guest", "fit", "--job", "j", "--hours", "3", "--cores", "1", "--ram", "1G",
                       "--new-usd-per-hour", "9")["recommendation"]
        self.assertEqual(rec["action"], "reuse-with-extension")
        e = self.cli("--agent", "guest", "extend", "--job", "j", "--hours", "3", "--cores", "1", "--ram", "1G")["request"]
        self.cli("--agent", "own", "lease", "--set", "kind=none")
        self.assertIn("no planned end", self.cli("--agent", "own", "approve", "--request", e["id"], code=hc.DENIED)["error"])
        self.cli("--agent", "own", "decline", "--request", m["id"])
        self.cli("--agent", "own", "lease", "--set", "kind=paid", "--set", "planned_end=+1h", "--set", "usd_per_hour=3")
        self.cli("--agent", "guest", "request", "--type", "objection", "--reason", "resume later", "--until", "+6h")
        self.patch_sim(clock_offset_s=40 * 60)
        # 40 min unanswered is longer than a new instance takes to be ready: the audit advises declining
        self.assertEqual(self.cli("--agent", "own", "audit")["hosts"][0]["recommendation"]["action"], "decline")
        self.cli("--agent", "own", "decline", "--request", e["id"])
        out = self.cli("--agent", "own", "audit")["hosts"][0]["recommendation"]
        self.assertEqual(out["action"], "check")
        self.assertIn("objects", out["why"])

    @unittest.skipIf(os.path.exists("/proc/sys/kernel/random/boot_id"), "Linux reads the kernel's boot id")
    def test_boot_id_does_not_depend_on_the_time_zone(self):
        self.assertRegex(hc.boot_id(), r"^(boottime \d+|unknown)$")


class TestSafeguards(Base):
    def test_audit_checks_instead_of_deleting_under_unclaimed_work(self):
        self.cli("--agent", "own", "lease", "--set", "kind=paid", "--set", "planned_end=+5h", "--set", "usd_per_hour=2")
        self.patch_sim(gpu_procs=[{"pid": 777777, "gpu": 0, "mem_gb": 10.0}],
                       gpus=[dict(T4, index=0, mem_used_gb=10.0), dict(T4, index=1)],
                       cpu_busy={"0": 1, "1": 1, "2": 1, "3": 1}, clock_offset_s=2 * 3600)
        rec = self.cli("--agent", "own", "audit")["hosts"][0]["recommendation"]
        self.assertEqual(rec["action"], "check")
        self.assertIn("outside claims", rec["why"])
        self.patch_sim(gpu_procs=[], gpus=[dict(T4, index=0), dict(T4, index=1)], cpu_busy={"0": 1, "1": 1})
        self.assertEqual(self.cli("--agent", "own", "audit")["hosts"][0]["recommendation"]["action"], "check")
        self.patch_sim(cpu_busy={})
        self.assertEqual(self.cli("--agent", "own", "audit")["hosts"][0]["recommendation"]["action"], "delete")

    def test_no_list_means_owner_only(self):
        root = os.path.join(self.tmp, "private")
        os.makedirs(root)
        self.write_sim(sim(), root)
        base = ["--local-root", root]
        self.cli("--agent", "own", "install", "--launcher", "setsid", base=base)
        self.assertEqual(load(os.path.join(root, "host.json"))["share_with"], [])
        self.assertIn("not shared", self.cli("--agent", "stranger", "claim", "--job", "j", "--cores", "1", "--ram", "1G",
                                             base=base, code=hc.NOFIT)["reasons"][0])
        self.cli("--agent", "own", "claim", "--job", "j", "--cores", "1", "--ram", "1G", base=base)
        raw = load(os.path.join(root, "host.json"))
        del raw["share_with"]
        hc.write_json(os.path.join(root, "host.json"), raw)
        self.cli("--agent", "stranger", "claim", "--job", "k", "--cores", "1", "--ram", "1G", base=base, code=hc.NOFIT)

    def test_guests_never_preempt_the_dedicated_project(self):
        self.cli("--agent", "own", "install", "--mode", "dedicated:own")
        mine = self.claim("own", "explore", "--class", "P2", "--gpu", "any", "--pid", str(self.sleeper().pid))["claim"]
        self.patch_sim(clock_offset_s=25 * 60)
        r = self.cli("--agent", "guest", "yield", "--claim", mine["id"], "--class", "P1", "--borrowed", code=hc.DENIED)
        self.assertIn("dedicated to own", r["error"])
        self.cli("--agent", "guest", "yield", "--claim", mine["id"], "--class", "P0", code=hc.DENIED)

    def test_a_process_belongs_to_one_claim(self):
        victim = self.sleeper()
        self.claim("b", "real", "--pid", str(victim.pid))
        self.assertIn("belongs to claim", self.claim("a", "adopt", "--pid", str(victim.pid), code=hc.DENIED)["error"])
        self.assertIsNone(victim.poll())

    def test_run_refits_an_earlier_bare_claim(self):
        self.claim("a", "j", cores=1, ram="1G")
        r = self.cli("--agent", "a", "run", "--job", "j", "--cores", "6", "--ram", "20G", "--", "sleep", "1")
        self.assertEqual((len(r["claim"]["resources"]["cores"]), r["claim"]["resources"]["ram_gb"]), (6, 20.0))
        self.assertIn("resized", [e.get("reason") for e in self.history() if e["event"] == "end"])
        self.assertTrue(wait_for(lambda: not self.all_claims(), 20))
        small = self.claim("a", "k", cores=1, ram="1G")["claim"]
        r = self.cli("--agent", "a", "run", "--job", "k", "--cores", "64", "--ram", "1G", "--", "true", code=hc.NOFIT)
        self.assertIn("keeps its earlier size", r["error"])
        self.assertEqual(self.all_claims()[0]["resources"]["cores"], small["resources"]["cores"])

    def test_claims_are_private_and_other_projects_see_only_the_program(self):
        r = self.cli("--agent", "a", "run", "--job", "s", "--cores", "1", "--ram", "1G", "--", "sh", "-c",
                     "TOKEN=hunter2 sleep 5")
        cid = r["claim"]["id"]
        self.assertEqual(os.stat(os.path.join(self.root, "claims", cid + ".json")).st_mode & 0o777, 0o600)
        self.assertEqual(os.stat(r["claim"]["run_dir"]).st_mode & 0o777, 0o700)
        other = self.cli("--agent", "other", "status")["hosts"]["local"]["claims"][0]
        self.assertEqual(other["cmd"], ["sh"])
        mine = self.cli("--agent", "a", "status")["hosts"]["local"]["claims"][0]
        self.assertIn("TOKEN=hunter2 sleep 5", mine["cmd"])

    def test_unwatched_claims_show_when_they_lapse(self):
        c = self.claim("a", "bare")["claim"]
        lapse = hc.parse_iso(c["lapses_at"]) - hc.parse_iso(c["heartbeat"])
        self.assertAlmostEqual(lapse, 15 * 60, delta=1)
        self.assertNotIn("lapses_at", self.claim("a", "tied", "--pid", str(self.sleeper().pid))["claim"])
        buf = io.StringIO()
        with redirect_stdout(buf):
            hc.main(["--local-root", self.root, "--agent", "a", "claim", "--job", "bare", "--cores", "1", "--ram", "1G"])
        self.assertIn("lapses at", buf.getvalue())

    def test_owner_prunes_old_run_folders(self):
        old = os.path.join(self.root, "run", "a--old--20200101T000000Z-0000")
        os.makedirs(old)
        hc.write_json(os.path.join(old, "done.json"), {"ended": hc.iso(time.time() - 20 * 86400)})
        fresh = os.path.join(self.root, "run", "a--new--20200101T000000Z-0001")
        os.makedirs(fresh)
        hc.write_json(os.path.join(fresh, "done.json"), {"ended": hc.iso(time.time() - 86400)})
        self.assertEqual(self.cli("--agent", "own", "audit")["hosts"][0]["result"]["old_runs"], 1)
        self.assertEqual(self.cli("--agent", "guest", "reap")["hosts"]["local"]["pruned_runs"], [])
        self.assertTrue(os.path.exists(old))
        self.assertEqual(self.cli("--agent", "own", "reap")["hosts"]["local"]["pruned_runs"], [os.path.basename(old)])
        self.assertFalse(os.path.exists(old))
        self.assertTrue(os.path.exists(fresh))

    def test_a_timeout_says_to_check_status_first(self):
        slow = os.path.join(self.tmp, "slow-ssh")
        with open(slow, "w") as f:
            f.write("#!/bin/sh\nsleep 5\n")
        os.chmod(slow, 0o755)
        inv = os.path.join(self.tmp, "inv.json")
        with open(inv, "w") as f:
            json.dump([{"name": "far", "target": "user@far"}], f)
        r = self.cli("--agent", "a", "--timeout", "1", "run", "--job", "j", "--cores", "1", "--ram", "1G", "--", "true",
                     base=["--hosts", inv, "--ssh", slow], code=hc.UNREACHABLE)
        self.assertIn("status --host far", r["hint"])

    def test_container_processes_count_through_the_claimed_pid(self):
        claims = [{"id": "c1", "pid": 100, "pid_start": "s100", "resources": {"gpus": [{"index": 0, "share": 1.0}]}}]
        procs = {100: (1, 100, "s100", 0.1), 200: (100, 100, "s200", 2.0), 300: (200, 300, "s300", 1.0),
                 400: (1, 400, "s400", 1.0)}
        rows, rss, big = hc.attribute({"procs": procs, "gpu_procs": [{"pid": 300, "gpu": 0, "mem_gb": 4.0},
                                                                     {"pid": 400, "gpu": 0, "mem_gb": 1.0}]}, claims)
        self.assertEqual([r["claim"] for r in rows], ["c1", None])
        self.assertAlmostEqual(rss["c1"], 3.1)
        # the 2 GiB process belongs to the claim; only the unclaimed 1 GiB one is outside work
        self.assertEqual(big, [{"pid": 400, "rss_gb": 1.0}])

    def test_simulated_readings_need_the_test_switch(self):
        os.environ.pop("HOSTCLAIMS_SIMULATE")
        try:
            self.assertIsNone(hc.load_sim(hc.Root(self.root)))
            self.assertNotEqual(hc.take_probe(hc.Root(self.root), hc.host_cfg(hc.Root(self.root)), None, False)["boot_id"],
                                "boot-1")
        finally:
            os.environ["HOSTCLAIMS_SIMULATE"] = "1"
        self.assertEqual(hc.load_sim(hc.Root(self.root))["boot_id"], "boot-1")

    def test_ssh_targets_never_start_with_a_dash(self):
        self.assertIn("bad ssh target", self.cli("pool", "show", "--file=-oProxyCommand=x:/tmp/p.json",
                                                 code=hc.USAGE)["error"])
        inv = os.path.join(self.tmp, "inv.json")
        with open(inv, "w") as f:
            json.dump([{"name": "evil", "target": "-oProxyCommand=touch /tmp/x"}], f)
        self.cli("status", base=["--hosts", inv], code=hc.USAGE)

    def test_unified_memory_gpus(self):
        gpus = hc.parse_gpu_csv("0, GPU-aa, NVIDIA GB10, [N/A], [N/A], 3\n")
        self.assertEqual((gpus[0]["mem_total_gb"], gpus[0]["mem_used_gb"], gpus[0]["name"]), (None, 0.0, "NVIDIA GB10"))
        procs = hc.parse_apps_csv("123, GPU-aa, [N/A]\n", gpus)
        self.assertEqual(procs, [{"pid": 123, "gpu": 0, "mem_gb": 0.0}])
        self.patch_sim(gpus=[{"index": 0, "name": "NVIDIA GB10", "mem_total_gb": None}])
        g = self.claim("a", "u", "--gpu", "any")["claim"]["resources"]["gpus"][0]
        self.assertEqual((g["index"], g["mem_gb"]), (0, None))

    def test_docs_carry_the_guards(self):
        root = os.path.dirname(HERE)
        rule = text(os.path.join(root, "shared", "resource-sharing.rule.md"))
        skill = text(os.path.join(root, "shared", "resource-sharing.skill.md"))
        self.assertIn("audit --host", rule)
        self.assertIn("uninstall", rule)
        self.assertIn("--pid", rule)
        self.assertIn("kaggle-sharing", skill)
        self.assertIn("rental term", skill)
        self.assertIn("run `status` on that host", " ".join(skill.split()))
        self.assertNotIn("--agent %s", SRC)
        self.assertIn("request", hc.__doc__)
        self.assertIn("audit", hc.__doc__)


class TestOwnerIdentity(TestDiscovery):
    def test_printed_commands_act_as_the_caller_and_names_cannot_be_borrowed(self):
        own, guest = ["--project", self.a], ["--project", self.b]
        self.cli("install", "--host", "gpu-1", "--launcher", "setsid", "--lease", "kind=paid", "--lease",
                 "planned_end=+1h", "--lease", "usd_per_hour=2", base=own)
        r = self.cli("extend", "--host", "proj-a/gpu-1", "--job", "j", "--hours", "3", "--cores", "1", "--ram", "1G",
                     base=guest)
        self.assertNotIn("--agent", r["text"])
        rid = r["request"]["id"]
        self.cli("approve", "--host", "proj-a/gpu-1", "--request", rid, base=guest, code=hc.DENIED)
        self.assertIn("run as this project", self.cli("--agent", "proj-a", "approve", "--host", "proj-a/gpu-1",
                                                       "--request", rid, base=guest, code=hc.DENIED)["error"])
        self.cli("--agent", "proj-a", "lease", "--host", "proj-a/gpu-1", "--set", "planned_end=+9h", base=guest,
                 code=hc.DENIED)
        self.assertEqual(self.cli("approve", "--host", "gpu-1", "--request", rid, base=own)["request"]["state"],
                         "approved")

    test_one_way_and_mutual_sharing = None
    test_owner_installs_and_guests_claim_and_ask = None


class TestSharedHosts(TestDiscovery):
    # owners add and retire machines; guests pick the changes up with shared, owners share at once with install --all
    def shared(self, *args, **kw):
        return self.cli("shared", *args, base=["--project", kw.pop("project", self.b)], **kw)

    def plain(self, *args, **kw):
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = hc.main(["--project", kw.get("project", self.b)] + list(args))
        return rc, buf.getvalue()

    def a_inventory(self, edit):
        path = os.path.join(self.a, "ai", ".memory", "hosts.json")
        hosts = load(path)
        hosts = edit(hosts) or hosts
        with open(path, "w") as f:
            json.dump(hosts, f)

    def a_shares(self, *slugs):
        with open(os.path.join(self.a, "ai", ".memory", "resource-sharing.json"), "w") as f:
            json.dump({"project": "proj-a", "share_with": list(slugs)}, f)

    def new_root(self, name):
        root = os.path.join(self.tmp, "hosts", "proj-a-" + name)
        os.makedirs(root)
        self.write_sim(sim(gpus=[{"index": i, "name": "NVIDIA H200 NVL", "mem_total_gb": 140.0} for i in range(2)]), root)
        return root

    def test_new_changed_gone_until_acknowledged(self):
        seen = os.path.join(self.b, "aipack", ".memory", "resource-sharing-seen.json")
        r = self.shared(code=hc.CHANGES)
        # only what proj-a owns is shared; its gpu-1 clashes with proj-b's own gpu-1, so it takes the project prefix
        self.assertEqual([(h["key"], h["name"], h["owner"], h["target"], h["lease_kind"]) for h in r["new"]],
                         [("proj-a/gpu-1", "proj-a/gpu-1", "proj-a", "local", None)])
        self.shared(code=hc.CHANGES)
        self.assertFalse(os.path.exists(seen))
        self.assertTrue(self.shared("--ack")["acknowledged"])
        self.assertEqual(list(load(seen)["hosts"]), ["proj-a/gpu-1"])
        r = self.shared()
        self.assertEqual((r["new"], r["gone"], r["changed"], [h["key"] for h in r["unchanged"]]),
                         ([], [], [], ["proj-a/gpu-1"]))
        # the owner adds a 2x H200 box and moves gpu-1 to a paid lease
        root = self.new_root("h200")

        def grow(hosts):
            hosts[0]["lease"] = {"kind": "paid", "planned_end": "2026-10-01T06:00Z", "usd_per_hour": 7.5}
            hosts.append({"name": "h200", "target": "local", "root": root, "gpus": "2x H200 NVL",
                          "lease": {"kind": "free", "planned_end": "2026-10-02T00:00-07:00"}})
        self.a_inventory(grow)
        r = self.shared(code=hc.CHANGES)
        self.assertEqual([(h["key"], h["name"], h["gpus"], h["lease_kind"], h["planned_end"]) for h in r["new"]],
                         [("proj-a/h200", "h200", "2x H200 NVL", "free", "2026-10-02T07:00:00Z")])
        self.assertEqual(r["changed"][0]["changes"], {"lease_kind": [None, "paid"],
                                                      "planned_end": [None, "2026-10-01T06:00:00Z"]})
        rc, out = self.plain("shared")
        self.assertEqual(rc, hc.CHANGES)
        for s in ("NEW     h200 [owner proj-a]", "GPUs 2x H200 NVL", "CHANGED proj-a/gpu-1", "lease ? -> paid",
                  "shared --ack"):
            self.assertIn(s, out)
        self.shared("--ack")
        # it retires the box, then stops sharing with proj-b altogether
        self.a_inventory(lambda hosts: hosts[:1])
        self.assertEqual([h["key"] for h in self.shared(code=hc.CHANGES)["gone"]], ["proj-a/h200"])
        self.assertIn("GONE    h200", self.plain("shared")[1])
        self.shared("--ack")
        self.a_shares("proj-c")
        r = self.shared(code=hc.CHANGES)
        self.assertEqual(([h["key"] for h in r["gone"]], r["new"]), (["proj-a/gpu-1"], []))

    def test_unreadable_files_never_count_as_gone(self):
        self.shared("--ack")
        inv = os.path.join(self.a, "ai", ".memory", "hosts.json")
        good = text(inv)
        with open(inv, "w") as f:
            f.write('[{"name": "gpu-1", ')
        r = self.shared()
        self.assertEqual((r["gone"], [h["key"] for h in r["unread"]]), ([], ["proj-a/gpu-1"]))
        self.assertTrue(any("cannot read" in n and "proj-a are skipped" in n for n in r["notes"]))
        self.shared("--ack")
        with open(inv, "w") as f:
            f.write(good)
        self.assertEqual([h["key"] for h in self.shared()["unchanged"]], ["proj-a/gpu-1"])
        with open(os.path.join(self.a, "ai", ".memory", "resource-sharing.json"), "w") as f:
            f.write("{")
        self.assertEqual([h["key"] for h in self.shared()["unread"]], ["proj-a/gpu-1"])
        with open(os.path.join(self.b, "aipack", ".memory", "resource-sharing-seen.json"), "w") as f:
            f.write("not json")
        self.a_shares("proj-b")
        r = self.shared(code=hc.CHANGES)
        self.assertEqual([h["key"] for h in r["new"]], ["proj-a/gpu-1"])
        self.assertTrue(any("seen list" in n for n in r["notes"]))

    def test_probe_install_all_and_the_audit_sync_flags(self):
        own = ["--project", self.a]
        live = self.shared("--probe", code=hc.CHANGES)["live"]["proj-a/gpu-1"]
        self.assertEqual((live["ok"], live["installed"]), (True, False))
        au = self.cli("audit", base=own)["hosts"]
        self.assertEqual([(r["host"], r["fix"]["why"]) for r in au], [("gpu-1", "in the inventory but not installed")])
        self.assertIn("install --host gpu-1", au[0]["fix"]["cmd"])
        self.cli("install", "--all", "--host", "gpu-1", base=own, code=hc.USAGE)
        self.cli("install", "--all", "--owner", "proj-c", base=own, code=hc.USAGE)
        self.cli("install", "--all", "--lease", "kind=free", base=own, code=hc.USAGE)
        r = self.cli("install", "--all", "--launcher", "setsid", base=own)
        # only what proj-a owns: not proj-x's borrowed host, nor proj-c's cpu-1 that proj-c shares with proj-a
        self.assertEqual((sorted(r["hosts"]), r["hosts"]["gpu-1"]["new"]), (["gpu-1"], True))
        for other in ("proj-a-borrowed", "proj-c-cpu-1"):
            self.assertFalse(os.path.exists(os.path.join(self.tmp, "hosts", other, "host.json")))
        self.assertFalse(self.cli("install", "--all", base=own)["hosts"]["gpu-1"]["new"])
        self.assertIsNone(self.cli("audit", base=own)["hosts"][0]["fix"])
        live = self.shared("--probe", code=hc.CHANGES)["live"]["proj-a/gpu-1"]
        self.assertEqual((live["installed"], live["admitted"], live["tag"]), (True, True, "free"))
        self.assertEqual([g["name"] for g in live["free"]["gpus"]], ["Tesla T4", "Tesla T4"])
        # proj-b is dropped and synced, then listed again without a sync: it sees the host, which does not admit it yet
        self.a_shares("proj-c")
        self.cli("install", "--all", base=own)
        self.a_shares("proj-c", "proj-b")
        live = self.shared("--probe", code=hc.CHANGES)["live"]["proj-a/gpu-1"]
        self.assertFalse(live["admitted"])
        self.assertIn("not shared with proj-b", live["why"][0])
        fix = self.cli("audit", base=own)["hosts"][0]["fix"]
        self.assertIn("differs from resource-sharing.json", fix["why"])
        self.assertIn("install --host gpu-1", fix["cmd"])
        # a new machine in the owner's inventory: flagged with its command, then installed and synced in one step
        root = self.new_root("h200")
        self.a_inventory(lambda hosts: hosts + [{"name": "h200", "target": "local", "root": root}])
        rc, out = self.plain("audit", project=self.a)
        self.assertIn("install --host h200", out)
        self.assertIn("install --all", out)
        rc, out = self.plain("install", "--all", "--launcher", "setsid", project=self.a)
        self.assertEqual(rc, 0, out)
        self.assertIn("shared with proj-c, proj-b: tell them", out)
        self.assertEqual([r["fix"] for r in self.cli("audit", base=own)["hosts"]], [None, None])
        live = self.shared("--probe", code=hc.CHANGES)["live"]
        self.assertEqual([live[k]["admitted"] for k in sorted(live)], [True, True])
        self.assertEqual(live["proj-a/h200"]["free"]["gpus"][0]["name"], "NVIDIA H200 NVL")
        self.assertIn("admits you", self.plain("shared", "--probe")[1])

    def test_inventory_files_and_refusals(self):
        self.cli("shared", code=hc.USAGE)
        roots = dict((n, os.path.join(self.tmp, "inv-" + n)) for n in ("p1", "p2", "q1"))
        for root in roots.values():
            os.makedirs(root)
            self.write_sim(sim(), root)
        inv = os.path.join(self.tmp, "inv.json")
        with open(inv, "w") as f:
            json.dump([{"name": "p1", "target": "local", "root": roots["p1"], "owner": "p"},
                       {"name": "p2", "target": "local", "root": roots["p2"]},
                       {"name": "q1", "target": "local", "root": roots["q1"], "owner": "q"},
                       {"name": "far", "target": "user@far", "owner": "q", "lease": {"kind": "none"}}], f)
        base = ["--hosts", inv]
        # install --all: the caller's hosts (an entry without an owner counts as the caller's, as in audit)
        r = self.cli("--agent", "p", "install", "--all", "--launcher", "setsid", base=base)
        self.assertEqual(sorted(r["hosts"]), ["p1", "p2"])
        self.assertFalse(os.path.exists(os.path.join(roots["q1"], "host.json")))
        self.cli("--agent", "p", "install", "--all", base=base, as_owner=False, code=hc.DENIED)
        # shared with an inventory file: the hosts another project owns, against an explicit seen list
        self.cli("--agent", "p", "shared", base=base, code=hc.USAGE)
        seen = os.path.join(self.tmp, "seen.json")
        r = self.cli("--agent", "p", "shared", "--seen", seen, base=base, code=hc.CHANGES)
        self.assertEqual([(h["key"], h["lease_kind"]) for h in r["new"]], [("q/far", "none"), ("q/q1", None)])
        self.cli("--agent", "p", "shared", "--seen", seen, "--ack", base=base)
        self.assertEqual(self.cli("--agent", "p", "shared", "--seen", seen, base=base)["new"], [])

    def test_seen_lists_written_before_folders_still_load(self):
        # a seen list from before records kept the owner's folder: no false NEW, GONE or CHANGED after the upgrade
        seen = os.path.join(self.b, "aipack", ".memory", "resource-sharing-seen.json")
        self.shared("--ack")
        doc = load(seen)
        self.assertEqual(doc["hosts"]["proj-a/gpu-1"]["folder"], os.path.join("projects", "my", "proj-a"))
        for r in doc["hosts"].values():
            del r["folder"]
        with open(seen, "w") as f:
            json.dump(doc, f)
        r = self.shared()
        self.assertEqual((r["new"], r["gone"], r["changed"], [h["key"] for h in r["unchanged"]]),
                         ([], [], [], ["proj-a/gpu-1"]))
        self.assertIn("no changes since the last ack", self.plain("shared")[1])
        # such a record still stays as seen while its owner cannot be read, matched by slug
        with open(os.path.join(self.a, "ai", ".memory", "hosts.json"), "w") as f:
            f.write("[")
        r = self.shared()
        self.assertEqual((r["gone"], [h["key"] for h in r["unread"]]), ([], ["proj-a/gpu-1"]))

    def test_unreadable_sharing_file_of_an_owner_whose_folder_is_not_its_slug(self):
        # embedded layout: the slug comes only from the sharing file, so a broken one must not make its hosts GONE
        alpha = self.project("my/alpha/alpha-repo", "ai", "alpha", ["proj-b"], [])
        # a remote target: shared reads no host without --probe
        with open(os.path.join(alpha, "ai", ".memory", "hosts.json"), "w") as f:
            json.dump([{"name": "gpu-7", "target": "user@alpha-7"}], f)
        self.assertEqual([h["key"] for h in self.shared("--ack")["new"]], ["alpha/gpu-7", "proj-a/gpu-1"])
        with open(os.path.join(alpha, "ai", ".memory", "resource-sharing.json"), "w") as f:
            f.write('{"project": "alpha", "share_')
        r = self.shared()
        self.assertEqual((r["gone"], [h["key"] for h in r["unread"]]), ([], ["alpha/gpu-7"]))
        folder = os.path.join("projects", "my", "alpha", "alpha-repo")
        self.assertTrue(any(n.endswith("the hosts of the project in %s are skipped" % folder) for n in r["notes"]),
                        r["notes"])
        self.assertFalse(any("the hosts of alpha-repo" in n for n in r["notes"]))
        rc, out = self.plain("shared")
        self.assertEqual(rc, 0, out)
        self.assertIn("UNREAD  gpu-7 [owner alpha] user@alpha-7; lease ?; GPUs ?: alpha's files cannot be read now", out)

    def test_broken_own_sharing_file_is_refused(self):
        # read as missing it would mean no sharing: install, audit and shared stop and name the file instead
        cfg = os.path.join(self.a, "ai", ".memory", "resource-sharing.json")
        with open(cfg, "w") as f:
            f.write('{"project": "proj-a", "share_with": ["proj-b", "proj-c",]}')
        own = ["--project", self.a]
        r = self.cli("install", "--all", "--launcher", "setsid", base=own, code=hc.USAGE)
        self.assertIn("cannot read %s" % cfg, r["error"])
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "hosts", "proj-a-gpu-1", "host.json")))
        self.assertIn("cannot read %s" % cfg, self.cli("audit", base=own, code=hc.USAGE)["error"])
        self.assertIn("cannot read %s" % cfg, self.shared(project=self.a, code=hc.USAGE)["error"])

    def test_printed_follow_up_commands_run_as_printed(self):
        # outside a project: the acknowledge line keeps --seen and --agent, the audit's fixes --agent and --as-owner
        root = os.path.join(self.tmp, "inv-p1")
        os.makedirs(root)
        self.write_sim(sim(), root)
        inv, seen = os.path.join(self.tmp, "inv.json"), os.path.join(self.tmp, "seen.json")
        with open(inv, "w") as f:
            json.dump([{"name": "p1", "target": "local", "root": root, "owner": "p"},
                       {"name": "far", "target": "user@far", "owner": "q"}], f)

        def run_printed(marker, *args):
            buf = io.StringIO()
            with redirect_stdout(buf):
                hc.main(["--hosts", inv] + list(args))
            cmd = [ln for ln in buf.getvalue().splitlines() if marker in ln][0].split(marker, 1)[1]
            self.assertTrue(cmd.startswith("python3 "), cmd)
            p = subprocess.run([sys.executable] + shlex.split(cmd)[1:], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            self.assertEqual(p.returncode, 0, (cmd, p.stdout, p.stderr))
            return cmd

        self.assertIn("--seen", run_printed("Then acknowledge: ", "--agent", "p", "shared", "--seen", seen))
        self.assertEqual(self.cli("--agent", "p", "shared", "--seen", seen, base=["--hosts", inv])["new"], [])
        self.assertIn("--as-owner", run_printed("(then tell the projects you share with): ", "--agent", "p",
                                                "--as-owner", "audit"))
        self.assertTrue(os.path.exists(os.path.join(root, "host.json")))

    @unittest.skipIf(hasattr(os, "geteuid") and os.geteuid() == 0, "root reads files whatever their mode")
    def test_peer_files_without_read_permission(self):
        # a peer file that exists but cannot be opened counts as unreadable, not as a crash
        self.shared("--ack")
        mem = os.path.join(self.a, "ai", ".memory")
        for name, who in (("resource-sharing.json", "the project in " + os.path.join("projects", "my", "proj-a")),
                          ("hosts.json", "proj-a")):
            path = os.path.join(mem, name)
            os.chmod(path, 0)
            try:
                r = self.shared()
                self.assertEqual((r["gone"], [h["key"] for h in r["unread"]]), ([], ["proj-a/gpu-1"]))
                self.assertTrue(any(n.startswith("cannot read %s" % path) and
                                    n.endswith("the hosts of %s are skipped" % who) for n in r["notes"]), r["notes"])
                self.assertIn("gpu-1", self.cli("status", base=["--project", self.b])["hosts"])
            finally:
                os.chmod(path, 0o644)

    test_one_way_and_mutual_sharing = None
    test_owner_installs_and_guests_claim_and_ask = None


class RealBase(Base):
    # the machine's own readings: no simulate.json in effect (only readers named in a test are patched)
    def setUp(self):
        Base.setUp(self)
        self.cli("--agent", "own", "install", "--disk-free-pct", "0", "--reserve-ram", "0", "--reserve-cores", "0")
        os.environ.pop("HOSTCLAIMS_SIMULATE")
        os.unlink(os.path.join(self.root, "simulate.json"))
        self.patched = []

    def tearDown(self):
        # undo in reverse, so a reader patched twice ends at its original
        for name, value in reversed(self.patched):
            setattr(hc, name, value)
        Base.tearDown(self)

    def patch(self, name, value):
        self.patched.append((name, getattr(hc, name)))
        setattr(hc, name, value)


class TestRealReadings(RealBase):
    def test_audit_samples_cpu_on_a_real_host(self):
        self.cli("--agent", "own", "lease", "--set", "kind=paid", "--set", "planned_end=+5h", "--set", "usd_per_hour=2")
        self.patch("cpu_busy", lambda s: dict((c, 1.0) for c in hc.online_cpus()[:4]))
        t = time.time() + 2 * 3600
        au = hc.host_op({"op": "audit", "root": self.root, "agent": "own", "now": t})
        self.assertEqual(au["outside"]["cpu_cores"], 4.0)
        rec = hc.audit_recommend(au, hc.AUDIT_DEFAULTS)
        self.assertEqual(rec["action"], "check")
        self.patch("cpu_busy", lambda s: {})
        au = hc.host_op({"op": "audit", "root": self.root, "agent": "own", "now": t})
        self.assertEqual(hc.audit_recommend(au, hc.AUDIT_DEFAULTS)["action"], "delete")

    def test_repeat_claim_ties_the_process(self):
        first = self.claim("a", "bare")["claim"]
        self.assertIn("lapses_at", first)
        p = self.sleeper()
        tied = self.claim("a", "bare", "--pid", str(p.pid))["claim"]
        self.assertEqual((tied["id"], tied["pid"]), (first["id"], p.pid))
        self.assertNotIn("lapses_at", tied)
        self.set_claim(first["id"], heartbeat=hc.iso(time.time() - 30 * 60))
        self.assertEqual(self.cli("reap")["hosts"]["local"]["reaped"], [])
        self.claim("a", "bare", "--pid", str(self.sleeper().pid), code=hc.DENIED)
        other = self.sleeper()
        self.claim("b", "real", "--pid", str(other.pid))
        self.claim("a", "bare2")
        self.claim("a", "bare2", "--pid", str(other.pid), code=hc.DENIED)

    def test_fit_stops_waiting_once_a_new_instance_would_be_ready(self):
        self.cli("--agent", "own", "lease", "--set", "kind=paid", "--set", "planned_end=+1h", "--set", "usd_per_hour=2")
        rid = self.cli("--agent", "guest", "extend", "--job", "j", "--hours", "3", "--cores", "1", "--ram", "1G")["request"]["id"]
        fit = lambda *x: self.cli("--agent", "guest", "fit", "--job", "j", "--hours", "3", "--cores", "1", "--ram", "1G",
                                  "--new-usd-per-hour", "1", *x)["recommendation"]
        self.assertEqual(fit()["action"], "wait-for-owner")
        path = os.path.join(self.root, "requests", rid + ".json")
        r = load(path)
        r["created"] = hc.iso(time.time() - 40 * 60)
        hc.write_json(path, r)
        rec = fit()
        self.assertEqual(rec["action"], "launch-new")
        self.assertIn("has not answered", rec["why"])
        self.assertEqual(fit("--can-wait")["action"], "wait-for-owner")

    def test_owner_actions_outside_a_project_need_as_owner(self):
        inv = os.path.join(self.tmp, "inv.json")
        with open(inv, "w") as f:
            json.dump([{"name": "h", "target": "local", "root": self.root}], f)
        self.cli("--agent", "own", "lease", "--set", "kind=paid", "--set", "planned_end=+1h", "--set", "usd_per_hour=2")
        rid = self.cli("--agent", "guest", "extend", "--job", "j", "--hours", "3", "--cores", "1", "--ram", "1G")["request"]["id"]
        r = self.cli("--agent", "own", "approve", "--host", "h", "--request", rid, base=["--hosts", inv], as_owner=False,
                     code=hc.DENIED)
        self.assertIn("--as-owner", r["error"])
        self.cli("--agent", "own", "lease", "--set", "planned_end=+2h", as_owner=False, code=hc.DENIED)
        self.assertEqual(self.cli("--agent", "own", "approve", "--host", "h", "--request", rid,
                                  base=["--hosts", inv])["request"]["state"], "approved")

    def test_failed_launch_after_a_refit_restores_the_bare_claim(self):
        bare = self.claim("a", "j", cores=1, ram="1G")["claim"]
        with open(os.path.join(self.root, "run", bare["id"]), "w") as f:
            f.write("a file where the run folder goes\n")
        r = self.cli("--agent", "a", "run", "--job", "j", "--cores", "4", "--ram", "8G", "--", "true", code=hc.ERROR)
        self.assertIn("FileExistsError", r["error"])
        c = self.all_claims()[0]
        self.assertEqual((c["id"], c["resources"]["cores"], c["resources"]["ram_gb"], c.get("launcher")),
                         (bare["id"], bare["resources"]["cores"], 1.0, None))
        self.assertEqual([(e["event"], e.get("reason")) for e in self.history() if e["event"] in ("end", "claim")][-4:],
                         [("end", "resized"), ("claim", None), ("end", "launch-failed"), ("claim", "restored")])

    def test_gpu_mem_matches_unified_memory_gpus_by_share(self):
        gpus = hc.parse_gpu_csv("0, GPU-u1, NVIDIA GB10, [N/A], [N/A], 0\n")
        self.patch("nvidia", lambda: (gpus, [], None))
        g = self.claim("a", "u", "--gpu", "any", "--gpu-mem", "40G")["claim"]["resources"]["gpus"][0]
        self.assertEqual((g["index"], g["share"], g["mem_gb"]), (0, 0.5, None))
        self.patch("nvidia", lambda: (hc.parse_gpu_csv("0, GPU-t4, Tesla T4, 15360, 5, 0\n"), [], None))
        self.claim("a", "t", "--gpu", "any", "--gpu-mem", "40G", code=hc.NOFIT)

    def test_ssh_uri_and_ipv6_targets(self):
        for t in ("user@2001:db8::1", "user@[2001:db8::1]", "ssh://user@host:2222", "root@192.0.2.5"):
            self.assertEqual(hc.valid_target(t), t)
        for t in ("-oProxyCommand=x", "ssh://-oX", "a b", ""):
            self.assertRaises(hc.Fail, hc.valid_target, t)
        home = os.path.join(self.tmp, "home")
        root = os.path.join(home, ".solaris", "claims")
        os.makedirs(root)
        fake = os.path.join(self.tmp, "fake-ssh")
        with open(fake, "w") as f:
            f.write('#!/bin/sh\nwhile [ $# -gt 0 ]; do case "$1" in -o|-i|-p|-l|-F|-J) shift 2;; -*) shift;; *) break;; '
                    'esac; done\nshift\nexec /bin/sh -c "$*"\n')
        os.chmod(fake, 0o755)
        inv = os.path.join(self.tmp, "inv6.json")
        with open(inv, "w") as f:
            json.dump([{"name": "v6", "target": "ssh://own@[2001:db8::1]:2222"}], f)
        old = os.environ.get("HOME")
        os.environ["HOME"] = home
        try:
            r = self.cli("--agent", "own", "install", "--launcher", "setsid", "--disk-free-pct", "0",
                         base=["--hosts", inv, "--ssh", fake])["hosts"]["v6"]
            self.assertTrue(r["new"])
        finally:
            os.environ["HOME"] = old

    def test_idle_memory_is_not_work(self):
        # OS memory with no large unclaimed process: an idle paid host may be deleted
        self.cli("--agent", "own", "lease", "--set", "kind=paid", "--set", "planned_end=+5h", "--set", "usd_per_hour=2")
        self.patch("cpu_busy", lambda s: {})
        self.patch("meminfo", lambda: (16.0, 13.0))
        au = hc.host_op({"op": "audit", "root": self.root, "agent": "own", "now": time.time() + 3 * 3600})
        self.assertEqual(au["outside"]["ram_gb"], 0.0)
        self.assertEqual(hc.audit_recommend(au, hc.AUDIT_DEFAULTS)["action"], "delete")

    def test_large_unclaimed_processes_are_work(self):
        table = {10: (1, 10, "s10", 0.2), 11: (1, 11, "s11", 3.0), 12: (11, 11, "s12", 1.5), 13: (1, 13, "s13", 0.9)}
        claims = [{"id": "c", "pid": 11, "pid_start": "s11", "resources": {}}]
        _, rss, big = hc.attribute({"procs": table, "gpu_procs": []}, claims, 1.0)
        self.assertEqual((big, round(rss["c"], 2)), ([], 4.5))
        _, _, big = hc.attribute({"procs": table, "gpu_procs": []}, [], 1.0)
        self.assertEqual(sorted(b["pid"] for b in big), [11, 12])


class TestWithdraw(RealBase):
    def setUp(self):
        RealBase.setUp(self)
        self.cli("--agent", "own", "lease", "--set", "kind=paid", "--set", "planned_end=+1h", "--set", "usd_per_hour=2")
        self.rid = self.cli("--agent", "guest", "extend", "--job", "j", "--hours", "3", "--cores", "1",
                            "--ram", "1G")["request"]["id"]

    def age(self, minutes):
        path = os.path.join(self.root, "requests", self.rid + ".json")
        r = load(path)
        r["created"] = hc.iso(time.time() - minutes * 60)
        hc.write_json(path, r)

    def test_only_the_requester_withdraws(self):
        self.cli("--agent", "b", "request", "--withdraw", self.rid, code=hc.DENIED)
        self.assertIn("owner declines", self.cli("--agent", "own", "request", "--withdraw", self.rid,
                                                 code=hc.DENIED)["error"])
        self.assertEqual(self.cli("--agent", "guest", "request", "--withdraw", self.rid)["request"]["state"], "withdrawn")
        self.cli("--agent", "guest", "request", "--withdraw", self.rid, code=hc.DENIED)
        self.assertIn({"event": "request-withdrawn", "request": self.rid, "by": "guest"},
                      [dict((k, e.get(k)) for k in ("event", "request", "by")) for e in self.history()])
        self.assertEqual([q["state"] for q in self.cli("status")["hosts"]["local"]["requests"]], ["withdrawn"])
        au = hc.host_op({"op": "audit", "root": self.root, "agent": "own", "now": time.time() + 2 * 3600})
        self.assertEqual(hc.audit_recommend(au, hc.AUDIT_DEFAULTS)["action"], "delete")
        self.cli("--agent", "guest", "request", code=hc.USAGE)

    def test_fit_prints_the_withdraw_command_with_launch_new(self):
        self.age(40)
        rec = self.cli("--agent", "guest", "fit", "--job", "j", "--hours", "3", "--cores", "1", "--ram", "1G",
                       "--new-usd-per-hour", "1")["recommendation"]
        self.assertEqual(rec["action"], "launch-new")
        self.assertIn("request --host local --withdraw " + self.rid, rec["withdraw_cmd"])
        buf = io.StringIO()
        with redirect_stdout(buf):
            hc.main(["--local-root", self.root, "--agent", "guest", "fit", "--job", "j", "--hours", "3", "--cores", "1",
                     "--ram", "1G"])
        self.assertIn("withdraw the pending request", buf.getvalue())

    def test_audit_advises_declining_an_old_extension_request(self):
        self.assertEqual(self.cli("--agent", "own", "audit")["hosts"][0]["recommendation"]["action"], "extend")
        self.age(40)
        rec = self.cli("--agent", "own", "audit")["hosts"][0]["recommendation"]
        self.assertEqual(rec["action"], "decline")
        self.assertIn(self.rid, rec["why"])
        self.cli("--agent", "own", "decline", "--request", self.rid)
        au = hc.host_op({"op": "audit", "root": self.root, "agent": "own", "now": time.time() + 2 * 3600})
        self.assertEqual(hc.audit_recommend(au, hc.AUDIT_DEFAULTS)["action"], "delete")


class TestRevisedDocs(unittest.TestCase):
    def test_docs(self):
        root = os.path.dirname(HERE)
        rule = " ".join(text(os.path.join(root, "shared", "resource-sharing.rule.md")).split())
        skill = " ".join(text(os.path.join(root, "shared", "resource-sharing.skill.md")).split())
        self.assertIn("prunes finished run folders older than 14 days, `$HOSTCLAIMS_RUN_DIR` included", rule)
        self.assertNotIn("never deletes", rule)
        self.assertIn("--as-owner", rule)
        self.assertIn("repeat the same `claim` with", skill)
        self.assertIn("--can-wait", skill)
        self.assertIn("1 GiB or more", skill)
        self.assertIn("`--gpu-mem` matches them by share", skill)
        self.assertIn("request --host H --withdraw ID", skill)
        self.assertIn("- `decline`: a guest's extension request is older than a new instance takes to be ready", skill)

    def test_sharing_changes_docs(self):
        root = os.path.dirname(HERE)
        rule = " ".join(text(os.path.join(root, "shared", "resource-sharing.rule.md")).split())
        skill = " ".join(text(os.path.join(root, "shared", "resource-sharing.skill.md")).split())
        self.assertIn("Guests run `shared` at every audit and at least hourly", rule)
        self.assertIn("Owners run `install --all` right after adding machines or changing `share_with`", rule)
        self.assertIn("6 the hosts shared with this project changed since the last `shared --ack`", skill)
        self.assertIn("resource-sharing-seen.json", skill)
        self.assertIn("6 the hosts shared with this project changed since the last `shared --ack`",
                      " ".join(hc.__doc__.split()))
        self.assertEqual(hc.CHANGES, 6)


@unittest.skipUnless(sys.platform.startswith("linux") and os.path.isdir("/proc/self"), "real /proc readings: Linux only")
class TestLinuxReadings(unittest.TestCase):
    # no simulate.json here: the host's own /proc, nvidia-smi, memory and disk readings
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="hc-linux-")
        self.cwd = os.getcwd()
        os.chdir(self.tmp)
        self.saved_env = dict((k, os.environ.pop(k)) for k in list(os.environ) if k.startswith("HOSTCLAIMS_"))
        self.root = os.path.join(self.tmp, "root")
        self.procs = []

    def tearDown(self):
        for p in self.procs:
            try:
                os.killpg(p.pid, signal.SIGKILL)
            except OSError:
                pass
            p.wait(5)
        os.chdir(self.cwd)
        os.environ.update(self.saved_env)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def cli(self, *args, **kw):
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = hc.main(["--local-root", self.root, "--json", "--as-owner"] + list(args))
        self.assertEqual(rc, kw.get("code", 0), buf.getvalue())
        return json.loads(buf.getvalue())

    def test_real_readings_liveness_and_process_tree(self):
        with open("/proc/sys/kernel/random/boot_id") as f:
            self.assertEqual(hc.boot_id(), f.read().strip())
        self.cli("--agent", "a", "install", "--launcher", "setsid", "--share-with", "*")
        p = subprocess.Popen(["sh", "-c", "sleep 60 & wait"], start_new_session=True)
        self.procs.append(p)
        time.sleep(0.5)
        cid = self.cli("--agent", "a", "claim", "--job", "j", "--cores", "1", "--ram", "1G", "--pid", str(p.pid))["claim"]["id"]
        table = hc.proc_table()
        self.assertEqual(table[p.pid][0], os.getpid())
        child = [pid for pid, info in table.items() if info[0] == p.pid]
        self.assertEqual(len(child), 1)
        r = self.cli("--agent", "b", "claim", "--job", "k", "--cores", "1", "--ram", "1G", "--pid", str(child[0]),
                     code=hc.DENIED)
        self.assertIn(cid, r["error"])
        st = self.cli("status")["hosts"]["local"]
        self.assertEqual(st["claims"][0]["state"], "live")
        self.assertGreater(st["free"]["cores"], 0)
        self.assertGreater(st["free"]["ram_gb"], 0)
        os.killpg(p.pid, signal.SIGKILL)
        p.wait(5)
        path = os.path.join(self.root, "claims", cid + ".json")
        c = load(path)
        c["heartbeat"] = hc.iso(time.time() - 16 * 60)
        hc.write_json(path, c, 0o600)
        self.assertEqual([x["id"] for x in self.cli("reap")["hosts"]["local"]["reaped"]], [cid])

    def audit(self, t):
        au = hc.host_op({"op": "audit", "root": self.root, "agent": "a", "now": t})
        return au, hc.audit_recommend(au, hc.AUDIT_DEFAULTS)

    def test_real_outside_work_blocks_delete(self):
        self.cli("--agent", "a", "install", "--launcher", "setsid", "--share-with", "*")
        self.cli("--agent", "a", "lease", "--set", "kind=paid", "--set", "planned_end=+5h", "--set", "usd_per_hour=2")
        t = time.time() + 2 * 3600
        au, rec = self.audit(t)
        self.assertEqual(rec["action"], "delete", au["outside"])
        big = subprocess.Popen([sys.executable, "-c", "b = bytearray(int(1.5 * 2 ** 30)); import time; time.sleep(60)"],
                               start_new_session=True)
        self.procs.append(big)
        self.assertTrue(wait_for(lambda: (hc.proc_table().get(big.pid) or (0, 0, 0, 0))[3] > 1.4, 20))
        au, rec = self.audit(t)
        self.assertIn(big.pid, au["outside"]["pids"])
        self.assertEqual(rec["action"], "check")
        os.killpg(big.pid, signal.SIGKILL)
        big.wait(5)
        spin = subprocess.Popen(["sh", "-c", "while :; do :; done & while :; do :; done"], start_new_session=True)
        self.procs.append(spin)
        time.sleep(1)
        au, rec = self.audit(t)
        self.assertGreaterEqual(au["outside"]["cpu_cores"], 1.0)
        self.assertEqual(rec["action"], "check")

    def test_real_run_is_pinned(self):
        self.cli("--agent", "a", "install", "--launcher", "setsid", "--share-with", "*", "--rule", "heartbeat_s=1")
        r = self.cli("--agent", "a", "run", "--job", "pin", "--cores", "2", "--ram", "1G", "--",
                     "sh", "-c", "grep Cpus_allowed_list /proc/self/status")
        done = os.path.join(r["claim"]["run_dir"], "done.json")
        self.assertTrue(wait_for(lambda: os.path.exists(done), 20))
        want = hc.fmt_cores(r["claim"]["resources"]["cores"])
        self.assertIn("Cpus_allowed_list:\t" + want, text(os.path.join(r["claim"]["run_dir"], "job.log")))


class TestPortability(unittest.TestCase):
    def test_python38_grammar(self):
        ast.parse(SRC, feature_version=(3, 8))

    def test_small_parsers(self):
        self.assertEqual(hc.parse_cores("0-3,8,10-11"), [0, 1, 2, 3, 8, 10, 11])
        self.assertEqual(hc.fmt_cores([0, 1, 2, 3, 8, 10, 11]), "0-3,8,10-11")
        self.assertEqual(hc.parse_gb("512M"), 0.5)
        self.assertEqual(hc.parse_hours("90m"), 1.5)
        self.assertEqual(hc.parse_iso("2026-09-29T18:00-07:00"), hc.parse_iso("2026-09-30T01:00Z"))
        self.assertEqual(hc.parse_gpu_spec("any:0.25:6G"), {"index": None, "share": 0.25, "mem_gb": 6.0})


if __name__ == "__main__":
    unittest.main()
