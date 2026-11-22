# rev. 3

"""kaggle_submit: one gated competition submission, from a submission record.

This is a Kaggle write: `competitions submit` spends one of the day's
submissions and cannot be taken back. Run --go only with the owner's approval of
the exact command the check prints, or under the owner's standing grant (then
with a one-line heads-up).

    python3 <plugin-dir>/tools/kaggle_submit.py <record.json>                             checks; prints the command
    python3 <plugin-dir>/tools/kaggle_submit.py <record.json> --go --review "<one line>"  checks, then one submit
    python3 <plugin-dir>/tools/kaggle_submit.py <record.json> --landed <ref>              records an unclear attempt
                                                                                          as landed; no Kaggle call

The record is a JSON file kept with each submission; fields other than these
are left as they are:

    {"competition": "<slug>", "file": "<file>", "message": "<one line>",
     "kernel": "<owner>/<kernel>", "version": <N>}

In a code competition, kernel and version name the saved kernel version to
submit (the version `kernels push` printed) and file is the output file it
wrote (submission.csv, say); without kernel, file is the local file to upload,
relative to the record's folder. It refuses (exit 3) unless:

- the record is complete: competition, file and a one-line message, and with
  kernel a version;
- no earlier attempt in the record landed or may have landed: after an unclear
  one, read `competitions submissions <slug> --page-size 100` and `competitions
  submission-limits <slug>` first, and pass --checked only when it is not listed
  (an attempt Kaggle refused did not land, so it blocks nothing); when it is
  listed, record that with --landed (below);
- the pre-submit check (kaggle_presubmit.py) ran at most --fresh minutes ago
  (default 30) and a review recorded whatever it found (--ack after its last
  change);
- the kernel run finished (`kernels status <kernel>` through the gateway says
  COMPLETE; it names the latest version only), or, without kernel, the file is
  there and not empty;
- with --go, --review gives one line: what was new in the check and the decision.

--go then appends the attempt to the record's "attempts" (result
"submitting"), runs the gateway's `competitions submit` exactly once, and
records the result: "submitted" when the CLI reports success; "refused" (exit
5) when the CLI failed and the gateway names Kaggle's refusal (HTTP 400, 401,
403, 404 or 429, statuses where nothing was created, with an error message in
the body, "Your team already has 1 pending submissions", say: the gateway
reads the body's message, never a header), with that reason in the attempt's
"reason", printed as REFUSED BY KAGGLE; else "unclear" (exit 4), for any
outcome that may have landed (a timeout, a 5xx, a failed upload, any other
4xx such as 408, 409 or 499, or one of those five without Kaggle's message).
It never retries: after
an unclear result read `competitions submissions <slug> --page-size 100` and
`competitions submission-limits <slug>` before anything else; after a refusal nothing landed, so a
later --go may follow once its reason is gone (a pending submission scored,
say). The gateway refuses a `competitions submit` unless the call carries
KAGGLE_SUBMIT_GATED=1, which this tool sets on its own submit call only.

After an unclear attempt, the submissions read decides between two flags:

- Listed: a row carries the record's exact message and is dated inside the
  attempt's time window (from the attempt's "at" to a few minutes after its
  "ended"). Run --landed <ref> with that row's ref (the submission's number).
  It rewrites the record's last attempt that may have landed (result "unclear",
  or "submitting" when the run was cut off) to "submitted", with the ref in
  "ref", the result it had in "was" and the time of the change in "marked". The
  record then reads as submitted, so a later --go on it is refused, as after
  any submit. --landed makes no Kaggle call and runs none of the other checks;
  it takes no --go, --review, --checked or --fresh. It refuses (exit 3) when the
  record already holds a submitted attempt or holds none that may have landed.
- Not listed: a clean read shows no row with the exact message in that time
  window. Only then pass --checked with the next --go. A read that failed (an
  error, HTTP 429, a timeout) or came back empty is not "not listed": it says
  nothing about the attempt, so read again before either flag. A team with more
  than 20 submissions reads with `--page-size 100`, the CLI's default being the
  newest 20 rows.

One run at a time per record: a run takes a non-blocking flock on
<record>.lock before it reads the record and holds it to the end, so a second
run on the same record is refused (exit 3) while the first may be submitting;
it fails closed (exit 1) where the lock cannot be taken. The empty lock file
stays beside the record.

Run it from the project root or task folder; the gateway is --gateway, default
the kaggle.py beside this file. Exit codes: 0 submitted (without --go: every
check passed; with --landed: the attempt is recorded as landed), 1 error, 2 bad
usage, 3 refused by the checks (nothing sent; with --landed: nothing changed),
4 unclear result (may have landed), 5 refused by Kaggle (nothing landed; the
reason is printed and recorded). The only file written is the record itself,
through a temp file and a rename. Stdlib only.
"""

