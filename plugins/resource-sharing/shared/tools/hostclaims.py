# rev. 5

"""hostclaims: share hosts between agents through claim files kept on each host.

Runs on the controller, the machine an agent works from. Each call sends this
same file over ssh to the host (`python3 -` with the script on stdin); there it
reads and writes the host's claims folder under a file lock, so checking free
capacity and writing a claim happen in one step. No daemons: between calls only
the jobs started with `run` and their watchers (in tmux) run on a host.

    python3 hostclaims.py [--hosts FILE] [--ssh PROG] [--agent NAME] <command> ...

Commands: install, uninstall, status, shared, claim, run, renew, release, reap,
yield, usage, pool, lease, extend, request, approve, decline, fit, audit
(`<command> -h` lists options). Host footprint: ~/.solaris/claims/ (host.json, status.json,
history.jsonl, .lock, claims/, stale/, run/<claim-id>/, requests/, pools/).
Stdlib only; Python 3.8 or newer on the controller and on every host.
Exit codes: 0 ok, 1 error, 2 bad usage, 3 does not fit, 4 host unreachable
(a claim or launch may still have happened: check status), 5 refused by a
sharing rule, 6 the hosts shared with this project changed since the last
`shared --ack`.
"""

import argparse
import calendar
import copy
import fcntl
import json
import math
import os
import re
import secrets
import shlex
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor

SCHEMA = 1
DEFAULT_ROOT = "~/.solaris/claims"
MARK = "@@hostclaims@@ "
OK, ERROR, USAGE, NOFIT, UNREACHABLE, DENIED = 0, 1, 2, 3, 4, 5
# shared: the hosts other projects share with this one changed since its last `shared --ack`
CHANGES = 6
CLASSES = ("P0", "P1", "P2", "P3")
DEFAULT_CLASSES = {
    "P0": {"share": 1.0, "preemptible": False},
    "P1": {"share": 0.5, "preemptible": False},
    "P2": {"share": 0.25, "preemptible": True},
    "P3": {"share": 0.25, "preemptible": True},
}
# host rules live in host.json so every agent on a host applies the same numbers
HOST_RULES = {
    "stale_min": 15.0,
    "yield_min_age_min": 20.0,
    "yield_grace_min": 10.0,
    "heartbeat_s": 60.0,
    "request_timeout_min": 120.0,
    "gpu_idle_mem_gb": 1.0,
    "unclaimed_gpu_share": 1.0,
    "cpu_sample_s": 0.5,
    "lock_timeout_s": 60.0,
    "recent_hours": 12.0,
    "rerun_guard_min": 15.0,
    "run_keep_days": 14.0,
    "outside_proc_min_gb": 1.0,
}
DEFAULT_RESERVE = {"cores": 1, "ram_gb": 4.0, "disk_free_pct": 10.0}
FIT_DEFAULTS = {
    "end_margin_min": 15.0,
    "reuse_extra_wait_min": 30.0,
    "extension_max_hours": 4.0,
    "extension_max_usd": 20.0,
    "extension_max_cost_ratio": 0.5,
    "extension_max_start_min": 10.0,
    "new_instance": {"create_min": 3.0, "not_ready_min": 12.0, "setup_min": 20.0,
                     "delete_min": 7.0, "usd_per_hour": {}},
}
AUDIT_DEFAULTS = {"paid_idle_max_min": 30.0, "free_idle_max_h": 24.0, "renew_before_h": 48.0,
                  "window_hours": 24.0}
LEASE_KINDS = ("none", "free", "paid")
LEASE_KEYS = ("kind", "planned_end", "usd_per_hour", "gpu_type", "instance", "provider", "started", "note")
REQUEST_TYPES = ("extension", "maintenance", "objection")
CONFIG_NAME = "resource-sharing.json"
SEEN_NAME = "resource-sharing-seen.json"
# what a guest compares between its seen list and the hosts shared with it now
SHARED_FIELDS = ("name", "owner", "target", "lease_kind", "planned_end", "gpus")
THREAD_VARS = ("OMP_NUM_THREADS", "MKL_NUM_THREADS", "OPENBLAS_NUM_THREADS",
               "NUMEXPR_NUM_THREADS", "NUMEXPR_MAX_THREADS", "VECLIB_MAXIMUM_THREADS",
               "BLIS_NUM_THREADS", "RAYON_NUM_THREADS", "POLARS_MAX_THREADS")
SIGNALS = ("TERM", "INT", "HUP", "USR1", "USR2", "none")
ROOT_ENTRIES = ("host.json", "status.json", "history.jsonl", ".lock", "claims", "stale", "run",
                "requests", "pools", "simulate.json")
TRANSIENT = ("state", "why")
NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,200}$")
POOL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")
EPS = 1e-6
GIB = float(1 << 30)
# processes smaller than this count by their RSS (a PSS read walks the page tables; small ones change nothing)
PSS_MIN_GB = 1.0 / 64
# a process pinned to some cores holds them only from this size up (helpers and kernel threads are smaller)
PINNED_MIN_GB = 1.0 / 16
# paired claims: the next claim's yield follows this long after its partner's grace ran out
DELIVER_MARGIN_S = 30.0
NONE_WORDS = ("none", "-", "")


class Fail(Exception):
    def __init__(self, msg, code=ERROR, **extra):
        Exception.__init__(self, msg)
        self.code = code
        self.extra = extra


# time, sizes and formatting

def iso(ts):
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(ts))


_ISO_RE = re.compile(r"^(\d{4})-(\d\d)-(\d\d)[T ](\d\d):(\d\d)(?::(\d\d)(?:\.\d+)?)?\s*(Z|[+-]\d\d:?\d\d)$")


def parse_iso(s):
    m = _ISO_RE.match(str(s).strip())
    if not m:
        raise ValueError("bad time %r (use 2026-09-30T18:00Z, an offset such as -07:00, or +3h)" % (s,))
    y, mo, d, h, mi, sec, tz = m.groups()
    ts = calendar.timegm((int(y), int(mo), int(d), int(h), int(mi), int(sec or 0), 0, 0, 0))
    if tz != "Z":
        off = tz[1:].replace(":", "")
        secs = int(off[:2]) * 3600 + int(off[2:]) * 60
        ts = ts - secs if tz[0] == "+" else ts + secs
    return float(ts)


_DUR_RE = re.compile(r"^(\d+(?:\.\d*)?|\.\d+)\s*([smhd]?)$")


def parse_hours(s):
    m = _DUR_RE.match(str(s).strip().lower())
    if not m:
        raise ValueError("bad duration %r (use 90m, 1.5h or 2d)" % (s,))
    return float(m.group(1)) * {"s": 1 / 3600.0, "m": 1 / 60.0, "h": 1.0, "d": 24.0}[m.group(2) or "h"]


def parse_when(s, base):
    s = str(s).strip()
    if s.startswith("+"):
        return base + parse_hours(s[1:]) * 3600
    return parse_iso(s)


_SIZE_RE = re.compile(r"^(\d+(?:\.\d*)?|\.\d+)\s*([kmgt]?)(?:ib|b)?$")


def parse_gb(s):
    m = _SIZE_RE.match(str(s).strip().lower())
    if not m:
        raise ValueError("bad size %r (use 512M, 16G or 1.5T)" % (s,))
    return float(m.group(1)) * {"k": 1 / 1048576.0, "m": 1 / 1024.0, "g": 1.0, "t": 1024.0}[m.group(2) or "g"]


def parse_cores(s):
    out = []
    for part in str(s).split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            a, b = part.split("-", 1)
            out.extend(range(int(a), int(b) + 1))
        else:
            out.append(int(part))
    return sorted(set(out))


def fmt_cores(cores):
    cores = sorted(set(int(c) for c in cores))
    out, i = [], 0
    while i < len(cores):
        j = i
        while j + 1 < len(cores) and cores[j + 1] == cores[j] + 1:
            j += 1
        out.append(str(cores[i]) if i == j else "%d-%d" % (cores[i], cores[j]))
        i = j + 1
    return ",".join(out)


def fmt_gb(x):
    if x is None:
        return "?"
    return "%.0fG" % x if abs(x) >= 100 else "%.1fG" % x


