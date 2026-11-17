# Copyright 2026 Mikhail Yurasov <me@yurasov.me>
# SPDX-License-Identifier: Apache-2.0

"""Self-compaction for OpenCode 2 sessions (stdlib only).

OpenCode 2 gives the model no compaction tool, its ``/compact`` is a TUI action, and its automatic compaction fires
near the context window's limit, wherever the turn happens to be, with nothing saved first. This tool lets a
top-level OpenCode 2 session compact itself earlier, at a moment it picks, after it has saved its state. The
procedure around it is the ``opencode2-selfcompact`` skill (``solaris/skills/opencode2-selfcompact.skill.md``).

``check`` measures the calling session's context (the prompt of its newest finished model step: fresh input plus
cache reads and writes) against the limit and says whether a compaction is due. ``request`` queues the compaction
through the OpenCode 2 API (``POST /api/session/<id>/compact``) once the gates pass: the checkpoint was rewritten in
the last 20 minutes and holds at least 400 bytes of state, it names every running subagent session, and no git
command holds ``index.lock`` (in the project's repositories with ``--dir``, else in the Solaris checkout). Queue
delivery (the default) compacts right after the turn's final message; ``--now`` compacts at the next step boundary
and the turn goes on, and it also refuses running subagents, staged changes and an unfinished merge, rebase,
cherry-pick or revert. ``--force`` (the owner asked for a compaction) skips the enabled, limit and interval gates,
never the others; ``--dry-run`` runs every gate and queues nothing. ``set-limit`` overrides the limit for the
calling session only; ``set-limit off`` turns self-compaction off for that session alone (an autocode agent whose
compaction autocode runs: the autocode plugin's start step), and ``--clear`` drops either override.
``interactions add`` runs the same measurement (within a few seconds, skipped while self-compaction is off) and
prints one line when a compaction is due, so a session learns it without spending a call on it.

The session is ``$OPENCODE_SESSION_ID``, which OpenCode 2 sets in every tool shell; it must be a top-level session
that is running right now. The API is the background service recorded in
``${XDG_STATE_HOME:-~/.local/state}/opencode/service.json`` (its password goes only to that local address, and only
while the recorded process lives), or ``--server-url``, a local address, with ``OPENCODE_SERVER_PASSWORD``. Claude
Code, Cursor, OpenCode 1.x and subagent sessions get "not applicable" and nothing changes. A program started from a
running session's tool shell (another agent CLI, a script) inherits the variable and would act for that session,
so start such workers with ``env -u OPENCODE_SESSION_ID``; Claude Code is recognized by ``CLAUDECODE``.

The checkpoint is the project's ``<pack>/.memory/context.md`` with ``--dir`` (its existing pre-compaction save
point; when it has a ``## Session Context`` heading only the text below it counts), else
``.memory/selfcompact/<session id>.md`` in the framework, which needs ``## Now`` and ``## Next Actions`` headings.
Settings, each optional, come from the project's ``<pack>/.memory/config.json``, then its ``<pack>/defaults.json``,
then the framework's ``.memory/config.json``: ``selfcompact.enabled`` (default true), ``selfcompact.limit``
(context tokens, an integer or k/m shorthand such as ``500k``; default 500k, floor 100k) and
``selfcompact.min_interval_minutes`` (default 30). A bad value falls back to its default and is reported as
``config_error``. ``set-limit`` keeps its override in ``${XDG_CACHE_HOME:-~/.cache}/solaris/selfcompact/``.
Paths and commands are printed relative to the current folder (the Solaris root) when they lie inside it.

Run::

    uv run -m solaris.tools.session_compact check [--dir <project>] [--brief]
    uv run -m solaris.tools.session_compact request [--dir <project>] [--now] [--force] [--dry-run]
    uv run -m solaris.tools.session_compact set-limit 333k|off|--clear [--dir <project>]

Exit codes: 0 done (``check`` always, unless ``--dir`` holds no ai-pack; ``request`` also when a compaction is
already queued, ``pending``); 2 bad arguments or no ai-pack at ``--dir``; 3 not applicable (see above) or the API
failed; 4 a gate refused (``refused`` lists each reason); 5 not due (under the limit, inside the minimum interval,
disabled, or compacted since the newest measured step).
"""

from __future__ import annotations

import argparse
import base64
import http.client
import ipaddress
import json
import math
import os
import re
import shlex
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path

from solaris.tools import pack as P
from solaris.tools import session_clock as C
from solaris.tools.fileio import write_text_atomic

