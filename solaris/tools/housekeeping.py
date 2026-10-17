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
``older_than_days``, and (b) folders under ``__data/`` or ``__out/`` that hold a ``.disposable`` or ``.recreatable``
marker file. It never deletes outside ``__data/``, ``__out/`` and ``<pack>/.memory/archive/``, never ``.git``, a mount
point or a tree that holds either, never a symlink or anything reached through one, never anything protected by a
``.keep`` marker (in the entry, inside it or above it) or by a ``protect`` glob, and never a tree it could not read in
full.

Re-creatable data (data that can be fetched or rebuilt again and is unlikely to be needed) is pruned at every
housekeeping pass without asking the owner. A rule names it with ``"recreatable": true`` and a required one-line
``"how"`` (how to get the data back: a download command, or the script that rebuilds it); a ``.recreatable`` marker
file names its folder like ``.disposable`` does, its first line being the ``how`` (a marker without one is held).
Every guard above and below still applies, a re-creatable rule never reaches into the pack, and each deletion's log
line carries its ``how``.

Never deleted, whatever a rule or marker says: anything in the pack (only a plain rule reaches
``<pack>/.memory/archive/``), ``.git``, submissions (a path part named ``submission*``) and raw traces (a path part
named ``trace``, ``traces``, ``trace-*``, ``traces-*`` or ``*.trace``), whether in the entry's path, the entry itself
or anywhere inside it; names match case-insensitively. Protected unless the project says otherwise: the ``protect``
globs, ``.keep`` markers, and the ``protect_names`` name globs, matched the same way as the never-deleted names; their
default holds raw data and run logs (``raw``, ``raw-*``, ``raw_*``, ``log``, ``logs``, ``*.log``, ``*.log.*``). A
project changes that list in its config, or sets it to ``[]`` so that, say, job scratch with a log in it or
re-downloadable ``__data/raw/`` can be pruned by its rules.

