# Copyright 2026 Mikhail Yurasov <me@yurasov.me>
# SPDX-License-Identifier: Apache-2.0

"""Keep a project within its size budget and its folders organized: report, tidy and prune (stdlib only).

``report`` (the default, read-only) lists the sizes of the project root's top-level entries and of the children
of ``__data/`` and ``__out/`` over 1 GB, the budget status, the untidy items with where ``tidy`` would move them,
the prune candidates with size, age and the rule that matched, and layout notes (loose files in ``tools/`` other
than ``README.md``, and top-level entries that are not on the root allowlist; hidden entries are not checked).

``tidy`` moves, never deletes: entries of ``<pack>/.memory/`` that are not on its allowlist go to
``<pack>/.memory/archive/<YYYY-MM>/`` (the UTC month of their newest mtime), older ``handover-*.md`` notes too
(the newest one stays), and job scratch folders, the subfolders of ``<pack>/.memory/jobs/``, go to
``__out/jobs/``. A move never overwrites: a taken name gets a ``-2``, ``-3``... suffix.

``prune`` deletes only (a) entries matched by a ``prune`` rule of the config and older than the rule's
``older_than_days``, and (b) folders under ``__data/`` or ``__out/`` that hold a ``.disposable`` marker file. It never
deletes outside ``__data/``, ``__out/`` and ``<pack>/.memory/archive/``, never ``.git``, a mount point or a tree that
holds either, never a symlink or anything reached through one, never anything protected by a ``.keep`` marker (in
the entry, inside it or above it) or by a ``protect`` glob, and never a tree it could not read in full.

Both ``tidy`` and ``prune`` print their plan unless ``--apply`` is given, and neither touches anything modified
within ``keep_recent_hours`` (default 24); an entry's age is the newest mtime anywhere in its tree. Walks never
follow symlinks, and a hard-linked file counts once. Every applied move or deletion appends one JSON line (``ts``
UTC, ``action``, ``path``, ``to`` for a move, ``bytes``, ``rule``) to ``<pack>/.memory/housekeeping.jsonl``.

Config (optional, project-owned, committed), ``<pack>/housekeeping.json``::

    {"budget_gb": {"project": 150, "__data": 80, "__out": 60}, "keep_recent_hours": 24,
     "prune": [{"glob": "__out/*", "older_than_days": 7}], "protect": ["__data/kaggle"],
     "memory_allow": ["notes-*.md"], "root_allow": ["paper"]}

Globs are relative to the project root (``{pack}`` stands for the pack folder, ``**`` spans folders); a
``budget_gb`` key is ``project`` or a path; a GB is 2**30 bytes; keys starting with ``_`` are comments. Without
the file: no budgets, no prune rules (the markers still work), the built-in allowlists.

Run::

    uv run -m solaris.tools.housekeeping --dir projects/<slug>                   # report
    uv run -m solaris.tools.housekeeping --dir projects/<slug> tidy [--apply]    # plan, or move
    uv run -m solaris.tools.housekeeping --dir projects/<slug> prune [--apply]   # plan, or delete
    uv run -m solaris.tools.housekeeping --dir projects/<slug> report --json

Exit codes: 0 nothing to do (or every applied action done); 3 attention: over a budget, untidy items or prune
candidates (``tidy`` and ``prune`` count only their own items); 2 bad usage; 1 an error (a bad --dir or config, or
an applied action that failed).
"""

from __future__ import annotations

import argparse
import errno
import fnmatch
import json
import math
import os
import shutil
import stat
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from solaris.tools import pack as P
from solaris.tools.interactions import append_line

CONFIG = "housekeeping.json"
LOG = "housekeeping.jsonl"
ATTENTION = 3
GB = 1024 ** 3
LARGE = GB             # children of __data/ and __out/ at or over this are listed
KEEP_RECENT_HOURS = 24
KEEP, DISPOSABLE = ".keep", ".disposable"
DATA_ROOTS = ("__data", "__out")
HANDOVER = "handover-*.md"
LIST_MAX = 30          # text lines per list; --json lists everything
UNREADABLE_MAX = 50
MAX_DEPTH = 400

# Canonical entries of <pack>/.memory/: the framework's, then the bundled plugins' state files.
MEMORY_ALLOW = (
    "context.md", "directions.md", "improvements.md", "resources.md", "credentials.md", "config.json",
    "schedule.json", "spend.jsonl", "housekeeping.jsonl", "interactions", "interactions.jsonl", "jobs", "archive",
    ".empty", "spec-v0.md", "PAUSED",
    "hosts.json", "resource-sharing.json", "resource-sharing-seen.json", "lease-ends.json", "colossus-leases.md",
    "ai-spend.jsonl", "brev-costs.md", "visual", "visual-qa-endpoints.json",
)
# OS and sync bookkeeping in .memory/: never moved (the session-start sweep merges conflict copies).
MEMORY_IGNORE = (".DS_Store", ".stfolder", ".stversions", ".stignore", ".syncthing.*", "~syncthing~*",
                 ".solaris-tmp-*", "*.sync-conflict-*")