REPO_ROOT = Path(__file__).resolve().parents[2]
SKILL = "solaris/skills/opencode2-selfcompact.skill.md"
SESSION_ENV = "OPENCODE_SESSION_ID"
DEFAULT_LIMIT = 500_000
MIN_LIMIT = 100_000
DEFAULT_INTERVAL = 30
CHECKPOINT_MAX_AGE_S = 20 * 60
CHECKPOINT_MIN_BYTES = 400
SESSION_HEADINGS = ("Now", "Next Actions")
KEEP_DAYS = 30
HINT_TIMEOUT, HINT_BUDGET = 2.0, 6.0  # seconds per call and in all, for the interactions add check
EXIT_OK, EXIT_USAGE, EXIT_NA, EXIT_REFUSED, EXIT_NOT_DUE = 0, 2, 3, 4, 5
GIT_MARKERS = (("index.lock", "a git command holds index.lock"), ("MERGE_HEAD", "a merge is in progress"),
               ("rebase-merge", "a rebase is in progress"), ("rebase-apply", "a rebase or am is in progress"),
               ("CHERRY_PICK_HEAD", "a cherry-pick is in progress"), ("REVERT_HEAD", "a revert is in progress"))


class ApiError(Exception):
    pass


def parse_tokens(value) -> int:
    """A token count from an integer or k/m shorthand (``500k``, ``1m``, ``1.5m``); ValueError otherwise."""
    if isinstance(value, bool):
        raise ValueError(f"not a token count: {value!r}")
    if isinstance(value, (int, float)):
        n = float(value)
    else:
        text = str(value).strip().lower().replace("_", "").replace(",", "")
        m = re.fullmatch(r"(\d+(?:\.\d+)?)\s*([km]?)", text)
        if not m:
            raise ValueError(f"not a token count: {value!r}")
        n = float(m.group(1)) * {"": 1, "k": 1_000, "m": 1_000_000}[m.group(2)]
    if not math.isfinite(n) or n < 1:
        raise ValueError(f"not a positive token count: {value!r}")
    return int(n)


def _limit(value) -> int:
    n = parse_tokens(value)
    if n < MIN_LIMIT:
        raise ValueError(f"{value!r} is below the {MIN_LIMIT // 1000}k floor")
    return n


def _flag(value) -> bool:
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in ("true", "on", "yes", "1"):
        return True
    if text in ("false", "off", "no", "0"):
        return False
    raise ValueError(f"not true or false: {value!r}")


def _minutes(value) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        raise ValueError(f"not a number of minutes: {value!r}")
    n = float(value)
    if not math.isfinite(n) or n < 0:
        raise ValueError(f"not a number of minutes: {value!r}")
    return n


SETTINGS = {"enabled": _flag, "limit": _limit, "min_interval_minutes": _minutes}


def _rel(path) -> str:
    """``path`` relative to the current folder when it lies inside it, else absolute."""
    p = Path(path).resolve()
    try:
        return str(p.relative_to(Path.cwd().resolve()))
    except (ValueError, OSError):
        return str(p)


def _json_object(path: Path) -> "tuple[dict, str | None]":
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}, None
    except (OSError, ValueError) as e:
        return {}, f"cannot read {_rel(path)}: {e}"
    return (data, None) if isinstance(data, dict) else ({}, f"{_rel(path)} is not a JSON object")


def load_config(pack: "Path | None" = None) -> dict:
    """The selfcompact.* settings with defaults; a project's files win over the framework's, key by key."""
    cfg = {"enabled": True, "limit": DEFAULT_LIMIT, "min_interval_minutes": DEFAULT_INTERVAL,
           "limit_source": "default", "error": None}
    files = [pack / ".memory" / "config.json", pack / "defaults.json"] if pack is not None else []
    found: dict = {}
    for path in files + [REPO_ROOT / ".memory" / "config.json"]:
        data, error = _json_object(path)
        cfg["error"] = cfg["error"] or error
        for key in SETTINGS:
            if "selfcompact." + key in data and key not in found:
                found[key] = (data["selfcompact." + key], path)
    for key, (raw, path) in found.items():
        try:
            cfg[key] = SETTINGS[key](raw)
        except (TypeError, ValueError) as e:
            cfg["error"] = cfg["error"] or f"selfcompact.{key} in {_rel(path)}: {e}; the default applies"
            continue
        if key == "limit":
            cfg["limit_source"] = "config"
    return cfg