import argparse
import fcntl
import importlib.util
import json
import os
import re
import shlex
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

ISO = "%Y-%m-%dT%H:%M:%SZ"
SLUG_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")
REF_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*/[A-Za-z0-9][A-Za-z0-9._-]*")
# what the CLI prints after a submit that went through; "Could not submit" when the upload failed, with exit 0
SUCCESS_RE = re.compile(r"successfully submitted|submissions? remaining today", re.I)
FAILED_RE = re.compile(r"could not submit", re.I)
# the line the gateway adds when Kaggle answers a submit call with a status where nothing was created and an error
# message in the body (kaggle.py REFUSED_LINE, REFUSED_STATUSES): the status, the kind (FAILED_PRECONDITION, say) and
# Kaggle's message; any other status (408, 409, 499, ...) may have landed, so it never reads as refused
REFUSED_STATUSES = (400, 401, 403, 404, 429)
REFUSED_RE = re.compile(r"^kaggle gateway: Kaggle refused the submit \((%s)(?: ([^)]*))?\): (.+?)\s*$"
                        % "|".join(map(str, REFUSED_STATUSES)), re.M)
STATUS_RE = re.compile(r'has status "(?:KernelWorkerStatus\.)?(\w+)"')
EXIT_REFUSED, EXIT_UNCLEAR, EXIT_KAGGLE_REFUSED = 3, 4, 5
# the results of an attempt that may have landed: --landed rewrites the last such attempt to "submitted"
MAY_HAVE_LANDED = ("unclear", "submitting")
# the submissions read the messages name: the CLI's default page is the newest 20 rows
SUBMISSIONS_PAGE = 100
REVIEW_MAX = 500
TOOLS = Path(__file__).resolve().parent
# the gateway passes a `competitions submit` only with this mark, which this tool sets on its own submit call
GATE_MARK = "KAGGLE_SUBMIT_GATED"


class SubmitError(Exception):
    pass


def utc_now():
    return datetime.now(timezone.utc).replace(microsecond=0)


def iso(t):
    return t.astimezone(timezone.utc).strftime(ISO)


def tail(text, n=300):
    return " ".join(str(text or "").strip()[-n:].split()) or "no output"


def load_tool(name):
    """A tool module from this folder (kaggle_presubmit), loaded by path."""
    path = TOOLS / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"kaggle_submit_{name}", path)
    if spec is None or spec.loader is None:
        raise SubmitError(f"cannot load {path}")
    mod = importlib.util.module_from_spec(spec)
    # keep __pycache__ out of the plugin folder
    sys.dont_write_bytecode = True
    try:
        spec.loader.exec_module(mod)
    except Exception as e:
        raise SubmitError(f"cannot load {path}: {e}") from None
    return mod


def _run(cmd, cwd, submit=False, timeout=600):
    # a read carries KAGGLE_SHARE_QUIET=1, so it leaves no activity stamp; the submit itself is stamped, and only it
    # carries the gateway's mark
    env = {**os.environ, GATE_MARK: "1"} if submit else {**os.environ, "KAGGLE_SHARE_QUIET": "1"}
    try:
        p = subprocess.run(cmd, cwd=str(cwd), stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                           encoding="utf-8", errors="replace", timeout=timeout, env=env)
    except subprocess.TimeoutExpired:
        return 124, f"timed out after {timeout} s"
    return p.returncode, p.stdout


def hold(path):
    """Take <record>.lock for this run, without waiting: the open lock, kept until the run ends, or None while
    another run holds it."""
    lock = f"{path}.lock"
    try:
        fd = os.open(lock, os.O_RDWR | os.O_CREAT, 0o644)
    except OSError as e:
        raise SubmitError(f"cannot open the lock {lock}: {e}") from None
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        os.close(fd)
        return None
    except OSError as e:
        # fail closed: an unlocked run could submit a second time
        os.close(fd)
        raise SubmitError(f"cannot lock {lock} ({e}): keep the record on a local disk") from None
    return fd


def read_record(path):
    try:
        rec = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise SubmitError(f"cannot read the record {path}: {e}") from None
    if not isinstance(rec, dict):
        raise SubmitError(f"the record {path} is not a JSON object")
    return rec


