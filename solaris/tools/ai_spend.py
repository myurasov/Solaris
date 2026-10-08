# Copyright 2026 Mikhail Yurasov <me@yurasov.me>
# SPDX-License-Identifier: Apache-2.0

"""Estimated AI spend per project and day, from the harness's own usage records (stdlib only).

The records are this machine's Claude Code transcripts, ``~/.claude/projects/**/*.jsonl`` (under
``$CLAUDE_CONFIG_DIR`` when it is set), subagent and workflow transcripts included: the only usage records the
projects' own spend tools read. Sessions run on another machine are counted only there.
Of each assistant line only the usage counts, model, response id, timestamp, session id and working directory are
used (with --detail also its effort, sidechain mark and agent id, and of each hook attachment its type, event and the
start of a failed hook's output); message content is never kept or printed. A response logged on several lines (one
per content block, or copied into a resumed session) counts once. Calls Claude Code makes for itself (session titles,
web page summaries) are not in the transcripts, so they are not counted.

Spend is an estimate at list prices (PRICES), not a bill: uncached input, cache writes (1.25x input for 5-minute
entries, 2x for 1-hour ones), cache reads and output, per model. A model missing from PRICES is counted in tokens
and named in the output, never priced.

A response belongs to the project holding its working directory: the nearest folder at or above it with an ai-pack
(solaris.tools.pack; the home folder and above are never searched). A response made outside every project (at the
Solaris root, say, as subagents often are) goes to the project most of its session's responses went to, else it
stays unattributed. Days are the owner's: ``owner.timezone`` (an IANA zone name) for one project (--dir) in its
``<pack>/.memory/config.json``, else its ``<pack>/defaults.json``; then in the framework's .memory/config.json;
else the machine's zone. A project's approximate daily limit is ``ai.daily_budget_usd`` in
``<pack>/.memory/config.json``, else in ``<pack>/defaults.json``.

--detail adds who answered, for the responses in the window (attributed as above): the main thread's responses
(``isSidechain`` false, in a session's own transcript) with their models and the ``effort`` each line records; each
worker transcript (``<session>/subagents/**/agent-<id>.jsonl``, or sidechain lines of an older main transcript) with
its session (the first 8 characters), agent id, first response, model, effort and estimated spend; and the hook runs
the transcripts record as attachments (``hook_success``, or a failure: a ``hook_*`` type naming an error or a
cancel), with the newest 50 failures and the first 120 characters of each one's output. A field an older transcript
lacks (effort, agent id, hook event) is left out or null, never guessed.

Run::

    uv run -m solaris.tools.ai_spend                                  # every project plus unattributed, per day
    uv run -m solaris.tools.ai_spend --dir projects/<slug> --today    # one project, today: the pacing check
    uv run -m solaris.tools.ai_spend --since 2026-09-28 --json        # from a day (or an ISO time) on, as JSON
    uv run -m solaris.tools.ai_spend --dir projects/<slug> --since 2026-10-07T18:00-07:00 --json --detail

Exit codes: 0 fine; 3 a reported project's estimate for today is over its daily limit; 1 a bad --dir or config
(the report still prints when only a limit is bad); 2 bad arguments.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import time
from collections import Counter, defaultdict
from datetime import datetime, time as dtime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from solaris.tools import pack as P

REPO_ROOT = Path(__file__).resolve().parents[2]
FRAMEWORK_CONFIG = REPO_ROOT / ".memory" / "config.json"
TZ_KEY = "owner.timezone"
BUDGET_KEY = "ai.daily_budget_usd"
OVER = 3   # exit code: a reported project is over its daily limit today
UNATTRIBUTED = "(unattributed)"

# List prices in US dollars per million tokens: (input, output, cache read). A cache write costs 1.25x input for a
# 5-minute entry and 2x for a 1-hour one. Update from Anthropic's pricing page together with
# solaris/info/model-tiers.md, which quotes the prices of the tier models.
PRICES_AS_OF = "2026-10-01"
PRICES = {
    "claude-fable-5-1": (10.00, 50.00, 0.25),
    "claude-fable-5": (10.00, 50.00, 1.00),
    "claude-opus-5-5": (4.00, 20.00, 0.20),
    "claude-opus-5": (5.00, 25.00, 0.50),
    "claude-opus-4-8": (5.00, 25.00, 0.50),
    "claude-sonnet-5-5": (2.00, 10.00, 0.20),
    "claude-sonnet-5": (2.00, 10.00, 0.20),
    "claude-haiku-4-5": (1.00, 5.00, 0.10),
}
WRITE_5M, WRITE_1H = 1.25, 2.0
# a model id's family and version, without a date or [1m] suffix or a cloud prefix: claude-haiku-4-5-20251001
_MODEL_RE = re.compile(r"claude-[a-z]+-\d{1,2}(?:-\d{1,2})?(?!\d)")
# --detail: a session shows by this many characters, a failed hook by this much of its output; failures listed
SESSION_CHARS, HOOK_TEXT, HOOK_FAILURES = 8, 120, 50
# a hook attachment whose type names one of these records a failed run; hook_success a run that worked
HOOK_FAILED = ("error", "cancel")

_now = time.time   # tests swap the clock


# ----------------------------------------------------------------- prices

def price_key(model: str) -> "str | None":
    """The PRICES key a model id prices as, or None when it names no Claude model."""
    m = _MODEL_RE.search((model or "").lower())
    return m.group(0) if m else None


def cost(model: str, tokens: "tuple[int, ...]") -> "float | None":
    """Estimated US dollars for one response's (input, 5-minute writes, 1-hour writes, cache reads, output)
    tokens; None for a model missing from PRICES."""
    price = PRICES.get(price_key(model))
    if price is None:
        return None
    inp, out, read = price
    i, w5, w1, r, o = tokens
    return (i * inp + w5 * inp * WRITE_5M + w1 * inp * WRITE_1H + r * read + o * out) / 1e6


# ----------------------------------------------------------------- transcripts

def transcripts_root() -> Path:
    return Path(os.environ.get("CLAUDE_CONFIG_DIR") or "~/.claude").expanduser() / "projects"


def _count(v) -> int:
    return v if isinstance(v, int) and not isinstance(v, bool) and v > 0 else 0


def usage_of(d: dict) -> "tuple | None":
    """(response id, epoch, session, cwd, model, tokens) of an assistant line with usage, else None. Tokens are
    (input, 5-minute cache writes, 1-hour cache writes, cache reads, output). Reads no message content."""
    msg = d.get("message")
    if d.get("type") != "assistant" or not isinstance(msg, dict):
        return None
    usage, model = msg.get("usage"), msg.get("model")
    rid = msg.get("id") or d.get("requestId")
    if not isinstance(usage, dict) or not isinstance(model, str) or model == "<synthetic>" or not rid:
        return None
    try:
        ts = datetime.fromisoformat(d["timestamp"]).timestamp()
    except (KeyError, TypeError, ValueError):
        return None
    split = usage.get("cache_creation") if isinstance(usage.get("cache_creation"), dict) else {}
    w1 = _count(split.get("ephemeral_1h_input_tokens"))
    writes = max(_count(usage.get("cache_creation_input_tokens")),
                 _count(split.get("ephemeral_5m_input_tokens")) + w1)
    w1 = min(w1, writes)   # an unsplit total counts as 5-minute writes
    tokens = (_count(usage.get("input_tokens")), writes - w1, w1, _count(usage.get("cache_read_input_tokens")),
              _count(usage.get("output_tokens")))
    if not any(tokens):
        return None
    return str(rid), ts, str(d.get("sessionId") or ""), str(d.get("cwd") or ""), model, tokens


def who_of(d: dict, path: Path, worker_file: bool) -> tuple:
    """(worker key or None for the main thread, agent id or None, effort or None) of an assistant line. A worker is a
    transcript under a session's folder, or a sidechain line of an older main transcript (keyed by its agent id)."""
    agent = d.get("agentId") if isinstance(d.get("agentId"), str) and d.get("agentId") else None
    if agent is None and worker_file and path.stem.startswith("agent-"):
        agent = path.stem[len("agent-"):]
    effort = d.get("effort") if isinstance(d.get("effort"), str) and d.get("effort") else None
    worker = worker_file or d.get("isSidechain") is True
    return ((path.as_posix(), agent) if worker else None), agent, effort