def override_path(sid: str) -> Path:
    return C.cache_dir() / "selfcompact" / f"{sid}.json"


def session_config(pack: "Path | None", sid: "str | None") -> dict:
    """load_config with the calling session's set-limit override applied (a limit, or off)."""
    cfg = load_config(pack)
    if not C.valid_session(sid):
        return cfg
    path = override_path(sid)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if data.get("enabled") is False:
            cfg.update(enabled=False, enabled_source="session")
            return cfg
        limit = _limit(data["limit"])
    except FileNotFoundError:
        return cfg
    except (OSError, ValueError, TypeError, KeyError, AttributeError) as e:
        cfg["error"] = cfg["error"] or f"session limit override {path.name}: {e}; ignored"
        return cfg
    cfg.update(limit=limit, limit_source="session")
    return cfg


def is_project_session(project_dir, name) -> bool:
    """True when a log call's ``--project`` names the project at ``--dir`` (an ad-hoc task linked to a project
    logs there too, under its own name, and keeps its checkpoint in the framework)."""
    if not project_dir or not isinstance(name, str):
        return False
    try:
        pack = P.require_pack(project_dir)
        data = json.loads((pack / "manifest.json").read_text(encoding="utf-8")).get("project") or {}
    except (P.PackError, OSError, ValueError, AttributeError):
        return False
    names = {Path(project_dir).resolve().name, data.get("slug"), data.get("name")}
    return name.strip().lower() in {n.strip().lower() for n in names if isinstance(n, str) and n.strip()}


def local_url(value: str) -> str:
    """``value`` when it names a loopback API root with an explicit port; ValueError otherwise."""
    u = urllib.parse.urlparse(value)
    try:
        local = u.hostname == "localhost" or ipaddress.ip_address(u.hostname or "").is_loopback
        port = u.port
    except ValueError:
        local, port = False, None
    if (u.scheme not in ("http", "https") or not local or not port or u.username or u.password
            or u.path not in ("", "/") or u.query or u.fragment):
        raise ValueError(f"not a local OpenCode API root: {value!r}")
    return value.rstrip("/")


def service_record(env=None) -> dict:
    """The OpenCode 2 background service's state file (url, pid, password), or {}."""
    env = os.environ if env is None else env
    base = env.get("XDG_STATE_HOME") or ""
    root = Path(base) if base and os.path.isabs(base) else Path(env.get("HOME") or Path.home()) / ".local" / "state"
    data, _ = _json_object(root / "opencode" / "service.json")
    return data


def _alive(pid) -> bool:
    if isinstance(pid, bool) or not isinstance(pid, int) or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def _created(item: dict) -> "float | None":
    t = item.get("time")
    c = t.get("created") if isinstance(t, dict) else None
    return c if isinstance(c, (int, float)) and not isinstance(c, bool) else None


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ApiError("the OpenCode API redirected")