def write_record(path, rec):
    # atomic replace: an interrupted write keeps the record whole
    path = Path(path)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(rec, f, indent=2, ensure_ascii=False)
            f.write("\n")
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def record_problems(rec, folder):
    """(problems, the submission: slug, file, message, kernel, version, the local file or None)."""
    probs = []
    slug, file, msg = rec.get("competition"), rec.get("file"), rec.get("message")
    kernel, version = rec.get("kernel"), rec.get("version")
    if not (isinstance(slug, str) and SLUG_RE.fullmatch(slug)):
        probs.append('"competition" is not a competition slug')
    if not (isinstance(file, str) and file.strip()):
        probs.append('"file" is missing')
    if not (isinstance(msg, str) and msg.strip()):
        probs.append('"message" is missing')
    elif "\n" in msg or "\r" in msg:
        probs.append('"message" is not one line')
    if isinstance(version, str) and version.isdigit():
        version = int(version)
    local = None
    if kernel is not None:
        if not (isinstance(kernel, str) and REF_RE.fullmatch(kernel)):
            probs.append('"kernel" is not a kernel ref owner/slug')
        if type(version) is not int or version < 1:
            probs.append('"version" is missing: the saved kernel version to submit, as `kernels push` printed it')
    elif version is not None:
        probs.append('"version" without "kernel"')
    elif isinstance(file, str) and file.strip():
        local = (Path(folder) / file).resolve()
        if not local.is_file() or local.stat().st_size == 0:
            probs.append(f'"file" {file} is not a file with content beside the record')
    return probs, (slug, file, msg.strip() if isinstance(msg, str) else msg, kernel, version, local)


def attempt_problem(rec, slug, checked):
    """Why an earlier attempt in the record blocks this one, else None."""
    attempts = rec.get("attempts", [])
    if not isinstance(attempts, list):
        return '"attempts" is not a list (this tool writes it)'
    attempts = [x for x in attempts if isinstance(x, dict)]
    done = [x for x in attempts if x.get("result") == "submitted"]
    if done:
        return f"already submitted at {done[-1].get('at')}: a new submission needs its own record"
    # an attempt Kaggle refused did not land: only one that may have landed needs --checked
    if attempts and not checked and attempts[-1].get("result") != "refused":
        last = attempts[-1]
        return (f"the attempt at {last.get('at')} ended {last.get('result')!r}, so it may have landed: read "
                f"`competitions submissions {slug} --page-size {SUBMISSIONS_PAGE}` and `competitions "
                f"submission-limits {slug}` first, and pass --checked only when it is not listed (a failed or empty "
                "read is not that); when it is listed, record it with --landed <ref>")
    return None


def mark_landed(rec, ref, now):
    """Rewrite the record's last attempt that may have landed (MAY_HAVE_LANDED) to "submitted" with the submission's
    ref: (that attempt, None), or (None, why not) with the record unchanged."""
    attempts = rec.get("attempts", [])
    if not isinstance(attempts, list):
        return None, '"attempts" is not a list (this tool writes it)'
    attempts = [x for x in attempts if isinstance(x, dict)]
    done = [x for x in attempts if x.get("result") == "submitted"]
    if done:
        return None, (f"the record already holds a submitted attempt (at {done[-1].get('at')}"
                      + (f", ref {done[-1]['ref']}" if done[-1].get("ref") else "") + "): nothing to mark")
    open_ = [x for x in attempts if x.get("result") in MAY_HAVE_LANDED]
    if not open_:
        return None, ("no attempt in the record may have landed (none ended 'unclear' or was cut off while "
                      "'submitting'): nothing to mark")
    attempt = open_[-1]
    attempt.update(was=attempt["result"], result="submitted", ref=ref, marked=iso(now))
    return attempt, None


def review_problem(review, go):
    if review is None:
        return ('--go needs --review "<one line: what was new in the pre-submit check, and the decision>"'
                if go else None)
    if "\n" in review or "\r" in review:
        return "--review must be one line"
    if not review.strip():
        return "--review is empty"
    if len(review.strip()) > REVIEW_MAX:
        return f"--review is {len(review.strip())} characters (at most {REVIEW_MAX})"
    return None


def refusal(code, out):
    """Kaggle's reason, "<status> <kind>: <message>", when the submit failed and the gateway names Kaggle's refusal
    (the last such line); else None, for an outcome that may have landed."""
    found = REFUSED_RE.findall(out or "") if code != 0 else []
    if not found:
        return None
    status, kind, message = found[-1]
    return f"{status}{' ' + kind if kind else ''}: {message}"


def kernel_status(ref, gateway, root, run):
    """(the run state `kernels status` prints, None) or (None, why it could not be read)."""
    code, out = run([sys.executable, str(gateway), "kernels", "status", ref], root)
    m = STATUS_RE.search(out or "")
    if code != 0 or not m:
        return None, f"kernels status {ref} failed (exit {code}): {tail(out)}"
    return m.group(1).upper(), None