def fmt_age(sec):
    if sec is None:
        return "?"
    sec = max(0, int(sec))
    if sec < 90:
        return "%ds" % sec
    if sec < 5400:
        return "%dm" % (sec // 60)
    if sec < 172800:
        return "%.1fh" % (sec / 3600.0)
    return "%.1fd" % (sec / 86400.0)


def local_time(ts):
    return time.strftime("%b %d %I:%M %p %Z", time.localtime(ts))


def clock(sim):
    return time.time() + float((sim or {}).get("clock_offset_s") or 0.0)


def now_of(req, sim):
    return float(req["now"]) if req.get("now") is not None else clock(sim)


def valid_name(s, what):
    if not isinstance(s, str) or not NAME_RE.match(s):
        raise Fail("%s must be 1-64 letters, digits, '_' or '-', starting with a letter or digit (got %r)"
                   % (what, s), USAGE)
    return s


# user@host, host, IPv6 (bare or in brackets) or ssh://[user@]host[:port]; never a leading '-'
TARGET_RE = re.compile(r"^(ssh://)?[A-Za-z0-9_\[][A-Za-z0-9_.@:%\[\]-]*$")


def valid_target(s):
    # an ssh target never starts with '-': it would be read as an ssh option
    if not isinstance(s, str) or not TARGET_RE.match(s):
        raise Fail("bad ssh target %r (use user@host, a host name, an IPv6 address or ssh://user@host:port)"
                   % (s,), USAGE)
    return s


def valid_id(s, what):
    if not isinstance(s, str) or not ID_RE.match(s):
        raise Fail("bad %s: %r" % (what, s), USAGE)
    return s


# files: atomic JSON writes, append-only JSON lines, flock

def read_json(path, default=None):
    # missing, a folder in its place, or unparsable: the default; unreadable (permissions) still raises
    try:
        with open(path) as f:
            return json.load(f)
    except (FileNotFoundError, NotADirectoryError, IsADirectoryError, ValueError):
        return default


def write_json(path, obj, mode=0o644):
    d = os.path.dirname(path) or "."
    fd, tmp = tempfile.mkstemp(prefix=".tmp-", dir=d)
    try:
        os.fchmod(fd, mode)
        with os.fdopen(fd, "w") as f:
            json.dump(obj, f, indent=1, sort_keys=True)
            f.write("\n")
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def append_jsonl(path, obj):
    with open(path, "a") as f:
        f.write(json.dumps(obj, sort_keys=True) + "\n")


def read_jsonl(path):
    out = []
    try:
        with open(path) as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        out.append(json.loads(line))
                    except ValueError:
                        pass
    except FileNotFoundError:
        pass
    return out


class Lock(object):
    def __init__(self, path, timeout=60.0):
        self.path, self.timeout, self.fd = path, float(timeout), None

    def __enter__(self):
        self.fd = os.open(self.path, os.O_RDWR | os.O_CREAT, 0o644)
        deadline = time.monotonic() + self.timeout
        while True:
            try:
                fcntl.flock(self.fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                return self
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    os.close(self.fd)
                    self.fd = None
                    raise Fail("lock busy for %.0f s: %s" % (self.timeout, self.path))
                time.sleep(0.05)

    def __exit__(self, *exc):
        if self.fd is not None:
            try:
                fcntl.flock(self.fd, fcntl.LOCK_UN)
            finally:
                os.close(self.fd)
                self.fd = None
        return False


class Root(object):
    def __init__(self, path=None):
        self.path = os.path.abspath(os.path.expanduser(path or DEFAULT_ROOT))

    def p(self, *parts):
        return os.path.join(self.path, *parts)

    def installed(self):
        return os.path.isfile(self.p("host.json"))

    def lock(self, cfg):
        return Lock(self.p(".lock"), cfg["rules"]["lock_timeout_s"])


def open_root(req):
    root = Root(req.get("root"))
    if not root.installed():
        raise Fail("hostclaims is not installed at %s (run install first)" % root.path, ERROR, installed=False)
    return root


def host_cfg(root):
    raw = read_json(root.p("host.json"), {}) or {}
    rules = dict(HOST_RULES)
    for k, v in (raw.get("rules") or {}).items():
        if k in rules:
            rules[k] = float(v)
    reserve = dict(DEFAULT_RESERVE)
    reserve.update(raw.get("reserve") or {})
    return {"raw": raw, "rules": rules, "reserve": reserve, "mode": raw.get("mode") or "shared",
            "launcher": raw.get("launcher") or "tmux", "disk_path": raw.get("disk_path"),
            "lease": raw.get("lease"), "owner": raw.get("owner"), "share_with": raw.get("share_with"),
            "keep_until": raw.get("keep_until")}


def effective_lease(cfg, inv_lease):
    # the host's own copy wins over the inventory's
    lease = dict(cfg.get("lease") or inv_lease or {})
    lease["kind"] = lease.get("kind") or "none"
    return lease


def host_owner(cfg, req):
    # one owner per host: the host's record wins over the inventory's
    return cfg.get("owner") or req.get("inv_owner")


def need_owner(cfg, req, what):
    owner = host_owner(cfg, req)
    if owner and req.get("agent") != owner:
        raise Fail("only the owner (%s) %s; guests file a request instead" % (owner, what), DENIED, owner=owner)
    return owner


# host readings (Linux); tests replace them with a simulate.json in the root

def load_sim(root):
    # simulated readings count only when HOSTCLAIMS_SIMULATE=1, so a stray file never fakes a real host
    if os.environ.get("HOSTCLAIMS_SIMULATE") != "1":
        return None
    return read_json(root.p("simulate.json"))


def run_quiet(cmd, timeout=20):
    try:
        r = subprocess.run(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                           universal_newlines=True, timeout=timeout)
        return r.returncode, r.stdout, r.stderr
    except subprocess.TimeoutExpired:
        return None, "", "timed out after %d s" % timeout
    except OSError as e:
        return None, "", str(e)


def boot_id():
    try:
        with open("/proc/sys/kernel/random/boot_id") as f:
            return f.read().strip()
    except OSError:
        pass
    rc, out, _ = run_quiet(["sysctl", "-n", "kern.boottime"], 10)
    # keep only the boot second: the rest of the text is a local date that depends on TZ
    m = re.search(r"sec\s*=\s*(\d+)", out or "")
    return ("boottime " + m.group(1)) if rc == 0 and m else "unknown"


NETWORK_FS = ("nfs", "nfs4", "cifs", "smb3", "smbfs", "lustre", "gpfs", "beegfs", "glusterfs", "ceph", "afs", "9p",
              "fuse.sshfs", "fuse.glusterfs", "fuse.ceph")


def machine_id(sim=None):
    if sim and sim.get("machine_id"):
        return str(sim["machine_id"])
    for p in ("/etc/machine-id", "/var/lib/dbus/machine-id"):
        try:
            with open(p) as f:
                v = f.read().strip()
            if v:
                return v
        except OSError:
            pass
    return "host " + socket.gethostname()


def fs_type(path):
    # filesystem type of the mount holding path (Linux), else None
    best, kind = "", None
    try:
        with open("/proc/mounts") as f:
            for line in f:
                parts = line.split()
                if len(parts) < 3:
                    continue
                mnt = parts[1].replace("\\040", " ")
                if (path == mnt or path.startswith(mnt.rstrip("/") + "/")) and len(mnt) >= len(best):
                    best, kind = mnt, parts[2]
    except OSError:
        return None
    return kind


def check_machine(root, cfg):
    # a claims folder belongs to one machine: a home folder shared between hosts would mix their claims
    rec = cfg["raw"].get("machine")
    here = machine_id(load_sim(root))
    if rec and rec != here:
        raise Fail("this claims folder belongs to another machine (%s): is the home folder shared between hosts? Give "
                   "each host its own root on a local disk (the inventory's root field), or have the owner re-run "
                   "install here to take it over" % rec, DENIED)


def online_cpus():
    try:
        with open("/sys/devices/system/cpu/online") as f:
            cpus = parse_cores(f.read())
        if cpus:
            return cpus
    except (OSError, ValueError):
        pass
    return list(range(os.cpu_count() or 1))


def _stat_fields(pid):
    with open("/proc/%d/stat" % pid) as f:
        s = f.read()
    return s[s.rindex(")") + 2:].split()


def proc_start(pid):
    # start time of a live process (PID reuse safe); None when gone or a zombie
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return None
    if pid <= 0:
        return None
    if os.path.isdir("/proc/self"):
        try:
            f = _stat_fields(pid)
            return None if f[0] in ("Z", "X") else f[19]
        except (OSError, ValueError, IndexError):
            return None
    rc, out, _ = run_quiet(["ps", "-o", "stat=,lstart=", "-p", str(pid)], 10)
    line = out.strip()
    if rc != 0 or not line or line.startswith("Z"):
        return None
    return " ".join(line.split()[1:])


def pid_alive(pid, start):
    return bool(pid) and start is not None and proc_start(pid) == start


def proc_table():
    # pid -> (ppid, session, start, rss_gb); None off Linux
    if not os.path.isdir("/proc/self"):
        return None
    page = os.sysconf("SC_PAGE_SIZE") / GIB
    out = {}
    for name in os.listdir("/proc"):
        if not name.isdigit():
            continue
        try:
            f = _stat_fields(int(name))
            if f[0] in ("Z", "X"):
                continue
            out[int(name)] = (int(f[1]), int(f[3]), f[19], int(f[21]) * page)
        except (OSError, ValueError, IndexError):
            continue
    return out


def environ_claim(pid, ids):
    try:
        with open("/proc/%d/environ" % pid, "rb") as f:
            data = f.read()
    except OSError:
        return None
    for item in data.split(b"\0"):
        if item.startswith(b"HOSTCLAIMS_CLAIM_ID="):
            cid = item.split(b"=", 1)[1].decode("utf-8", "replace")
            return cid if cid in ids else None
    return None


def read_pss(pid):
    # proportional set size in GiB: a page shared by several processes counts once across them, so forked workers
    # that share their parent's memory are not counted many times; None when it cannot be read (another login's
    # process, a kernel older than 4.14, a process gone)
    try:
        with open("/proc/%d/smaps_rollup" % int(pid)) as f:
            for line in f:
                if line.startswith("Pss:"):
                    return int(line.split()[1]) / 1048576.0
    except (OSError, ValueError, IndexError):
        return None
    return None


def prog_name(pid):
    # a process's program name (never its arguments), or None
    try:
        with open("/proc/%d/comm" % int(pid)) as f:
            return f.read().strip() or None
    except (OSError, ValueError):
        pass
    rc, out, _ = run_quiet(["ps", "-o", "comm=", "-p", str(int(pid))], 10)
    name = out.strip()
    return os.path.basename(name) if rc == 0 and name else None


def pinned_procs(procs, cpus):
    # processes pinned to fewer than half of the online cores (a policy over one node of a two-node host is no pin),
    # leaving out kernel threads and small helpers: [{pid, cores, rss_gb}]; Linux only
    if procs is None or not hasattr(os, "sched_getaffinity"):
        return []
    online = set(cpus)
    out = []
    for pid, info in procs.items():
        if pid <= 2 or info[0] == 2 or info[3] < PINNED_MIN_GB or pid == os.getpid():
            continue
        try:
            s = set(os.sched_getaffinity(pid)) & online
        except (OSError, OverflowError, ValueError):
            # gone, not ours, or not a real pid
            continue
        if s and len(s) * 2 < len(online):
            out.append({"pid": pid, "cores": sorted(s), "rss_gb": round(info[3], 2)})
    return out


def cpu_times():
    out = {}
    try:
        with open("/proc/stat") as f:
            for line in f:
                if line.startswith("cpu") and line[3:4].isdigit():
                    parts = line.split()
                    vals = [int(x) for x in parts[1:9]]
                    idle = vals[3] + (vals[4] if len(vals) > 4 else 0)
                    out[int(parts[0][3:])] = (sum(vals), idle)
    except (OSError, ValueError):
        return {}
    return out


def cpu_busy(sample_s):
    if sample_s <= 0:
        return {}
    a = cpu_times()
    if not a:
        return {}
    time.sleep(sample_s)
    b = cpu_times()
    busy = {}
    for cpu, (tot, idle) in b.items():
        if cpu in a:
            dt, di = tot - a[cpu][0], idle - a[cpu][1]
            busy[cpu] = max(0.0, min(1.0, 1.0 - float(di) / dt)) if dt > 0 else 0.0
    return busy


def meminfo():
    vals = {}
    try:
        with open("/proc/meminfo") as f:
            for line in f:
                k, _, v = line.partition(":")
                parts = v.split()
                if parts:
                    vals[k.strip()] = float(parts[0]) / 1048576.0
    except (OSError, ValueError):
        pass
    total = vals.get("MemTotal")
    avail = vals.get("MemAvailable", vals.get("MemFree"))
    if total is None:
        rc, out, _ = run_quiet(["sysctl", "-n", "hw.memsize"], 10)
        if rc == 0 and out.strip().isdigit():
            total = int(out.strip()) / GIB
    return total, avail


def _num(s):
    try:
        return float(s)
    except (TypeError, ValueError):
        return None


def nvidia():
    exe = shutil.which("nvidia-smi")
    if not exe:
        return [], [], None
    rc, out, err = run_quiet([exe, "--query-gpu=index,uuid,name,memory.total,memory.used,utilization.gpu",
                              "--format=csv,noheader,nounits"], 30)
    if rc != 0:
        return [], [], "nvidia-smi failed: " + (err or out).strip()[:200]
    gpus = parse_gpu_csv(out)
    rc, out, err = run_quiet([exe, "--query-compute-apps=pid,gpu_uuid,used_memory",
                              "--format=csv,noheader,nounits"], 30)
    if rc != 0:
        return gpus, [], "nvidia-smi compute-apps failed: " + (err or out).strip()[:200]
    return gpus, parse_apps_csv(out, gpus), None


def parse_gpu_csv(out):
    # unified-memory GPUs report memory as [N/A]: only shares are checked on them
    gpus = []
    for line in out.splitlines():
        f = [x.strip() for x in line.split(",")]
        if len(f) < 6 or not f[0].isdigit():
            continue
        tot, used = _num(f[-3]), _num(f[-2])
        gpus.append({"index": int(f[0]), "uuid": f[1], "name": ",".join(f[2:-3]).strip(),
                     "mem_total_gb": tot / 1024.0 if tot else None,
                     "mem_used_gb": (used or 0.0) / 1024.0, "util": _num(f[-1])})
    return gpus


def parse_apps_csv(out, gpus):
    idx = dict((g["uuid"], g["index"]) for g in gpus)
    procs = []
    for line in out.splitlines():
        f = [x.strip() for x in line.split(",")]
        if len(f) < 3 or not f[0].isdigit():
            continue
        procs.append({"pid": int(f[0]), "gpu": idx.get(f[1]), "mem_gb": (_num(f[2]) or 0.0) / 1024.0})
    return procs


def take_probe(root, cfg, sim, sample=True):
    sim = sim or {}
    warnings = []
    pr = {"sim": bool(sim), "warnings": warnings}
    pr["boot_id"] = str(sim["boot_id"]) if "boot_id" in sim else boot_id()
    if "cpus" in sim:
        c = sim["cpus"]
        if isinstance(c, (int, float)):
            pr["cpus"] = list(range(int(c)))
        elif isinstance(c, str):
            pr["cpus"] = parse_cores(c)
        else:
            pr["cpus"] = sorted(int(x) for x in c)
    else:
        pr["cpus"] = online_cpus()
    if sim:
        pr["busy"] = dict((int(k), float(v)) for k, v in (sim.get("cpu_busy") or {}).items())
    else:
        pr["busy"] = cpu_busy(cfg["rules"]["cpu_sample_s"]) if sample else {}
    if "ram_total_gb" in sim:
        total = float(sim["ram_total_gb"])
        if "ram_avail_gb" in sim:
            avail = float(sim["ram_avail_gb"])
        else:
            avail = total - float(sim.get("ram_used_gb") or 0.0)
    else:
        total, avail = meminfo()
    pr["ram_total_gb"], pr["ram_avail_gb"] = total, avail
    if "disk_total_gb" in sim:
        pr["disk_total_gb"] = float(sim["disk_total_gb"])
        pr["disk_free_gb"] = float(sim.get("disk_free_gb", sim["disk_total_gb"]))
    else:
        path = cfg.get("disk_path") or (root.path if os.path.isdir(root.path) else "~")
        try:
            du = shutil.disk_usage(os.path.expanduser(path))
            pr["disk_total_gb"], pr["disk_free_gb"] = du.total / GIB, du.free / GIB
        except OSError as e:
            pr["disk_total_gb"] = pr["disk_free_gb"] = None
            warnings.append("disk: %s" % e)
    if "gpus" in sim:
        gpus = []
        for g in sim["gpus"]:
            gpus.append({"index": int(g["index"]), "uuid": g.get("uuid") or "GPU-sim-%s" % g["index"],
                         "name": g.get("name") or "GPU", "mem_total_gb": g.get("mem_total_gb"),
                         "mem_used_gb": float(g.get("mem_used_gb") or 0.0), "util": g.get("util")})
        procs, err = [dict(p) for p in sim.get("gpu_procs") or []], None
    else:
        gpus, procs, err = nvidia()
    if err:
        warnings.append(err)
    pr["gpus"], pr["gpu_procs"], pr["gpu_error"] = gpus, procs, err
    pr["procs"] = None if sim else proc_table()
    # processes pinned to some cores: simulated rows are taken as given
    pr["pinned"] = [dict(p) for p in sim.get("pinned") or []] if sim else pinned_procs(pr["procs"], pr["cpus"])
    pr["claim_rss_gb"] = dict(sim.get("claim_rss_gb") or {})
    pr["unclaimed_procs"] = list(sim.get("unclaimed_procs") or [])
    return pr


def load_state(root, req, cfg, sim, sample=True):
    return {"root": root, "cfg": cfg, "sim": sim, "now": now_of(req, sim),
            "probe": take_probe(root, cfg, sim, sample), "lease": effective_lease(cfg, req.get("inv_lease")),
            "gpu_rows": [], "mem": {}, "mem_src": {}, "big": [], "pinned": []}


# claims on disk

def claim_path(root, cid):
    return root.p("claims", cid + ".json")


def read_claims(root):
    out = []
    try:
        names = sorted(os.listdir(root.p("claims")))
    except FileNotFoundError:
        return out
    for n in names:
        if n.endswith(".json") and not n.startswith("."):
            c = read_json(root.p("claims", n))
            if isinstance(c, dict) and c.get("id") and c.get("agent"):
                out.append(c)
    return out


def save_claim(root, c):
    # claims hold commands and paths: readable by the host's login only
    write_json(claim_path(root, c["id"]), dict((k, v) for k, v in c.items() if k not in TRANSIENT), 0o600)


def started_ts(c):
    return parse_iso(c.get("started") or c["created"])


def claim_procs(c):
    # the processes tied to a claim, [(pid, start)]: its job (pid; older tool versions read only this one) and the
    # ones attached with claim --pid or renew --pid (pids)
    out, seen = [], set()
    for p in [{"pid": c.get("pid"), "pid_start": c.get("pid_start")}] + list(c.get("pids") or []):
        try:
            pid = int((p or {}).get("pid") or 0)
        except (TypeError, ValueError, AttributeError):
            continue
        if pid > 0 and pid not in seen:
            seen.add(pid)
            out.append((pid, p.get("pid_start")))
    return out


def live_procs(c):
    return [(p, s) for p, s in claim_procs(c) if pid_alive(p, s)]


def last_alive(c):
    # when the claim was last known to be in use: its heartbeat, or a later sighting of a tied process
    t = parse_iso(c.get("heartbeat") or c["created"])
    try:
        return max(t, parse_iso(c["seen"])) if c.get("seen") else t
    except ValueError:
        return t


def classify(c, st):
    # live, orphan (launcher gone, job running) or stale (reboot, or old heartbeat and no job)
    rules = st["cfg"]["rules"]
    if c.get("boot_id") and c["boot_id"] != st["probe"]["boot_id"]:
        return "stale", "host rebooted (boot id changed)"
    hb_age = st["now"] - parse_iso(c.get("heartbeat") or c["created"])
    job = bool(live_procs(c))
    la = c.get("launcher") or {}
    if la.get("pid"):
        if pid_alive(la.get("pid"), la.get("pid_start")):
            return "live", ""
        if job:
            return "orphan", "launcher gone, job still running"
    elif job:
        return "live", ""
    if hb_age <= rules["stale_min"] * 60:
        return "live", ""
    why = "heartbeat %d min old and no live job" % int(hb_age // 60)
    if c.get("seen") and not la.get("pid"):
        why += "; its process was last seen alive %s" % c["seen"]
    return "stale", why


def classify_all(claims, st):
    for c in claims:
        c["state"], c["why"] = classify(c, st)
    return [c for c in claims if c["state"] != "stale"]


def usage_event(event, c, end_ts, t, **extra):
    start = parse_iso(c["created"])
    res = c.get("resources") or {}
    ev = {"event": event, "ts": iso(t), "id": c["id"], "agent": c["agent"], "job": c["job"],
          "class": c.get("class"), "start": c["created"], "end": iso(max(start, end_ts)),
          "hours": round(max(0.0, end_ts - start) / 3600.0, 4),
          "gpus": [{"index": g.get("index"), "name": g.get("name"), "share": g.get("share")}
                   for g in res.get("gpus") or []],
          "cores": len(res.get("cores") or [])}
    ev.update(extra)
    return ev


def move_stale(root, st, c):
    doc = dict((k, v) for k, v in c.items() if k not in TRANSIENT)
    doc["stale"] = {"reason": c.get("why"), "at": iso(st["now"])}
    path = claim_path(root, c["id"])
    write_json(path, doc, 0o600)
    os.makedirs(root.p("stale"), exist_ok=True)
    os.rename(path, root.p("stale", c["id"] + ".json"))
    append_jsonl(root.p("history.jsonl"), usage_event("stale", c, last_alive(c), st["now"], reason=c.get("why")))


def track_procs(root, st, c):
    # under the lock, for a claim without a watcher: note when a tied process was last seen alive (the usage ledger's
    # end if it later lapses), and keep pid on a live process for older tool versions, which read only that one
    if (c.get("launcher") or {}).get("pid"):
        return
    live = live_procs(c)
    if not live:
        return
    changed = False
    if not pid_alive(c.get("pid"), c.get("pid_start")):
        pid, start = live[0]
        rest = [p for p in c.get("pids") or [] if isinstance(p, dict) and p.get("pid") != pid
                and (p.get("pid"), p.get("pid_start")) in live]
        c.update({"pid": pid, "pid_start": start, "pids": rest})
        changed = True
    try:
        fresh = c.get("seen") and st["now"] - parse_iso(c["seen"]) < 60
    except ValueError:
        fresh = False
    if not fresh:
        c["seen"] = iso(st["now"])
        changed = True
    if changed:
        save_claim(root, c)


def reap_locked(root, st, claims):
    keep, moved = [], []
    for c in claims:
        c["state"], c["why"] = classify(c, st)
        if c["state"] == "stale":
            move_stale(root, st, c)
            moved.append(c)
        else:
            track_procs(root, st, c)
            keep.append(c)
    # paired yields whose turn came; the callers' copies take the delivered request, so a later save keeps it
    for cid in deliver_due(root, st):
        cur = read_json(claim_path(root, cid)) or {}
        for c in keep:
            if c["id"] == cid:
                c["yield"] = cur.get("yield", c.get("yield"))
    return keep, moved


def remove_claim(root, st, c, reason, **extra):
    append_jsonl(root.p("history.jsonl"), usage_event("end", c, st["now"], st["now"], reason=reason, **extra))
    os.unlink(claim_path(root, c["id"]))


def claim_view(c, t, viewer=None, stale_min=None):
    v = dict(c)
    v.setdefault("state", "live")
    v["cores_text"] = fmt_cores((c.get("resources") or {}).get("cores") or [])
    try:
        v["age_s"] = int(t - parse_iso(c["created"]))
        v["heartbeat_age_s"] = int(t - parse_iso(c.get("heartbeat") or c["created"]))
        if stale_min is not None and not claim_procs(c) and not (c.get("launcher") or {}).get("pid"):
            # nothing watches this claim: it lapses unless renewed
            v["lapses_at"] = iso(parse_iso(c.get("heartbeat") or c["created"]) + stale_min * 60)
    except (KeyError, ValueError):
        pass
    if viewer is not None and c.get("agent") != viewer and v.get("cmd"):
        # other projects see only the program, never its arguments
        v["cmd"] = [str(v["cmd"][0])]
    return v


# capacity: host totals minus live claims minus unclaimed use

def claim_owner(pr, claims):
    # (owner, leaders, watchers): owner(pid) is the claim a process belongs to, by its tied processes and their trees
    # (by parent, or by session when a tied process leads one); watchers maps each claim's watcher to it
    procs = pr.get("procs")
    leaders = {}
    for c in claims:
        for pid, start in claim_procs(c):
            if procs is None or (pid in procs and procs[pid][2] == start):
                leaders[pid] = c["id"]
    watchers = dict(((c.get("launcher") or {}).get("pid"), c["id"]) for c in claims
                    if (c.get("launcher") or {}).get("pid"))

    def owner(pid):
        if pid in leaders:
            return leaders[pid]
        if procs is None or pid not in procs:
            return None
        if procs[pid][1] in leaders:
            return leaders[procs[pid][1]]
        p, hops = procs[pid][0], 0
        while p and hops < 64:
            if p in leaders:
                return leaders[p]
            if p not in procs:
                return None
            p, hops = procs[p][0], hops + 1
        return None

    return owner, leaders, watchers


def proc_mem(pid, rss_gb, mem_of):
    # (GiB, how): PSS where mem_of reads it, else RSS; small processes count by RSS without a read
    if mem_of is None:
        return rss_gb, "rss"
    if rss_gb < PSS_MIN_GB:
        return rss_gb, None
    gb = mem_of(pid)
    return (gb, "pss") if gb is not None else (rss_gb, "rss")


def attribute(pr, claims, min_gb=1.0, mem_of=None, src=None):
    # GPU processes and memory per claim, plus large processes that belong to no claim. Memory is PSS where mem_of
    # reads it (a page shared by forked workers counts once), else summed RSS; src, when given, gets per claim how
    # its memory was measured ("pss", "rss", "pss+rss" or "sim")
    procs = pr.get("procs")
    ids = set(c["id"] for c in claims)
    owner, leaders, watchers = claim_owner(pr, claims)
    rows = []
    for gp in pr.get("gpu_procs") or []:
        pid = int(gp.get("pid") or 0)
        cid = gp.get("claim") if gp.get("claim") in ids else owner(pid)
        if cid is None and procs is not None and pid:
            cid = environ_claim(pid, ids)
        rows.append({"pid": pid, "gpu": gp.get("gpu"), "mem_gb": float(gp.get("mem_gb") or 0.0), "claim": cid})
    mem, how, big = {}, {}, []
    if procs is not None:
        for pid, info in procs.items():
            cid = owner(pid) if leaders else None
            if cid is None and info[3] >= min_gb and pid not in watchers and pid != os.getpid():
                cid = environ_claim(pid, ids)
                if cid is None:
                    gb, _ = proc_mem(pid, info[3], mem_of)
                    if gb >= min_gb:
                        big.append({"pid": pid, "rss_gb": round(info[3], 2), "mem_gb": round(gb, 2)})
                    continue
            if cid:
                gb, h = proc_mem(pid, info[3], mem_of)
                mem[cid] = mem.get(cid, 0.0) + gb
                if h:
                    how.setdefault(cid, set()).add(h)
    else:
        for b in pr.get("unclaimed_procs") or []:
            rss = float(b.get("rss_gb") or 0.0)
            gb = float(b["mem_gb"]) if b.get("mem_gb") is not None else rss
            if gb >= min_gb:
                big.append({"pid": b.get("pid"), "rss_gb": rss, "mem_gb": gb})
    for cid, gb in (pr.get("claim_rss_gb") or {}).items():
        mem[cid] = float(gb)
        how[cid] = set(["sim"])
    if src is not None:
        src.update((cid, "+".join(sorted(h))) for cid, h in how.items())
    return rows, mem, big


def pinned_rows(pr, claims):
    # the pinned processes with their claim and the cores they run on outside it; a process tied to no claim counts
    # only on cores no claim holds (on a claim's cores it is most likely that claim's untied job)
    owner, _, watchers = claim_owner(pr, claims)
    held = dict((c["id"], set((c.get("resources") or {}).get("cores") or [])) for c in claims)
    every = set(x for cores in held.values() for x in cores)
    out = []
    for p in pr.get("pinned") or []:
        try:
            pid = int(p.get("pid"))
            cores = sorted(set(int(x) for x in p.get("cores") or []))
        except (TypeError, ValueError):
            continue
        cid = p.get("claim") if p.get("claim") in held else (watchers.get(pid) or owner(pid))
        if cid is None and pr.get("procs") is not None:
            cid = environ_claim(pid, set(held))
        mine = held[cid] if cid is not None else every
        row = {"pid": pid, "cores": cores, "claim": cid, "rss_gb": p.get("rss_gb"),
               "outside": [x for x in cores if x not in mine]}
        if cid is None and p.get("claim"):
            # a simulated row naming a claim that is not live (one that lapsed, say)
            row["named"] = p["claim"]
        out.append(row)
    return out


def own_pinned(st, prev, pids, starts):
    # the pinned rows, outside every live claim, of the job a new claim is for: the processes of the claim of the same
    # job that lapsed (its tied processes and their trees, those naming it in HOSTCLAIMS_CLAIM_ID, and any pinned only
    # within its cores, as a job started by hand under it is), and the processes given with --pid and their trees
    pr = st["probe"]
    rows = [p for p in st.get("pinned") or [] if p.get("claim") is None]
    if not rows or not (prev or pids):
        return []
    tied = []
    if prev:
        tied.append({"id": prev["id"], "pid": prev.get("pid"), "pid_start": prev.get("pid_start"),
                     "pids": prev.get("pids") or []})
    if pids:
        tied.append({"id": "--pid", "pid": pids[0], "pid_start": starts[pids[0]],
                     "pids": [{"pid": x, "pid_start": starts[x]} for x in pids[1:]]})
    owner = claim_owner(pr, tied)[0]
    cores = set(((prev or {}).get("resources") or {}).get("cores") or [])
    out = []
    for p in rows:
        mine = owner(p["pid"]) is not None
        if not mine and prev:
            mine = (p.get("named") == prev["id"] or (cores and set(p["cores"]) <= cores)
                    or (pr.get("procs") is not None and environ_claim(p["pid"], set([prev["id"]])) is not None))
        if mine:
            out.append(p)
    return out


def attach_claims(st, claims):
    st["mem_src"] = {}
    # PSS from the machine's own readings only; simulated hosts give per-claim numbers
    mem_of = None if st.get("sim") else read_pss
    st["gpu_rows"], st["mem"], st["big"] = attribute(st["probe"], claims, st["cfg"]["rules"]["outside_proc_min_gb"],
                                                     mem_of, st["mem_src"])
    st["pinned"] = pinned_rows(st["probe"], claims)


def capacity(st, claims, exclude=()):
    pr, rules, res = st["probe"], st["cfg"]["rules"], st["cfg"]["reserve"]
    active = [c for c in claims if c["id"] not in exclude]
    rows, mem = st["gpu_rows"], st.get("mem") or {}
    claimed, held = set(), set()
    for c in claims:
        cores = (c.get("resources") or {}).get("cores") or []
        held.update(cores)
        if c["id"] not in exclude:
            claimed.update(cores)
    # cores a pinned process runs on outside its claim (or outside every claim) are in use: never granted; a what-if
    # that drops a claim drops its processes too
    pinned = set(x for p in st.get("pinned") or [] if p.get("claim") is None or p["claim"] not in exclude
                 for x in p.get("outside") or [])
    free_set = [c for c in pr["cpus"] if c not in claimed and c not in pinned]
    # load on cores a claim holds is that claim's, even when a what-if drops the claim
    unclaimed_cpu = sum(pr["busy"].get(c, 0.0) for c in pr["cpus"] if c not in held)
    # the reserve absorbs the first cores of unclaimed load; load on pinned cores already took those cores out, and
    # load on the cores a claim's own job runs on (own_cores, while that claim is planned) is that job's
    own = set(st.get("own_cores") or [])
    loose = sum(pr["busy"].get(c, 0.0) for c in pr["cpus"] if c not in held and c not in pinned and c not in own)
    hidden = max(int(res["cores"]), int(math.ceil(loose - 0.25)) if loose > 0.25 else 0)
    total, avail = pr["ram_total_gb"], pr["ram_avail_gb"]
    # a claim's memory in use is the PSS of its processes (summed RSS where PSS cannot be read)
    all_mem = sum(mem.get(c["id"], 0.0) for c in claims)
    ram_free = unclaimed_ram = None
    if total is not None:
        used = (total - avail) if avail is not None else all_mem
        unclaimed_ram = max(0.0, used - all_mem)
        committed = sum(max(float((c.get("resources") or {}).get("ram_gb") or 0.0), mem.get(c["id"], 0.0))
                        for c in active)
        ram_free = total - float(res["ram_gb"]) - committed - unclaimed_ram
    dt, df = pr["disk_total_gb"], pr["disk_free_gb"]
    floor = (dt or 0.0) * float(res["disk_free_pct"]) / 100.0
    below = df is not None and df < floor
    disk_free = None
    if df is not None:
        disk_free = df - floor - sum(float((c.get("resources") or {}).get("disk_gb") or 0.0) for c in active)
    gpus = []
    for g in pr["gpus"]:
        i = g["index"]
        on_gpu = set(c["id"] for c in claims
                     if any(x.get("index") == i for x in (c.get("resources") or {}).get("gpus") or []))
        share, mem, n = 0.0, 0.0, 0
        for c in active:
            for x in (c.get("resources") or {}).get("gpus") or []:
                if x.get("index") == i:
                    share += float(x.get("share") or 0.0)
                    actual = sum(r["mem_gb"] for r in rows if r["gpu"] == i and r["claim"] == c["id"])
                    mem += max(float(x.get("mem_gb") or 0.0), actual)
                    n += 1
        # a process counts as claimed only on a GPU its claim holds
        claimed_mem = sum(r["mem_gb"] for r in rows if r["gpu"] == i and r["claim"] in on_gpu)
        stray = [r for r in rows if r["gpu"] == i and r["claim"] not in on_gpu]
        unclaimed_mem = max(0.0, float(g.get("mem_used_gb") or 0.0) - claimed_mem)
        busy = bool(stray) or unclaimed_mem >= rules["gpu_idle_mem_gb"]
        if busy:
            share += rules["unclaimed_gpu_share"]
        tot = g.get("mem_total_gb")
        gpus.append({"index": i, "name": g.get("name"), "uuid": g.get("uuid"), "mem_total_gb": tot,
                     "share_used": round(share, 6), "free_share": round(max(0.0, 1.0 - share), 6),
                     "free_mem_gb": None if not tot else round(tot - mem - unclaimed_mem, 3),
                     "claims": n, "unclaimed": busy, "unclaimed_mem_gb": round(unclaimed_mem, 3),
                     "unclaimed_pids": [r["pid"] for r in stray]})
    return {"cores_free_set": free_set, "cores_free": max(0, len(free_set) - hidden),
            "unclaimed_cpu": unclaimed_cpu, "ram_free_gb": ram_free, "unclaimed_ram_gb": unclaimed_ram,
            "disk_free_gb": disk_free, "below_floor": below, "gpus": gpus, "outside_procs": list(st.get("big") or []),
            "pinned_cores": sorted(pinned)}


def free_summary(cap):
    r = lambda x, n=1: None if x is None else round(x, n)
    return {"cores": cap["cores_free"], "ram_gb": r(cap["ram_free_gb"]), "disk_gb": r(cap["disk_free_gb"]),
            "below_floor": cap["below_floor"], "unclaimed_cpu": round(cap["unclaimed_cpu"], 2),
            "unclaimed_ram_gb": r(cap["unclaimed_ram_gb"]), "outside_procs": cap.get("outside_procs") or [],
            "pinned_cores": cap.get("pinned_cores") or [],
            "gpus": [{"index": g["index"], "name": g["name"], "free_share": g["free_share"],
                      "free_mem_gb": g["free_mem_gb"], "claims": g["claims"], "unclaimed": g["unclaimed"]}
                     for g in cap["gpus"]]}


def outside_use(st, cap):
    # work outside any claim: a GPU in use, a core or more of load, or large unclaimed processes (by PSS where it
    # can be read); the OS's own memory, caches and /dev/shm are not work
    gpus = [g["index"] for g in cap["gpus"] if g["unclaimed"]]
    cpu = cap["unclaimed_cpu"] if cap["unclaimed_cpu"] >= 1.0 else 0.0
    big = cap.get("outside_procs") or []
    ram = sum(b["mem_gb"] if b.get("mem_gb") is not None else b["rss_gb"] for b in big)
    return {"gpus": gpus, "cpu_cores": round(cpu, 2), "ram_gb": round(ram, 1), "pids": [b["pid"] for b in big][:20],
            "any": bool(gpus or cpu or ram)}


def host_tag(st, active, cap):
    if st["cfg"]["mode"] == "draining":
        return "busy"
    if not active and not outside_use(st, cap)["any"]:
        return "free"
    min_share = min(v["share"] for v in DEFAULT_CLASSES.values())
    full = (cap["cores_free"] < 1 or (cap["ram_free_gb"] is not None and cap["ram_free_gb"] < 1.0)
            or cap["below_floor"]
            or (bool(cap["gpus"]) and all(g["free_share"] < min_share - EPS for g in cap["gpus"])))
    return "busy" if full else "partly-busy"


def write_status(root, st, active, cap):
    tag = host_tag(st, active, cap)
    doc = {"schema": SCHEMA, "tag": tag, "updated": iso(st["now"]), "mode": st["cfg"]["mode"],
           "lease": st["lease"], "claims": len(active), "agents": sorted(set(c["agent"] for c in active)),
           "free": free_summary(cap)}
    try:
        write_json(root.p("status.json"), doc)
    except OSError:
        pass
    return tag


def refresh_status(root):
    try:
        cfg = host_cfg(root)
        st = load_state(root, {}, cfg, load_sim(root))
        active = classify_all(read_claims(root), st)
        attach_claims(st, active)
        write_status(root, st, active, capacity(st, active))
    except Exception:
        pass


# planning: does a request fit, and on which cores and GPUs

def want_from(req, need_agent=True):
    classes = req.get("classes") or DEFAULT_CLASSES
    agent = req.get("agent")
    if need_agent:
        if not agent:
            raise Fail("--agent (or HOSTCLAIMS_AGENT) is required", USAGE)
        valid_name(agent, "agent")
    cls = str(req.get("class") or "P1").upper()
    if cls not in CLASSES:
        raise Fail("class must be one of P0, P1, P2, P3", USAGE)
    cdef = dict(DEFAULT_CLASSES[cls])
    cdef.update(classes.get(cls) or {})
    pre = req.get("preemptible")
    if pre is None:
        pre = bool(cdef.get("preemptible"))
    if cls == "P0" and pre:
        raise Fail("P0 claims are never preemptible", USAGE)
    if cls == "P3" and not pre:
        raise Fail("P3 claims are always preemptible", USAGE)
    gpus = [{"index": s.get("index"), "share": s.get("share"), "mem_gb": s.get("mem_gb")}
            for s in req.get("gpus") or []]
    explicit = [g["index"] for g in gpus if g["index"] is not None]
    if len(explicit) != len(set(explicit)):
        raise Fail("the same GPU index is given twice", USAGE)
    for g in gpus:
        if g["share"] is not None and not (0 < float(g["share"]) <= 1 + EPS):
            raise Fail("a GPU share must be above 0 and at most 1", USAGE)
    cores = int(req.get("cores") or 0)
    if cores < 0:
        raise Fail("cores must not be negative", USAGE)
    hours = req.get("hours")
    return {"agent": agent, "job": req.get("job"), "class": cls, "preemptible": bool(pre),
            "borrowed": bool(req.get("borrowed")), "class_share": float(cdef.get("share") or 0.25),
            "cores": cores, "ram_gb": float(req.get("ram_gb") or 0.0), "disk_gb": float(req.get("disk_gb") or 0.0),
            "gpus": gpus, "gpu_type": req.get("gpu_type"), "gpu_mem_gb": req.get("gpu_mem_gb"),
            "hours": None if hours is None else float(hours)}


def gpu_matches(g, want):
    t = want.get("gpu_type")
    if t and str(t).lower() not in str(g.get("name") or "").lower():
        return False
    m = want.get("gpu_mem_gb")
    # a GPU that reports no memory (unified memory) is matched by share only
    if m and g.get("mem_total_gb") is not None and g["mem_total_gb"] + EPS < float(m):
        return False
    return True


def admission(cfg, agent, borrowed, preemptible, new=True):
    # sharing list, draining and dedicated modes: who may use this host at all, and with which flags (new=False: a
    # change to a live claim, which a draining host still allows)
    out = []
    # a host is private to its owner until the owner lists who else may use it (a missing list shares with nobody)
    sw, owner = cfg.get("share_with") or [], cfg.get("owner")
    if agent and "*" not in sw and agent not in sw and agent != owner:
        out.append(("mode", "this host is not shared with %s (owner %s shares it with: %s)"
                    % (agent, owner, ", ".join(sw) or "nobody")))
    mode = cfg["mode"]
    if mode == "draining":
        if new:
            out.append(("mode", "host is draining: no new claims"))
    elif mode.startswith("dedicated:"):
        dedicated = mode.split(":", 1)[1]
        if agent != dedicated and not (borrowed and preemptible):
            out.append(("mode", "host is dedicated to %s (other agents need a preemptible --borrowed claim)"
                        % dedicated))
    return out


def plan(st, claims, want, exclude=(), lease_check=True, prefer=None):
    # prefer, taken first while free: "first", the cores the claim's own job runs pinned to; then "cores" and "gpus",
    # what a lapsed claim of the same job held
    pr, cfg = st["probe"], st["cfg"]
    cap = capacity(st, claims, exclude)
    reasons, warnings = admission(cfg, want["agent"], want["borrowed"], want["preemptible"]), []
    pf, pc = set((prefer or {}).get("first") or []), set((prefer or {}).get("cores") or [])
    pg = set((prefer or {}).get("gpus") or [])
    cores = []
    if want["cores"] > cap["cores_free"]:
        reasons.append(("cores", "cores: want %d, %d free" % (want["cores"], cap["cores_free"])))
    else:
        busy = pr["busy"]
        order = sorted(cap["cores_free_set"], key=lambda c: (c not in pf, c not in pc, busy.get(c, 0.0) >= 0.5, c))
        cores = sorted(order[:want["cores"]])
    if cap["ram_free_gb"] is not None and want["ram_gb"] > cap["ram_free_gb"] + EPS:
        reasons.append(("ram", "ram: want %s, %s free" % (fmt_gb(want["ram_gb"]), fmt_gb(max(0.0, cap["ram_free_gb"])))))
    if cap["below_floor"]:
        reasons.append(("disk", "disk: free space is below the host's floor"))
    elif cap["disk_free_gb"] is not None and want["disk_gb"] > cap["disk_free_gb"] + EPS:
        reasons.append(("disk", "disk: want %s, %s free" % (fmt_gb(want["disk_gb"]), fmt_gb(max(0.0, cap["disk_free_gb"])))))
    chosen = []
    if want["gpus"]:
        if not cap["gpus"]:
            reasons.append(("gpu", "gpu: none on this host" + (" (%s)" % pr["gpu_error"] if pr.get("gpu_error") else "")))
        else:
            pool = dict((g["index"], dict(g)) for g in cap["gpus"])
            for spec in sorted(want["gpus"], key=lambda s: s["index"] is None):
                taken = set(x["index"] for x in chosen)
                share = float(spec["share"]) if spec["share"] is not None else want["class_share"]
                if spec["index"] is not None:
                    g = pool.get(int(spec["index"]))
                    if g is None:
                        reasons.append(("gpu", "gpu: no GPU %s here" % spec["index"]))
                        continue
                    cands = [g] if gpu_matches(g, want) else []
                else:
                    cands = sorted((g for g in pool.values() if g["index"] not in taken and gpu_matches(g, want)),
                                   key=lambda g: (g["index"] not in pg, g["share_used"], g["index"]))
                pick = None
                for g in cands:
                    if g["free_share"] + EPS < share:
                        continue
                    mem, free_mem = spec["mem_gb"], g.get("free_mem_gb")
                    if mem is None and g.get("mem_total_gb"):
                        # an unstated memory slice shrinks to what is free, down to half the fair slice
                        fair = share * g["mem_total_gb"]
                        if free_mem is not None and free_mem + EPS < 0.5 * fair:
                            continue
                        mem = fair if free_mem is None else max(0.0, min(fair, free_mem))
                    elif mem is not None and free_mem is not None and free_mem + EPS < mem:
                        continue
                    pick = (g, mem)
                    break
                if pick is None:
                    if not cands:
                        what = "GPU %s" % spec["index"] if spec["index"] is not None else "a GPU"
                        reasons.append(("gpu", "gpu: %s matching %s" % (
                            "no" if spec["index"] is None else what + " is not",
                            "/".join(x for x in (want.get("gpu_type"), fmt_gb(want["gpu_mem_gb"]) if want.get("gpu_mem_gb") else None) if x) or "the request")))
                    else:
                        states = ", ".join("GPU %d %.2f free, %s" % (g["index"], g["free_share"], fmt_gb(g["free_mem_gb"]))
                                           for g in cands[:8])
                        reasons.append(("gpu", "gpu: no GPU with a %.2f share%s free (%s)" % (
                            share, "" if spec["mem_gb"] is None else " and %s" % fmt_gb(spec["mem_gb"]), states)))
                    continue
                g, mem = pick
                before = g["share_used"]
                g["free_share"] -= share
                g["share_used"] += share
                if mem is not None and g.get("free_mem_gb") is not None:
                    g["free_mem_gb"] -= mem
                chosen.append({"index": g["index"], "share": round(share, 4),
                               "mem_gb": None if mem is None else round(mem, 3),
                               "name": g.get("name"), "uuid": g.get("uuid"), "contention": round(before, 4)})
    lease = st["lease"]
    if lease_check and lease.get("kind") in ("paid", "free") and lease.get("planned_end"):
        end = parse_iso(lease["planned_end"])
        hours = want.get("hours")
        if lease["kind"] == "paid":
            if hours is None:
                reasons.append(("lease", "lease: a paid host; pass --hours so the claim can be checked against its planned end"))
            elif st["now"] + hours * 3600 > end + 1:
                reasons.append(("lease", "lease: would run past the planned end %s; ask its owner with `extend`" % lease["planned_end"]))
        elif hours is not None and st["now"] + hours * 3600 > end:
            warnings.append("the lease ends %s, before this claim's eta; its owner must renew it (ask with extend)"
                            % lease["planned_end"])
    return {"ok": not reasons, "reasons": reasons, "warnings": warnings, "cores": cores, "gpus": chosen, "cap": cap}


def may_yield(req_cls, req_borrowed, req_agent, c, cfg):
    rank = CLASSES.index
    tc = c.get("class") or "P1"
    mode = cfg["mode"]
    # on a dedicated host, other projects never preempt the dedicated project's own jobs
    if mode.startswith("dedicated:") and c["agent"] == mode.split(":", 1)[1] and req_agent != c["agent"]:
        return False
    if rank(req_cls) < rank(tc):
        return True
    if rank(req_cls) == rank(tc) and c.get("borrowed") and not req_borrowed:
        return True
    mode = cfg["mode"]
    if (mode.startswith("dedicated:") and mode.split(":", 1)[1] == req_agent and c.get("borrowed")
            and c["agent"] != req_agent):
        return True
    return False


def yield_group(c, claims):
    # the claims that yield together with c: its agent's claims linked to it by yield_with, either way
    mine = [x for x in claims if x["agent"] == c["agent"]]
    by_job = dict((x["job"], x) for x in mine)
    group, todo = {c["id"]: c}, [c]
    while todo:
        x = todo.pop()
        links = [by_job.get(j) for j in x.get("yield_with") or []]
        links += [y for y in mine if x["job"] in (y.get("yield_with") or [])]
        for y in links:
            if y is not None and y["id"] not in group:
                group[y["id"]] = y
                todo.append(y)
    return list(group.values())


def yield_due(c, by_id, t):
    # a deferred yield is due once each partner it waits for has ended, or has had its own grace and a margin
    for pid_ in (c.get("yield") or {}).get("after") or []:
        p = by_id.get(pid_)
        y = (p or {}).get("yield") or {}
        if p is None or not y:
            continue
        if y.get("deferred"):
            return False
        try:
            start = parse_iso(y.get("delivered") or y.get("requested"))
        except (TypeError, ValueError):
            continue
        if t < start + float(y.get("grace_min") or 0.0) * 60 + DELIVER_MARGIN_S:
            return False
    return True


def deliver_due(root, t_or_st):
    # under the lock: write the yield files of paired claims whose partners have ended or had their grace
    t = t_or_st["now"] if isinstance(t_or_st, dict) else float(t_or_st)
    claims = read_claims(root)
    waiting = [c for c in claims if (c.get("yield") or {}).get("deferred")]
    by_id = dict((c["id"], c) for c in claims)
    out = []
    for c in sorted(waiting, key=lambda x: x["id"]):
        if not yield_due(c, by_id, t):
            continue
        y = dict(c["yield"], deferred=False, delivered=iso(t))
        try:
            write_yield(root, c, y)
            append_jsonl(root.p("history.jsonl"), {"event": "yield-deliver", "ts": iso(t), "id": c["id"],
                                                   "agent": c["agent"], "job": c["job"], "after": y.get("after")})
        except OSError:
            # a full disk, say: the next call on the host tries again
            continue
        out.append(c["id"])
    return out


def yield_candidates(st, claims, want):
    # (claims to ask, claims already asked): the smallest new set that, with the pending yields, makes it fit
    t = st["now"]
    min_age = st["cfg"]["rules"]["yield_min_age_min"] * 60
    pending = [c["id"] for c in claims if c.get("yield")]
    if pending and plan(st, claims, want, exclude=set(pending), lease_check=False)["ok"]:
        return [], pending
    cands = [c for c in claims if c.get("state") == "live" and c.get("preemptible") and not c.get("yield")
             and t - started_ts(c) >= min_age and may_yield(want["class"], want["borrowed"], want["agent"], c, st["cfg"])]
    rank = CLASSES.index
    cands.sort(key=lambda c: (-rank(c.get("class") or "P1"), not c.get("borrowed"), -started_ts(c)))
    # asking a paired claim frees its partners too: they yield with it
    group = dict((c["id"], set(m["id"] for m in yield_group(c, claims))) for c in cands)

    def freed(ids):
        return set(x for i in ids for x in group[i]) | set(pending)

    chosen = []
    for c in cands:
        chosen.append(c["id"])
        if plan(st, claims, want, exclude=freed(chosen), lease_check=False)["ok"]:
            break
    else:
        return [], pending
    for cid in list(reversed(chosen)):
        trial = [x for x in chosen if x != cid]
        if trial and plan(st, claims, want, exclude=freed(trial), lease_check=False)["ok"]:
            chosen.remove(cid)
    return chosen, pending


def eta_start(st, claims, want):
    # minutes until the request fits if claims end at their eta (overdue ones count as ending soon)
    t = st["now"]
    soon = st["cfg"]["rules"]["stale_min"] * 60
    ends = sorted((max(parse_iso(c["eta"]), t + soon), c["id"]) for c in claims if c.get("eta"))
    gone = set()
    for end, cid in ends:
        gone.add(cid)
        if plan(st, claims, want, exclude=gone, lease_check=False)["ok"]:
            return round((end - t) / 60.0, 1)
    return None


def hardware_match(st, want):
    pr, res = st["probe"], st["cfg"]["reserve"]
    out = []
    if want["cores"] > len(pr["cpus"]) - int(res["cores"]):
        out.append("cores: the host has %d" % len(pr["cpus"]))
    if pr["ram_total_gb"] is not None and want["ram_gb"] > pr["ram_total_gb"] - float(res["ram_gb"]):
        out.append("ram: the host has %s" % fmt_gb(pr["ram_total_gb"]))
    if want["gpus"]:
        match = [g for g in pr["gpus"] if gpu_matches(g, want)]
        if len(match) < len(want["gpus"]):
            names = sorted(set(g.get("name") or "?" for g in pr["gpus"])) or ["none"]
            out.append("gpu: %d matching GPU(s), want %d (has %s)" % (len(match), len(want["gpus"]), ", ".join(names)))
    return out


# lease settings and extension requests

def apply_lease(cur, sets, t):
    cur = dict(cur or {})
    for k, v in sets.items():
        if k not in LEASE_KEYS:
            raise Fail("unknown lease key %r (known: %s)" % (k, ", ".join(LEASE_KEYS)), USAGE)
        if v is None or v == "":
            cur.pop(k, None)
            continue
        if k == "kind" and v not in LEASE_KINDS:
            raise Fail("lease kind must be none, free or paid", USAGE)
        if k in ("planned_end", "started"):
            v = iso(parse_when(v, t))
        if k == "usd_per_hour":
            v = float(v)
        cur[k] = v
    cur["kind"] = cur.get("kind") or "none"
    if cur["kind"] == "none":
        cur = dict((k, v) for k, v in cur.items() if k in ("kind", "note"))
    if cur["kind"] == "paid":
        missing = [k for k in ("planned_end", "usd_per_hour") if cur.get(k) in (None, "")]
        if missing:
            raise Fail("a paid lease needs %s" % ", ".join(missing), USAGE)
        cur.setdefault("started", iso(t))
    return cur


def list_requests(root, t):
    out = []
    d = root.p("requests")
    for n in sorted(os.listdir(d)) if os.path.isdir(d) else []:
        if not n.endswith(".json") or n.startswith("."):
            continue
        r = read_json(os.path.join(d, n))
        if not isinstance(r, dict) or not r.get("id"):
            continue
        r.setdefault("type", "extension")
        if r.get("state") == "pending" and parse_iso(r["expires"]) < t:
            r["state"] = "expired"
        out.append(r)
    return out


def expire_requests(root, t):
    out = []
    d = root.p("requests")
    for n in sorted(os.listdir(d)) if os.path.isdir(d) else []:
        path = os.path.join(d, n)
        r = read_json(path) if n.endswith(".json") else None
        if isinstance(r, dict) and r.get("state") == "pending" and parse_iso(r["expires"]) < t:
            r["state"], r["decided"] = "expired", iso(t)
            write_json(path, r)
            append_jsonl(root.p("history.jsonl"), {"event": "request-expired", "ts": iso(t), "request": r["id"]})
            out.append(r["id"])
    return out


# host operations: each takes a request dict and returns a result dict

def op_install(req, src):
    root = Root(req.get("root"))
    agent = req.get("agent")
    if not agent:
        raise Fail("--agent (or the project's slug) is required to install", USAGE)
    valid_name(agent, "agent")
    sim = load_sim(root)
    t = now_of(req, sim)
    s = req.get("settings") or {}
    if not root.installed() and not req.get("root"):
        kind = (sim or {}).get("fs_type") or fs_type(os.path.realpath(root.path))
        if kind in NETWORK_FS:
            raise Fail("%s is on a network filesystem (%s) that other machines may share: set a root on a local disk "
                       "for this host in the inventory (root field)" % (root.path, kind), DENIED)
    fresh_dir = not os.path.isdir(root.path)
    os.makedirs(root.path, exist_ok=True)
    with Lock(root.p(".lock"), HOST_RULES["lock_timeout_s"]):
        raw = read_json(root.p("host.json"), None)
        new = raw is None
        owner = (raw or {}).get("owner") or req.get("inv_owner") or agent
        if agent != owner:
            if new and fresh_dir:
                os.unlink(root.p(".lock"))
                os.rmdir(root.path)
            raise Fail("only the owner (%s) installs or changes hostclaims on this host; guests file a request instead"
                       % owner, DENIED, owner=owner)
        for d in ("claims", "stale", "run", "requests", "pools"):
            os.makedirs(root.p(d), exist_ok=True)
        if new:
            raw = {"schema": SCHEMA, "installed": iso(t), "mode": "shared", "reserve": dict(DEFAULT_RESERVE),
                   "rules": {}, "lease": None}
        here = machine_id(sim)
        if raw.get("machine") and raw["machine"] != here:
            append_jsonl(root.p("history.jsonl"), {"event": "rebind", "ts": iso(t), "from": raw["machine"], "to": here,
                                                   "by": agent})
        raw["machine"] = here
        raw["owner"] = owner
        if s.get("owner") and s["owner"] != owner:
            raw["owner"] = valid_name(s["owner"], "owner")
            append_jsonl(root.p("history.jsonl"), {"event": "owner", "ts": iso(t), "from": owner, "to": s["owner"]})
        if req.get("share_with") is not None:
            raw["share_with"] = [valid_name(x, "share_with entry") if x != "*" else x for x in req["share_with"]]
        elif raw.get("share_with") is None:
            # no sharing file and no --share-with: private to the owner until it says otherwise
            raw["share_with"] = []
        if s.get("mode"):
            m = s["mode"]
            if not (m in ("shared", "draining") or (m.startswith("dedicated:") and NAME_RE.match(m.split(":", 1)[1]))):
                raise Fail("mode must be shared, draining or dedicated:<agent>", USAGE)
            raw["mode"] = m
        for k in ("cores", "ram_gb", "disk_free_pct"):
            v = s.get("reserve_" + k)
            if v is not None:
                raw.setdefault("reserve", {})[k] = int(v) if k == "cores" else float(v)
        if s.get("disk_path"):
            raw["disk_path"] = s["disk_path"]
        if s.get("launcher"):
            if s["launcher"] not in ("tmux", "setsid"):
                raise Fail("launcher must be tmux or setsid", USAGE)
            raw["launcher"] = s["launcher"]
        for k, v in (s.get("rules") or {}).items():
            if k not in HOST_RULES:
                raise Fail("unknown host rule %r (known: %s)" % (k, ", ".join(sorted(HOST_RULES))), USAGE)
            raw.setdefault("rules", {})[k] = float(v)
        if s.get("lease"):
            raw["lease"] = apply_lease(raw.get("lease") or req.get("inv_lease") or {}, s["lease"], t)
        elif new and req.get("inv_lease"):
            raw["lease"] = apply_lease({}, dict((k, v) for k, v in req["inv_lease"].items() if k in LEASE_KEYS), t)
        raw["updated"] = iso(t)
        write_json(root.p("host.json"), raw)
        append_jsonl(root.p("history.jsonl"), {"event": "install", "ts": iso(t), "by": req.get("agent"), "new": new})
    cfg = host_cfg(root)
    st = load_state(root, req, cfg, sim)
    active = classify_all(read_claims(root), st)
    attach_claims(st, active)
    tag = write_status(root, st, active, capacity(st, active))
    pr = st["probe"]
    facts = {"python": sys.version.split()[0], "tmux": shutil.which("tmux"), "taskset": shutil.which("taskset"),
             "nvidia_smi": shutil.which("nvidia-smi"), "cpus": len(pr["cpus"]), "ram_gb": pr["ram_total_gb"],
             "disk_gb": pr["disk_total_gb"], "boot_id": pr["boot_id"],
             "gpus": [{"index": g["index"], "name": g["name"], "mem_total_gb": g["mem_total_gb"]} for g in pr["gpus"]],
             "warnings": pr["warnings"]}
    return {"root": root.path, "new": new, "host": raw, "facts": facts, "tag": tag}


def op_uninstall(req, src):
    root = Root(req.get("root"))
    if not root.installed():
        return {"removed": False, "root": root.path, "note": "not installed"}
    extra = [n for n in os.listdir(root.path) if n not in ROOT_ENTRIES and not n.startswith(".tmp-")]
    if extra:
        raise Fail("refused: %s holds files hostclaims did not create (%s)" % (root.path, ", ".join(sorted(extra)[:5])),
                   DENIED)
    cfg = host_cfg(root)
    check_machine(root, cfg)
    need_owner(cfg, req, "uninstalls hostclaims")
    sim = load_sim(root)
    st = load_state(root, req, cfg, sim, sample=False)
    with root.lock(cfg):
        live = classify_all(read_claims(root), st)
        if live:
            raise Fail("refused: %d live claim(s) on this host (release them or let them end first)" % len(live),
                       DENIED, claims=[c["id"] for c in live])
        holds = []
        for n in sorted(os.listdir(root.p("pools"))) if os.path.isdir(root.p("pools")) else []:
            data = read_json(root.p("pools", n)) if n.endswith(".json") else None
            for name, p in ((data or {}).get("pools") or {}).items():
                pool_usage(p, st["now"])
                holds.extend("%s:%s/%s" % (name, h["agent"], h["id"]) for h in p.get("holds") or [])
        if holds:
            raise Fail("refused: open pool holds under %s (%s)" % (root.p("pools"), ", ".join(holds[:5])), DENIED)
        shutil.rmtree(root.path)
    return {"removed": True, "root": root.path}


def core_overlaps(claims):
    # pairs of claims that hold a core in common: never granted by this tool, but a hand edit or another version can
    out = []
    for i, a in enumerate(claims):
        ca = set((a.get("resources") or {}).get("cores") or [])
        for b in claims[i + 1:]:
            both = ca & set((b.get("resources") or {}).get("cores") or [])
            if both:
                out.append({"claims": sorted([a["id"], b["id"]]), "cores": sorted(both)})
    return out


def gpu_last_ends(hist):
    # per GPU index, the latest end among the claims that held it (a resize is no end)
    out = {}
    for e in hist:
        if e.get("event") not in ("end", "stale") or e.get("reason") == "resized":
            continue
        try:
            end = parse_iso(e["end"])
        except (KeyError, TypeError, ValueError):
            continue
        for g in e.get("gpus") or []:
            i = (g or {}).get("index")
            if i is not None and (i not in out or end >= out[i][0]):
                out[i] = (end, e)
    return dict((i, {"end": iso(end), "claim": e.get("id"), "agent": e.get("agent"), "job": e.get("job"),
                     "reason": e.get("reason")}) for i, (end, e) in out.items())


def op_status(req, src):
    root = Root(req.get("root"))
    if not root.installed():
        return {"installed": False, "root": root.path}
    cfg = host_cfg(root)
    check_machine(root, cfg)
    st = load_state(root, req, cfg, load_sim(root))
    t = st["now"]
    claims = read_claims(root)
    by_id = dict((c["id"], c) for c in claims)
    if any((c.get("yield") or {}).get("deferred") and yield_due(c, by_id, t) for c in claims):
        # a paired claim's turn to yield has come: deliver it now rather than at the next claim on this host
        with root.lock(cfg):
            deliver_due(root, t)
        claims = read_claims(root)
    active = classify_all(claims, st)
    attach_claims(st, active)
    cap = capacity(st, active)
    tag = write_status(root, st, active, cap)
    since = t - cfg["rules"]["recent_hours"] * 3600
    hist = read_jsonl(root.p("history.jsonl"))
    recent = [e for e in hist if e.get("event") in ("end", "stale") and e.get("ts") and parse_iso(e["ts"]) >= since]
    stale = []
    d = root.p("stale")
    for n in sorted(os.listdir(d)) if os.path.isdir(d) else []:
        s = read_json(os.path.join(d, n)) if n.endswith(".json") else None
        if isinstance(s, dict) and parse_iso((s.get("stale") or {}).get("at") or s["created"]) >= since:
            stale.append({"id": s["id"], "agent": s["agent"], "job": s["job"], "stale": s.get("stale")})
    reqs = [r for r in list_requests(root, t)
            if r["state"] == "pending" or parse_iso(r.get("decided") or r["created"]) >= since]
    views = []
    for c in claims:
        v = claim_view(c, t, req.get("agent") or "", cfg["rules"]["stale_min"])
        # memory in use (PSS, or summed RSS where PSS cannot be read) against the claim's declaration
        if c["id"] in st["mem"]:
            v["mem_gb"] = round(st["mem"][c["id"]], 2)
            v["mem_source"] = st["mem_src"].get(c["id"])
        if claim_procs(c):
            v["procs"] = procs_view(c)
        views.append(v)
    free = free_summary(cap)
    # each GPU's latest claim end, so a watcher tells a GPU between short runs from an idle one
    last = gpu_last_ends(hist)
    for g in free["gpus"]:
        e = last.get(g["index"])
        g["last_claim_end"] = e["end"] if e else None
        g["last_claim"] = e
    pinned = [dict(p, prog=prog_name(p["pid"])) for p in st["pinned"] if p["outside"]]
    return {"installed": True, "root": root.path, "now": t, "tag": tag, "mode": cfg["mode"], "lease": st["lease"],
            "owner": host_owner(cfg, req), "share_with": cfg.get("share_with"), "keep_until": cfg.get("keep_until"),
            "boot_id": st["probe"]["boot_id"], "free": free, "outside": outside_use(st, cap),
            "claims": views, "overlaps": core_overlaps(active), "pinned": pinned,
            "unclaimed_gpu_procs": [r for r in st["gpu_rows"] if not any(
                r["claim"] == c["id"] and any(x.get("index") == r["gpu"] for x in (c.get("resources") or {}).get("gpus") or [])
                for c in active)],
            "requests": reqs, "recent": recent[-20:], "stale": stale[-20:], "warnings": st["probe"]["warnings"]}


def recent_run_end(root, agent, job, since):
    last = None
    for e in read_jsonl(root.p("history.jsonl")):
        if e.get("event") == "end" and "exit_code" in e and e.get("agent") == agent and e.get("job") == job:
            try:
                if parse_iso(e["end"]) >= since:
                    last = e
            except (KeyError, ValueError):
                pass
    return last


def new_claim(want, p, st, req, pids, starts, partners=None):
    # the first process given is the job (older tool versions read only that one); more go to pids
    t = st["now"]
    cid = "%s--%s--%s-%s" % (want["agent"], want["job"], time.strftime("%Y%m%dT%H%M%SZ", time.gmtime(t)),
                            secrets.token_hex(2))
    hours = want["hours"]
    c = {"schema": SCHEMA, "id": cid, "agent": want["agent"], "job": want["job"], "class": want["class"],
         "preemptible": want["preemptible"], "borrowed": want["borrowed"],
         "resources": {"cores": p["cores"], "ram_gb": want["ram_gb"], "disk_gb": want["disk_gb"],
                       "gpus": [dict((k, v) for k, v in g.items() if k != "contention") for g in p["gpus"]]},
         "created": iso(t), "heartbeat": iso(t), "hours": hours,
         "eta": iso(t + hours * 3600) if hours else None, "boot_id": st["probe"]["boot_id"],
         "pid": pids[0] if pids else None, "pid_start": starts.get(pids[0]) if pids else None, "launcher": None,
         "controller": req.get("controller"), "brief": req.get("brief"), "note": req.get("note"),
         "yield": None, "warnings": p["warnings"]}
    if len(pids) > 1:
        c["pids"] = [{"pid": x, "pid_start": starts[x], "since": iso(t)} for x in pids[1:]]
    if partners:
        c["yield_with"] = list(partners)
    return c


def refit_claim(c, want, p, st):
    t = st["now"]
    c = dict((k, v) for k, v in c.items() if k not in TRANSIENT)
    c.update({"class": want["class"], "preemptible": want["preemptible"], "borrowed": want["borrowed"],
              "resources": {"cores": p["cores"], "ram_gb": want["ram_gb"], "disk_gb": want["disk_gb"],
                            "gpus": [dict((k, v) for k, v in g.items() if k != "contention") for g in p["gpus"]]},
              "created": iso(t), "heartbeat": iso(t), "hours": want["hours"],
              "eta": iso(t + want["hours"] * 3600) if want["hours"] else None, "warnings": p["warnings"]})
    return c


def pid_holder(pid, claims, procs):
    # the claim a process already belongs to: one of its tied processes or its watcher, or on Linux a member of the
    # tree of one of its tied processes
    for c in claims:
        if pid in [x for x, _ in claim_procs(c)] or pid == (c.get("launcher") or {}).get("pid"):
            return c
    if procs is None or pid not in procs:
        return None
    leaders = dict((x, c) for c in claims for x, _ in claim_procs(c))
    if procs[pid][1] in leaders:
        return leaders[procs[pid][1]]
    p, hops = procs[pid][0], 0
    while p and hops < 64:
        if p in leaders:
            return leaders[p]
        if p not in procs:
            return None
        p, hops = procs[p][0], hops + 1
    return None


def pid_list(v):
    # --pid given once (an older controller sends one number) or repeated
    vals = v if isinstance(v, (list, tuple)) else ([v] if v not in (None, "", 0) else [])
    out = []
    for x in vals:
        try:
            pid = int(x)
        except (TypeError, ValueError):
            raise Fail("bad pid %r" % (x,), USAGE)
        if pid <= 0:
            raise Fail("bad pid %r" % (x,), USAGE)
        if pid not in out:
            out.append(pid)
    return out


def proc_starts(pids):
    # pid -> start time for each running process; a pid that is not running is refused before anything changes
    out = {}
    for pid in pids:
        s = proc_start(pid)
        if s is None:
            raise Fail("no running process %s" % pid)
        out[pid] = s
    return out


def attach_procs(root, st, c, others, pids, starts):
    # tie more processes to the caller's own claim, so their memory counts toward it and the claim lives while any
    # runs: the first becomes the job (pid) of a claim without a watcher and without a live job, the others go to
    # pids; a process another live claim holds is refused. Returns what was attached.
    out = []
    have = dict(claim_procs(c))
    la = c.get("launcher") or {}
    if la.get("pid"):
        # the claim's own watcher belongs to it already
        have[la["pid"]] = la.get("pid_start")
    for pid in pids:
        if have.get(pid) == starts[pid]:
            out.append({"pid": pid, "prog": prog_name(pid), "new": False})
            continue
        holder = pid_holder(pid, others, st["probe"].get("procs"))
        if holder is not None:
            raise Fail("pid %s belongs to claim %s of %s: tie a claim only to your own process"
                       % (pid, holder["id"], holder["agent"]), DENIED)
    t = iso(st["now"])
    for pid in pids:
        if have.get(pid) == starts[pid]:
            continue
        if not c.get("launcher") and not pid_alive(c.get("pid"), c.get("pid_start")):
            c["pid"], c["pid_start"] = pid, starts[pid]
        else:
            c["pids"] = [x for x in c.get("pids") or [] if isinstance(x, dict) and x.get("pid") != pid] + [
                {"pid": pid, "pid_start": starts[pid], "since": t}]
        have[pid] = starts[pid]
        prog = prog_name(pid)
        out.append({"pid": pid, "prog": prog, "new": True})
        append_jsonl(root.p("history.jsonl"), {"event": "tie", "ts": t, "id": c["id"], "agent": c["agent"],
                                               "job": c["job"], "pid": pid, "prog": prog})
    return out


def procs_view(c):
    # the tied processes as status shows them: pid, program name, alive
    return [{"pid": pid, "prog": prog_name(pid), "alive": pid_alive(pid, start)} for pid, start in claim_procs(c)]


def lapsed_claim(root, reaped, want, t, window_s):
    # the newest claim of this agent and job that lapsed (moved to stale/) within window_s, or None: a re-claim takes
    # its cores and GPUs again while they are free, since its job may still run there
    best = None
    for c in reaped:
        if c["agent"] == want["agent"] and c["job"] == want["job"]:
            best = dict(c, stale={"at": iso(t), "reason": c.get("why")})
    if best is None:
        prefix = "%s--%s--" % (want["agent"], want["job"])
        d = root.p("stale")
        for n in sorted(os.listdir(d)) if os.path.isdir(d) else []:
            if not n.startswith(prefix) or not n.endswith(".json"):
                continue
            s = read_json(os.path.join(d, n))
            try:
                at = parse_iso((s.get("stale") or {}).get("at"))
            except (AttributeError, TypeError, ValueError):
                continue
            if isinstance(s, dict) and s.get("agent") == want["agent"] and s.get("job") == want["job"] \
                    and t - at <= window_s and (best is None or at >= parse_iso(best["stale"]["at"])):
                best = s
    return best


def partner_list(v, job):
    # --yield-with: the jobs this claim yields after (None when not given; "none" clears the list)
    if v is None:
        return None
    vals = [x.strip() for item in (v if isinstance(v, (list, tuple)) else [v]) for x in str(item).split(",")]
    out = []
    for x in vals:
        if x.lower() in NONE_WORDS:
            continue
        valid_name(x, "--yield-with job")
        if x == job:
            raise Fail("a claim cannot yield after itself", USAGE)
        if x not in out:
            out.append(x)
    return out


def check_partners(live, agent, job, partners):
    # the yield order may not loop: a claim yields after the jobs it names, and they after theirs
    names = dict((c["job"], list(c.get("yield_with") or [])) for c in live if c["agent"] == agent)
    names[job] = list(partners)
    path, done = [], set()

    def walk(j):
        if j in path:
            raise Fail("the yield order loops (%s): name each pair once, on the claim that yields last"
                       % " after ".join(path[path.index(j):] + [j]), USAGE)
        if j in done:
            return
        path.append(j)
        for k in names.get(j) or []:
            walk(k)
        path.pop()
        done.add(j)

    walk(job)


def launch_job(root, cfg, c, src, req):
    if not src:
        raise Fail("no tool source to start the job's watcher with")
    rd = root.p("run", c["id"])
    os.makedirs(rd, exist_ok=True)
    os.chmod(rd, 0o700)
    sig = req.get("yield_signal") or "TERM"
    if sig not in SIGNALS:
        raise Fail("yield signal must be one of %s" % ", ".join(SIGNALS), USAGE)
    c.update({"run_dir": rd, "cmd": list(req["cmd"]), "cwd": os.path.expanduser(req.get("cwd") or "~"),
              "done_file": req.get("done_file"), "yield_signal": sig,
              "grace_min": float(req.get("grace_min") or cfg["rules"]["yield_grace_min"]),
              "yield_file": os.path.join(rd, "yield.json")})
    with open(os.path.join(rd, "hostclaims.py"), "w") as f:
        f.write(src)
    with open(os.path.join(rd, "job.sh"), "w") as f:
        f.write("#!/bin/sh\n# hostclaims job for claim %s\nexec %s\n" % (c["id"], " ".join(shlex.quote(a) for a in c["cmd"])))
    argv = [sys.executable or "python3", os.path.join(rd, "hostclaims.py"), "_wrap", "--root", root.path,
            "--claim", c["id"]]
    tmux = shutil.which("tmux") if cfg["launcher"] == "tmux" else None
    if tmux:
        session = "hc-%s--%s" % (c["agent"], c["job"])
        errf = os.path.join(rd, "launch.err")
        with open(errf, "w") as ef:
            try:
                rc = subprocess.run([tmux, "new-session", "-d", "-s", session, " ".join(shlex.quote(a) for a in argv)],
                                    stdin=subprocess.DEVNULL, stdout=ef, stderr=ef, timeout=30).returncode
            except (OSError, subprocess.TimeoutExpired) as e:
                rc = str(e)
        if rc != 0:
            with open(errf) as ef:
                raise Fail("tmux new-session failed (%s): %s" % (rc, ef.read().strip()[:300]))
        c["launcher"] = {"kind": "tmux", "session": session}
    else:
        # double fork: the watcher is reparented to init, so nothing here waits on it
        subprocess.run(["/bin/sh", "-c", '"$@" </dev/null >/dev/null 2>&1 &', "hostclaims-launch"] + argv,
                       stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                       start_new_session=True, close_fds=True, cwd=rd, timeout=30)
        c["launcher"] = {"kind": "setsid", "session": None}
    save_claim(root, c)


def op_claim(req, src, launch=False):
    root = open_root(req)
    cfg = host_cfg(root)
    check_machine(root, cfg)
    want = want_from(req)
    valid_name(want["job"], "job")
    if launch and not req.get("cmd"):
        raise Fail("run needs the job's command after --", USAGE)
    partners = partner_list(req.get("yield_with"), want["job"])
    pids = pid_list(req.get("pid"))
    if pids and launch:
        raise Fail("--pid is for claim and renew (run watches the job it starts)", USAGE)
    starts = proc_starts(pids)
    st = load_state(root, req, cfg, load_sim(root))
    created = False
    with root.lock(cfg):
        live, reaped = reap_locked(root, st, read_claims(root))
        mine = [c for c in live if c["agent"] == want["agent"] and c["job"] == want["job"]]
        c = mine[0] if mine else None
        if partners is not None:
            check_partners(live, want["agent"], want["job"], partners)
        if c is not None and not (launch and not c.get("launcher") and not claim_procs(c)):
            # a repeat returns the claim: it renews a bare claim, --pid attaches processes (to a run's claim too),
            # and --yield-with sets the yield order
            attached = attach_procs(root, st, c, [x for x in live if x["id"] != c["id"]], pids, starts)
            changed = any(a["new"] for a in attached)
            if partners is not None and partners != list(c.get("yield_with") or []):
                c["yield_with"], changed = partners, True
            if not c.get("launcher"):
                c["heartbeat"], changed = iso(st["now"]), True
            if changed:
                save_claim(root, c)
            v = claim_view(c, st["now"], stale_min=cfg["rules"]["stale_min"])
            v["procs"] = procs_view(c)
            return {"existing": True, "claim": v, "attached": attached, "reaped": [x["id"] for x in reaped]}
        if c is None and launch and not req.get("rerun"):
            fin = recent_run_end(root, want["agent"], want["job"], st["now"] - cfg["rules"]["rerun_guard_min"] * 60)
            if fin:
                return {"existing": True, "finished": fin,
                        "note": "this job ended at %s; pass --rerun to start it again" % fin["end"]}
        for pid in pids:
            holder = pid_holder(pid, live, st["probe"].get("procs"))
            if holder is not None:
                raise Fail("pid %s belongs to claim %s of %s: tie a claim only to your own process"
                           % (pid, holder["id"], holder["agent"]), DENIED)
        attach_claims(st, live)
        # a claim of this job that lapsed a moment ago: take its cores and GPUs again while they are free
        prev = None
        if c is None:
            prev = lapsed_claim(root, reaped, want, st["now"], 4 * cfg["rules"]["stale_min"] * 60)
        # this job's own processes pinned outside every claim (the lapsed claim's job still running, or a process given
        # with --pid): their cores, and the load on them, are this claim's to take, never another's
        own = own_pinned(st, prev, pids, starts) if c is None else []
        job_cores = sorted(set(x for q in own for x in q["cores"]))
        if own:
            st["pinned"] = [q for q in st["pinned"] if not any(q is o for o in own)]
            st["own_cores"] = job_cores
        prefer = None
        if prev or own:
            res = (prev or {}).get("resources") or {}
            prefer = {"first": job_cores, "cores": res.get("cores") or [],
                      "gpus": [g.get("index") for g in res.get("gpus") or []]}
        # a run over an earlier bare claim re-fits that claim to the run's request
        p = plan(st, live, want, exclude=set([c["id"]]) if c is not None else (), prefer=prefer)
        if not p["ok"]:
            kinds = set(k for k, _ in p["reasons"])
            cands, pending = [], []
            if c is None and kinds <= set(["cores", "ram", "disk", "gpu"]) and not p["cap"]["below_floor"]:
                cands, pending = yield_candidates(st, live, want)
            write_status(root, st, live, p["cap"])
            raise Fail("does not fit on this host" + ("" if c is None else " at the run's size (claim %s keeps "
                                                                          "its earlier size)" % c["id"]), NOFIT,
                       reasons=[m for _, m in p["reasons"]], yield_candidates=cands, yields_pending=pending,
                       free=free_summary(p["cap"]), reaped=[x["id"] for x in reaped])
        # never grant a core a live claim holds, whatever the planning above concluded
        taken = dict((x, o["id"]) for o in live if c is None or o["id"] != c["id"]
                     for x in (o.get("resources") or {}).get("cores") or [])
        clash = sorted(x for x in p["cores"] if x in taken)
        if clash:
            raise Fail("refused: cores %s are held by claim %s; nothing was claimed" % (
                fmt_cores(clash), ", ".join(sorted(set(taken[x] for x in clash)))), ERROR)
        warnings = p["warnings"]
        before = c
        if c is None:
            c = new_claim(want, p, st, req, pids, starts, partners)
            created = True
            for pid in pids:
                append_jsonl(root.p("history.jsonl"), {"event": "tie", "ts": iso(st["now"]), "id": c["id"],
                                                       "agent": c["agent"], "job": c["job"], "pid": pid})
        else:
            append_jsonl(root.p("history.jsonl"), usage_event("end", c, st["now"], st["now"], reason="resized"))
            c = refit_claim(c, want, p, st)
            if partners is not None:
                c["yield_with"] = partners
        save_claim(root, c)
        append_jsonl(root.p("history.jsonl"), {"event": "claim", "ts": iso(st["now"]), "id": c["id"],
                                               "agent": c["agent"], "job": c["job"], "class": c["class"],
                                               "resources": c["resources"], "hours": c.get("hours")})
        if launch:
            try:
                launch_job(root, cfg, c, src, req)
            except Exception:
                if created:
                    remove_claim(root, st, c, "launch-failed")
                elif before is not None:
                    # a failed launch after a re-fit puts the bare claim back at its earlier size
                    append_jsonl(root.p("history.jsonl"), usage_event("end", c, st["now"], st["now"],
                                                                      reason="launch-failed"))
                    back = dict((k, v) for k, v in before.items() if k not in TRANSIENT)
                    back.update({"created": iso(st["now"]), "heartbeat": iso(st["now"])})
                    save_claim(root, back)
                    append_jsonl(root.p("history.jsonl"), {"event": "claim", "ts": iso(st["now"]), "id": back["id"],
                                                           "agent": back["agent"], "job": back["job"],
                                                           "class": back.get("class"), "resources": back["resources"],
                                                           "hours": back.get("hours"), "reason": "restored"})
                raise
        st["own_cores"] = []
        others = [x for x in live if x["id"] != c["id"]] + [c]
        attach_claims(st, others)
        write_status(root, st, others, capacity(st, others))
    out = {"existing": False, "claim": claim_view(c, st["now"], stale_min=cfg["rules"]["stale_min"]), "warnings": warnings,
           "reaped": [x["id"] for x in reaped]}
    if pids:
        out["claim"]["procs"] = procs_view(c)
    granted = set(c["resources"]["cores"])
    if prev:
        before_cores = sorted((prev.get("resources") or {}).get("cores") or [])
        # kept: the job still runs on cores of this claim (where it runs pinned, else where its claim was)
        out["lapsed"] = {"id": prev["id"], "at": (prev.get("stale") or {}).get("at"), "cores_before": before_cores,
                         "job_cores": job_cores, "cores": c["resources"]["cores"],
                         "kept": set(job_cores or before_cores) <= granted}
    elif job_cores:
        out["pinned_job"] = {"cores": job_cores, "kept": set(job_cores) <= granted}
    if launch:
        deadline = time.time() + 8.0
        while time.time() < deadline:
            cur = read_json(claim_path(root, c["id"]))
            if cur is None or cur.get("pid"):
                break
            time.sleep(0.1)
        cur = read_json(claim_path(root, c["id"]))
        if cur:
            out["claim"] = claim_view(cur, st["now"])
        out["done"] = read_json(os.path.join(c["run_dir"], "done.json"))
    return out


def op_run(req, src):
    return op_claim(req, src, launch=True)


def op_renew(req, src):
    # the caller's own live claims, changed in place without a release: a new end (eta), preemptible or borrowed,
    # more processes, the yield order; a bare claim's heartbeat is refreshed too
    root = open_root(req)
    cfg = host_cfg(root)
    check_machine(root, cfg)
    agent = req.get("agent")
    if not agent:
        raise Fail("--agent (or HOSTCLAIMS_AGENT) is required", USAGE)
    valid_name(agent, "agent")
    every = bool(req.get("all"))
    if every == bool(req.get("job")):
        raise Fail("pass --job, or --all for every claim of yours on the host", USAGE)
    if not every:
        valid_name(req.get("job"), "job")
    if req.get("hours") is not None and req.get("until"):
        raise Fail("pass --hours or --until, not both", USAGE)
    pids = pid_list(req.get("pid"))
    if every and (pids or req.get("yield_with") is not None):
        raise Fail("--pid and --yield-with change one claim: pass --job", USAGE)
    starts = proc_starts(pids)
    st = load_state(root, req, cfg, load_sim(root), sample=False)
    t = st["now"]
    end = None
    if req.get("until"):
        end = parse_when(req["until"], t)
    elif req.get("hours") is not None:
        end = t + float(req["hours"]) * 3600
    if end is not None and end <= t:
        raise Fail("the new end must be in the future", USAGE)
    warnings = []
    lease = st["lease"]
    if end is not None and lease.get("kind") in ("paid", "free") and lease.get("planned_end"):
        planned = parse_iso(lease["planned_end"])
        if lease["kind"] == "paid" and end > planned + 1:
            raise Fail("does not fit: the new end is past the host's planned end", NOFIT,
                       reasons=["lease: would run past the planned end %s; ask its owner with `extend`"
                                % lease["planned_end"]])
        if lease["kind"] == "free" and end > planned:
            warnings.append("the lease ends %s, before this claim's new end; its owner must renew it (ask with "
                            "extend)" % lease["planned_end"])
    with root.lock(cfg):
        live, reaped = reap_locked(root, st, read_claims(root))
        mine = [c for c in live if c["agent"] == agent and (every or c["job"] == req.get("job"))]
        if not mine:
            gone = [c["id"] for c in reaped if c["agent"] == agent and (every or c["job"] == req.get("job"))]
            raise Fail("no live claim of %s%s on this host%s" % (
                agent, "" if every else " for job %s" % req.get("job"),
                ("; it lapsed just now and was moved to stale/ (%s): claim it again" % ", ".join(gone)) if gone else ""))
        # every change is checked before any claim is written
        plans = []
        for c in mine:
            ch = {}
            if end is not None and c.get("eta") != iso(end):
                ch["eta"] = [c.get("eta"), iso(end)]
            cls = c.get("class") or "P1"
            pre = req.get("preemptible")
            if pre is not None and bool(pre) != bool(c.get("preemptible")):
                if cls == "P0" and pre:
                    raise Fail("claim %s is P0, and P0 claims are never preemptible" % c["id"], USAGE)
                if cls == "P3" and not pre:
                    raise Fail("claim %s is P3, and P3 claims are always preemptible" % c["id"], USAGE)
                ch["preemptible"] = [bool(c.get("preemptible")), bool(pre)]
            bor = req.get("borrowed")
            if bor is not None and bool(bor) != bool(c.get("borrowed")):
                ch["borrowed"] = [bool(c.get("borrowed")), bool(bor)]
            if any(ch[k][0] and not ch[k][1] for k in ("preemptible", "borrowed") if k in ch):
                # dropping a flag is a fresh admission: on a host dedicated to another project a claim stays
                # preemptible and borrowed, so its owner may still ask it to yield
                refused = admission(cfg, agent, ch.get("borrowed", [0, bool(c.get("borrowed"))])[1],
                                    ch.get("preemptible", [0, bool(c.get("preemptible"))])[1], new=False)
                if refused:
                    raise Fail("refused for claim %s: %s" % (c["id"], "; ".join(m for _, m in refused)), DENIED)
            partners = partner_list(req.get("yield_with"), c["job"])
            if partners is not None and partners != list(c.get("yield_with") or []):
                check_partners(live, agent, c["job"], partners)
                ch["yield_with"] = [list(c.get("yield_with") or []), partners]
            plans.append((c, ch))
        out = []
        for c, ch in plans:
            if "eta" in ch:
                c["eta"] = ch["eta"][1]
                c["hours"] = round((end - parse_iso(c["created"])) / 3600.0, 4)
            for k in ("preemptible", "borrowed", "yield_with"):
                if k in ch:
                    c[k] = ch[k][1]
            attached = attach_procs(root, st, c, [x for x in live if x["id"] != c["id"]], pids, starts) if pids else []
            if any(a["new"] for a in attached):
                ch["pids"] = [a["pid"] for a in attached if a["new"]]
            if not c.get("launcher"):
                c["heartbeat"] = iso(t)
            c["renewed"] = iso(t)
            save_claim(root, c)
            append_jsonl(root.p("history.jsonl"), {"event": "renew", "ts": iso(t), "id": c["id"], "agent": agent,
                                                   "job": c["job"], "changes": ch})
            v = claim_view(c, t, stale_min=cfg["rules"]["stale_min"])
            v["procs"] = procs_view(c)
            out.append({"claim": v, "changes": ch, "attached": attached})
        deliver_due(root, t)
    return {"renewed": out, "warnings": warnings, "reaped": [x["id"] for x in reaped]}


def write_yield(root, c, y):
    rd = root.p("run", c["id"])
    os.makedirs(rd, exist_ok=True)
    write_json(os.path.join(rd, "yield.json"), y)
    c["yield"] = y
    save_claim(root, c)


def find_claim(claims, req):
    cid = req.get("claim")
    for c in claims:
        if (cid and c["id"] == cid) or (not cid and c["agent"] == req.get("agent") and c["job"] == req.get("job")):
            return c
    return None


def op_release(req, src):
    root = open_root(req)
    cfg = host_cfg(root)
    check_machine(root, cfg)
    agent = req.get("agent")
    if not agent:
        raise Fail("--agent (or HOSTCLAIMS_AGENT) is required", USAGE)
    st = load_state(root, req, cfg, load_sim(root))
    with root.lock(cfg):
        c = find_claim(read_claims(root), req)
        if c is None:
            raise Fail("no live claim %s" % (req.get("claim") or "%s/%s" % (agent, req.get("job"))))
        if c["agent"] != agent:
            raise Fail("claim %s belongs to %s: never release another agent's claim (reap moves stale ones)"
                       % (c["id"], c["agent"]), DENIED)
        la = c.get("launcher") or {}
        launcher = pid_alive(la.get("pid"), la.get("pid_start"))
        running = live_procs(c)
        job = bool(running)
        grace = float(c.get("grace_min") or cfg["rules"]["yield_grace_min"])
        if launcher and c.get("pid") and not pid_alive(c.get("pid"), c.get("pid_start")):
            return {"ending": True, "claim": claim_view(c, st["now"]),
                    "note": "the job has ended; its launcher releases the claim in a moment"}
        if job or launcher:
            if not req.get("stop"):
                how = ("TERM, then KILL after %g min" % grace) if launcher else "TERM to its process group"
                raise Fail("the job is still running (pid %s); pass --stop to stop it (%s) or wait for it to end"
                           % (", ".join(str(p) for p, _ in running) or c.get("pid"), how), DENIED)
            if launcher:
                write_yield(root, c, {"kind": "stop", "by_agent": agent, "by_job": c["job"], "class": c.get("class"),
                                      "reason": req.get("reason") or "stopped by its agent",
                                      "requested": iso(st["now"]), "acked": None, "grace_min": grace})
                return {"stopping": True, "claim": claim_view(c, st["now"]),
                        "note": "its launcher stops the job and releases the claim"}
            # no watcher (an orphan, or a claim tied with --pid): signal each tied process's group, release once
            # they are gone
            for pid, _ in running:
                signal_group(int(pid), signal.SIGTERM)
            deadline = time.time() + 10.0
            while time.time() < deadline and live_procs(c):
                time.sleep(0.2)
            left = live_procs(c)
            if left:
                return {"stopping": True, "claim": claim_view(c, st["now"]),
                        "note": "TERM sent to pid %s and its group; the claim stays until it exits (release again, "
                                "or it goes stale %g min after)" % (", ".join(str(p) for p, _ in left),
                                                                    cfg["rules"]["stale_min"])}
        remove_claim(root, st, c, "stopped" if job else ("yielded" if (c.get("yield") or {}).get("kind") == "yield"
                                                         and not (c.get("yield") or {}).get("deferred") else "released"))
        deliver_due(root, st)
        live = classify_all(read_claims(root), st)
        attach_claims(st, live)
        write_status(root, st, live, capacity(st, live))
    return {"released": c["id"]}


def op_reap(req, src):
    root = Root(req.get("root"))
    if not root.installed():
        return {"installed": False, "reaped": [], "orphans": [], "live": 0, "expired_requests": []}
    cfg = host_cfg(root)
    check_machine(root, cfg)
    st = load_state(root, req, cfg, load_sim(root))
    pruned = []
    with root.lock(cfg):
        live, moved = reap_locked(root, st, read_claims(root))
        expired = expire_requests(root, st["now"])
        # the owner also prunes finished run folders (logs, done markers) past run_keep_days
        if req.get("agent") and req.get("agent") == host_owner(cfg, req):
            for path in old_runs(root, st["now"], cfg["rules"]["run_keep_days"], set(c["id"] for c in live)):
                shutil.rmtree(path, ignore_errors=True)
                pruned.append(os.path.basename(path))
            if pruned:
                append_jsonl(root.p("history.jsonl"), {"event": "prune", "ts": iso(st["now"]), "runs": pruned})
        attach_claims(st, live)
        write_status(root, st, live, capacity(st, live))
    return {"reaped": [{"id": c["id"], "agent": c["agent"], "job": c["job"], "reason": c["why"]} for c in moved],
            "orphans": [{"id": c["id"], "agent": c["agent"], "job": c["job"], "pid": c.get("pid")}
                        for c in live if c["state"] == "orphan"],
            "live": len(live), "expired_requests": expired, "pruned_runs": pruned}


def op_yield(req, src):
    root = open_root(req)
    cfg = host_cfg(root)
    check_machine(root, cfg)
    agent = req.get("agent")
    if not agent:
        raise Fail("--agent (or HOSTCLAIMS_AGENT) is required", USAGE)
    cls = str(req.get("class") or "").upper()
    if cls not in CLASSES:
        raise Fail("--class (the asking job's class, P0-P3) is required", USAGE)
    target = valid_id(req.get("claim"), "claim id")
    st = load_state(root, req, cfg, load_sim(root), sample=False)
    with root.lock(cfg):
        claims = read_claims(root)
        c = find_claim(claims, {"claim": target})
        if c is None:
            raise Fail("no live claim %s" % target)
        state, why = classify(c, st)
        if state == "stale":
            raise Fail("claim %s is stale (%s); reap it instead" % (target, why), DENIED)
        if c.get("yield"):
            return {"existing": True, "yield": c["yield"], "claim": claim_view(c, st["now"], agent)}
        # only an agent that could claim here may ask others to make room
        refused = admission(cfg, agent, bool(req.get("borrowed")), True)
        if refused:
            raise Fail("you may not ask for room here: " + "; ".join(m for _, m in refused), DENIED)
        mode = cfg["mode"]
        if mode.startswith("dedicated:") and c["agent"] == mode.split(":", 1)[1] and agent != c["agent"]:
            raise Fail("this host is dedicated to %s: other projects never preempt its jobs" % c["agent"], DENIED)
        if not c.get("preemptible"):
            raise Fail("claim %s is not preemptible: only its own agent stops it" % target, DENIED)
        age = st["now"] - started_ts(c)
        min_age = cfg["rules"]["yield_min_age_min"] * 60
        if age < min_age:
            raise Fail("claim %s is %d min old; jobs younger than %g min are not asked to yield"
                       % (target, age // 60, min_age / 60), DENIED)
        if not may_yield(cls, bool(req.get("borrowed")), agent, c, cfg):
            raise Fail("a %s request may ask only lower classes, or same-class borrowed claims, to yield (target: %s%s)"
                       % (cls, c.get("class"), ", borrowed" if c.get("borrowed") else ""), DENIED)
        y = {"kind": "yield", "by_agent": agent, "by_job": req.get("job"), "class": cls,
             "borrowed": bool(req.get("borrowed")), "reason": req.get("reason") or "", "requested": iso(st["now"]),
             "acked": None, "grace_min": float(c.get("grace_min") or cfg["rules"]["yield_grace_min"])}
        # paired claims (yield_with) yield together: a claim gets the request once the partners it names have ended
        # or had their grace, so a server outlives the queue that calls it
        live = [x for x in claims if x["id"] == c["id"] or classify(x, st)[0] != "stale"]
        group = yield_group(c, live)
        sent = []
        for m in sorted(group, key=lambda x: (x["id"] != c["id"], x["id"])):
            if m.get("yield"):
                continue
            ym = dict(y) if m is c else dict(y, via=c["id"],
                                              grace_min=float(m.get("grace_min") or cfg["rules"]["yield_grace_min"]))
            after = [x["id"] for x in group if x["id"] != m["id"] and x["job"] in (m.get("yield_with") or [])]
            if after:
                ym.update(after=after, deferred=True)
                m["yield"] = ym
                save_claim(root, m)
            else:
                write_yield(root, m, ym)
            ev = {"event": "yield-request", "ts": iso(st["now"]), "id": m["id"], "agent": m["agent"], "job": m["job"],
                  "by_agent": agent, "by_job": req.get("job"), "class": cls, "reason": y["reason"]}
            if m is not c:
                ev["via"] = c["id"]
            if after:
                ev["after"] = after
            append_jsonl(root.p("history.jsonl"), ev)
            sent.append({"id": m["id"], "job": m["job"], "deferred": bool(after), "after": after})
        y = c["yield"]
    note = None
    if state == "orphan":
        note = "its launcher is gone, so nothing sends a signal: the job must watch its yield file"
    elif not c.get("launcher"):
        note = "a claim without a launcher: its agent sees the request in status and stops the job itself"
    return {"yield": y, "claim": claim_view(c, st["now"], agent), "note": note, "group": sent}


def usage_from(root, t, window):
    # per agent: GPU-hours by GPU name (share x hours) and CPU-hours (cores x hours) inside the window
    t0 = t - window * 3600
    agents = {}

    def add(agent, start, end, gpus, ncores):
        a = agents.setdefault(agent, {"gpu_hours": {}, "cpu_hours": 0.0, "claims": 0})
        s, e = max(start, t0), min(end, t)
        if e <= s:
            return
        h = (e - s) / 3600.0
        for g in gpus:
            key = g.get("name") or "GPU"
            a["gpu_hours"][key] = a["gpu_hours"].get(key, 0.0) + float(g.get("share") or 0.0) * h
        a["cpu_hours"] += ncores * h
        a["claims"] += 1

    for e in read_jsonl(root.p("history.jsonl")):
        if e.get("event") in ("end", "stale"):
            try:
                add(e["agent"], parse_iso(e["start"]), parse_iso(e["end"]), e.get("gpus") or [], int(e.get("cores") or 0))
            except (KeyError, ValueError):
                pass
    for c in read_claims(root):
        res = c.get("resources") or {}
        add(c["agent"], parse_iso(c["created"]), t, res.get("gpus") or [], len(res.get("cores") or []))
    for a in agents.values():
        a["cpu_hours"] = round(a["cpu_hours"], 3)
        a["gpu_hours"] = dict((k, round(v, 3)) for k, v in a["gpu_hours"].items())
    return agents


def op_usage(req, src):
    root = Root(req.get("root"))
    if not root.installed():
        return {"installed": False, "agents": {}}
    check_machine(root, host_cfg(root))
    t = now_of(req, load_sim(root))
    window = float(req.get("window_hours") or 24.0)
    return {"window_hours": window, "now": t, "agents": usage_from(root, t, window)}


def op_lease(req, src):
    root = open_root(req)
    cfg = host_cfg(root)
    check_machine(root, cfg)
    t = now_of(req, load_sim(root))
    sets = req.get("set") or {}
    if sets:
        need_owner(cfg, req, "changes the lease (renewals, extensions)")
        with root.lock(cfg):
            raw = read_json(root.p("host.json"), {}) or {}
            raw["lease"] = apply_lease(raw.get("lease") or req.get("inv_lease") or {}, sets, t)
            write_json(root.p("host.json"), raw)
            append_jsonl(root.p("history.jsonl"), {"event": "lease", "ts": iso(t), "by": req.get("agent"),
                                                   "lease": raw["lease"]})
        cfg = host_cfg(root)
    source = "host" if cfg.get("lease") else ("inventory" if req.get("inv_lease") else "default")
    return {"lease": effective_lease(cfg, req.get("inv_lease")), "source": source, "now": t}


def op_extend(req, src):
    root = open_root(req)
    cfg = host_cfg(root)
    check_machine(root, cfg)
    want = want_from(req)
    valid_name(want["job"], "job")
    if want["hours"] is None:
        raise Fail("--hours is required", USAGE)
    st = load_state(root, req, cfg, load_sim(root))
    lease, t = st["lease"], st["now"]
    if lease.get("kind") not in ("paid", "free") or not lease.get("planned_end"):
        raise Fail("this host has no planned end (lease kind %s): claim it directly" % lease.get("kind"), USAGE)
    with root.lock(cfg):
        live, _ = reap_locked(root, st, read_claims(root))
        for r in list_requests(root, t):
            if (r["type"] == "extension" and r["agent"] == want["agent"] and r["job"] == want["job"]
                    and r["state"] == "pending"):
                return {"existing": True, "request": r}
        attach_claims(st, live)
        p = plan(st, live, want, lease_check=False)
        if not p["ok"]:
            raise Fail("the job does not fit here now: " + "; ".join(m for _, m in p["reasons"]), NOFIT)
        end = parse_iso(lease["planned_end"])
        needed = t + want["hours"] * 3600 + float(req.get("margin_min") or 0.0) * 60
        extra = (needed - end) / 3600.0
        if extra <= 0:
            return {"needed": False, "planned_end": lease["planned_end"],
                    "note": "the job ends before the planned end: claim it directly"}
        rate = float(lease.get("usd_per_hour") or 0.0) if lease["kind"] == "paid" else 0.0
        rid = "r-%s--%s--%s-%s" % (want["agent"], want["job"], time.strftime("%Y%m%dT%H%M%SZ", time.gmtime(t)),
                                  secrets.token_hex(2))
        timeout = cfg["rules"]["request_timeout_min"]
        r = {"schema": SCHEMA, "id": rid, "type": "extension", "state": "pending", "kind": lease["kind"],
             "owner": host_owner(cfg, req), "instance": lease.get("instance"),
             "gpu_type": lease.get("gpu_type"), "host": req.get("host_name"), "agent": want["agent"],
             "job": want["job"], "class": want["class"], "reason": req.get("reason"), "created": iso(t),
             "expires": iso(t + timeout * 60), "hours": want["hours"], "planned_end": lease["planned_end"],
             "needed_until": iso(needed), "extra_hours": round(extra, 2), "usd_per_hour": rate,
             "est_extra_usd": round(extra * rate, 2),
             "fit": {"gpus": p["gpus"], "cores": len(p["cores"]), "ram_gb": want["ram_gb"],
                     "disk_gb": want["disk_gb"], "free": free_summary(p["cap"])},
             "new_instance": req.get("new_instance")}
        os.makedirs(root.p("requests"), exist_ok=True)
        write_json(root.p("requests", rid + ".json"), r)
        append_jsonl(root.p("history.jsonl"), {"event": "request", "ts": iso(t), "request": rid, "agent": want["agent"],
                                               "job": want["job"], "extra_hours": r["extra_hours"],
                                               "est_extra_usd": r["est_extra_usd"]})
    return {"request": r}


def op_request(req, src):
    # a guest asks the owner for maintenance (reboot, driver, disk) or objects to a release before a time
    root = open_root(req)
    cfg = host_cfg(root)
    check_machine(root, cfg)
    agent = req.get("agent")
    if not agent:
        raise Fail("--agent (or the project's slug) is required", USAGE)
    valid_name(agent, "agent")
    kind = req.get("type")
    if kind not in ("maintenance", "objection"):
        raise Fail("request type must be maintenance or objection (use extend for more time)", USAGE)
    reason = (req.get("reason") or "").strip()
    if not reason:
        raise Fail("--reason is required: say what you need and why", USAGE)
    t = now_of(req, load_sim(root))
    until = None
    if kind == "objection":
        if not req.get("until"):
            raise Fail("an objection needs --until: keep the host until when", USAGE)
        until = parse_when(req["until"], t)
        if until <= t:
            raise Fail("--until must be in the future", USAGE)
    with root.lock(cfg):
        for r in list_requests(root, t):
            if r["type"] == kind and r["agent"] == agent and r["state"] == "pending" and r.get("reason") == reason:
                return {"existing": True, "request": r}
        rid = "r-%s--%s--%s-%s" % (agent, kind, time.strftime("%Y%m%dT%H%M%SZ", time.gmtime(t)), secrets.token_hex(2))
        r = {"schema": SCHEMA, "id": rid, "type": kind, "state": "pending", "owner": host_owner(cfg, req),
             "host": req.get("host_name"), "agent": agent, "job": req.get("job"), "reason": reason,
             "created": iso(t), "expires": iso(until if until else t + 24 * 3600),
             "until": iso(until) if until else None}
        os.makedirs(root.p("requests"), exist_ok=True)
        write_json(root.p("requests", rid + ".json"), r)
        append_jsonl(root.p("history.jsonl"), {"event": "request", "ts": iso(t), "request": rid, "type": kind,
                                               "agent": agent})
    return {"request": r}


def op_withdraw(req, src):
    # the requesting project takes back its own pending request (for example after launching elsewhere)
    root = open_root(req)
    cfg = host_cfg(root)
    check_machine(root, cfg)
    agent = req.get("agent")
    if not agent:
        raise Fail("--agent (or the project's slug) is required", USAGE)
    valid_name(agent, "agent")
    rid = valid_id(req.get("request"), "request id")
    t = now_of(req, load_sim(root))
    path = root.p("requests", rid + ".json")
    with root.lock(cfg):
        r = read_json(path)
        if not r:
            raise Fail("no request %s" % rid)
        if r.get("agent") != agent:
            raise Fail("only the requesting project (%s) may withdraw request %s; the owner declines instead"
                       % (r.get("agent"), rid), DENIED)
        if r["state"] == "pending" and parse_iso(r["expires"]) < t:
            r["state"], r["decided"] = "expired", iso(t)
            write_json(path, r)
        if r["state"] != "pending":
            raise Fail("request %s is already %s" % (rid, r["state"]), DENIED, request=r)
        r["state"], r["decided"], r["withdrawn_by"] = "withdrawn", iso(t), agent
        write_json(path, r)
        append_jsonl(root.p("history.jsonl"), {"event": "request-withdrawn", "ts": iso(t), "request": rid, "by": agent})
    return {"request": r}


def op_decide(req, src):
    root = open_root(req)
    cfg = host_cfg(root)
    check_machine(root, cfg)
    t = now_of(req, load_sim(root))
    rid = valid_id(req.get("request"), "request id")
    decision = req.get("decision")
    if decision not in ("approve", "decline"):
        raise Fail("decision must be approve or decline", USAGE)
    path = root.p("requests", rid + ".json")
    with root.lock(cfg):
        r = read_json(path)
        if not r:
            raise Fail("no request %s" % rid)
        owner = host_owner(cfg, req)
        if not owner or req.get("agent") != owner:
            raise Fail("only the host's owner (%s) may approve or decline requests" % owner, DENIED)
        if r["state"] == "pending" and parse_iso(r["expires"]) < t:
            r["state"], r["decided"] = "expired", iso(t)
            write_json(path, r)
        if r["state"] != "pending":
            raise Fail("request %s is already %s" % (rid, r["state"]), DENIED, request=r)
        kind = r.get("type") or "extension"
        if decision == "approve" and kind == "extension":
            raw = read_json(root.p("host.json"), {}) or {}
            nl = dict(raw.get("lease") or effective_lease(cfg, req.get("inv_lease")))
            if not nl.get("planned_end"):
                raise Fail("the lease has no planned end any more; nothing to extend (decline the request)", DENIED)
            end = max(parse_iso(nl["planned_end"]), parse_iso(r["needed_until"]))
            nl["planned_end"] = iso(end)
            raw["lease"] = nl
            write_json(root.p("host.json"), raw)
            r["state"], r["new_planned_end"] = "approved", iso(end)
        elif decision == "approve" and kind == "objection":
            raw = read_json(root.p("host.json"), {}) or {}
            keep = parse_iso(r["until"])
            if raw.get("keep_until"):
                keep = max(keep, parse_iso(raw["keep_until"]))
            raw["keep_until"] = iso(keep)
            write_json(root.p("host.json"), raw)
            r["state"] = "approved"
        elif decision == "approve":
            r["state"] = "approved"
        else:
            r["state"], r["decline_reason"] = "declined", req.get("reason")
        r["decided_by"], r["decided"] = req.get("agent"), iso(t)
        write_json(path, r)
        append_jsonl(root.p("history.jsonl"), {"event": "request-" + r["state"], "ts": iso(t), "request": rid,
                                               "by": req.get("agent")})
    return {"request": r, "lease": effective_lease(host_cfg(root), req.get("inv_lease"))}


def op_probe(req, src):
    root = Root(req.get("root"))
    if not root.installed():
        return {"installed": False}
    cfg = host_cfg(root)
    check_machine(root, cfg)
    st = load_state(root, req, cfg, load_sim(root))
    want = want_from(req, need_agent=False)
    active = classify_all(read_claims(root), st)
    attach_claims(st, active)
    cap = capacity(st, active)
    p = plan(st, active, want, lease_check=False)
    start = 0.0 if p["ok"] else eta_start(st, active, want)
    # extension requests for this job: pending ones, and answers from the last recent_hours
    since = st["now"] - cfg["rules"]["recent_hours"] * 3600
    reqs = [r for r in list_requests(root, st["now"])
            if r["type"] == "extension" and (not want["agent"] or r["agent"] == want["agent"])
            and (not want["job"] or r.get("job") == want["job"])
            and (r["state"] == "pending" or parse_iso(r.get("decided") or r["created"]) >= since)]
    return {"installed": True, "now": st["now"], "lease": st["lease"], "mode": cfg["mode"],
            "owner": host_owner(cfg, req), "tag": host_tag(st, active, cap), "free": free_summary(cap), "fits_now": p["ok"],
            "reasons": [m for _, m in p["reasons"]], "reason_kinds": sorted(set(k for k, _ in p["reasons"])),
            "start_in_min": start, "hw_reasons": hardware_match(st, want),
            "plan": {"gpus": p["gpus"], "cores": len(p["cores"])},
            "contention": round(sum(g.get("contention", 0.0) for g in p["gpus"]), 4),
            "gpu_types": sorted(set(g.get("name") or "?" for g in st["probe"]["gpus"])), "requests": reqs}


def last_activity(root, claims, cfg):
    last = parse_iso(cfg["raw"].get("installed") or iso(0))
    for e in read_jsonl(root.p("history.jsonl")):
        try:
            if e.get("event") in ("end", "stale"):
                last = max(last, parse_iso(e["end"]))
            elif e.get("event") == "claim":
                last = max(last, parse_iso(e["ts"]))
        except (KeyError, ValueError):
            pass
    for c in claims:
        last = max(last, parse_iso(c.get("heartbeat") or c["created"]))
    return last


def op_audit(req, src):
    # the owner's view: lease, idle time, usage by project, cost so far, open requests
    root = Root(req.get("root"))
    if not root.installed():
        return {"installed": False}
    cfg = host_cfg(root)
    check_machine(root, cfg)
    st = load_state(root, req, cfg, load_sim(root))
    t = st["now"]
    active = classify_all(read_claims(root), st)
    attach_claims(st, active)
    cap = capacity(st, active)
    window = float(req.get("window_hours") or AUDIT_DEFAULTS["window_hours"])
    lease = st["lease"]
    cost = None
    if lease.get("kind") == "paid" and lease.get("usd_per_hour"):
        start = lease.get("started") or cfg["raw"].get("installed")
        if start:
            cost = round(float(lease["usd_per_hour"]) * max(0.0, t - parse_iso(start)) / 3600.0, 2)
    last = last_activity(root, active, cfg)
    outside = outside_use(st, cap)
    return {"installed": True, "now": t, "owner": host_owner(cfg, req), "share_with": cfg.get("share_with"),
            "lease": lease, "mode": cfg["mode"], "keep_until": cfg.get("keep_until"), "tag": host_tag(st, active, cap),
            "live_claims": [{"id": c["id"], "agent": c["agent"], "job": c["job"], "state": c["state"],
                             "class": c.get("class"), "eta": c.get("eta")} for c in active],
            "last_activity": iso(last), "idle_s": 0 if (active or outside["any"]) else max(0, int(t - last)),
            "outside": outside, "usage": usage_from(root, t, window), "window_hours": window, "cost_so_far_usd": cost,
            "requests": [r for r in list_requests(root, t) if r["state"] == "pending"], "free": free_summary(cap),
            "old_runs": len(old_runs(root, t, cfg["rules"]["run_keep_days"], set(c["id"] for c in active))),
            "run_keep_days": cfg["rules"]["run_keep_days"]}


def old_runs(root, t, days, keep_ids):
    # finished run folders older than days (by done.json's end, else their newest file), never a live claim's
    out = []
    d = root.p("run")
    for n in sorted(os.listdir(d)) if os.path.isdir(d) else []:
        path = os.path.join(d, n)
        if n in keep_ids or not os.path.isdir(path) or os.path.exists(claim_path(root, n)):
            continue
        done = read_json(os.path.join(path, "done.json"))
        try:
            if done and done.get("ended"):
                end = parse_iso(done["ended"])
            else:
                end = max([os.path.getmtime(path)] + [os.path.getmtime(os.path.join(path, f)) for f in os.listdir(path)])
        except (OSError, ValueError):
            continue
        if t - end > float(days) * 86400:
            out.append(path)
    return out


# pools: named shared counters with per-agent caps in one coordination file

def pool_cap(p, agent):
    caps = p.get("caps") or {}
    if agent in caps:
        return float(caps[agent])
    if "*" in caps:
        return float(caps["*"])
    return float(p.get("capacity") or 0.0)


def pool_usage(p, t):
    # expire holds past their end; budget pools (window_hours > 0) keep closed holds for the window
    window = float(p.get("window_hours") or 0.0)
    keep, closed = [], list(p.get("closed") or [])
    for h in p.get("holds") or []:
        if h.get("until") and parse_iso(h["until"]) < t:
            closed.append(dict(h, ended=h["until"], reason="expired", used=float(h.get("amount") or 1.0)))
        else:
            keep.append(h)
    if window > 0:
        closed = [c for c in closed if parse_iso(c["ended"]) >= t - window * 3600]
    else:
        closed = closed[-50:]
    p["holds"], p["closed"] = keep, closed
    used, per = 0.0, {}
    for h in keep:
        a = float(h.get("amount") or 1.0)
        used += a
        per[h["agent"]] = per.get(h["agent"], 0.0) + a
    if window > 0:
        for c in closed:
            a = float(c.get("used") or 0.0)
            used += a
            per[c["agent"]] = per.get(c["agent"], 0.0) + a
    return used, per


def pool_view(name, p, t):
    used, per = pool_usage(p, t)
    agents = sorted(set(list(per) + [k for k in (p.get("caps") or {}) if k != "*"]))
    return {"name": name, "capacity": p.get("capacity"), "used": round(used, 3), "unit": p.get("unit"),
            "window_hours": p.get("window_hours") or 0, "borrow_max_hours": p.get("borrow_max_hours") or 0,
            "caps": p.get("caps") or {}, "note": p.get("note"), "owner": p.get("owner"),
            "agents": dict((a, {"used": round(per.get(a, 0.0), 3), "cap": pool_cap(p, a)}) for a in agents),
            "holds": p.get("holds") or []}


def op_pool(req, src):
    path = os.path.abspath(os.path.expanduser(req.get("file") or ""))
    if not req.get("file"):
        raise Fail("a pool file is required", USAGE)
    action = req.get("action")
    t = float(req["now"]) if req.get("now") is not None else time.time()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with Lock(path + ".lock", HOST_RULES["lock_timeout_s"]):
        data = read_json(path, None) or {"schema": SCHEMA, "pools": {}}
        pools = data.setdefault("pools", {})
        if action == "show":
            return {"file": path, "pools": [pool_view(n, p, t) for n, p in sorted(pools.items())]}
        if action == "define":
            defs = req.get("defs") or {}
            if not defs:
                raise Fail("nothing to define: pass --name and --capacity, or --preset FILE", USAGE)
            definer = req.get("agent")
            if not definer:
                raise Fail("--agent (or the project's slug) is required to define pools", USAGE)
            valid_name(definer, "agent")
            for name, d in defs.items():
                if not POOL_RE.match(name):
                    raise Fail("bad pool name %r" % name, USAGE)
                p = pools.get(name) or {"holds": [], "closed": [], "owner": definer}
                # whoever defined a pool owns its capacity and caps; others ask them
                if p.get("owner") and p["owner"] != definer:
                    raise Fail("pool %s belongs to %s: only they change its capacity and caps" % (name, p["owner"]),
                               DENIED)
                p["owner"] = definer
                for k in ("capacity", "caps", "window_hours", "borrow_max_hours", "default_hours", "unit", "note"):
                    if d.get(k) is not None:
                        p[k] = d[k]
                if float(p.get("capacity") or 0) <= 0:
                    raise Fail("pool %s needs a capacity above 0" % name, USAGE)
                pools[name] = p
            write_json(path, data)
            return {"file": path, "pools": [pool_view(n, pools[n], t) for n in sorted(defs)]}
        name = req.get("name")
        if name not in pools:
            raise Fail("no pool %r in %s (define it first)" % (name, path))
        p = pools[name]
        agent = req.get("agent")
        if not agent:
            raise Fail("--agent (or HOSTCLAIMS_AGENT) is required", USAGE)
        valid_name(agent, "agent")
        hid = valid_id(req.get("id"), "hold id")
        used, per = pool_usage(p, t)
        hours = req.get("hours")
        if action == "acquire":
            for h in p["holds"]:
                if h["id"] == hid and h["agent"] == agent:
                    if hours is not None:
                        bmax = float(p.get("borrow_max_hours") or 0.0)
                        if h.get("borrowed") and float(hours) > bmax + EPS:
                            raise Fail("a borrowed hold may run at most %g h from now" % bmax, DENIED)
                        h["until"] = iso(t + float(hours) * 3600)
                        write_json(path, data)
                    return {"existing": True, "hold": h, "pool": pool_view(name, p, t)}
            amount = float(req.get("amount") or 1.0)
            capacity = float(p["capacity"])
            if used + amount > capacity + EPS:
                raise Fail("pool %s is full: %g of %g %s in use" % (name, used, capacity, p.get("unit") or ""), NOFIT,
                           pool=pool_view(name, p, t))
            borrowed = False
            if per.get(agent, 0.0) + amount > pool_cap(p, agent) + EPS:
                others = sorted(set(h["agent"] for h in p["holds"] if h["agent"] != agent))
                bmax = float(p.get("borrow_max_hours") or 0.0)
                if not req.get("borrow"):
                    raise Fail("%s is at its cap in pool %s (%g of %g); --borrow may exceed it only while no other "
                               "agent holds any and the hold ends within %g h" % (agent, name, per.get(agent, 0.0),
                                                                               pool_cap(p, agent), bmax),
                               DENIED, pool=pool_view(name, p, t))
                if float(p.get("window_hours") or 0) > 0:
                    raise Fail("budget pools do not lend: ask the owner to change the caps", DENIED)
                if others:
                    raise Fail("cannot borrow in pool %s: %s hold(s) there" % (name, ", ".join(others)), DENIED)
                if hours is None or float(hours) > bmax + EPS:
                    raise Fail("a borrowed hold must end within %g h (pass --hours)" % bmax, DENIED)
                borrowed = True
            if hours is None:
                hours = float(p.get("default_hours") or 12.0)
            h = {"agent": agent, "id": hid, "amount": amount, "since": iso(t), "until": iso(t + float(hours) * 3600),
                 "borrowed": borrowed, "note": req.get("note")}
            p["holds"].append(h)
            write_json(path, data)
            return {"existing": False, "hold": h, "pool": pool_view(name, p, t)}
        if action == "release":
            h = next((x for x in p["holds"] if x["id"] == hid and x["agent"] == agent), None)
            if h is None:
                other = next((x for x in p["holds"] if x["id"] == hid), None)
                if other is not None:
                    raise Fail("hold %s belongs to %s: never release another agent's hold" % (hid, other["agent"]), DENIED)
                done = next((x for x in p["closed"] if x["id"] == hid and x["agent"] == agent), None)
                if done is not None:
                    return {"released": False, "note": "already closed (%s)" % done.get("reason"), "hold": done}
                raise Fail("no hold %s for %s in pool %s" % (hid, agent, name))
            p["holds"].remove(h)
            actual = req.get("actual")
            use = float(actual) if actual is not None else float(h.get("amount") or 1.0)
            p["closed"].append(dict(h, ended=iso(t), reason="released", used=use))
            pool_usage(p, t)
            write_json(path, data)
            return {"released": True, "hold": h, "pool": pool_view(name, p, t)}
        raise Fail("pool action must be show, define, acquire or release", USAGE)


HOST_OPS = {"install": op_install, "uninstall": op_uninstall, "status": op_status, "claim": op_claim,
            "run": op_run, "renew": op_renew, "release": op_release, "reap": op_reap, "yield": op_yield, "usage": op_usage,
            "lease": op_lease, "extend": op_extend, "request": op_request, "withdraw": op_withdraw, "decide": op_decide, "probe": op_probe,
            "audit": op_audit, "pool": op_pool}


def host_op(req, src=None):
    fn = HOST_OPS.get(req.get("op"))
    if fn is None:
        return {"ok": False, "code": ERROR, "error": "unknown op %r" % req.get("op")}
    try:
        res = fn(req, src)
    except Fail as e:
        res = dict(e.extra, ok=False, code=e.code, error=str(e))
    except ValueError as e:
        res = {"ok": False, "code": USAGE, "error": str(e)}
    res.setdefault("ok", True)
    res.setdefault("code", OK)
    return res


def host_entry(req_json, src):
    # entry point on a host: the controller appends a call to this after the source
    try:
        signal.signal(signal.SIGHUP, signal.SIG_IGN)
    except (ValueError, OSError):
        pass
    try:
        res = host_op(json.loads(req_json), src)
    except Exception as e:
        import traceback
        res = {"ok": False, "code": ERROR, "error": "%s: %s" % (type(e).__name__, e),
               "trace": traceback.format_exc()[-3000:]}
    sys.stdout.write(MARK + json.dumps(res) + "\n")
    sys.stdout.flush()


# the watcher: runs the job, keeps the heartbeat, delivers yield requests, writes the done marker

def signal_group(pid, sig):
    try:
        os.killpg(pid, sig)
    except OSError:
        try:
            os.kill(pid, sig)
        except OSError:
            pass


def wrap_main(root_path, cid):
    root = Root(root_path)
    rd = root.p("run", cid)
    with open(os.path.join(rd, "wrapper.log"), "a") as logf:
        return watch(root, rd, cid, logf)


def watch(root, rd, cid, logf):
    off = float((load_sim(root) or {}).get("clock_offset_s") or 0.0)
    rules = host_cfg(root)["rules"]
    path = claim_path(root, cid)

    def clk():
        return time.time() + off

    def log(msg):
        try:
            logf.write("%s %s\n" % (iso(clk()), msg))
            logf.flush()
        except (OSError, ValueError):
            pass

    def locked():
        return Lock(root.p(".lock"), rules["lock_timeout_s"])

    got = []

    def on_signal(signum, frame):
        got.append(signum)

    for s in (signal.SIGTERM, signal.SIGHUP, signal.SIGINT):
        signal.signal(s, on_signal)
    me = {"pid": os.getpid(), "pid_start": proc_start(os.getpid())}
    c = None
    for attempt in range(5):
        try:
            with locked():
                c = read_json(path)
                if c is None:
                    # released or reaped before the job started: never bring it back
                    log("claim %s is gone; not starting the job" % cid)
                    return 1
                c["launcher"] = dict(c.get("launcher") or {}, **me)
                save_claim(root, c)
            break
        except Fail as e:
            c = None
            log("waiting for the lock: %s" % e)
    if c is None:
        log("could not take the lock; not starting the job")
        return 1
    res = c.get("resources") or {}
    cores = res.get("cores") or []
    env = dict(os.environ)
    env.pop("TMUX", None)
    env.pop("TMUX_PANE", None)
    env["CUDA_DEVICE_ORDER"] = "PCI_BUS_ID"
    env["CUDA_VISIBLE_DEVICES"] = ",".join(str(g["index"]) for g in res.get("gpus") or [])
    env["HOSTCLAIMS_CLAIM_ID"] = cid
    env["HOSTCLAIMS_YIELD_FILE"] = c.get("yield_file") or os.path.join(rd, "yield.json")
    env["HOSTCLAIMS_RUN_DIR"] = rd
    if cores:
        for k in THREAD_VARS:
            env[k] = str(len(cores))
    argv = ["/bin/sh", os.path.join(rd, "job.sh")]
    pinned = None
    if cores:
        ts = shutil.which("taskset")
        if ts:
            argv = [ts, "-c", fmt_cores(cores)] + argv
            pinned = "taskset"
        elif hasattr(os, "sched_setaffinity"):
            try:
                os.sched_setaffinity(0, cores)
                pinned = "sched_setaffinity"
            except OSError as e:
                log("could not pin cores: %s" % e)
        else:
            log("no taskset here: cores %s are not pinned" % fmt_cores(cores))
    t0 = clk()
    kind, rc = None, None
    try:
        with open(os.path.join(rd, "job.log"), "ab") as joblog:
            proc = subprocess.Popen(argv, cwd=c.get("cwd") or os.path.expanduser("~"), env=env,
                                    stdin=subprocess.DEVNULL, stdout=joblog, stderr=subprocess.STDOUT,
                                    start_new_session=True)
    except OSError as e:
        log("could not start the job: %s" % e)
        proc = None
    if proc is not None:
        try:
            with locked():
                cur = read_json(path)
                if cur:
                    cur.update({"pid": proc.pid, "pid_start": proc_start(proc.pid), "started": iso(t0),
                                "heartbeat": iso(t0), "pinned": pinned})
                    save_claim(root, cur)
                    c = cur
        except Fail as e:
            log("could not record the job pid: %s" % e)
        log("started pid %d: cores %s (%s), CUDA_VISIBLE_DEVICES=%s" % (
            proc.pid, fmt_cores(cores) or "-", pinned or "not pinned", env["CUDA_VISIBLE_DEVICES"]))
        hb_s = max(1.0, rules["heartbeat_s"])
        next_hb = clk() + hb_s
        kill_at = None
        yfile = env["HOSTCLAIMS_YIELD_FILE"]
        while True:
            rc = proc.poll()
            if rc is not None:
                break
            try:
                t = clk()
                if got and kind is None:
                    kind = "stop"
                    grace = float(c.get("grace_min") or rules["yield_grace_min"]) * 60
                    log("got signal %d: TERM to the job, KILL after %d s" % (got[0], grace))
                    signal_group(proc.pid, signal.SIGTERM)
                    kill_at = t + grace
                elif len(got) > 1 and kill_at is not None:
                    log("second signal: killing the job now")
                    kill_at = t
                if kind == "yield" and os.path.exists(yfile) and (read_json(yfile) or {}).get("kind") == "stop":
                    # a stop by the claim's own project after a yield (one the job handles itself, say): TERM now,
                    # and KILL no later than the stop's grace
                    kind = "stop"
                    grace = float(c.get("grace_min") or rules["yield_grace_min"]) * 60
                    log("stop requested after the yield: TERM to the job, KILL within %d s" % grace)
                    signal_group(proc.pid, signal.SIGTERM)
                    kill_at = t + grace if kill_at is None else min(kill_at, t + grace)
                if kind is None and os.path.exists(yfile):
                    y = read_json(yfile) or {}
                    kind = y.get("kind") or "yield"
                    # a stop by the claim's own project is always TERM; yields use the claim's signal (none: no signal,
                    # the job reads its yield file and stops itself; the KILL at the end of the grace still comes)
                    sig = "TERM" if kind == "stop" else (c.get("yield_signal") or "TERM")
                    if sig != "none":
                        signal_group(proc.pid, getattr(signal, "SIG" + sig))
                    grace = float(y.get("grace_min") or c.get("grace_min") or rules["yield_grace_min"]) * 60
                    kill_at = t + grace
                    log("%s requested by %s: %s, KILL after %d s" % (kind, y.get("by_agent"), (
                        "sent " + sig) if sig != "none" else "no signal (the job stops itself)", grace))
                    with locked():
                        cur = read_json(path)
                        if cur:
                            cur["yield"] = dict(cur.get("yield") or y, acked=iso(t))
                            save_claim(root, cur)
                            append_jsonl(root.p("history.jsonl"), {"event": "yield-ack", "ts": iso(t), "id": cid,
                                                                   "kind": kind})
                if kill_at is not None and t >= kill_at:
                    log("grace period over: killing the job")
                    signal_group(proc.pid, signal.SIGKILL)
                    kill_at = None
                if t >= next_hb:
                    with locked():
                        cur = read_json(path)
                        if cur:
                            cur["heartbeat"] = iso(t)
                            if not (cur.get("launcher") or {}).get("pid"):
                                cur["launcher"] = dict(cur.get("launcher") or {}, **me)
                            save_claim(root, cur)
                            # this claim's paired yield, once the partners it waits for are done
                            if (cur.get("yield") or {}).get("deferred"):
                                deliver_due(root, t)
                        else:
                            log("the claim file is gone; the job keeps running")
                    next_hb = t + hb_s
            except Exception as e:
                log("watch error: %s" % e)
            time.sleep(min(1.0, hb_s / 2.0))
        if kind is None and os.path.exists(yfile):
            # the job read its yield file and ended before the watcher looked: it yielded
            kind = (read_json(yfile) or {}).get("kind") or "yield"
    t = clk()
    if rc is None:
        code, signame = 127, None
    elif rc < 0:
        code = 128 - rc
        try:
            signame = signal.Signals(-rc).name
        except ValueError:
            signame = str(-rc)
    else:
        code, signame = rc, None
    reason = {"yield": "yielded", "stop": "stopped"}.get(kind) or ("finished" if code == 0 else "failed")
    done = {"claim": cid, "agent": c["agent"], "job": c["job"], "exit_code": code, "signal": signame,
            "reason": reason, "started": iso(t0), "ended": iso(t), "hours": round((t - t0) / 3600.0, 4),
            "log": os.path.join(rd, "job.log")}
    try:
        write_json(os.path.join(rd, "done.json"), done)
    except OSError as e:
        log("could not write done.json: %s" % e)
    if c.get("done_file"):
        try:
            extra = os.path.expanduser(c["done_file"])
            os.makedirs(os.path.dirname(extra) or ".", exist_ok=True)
            write_json(extra, done)
        except OSError as e:
            log("could not write %s: %s" % (c["done_file"], e))
    ended = False
    for attempt in range(6):
        try:
            with locked():
                cur = read_json(path)
                if cur and cur.get("id") == cid:
                    if not ended:
                        append_jsonl(root.p("history.jsonl"), usage_event("end", cur, t, t, reason=reason,
                                                                          exit_code=code))
                        ended = True
                    os.unlink(path)
                # paired claims waiting for this one to end get their yield now
                try:
                    deliver_due(root, t)
                except Exception as e:
                    log("paired yields not delivered: %s" % e)
            break
        except Exception as e:
            log("release retry: %s" % e)
            time.sleep(5)
    refresh_status(root)
    try:
        os.unlink(os.path.join(rd, "hostclaims.py"))
    except OSError:
        pass
    log("done: %s, exit code %d" % (reason, code))
    return 0


# controller side: projects, sharing links, inventory, policy, transport

def is_pack(d):
    # an ai-pack manifest carries framework_version and a project object; a plugin's has neither
    try:
        with open(os.path.join(d, "manifest.json"), encoding="utf-8") as f:
            m = json.load(f)
    except (OSError, ValueError):
        return False
    return isinstance(m, dict) and "framework_version" in m and isinstance(m.get("project"), dict)


def pack_of(d):
    # the name of d's ai-pack folder, whatever it is: its one direct child folder holding an ai-pack manifest.json,
    # else None; more than one is an error
    try:
        packs = sorted(n for n in os.listdir(d) if not n.startswith(".") and is_pack(os.path.join(d, n)))
    except OSError:
        return None
    if len(packs) > 1:
        raise Fail("%s: more than one ai-pack (%s)" % (d, ", ".join(packs)), USAGE)
    return packs[0] if packs else None


def unread_pack(d):
    # the manifest.json of a child folder of d that may be an ai-pack's but cannot be read: one that cannot be opened,
    # or names framework_version but is not JSON (a merge conflict, say); None when there is none
    try:
        names = sorted(n for n in os.listdir(d) if not n.startswith("."))
    except OSError:
        return None
    for n in names:
        path = os.path.join(d, n, "manifest.json")
        # a child folder that cannot be searched is passed over (os.path.isfile never raises)
        if not os.path.isfile(path):
            continue
        try:
            with open(path, "rb") as f:
                raw = f.read()
        except OSError:
            return path
        try:
            json.loads(raw.decode("utf-8"))
        except ValueError:
            if b"framework_version" in raw:
                return path
    return None


def home_or_above(d):
    # the home folder, a folder above it, or /: the walk up never reads these (on macOS ~/Desktop can raise
    # privacy prompts, and an automounted /home is slow)
    if os.path.dirname(d) == d:
        return True
    home = os.path.expanduser("~")
    homes = (os.path.abspath(home), os.path.realpath(home)) if os.path.isabs(home) else ()
    return any(h == d or h.startswith(d + os.sep) for h in homes)


def find_project(start=None):
    # (project root, pack folder): the given folder, else from the working folder up, else from this file's place
    if start:
        d = os.path.abspath(os.path.expanduser(start))
        pack = pack_of(d)
        if not pack:
            raise Fail("%s is not a project (no child folder holding an ai-pack manifest.json)" % d, USAGE)
        return d, pack
    d = os.getcwd()
    while not home_or_above(d):
        pack = pack_of(d)
        if pack:
            return d, pack
        d = os.path.dirname(d)
    # a copied install sits at <project>/<pack>/plugins/resource-sharing/tools/hostclaims.py; a project root at home
    # or above is never listed
    d = os.path.dirname(os.path.abspath(__file__))
    while True:
        parent = os.path.dirname(d)
        if parent == d:
            return None, None
        if os.path.basename(d) == "plugins" and not home_or_above(os.path.dirname(parent)) and is_pack(parent):
            if pack_of(os.path.dirname(parent)) == os.path.basename(parent):
                return os.path.dirname(parent), os.path.basename(parent)
        d = parent


def project_info(root, pack):
    path = os.path.join(root, pack, ".memory", CONFIG_NAME)
    raw = read_json(path)
    cfg = raw if isinstance(raw, dict) else {}
    return {"root": root, "pack": pack, "slug": cfg.get("project") or os.path.basename(root), "config": cfg,
            "share_with": [str(x) for x in cfg.get("share_with") or []], "has_config": bool(cfg),
            "config_error": os.path.exists(path) and not isinstance(raw, dict)}


def solaris_root_of(root):
    # projects live at <solaris>/projects/<group>/<slug>/ (or one level deeper)
    d = os.path.abspath(root)
    while True:
        parent = os.path.dirname(d)
        if parent == d:
            return None
        if os.path.basename(parent) == "projects":
            return os.path.dirname(parent)
        d = parent


def scan_projects(sroot):
    # (root, pack, why) for each project folder in the tree, never one inside another; a folder whose ai-pack cannot
    # be told (two or more, or none but a manifest that cannot be read) has pack None and why it is skipped, so one
    # broken project never stops the scan of the others
    def dirs(d):
        try:
            return sorted(os.path.join(d, n) for n in os.listdir(d)
                          if not n.startswith(".") and os.path.isdir(os.path.join(d, n)))
        except OSError:
            return []

    def look(d):
        try:
            pack = pack_of(d)
        except Fail as e:
            return None, str(e)
        path = None if pack else unread_pack(d)
        return pack, ("cannot read %s" % path) if path else None

    out = []
    for group in dirs(os.path.join(sroot, "projects")):
        for d in dirs(group):
            pack, why = look(d)
            if pack or why:
                out.append((d, pack, why))
                continue
            for d2 in dirs(d):
                pack, why = look(d2)
                if pack or why:
                    out.append((d2, pack, why))
    return out


def read_inventory(path):
    data = read_json(path)
    if data is None:
        return None
    if isinstance(data, dict):
        data = data.get("hosts") or []
    out = []
    for h in data:
        if not isinstance(h, dict) or not h.get("name") or not h.get("target"):
            raise Fail("inventory entries need name and target: %r in %s" % (h, path), USAGE)
        if h["target"] != "local":
            valid_target(h["target"])
        out.append(dict(h))
    return out


def peer_hosts(me, unread=None):
    # hosts that other projects in this Solaris tree own and share with this project;
    # unread (a dict) maps the folder of each project whose sharing file or inventory could not be read, or whose
    # ai-pack could not be told, to its slug
    out, notes, found = [], [], set()
    sroot = solaris_root_of(me["root"])
    if not sroot:
        return out, notes
    for root, pack, why in scan_projects(sroot):
        if os.path.abspath(root) == os.path.abspath(me["root"]):
            continue
        # the folder under the Solaris root names a project even when its sharing file, and so its slug, is unreadable
        folder = os.path.relpath(root, sroot)
        p = None
        try:
            if why:
                # its ai-pack cannot be told, so neither can its sharing file: unread, like an unreadable one
                raise Fail(why)
            path = os.path.join(root, pack, ".memory", "hosts.json")
            p = project_info(root, pack)
            found.add(p["slug"])
            if p["config_error"]:
                raise Fail("cannot read %s" % os.path.join(root, pack, ".memory", CONFIG_NAME))
            if me["slug"] not in p["share_with"] and "*" not in p["share_with"]:
                continue
            inv = read_inventory(path)
            if inv is None and os.path.exists(path):
                raise Fail("cannot read %s" % path)
        except (Fail, OSError) as e:
            # OSError: a file that exists but cannot be opened (permissions)
            err = e if isinstance(e, Fail) else "cannot read %s: %s" % (e.filename, e.strerror)
            who = p["slug"] if p and not p["config_error"] else "the project in " + folder
            notes.append("%s: the hosts of %s are skipped" % (err, who))
            if unread is not None:
                unread[folder] = p["slug"] if p else os.path.basename(root)
            continue
        for h in inv or []:
            owner = h.get("owner") or p["slug"]
            # only what the peer owns is shared; hosts it merely uses are not passed on
            if owner == p["slug"]:
                out.append(dict(h, project=p["slug"], owner=owner, owner_explicit=True,
                                shared_key="%s/%s" % (p["slug"], h["name"]), project_folder=folder))
    for s in me["share_with"]:
        if s != "*" and s not in found:
            notes.append("share_with names %s, which is not a project in this Solaris tree" % s)
    return out, notes


def merge_hosts(own, peers):
    out = list(own)
    names = set(h["name"] for h in out)
    by_target = dict((h["target"], h) for h in out if h["target"] != "local")
    notes = []
    for h in peers:
        mine = by_target.get(h["target"])
        if mine is not None:
            if mine.get("owner") != h["owner"]:
                notes.append("%s is also listed by %s, which owns it; set owner in one inventory" % (mine["name"],
                                                                                                   h["project"]))
            continue
        name = h["name"] if h["name"] not in names else "%s/%s" % (h["project"], h["name"])
        h = dict(h, name=name)
        names.add(name)
        by_target[h["target"]] = h
        out.append(h)
    return out, notes


def load_inventory(ctx):
    me = ctx.project
    slug = me["slug"] if me else None

    def own(hosts):
        return [dict(h, project=h.get("project") or slug, owner=h.get("owner") or slug,
                     owner_explicit=bool(h.get("owner"))) for h in hosts]

    if ctx.local_root:
        return own([{"name": "local", "target": "local", "root": ctx.local_root}])
    path = ctx.hosts_path or os.environ.get("HOSTCLAIMS_HOSTS")
    if path:
        inv = read_inventory(os.path.expanduser(path))
        if inv is None:
            raise Fail("cannot read the host inventory %s" % path, USAGE)
        return own(inv)
    if not me:
        raise Fail("no host inventory: pass --hosts FILE or --local-root DIR, set HOSTCLAIMS_HOSTS, or run inside "
                   "a project", USAGE)
    inv = read_inventory(os.path.join(me["root"], me["pack"], ".memory", "hosts.json")) or []
    peers, notes = peer_hosts(me)
    merged, more = merge_hosts(own(inv), peers)
    ctx.notes = notes + more
    if not merged:
        raise Fail("no hosts: %s/.memory/hosts.json is missing or empty, and no project shares hosts with %s"
                   % (me["pack"], slug), USAGE)
    return merged


def load_policy(path, project):
    # health: hosthealth.py's settings (power: default or max)
    pol = {"weights": {}, "classes": copy.deepcopy(DEFAULT_CLASSES), "fit": copy.deepcopy(FIT_DEFAULTS),
           "audit": dict(AUDIT_DEFAULTS), "health": {}}
    user = None
    if path:
        user = read_json(os.path.expanduser(path))
        if not isinstance(user, dict):
            raise Fail("cannot read the policy file %s" % path, USAGE)
    elif project and isinstance(project["config"].get("policy"), dict):
        user = project["config"]["policy"]
        path = os.path.join(project["root"], project["pack"], ".memory", CONFIG_NAME)
    if not user:
        return pol, None
    pol["weights"].update(user.get("weights") or {})
    for k, v in (user.get("classes") or {}).items():
        if k in pol["classes"]:
            pol["classes"][k].update(v)
    for k, v in (user.get("fit") or {}).items():
        if k == "new_instance":
            pol["fit"]["new_instance"].update(v)
        else:
            pol["fit"][k] = v
    pol["audit"].update(user.get("audit") or {})
    pol["health"].update(user.get("health") or {})
    return pol, path


def expand_opts(opts):
    if isinstance(opts, str):
        opts = shlex.split(opts)
    out = []
    for o in opts:
        o = str(o)
        if o.startswith("~"):
            o = os.path.expanduser(o)
        elif "=~" in o:
            k, v = o.split("=", 1)
            o = k + "=" + os.path.expanduser(v)
        out.append(o)
    return out


def source_text():
    with open(os.path.abspath(__file__), encoding="utf-8") as f:
        return f.read()


def payload(req, src):
    return ("SRC = %r\nG = {'__name__': 'hostclaims_host'}\nexec(compile(SRC, 'hostclaims.py', 'exec'), G)\n"
            "G['host_entry'](%r, SRC)\n" % (src, json.dumps(req)))


class Ctx(object):
    def __init__(self, args):
        self.args = args
        self.local_root = getattr(args, "local_root", None)
        self.hosts_path = getattr(args, "hosts", None)
        self.ssh = shlex.split(getattr(args, "ssh", None) or os.environ.get("HOSTCLAIMS_SSH") or "ssh")
        self.json = bool(getattr(args, "json", False))
        self.as_owner = bool(getattr(args, "as_owner", False))
        self.timeout = float(getattr(args, "timeout", None) or 180)
        root, pack = find_project(getattr(args, "project", None))
        self.project = project_info(root, pack) if root else None
        self.agent = (getattr(args, "agent", None) or os.environ.get("HOSTCLAIMS_AGENT")
                      or (self.project["slug"] if self.project else None))
        self.policy, self.policy_path = load_policy(getattr(args, "policy", None) or os.environ.get("HOSTCLAIMS_POLICY"),
                                                    self.project)
        self.notes = []
        self._inv = None

    def inventory(self):
        if self._inv is None:
            self._inv = load_inventory(self)
        return self._inv

    def hosts(self, names, default_all=True):
        inv = self.inventory()
        if not names:
            if default_all or len(inv) == 1:
                return inv
            raise Fail("--host is required (inventory: %s)" % ", ".join(h["name"] for h in inv), USAGE)
        byname = dict((h["name"], h) for h in inv)
        for h in inv:
            byname.setdefault("%s/%s" % (h.get("project"), h["name"]), h)
        out = []
        for n in names:
            if n not in byname:
                raise Fail("unknown host %r (inventory: %s)" % (n, ", ".join(h["name"] for h in inv)), USAGE)
            out.append(byname[n])
        return out

    def host(self, name):
        return self.hosts([name] if name else [], default_all=False)[0]

    def call(self, host, req):
        req = dict(req)
        if host.get("root") and not req.get("root"):
            req["root"] = host["root"]
        req["inv_lease"] = host.get("lease")
        req["inv_owner"] = host.get("owner")
        req["host_name"] = host["name"]
        req.setdefault("agent", self.agent)
        req.setdefault("controller", socket.gethostname())
        res = self.transport(host, req)
        res.setdefault("project", host.get("project"))
        return res

    def transport(self, host, req):
        src = source_text()
        if host.get("target") == "local":
            # the same answer shape as a remote host gives, errors included
            try:
                return host_op(json.loads(json.dumps(req)), src)
            except Exception as e:
                return {"ok": False, "code": ERROR, "error": "%s: %s" % (type(e).__name__, e)}
        cmd = self.ssh + expand_opts(host.get("opts") or []) + [
            "-o", "BatchMode=yes", "-o", "ConnectTimeout=20", host["target"], "python3 -"]
        try:
            r = subprocess.run(cmd, input=payload(req, src).encode("utf-8"), stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE, timeout=self.timeout)
        except subprocess.TimeoutExpired:
            return {"ok": False, "code": UNREACHABLE, "error": "no answer within %d s" % self.timeout}
        except OSError as e:
            return {"ok": False, "code": ERROR, "error": "cannot run %s: %s" % (self.ssh[0], e)}
        out = r.stdout.decode("utf-8", "replace")
        for line in reversed(out.splitlines()):
            if line.startswith(MARK):
                try:
                    return json.loads(line[len(MARK):])
                except ValueError:
                    break
        err = r.stderr.decode("utf-8", "replace").strip()
        return {"ok": False, "code": UNREACHABLE if r.returncode == 255 else ERROR,
                "error": "host call failed (exit %d): %s" % (r.returncode, (err or out.strip())[-600:])}

    def call_many(self, hosts, req):
        results = {}
        remote = [h for h in hosts if h.get("target") != "local"]
        for h in hosts:
            if h.get("target") == "local":
                results[h["name"]] = self.call(h, req)
        if remote:
            with ThreadPoolExecutor(max_workers=min(16, len(remote))) as ex:
                futs = [(h, ex.submit(self.call, h, req)) for h in remote]
                for h, f in futs:
                    results[h["name"]] = f.result()
        return [(h, results[h["name"]]) for h in hosts]

    def need_agent(self):
        if not self.agent:
            raise Fail("--agent (or HOSTCLAIMS_AGENT, or a project's resource-sharing.json) is required", USAGE)
        return valid_name(self.agent, "agent")

    def need_owner_identity(self):
        # owner actions act as the calling project; --agent or HOSTCLAIMS_AGENT cannot borrow another's name
        me = self.need_agent()
        if self.project and me != self.project["slug"]:
            raise Fail("owner actions run as this project (%s), not as %s: run them from the owning project"
                       % (self.project["slug"], me), DENIED)
        if not self.project and not self.as_owner:
            # outside a project nothing proves who is calling: the caller must say so explicitly
            raise Fail("owner actions outside a project need --as-owner, confirming you act for %s; run them from "
                       "the owning project instead where you can" % me, DENIED)
        return me

    def need_config(self):
        # a broken sharing file of this project would read as none: no sharing, and the folder's name as its slug
        if self.project and self.project["config_error"]:
            raise Fail("cannot read %s: fix it first (it must hold a JSON object)" % os.path.join(
                self.project["root"], self.project["pack"], ".memory", CONFIG_NAME), USAGE)


def emit(ctx, obj, lines):
    if ctx.json:
        print(json.dumps(obj, indent=1, sort_keys=True))
    else:
        for ln in lines:
            print(ln)


def fail_lines(name, r):
    lines = ["%s: %s" % (name, r.get("error"))]
    for m in r.get("reasons") or []:
        lines.append("  - " + m)
    if r.get("yields_pending") and not r.get("yield_candidates"):
        lines.append("  yields already requested may free enough: wait for them (%s)" % ", ".join(r["yields_pending"]))
    if r.get("yield_candidates"):
        lines.append("  preemptible lower-priority claims (older than the yield age) that would free enough: "
                     + ", ".join(r["yield_candidates"]))
        lines.append("  ask each with: yield --host %s --claim <id> --job <your job> --class <your class> --reason ..." % name)
    return lines


def exit_code(results):
    codes = [r.get("code", OK) for _, r in results if not r.get("ok")]
    if not codes:
        return OK
    return UNREACHABLE if UNREACHABLE in codes else codes[0]


def parse_gpu_spec(s):
    parts = str(s).split(":")
    if len(parts) > 3:
        raise ValueError("bad --gpu %r (use INDEX|any[:SHARE[:MEM]])" % s)
    idx = parts[0].strip().lower()
    return {"index": None if idx in ("any", "") else int(idx),
            "share": float(parts[1]) if len(parts) > 1 and parts[1].strip() else None,
            "mem_gb": parse_gb(parts[2]) if len(parts) > 2 and parts[2].strip() else None}


def want_req(ctx, a):
    gpus = [parse_gpu_spec(s) for s in a.gpu or []]
    gpus += [{"index": None, "share": None, "mem_gb": None} for _ in range(a.gpus or 0)]
    return {"agent": ctx.agent, "job": a.job, "class": a.cls, "preemptible": a.preemptible,
            "borrowed": a.borrowed, "cores": a.cores or 0, "ram_gb": parse_gb(a.ram) if a.ram else 0.0,
            "disk_gb": parse_gb(a.disk) if a.disk else 0.0, "gpus": gpus, "gpu_type": a.gpu_type,
            "gpu_mem_gb": parse_gb(a.gpu_mem) if a.gpu_mem else None,
            "hours": parse_hours(a.hours) if a.hours else None, "classes": ctx.policy["classes"]}


def want_flags(a):
    out = ["--job", a.job] if a.job else []
    for flag, v in (("--class", a.cls), ("--cores", a.cores), ("--ram", a.ram), ("--disk", a.disk),
                    ("--gpus", a.gpus), ("--gpu-type", a.gpu_type), ("--gpu-mem", a.gpu_mem), ("--hours", a.hours)):
        if v:
            out += [flag, str(v)]
    for s in a.gpu or []:
        out += ["--gpu", s]
    if a.preemptible:
        out.append("--preemptible")
    return out


def kv_pairs(items, what):
    out = {}
    for it in items or []:
        if "=" not in it:
            raise Fail("%s wants KEY=VALUE, got %r" % (what, it), USAGE)
        k, v = it.split("=", 1)
        out[k.strip()] = v.strip()
    return out


def lease_text(lease, t):
    kind = (lease or {}).get("kind") or "none"
    if kind == "none":
        return "none"
    s = kind
    if lease.get("planned_end"):
        end = parse_iso(lease["planned_end"])
        s += ", ends %s (%s)" % (local_time(end), ("in " + fmt_age(end - t)) if end > t else "passed")
    if kind == "paid":
        s += ", $%.2f/h" % float(lease.get("usd_per_hour") or 0.0)
        for k in ("gpu_type", "instance", "provider"):
            if lease.get(k):
                s += ", %s %s" % (k.replace("_", " "), lease[k])
    return s


def claim_line(c):
    res = c.get("resources") or {}
    g = ",".join("%s:%g" % (x.get("index"), x.get("share") or 0) for x in res.get("gpus") or [])
    flags = (c.get("class") or "") + ("*" if c.get("preemptible") else "") + (" borrowed" if c.get("borrowed") else "")
    y = ""
    if c.get("yield"):
        yy = c["yield"]
        if yy.get("deferred"):
            y = "; %s asked by %s, follows when its partner is done" % (yy.get("kind"), yy.get("by_agent"))
        else:
            y = "; %s asked by %s%s" % (yy.get("kind"), yy.get("by_agent"), " (acked)" if yy.get("acked") else "")
    eta = (", eta " + local_time(parse_iso(c["eta"]))) if c.get("eta") else ""
    mem = ""
    if c.get("mem_gb") is not None:
        over = float(c["mem_gb"]) - float(res.get("ram_gb") or 0.0)
        mem = " (%s in use%s)" % (fmt_gb(c["mem_gb"]), (", %s over" % fmt_gb(over)) if over >= 0.05 else "")
    procs = ""
    if c.get("procs"):
        procs = "; pid " + ", ".join("%s%s%s" % (p.get("pid"), (" " + p["prog"]) if p.get("prog") else "",
                                                 "" if p.get("alive") else " (gone)") for p in c["procs"])
    pair = ("; yields after " + ", ".join(c["yield_with"])) if c.get("yield_with") else ""
    return "  %-7s %s/%s %s: cores %s, %s RAM%s%s%s; hb %s ago, age %s%s%s%s  [%s]" % (
        c.get("state") or "", c.get("agent"), c.get("job"), flags, c.get("cores_text") or "-",
        fmt_gb(res.get("ram_gb")), mem, (", GPU " + g) if g else "", procs, fmt_age(c.get("heartbeat_age_s")),
        fmt_age(c.get("age_s")), eta, pair, y, c.get("id"))


def request_text(r, host):
    # the text an agent shows the owner (or its human) in chat
    tool = "python3 " + shlex.quote(os.path.abspath(__file__))
    # no --agent here: the owner runs these from its own project, where they act as the owner
    cmds = ("From the owner's project: approve with `%s approve --host %s --request %s`, or decline with "
            "`%s decline --host %s --request %s --reason ...`." % (tool, host, r["id"], tool, host, r["id"]))
    kind = r.get("type") or "extension"
    if kind == "maintenance":
        return ("Maintenance request %s: %s asks the owner (%s) of %s: %s. Approving records the owner's yes; the owner "
                "then does the work (drain first with install --mode draining). %s"
                % (r["id"], r["agent"], r.get("owner"), host, r.get("reason"), cmds))
    if kind == "objection":
        return ("Release objection %s: %s asks the owner (%s) to keep %s until %s: %s. Approving records keep-until, "
                "so the audit will not suggest releasing or deleting it earlier. %s"
                % (r["id"], r["agent"], r.get("owner"), host, local_time(parse_iso(r["until"])), r.get("reason"), cmds))
    ni = r.get("new_instance") or {}
    alt = ""
    if ni.get("wait_min") is not None:
        alt = " A new instance instead would be ready in about %d min" % round(ni["wait_min"])
        alt += (" and cost about $%.2f." % ni["usd"]) if ni.get("usd") is not None else "."
    fit = r.get("fit") or {}
    gp = ", ".join("GPU %s (%s, %.2f share)" % (g.get("index"), g.get("name"), g.get("share") or 0)
                   for g in fit.get("gpus") or [])
    return ("Extension request %s: %s asks the owner (%s) to keep %s%s about %.1f h past its planned end (%s), until "
            "%s, for job %s (%.1f h, class %s)%s. Estimated extra cost: $%.2f at $%.2f/h. It fits there now: %s%d cores, "
            "%s RAM.%s Approving moves the planned end. %s Unanswered, it expires at %s and the requester launches a "
            "new instance."
            % (r["id"], r["agent"], r.get("owner"), host, (" (instance %s)" % r["instance"]) if r.get("instance") else "",
               r["extra_hours"], local_time(parse_iso(r["planned_end"])), local_time(parse_iso(r["needed_until"])),
               r["job"], r["hours"], r.get("class"), (": " + r["reason"]) if r.get("reason") else "",
               r["est_extra_usd"], r.get("usd_per_hour") or 0.0, (gp + ", ") if gp else "", fit.get("cores") or 0,
               fmt_gb(fit.get("ram_gb")), alt, cmds, local_time(parse_iso(r["expires"]))))


def tool_cmd(ctx, *args):
    # a command to run as printed: this tool, the caller's --project/--hosts/--local-root and any explicit
    # --agent and --as-owner, then args
    parts = ["python3", os.path.abspath(__file__)]
    for flag, v in (("--project", getattr(ctx.args, "project", None)), ("--hosts", ctx.hosts_path),
                    ("--local-root", ctx.local_root)):
        if v:
            parts += [flag, os.path.abspath(os.path.expanduser(v))]
    if getattr(ctx.args, "agent", None):
        parts += ["--agent", ctx.args.agent]
    if ctx.as_owner:
        parts.append("--as-owner")
    return " ".join(shlex.quote(str(x)) for x in parts + list(args))


def host_label(h, r=None):
    owner = (r or {}).get("owner") or h.get("owner")
    return "%s [project %s, owner %s]" % (h["name"], h.get("project") or "-", owner or "-")


def status_lines(h, r):
    name = h["name"]
    if not r.get("ok"):
        return fail_lines(host_label(h), r)
    if not r.get("installed"):
        return ["%s: not installed (its owner runs install)" % host_label(h)]
    t = r["now"]
    f = r["free"]
    sw = r.get("share_with")
    lines = ["%s  %s  mode %s  lease %s%s" % (host_label(h, r), r["tag"], r["mode"], lease_text(r.get("lease"), t),
                                             "" if sw is None else "  shared with: %s" % (", ".join(sw) or "nobody"))]
    def last_end(g):
        if g.get("claims") or not g.get("last_claim_end"):
            return ""
        try:
            return ", last claim ended %s ago" % fmt_age(t - parse_iso(g["last_claim_end"]))
        except ValueError:
            return ""

    gp = " | ".join("GPU %d %s: %.2f free, %s%s%s" % (g["index"], g["name"], g["free_share"], fmt_gb(g["free_mem_gb"]),
                                                       ", unclaimed use" if g["unclaimed"] else "", last_end(g))
                    for g in f["gpus"])
    lines.append("  free: %d cores, %s RAM, %s disk%s%s" % (
        f["cores"], fmt_gb(f["ram_gb"]), fmt_gb(f["disk_gb"]), " (below the floor)" if f["below_floor"] else "",
        ("; " + gp) if gp else ""))
    for c in r.get("claims") or []:
        lines.append(claim_line(c))
    for o in r.get("overlaps") or []:
        lines.append("  warning: claims %s share cores %s" % (" and ".join(o["claims"]), fmt_cores(o["cores"])))
    for p in r.get("pinned") or []:
        who = "pid %s%s" % (p["pid"], (" (%s)" % p["prog"]) if p.get("prog") else "")
        if p.get("claim"):
            lines.append("  warning: %s of claim %s runs on cores %s outside its claim: no claim gets them while it "
                         "runs there" % (who, p["claim"], fmt_cores(p["outside"])))
        else:
            lines.append("  pinned outside claims: %s on cores %s: no claim gets them while it runs (a job whose claim "
                         "lapsed? its project claims or stops it)" % (who, fmt_cores(p["outside"])))
    for g in r.get("unclaimed_gpu_procs") or []:
        lines.append("  unclaimed GPU process: GPU %s pid %s (%s)" % (g.get("gpu"), g.get("pid"), fmt_gb(g.get("mem_gb"))))
    out = r.get("outside") or {}
    if out.get("cpu_cores") or out.get("ram_gb"):
        lines.append("  work outside claims: %s%s" % (
            ("%.1f cores busy " % out["cpu_cores"]) if out.get("cpu_cores") else "",
            ("%s in large processes (pids %s)" % (fmt_gb(out["ram_gb"]), ", ".join(str(p) for p in out.get("pids") or [])))
            if out.get("ram_gb") else ""))
    for q in r.get("requests") or []:
        if q["state"] == "pending":
            lines.append("  PENDING " + request_text(q, name))
        else:
            lines.append("  request %s (%s by %s): %s" % (q["id"], q.get("type"), q.get("agent"), q["state"]))
    for e in r.get("recent") or []:
        lines.append("  ended %s/%s: %s%s, %.2f h" % (e.get("agent"), e.get("job"), e.get("reason"),
                                                     (" exit %s" % e["exit_code"]) if "exit_code" in e else "",
                                                     e.get("hours") or 0))
    for s in r.get("stale") or []:
        lines.append("  stale %s/%s: %s  [stale/%s.json]" % (s["agent"], s["job"], (s.get("stale") or {}).get("reason"),
                                                              s["id"]))
    for w in r.get("warnings") or []:
        lines.append("  warning: " + w)
    return lines


# controller commands

def cmd_install(ctx, a):
    settings = {"mode": a.mode, "reserve_cores": a.reserve_cores,
                "reserve_ram_gb": parse_gb(a.reserve_ram) if a.reserve_ram else None,
                "reserve_disk_free_pct": a.disk_free_pct, "disk_path": a.disk_path, "launcher": a.launcher,
                "rules": kv_pairs(a.rule, "--rule"), "lease": kv_pairs(a.lease, "--lease"), "owner": a.owner}
    me = ctx.need_owner_identity()
    ctx.need_config()
    req = {"op": "install", "settings": settings}
    if a.share_with is not None:
        sw = a.share_with.strip()
        req["share_with"] = [] if sw in ("", "none", "-") else [x.strip() for x in sw.split(",") if x.strip()]
    elif ctx.project and ctx.project["has_config"]:
        req["share_with"] = ctx.project["share_with"]
    if a.all:
        if a.host:
            raise Fail("pass --all or --host, not both", USAGE)
        if a.owner or a.lease:
            raise Fail("--owner and --lease differ per host: pass them with --host, not with --all", USAGE)
        # every host this project owns in its inventory; the hosts others share with it stay theirs
        hosts = [h for h in ctx.inventory() if h.get("owner") in (me, None)]
    else:
        hosts = ctx.hosts(a.host or [], default_all=False)
    results = ctx.call_many(hosts, req)
    lines = [] if hosts else ["no hosts owned by %s in the inventory" % me]
    for h, r in results:
        if not r.get("ok"):
            lines += fail_lines(h["name"], r)
            continue
        f = r["facts"]
        lines.append("%s: %s %s (%s); python %s, tmux %s, taskset %s; %d cpus, %s RAM, %s disk, GPUs: %s" % (
            host_label(h, {"owner": r["host"].get("owner")}), "installed" if r["new"] else "updated", r["root"], r["tag"],
            f["python"], "yes" if f["tmux"] else "NO", "yes" if f["taskset"] else "no", f["cpus"], fmt_gb(f["ram_gb"]),
            fmt_gb(f["disk_gb"]), ", ".join("%d %s" % (g["index"], g["name"]) for g in f["gpus"]) or "none"))
        for w in f.get("warnings") or []:
            lines.append("  warning: " + w)
    sw = req.get("share_with")
    if sw and any(r.get("ok") for _, r in results):
        lines.append("shared with %s: tell them (their `shared` lists new and changed hosts)"
                     % ("every project" if "*" in sw else ", ".join(sw)))
    emit(ctx, {"hosts": dict((h["name"], r) for h, r in results), "share_with": sw}, lines)
    return exit_code(results)


def cmd_uninstall(ctx, a):
    ctx.need_owner_identity()
    h = ctx.host(a.host)
    r = ctx.call(h, {"op": "uninstall"})
    lines = fail_lines(h["name"], r) if not r.get("ok") else [
        "%s: %s %s" % (h["name"], "removed" if r.get("removed") else "nothing to remove at", r["root"])]
    emit(ctx, dict(r, host=h["name"]), lines)
    return r.get("code", OK)


def cmd_status(ctx, a):
    results = ctx.call_many(ctx.hosts(a.host), {"op": "status"})
    lines = []
    for h, r in results:
        lines += status_lines(h, r)
    for n in ctx.notes:
        lines.append("note: " + n)
    emit(ctx, {"hosts": dict((h["name"], r) for h, r in results), "notes": ctx.notes}, lines)
    return exit_code(results)


def shared_record(h, key):
    # what a guest tracks about a host shared with it, from the owner's inventory alone (no ssh)
    lease = h.get("lease") if isinstance(h.get("lease"), dict) else None
    end = (lease or {}).get("planned_end") or None
    if end:
        try:
            end = iso(parse_iso(end))
        except ValueError:
            end = str(end)
    gpus = h.get("gpus")
    if gpus in (None, "", []):
        gpus = (lease or {}).get("gpu_type")
    if isinstance(gpus, (list, tuple)):
        gpus = ", ".join(str(g) for g in gpus)
    return {"key": key, "name": h["name"], "project": h.get("project"), "owner": h.get("owner"),
            "target": h.get("target"), "lease_kind": None if lease is None else (lease.get("kind") or "none"),
            "planned_end": end, "usd_per_hour": (lease or {}).get("usd_per_hour"),
            "gpus": None if gpus in (None, "") else str(gpus), "folder": h.get("project_folder")}


def shared_now(ctx, inv_file, me):
    # records and inventory hosts by key (owner/name) for what other projects share with this one now, and the
    # projects whose files could not be read, folder to slug (their hosts count neither as shared nor as gone)
    cur, hosts, unread = {}, {}, {}
    if inv_file:
        # exactly this inventory: the hosts in it that another project owns
        for h in ctx.inventory():
            if h.get("owner") and h["owner"] != me:
                key = "%s/%s" % (h["owner"], h["name"])
                cur[key], hosts[key] = shared_record(dict(h, project=h["owner"]), key), h
        return cur, hosts, unread
    p = ctx.project
    if not solaris_root_of(p["root"]):
        raise Fail("%s is not in a Solaris tree (projects/<group>/<slug>/), so no sharing links can be read" % p["root"])
    own = [dict(h, project=p["slug"], owner=h.get("owner") or p["slug"])
           for h in read_inventory(os.path.join(p["root"], p["pack"], ".memory", "hosts.json")) or []]
    peers, notes = peer_hosts(p, unread)
    merged, more = merge_hosts(own, peers)
    ctx.notes = notes + more
    # the names status and claim take: a clash becomes <project>/<name>; a host this project lists keeps its name
    by_key = dict((h["shared_key"], h) for h in merged if h.get("shared_key"))
    by_target = dict((h["target"], h) for h in merged if h["target"] != "local")
    for h in peers:
        k = h["shared_key"]
        host = by_key.get(k) or by_target.get(h["target"]) or h
        cur[k], hosts[k] = shared_record(dict(h, name=host["name"]), k), host
    return cur, hosts, unread


def safe_lease_text(lease, t):
    try:
        return lease_text(lease, t)
    except (ValueError, TypeError):
        return "%s, ends %s" % ((lease or {}).get("kind") or "?", (lease or {}).get("planned_end") or "?")


def gpu_summary(gpus):
    # "2x NVIDIA H200 NVL (1.50 free)": GPUs by model with their summed free share
    by = {}
    for g in gpus or []:
        n = g.get("name") or "?"
        c, fr = by.get(n, (0, 0.0))
        by[n] = (c + 1, fr + float(g.get("free_share") or 0.0))
    return ", ".join("%dx %s (%.2f free)" % (c, n, fr) for n, (c, fr) in sorted(by.items())) or "none"


def shared_line(r, t):
    lease = "?" if r.get("lease_kind") is None else safe_lease_text(
        {"kind": r["lease_kind"], "planned_end": r.get("planned_end"), "usd_per_hour": r.get("usd_per_hour")}, t)
    return "%s [owner %s] %s; lease %s; GPUs %s" % (r.get("name"), r.get("owner") or "-", r.get("target") or "-", lease,
                                                    r.get("gpus") or "?")


def when_text(v):
    try:
        return local_time(parse_iso(v))
    except ValueError:
        return str(v)


def shared_value(f, v):
    if v is None:
        return "?"
    return when_text(v) if f == "planned_end" else str(v)


def shared_live(r, me):
    # the part of a status answer a guest needs: tag, free capacity, GPUs, the host's own lease, admission
    if not r.get("ok"):
        return {"ok": False, "code": r.get("code"), "error": r.get("error")}
    if not r.get("installed"):
        return {"ok": True, "installed": False}
    why = [m for _, m in admission({"share_with": r.get("share_with"), "owner": r.get("owner"),
                                    "mode": r.get("mode") or "shared"}, me, False, False)]
    return {"ok": True, "installed": True, "now": r.get("now"), "tag": r.get("tag"), "free": r.get("free"),
            "lease": r.get("lease"), "owner": r.get("owner"), "share_with": r.get("share_with"), "mode": r.get("mode"),
            "admitted": not why, "why": why}


def live_line(v, t):
    if not v.get("ok"):
        return "  live: %s" % v.get("error")
    if not v.get("installed"):
        return "  live: not installed yet (its owner runs install --all)"
    f = v.get("free") or {}
    return "  live: %s; free %s cores, %s RAM, %s disk; GPUs %s; lease %s; %s" % (
        v.get("tag"), f.get("cores"), fmt_gb(f.get("ram_gb")), fmt_gb(f.get("disk_gb")), gpu_summary(f.get("gpus")),
        safe_lease_text(v.get("lease"), v.get("now") or t),
        "admits you" if v.get("admitted") else "does not admit you: " + "; ".join(v.get("why") or []))


def cmd_shared(ctx, a):
    # guest side: the hosts other projects share with this one against the seen list; no ssh unless --probe
    inv_file = ctx.hosts_path or os.environ.get("HOSTCLAIMS_HOSTS")
    if ctx.local_root or not (ctx.project or inv_file):
        raise Fail("shared lists the hosts other projects share with this one: run it in a project (or pass "
                   "--hosts FILE with --seen FILE)", USAGE)
    if a.seen:
        seen_path = os.path.abspath(os.path.expanduser(a.seen))
    elif inv_file:
        raise Fail("with --hosts, pass --seen FILE as well (the project's seen list tracks the hosts found through "
                   "sharing links)", USAGE)
    else:
        seen_path = os.path.join(ctx.project["root"], ctx.project["pack"], ".memory", SEEN_NAME)
    ctx.need_config()
    me = ctx.need_agent()
    t = time.time()
    cur, hosts, unread = shared_now(ctx, inv_file, me)
    raw = read_json(seen_path)
    if os.path.exists(seen_path) and not isinstance(raw, dict):
        ctx.notes.append("cannot read the seen list %s: every host counts as new" % seen_path)
    seen = raw if isinstance(raw, dict) else {}
    old = seen.get("hosts") if isinstance(seen.get("hosts"), dict) else {}
    old = dict((k, v) for k, v in old.items() if isinstance(v, dict))

    def unreadable(r):
        # by the owner's folder; a record from a seen list written before folders were kept matches by slug
        return r["folder"] in unread if r.get("folder") else r.get("project") in unread.values()

    new = [cur[k] for k in sorted(cur) if k not in old]
    gone = [old[k] for k in sorted(old) if k not in cur and not unreadable(old[k])]
    kept = [old[k] for k in sorted(old) if k not in cur and unreadable(old[k])]
    changed, same = [], []
    for k in sorted(set(cur) & set(old)):
        diff = dict((f, [old[k].get(f), cur[k].get(f)]) for f in SHARED_FIELDS if old[k].get(f) != cur[k].get(f))
        if diff:
            changed.append(dict(cur[k], changes=diff))
        else:
            same.append(cur[k])
    live, results = {}, []
    if a.probe and cur:
        # one status call per host, over ssh: tag, free capacity, GPUs, and whether the host admits this project
        todo = dict((hosts[k]["name"], hosts[k]) for k in sorted(cur))
        results = ctx.call_many(list(todo.values()), {"op": "status"})
        by_name = dict((h["name"], shared_live(r, me)) for h, r in results)
        live = dict((k, by_name[hosts[k]["name"]]) for k in cur)
    last = seen.get("acked")
    projects = sorted(set(r.get("project") or "-" for r in cur.values()))
    lines = ["shared with %s: %d host(s)%s; last ack %s" % (
        me, len(cur), (" from " + ", ".join(projects)) if projects else "", when_text(last) if last else "never")]
    labels = {"name": "name", "owner": "owner", "target": "target", "lease_kind": "lease", "planned_end": "planned end",
              "gpus": "GPUs"}
    for tag, recs in (("NEW", new), ("CHANGED", changed)):
        for r in recs:
            lines.append("%-7s %s" % (tag, shared_line(r, t)))
            if r.get("changes"):
                lines.append("  was: " + "; ".join("%s %s -> %s" % (labels[f], shared_value(f, r["changes"][f][0]),
                                                                     shared_value(f, r["changes"][f][1]))
                                                   for f in SHARED_FIELDS if f in r["changes"]))
            if live:
                lines.append(live_line(live[r["key"]], t))
    for r in gone:
        lines.append("GONE    %s: no longer shared with you" % shared_line(r, t))
    for r in kept:
        lines.append("UNREAD  %s: %s's files cannot be read now, so it stays as seen" % (shared_line(r, t),
                                                                                         r.get("project")))
    if live:
        for r in same:
            lines.append("same    " + shared_line(r, t))
            lines.append(live_line(live[r["key"]], t))
    elif same:
        lines.append("unchanged: " + ", ".join(r["name"] for r in same))
    for n in ctx.notes:
        lines.append("note: " + n)
    pending = bool(new or gone or changed)
    if a.ack:
        doc = {"schema": SCHEMA, "project": me, "acked": iso(t), "hosts": dict((r["key"], r) for r in kept)}
        doc["hosts"].update(cur)
        try:
            os.makedirs(os.path.dirname(seen_path), exist_ok=True)
            write_json(seen_path, doc)
        except OSError as e:
            raise Fail("cannot write the seen list %s: %s" % (seen_path, e))
        lines.append("acknowledged %d host(s) in %s" % (len(doc["hosts"]), seen_path))
    elif pending:
        lines.append("%d new, %d gone, %d changed since the last ack. NEW: `status --host H` (or `fit`), then `claim` or "
                     "`run`; GONE: start nothing there and release your claims there; CHANGED: check lease ends and "
                     "targets against your claims. Then acknowledge: %s" % (
                         len(new), len(gone), len(changed),
                         tool_cmd(ctx, "shared", "--ack", *(["--seen", seen_path] if a.seen else []))))
    else:
        lines.append("no changes since the last ack")
    out = {"project": me, "seen_file": seen_path, "last_ack": last, "new": new, "gone": gone, "changed": changed,
           "unchanged": same, "unread": kept, "notes": ctx.notes, "acknowledged": bool(a.ack)}
    if a.probe:
        out["live"] = live
    emit(ctx, out, lines)
    if a.ack:
        return OK
    return CHANGES if pending else exit_code(results)


def cmd_request(ctx, a):
    ctx.need_agent()
    h = ctx.host(a.host)
    if a.withdraw:
        r = ctx.call(h, {"op": "withdraw", "request": a.withdraw})
        lines = fail_lines(h["name"], r) if not r.get("ok") else ["%s: withdrew request %s" % (h["name"], a.withdraw)]
        emit(ctx, dict(r, host=h["name"]), lines)
        return r.get("code", OK)
    if not a.type:
        raise Fail("pass --type maintenance|objection with --reason, or --withdraw ID", USAGE)
    r = ctx.call(h, {"op": "request", "type": a.type, "reason": a.reason, "until": a.until, "job": a.job})
    if not r.get("ok"):
        lines = fail_lines(h["name"], r)
    else:
        r["text"] = request_text(r["request"], h["name"])
        lines = ["%s: %s request %s" % (h["name"], "existing" if r.get("existing") else "filed", r["request"]["id"]),
                 "  show the owner this text:", "  " + r["text"]]
    emit(ctx, dict(r, host=h["name"]), lines)
    return r.get("code", OK)


def audit_recommend(r, ap):
    # keep, extend, release or delete for one owned host; paid hosts get the strict idle limit
    if not r.get("ok"):
        kind = ((r.get("lease") or {}).get("kind")) or "?"
        return {"action": "check", "why": "unreachable (%s)%s" % (r.get("error"), "; a paid instance bills while unreachable"
                                                                  if kind == "paid" else "")}
    if not r.get("installed"):
        return {"action": "check", "why": "hostclaims is not installed there, so claims and use are unknown: install it"}
    t = r["now"]
    lease = r.get("lease") or {}
    kind = lease.get("kind") or "none"
    end = parse_iso(lease["planned_end"]) if lease.get("planned_end") else None
    idle_min = (r.get("idle_s") or 0) / 60.0
    live = r.get("live_claims") or []
    pend = r.get("requests") or []
    ext = [q for q in pend if q.get("type") == "extension"]
    keep = parse_iso(r["keep_until"]) if r.get("keep_until") else None
    etas = [parse_iso(c["eta"]) for c in live if c.get("eta")]
    if kind == "none":
        return {"action": "keep", "why": "an owned machine%s" % (", idle %s" % fmt_age(r.get("idle_s")) if not live else "")}
    if ext:
        q = ext[0]
        age = (t - parse_iso(q["created"])) / 60.0 if q.get("created") else 0.0
        stale = float(ap.get("request_stale_min") or 35.0)
        if age >= stale:
            # older than a new instance's ready time: the guest has most likely launched elsewhere
            return {"action": "decline", "why": "%s's request %s is %d min old, longer than a new instance takes to be "
                                                "ready (%d min): decline it unless they still wait" % (
                                                    q["agent"], q["id"], age, stale)}
        return {"action": "extend", "why": "%s asks for %.1f h more (about $%.2f): approve or decline %s" % (
            q["agent"], q.get("extra_hours") or 0, q.get("est_extra_usd") or 0, q["id"])}
    if live:
        if end and etas and max(etas) > end:
            return {"action": "extend", "why": "live claims run until %s, past the planned end" % local_time(max(etas))}
        if kind == "free" and end and (end - t) / 3600.0 <= float(ap["renew_before_h"]):
            return {"action": "extend", "why": "in use and the lease ends within %g h: renew it" % float(ap["renew_before_h"])}
        return {"action": "keep", "why": "in use: %d live claim(s)" % len(live)}
    out = r.get("outside") or {}
    if out.get("any"):
        # work outside claims is still work: never advise deleting or releasing under it
        what = ", ".join(x for x in (
            ("GPU %s" % ",".join(str(i) for i in out.get("gpus") or [])) if out.get("gpus") else "",
            ("%.1f cores" % out["cpu_cores"]) if out.get("cpu_cores") else "",
            ("%s RAM" % fmt_gb(out["ram_gb"])) if out.get("ram_gb") else "") if x)
        return {"action": "check", "why": "busy outside claims (%s): find who runs it and have them claim it; never "
                                          "delete or release under running work" % what}
    if keep and keep > t:
        return {"action": "keep", "why": "an approved objection keeps it until %s" % local_time(keep)}
    objections = [q for q in pend if q.get("type") == "objection" and q.get("until") and parse_iso(q["until"]) > t]
    if objections:
        q = objections[0]
        return {"action": "check", "why": "%s objects to a release before %s: approve or decline %s first" % (
            q["agent"], local_time(parse_iso(q["until"])), q["id"])}
    if kind == "paid":
        if end and t >= end:
            return {"action": "delete", "why": "past its planned end with no live claims; it bills until deleted"}
        if idle_min >= float(ap["paid_idle_max_min"]):
            return {"action": "delete", "why": "paid and idle %d min (limit %g min); it bills until deleted" % (
                idle_min, float(ap["paid_idle_max_min"]))}
        return {"action": "keep", "why": "paid and idle %d min of the %g allowed: decide before the limit" % (
            idle_min, float(ap["paid_idle_max_min"]))}
    if idle_min >= float(ap["free_idle_max_h"]) * 60:
        return {"action": "release", "why": "idle %.1f h (limit %g h) with no claims or objections" % (
            idle_min / 60.0, float(ap["free_idle_max_h"]))}
    return {"action": "keep", "why": "idle %s; renew only while it is in use or asked for" % fmt_age(r.get("idle_s"))}


def cmd_audit(ctx, a):
    me = ctx.need_agent()
    ctx.need_config()
    # an extension request older than a new instance's ready time is advised for decline
    ap = dict(ctx.policy["audit"], request_stale_min=new_instance(ctx.policy["fit"], 0.0, None)["wait_min"])
    window = parse_hours(a.window) if a.window else float(ctx.policy["audit"]["window_hours"])
    hosts = [h for h in ctx.hosts(a.host) if h.get("owner") in (me, None)]
    results = ctx.call_many(hosts, {"op": "audit", "window_hours": window})
    want_sw = ctx.project["share_with"] if ctx.project and ctx.project["has_config"] else None
    rows, lines, fixes = [], [], 0
    for h, r in results:
        if r.get("ok") and r.get("installed") and r.get("owner") not in (me, None):
            continue
        rec = audit_recommend(dict(r, lease=r.get("lease") or h.get("lease")), ap)
        # an owned host that guests cannot see correctly yet: not installed, or its sharing list is out of date
        fix = None
        if r.get("ok") and not r.get("installed"):
            fix = {"why": "in the inventory but not installed", "cmd": tool_cmd(ctx, "install", "--host", h["name"])}
        elif r.get("ok") and want_sw is not None and sorted(want_sw) != sorted(r.get("share_with") or []):
            fix = {"why": "sharing on the host (%s) differs from resource-sharing.json (%s)" % (
                ", ".join(r.get("share_with") or []) or "unset", ", ".join(want_sw) or "nobody"),
                "cmd": tool_cmd(ctx, "install", "--host", h["name"])}
        fixes += 1 if fix else 0
        rows.append({"host": h["name"], "result": r, "recommendation": rec, "fix": fix})
        if not r.get("ok") or not r.get("installed"):
            lines.append("%s: %s - %s" % (host_label(h, r), rec["action"], rec["why"]))
            if fix:
                lines.append("  fix: " + fix["cmd"])
            continue
        lease = r.get("lease") or {}
        cost = (", cost so far $%.2f" % r["cost_so_far_usd"]) if r.get("cost_so_far_usd") is not None else ""
        lines.append("%s  lease %s%s; idle %s; %d live claim(s)" % (
            host_label(h, r), lease_text(lease, r["now"]), cost, fmt_age(r.get("idle_s")), len(r.get("live_claims") or [])))
        use = "; ".join("%s %s%.2f CPU-h" % (ag, "".join("%.2f %s GPU-h, " % (v, k) for k, v in sorted(u["gpu_hours"].items())),
                                                u["cpu_hours"]) for ag, u in sorted((r.get("usage") or {}).items()))
        lines.append("  use, last %g h: %s" % (window, use or "none"))
        if fix:
            lines.append("  %s: sync it with %s" % (fix["why"], fix["cmd"]))
        for q in r.get("requests") or []:
            lines.append("  open request: " + request_text(q, h["name"]))
        if r.get("old_runs"):
            lines.append("  %d finished run folder(s) older than %g days: reap prunes them" % (
                r["old_runs"], r.get("run_keep_days") or 0))
        lines.append("  recommend: %s - %s" % (rec["action"], rec["why"]))
    if not results:
        lines.append("no hosts owned by %s in the inventory" % me)
    if fixes:
        lines.append("install or sync every owned host at once (then tell the projects you share with): %s"
                     % tool_cmd(ctx, "install", "--all"))
    emit(ctx, {"owner": me, "window_hours": window, "hosts": rows}, lines)
    return exit_code(results)


def attached_lines(name, c, attached):
    # what --pid did: the processes tied now, with their program names, so a launcher that exits at once shows
    out = []
    for x in attached or []:
        out.append("  %s pid %s%s to claim %s" % ("attached" if x.get("new") else "already tied:", x["pid"],
                                                 (" (%s)" % x["prog"]) if x.get("prog") else "", c["id"]))
    if attached:
        out.append("  the claim lives while any of its processes runs (pid %s): tie the job's own long-lived process, "
                   "not a launcher that exits at once" % ", ".join(str(p["pid"]) for p in c.get("procs") or []))
    return out


def cmd_claim(ctx, a, launch=False):
    ctx.need_agent()
    h = ctx.host(a.host)
    req = dict(want_req(ctx, a), op="run" if launch else "claim", pid=getattr(a, "pid", None), brief=a.brief, note=a.note,
               yield_with=a.yield_with)
    if launch:
        req.update(cmd=a.cmd, cwd=a.cwd, done_file=a.done_file, yield_signal=a.yield_signal, grace_min=a.grace_min,
                   rerun=a.rerun)
    r = ctx.call(h, req)
    if not r.get("ok"):
        if r.get("code") == UNREACHABLE:
            # the claim, or the launch, may have landed before the answer was lost
            r["hint"] = ("the %s may have happened before the connection failed: run status --host %s before trying "
                         "another host (repeating the same %s only returns the existing claim)"
                         % ("launch" if launch else "claim", h["name"], "run" if launch else "claim"))
        emit(ctx, dict(r, host=h["name"]), fail_lines(h["name"], r) + (["  " + r["hint"]] if r.get("hint") else []))
        return r.get("code", ERROR)
    lines = []
    if r.get("finished"):
        lines.append("%s: %s" % (h["name"], r["note"]))
    else:
        c = r["claim"]
        lines.append("%s: %s %s" % (h["name"], "already claimed:" if r.get("existing") else "claimed", c["id"]))
        lines.append(claim_line(c))
        lines += attached_lines(h["name"], c, r.get("attached") or ([dict(p, new=True) for p in c.get("procs") or []]
                                                                    if not r.get("existing") else []))
        lp = r.get("lapsed")
        if lp:
            now = fmt_cores(lp["cores"]) or "-"
            if lp.get("kept"):
                how = ": this one keeps its cores %s" % (fmt_cores(lp.get("job_cores") or lp["cores_before"]) or "-")
            elif lp.get("job_cores"):
                how = (": its job runs pinned to cores %s, this one's are %s: move it (taskset -acp %s <pid>)"
                       % (fmt_cores(lp["job_cores"]), now, now))
            else:
                how = (": its cores were %s, this one's are %s: move a job still running there (taskset -acp %s <pid>)"
                       % (fmt_cores(lp["cores_before"]) or "-", now, now))
            lines.append("  your earlier claim of this job lapsed%s (stale/%s.json)%s" % (
                (" at " + local_time(parse_iso(lp["at"]))) if lp.get("at") else "", lp["id"], how))
        pj = r.get("pinned_job")
        if pj and not pj.get("kept"):
            now = fmt_cores(c["resources"]["cores"]) or "-"
            lines.append("  the process you tied runs pinned to cores %s, beyond this claim's %s: re-pin it (taskset "
                         "-acp %s <pid>) or claim more cores" % (fmt_cores(pj["cores"]), now, now))
        if c.get("lapses_at"):
            lines.append("  nothing watches this claim: it lapses at %s unless you renew it (renew --job %s, or re-run "
                         "this claim) or tie it to your process with --pid" % (local_time(parse_iso(c["lapses_at"])),
                                                                              c.get("job")))
        if launch and not r.get("existing"):
            lines.append("  %s; pid %s; run dir %s (job.log, done.json)" % (
                "tmux session %s" % c["launcher"]["session"] if (c.get("launcher") or {}).get("session")
                else "detached", c.get("pid") or "starting", c.get("run_dir")))
            if r.get("done"):
                lines.append("  already done: %s, exit code %s" % (r["done"]["reason"], r["done"]["exit_code"]))
        for w in r.get("warnings") or []:
            lines.append("  warning: " + w)
    emit(ctx, dict(r, host=h["name"]), lines)
    return OK


def cmd_release(ctx, a):
    ctx.need_agent()
    if not a.job and not a.claim:
        raise Fail("pass --job or --claim", USAGE)
    h = ctx.host(a.host)
    r = ctx.call(h, {"op": "release", "job": a.job, "claim": a.claim, "stop": a.stop, "reason": a.reason})
    if not r.get("ok"):
        lines = fail_lines(h["name"], r)
    elif r.get("released"):
        lines = ["%s: released %s" % (h["name"], r["released"])]
    else:
        lines = ["%s: %s" % (h["name"], r.get("note"))]
    emit(ctx, dict(r, host=h["name"]), lines)
    return r.get("code", OK)


def cmd_reap(ctx, a):
    results = ctx.call_many(ctx.hosts(a.host), {"op": "reap"})
    lines = []
    for h, r in results:
        if not r.get("ok"):
            lines += fail_lines(h["name"], r)
            continue
        if r.get("installed") is False:
            lines.append("%s: not installed" % h["name"])
            continue
        lines.append("%s: %d live, %d moved to stale/, %d orphan(s)%s%s" % (
            h["name"], r["live"], len(r["reaped"]), len(r["orphans"]),
            (", %d request(s) expired" % len(r["expired_requests"])) if r["expired_requests"] else "",
            (", %d old run folder(s) pruned" % len(r["pruned_runs"])) if r.get("pruned_runs") else ""))
        for c in r["reaped"]:
            lines.append("  stale: %s (%s)" % (c["id"], c["reason"]))
        for c in r["orphans"]:
            lines.append("  orphan kept: %s (pid %s)" % (c["id"], c["pid"]))
    emit(ctx, {"hosts": dict((h["name"], r) for h, r in results)}, lines)
    return exit_code(results)


def cmd_yield(ctx, a):
    ctx.need_agent()
    h = ctx.host(a.host)
    r = ctx.call(h, {"op": "yield", "claim": a.claim, "job": a.job, "class": a.cls, "borrowed": a.borrowed,
                     "reason": a.reason})
    if not r.get("ok"):
        lines = fail_lines(h["name"], r)
    else:
        lines = ["%s: %s yield request for %s (grace %g min)" % (
            h["name"], "existing" if r.get("existing") else "sent", a.claim, r["yield"].get("grace_min") or 0)]
        group = r.get("group") or []
        if len(group) > 1:
            first = [g["job"] for g in group if not g["deferred"]]
            later = [g["job"] for g in group if g["deferred"]]
            lines.append("  paired claims yield together: %s now%s" % (
                ", ".join(first) or "none", ("; then %s, each once the partners it names have ended or had their grace"
                                             % ", ".join(later)) if later else ""))
        if r.get("note"):
            lines.append("  note: " + r["note"])
    emit(ctx, dict(r, host=h["name"]), lines)
    return r.get("code", OK)


def cmd_renew(ctx, a):
    ctx.need_agent()
    if bool(a.job) == bool(a.all):
        raise Fail("pass --job J, or --all for every claim of yours on the host", USAGE)
    h = ctx.host(a.host)
    req = {"op": "renew", "job": a.job, "all": a.all, "hours": parse_hours(a.hours) if a.hours else None,
           "until": a.until, "preemptible": a.preemptible, "borrowed": a.borrowed, "pid": a.pid,
           "yield_with": a.yield_with}
    r = ctx.call(h, req)
    if not r.get("ok"):
        lines = fail_lines(h["name"], r)
    else:
        lines = []
        for x in r.get("renewed") or []:
            c, ch = x["claim"], x.get("changes") or {}
            what = []
            if "eta" in ch:
                what.append("end %s" % local_time(parse_iso(ch["eta"][1])))
            for k in ("preemptible", "borrowed"):
                if k in ch:
                    what.append(k if ch[k][1] else "not " + k)
            if "yield_with" in ch:
                what.append("yields after %s" % (", ".join(ch["yield_with"][1]) or "nothing"))
            lines.append("%s: renewed %s/%s%s  [%s]" % (h["name"], c["agent"], c["job"],
                                                        (": " + "; ".join(what)) if what else "", c["id"]))
            lines.append(claim_line(c))
            lines += attached_lines(h["name"], c, x.get("attached"))
        for w in r.get("warnings") or []:
            lines.append("  warning: " + w)
    emit(ctx, dict(r, host=h["name"]), lines)
    return r.get("code", OK)


def cmd_usage(ctx, a):
    window = parse_hours(a.window)
    results = ctx.call_many(ctx.hosts(a.host), {"op": "usage", "window_hours": window})
    agents = {}
    for h, r in results:
        if not r.get("ok") or r.get("installed") is False:
            continue
        for name, u in r["agents"].items():
            x = agents.setdefault(name, {"gpu_hours": {}, "cpu_hours": 0.0, "claims": 0})
            for k, v in u["gpu_hours"].items():
                x["gpu_hours"][k] = x["gpu_hours"].get(k, 0.0) + v
            x["cpu_hours"] += u["cpu_hours"]
            x["claims"] += u["claims"]
    weights = ctx.policy["weights"]
    names = sorted(set(agents) | set(weights))
    wsum = sum(float(weights.get(n, 1.0)) for n in names) or 1.0
    totals = {}
    for x in agents.values():
        for k, v in x["gpu_hours"].items():
            totals[k] = totals.get(k, 0.0) + v
    for n in names:
        x = agents.setdefault(n, {"gpu_hours": {}, "cpu_hours": 0.0, "claims": 0})
        x["weight"] = float(weights.get(n, 1.0))
        x["due_share"] = round(x["weight"] / wsum, 3)
        x["gpu_share"] = dict((k, round(x["gpu_hours"].get(k, 0.0) / totals[k], 3)) for k in totals if totals[k] > 0)
    lines = ["usage over the last %g h on %d host(s):" % (window, sum(1 for _, r in results if r.get("ok")))]
    for n in names:
        x = agents[n]
        gp = ", ".join("%s %.2f GPU-h (%.0f%%)" % (k, v, 100 * x["gpu_share"].get(k, 0.0))
                       for k, v in sorted(x["gpu_hours"].items())) or "no GPU"
        lines.append("  %s: %s; %.2f CPU-h; %d claim(s); weight %g, due %.0f%% of each GPU type" % (
            n, gp, x["cpu_hours"], x["claims"], x["weight"], 100 * x["due_share"]))
    for h, r in results:
        if not r.get("ok"):
            lines += fail_lines(h["name"], r)
    emit(ctx, {"window_hours": window, "agents": agents, "hosts": dict((h["name"], r) for h, r in results)}, lines)
    return exit_code(results)


def pool_location(ctx, loc):
    # PATH (local) or HOST:PATH (an inventory name, or a raw ssh target)
    m = re.match(r"^([^/:~][^:/]*):(.+)$", loc)
    if not m:
        return None, loc
    name, path = m.group(1), m.group(2)
    try:
        inv = ctx.inventory()
    except Fail:
        inv = []
    for h in inv:
        if h["name"] == name:
            return h, path
    return {"name": name, "target": valid_target(name), "opts": []}, path


def cmd_pool(ctx, a):
    loc = a.file or os.environ.get("HOSTCLAIMS_POOL_FILE")
    if not loc:
        raise Fail("--file PATH or HOST:PATH is required (or set HOSTCLAIMS_POOL_FILE)", USAGE)
    host, path = pool_location(ctx, loc)
    defs = None
    if a.action == "define":
        if a.preset:
            data = read_json(os.path.expanduser(a.preset))
            if not isinstance(data, dict) or not isinstance(data.get("pools"), dict):
                raise Fail("cannot read pools from the preset %s" % a.preset, USAGE)
            defs = dict((k, v) for k, v in data["pools"].items() if not k.startswith("_"))
        if a.name and a.capacity is not None:
            caps = dict((k, float(v)) for k, v in kv_pairs(a.cap, "--cap").items())
            defs = dict(defs or {})
            defs[a.name] = {"capacity": a.capacity, "caps": caps or None, "window_hours": a.window_hours,
                            "borrow_max_hours": a.borrow_max_hours, "default_hours": a.default_hours,
                            "unit": a.unit, "note": a.note}
    req = {"op": "pool", "file": path, "action": a.action, "name": a.name, "agent": ctx.agent, "id": a.id,
           "amount": a.amount, "hours": parse_hours(a.hours) if a.hours else None, "borrow": a.borrow,
           "note": a.note, "actual": a.actual, "defs": defs}
    if a.action == "define":
        ctx.need_owner_identity()
    elif a.action in ("acquire", "release"):
        ctx.need_agent()
    if host is None:
        req["controller"] = socket.gethostname()
        r = host_op(req, None)
    else:
        r = ctx.call(host, req)
    where = (host["name"] + ":" if host else "") + path
    if not r.get("ok"):
        lines = fail_lines(where, r)
    elif a.action in ("show", "define"):
        lines = ["%s:" % where]
        for p in r["pools"]:
            lines.append("  %s: %g of %g %s in use%s" % (
                p["name"], p["used"], p["capacity"], p.get("unit") or "",
                (" (rolling %g h)" % p["window_hours"]) if p["window_hours"] else ""))
            for ag, u in sorted(p["agents"].items()):
                lines.append("    %s: %g of cap %g" % (ag, u["used"], u["cap"]))
            for hd in p["holds"]:
                lines.append("    hold %s/%s: %g until %s%s" % (hd["agent"], hd["id"], hd.get("amount") or 1,
                                                                local_time(parse_iso(hd["until"])),
                                                                " (borrowed)" if hd.get("borrowed") else ""))
    elif a.action == "acquire":
        hd = r["hold"]
        lines = ["%s: %s %s/%s in %s (%g, until %s%s)" % (
            where, "already held" if r.get("existing") else "acquired", hd["agent"], hd["id"], a.name,
            hd.get("amount") or 1, local_time(parse_iso(hd["until"])), ", borrowed" if hd.get("borrowed") else "")]
    else:
        lines = ["%s: %s" % (where, "released %s" % a.id if r.get("released") else r.get("note"))]
    emit(ctx, dict(r, where=where), lines)
    return r.get("code", OK)


def cmd_lease(ctx, a):
    h = ctx.host(a.host)
    sets = kv_pairs(a.set, "--set")
    if sets:
        ctx.need_owner_identity()
    r = ctx.call(h, {"op": "lease", "set": sets})
    lines = fail_lines(h["name"], r) if not r.get("ok") else [
        "%s: lease %s (from %s)" % (h["name"], lease_text(r["lease"], r["now"]), r["source"])]
    emit(ctx, dict(r, host=h["name"]), lines)
    return r.get("code", OK)


def new_instance(fp, hours, gpu_type, usd=None, wait=None, stage_min=0.0):
    ni = fp["new_instance"]
    if wait is None:
        wait = float(ni["create_min"]) + float(ni["not_ready_min"]) + float(ni["setup_min"]) + float(stage_min or 0.0)
    rate = usd
    prices = ni.get("usd_per_hour") or {}
    if rate is None and gpu_type:
        for k, v in prices.items():
            if k.lower() == str(gpu_type).lower():
                rate = float(v)
        if rate is None:
            for k, v in prices.items():
                if k.lower() in str(gpu_type).lower() or str(gpu_type).lower() in k.lower():
                    rate = float(v)
                    break
    billed_min = max(0.0, float(wait) - float(ni["create_min"])) + float(hours) * 60 + float(ni["delete_min"])
    return {"wait_min": round(float(wait), 1), "usd_per_hour": rate, "gpu_type": gpu_type,
            "usd": None if rate is None else round(rate * billed_min / 60.0, 2), "billed_hours": round(billed_min / 60.0, 2)}


def rank_fit(rows, hours, fp, new, can_wait=False):
    margin = float(fp["end_margin_min"]) / 60.0
    table = []
    for r in rows:
        e = {"host": r["name"], "project": r.get("project"), "owner": r.get("owner"), "verdict": None, "note": "",
             "start_min": None, "ext_h": 0.0, "extra_usd": 0.0, "contention": 0.0, "pending": [], "refused": []}
        table.append(e)
        if not r.get("ok"):
            e.update(verdict="unreachable", note=r.get("error") or "")
            continue
        if not r.get("installed"):
            e.update(verdict="not-installed", note="run install first")
            continue
        lease = r.get("lease") or {}
        kind = lease.get("kind") or "none"
        e.update(kind=kind, gpu_types=r.get("gpu_types"), tag=r.get("tag"), fits_now=r.get("fits_now"),
                 start_min=r.get("start_in_min"), contention=r.get("contention") or 0.0)
        if r.get("hw_reasons"):
            e.update(verdict="no-match", note="; ".join(r["hw_reasons"]))
            continue
        if "mode" in (r.get("reason_kinds") or []):
            e.update(verdict="not-admitted", note="; ".join(r.get("reasons") or []))
            continue
        start = r.get("start_in_min")
        left = None
        if kind in ("paid", "free") and lease.get("planned_end"):
            left = (parse_iso(lease["planned_end"]) - float(r["now"])) / 3600.0
        need = (start or 0.0) / 60.0 + float(hours) + margin
        ext = max(0.0, need - left) if left is not None else 0.0
        rate = float(lease.get("usd_per_hour") or 0.0)
        reqs = [q for q in r.get("requests") or [] if (q.get("type") or "extension") == "extension"]
        pend = [q for q in reqs if q.get("state") == "pending"]
        e.update(left_h=None if left is None else round(left, 2), need_h=round(need, 2), ext_h=round(ext, 2),
                 extra_usd=round(ext * rate, 2) if kind == "paid" else 0.0, usd_per_hour=rate if kind == "paid" else None,
                 pending=[q["id"] for q in pend],
                 pending_min=round(max((float(r["now"]) - (parse_iso(q["created"]) if q.get("created") else float(r["now"])))
                                       / 60.0 for q in pend), 1) if pend else None,
                 refused=[q["id"] + " " + q["state"] for q in reqs if q.get("state") in ("declined", "expired")])
        if start is None:
            e.update(verdict="busy", note="; ".join(r.get("reasons") or []) or "no free capacity and no claim end in sight")
        elif ext <= 0:
            e.update(verdict="as-is")
        elif kind == "free":
            e.update(verdict="as-is", note="its lease must be extended %.1f h (a free lease: its owner renews by rule)" % ext)
        elif e["refused"]:
            e.update(verdict="refused", note="extension " + e["refused"][-1])
        else:
            e.update(verdict="extension")
    return table, recommend(table, fp, new, can_wait)


def recommend(table, fp, new, can_wait=False):
    wait = new.get("wait_min")
    as_is = sorted([e for e in table if e["verdict"] == "as-is"],
                   key=lambda e: (e["start_min"], e["ext_h"] > 0, e["contention"], e["host"]))
    if as_is and (wait is None or as_is[0]["start_min"] <= wait + float(fp["reuse_extra_wait_min"])):
        b = as_is[0]
        why = "fits now" if b["start_min"] <= 0 else "starts in about %d min" % round(b["start_min"])
        if b["contention"]:
            why += ", sharing its GPU (%.2f of it already claimed)" % b["contention"]
        if b["ext_h"] > 0:
            why += "; " + b["note"]
        return {"action": "reuse", "host": b["host"], "why": why + "; no extra cost"}
    pend = [e for e in table if e.get("pending")]
    # wait for the owner only as long as a new instance would take to be ready, unless the job can wait
    if pend and (can_wait or wait is None or (pend[0].get("pending_min") or 0.0) < wait):
        why = "an extension request is pending there (%d min so far); " % round(pend[0].get("pending_min") or 0.0)
        why += ("wait for the owner's answer or its timeout" if can_wait or wait is None else
                "wait up to %d min in all (a new instance's wait), then launch new" % round(wait))
        return {"action": "wait-for-owner", "host": pend[0]["host"], "request": pend[0]["pending"][0], "why": why}
    usd = new.get("usd")
    ext = [e for e in table if e["verdict"] == "extension" and not e.get("pending") and e["start_min"] is not None
           and e["start_min"] <= float(fp["extension_max_start_min"]) and e["ext_h"] <= float(fp["extension_max_hours"])
           and e["extra_usd"] <= float(fp["extension_max_usd"])
           and (usd is None or e["extra_usd"] <= float(fp["extension_max_cost_ratio"]) * usd)]
    ext.sort(key=lambda e: (e["extra_usd"], e["start_min"], e["host"]))
    if ext:
        b = ext[0]
        return {"action": "reuse-with-extension", "host": b["host"], "extra_hours": b["ext_h"], "extra_usd": b["extra_usd"],
                "why": "fits now; needs %.1f h past the planned end for about $%.2f, against a new instance at %s after a "
                       "%s wait" % (b["ext_h"], b["extra_usd"], ("about $%.2f" % usd) if usd is not None else "an unknown price",
                                    ("%d min" % round(wait)) if wait is not None else "?")}
    why = "no existing host fits within its time left and the extension thresholds"
    out = {}
    if pend:
        why = ("the owner has not answered in %d min, as long as a new instance takes to be ready: launch new and "
               "withdraw the pending request" % round(pend[0].get("pending_min") or 0.0))
        out = {"withdraw": {"host": pend[0]["host"], "request": pend[0]["pending"][0]}}
    elif as_is:
        why = "the best existing host (%s) starts in about %d min, later than a new instance (%d min) plus %g min" % (
            as_is[0]["host"], round(as_is[0]["start_min"]), round(wait), float(fp["reuse_extra_wait_min"]))
    out.update({"action": "launch-new", "why": why, "wait_min": wait, "usd": usd, "usd_per_hour": new.get("usd_per_hour"),
                "via": "the nvidia-brev plugin's brev-run flow; then register the instance with install --lease kind=paid ..."})
    return out


def cmd_fit(ctx, a):
    if not a.hours:
        raise Fail("--hours is required", USAGE)
    req = dict(want_req(ctx, a), op="probe")
    hours = req["hours"]
    results = ctx.call_many(ctx.hosts(a.host), req)
    rows = [dict(r, name=h["name"]) for h, r in results]
    fp = ctx.policy["fit"]
    new = new_instance(fp, hours, a.new_gpu_type or a.gpu_type, a.new_usd_per_hour, a.new_wait_min, a.stage_min)
    table, rec = rank_fit(rows, hours, fp, new, bool(getattr(a, "can_wait", False)))
    if rec.get("withdraw"):
        rec["withdraw_cmd"] = "python3 %s request --host %s --withdraw %s" % (
            shlex.quote(os.path.abspath(__file__)), rec["withdraw"]["host"], rec["withdraw"]["request"])
    if rec["action"] == "reuse-with-extension":
        flags = want_flags(a)
        for flag, v in (("--new-usd-per-hour", a.new_usd_per_hour), ("--new-gpu-type", a.new_gpu_type),
                        ("--new-wait-min", a.new_wait_min), ("--stage-min", a.stage_min)):
            if v:
                flags += [flag, str(v)]
        rec["extend_cmd"] = "python3 %s extend --host %s %s" % (shlex.quote(os.path.abspath(__file__)), rec["host"],
                                                               " ".join(shlex.quote(x) for x in flags))
    lines = ["fit for %.1f h: %d core(s), %s RAM%s" % (hours, req["cores"], fmt_gb(req["ram_gb"]),
                                                       (", %d GPU(s)%s" % (len(req["gpus"]), (" " + a.gpu_type) if a.gpu_type else ""))
                                                       if req["gpus"] else "")]
    for e in table:
        bits = [e["verdict"]]
        if e.get("kind"):
            bits.append("lease " + e["kind"])
        if e.get("start_min") is not None and e["verdict"] not in ("no-match", "not-admitted"):
            bits.append("start %s" % ("now" if e["start_min"] <= 0 else "in %d min" % round(e["start_min"])))
        if e.get("left_h") is not None:
            bits.append("%.1f h left" % e["left_h"])
        if e.get("ext_h"):
            bits.append("needs %.1f h more" % e["ext_h"])
        if e.get("extra_usd"):
            bits.append("extra $%.2f" % e["extra_usd"])
        if e.get("contention"):
            bits.append("GPU already %.2f claimed" % e["contention"])
        lines.append("  %-16s %s%s" % ("%s (%s)" % (e["host"], e.get("owner") or e.get("project") or "-"),
                                       ", ".join(bits), ("; " + e["note"]) if e.get("note") else ""))
    lines.append("  %-16s ready in about %d min, %s" % ("new instance", round(new["wait_min"]),
                                                        ("about $%.2f" % new["usd"]) if new["usd"] is not None
                                                        else "price unknown (pass --new-usd-per-hour)"))
    lines.append("recommend: %s%s - %s" % (rec["action"], (" " + rec["host"]) if rec.get("host") else "", rec["why"]))
    if rec.get("extend_cmd"):
        lines.append("  file the request: " + rec["extend_cmd"])
    if rec.get("withdraw_cmd"):
        lines.append("  after launching, withdraw the pending request: " + rec["withdraw_cmd"])
    emit(ctx, {"hosts": table, "new_instance": new, "recommendation": rec}, lines)
    return exit_code(results) if rec["action"] == "launch-new" and not any(r.get("ok") for _, r in results) else OK


def cmd_extend(ctx, a):
    ctx.need_agent()
    if not a.hours:
        raise Fail("--hours is required", USAGE)
    h = ctx.host(a.host)
    fp = ctx.policy["fit"]
    req = dict(want_req(ctx, a), op="extend", reason=a.reason, margin_min=fp["end_margin_min"])
    req["new_instance"] = new_instance(fp, req["hours"], a.new_gpu_type or a.gpu_type, a.new_usd_per_hour,
                                       a.new_wait_min, a.stage_min)
    r = ctx.call(h, req)
    if not r.get("ok"):
        lines = fail_lines(h["name"], r)
    elif r.get("request"):
        r["text"] = request_text(r["request"], h["name"])
        lines = ["%s: %s request %s" % (h["name"], "existing" if r.get("existing") else "filed", r["request"]["id"]),
                 "  show the owner this text:", "  " + r["text"]]
    else:
        lines = ["%s: %s" % (h["name"], r.get("note"))]
    emit(ctx, dict(r, host=h["name"]), lines)
    return r.get("code", OK)


def cmd_decide(ctx, a, decision):
    ctx.need_owner_identity()
    h = ctx.host(a.host)
    r = ctx.call(h, {"op": "decide", "request": a.request, "decision": decision,
                     "reason": getattr(a, "reason", None)})
    if not r.get("ok"):
        lines = fail_lines(h["name"], r)
    else:
        q = r["request"]
        lines = ["%s: request %s %s%s" % (h["name"], q["id"], q["state"],
                                          (" (planned end now %s)" % local_time(parse_iso(q["new_planned_end"])))
                                          if q.get("new_planned_end") else "")]
        if decision == "approve":
            lines.append("  the owner's teardown plan must move to the new planned end too")
    emit(ctx, dict(r, host=h["name"]), lines)
    return r.get("code", OK)


def add_common(p, top):
    d = (lambda v: v) if top else (lambda v: argparse.SUPPRESS)
    p.add_argument("--hosts", default=d(None), help="use exactly this inventory JSON [{name, target, opts}] instead "
                   "of the project's hosts.json merged with the hosts other projects share (or HOSTCLAIMS_HOSTS)")
    p.add_argument("--project", default=d(None), help="project folder to act for (default: found from here)")
    p.add_argument("--local-root", default=d(None), help="work on a claims folder on this machine instead (no ssh)")
    p.add_argument("--ssh", default=d(None), help="ssh program (default ssh, or HOSTCLAIMS_SSH)")
    p.add_argument("--policy", default=d(None), help="policy JSON: weights, classes, fit and audit thresholds "
                   "(default: the policy block of <pack>/.memory/resource-sharing.json, or HOSTCLAIMS_POLICY)")
    p.add_argument("--agent", default=d(None), help="who acts: default HOSTCLAIMS_AGENT, else the project's slug")
    p.add_argument("--as-owner", action="store_true", default=d(False),
                   help="outside a project: confirm that owner actions (install, lease, approve, decline) act for --agent")
    p.add_argument("--timeout", type=float, default=d(None), help="seconds per host call (default 180)")
    p.add_argument("--json", action="store_true", default=d(False), help="print JSON")


def add_want(p, required):
    p.add_argument("--job", required=required, help="job id, unique per agent (letters, digits, _ and -)")
    p.add_argument("--class", dest="cls", help="priority class P0-P3 (default P1)")
    p.add_argument("--preemptible", dest="preemptible", action="store_const", const=True, default=None,
                   help="may be asked to yield (P2 and P3 are by default; P0 never)")
    p.add_argument("--no-preemptible", dest="preemptible", action="store_const", const=False)
    p.add_argument("--borrowed", action="store_true", help="runs beyond this agent's fair share")
    p.add_argument("--cores", type=int, required=required, help="CPU cores to pin")
    p.add_argument("--ram", required=required, help="RAM to reserve, e.g. 32G")
    p.add_argument("--disk", help="disk to reserve, e.g. 50G")
    p.add_argument("--gpu", action="append", help="INDEX|any[:SHARE[:MEM]], repeat per GPU (share defaults by class)")
    p.add_argument("--gpus", type=int, help="N GPUs picked automatically")
    p.add_argument("--gpu-type", help="GPU name must contain this, e.g. T4")
    p.add_argument("--gpu-mem", help="GPU memory at least, e.g. 40G")
    p.add_argument("--hours", help="expected duration, e.g. 90m or 2.5h (required on paid hosts)")


def build_parser():
    ap = argparse.ArgumentParser(prog="hostclaims.py", description="Share hosts between agents through claim files kept "
                                 "on each host. See the resource-sharing skill.")
    add_common(ap, True)
    sub = ap.add_subparsers(dest="command", metavar="<command>")
    sub.required = True

    def cmd(name, text):
        sp = sub.add_parser(name, help=text, description=text)
        add_common(sp, False)
        return sp

    sp = cmd("install", "owner: create or update the claims folder on hosts (idempotent)")
    sp.add_argument("--host", action="append", help="inventory host (repeat; default: the only host)")
    sp.add_argument("--all", action="store_true", help="every host this project owns in its inventory, with the "
                    "current share_with: run it right after adding machines or changing share_with")
    sp.add_argument("--owner", help="hand the host to another project (the new owner)")
    sp.add_argument("--share-with", help="comma list of projects admitted besides the owner ('*' for all, 'none'); "
                    "default: share_with from resource-sharing.json")
    sp.add_argument("--mode", help="shared (default), draining, or dedicated:<agent>")
    sp.add_argument("--reserve-cores", type=int)
    sp.add_argument("--reserve-ram")
    sp.add_argument("--disk-free-pct", type=float)
    sp.add_argument("--disk-path", help="filesystem the disk numbers come from (default: the claims folder's)")
    sp.add_argument("--launcher", choices=("tmux", "setsid"))
    sp.add_argument("--rule", action="append", metavar="KEY=VALUE", help="host rule: " + ", ".join(sorted(HOST_RULES)))
    sp.add_argument("--lease", action="append", metavar="KEY=VALUE", help="lease field: " + ", ".join(LEASE_KEYS))
    sp = cmd("uninstall", "remove the claims folder (refused while claims are live)")
    sp.add_argument("--host")
    sp = cmd("status", "host tags, free capacity, claims, requests")
    sp.add_argument("--host", action="append")
    sp = cmd("shared", "guest: the hosts other projects share with this one against the seen list: new, gone and "
             "changed hosts (exit 6 until --ack); no ssh unless --probe")
    sp.add_argument("--ack", action="store_true", help="record the current set as seen, after acting on the changes")
    sp.add_argument("--probe", action="store_true", help="also read each host's tag, free capacity and GPUs, and "
                    "whether it admits this project (ssh)")
    sp.add_argument("--seen", help="the seen list (default <pack>/.memory/%s; required with --hosts)" % SEEN_NAME)
    yield_with_help = ("yield after this job of yours on the same host: a yield request on either claim goes to both, "
                       "the named job first (repeat for more; 'none' clears)")
    for name, text in (("claim", "reserve capacity for a job you start yourself (idempotent per agent and job; a repeat "
                                 "renews it, and --pid attaches processes)"),
                       ("run", "claim, then start the job in tmux, pinned and watched (put the command after --)")):
        sp = cmd(name, text)
        sp.add_argument("--host")
        add_want(sp, True)
        sp.add_argument("--brief", help="where the job's brief lives")
        sp.add_argument("--note")
        sp.add_argument("--yield-with", action="append", metavar="JOB", help=yield_with_help)
        if name == "claim":
            sp.add_argument("--pid", type=int, action="append", help="tie the claim to this running process (repeat "
                            "for more; on an existing claim of yours, run's included, it attaches the process)")
        else:
            sp.add_argument("--cwd", help="working folder on the host (default home)")
            sp.add_argument("--done-file", help="also write the done marker here")
            sp.add_argument("--yield-signal", choices=SIGNALS, help="signal sent on a yield request (default TERM; "
                            "none: no signal - the job reads $HOSTCLAIMS_YIELD_FILE and stops itself within the grace, "
                            "after which KILL still comes)")
            sp.add_argument("--grace-min", type=float, help="minutes from a yield request to KILL (default 10)")
            sp.add_argument("--rerun", action="store_true", help="start again although this job ended moments ago")
    sp = cmd("renew", "change your live claim without releasing it: its end, preemptible or borrowed, more processes, "
             "its yield order; refreshes a bare claim's heartbeat")
    sp.add_argument("--host")
    sp.add_argument("--job", help="the claim's job")
    sp.add_argument("--all", action="store_true", help="every claim of yours on the host (not with --pid or "
                    "--yield-with)")
    sp.add_argument("--hours", help="new end this long from now, e.g. 90m or 6h")
    sp.add_argument("--until", help="new end at this time (ISO time or +3h); past a paid lease's planned end it is "
                    "refused (ask with extend)")
    sp.add_argument("--preemptible", dest="preemptible", action="store_const", const=True, default=None,
                    help="may now be asked to yield (P0 never)")
    sp.add_argument("--no-preemptible", dest="preemptible", action="store_const", const=False,
                    help="may no longer be asked to yield (P3 always may)")
    sp.add_argument("--borrowed", dest="borrowed", action="store_const", const=True, default=None,
                    help="runs beyond this agent's fair share")
    sp.add_argument("--no-borrowed", dest="borrowed", action="store_const", const=False)
    sp.add_argument("--pid", type=int, action="append", help="attach this running process (repeat for more)")
    sp.add_argument("--yield-with", action="append", metavar="JOB", help=yield_with_help)
    sp = cmd("release", "release your claim (--stop to stop a running job first)")
    sp.add_argument("--host")
    sp.add_argument("--job")
    sp.add_argument("--claim")
    sp.add_argument("--stop", action="store_true")
    sp.add_argument("--reason")
    sp = cmd("reap", "move stale claims to stale/ (never deletes; orphans stay)")
    sp.add_argument("--host", action="append")
    sp = cmd("yield", "ask a preemptible lower-priority claim to checkpoint and stop")
    sp.add_argument("--host")
    sp.add_argument("--claim", required=True, help="the claim to ask")
    sp.add_argument("--job", help="your job that needs the room")
    sp.add_argument("--class", dest="cls", required=True, help="your job's class")
    sp.add_argument("--borrowed", action="store_true", help="your job would run as borrowed")
    sp.add_argument("--reason")
    sp = cmd("usage", "GPU-hours and CPU-hours per agent from the hosts' ledgers")
    sp.add_argument("--host", action="append")
    sp.add_argument("--window", default="24h")
    sp = cmd("pool", "named shared counters with per-agent caps in one coordination file")
    sp.add_argument("action", choices=("show", "define", "acquire", "release"))
    sp.add_argument("--file", help="PATH or HOST:PATH of the pool file (or HOSTCLAIMS_POOL_FILE)")
    sp.add_argument("--name", help="pool name")
    sp.add_argument("--preset", help="define: JSON file with a pools map")
    sp.add_argument("--capacity", type=float)
    sp.add_argument("--cap", action="append", metavar="AGENT=N", help="per-agent cap ('*' for everyone else)")
    sp.add_argument("--window-hours", type=float, help="budget pools: closed holds count for this long")
    sp.add_argument("--borrow-max-hours", type=float)
    sp.add_argument("--default-hours", type=float)
    sp.add_argument("--unit")
    sp.add_argument("--note")
    sp.add_argument("--id", help="hold id, unique per agent")
    sp.add_argument("--amount", type=float)
    sp.add_argument("--hours", help="hold length (acquire again to extend)")
    sp.add_argument("--borrow", action="store_true")
    sp.add_argument("--actual", type=float, help="release: amount really used (budget pools)")
    sp = cmd("lease", "show or set a host's lease (kind none, free or paid)")
    sp.add_argument("--host")
    sp.add_argument("--set", action="append", metavar="KEY=VALUE", help=", ".join(LEASE_KEYS))
    for name, text in (("extend", "ask a host's owner to move its planned end for your job"),
                       ("fit", "rank hosts for a job against launching a new instance")):
        sp = cmd(name, text)
        sp.add_argument("--host", action="append" if name == "fit" else None)
        add_want(sp, False)
        sp.add_argument("--new-usd-per-hour", type=float, help="price of a new instance")
        sp.add_argument("--new-gpu-type", help="GPU type to price a new instance by (default --gpu-type)")
        sp.add_argument("--new-wait-min", type=float, help="minutes until a new instance is ready (default from policy)")
        sp.add_argument("--stage-min", type=float, default=0.0, help="data staging minutes on a new instance")
        if name == "extend":
            sp.add_argument("--reason")
        else:
            sp.add_argument("--can-wait", action="store_true",
                            help="the job can wait for the owner's answer until the request expires")
    sp = cmd("request", "guest: ask the owner for maintenance, object to a release before a time, or withdraw a request")
    sp.add_argument("--host")
    sp.add_argument("--type", choices=("maintenance", "objection"))
    sp.add_argument("--reason", help="what you need and why")
    sp.add_argument("--withdraw", metavar="ID", help="take back your own pending request (for example after launching new)")
    sp.add_argument("--until", help="objection: keep the host until this time (ISO time or +3h)")
    sp.add_argument("--job")
    sp = cmd("approve", "owner: approve a request (an extension moves the planned end)")
    sp.add_argument("--host")
    sp.add_argument("--request", required=True)
    sp = cmd("decline", "owner: decline a request")
    sp.add_argument("--host")
    sp.add_argument("--request", required=True)
    sp.add_argument("--reason")
    sp = cmd("audit", "owner: your hosts' lease, idle time, use by project, cost so far, requests, and a recommendation")
    sp.add_argument("--host", action="append")
    sp.add_argument("--window", help="usage window (default 24h)")
    sp = sub.add_parser("_wrap")
    sp.add_argument("--root", required=True)
    sp.add_argument("--claim", required=True)
    return ap


COMMANDS = {"install": cmd_install, "uninstall": cmd_uninstall, "status": cmd_status, "claim": cmd_claim,
            "run": lambda ctx, a: cmd_claim(ctx, a, launch=True), "renew": cmd_renew, "release": cmd_release,
            "reap": cmd_reap,
            "yield": cmd_yield, "usage": cmd_usage, "pool": cmd_pool, "lease": cmd_lease, "extend": cmd_extend,
            "fit": cmd_fit, "request": cmd_request, "audit": cmd_audit, "shared": cmd_shared,
            "approve": lambda ctx, a: cmd_decide(ctx, a, "approve"),
            "decline": lambda ctx, a: cmd_decide(ctx, a, "decline")}


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    cmd = []
    if "--" in argv:
        i = argv.index("--")
        argv, cmd = argv[:i], argv[i + 1:]
    a = build_parser().parse_args(argv)
    if a.command == "_wrap":
        return wrap_main(a.root, a.claim)
    a.cmd = cmd
    if cmd and a.command != "run":
        print("hostclaims: only run takes a command after --", file=sys.stderr)
        return USAGE
    if a.command == "run" and not cmd:
        print("hostclaims: run needs the job's command after --", file=sys.stderr)
        return USAGE
    try:
        ctx = Ctx(a)
        return COMMANDS[a.command](ctx, a)
    except Fail as e:
        if getattr(a, "json", False):
            print(json.dumps(dict(e.extra, ok=False, code=e.code, error=str(e)), indent=1, sort_keys=True))
        else:
            print("hostclaims: %s" % e, file=sys.stderr)
        return e.code
    except ValueError as e:
        print("hostclaims: %s" % e, file=sys.stderr)
        return USAGE


if __name__ == "__main__":
    sys.exit(main())
