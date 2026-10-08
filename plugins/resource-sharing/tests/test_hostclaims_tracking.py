# rev. 1

"""Tests for shared/tools/hostclaims.py: cores held by running jobs, memory as PSS, attached processes, renew,
paired claims and their yield order, and each GPU's last claim (stdlib unittest).

Uses the harness of test_hostclaims.py: temp claims folders with simulated readings, real short-lived processes for
liveness, and fake process tables and memory readers for the Linux readings (never the real machine's processes in
an assertion).

    python3 -m unittest discover -s plugins/resource-sharing/tests -v
"""

import io
import json
import os
import signal
import subprocess
import sys
import time
import unittest
from contextlib import redirect_stdout

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from test_hostclaims import T4, Base, RealBase, hc, load, sim, text, wait_for  # noqa: E402

# process ids no machine hands out (above the kernel's pid_max), for fake process tables
FAKE = 4000000000


def history_events(base, event):
    return [e for e in base.history() if e.get("event") == event]


class TestCores(Base):
    def cores(self, r):
        return set(r["claim"]["resources"]["cores"])

    def test_live_claims_never_share_cores_whatever_their_class(self):
        # the reported case: an owner's CPU jobs (a bare claim renewed by hand, a run, small claims) and a guest's
        # P1 preemptible borrowed claim with a GPU, on a 72-core host
        self.write_sim(sim(cpus=72, ram_total_gb=736.0, gpus=[dict(T4, index=0)]))
        held = []
        held.append(self.cores(self.claim("own", "io", cores=1)))
        held.append(self.cores(self.claim("own", "freq", cores=2, ram="8G")))
        r = self.cli("--agent", "own", "run", "--job", "read", "--cores", "16", "--ram", "64G", "--hours", "2", "--",
                     "sleep", "30")
        held.append(set(r["claim"]["resources"]["cores"]))
        guest = self.claim("guest", "p1b", "--class", "P1", "--preemptible", "--borrowed", "--gpu", "0", "--hours",
                           "8", cores=8, ram="200G")
        held.append(self.cores(guest))
        held.append(self.cores(self.claim("own", "taut", "--class", "P2", cores=4)))
        for i, a in enumerate(held):
            for b in held[i + 1:]:
                self.assertFalse(a & b, (a, b))
        # a renewal of the bare claim keeps its cores and still overlaps nothing
        self.assertEqual(self.cores(self.claim("own", "io", cores=1)), held[0])

    def test_a_lapsed_claims_running_job_keeps_its_cores(self):
        # a bare claim lapses between renewals while its job still runs pinned to the claim's cores
        old = self.claim("a", "fetch", cores=4)["claim"]
        self.assertEqual(old["resources"]["cores"], [0, 1, 2, 3])
        self.patch_sim(pinned=[{"pid": FAKE + 1, "cores": [0, 1, 2, 3], "rss_gb": 2.0}], clock_offset_s=16 * 60)
        # another project's claim must not get the cores the job still runs on
        b = self.claim("b", "train", cores=4)
        self.assertFalse(self.cores(b) & set([0, 1, 2, 3]), b["claim"]["resources"]["cores"])
        self.claim("c", "more", cores=1, code=hc.NOFIT)
        st = self.cli("status")["hosts"]["local"]
        self.assertEqual([(p["pid"], p["outside"]) for p in st["pinned"]], [(FAKE + 1, [0, 1, 2, 3])])
        self.assertIsNone(st["pinned"][0]["claim"])

    def test_a_job_pinned_outside_its_claim_holds_those_cores_and_shows(self):
        p = self.sleeper()
        a = self.claim("a", "x", "--pid", str(p.pid), cores=2)["claim"]
        self.assertEqual(a["resources"]["cores"], [0, 1])
        self.patch_sim(pinned=[{"pid": p.pid, "cores": [0, 1, 2, 3], "rss_gb": 1.0}])
        self.assertEqual(self.cores(self.claim("b", "y", cores=4)), set([4, 5, 6, 7]))
        self.claim("c", "z", cores=1, code=hc.NOFIT)
        st = self.cli("status")["hosts"]["local"]
        self.assertEqual([(x["pid"], x["claim"], x["outside"]) for x in st["pinned"]], [(p.pid, a["id"], [2, 3])])
        lines = hc.status_lines({"name": "local"}, st)
        self.assertTrue(any("runs on cores 2-3 outside its claim" in ln for ln in lines), lines)

    def test_an_untied_job_on_its_own_claims_cores_is_no_alarm(self):
        # claim first, then start the job pinned to the claim's cores without tying it: the usual bare-claim pattern
        a = self.claim("a", "bare", cores=2)["claim"]
        self.patch_sim(pinned=[{"pid": FAKE + 3, "cores": a["resources"]["cores"], "rss_gb": 1.0}])
        st = self.cli("status")["hosts"]["local"]
        self.assertEqual((st["pinned"], st["free"]["cores"]), ([], 6))

    def test_pinned_load_is_not_counted_twice(self):
        # four busy cores pinned outside any claim: those four are held, and their load hides no further core
        self.patch_sim(pinned=[{"pid": FAKE + 2, "cores": [4, 5, 6, 7], "rss_gb": 3.0}],
                       cpu_busy={"4": 1.0, "5": 1.0, "6": 1.0, "7": 1.0})
        self.assertEqual(self.cores(self.claim("a", "w", cores=4)), set([0, 1, 2, 3]))
        free = self.cli("status")["hosts"]["local"]["free"]
        self.assertEqual(free["cores"], 0)
        self.assertEqual(free["unclaimed_cpu"], 4.0)

    def test_status_names_claims_that_share_cores(self):
        # a claim file another tool version or a hand edit left on cores a live claim holds
        a = self.claim("a", "one", cores=2)["claim"]
        b = self.claim("b", "two", cores=2)["claim"]
        self.set_claim(b["id"], resources=dict(b["resources"], cores=[1, 2]))
        st = self.cli("status")["hosts"]["local"]
        self.assertEqual(st["overlaps"], [{"claims": sorted([a["id"], b["id"]]), "cores": [1]}])
        self.assertTrue(any("share cores 1" in ln for ln in hc.status_lines({"name": "local"}, st)))
        # and a new claim is never granted a core a live claim holds
        c = self.claim("c", "three", cores=3)["claim"]
        self.assertFalse(set(c["resources"]["cores"]) & set([0, 1, 2]))

    def test_a_reclaim_takes_back_the_cores_its_running_job_is_pinned_to(self):
        # the job runs on, untied and busy, pinned to its lapsed claim's cores; another project takes the rest of the host
        self.assertEqual(self.claim("a", "fetch", cores=4)["claim"]["resources"]["cores"], [0, 1, 2, 3])
        self.patch_sim(pinned=[{"pid": FAKE + 7, "cores": [0, 1, 2, 3], "rss_gb": 2.0}], clock_offset_s=16 * 60,
                       cpu_busy={"0": 1.0, "1": 1.0, "2": 1.0, "3": 1.0})
        self.assertEqual(self.claim("other", "rest", cores=4)["claim"]["resources"]["cores"], [4, 5, 6, 7])
        # the same job's re-claim gets its job's cores back, and the job's own load hides none of them
        r = self.claim("a", "fetch", cores=4)
        self.assertEqual((r["claim"]["resources"]["cores"], r["lapsed"]["kept"]), ([0, 1, 2, 3], True))
        st = self.cli("status")["hosts"]["local"]
        self.assertEqual((st["pinned"], st["free"]["cores"]), ([], 0))
        # another project still cannot take them while the job runs
        self.claim("b", "more", cores=1, code=hc.NOFIT)

    def test_a_reclaim_with_pid_takes_back_the_cores_its_job_runs_on(self):
        job = self.sleeper()
        self.claim("a", "fetch", cores=4)
        self.patch_sim(pinned=[{"pid": job.pid, "cores": [0, 1, 2, 3], "rss_gb": 2.0}], clock_offset_s=16 * 60)
        r = self.claim("a", "fetch", "--pid", str(job.pid), cores=4)
        self.assertEqual(r["claim"]["resources"]["cores"], [0, 1, 2, 3])
        # the job runs inside its claim: nothing pinned outside, the other four cores free
        st = self.cli("status")["hosts"]["local"]
        self.assertEqual((st["pinned"], st["free"]["cores"]), ([], 4))

    def test_a_claim_tied_to_a_pinned_process_takes_the_cores_it_runs_on(self):
        job = self.sleeper()
        self.patch_sim(pinned=[{"pid": job.pid, "cores": [4, 5, 6, 7], "rss_gb": 2.0}])
        r = self.claim("a", "late", "--pid", str(job.pid), cores=4)
        self.assertEqual(r["claim"]["resources"]["cores"], [4, 5, 6, 7])
        self.assertEqual(self.cli("status")["hosts"]["local"]["pinned"], [])
        # a smaller claim keeps what it can, and the answer says where the process runs outside it
        job2 = self.sleeper()
        self.patch_sim(pinned=[{"pid": job.pid, "cores": [4, 5, 6, 7], "rss_gb": 2.0},
                               {"pid": job2.pid, "cores": [0, 1, 2], "rss_gb": 2.0}])
        r = self.claim("b", "small", "--pid", str(job2.pid), cores=2)
        self.assertEqual((r["claim"]["resources"]["cores"], r["pinned_job"]["kept"]), ([0, 1], False))
        st = self.cli("status")["hosts"]["local"]
        self.assertEqual([(p["pid"], p["outside"]) for p in st["pinned"]], [(job2.pid, [2])])

    def test_the_grant_guard_refuses_a_plan_that_overlaps_a_live_claim(self):
        from unittest import mock
        held = self.claim("a", "one", cores=2)["claim"]
        real = hc.plan

        def faulty(*args, **kw):
            # a planning fault: one of the planned cores is a core a live claim holds
            p = real(*args, **kw)
            p["cores"] = sorted(set(p["cores"][1:]) | set(held["resources"]["cores"][:1]))
            return p

        with mock.patch.object(hc, "plan", faulty):
            r = self.claim("b", "two", cores=2, code=hc.ERROR)
        self.assertIn("are held by claim %s; nothing was claimed" % held["id"], r["error"])
        self.assertEqual([c["id"] for c in self.all_claims()], [held["id"]])

    def test_a_reclaim_after_a_lapse_keeps_the_cores_it_had(self):
        self.claim("other", "first", cores=1)
        mine = self.claim("a", "io", cores=2)["claim"]
        self.assertEqual(mine["resources"]["cores"], [1, 2])
        self.patch_sim(clock_offset_s=16 * 60)
        self.cli("--agent", "other", "release", "--job", "first")
        again = self.claim("a", "io", cores=2)
        self.assertNotEqual(again["claim"]["id"], mine["id"])
        self.assertEqual(again["claim"]["resources"]["cores"], [1, 2])
        self.assertEqual((again["lapsed"]["id"], again["lapsed"]["kept"]), (mine["id"], True))
        # it lapses again, and this time another claim takes one of its cores first: the answer says where to move
        self.patch_sim(clock_offset_s=40 * 60)
        self.assertEqual(self.claim("b", "k", cores=2)["claim"]["resources"]["cores"], [0, 1])
        buf = io.StringIO()
        with redirect_stdout(buf):
            hc.main(["--local-root", self.root, "--agent", "a", "claim", "--job", "io", "--cores", "2", "--ram", "1G"])
        self.assertIn("lapsed", buf.getvalue())
        self.assertIn("its cores were 1-2, this one's are 2-3", buf.getvalue())
        self.assertIn("taskset -acp 2-3", buf.getvalue())