def _shown(p, root):
    # a path for the printed command: relative to the root when inside it
    try:
        return str(Path(p).resolve().relative_to(Path(root).resolve()))
    except ValueError:
        return str(p)


def minutes_arg(text):
    try:
        v = float(text)
    except ValueError:
        v = -1.0
    if not 0 < v <= 1440:
        raise argparse.ArgumentTypeError(f"not a number of minutes from 0 to 1440: {text!r}")
    return v


def ref_arg(text):
    # a submission's ref, as `competitions submissions` lists it: its number
    if not text.isdigit() or int(text) < 1:
        raise argparse.ArgumentTypeError(f"not a submission ref (its number in `competitions submissions`): {text!r}")
    return int(text)


def main(argv=None, now=None):
    p = argparse.ArgumentParser(prog="kaggle_submit.py", description="One gated Kaggle submission from a submission "
                                "record (a Kaggle write: --go only with the owner's approval or standing grant).")
    p.add_argument("record", help="the submission record (JSON)")
    p.add_argument("--go", action="store_true", help="submit once after the checks (default: the checks only)")
    p.add_argument("--review", help="one line: what was new in the pre-submit check, and the decision (for --go)")
    p.add_argument("--checked", action="store_true", help="the earlier unclear attempt is not listed in `competitions "
                   "submissions` (read it and `competitions submission-limits` first)")
    p.add_argument("--landed", type=ref_arg, metavar="REF", help="no submit: record the last unclear attempt as "
                   "landed, with the ref `competitions submissions` lists for it (a clean read that shows the exact "
                   "message in the attempt's time window)")
    p.add_argument("--fresh", type=minutes_arg, help="the pre-submit check may be at most this many minutes old "
                   "(default 30)")
    p.add_argument("--gateway", help="the kaggle.py gateway to call (default: the one beside this file)")
    a = p.parse_args(argv)
    if a.landed is not None and (a.go or a.review is not None or a.checked or a.fresh is not None):
        p.error("--landed only records an attempt as landed: it takes no --go, --review, --checked or --fresh")
    a.fresh = 30.0 if a.fresh is None else a.fresh
    try:
        presubmit = load_tool("kaggle_presubmit")
    except SubmitError as e:
        print(f"kaggle_submit: {e}", file=sys.stderr)
        return 1
    try:
        root = presubmit.find_root()
        if root is None:
            raise SubmitError("no project or task folder here: run from one")
        gw = Path(a.gateway).resolve() if a.gateway else TOOLS / "kaggle.py"
        if a.landed is None and not gw.is_file():
            raise SubmitError(f"gateway not found: {gw} (pass --gateway)")
        path = Path(a.record).resolve()
        if not path.is_file():
            # checked before the lock, so a wrong path leaves no lock file behind
            raise SubmitError(f"cannot read the record {path}: no such file")
        lock = hold(path)
    except (SubmitError, presubmit.PresubmitError) as e:
        print(f"kaggle_submit: {e}", file=sys.stderr)
        return 1
    if lock is None:
        print(f"REFUSED:\n  - another kaggle_submit.py run holds {_shown(path, root)}.lock and may be submitting this "
              "record now: let it end, then read the record before running again")
        return EXIT_REFUSED
    try:
        if a.landed is not None:
            return landed_locked(a.landed, root, path, now or utc_now())
        return run_locked(a, presubmit, root, gw, path, now or utc_now())
    finally:
        os.close(lock)


def landed_locked(ref, root, path, now):
    """--landed under the record's lock: mark the last attempt that may have landed as submitted; no Kaggle call."""
    try:
        rec = read_record(path)
    except SubmitError as e:
        print(f"kaggle_submit: {e}", file=sys.stderr)
        return 1
    print(f"record     {_shown(path, root)}")
    attempt, why = mark_landed(rec, ref, now)
    if why:
        print(f"REFUSED:\n  - {why}")
        return EXIT_REFUSED
    try:
        write_record(path, rec)
    except OSError as e:
        print(f"kaggle_submit: cannot write the record {path}: {e}; nothing changed", file=sys.stderr)
        return 1
    print(f"MARKED LANDED: the attempt at {attempt.get('at')} (it ended {attempt['was']!r}) now reads 'submitted', "
          f"ref {ref}; recorded in {_shown(path, root)}. A new submission needs its own record")
    return 0