Live jobs hold what they use. A job is a folder or a loose script in ``<pack>/.memory/jobs/`` or ``__out/jobs/``; it
is live when the context file or the newest handover note names it, when it changed within ``keep_recent_hours``,
when a running process's command line names it, or when a live job's script names it. Before ``tidy`` moves or
``prune`` deletes an entry, the tool reads the scripts of the live jobs (code and config files, up to four folders
deep), the project files that running processes name by an absolute path, and the processes' command lines (``ps``).
An entry that any of them names, by its path from the project root (written relative or absolute, the entry itself
or a path inside it) or, for a job folder, by its bare name, is held, and the plan prints the referring file, or a
process as its pid, its executable's name and the path it names (never its command line, which may carry a
secret). ``prune`` also holds an entry the context file or the newest handover note names, and anything inside a
live job or holding one. When the process list cannot be read, or the scripts pass the read caps, every move and
deletion is held, with a note. A path a job builds at run time (other than through a job folder's name) or writes
relative to its own folder is not seen: name such paths in the context file, or mark them ``.keep``.

Both ``tidy`` and ``prune`` print their plan unless ``--apply`` is given, and neither touches anything modified
within ``keep_recent_hours`` (default 24); an entry's age is the newest mtime or ctime anywhere in its tree. Walks
never follow symlinks, and a hard-linked file counts once. Every applied move or deletion appends one JSON line
(``ts`` UTC, ``action``, ``path``, ``to`` for a move, ``bytes``, ``rule``, and ``how`` for re-creatable data) to
``<pack>/.memory/housekeeping.jsonl``.

Config (optional, project-owned, committed), ``<pack>/housekeeping.json``::

    {"budget_gb": {"project": 150, "__data": 80, "__out": 60}, "keep_recent_hours": 24,
     "prune": [{"glob": "__out/*", "older_than_days": 7},
               {"glob": "__data/downloads/*", "older_than_days": 2, "recreatable": true,
                "how": "tools/fetch/fetch.sh downloads it again"}],
     "protect": ["__data/source"], "protect_names": ["log", "logs", "*.log"],
     "memory_allow": ["notes-*.md"], "root_allow": ["paper"]}

Globs are relative to the project root (``{pack}`` stands for the pack folder, ``**`` spans folders), except
``protect_names``, which are names (no ``/``) and replace the default list when given; a
``budget_gb`` key is ``project`` or a path; a GB is 2**30 bytes; a rule may carry a ``note``; keys starting with ``_``
are comments. Without the file: no budgets, no prune rules (the markers still work), the built-in allowlists.

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
import re
import shutil
import stat
import subprocess
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
KEEP, DISPOSABLE, RECREATABLE = ".keep", ".disposable", ".recreatable"
DATA_ROOTS = ("__data", "__out")
JOB_ROOTS = ("{pack}/.memory/jobs", "__out/jobs")
HANDOVER = "handover-*.md"
LIST_MAX = 30          # text lines per list; --json lists everything
UNREADABLE_MAX = 50
MAX_DEPTH = 400
HOW_MAX = 300          # characters of a re-creatable rule's or marker's how

# Never deleted, whatever a rule or marker says: a path part with one of these names (case-insensitive).
NEVER_NAMES = (
    ("submission", ("submission*",)),
    ("raw trace", ("trace", "traces", "trace-*", "traces-*", "*.trace")),
)
_NEVER = tuple((kind, re.compile("|".join(fnmatch.translate(p) for p in pats))) for kind, pats in NEVER_NAMES)
# Held by default the same way, but a project may change or drop them (protect_names in housekeeping.json).
PROTECT_NAMES = ("raw", "raw-*", "raw_*", "log", "logs", "*.log", "*.log.*")

# Scripts of live jobs read for the paths they use: code at any depth up to SCAN_DEPTH, config near the top.
CODE_SUFFIXES = (".sh", ".bash", ".zsh", ".ksh", ".py", ".pl", ".rb", ".r", ".jl", ".js", ".mjs", ".ts", ".lua",
                 ".sbatch", ".slurm", ".pbs", ".job", ".cmd", ".bat", ".ps1", ".mk")
CONFIG_SUFFIXES = (".json", ".yaml", ".yml", ".toml", ".cfg", ".conf", ".ini")
SCRIPT_NAMES = ("makefile", "dockerfile", "justfile", "procfile", "crontab")
SECRET_NAMES = ("*.env", ".env*", "credentials*", "auth.json", "kaggle.json", "*.pem", "*.key", "id_rsa*",
                "id_ed25519*", "*secret*", "token", "token.*", "*.token", "*_token", "*_token.*")
SKIP_DIRS = ("__pycache__", "node_modules", ".git", ".venv", "venv", "site-packages")
SCAN_DEPTH = 4         # folder levels below a live job read for code; config files only at the top two
SCRIPT_MAX = 256 * 1024
SCAN_FILES = 5000      # past either cap every move and deletion is held: the references are not fully known
SCAN_BYTES = 128 * 1024 ** 2
_TOKEN = re.compile(r"[\w.\-/+@%~]+")
FRAGMENT_MAX = 12      # path parts per indexed fragment
TOKEN_PARTS = 64

# Canonical entries of <pack>/.memory/: the framework's, then the bundled plugins' state files.
MEMORY_ALLOW = (
    "context.md", "directions.md", "improvements.md", "resources.md", "credentials.md", "config.json",
    "schedule.json", "spend.jsonl", "housekeeping.jsonl", "interactions", "interactions.jsonl", "jobs", "archive",
    ".empty", "spec-v0.md", "PAUSED",
    "hosts.json", "resource-sharing.json", "resource-sharing-seen.json", "lease-ends.json", "colossus-leases.md",
    "ai-spend.jsonl", "brev-costs.md", "visual", "visual-qa-endpoints.json",
    "clock.json", "alerts.jsonl", "host-health.json", "host-health-state.json", "card.md",
)
# OS and sync bookkeeping in .memory/: never moved (the session-start sweep merges conflict copies).
MEMORY_IGNORE = (".DS_Store", ".stfolder", ".stversions", ".stignore", ".syncthing.*", "~syncthing~*",
                 ".solaris-tmp-*", "*.sync-conflict-*")
# Top-level entries of a project root besides its pack and the manifest's workspaces.
ROOT_ALLOW = ("AGENTS.md", "CLAUDE.md", "README.md", "LICENSE*", "NOTICE*", "DISCLAIMER*", "source", "tools",
              "reports", "research", "submissions", "__*")
CONFIG_KEYS = ("budget_gb", "keep_recent_hours", "prune", "protect", "protect_names", "memory_allow", "root_allow")

HELP_EPILOG = f"""\
re-creatable data (pruned at every pass without asking; each deletion logs its how):
  a prune rule in <pack>/housekeeping.json with "recreatable": true and "how": "<one line: how to get it back>",
  e.g. {{"glob": "__data/downloads/*", "older_than_days": 2, "recreatable": true, "how": "tools/fetch/fetch.sh"}};
  or a .recreatable marker file in a folder under __data/ or __out/, its first line the how.
never deleted, whatever a rule or marker says:
  the pack (a plain rule may reach <pack>/.memory/archive/), .git, submissions and raw traces
  ({', '.join(p for _, pats in NEVER_NAMES for p in pats)}) anywhere in an entry's path or inside it.
protected unless the project says otherwise:
  protect globs, .keep markers, and protect_names: names held anywhere in an entry's path or inside it, by default
  {json.dumps(list(PROTECT_NAMES))} (raw data and run logs); set "protect_names" in housekeeping.json to change the
  list, or to [] to drop it.
held while in use (tidy and prune):
  an entry that a live job's script or a running process names (by its path from the project root, or a job
  folder by its name); prune also holds what the context file or newest handover note names and anything inside
  a live job. A job (in <pack>/.memory/jobs/ or __out/jobs/) is live when named there, changed within
  keep_recent_hours, named by a running process, or named by a live job's script. The plan prints the referrer
  (a file, or a process as its pid, executable name and the path it names). When the process list cannot be
  read, or the scripts pass the read caps, every move and deletion is held.
"""

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
           "protect": [], "protect_names": list(PROTECT_NAMES), "memory_allow": [], "root_allow": []}
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
                "glob", "older_than_days", "note", "recreatable", "how"}:
            raise HousekeepingError(f"{what} must be an object with glob and older_than_days (and optional "
                                    "recreatable, how and note)")
        recreatable = rule.get("recreatable", False)
        if not isinstance(recreatable, bool):
            raise HousekeepingError(f"{what}.recreatable must be true or false")
        how = rule.get("how")
        if recreatable:
            if not isinstance(how, str) or not how.strip():
                raise HousekeepingError(f"{what}: a recreatable rule needs how (one line: how to get the data back)")
            if "\n" in how.strip() or "\r" in how or len(how.strip()) > HOW_MAX:
                raise HousekeepingError(f"{what}.how must be one line of at most {HOW_MAX} characters")
            how = how.strip()
        elif how is not None:
            raise HousekeepingError(f"{what}.how goes with \"recreatable\": true")
        cfg["prune"].append({"glob": _rel_glob(rule["glob"], f"{what}.glob"),
                             "older_than_days": _number(rule["older_than_days"], f"{what}.older_than_days"),
                             "recreatable": recreatable, "how": how})
    for key in ("protect", "protect_names", "memory_allow", "root_allow"):
        if key in data:
            cfg[key] = _str_list(data[key], f"{where}: {key}")
    for pattern in cfg["protect"]:
        _rel_glob(pattern, f"{where}: protect entry")
    for name in cfg["protect_names"]:
        if "/" in name or "\\" in name:
            raise HousekeepingError(f"{where}: protect_names entry {name!r} must be a name, not a path")
    return cfg