def hook_of(d: dict) -> "tuple | None":
    """(epoch, session, cwd, event, failed, text) of a hook attachment that records a run: hook_success, or a failure
    (a hook_* type naming an error or a cancel), whose text is the start of its output. None for any other line, or
    a hook attachment that adds context or a decision to a run."""
    a = d.get("attachment")
    kind = a.get("type") if isinstance(a, dict) else None
    if d.get("type") != "attachment" or not isinstance(kind, str) or not kind.startswith("hook_"):
        return None
    failed = any(word in kind for word in HOOK_FAILED)
    if kind != "hook_success" and not failed:
        return None
    try:
        ts = datetime.fromisoformat(d["timestamp"]).timestamp()
    except (KeyError, TypeError, ValueError):
        return None
    text = ""
    if failed:
        out = next((a[k] for k in ("stderr", "stdout", "content") if isinstance(a.get(k), str) and a[k].strip()), "")
        text = " ".join(out.split())[:HOOK_TEXT]
    event = a.get("hookEvent") if isinstance(a.get("hookEvent"), str) else None
    return ts, str(d.get("sessionId") or ""), str(d.get("cwd") or ""), event, failed, text


def scan(root: Path, start: float, detail: "dict | None" = None) -> dict:
    """{response id: [epoch, session, cwd, model, tokens]} from every transcript under ``root`` written at or after
    ``start`` (a file last written before it holds nothing newer). Older responses in those files come back too:
    they help place a session in its project. With ``detail`` (a dict) it also fills detail["who"], {response id:
    who_of(...)} of the line each response counts at, and detail["hooks"], {line id: hook_of(...)} (a hook line
    copied into a resumed session counts once)."""
    found: dict = {}
    who = hooks = None
    if detail is not None:
        who, hooks = detail.setdefault("who", {}), detail.setdefault("hooks", {})
    if not root.is_dir():
        return found
    for path in sorted(root.rglob("*.jsonl")):
        try:
            if path.stat().st_mtime < start:
                continue
            fh = path.open("rb")
        except OSError:
            continue
        parts = path.relative_to(root).parts   # <project dir>/<session>.jsonl or <project dir>/<session>/...
        folder_session = Path(parts[1]).stem if len(parts) > 1 else path.stem
        worker_file = len(parts) > 2   # subagents/ and subagents/workflows/ transcripts sit in the session's folder
        with fh:
            for n, raw in enumerate(fh):
                hook_line = hooks is not None and b'"hook_' in raw
                if b'"usage"' not in raw and not hook_line:   # only assistant lines carry usage: skip the rest unparsed
                    continue
                try:
                    d = json.loads(raw)
                except ValueError:
                    continue
                if not isinstance(d, dict):
                    continue
                if hook_line and d.get("type") == "attachment":
                    hook = hook_of(d)
                    if hook is not None:
                        key = d.get("uuid") if isinstance(d.get("uuid"), str) else f"{path}:{n}"
                        hooks.setdefault(key, (hook[0], hook[1] or folder_session, *hook[2:]))
                    continue
                rec = usage_of(d)
                if rec is None:
                    continue
                rid, ts, session, cwd, model, tokens = rec
                old = found.get(rid)
                if old is None:
                    found[rid] = [ts, session or folder_session, cwd, model, tokens]
                    if who is not None:
                        who[rid] = who_of(d, path, worker_file)
                    continue
                # the same response again: a later content block (output still growing) or a copy in a resumed
                # session; the largest counts are the final ones, and the earliest line is where it was spent
                merged = tuple(map(max, old[4], tokens))
                if ts < old[0]:
                    found[rid] = [ts, session or folder_session, cwd, model, merged]
                    if who is not None:
                        who[rid] = who_of(d, path, worker_file)
                else:
                    old[4] = merged
    return found