def run_locked(a, presubmit, root, gw, path, now):
    """One run under the record's lock: read the record, check it and, with --go, submit once."""
    try:
        # read under the lock: an attempt an earlier run wrote is in it
        rec = read_record(path)
    except SubmitError as e:
        print(f"kaggle_submit: {e}", file=sys.stderr)
        return 1
    problems, (slug, file, msg, kernel, version, local) = record_problems(rec, path.parent)
    slug_ok = isinstance(slug, str) and bool(SLUG_RE.fullmatch(slug))
    problems += [x for x in (attempt_problem(rec, slug, a.checked) if slug_ok else None,
                             review_problem(a.review, a.go)) if x]
    print(f"record     {_shown(path, root)}")
    print(f"submit     {slug}: " + (f"{kernel} version {version}, its output file {file}" if kernel else
                                    f"the file {_shown(local, root) if local else file}"))
    print(f"message    {msg or '(missing)'}")
    if a.review is not None and a.review.strip():
        print(f"review     {a.review.strip()}")
    if slug_ok:
        why, summary = presubmit.review_status(slug, root, now=now, fresh_minutes=a.fresh)
        print(f"presubmit  {summary}")
        if why:
            problems.append(why)
    if kernel and not problems:
        # read only when everything else holds: no Kaggle call for a submit that is refused anyway
        status, why = kernel_status(kernel, gw, root, _run)
        print(f"kernel     latest run {status or 'unknown'} (the status names the latest version only: make sure it is "
              f"version {version})")
        if why or status != "COMPLETE":
            problems.append(why or f"the kernel run is {status}, not COMPLETE: submit once it has finished")
    elif local and local.is_file():
        print(f"file       {local.stat().st_size:,} bytes")
    if problems:
        print("REFUSED:\n" + "\n".join(f"  - {x}" for x in problems))
        return EXIT_REFUSED
    cmd = ["competitions", "submit", slug, "-f", _shown(local, root) if local else file, "-m", msg]
    if kernel:
        cmd += ["-k", kernel, "-v", str(version)]
    shown = shlex.join(["python3", _shown(gw, root), *cmd])
    print(f"command    (from {root})\n{shown}")
    if not a.go:
        print("CHECKS PASSED: nothing submitted. A Kaggle write: with the owner's go-ahead on this command, run again "
              'with --go --review "<one line>"')
        return 0
    attempt = {"at": iso(now), "review": a.review.strip(), "command": shown, "result": "submitting"}
    if a.checked:
        attempt["checked"] = "the earlier attempt was not listed in competitions submissions"
    rec.setdefault("attempts", []).append(attempt)
    try:
        # recorded first: an interruption from here on reads as an attempt that may have landed
        write_record(path, rec)
    except OSError as e:
        print(f"kaggle_submit: cannot write the record {path}: {e}; nothing submitted", file=sys.stderr)
        return 1
    print(f"submitting at {iso(utc_now())} ...", flush=True)
    code, out = _run([sys.executable, str(gw), *cmd], root, submit=True, timeout=1800)
    print((out or "").strip())
    ok = code == 0 and bool(SUCCESS_RE.search(out or "")) and not FAILED_RE.search(out or "")
    refused = refusal(code, out) if not ok else None
    attempt.update(result="submitted" if ok else "refused" if refused else "unclear", exit=code,
                   output=tail(out, 1000), ended=iso(utc_now()))
    if refused:
        attempt["reason"] = refused
    try:
        write_record(path, rec)
    except OSError as e:
        print(f"kaggle_submit: the submit ran (exit {code}) but the record {path} could not be updated: {e}; it still "
              "reads as an attempt that may have landed", file=sys.stderr)
    if ok:
        print(f"SUBMITTED: recorded in {_shown(path, root)}")
        return 0
    if refused:
        print(f"REFUSED BY KAGGLE: {refused}\nNothing landed; recorded in {_shown(path, root)}. A later --go may "
              "follow once that reason is gone (a pending submission scored, say)")
        return EXIT_KAGGLE_REFUSED
    gw_shown = shlex.quote(_shown(gw, root))
    print(f"UNCLEAR (exit {code}): the submission may or may not have landed. Do not submit again: first read "
          f"`python3 {gw_shown} competitions submissions {slug} --page-size {SUBMISSIONS_PAGE}` and `python3 "
          f"{gw_shown} competitions submission-limits {slug}`; recorded in {_shown(path, root)}. Listed there (the "
          f"exact message, in this attempt's time window): run again with --landed <ref>. Not listed in a clean "
          f"read: --checked with the next --go. A failed or empty read decides nothing")
    return EXIT_UNCLEAR


if __name__ == "__main__":
    sys.exit(main())