class Client:
    """The few OpenCode 2 ``/api`` routes this tool uses. ``budget`` caps the seconds all calls may take."""

    def __init__(self, url: str, auth: "tuple[str, str] | None", timeout: float = 5.0,
                 budget: "float | None" = None):
        self.url = local_url(url)
        self.headers = {"Content-Type": "application/json", "Accept": "application/json"}
        if auth:
            self.headers["Authorization"] = "Basic " + base64.b64encode(f"{auth[0]}:{auth[1]}".encode()).decode()
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect())
        self.timeout = timeout
        self.deadline = None if budget is None else time.monotonic() + budget

    def remaining(self) -> "float | None":
        return None if self.deadline is None else self.deadline - time.monotonic()

    def call(self, path: str, body=None):
        timeout = self.timeout
        left = self.remaining()
        if left is not None:
            if left <= 0:
                raise ApiError("out of time")
            timeout = min(timeout, left)
        data = None if body is None else json.dumps(body).encode()
        req = urllib.request.Request(self.url + path, data=data, headers=self.headers,
                                     method="GET" if body is None else "POST")
        try:
            with self.opener.open(req, timeout=timeout) as resp:
                raw = resp.read()
        except urllib.error.HTTPError as e:
            raise ApiError("authentication failed" if e.code == 401 else f"HTTP {e.code} from {path}") from None
        except (OSError, http.client.HTTPException, ValueError) as e:
            raise ApiError(f"{path}: {e}") from None
        try:
            return json.loads(raw)
        except ValueError:
            raise ApiError(f"{path}: the reply is not JSON") from None

    def data(self, path: str, body=None):
        reply = self.call(path, body)
        if not isinstance(reply, dict) or "data" not in reply:
            raise ApiError(f"{path}: unexpected reply")
        return reply["data"]

    def _session_path(self, sid: str) -> str:
        return "/api/session/" + urllib.parse.quote(sid, safe="")

    def session(self, sid: str) -> dict:
        info = self.data(self._session_path(sid))
        if not isinstance(info, dict) or info.get("id") != sid:
            raise ApiError("unexpected session reply")
        return info

    def active(self) -> dict:
        active = self.data("/api/session/active")
        if not isinstance(active, dict):
            raise ApiError("unexpected active-session reply")
        return active

    def messages(self, sid: str, kind: str, limit: int) -> list:
        query = urllib.parse.urlencode({"type": kind, "order": "desc", "limit": str(limit)})
        items = self.data(self._session_path(sid) + "/message?" + query)
        if not isinstance(items, list):
            raise ApiError("unexpected message-list reply")
        return [m for m in items if isinstance(m, dict)]

    def usage(self, sid: str) -> "dict | None":
        """The prompt size of the newest finished model step (input plus cache reads and writes), or None."""
        for m in self.messages(sid, "assistant", 5):
            tokens = m.get("tokens") if isinstance(m.get("tokens"), dict) else {}
            cache = tokens.get("cache") if isinstance(tokens.get("cache"), dict) else {}
            try:
                total = sum(int(v or 0) for v in (tokens.get("input"), cache.get("read"), cache.get("write")))
            except (TypeError, ValueError):
                continue
            if total > 0:
                return {"tokens": total, "created_ms": _created(m)}
        return None

    def last_compaction(self, sid: str) -> "dict | None":
        for m in self.messages(sid, "compaction", 1):
            return {"created_ms": _created(m), "status": m.get("status")}
        return None

    def inbox(self, sid: str) -> list:
        items = self.data(self._session_path(sid) + "/inbox")
        if not isinstance(items, list):
            raise ApiError("unexpected inbox reply")
        return items

    def queued_compaction(self, sid: str) -> "dict | None":
        return next((i for i in self.inbox(sid) if isinstance(i, dict) and i.get("type") == "compaction"), None)

    def running_children(self, sid: str, max_depth: int = 6) -> list:
        """Running sessions whose parent chain reaches ``sid`` (its subagents and theirs)."""
        found = []
        for other in self.active():
            if other == sid:
                continue
            current = other
            for _ in range(max_depth):
                parent = self.session(current).get("parentID")
                if not parent:
                    break
                if parent == sid:
                    found.append(other)
                    break
                current = parent
        return found

    def compact(self, sid: str, delivery: str) -> dict:
        if delivery not in ("queue", "steer"):
            raise ValueError(f"unknown delivery {delivery!r}")
        # The route steers when no delivery is given, so always name one.
        item = self.data(self._session_path(sid) + "/compact", {"delivery": delivery})
        if not isinstance(item, dict) or item.get("type") != "compaction":
            raise ApiError("unexpected compaction reply")
        return item


def connect(server_url: "str | None" = None, timeout: float = 5.0, budget: "float | None" = None) -> Client:
    """A client for the OpenCode 2 server; ApiError when there is none or it is not 2.x."""
    record = service_record()
    try:
        recorded = local_url(str(record.get("url") or ""))
    except ValueError:
        recorded = None
    try:
        url = local_url(server_url) if server_url else recorded
    except ValueError as e:
        raise ApiError(str(e)) from None
    if not url:
        raise ApiError("no OpenCode 2 background service is recorded")
    expect_pid = None
    if url == recorded and record.get("password"):
        # A dead service's port may belong to another program now: never send it the password.
        if not _alive(record.get("pid")):
            raise ApiError("the recorded OpenCode service is not running (stale service.json)")
        auth, expect_pid = ("opencode", str(record["password"])), record["pid"]
    elif os.environ.get("OPENCODE_SERVER_PASSWORD"):
        auth = (os.environ.get("OPENCODE_SERVER_USERNAME") or "opencode", os.environ["OPENCODE_SERVER_PASSWORD"])
    else:
        auth = None
    client = Client(url, auth, timeout, budget)
    info = client.call("/api/info")
    version = info.get("version") if isinstance(info, dict) else None
    major = re.match(r"(\d+)\.", version) if isinstance(version, str) else None
    if not major or int(major.group(1)) < 2:
        raise ApiError("the server is not OpenCode 2")
    if expect_pid is not None and info.get("pid") != expect_pid:
        raise ApiError("the server at the recorded address is not the recorded service")
    return client