# ----------------------------------------------------------------- projects

class Projects:
    """Finds the project folder of a working directory (cached per run)."""

    def __init__(self):
        home = Path.home()
        # never listed: no project lives there, and listing ~/Desktop can raise macOS privacy prompts
        self.stops = {home, *home.parents, home.resolve(), *home.resolve().parents}
        self.by_cwd: dict = {}
        self.is_project: dict = {}

    def root_of(self, cwd: str) -> "Path | None":
        """The nearest folder at or above ``cwd`` that holds one ai-pack."""
        if cwd not in self.by_cwd:
            self.by_cwd[cwd] = None
            here = Path(cwd)
            for d in ((here, *here.parents) if here.is_absolute() else ()):
                if d in self.stops:
                    break
                if d not in self.is_project:
                    try:
                        self.is_project[d] = P.find_pack(d) is not None
                    except P.PackError:   # two packs: a broken project, nothing to attribute to
                        self.is_project[d] = False
                if self.is_project[d]:
                    self.by_cwd[cwd] = d.resolve()
                    break
        return self.by_cwd[cwd]


def label(root: "Path | None") -> "str | None":
    """How a project is shown: relative to the Solaris checkout, else to the home folder, else in full."""
    if root is None:
        return None
    for base, prefix in ((REPO_ROOT.resolve(), ""), (Path.home().resolve(), "~/")):
        try:
            return prefix + root.relative_to(base).as_posix()
        except ValueError:
            pass
    return root.as_posix()


