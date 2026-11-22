# rev. 1

"""kaggle_watch: wait until one submission is scored or fails.

Polls the team's submissions through the gateway, read-only, until the submission named has a score or an error,
then prints one line and exits with a code that says which.

    python3 <plugin-dir>/tools/kaggle_watch.py <slug> --ref REF [--every SECONDS] [--limit MINUTES]
    python3 <plugin-dir>/tools/kaggle_watch.py <slug> --message TEXT [--every SECONDS] [--limit MINUTES]

  --ref REF          the submission's ref, as `kaggle.py --sdk submissions <slug>` lists it
  --message TEXT     the newest submission whose description is exactly TEXT (spaces aside): for a submission
                     whose ref is not known yet. It is chosen again at every read, by the date Kaggle gives, and
                     the result line names the ref it watched and that submission's date: an earlier submission
                     with the same description can be the newest one listed for a moment after a submit, so
                     check the ref and the date, or give every submission its own description. Two submissions
                     with the description that are both still pending cannot be told apart: exit 2
  --every SECONDS    the time between two reads (default 180, at least 20)
  --limit MINUTES    how long to wait in all (default 240); then exit 4
  --name NAME        the heartbeat file's name (default: the ref, or message-<hash> with --message)
  --gateway PATH     the gateway (default: the kaggle.py beside this file)

Run it from the project root or task folder. Each read is the gateway's `--sdk submissions <slug> --page-size 100
--json`: the team's newest 100 submissions, one Kaggle call, with no activity stamp (KAGGLE_SHARE_QUIET=1). A
read that failed (the gateway's exit code, a timeout, no JSON) or that Kaggle answered with HTTP 429 never counts
as "not listed": the wait before the next read doubles each time, up to 15 minutes (never less than --every),
and goes back to --every after a read that worked. A submission missing from a clean read is read once more,
half a minute later at most, before it counts as not listed.

The last line on stdout is the result, and the exit code says which:
  0  scored: `<ref> <public score> COMPLETE` (the score is - when Kaggle shows none)
  3  errored: `<ref> ERROR: <Kaggle's error text>`; any status that is none of PENDING, RUNNING, QUEUED and
     COMPLETE ends the watch this way, with that status in the line
  4  the limit passed and the submission was still pending, or no read worked: `<ref> still pending after <N>
     min ...`; read the submission list by hand, then start the watch again if it is still pending
  5  not listed: two clean reads, with no clean read that listed it in between, did not show the submission
     among the newest 100
  2  bad usage (also: no project or task folder here, two ai-packs, no gateway), or --message fits two
     submissions that are both still pending: `2 submissions with the description ... (refs A, B): name one
     with --ref`
  1  the tool itself failed in a way it did not expect; the line on stderr says how
  143 stopped by a signal
With --message the lines for 0, 3 and 4 end with `(by message: the newest of <N> with this description, sent
<date>)`. Each change of state is one line on stderr.

Heartbeat: <context>/__data/kaggle/<slug>/watch/<name>.json, written whole at every read and at the end:
{"slug", "ref", "message", "pid", "host", "started", "limit_at", "every", "polls", "checked" (the last read),
"next" (when the next read is due, null at the end), "state" (pending, read failed, rate-limited, not listed,
scored, errored, limit, ambiguous, stopped), "status" (Kaggle's last status), "public_score", "error", "exit"
(null while it runs)}. With --message, "ref" is the submission chosen at the last read. Times are UTC. A watch
is alive while "exit" is null and "next" is not more than a few minutes past; a file with "exit" null and "next"
long past is a watch that died.

As a watched background job. A submission can take hours, so the watch runs outside the turn, as a job of the
autocode plugin's guard, which writes down its exit code and fires a one-shot wake with it. Set the wake first
(autocode_wake action=set, trigger "fire-only once"), then start the job; --deadline is required, and a little
longer than --limit lets the watch end by itself first:

    python3 <pack>/plugins/autocode/tools/guard.py job start --name <n> --wake <w> --deadline <d> -- \\
        python3 <pack>/plugins/kaggle/tools/kaggle_watch.py <slug> --ref <REF> --limit <MINUTES>

(a shell line, with a pipe or a redirect, goes after the -- as `sh -c '...'`). This tool never calls autocode
itself. Stdlib only.
"""