def _names_re(patterns) -> "re.Pattern | None":
    """One case-insensitive matcher for a list of name globs (None for an empty list)."""
    pats = [fnmatch.translate(p.casefold()) for p in patterns]
    return re.compile("|".join(pats)) if pats else None


class Tree:
    """What a walk found in one entry's tree."""

    __slots__ = ("bytes", "files", "newest", "complete", "keep", "git", "mount", "disposable", "recreatable", "never",
                 "guarded")

    def __init__(self, newest: float = 0.0):
        self.bytes = 0
        self.files = 0
        self.newest = newest        # newest mtime or ctime anywhere in the tree, the entry itself included
        self.complete = True        # every folder in it was read
        self.keep = False           # a .keep marker in it or below it
        self.git = False            # a .git in it or below it
        self.mount = False          # a folder in it or below it is on another file system
        self.disposable = False     # this folder itself holds a .disposable marker file
        self.recreatable = False    # this folder itself holds a .recreatable marker file
        self.never = ""             # a never-deleted entry (submission, raw trace) below it, relative to the root
        self.guarded = ""           # an entry below it that a protect_names pattern names, relative to the root

    def add(self, other: "Tree") -> None:
        self.bytes += other.bytes
        self.files += other.files
        self.newest = max(self.newest, other.newest)
        self.complete = self.complete and other.complete
        self.keep = self.keep or other.keep
        self.git = self.git or other.git
        self.mount = self.mount or other.mount
        self.never = self.never or other.never
        self.guarded = self.guarded or other.guarded


