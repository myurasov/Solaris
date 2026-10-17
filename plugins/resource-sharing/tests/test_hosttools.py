"""Tests for shared/tools/hosthealth.py and shared/tools/hostdash.py (stdlib unittest, offline).

Parsing and decisions run on canned host output. The end-to-end cases run the real shell scripts on this machine,
through a fake ssh program or as the local target, with stand-in nvidia-smi, journalctl, dmesg and systemctl
programs first on PATH and a fake CPU folder: no sudo, and no real GPU, CPU or kernel-log reading is used for a
decision or changed. The health file cases write into temporary project folders.

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
import time
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

# a kernel journal this login may not read: journalctl answers with a hint and no kernel lines, as for a login
# outside the adm group (the default here, so the cases above read the stand-in dmesg)
FAKE_JOURNAL_DENIED = ('#!/bin/sh\necho "Hint: You are currently not seeing messages from other users and the system." '
                       '>&2\n')
# a readable journal of this boot, its kernel lines in a JSON file as [[cursor number, line]], every call logged:
# --show-cursor ends with the last line's cursor (none when no line is shown), --after-cursor=cN shows the lines past
# N (a vacuumed cursor resumes at the next line left). ROOT_ONLY makes it readable through sudo only.
FAKE_JOURNAL = r'''#!@PY@
import json, os, sys
args = sys.argv[1:]
with open(@LOG@, "a") as f:
    f.write("journalctl " + " ".join(args) + "\n")
if @ROOT_ONLY@ and not os.environ.get("HC_SUDO"):
    sys.stderr.write("No journal files were opened due to insufficient permissions.\n")
    sys.exit(1)
if "-n" in args and args[args.index("-n") + 1] == "0":
    sys.exit(0)
with open(@LINES@) as f:
    lines = json.load(f)
after = [a.split("=", 1)[1] for a in args if a.startswith("--after-cursor=")]
if after:
    if not after[0].startswith("c") or not after[0][1:].isdigit():
        sys.stderr.write("Failed to seek to cursor: Invalid argument\n")
        sys.exit(1)
    lines = [x for x in lines if x[0] > int(after[0][1:])]
for _, text in lines:
    print(text)
if "--show-cursor" in args and lines:
    print("-- cursor: c%d" % lines[-1][0])
'''
X79 = "NVRM: Xid (PCI:0000:3b:00): 79, pid=0, GPU has fallen off the bus."
JOURNAL_LINES = [X79, X79, X79, "NVRM: Xid (PCI:0000:3c:00): 13, pid=123, Graphics Exception",
                 "NVRM: Xid (PCI:0000:3d:00): 149, NETIR_LINK_DOWN Nonfatal", "NVRM: RmInitAdapter failed! (0x22:0x38:776)",
                 "eth0: link up"]


def boot_id():
    # what the probe reads here for the boot (nothing off Linux)
    try:
        with open("/proc/sys/kernel/random/boot_id") as f:
            return f.read().strip()
    except OSError:
        return ""

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
        write(os.path.join(self.bin, "journalctl"), FAKE_JOURNAL_DENIED, 0o755)
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

    def journal(self, lines=None, root_only=False):
        self.jlines = os.path.join(self.tmp, "journal.json")
        self.set_journal(JOURNAL_LINES if lines is None else lines)
        write(os.path.join(self.bin, "journalctl"), FAKE_JOURNAL.replace("@PY@", sys.executable).replace(
            "@LOG@", repr(self.log)).replace("@LINES@", repr(self.jlines)).replace("@ROOT_ONLY@", repr(root_only)),
            0o755)

    def set_journal(self, lines, first=1):
        with open(self.jlines, "w") as f:
            json.dump([[first + i, x] for i, x in enumerate(lines)], f)

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


class TestPowerMax(Machine):
    def run_script(self, scope, power):
        r = subprocess.run(["sh", "-s"], input=hh.host_script(scope, power=power).encode(), stdout=subprocess.PIPE,
                           stderr=subprocess.PIPE, timeout=60)
        return r.stdout.decode()

    def test_owner_raises_to_the_maximum_and_a_guest_only_to_the_default(self):
        # cards whose 450 W default is below their 600 W maximum
        self.set_gpus([gpu(i, persistence="Enabled", limit=lim, default="450.00")
                       for i, lim in enumerate(("400.00", "450.00", "600.00"))])
        out = self.run_script({"scope": "host"}, "max")
        self.assertEqual([ln[2:] for ln in out.splitlines() if ln.startswith("F|GPU")],
                         ["GPU 0 power limit 400.00 -> 600.00 W", "GPU 1 power limit 450.00 -> 600.00 W"])
        self.assertEqual([g["power.limit"] for _, g in sorted(self.gpus().items())], ["600.00"] * 3)
        rec = hh.assess(out, 3, "max")
        self.assertEqual(([p for p in rec["problems"] if "power" in p], [g["power_max_w"] for g in rec["gpus"]]),
                         ([], [600.0] * 3))
        # the maximum is the owner's call: a guest's scope stops at the default
        self.set_gpus([gpu(i, persistence="Enabled", limit="400.00", default="450.00") for i in range(2)])
        out = self.run_script({"scope": "claims", "gpus": [1], "cores": []}, "max")
        self.assertEqual([ln[2:] for ln in out.splitlines() if ln.startswith("F|GPU")],
                         ["GPU 1 power limit 400.00 -> 450.00 W"])
        self.assertIn("POWER=default\n", hh.host_script({"scope": "claims", "gpus": [1], "cores": []}, power="max"))

    def test_the_check_holds_the_limit_against_its_target(self):
        self.set_gpus([gpu(0, persistence="Enabled", limit="450.00", default="450.00", maximum="600.00")])
        out = self.run_script(None, "default")
        self.assertIn("PMAX|0, 600.00", out)
        self.assertEqual([p for p in hh.assess(out, 1)["problems"] if "power" in p], [])
        self.assertEqual([p for p in hh.assess(out, 1, "max")["problems"] if "power" in p],
                         ["GPU 0 power limit 450 W below its 600 W maximum"])
        self.assertIn("450/450W (max 600W)", hh.gpu_text(hh.assess(out, 1)["gpus"][0]))
        # a driver that reports no maximum: the check holds the limit against the default
        self.assertEqual(hh.assess(HEALTHY, 2, "max")["problems"], [])
        self.assertEqual([p for p in hh.assess(SICK, 2, "max")["problems"] if "power" in p],
                         ["GPU 0 power limit 500 W below its 600 W default"])

    def test_the_maximum_is_judged_only_on_hosts_the_project_owns(self):
        # a clean host: empty kernel log, performance governors, GPUs at their 450 W default below a 600 W maximum
        write(os.path.join(self.bin, "dmesg"), "#!/bin/sh\n", 0o755)
        for n in os.listdir(self.cpu):
            write(os.path.join(self.cpu, n, "cpufreq", "scaling_governor"), "performance\n")
        self.set_gpus([gpu(i, persistence="Enabled", limit="450.00", default="450.00") for i in range(2)])
        mine = {"name": "mine", "target": "local", "owner": "proj", "gpus": "2x H200 NVL"}
        theirs = {"name": "theirs", "target": "local", "owner": "peer", "gpus": "2x H200 NVL"}
        rc, out = self.run_tool(hh, "--hosts", self.inventory([mine, theirs]), "--agent", "proj", "--power", "max",
                                "--json", code=hh.PROBLEMS)
        recs = json.loads(out)["hosts"]
        self.assertEqual(recs["mine"]["problems"], ["GPU 0 power limit 450 W below its 600 W maximum",
                                                    "GPU 1 power limit 450 W below its 600 W maximum"])
        self.assertEqual((recs["theirs"]["problems"], recs["mine"]["power"], recs["theirs"]["power"]),
                         ([], "max", "default"))
        # a project that asks for the maximum gets no lasting problem on a host it only uses
        self.run_tool(hh, "--hosts", self.inventory([theirs]), "--agent", "proj", "--power", "max", code=hh.OK)
        self.run_tool(hh, "--hosts", self.inventory([theirs]), "--agent", "peer", "--power", "max", code=hh.PROBLEMS)
        self.assertFalse([c for c in self.calls() if " -pl " in " %s " % c])

    def test_power_option_and_the_policy_end_to_end(self):
        proj = os.path.join(self.tmp, "proj")
        write(os.path.join(proj, "ai", "manifest.json"), json.dumps({"framework_version": "0.40.0",
                                                                     "project": {"name": "proj"}}))
        conf = os.path.join(proj, "ai", ".memory", "resource-sharing.json")
        write(conf, json.dumps({"project": "proj", "share_with": ["guest"]}))
        inv = self.inventory([{"name": "box", "target": "local", "root": self.claims_root(), "owner": "proj",
                               "gpus": "3x H200"}])
        base = ["--project", proj, "--hosts", inv]
        self.run_tool(hc, *base + ["install", "--launcher", "setsid", "--reserve-cores", "0", "--reserve-ram", "0"],
                      code=0)
        self.set_gpus([gpu(i, persistence="Enabled", limit="450.00", default="450.00") for i in range(3)])
        # read-only: the maximum is the target the check holds the limit to
        rc, out = self.run_tool(hh, *base + ["--power", "max"], code=hh.PROBLEMS)
        self.assertIn("GPU 0 power limit 450 W below its 600 W maximum", out)
        self.assertFalse([c for c in self.calls() if " -pl " in " %s " % c])
        # the default stays the default limit
        # (the stand-in kernel log always holds an Xid, so every run here finds problems)
        self.run_tool(hh, *base + ["--fix"], code=hh.PROBLEMS)
        self.assertEqual(set(g["power.limit"] for g in self.gpus().values()), {"450.00"})
        # the owner's policy asks for the maximum
        write(conf, json.dumps({"project": "proj", "share_with": ["guest"], "policy": {"health": {"power": "max"}}}))
        rc, out = self.run_tool(hh, *base + ["--fix"], code=hh.PROBLEMS)
        self.assertIn("GPU 0 power limit 450.00 -> 600.00 W", out)
        self.assertEqual(set(g["power.limit"] for g in self.gpus().values()), {"600.00"})
        # a guest asking for the maximum is told it is the owner's call, and gets the default
        self.set_gpus([gpu(i, persistence="Enabled", limit="400.00", default="450.00") for i in range(3)])
        self.run_tool(hc, *["--hosts", inv, "--agent", "guest", "claim", "--job", "j", "--cores", "1", "--ram", "1G",
                            "--gpu", "1"], code=0)
        rc, out = self.run_tool(hh, *["--hosts", inv, "--agent", "guest", "--as-owner", "--fix", "--power", "max"])
        self.assertIn("fixed: GPU 1 power limit 400.00 -> 450.00 W", out)
        self.assertIn("power max is the owner's call", out)
        # and its check holds the limits to the default there, as its fix does
        self.assertNotIn("maximum", out)
        write(conf, json.dumps({"project": "proj", "policy": {"health": {"power": "most"}}}))
        self.run_tool(hh, *base, code=hh.USAGE)


class TestKernelJournal(Machine):
    def probe(self, script=None, kernel=None):
        r = subprocess.run(["sh", "-s"], input=(script or hh.host_script(None, kernel=kernel)).encode(),
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=60)
        return hh.assess(r.stdout.decode(), 3)

    def lines(self, rec):
        return hh.host_lines({"name": "box"}, dict(rec, owner=None, fix=None))

    def test_the_journal_of_this_boot_keeps_what_the_ring_buffer_lost(self):
        self.journal()
        rec = self.probe()
        self.assertEqual((rec["xid_source"], rec["xid_read"], rec["xids"], rec["gpu_start_failures"], rec["xid_cursor"]),
                         ("journal", "boot", {79: 3, 13: 1, 149: 1}, 1, "c7"))
        self.assertIn("journalctl -k -b 0 -o cat --no-pager --show-cursor", self.calls())
        self.assertIn("Xid 79 x3, 149 x1 since boot (hardware or driver class: see NVIDIA's Xid catalog)",
                      rec["problems"])
        self.assertTrue(self.lines(rec)[1].endswith("; Xid from journalctl -k -b 0"), self.lines(rec))
        # without --write dmesg is not read beside the journal
        self.assertEqual(rec["xids_alt"], {})

    def test_after_the_first_reading_only_the_new_lines_are_read(self):
        self.journal()
        kernel = {"cursor": "c7", "boot": boot_id(), "base": {79: 3, 13: 1, 149: 1}, "init": 1}
        self.set_journal(JOURNAL_LINES + [X79])
        rec = self.probe(kernel=kernel)
        self.assertEqual((rec["xid_read"], rec["xids"], rec["gpu_start_failures"], rec["xid_cursor"]),
                         ("after", {79: 4, 13: 1, 149: 1}, 1, "c8"))
        self.assertIn("journalctl -k -b 0 -o cat --no-pager --show-cursor --after-cursor=c7", self.calls())
        self.assertIn("(the lines since the last check, added to its counts)", self.lines(rec)[1])
        # with --write, dmesg is counted beside it for its own base
        self.assertEqual(rec["xids_alt"], {"dmesg": {79: 1, 13: 1}})
        # nothing new: the counts stand, and no cursor comes back (the state keeps the old one)
        rec = self.probe(kernel=dict(kernel, cursor="c8", base={79: 4, 13: 1, 149: 1}))
        self.assertEqual((rec["xid_read"], rec["xids"], rec["xid_cursor"]), ("after", {79: 4, 13: 1, 149: 1}, None))
        # another boot, or a cursor the journal refuses: the whole boot is read again
        for k in (dict(kernel, boot="another-boot"), dict(kernel, cursor="bad cursor")):
            rec = self.probe(kernel=k)
            self.assertEqual((rec["xid_read"], rec["xids"][79]), ("boot", 4))

    def test_a_vacuumed_journal(self):
        # journald dropped the boot's oldest lines: a whole-boot reading counts what is left, and a cursor whose line
        # is gone resumes at the next line left, so no new event is lost
        self.journal()
        self.set_journal(JOURNAL_LINES[3:] + [X79], first=4)
        self.assertEqual(self.probe()["xids"], {79: 1, 13: 1, 149: 1})
        rec = self.probe(kernel={"cursor": "c2", "boot": boot_id(), "base": {79: 2}, "init": 0})
        self.assertEqual((rec["xid_read"], rec["xids"], rec["xid_cursor"]), ("after", {79: 3, 13: 1, 149: 1}, "c8"))

    def test_a_denied_or_failing_journal_falls_back_to_dmesg(self):
        # the default stand-in answers with a hint and exit 0; a journal with no files fails
        for journal in (FAKE_JOURNAL_DENIED, "#!/bin/sh\necho 'No journal files were found.' >&2\nexit 1\n"):
            write(os.path.join(self.bin, "journalctl"), journal, 0o755)
            rec = self.probe()
            self.assertEqual((rec["xid_source"], rec["xids"], rec["gpu_start_failures"]), ("dmesg", {79: 1, 13: 1}, 1))
            self.assertIn("; Xid from dmesg (a ring buffer: on a busy host its oldest lines are gone)",
                          self.lines(rec)[1])
        # neither readable: unknown, as before
        write(os.path.join(self.bin, "dmesg"), FAKE_DMESG_ROOT, 0o755)
        rec = self.probe()
        self.assertEqual((rec["xid_source"], rec["xids"]), (None, None))
        self.assertNotIn("Xid from", self.lines(rec)[1])

    def test_a_root_only_journal_is_read_through_sudo_in_the_owners_scope_only(self):
        if os.getuid() == 0:
            self.skipTest("root needs no sudo")
        self.journal(root_only=True)
        write(os.path.join(self.bin, "dmesg"), FAKE_DMESG_ROOT, 0o755)
        write(os.path.join(self.bin, "sudo"), FAKE_SUDO.replace("@LOG@", repr(self.log)), 0o755)
        rec = self.probe(HOST_SCRIPT({"scope": "host"}, cpu=self.cpu))
        self.assertEqual((rec["xid_source"], rec["xids"][79]), ("journal", 3))
        self.assertIn("sudo -n journalctl -k -b 0 -o cat --no-pager --show-cursor", self.calls())
        self.assertNotIn("sudo -n dmesg", self.calls())
        self.set_gpus([gpu(0), gpu(1), gpu(2)])
        rec = self.probe(HOST_SCRIPT({"scope": "claims", "gpus": [1], "cores": [0]}, cpu=self.cpu))
        self.assertIsNone(rec["xids"])
        self.assertEqual([c for c in self.calls() if c.startswith("sudo ")], [])


T0 = 1_900_000_000.0


class TestHealthJudgement(unittest.TestCase):
    def test_thresholds(self):
        th = dict(hh.HEALTH)
        rec = hh.assess(HEALTHY, 2)
        self.assertEqual(hh.readings(rec, 2, th, {}), {})
        g = rec["gpus"]
        g[1]["temp_c"] = 86.0
        self.assertEqual(hh.readings(rec, 2, th, None)["temp"], ("warn", "GPU 1 at 86 C"))
        g[1]["temp_c"] = 90.0
        self.assertEqual(hh.readings(rec, 2, th, None)["temp"], ("bad", "GPU 1 at 90 C"))
        self.assertNotIn("temp", hh.readings(rec, 2, dict(th, temp_warn_c=0, temp_bad_c=0), None))
        # GPUs missing against the inventory: bad, or warn below gpus_missing_bad
        self.assertEqual(hh.readings(rec, 3, th, None)["gpus"], ("bad", "2 of 3 GPUs visible"))
        self.assertEqual(hh.readings(rec, 3, dict(th, gpus_missing_bad=2), None)["gpus"][0], "warn")
        # new Xid events: warn, bad for a code that needs a GPU reset or a reboot
        self.assertEqual(hh.readings(rec, 2, th, {149: 2})["xid"], ("warn", "2 new Xid (149 x2)"))
        self.assertEqual(hh.readings(rec, 2, th, {79: 1, 149: 1})["xid"], ("bad", "2 new Xid (79 x1, 149 x1)"))
        self.assertNotIn("xid", hh.readings(rec, 2, dict(th, xid_new_warn=3), {149: 2}))
        # a GPU busy with almost no memory in use; a unified-memory GPU reports none and is not judged
        g[0]["mem_used_gb"] = 0.2
        self.assertEqual(hh.readings(rec, 2, th, None)["idle"][1],
                         "busy with almost no memory in use (hung, or a stray process): GPU 0 98% with 0.2 GB")
        self.assertNotIn("idle", hh.readings(rec, 2, dict(th, idle_busy_pct=0), None))
        unified = hh.assess(UNIFIED, 1)
        unified["gpus"][0]["util_pct"] = 100.0
        self.assertEqual(hh.readings(unified, 1, th, None), {})
        sick = hh.readings(hh.assess(SICK, 2), 2, th, None)
        self.assertEqual(sick, {
            "driver": ("bad", "nvidia-smi: Unable to determine the device handle for GPU0000:3B:00.0: Unknown Error"[:80]),
            "gpus": ("bad", "1 of 2 GPUs visible"), "temp": ("bad", "GPU 0 at 91 C"),
            "throttle": ("warn", "held back: GPU 0 hardware slowdown, hardware thermal slowdown"),
            "ecc": ("warn", "uncorrected ECC errors: GPU 0 2")})
        self.assertNotIn("throttle", hh.readings(hh.assess(SICK, 2), 2, dict(th, throttle_warn=False), None))

    def test_zero_turns_every_setting_off(self):
        th = dict(hh.HEALTH)
        rec = hh.assess(SICK, 3)
        rec["gpus"][0].update(util_pct=100.0, mem_used_gb=0.2)
        on = hh.readings(rec, 3, th, {79: 1, 149: 2})
        self.assertEqual(set(on), set(["driver", "gpus", "temp", "xid", "throttle", "ecc", "idle"]))

        def off(**kw):
            return hh.readings(rec, 3, dict(th, **kw), {79: 1, 149: 2})
        # each pair's bad level alone, then both
        self.assertEqual((off(temp_bad_c=0)["temp"][0], off(gpus_missing_bad=0)["gpus"][0]), ("warn", "warn"))
        self.assertEqual((off(temp_warn_c=0)["temp"][0], off(gpus_missing_warn=0)["gpus"][0]), ("bad", "bad"))
        self.assertNotIn("temp", off(temp_warn_c=0, temp_bad_c=0))
        self.assertNotIn("gpus", off(gpus_missing_warn=0, gpus_missing_bad=0))
        self.assertEqual(off(xid_bad_codes=[])["xid"][0], "warn")
        self.assertEqual(off(xid_new_warn=0)["xid"][0], "bad")
        self.assertNotIn("xid", off(xid_new_warn=0, xid_bad_codes=[]))
        self.assertNotIn("throttle", off(throttle_warn=False))
        self.assertNotIn("idle", off(idle_busy_pct=0))
        self.assertNotIn("idle", off(idle_mem_gb=0))
        # an unreachable host never trips, a level clears at the first clean check, a reading is never stale
        down, st = {"error": "ssh: x", "code": hh.UNREACHABLE}, {}
        for _ in range(3):
            _, st, ch = hh.judge({"name": "box"}, down, st, dict(th, unreachable_bad_checks=0), T0)
            self.assertEqual(ch, [])
        lv, _ = hh.hold({}, {"temp": ("bad", "GPU 0 at 95 C")}, set(), th, T0)
        self.assertEqual(hh.hold(lv, {}, set(), dict(th, clear_checks=0), T0)[1], [("CLEARED", "temp", "bad", "now ok")])
        self.assertEqual((hh.is_stale(10 ** 9, dict(th, stale_h=0)), hh.is_stale(4 * 3600, th), hh.is_stale(None, th)),
                         (False, True, True))
        # every setting is covered above
        self.assertEqual(sorted(hh.HEALTH), sorted([
            "temp_warn_c", "temp_bad_c", "gpus_missing_warn", "gpus_missing_bad", "xid_new_warn", "xid_bad_codes",
            "throttle_warn", "idle_busy_pct", "idle_mem_gb", "unreachable_bad_checks", "clear_checks", "stale_h"]))

    def reading(self, src, xids, boot="b1", alt=None, cursor=None, read="boot"):
        return {"xids": xids, "xid_source": src, "boot_id": boot, "xids_alt": alt or {}, "xid_cursor": cursor,
                "xid_read": read if src == "journal" else None, "gpu_start_failures": 0}

    def test_each_source_keeps_its_own_xid_base(self):
        R = self.reading
        # the owner's sudo run reads the journal, and dmesg beside it; a plain run reads dmesg only
        new, k = hh.count_xids(R("journal", {79: 1, 13: 5}, alt={"dmesg": {79: 1}}, cursor="c1"), {})
        self.assertEqual(new, {})
        new, k = hh.count_xids(R("dmesg", {79: 2}), k)
        self.assertEqual(new, {79: 1})
        # the journal sees that event too, and one more: only the second is new
        new, k = hh.count_xids(R("journal", {79: 3, 13: 5}, alt={"dmesg": {79: 3}}, cursor="c3", read="after"), k)
        self.assertEqual(new, {79: 1})
        self.assertEqual(hh.count_xids(R("dmesg", {79: 3}), k)[0], {})
        # dmesg alone sees an event, then its ring buffer wraps and hides the next: the journal counts that one once
        new, k = hh.count_xids(R("dmesg", {79: 4}), k)
        self.assertEqual(new, {79: 1})
        new, k = hh.count_xids(R("dmesg", {79: 1}), k)
        self.assertEqual(new, {})
        new, k = hh.count_xids(R("journal", {79: 5, 13: 5}, cursor="c5", read="after"), k)
        self.assertEqual(new, {79: 1})
        self.assertEqual((k["sources"]["journal"]["pending"], k["sources"]["dmesg"]["pending"]), ({}, {"79": 1}))

    def test_a_first_source_reboots_and_cursors(self):
        R = self.reading
        _, k = hh.count_xids(R("dmesg", {79: 1}), {})
        # the journal's first reading counts through dmesg read beside it, which has a base
        new, k = hh.count_xids(R("journal", {79: 4}, alt={"dmesg": {79: 2}}, cursor="c4"), k)
        self.assertEqual((new, k["sources"]["journal"]["xids"]), ({79: 1}, {"79": 4}))
        # after a reboot every Xid since boot is new, once: the journal starts the boot from nothing with it pending
        new, k = hh.count_xids(R("dmesg", {79: 2, 48: 1}, boot="b2"), k)
        self.assertEqual((new, k["boot"], k["sources"]["journal"]), ({79: 2, 48: 1}, "b2",
                                                                    {"xids": {}, "pending": {"48": 1, "79": 2}}))
        new, k = hh.count_xids(R("journal", {79: 2, 48: 1}, boot="b2", cursor="c9"), k)
        self.assertEqual(new, {})
        # a read with no line after the cursor keeps it; the probe gets it with the base it continues from
        _, k = hh.count_xids(R("journal", {79: 2, 48: 1, 13: 1}, boot="b2", read="after"), k)
        self.assertEqual(hh.kernel_arg({"kernel": k}), {"cursor": "c9", "boot": "b2", "base": {79: 2, 48: 1, 13: 1},
                                                        "init": 0})
        # a whole-boot read without a cursor drops it; an unread kernel log changes nothing
        _, k2 = hh.count_xids(R("journal", {79: 2, 48: 1}, boot="b2"), k)
        self.assertEqual(hh.kernel_arg({"kernel": k2}), {})
        self.assertEqual(hh.count_xids(dict(R("journal", None)), k), (None, k))

    def test_a_rise_counts_at_once_a_fall_after_clean_checks(self):
        th = dict(hh.HEALTH)
        lv, ch = hh.hold({}, {"temp": ("bad", "GPU 2 at 93 C")}, set(), th, T0)
        self.assertEqual(ch, [("TRIPPED", "temp", "bad", "GPU 2 at 93 C")])
        lv2, ch = hh.hold(lv, {"temp": ("bad", "GPU 2 at 94 C")}, set(), th, T0 + 3600)
        self.assertEqual((ch, lv2["temp"]["detail"], lv2["temp"]["since"]), ([], "GPU 2 at 94 C", lv["temp"]["since"]))
        lv3, ch = hh.hold(lv2, {"temp": ("warn", "GPU 2 at 87 C")}, set(), th, T0)
        self.assertEqual((ch, lv3["temp"]["level"], lv3["temp"]["clean"]), ([], "bad", 1))
        # a rise in between counts from zero again
        again, ch = hh.hold(lv3, {"temp": ("bad", "GPU 2 at 91 C")}, set(), th, T0)
        self.assertEqual((ch, again["temp"]["clean"]), ([], 0))
        lv4, ch = hh.hold(lv3, {"temp": ("warn", "GPU 2 at 86 C")}, set(), th, T0)
        self.assertEqual((ch, lv4["temp"]["level"]), ([("CLEARED", "temp", "bad", "now warn: GPU 2 at 86 C")], "warn"))
        _, ch = hh.hold(lv4, {}, set(), dict(th, clear_checks=1), T0)
        self.assertEqual(ch, [("CLEARED", "temp", "warn", "now ok")])
        # reach clears like any other level; a key not read this time keeps its state
        prev = {"reach": {"level": "bad", "detail": "unreachable: x", "clean": 0},
                "xid": {"level": "bad", "detail": "1 new Xid (79 x1)", "clean": 0}}
        lv, ch = hh.hold(prev, {}, set(["xid"]), th, T0)
        self.assertEqual((ch, lv["reach"]["clean"], lv["xid"]), ([], 1, prev["xid"]))

    def test_one_failed_check_sends_nothing(self):
        th = dict(hh.HEALTH)
        h = {"name": "box", "gpus": "2x H200 NVL"}
        down = {"error": "ssh: connect to host box port 22: Connection timed out", "code": hh.UNREACHABLE}
        up = dict(hh.assess(HEALTHY, 2), error=None)
        last = {"health": "warn", "gpus": 2, "gpus_visible": 2, "busy": 0.97, "xid_new": 0, "temp_max_c": 86,
                "notes": ["temp warn: GPU 1 at 86 C"], "checked": "2026-10-17T12:00:00-07:00"}
        prev = {"levels": {"temp": {"level": "warn", "detail": "GPU 1 at 86 C", "clean": 0}},
                "kernel": {"boot": "b", "sources": {"dmesg": {"xids": {"79": 1}, "pending": {}}}}}
        why = "unreachable: ssh: connect to host box port 22: Connection timed out"
        e, st, ch = hh.judge(h, down, prev, th, T0, last)
        # the entry keeps its last reading, with a note; nothing to send
        self.assertEqual(ch, [])
        self.assertEqual((e["health"], e["busy"], e["temp_max_c"], e["checked"]), ("warn", 0.97, 86, last["checked"]))
        self.assertEqual(e["notes"], ["temp warn: GPU 1 at 86 C", why + " (1 of 2 failed checks in a row before it counts)"])
        self.assertEqual((st["unreachable"], st["levels"]["temp"]["level"], st["kernel"]), (1, "warn", prev["kernel"]))
        # back at the next check: still nothing about reach
        self.assertFalse([c for c in hh.judge(h, up, st, th, T0)[2] if c[1] == "reach"])
        # the second failure in a row trips it, and the readings are unknown
        e, st, ch = hh.judge(h, down, st, th, T0, last)
        self.assertEqual(ch, [("TRIPPED", "reach", "bad", why + " (2 failed check(s) in a row)")])
        self.assertEqual((e["health"], e["gpus"], e["gpus_visible"], e["busy"], e["temp_max_c"]), ("bad", 2, None, None,
                                                                                                   None))
        self.assertEqual(st["levels"]["temp"]["level"], "warn")
        # it clears after clear_checks good checks; a failure in between keeps it tripped and counts again
        e, st, ch = hh.judge(h, up, st, th, T0)
        self.assertEqual((ch, e["health"]), ([], "bad"))
        e, st, ch = hh.judge(h, down, st, th, T0, e)
        self.assertEqual((ch, e["health"], st["levels"]["reach"]["clean"]), ([], "bad", 0))
        # (temp's clean count went on from before the failure: a failed check reads nothing)
        e, st, ch1 = hh.judge(h, up, st, th, T0)
        e, st, ch2 = hh.judge(h, up, st, th, T0)
        self.assertEqual(([c[:2] for c in ch1], [c[:2] for c in ch2], e["health"]),
                         ([("CLEARED", "temp")], [("CLEARED", "reach")], "ok"))

    def test_settings_from_the_pack(self):
        tmp = tempfile.mkdtemp(prefix="hc-th-")
        self.addCleanup(shutil.rmtree, tmp, True)
        write(os.path.join(tmp, "ai", "defaults.json"), json.dumps({"hosthealth.temp_warn_c": 80,
                                                                    "hosthealth.clear_checks": 3, "economy.level": 1}))
        conf = os.path.join(tmp, "ai", ".memory", "config.json")
        write(conf, json.dumps({"hosthealth.temp_warn_c": 83, "hosthealth.xid_bad_codes": [79],
                                "hosthealth.colour": "red"}))
        th, notes = hh.health_settings({"root": tmp, "pack": "ai"})
        self.assertEqual((th["temp_warn_c"], th["clear_checks"], th["xid_bad_codes"], th["temp_bad_c"]),
                         (83, 3, [79], 90))
        self.assertTrue(any("unknown setting hosthealth.colour left out" in n for n in notes), notes)
        for bad in ({"hosthealth.temp_bad_c": "hot"}, {"hosthealth.throttle_warn": 1},
                    {"hosthealth.xid_bad_codes": 79}, {"hosthealth.idle_mem_gb": -1}):
            write(conf, json.dumps(bad))
            with self.assertRaises(hc.Fail) as e:
                hh.health_settings({"root": tmp, "pack": "ai"})
            self.assertEqual(e.exception.code, hh.USAGE)
        write(conf, "{")
        self.assertTrue(any(n.startswith("cannot read " + conf) for n in hh.health_settings({"root": tmp,
                                                                                            "pack": "ai"})[1]))


class TestHealthFile(Machine):
    def project(self, hosts, share_with=None):
        proj = os.path.join(self.tmp, "proj")
        write(os.path.join(proj, "ai", "manifest.json"), json.dumps({"framework_version": "0.44.0",
                                                                     "project": {"name": "proj"}}))
        write(os.path.join(proj, "ai", ".memory", "hosts.json"), json.dumps(hosts))
        if share_with is not None:
            write(os.path.join(proj, "ai", ".memory", "resource-sharing.json"),
                  json.dumps({"project": "proj", "share_with": share_with}))
        return proj

    def check(self, proj, *extra):
        return self.run_tool(hh, "--project", proj, "--write", *extra)[1].splitlines()

    def doc(self, proj, name=hh.HEALTH_FILE):
        return json.loads(read(os.path.join(proj, "ai", ".memory", name)))

    def test_the_health_file_and_threshold_crossings(self):
        self.set_gpus([gpu(i) for i in range(3)])
        proj = self.project([{"name": "box", "target": "local", "gpus": "3x H200 NVL"}], share_with=["guest"])
        out = self.check(proj)
        path = os.path.join(proj, "ai", ".memory", hh.HEALTH_FILE)
        doc = self.doc(proj)
        e = doc["hosts"]["box"]
        self.assertEqual(sorted(doc), ["checked", "hosts", "schema"])
        self.assertEqual(sorted(e), ["busy", "checked", "gpus", "gpus_visible", "health", "notes", "temp_max_c",
                                     "xid_new"])
        self.assertEqual((doc["schema"], e["health"], e["gpus"], e["gpus_visible"], e["busy"], e["xid_new"],
                          e["temp_max_c"], e["notes"]), (1, "ok", 3, 3, 0.07, 0, 45, []))
        for t in (doc["checked"], e["checked"]):
            self.assertLess(abs(hc.parse_iso(t) - time.time()), 120)
        self.assertIn("health file %s: 1 host(s): 1 ok; no threshold crossed since the last check" % path, out)
        self.assertFalse([ln for ln in out if ln.startswith(("TRIPPED", "CLEARED", "send the"))], out)
        # GPU 2 runs hot: one line to send, and the file says bad
        hot = gpu(2)
        hot["temperature.gpu"] = "93"
        self.set_gpus([gpu(0), gpu(1), hot])
        out = self.check(proj)
        self.assertIn("TRIPPED box temp bad: GPU 2 at 93 C", out)
        self.assertIn("send the TRIPPED and CLEARED lines of box to the projects you share them with (guest); they "
                      "read the rest in the health file", out)
        e = self.doc(proj)["hosts"]["box"]
        self.assertEqual((e["health"], e["temp_max_c"], e["notes"]), ("bad", 93, ["temp bad: GPU 2 at 93 C"]))
        # the same reading an hour later: nothing to send
        out = self.check(proj)
        self.assertFalse([ln for ln in out if ln.startswith(("TRIPPED", "CLEARED", "send the"))], out)
        # cooled down: held for one clean check, then cleared
        self.set_gpus([gpu(i) for i in range(3)])
        self.assertFalse([ln for ln in self.check(proj) if ln.startswith("CLEARED")])
        self.assertEqual(self.doc(proj)["hosts"]["box"]["notes"], ["temp bad: GPU 2 at 93 C (clean for 1 of 2 checks)"])
        self.assertIn("CLEARED box temp bad: now ok", self.check(proj))
        self.assertEqual(self.doc(proj)["hosts"]["box"]["health"], "ok")
        # a new Xid 79 in the kernel log is bad at once: the GPU fell off the bus
        write(os.path.join(self.bin, "dmesg"), FAKE_DMESG + "echo '[ 99.0] NVRM: Xid (PCI:0000:3b:00): 79, pid=0, GPU "
                                                            "has fallen off the bus.'\n", 0o755)
        self.assertIn("TRIPPED box xid bad: 1 new Xid (79 x1)", self.check(proj))
        self.assertEqual(self.doc(proj)["hosts"]["box"]["xid_new"], 1)
        self.assertEqual(self.doc(proj, hh.STATE_FILE)["hosts"]["box"]["kernel"]["sources"]["dmesg"]["xids"],
                         {"13": 1, "79": 2})
        # the project clears at the first clean check
        write(os.path.join(proj, "ai", ".memory", "config.json"), json.dumps({"hosthealth.clear_checks": 1}))
        self.assertIn("CLEARED box xid bad: now ok", self.check(proj))
        self.set_gpus([gpu(0), gpu(1)])
        self.assertIn("TRIPPED box gpus bad: 2 of 3 GPUs visible", self.check(proj))
        # a setting the tool cannot use stops it before any check
        write(os.path.join(proj, "ai", ".memory", "config.json"), json.dumps({"hosthealth.temp_bad_c": "hot"}))
        self.run_tool(hh, "--project", proj, "--write", code=hh.USAGE)

    def test_the_journal_is_read_on_from_the_last_check_and_sources_may_alternate(self):
        # the journal readable (as through the owner's sudo), then denied (a plain run reads dmesg), then readable
        self.set_gpus([gpu(i) for i in range(3)])
        proj = self.project([{"name": "box", "target": "local", "gpus": "3x H200 NVL"}])
        dmesg = os.path.join(self.bin, "dmesg")
        self.journal()
        self.check(proj)

        def sources():
            return self.doc(proj, hh.STATE_FILE)["hosts"]["box"]["kernel"]["sources"]
        self.assertEqual((sources()["journal"]["cursor"], sources()["dmesg"]["xids"]), ("c7", {"13": 1, "79": 1}))
        self.assertEqual(self.doc(proj)["hosts"]["box"]["xid_new"], 0)
        self.assertFalse([c for c in self.calls() if "--after-cursor" in c])
        # a new event: only the journal lines after the cursor are read
        self.set_journal(JOURNAL_LINES + [X79])
        write(dmesg, FAKE_DMESG + "echo '[ 99.0] %s'\n" % X79, 0o755)
        out = self.check(proj)
        self.assertIn("journalctl -k -b 0 -o cat --no-pager --show-cursor --after-cursor=c7", self.calls())
        self.assertIn("TRIPPED box xid bad: 1 new Xid (79 x1)", out)
        # a plain run reads only dmesg, which shows one more event
        write(os.path.join(self.bin, "journalctl"), FAKE_JOURNAL_DENIED, 0o755)
        write(dmesg, FAKE_DMESG + "echo '[ 99.0] %s'\necho '[ 99.5] %s'\n" % (X79, X79), 0o755)
        self.check(proj)
        self.assertEqual(self.doc(proj)["hosts"]["box"]["xid_new"], 1)
        # the journal again, holding that event and nothing more: it is not counted twice
        self.journal(JOURNAL_LINES + [X79, X79])
        self.set_gpus([gpu(i) for i in range(3)])
        self.check(proj)
        self.assertIn("journalctl -k -b 0 -o cat --no-pager --show-cursor --after-cursor=c8", self.calls())
        self.assertEqual(self.doc(proj)["hosts"]["box"]["xid_new"], 0)
        self.assertEqual(sources()["journal"]["xids"], {"13": 1, "149": 1, "79": 5})

    def test_unreachable_hosts_and_hosts_no_longer_listed(self):
        down = os.path.join(self.tmp, "down-ssh")
        write(down, "#!/bin/sh\necho 'ssh: connect to host far port 22: Connection timed out' >&2\nexit 255\n", 0o755)
        box = {"name": "box", "target": "local", "gpus": "3x H200 NVL"}
        proj = self.project([box, {"name": "far", "target": "user@far"}])
        # one failed check sends nothing: a note only
        out = self.check(proj, "--ssh", down)
        self.assertFalse([ln for ln in out if ln.startswith(("TRIPPED", "CLEARED"))], out)
        e = self.doc(proj)["hosts"]["far"]
        self.assertEqual((e["health"], e["gpus"], e["gpus_visible"], e["busy"], e["notes"]), (
            "ok", None, None, None, ["unreachable: ssh: connect to host far port 22: Connection timed out (1 of 2 failed "
                                     "checks in a row before it counts)"]))
        self.assertIn("TRIPPED far reach bad: unreachable: ssh: connect to host far port 22: Connection timed out (2 "
                      "failed check(s) in a row)", self.check(proj, "--ssh", down))
        # a check of one host leaves the other's entry as it was
        self.check(proj, "--host", "box")
        self.assertEqual(self.doc(proj)["hosts"]["far"]["health"], "bad")
        # a host retired from the inventory leaves the file and the state
        write(os.path.join(proj, "ai", ".memory", "hosts.json"), json.dumps([box]))
        self.check(proj)
        self.assertEqual((sorted(self.doc(proj)["hosts"]), sorted(self.doc(proj, hh.STATE_FILE)["hosts"])),
                         (["box"], ["box"]))
        # --write and --shared need a project, --shared no checks
        self.run_tool(hh, "--hosts", self.inventory([box]), "--write", code=hh.USAGE)
        self.run_tool(hh, "--project", proj, "--shared", "--fix", code=hh.USAGE)

    def test_guests_read_the_owners_file(self):
        sol = os.path.join(self.tmp, "sol")

        def project(slug, share_with, hosts, config=None):
            root = os.path.join(sol, "projects", "my", slug)
            write(os.path.join(root, "ai", "manifest.json"), json.dumps({"framework_version": "0.44.0",
                                                                         "project": {"name": slug}}))
            write(os.path.join(root, "ai", ".memory", "resource-sharing.json"),
                  json.dumps({"project": slug, "share_with": share_with}))
            write(os.path.join(root, "ai", ".memory", "hosts.json"), json.dumps(hosts))
            return root

        owner = project("landlord", ["guest"], [{"name": "box", "target": "user@box", "gpus": "8x H100"},
                                                {"name": "spare", "target": "user@spare"}])
        guest = project("guest", [], [])
        rc, out = self.run_tool(hh, "--project", guest, "--shared", code=hh.PROBLEMS)
        self.assertIn("box [owner landlord]: no reading in its owner's health file: ask landlord to run hosthealth.py "
                      "--write with its hourly checks", out)
        now = time.time()

        def entry(health, age_s, notes=()):
            return {"health": health, "gpus": 8, "gpus_visible": 8, "busy": 0.97, "xid_new": 0, "temp_max_c": 87,
                    "notes": list(notes), "checked": hh.local_iso(now - age_s)}

        def publish(**hosts):
            write(os.path.join(owner, "ai", ".memory", hh.HEALTH_FILE),
                  json.dumps({"schema": 1, "checked": hh.local_iso(now), "hosts": hosts}))

        publish(box=entry("warn", 600, ["temp warn: GPU 2 at 87 C"]), spare=entry("ok", 5 * 3600))
        rc, out = self.run_tool(hh, "--project", guest, "--shared", code=hh.PROBLEMS)
        lines = out.splitlines()
        self.assertIn("box [owner landlord]: warn, checked 10m ago; 8 of 8 GPUs visible, busy 97%, hottest 87 C, 0 new "
                      "Xid", lines)
        self.assertIn("  temp warn: GPU 2 at 87 C", lines)
        self.assertIn("spare [owner landlord]: ok, checked 5.0h ago: stale, its owner's checks may have stopped (ask "
                      "it); 8 of 8 GPUs visible, busy 97%, hottest 87 C, 0 new Xid", lines)
        rc, out = self.run_tool(hh, "--project", guest, "--shared", "--json", code=hh.PROBLEMS)
        recs = json.loads(out)["hosts"]
        self.assertEqual((recs["landlord/box"]["health"], recs["landlord/box"]["stale"], recs["landlord/spare"]["stale"]),
                         ("warn", False, True))
        # all fresh and ok, by the guest's own staleness setting
        write(os.path.join(guest, "ai", ".memory", "config.json"), json.dumps({"hosthealth.stale_h": 6}))
        publish(box=entry("ok", 600), spare=entry("ok", 5 * 3600))
        self.run_tool(hh, "--project", guest, "--shared", code=hh.OK)


class TestDashboard(Machine):
    def test_a_claim_with_an_attached_process_still_has_one(self):
        root = os.path.join(self.tmp, "claims")
        dead = subprocess.Popen(["true"])
        dead.wait()
        write(os.path.join(root, "claims", "c--more.json"), json.dumps({
            "id": "c--more", "agent": "c", "job": "more", "class": "P1", "pid": dead.pid,
            "pids": [{"pid": os.getpid(), "pid_start": "x"}], "created": "2026-01-01T00:00:00Z",
            "resources": {"cores": [0], "gpus": []}}))
        inv = self.inventory([{"name": "gpu-box", "target": "user@gpu-box", "root": root}])
        rc, out = self.run_tool(hd, "--once", "--hosts", inv, "--ssh", self.ssh, code=0)
        self.assertIn("c/more P1 1 cores", out)
        self.assertNotIn("(no process)", out)

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

    def test_docs_carry_the_health_file(self):
        root = os.path.dirname(HERE)
        skill = " ".join(read(os.path.join(root, "shared", "resource-sharing.skill.md")).split())
        rule = " ".join(read(os.path.join(root, "shared", "resource-sharing.rule.md")).split())
        for want in ("host-health.json", "`TRIPPED <host> <what>`", "`CLEARED <host> <what>`", "hosthealth.py --shared",
                     "journalctl -k -b 0", "`--hosts NAME[,NAME]`", "never sends hourly readings"):
            self.assertIn(want, skill)
        # every threshold is documented, in the skill and the tool's own help
        for key in hh.HEALTH:
            self.assertIn("`%s`" % key, skill)
            self.assertIn(key, hh.__doc__)
        for want in ("host-health.json", "TRIPPED", "hosthealth.py --shared", "never ask for, send or acknowledge "
                     "hourly readings"):
            self.assertIn(want, rule)


if __name__ == "__main__":
    unittest.main()
