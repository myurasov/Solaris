# rev. 2

"""kaggle_share: share one Kaggle account's sessions and GPU quota between projects.

One Kaggle account serves every competition project: runs are capped at a few
concurrent sessions (by default 5 CPU and 2 GPU) and GPU time at a weekly quota.
This tool finds the projects actively using the account, splits those resources
between them - equally by default, or as the user directs - and hands out a
lease before each run.

    python3 <plugin-dir>/tools/kaggle_share.py status [--json]
    python3 <plugin-dir>/tools/kaggle_share.py acquire (--kind cpu|gpu | --path <kernel dir>) [--wait MIN]
    python3 <plugin-dir>/tools/kaggle_share.py release [<lease id>] [--kernel REF | --path DIR] [--all]
    python3 <plugin-dir>/tools/kaggle_share.py ledger
    python3 <plugin-dir>/tools/kaggle_share.py config [--equal | --only P | --weight P=W] [--cap P:KIND=N] ...
    python3 <plugin-dir>/tools/kaggle_share.py stamp

Detection. Account-wide, through the gateway (--gateway; default the kaggle.py
beside this file): the GPU quota, the account's kernels, and the status of each
one run in the last 12 hours (scan_hours in sharing.json) - this covers
projects on any machine. On this machine: activity stamps, one per project or
task folder, that the gateway writes on each call (stamp()). A project is
active when it made gateway calls in the last 24 hours, has a queued or running
kernel, or holds a lease or a waiting request. Kernels map to projects through
the ids in their kernel-metadata.json files (projects in the Solaris tree with
an ai or aipack pack, embedded repos included, and any folder a stamp names).
Leases, waiting requests and ledger entries follow the project folder, so two
projects with the same folder name never share a count.

Split. Every active project gets an equal share of each session pool (rounded
up) and of the weekly GPU hours; sharing.json overrides that with fixed
weights, per-project caps, or "only these projects". A project may take one
more session within its share; beyond it only while the other sharing projects
use none of that kind and none is waiting (a borrowed lease). The pool limit
always holds.

State lives in ~/.solaris/kaggle/ (KAGGLE_SHARE_DIR or --state moves it; never
~/.kaggle, which holds credentials): activity/ (stamps), sharing.json (the
split), state.json (leases, waiting requests), ledger.jsonl (closed leases: the
hours by project), account.json (the last account read), .lock (flock). Kaggle
is only read. Exit codes: 0 ok, 1 error, 2 bad usage, 3 refused. Stdlib only.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import secrets
import shutil
import subprocess
import sys
import tempfile
import time
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

try:
    import fcntl
except ImportError:  # no flock on this platform: state writes go unlocked
    fcntl = None

SCHEMA = 1
ISO = "%Y-%m-%dT%H:%M:%SZ"
ENV_DIR = "KAGGLE_SHARE_DIR"
# set on the plugin's own monitoring calls, so checking never makes a project look active
QUIET_ENV = "KAGGLE_SHARE_QUIET"
PACKS = ("ai", "aipack")
KINDS = ("cpu", "gpu")
DEFAULTS = {"limits": {"cpu": 5, "gpu": 2, "gpu_hours": 30.0}, "reserve": {"cpu": 0, "gpu": 0},
            "active_hours": 24, "lease_hours": 12, "scan_hours": 12}
# kernel run states (kagglesdk KernelWorkerStatus); a cancel in progress still holds its session
RUNNING = {"QUEUED", "RUNNING", "CANCEL_REQUESTED"}
DONE = {"COMPLETE", "ERROR", "CANCEL_ACKNOWLEDGED", "NEW_SCRIPT"}
STATUS_RE = re.compile(r'has status "(?:KernelWorkerStatus\.)?([A-Z_]+)"')
SKIP_DIRS = {"node_modules", "site-packages"}
LIST_PAGE = 50
MAX_STATUS = 25
FRESH_SECONDS = 90
EXIT_REFUSED = 3
# Kaggle's clock and this machine's can differ: a run started this much before a lease can still be its run
CLOCK_SLACK = timedelta(minutes=10)


class ShareError(Exception):
    pass


# ---- time

def now_utc():
    return datetime.now(timezone.utc).replace(microsecond=0)


def iso(t):
    return t.astimezone(timezone.utc).strftime(ISO)


def parse_time(text):
    """A stored or Kaggle time (ISO 8601; no offset means UTC)."""
    t = datetime.fromisoformat(str(text).strip().replace("Z", "+00:00"))
    return t if t.tzinfo else t.replace(tzinfo=timezone.utc)


def local(t, fmt="%b %d %H:%M %Z"):
    # shown in this machine's time zone
    return t.astimezone().strftime(fmt)


def ago(t, now):
    s = max(0.0, (now - t).total_seconds())
    if s < 90:
        return f"{int(s)} s ago"
    if s < 5400:
        return f"{int(s // 60)} min ago"
    return f"{s / 3600:.1f} h ago" if s < 172800 else f"{s / 86400:.1f} d ago"


# ---- files

def state_dir(path=None):
    return Path(path or os.environ.get(ENV_DIR) or "~/.solaris/kaggle").expanduser()


@contextmanager
def locked(base):
    # one writer at a time: agents, the gateway and waiting requests share these files
    base = Path(base)
    base.mkdir(parents=True, exist_ok=True)
    with open(base / ".lock", "a") as f:
        if fcntl:
            try:
                fcntl.flock(f, fcntl.LOCK_EX)
            except OSError:
                pass  # no flock on this filesystem: go unlocked
        yield


def read_json(path, default):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def write_json(path, data):
    # atomic replace: a reader never sees half a file
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=1, ensure_ascii=False)
        f.write("\n")
    os.replace(tmp, path)


# ---- contexts, kernels and stamps

def context_kind(d):
    """'project' for a project root, 'task' for an ad-hoc task folder, else None."""
    d = Path(d)
    if any((d / p / "manifest.json").is_file() for p in PACKS):
        return "project"
    notes = d / "notes.md"
    if notes.is_file() and "ad-hoc-task" in notes.read_text(errors="replace")[:4096]:
        return "task"
    return None


def find_context(start=None):
    """The project root or ad-hoc task folder around start (a project wins), else None."""
    cwd = Path(start or os.getcwd()).resolve()
    chain = (cwd, *cwd.parents)
    for want in ("project", "task"):
        for d in chain:
            if context_kind(d) == want:
                return d
    # a copied overlay sits at <project>/<pack>/plugins/kaggle/tools/kaggle_share.py
    up = Path(__file__).resolve().parents
    if len(up) > 4 and up[2].name == "plugins" and up[3].name in PACKS and (up[3] / "manifest.json").is_file():
        return up[4]
    return None


def find_solaris(*starts):
    """The Solaris checkout holding one of these paths (or this tool), else None."""
    for s in (*starts, Path(__file__).resolve()):
        if not s:
            continue
        p = Path(s).resolve()
        for d in (p, *p.parents):
            if (d / "solaris" / "solaris.agent.md").is_file() and (d / "projects").is_dir():
                return d
    return None


def tree_projects(solaris):
    """Project roots under <solaris>/projects/: <slug>, <group>/<slug>, and the embedded repos under them."""
    base = Path(solaris) / "projects"
    found = []
    # shallow first, so a folder inside a project already found is never a project of its own
    for pattern in ("*", "*/*", "*/*/*"):
        for d in sorted(base.glob(pattern)):
            if d.is_dir() and not any(p in found for p in d.parents) and context_kind(d) == "project":
                found.append(d)
    return sorted(found)


def meta_kind(meta):
    """cpu, gpu or tpu from a kernel-metadata.json."""
    shape = str(meta.get("machine_shape") or "").lower()
    if str(meta.get("enable_tpu")).lower() == "true" or "tpu" in shape:
        return "tpu"
    if str(meta.get("enable_gpu")).lower() == "true" or "nvidia" in shape or "gpu" in shape:
        return "gpu"
    return "cpu"


def kernel_files(root, depth=4):
    """kernel-metadata.json files under a context, skipping hidden, local-only (__*) and package folders."""
    found = []

    def walk(d, left):
        try:
            entries = list(os.scandir(d))
        except OSError:
            return
        for e in entries:
            if e.name == "kernel-metadata.json" and e.is_file():
                found.append(Path(e.path))
            elif (left > 0 and e.is_dir(follow_symlinks=False) and not e.name.startswith((".", "__"))
                  and e.name not in SKIP_DIRS):
                walk(e.path, left - 1)

    walk(root, depth)
    return found


def read_kernel(path):
    """(ref, kind) of one kernel folder or kernel-metadata.json."""
    p = Path(path)
    meta = read_json(p / "kernel-metadata.json" if p.is_dir() else p, None)
    raw = str(meta.get("id") or "").strip() if isinstance(meta, dict) else ""
    # kernels init writes the placeholder <user>/INSERT_KERNEL_SLUG_HERE; real slugs may contain "insert"
    if "/" not in raw or "INSERT_" in raw:
        raise ShareError(f"no kernel id in {p}")
    return raw.lower(), meta_kind(meta)


def context_kernels(root):
    kernels = {}
    for f in kernel_files(root):
        try:
            ref, kind = read_kernel(f)
        except ShareError:
            continue
        kernels.setdefault(ref, kind)
    return kernels


def has_kaggle_plugin(root):
    return any((Path(root) / p / "plugins" / "kaggle").is_dir()
               or (Path(root) / p / "plugins" / "kaggle.link.md").is_file() for p in PACKS)


def stamp_path(base, root):
    root = str(Path(root).resolve())
    name = re.sub(r"[^A-Za-z0-9._-]", "_", Path(root).name)[:60] or "root"
    return Path(base) / "activity" / f"{name}-{hashlib.sha1(root.encode()).hexdigest()[:10]}.json"


def command_of(argv):
    # only the command words (e.g. "kernels push"), never arguments: paths, messages and ids stay out
    words = []
    for a in argv:
        if str(a).startswith("-") or len(words) == 2:
            break
        words.append(str(a))
    return " ".join(words)


def stamp(ctx, argv=(), now=None, base=None):
    """Record one gateway call made from the context folder ctx (the gateway hook); returns the stamp path.

    Calls carrying KAGGLE_SHARE_QUIET=1 (the plugin's own monitoring) are not recorded: returns None.
    """
    if os.environ.get(QUIET_ENV) == "1":
        return None
    ctx = Path(ctx).resolve()
    path = stamp_path(state_dir(base), ctx)
    t = iso(now or now_utc())
    old = read_json(path, {})
    old = old if isinstance(old, dict) else {}
    write_json(path, {"schema": SCHEMA, "root": str(ctx), "name": ctx.name, "kind": context_kind(ctx) or "folder",
                      "first": old.get("first", t), "last": t, "calls": int(old.get("calls", 0)) + 1,
                      "command": command_of(argv)})
    return path


def load_stamps(base):
    out = []
    for p in sorted((Path(base) / "activity").glob("*.json")):
        s = read_json(p, None)
        if isinstance(s, dict) and s.get("root") and s.get("last"):
            out.append(s)
    return out


def gather(solaris, stamps):
    """Every Kaggle-using context: {name: {name, root, kind, last, calls, kernels {ref: kind}}}.

    A name is the folder name; where several folders share one, each gets its parent folder added
    ("alpha (my)"). The root is the identity: leases and the ledger follow it, not the name.
    """
    roots = {}
    for d in tree_projects(solaris) if solaris else []:
        roots[str(d.resolve())] = {"kind": "project"}
    for s in stamps:
        if Path(s["root"]).is_dir():
            roots.setdefault(s["root"], {"kind": s.get("kind") or "folder"})["stamp"] = s
    found = []
    for r, info in sorted(roots.items()):
        root, s = Path(r), info.get("stamp") or {}
        kernels = context_kernels(root)
        if not kernels and not s and not has_kaggle_plugin(root):
            continue  # nothing to do with Kaggle
        found.append((r, root, info, s, kernels))
    counts = {}
    for _r, root, *_rest in found:
        counts[root.name] = counts.get(root.name, 0) + 1
    contexts = {}
    for r, root, info, s, kernels in found:
        name = root.name if counts[root.name] == 1 else f"{root.name} ({root.parent.name})"
        if name in contexts:
            # the parent folder repeats too: a short id of the path keeps names apart
            name = f"{name} {hashlib.sha1(r.encode()).hexdigest()[:4]}"
        contexts[name] = {"name": name, "root": r, "kind": info["kind"], "last": s.get("last"),
                          "calls": s.get("calls", 0), "kernels": kernels}
    return contexts


def name_for(contexts, root, default):
    """The name gather() gave the context folder root, else default."""
    r = str(Path(root).resolve()) if root else None
    return next((n for n, c in contexts.items() if r and c.get("root") == r), default)


# ---- reading the account

def _run(cmd, cwd):
    try:
        p = subprocess.run(cmd, cwd=str(cwd), stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                           encoding="utf-8", errors="replace", timeout=180, env={**os.environ, QUIET_ENV: "1"})
    except subprocess.TimeoutExpired:
        return 124, "timed out"
    return p.returncode, p.stdout if p.returncode == 0 else p.stdout + p.stderr


def json_rows(text):
    """The JSON list in a CLI response, skipping notices before it."""
    lines = text.splitlines()
    for i, line in enumerate(lines):
        if line.strip().startswith(("[", "{")):
            try:
                data, _ = json.JSONDecoder().raw_decode("\n".join(lines[i:]).strip())
            except ValueError:
                continue
            return data if isinstance(data, list) else [data]
    if "Not found" in text or "No quota information" in text:
        return []
    raise ShareError("no JSON in the Kaggle output")


def _hours(text):
    # quota values print as "1.52h"
    try:
        return float(str(text).strip().rstrip("h"))
    except ValueError:
        return None


def parse_quota(text):
    quota = {}
    for r in json_rows(text):
        name = str(r.get("resource", "")).lower()
        if name in ("gpu", "tpu"):
            quota[name] = {"used": _hours(r.get("used")), "remaining": _hours(r.get("remaining")),
                           "total": _hours(r.get("total")), "refresh": r.get("refreshAt") or None}
    return quota


def parse_status(text):
    m = STATUS_RE.search(text)
    if not m:
        raise ShareError(f"no kernel status in: {text.strip()[:120]}")
    return m.group(1)


def scan_account(gateway, cwd, *, run=_run, now=None, cache=None, scan_hours=12):
    """GPU quota, the account's recent kernels and the status of each one run within scan_hours.

    A finished run stays finished until its kernel runs again, so cached finished states are reused.
    """
    now = now or now_utc()
    calls, errors = 0, []

    def call(*args):
        nonlocal calls
        calls += 1
        code, out = run([sys.executable, str(gateway), *args], cwd)
        if code != 0:
            raise ShareError(f"`{' '.join(args[:2])}` failed (exit {code}): {out.strip()[-200:]}")
        return out

    quota = None
    try:
        quota = parse_quota(call("quota", "--format", "json"))
    except ShareError as e:
        errors.append(str(e))
    rows = None
    try:
        rows = json_rows(call("kernels", "list", "--mine", "--sort-by", "dateRun", "--page-size", str(LIST_PAGE),
                              "--format", "json"))
    except ShareError as e:
        errors.append(str(e))
    known = {k["ref"]: k for k in (cache or {}).get("kernels", [])}
    cutoff = now - timedelta(hours=float(scan_hours))
    recent = []
    for r in rows or []:
        ref = str(r.get("ref") or "").lower()
        try:
            last = parse_time(r.get("lastRunTime"))
        except (TypeError, ValueError):
            continue
        if ref and last >= cutoff:
            recent.append((ref, str(r.get("lastRunTime"))))
    kernels = []
    for ref, last in recent[:MAX_STATUS]:
        old = known.get(ref)
        if old and old.get("last_run") == last and old.get("status") in DONE:
            status = old["status"]
        else:
            try:
                status = parse_status(call("kernels", "status", ref))
            except ShareError as e:
                errors.append(str(e))
                status = "UNKNOWN"
        kernels.append({"ref": ref, "last_run": last, "status": status})
    return {"at": iso(now), "listed": rows is not None, "quota": quota, "kernels": kernels,
            "truncated": max(0, len(recent) - MAX_STATUS), "calls": calls, "errors": errors,
            "kinds": dict((cache or {}).get("kinds", {}))}


def probe_kind(ref, gateway, cwd, run=_run):
    """Kind of a kernel no local folder describes: pull only its metadata into a throwaway folder (read-only)."""
    base = Path(cwd) / "__data" / "kaggle"
    base.mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.mkdtemp(prefix=".probe-", dir=base))
    try:
        code, _ = run([sys.executable, str(gateway), "kernels", "pull", ref, "-p", str(tmp), "-m"], cwd)
        meta = read_json(tmp / "kernel-metadata.json", None)
        return meta_kind(meta) if code == 0 and isinstance(meta, dict) else None
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def get_account(base, gateway, cwd, *, max_age=FRESH_SECONDS, offline=False, probe_refs=None, run=_run,
                now=None, scan_hours=12):
    """The account read: the cached one when fresh (or offline), else a new read saved to account.json."""
    now = now or now_utc()
    cached = read_json(Path(base) / "account.json", None)
    cached = cached if isinstance(cached, dict) and cached.get("at") else None
    if offline or (cached and max_age and (now - parse_time(cached["at"])).total_seconds() < max_age):
        return cached
    account = scan_account(gateway, cwd, run=run, now=now, cache=cached, scan_hours=scan_hours)
    for ref in probe_refs(account) if probe_refs else []:
        kind = probe_kind(ref, gateway, cwd, run)
        account["calls"] += 1
        if kind:
            account["kinds"][ref] = kind
    with locked(base):
        write_json(Path(base) / "account.json", account)
    return account


# ---- config, leases and the ledger

def load_config(base):
    """The user's split (sharing.json) over the defaults."""
    raw = read_json(Path(base) / "sharing.json", {})
    raw = raw if isinstance(raw, dict) else {}
    cfg = {"weights": {}, "only": [], "caps": {}, "note": None}
    cfg.update(json.loads(json.dumps(DEFAULTS)))
    for k in ("weights", "only", "caps", "note", "active_hours", "lease_hours", "scan_hours"):
        if raw.get(k) is not None:
            cfg[k] = raw[k]
    for k in ("limits", "reserve"):
        cfg[k].update(raw.get(k) if isinstance(raw.get(k), dict) else {})
    # tolerate hand edits: "only": "X" means ["X"]
    if isinstance(cfg["only"], str):
        cfg["only"] = [cfg["only"]]
    for k in ("weights", "caps"):
        if not isinstance(cfg[k], dict):
            cfg[k] = {}
    cfg["mode"] = "only" if cfg["only"] else ("weights" if cfg["weights"] else "auto")
    return cfg


def load_state(base):
    s = read_json(Path(base) / "state.json", {})
    s = s if isinstance(s, dict) else {}
    return {"leases": dict(s.get("leases") or {}), "waiters": dict(s.get("waiters") or {})}


def load_ledger(base):
    out = []
    p = Path(base) / "ledger.jsonl"
    if p.is_file():
        for line in p.read_text(encoding="utf-8").splitlines():
            try:
                e = json.loads(line)
            except ValueError:
                continue  # a torn line from an interrupted write
            if isinstance(e, dict) and e.get("end") and e.get("project"):
                out.append(e)
    return out


def close_lease(base, state, lease_id, how, now):
    """Move a lease to the ledger; returns (lease, hours)."""
    lease = state["leases"].pop(lease_id)
    start, until = parse_time(lease["acquired"]), parse_time(lease["expires"])
    end = min(now, until)
    hours = max(0.0, (end - start).total_seconds() / 3600)
    entry = {"project": lease["project"], "root": lease.get("root"), "kind": lease["kind"],
             "kernel": lease.get("kernel"), "start": lease["acquired"], "end": iso(end), "hours": round(hours, 3),
             "borrowed": bool(lease.get("borrowed")), "how": how}
    with open(Path(base) / "ledger.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    return lease, hours


def _alive(pid):
    try:
        os.kill(int(pid), 0)
    except ProcessLookupError:
        return False
    except (PermissionError, TypeError, ValueError, OverflowError):
        return True
    return True


def run_finished(lease, run):
    """Whether Kaggle shows the leased kernel's run as over.

    The run must not be the one Kaggle showed when the lease was taken (prior_run, both times on
    Kaggle's clock), and must have started no earlier than the lease less CLOCK_SLACK (Kaggle's
    clock against this machine's).
    """
    if not run or run["status"] not in DONE:
        return False
    if "prior_run" in lease and run["last_run"] == lease["prior_run"]:
        return False
    return parse_time(run["last_run"]) >= parse_time(lease["acquired"]) - CLOCK_SLACK


def tidy(base, state, account, now):
    """Close expired leases and leases whose kernel run has finished; drop gone waiters. Returns closed ones."""
    closed = []
    runs = {k["ref"]: k for k in (account or {}).get("kernels", [])}
    for lid, lease in list(state["leases"].items()):
        run = runs.get(str(lease.get("kernel") or "").lower())
        if now >= parse_time(lease["expires"]):
            closed.append(close_lease(base, state, lid, "expired", now))
        elif run_finished(lease, run):
            closed.append(close_lease(base, state, lid, "finished", now))
    for wid, w in list(state["waiters"].items()):
        if now >= parse_time(w["until"]) or not _alive(w.get("pid")):
            del state["waiters"][wid]
    return closed


# ---- the split

def _ceil(x):
    return int(math.ceil(round(x, 9)))


def _blank(name):
    return {"name": name, "root": None, "kind": "project", "last": None, "calls": 0, "kernels": {}}


def build_view(cfg, contexts, account, state, ledger, now, requester=None):
    """Who is active, what each uses, and each one's share of the sessions and the week's GPU hours."""
    contexts = {n: dict(c) for n, c in contexts.items()}
    # leases, waiting requests and ledger entries belong to their project folder (root), else their stored name
    by_root = {c["root"]: n for n, c in contexts.items() if c.get("root")}

    def owned(x):
        return dict(x, project=by_root.get(x.get("root")) or x["project"])

    leases = [owned(x) for x in state["leases"].values()]
    waiters = [owned(w) for w in state["waiters"].values()]
    ledger = [owned(e) for e in ledger]
    extra = [x["project"] for x in (*leases, *waiters)]
    extra += list(cfg["only"]) + list(cfg["weights"]) + ([requester] if requester else [])
    for n in extra:
        contexts.setdefault(n, _blank(n))
    account = account or {}
    owner = {}
    for n, c in sorted(contexts.items(), key=lambda kv: kv[1].get("last") or ""):
        for ref in c["kernels"]:
            owner[ref] = n  # a kernel in two folders goes to the one used last
    leased = {str(x.get("kernel")).lower(): x for x in leases if x.get("kernel")}
    running = []
    for k in account.get("kernels", []):
        if k["status"] not in RUNNING and k["status"] != "UNKNOWN":
            continue
        lease = leased.get(k["ref"])
        proj = owner.get(k["ref"]) or (lease["project"] if lease else None)
        kind = ((lease or {}).get("kind") or contexts.get(proj, {}).get("kernels", {}).get(k["ref"])
                or account.get("kinds", {}).get(k["ref"]) or "unknown")
        running.append(dict(k, project=proj, kind=kind))
    names = sorted(contexts)
    leases_n = {n: {k: 0 for k in KINDS} for n in names}
    runs_n = {n: {k: 0 for k in KINDS} for n in names}
    for x in leases:
        if x["kind"] in KINDS:
            leases_n[x["project"]][x["kind"]] += 1
    other = {"cpu": 0, "gpu": 0, "tpu": 0, "unknown": 0}
    for r in running:
        if r["project"] in runs_n and r["kind"] in KINDS:
            runs_n[r["project"]][r["kind"]] += 1
        else:
            other[r["kind"] if r["kind"] in other else "unknown"] += 1
    # a lease and the run it was taken for are one session
    used = {n: {k: max(leases_n[n][k], runs_n[n][k]) for k in KINDS} for n in names}
    in_use = {k: sum(used[n][k] for n in names) + other[k] for k in KINDS}
    active = {}
    for n in names:
        c, why = contexts[n], []
        if c.get("last") and now - parse_time(c["last"]) <= timedelta(hours=float(cfg["active_hours"])):
            why.append(f"gateway call {ago(parse_time(c['last']), now)}")
        nrun = sum(1 for r in running if r["project"] == n)
        if nrun:
            why.append(f"{nrun} queued or running")
        nl = sum(1 for x in leases if x["project"] == n)
        if nl:
            why.append(f"{nl} lease" + ("s" if nl > 1 else ""))
        if any(w["project"] == n for w in waiters):
            why.append("waiting")
        if n == requester:
            why.append("asking")
        if why:
            active[n] = why
    if cfg["mode"] == "only":
        weights = {n: 1.0 for n in cfg["only"]}
    elif cfg["mode"] == "weights":
        weights = {n: float(w) for n, w in cfg["weights"].items() if float(w) > 0}
    else:
        weights = {n: 1.0 for n in active}
    total_w = sum(weights.values())
    cap = {k: max(0, int(cfg["limits"][k]) - int(cfg["reserve"].get(k) or 0)) for k in KINDS}
    gpu_q = (account.get("quota") or {}).get("gpu") or {}
    refresh = parse_time(gpu_q["refresh"]) if gpu_q.get("refresh") else None
    week_start = refresh - timedelta(days=7) if refresh and refresh > now else now - timedelta(days=7)
    total_h = gpu_q["total"] if gpu_q.get("total") is not None else float(cfg["limits"]["gpu_hours"])
    hours = {n: 0.0 for n in names}
    for e in ledger:
        if e.get("kind") == "gpu" and e["project"] in hours and parse_time(e["end"]) >= week_start:
            hours[e["project"]] += float(e.get("hours") or 0)
    for x in leases:
        if x["kind"] == "gpu":
            start = max(parse_time(x["acquired"]), week_start)
            hours[x["project"]] += max(0.0, (now - start).total_seconds() / 3600)
    left = gpu_q.get("remaining")
    if left is None:
        left = max(0.0, total_h - sum(hours.values()))
    projects = {}
    for n in names:
        w = weights.get(n, 0.0)
        caps = cfg["caps"].get(n) or {}
        share = {k: _ceil(cap[k] * w / total_w) if w and total_w else 0 for k in KINDS}
        share = {k: min(v, int(caps[k])) if caps.get(k) is not None else v for k, v in share.items()}
        budget = total_h * w / total_w if w and total_w else 0.0
        if caps.get("gpu_hours") is not None:
            budget = min(budget, float(caps["gpu_hours"]))
        projects[n] = {"active": active.get(n), "weight": w, "share": share, "used": used[n],
                       "gpu_hours": round(hours[n], 3), "gpu_budget": round(budget, 3),
                       "root": contexts[n].get("root"), "kernels": len(contexts[n].get("kernels") or {})}
    return {"at": iso(now), "mode": cfg["mode"], "cap": cap, "in_use": in_use, "other": other,
            "projects": projects, "running": running, "leases": leases,
            "waiters": waiters, "quota": account.get("quota"),
            "gpu_hours_total": total_h, "gpu_hours_left": left, "week_start": iso(week_start),
            "account_at": account.get("at"), "listed": account.get("listed", False),
            "errors": account.get("errors", []), "truncated": account.get("truncated", 0),
            "recent": len(account.get("kernels", [])), "calls": account.get("calls", 0)}


def decide(view, cfg, project, kind, hours=None):
    """(granted, borrowed, reason) for one more session of kind for project."""
    p = view["projects"][project]
    caps = cfg["caps"].get(project) or {}
    # a run whose kind is unknown may hold either pool
    if view["in_use"][kind] + view["other"]["unknown"] >= view["cap"][kind]:
        return False, False, f"all {view['cap'][kind]} {kind.upper()} sessions are in use"
    if caps.get(kind) is not None and p["used"][kind] >= int(caps[kind]):
        return False, False, f"{project} is capped at {caps[kind]} {kind.upper()} session(s)"
    if cfg["mode"] == "only" and project not in cfg["only"]:
        return False, False, f"the split gives the account only to {', '.join(cfg['only'])}"
    need = float(hours or 0)
    if kind == "gpu":
        left = view["gpu_hours_left"]
        if left is not None and (left <= 0 or left < need):
            return False, False, f"{left:.1f} GPU hours left this week"
        if caps.get("gpu_hours") is not None and p["gpu_hours"] + need > float(caps["gpu_hours"]):
            return False, False, f"{project} is capped at {caps['gpu_hours']} GPU hours a week"
    over = p["used"][kind] >= p["share"][kind]
    if kind == "gpu" and p["gpu_hours"] + need > p["gpu_budget"]:
        over = True
    if not over:
        return True, False, "within its share"
    busy = [n for n, q in view["projects"].items() if n != project and q["weight"] > 0
            and (q["used"][kind] > 0 or any(w["project"] == n and w["kind"] == kind for w in view["waiters"]))]
    if busy:
        return False, False, f"{project} has used its share and {', '.join(busy)} still use or wait for theirs"
    return True, True, "borrowed while the other projects are idle"


def try_acquire(base, cfg, contexts, account, project, kind, *, kernel=None, root=None, hours=None, note=None,
                now=None):
    """One attempt under the lock; returns (lease or None, reason, view)."""
    now = now or now_utc()
    with locked(base):
        state = load_state(base)
        tidy(base, state, account, now)
        view = build_view(cfg, contexts, account, state, load_ledger(base), now, requester=project)
        ok, borrowed, why = decide(view, cfg, project, kind, hours)
        lease = None
        if ok:
            tag = re.sub(r"[^A-Za-z0-9]+", "-", project).strip("-")[:24] or "project"
            lease = {"id": f"{tag}-{kind}-{now:%m%d%H%M%S}-{secrets.token_hex(2)}", "project": project,
                     "root": str(root) if root else None, "kind": kind, "kernel": kernel, "acquired": iso(now),
                     "expires": iso(now + timedelta(hours=float(cfg["lease_hours"]))), "borrowed": borrowed,
                     "hours": hours, "note": note}
            if kernel and (account or {}).get("listed"):
                # the kernel's last run as Kaggle showed it now: that run never closes this lease
                lease["prior_run"] = next((k["last_run"] for k in account.get("kernels", []) if k["ref"] == kernel),
                                          None)
            state["leases"][lease["id"]] = lease
        write_json(Path(base) / "state.json", state)
    return lease, why, view


def set_waiter(base, waiter=None, remove=None):
    with locked(base):
        state = load_state(base)
        if remove:
            state["waiters"].pop(remove, None)
        if waiter:
            state["waiters"][waiter["id"]] = waiter
        write_json(Path(base) / "state.json", state)


def _owner_is(lease, project, root):
    # a lease taken inside a project folder matches that folder; one taken with --project matches the name
    if root and lease.get("root"):
        return lease["root"] == str(root)
    return project is None or lease["project"] == project


def release(base, *, ids=(), project=None, root=None, kernel=None, kind=None, all_=False, now=None):
    """Close matching leases into the ledger; returns [(lease, hours)]."""
    now = now or now_utc()
    with locked(base):
        state = load_state(base)
        if ids:
            missing = [i for i in ids if i not in state["leases"]]
            if missing:
                raise ShareError(f"no such lease: {', '.join(missing)}")
            match = list(ids)
        else:
            match = [i for i, x in state["leases"].items() if _owner_is(x, project, root)
                     and (kernel is None or str(x.get("kernel") or "").lower() == kernel.lower())
                     and (kind is None or x["kind"] == kind)]
            if len(match) > 1 and not all_:
                listed = "\n".join(f"  {i}  {state['leases'][i]['kind']}  {state['leases'][i].get('kernel') or '-'}"
                                   for i in match)
                raise ShareError(f"{len(match)} leases match - name one, or pass --all:\n{listed}")
        closed = [close_lease(base, state, i, "released", now) for i in match]
        write_json(Path(base) / "state.json", state)
    return closed


def gpu_ledger(base, now=None, week_start=None):
    """GPU and CPU lease hours by project: this Kaggle week and in total."""
    now = now or now_utc()
    week_start = week_start or now - timedelta(days=7)
    out = {}
    for e in load_ledger(base):
        row = out.setdefault(e["project"], {"gpu_week": 0.0, "gpu_total": 0.0, "cpu_week": 0.0, "cpu_total": 0.0,
                                            "runs": 0, "borrowed": 0})
        k, h = e.get("kind"), float(e.get("hours") or 0)
        if k in KINDS:
            row[f"{k}_total"] += h
            if parse_time(e["end"]) >= week_start:
                row[f"{k}_week"] += h
        row["runs"] += 1
        row["borrowed"] += 1 if e.get("borrowed") else 0
    return out


# ---- commands

def _ctx_name(root):
    return Path(root).name if root else None


def _gateway(a):
    return Path(a.gateway).resolve() if a.gateway else Path(__file__).resolve().parent / "kaggle.py"


def _world(a, base, root, *, max_age, offline):
    """(config, contexts, account) for a command."""
    cfg = load_config(base)
    solaris = None if a.solaris == "none" else (Path(a.solaris) if a.solaris else find_solaris(root, os.getcwd()))
    contexts = gather(solaris, load_stamps(base))
    gw = _gateway(a)
    if not offline and not gw.is_file():
        raise ShareError(f"gateway not found: {gw} (pass --gateway, or --offline)")
    known = {ref for c in contexts.values() for ref in c["kernels"]}

    def unknown_refs(acc):
        # running kernels no local folder describes (other machines, the web editor)
        return [k["ref"] for k in acc["kernels"] if k["status"] in RUNNING and k["ref"] not in known
                and k["ref"] not in acc["kinds"]][:5]

    # probes download into the context folder, so only from inside one
    probe = unknown_refs if getattr(a, "probe", False) and root else None
    account = get_account(base, gw, root or Path.cwd(), max_age=max_age, offline=offline, probe_refs=probe,
                          scan_hours=float(cfg["scan_hours"]))
    return cfg, contexts, account


def _cell(used, share):
    return f"{used}/{share}"


def print_view(view, cfg, now):
    q = view["quota"] or {}
    head = f"Kaggle account sharing, {local(now)}"
    if view["account_at"]:
        head += f"; account read {ago(parse_time(view['account_at']), now)}"
        head += f" ({view['calls']} Kaggle calls)" if view["calls"] else ""
    else:
        head += "; account not read (offline)"
    print(head)
    g = q.get("gpu") or {}
    if g.get("total") is not None and g.get("used") is not None:
        reset = f"; resets {local(parse_time(g['refresh']))}" if g.get("refresh") else ""
        print(f"GPU quota: {g['used']:.2f} of {g['total']:.2f} h used this week, "
              f"{view['gpu_hours_left']:.2f} h left{reset}")
    t = q.get("tpu") or {}
    if t.get("total") is not None and t.get("used"):
        # shown only when some TPU time was used
        print(f"TPU quota: {t['used']:.2f} of {t['total']:.2f} h used this week")
    cap, use, other = view["cap"], view["in_use"], view["other"]
    extra = ", ".join(f"{v} {k}" for k, v in (("of unknown kind", other["unknown"]), ("TPU", other["tpu"])) if v)
    reserve = ", ".join(f"{v} {k.upper()}" for k, v in cfg["reserve"].items() if v)
    print(f"Sessions in use: CPU {use['cpu']}/{cap['cpu']}, GPU {use['gpu']}/{cap['gpu']}"
          + (f" (plus {extra})" if extra else "") + (f"; kept free for the owner: {reserve}" if reserve else ""))
    split = {"auto": "equal shares among active projects",
             "weights": "fixed weights " + ", ".join(f"{n}={w:g}" for n, w in cfg["weights"].items()),
             "only": "only " + ", ".join(cfg["only"])}[view["mode"]]
    print(f"Split: {split} (sharing.json)" if view["mode"] != "auto" else f"Split: {split}")
    shown = {n: p for n, p in view["projects"].items() if p["active"] or p["weight"] or any(p["used"].values())}
    print(f"  {'project':<28} {'CPU use/share':>13} {'GPU use/share':>13} {'GPU h week use/budget':>22}  active because")
    for n, p in sorted(shown.items(), key=lambda kv: (not kv[1]["active"], kv[0])):
        print(f"  {n[:28]:<28} {_cell(p['used']['cpu'], p['share']['cpu']):>13} "
              f"{_cell(p['used']['gpu'], p['share']['gpu']):>13} "
              f"{p['gpu_hours']:>11.2f} / {p['gpu_budget']:<8.2f}  {'; '.join(p['active'] or ['idle'])}")
    idle = sorted(n for n, p in view["projects"].items() if n not in shown and p["kernels"])
    if idle:
        print(f"  idle projects with kernels: {', '.join(idle[:8])}" + (" ..." if len(idle) > 8 else ""))
    if view["listed"]:
        fin = view["recent"] - len(view["running"])
        print(f"Queued or running kernels (account-wide, run in the last {cfg['scan_hours']} h): "
              + ("none" if not view["running"] else str(len(view["running"])))
              + (f"; {fin} recent run(s) finished" if fin else ""))
        for r in view["running"]:
            print(f"  {r['status']:<17} {r['kind']:<7} {r['project'] or '(no local folder)':<24} {r['ref']}  "
                  f"(started {ago(parse_time(r['last_run']), now)})")
    for x in view["leases"]:
        print(f"Lease {x['id']}: {x['project']} {x['kind'].upper()}"
              + (f" for {x['kernel']}" if x.get("kernel") else "") + (" (borrowed)" if x.get("borrowed") else "")
              + f", taken {ago(parse_time(x['acquired']), now)}, expires {local(parse_time(x['expires']))}")
    for w in view["waiters"]:
        print(f"Waiting: {w['project']} for {w['kind'].upper()} since {ago(parse_time(w['since']), now)}")
    for e in view["errors"]:
        print(f"Note: {e}")
    if view["truncated"]:
        print(f"Note: {view['truncated']} more recent kernels were not checked")
    if other["unknown"]:
        print("Note: runs of unknown kind count against both pools; --probe reads their metadata")


def cmd_status(a):
    base = state_dir(a.state)
    root = find_context()
    cfg, contexts, account = _world(a, base, root, max_age=0 if not a.cached else 3600, offline=a.offline)
    now = now_utc()
    with locked(base):
        state = load_state(base)
        closed = tidy(base, state, account, now)
        write_json(base / "state.json", state)
    view = build_view(cfg, contexts, account, state, load_ledger(base), now)
    if a.json:
        print(json.dumps(view, indent=1, ensure_ascii=False))
        return 0
    for lease, h in closed:
        print(f"Closed lease {lease['id']} ({lease['project']} {lease['kind'].upper()}, {h:.2f} h)")
    print_view(view, cfg, now)
    return 0


def _target(a):
    """(project name, root, owner, kernel ref, kind) for acquire and release.

    owner is the project folder a lease belongs to: the context folder, or None when --project names it.
    """
    root = find_context(a.path) if getattr(a, "path", None) else find_context()
    kernel, kind = (a.kernel.lower() if getattr(a, "kernel", None) else None), getattr(a, "kind", None)
    if getattr(a, "path", None):
        ref, meta_k = read_kernel(a.path)
        kernel, kind = kernel or ref, kind or (meta_k if meta_k in KINDS else None)
    project = a.project or _ctx_name(root)
    if not project:
        raise ShareError("no project here - run from a project or task folder, or pass --project")
    return project, root, None if a.project else root, kernel, kind


def cmd_acquire(a):
    base = state_dir(a.state)
    project, root, owner, kernel, kind = _target(a)
    if kind not in KINDS:
        raise ShareError("pass --kind cpu|gpu (or --path to a kernel folder whose metadata says)")
    deadline, wid = time.time() + a.wait * 60, None
    try:
        while True:
            cfg, contexts, account = _world(a, base, root, max_age=FRESH_SECONDS, offline=a.offline)
            # the name status shows for this folder (two folders of one name are told apart)
            project = name_for(contexts, owner, project)
            lease, why, view = try_acquire(base, cfg, contexts, account, project, kind, kernel=kernel, root=owner,
                                           hours=a.hours, note=a.note)
            if lease:
                if a.json:
                    print(json.dumps(lease, indent=1))
                else:
                    print(f"lease {lease['id']}: {project} {kind.upper()} ({why}); release it when the run ends")
                return 0
            if time.time() >= deadline:
                p = view["projects"][project]
                print(f"refused: {why}. {project} uses {p['used'][kind]} of its {p['share'][kind]} "
                      f"{kind.upper()} share; in use {view['in_use'][kind]}/{view['cap'][kind]}", file=sys.stderr)
                return EXIT_REFUSED
            if wid is None:
                wid = f"wait-{secrets.token_hex(3)}"
                now = now_utc()
                set_waiter(base, {"id": wid, "project": project, "root": str(owner) if owner else None,
                                  "kind": kind, "since": iso(now), "until": iso(now + timedelta(minutes=a.wait)),
                                  "pid": os.getpid()})
                print(f"waiting up to {a.wait:g} min: {why}", file=sys.stderr)
            time.sleep(max(1.0, min(a.poll, deadline - time.time())))
    finally:
        if wid:
            set_waiter(base, remove=wid)


def cmd_release(a):
    base = state_dir(a.state)
    ids = list(a.lease or [])
    project = owner = kernel = kind = None
    if not ids:
        project, _root, owner, kernel, kind = _target(a)
        kind = a.kind
    closed = release(base, ids=ids, project=project, root=owner, kernel=kernel, kind=kind, all_=a.all)
    if not closed:
        print("no matching lease")
        return 1
    for lease, h in closed:
        print(f"released {lease['id']} ({lease['project']} {lease['kind'].upper()}, {h:.2f} h)")
    return 0


def cmd_ledger(a):
    base = state_dir(a.state)
    acc = read_json(base / "account.json", None) or {}
    gpu_q = (acc.get("quota") or {}).get("gpu") or {}
    now = now_utc()
    refresh = parse_time(gpu_q["refresh"]) if gpu_q.get("refresh") else None
    start = refresh - timedelta(days=7) if refresh and refresh > now else now - timedelta(days=7)
    rows = gpu_ledger(base, now, start)
    open_ = load_state(base)["leases"].values()
    if a.json:
        print(json.dumps({"week_start": iso(start), "projects": rows, "open": list(open_)}, indent=1))
        return 0
    print(f"Lease hours by project (an upper bound on run time), week from {local(start)}:")
    if not rows:
        print("  none yet")
    for n, r in sorted(rows.items()):
        print(f"  {n[:28]:<28} GPU {r['gpu_week']:6.2f} h this week, {r['gpu_total']:7.2f} h in all; "
              f"CPU {r['cpu_week']:6.2f} h this week; {r['runs']} leases, {r['borrowed']} borrowed")
    for x in open_:
        print(f"  open: {x['id']} ({ago(parse_time(x['acquired']), now)})")
    if gpu_q.get("used") is not None:
        print(f"Account GPU use this week: {gpu_q['used']:.2f} of {gpu_q['total']:.2f} h (from the last read)")
    return 0


def _pairs(items, what):
    out = {}
    for item in items or []:
        key, sep, val = item.rpartition("=")
        if not sep or not key:
            raise ShareError(f"{what} takes NAME=VALUE, not {item!r}")
        try:
            out[key] = float(val)
        except ValueError:
            raise ShareError(f"{what}: {val!r} is not a number") from None
    return out


def cmd_config(a):
    base = state_dir(a.state)
    with locked(base):
        raw = read_json(base / "sharing.json", {})
        raw = raw if isinstance(raw, dict) else {}
        before = json.dumps(raw, sort_keys=True)
        if a.equal:
            raw.pop("weights", None)
            raw.pop("only", None)
        if a.only:
            raw["only"], raw["weights"] = list(a.only), {}
        if a.weight:
            raw["weights"] = {**(raw.get("weights") or {}), **_pairs(a.weight, "--weight")}
            raw["only"] = []
        for item in a.cap or []:
            proj, sep, rest = item.partition(":")
            vals = _pairs([rest], "--cap") if sep else None
            if not vals or next(iter(vals)) not in (*KINDS, "gpu_hours"):
                raise ShareError(f"--cap takes PROJECT:cpu|gpu|gpu_hours=N, not {item!r}")
            raw.setdefault("caps", {}).setdefault(proj, {}).update(vals)
        for proj in a.uncap or []:
            (raw.get("caps") or {}).pop(proj, None)
        for key, flag in (("reserve", a.reserve), ("limits", a.limit)):
            vals = _pairs(flag, f"--{key}")
            bad = [k for k in vals if k not in (*KINDS, "gpu_hours")]
            if bad:
                raise ShareError(f"--{key} names cpu, gpu or gpu_hours, not {', '.join(bad)}")
            if vals:
                raw.setdefault(key, {}).update(vals)
        if a.active_hours is not None:
            raw["active_hours"] = a.active_hours
        if a.note is not None:
            raw["note"] = a.note
        if json.dumps(raw, sort_keys=True) != before:
            raw["schema"], raw["updated"] = SCHEMA, iso(now_utc())
            write_json(base / "sharing.json", raw)
    cfg = load_config(base)
    shown = {k: cfg[k] for k in ("mode", "weights", "only", "caps", "reserve", "limits", "active_hours", "note")}
    print(json.dumps(shown, indent=1, ensure_ascii=False))
    return 0


def cmd_stamp(a):
    root = find_context(a.path) if a.path else find_context()
    if not root:
        raise ShareError("no project or task folder here")
    path = stamp(root, ["stamp"], base=a.state)
    print(f"stamped {path}" if path else f"not stamped: {QUIET_ENV}=1 is set")
    return 0


def main(argv=None):
    p = argparse.ArgumentParser(prog="kaggle_share.py", description="Share one Kaggle account's sessions and "
                                "GPU quota between the projects using it.")
    sub = p.add_subparsers(dest="cmd", required=True)
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--state", help="state folder (default ~/.solaris/kaggle, or $KAGGLE_SHARE_DIR)")
    common.add_argument("--json", action="store_true", help="machine-readable output")
    reads = argparse.ArgumentParser(add_help=False)
    reads.add_argument("--gateway", help="the kaggle.py gateway (default: the one beside this file)")
    reads.add_argument("--solaris", help="Solaris checkout to find projects in (default: found; none to skip)")
    reads.add_argument("--offline", action="store_true", help="no Kaggle reads: use the last account read")
    reads.add_argument("--probe", action="store_true", help="read the metadata of running kernels no local "
                       "folder describes (read-only kernels pull -m)")
    s = sub.add_parser("status", parents=[common, reads], help="active projects, sessions in use and shares")
    s.add_argument("--cached", action="store_true", help="reuse an account read up to an hour old")
    s = sub.add_parser("acquire", parents=[common, reads], help="take a session lease before kernels push")
    s.add_argument("--kind", choices=KINDS)
    s.add_argument("--path", help="kernel folder: its kernel-metadata.json gives the kernel and the kind")
    s.add_argument("--kernel", help="kernel ref owner/slug the lease is for")
    s.add_argument("--project", help="project name (default: the project or task folder here)")
    s.add_argument("--hours", type=float, help="expected GPU hours of the run")
    s.add_argument("--wait", type=float, default=0, help="minutes to wait for a free share (default 0)")
    s.add_argument("--poll", type=float, default=120, help="seconds between checks while waiting")
    s.add_argument("--note", help="free text kept with the lease")
    s = sub.add_parser("release", parents=[common], help="close a lease when its run has ended")
    s.add_argument("lease", nargs="*", help="lease ids (default: this project's leases matching the options)")
    s.add_argument("--kernel")
    s.add_argument("--path")
    s.add_argument("--project")
    s.add_argument("--kind", choices=KINDS)
    s.add_argument("--all", action="store_true", help="release every match")
    sub.add_parser("ledger", parents=[common], help="lease hours by project")
    s = sub.add_parser("config", parents=[common], help="show or set the user's split")
    s.add_argument("--equal", action="store_true", help="equal shares among active projects (clears the rest)")
    s.add_argument("--only", action="append", metavar="PROJECT", help="only these projects get shares")
    s.add_argument("--weight", action="append", metavar="PROJECT=W", help="fixed weight for a project")
    s.add_argument("--cap", action="append", metavar="PROJECT:KIND=N", help="cap: cpu, gpu or gpu_hours")
    s.add_argument("--uncap", action="append", metavar="PROJECT")
    s.add_argument("--reserve", action="append", metavar="KIND=N", help="sessions kept free for the owner")
    s.add_argument("--limit", action="append", metavar="KIND=N", help="account limits: cpu, gpu, gpu_hours")
    s.add_argument("--active-hours", type=float, help="gateway calls this recent make a project active")
    s.add_argument("--note", help="free text: the user's direction in words")
    s = sub.add_parser("stamp", parents=[common], help="mark this project active (the gateway does this itself)")
    s.add_argument("--path", help="a folder inside the project or task")
    a = p.parse_args(argv)
    commands = {"status": cmd_status, "acquire": cmd_acquire, "release": cmd_release, "ledger": cmd_ledger,
                "config": cmd_config, "stamp": cmd_stamp}
    try:
        return commands[a.cmd](a)
    except ShareError as e:
        print(f"kaggle_share: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