class Walker:
    """Measures trees under a project root: never follows symlinks, counts each hard-linked file once."""

    def __init__(self, root: str, record: tuple = (), names: "re.Pattern | None" = None):
        self.root = root
        self.record = tuple(r + "/" for r in record)   # keep the Tree of every folder at or under these
        self.names = names                              # protect_names: note the first entry they name
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
                        elif entry.name == RECREATABLE and stat.S_ISREG(st.st_mode):
                            tree.recreatable = True
                    if _never_kind(entry.name):
                        child.never = sub
                    if self.names is not None and self.names.match(entry.name.casefold()):
                        child.guarded = sub
                    tree.add(child)
                    if children is not None:
                        children.append((entry.name, is_dir, child))
        except OSError:
            tree.add(self._lost(rel))
        if (rel + "/").startswith(self.record):
            self.dirs[rel] = tree
        return tree


def _never_kind(name: str) -> str:
    """``submission`` or ``raw trace`` when a path part with this name is never deleted, else empty."""
    folded = name.casefold()
    return next((kind for kind, pattern in _NEVER if pattern.match(folded)), "")


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


def _read_text(path: str, limit: int = SCRIPT_MAX) -> "str | None":
    """The start of a text file; None for a binary file or one that cannot be read."""
    try:
        with open(path, "rb") as f:
            data = f.read(limit)
    except OSError:
        return None
    if b"\0" in data[:4096]:
        return None
    return data.decode("utf-8", errors="replace")


def _newest_handover(root: str, memory: str) -> "str | None":
    notes = [e for e in _listdir(os.path.join(root, memory))
             if fnmatch.fnmatchcase(e.name, HANDOVER) and e.is_file(follow_symlinks=False)]
    return max(notes, key=lambda e: (_mtime(e), e.name)).name if notes else None


def _index(table: dict, text: str, ref: tuple, words: bool = True) -> None:
    """Map each path fragment in ``text`` (every run of consecutive parts of a path-like token, casefolded) to
    ``ref``; lone words too with ``words``."""
    for token in set(_TOKEN.findall(text)):
        if "/" not in token and not words:
            continue
        parts = [p for p in token.casefold().split("/") if p not in ("", ".")][:TOKEN_PARTS]
        for i in range(len(parts)):
            for j in range(i + 1, min(len(parts), i + FRAGMENT_MAX) + 1):
                refs = table.setdefault("/".join(parts[i:j]), [])
                if len(refs) < 3 and ref not in refs:
                    refs.append(ref)


def _processes() -> list:
    """``(pid, command line)`` of the running processes but this one and its ancestors (the command that started
    this run names paths too); OSError when ps fails."""
    try:
        out = subprocess.run(["ps", "-A", "-ww", "-o", "pid=,ppid=,command="], capture_output=True,
                             encoding="utf-8", errors="replace", timeout=30, check=False)
    except (subprocess.SubprocessError, ValueError) as exc:
        raise OSError(str(exc)) from None
    if out.returncode != 0:
        raise OSError(f"ps exit {out.returncode}")
    rows = {}
    for row in out.stdout.splitlines():
        cols = row.split(None, 2)
        if len(cols) == 3 and cols[0].isdigit() and cols[1].isdigit():
            rows[int(cols[0])] = (int(cols[1]), cols[2].strip())
    mine, pid = set(), os.getpid()
    while pid > 1 and pid not in mine and len(mine) < 64:
        mine.add(pid)
        pid = rows[pid][0] if pid in rows else os.getppid() if pid == os.getpid() else 0
    return [(pid, cmd) for pid, (_, cmd) in sorted(rows.items()) if pid not in mine and cmd]


def _exe_name(cmd: str) -> str:
    """The name of a command line's executable (its first word's last part), safe to print."""
    first = cmd.split(None, 1)[0] if cmd.strip() else ""
    return re.sub(r"[^\w.+-]", "", first.rsplit("/", 1)[-1])[:40] or "?"


def _script_kind(name: str, st) -> str:
    """``code``, ``config`` or empty: whether a file is read for the paths it uses (never a secret or a big file)."""
    folded = name.casefold()
    if (name.startswith(".") or not stat.S_ISREG(st.st_mode) or st.st_size > SCRIPT_MAX
            or _matches(name, SECRET_NAMES)):
        return ""
    if folded.endswith(CODE_SUFFIXES) or folded in SCRIPT_NAMES or st.st_mode & 0o111:
        return "code"
    return "config" if folded.endswith(CONFIG_SUFFIXES) else ""