# Top-level entries of a project root besides its pack and the manifest's workspaces.
ROOT_ALLOW = ("AGENTS.md", "CLAUDE.md", "README.md", "LICENSE*", "NOTICE*", "DISCLAIMER*", "source", "tools",
              "reports", "research", "submissions", "__*")
CONFIG_KEYS = ("budget_gb", "keep_recent_hours", "prune", "protect", "memory_allow", "root_allow")

_now = time.time   # tests swap the clock


class HousekeepingError(Exception):
    """A bad project folder or config."""


def _number(value, what: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
        raise HousekeepingError(f"{what} must be a number >= 0, not {value!r}")
    return float(value)


def _rel_glob(value, what: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise HousekeepingError(f"{what} must be a non-empty string, not {value!r}")
    if value.startswith(("/", "\\", "~")) or ".." in value.replace("\\", "/").split("/"):
        raise HousekeepingError(f"{what} {value!r} must stay inside the project (relative, no '..')")
    if os.path.normpath(value) == ".":
        raise HousekeepingError(f"{what} {value!r} names the project root itself")
    return value


def _str_list(value, what: str) -> list:
    if not isinstance(value, list) or not all(isinstance(v, str) and v for v in value):
        raise HousekeepingError(f"{what} must be a list of non-empty strings")
    return list(value)


def load_config(pack: Path) -> dict:
    """The project's ``<pack>/housekeeping.json`` over the defaults; HousekeepingError when it is malformed."""
    cfg = {"file": None, "budget_gb": {}, "keep_recent_hours": float(KEEP_RECENT_HOURS), "prune": [],
           "protect": [], "memory_allow": [], "root_allow": []}
    path = pack / CONFIG
    if not os.path.lexists(path):
        return cfg
    where = f"{pack.name}/{CONFIG}"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise HousekeepingError(f"{where} is unreadable: {exc}") from None
    if not isinstance(data, dict):
        raise HousekeepingError(f"{where} must hold a JSON object")
    unknown = sorted(k for k in data if k not in CONFIG_KEYS and not k.startswith("_"))
    if unknown:
        raise HousekeepingError(f"{where}: unknown key(s) {', '.join(unknown)} (known: {', '.join(CONFIG_KEYS)})")
    cfg["file"] = where
    if "keep_recent_hours" in data:
        cfg["keep_recent_hours"] = _number(data["keep_recent_hours"], f"{where}: keep_recent_hours")
    budgets = data.get("budget_gb", {})
    if not isinstance(budgets, dict):
        raise HousekeepingError(f"{where}: budget_gb must be an object of name -> GB")
    for key, gb in budgets.items():
        if key != "project":
            _rel_glob(key, f"{where}: budget_gb key")
            if any(c in key for c in "*?["):
                raise HousekeepingError(f"{where}: budget_gb key {key!r} must be a path, not a glob")
        cfg["budget_gb"][key] = _number(gb, f"{where}: budget_gb[{key!r}]")
    rules = data.get("prune", [])
    if not isinstance(rules, list):
        raise HousekeepingError(f"{where}: prune must be a list of rules")
    for i, rule in enumerate(rules):
        what = f"{where}: prune[{i}]"
        if not isinstance(rule, dict) or {"glob", "older_than_days"} - set(rule) or set(rule) - {
                "glob", "older_than_days", "note"}:
            raise HousekeepingError(f"{what} must be an object with glob and older_than_days (and an optional note)")
        cfg["prune"].append({"glob": _rel_glob(rule["glob"], f"{what}.glob"),
                             "older_than_days": _number(rule["older_than_days"], f"{what}.older_than_days")})
    for key in ("protect", "memory_allow", "root_allow"):
        if key in data:
            cfg[key] = _str_list(data[key], f"{where}: {key}")
    for pattern in cfg["protect"]:
        _rel_glob(pattern, f"{where}: protect entry")
    return cfg


class Tree:
    """What a walk found in one entry's tree."""

    __slots__ = ("bytes", "files", "newest", "complete", "keep", "git", "mount", "disposable")

    def __init__(self, newest: float = 0.0):
        self.bytes = 0
        self.files = 0
        self.newest = newest        # newest mtime or ctime anywhere in the tree, the entry itself included
        self.complete = True        # every folder in it was read
        self.keep = False           # a .keep marker in it or below it
        self.git = False            # a .git in it or below it
        self.mount = False          # a folder in it or below it is on another file system
        self.disposable = False     # this folder itself holds a .disposable marker file

    def add(self, other: "Tree") -> None:
        self.bytes += other.bytes
        self.files += other.files
        self.newest = max(self.newest, other.newest)
        self.complete = self.complete and other.complete
        self.keep = self.keep or other.keep
        self.git = self.git or other.git
        self.mount = self.mount or other.mount


class Walker:
    """Measures trees under a project root: never follows symlinks, counts each hard-linked file once."""

    def __init__(self, root: str, record: tuple = ()):
        self.root = root
        self.record = tuple(r + "/" for r in record)   # keep the Tree of every folder at or under these
        self.dirs: dict = {}
        self.seen: set = set()
        self.unreadable: list = []

    def measure(self, rel: str, children: "list | None" = None) -> Tree:
        """The Tree of the entry at ``rel``: a folder is walked; anything else, a symlink too, counts itself.
        With ``children``, a folder's direct children are appended as ``(name, is_dir, Tree)``."""
        try:
            st = os.lstat(os.path.join(self.root, rel))
        except OSError:
            return self._lost(rel)
        if stat.S_ISDIR(st.st_mode):
            return self._walk(rel, st, 0, children)
        return self._file(rel.rsplit("/", 1)[-1], st)

    def _lost(self, rel: str) -> Tree:
        tree = Tree()
        tree.complete = False
        if len(self.unreadable) < UNREADABLE_MAX:
            self.unreadable.append(rel)
        return tree

    def _file(self, name: str, st) -> Tree:
        tree = Tree(_changed(st))
        tree.keep = name == KEEP
        tree.git = name == ".git"
        if st.st_nlink > 1:
            key = (st.st_dev, st.st_ino)
            if key in self.seen:
                return tree
            self.seen.add(key)
        tree.bytes, tree.files = st.st_size, 1
        return tree

    def _walk(self, rel: str, here, depth: int, children: "list | None" = None) -> Tree:
        tree = Tree(_changed(here))
        try:
            with os.scandir(os.path.join(self.root, rel)) as entries:
                for entry in entries:
                    sub = f"{rel}/{entry.name}" if rel else entry.name
                    try:
                        st = entry.stat(follow_symlinks=False)
                    except OSError:
                        tree.add(self._lost(sub))
                        continue
                    is_dir = stat.S_ISDIR(st.st_mode)
                    if is_dir:
                        if depth >= MAX_DEPTH:
                            child = self._lost(sub)
                        else:
                            child = self._walk(sub, st, depth + 1)
                        child.mount = child.mount or st.st_dev != here.st_dev
                        tree.keep = tree.keep or entry.name == KEEP
                        tree.git = tree.git or entry.name == ".git"
                    else:
                        child = self._file(entry.name, st)
                        if entry.name == DISPOSABLE and stat.S_ISREG(st.st_mode):
                            tree.disposable = True
                    tree.add(child)
                    if children is not None:
                        children.append((entry.name, is_dir, child))
        except OSError:
            tree.add(self._lost(rel))
        if (rel + "/").startswith(self.record):
            self.dirs[rel] = tree
        return tree


def _changed(st) -> float:
    # ctime too: a copy that kept old mtimes (rsync -a, cp -p, unzip) still counts as new
    return max(st.st_mtime, st.st_ctime)


def _iso(t: float) -> str:
    return datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _norm(path: str) -> str:
    return os.path.normpath(path).replace(os.sep, "/")


def _matches(name: str, patterns) -> bool:
    # case-insensitive: some file systems are; holding more back is the safe side
    return any(fnmatch.fnmatchcase(name.casefold(), p.casefold()) for p in patterns)


def _inside(path: str, root: str) -> bool:
    """True when ``path``, symlinks resolved, is ``root`` (a real path) or below it."""
    real = os.path.realpath(path)
    return real == root or real.startswith(root.rstrip(os.sep) + os.sep)


def _symlink_on_path(root: str, rel: str) -> bool:
    """True when ``rel`` or any folder on the way to it from ``root`` is a symlink."""
    here = root
    for part in rel.split("/"):
        here = os.path.join(here, part)
        if os.path.islink(here):
            return True
    return False


def _glob(root: str, pattern: str, hidden: bool, case_sensitive: bool = True) -> list:
    """Paths relative to ``root`` that match ``pattern``; ``**`` never enters a symlinked folder. Without ``hidden``
    a hidden name matches only where the pattern spells it out."""
    spelled = {part for part in pattern.split("/") if not any(c in part for c in "*?[")}
    try:
        paths = list(Path(root).glob(pattern, case_sensitive=case_sensitive, recurse_symlinks=False))
    except ValueError as exc:
        raise HousekeepingError(f"bad glob {pattern!r}: {exc}") from None
    hits = {p.relative_to(root).as_posix() for p in paths}
    if not hidden:
        hits = {h for h in hits if all(not part.startswith(".") or part in spelled for part in h.split("/"))}
    return sorted(hits)


def _listdir(path: str) -> list:
    try:
        with os.scandir(path) as entries:
            return sorted(entries, key=lambda e: e.name)
    except OSError:
        return []


def _mtime(entry) -> float:
    try:
        return entry.stat(follow_symlinks=False).st_mtime
    except OSError:
        return 0.0


def _free(root: str, dest: str, is_dir: bool, taken: set) -> str:
    """``dest``, or the first ``-2``, ``-3``... variant of it that neither exists nor is planned already."""
    head, name = dest.rsplit("/", 1)
    stem, ext = (name, "") if is_dir else os.path.splitext(name)
    pick, n = dest, 1
    while pick in taken or os.path.lexists(os.path.join(root, pick)):
        n += 1
        pick = f"{head}/{stem}-{n}{ext}"
    taken.add(pick)
    return pick


def _plan_move(root, rel, dest, is_dir, tree, why, recent, moves, held, taken) -> None:
    item = {"path": rel, "dir": is_dir, "bytes": tree.bytes, "newest": _iso(tree.newest), "reason": why}
    if tree.newest > recent:
        held.append({**item, "held": "recent"})
    elif not tree.complete:
        held.append({**item, "held": "unreadable parts"})
    elif tree.keep:
        held.append({**item, "held": ".keep"})
    elif tree.mount:
        held.append({**item, "held": "mount point"})
    elif not (_inside(os.path.dirname(os.path.join(root, rel)), root)
              and _inside(os.path.dirname(os.path.join(root, dest)), root)):
        held.append({**item, "held": "outside the project"})
    else:
        moves.append({**item, "to": _free(root, dest, is_dir, taken)})


def plan_tidy(root: str, pack: str, cfg: dict, recent: float) -> "tuple[list, list]":
    """The moves tidy would make, and the untidy items held back (recent, unreadable or leaving the project)."""
    walker = Walker(root)
    memory = f"{pack}/.memory"
    moves, held, taken = [], [], set()
    entries = _listdir(os.path.join(root, memory))
    allow = MEMORY_ALLOW + tuple(cfg["memory_allow"])
    notes = [e for e in entries if fnmatch.fnmatchcase(e.name, HANDOVER) and e.is_file(follow_symlinks=False)]
    newest_note = max(notes, key=lambda e: (_mtime(e), e.name)).name if notes else None
    note_names = {e.name for e in notes}
    for entry in entries:
        if (entry.name.startswith(".") or _matches(entry.name, MEMORY_IGNORE) or _matches(entry.name, allow)
                or entry.name == newest_note):
            continue
        rel = f"{memory}/{entry.name}"
        tree = walker.measure(rel)
        month = time.strftime("%Y-%m", time.gmtime(_mtime(entry) or tree.newest))
        why = "older handover note" if entry.name in note_names else "not on the .memory allowlist"
        _plan_move(root, rel, f"{memory}/archive/{month}/{entry.name}", entry.is_dir(follow_symlinks=False), tree,
                   why, recent, moves, held, taken)
    # a job named in the context file or the newest handover note may still be running
    named = ""
    for name in ("context.md", newest_note):
        if name:
            try:
                named += Path(root, memory, name).read_text(errors="replace")
            except OSError:
                pass
    for entry in _listdir(os.path.join(root, memory, "jobs")):
        if entry.is_dir(follow_symlinks=False):
            rel = f"{memory}/jobs/{entry.name}"
            tree = walker.measure(rel)
            if entry.name in named:
                held.append({"path": rel, "dir": True, "bytes": tree.bytes, "newest": _iso(tree.newest),
                             "reason": "job scratch", "held": "named in context or handover"})
                continue
            _plan_move(root, rel, f"__out/jobs/{entry.name}", True, tree, "job scratch", recent, moves, held, taken)
    moves.sort(key=lambda m: (-m["bytes"], m["path"]))
    return moves, held


def _check_prune(root, rel, rule, days, prune_roots, protect, protected, walker, recent, now) -> "tuple[dict, str]":
    """The candidate's item, and why it may not be deleted (empty when it may)."""
    item = {"path": rel, "rule": rule}
    parts = rel.split("/")
    if rel in ("", ".") or ".." in parts or os.path.isabs(rel):
        return item, "outside the project"
    if ".git" in parts:
        return item, ".git"
    if not any(rel.startswith(r + "/") for r in prune_roots):
        return item, "outside __data/, __out/ and the archive"
    if _symlink_on_path(root, rel):
        return item, "symlink"
    path = os.path.join(root, rel)
    if not _inside(path, root):
        return item, "outside the project"
    tree = walker.dirs.get(rel) or walker.measure(rel)
    item.update({"dir": os.path.isdir(path), "bytes": tree.bytes, "newest": _iso(tree.newest),
                 "age_days": round((now - tree.newest) / 86400, 1)})
    above = ["/".join(parts[:i]) for i in range(1, len(parts) + 1)]
    if tree.keep or any(os.path.lexists(os.path.join(root, a, KEEP)) for a in above):
        return item, ".keep"
    if any(_matches(a, protect) for a in above) or any(
            p.casefold() == rel.casefold() or p.casefold().startswith(rel.casefold() + "/")
            or rel.casefold().startswith(p.casefold() + "/") for p in protected):
        return item, "protected"
    if tree.git:
        return item, "holds .git"
    try:
        own_fs = os.lstat(path).st_dev == os.lstat(os.path.dirname(path)).st_dev
    except OSError:
        own_fs = False
    if tree.mount or not own_fs:
        return item, "mount point"
    if not tree.complete:
        return item, "unreadable parts"
    if tree.newest > recent:
        return item, "recent"
    for a in above[:-1]:
        if a in prune_roots:
            continue
        up = walker.dirs.get(a)
        if up is not None and up.newest > recent:
            return item, "inside a recent folder"
    if days is not None and tree.newest > now - days * 86400:
        return item, f"newer than {days:g} days"
    return item, ""


def plan_prune(root: str, pack: str, cfg: dict, now: float, walker: Walker) -> "tuple[list, list]":
    """Prune candidates and the matches held back. ``walker`` has walked the prune roots, recording them."""
    recent = now - cfg["keep_recent_hours"] * 3600
    prune_roots = (*DATA_ROOTS, f"{pack}/.memory/archive")
    held, found = [], {}
    for r in prune_roots:
        if os.path.islink(os.path.join(root, r)):
            held.append({"path": r, "rule": "", "held": "symlink: not walked"})
    for rel, tree in walker.dirs.items():
        if tree.disposable and "/" in rel and rel.split("/", 1)[0] in DATA_ROOTS:
            found.setdefault(rel, (DISPOSABLE, None))
    for rule in cfg["prune"]:
        label = f"{rule['glob']} older than {rule['older_than_days']:g} days"
        for rel in _glob(root, rule["glob"].replace("{pack}", pack), hidden=False):
            found.setdefault(rel, (label, rule["older_than_days"]))
    for rel, (label, days) in found.items():
        found[rel] = (label, days)
    protect = [p.replace("{pack}", pack) for p in cfg["protect"]]
    protected = {rel for p in protect for rel in _glob(root, p, hidden=True, case_sensitive=False)}
    cands = []
    for rel in sorted(found, key=lambda r: (r.count("/"), r)):
        if any(rel.startswith(c["path"] + "/") for c in cands):
            continue   # inside a candidate already
        rule, days = found[rel]
        item, why = _check_prune(root, rel, rule, days, prune_roots, protect, protected, walker, recent, now)
        if why:
            held.append({**item, "held": why})
        else:
            cands.append({**item, "older_than_days": days})
    cands.sort(key=lambda c: (-c["bytes"], c["path"]))
    return cands, held


def apply_moves(root: str, log: str, moves: list) -> "tuple[list, list]":
    """Make the planned moves (never over an existing entry); log each one made."""
    done, failed = [], []
    for m in moves:
        src, dst = os.path.join(root, m["path"]), os.path.join(root, m["to"])
        try:
            if not os.path.lexists(src):
                raise OSError("gone")
            if os.path.lexists(dst):
                raise OSError(f"{m['to']} exists")
            if not (_inside(os.path.dirname(src), root) and _inside(os.path.dirname(dst), root)):
                raise OSError("refused: outside the project")
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            try:
                if os.path.isdir(src) and not os.path.islink(src):
                    if os.path.lexists(dst):
                        raise OSError(f"{m['to']} exists")
                    os.rename(src, dst)
                else:
                    os.link(src, dst, follow_symlinks=False)   # fails if dst appeared meanwhile
                    os.unlink(src)
            except OSError as exc:
                if exc.errno == errno.EXDEV:
                    raise OSError("on another file system: not moved") from None
                raise
        except OSError as exc:
            failed.append({"action": "move", "path": m["path"], "to": m["to"], "error": str(exc)})
            continue
        entry = {"ts": _iso(_now()), "action": "move", "path": m["path"], "to": m["to"], "bytes": m["bytes"],
                 "rule": m["reason"]}
        _record(log, entry, failed)
        done.append(entry)
    return done, failed


def _record(log: str, entry: dict, failed: list) -> None:
    """Append ``entry`` to the housekeeping log; a write that fails is a failure of its own."""
    try:
        append_line(log, entry)
    except OSError as exc:
        failed.append({"action": "log", "path": entry["path"], "error": f"not logged: {exc}"})


def apply_prune(root: str, log: str, cands: list, recheck=None) -> "tuple[list, list]":
    """Delete the candidates, checking each one again first (``recheck`` returns why it may not go); log each
    deletion run."""
    done, failed = [], []
    for c in cands:
        rel, path = c["path"], os.path.join(root, c["path"])
        if _symlink_on_path(root, rel) or not _inside(path, root):
            failed.append({"action": "delete", "path": rel, "error": "refused: symlink or outside the project"})
            continue
        why = recheck(c) if recheck else ""
        if why:
            failed.append({"action": "delete", "path": rel, "error": f"refused on recheck: {why}"})
            continue
        entry = {"ts": "", "action": "delete", "path": rel, "bytes": c["bytes"], "rule": c["rule"]}
        try:
            if stat.S_ISDIR(os.lstat(path).st_mode):
                shutil.rmtree(path)
            else:
                os.unlink(path)
        except OSError as exc:
            entry["error"] = str(exc)
            failed.append(entry)
        else:
            done.append(entry)
        entry["ts"] = _iso(_now())
        _record(log, entry, failed)
    return done, failed


def measure_sizes(root: str, walker: Walker) -> dict:
    """Sizes of the root's top-level entries and of the __data/ and __out/ children at or over LARGE."""
    entries, large = [], []
    for entry in _listdir(root):
        kids = [] if entry.name in DATA_ROOTS else None
        tree = walker.measure(entry.name, kids)
        entries.append({"path": entry.name, "dir": entry.is_dir(follow_symlinks=False), "bytes": tree.bytes,
                        "files": tree.files})
        for name, is_dir, kid in kids or ():
            if kid.bytes >= LARGE:
                large.append({"path": f"{entry.name}/{name}", "dir": is_dir, "bytes": kid.bytes,
                              "files": kid.files})
    entries.sort(key=lambda e: (-e["bytes"], e["path"]))
    large.sort(key=lambda e: (-e["bytes"], e["path"]))
    return {"total_bytes": sum(e["bytes"] for e in entries), "files": sum(e["files"] for e in entries),
            "entries": entries, "large": large}


def check_budgets(root: str, pack: str, cfg: dict, sizes: dict, walker: Walker) -> list:
    out = []
    top = {e["path"]: e["bytes"] for e in sizes["entries"]}
    for key, gb in cfg["budget_gb"].items():
        if key == "project":
            size = sizes["total_bytes"]
        else:
            rel = _norm(key.replace("{pack}", pack)).rstrip("/")
            path = os.path.join(root, rel)
            if not os.path.lexists(path) or _symlink_on_path(root, rel):
                why = "missing" if not os.path.lexists(path) else "symlink"
                out.append({"name": key, "bytes": 0, "budget_gb": gb, "budget_bytes": int(gb * GB), "over": False,
                            "measured": False, "note": f"not measured: {why}"})
                continue
            tree = walker.dirs.get(rel)
            size = top[rel] if rel in top else tree.bytes if tree else Walker(root).measure(rel).bytes
        out.append({"name": key, "bytes": size, "budget_gb": gb, "budget_bytes": int(gb * GB),
                    "over": size > gb * GB, "measured": True})
    return out


def layout_notes(root: str, pack: str, cfg: dict, manifest: dict) -> list:
    """Report-only checks: loose files in tools/, and top-level entries off the root allowlist."""
    project = manifest.get("project") if isinstance(manifest.get("project"), dict) else {}
    if project.get("mode") == "embedded":
        return []   # the root is a code repo
    workspaces = [w for w in project.get("workspaces") or [] if isinstance(w, str) and w]
    allow = ROOT_ALLOW + (pack, *workspaces, *cfg["root_allow"])
    notes = []
    for entry in _listdir(root):
        if not entry.name.startswith(".") and not _matches(entry.name, allow):
            notes.append({"path": entry.name, "note": "not on the root allowlist (move it, or add it to root_allow)"})
    for entry in _listdir(os.path.join(root, "tools")):
        if entry.name != "README.md" and not entry.name.startswith(".") and not entry.is_dir(follow_symlinks=False):
            notes.append({"path": f"tools/{entry.name}",
                          "note": "loose file in tools/ (each tool belongs in its own subfolder)"})
    return notes


def run(root: str, pack: str, cfg: dict, manifest: dict, command: str, apply: bool) -> dict:
    """Do ``command`` on the project at ``root`` (a real path); the result as a JSON-ready dict."""
    started, now = time.monotonic(), _now()
    recent = now - cfg["keep_recent_hours"] * 3600
    log = os.path.join(root, pack, ".memory", LOG)
    res = {"project": root, "pack": pack, "command": command, "apply": apply,
           "config": {"file": cfg["file"], "keep_recent_hours": cfg["keep_recent_hours"]}, "elapsed_s": 0.0,
           "sizes": None, "budgets": None, "untidy": None, "untidy_held": None, "prune": None, "prune_held": None,
           "layout": None, "unreadable": [], "applied": [], "failed": [], "exit": 0, "reason": ""}
    walker = Walker(root, record=(*DATA_ROOTS, f"{pack}/.memory/archive"))
    if command == "report":
        res["sizes"] = measure_sizes(root, walker)
        res["budgets"] = check_budgets(root, pack, cfg, res["sizes"], walker)
        res["layout"] = layout_notes(root, pack, cfg, manifest)
    elif command == "prune":
        for r in walker.record:
            if os.path.lexists(os.path.join(root, r)):
                walker.measure(r.rstrip("/"))
    if command in ("report", "tidy"):
        res["untidy"], res["untidy_held"] = plan_tidy(root, pack, cfg, recent)
    if command in ("report", "prune"):
        res["prune"], res["prune_held"] = plan_prune(root, pack, cfg, now, walker)
    res["unreadable"] = [u for u in walker.unreadable if os.path.lexists(os.path.join(root, u))]
    if apply and command == "tidy":
        res["applied"], res["failed"] = apply_moves(root, log, res["untidy"])
    elif apply and command == "prune":
        prune_roots = (*DATA_ROOTS, f"{pack}/.memory/archive")
        protect = [p.replace("{pack}", pack) for p in cfg["protect"]]
        protected = {rel for p in protect for rel in _glob(root, p, hidden=True, case_sensitive=False)}

        def recheck(c):
            # a fresh look at the candidate itself right before it goes
            return _check_prune(root, c["path"], c["rule"], c.get("older_than_days"), prune_roots, protect,
                                protected, Walker(root), now - cfg["keep_recent_hours"] * 3600, _now())[1]
        res["applied"], res["failed"] = apply_prune(root, log, res["prune"], recheck)
    res["elapsed_s"] = round(time.monotonic() - started, 2)
    attention = []
    if res["budgets"] and any(b["over"] for b in res["budgets"]):
        attention.append("over budget")
    if res["budgets"] and any(not b.get("measured", True) for b in res["budgets"]):
        attention.append("budget not measured")
    if not apply and res["untidy"]:
        attention.append("untidy items")
    if not apply and res["prune"]:
        attention.append("prune candidates")
    if res["failed"]:
        res["exit"], res["reason"] = 1, f"{len(res['failed'])} action(s) failed"
    elif attention:
        res["exit"], res["reason"] = ATTENTION, "attention: " + ", ".join(attention)
    else:
        res["reason"] = f"done: {len(res['applied'])} applied" if apply and res["applied"] else "nothing to do"
    return res


def human(n: float) -> str:
    if n < 1024:
        return f"{int(n)} B"
    for unit in ("KB", "MB", "GB", "TB"):
        n /= 1024
        if n < 1024 or unit == "TB":
            return f"{n:.1f} {unit}"
    return ""


def _shown(item: dict) -> str:
    return item["path"] + ("/" if item.get("dir") else "")


def _n(count: int, noun: str) -> str:
    return f"{count} {noun}{'' if count == 1 else 's'}"


def _capped(lines: list, rows: list, fmt) -> None:
    lines.extend(fmt(r) for r in rows[:LIST_MAX])
    if len(rows) > LIST_MAX:
        lines.append(f"  ... {len(rows) - LIST_MAX} more (--json lists all)")


def _held_summary(held: list) -> str:
    if not held:
        return ""
    counts = Counter(h["held"] for h in held)
    return f"; held back {len(held)}: " + ", ".join(f"{n} {why}" for why, n in counts.most_common())


def render(res: dict, where: str) -> str:
    """The result as compact text, paths relative to the project root."""
    conf = f"config {res['config']['file']}" if res["config"]["file"] else "no housekeeping.json, defaults"
    lines = [f"housekeeping {res['command']}{' --apply' if res['apply'] else ''}: {where} (pack {res['pack']}; "
             f"{conf}; keep_recent_hours {res['config']['keep_recent_hours']:g}) in {res['elapsed_s']:.1f} s"]
    tail = []
    sizes = res["sizes"]
    if sizes is not None:
        lines.append(f"Sizes: {human(sizes['total_bytes'])} in {sizes['files']:,} files")
        _capped(lines, sizes["entries"], lambda e: f"  {human(e['bytes']):>9}  {_shown(e)}")
        if sizes["large"]:
            lines.append(f"  Over {human(LARGE)} in {' and '.join(r + '/' for r in DATA_ROOTS)}:")
            _capped(lines, sizes["large"], lambda e: f"  {human(e['bytes']):>9}  {_shown(e)}")
        tail.append(human(sizes["total_bytes"]))
    if res["budgets"] is not None:
        if not res["budgets"]:
            lines.append("Budgets: none set (budget_gb in <pack>/housekeeping.json)")
        else:
            lines.append("Budgets:")
            lines.extend(f"  {'OVER' if b['over'] else 'ok':4}  {b['name']}: {human(b['bytes'])} of "
                         f"{b['budget_gb']:g} GB" if b.get("measured", True) else
                         f"  ??    {b['name']}: {b['note']} ({b['budget_gb']:g} GB budget)" for b in res["budgets"])
            tail.append(f"{sum(b['over'] for b in res['budgets'])} over budget")
    if res["untidy"] is not None and not res["apply"]:
        moves = res["untidy"]
        lines.append(f"Untidy: {len(moves)} to move ({human(sum(m['bytes'] for m in moves))})"
                     + _held_summary(res["untidy_held"]))
        _capped(lines, moves, lambda m: f"  {_shown(m)} -> {m['to']}{'/' if m['dir'] else ''}  "
                                        f"({human(m['bytes'])}; {m['reason']})")
        tail.append(f"{len(moves)} untidy")
    if res["prune"] is not None and not res["apply"]:
        cands = res["prune"]
        lines.append(f"Prune candidates: {len(cands)} ({human(sum(c['bytes'] for c in cands))})"
                     + _held_summary(res["prune_held"]))
        _capped(lines, cands, lambda c: f"  {human(c['bytes']):>9}  {c['age_days']:6.1f} d  {_shown(c)}  "
                                        f"[{c['rule']}]")
        tail.append(f"{_n(len(cands), 'prune candidate')} ({human(sum(c['bytes'] for c in cands))})")
    if res["apply"]:
        verb = "Moved" if res["command"] == "tidy" else "Deleted"
        done = res["applied"]
        lines.append(f"{verb}: {len(done)} ({human(sum(d['bytes'] for d in done))})")
        _capped(lines, done, lambda d: f"  {d['path']}" + (f" -> {d['to']}" if "to" in d else ""))
        tail.append(f"{verb.lower()} {len(done)}")
        if res["failed"]:
            lines.append(f"Failed: {len(res['failed'])}")
            _capped(lines, res["failed"], lambda f: f"  {f['path']}: {f['error']}")
            tail.append(f"{len(res['failed'])} failed")
    if res["layout"] is not None:
        lines.append(f"Layout notes: {len(res['layout'])}")
        _capped(lines, res["layout"], lambda n: f"  {n['path']}: {n['note']}")
        tail.append(_n(len(res["layout"]), "layout note"))
    if res["unreadable"]:
        lines.append(f"Unreadable: {len(res['unreadable'])}")
        _capped(lines, res["unreadable"], lambda u: f"  {u}")
    lines.append("Total: " + "; ".join([*tail, f"exit {res['exit']}: {res['reason']}"]))
    return "\n".join(lines)


def main(argv: "list[str] | None" = None) -> int:
    parser = argparse.ArgumentParser(prog="solaris.tools.housekeeping", description=__doc__.splitlines()[0])
    parser.add_argument("command", nargs="?", choices=("report", "tidy", "prune"), default="report",
                        help="report (default, read-only), tidy (move untidy items) or prune (delete by the rules)")
    parser.add_argument("--dir", required=True, help="the project root (embedded mode: the repo root)")
    parser.add_argument("--apply", action="store_true", help="tidy: make the moves; prune: delete (default: plan)")
    parser.add_argument("--json", action="store_true", help="print JSON (sizes in bytes) instead of text")
    args = parser.parse_args(argv)
    if args.apply and args.command == "report":
        parser.error("--apply goes with tidy or prune; report is read-only")
    root = Path(args.dir)
    try:
        if not root.is_dir():
            raise HousekeepingError(f"{root} not found: --dir must be a project root (embedded mode: the repo root)")
        pack = P.require_pack(root)
        cfg = load_config(pack)
        manifest = json.loads((pack / "manifest.json").read_text(encoding="utf-8"))
    except (HousekeepingError, P.PackError, OSError, ValueError) as exc:
        print(f"housekeeping: {exc}")
        return 1
    try:
        res = run(os.path.realpath(root), pack.name, cfg, manifest, args.command, args.apply)
    except HousekeepingError as exc:
        print(f"housekeeping: {exc}")
        return 1
    print(json.dumps(res, indent=2) if args.json else render(res, args.dir))
    return res["exit"]


if __name__ == "__main__":
    raise SystemExit(main())