class TestMemory(RealBase):
    # the machine's own readers, patched: a fake process table, a fake PSS reader and fixed memory totals
    def setUp(self):
        RealBase.setUp(self)
        self.patch("cpu_busy", lambda s: {})
        self.patch("meminfo", lambda: (512.0, 300.0))
        self.pss = {}
        self.calls = []

        def reader(pid):
            self.calls.append(pid)
            return self.pss.get(pid)

        self.patch("read_pss", reader)

    def forked(self, leaders, workers=60):
        # each leader (a real process, so its claim is live) heads a session of forked workers that share its pages:
        # 6 GiB RSS and 1.5 GiB PSS for the leader, 5.5 GiB RSS and 1.2 GiB PSS for each worker
        table, n = {}, FAKE
        for p in leaders:
            table[p.pid] = (os.getpid(), p.pid, hc.proc_start(p.pid), 6.0)
            self.pss[p.pid] = 1.5
            for _ in range(workers):
                n += 1
                table[n] = (p.pid, p.pid, "w%d" % n, 5.5)
                self.pss[n] = 1.2
        self.patch("proc_table", lambda: dict(table))
        return table

    def test_forked_workers_count_once(self):
        # the reported case: two runs with dozens of forked data-loader workers, each declared 48G, then a guest's 116G
        a, b = self.sleeper(), self.sleeper()
        self.forked([a, b])
        self.claim("own", "pace1", "--pid", str(a.pid), ram="48G")
        self.claim("own", "pace2", "--pid", str(b.pid), ram="48G")
        st = self.cli("status")["hosts"]["local"]
        mem = dict((c["job"], (c["mem_gb"], c["mem_source"])) for c in st["claims"])
        self.assertEqual(mem, {"pace1": (73.5, "pss"), "pace2": (73.5, "pss")})
        # used 212G: 147G is the two runs' PSS, 65G is outside them; free is what the declarations leave
        self.assertAlmostEqual(st["free"]["ram_gb"], 300.0, delta=0.2)
        self.assertAlmostEqual(st["free"]["unclaimed_ram_gb"], 65.0, delta=0.2)
        self.claim("guest", "queue", ram="116G")
        line = [ln for ln in hc.status_lines({"name": "local"}, self.cli("status")["hosts"]["local"]) if "pace1" in ln][0]
        self.assertIn("48.0G RAM (73.5G in use, 25.5G over)", line)
        # summed RSS counted each shared page once per worker: the same host looked full
        self.pss.clear()
        r = self.claim("guest", "queue2", ram="116G", code=hc.NOFIT)
        self.assertIn("ram", r["reasons"][0])
        st = self.cli("status")["hosts"]["local"]
        self.assertEqual(dict((c["job"], c.get("mem_source")) for c in st["claims"])["pace1"], "rss")

    def test_status_shows_the_tied_processes(self):
        p = self.sleeper()
        self.forked([p], workers=2)
        self.claim("own", "j", "--pid", str(p.pid), ram="8G")
        c = self.cli("status")["hosts"]["local"]["claims"][0]
        self.assertEqual([(x["pid"], x["alive"]) for x in c["procs"]], [(p.pid, True)])
        self.assertEqual(c["procs"][0]["prog"], "sleep")