import argparse
import hashlib
import json
import os
import re
import signal
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

NAME = "kaggle_watch"
ISO = "%Y-%m-%dT%H:%M:%SZ"
SLUG_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")
EXIT_SCORED, EXIT_FAILED, EXIT_USAGE, EXIT_ERRORED, EXIT_LIMIT, EXIT_NOT_LISTED = 0, 1, 2, 3, 4, 5
EXIT_STOPPED = 143
EVERY, MIN_EVERY = 180.0, 20.0
LIMIT_MINUTES = 240.0
# a failed read doubles the wait up to this (never below --every)
BACKOFF_MAX = 900.0
# a submission missing from a clean read is read again after at most this
CONFIRM = 30.0
READ_TIMEOUT = 180
PAGE = 100
# Kaggle's states that mean "not judged yet"; COMPLETE is scored, anything else failed
WAITING = ("", "PENDING", "RUNNING", "QUEUED")
RATE_RE = re.compile(r"\b429\b|too many requests", re.I)


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
        print(f"{NAME}: {d}: more than one ai-pack ({', '.join(p.name for p in packs)})", file=sys.stderr)
        sys.exit(EXIT_USAGE)
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
    """The project root (a folder with an ai-pack) or ad-hoc task folder around start, else None."""
    chain = walk_up(Path(start or os.getcwd()).resolve())
    for d in chain:
        if pack_of(d):
            return d
    for d in chain:
        notes = d / "notes.md"
        if notes.is_file() and "ad-hoc-task" in notes.read_text(errors="replace")[:4096]:
            return d
    # a copied overlay sits at <project>/<pack>/plugins/kaggle/tools/kaggle_watch.py
    up = Path(__file__).resolve().parents
    if len(up) > 4 and up[2].name == "plugins" and walk_up(up[4]) and is_pack(up[3]) and pack_of(up[4]) == up[3]:
        return up[4]
    return None


def iso(t):
    return time.strftime(ISO, time.gmtime(t))


def one_line(text, width=300):
    return " ".join(str(text or "").split())[:width]


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


def read_submissions(gateway, slug, cwd):
    """One read of the team's newest submissions: (rows, None, False), or (None, why, rate-limited) for a read
    that failed."""
    cmd = [sys.executable, str(gateway), "--sdk", "submissions", slug, "--page-size", str(PAGE), "--json"]
    try:
        p = subprocess.run(cmd, cwd=str(cwd), capture_output=True, text=True, encoding="utf-8", errors="replace",
                           timeout=READ_TIMEOUT, env={**os.environ, "KAGGLE_SHARE_QUIET": "1"})
    except subprocess.TimeoutExpired:
        return None, f"timed out after {READ_TIMEOUT} s", False
    except OSError as e:
        return None, f"cannot run the gateway: {e.strerror or type(e).__name__}", False
    if p.returncode != 0:
        why = one_line((p.stderr or p.stdout).strip()[-300:]) or "no output"
        return None, f"exit {p.returncode}: {why}", bool(RATE_RE.search(p.stderr + p.stdout))
    try:
        doc = json.loads(p.stdout[p.stdout.index("{"):])
        rows = doc["submissions"]
        if not isinstance(rows, list) or not all(isinstance(r, dict) for r in rows):
            raise ValueError("no list of submissions")
    except (ValueError, KeyError, TypeError):
        return None, "the read gave no JSON list of submissions", False
    return rows, None, False