def open_session(sid, server_url=None, timeout: float = 5.0, budget=None) -> "tuple[Client | None, str | None]":
    """The client when ``sid`` is a running top-level OpenCode 2 session, else (None, why not)."""
    if not sid:
        return None, f"no {SESSION_ENV}: not an OpenCode 2 tool shell"
    if not C.valid_session(sid):
        return None, f"{SESSION_ENV} is not a session id"
    if os.environ.get("CLAUDECODE"):
        return None, f"a Claude Code shell that inherited {SESSION_ENV}"
    try:
        client = connect(server_url, timeout, budget)
        if client.session(sid).get("parentID"):
            return None, "a subagent session; only top-level sessions compact themselves"
        if sid not in client.active():
            return None, "the session is not running, so this shell is not its own"
    except ApiError as e:
        return None, f"cannot read the session from the OpenCode API ({e})"
    return client, None


def _clock(ms: float) -> str:
    return datetime.fromtimestamp(ms / 1000).astimezone().strftime("%H:%M")


def assess(client: Client, sid: str, cfg: dict, now: "float | None" = None) -> dict:
    """Measure the session's context and decide whether a compaction is due."""
    now = time.time() if now is None else now
    usage = client.usage(sid)
    tokens = usage["tokens"] if usage else None
    out = {"context_tokens": tokens, "limit": cfg["limit"], "limit_source": cfg["limit_source"],
           "enabled": cfg["enabled"], "percent": round(100 * tokens / cfg["limit"], 1) if tokens else None,
           "due": False, "pending": False}
    queued = client.queued_compaction(sid)
    if queued is not None:
        out.update(pending=True, inbox_id=queued.get("id"), reason="a compaction is already queued")
        return out
    if tokens is None:
        out["reason"] = "no finished model step to measure yet"
        return out
    if tokens < cfg["limit"]:
        out["reason"] = "under the limit"
        return out
    last = client.last_compaction(sid) or {}
    created = last.get("created_ms")
    failed = " (it failed)" if last.get("status") == "failed" else ""
    if created and created >= (usage.get("created_ms") or 0):
        out["reason"] = f"a compaction ran after the newest measured step{failed}"
    elif created and now - created / 1000 < cfg["min_interval_minutes"] * 60:
        out["reason"] = (f"the last compaction{failed}, at {_clock(created)}, is inside the "
                         f"{cfg['min_interval_minutes']:g}-minute minimum interval")
    elif not cfg["enabled"]:
        out["reason"] = ("over the limit, but self-compaction is off for this session (set-limit off)"
                         if cfg.get("enabled_source") == "session" else "over the limit, but selfcompact.enabled is off")
    else:
        out.update(due=True, reason="the context is at or over the limit")
    return out


def checkpoint_path(project_dir, sid: str) -> Path:
    if project_dir:
        return P.require_pack(project_dir) / ".memory" / "context.md"
    return REPO_ROOT / ".memory" / "selfcompact" / f"{sid}.md"


def checkpoint_problems(path: Path, project: bool, now: float) -> list:
    if not path.is_file():
        return [f"no checkpoint at {_rel(path)}: write it first"]
    problems = []
    age = now - path.stat().st_mtime
    if age > CHECKPOINT_MAX_AGE_S:
        problems.append(f"the checkpoint was last written {int(age // 60)} minutes ago: rewrite it right before "
                        f"requesting (at most {CHECKPOINT_MAX_AGE_S // 60} minutes)")
    text = path.read_text(encoding="utf-8", errors="replace")
    body = text
    if project:
        heading = re.search(r"^##\s+Session Context\b.*$", text, re.I | re.M)
        body = text[heading.end():] if heading else text
    if len(re.sub(r"<!--.*?-->", "", body, flags=re.S).strip().encode()) < CHECKPOINT_MIN_BYTES:
        problems.append(f"the checkpoint holds under {CHECKPOINT_MIN_BYTES} bytes of state")
    if not project:
        missing = [h for h in SESSION_HEADINGS if not re.search(rf"^##\s+{re.escape(h)}\b", text, re.I | re.M)]
        if missing:
            problems.append("the checkpoint lacks " + ", ".join(f"'## {h}'" for h in missing))
    return problems