class TestMemoryReaders(unittest.TestCase):
    def test_attribute_reads_pss_falls_back_to_rss_and_skips_small_processes(self):
        procs = {FAKE + 1: (1, FAKE + 1, "s1", 4.0), FAKE + 2: (FAKE + 1, FAKE + 1, "s2", 4.0),
                 FAKE + 3: (FAKE + 1, FAKE + 1, "s3", 4.0), FAKE + 4: (FAKE + 1, FAKE + 1, "s4", 0.001),
                 FAKE + 5: (1, FAKE + 5, "s5", 2.0), FAKE + 6: (1, FAKE + 6, "s6", 2.0)}
        pss = {FAKE + 1: 1.0, FAKE + 2: 0.5, FAKE + 3: None, FAKE + 5: 0.4, FAKE + 6: 1.5}
        calls = []

        def reader(pid):
            calls.append(pid)
            return pss.get(pid)

        claims = [{"id": "c", "pid": FAKE + 1, "pid_start": "s1", "resources": {}}]
        src = {}
        _, mem, big = hc.attribute({"procs": procs, "gpu_procs": []}, claims, 1.0, reader, src)
        # PSS where it reads, RSS where it does not (FAKE+3), small processes by RSS without a read
        self.assertAlmostEqual(mem["c"], 1.0 + 0.5 + 4.0 + 0.001)
        self.assertEqual(src["c"], "pss+rss")
        self.assertNotIn(FAKE + 4, calls)
        # a 2 GiB-RSS process whose own share is 0.4 GiB is no large outside process
        self.assertEqual(big, [{"pid": FAKE + 6, "rss_gb": 2.0, "mem_gb": 1.5}])
        _, mem, _ = hc.attribute({"procs": procs, "gpu_procs": []}, claims, 1.0)
        self.assertAlmostEqual(mem["c"], 12.001)

    def test_pinned_procs_reads_affinity_and_leaves_out_threads_helpers_and_node_policies(self):
        from unittest import mock
        # pid -> (ppid, session, start, rss GiB) on a 16-core host
        table = {1: (0, 1, "s", 0.01), 2: (0, 0, "s", 0.0), 50: (2, 0, "k", 0.0), FAKE + 1: (1, FAKE + 1, "a", 2.0),
                 FAKE + 2: (1, FAKE + 2, "b", 2.0), FAKE + 3: (1, FAKE + 3, "c", 0.01),
                 FAKE + 4: (1, FAKE + 4, "d", 2.0), FAKE + 5: (1, FAKE + 5, "e", 2.0)}
        # a kernel thread on core 7, a job on 0-3, one unpinned, a small helper on 5, one on a whole node of two
        aff = {50: set([7]), FAKE + 1: set([0, 1, 2, 3]), FAKE + 2: set(range(16)), FAKE + 3: set([5]),
               FAKE + 4: set(range(8))}

        def getaff(pid):
            if pid not in aff:
                raise ProcessLookupError(pid)
            return aff[pid]

        with mock.patch.object(hc.os, "sched_getaffinity", side_effect=getaff, create=True):
            self.assertEqual(hc.pinned_procs(table, list(range(16))),
                             [{"pid": FAKE + 1, "cores": [0, 1, 2, 3], "rss_gb": 2.0}])
        self.assertEqual(hc.pinned_procs(None, list(range(16))), [])

    def test_read_pss_parses_smaps_rollup(self):
        from unittest import mock
        rollup = "00400000-7ffff000 ---p 00000000 00:00 0  [rollup]\nRss:  5767168 kB\nPss:  1258291 kB\nPss_Anon: 1 kB\n"
        with mock.patch.object(hc, "open", mock.mock_open(read_data=rollup), create=True):
            self.assertAlmostEqual(hc.read_pss(123), 1258291 / 1048576.0)
        with mock.patch.object(hc, "open", side_effect=PermissionError, create=True):
            self.assertIsNone(hc.read_pss(123))