def matches(rows, ref, message):
    """The rows that are the submission watched: the one with the ref, else every row whose description is the
    message, the newest first (by the date Kaggle gives; where dates are equal or missing, as Kaggle lists them,
    which is newest first)."""
    if ref is not None:
        return [r for r in rows if str(r.get("ref")) == ref][:1]
    want = " ".join(str(message or "").split())
    found = [(i, r) for i, r in enumerate(rows) if " ".join(str(r.get("description") or "").split()) == want]
    found.sort(key=lambda ir: (str(ir[1].get("date") or ""), -ir[0]), reverse=True)
    return [r for _i, r in found]


def state_of(sub):
    # SubmissionStatus.COMPLETE and COMPLETE both read as COMPLETE
    return str(sub.get("status") or "").strip().rsplit(".", 1)[-1].upper()


def watch(read, ref, message, every, limit, beat, say, sleep=time.sleep, now=time.time, confirm=None):
    """Poll until the submission is scored or errored, the limit passes, or it is not listed: (exit code, line)."""
    confirm = CONFIRM if confirm is None else confirm
    deadline = now() + limit
    who = ref if ref is not None else f'the submission "{one_line(message, 80)}"'
    fails = missing = polls = 0
    said = last = None
    how = ""
    failed = "no read worked"

    def end(code, state, line, **more):
        beat(**{**fields, "state": state, "next": None, "exit": code, **more})
        return code, line

    while True:
        rows, why, limited = read()
        t = now()
        polls += 1
        fields = {"polls": polls, "checked": iso(t), "error": None}
        if rows is None:
            # a read that failed says nothing about the submission: it never counts toward "not listed"
            fails += 1
            state, failed = ("rate-limited" if limited else "read failed"), f"the last read failed ({why})"
            wait = max(every, min(every * 2 ** min(fails, 20), BACKOFF_MAX))
            fields["error"] = why
        else:
            fails, failed = 0, None
            found = matches(rows, ref, message)
            waiting = [r for r in found if state_of(r) in WAITING]
            if ref is None and len(waiting) > 1:
                refs = ", ".join(str(r.get("ref")) for r in waiting)
                return end(EXIT_USAGE, "ambiguous", f'{len(waiting)} submissions with the description '
                           f'"{one_line(message, 80)}" are still pending (refs {refs}): name one with --ref')
            if not found:
                missing += 1
                if missing >= 2:
                    return end(EXIT_NOT_LISTED, "not listed", f"{who} is not listed: two clean reads did not show "
                               f"it among the team's newest {PAGE} submissions")
                state, wait = "not listed", min(every, confirm)
            else:
                missing, sub = 0, found[0]
                if ref is None:
                    # chosen again at every read: the newest with the description, named with its date
                    who = str(sub.get("ref")) if sub.get("ref") is not None else who
                    how = (f" (by message: the newest of {len(found)} with this description, sent "
                           f"{one_line(sub.get('date')) or 'at a time Kaggle does not give'})")
                status, score = state_of(sub), sub.get("publicScore")
                fields.update(ref=sub.get("ref") if ref is None else ref, status=status or None, public_score=score)
                if status == "COMPLETE":
                    return end(EXIT_SCORED, "scored", f"{who} {one_line(score) or '-'} COMPLETE{how}")
                if status not in WAITING:
                    text = one_line(sub.get("errorDescription"), 500) or "Kaggle gave no error text"
                    return end(EXIT_ERRORED, "errored", f"{who} {status}: {text}{how}", error=text)
                state, wait, last = "pending", every, (status or "PENDING", t)
        if t >= deadline:
            seen = f"{last[0]} at {iso(last[1])}" if last else "never seen"
            return end(EXIT_LIMIT, "limit", f"{who} still pending after {limit / 60:g} min: "
                       f"{failed or 'the last read was clean'}; last status {seen}{how}")
        wait = min(wait, deadline - t)
        beat(**{**fields, "state": state, "next": iso(t + wait)})
        if state != said:
            said = state
            say(f"{iso(t)} {who}: {state}" + (f" ({why})" if rows is None else ""))
        sleep(wait)


def positive(text):
    try:
        v = float(text)
    except ValueError:
        v = 0
    if not v > 0 or v != v or v == float("inf"):
        raise argparse.ArgumentTypeError(f"not a number above 0: {text}")
    return v