def repositories(project_dir) -> list:
    """The project's own repositories (its root and each workspace) with ``project_dir``, else the Solaris
    checkout: a project session never works in the framework's repository."""
    if not project_dir:
        dirs = [REPO_ROOT]
    else:
        root = Path(project_dir).resolve()
        dirs = [root]
        try:
            dirs += sorted(p for p in root.iterdir() if p.is_dir() and not p.name.startswith("."))
        except OSError:
            pass
    return [d for d in dirs if (d / ".git").exists()]


def git_problems(repo: Path, deadline: "float | None" = None) -> list:
    """Unfinished git operations in ``repo``; nothing when git cannot tell (or time runs out)."""
    env = {**os.environ, "GIT_OPTIONAL_LOCKS": "0"}  # never take the index lock ourselves

    def git(*args):
        timeout = 30.0 if deadline is None else min(30.0, deadline - time.monotonic())
        if timeout <= 0:
            raise subprocess.TimeoutExpired("git", 0)
        return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, timeout=timeout,
                              env=env, check=False)

    try:
        found = git("rev-parse", "--absolute-git-dir")
    except (OSError, subprocess.SubprocessError):
        return []
    if found.returncode != 0 or not found.stdout.strip():
        return []
    git_dir = Path(found.stdout.strip())
    problems = [label for marker, label in GIT_MARKERS if (git_dir / marker).exists()]
    try:
        staged = git("diff", "--cached", "--quiet")
    except (OSError, subprocess.SubprocessError):
        return problems
    if staged.returncode == 1:
        problems.append("staged changes wait for a commit")
    return problems


def stable_point(client: Client, sid: str, project_dir, *, steer: bool,
                 checkpoint_text: "str | None" = None) -> "tuple[list, list]":
    """(refusals, warnings) beyond the checkpoint's own shape. ``checkpoint_text`` None: none is written yet.

    A queued compaction waits for the turn to end, so the session can still finish its own commit, and running
    subagents need only be named in the checkpoint; a steered one (``--now``) refuses both. A held index.lock
    always refuses.
    """
    refusals, warnings = [], []
    try:
        running = client.running_children(sid)
    except ApiError as e:
        refusals.append(f"cannot list running subagent sessions ({e}); try again next turn")
        running = []
    if running:
        names = ", ".join(running)
        if steer:
            refusals.append(f"subagent sessions are still running ({names}): --now waits for them")
        elif checkpoint_text is None:
            warnings.append(f"subagent sessions are running ({names}): name each in the checkpoint with what to do "
                            "with its result")
        else:
            missing = [s for s in running if s not in checkpoint_text]
            if missing:
                refusals.append("running subagent sessions missing from the checkpoint: " + ", ".join(missing))
            else:
                warnings.append(f"subagent sessions are running ({names}); their results arrive after the compaction")
    left = client.remaining()
    deadline = None if left is None else time.monotonic() + left
    for repo in repositories(project_dir):
        where = "the Solaris checkout" if repo == REPO_ROOT else _rel(repo)
        for problem in git_problems(repo, deadline):
            if "index.lock" in problem:
                refusals.append(f"git: {problem} in {where}; wait for it to finish, never delete the lock")
            elif steer:
                refusals.append(f"git: {problem} in {where}")
            else:
                warnings.append(f"git: {problem} in {where} (finish your own before the turn ends; another "
                                "session's is not yours)")
    return refusals, warnings


def request_command(project_dir) -> str:
    cmd = "uv run -m solaris.tools.session_compact request"
    return cmd + (" --dir " + shlex.quote(_rel(project_dir)) if project_dir else "")


def report(project_dir=None, server_url=None, timeout: float = 5.0, budget=None, cfg=None) -> dict:
    """What ``check`` prints, as a dict. PackError when ``project_dir`` holds no ai-pack."""
    sid = os.environ.get(SESSION_ENV)
    if cfg is None:
        cfg = session_config(P.require_pack(project_dir) if project_dir else None, sid)
    client, why = open_session(sid, server_url, timeout, budget)
    out: dict = {"applicable": client is not None, "config_error": cfg["error"]}
    if client is None:
        out["reason"] = why
        return out
    out.update(session_id=sid, checkpoint=_rel(checkpoint_path(project_dir, sid)),
               request=request_command(project_dir))
    try:
        out.update(assess(client, sid, cfg))
        if out["due"]:
            refusals, warnings = stable_point(client, sid, project_dir, steer=False)
            if refusals:
                out["blocked"] = refusals
            if warnings:
                out["warnings"] = warnings
    except ApiError as e:
        out["error"] = str(e)
    return out