class TestAttach(Base):
    def path(self, cid):
        return os.path.join(self.root, "claims", cid + ".json")

    def test_more_processes_attach_and_keep_the_claim_live(self):
        p1, p2 = self.sleeper(), self.sleeper()
        c = self.claim("a", "fetch", "--pid", str(p1.pid), cores=0)["claim"]
        r = self.claim("a", "fetch", "--pid", str(p2.pid), cores=0)
        self.assertTrue(r["existing"])
        self.assertEqual([(x["pid"], x["new"]) for x in r["attached"]], [(p2.pid, True)])
        self.assertFalse(self.claim("a", "fetch", "--pid", str(p2.pid), cores=0)["attached"][0]["new"])
        p1.kill()
        p1.wait()
        self.set_claim(c["id"], heartbeat=hc.iso(time.time() - 30 * 60))
        self.assertEqual(self.cli("reap")["hosts"]["local"]["reaped"], [])
        # older tool versions read only pid: it moves to the process that still runs
        cur = load(self.path(c["id"]))
        self.assertEqual(cur["pid"], p2.pid)
        self.assertFalse([x for x in cur.get("pids") or [] if x["pid"] == p1.pid])
        self.assertTrue(cur.get("seen"))
        st = self.cli("status")["hosts"]["local"]["claims"][0]
        self.assertEqual([(x["pid"], x["alive"]) for x in st["procs"]], [(p2.pid, True)])

    def test_a_lapsed_tied_claim_ends_at_its_last_sighting(self):
        p = self.sleeper()
        c = self.claim("a", "j", "--pid", str(p.pid))["claim"]
        old = hc.iso(time.time() - 3 * 3600)
        self.set_claim(c["id"], heartbeat=old, created=old)
        self.cli("reap")
        seen = load(self.path(c["id"]))["seen"]
        p.kill()
        p.wait()
        self.assertEqual([x["id"] for x in self.cli("reap")["hosts"]["local"]["reaped"]], [c["id"]])
        ev = history_events(self, "stale")[-1]
        self.assertEqual(ev["end"], seen)
        self.assertGreater(ev["hours"], 2.9)
        self.assertIn("last seen alive", ev["reason"])

    def test_pid_attaches_to_a_run_claim_and_survives_its_heartbeats(self):
        r = self.cli("--agent", "a", "run", "--job", "srv", "--cores", "1", "--ram", "1G", "--", "sleep", "30")
        cid = r["claim"]["id"]
        self.assertTrue(wait_for(lambda: self.all_claims()[0].get("pid")))
        extra = self.sleeper()
        out = self.claim("a", "srv", "--pid", str(extra.pid))
        self.assertEqual([x["pid"] for x in out["attached"]], [extra.pid])
        hb = load(self.path(cid))["heartbeat"]
        self.assertTrue(wait_for(lambda: load(self.path(cid))["heartbeat"] != hb, 10))
        cur = load(self.path(cid))
        self.assertEqual([x["pid"] for x in cur["pids"]], [extra.pid])
        self.assertNotEqual(cur["pid"], extra.pid)
        self.assertTrue(self.cli("--agent", "a", "release", "--job", "srv", "--stop")["stopping"])

    def test_two_pids_at_once_and_a_stop_signals_each(self):
        p1, p2 = self.sleeper(), self.sleeper()
        c = self.claim("a", "pair", "--pid", str(p1.pid), "--pid", str(p2.pid))["claim"]
        self.assertEqual((c["pid"], [x["pid"] for x in c["pids"]]), (p1.pid, [p2.pid]))
        self.assertEqual(sorted(e["pid"] for e in history_events(self, "tie")), sorted([p1.pid, p2.pid]))
        self.cli("--agent", "a", "release", "--job", "pair", code=hc.DENIED)
        self.assertTrue(self.cli("--agent", "a", "release", "--job", "pair", "--stop")["released"])
        self.assertEqual((p1.wait(10), p2.wait(10)), (-signal.SIGTERM, -signal.SIGTERM))

    def test_the_answer_names_the_attached_program(self):
        p = self.sleeper()
        self.claim("a", "x")
        buf = io.StringIO()
        with redirect_stdout(buf):
            hc.main(["--local-root", self.root, "--agent", "a", "claim", "--job", "x", "--cores", "1", "--ram", "1G",
                     "--pid", str(p.pid)])
        self.assertIn("attached pid %d (sleep)" % p.pid, buf.getvalue())
        self.assertNotIn("lapses at", buf.getvalue())