def _json_object(path: Path) -> dict:
    """A JSON object file's content, {} when the file is absent; ValueError with a clean message otherwise."""
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return {}
    except (OSError, UnicodeDecodeError) as exc:
        raise ValueError(f"{path}: cannot read it ({exc})") from None
    try:
        data = json.loads(text)
    except ValueError as exc:
        raise ValueError(f"{path}: not valid JSON ({exc})") from None
    if not isinstance(data, dict):
        raise ValueError(f"{path}: must hold a JSON object")
    return data


def daily_budget(root: Path) -> "float | None":
    """The project's ai.daily_budget_usd (<pack>/.memory/config.json wins over <pack>/defaults.json), or None when
    unset; ValueError when a file is unreadable or the value is not a positive number."""
    try:
        pack = P.require_pack(root)
    except P.PackError as exc:
        raise ValueError(str(exc)) from None
    for f in (pack / ".memory" / "config.json", pack / "defaults.json"):
        cfg = _json_object(f)
        if BUDGET_KEY not in cfg:
            continue
        v = cfg[BUDGET_KEY]
        if v is None:
            return None
        if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or v <= 0:
            raise ValueError(f"{f}: {BUDGET_KEY} must be a positive number of US dollars, got {v!r}")
        return float(v)
    return None


# ----------------------------------------------------------------- days

def owner_zone(pack: "Path | None" = None) -> "ZoneInfo | None":
    """The owner's time zone: owner.timezone in the project's <pack>/.memory/config.json, else its
    <pack>/defaults.json (when ``pack`` is given), else the framework config; None for the machine's zone."""
    files = (pack / ".memory" / "config.json", pack / "defaults.json") if pack is not None else ()
    for f in (*files, FRAMEWORK_CONFIG):
        name = _json_object(f).get(TZ_KEY)
        if name is None:
            continue
        try:
            return ZoneInfo(name)
        except (ZoneInfoNotFoundError, ValueError, TypeError, OSError):
            raise ValueError(f"{f}: {TZ_KEY} {name!r} is not a time zone name such as "
                             "America/Los_Angeles") from None
    return None


def day_of(ts: float, tz: "ZoneInfo | None") -> str:
    return datetime.fromtimestamp(ts, timezone.utc).astimezone(tz).date().isoformat()


