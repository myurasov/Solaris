# rev. 1

"""kaggle_scout: which Kaggle competitions are open, and a first look at one.

Lists the competitions whose deadline is still ahead, read-only, through the gateway: for each its ref (the
slug), the whole days left, the category, the reward, the number of teams, and whether this account has entered.

    python3 <plugin-dir>/tools/kaggle_scout.py [--json] [--min-days N] [--detail SLUG ...] [--no-list]

  --min-days N       only competitions with at least N whole days left (default 0: every open one)
  --detail SLUG ...  also, for each competition named: the top of its public leaderboard and its data files
  --no-list          with --detail: skip the list, read only the details
  --json             one JSON document instead of the table
  --gateway PATH     the gateway (default: the kaggle.py beside this file)

It works wherever the gateway does: in a project, in an ad-hoc task folder and at the framework root. It writes
nothing: no file, no activity stamp (KAGGLE_SHARE_QUIET=1) and no saved leaderboard read (KAGGLE_LB_RECORD=0).

The list is the union of eight reads, `competitions list --group <general|entered> --sort-by <latestDeadline|
recentlyCreated|prize|earliestDeadline> --page-size 200 --format json`: Kaggle returns at most 200 competitions
a read, most of them closed, so four sort orders of each group are merged, and a row is kept when its deadline is
in the future. "entered" is yes when Kaggle marks the row so or when the entered group lists it. A read that
fails is named in a note, and the list is built from the rest. Each --detail competition takes two reads:
`competitions leaderboard <slug> --show --csv` (the first page: the top 5 teams and the number of rows read) and
`competitions files <slug> --csv` (the number of files and the first 10 names with their sizes). Kaggle refuses
the files of a competition whose rules the account has not accepted; that shows as "not readable".

The table is sorted by days left, soonest first. With --json: {"fetched_at", "reads", "failed": ["<read>:
<why>"], "competitions": [{"ref", "url", "deadline", "days_left", "category", "reward", "teams", "entered"}],
"details": {"<slug>": {"board": {"rows", "top": [{"rank", "team", "score"}]} or {"error"}, "files": {"count",
"names": ["<name> (<size>)"]} or {"error"}}}}; times are UTC.

Exit codes: 0 read (failed reads are noted), 1 no read worked, 2 bad usage. Stdlib only.
"""

import argparse
import csv
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

NAME = "kaggle_scout"
ISO = "%Y-%m-%dT%H:%M:%SZ"
SLUG_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")
GROUPS = ("general", "entered")
SORTS = ("latestDeadline", "recentlyCreated", "prize", "earliestDeadline")
PAGE = 200
READ_TIMEOUT = 300
TOP, FILES = 5, 10


def one_line(text, width=200):
    return " ".join(str(text or "").split())[:width]


def call(gateway, args):
    """One read through the gateway: (stdout, None), or (None, why) when it failed."""
    env = {**os.environ, "KAGGLE_SHARE_QUIET": "1", "KAGGLE_LB_RECORD": "0"}
    try:
        p = subprocess.run([sys.executable, str(gateway), *args], capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=READ_TIMEOUT, env=env)
    except subprocess.TimeoutExpired:
        return None, f"timed out after {READ_TIMEOUT} s"
    except OSError as e:
        return None, f"cannot run the gateway: {e.strerror or type(e).__name__}"
    if p.returncode != 0:
        # the gateway's own notes come first on stderr: the reason is at the end
        return None, f"exit {p.returncode}: {one_line((p.stderr or p.stdout).strip()[-200:]) or 'no output'}"
    return p.stdout, None