class TestRenew(Base):
    def renew(self, agent, *args, **kw):
        return self.cli("--agent", agent, "renew", *args, **kw)

    def test_renew_moves_the_end_and_flags_without_a_release(self):
        c = self.claim("a", "worker", "--class", "P2", "--no-preemptible", "--hours", "1",
                       "--pid", str(self.sleeper().pid))["claim"]
        r = self.renew("a", "--job", "worker", "--hours", "6", "--preemptible", "--borrowed")
        x = r["renewed"][0]
        self.assertEqual(x["claim"]["id"], c["id"])
        self.assertAlmostEqual(hc.parse_iso(x["claim"]["eta"]) - time.time(), 6 * 3600, delta=120)
        self.assertEqual((x["claim"]["preemptible"], x["claim"]["borrowed"]), (True, True))
        self.assertEqual(sorted(x["changes"]), ["borrowed", "eta", "preemptible"])
        self.assertEqual(len(self.all_claims()), 1)
        self.assertEqual([e["id"] for e in history_events(self, "renew")], [c["id"]])
        self.assertEqual(history_events(self, "end"), [])
        # now another project may ask it to yield
        self.patch_sim(clock_offset_s=25 * 60)
        self.cli("--agent", "b", "yield", "--claim", c["id"], "--class", "P1")

    def test_renew_checks_class_rules_and_the_paid_lease(self):
        self.claim("a", "p0", "--class", "P0")
        self.renew("a", "--job", "p0", "--preemptible", code=hc.USAGE)
        self.claim("a", "p3", "--class", "P3")
        self.renew("a", "--job", "p3", "--no-preemptible", code=hc.USAGE)
        self.cli("--agent", "own", "lease", "--set", "kind=paid", "--set", "planned_end=+3h", "--set", "usd_per_hour=2")
        r = self.renew("a", "--job", "p0", "--hours", "5", code=hc.NOFIT)
        self.assertIn("planned end", r["reasons"][0])
        self.renew("a", "--job", "p0", "--until", "+2h")
        self.renew("a", "--job", "p0", "--hours", "1", "--until", "+2h", code=hc.USAGE)
        self.renew("a", "--job", "nope", code=hc.ERROR)
        self.renew("b", "--job", "p0", code=hc.ERROR)
        self.renew("a", code=hc.USAGE)

    def test_renew_keeps_the_admission_rules_of_a_dedicated_host(self):
        # a claim from before the owner dedicated the host may still become more yieldable
        early = self.claim("guest", "early", "--pid", str(self.sleeper().pid))["claim"]
        self.cli("--agent", "own", "install", "--mode", "dedicated:own")
        self.renew("guest", "--job", "early", "--preemptible")
        self.claim("guest", "g", "--pid", str(self.sleeper().pid), code=hc.NOFIT)
        g = self.claim("guest", "g", "--preemptible", "--borrowed", "--pid", str(self.sleeper().pid))["claim"]
        # dropping either flag would put the claim beyond the owner's reach: refused, nothing written
        for flag in ("--no-preemptible", "--no-borrowed"):
            r = self.renew("guest", "--job", "g", flag, "--hours", "2", code=hc.DENIED)
            self.assertIn("dedicated to own", r["error"])
        cur = load(os.path.join(self.root, "claims", g["id"] + ".json"))
        self.assertEqual((cur["preemptible"], cur["borrowed"], cur.get("eta")), (True, True, None))
        self.patch_sim(clock_offset_s=25 * 60)
        self.cli("--agent", "own", "yield", "--claim", g["id"], "--class", "P0")
        # the owner changes its own claims freely, and the guest every other setting
        self.claim("own", "mine", "--class", "P2", "--pid", str(self.sleeper().pid))
        self.renew("own", "--job", "mine", "--no-preemptible")
        self.renew("guest", "--job", "g", "--hours", "2")
        # a draining host takes no new claims but lets live ones change
        self.cli("--agent", "own", "install", "--mode", "draining")
        self.assertEqual(self.renew("guest", "--job", "early", "--no-preemptible")["renewed"][0]["claim"]["id"],
                         early["id"])

    def test_renew_all_and_a_lapsed_claim(self):
        self.claim("a", "x")
        self.claim("a", "y")
        self.claim("b", "z")
        r = self.renew("a", "--all", "--hours", "2")
        self.assertEqual(sorted(x["claim"]["job"] for x in r["renewed"]), ["x", "y"])
        self.renew("a", "--all", "--pid", str(self.sleeper().pid), code=hc.USAGE)
        self.patch_sim(clock_offset_s=16 * 60)
        r = self.renew("a", "--job", "x", code=hc.ERROR)
        self.assertIn("stale/", r["error"])

    def test_renew_refreshes_a_bare_claims_heartbeat_and_prints_it(self):
        self.claim("a", "bare")
        self.patch_sim(clock_offset_s=10 * 60)
        self.assertEqual(self.renew("a", "--job", "bare")["renewed"][0]["claim"]["heartbeat_age_s"], 0)
        self.patch_sim(clock_offset_s=20 * 60)
        self.assertEqual(self.cli("reap")["hosts"]["local"]["reaped"], [])
        buf = io.StringIO()
        with redirect_stdout(buf):
            hc.main(["--local-root", self.root, "--agent", "a", "renew", "--job", "bare", "--hours", "3"])
        self.assertIn("renewed a/bare: end ", buf.getvalue())

    def test_renew_attaches_a_process(self):
        self.claim("a", "j")
        p = self.sleeper()
        x = self.renew("a", "--job", "j", "--pid", str(p.pid))["renewed"][0]
        self.assertEqual((x["claim"]["pid"], x["changes"]["pids"]), (p.pid, [p.pid]))
        self.assertNotIn("lapses_at", x["claim"])


