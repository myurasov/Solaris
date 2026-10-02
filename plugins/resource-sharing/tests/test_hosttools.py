"""Tests for shared/tools/hosthealth.py and shared/tools/hostdash.py (stdlib unittest, offline).

Parsing and decisions run on canned host output. The end-to-end cases run the real shell scripts on this machine,
through a fake ssh program or as the local target, with stand-in nvidia-smi, dmesg and systemctl programs first on
PATH and a fake CPU folder: no sudo, and no real GPU or CPU setting is read for a decision or changed.

    python3 -m unittest discover -s plugins/resource-sharing/tests -v
"""

import ast
import functools
import importlib.util
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import types
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
TOOLS = os.path.join(os.path.dirname(HERE), "shared", "tools")


def load(name):
    spec = importlib.util.spec_from_file_location(name, os.path.join(TOOLS, name + ".py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


hh = load("hosthealth")
hd = load("hostdash")
hc = hh.hc
# the script builder as shipped: the Machine cases patch hh.host_script to run without sudo
HOST_SCRIPT = hh.host_script

HEALTHY = """KERNEL|6.8.0-45-generic
CPUS|72
LOAD|3.25
MEM|734003200
DISK|1258291200
GOV|performance:72
XID|43:4
INIT|0
G|0, NVIDIA H200 NVL, Enabled, [N/A], 600.00, 600.00, 1980, 1980, 0x0000000000000000, 45, 98, 72000, 143771, 0, Default
G|1, NVIDIA H200 NVL, Enabled, [N/A], 600.00, 600.00, 1980, 1980, 0x0000000000000004, 50, 100, 1024, 143771, 0, Default
"""

SICK = """KERNEL|6.8.0-45-generic
CPUS|72
LOAD|80.0
MEM|1048576
DISK|10485760
GOV|ondemand:70 performance:2
XID|13:1 79:2 48:1
INIT|3
G|0, NVIDIA H200 NVL, Disabled, Enabled, 500.00, 600.00, 1400, 1980, 0x0000000000000048, 91, 100, 140000, 143771, 2, Exclusive_Process
G|Unable to determine the device handle for GPU0000:3B:00.0: Unknown Error
"""

UNIFIED = """KERNEL|6.11.0-1016-nvidia
CPUS|20
LOAD|0.10
MEM|100000000
DISK|500000000
GOV|
XID|
INIT|0
G|0, NVIDIA GB10, Enabled, [N/A], [N/A], [N/A], 2418, 3003, 0x0000000000000000, 40, 0, [N/A], [N/A], [N/A], Default
"""

FAKE_SSH = ('#!/bin/sh\nwhile [ $# -gt 0 ]; do case "$1" in -o|-i|-p|-l|-F|-J) shift 2;; -*) shift;; *) break;; '
            'esac; done\nshift\nexec /bin/sh -c "$*"\n')

# a stand-in nvidia-smi: GPUs from a JSON state file (keys are query field names), every call logged
FAKE_SMI = r'''#!@PY@
import json, sys
state_path, log_path = @STATE@, @LOG@
with open(state_path) as f:
    st = json.load(f)
args = sys.argv[1:]
with open(log_path, "a") as f:
    f.write(" ".join(args) + "\n")
if st.get("exit"):
    # a driver that is down, or exit 124 as timeout(1) reports a hung call
    print(st.get("say", ""))
    sys.exit(st["exit"])
idx = args[args.index("-i") + 1] if "-i" in args else None
gpus = [g for g in st["gpus"] if idx is None or str(g["index"]) == idx]
if "-pm" in args or "-pl" in args:
    for g in gpus:
        if "-pm" in args:
            g["persistence_mode"] = "Enabled"
        else:
            g["power.limit"] = "%.2f" % float(args[args.index("-pl") + 1])
    with open(state_path, "w") as f:
        json.dump(st, f)
    sys.exit(0)
q = [a.split("=", 1)[1] for a in args if a.startswith("--query-gpu=")]
if q:
    fields = q[0].split(",")
    if st.get("old_driver") and "clocks_event_reasons.active" in fields:
        print('Field "clocks_event_reasons.active" is not a valid field to query.')
        sys.exit(2)
    for g in gpus:
        print(", ".join(str(g.get(k.replace("clocks_throttle_reasons", "clocks_event_reasons"), "[N/A]"))
                        for k in fields))
'''

FAKE_DMESG = ("#!/bin/sh\necho '[ 10.0] NVRM: Xid (PCI:0000:3b:00): 79, pid=0, GPU has fallen off the bus.'\n"
              "echo '[ 11.0] NVRM: Xid (PCI:0000:3c:00): 13, pid=123, Graphics Exception'\n"
              "echo '[ 12.0] NVRM: RmInitAdapter failed! (0x22:0x38:776)'\n")

FAKE_SYSTEMCTL = ('#!/bin/sh\necho "$*" >> @LOG@\ncase "$1" in cat) exit 0;; is-active) exit 3;; enable) exit 0;; esac\n'
                  'exit 1\n')

# a kernel log only root reads (dmesg_restrict), and a passwordless sudo that logs each call
FAKE_DMESG_ROOT = ('#!/bin/sh\n[ -n "$HC_SUDO" ] || { echo "dmesg: read kernel buffer failed: Operation not '
                   'permitted" >&2; exit 1; }\n' + FAKE_DMESG.split("\n", 1)[1])
FAKE_SUDO = '#!/bin/sh\necho "sudo $*" >> @LOG@\n[ "$1" = -n ] && shift\nHC_SUDO=1\nexport HC_SUDO\nexec "$@"\n'

# a stand-in procps ps: this ps itself (as busy as a fresh process looks), then processes of this login and of two
# others whose command lines hold secrets; every call logged
FAKE_PS = r'''#!@PY@
import os, sys
args = sys.argv[1:]
with open(@LOG@, "a") as f:
    f.write("ps " + " ".join(args) + "\n")
me = os.getuid()
# pid, ppid, uid, %cpu, elapsed, user, program, command line
rows = [(os.getpid(), os.getppid(), me, 99.9, "00:00", "me", "ps", "ps -eo"),
        (4001, 1, me + 1, 95.0, "1-02:00:00", "alice", "python3", "python3 serve.py --token SECRET-A"),
        (4002, 1, me, 90.0, "01:00:00", "me", "python", "python train.py --lr 3e-4"),
        (4003, 1, me + 2, 80.0, "05:00", "bob", "Web Content", "sh -c KEY=SECRET-B run"),
        (4004, 1, me, 10.0, "00:10", "me", "idle", "idle --quiet")]
if "-p" in args:
    hit = [r for r in rows if str(r[0]) == args[args.index("-p") + 1]]
    for r in hit:
        print(r[7] if "args=" in args else r[6])
    sys.exit(0 if hit else 1)
for r in sorted(rows, key=lambda r: -r[3]):
    print("%7d %7d %5d %5.1f %11s %-8s %s" % r[:7])
'''


def gpu(index, persistence="Disabled", limit="500.00", maximum="600.00", name="NVIDIA H200 NVL", default="600.00"):
    return {"index": index, "name": name, "persistence_mode": persistence, "mig.mode.current": "[N/A]",
            "power.limit": limit, "power.default_limit": default, "power.max_limit": maximum,
            "clocks.sm": "1980", "clocks.max.sm": "1980",
            "clocks_event_reasons.active": "0x0000000000000000", "temperature.gpu": "45", "utilization.gpu": "7",
            "memory.used": "2048", "memory.total": "143771", "ecc.errors.uncorrected.volatile.total": "0",
            "compute_mode": "Default"}


def write(path, text, mode=0o644):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write(text)
    os.chmod(path, mode)


def read(path):
    with open(path) as f:
        return f.read()


class Machine(unittest.TestCase):
    """A temp folder with stand-in programs first on PATH, a fake CPU folder and a fake ssh."""

    def setUp(self):
        self.tmp = os.path.realpath(tempfile.mkdtemp(prefix="hc-tools-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.addCleanup(os.chdir, os.getcwd())
        os.chdir(self.tmp)
        env = dict((k, v) for k, v in os.environ.items() if not k.startswith("HOSTCLAIMS_"))
        self.bin = os.path.join(self.tmp, "bin")
        env["PATH"] = self.bin + os.pathsep + env.get("PATH", "")
        patcher = mock.patch.dict(os.environ, env, clear=True)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.state = os.path.join(self.tmp, "gpus.json")
        self.log = os.path.join(self.tmp, "calls.log")
        smi = FAKE_SMI.replace("@PY@", sys.executable).replace("@STATE@", repr(self.state)).replace("@LOG@",
                                                                                                    repr(self.log))
        write(os.path.join(self.bin, "nvidia-smi"), smi, 0o755)
        write(os.path.join(self.bin, "dmesg"), FAKE_DMESG, 0o755)
        write(os.path.join(self.bin, "systemctl"), FAKE_SYSTEMCTL.replace("@LOG@", repr(self.log)), 0o755)
        self.ssh = os.path.join(self.tmp, "fake-ssh")
        write(self.ssh, FAKE_SSH, 0o755)
        self.cpu = os.path.join(self.tmp, "cpu")
        self.set_gpus([gpu(0), gpu(1, limit="600.00"), gpu(2, persistence="Enabled", limit="450.00")])
        self.set_cpus({0: "0", 1: "1", 2: "2", 3: "3"})
        # the health scripts run here without sudo and against the fake CPU folder
        p = mock.patch.object(hh, "host_script", functools.partial(hh.host_script, sudo=False, cpu=self.cpu))
        p.start()
        self.addCleanup(p.stop)

    def set_gpus(self, gpus, **extra):
        with open(self.state, "w") as f:
            json.dump(dict(extra, gpus=gpus), f)
        if os.path.exists(self.log):
            os.unlink(self.log)

    def gpus(self):
        with open(self.state) as f:
            return dict((g["index"], g) for g in json.load(f)["gpus"])

    def set_cpus(self, related):
        shutil.rmtree(self.cpu, True)
        for c, rel in related.items():
            d = os.path.join(self.cpu, "cpu%d" % c, "cpufreq")
            write(os.path.join(d, "scaling_governor"), "ondemand\n")
            write(os.path.join(d, "scaling_available_governors"), "ondemand performance schedutil\n")
            write(os.path.join(d, "related_cpus"), rel + "\n")

    def governors(self):
        return dict((int(n[3:]), read(os.path.join(self.cpu, n, "cpufreq", "scaling_governor")).strip())
                    for n in os.listdir(self.cpu))

    def calls(self):
        return read(self.log).splitlines() if os.path.exists(self.log) else []

    def run_tool(self, tool, *args, **kw):
        buf = io.StringIO()
        with redirect_stdout(buf), redirect_stderr(io.StringIO()):
            rc = tool.main(list(args))
        out = buf.getvalue()
        if kw.get("code") is not None:
            self.assertEqual(rc, kw["code"], out)
        return rc, out

    def inventory(self, hosts):
        path = os.path.join(self.tmp, "hosts.json")
        with open(path, "w") as f:
            json.dump(hosts, f)
        return path

    def claims_root(self):
        # a claims folder on this machine whose readings hostclaims simulates: 4 cores and 3 GPUs
        root = os.path.join(self.tmp, "claims")
        write(os.path.join(root, "simulate.json"), json.dumps({
            "boot_id": "b1", "cpus": 4, "ram_total_gb": 64.0, "disk_total_gb": 100.0, "disk_free_gb": 100.0,
            "gpus": [{"index": i, "name": "NVIDIA H200 NVL", "mem_total_gb": 140.0} for i in range(3)]}))
        os.environ["HOSTCLAIMS_SIMULATE"] = "1"
        return root

    def install(self, base, owner="own"):
        self.run_tool(hc, *base + ["--agent", owner, "--as-owner", "install", "--launcher", "setsid", "--share-with",
                                   "*", "--reserve-cores", "0", "--reserve-ram", "0"], code=0)


class TestHealthParsing(unittest.TestCase):
    def test_want_gpus(self):
        self.assertEqual([hh.want_gpus(v) for v in ("2x H200 NVL", "1x GB300 256 GB (aarch64)", "8 X B200", "H200",
                                                    "16GB", ["a", "b"], 4, None, True)],
                         [2, 1, 8, None, None, 2, 4, None, None])

    def test_healthy_host(self):
        rec = hh.assess(HEALTHY, 2)
        self.assertEqual((rec["problems"], rec["notes"]), ([], []))
        self.assertEqual((rec["cpus"], rec["load"], round(rec["mem_free_gb"]), round(rec["disk_free_gb"])),
                         (72, 3.25, 700, 1200))
        self.assertEqual([g["index"] for g in rec["gpus"]], [0, 1])
        # application-class Xid 43 and the power cap at the limit are not problems
        self.assertEqual(rec["xids"], {43: 4})
        self.assertIn("0 98% 70/140G 45C 1980/1980MHz 600/600W", hh.gpu_text(rec["gpus"][0]))

    def test_every_problem_is_named(self):
        probs = hh.assess(SICK, 2)["problems"]
        for want in ("nvidia-smi: Unable to determine the device handle", "1 of 2 GPUs visible",
                     "GPU start failed 3 time(s) since boot (RmInitAdapter)", "Xid 48 x1, 79 x2 since boot",
                     "GPU 0 persistence mode off", "GPU 0 MIG mode Enabled", "GPU 0 compute mode Exclusive_Process",
                     "GPU 0 power limit 500 W below its 600 W default",
                     "GPU 0 held back: hardware slowdown, hardware thermal slowdown", "GPU 0 uncorrected ECC errors: 2",
                     "CPU governor ondemand on 70 cores"):
            self.assertTrue(any(p.startswith(want) for p in probs), (want, probs))
        self.assertFalse(any("13" in p for p in probs if p.startswith("Xid")))

    def test_power_limit_is_held_against_the_default(self):
        # only a limit below the default is a problem, not one raised above it towards the card's maximum
        def power(limit, default):
            g = hh.parse_gpu("0, NVIDIA L40S, Enabled, [N/A], %s, %s, 2520, 2520, 0x0, 40, 0, 1, 46068, 0, Default"
                             % (limit, default))
            return [p for p in hh.gpu_problems(g) if "power" in p]
        self.assertEqual(power("200.00", "250.00"), ["GPU 0 power limit 200 W below its 250 W default"])
        self.assertEqual(power("250.00", "250.00"), [])
        self.assertEqual(power("300.00", "250.00"), [])
        self.assertNotIn("power.max_limit", hh.FIELDS)

    def test_unified_memory_and_missing_readings_are_not_problems(self):
        rec = hh.assess(UNIFIED, 1)
        self.assertEqual(rec["problems"], [])
        self.assertIn("shared mem", hh.gpu_text(rec["gpus"][0]))
        # a name with commas keeps them
        g = hh.parse_gpu("3, Odd, Name, Enabled, [N/A], 1.00, 1.00, 1, 1, 0x0, 1, 1, 1, 1, 0, Default")
        self.assertEqual((g["index"], g["name"]), (3, "Odd,Name"))
        self.assertIsNone(hh.parse_gpu("No devices were found"))

    def test_driver_missing_and_unreadable_kernel_log(self):
        out = "KERNEL|6.8.0\nXID|?\nNOSMI|\n"
        self.assertEqual(hh.assess(out, 2)["problems"], ["no nvidia-smi: the NVIDIA driver is missing"])
        rec = hh.assess(out, None)
        self.assertEqual(rec["problems"], [])
        self.assertTrue(rec["notes"][0].startswith("kernel log not readable"))
        notes = hh.assess("KERNEL|x\n" + "".join(HEALTHY.splitlines(True)[-2:]) * 2, 2)["notes"]
        self.assertTrue(any(n.startswith("4 GPUs visible where the inventory names 2") for n in notes), notes)

    def test_unreachable_and_exit_codes(self):
        recs = [{"code": hh.OK, "problems": []}]
        self.assertEqual(hh.exit_code(recs), hh.OK)
        self.assertEqual(hh.exit_code(recs + [{"code": hh.OK, "problems": ["x"]}]), hh.PROBLEMS)
        self.assertEqual(hh.exit_code(recs + [{"code": hh.UNREACHABLE, "problems": ["x"]}]), hh.UNREACHABLE)


class TestFixScope(unittest.TestCase):
    def claim(self, agent, state, gpus, cores):
        return {"agent": agent, "state": state, "resources": {"gpus": [{"index": i, "share": 0.5} for i in gpus],
                                                               "cores": cores}}

    def test_owner_guest_and_stranger(self):
        h = {"name": "box", "owner": "a", "owner_explicit": True}
        self.assertEqual(hh.fix_scope("a", h, {"ok": True, "installed": True, "owner": "a"})["scope"], "host")
        # not installed: the owner the inventory names decides
        self.assertEqual(hh.fix_scope("a", h, {"ok": True, "installed": False})["scope"], "host")
        self.assertIsNone(hh.fix_scope("b", h, {"ok": True, "installed": False})["scope"])
        # the host's own record wins: handed over to b, a is a guest there
        claims = [self.claim("a", "live", [1], [4, 5]), self.claim("a", "orphan", [3], [7]),
                  self.claim("a", "stale", [0], [0]), self.claim("c", "live", [2], [6])]
        s = hh.fix_scope("a", h, {"ok": True, "installed": True, "owner": "b", "claims": claims})
        self.assertEqual((s["scope"], s["gpus"], s["cores"]), ("claims", [1, 3], [4, 5, 7]))
        s = hh.fix_scope("d", h, {"ok": True, "installed": True, "owner": "b", "claims": claims})
        self.assertIsNone(s["scope"])
        self.assertIn("maintenance request", s["why"])

    def test_a_listed_host_with_no_owner_named_is_nobodys(self):
        # what hostclaims gives a --hosts or hosts.json entry without owner: the caller's slug, or none
        for h in ({"name": "box", "owner": "a", "owner_explicit": False}, {"name": "box", "owner": None}):
            s = hh.fix_scope("a", h, {"ok": True, "installed": False})
            self.assertIsNone(s["scope"])
            self.assertTrue(s["why"].startswith("no owner on record"), s["why"])
            # the host's own record still makes it a's
            self.assertEqual(hh.fix_scope("a", h, {"ok": True, "installed": True, "owner": "a"})["scope"], "host")

    def test_status_names_only_an_owner_the_inventory_names(self):
        seen = []
        ctx = types.SimpleNamespace(call=lambda host, req: seen.append(host.get("owner")) or {"ok": True,
                                                                                            "installed": False})
        with mock.patch.object(hh, "run_script", return_value=(HEALTHY, None, hh.OK)) as run:
            rec = hh.check_host(ctx, {"name": "box", "target": "u@box", "owner": "a", "owner_explicit": False}, "a",
                                True, 10)
            self.assertIsNone(rec["fix"]["scope"])
            self.assertNotIn("GPUS=", run.call_args[0][2])
            hh.check_host(ctx, {"name": "box", "target": "u@box", "owner": "a", "owner_explicit": True}, "a", True, 10)
            self.assertIn("GPUS=all", run.call_args[0][2])
        self.assertEqual(seen, [None, "a"])

    def test_guest_leaves_a_gpu_in_other_use_alone(self):
        h = {"name": "box", "owner": "b", "owner_explicit": True}
        # a holds a quarter of GPU 1 and all of GPU 2; c holds a quarter of GPU 1 too; d's stale claim holds nothing
        claims = [self.claim("a", "live", [1, 2], [4]), self.claim("c", "orphan", [1], [5]),
                  self.claim("d", "stale", [2], [6])]
        claims[0]["resources"]["gpus"][0]["share"] = claims[1]["resources"]["gpus"][0]["share"] = 0.25
        st = {"ok": True, "installed": True, "owner": "b", "claims": claims}
        s = hh.fix_scope("a", h, st)
        self.assertEqual((s["gpus"], s["shared_gpus"], s["cores"]), ([2], [1], [4]))
        # work outside the claims on GPU 2 leaves it alone too
        st["free"] = {"gpus": [{"index": 1, "unclaimed": False}, {"index": 2, "unclaimed": True}]}
        s = hh.fix_scope("a", h, st)
        self.assertEqual((s["gpus"], s["shared_gpus"]), ([], [1, 2]))
        self.assertIn("  fix: only your claims here (GPUs none; cores 4); GPUs 1,2 also in other use: left to the "
                      "owner", hh.host_lines(h, {"owner": "b", "problems": [], "notes": [], "error": "x", "fix": s}))
        # the owner's scope is the whole host whoever shares its GPUs
        self.assertEqual(hh.fix_scope("b", h, st)["scope"], "host")

    def test_script_carries_only_the_scope(self):
        whole = hh.host_script({"scope": "host"})
        self.assertIn("GPUS=all\nCORES=all\nDAEMON=1\n", whole)
        part = hh.host_script({"scope": "claims", "gpus": [1, 3], "cores": [4, 5]})
        self.assertIn("GPUS='1 3'\nCORES='4 5'\nDAEMON=0\n", part)
        for s in (hh.host_script(None), hh.host_script({"scope": None})):
            self.assertNotIn("GPUS=", s)
            self.assertNotIn("-pm 1", s)
        # sudo only in the owner's scope: never for a guest's claims, nor a run without --fix
        self.assertIn('SUDO="sudo -n"', whole)
        for s in (part, hh.host_script(None), hh.host_script({"scope": None}),
                  hh.host_script({"scope": "host"}, sudo=False)):
            self.assertNotIn('SUDO="sudo -n"', s)


class TestFixScript(Machine):
    def run_script(self, scope, full=False):
        r = subprocess.run(["sh", "-s"], input=hh.host_script(scope).encode(), stdout=subprocess.PIPE,
                           stderr=subprocess.PIPE, timeout=60)
        out = r.stdout.decode()
        return out if full else [ln[2:] for ln in out.splitlines() if ln.startswith("F|")]

    def test_no_gpu_is_touched_when_the_driver_is_down_or_hung(self):
        for code, say, want in ((9, "NVIDIA-SMI has failed because it couldn't communicate with the NVIDIA driver.",
                                 "nvidia-smi: NVIDIA-SMI has failed"),
                                (124, "", "nvidia-smi: nvidia-smi did not answer within 60 s")):
            self.set_gpus([gpu(0)], exit=code, say=say)
            self.set_cpus({0: "0", 1: "1"})
            out = self.run_script({"scope": "host"}, full=True)
            self.assertEqual([ln[2:] for ln in out.splitlines() if ln.startswith("F|")],
                             ["CPU governor performance on 2 cores"])
            self.assertFalse([c for c in self.calls() if " -pm " in " %s " % c or " -pl " in " %s " % c])
            # the probe asks once: no fallback query after a hang
            self.assertEqual(len([c for c in self.calls() if "compute_mode" in c]), 1)
            probs = hh.assess(out, 1)["problems"]
            self.assertTrue(probs[0].startswith(want), probs)
            self.assertEqual(probs[1], "0 of 1 GPUs visible")

    def test_power_limit_goes_back_to_the_default_never_the_maximum(self):
        # cards whose 450 W default is below their 600 W maximum: limits under, at and raised above the default
        self.set_gpus([gpu(i, persistence="Enabled", limit=lim, default="450.00")
                       for i, lim in enumerate(("400.00", "450.00", "500.00"))])
        out = self.run_script({"scope": "host"}, full=True)
        self.assertEqual([ln[2:] for ln in out.splitlines() if ln.startswith("F|GPU")],
                         ["GPU 0 power limit 400.00 -> 450.00 W"])
        self.assertEqual([c for c in self.calls() if " -pl " in " %s " % c], ["-i 0 -pl 450.00"])
        self.assertEqual([g["power.limit"] for _, g in sorted(self.gpus().items())], ["450.00", "450.00", "500.00"])
        # the probe runs after the fixes: no power problem is left
        self.assertFalse([p for p in hh.assess(out, 3)["problems"] if "power" in p])

    def test_sudo_only_in_the_owners_scope(self):
        if os.getuid() == 0:
            self.skipTest("root needs no sudo")
        write(os.path.join(self.bin, "dmesg"), FAKE_DMESG_ROOT, 0o755)
        write(os.path.join(self.bin, "sudo"), FAKE_SUDO.replace("@LOG@", repr(self.log)), 0o755)
        guest = {"scope": "claims", "gpus": [1], "cores": [0]}
        for scope in (None, guest, {"scope": "host"}):
            self.set_gpus([gpu(0), gpu(1)])
            r = subprocess.run(["sh", "-s"], input=HOST_SCRIPT(scope, cpu=self.cpu).encode(), stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE, timeout=60)
            rec = hh.assess(r.stdout.decode(), 2)
            sudo = [c for c in self.calls() if c.startswith("sudo ")]
            if scope and scope["scope"] == "host":
                for want in ("sudo -n dmesg", "sudo -n nvidia-smi -i 0 -pm 1", "sudo -n nvidia-smi -i 0 -pl 600.00"):
                    self.assertIn(want, sudo)
                self.assertEqual(rec["xids"], {13: 1, 79: 1})
            else:
                # a run without --fix and a guest read and change only what the login may
                self.assertEqual(sudo, [], scope)
                self.assertIsNone(rec["xids"])
                self.assertTrue(any(n.startswith("kernel log not readable") for n in rec["notes"]), rec["notes"])
        self.assertEqual(rec["gpu_start_failures"], 1)

    def test_claims_touch_only_their_gpus_and_cores(self):
        # core 2 shares its frequency policy with core 3, which is not claimed
        self.set_cpus({0: "0", 1: "1", 2: "2 3", 3: "2 3"})
        fixed = self.run_script({"scope": "claims", "gpus": [1, 2], "cores": [0, 2]})
        self.assertEqual(fixed, ["GPU 1 persistence mode on", "GPU 2 power limit 450.00 -> 600.00 W",
                                 "CPU governor performance on 1 cores", "CPU governor: 1 claimed cores share a "
                                 "frequency policy with other cores: left to the owner"])
        g = self.gpus()
        self.assertEqual((g[0]["persistence_mode"], g[0]["power.limit"]), ("Disabled", "500.00"))
        self.assertEqual((g[1]["persistence_mode"], g[2]["power.limit"]), ("Enabled", "600.00"))
        self.assertFalse(any(" -i 0 " in " %s " % c for c in self.calls()), self.calls())
        self.assertFalse(any(c.startswith("enable") for c in self.calls()))
        self.assertEqual(self.governors(), {0: "performance", 1: "ondemand", 2: "ondemand", 3: "ondemand"})

    def test_owner_touches_everything_and_a_second_run_changes_nothing(self):
        fixed = self.run_script({"scope": "host"})
        self.assertEqual(fixed, ["GPU 0 persistence mode on", "GPU 0 power limit 500.00 -> 600.00 W",
                                 "GPU 1 persistence mode on", "GPU 2 power limit 450.00 -> 600.00 W",
                                 "nvidia-persistenced enabled", "CPU governor performance on 4 cores"])
        self.assertEqual(set(self.governors().values()), {"performance"})
        self.assertEqual(self.run_script({"scope": "host"}), ["nvidia-persistenced enabled"])
        self.assertEqual(self.run_script(None), [])


class TestHealthEndToEnd(Machine):
    def test_read_only_sweep_through_ssh(self):
        inv = self.inventory([{"name": "gpu-box", "target": "user@gpu-box", "opts": ["-i", "~/.ssh/key"],
                               "gpus": "4x H200 NVL"}])
        rc, out = self.run_tool(hh, "--hosts", inv, "--ssh", self.ssh, "--json", code=hh.PROBLEMS)
        rec = json.loads(out)["hosts"]["gpu-box"]
        self.assertEqual([g["index"] for g in rec["gpus"]], [0, 1, 2])
        for want in ("3 of 4 GPUs visible", "GPU start failed 1 time(s)", "Xid 79 x1 since boot",
                     "GPU 0 persistence mode off", "GPU 0 power limit 500 W", "CPU governor ondemand on 4 cores"):
            self.assertTrue(any(p.startswith(want) for p in rec["problems"]), (want, rec["problems"]))
        self.assertIsNone(rec["fix"])
        # read-only: nothing was set
        self.assertFalse([c for c in self.calls() if " -pm " in " %s " % c or " -pl " in " %s " % c])
        self.assertEqual(set(self.governors().values()), {"ondemand"})
        # an older driver names the clock-event reasons differently
        self.set_gpus([gpu(0, persistence="Enabled", limit="600.00")], old_driver=True)
        rc, out = self.run_tool(hh, "--hosts", inv, "--ssh", self.ssh, "--host", "gpu-box")
        self.assertIn("GPUs (1x NVIDIA H200 NVL): 0 7% 2/140G 45C", out)
        self.assertIn("! 1 of 4 GPUs visible", out)

    def test_unreachable_host(self):
        down = os.path.join(self.tmp, "down-ssh")
        write(down, "#!/bin/sh\necho 'ssh: connect to host x port 22: Connection timed out' >&2\nexit 255\n", 0o755)
        inv = self.inventory([{"name": "far", "target": "user@far"}])
        rc, out = self.run_tool(hh, "--hosts", inv, "--ssh", down, code=hh.UNREACHABLE)
        self.assertIn("! unreachable: ssh: connect to host x port 22: Connection timed out", out)
        self.run_tool(hh, "--hosts", inv, "--host", "nope", code=hh.USAGE)
        self.run_tool(hh, "--hosts", inv, "--timeout", "0", code=hh.USAGE)

    def test_fix_follows_ownership_and_claims(self):
        inv = self.inventory([{"name": "box", "target": "local", "root": self.claims_root(), "owner": "own",
                               "gpus": "3x H200"}])
        base = ["--hosts", inv]
        self.install(base)
        rc, out = self.run_tool(hc, *base + ["--agent", "guest", "--json", "claim", "--job", "j", "--cores", "2",
                                             "--ram", "1G", "--gpu", "1"], code=0)
        cores = json.loads(out)["claim"]["resources"]["cores"]
        free = [c for c in range(4) if c not in cores]
        # the guest's second core shares its frequency policy with a core it did not claim
        shared = "%d %d" % tuple(sorted((cores[1], free[0])))
        self.set_cpus({cores[0]: str(cores[0]), cores[1]: shared, free[0]: shared, free[1]: str(free[1])})

        # --fix acts as the calling project: outside a project only with --as-owner
        self.run_tool(hh, *base + ["--agent", "guest", "--fix"], code=hh.DENIED)
        rc, out = self.run_tool(hh, *base + ["--agent", "guest", "--as-owner", "--fix", "--json"])
        rec = json.loads(out)["hosts"]["box"]
        self.assertEqual((rec["fix"]["scope"], rec["fix"]["gpus"], rec["fix"]["cores"]), ("claims", [1], sorted(cores)))
        self.assertIn("GPU 1 persistence mode on", rec["fixed"])
        self.assertEqual([i for i, g in sorted(self.gpus().items()) if g["persistence_mode"] == "Enabled"], [1, 2])
        self.assertEqual(self.governors(), {cores[0]: "performance", cores[1]: "ondemand", free[0]: "ondemand",
                                            free[1]: "ondemand"})

        # a project with no claim there changes nothing
        self.set_gpus([gpu(0), gpu(1), gpu(2)])
        rc, out = self.run_tool(hh, *base + ["--agent", "other", "--as-owner", "--fix"])
        self.assertIn("fix: none here: owned by own, and other holds no live claim here", out)
        self.assertFalse([c for c in self.calls() if " -pm " in " %s " % c or " -pl " in " %s " % c])

        # the owner sets every GPU and core
        rc, out = self.run_tool(hh, *base + ["--agent", "own", "--as-owner", "--fix"])
        self.assertIn("fixed: GPU 0 persistence mode on; GPU 0 power limit 500.00 -> 600.00 W", out)
        self.assertEqual(set(g["persistence_mode"] for g in self.gpus().values()), {"Enabled"})
        self.assertEqual(set(self.governors().values()), {"performance"})

    def test_guest_leaves_a_gpu_another_claim_shares_alone(self):
        inv = self.inventory([{"name": "box", "target": "local", "root": self.claims_root(), "owner": "own"}])
        base = ["--hosts", inv]
        self.install(base)
        # the guest holds a quarter of GPU 1 and GPU 2 alone; another project holds a quarter of GPU 1 too
        self.run_tool(hc, *base + ["--agent", "guest", "claim", "--job", "j", "--cores", "1", "--ram", "1G", "--gpu",
                                   "1:0.25", "--gpu", "2"], code=0)
        self.run_tool(hc, *base + ["--agent", "other", "claim", "--job", "k", "--cores", "1", "--ram", "1G", "--gpu",
                                   "1:0.25"], code=0)
        rc, out = self.run_tool(hh, *base + ["--agent", "guest", "--as-owner", "--fix"])
        self.assertIn("(GPUs 2; cores ", out)
        self.assertIn("; GPUs 1 also in other use: left to the owner", out)
        self.assertIn("fixed: GPU 2 power limit 450.00 -> 600.00 W", out)
        self.assertFalse([c for c in self.calls() if c.startswith("-i 1 ")], self.calls())
        self.assertEqual(self.gpus()[1]["persistence_mode"], "Disabled")
        # once the other claim is released, GPU 1 is the guest's alone
        self.run_tool(hc, *base + ["--agent", "other", "release", "--job", "k"], code=0)
        rc, out = self.run_tool(hh, *base + ["--agent", "guest", "--as-owner", "--fix"])
        self.assertIn("fixed: GPU 1 persistence mode on", out)
        self.assertNotIn("in other use", out)

    def test_fix_needs_an_owner_on_record(self):
        # a project's --hosts file lists a host with no owner: hostclaims fills in the project, which is no record
        proj = os.path.join(self.tmp, "proj")
        write(os.path.join(proj, "ai", "manifest.json"), json.dumps({"framework_version": "0.40.0",
                                                                     "project": {"name": "proj"}}))
        inv = self.inventory([{"name": "box", "target": "local", "root": self.claims_root()}])
        base = ["--project", proj, "--hosts", inv]
        rc, out = self.run_tool(hh, *base + ["--fix"])
        self.assertIn("fix: none here: no owner on record", out)
        self.assertFalse([c for c in self.calls() if " -pm " in " %s " % c or " -pl " in " %s " % c])
        self.assertEqual(set(self.governors().values()), {"ondemand"})
        # installing writes the project into the host's own record: then the whole host is its to fix
        self.run_tool(hc, *base + ["install", "--launcher", "setsid"], code=0)
        rc, out = self.run_tool(hh, *base + ["--fix"])
        self.assertIn("fixed: GPU 0 persistence mode on", out)
        self.assertEqual(set(self.governors().values()), {"performance"})


class TestDashboard(Machine):
    def test_ssh_reuses_one_connection(self):
        ctx = types.SimpleNamespace(ssh=["/tmp/hss"])
        cmd = hd.ssh_cmd(ctx, {"name": "a", "target": "user@a", "opts": ["-o", "ControlPath=/mine"]})
        self.assertEqual(cmd[:3], ["/tmp/hss", "-o", "ControlPath=/mine"])
        for opt in ("ControlMaster=auto", "ControlPersist=120", "BatchMode=yes"):
            self.assertIn(opt, cmd)
        self.assertTrue(any(o.startswith("ControlPath=") and o.endswith("%C") for o in cmd[3:]))
        self.assertEqual(cmd[-2:], ["user@a", "sh -s"])
        self.assertEqual(hd.ssh_cmd(ctx, {"name": "here", "target": "local"}), ["sh", "-s"])

    def test_render(self):
        r = hd.parse("ncpu=8\nload=7.6\nmem=4194304/8388608\ndisk=10/100\ngpu=0, 97, 40960, 81920\n"
                     "gpu=1, 0, [N/A], [N/A]\ntmux=hc-proj--train\nproc= 99.0  01:00 user python  train.py\n"
                     'claim={"agent": "proj", "job": "train", "class": "P1", "gpus": [[0, 1.0], [1, 0.25]], '
                     '"cores": 4, "created": "2026-01-01T00:00:00Z", "alive": false}\n', "box")
        c = types.SimpleNamespace(lock=threading.Lock(), data=[r, {"name": "far", "error": "no answer within 10 s"}],
                                  updated="12:00:00")
        lines = hd.render_lines(c, 200)
        text = [t for t, _ in lines]
        row = [x for x in lines if x[0].startswith("box")][0]
        self.assertIn("7.6/8", row[0])
        self.assertIn("4/8G", row[0])
        self.assertIn("#0  97% 40/80G  #1   0% shared mem", row[0])
        self.assertEqual(row[1], 2)
        self.assertTrue(any(t.startswith("  claims: proj/train P1 GPU 0,1:0.25 4 cores") and t.endswith("(no process)")
                            for t in text), text)
        self.assertIn("  tmux: hc-proj--train", text)
        self.assertIn("  99.0 01:00 user python train.py", text)
        self.assertIn(("far               unreachable: no answer within 10 s", 2), lines)

    def test_once_through_ssh(self):
        root = os.path.join(self.tmp, "claims")
        dead = subprocess.Popen(["true"])
        dead.wait()
        for name, pid in (("a--run", os.getpid()), ("b--gone", dead.pid)):
            write(os.path.join(root, "claims", name + ".json"), json.dumps({
                "id": name, "agent": name.split("--")[0], "job": name.split("--")[1], "class": "P2", "pid": pid,
                "created": "2026-01-01T00:00:00Z", "cmd": ["secret", "--token", "x"],
                "resources": {"cores": [0, 1], "gpus": [{"index": 1, "share": 0.5}]}}))
        inv = self.inventory([{"name": "gpu-box", "target": "user@gpu-box", "root": root}])
        rc, out = self.run_tool(hd, "--once", "--hosts", inv, "--ssh", self.ssh, code=0)
        self.assertIn("gpu-box", out)
        self.assertIn("#0   7% 2/140G  #1   7% 2/140G  #2   7% 2/140G", out)
        self.assertIn("a/run P2 GPU 1:0.5 2 cores", out)
        self.assertIn("b/gone P2 GPU 1:0.5 2 cores", out)
        self.assertEqual(out.count("(no process)"), 1)
        self.assertNotIn("secret", out)
        down = os.path.join(self.tmp, "down-ssh")
        write(down, "#!/bin/sh\necho 'ssh: Could not resolve hostname gpu-box' >&2\nexit 255\n", 0o755)
        rc, out = self.run_tool(hd, "--once", "--hosts", inv, "--ssh", down, code=hd.UNREACHABLE)
        self.assertIn("unreachable: ssh: Could not resolve hostname gpu-box", out)

    def test_other_logins_processes_show_only_their_program(self):
        write(os.path.join(self.bin, "ps"), FAKE_PS.replace("@PY@", sys.executable).replace("@LOG@", repr(self.log)),
              0o755)
        inv = self.inventory([{"name": "gpu-box", "target": "user@gpu-box", "root": os.path.join(self.tmp, "none")}])
        rc, out = self.run_tool(hd, "--once", "--hosts", inv, "--ssh", self.ssh, code=0)
        lines = out.splitlines()
        # the three busiest but the probe's own ps: this login's with its command line, the others' by program
        for want in ("  95.0 1-02:00:00 alice python3", "  90.0 01:00:00 me python train.py --lr 3e-4",
                     "  80.0 05:00 bob Web Content"):
            self.assertIn(want, lines)
        for gone in ("SECRET", "serve.py", "KEY=", "99.9", "idle"):
            self.assertNotIn(gone, out)
        # the command lines of other logins are never even read
        self.assertEqual([c for c in self.calls() if c.startswith("ps -o")], ["ps -o args= -p 4002"])

    def test_bad_input(self):
        inv = self.inventory([{"name": "a", "target": "user@a"}])
        for args in (["-n", "0"], ["--timeout", "-1"]):
            with self.assertRaises(SystemExit) as e, redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                hd.main(["--once", "--hosts", inv] + args)
            self.assertEqual(e.exception.code, 2)
        self.run_tool(hd, "--once", "--hosts", inv, "--host", "nope", code=hc.USAGE)


class TestPortability(unittest.TestCase):
    def test_python38_grammar(self):
        for name in ("hosthealth", "hostdash"):
            ast.parse(read(os.path.join(TOOLS, name + ".py")), feature_version=(3, 8))

    def test_docs_name_the_tools(self):
        root = os.path.dirname(HERE)
        skill = read(os.path.join(root, "shared", "resource-sharing.skill.md"))
        rule = read(os.path.join(root, "shared", "resource-sharing.rule.md"))
        readme = read(os.path.join(root, "README.md"))
        for doc in (skill, readme):
            self.assertIn("hosthealth.py", doc)
            self.assertIn("hostdash.py", doc)
        self.assertIn("hosthealth.py --fix", rule)


if __name__ == "__main__":
    unittest.main()