class Refs:
    """The live jobs, and the paths that they, running processes and the context file name."""

    def __init__(self):
        self.named = {}          # path fragment -> refs from the context file and the newest handover note
        self.used = {}           # path fragment -> refs from live jobs' scripts and processes' command lines
        self.live = {}           # job (path from the root) -> why it counts as live
        self.scanned = 0         # script files read
        self.bytes = 0
        self.processes = None    # command lines read; None when ps did not run
        self.complete = True     # the process list and every script of every live job were read
        self.note = ""

    def held(self, rel: str, job: bool, named: bool = True, live: bool = True) -> "tuple[str, str]":
        """Why ``rel`` must stay and what refers to it, or two empty strings. A job folder also matches by name."""
        if not self.complete:
            return "references not fully read", self.note
        return self.lookup(rel, job, named, live)

    def lookup(self, rel: str, job: bool, named: bool = True, live: bool = True) -> "tuple[str, str]":
        """``held`` without the completeness check. A process is shown by its pid, its executable's name and the
        path it names, never by its command line (it may carry a secret)."""
        keys = [rel.casefold()]
        if job:
            keys.append(keys[0].rsplit("/", 1)[-1])
        for table in (self.used, self.named) if named else (self.used,):
            for key in keys:
                for why, by, path in table.get(key, ()):
                    if path and (path == rel or path.startswith(rel + "/")):
                        continue   # the entry's own scripts
                    if why == "used by a process":
                        by += f": {rel if key == keys[0] else rel.rsplit('/', 1)[-1]}"
                    return why, by
        if live:
            for job_rel, why in self.live.items():
                if rel.startswith(job_rel + "/") or job_rel.startswith(rel + "/"):
                    return "live job", f"{job_rel} ({why})"
        return "", ""

    def read(self, root: str, rel: str, st) -> bool:
        """Index one script; False once a cap is reached (then nothing more is read and every move or deletion
        is held)."""
        if self.scanned >= SCAN_FILES or self.bytes + st.st_size > SCAN_BYTES:
            self.complete = False
            self.note = (f"live job scripts past {SCAN_FILES} files or {human(SCAN_BYTES)}: every move and deletion "
                         "held")
            return False
        text = _read_text(os.path.join(root, rel))
        self.scanned += 1
        self.bytes += st.st_size
        if text:
            _index(self.used, text, ("used by a live job", rel, rel))
        return True

    def scan(self, root: str, rel: str) -> None:
        """Read the scripts of the job (folder or file) at ``rel``."""
        try:
            st = os.lstat(os.path.join(root, rel))
        except OSError:
            return
        if not stat.S_ISDIR(st.st_mode):
            if _script_kind(rel.rsplit("/", 1)[-1], st):
                self.read(root, rel, st)
            return
        stack = [(rel, 0)]
        while stack:
            here, depth = stack.pop()
            for entry in _listdir(os.path.join(root, here)):
                sub = f"{here}/{entry.name}"
                try:
                    st = entry.stat(follow_symlinks=False)
                except OSError:
                    continue
                if stat.S_ISDIR(st.st_mode):
                    if depth < SCAN_DEPTH and not entry.name.startswith(".") and entry.name.casefold() not in SKIP_DIRS:
                        stack.append((sub, depth + 1))
                    continue
                kind = _script_kind(entry.name, st)
                if (kind == "code" or (kind == "config" and depth <= 1)) and not self.read(root, sub, st):
                    return


def find_refs(root: str, pack: str, recent: float, walker: "Walker | None" = None) -> Refs:
    """Find the live jobs and index what they, the running processes and the context file name (module doc)."""
    refs = Refs()
    memory = f"{pack}/.memory"
    named = []
    for name in ("context.md", _newest_handover(root, memory)):
        text = _read_text(os.path.join(root, memory, name), 16 * 1024 ** 2) if name else None
        if text:
            named.append(text)
            _index(refs.named, text, ("named in context or handover", f"{memory}/{name}", None))
    files = []
    try:
        procs = _processes()
    except OSError as exc:
        # a running job that has not written for a while is then unknown: hold everything, like an over-cap scan
        refs.complete, procs = False, []
        refs.note = f"processes not read ({exc}): every move and deletion held"
    else:
        refs.processes = len(procs)
    for pid, cmd in procs:
        _index(refs.used, cmd, ("used by a process", f"process {pid} ({_exe_name(cmd)})", None), words=False)
        for token in _TOKEN.findall(cmd):
            if token.startswith("/") and len(files) < SCAN_FILES:
                real = os.path.realpath(token)
                if real.startswith(root + os.sep) and os.path.isfile(real):
                    files.append(_norm(os.path.relpath(real, root)))
    jobs = []
    for top in JOB_ROOTS:
        top = top.replace("{pack}", pack)
        if _symlink_on_path(root, top):
            continue
        for entry in _listdir(os.path.join(root, top)):
            try:
                st = entry.stat(follow_symlinks=False)
            except OSError:
                continue
            if not entry.name.startswith(".") and (stat.S_ISDIR(st.st_mode) or _script_kind(entry.name, st)):
                jobs.append(f"{top}/{entry.name}")
    for rel in jobs:
        tree = walker.dirs.get(rel) if walker else None
        if any(rel.rsplit("/", 1)[-1] in text for text in named):
            refs.live[rel] = "named in context or handover"
        elif refs.lookup(rel, True, named=False, live=False)[0]:
            refs.live[rel] = "used by a process"
        elif (tree or Walker(root).measure(rel)).newest > recent:
            refs.live[rel] = "recent"
    # a job that a live job's script names is live too, and so on
    queue, seen = [*refs.live, *files], set()
    while queue and refs.complete:
        rel = queue.pop(0)
        if rel in seen:
            continue
        seen.add(rel)
        refs.scan(root, rel)
        if not refs.complete:
            break
        for job in jobs:
            if job not in refs.live:
                why = refs.lookup(job, True, named=False, live=False)[0]
                if why:
                    refs.live[job] = why
                    queue.append(job)
    return refs