class TestPairs(Base):
    def run_job(self, job, *extra):
        loop = "trap 'echo %s-stop; exit 0' TERM; while :; do sleep 0.2; done" % job
        return self.cli("--agent", "a", "run", "--job", job, "--class", "P2", "--cores", "1", "--ram", "1G", *extra,
                        "--", "sh", "-c", loop)["claim"]

    def test_a_yield_reaches_the_queue_first_and_the_server_after_it(self):
        q = self.run_job("queue")
        s = self.run_job("server", "--yield-with", "queue")
        self.assertEqual(s["yield_with"], ["queue"])
        self.assertTrue(wait_for(lambda: len(self.all_claims()) == 2 and all(c.get("pid") for c in self.all_claims())))
        self.patch_sim(clock_offset_s=25 * 60)
        # asked on the server: the queue it names goes first
        r = self.cli("--agent", "b", "yield", "--claim", s["id"], "--class", "P0", "--job", "urgent")
        self.assertEqual(sorted((g["job"], g["deferred"]) for g in r["group"]), [("queue", False), ("server", True)])
        done = [os.path.join(c["run_dir"], "done.json") for c in (q, s)]
        self.assertTrue(wait_for(lambda: all(os.path.exists(d) for d in done) and not self.all_claims(), 30))
        events = [(e["event"], e.get("job")) for e in self.history() if e["event"] in ("end", "yield-deliver")]
        self.assertEqual(events, [("end", "queue"), ("yield-deliver", "server"), ("end", "server")])
        self.assertEqual([load(d)["reason"] for d in done], ["yielded", "yielded"])
        self.assertIn("server-stop", text(os.path.join(s["run_dir"], "job.log")))

    def test_a_partner_past_its_grace_lets_the_next_yield(self):
        q = self.claim("a", "queue", "--class", "P2", "--pid", str(self.sleeper().pid))["claim"]
        s = self.claim("a", "server", "--class", "P2", "--yield-with", "queue", "--pid", str(self.sleeper().pid))["claim"]
        self.patch_sim(clock_offset_s=25 * 60)
        self.cli("--agent", "b", "yield", "--claim", q["id"], "--class", "P0")
        st = self.cli("status")["hosts"]["local"]
        ys = dict((c["job"], c["yield"]) for c in st["claims"])
        self.assertFalse(ys["queue"].get("deferred"))
        self.assertEqual((ys["server"]["deferred"], ys["server"]["after"], ys["server"]["via"]), (True, [q["id"]], q["id"]))
        self.assertTrue(any("follows when its partner is done" in ln for ln in hc.status_lines({"name": "local"}, st)))
        self.assertTrue(os.path.exists(os.path.join(self.root, "run", q["id"], "yield.json")))
        self.assertFalse(os.path.exists(os.path.join(self.root, "run", s["id"], "yield.json")))
        # the queue's job ignores it: once its 10-minute grace and a margin pass, the server's turn comes
        self.patch_sim(clock_offset_s=36 * 60)
        st = self.cli("status")["hosts"]["local"]
        self.assertFalse(dict((c["job"], c["yield"]) for c in st["claims"])["server"]["deferred"])
        self.assertTrue(os.path.exists(os.path.join(self.root, "run", s["id"], "yield.json")))
        self.assertEqual([e["job"] for e in history_events(self, "yield-deliver")], ["server"])

    def test_the_yield_order_may_not_loop(self):
        self.claim("a", "x", "--yield-with", "y")
        self.assertIn("loops", self.claim("a", "y", "--yield-with", "x", code=hc.USAGE)["error"])
        self.claim("a", "z", "--yield-with", "z", code=hc.USAGE)
        self.claim("a", "y")
        self.cli("--agent", "a", "renew", "--job", "y", "--yield-with", "x", code=hc.USAGE)
        r = self.cli("--agent", "a", "renew", "--job", "x", "--yield-with", "none")
        self.assertEqual(r["renewed"][0]["claim"]["yield_with"], [])
        self.cli("--agent", "a", "renew", "--job", "y", "--yield-with", "x")

    def test_asking_one_paired_claim_frees_its_partner(self):
        self.claim("a", "queue", "--class", "P3", "--gpu", "0:0.5", "--pid", str(self.sleeper().pid))
        self.claim("a", "server", "--class", "P2", "--gpu", "0:0.5", "--yield-with", "queue",
                   "--pid", str(self.sleeper().pid))
        self.claim("c", "busy", "--class", "P1", "--gpu", "1:1", "--pid", str(self.sleeper().pid))
        self.patch_sim(clock_offset_s=25 * 60)
        r = self.claim("b", "urgent", "--class", "P0", "--gpu", "any", code=hc.NOFIT)
        self.assertEqual(len(r["yield_candidates"]), 1)