def day_start(ts: float, tz: "ZoneInfo | None") -> float:
    """Epoch of the start of the owner's day holding ``ts``."""
    day = datetime.fromtimestamp(ts, timezone.utc).astimezone(tz).date()
    return datetime.combine(day, dtime(), tzinfo=tz).timestamp()   # no zone: the machine's midnight


def parse_since(text: str, tz: "ZoneInfo | None") -> float:
    """Epoch of an ISO day (its start in the owner's zone) or time (the owner's zone unless it has an offset)."""
    dt = datetime.fromisoformat(text.strip())
    if dt.tzinfo is None and tz is not None:
        dt = dt.replace(tzinfo=tz)
    return dt.timestamp()


# ----------------------------------------------------------------- report

def attribution(found: dict) -> tuple:
    """(Projects, {session: the project most of its responses with a project went to})."""
    projects = Projects()
    votes: dict = defaultdict(Counter)
    for _ts, session, cwd, _model, _tokens in found.values():
        root = projects.root_of(cwd)
        if root is not None:
            votes[session][root] += 1
    return projects, {session: c.most_common(1)[0][0] for session, c in votes.items()}


def report(found: dict, tz: "ZoneInfo | None", start: float, now: float, only: "Path | None" = None) -> dict:
    """Per (day, project) rows from ``start`` on, today's spend against each reported project's daily limit, the
    unpriced models and any limit that could not be read. ``only`` keeps one project."""
    projects, lead = attribution(found)
    today0 = day_start(now, tz)
    sums: dict = {}   # (day, root) -> [requests, input, cache writes, cache reads, output, usd]
    today: dict = defaultdict(float)
    unpriced: Counter = Counter()
    for ts, session, cwd, model, tokens in found.values():
        if ts < min(start, today0):
            continue
        root = projects.root_of(cwd) or lead.get(session)
        if only is not None and root != only:
            continue
        usd = cost(model, tokens)
        if usd is None:
            unpriced[model] += 1
        if ts >= today0:
            today[root] += usd or 0.0
        if ts < start:
            continue
        row = sums.setdefault((day_of(ts, tz), root), [0, 0, 0, 0, 0, 0.0])
        for i, n in enumerate((1, tokens[0], tokens[1] + tokens[2], tokens[3], tokens[4], usd or 0.0)):
            row[i] += n
    rows = [{"day": day, "project": label(root), "requests": s[0], "input_tokens": s[1], "cache_write_tokens": s[2],
             "cache_read_tokens": s[3], "output_tokens": s[4], "usd": round(s[5], 4)}
            for (day, root), s in sorted(sums.items(), key=lambda kv: (kv[0][0], label(kv[0][1]) or "~"))]
    budgets, problems = [], []
    roots = [only] if only is not None else sorted({r for _d, r in sums} | set(today), key=lambda r: label(r) or "")
    for root in roots:
        if root is None:
            continue
        try:
            limit = daily_budget(root)
        except ValueError as exc:
            problems.append(str(exc))
            continue
        if limit is not None:
            spent = round(today.get(root, 0.0), 4)
            budgets.append({"project": label(root), "daily_budget_usd": limit, "today_usd": spent,
                            "over": spent > limit})
    return {"rows": rows, "budgets": budgets, "unpriced_models": dict(unpriced.most_common()),
            "problems": problems}


def _when(ts: float, tz: "ZoneInfo | None") -> str:
    return datetime.fromtimestamp(ts, timezone.utc).astimezone(tz).isoformat(timespec="seconds")