def _plan_move(root, rel, dest, is_dir, tree, why, recent, moves, held, taken, refs=None, job=False) -> None:
    item = {"path": rel, "dir": is_dir, "bytes": tree.bytes, "newest": _iso(tree.newest), "reason": why}
    used, by = refs.held(rel, job, named=False, live=False) if refs else ("", "")
    if used:
        held.append({**item, "held": used, "by": by})
    elif tree.newest > recent:
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


def plan_tidy(root: str, pack: str, cfg: dict, recent: float, refs: "Refs | None" = None) -> "tuple[list, list]":
    """The moves tidy would make, and the untidy items held back (in use, recent, unreadable or leaving the
    project)."""
    walker = Walker(root)
    memory = f"{pack}/.memory"
    moves, held, taken = [], [], set()
    entries = _listdir(os.path.join(root, memory))
    allow = MEMORY_ALLOW + tuple(cfg["memory_allow"])
    newest_note = _newest_handover(root, memory)
    note_names = {e.name for e in entries if fnmatch.fnmatchcase(e.name, HANDOVER) and e.is_file(follow_symlinks=False)}
    for entry in entries:
        if (entry.name.startswith(".") or _matches(entry.name, MEMORY_IGNORE) or _matches(entry.name, allow)
                or entry.name == newest_note):
            continue
        rel = f"{memory}/{entry.name}"
        tree = walker.measure(rel)
        month = time.strftime("%Y-%m", time.gmtime(_mtime(entry) or tree.newest))
        why = "older handover note" if entry.name in note_names else "not on the .memory allowlist"
        _plan_move(root, rel, f"{memory}/archive/{month}/{entry.name}", entry.is_dir(follow_symlinks=False), tree,
                   why, recent, moves, held, taken, refs)
    # a job named in the context file or the newest handover note may still be running
    named = []
    for name in ("context.md", newest_note):
        if name:
            try:
                named.append((f"{memory}/{name}", Path(root, memory, name).read_text(errors="replace")))
            except OSError:
                pass
    for entry in _listdir(os.path.join(root, memory, "jobs")):
        if entry.is_dir(follow_symlinks=False):
            rel = f"{memory}/jobs/{entry.name}"
            tree = walker.measure(rel)
            by = next((src for src, text in named if entry.name in text), "")
            if by:
                held.append({"path": rel, "dir": True, "bytes": tree.bytes, "newest": _iso(tree.newest),
                             "reason": "job scratch", "held": "named in context or handover", "by": by})
                continue
            _plan_move(root, rel, f"__out/jobs/{entry.name}", True, tree, "job scratch", recent, moves, held, taken,
                       refs, job=True)
    moves.sort(key=lambda m: (-m["bytes"], m["path"]))
    return moves, held