class TestYieldSignalNone(Base):
    def start(self, job, *extra):
        r = self.cli("--agent", "a", "run", "--job", "quiet", "--class", "P3", "--yield-signal", "none", "--cores", "1",
                     "--ram", "1G", *extra, "--", "sh", "-c", job)["claim"]
        c = wait_for(lambda: [x for x in self.all_claims() if x.get("pid") and (x.get("launcher") or {}).get("pid")])
        self.patch_sim(clock_offset_s=25 * 60)
        return r["id"], r["run_dir"], c[0]

    def test_a_job_reads_its_yield_file_and_ends_itself(self):
        cid, rd, _ = self.start('while [ ! -f "$HOSTCLAIMS_YIELD_FILE" ]; do sleep 0.2; done; echo saw-it; exit 0')
        self.cli("--agent", "b", "yield", "--claim", cid, "--class", "P0")
        self.assertTrue(wait_for(lambda: os.path.exists(os.path.join(rd, "done.json")), 20))
        self.assertEqual((load(os.path.join(rd, "done.json"))["reason"], load(os.path.join(rd, "done.json"))["exit_code"]),
                         ("yielded", 0))
        self.assertIn("saw-it", text(os.path.join(rd, "job.log")))

    def test_no_signal_at_the_request_and_kill_when_the_grace_runs_out(self):
        cid, rd, _ = self.start("trap 'echo got-term' TERM; while :; do sleep 0.2; done", "--grace-min", "0.05")
        self.cli("--agent", "b", "yield", "--claim", cid, "--class", "P0")
        self.assertTrue(wait_for(lambda: os.path.exists(os.path.join(rd, "done.json")), 30))
        done = load(os.path.join(rd, "done.json"))
        self.assertEqual((done["reason"], done["signal"]), ("yielded", "SIGKILL"))
        self.assertNotIn("got-term", text(os.path.join(rd, "job.log")))
        self.assertIn("no signal (the job stops itself)", text(os.path.join(rd, "wrapper.log")))

    def test_a_stop_after_the_yield_sends_term_at_once(self):
        cid, rd, _ = self.start("while :; do sleep 0.2; done")
        self.cli("--agent", "b", "yield", "--claim", cid, "--class", "P0")
        path = os.path.join(self.root, "claims", cid + ".json")
        self.assertTrue(wait_for(lambda: (load(path).get("yield") or {}).get("acked"), 15))
        # the default grace is 10 minutes: only the stop's TERM ends the job this soon
        self.cli("--agent", "a", "release", "--job", "quiet", "--stop")
        self.assertTrue(wait_for(lambda: os.path.exists(os.path.join(rd, "done.json")), 20))
        done = load(os.path.join(rd, "done.json"))
        self.assertEqual((done["reason"], done["signal"]), ("stopped", "SIGTERM"))


