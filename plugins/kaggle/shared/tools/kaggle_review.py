# rev. 2

"""kaggle_review: one sandboxed outside review through OpenCode.

A model from another family reads a plan, a pick or a result and attacks it. This tool runs one such reviewer
through OpenCode (`opencode run`), read-only, on copies of the files it is given, and saves the reviewer's text.

    python3 <plugin-dir>/tools/kaggle_review.py ID PROMPT_FILE [FILE ...]              one review of the files given
    python3 <plugin-dir>/tools/kaggle_review.py --package FOLDER [--id ID] [--day DAY]  one review of a package
    python3 <plugin-dir>/tools/kaggle_review.py --gate                                 the key gate alone, no review

Run it from the project root (or pass --root). Paths are relative to the project root. PROMPT_FILE holds the
questions and is sent as the message; each FILE (a file or a folder) is copied for the reviewer to read. A package
is a folder the master prepares: prompt.md (the questions), STATE.md (the state, written for an outsider),
files.txt (one path per line, relative to the project root; blank lines and lines that start with # are skipped)
and, last, READY (one line, the day the package is for, YYYY-MM-DD). The package form reviews prompt.md, STATE.md
and the files named in files.txt; ID is then the folder's name, or --id.

A review can take as long as --timeout. Called from a turn, give the shell tool a time limit above --timeout, or
run the tool as a watched background job of the autocode plugin (`guard.py job start ... -- python3
<plugin-dir>/tools/kaggle_review.py ...`): a shell tool that ends the call first stops the review.

Flags:
  --model PROVIDER/MODEL  the reviewer, a model of the session's OpenCode config (also PROVIDER/MODEL#VARIANT);
                          default "kaggle.review.model"
  --variant VARIANT       the model's variant (its effort); default "kaggle.review.variant" when the model comes
                          from "kaggle.review.model", else the model's own default
  --reviewer NAME         a member of the pool "kaggle.review.pool": its model and variant, and the tag NAME
  --pool                  run every member of the pool at once on the same input, each as --reviewer NAME; the
                          worst exit code wins, and a member killed by a signal counts as a failed run (1)
  --tag TAG               added to ID as ID-TAG, so several reviewers of one input write to separate folders
  --out FILE              where the review goes (default __out/review/<ID>/review.md); the run's other files go
                          beside it; not with --pool
  --package FOLDER        the package form (see above); a link to the folder works, and names the folder it points at
  --id ID                 the package form: the review's ID (default: the package folder's name)
  --day DAY               the package form: READY must hold this day (today, or YYYY-MM-DD), else exit 3; today is
                          the owner's day ("owner.timezone" in the pack's settings, else this machine's zone)
  --force                 run again though the review is there; the earlier review keeps a dated name (below)
  --allow-long            send .md and .txt lines over 2,000 characters as they are (without it: exit 2; a reviewer's
                          reader cuts such lines and can burn a run on them; wrap them at about 120 first)
  --timeout SECONDS       the reviewer's time limit (default "kaggle.review.timeout", else 3600)
  --gate                  run only the key gate for the model (below)
  --root DIR              the project root (default: found from the working directory)
  --opencode PATH         the OpenCode command (default: opencode on PATH, else ~/.opencode/bin/opencode)
  --session-config FILE   the session's OpenCode config (default "kaggle.review.session_config", else
                          ~/.config/opencode/opencode.jsonc, else opencode.json beside it)

Pack settings (<pack>/.memory/config.json, else <pack>/defaults.json):
  "kaggle.review.model"           the default reviewer, "provider/model"
  "kaggle.review.variant"         its variant
  "kaggle.review.pool"            {"<name>": {"model": "provider/model", "variant": "<variant>"}, ...}
  "kaggle.review.timeout"         seconds
  "kaggle.review.max_steps"       the most model calls one review may make (default 150; the longest of 720 real
                                  reviews took 111): at the limit OpenCode asks for the answer without another
                                  tool call, and the tool warns that the review may be cut short. With the time
                                  limit it is what bounds a review's cost: there is no limit in dollars
  "kaggle.review.session_config"  the session's OpenCode config file
  "kaggle.review.base_url"        when set, the provider's endpoint in that config must be this one, else the key
                                  is not used (exit 4)
  "kaggle.review.category"        the cost ledger's category for reviews (default "outside reviews")
  "kaggle.review.max_mb"          the most the copies may hold, in MB (default 200)
  "owner.timezone"                the zone of the ledger's day and of --day today

When the review is already there. A call exits 0 without running anything when the review file has text and its
own record (run.json beside it) says that a run for this ID wrote that file and ended well, and, for a package,
that READY held then what it holds now. The file alone does not count. So a package folder used again on a later
day, with a new READY, is reviewed again; with --day, yesterday's READY is refused first (exit 3). Before a new
run (a new READY, --force, a review file without such a record) the review that is there and its record move to
dated names beside it, review-<day>.md and run-<day>.json (the day its package was for, else the day it was
written; -2, -3, ... when the name is taken): nothing is overwritten.

The sandbox:
- The reviewer works in a fresh temporary folder (under $TMPDIR) that holds only copies of the files given. The
  tool refuses to run (exit 5) when that folder is below a folder that holds .git, .opencode, .claude, .agents,
  AGENTS.md, CLAUDE.md or opencode.json(c): OpenCode reads those in every folder above its own. A file keeps its
  project path in the copy; a file from the review's own folder or from the package goes to the top.
- Never copied: symbolic links inside a folder; anything in a .git, .memory, .opencode, .claude, .agents, .venv*
  or __pycache__ folder; AGENTS.md, CLAUDE.md, opencode.json(c); and files named like credentials (credentials*,
  kaggle.json, auth.json, secrets*, *.secret, *.token, .env, .env.*, .netrc, .npmrc, .pypirc, *.pem, *.key,
  id_rsa*, id_ed25519*). Named on the command line or in files.txt, such a file stops the run (exit 2); inside a
  folder it is left out and named on stderr. The check goes by names: a secret in a file with an ordinary name
  is copied like any other file.
- A package folder or a file outside the project stops the run (exit 2). Inside the project is what lies below
  its root, and what lies below the target of a linked root: a top-level __data or __out that is a symbolic link
  to a folder outside the project and that <pack>/housekeeping.json lists under "linked_roots" (the rule of the
  framework's housekeeping tool, for a project that keeps those folders on another drive). Such a file keeps its
  project path in the copy (__out/...), and it is the same file named through the link or by its real path. A
  top-level link that is not listed is not followed, and no path may leave the project or a linked root through
  .. or through another link. The prompt file and --out may be anywhere.
- OpenCode gets a home of its own for the run: a second temporary folder (mode 700) that is its HOME, its XDG
  config, data, state and cache folders and its TMPDIR. So the owner's OpenCode config, stored credentials and
  sessions are never loaded or touched, and what OpenCode writes of its own (its log, its session database) is
  in that folder. Its environment holds a short list of the caller's variables (PATH, the locale, the time zone,
  the user's name and shell, the proxy and certificate variables) and the key: no Kaggle, autocode or OpenCode
  variable reaches it, so it never acts for the calling session.
- The private config, in that home, holds the reviewer's provider with that one model, copied from the session's
  config without any key, and a read-only agent "review" with the step limit: every tool is denied, then read,
  glob, grep and list are allowed, then every write, shell, web, skill and subagent tool and every read outside
  the folder (external_directory) is denied by name. No other provider may be used; web search, sharing, updates
  and snapshots are off.
- The prompt goes to OpenCode on stdin, never on its command line, which other users of the machine can read.
- The reviewer runs in its own session and process group. The whole group is killed when the reviewer ends,
  when it is over its time limit (TERM, then KILL 20 seconds later) and, at once, when the runner gets SIGTERM,
  SIGHUP or SIGINT. On Linux the kernel also kills the reviewer when the runner dies in any other way (prctl
  PR_SET_PDEATHSIG); on other systems a reviewer can outlive a runner that was killed outright.
- Both temporary folders are removed when the run ends, on every way out the runner can take: a good end, a
  failure, the time limit, a stop signal. A runner killed outright (SIGKILL, a power loss) cannot remove them:
  they stay in $TMPDIR, named kaggle-review-*, until the next run of this tool in the project removes them. It
  finds them by the records in <root>/__data/kaggle/review/running/, one per run in progress.
- After the run the session's record is checked: a tool call that named a path outside the folder is a WARNING
  on stderr and is listed in run.json ("outside_paths"), with the count of denied calls.

The key: the review uses the key the calling session's own provider has. It is read at run time from that
provider's block in the session's OpenCode config (a literal value, or {env:NAME} resolved from the
environment), held in this process's memory, and given to OpenCode only as the environment variable
KAGGLE_REVIEW_KEY, which the private config names as {env:KAGGLE_REVIEW_KEY}. The call that reads the session
back afterwards gets no key. This tool never prints the key and never writes it to a file. OpenCode can: when a
provider's error repeats the key, OpenCode puts that error into its output, its log and its session database
(seen with OpenCode 2.3). So the key is cut from events.jsonl and stderr.log, as it is and as JSON text writes
it, before the run ends, and the log and the database are in the run's own home, which is removed. They outlast a
run only when the runner was killed outright, until the next run removes them. While the reviewer runs, the key
is in that process's environment, which the same user and root can read, as with any process.

The key gate: before a model's first real call, and again whenever the private config or the OpenCode binary
changed, one call is made with an invalid key and a one-line prompt, in two temporary folders of its own. It
must fail as a refused key (HTTP 401 or 403). If it gets an answer, or fails another way, some other credential
may be in use: nothing is run (exit 6). Passed gates are kept in <root>/__data/kaggle/review/gates.json (written
whole, under the .lock beside it); the gate call's own events.jsonl and stderr.log, which never see the real key,
in <root>/__data/kaggle/review/gate/<model>/.

Files written, beside --out (default <root>/__out/review/<ID>/):
  review.md          the reviewer's text; written only when OpenCode ended well and gave text
  review.partial.md  the text of a run that failed or timed out, when it gave any
  review-<day>.md, run-<day>.json   an earlier review and its record, kept when the review was run again
  events.jsonl       OpenCode's JSON event stream
  stderr.log         OpenCode's stderr
  copies.txt         the files the reviewer was given, as it saw them
  run.json           the run's record: the day, the ID, the package and what its READY held, model, variant, exit
                     codes, seconds, steps and the step limit, tokens, OpenCode's cost estimate, tool calls, denied
                     calls, errors, sessions, paths outside the folder, files left out, the review file
  .lock              held while a run writes here: a second run of the same review exits 1 at once
and one line per run that ended by itself or at its time limit in the cost ledger <pack>/.memory/spend.jsonl, as
the status page reads it: {"day": "YYYY-MM-DD", "category": "outside reviews", "usd": 0.1578, "what": "opencode
review <ID> (<model>, <variant>; opencode estimate)"}. A gate call costs nothing and writes no line. A run that a
signal stopped writes no review, no run.json and no ledger line: what it cost is not known.

The last line on stdout is the result: `kaggle_review <ID>: review written, ... -> <file>`, `... exists already;
not run again ...`, `... inputs not ready: <why>` or `... FAILED (<why>) ...`. Exit codes: 0 the review is there
(written now, or it was there already; with --gate: the gate passed), 1 the run failed (OpenCode failed or timed
out, the reviewer gave no text, or another run holds this review), 2 bad usage or bad input files, 3 the package
is not ready (a part is missing or empty, a file it names is missing, or READY is not --day), 4 no usable
provider or key in the session's OpenCode config, 5 the sandbox could not be made safe, 6 the key gate failed,
143, 129 or 130 the runner was stopped by SIGTERM, SIGHUP or SIGINT (128 plus the signal's number). With --pool:
the highest of the members' codes, a member killed by a signal counting as 1. Stdlib only; needs OpenCode 2.
"""