class Guard:
    """What every prune candidate is checked against, when planned and again right before it is deleted."""

    def __init__(self, root: str, pack: str, cfg: dict, now: float, refs: "Refs | None" = None):
        self.root, self.pack, self.refs = root, pack, refs
        self.recent = now - cfg["keep_recent_hours"] * 3600
        self.prune_roots = (*DATA_ROOTS, f"{pack}/.memory/archive")
        self.jobs = tuple(r.replace("{pack}", pack) for r in JOB_ROOTS)
        self.protect = [p.replace("{pack}", pack) for p in cfg["protect"]]
        self.protected = {rel for p in self.protect for rel in _glob(root, p, hidden=True, case_sensitive=False)}
        self.names = _names_re(cfg["protect_names"])

    def check(self, rel: str, match: dict, walker: Walker, now: float) -> "tuple[dict, str]":
        """The candidate's item, and why it may not be deleted (empty when it may)."""
        root = self.root
        item = {"path": rel, "rule": match["rule"]}
        parts = rel.split("/")
        if rel in ("", ".") or ".." in parts or os.path.isabs(rel):
            return item, "outside the project"
        if ".git" in parts:
            return item, ".git"
        if not any(rel.startswith(r + "/") for r in self.prune_roots):
            return item, "outside __data/, __out/ and the archive"
        if match["how"] is not None and (rel + "/").startswith(self.pack + "/"):
            return item, "in the pack: re-creatable rules never reach it"
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
        if any(_matches(a, self.protect) for a in above) or any(
                p.casefold() == rel.casefold() or p.casefold().startswith(rel.casefold() + "/")
                or rel.casefold().startswith(p.casefold() + "/") for p in self.protected):
            return item, "protected"
        if tree.git:
            return item, "holds .git"
        never = next((a for a in above if _never_kind(a.rsplit("/", 1)[-1])), "") or tree.never
        if never:
            item["entry"] = never
            return item, f"never deleted: {_never_kind(never.rsplit('/', 1)[-1])}"
        if self.names is not None:
            named = next((a for a in above if self.names.match(a.rsplit("/", 1)[-1].casefold())), "") or tree.guarded
            if named:
                item["entry"] = named
                return item, "protected name"
        try:
            own_fs = os.lstat(path).st_dev == os.lstat(os.path.dirname(path)).st_dev
        except OSError:
            own_fs = False
        if tree.mount or not own_fs:
            return item, "mount point"
        if not tree.complete:
            return item, "unreadable parts"
        if match["how"] == "":
            return item, f"no how: the first line of {RECREATABLE} says how to get the data back"
        if self.refs:
            why, by = self.refs.held(rel, rel.rsplit("/", 1)[0] in self.jobs)
            if why:
                item["by"] = by
                return item, why
        if tree.newest > self.recent:
            return item, "recent"
        for a in above[:-1]:
            if a in self.prune_roots:
                continue
            up = walker.dirs.get(a)
            if up is not None and up.newest > self.recent:
                return item, "inside a recent folder"
        days = match["days"]
        if days is not None and tree.newest > now - days * 86400:
            return item, f"newer than {days:g} days"
        return item, ""


def _marker_how(root: str, rel: str) -> str:
    """The first line of the folder's ``.recreatable`` marker (empty when it has none)."""
    text = _read_text(os.path.join(root, rel, RECREATABLE), 4096) or ""
    return (text.splitlines() or [""])[0].strip()[:HOW_MAX]


def plan_prune(root: str, pack: str, cfg: dict, now: float, walker: Walker,
               refs: "Refs | None" = None) -> "tuple[list, list]":
    """Prune candidates and the matches held back. ``walker`` has walked the prune roots, recording them."""
    guard = Guard(root, pack, cfg, now, refs)
    held, found = [], {}
    for r in guard.prune_roots:
        if os.path.islink(os.path.join(root, r)):
            held.append({"path": r, "rule": "", "held": "symlink: not walked"})
    for rel, tree in walker.dirs.items():
        if "/" in rel and rel.split("/", 1)[0] in DATA_ROOTS:
            if tree.recreatable:
                found.setdefault(rel, {"rule": RECREATABLE, "days": None, "how": _marker_how(root, rel)})
            elif tree.disposable:
                found.setdefault(rel, {"rule": DISPOSABLE, "days": None, "how": None})
    for rule in cfg["prune"]:
        label = f"{rule['glob']} older than {rule['older_than_days']:g} days"
        if rule.get("recreatable"):
            label += ", re-creatable"
        for rel in _glob(root, rule["glob"].replace("{pack}", pack), hidden=False):
            found.setdefault(rel, {"rule": label, "days": rule["older_than_days"], "how": rule.get("how")})
    cands = []
    for rel in sorted(found, key=lambda r: (r.count("/"), r)):
        if any(rel.startswith(c["path"] + "/") for c in cands):
            continue   # inside a candidate already
        match = found[rel]
        item, why = guard.check(rel, match, walker, now)
        if why:
            held.append({**item, "held": why})
        else:
            cands.append({**item, "older_than_days": match["days"], "how": match["how"]})
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
        if c.get("how"):
            entry["how"] = c["how"]
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
           "layout": None, "live": None, "unreadable": [], "applied": [], "failed": [], "exit": 0, "reason": ""}
    walker = Walker(root, record=(*DATA_ROOTS, f"{pack}/.memory/archive"), names=_names_re(cfg["protect_names"]))
    if command == "report":
        res["sizes"] = measure_sizes(root, walker)
        res["budgets"] = check_budgets(root, pack, cfg, res["sizes"], walker)
        res["layout"] = layout_notes(root, pack, cfg, manifest)
    elif command == "prune":
        for r in walker.record:
            if os.path.lexists(os.path.join(root, r)):
                walker.measure(r.rstrip("/"))
    refs = find_refs(root, pack, recent, walker)
    res["live"] = {"jobs": [{"path": p, "why": w} for p, w in sorted(refs.live.items())], "scripts": refs.scanned,
                   "processes": refs.processes, "complete": refs.complete, "note": refs.note}
    if command in ("report", "tidy"):
        res["untidy"], res["untidy_held"] = plan_tidy(root, pack, cfg, recent, refs)
    if command in ("report", "prune"):
        res["prune"], res["prune_held"] = plan_prune(root, pack, cfg, now, walker, refs)
    res["unreadable"] = [u for u in walker.unreadable if os.path.lexists(os.path.join(root, u))]
    if apply and command == "tidy":
        res["applied"], res["failed"] = apply_moves(root, log, res["untidy"])
    elif apply and command == "prune":
        guard = Guard(root, pack, cfg, now, refs)

        def recheck(c):
            # a fresh look at the candidate itself right before it goes
            match = {"rule": c["rule"], "days": c.get("older_than_days"), "how": c.get("how")}
            return guard.check(c["path"], match, Walker(root, names=guard.names), _now())[1]
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