def _tokens(n) -> str:
    return "unknown" if n is None else f"{n / 1000:.0f}k"


def brief_line(rep: dict) -> str:
    if not rep.get("applicable"):
        return f"self-compaction: not applicable ({rep.get('reason')})"
    if rep.get("error"):
        return f"self-compaction: check failed ({rep['error']})"
    head = f"self-compaction: {_tokens(rep.get('context_tokens'))} of {_tokens(rep.get('limit'))} context tokens"
    if rep.get("percent"):
        head += f" ({rep['percent']:g}%)"
    if rep.get("due") and rep.get("blocked"):
        line = (f"{head}, DUE but blocked: {'; '.join(rep['blocked'])}. Write no checkpoint yet; the next log call "
                f"checks again ({SKILL})")
    elif rep.get("due"):
        line = (f"{head}, DUE: follow {SKILL} - reach a stable point, save state, rewrite {rep['checkpoint']}, "
                f"then run: {rep['request']}")
    else:
        line = f"{head}, not due ({rep.get('reason')})"
    if rep.get("due") and rep.get("warnings"):
        line += ". Warnings: " + "; ".join(rep["warnings"])
    if rep.get("config_error"):
        line += f" [config: {rep['config_error']}]"
    return line


def due_line(project_dir=None) -> "str | None":
    """The one line ``interactions add`` prints when a compaction is due, else None; never raises."""
    sid = os.environ.get(SESSION_ENV)
    if not sid:
        return None
    try:
        cfg = session_config(P.require_pack(project_dir) if project_dir else None, sid)
        if not cfg["enabled"]:
            return None
        rep = report(project_dir, timeout=HINT_TIMEOUT, budget=HINT_BUDGET, cfg=cfg)
    except Exception:  # noqa: BLE001 - logging a turn never fails because of this check
        return None
    if not rep.get("applicable") or rep.get("error") or not rep.get("due"):
        return None
    return brief_line(rep)


def prune(folder: Path, suffix: str, now: float) -> None:
    """Delete this tool's files in ``folder`` last written over KEEP_DAYS ago."""
    try:
        files = list(folder.glob("*" + suffix))
    except OSError:
        return
    for f in files:
        try:
            if now - f.stat().st_mtime > KEEP_DAYS * 86400:
                f.unlink()
        except OSError:
            continue


def _emit(code: int, **fields) -> int:
    print(json.dumps(fields))
    return code


def cmd_check(a) -> int:
    pack = P.require_pack(a.dir) if a.dir else None  # a bad --dir is a usage error (exit 2)
    try:
        rep = report(a.dir, a.server_url, cfg=session_config(pack, os.environ.get(SESSION_ENV)))
    except Exception as e:  # noqa: BLE001 - check reports, it never fails the call it rides on
        rep = {"applicable": True, "error": f"{type(e).__name__}: {e}"}
    print(brief_line(rep) if a.brief else json.dumps(rep))
    return EXIT_OK


def cmd_request(a) -> int:
    sid = os.environ.get(SESSION_ENV)
    pack = P.require_pack(a.dir) if a.dir else None
    cfg = session_config(pack, sid)
    client, why = open_session(sid, a.server_url)
    if client is None:
        return _emit(EXIT_NA, requested=False, reason=why)
    checkpoint = checkpoint_path(a.dir, sid)
    after = {"checkpoint": _rel(checkpoint),
             "next": f"After the compaction, read {_rel(checkpoint)} before anything else."}
    try:
        rep = assess(client, sid, cfg)
    except ApiError as e:
        return _emit(EXIT_NA, requested=False, reason=f"cannot read the session ({e})")
    common = {"session_id": sid, "context_tokens": rep["context_tokens"], "limit": rep["limit"],
              "limit_source": rep["limit_source"], "config_error": cfg["error"]}
    if rep["pending"]:
        return _emit(EXIT_OK, requested=False, pending=True, inbox_id=rep.get("inbox_id"), reason=rep["reason"],
                     **after, **common)
    if not rep["due"] and not a.force:
        return _emit(EXIT_NOT_DUE, requested=False, reason=rep["reason"], **common)
    try:
        problems = checkpoint_problems(checkpoint, bool(a.dir), time.time())
        text = checkpoint.read_text(encoding="utf-8", errors="replace") if checkpoint.is_file() else ""
    except OSError as e:
        problems, text = [f"cannot read the checkpoint ({e})"], ""
    refusals, warnings = stable_point(client, sid, a.dir, steer=a.now, checkpoint_text=text)
    problems += refusals
    if warnings:
        common["warnings"] = warnings
    if problems:
        return _emit(EXIT_REFUSED, requested=False, refused=problems, **common)
    delivery = "steer" if a.now else "queue"
    if a.dry_run:
        return _emit(EXIT_OK, requested=False, dry_run=True, delivery=delivery, checkpoint=_rel(checkpoint), **common)
    try:
        item = client.compact(sid, delivery)
    except ApiError as e:
        # The server may have queued it before the reply was lost: look before calling this a failure.
        try:
            item = client.queued_compaction(sid)
        except ApiError:
            item = None
        if item is None:
            return _emit(EXIT_NA, requested=False, reason=f"the compaction request failed ({e})", **common)
    now = time.time()
    if not a.dir:  # a project session never touches framework files
        prune(REPO_ROOT / ".memory" / "selfcompact", ".md", now)
    prune(override_path(sid).parent, ".json", now)
    when = ("at the next step boundary; this turn goes on with the compacted context" if a.now
            else "right after this turn's final message")
    return _emit(EXIT_OK, requested=True, delivery=delivery, inbox_id=item.get("id"),
                 forced=bool(a.force and not rep["due"]), compacts=when, **after, **common)