def parse(argv):
    p = argparse.ArgumentParser(prog="kaggle_watch.py", description="Wait until one Kaggle submission is scored or "
                                "fails: polls the team's submissions, read-only, and exits with a code that says "
                                "how it ended (0 scored, 3 errored, 4 the limit passed, 5 not listed).")
    p.add_argument("slug", help="competition slug")
    which = p.add_mutually_exclusive_group(required=True)
    which.add_argument("--ref", help="the submission's ref")
    which.add_argument("--message", metavar="TEXT", help="the newest submission with exactly this description")
    p.add_argument("--every", type=positive, default=EVERY, metavar="SECONDS",
                   help=f"time between two reads (default {EVERY:g}, at least {MIN_EVERY:g})")
    p.add_argument("--limit", type=positive, default=LIMIT_MINUTES, metavar="MINUTES",
                   help=f"how long to wait in all (default {LIMIT_MINUTES:g})")
    p.add_argument("--name", help="the heartbeat file's name (default: the ref, or message-<hash>)")
    p.add_argument("--gateway", help="the gateway (default: the kaggle.py beside this file)")
    a = p.parse_args(argv)
    if not SLUG_RE.fullmatch(a.slug):
        p.error(f"not a competition slug: {a.slug}")
    if a.every < MIN_EVERY:
        p.error(f"--every is at least {MIN_EVERY:g} seconds")
    if a.ref is not None and not SLUG_RE.fullmatch(a.ref):
        p.error(f"not a submission ref: {a.ref}")
    if a.message is not None and not a.message.strip():
        p.error("--message is empty")
    if a.name is None:
        a.name = a.ref if a.ref is not None else "message-" + hashlib.sha256(
            " ".join(a.message.split()).encode()).hexdigest()[:10]
    if not SLUG_RE.fullmatch(a.name):
        p.error(f"not a name: {a.name} (letters, digits, . _ -)")
    return a


def main(argv=None):
    a = parse(argv)
    root = find_root()
    if root is None:
        print(f"{NAME}: no project or task folder here: run it from one", file=sys.stderr)
        return EXIT_USAGE
    gateway = Path(a.gateway).resolve() if a.gateway else Path(__file__).resolve().parent / "kaggle.py"
    if not gateway.is_file():
        print(f"{NAME}: gateway not found: {gateway} (pass --gateway)", file=sys.stderr)
        return EXIT_USAGE
    path = root / "__data" / "kaggle" / a.slug / "watch" / f"{a.name}.json"
    started = time.time()
    doc = {"slug": a.slug, "ref": a.ref, "message": a.message, "pid": os.getpid(), "host": socket.gethostname(),
           "started": iso(started), "limit_at": iso(started + a.limit * 60), "every": a.every, "polls": 0,
           "checked": None, "next": iso(started), "state": "pending", "status": None, "public_score": None,
           "error": None, "exit": None}

    def beat(**fields):
        doc.update(fields)
        write_atomic(path, json.dumps(doc, indent=1) + "\n")

    def say(text):
        print(f"{NAME}: {text}", file=sys.stderr, flush=True)

    def stopped(signum, _frame):
        # the guard's `job stop`, or a shutdown: the heartbeat says so
        beat(state="stopped", next=None, exit=EXIT_STOPPED)
        say(f"stopped by signal {signum}")
        sys.exit(EXIT_STOPPED)

    try:
        beat()
        for sig in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
            signal.signal(sig, stopped)
        code, line = watch(lambda: read_submissions(gateway, a.slug, root), a.ref, a.message, a.every,
                           a.limit * 60, beat, say)
    except Exception as e:  # nothing above is expected to fail: say it in one line, as an error of the tool's own
        print(f"{NAME}: the watch itself failed ({type(e).__name__}: {one_line(e)})", file=sys.stderr)
        return EXIT_FAILED
    print(line, flush=True)
    return code


if __name__ == "__main__":
    sys.exit(main())