def _held_by(item: dict) -> str:
    return f"  held  {_shown(item)}  {item['held']}: {item['by']}"


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
    live = res.get("live")
    if live and ((live["jobs"] and not res["apply"]) or live["note"]):
        procs = "no process list" if live["processes"] is None else f"{live['processes']} processes"
        lines.append(f"Live jobs: {len(live['jobs'])} ({live['scripts']} scripts read, {procs})"
                     + (f"; {live['note']}" if live["note"] else ""))
        if not res["apply"]:
            _capped(lines, live["jobs"], lambda j: f"  {j['path']}  ({j['why']})")
    if res["untidy"] is not None and not res["apply"]:
        moves = res["untidy"]
        lines.append(f"Untidy: {len(moves)} to move ({human(sum(m['bytes'] for m in moves))})"
                     + _held_summary(res["untidy_held"]))
        _capped(lines, moves, lambda m: f"  {_shown(m)} -> {m['to']}{'/' if m['dir'] else ''}  "
                                        f"({human(m['bytes'])}; {m['reason']})")
        _capped(lines, [h for h in res["untidy_held"] if h.get("by")], _held_by)
        tail.append(f"{len(moves)} untidy")
    if res["prune"] is not None and not res["apply"]:
        cands = res["prune"]
        lines.append(f"Prune candidates: {len(cands)} ({human(sum(c['bytes'] for c in cands))})"
                     + _held_summary(res["prune_held"]))
        _capped(lines, cands, lambda c: f"  {human(c['bytes']):>9}  {c['age_days']:6.1f} d  {_shown(c)}  "
                                        f"[{c['rule']}]" + (f"  how: {c['how']}" if c.get("how") else ""))
        _capped(lines, [h for h in res["prune_held"] if h.get("by")], _held_by)
        tail.append(f"{_n(len(cands), 'prune candidate')} ({human(sum(c['bytes'] for c in cands))})")
    if res["apply"]:
        verb = "Moved" if res["command"] == "tidy" else "Deleted"
        done = res["applied"]
        lines.append(f"{verb}: {len(done)} ({human(sum(d['bytes'] for d in done))})")
        _capped(lines, done, lambda d: f"  {d['path']}" + (f" -> {d['to']}" if "to" in d else "")
                + (f"  (how: {d['how']})" if d.get("how") else ""))
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
    parser = argparse.ArgumentParser(prog="solaris.tools.housekeeping", description=__doc__.splitlines()[0],
                                     epilog=HELP_EPILOG, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("command", nargs="?", choices=("report", "tidy", "prune"), default="report",
                        help="report (default, read-only), tidy (move untidy items) or prune (delete by the rules "
                             "and markers, re-creatable data included)")
    parser.add_argument("--dir", required=True, help="the project root (embedded mode: the repo root)")
    parser.add_argument("--apply", action="store_true", help="tidy: make the moves; prune: delete, logging each "
                                                             "deletion (default: print the plan)")
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