import argparse
import ctypes
import fcntl
import fnmatch
import hashlib
import json
import os
import pwd
import re
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
from collections import Counter
from contextlib import contextmanager
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

NAME = "kaggle_review"
EXIT_FAILED, EXIT_USAGE, EXIT_NOT_READY, EXIT_NO_KEY, EXIT_SANDBOX, EXIT_GATE = 1, 2, 3, 4, 5, 6
ISO = "%Y-%m-%dT%H:%M:%SZ"
ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")
# the only name the key has outside this process: the private config reads it from the child's environment
KEY_ENV = "KAGGLE_REVIEW_KEY"
ENV_RE = re.compile(r"\{env:([A-Za-z_][A-Za-z0-9_]*)\}")
AGENT = "review"
READ_TOOLS = ("read", "glob", "grep", "list")
# denied by name after the allows (the last matching rule wins), on top of the deny-everything rule
DENIED = ("edit", "write", "shell", "bash", "execute", "webfetch", "websearch", "browser", "task", "subagent",
          "skill", "external_directory")
# option names that may hold a secret: never copied from the session's provider block
SECRET_RE = re.compile(r"api[_-]?key|secret|password|authorization|credential|^headers?$|token$", re.I)
# what the child's environment keeps of the caller's
KEEP_ENV = ("PATH", "LANG", "LC_ALL", "LC_CTYPE", "TERM", "TZ", "USER", "LOGNAME", "SHELL",
            "HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY", "http_proxy", "https_proxy", "no_proxy",
            "SSL_CERT_FILE", "SSL_CERT_DIR", "NODE_EXTRA_CA_CERTS")
# never copied: folders by name, what OpenCode would load as instructions or config, files named like credentials
# (OpenCode 2 reads .opencode, .claude and .agents folders and opencode.json(c) in the folder and in every folder
# above it, and an AGENTS.md beside any file it reads)
SKIP_DIRS = (".git", ".memory", ".opencode", ".claude", ".agents", ".venv*", "__pycache__")
LOADED = ("AGENTS.md", "CLAUDE.md", "opencode.json", "opencode.jsonc")
PRIVATE = ("credentials*", "kaggle.json", "auth.json", "secrets*", "*.secret", "*.token", ".env", ".env.*", ".netrc",
           ".npmrc", ".pypirc", "*.pem", "*.key", "id_rsa*", "id_ed25519*")
# what no folder above the reviewer's may hold
ABOVE = (".git", ".opencode", ".claude", ".agents", *LOADED)
TOKENS = ("input", "output", "reasoning", "cache_read", "cache_write")
MAX_LINE = 2000
MAX_MB = 200
# model calls a review may make: the longest of 720 real reviews took 111
MAX_STEPS = 150
TIMEOUT = 3600
CATEGORY = "outside reviews"
# every throwaway folder's name starts so; a later run removes those a killed run left
FOLDER_PREFIX = "kaggle-review-"
# a stopped runner ends its reviewer at once; a reviewer over its time limit gets this long after TERM
STOP_SIGNALS = (signal.SIGTERM, signal.SIGHUP, signal.SIGINT)
TERM_GRACE = 20
PR_SET_PDEATHSIG = 1
REMOVED = "[key removed]"
# the gate: a key no provider has, a prompt that costs nothing, a short limit
GATE_KEY = "invalid-key-for-the-gate"
GATE_PROMPT = "Reply with the single word OK."
GATE_TIMEOUT = 300
REFUSED_STATUSES = (401, 403)
K_MODEL, K_VARIANT, K_POOL, K_TIMEOUT = ("kaggle.review.model", "kaggle.review.variant", "kaggle.review.pool",
                                         "kaggle.review.timeout")
K_CONFIG, K_BASE, K_CATEGORY, K_MAX_MB, K_ZONE = ("kaggle.review.session_config", "kaggle.review.base_url",
                                                  "kaggle.review.category", "kaggle.review.max_mb",
                                                  "owner.timezone")
K_MAX_STEPS = "kaggle.review.max_steps"
# the top-level data folders a project may keep on another drive and link into its root; the framework's
# housekeeping tool counts such a link's target as part of the project when this file of the pack lists its name
DATA_ROOTS = ("__data", "__out")
HOUSEKEEPING = "housekeeping.json"
# set once per run: the names that file lists, and of those the links that count now, each with its real target
LISTED = []
LINKED = {}
# the signal that is stopping this run, once one came
STOPPING = []


class Stop(Exception):
    """The run ends here with this exit code and one line, which names the review when it has an ID."""

    def __init__(self, code, text, label=None):
        super().__init__(text)
        self.code = code
        self.label = label


# ---- the project, its pack and its settings