class TestGpuLastEnd(Base):
    def gpus(self):
        st = self.cli("status")["hosts"]["local"]
        return st, dict((g["index"], g) for g in st["free"]["gpus"])

    def test_each_gpu_gives_its_last_claim_end(self):
        self.claim("a", "short", "--gpu", "1")
        _, g = self.gpus()
        self.assertEqual((g[0]["last_claim_end"], g[1]["last_claim_end"]), (None, None))
        self.cli("--agent", "a", "release", "--job", "short")
        st, g = self.gpus()
        self.assertIsNone(g[0]["last_claim_end"])
        self.assertAlmostEqual(hc.parse_iso(g[1]["last_claim_end"]), time.time(), delta=60)
        self.assertEqual((g[1]["last_claim"]["agent"], g[1]["last_claim"]["job"], g[1]["last_claim"]["reason"]),
                         ("a", "short", "released"))
        self.assertTrue(any("GPU 1 Tesla T4: 1.00 free" in ln and "last claim ended" in ln
                            for ln in hc.status_lines({"name": "local"}, st)))
        # a run over an earlier bare claim re-fits it, which is no end; the run's own end is
        self.claim("a", "two", "--gpu", "0")
        self.cli("--agent", "a", "run", "--job", "two", "--gpu", "0", "--cores", "1", "--ram", "1G", "--", "true")
        self.assertTrue(wait_for(lambda: not self.all_claims(), 20))
        _, g = self.gpus()
        self.assertEqual(g[0]["last_claim"]["reason"], "finished")
        self.assertEqual(json.loads(json.dumps(g[0]["last_claim"]))["job"], "two")


class TestOlderClaims(Base):
    def test_claims_written_before_this_version_keep_working(self):
        # a claim file as the previous version wrote it: no pids, no yield order, no sightings
        p = self.sleeper()
        c = self.claim("a", "old", "--pid", str(p.pid))["claim"]
        doc = load(os.path.join(self.root, "claims", c["id"] + ".json"))
        for k in ("pids", "yield_with", "seen", "renewed"):
            doc.pop(k, None)
        hc.write_json(os.path.join(self.root, "claims", c["id"] + ".json"), doc, 0o600)
        self.assertEqual(self.cli("status")["hosts"]["local"]["claims"][0]["state"], "live")
        self.patch_sim(clock_offset_s=25 * 60)
        r = self.cli("--agent", "b", "yield", "--claim", c["id"], "--class", "P0", code=hc.DENIED)
        self.assertIn("not preemptible", r["error"])
        self.cli("--agent", "a", "renew", "--job", "old", "--preemptible")
        self.assertEqual(len(self.cli("--agent", "b", "yield", "--claim", c["id"], "--class", "P0")["group"]), 1)
        # what an older version reads (pid) is still the job
        cur = load(os.path.join(self.root, "claims", c["id"] + ".json"))
        self.assertEqual((cur["pid"], cur["pid_start"]), (p.pid, hc.proc_start(p.pid)))


class TestDocs(unittest.TestCase):
    def test_docs_name_every_new_command_and_option(self):
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        skill = " ".join(text(os.path.join(root, "shared", "resource-sharing.skill.md")).split())
        rule = " ".join(text(os.path.join(root, "shared", "resource-sharing.rule.md")).split())
        for want in ("`renew --host H --job J", "--until T", "--no-borrowed", "`--all`", "--yield-with JOB",
                     "`--yield-signal none` is for a job that must not be signalled",
                     "The `KILL` at the end of the grace still comes", "PSS (proportional set size",
                     "summed RSS where PSS cannot be read", "`last_claim_end`", "attaches that process",
                     "Cores a running process is pinned to", "**Paired claims.**", "`--power max`",
                     "\"health\": {\"power\": \"max\"}", "parallel flows", "accept a subset of",
                     "repeat the same `claim` with", "taskset -acp",
                     "A new claim takes first the cores its own job already runs pinned to",
                     "Dropping `--preemptible` or `--borrowed` passes the same admission as a claim",
                     "On any other host the check and the fix keep the default"):
            self.assertIn(want, skill)
        for want in ("renew --job J", "`--yield-signal none` gets no `TERM`", "`KILL` still comes", "`--yield-with`",
                     "`--power max` is the owner's call", "--pid"):
            self.assertIn(want, rule)
        self.assertIn("renew", hc.__doc__)
        hh_doc = text(os.path.join(root, "shared", "tools", "hosthealth.py"))
        self.assertIn("--power default|max", hh_doc)


if __name__ == "__main__":
    unittest.main()