def parse_time(value):
    """A Kaggle time (naive UTC, with T or a space, with or without a fraction or Z) as an aware UTC time."""
    try:
        t = datetime.fromisoformat(str(value).strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    return t if t.tzinfo else t.replace(tzinfo=timezone.utc)


def truthy(value):
    return value is True or str(value).strip().lower() == "true"


def count(value):
    try:
        return int(str(value).replace(",", "").strip())
    except ValueError:
        return None


def list_rows(text):
    """The rows of one `competitions list --format json` read; the CLI may print a notice before the list."""
    start = text.find("[")
    if start < 0:
        if not text.strip() or "no competitions" in text.lower():
            return []
        raise ValueError("no JSON list")
    rows = json.loads(text[start:])
    if not isinstance(rows, list):
        raise ValueError("no JSON list")
    return [r for r in rows if isinstance(r, dict)]


def slug_of(ref):
    return str(ref or "").strip().rstrip("/").rsplit("/", 1)[-1]


def read_list(gateway, now):
    """(open competitions by slug, reads made, failed reads) from the eight list reads."""
    found, failed, reads = {}, [], 0
    for group in GROUPS:
        for sort in SORTS:
            reads += 1
            out, why = call(gateway, ["competitions", "list", "--group", group, "--sort-by", sort,
                                      "--page-size", str(PAGE), "--format", "json"])
            if why is None:
                try:
                    rows = list_rows(out)
                except ValueError:
                    why = "the read gave no JSON list"
            if why is not None:
                failed.append(f"{group}/{sort}: {why}")
                continue
            for r in rows:
                ref = r.get("ref") or r.get("url")
                slug, deadline = slug_of(ref), parse_time(r.get("deadline"))
                if not SLUG_RE.fullmatch(slug) or deadline is None or deadline <= now:
                    continue
                c = found.setdefault(slug, {
                    "ref": slug,
                    "url": str(ref) if str(ref).startswith("http") else f"https://www.kaggle.com/competitions/{slug}",
                    "deadline": deadline.strftime(ISO), "days_left": int((deadline - now).total_seconds() // 86400),
                    "category": one_line(r.get("category"), 40), "reward": one_line(r.get("reward"), 40),
                    "teams": count(r.get("teamCount")), "entered": False})
                c["entered"] = c["entered"] or group == "entered" or truthy(r.get("userHasEntered"))
    return found, reads, failed


def csv_rows(text, column):
    """The rows of a CSV the CLI printed, from the header line that has this column; notices before it are
    skipped."""
    lines = text.splitlines()
    heads = [i for i, ln in enumerate(lines) if column.lower() in [c.strip().lower() for c in ln.split(",")]]
    if not heads:
        if not text.strip() or re.search(r"\bno (results|files|entries|teams)\b", text, re.I):
            return []
        raise ValueError(f"no CSV with a {column} column")
    rows = list(csv.DictReader(lines[heads[0]:]))
    return [{str(k).strip().lower(): v for k, v in r.items() if k is not None} for r in rows]


def read_detail(gateway, slug):
    """The top of the public board and the data files of one competition; each part an {"error"} when its read
    failed."""
    detail = {}
    out, why = call(gateway, ["competitions", "leaderboard", slug, "--show", "--csv"])
    if why is None:
        try:
            rows = csv_rows(out, "teamName")
            detail["board"] = {"rows": len(rows), "top": [
                {"rank": i, "team": one_line(r.get("teamname"), 60), "score": one_line(r.get("score"), 30)}
                for i, r in enumerate(rows[:TOP], 1)]}
        except ValueError as e:
            why = str(e)
    if why is not None:
        detail["board"] = {"error": why}
    out, why = call(gateway, ["competitions", "files", slug, "--csv"])
    if why is None:
        try:
            rows = csv_rows(out, "name")
            detail["files"] = {"count": len(rows), "names": [
                one_line(r.get("name"), 80) + (f" ({one_line(r.get('size'), 20)})" if r.get("size") else "")
                for r in rows[:FILES]]}
        except ValueError as e:
            why = str(e)
    if why is not None:
        detail["files"] = {"error": why}
    return detail


def table(comps):
    width = max([len(c["ref"]) for c in comps] + [3])
    out = [f"{'ref':<{width}}  {'days':>4}  {'category':<16}  {'reward':<14}  {'teams':>6}  entered"]
    for c in comps:
        teams = "-" if c["teams"] is None else str(c["teams"])
        out.append(f"{c['ref']:<{width}}  {c['days_left']:>4}  {c['category'][:16]:<16}  {c['reward'][:14]:<14}  "
                   f"{teams:>6}  {'yes' if c['entered'] else 'no'}")
    return out


def detail_lines(slug, d):
    out = [f"== {slug}"]
    board, files = d["board"], d["files"]
    if "error" in board:
        out.append(f"board: not readable ({board['error']})")
    else:
        top = "; ".join(f"{t['rank']}. {t['team']} {t['score']}" for t in board["top"]) or "no team yet"
        out.append(f"board ({board['rows']} rows read): {top}")
    if "error" in files:
        out.append(f"files: not readable ({files['error']})")
    else:
        more = f"; and {files['count'] - len(files['names'])} more" if files["count"] > len(files["names"]) else ""
        out.append(f"files ({files['count']}): {'; '.join(files['names']) or 'none'}{more}")
    return out


def days(text):
    try:
        v = int(text)
    except ValueError:
        v = -1
    if v < 0:
        raise argparse.ArgumentTypeError(f"not a number of days: {text}")
    return v


def parse(argv):
    p = argparse.ArgumentParser(prog="kaggle_scout.py", description="The Kaggle competitions that are open (ref, "
                                "days left, category, reward, teams, entered), read-only, through the gateway.")
    p.add_argument("--min-days", type=days, default=0, metavar="N",
                   help="only competitions with at least N whole days left (default 0)")
    p.add_argument("--detail", nargs="+", default=[], metavar="SLUG",
                   help="also the top of the public board and the data files of each competition named")
    p.add_argument("--no-list", action="store_true", help="with --detail: skip the list")
    p.add_argument("--json", action="store_true", help="print one JSON document instead of the table")
    p.add_argument("--gateway", help="the gateway (default: the kaggle.py beside this file)")
    a = p.parse_args(argv)
    a.detail = [slug_of(s) for s in a.detail]
    bad = [s for s in a.detail if not SLUG_RE.fullmatch(s)]
    if bad:
        p.error(f"not a competition slug: {bad[0]}")
    if a.no_list and not a.detail:
        p.error("--no-list needs --detail")
    return a


def main(argv=None):
    a = parse(argv)
    gateway = Path(a.gateway).resolve() if a.gateway else Path(__file__).resolve().parent / "kaggle.py"
    if not gateway.is_file():
        print(f"{NAME}: gateway not found: {gateway} (pass --gateway)", file=sys.stderr)
        return 2
    now = datetime.now(timezone.utc)
    found, reads, failed = ({}, 0, []) if a.no_list else read_list(gateway, now)
    comps = sorted((c for c in found.values() if c["days_left"] >= a.min_days),
                   key=lambda c: (c["days_left"], c["ref"]))
    details = {slug: read_detail(gateway, slug) for slug in dict.fromkeys(a.detail)}
    bad = sum("error" in part for d in details.values() for part in d.values())
    worked = (reads - len(failed)) + (2 * len(details) - bad)
    if a.json:
        print(json.dumps({"fetched_at": now.strftime(ISO), "reads": reads + 2 * len(details), "failed": failed,
                          "competitions": comps, "details": details}, indent=1))
    else:
        if not a.no_list:
            least = f" with at least {a.min_days} days left" if a.min_days else ""
            print(f"Open competitions{least}: {len(comps)} ({reads} list reads, read {now.strftime(ISO)})")
            if comps:
                print("\n".join(table(comps)))
            for f in failed:
                print(f"note: a list read failed ({f}); the list is built from the other reads")
        for slug, d in details.items():
            print("\n".join(detail_lines(slug, d)))
    if not worked:
        print(f"{NAME}: no read worked" + (f" ({failed[0]})" if failed else ""), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