def is_pack(d):
    # an ai-pack manifest carries framework_version and a project object; a plugin's has neither
    try:
        m = json.loads((Path(d) / "manifest.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    return isinstance(m, dict) and "framework_version" in m and isinstance(m.get("project"), dict)


def pack_of(d):
    """The ai-pack folder of d: its one direct child folder holding an ai-pack manifest.json, else None."""
    try:
        packs = sorted(c for c in Path(d).iterdir() if not c.name.startswith(".") and is_pack(c))
    except OSError:
        return None
    if len(packs) > 1:
        raise Stop(EXIT_USAGE, f"{d}: more than one ai-pack ({', '.join(p.name for p in packs)})")
    return packs[0] if packs else None


def walk_up(d):
    """d and the folders above it, stopping before the home folder, any folder above it, or /."""
    home = Path(os.path.expanduser("~"))
    homes = (home, home.resolve()) if home.is_absolute() else ()
    chain = []
    for f in (d, *d.parents):
        if f.parent == f or any(f == h or f in h.parents for h in homes):
            break
        chain.append(f)
    return chain


def find_root(start=None):
    """The project root (a folder with an ai-pack) at or above start, else a copied overlay's project, else None."""
    for d in walk_up(Path(start or os.getcwd()).resolve()):
        if pack_of(d):
            return d
    if start:
        return None
    # a copied overlay sits at <project>/<pack>/plugins/kaggle/tools/kaggle_review.py
    up = Path(__file__).resolve().parents
    if len(up) > 4 and up[2].name == "plugins" and walk_up(up[4]) and is_pack(up[3]) and pack_of(up[4]) == up[3]:
        return up[4]
    return None


class Settings:
    """The pack's settings: <pack>/.memory/config.json wins over <pack>/defaults.json."""

    def __init__(self, pack):
        self.layers = []
        for f in (pack / ".memory" / "config.json", pack / "defaults.json"):
            try:
                cfg = json.loads(f.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if isinstance(cfg, dict):
                self.layers.append(cfg)

    def get(self, key, default=None):
        for cfg in self.layers:
            if key in cfg:
                return cfg[key]
        return default


def owner_today(settings):
    """Today in the owner's zone ("owner.timezone"), else in this machine's."""
    name = settings.get(K_ZONE)
    try:
        tz = ZoneInfo(name) if isinstance(name, str) and name else None
    except Exception:  # an unknown zone name
        tz = None
    return (datetime.now(tz) if tz else datetime.now().astimezone()).date()


def below(path, base):
    """Whether a path is the base or below it; both as written, no link is followed."""
    path, base = Path(path), Path(base)
    return path == base or base in path.parents


def listed_roots(pack):
    """The names <pack>/housekeeping.json lists under "linked_roots", as the housekeeping tool takes them: a list
    of names, each __data or __out. A missing or unreadable file, or any other value, lists none."""
    try:
        data = json.loads((pack / HOUSEKEEPING).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    names = data.get("linked_roots") if isinstance(data, dict) else None
    if not isinstance(names, list) or not all(isinstance(n, str) and n in DATA_ROOTS for n in names):
        return []
    return list(dict.fromkeys(names))


def linked_roots(root, pack):
    """{name: real target} of the project's linked roots, by the housekeeping tool's rule: a top-level __data or
    __out that <pack>/housekeeping.json lists under "linked_roots", that is a symbolic link right now, and that
    leads to a folder outside the project which does not hold the project either. Two links whose targets
    overlap both fail the rule. The target of such a link counts as part of the project; every other link is a
    plain link."""
    found = {}
    for name in listed_roots(pack):
        link = os.path.join(root, name)
        if not os.path.islink(link):
            continue  # a real folder, or absent: nothing to follow
        real = Path(os.path.realpath(link))
        if real.is_dir() and not below(real, root) and not below(root, real):
            found[name] = real
    return {name: real for name, real in found.items()
            if not any(other != name and (below(real, base) or below(base, real)) for other, base in found.items())}


def project_path(path, root):
    """The path a file has in the project, relative to its root: for a path below the root as it is, for a path
    below a linked root's target under the link's name (__out/...). None for a path that is neither. The path
    is taken as written: no link in it is followed here."""
    path = Path(path)
    if below(path, root):
        return path.relative_to(root)
    for name, real in LINKED.items():
        if below(path, real):
            return Path(name) / path.relative_to(real)
    return None


def unlisted_link(path, root):
    """A word for a path refused as outside the project, when it is below a top-level data link that does not
    count as part of the project; else nothing."""
    links = [(os.path.realpath(Path(root) / name), name) for name in DATA_ROOTS
             if name not in LINKED and (Path(root) / name).is_symlink()]
    # where two links hold the path, the one that leads to the nearer folder is the one it is below
    for real, name in sorted(links, key=lambda x: len(x[0]), reverse=True):
        if not below(path, real):
            continue
        if name in LISTED:
            return (f" ({name} is listed as a linked root, but its link does not lead to a folder outside the "
                    "project and apart from the other linked root's)")
        return (f" ({name} is a link to a folder outside the project; it counts as part of the project once "
                f"\"linked_roots\" in the pack's {HOUSEKEEPING} lists it)")
    return ""


def rel(path, root):
    """A path as the project names it (see project_path), else as it is."""
    inside = project_path(path, root)
    return str(path) if inside is None else str(inside)


def write_atomic(path, text):
    """Write a file whole: a temp file beside it, then a rename."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


# ---- the session's OpenCode config: the provider and its key

def strip_jsonc(text):
    """JSON text from JSONC: comments outside strings and trailing commas removed."""
    out, i, n = [], 0, len(text)
    while i < n:
        c = text[i]
        if c == '"':
            j = i + 1
            while j < n and text[j] != '"':
                j += 2 if text[j] == "\\" else 1
            out.append(text[i:j + 1])
            i = j + 1
        elif text.startswith("//", i):
            j = text.find("\n", i)
            i = n if j < 0 else j
        elif text.startswith("/*", i):
            j = text.find("*/", i + 2)
            i = n if j < 0 else j + 2
        else:
            out.append(c)
            i += 1
    return re.sub(r",(\s*[}\]])", r"\1", "".join(out))


def real_home():
    # the account's home, not $HOME: a caller may run with another HOME
    try:
        return Path(pwd.getpwuid(os.getuid()).pw_dir)
    except KeyError:
        return Path(os.path.expanduser("~"))


def session_config_path(given):
    if given:
        return Path(str(given)).expanduser()
    d = real_home() / ".config" / "opencode"
    for name in ("opencode.jsonc", "opencode.json"):
        if (d / name).is_file():
            return d / name
    return d / "opencode.jsonc"


def read_provider(path, pid):
    """(the config's provider table name, the provider's block) from the session's OpenCode config. No message
    ever quotes the file: it holds the key."""
    try:
        cfg = json.loads(strip_jsonc(Path(path).read_text(encoding="utf-8")))
    except OSError as e:
        raise Stop(EXIT_NO_KEY, f"cannot read the session's OpenCode config {path} ({type(e).__name__})") from None
    except ValueError:
        raise Stop(EXIT_NO_KEY, f"the session's OpenCode config {path} is not JSON") from None
    for table in ("provider", "providers"):
        block = cfg.get(table, {}).get(pid) if isinstance(cfg, dict) and isinstance(cfg.get(table), dict) else None
        if isinstance(block, dict):
            return table, block
    raise Stop(EXIT_NO_KEY, f"no provider {pid} in the session's OpenCode config {path}")


def options_name(block):
    return "options" if isinstance(block.get("options"), dict) else "settings"


def session_key(block, pid, path, pin=None):
    """The provider's key from its block (a literal, or {env:NAME} resolved here). Errors never include it."""
    opts = block.get(options_name(block))
    opts = opts if isinstance(opts, dict) else {}
    if pin and opts.get("baseURL") != pin:
        raise Stop(EXIT_NO_KEY, f"the endpoint of {pid} in {path} is not {pin} ({K_BASE}); the key is not used")
    key = opts.get("apiKey")
    m = ENV_RE.fullmatch(key.strip()) if isinstance(key, str) else None
    if m:
        key = os.environ.get(m[1])
    if not isinstance(key, str) or not key.strip() or key.strip().startswith("{"):
        raise Stop(EXIT_NO_KEY, f"no usable apiKey for {pid} in {path}")
    return key.strip()


def variants_of(model_def):
    """The variant names a model's definition lists (a table by name, or a list of {"id": ...}); None when none."""
    v = model_def.get("variants") if isinstance(model_def, dict) else None
    if isinstance(v, dict):
        return sorted(v)
    if isinstance(v, list):
        return sorted(str(x.get("id")) for x in v if isinstance(x, dict) and x.get("id"))
    return None


def without_secrets(value):
    """A copy with every entry dropped whose name could hold a secret."""
    if isinstance(value, dict):
        return {k: without_secrets(v) for k, v in value.items() if not SECRET_RE.search(str(k))}
    if isinstance(value, list):
        return [without_secrets(v) for v in value]
    return value


def private_config(table, pid, block, mid, steps=MAX_STEPS):
    """The reviewer's OpenCode config: the provider with one model and the key as a placeholder, a read-only
    agent with a step limit, no other provider. It holds no secret."""
    opts = options_name(block)
    prov = without_secrets({k: v for k, v in block.items() if k not in ("models", opts)})
    prov[opts] = without_secrets(block.get(opts) if isinstance(block.get(opts), dict) else {})
    prov[opts]["apiKey"] = "{env:%s}" % KEY_ENV
    prov["models"] = {mid: without_secrets(block["models"][mid])}
    # the last matching rule wins: deny all, allow the read-only tools, then deny the rest again by name
    rules = ([{"action": "*", "resource": "*", "effect": "deny"}]
             + [{"action": a, "resource": "*", "effect": "allow"} for a in READ_TOOLS]
             + [{"action": a, "resource": "*", "effect": "deny"} for a in DENIED])
    return {
        "$schema": "https://opencode.ai/config.json",
        table: {pid: prov},
        "model": f"{pid}/{mid}",
        "permissions": rules,
        # at the step limit OpenCode asks for the answer in text, with no further tool call
        "agents": {AGENT: {"description": "Read-only outside reviewer", "mode": "primary", "steps": steps,
                           "permissions": rules}},
        "default_agent": AGENT,
        # no provider but this one (OpenCode otherwise offers its own hosted models)
        "experimental": {"policies": [{"action": "provider.use", "resource": "*", "effect": "deny"},
                                      {"action": "provider.use", "resource": pid, "effect": "allow"}]},
        "websearch": False, "update": "disable", "share": "disabled", "snapshots": False,
    }


def model_slug(model):
    return re.sub(r"[^A-Za-z0-9._-]+", "-", model).strip("-") or "model"


def private_home(home, config_text):
    """OpenCode's home for one run, in a throwaway folder: the child's HOME, XDG variables and TMPDIR, so that
    everything OpenCode writes of its own lands there. It keeps a log and a session database, which can hold
    whatever a provider's error echoed; the folder is removed after the run."""
    for d in (home / "home", home / "config" / "opencode", home / "data", home / "state", home / "cache",
              home / "tmp"):
        d.mkdir(parents=True, exist_ok=True)
    os.chmod(home, 0o700)
    (home / "config" / "opencode" / "opencode.jsonc").write_text(config_text, encoding="utf-8")
    return {"HOME": str(home / "home"), "XDG_CONFIG_HOME": str(home / "config"), "XDG_DATA_HOME": str(home / "data"),
            "XDG_STATE_HOME": str(home / "state"), "XDG_CACHE_HOME": str(home / "cache"), "TMPDIR": str(home / "tmp")}


def child_env(homes, key, ctx):
    """The reviewer's environment: a short list of the caller's variables, the private home, the key and PWD.
    OpenCode 2 takes its folder from PWD, not from the process's working directory."""
    env = {k: os.environ[k] for k in KEEP_ENV if k in os.environ}
    env.update(homes)
    env[KEY_ENV] = key
    env["PWD"] = str(ctx)
    return env


def find_opencode(given):
    path = given or shutil.which("opencode") or str(real_home() / ".opencode" / "bin" / "opencode")
    if not Path(path).is_file():
        raise Stop(EXIT_FAILED, f"the OpenCode command is not there: {path} (pass --opencode)")
    return os.path.abspath(path)


# ---- the run's throwaway folders

def sandbox_folder(label):
    """A fresh temporary folder that OpenCode cannot take for part of a project: outside every git repository
    and below no folder with instructions or an OpenCode config."""
    ctx = Path(tempfile.mkdtemp(prefix=f"{FOLDER_PREFIX}{label}-")).resolve()
    for d in ctx.parents:
        found = [n for n in ABOVE if (d / n).exists()]
        if found:
            shutil.rmtree(ctx, ignore_errors=True)
            raise Stop(EXIT_SANDBOX, f"the temporary folder {ctx} is below {d}, which holds {', '.join(found)}: "
                       "OpenCode would load that; set TMPDIR to a folder outside every repository")
    return ctx


def born(pid):
    """When a process started, as the kernel counts it (Linux /proc); None where that cannot be read."""
    try:
        with open(f"/proc/{pid}/stat", encoding="utf-8", errors="replace") as f:
            return f.read().rsplit(")", 1)[1].split()[19]
    except (OSError, IndexError):
        return None


def runner_gone(rec):
    """Whether the run that wrote this record has ended: its process is gone, or its number belongs to a later one.
    A record of another machine is never called gone."""
    pid = rec.get("pid")
    if rec.get("host") != socket.gethostname() or type(pid) is not int or pid < 1:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return True
    except OSError:
        return False
    return bool(rec.get("born")) and born(pid) not in (None, rec["born"])


def remove_folder(path):
    """Remove one throwaway folder: only a real folder of this user's whose name says it is one."""
    p = Path(str(path))
    try:
        st = os.lstat(p)
    except OSError:
        return
    if p.name.startswith(FOLDER_PREFIX) and os.path.isdir(p) and not p.is_symlink() and st.st_uid == os.getuid():
        shutil.rmtree(p, ignore_errors=True)
        if p.exists():
            # a file that appeared while the tree was walked
            time.sleep(0.2)
            shutil.rmtree(p, ignore_errors=True)


def sweep(review_dir):
    """Remove the folders of runs that were killed before they could (a runner killed outright, a power loss)."""
    try:
        records = sorted((review_dir / "running").glob("*.json"))
    except OSError:
        return
    for record in records:
        try:
            rec = json.loads(record.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(rec, dict) and runner_gone(rec):
            for folder in rec.get("folders") if isinstance(rec.get("folders"), list) else []:
                remove_folder(folder)
            record.unlink(missing_ok=True)


@contextmanager
def throwaway(review_dir, label):
    """The two folders of one OpenCode run, under $TMPDIR: the reviewer's (the copies) and OpenCode's own home.
    Both are removed when the block ends, however it ends; a record in <review_dir>/running/ lets a later run
    remove them when this one is killed outright."""
    sweep(review_dir)
    ctx = sandbox_folder(label)
    home, record = None, review_dir / "running" / f"{ctx.name}.json"
    try:
        home = Path(tempfile.mkdtemp(prefix=f"{FOLDER_PREFIX}home-{label}-")).resolve()
        write_atomic(record, json.dumps({"pid": os.getpid(), "born": born(os.getpid()), "host": socket.gethostname(),
                                         "started": time.strftime(ISO, time.gmtime()),
                                         "folders": [str(ctx), str(home)]}, indent=1) + "\n")
        yield ctx, home
    finally:
        # OpenCode's home first: it is the one that can hold an echoed key
        for folder in (home, ctx):
            if folder is not None:
                remove_folder(folder)
        record.unlink(missing_ok=True)


# ---- what the reviewer may be given

def unfit(parts):
    """Why a path with these parts is never copied, else None."""
    for p in parts[:-1]:
        if any(fnmatch.fnmatch(p, pat) for pat in SKIP_DIRS):
            return f"it is inside a {p} folder"
    name = parts[-1] if parts else ""
    if any(fnmatch.fnmatch(name, pat) for pat in SKIP_DIRS):
        return "a private or tool folder"
    if name in LOADED:
        return "OpenCode would load it as instructions or config"
    if any(fnmatch.fnmatch(name, pat) for pat in PRIVATE):
        return "it is named like a credentials file"
    return None


def copy_tree(src, dst, shown, budget, left_out):
    """Copy a file or a folder for the reviewer. Inside a folder, links and unfit names are left out."""
    if src.is_dir():
        for child in sorted(src.iterdir()):
            why = "a symbolic link" if child.is_symlink() else unfit((child.name,))
            if why:
                left_out.append(f"{shown}/{child.name} ({why})")
                continue
            copy_tree(child, dst / child.name, f"{shown}/{child.name}", budget, left_out)
        return
    if not src.is_file():
        left_out.append(f"{shown} (not a regular file)")
        return
    budget[0] -= src.stat().st_size
    if budget[0] < 0:
        raise Stop(EXIT_USAGE, f"the files given hold more than {budget[1]} MB ({K_MAX_MB}): name fewer or smaller "
                   "files")
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(src, dst)


def place(root, named, tops):
    """(the file to copy, where it goes in the reviewer's folder) for one path named by the caller. The path as
    it is named and the file it leads to must both be inside the project: below its root, or below the target of
    a linked root."""
    text = str(named).strip()
    path = Path(text) if os.path.isabs(text) else root / text
    path = Path(os.path.normpath(path))
    if not path.exists():
        raise Stop(EXIT_USAGE, f"no such file: {text}")
    src = path.resolve()
    as_named, as_real = project_path(path, root), project_path(src, root)
    if as_named is None or as_real is None or src == root:
        raise Stop(EXIT_USAGE, f"{text} is not a file or folder inside the project: the reviewer gets copies of "
                   f"project files only{unlisted_link(src, root)}")
    for inside in (as_named, as_real):
        why = unfit(inside.parts)
        if why:
            raise Stop(EXIT_USAGE, f"{text} is never given to a reviewer: {why}")
    # a file from the review's own folder or the package goes to the top; the rest keep their project paths.
    # The folders are compared as they really are, so a file is the same file through a link and by its real path
    spots = {Path(os.path.realpath(t)) for t in tops}
    top = src.parent in spots or Path(os.path.realpath(path.parent)) in spots
    return src, Path(src.name) if top else as_named


def long_lines(paths, cap=MAX_LINE):
    """(file, line number, length) of each line over the cap in the .md and .txt files given."""
    hits = []
    for p in paths:
        files = [p] if p.is_file() else sorted(q for q in p.rglob("*") if q.is_file() and not q.is_symlink())
        for q in files:
            if q.suffix not in (".md", ".txt"):
                continue
            try:
                lines = q.read_text(encoding="utf-8").splitlines()
            except (UnicodeDecodeError, OSError):
                continue
            hits += [(q, i, len(line)) for i, line in enumerate(lines, 1) if len(line) > cap]
    return hits


# ---- running OpenCode and reading what it did

def tied_to_runner(sig):
    """What a child does before it becomes its command: it asks the kernel to send it this signal when the runner
    dies (Linux prctl). None where that does not exist: the child is then tied by its process group alone."""
    parent = os.getpid()
    try:
        prctl = ctypes.CDLL(None, use_errno=True).prctl
    except (OSError, AttributeError):
        return None

    def tie():
        try:
            prctl(PR_SET_PDEATHSIG, int(sig), 0, 0, 0)
        except Exception:
            return
        if os.getppid() != parent:
            # the runner died before the tie was made
            os.kill(os.getpid(), signal.SIGKILL)

    return tie


def wait_unreaped(p, seconds):
    """Wait until a child has ended, without collecting it: True when it ended in time. Its process group keeps
    its number until the child is collected, so a signal to the group can only reach the child's own. Where the
    system cannot wait so (no os.waitid), the child is collected, and end_group then signals nothing."""
    if not hasattr(os, "waitid"):
        try:
            p.wait(timeout=seconds)
        except subprocess.TimeoutExpired:
            return False
        return True
    end = time.monotonic() + seconds
    while True:
        try:
            if os.waitid(os.P_PID, p.pid, os.WEXITED | os.WNOWAIT | os.WNOHANG) is not None:
                return True
        except ChildProcessError:
            return True
        left = end - time.monotonic()
        if left <= 0:
            return False
        time.sleep(min(0.1, left))


def end_group(p, grace):
    """End a child and everything in its process group: TERM and a wait of grace seconds when there is time,
    then KILL for the whole group, then the child is collected. A child already collected is left alone: its
    group's number may be another's by now."""
    if p.returncode is not None:
        return
    try:
        if grace > 0:
            os.killpg(p.pid, signal.SIGTERM)
            wait_unreaped(p, grace)
        if p.returncode is None:
            os.killpg(p.pid, signal.SIGKILL)
    except OSError:
        pass
    try:
        p.wait(timeout=10)
    except subprocess.TimeoutExpired:
        pass


def run_opencode(cmd, cwd, env, message, out_path, err_path, timeout):
    """(exit code, timed out) of one OpenCode run. Its stdout and stderr go to files; the message, if any, comes
    in on stdin, so a prompt is never on a command line that other users of the machine can read. The child runs
    in its own session and process group, tied to this runner; when it ends, is over its time or the runner is
    stopped, the whole group is killed."""
    with open(out_path, "w") as ev, open(err_path, "w") as er, tempfile.TemporaryFile() as stdin:
        # a file, not a pipe: `opencode run` reads stdin to its end before it starts
        stdin.write((message or "").encode("utf-8"))
        stdin.flush()
        stdin.seek(0)
        try:
            p = subprocess.Popen(cmd, cwd=cwd, env=env, stdin=stdin, stdout=ev, stderr=er, start_new_session=True,
                                 preexec_fn=tied_to_runner(signal.SIGKILL))
        except OSError as e:
            raise Stop(EXIT_FAILED, f"cannot start OpenCode ({cmd[0]}): {e.strerror or type(e).__name__}") from None
        ended = False
        try:
            ended = wait_unreaped(p, timeout)
        finally:
            end_group(p, 0 if ended or STOPPING else TERM_GRACE)
        return (p.returncode if ended and p.returncode is not None else 124), not ended


def key_forms(key):
    """The key as it can appear in a file: as it is, and as JSON text writes it (quotes, backslashes and
    non-ASCII letters escaped). The longest first."""
    forms = {key, json.dumps(key)[1:-1], json.dumps(key, ensure_ascii=False)[1:-1]}
    return sorted((f.encode("utf-8") for f in forms), key=len, reverse=True)


def scrub(path, key):
    """Cut the key from a file OpenCode wrote, should OpenCode or the provider have echoed it."""
    try:
        data = Path(path).read_bytes()
    except OSError:
        return False
    clean = data
    for form in key_forms(key):
        clean = clean.replace(form, REMOVED.encode())
    if clean == data:
        return False
    tmp = Path(f"{path}.tmp{os.getpid()}")
    tmp.write_bytes(clean)
    os.replace(tmp, path)
    return True


def events(path):
    """(text parts of the run's own session, its session id, error types) from the JSON event stream."""
    parts, sid, errs = {}, None, []
    try:
        f = open(path, errors="replace")
    except OSError:
        return [], None, []
    with f:
        for ln in f:
            try:
                e = json.loads(ln)
            except ValueError:
                continue
            if not isinstance(e, dict):
                continue
            sid = sid or e.get("sessionID")
            if e.get("type") == "error":
                er = e.get("error") if isinstance(e.get("error"), dict) else {}
                errs.append({"type": er.get("type"), "status": er.get("status")})
            part = e.get("part") if isinstance(e.get("part"), dict) else {}
            if e.get("sessionID") == sid and part.get("type") == "text" and part.get("text"):
                # a part sent again replaces its earlier text
                parts[part.get("id") or len(parts)] = part["text"]
    return list(parts.values()), sid, errs


def export(opencode, sid, env, cwd):
    """OpenCode's stored record of one session: messages with their tokens, cost and content; None on failure.
    It runs as the reviewer does (its own group, tied to the runner), in an environment without the key."""
    # a file, not a pipe: OpenCode exits before a piped stdout drains and cuts it at 64 KiB; it sits in the run's
    # own home folder and goes with it
    name = Path(env["XDG_STATE_HOME"]).parent / f"export-{sid}.json"
    try:
        run_opencode([opencode, "session", "export", "--standalone", sid], cwd, env, None, name, os.devnull, 120)
        out = name.read_text(encoding="utf-8", errors="replace")
    except (Stop, OSError):
        return None
    try:
        d = json.loads(out[out.index("{"):])
    except ValueError:
        return None
    return d if isinstance(d, dict) else None


def add_stats(opencode, sid, acc, env, cwd):
    """Add one session's steps, tokens, cost and tool calls to acc, then those of its child sessions. The event
    stream carries neither tokens nor cost for every step, so this reads the export."""
    d = export(opencode, sid, env, cwd)
    if d is None:
        acc["sessions"].append({"session": sid, "export": "failed"})
        return
    steps0, cost0, kids = acc["steps"], acc["cost"], []
    for m in d.get("messages") or []:
        if not isinstance(m, dict):
            continue
        for it in m.get("content") or []:
            if not isinstance(it, dict) or it.get("type") != "tool":
                continue
            st = it.get("state") if isinstance(it.get("state"), dict) else {}
            err, md, given = (st.get(k) if isinstance(st.get(k), dict) else {} for k in ("error", "metadata", "input"))
            acc["tools"][str(it.get("name"))] += 1
            if err.get("type") == "permission.rejected":
                acc["denied"] += 1
            if it.get("name") in ("task", "subagent"):
                kids.append(md.get("sessionId") or md.get("sessionID"))
            # every path a tool was pointed at, so the run can be checked for reads outside its folder
            acc["paths"] += [v for k, v in given.items() if k in ("filePath", "path") and isinstance(v, str)]
        if m.get("type") != "assistant":
            continue
        acc["steps"] += 1
        acc["cost"] += float(m.get("cost") or 0)
        t = m.get("tokens") if isinstance(m.get("tokens"), dict) else {}
        c = t.get("cache") if isinstance(t.get("cache"), dict) else {}
        for k, v in zip(TOKENS, (t.get("input"), t.get("output"), t.get("reasoning"), c.get("read"), c.get("write"))):
            acc["tok"][k] += int(v or 0)
    acc["sessions"].append({"session": sid, "steps": acc["steps"] - steps0, "cost": round(acc["cost"] - cost0, 6)})
    for k in filter(None, kids):
        add_stats(opencode, k, acc, env, cwd)


def outside(paths, ctx):
    """The paths a tool was pointed at that are not inside the reviewer's folder."""
    out = set()
    for p in paths:
        full = Path(os.path.normpath(p if os.path.isabs(p) else os.path.join(ctx, p)))
        if full != ctx and ctx not in full.parents:
            out.add(p)
    return sorted(out)


# ---- the key gate

def fingerprint(config_text, opencode):
    """What a passed gate holds for: the private config and the OpenCode binary."""
    try:
        st = os.stat(opencode)
        binary = f"{opencode}:{st.st_size}:{st.st_mtime_ns}"
    except OSError:
        binary = opencode
    return hashlib.sha256(f"{config_text}\n{binary}".encode()).hexdigest()


def gate_passed(review_dir, model, mark):
    try:
        gates = json.loads((review_dir / "gates.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    g = gates.get(model) if isinstance(gates, dict) else None
    return isinstance(g, dict) and g.get("fingerprint") == mark


def record_gate(review_dir, model, mark, status):
    # one writer at a time: reviewers of a pool pass their gates at once
    review_dir.mkdir(parents=True, exist_ok=True)
    with open(review_dir / ".lock", "a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            gates = json.loads((review_dir / "gates.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            gates = {}
        gates = gates if isinstance(gates, dict) else {}
        gates[model] = {"passed": time.strftime(ISO, time.gmtime()), "fingerprint": mark, "status": status}
        write_atomic(review_dir / "gates.json", json.dumps(gates, indent=1, sort_keys=True) + "\n")


def key_gate(opencode, model, config_text, review_dir, mark):
    """One call with an invalid key: it must fail as a refused key, else another credential may be in use."""
    store = review_dir / "gate" / model_slug(model)
    store.mkdir(parents=True, exist_ok=True)
    with throwaway(review_dir, "gate") as (ctx, home):
        cmd = [opencode, "run", "--standalone", "-m", model, "--agent", AGENT, "--format", "json",
               "--title", "key gate"]
        rc, timed_out = run_opencode(cmd, ctx, child_env(private_home(home, config_text), GATE_KEY, ctx),
                                     GATE_PROMPT, store / "events.jsonl", store / "stderr.log", GATE_TIMEOUT)
    texts, _sid, errs = events(store / "events.jsonl")
    refused = [e for e in errs if e.get("status") in REFUSED_STATUSES or e.get("type") == "provider.auth"]
    if texts:
        raise Stop(EXIT_GATE, f"the key gate failed for {model}: a call with an invalid key got an answer, so "
                   "another credential is in use; no review was run")
    if not refused:
        seen = ", ".join(f"{e.get('type')} {e.get('status')}" for e in errs) or "none"
        how = "timed out" if timed_out else f"ended with exit {rc}"
        raise Stop(EXIT_GATE, f"the key gate failed for {model}: a call with an invalid key {how} without a refused "
                   f"key (errors: {seen}); no review was run (see {store})")
    record_gate(review_dir, model, mark, refused[0].get("status"))
    return refused[0].get("status")


# ---- the reviewer, the input and the run

def split_model(text):
    """(provider/model, variant or None) of provider/model or provider/model#variant."""
    model, _, variant = str(text or "").strip().partition("#")
    pid, _, mid = model.partition("/")
    if not pid or not mid:
        raise Stop(EXIT_USAGE, f"not a model: {text!r} (provider/model, as the session's OpenCode config names it)")
    return model, variant or None


def pool_of(settings):
    pool = settings.get(K_POOL)
    if pool is None:
        return {}
    ok = isinstance(pool, dict) and all(
        ID_RE.fullmatch(str(k)) and isinstance(v, dict) and isinstance(v.get("model"), str) for k, v in pool.items())
    if not ok:
        raise Stop(EXIT_USAGE, f'"{K_POOL}" is not {{"<name>": {{"model": "provider/model", "variant": "..."}}}}')
    return pool


def reviewer(a, settings):
    """(provider/model, variant or None, tag or None) of this run's reviewer."""
    tag = a.tag
    if a.reviewer:
        if a.model or a.variant:
            raise Stop(EXIT_USAGE, "--reviewer takes its model and variant from the pool: drop --model and --variant")
        member = pool_of(settings).get(a.reviewer)
        if member is None:
            known = ", ".join(sorted(pool_of(settings))) or "none"
            raise Stop(EXIT_USAGE, f'no reviewer {a.reviewer} in "{K_POOL}" (known: {known})')
        model, variant = split_model(member["model"])
        variant = member.get("variant") or variant
        tag = tag or a.reviewer
    elif a.model:
        model, variant = split_model(a.model)
        variant = a.variant or variant
    else:
        named = settings.get(K_MODEL)
        if not isinstance(named, str) or not named.strip():
            raise Stop(EXIT_USAGE, f'no reviewer: pass --model provider/model or --reviewer NAME, or set "{K_MODEL}"')
        model, variant = split_model(named)
        variant = a.variant or variant or settings.get(K_VARIANT)
    if tag is not None and not ID_RE.fullmatch(tag):
        raise Stop(EXIT_USAGE, f"not a tag: {tag!r} (letters, digits, . _ -)")
    if variant is not None and not (isinstance(variant, str) and ID_RE.fullmatch(variant)):
        raise Stop(EXIT_USAGE, f"not a variant: {variant!r}")
    return model, variant, tag


def the_day(text, settings):
    if text == "today":
        return owner_today(settings).isoformat()
    try:
        return date.fromisoformat(text).isoformat()
    except ValueError:
        raise Stop(EXIT_USAGE, f"--day takes today or YYYY-MM-DD, not {text!r}") from None


def package_input(a, root, settings, tag):
    """(id, prompt file, files, folders whose files go to the top, what READY holds) of a review package, or exit
    3: not ready."""
    def tagged(base):
        return f"{base}-{tag}" if tag else base

    named = Path(a.package) if os.path.isabs(a.package) else root / a.package
    if not named.is_dir():
        raise Stop(EXIT_NOT_READY, f"inputs not ready: no package folder {a.package}", tagged(a.id or named.name))
    folder = named.resolve()
    label = tagged(a.id or folder.name)
    if project_path(folder, root) is None:
        raise Stop(EXIT_USAGE, f"the package folder {a.package} is not inside the project"
                   f"{unlisted_link(folder, root)}")
    shown = rel(folder, root)
    for name in ("prompt.md", "STATE.md", "files.txt"):
        f = folder / name
        if not f.is_file() or not f.read_text(encoding="utf-8", errors="replace").strip():
            raise Stop(EXIT_NOT_READY, f"inputs not ready: {shown}/{name} is missing or empty", label)
    try:
        ready = (folder / "READY").read_text(encoding="utf-8", errors="replace").strip() or None
    except OSError:
        ready = None
    if a.day:
        day = the_day(a.day, settings)
        if ready != day:
            raise Stop(EXIT_NOT_READY, f"inputs not ready for {day}: {shown}/READY "
                       f"{'says ' + ready[:40] if ready else 'is missing'}", label)
    listed = [ln.strip() for ln in (folder / "files.txt").read_text(encoding="utf-8", errors="replace").splitlines()]
    listed = [ln for ln in listed if ln and not ln.startswith("#")]
    if not listed:
        raise Stop(EXIT_NOT_READY, f"inputs not ready: {shown}/files.txt names no file", label)
    for ln in listed:
        if not (Path(ln) if os.path.isabs(ln) else root / ln).exists():
            raise Stop(EXIT_NOT_READY, f"inputs not ready: {shown}/files.txt names {ln}, which is not there", label)
    return label, folder / "prompt.md", [str(folder / "STATE.md"), *listed], [folder], ready


def review_input(a, root, settings, tag):
    """(id, prompt file, the files named, folders whose files go to the top, the package folder and what its READY
    holds, both None without a package) of this review."""
    if a.package:
        if a.id_or_prompt:
            raise Stop(EXIT_USAGE, "--package takes no ID, PROMPT_FILE or FILE arguments (the ID: --id)")
        if a.id is not None and not ID_RE.fullmatch(a.id):
            raise Stop(EXIT_USAGE, f"not a review ID: {a.id!r} (letters, digits, . _ -)")
        label, prompt, files, tops, ready = package_input(a, root, settings, tag)
        package = tops[0]
    else:
        if len(a.id_or_prompt) < 2:
            raise Stop(EXIT_USAGE, "usage: kaggle_review.py ID PROMPT_FILE [FILE ...], or --package FOLDER, or --gate")
        if a.id or a.day:
            raise Stop(EXIT_USAGE, "--id and --day belong to --package")
        label, prompt_name, *files = a.id_or_prompt
        label = f"{label}-{tag}" if tag else label
        prompt = Path(prompt_name) if os.path.isabs(prompt_name) else root / prompt_name
        if not prompt.is_file():
            raise Stop(EXIT_USAGE, f"no such prompt file: {prompt_name}")
        tops, package, ready = [], None, None
    if not ID_RE.fullmatch(label):
        raise Stop(EXIT_USAGE, f"not a review ID: {label!r} (letters, digits, . _ -)")
    return label, prompt, files, tops, package, ready


def read_record(path):
    """A run.json as a table; an empty one when it is missing or unreadable."""
    try:
        rec = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return rec if isinstance(rec, dict) else {}


def has_text(path):
    try:
        return bool(Path(path).read_text(encoding="utf-8", errors="replace").strip())
    except OSError:
        return False


def stands(out, record, label, shown, ready):
    """Whether the review that is there is the review of this input: the file has text, and its own record says
    that a run for this ID wrote this file and ended well, for the package as READY now names it."""
    return (has_text(out) and record.get("exit") == 0 and record.get("id") == label and record.get("review") == shown
            and record.get("ready") == ready)


def unused(path):
    """The path, or the first of path-2, path-3, ... that is not taken."""
    n, free = 1, path
    while free.exists():
        n += 1
        free = path.with_name(f"{path.stem}-{n}{path.suffix}")
    return free


def keep_earlier(out, store, record, root):
    """Move the review that is there, and its record, to dated names beside it (review-<day>.md, run-<day>.json):
    the day its package was for, else the day it was written. Nothing is overwritten. Returns the review's new
    place, or None when there was no review with text."""
    (store / "review.partial.md").unlink(missing_ok=True)
    if not has_text(out):
        out.unlink(missing_ok=True)
        return None
    day = next((d for d in (record.get("ready"), record.get("day"), str(record.get("ts") or "")[:10])
                if isinstance(d, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", d)), None)
    day = day or time.strftime("%Y-%m-%d", time.localtime(out.stat().st_mtime))
    kept = unused(out.with_name(f"{out.stem}-{day}{out.suffix}"))
    os.replace(out, kept)
    if record:
        write_atomic(unused(store / f"run-{day}.json"),
                     json.dumps({**record, "review": rel(kept, root)}, indent=1) + "\n")
        (store / "run.json").unlink(missing_ok=True)
    return kept


def ledger_line(pack, day, category, usd, what):
    """Append one cost to <pack>/.memory/spend.jsonl, in the status page's form."""
    path = pack / ".memory" / "spend.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps({"day": day, "category": category, "usd": round(usd, 4), "what": what}) + "\n"
    try:
        with open(path, "rb") as f:
            f.seek(-1, os.SEEK_END)
            if f.read(1) != b"\n":
                line = "\n" + line
    except OSError:
        pass  # no ledger yet, or an empty one
    # one write in append mode: a line from another writer at the same time stays whole
    fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o644)
    try:
        os.write(fd, line.encode())
    finally:
        os.close(fd)


def take(lock_path):
    """This review's lock, without waiting; None while another run holds it."""
    fd = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        os.close(fd)
        return None
    return fd


def run_pool(a, argv, settings):
    """Every member of the pool at once, each as its own run of this tool; the worst exit code wins, and a
    member killed by a signal counts as a failed run."""
    pool = pool_of(settings)
    if not pool:
        raise Stop(EXIT_USAGE, f'--pool needs "{K_POOL}" in the pack\'s settings')
    if a.out or a.reviewer or a.model or a.variant or a.tag:
        raise Stop(EXIT_USAGE, "--pool takes no --out, --reviewer, --model, --variant or --tag")
    rest = [x for x in argv if x != "--pool"]
    # a member is told to stop when this pool dies, and then ends its own reviewer
    procs = [(name, subprocess.Popen([sys.executable, str(Path(__file__).resolve()), *rest, "--reviewer", name],
                                     preexec_fn=tied_to_runner(signal.SIGTERM))) for name in sorted(pool)]
    try:
        codes = {name: p.wait() for name, p in procs}
    finally:
        # a stopped pool stops its members; each has a moment to end its reviewer and remove its folders
        running = [p for _name, p in procs if p.poll() is None]
        for p in running:
            p.terminate()
        for p in running:
            try:
                p.wait(timeout=2.5)
            except subprocess.TimeoutExpired:
                p.kill()
    print(f"{NAME}: pool done, exit codes: " + ", ".join(
        f"{n} {c}" + (f" (killed by signal {-c})" if c < 0 else "") for n, c in codes.items()))
    return max(c if c >= 0 else EXIT_FAILED for c in codes.values())


def parse(argv):
    p = argparse.ArgumentParser(prog="kaggle_review.py", description="One sandboxed outside review through OpenCode: "
                                "a reviewer reads copies of the files given, read-only, and its text is saved.")
    p.add_argument("id_or_prompt", nargs="*", metavar="ID PROMPT_FILE [FILE ...]",
                   help="the review's ID, the file with the questions, and the files or folders to review")
    p.add_argument("--package", metavar="FOLDER", help="review a package folder: prompt.md, STATE.md, files.txt")
    p.add_argument("--id", help="with --package: the review's ID (default: the folder's name)")
    p.add_argument("--day", help="with --package: READY must hold this day (today or YYYY-MM-DD)")
    p.add_argument("--model", metavar="PROVIDER/MODEL", help=f'the reviewer (default: "{K_MODEL}")')
    p.add_argument("--variant", help="the model's variant")
    p.add_argument("--reviewer", metavar="NAME", help=f'a member of "{K_POOL}"')
    p.add_argument("--pool", action="store_true", help=f'run every member of "{K_POOL}" at once')
    p.add_argument("--tag", help="added to the ID as ID-TAG")
    p.add_argument("--out", metavar="FILE", help="the review file (default: __out/review/<ID>/review.md)")
    p.add_argument("--force", action="store_true",
                   help="run again though the review is there; the earlier one keeps a dated name")
    p.add_argument("--allow-long", action="store_true", help="send .md and .txt lines over 2,000 characters")
    p.add_argument("--timeout", type=int, metavar="SECONDS", help=f"the reviewer's time limit (default {TIMEOUT})")
    p.add_argument("--gate", action="store_true", help="run only the key gate for the model")
    p.add_argument("--root", metavar="DIR", help="the project root (default: found from the working directory)")
    p.add_argument("--opencode", metavar="PATH", help="the OpenCode command")
    p.add_argument("--session-config", metavar="FILE", help="the session's OpenCode config")
    return p.parse_args(argv)


def run(a, argv):
    root = find_root(a.root)
    if root is None:
        raise Stop(EXIT_USAGE, "no project here: run it from the project root, or pass --root")
    pack = pack_of(root)
    settings = Settings(pack)
    if a.pool:
        return run_pool(a, argv, settings)
    LISTED[:] = listed_roots(pack)
    LINKED.clear()
    LINKED.update(linked_roots(root, pack))
    model, variant, tag = reviewer(a, settings)
    pid, _, mid = model.partition("/")
    timeout = a.timeout if a.timeout is not None else settings.get(K_TIMEOUT, TIMEOUT)
    if isinstance(timeout, bool) or not isinstance(timeout, int) or timeout < 1:
        raise Stop(EXIT_USAGE, f"the time limit is not a number of seconds: {timeout!r}")
    steps = settings.get(K_MAX_STEPS, MAX_STEPS)
    if isinstance(steps, bool) or not isinstance(steps, int) or steps < 1:
        raise Stop(EXIT_USAGE, f'"{K_MAX_STEPS}" is not a number of steps: {steps!r}')
    review_dir = root / "__data" / "kaggle" / "review"

    job = None
    if not a.gate:
        label, prompt_file, files, tops, package, ready = review_input(a, root, settings, tag)
        out = (Path(a.out) if os.path.isabs(a.out) else root / a.out) if a.out else \
            root / "__out" / "review" / label / "review.md"
        record = read_record(out.parent / "run.json")
        if stands(out, record, label, rel(out, root), ready) and not a.force:
            what = f"it reviewed the package of {ready}; " if ready else ""
            print(f"{NAME} {label}: {rel(out, root)} exists already; not run again ({what}--force runs it again)")
            return 0
        prompt = prompt_file.read_text(encoding="utf-8", errors="replace")
        if not prompt.strip():
            raise Stop(EXIT_USAGE, f"the prompt file {rel(prompt_file, root)} is empty")
        tops = [*tops, out.parent]
        # a file named twice is copied once
        placed = list(dict.fromkeys(place(root, f, tops) for f in files))
        dup = [str(d) for d, n in Counter(dst for _src, dst in placed).items() if n > 1]
        if dup:
            raise Stop(EXIT_USAGE, f"two files would land at {dup[0]} in the reviewer's folder: name one of them")
        if not a.allow_long:
            long = long_lines([prompt_file, *(src for src, _dst in placed)])
            if long:
                for f, i, n in long[:20]:
                    print(f"{NAME}: {rel(f, root)}:{i} has {n} characters (over {MAX_LINE})", file=sys.stderr)
                raise Stop(EXIT_USAGE, f"{len(long)} line(s) over {MAX_LINE} characters: wrap them at about 120, "
                           "or pass --allow-long")
        job = {"label": label, "prompt": prompt, "placed": placed, "out": out, "record": record, "ready": ready,
               "package": rel(package, root) if package else None, "model": model, "variant": variant,
               "timeout": timeout, "steps": steps}

    # the provider, the model and the key, from the session's own config
    cfg_path = session_config_path(a.session_config or settings.get(K_CONFIG))
    table, block = read_provider(cfg_path, pid)
    models = block.get("models") if isinstance(block.get("models"), dict) else {}
    if mid not in models:
        raise Stop(EXIT_USAGE, f"unknown model {model}: {cfg_path} lists for {pid}: "
                   + (", ".join(sorted(models)) or "no model"))
    known = variants_of(models[mid])
    if variant and known is not None and variant not in known:
        raise Stop(EXIT_USAGE, f"{model} has no variant {variant}; known: {', '.join(known) or 'none'}")
    key = session_key(block, pid, cfg_path, settings.get(K_BASE))
    config_text = json.dumps(private_config(table, pid, block, mid, steps), indent=1) + "\n"
    if any(form in config_text.encode("utf-8") for form in key_forms(key)):
        raise Stop(EXIT_NO_KEY, f"the block of {pid} in {cfg_path} holds the key under another name; nothing "
                   "written, nothing run")
    opencode = find_opencode(a.opencode)
    mark = fingerprint(config_text, opencode)
    if a.gate or not gate_passed(review_dir, model, mark):
        status = key_gate(opencode, model, config_text, review_dir, mark)
        if a.gate:
            print(f"{NAME}: key gate passed for {model}: a call with an invalid key was refused ({status})")
            return 0

    out = job["out"]
    out.parent.mkdir(parents=True, exist_ok=True)
    lock = take(out.parent / ".lock")
    if lock is None:
        print(f"{NAME} {job['label']}: FAILED (another run of this review holds {rel(out.parent / '.lock', root)}); "
              "nothing run")
        return EXIT_FAILED
    try:
        return review(root, pack, settings, review_dir, job, opencode, config_text, key)
    finally:
        os.close(lock)


def review(root, pack, settings, review_dir, job, opencode, config_text, key):
    """Copy the files, run the reviewer, read what it did, write the review, the record and the ledger line."""
    label, out, model, variant, timeout = job["label"], job["out"], job["model"], job["variant"], job["timeout"]
    store = out.parent
    max_mb = settings.get(K_MAX_MB, MAX_MB)
    if isinstance(max_mb, bool) or not isinstance(max_mb, (int, float)) or max_mb <= 0:
        raise Stop(EXIT_USAGE, f'"{K_MAX_MB}" is not a number of MB: {max_mb!r}')
    # the review that is there is of another day's package, or is run again on purpose: it keeps a dated name
    kept = keep_earlier(out, store, job["record"], root)
    if kept:
        print(f"{NAME} {label}: the earlier review is kept as {rel(kept, root)}", file=sys.stderr)
    left_out, acc = [], {"sessions": [], "steps": 0, "tok": dict.fromkeys(TOKENS, 0), "cost": 0.0, "paths": [],
                         "tools": Counter(), "denied": 0}
    with throwaway(review_dir, label) as (ctx, home):
        try:
            budget = [int(max_mb * 1024 * 1024), max_mb]
            for src, dst in job["placed"]:
                copy_tree(src, ctx / dst, str(dst), budget, left_out)
            write_atomic(store / "copies.txt", "".join(
                f"{p.relative_to(ctx)}\n" for p in sorted(ctx.rglob("*")) if p.is_file()))
            homes = private_home(home, config_text)
            # --thinking: reasoning parts come in the stream too; only text parts go into the review
            cmd = [opencode, "run", "--standalone", "--thinking", "-m", f"{model}#{variant}" if variant else model,
                   "--agent", AGENT, "--format", "json", "--title", f"review {label}"]
            t0 = time.time()
            rc, timed_out = run_opencode(cmd, ctx, child_env(homes, key, ctx), job["prompt"],
                                         store / "events.jsonl", store / "stderr.log", timeout)
            wall = time.time() - t0
            texts, sid, errs = events(store / "events.jsonl")
            if sid:
                # reading the session back needs no key
                add_stats(opencode, sid, acc, child_env(homes, GATE_KEY, ctx), ctx)
            strayed = outside(acc["paths"], ctx)
        finally:
            # on every way out: the key is cut from what OpenCode printed, then the two folders go
            cut = [f.name for f in (store / "events.jsonl", store / "stderr.log") if scrub(f, key)]
    if cut:
        print(f"{NAME} {label}: the key was echoed into {', '.join(cut)} and cut from there", file=sys.stderr)
    for form in key_forms(key):
        texts = [t.replace(form.decode("utf-8"), REMOVED) for t in texts]
    counted = bool(acc["sessions"]) and "export" not in acc["sessions"][0]
    tok, cost = acc["tok"], acc["cost"]
    text = "\n\n".join(texts).strip()
    why = (f"timed out after {timeout} s" if timed_out else f"OpenCode exit {rc}" if rc else
           "no review text" if not text else None)
    target = out if why is None else store / "review.partial.md"
    if text:
        write_atomic(target, text + "\n")
    code = 0 if why is None else EXIT_FAILED
    capped = counted and acc["steps"] >= job["steps"]
    day = owner_today(settings).isoformat()
    write_atomic(store / "run.json", json.dumps({
        "ts": time.strftime(ISO, time.gmtime()), "day": day, "id": label, "package": job["package"],
        "ready": job["ready"], "session": sid, "model": model, "variant": variant,
        "exit": code, "opencode_exit": rc, "timed_out": timed_out, "seconds": round(wall, 1),
        "steps": acc["steps"] if counted else None, "step_limit": job["steps"], "tokens": tok,
        "cost_usd": round(cost, 6),
        "cost_source": "opencode estimate" if counted else "none: the session export failed",
        "tools": dict(acc["tools"]), "denied_calls": acc["denied"], "errors": errs, "sessions": acc["sessions"],
        "outside_paths": strayed, "left_out": left_out, "review": rel(target, root) if text else None,
    }, indent=1) + "\n")
    note = "opencode estimate" if counted else "cost unknown: the session export failed"
    ledger_line(pack, day, str(settings.get(K_CATEGORY) or CATEGORY), cost,
                f"opencode review {label} ({model}{', ' + variant if variant else ''}; {note})")
    if left_out:
        print(f"{NAME} {label}: left out of the copies: {'; '.join(left_out[:10])}"
              + (f"; and {len(left_out) - 10} more (run.json)" if len(left_out) > 10 else ""), file=sys.stderr)
    if strayed:
        print(f"{NAME} {label}: WARNING: the reviewer pointed a tool at {len(strayed)} path(s) outside its folder; "
              f"see outside_paths in {rel(store / 'run.json', root)}", file=sys.stderr)
    if capped:
        print(f"{NAME} {label}: WARNING: the reviewer reached its step limit ({job['steps']}, {K_MAX_STEPS}): "
              "the review may be cut short", file=sys.stderr)
    if errs:
        seen = ", ".join(f"{e.get('type')} {e.get('status')}" for e in errs[:5])
        print(f"{NAME} {label}: OpenCode reported errors: {seen}", file=sys.stderr)
    spent = (f"{tok['input'] + tok['cache_read'] + tok['cache_write']} tokens in ({tok['cache_read']} cache read, "
             f"{tok['cache_write']} cache write), {tok['output']} out + {tok['reasoning']} reasoning, "
             f"${cost:.4f} ({note}), {wall:.0f} s")
    if why is None:
        print(f"{NAME} {label}: review written, {len(texts)} text part(s), {spent} -> {rel(out, root)}")
    else:
        part = f"; partial text in {rel(target, root)}" if text else ""
        print(f"{NAME} {label}: FAILED ({why}), {spent}{part}; see {rel(store, root)}/")
    return code


def stop(signum, _frame):
    """SIGTERM, SIGHUP or SIGINT: the run ends now. Its reviewer is killed at once and its folders are removed on
    the way out; a second signal does not cut that short."""
    if STOPPING:
        return
    STOPPING.append(signum)
    for sig in STOP_SIGNALS:
        signal.signal(sig, signal.SIG_IGN)
    raise SystemExit(128 + signum)


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    a = parse(argv)
    for sig in STOP_SIGNALS:
        signal.signal(sig, stop)
    try:
        return run(a, argv)
    except Stop as e:
        print(f"{NAME} {e.label}: {e}" if e.label else f"{NAME}: {e}")
        return e.code
    except SystemExit:
        if STOPPING:
            print(f"{NAME}: stopped by signal {STOPPING[0]}: the reviewer was ended and its folders removed",
                  flush=True)
        raise


if __name__ == "__main__":
    sys.exit(main())