def detail(found: dict, extra: dict, tz: "ZoneInfo | None", start: float, only: "Path | None" = None) -> dict:
    """Who answered from ``start`` on (see --detail): the main thread's responses with their models and efforts,
    each worker transcript, and the hook runs with their failures. ``extra`` is what scan() filled; ``only`` keeps
    one project, attributed as report() does."""
    projects, lead = attribution(found)
    who, hooks = extra.get("who") or {}, extra.get("hooks") or {}

    def kept(ts, session, cwd):
        return ts >= start and (only is None or (projects.root_of(cwd) or lead.get(session)) == only)

    turns, models, efforts, workers = 0, Counter(), Counter(), {}
    for rid, (ts, session, cwd, model, tokens) in found.items():
        if not kept(ts, session, cwd):
            continue
        key, agent, effort = who.get(rid, (None, None, None))
        if key is None:
            turns += 1
            models[model] += 1
            if effort:
                efforts[effort] += 1
            continue
        w = workers.setdefault(key, {"session": session[:SESSION_CHARS], "agent": agent, "first": ts,
                                     "models": Counter(), "efforts": Counter(), "usd": None})
        w["first"] = min(w["first"], ts)
        w["models"][model] += 1
        if effort:
            w["efforts"][effort] += 1
        usd = cost(model, tokens)
        if usd is not None:
            w["usd"] = (w["usd"] or 0.0) + usd
    rows = [{"session": w["session"], "agent": w["agent"], "first": _when(w["first"], tz),
             "model": w["models"].most_common(1)[0][0],
             "effort": w["efforts"].most_common(1)[0][0] if w["efforts"] else None,
             "usd": None if w["usd"] is None else round(w["usd"], 4)}
            for w in sorted(workers.values(), key=lambda w: (w["first"], w["session"], w["agent"] or ""))]
    runs, failures = 0, []
    for ts, session, cwd, event, failed, text in hooks.values():
        if kept(ts, session, cwd):
            runs += 1
            if failed:
                failures.append((ts, event, text))
    failures.sort(key=lambda f: f[0])
    return {"main": {"turns": turns, "models": dict(models.most_common()), "effort": dict(efforts.most_common())},
            "workers": rows,
            "hooks": {"runs": runs, "failed": len(failures),
                      "failures": [{"ts": _when(ts, tz), "event": event, "text": text}
                                   for ts, event, text in failures[-HOOK_FAILURES:]]}}


def print_detail(d: dict) -> None:
    main, hooks = d["main"], d["hooks"]

    def counts(c):
        return ", ".join(f"{k} {v:,}" for k, v in c.items()) or "none recorded"

    print(f"main thread: {main['turns']:,} responses; models {counts(main['models'])}; effort {counts(main['effort'])}")
    print(f"workers: {len(d['workers'])}")
    for w in d["workers"]:
        usd = "n/a" if w["usd"] is None else f"${w['usd']:,.2f}"
        print(f"  {w['session']} {w['agent'] or '-'}  from {w['first']}  {w['model']}  effort {w['effort'] or '-'}  "
              f"{usd}")
    print(f"hooks: {hooks['runs']:,} runs, {hooks['failed']:,} failed")
    for f in hooks["failures"][-5:]:
        print(f"  {f['ts']} {f['event'] or '?'}: {f['text']}")


def _tokens(n: int) -> str:
    for size, unit in ((1e9, "B"), (1e6, "M"), (1e3, "k")):
        if n >= size:
            return f"{n / size:.1f}{unit}"
    return str(n)


def _short(name: str, width: int = 40) -> str:
    return name if len(name) <= width else "..." + name[3 - width:]   # the tail names the project