def cmd_set_limit(a) -> int:
    sid = os.environ.get(SESSION_ENV)
    if not C.valid_session(sid):
        return _emit(EXIT_NA, ok=False, reason=f"no usable {SESSION_ENV}: not an OpenCode 2 tool shell")
    pack = P.require_pack(a.dir) if a.dir else None
    path = override_path(sid)
    if a.clear:
        try:
            path.unlink(missing_ok=True)
        except OSError as e:
            return _emit(EXIT_USAGE, ok=False, reason=f"cannot remove {path}: {e}")
    else:
        if a.tokens.strip().lower() == "off":
            override = {"enabled": False}
        else:
            try:
                override = {"limit": _limit(a.tokens)}
            except ValueError as e:
                return _emit(EXIT_USAGE, ok=False, reason=str(e))
        try:
            C.private_dirs(path.parent)
            stamp = datetime.now().astimezone().isoformat(timespec="seconds")
            write_text_atomic(path, json.dumps({**override, "set_at": stamp}) + "\n")
        except OSError as e:
            return _emit(EXIT_USAGE, ok=False, reason=f"cannot write {path}: {e}")
    cfg = session_config(pack, sid)
    return _emit(EXIT_OK, ok=True, session_id=sid, enabled=cfg["enabled"], limit=cfg["limit"],
                 limit_source=cfg["limit_source"], config_error=cfg["error"])


def main(argv: "list[str] | None" = None) -> int:
    shared = argparse.ArgumentParser(add_help=False)
    shared.add_argument("--dir", help="project folder: its <pack>/.memory/context.md is the checkpoint and its "
                                      "settings apply")
    shared.add_argument("--server-url", help="local OpenCode API root (default: the recorded background service)")
    parser = argparse.ArgumentParser(prog="solaris.tools.session_compact", description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="cmd", required=True)
    check = sub.add_parser("check", parents=[shared], help="measure the context against the limit (exits 0)")
    check.add_argument("--brief", action="store_true", help="one line instead of JSON")
    req = sub.add_parser("request", parents=[shared], help="queue a compaction once the gates pass")
    req.add_argument("--now", action="store_true", help="compact at the next step boundary; the turn goes on")
    req.add_argument("--force", action="store_true",
                     help="the owner asked: skip the enabled, limit and interval gates, never the others")
    req.add_argument("--dry-run", action="store_true", help="run every gate, queue nothing")
    lim = sub.add_parser("set-limit", parents=[shared], help="override the limit for this session only, or turn "
                                                             "self-compaction off for it")
    lim.add_argument("tokens", nargs="?", help="context tokens, an integer or k/m shorthand (floor 100k), or off")
    lim.add_argument("--clear", action="store_true", help="drop the override")
    a = parser.parse_args(argv)
    if a.cmd == "set-limit" and bool(a.tokens) == bool(a.clear):
        print("session_compact: set-limit takes a token count or --clear", file=sys.stderr)
        return EXIT_USAGE
    try:
        return {"check": cmd_check, "request": cmd_request, "set-limit": cmd_set_limit}[a.cmd](a)
    except P.PackError as e:
        print(f"session_compact: {e}", file=sys.stderr)
        return EXIT_USAGE


if __name__ == "__main__":
    sys.exit(main())