def print_table(rep: dict, header: str, per_day_totals: bool) -> None:
    print(header)
    rows = rep["rows"]
    if not rows:
        print("no Claude Code usage in that window")
    else:
        lines = []
        for day in sorted({r["day"] for r in rows}):
            group = [r for r in rows if r["day"] == day]
            lines += [(day, _short(r["project"] or UNATTRIBUTED), r) for r in group]
            if per_day_totals and len(group) > 1:
                total = {k: sum(r[k] for r in group) for k in group[0] if k not in ("day", "project")}
                lines.append((day, "total", total))
        w = max(len("project"), *(len(name) for _d, name, _r in lines))
        print(f"{'day':<10}  {'project':<{w}}  {'requests':>8}  {'input':>7}  {'c.write':>7}  {'c.read':>7}  "
              f"{'output':>7}  {'est. $':>10}")
        for day, name, r in lines:
            print(f"{day:<10}  {name:<{w}}  {r['requests']:>8,}  {_tokens(r['input_tokens']):>7}  "
                  f"{_tokens(r['cache_write_tokens']):>7}  {_tokens(r['cache_read_tokens']):>7}  "
                  f"{_tokens(r['output_tokens']):>7}  {r['usd']:>10,.2f}")
    for b in rep["budgets"]:
        pct = 100 * b["today_usd"] / b["daily_budget_usd"]
        print(f"daily limit {b['project']}: ${b['today_usd']:,.2f} today of about ${b['daily_budget_usd']:,.2f} "
              f"({pct:.0f}%)" + (", OVER" if b["over"] else ""))
    for model, n in rep["unpriced_models"].items():
        print(f"not priced: {model} ({n:,} requests; tokens counted) - add it to PRICES in solaris/tools/ai_spend.py")
    for problem in rep["problems"]:
        print(f"ai_spend: {problem}")


def main(argv: "list[str] | None" = None) -> int:
    parser = argparse.ArgumentParser(prog="solaris.tools.ai_spend", description=__doc__.splitlines()[0])
    parser.add_argument("--dir", help="one project's root folder (default: every project plus unattributed)")
    when = parser.add_mutually_exclusive_group()
    when.add_argument("--today", action="store_true", help="the owner's today only")
    when.add_argument("--since", metavar="ISO",
                      help="from this day (its start in the owner's zone) or ISO time on; default: everything kept")
    parser.add_argument("--json", action="store_true", help="print JSON instead of a table")
    parser.add_argument("--detail", action="store_true",
                        help="also who answered: the main thread's models and efforts, each worker transcript, and "
                             "the hook runs with their failures (in JSON, a \"detail\" object)")
    args = parser.parse_args(argv)
    only = pack = None
    if args.dir is not None:
        only = Path(args.dir)
        if not only.is_dir():
            print(f"ai_spend: {only} not found: --dir must be a project root (embedded mode: the repo root)")
            return 1
        try:
            pack = P.require_pack(only)
        except P.PackError as exc:
            print(f"ai_spend: {exc}")
            return 1
        only = only.resolve()
    try:
        tz = owner_zone(pack)
    except ValueError as exc:
        print(f"ai_spend: {exc}")
        return 1
    now = _now()
    start = 0.0
    if args.today:
        start = day_start(now, tz)
    elif args.since is not None:
        try:
            start = parse_since(args.since, tz)
        except ValueError:
            parser.error(f"--since {args.since!r} is not an ISO day or time (2026-09-28, 2026-09-28T08:00-07:00)")
        if start > now:
            parser.error(f"--since {args.since!r} is in the future")
    extra = {} if args.detail else None
    found = scan(transcripts_root(), min(start, day_start(now, tz)), extra)
    rep = report(found, tz, start, now, only)
    if extra is not None:
        rep["detail"] = detail(found, extra, tz, start, only)
    zone = tz.key if tz is not None else None
    since = datetime.fromtimestamp(start, timezone.utc).astimezone(tz) if start else None
    if args.json:
        print(json.dumps({"source": "claude-code transcripts", "prices_as_of": PRICES_AS_OF, "timezone": zone,
                          "since": since.isoformat() if since else None, **rep}, indent=2))
    else:
        window = since.strftime("since %Y-%m-%d %H:%M %Z") if since else "everything kept"
        days = zone or "the machine's zone"
        print_table(rep, f"AI spend estimate, {window} (days in {days}): Claude Code transcripts at list prices "
                         f"as of {PRICES_AS_OF}" + (f", {label(only)}" if only else ""), per_day_totals=only is None)
        if extra is not None:
            print_detail(rep["detail"])
    if rep["problems"]:
        return 1
    return OVER if any(b["over"] for b in rep["budgets"]) else 0


if __name__ == "__main__":
    raise SystemExit(main())
