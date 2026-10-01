# rev. 4

"""kaggle_lb: leaderboard history for any Kaggle competition.

Every read of a competition leaderboard is saved as a snapshot, so each team's
progress can be followed over time. A snapshot keeps only what the leaderboard
returns: per row the rank (the row's position in Kaggle's order), team id, team
name, score and submission date - no profile lookups, nothing else scraped.

    python3 <plugin-dir>/tools/kaggle_lb.py snapshot <slug>
    python3 <plugin-dir>/tools/kaggle_lb.py show <slug> [--top N]
    python3 <plugin-dir>/tools/kaggle_lb.py history <slug> --team <name or id>
    python3 <plugin-dir>/tools/kaggle_lb.py movers <slug> --since <hours>
    python3 <plugin-dir>/tools/kaggle_lb.py new-teams <slug> --since <hours>
    python3 <plugin-dir>/tools/kaggle_lb.py summary <slug> [--top N]
    python3 <plugin-dir>/tools/kaggle_lb.py record-raw <slug> --file <path>
    python3 <plugin-dir>/tools/kaggle_lb.py import <slug> <file> [<file> ...]
    python3 <plugin-dir>/tools/kaggle_lb.py notebooks <slug> [--max N]

Run it from the project root or task folder. Only `snapshot`, `show` and
`notebooks` call Kaggle, read-only, through the gateway (--gateway; default the
kaggle.py beside this file); the other commands read saved snapshots. Snapshots
live in <context>/__data/kaggle/<slug>/leaderboard/ - one gzip JSON file each
plus index.jsonl - or in <base>/<slug>/ with --dir <base> or
KAGGLE_LB_DIR=<base>. They are never overwritten; an incomplete read is kept,
flagged "partial". `notebooks` saves the competition's public notebooks the
same way, from the gateway's `--sdk notebooks` read: per notebook its ref
(owner/slug), title, last run time, votes and best public score (null when
unknown), in Kaggle's score order. Each list is one gzip JSON file in the
notebooks/ folder beside leaderboard/ (<base>/<slug>/notebooks/ with --dir),
flagged "incomplete" when the read says so; the command prints the list and
marks new notebooks and score changes since the previous list.
tee_leaderboard() is the gateway's hook: it passes a raw `competitions
leaderboard` call through unchanged and saves the read - what a --show printed,
and the zip a --download wrote (-p <folder>, $KAGGLE_PATH, or the working
folder). With no slug given, the competition is the one the CLI names on its
"Using competition:" line, else $KAGGLE_COMPETITION; no credential or config
file is ever read, so a --quiet read of the CLI's configured default, or a
download sent to its configured folder, is not saved (the hook says so).
Stdlib only.
"""

from __future__ import annotations

import argparse
import csv
import gzip
import io
import json
import os
import re
import subprocess
import sys
import time
import zipfile
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

try:
    import fcntl
except ImportError:  # no flock on this platform: writes go unlocked
    fcntl = None

SCHEMA = 1
PAGE_SIZE = 200
MAX_PAGES = 500
PAGE_PAUSE = 0.5
INDEX = "index.jsonl"
ENV_DIR = "KAGGLE_LB_DIR"
ENV_RECORD = "KAGGLE_LB_RECORD"
ISO = "%Y-%m-%dT%H:%M:%SZ"
MAX_CSV_BYTES = 200 * 1024 * 1024
SLUG_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")
# the CLI prints this on stdout ahead of the rows, even with --format json
TOKEN_RE = re.compile(r"^\s*next page token\s*[=:]\s*(\S+)\s*$", re.I | re.M)
# a downloaded board names its CSV with the UTC time: <slug>-publicleaderboard-2026-09-27T22:54:20.csv
STAMP_RE = re.compile(r"(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d)")
# with no slug given, the CLI prints this first (not under --quiet) for its configured default competition
USING_RE = re.compile(r"^Using competition: (\S+)[ \t]*$", re.M)
# the CLI's own environment overrides of its config: the default competition and the download folder
ENV_COMPETITION = "KAGGLE_COMPETITION"
ENV_PATH = "KAGGLE_PATH"
# stored fields by source column name (lowercased, letters only); other columns are dropped
FIELDS = {
    "teamid": "team_id", "teamname": "team_name", "score": "score",
    "submissiondate": "submission_date", "lastsubmissiondate": "submission_date",
}
# stored fields of one notebook in the gateway's --sdk notebooks read; others are dropped
NOTEBOOK_FIELDS = ("ref", "title", "lastRunTime", "votes", "score")


class LeaderboardError(Exception):
    pass


# ---- time and values

def utc_now():
    return datetime.now(timezone.utc).strftime(ISO)


def when(iso):
    return datetime.strptime(iso, ISO).replace(tzinfo=timezone.utc)


def local(iso, fmt="%b %d %H:%M %Z"):
    # shown in this machine's time zone
    return when(iso).astimezone().strftime(fmt)


def iso_arg(text):
    """A given time (ISO 8601; no offset means UTC) as stored UTC text."""
    t = datetime.fromisoformat(text.strip().replace("Z", "+00:00"))
    if t.tzinfo is None:
        t = t.replace(tzinfo=timezone.utc)
    return t.astimezone(timezone.utc).strftime(ISO)


def score_value(score):
    try:
        return float(score)
    except (TypeError, ValueError):
        return None


def check_slug(slug):
    if not SLUG_RE.fullmatch(slug or ""):
        raise LeaderboardError(f"not a competition slug: {slug!r}")
    return slug


# ---- storage

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
        raise LeaderboardError(f"{d}: more than one ai-pack ({', '.join(p.name for p in packs)})")
    return packs[0] if packs else None


def walk_up(d):
    """d and the folders above it, stopping before the home folder, any folder above it, or /."""
    home = Path(os.path.expanduser("~"))
    homes = (home, home.resolve()) if home.is_absolute() else ()
    chain = []
    for f in (d, *d.parents):
        # never read $HOME or above: on macOS ~/Desktop can raise privacy prompts, and an automounted /home is slow
        if f.parent == f or any(f == h or f in h.parents for h in homes):
            break
        chain.append(f)
    return chain


def find_root(start=None):
    """The project root (a folder with an ai-pack, see pack_of) or ad-hoc task folder around start, else None."""
    chain = walk_up(Path(start or os.getcwd()).resolve())
    for d in chain:
        if pack_of(d):
            return d
    for d in chain:
        notes = d / "notes.md"
        if notes.is_file() and "ad-hoc-task" in notes.read_text(errors="replace")[:4096]:
            return d
    # a copied overlay sits at <project>/<pack>/plugins/kaggle/tools/kaggle_lb.py; a project root at home or above
    # is never listed (walk_up is empty there)
    up = Path(__file__).resolve().parents
    if len(up) > 4 and up[2].name == "plugins" and walk_up(up[4]) and is_pack(up[3]) and pack_of(up[4]) == up[3]:
        return up[4]
    return None


def store_dir(slug, directory=None, root=None, kind="leaderboard"):
    """The folder holding this competition's saved reads of one kind: leaderboard or notebooks."""
    check_slug(slug)
    base = directory or os.environ.get(ENV_DIR)
    if base:
        # the board's snapshots sit in <base>/<slug>/ itself
        d = Path(base).expanduser() / slug
        return d if kind == "leaderboard" else d / kind
    root = root or find_root()
    if root is None:
        raise LeaderboardError("no project or task folder here - run from one, or pass --dir / set KAGGLE_LB_DIR")
    return Path(root) / "__data" / "kaggle" / slug / kind


@contextmanager
def locked(d):
    # one writer at a time per store: the gateway hook, a scheduled snapshot and agents can overlap
    with open(Path(d) / ".lock", "a") as f:
        if fcntl:
            try:
                fcntl.flock(f, fcntl.LOCK_EX)
            except OSError:
                pass  # no flock on this filesystem (e.g. NFS): go unlocked
        yield


def read_snapshot(path):
    with gzip.open(path, "rt", encoding="utf-8") as f:
        return json.load(f)


def index_entry(name, snap):
    rows = snap["rows"]
    top = rows[0]["score"] if rows and rows[0].get("rank") == 1 else None
    return {"file": name, "fetched_at": snap["fetched_at"], "rows": snap["row_count"], "pages": snap["pages"],
            "partial": snap["partial"], "imported": snap["imported"], "source": snap["source"], "top_score": top}


def _append_index(d, entry):
    with open(Path(d) / INDEX, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def _write_new(d, stem, obj):
    # gzip JSON as <stem>.json.gz, else <stem>-2.json.gz and on: never overwriting; called holding the lock
    data = gzip.compress(json.dumps(obj, ensure_ascii=False, separators=(",", ":")).encode("utf-8"), mtime=0)
    n = 1
    while True:
        path = d / (f"{stem}.json.gz" if n == 1 else f"{stem}-{n}.json.gz")
        try:
            with open(path, "xb") as f:
                f.write(data)
            return path
        except FileExistsError:
            n += 1


def save_snapshot(directory, snap):
    """Write one snapshot under a new file name (never overwriting) and add it to the index."""
    d = Path(directory)
    d.mkdir(parents=True, exist_ok=True)
    tags = [snap["fetched_at"].replace("-", "").replace(":", "")]
    if snap["imported"]:
        tags.append("imported")
    if snap["partial"]:
        tags.append("partial")
    with locked(d):
        path = _write_new(d, "-".join(tags), snap)
        _append_index(d, index_entry(path.name, snap))
    return path


def load_index(directory):
    """Index entries sorted by fetch time; snapshot files the index lacks are read and added to it."""
    d = Path(directory)
    if not d.is_dir():
        return []
    entries, seen = [], set()
    with locked(d):
        if (d / INDEX).is_file():
            for line in (d / INDEX).read_text(encoding="utf-8").splitlines():
                try:
                    e = json.loads(line)
                except ValueError:
                    continue  # a torn line from an interrupted write
                if isinstance(e, dict) and e.get("file") not in seen and (d / str(e.get("file"))).is_file():
                    seen.add(e["file"])
                    entries.append(e)
        for p in sorted(d.glob("*.json.gz")):
            if p.name in seen:
                continue
            try:
                e = index_entry(p.name, read_snapshot(p))
            except (OSError, ValueError, EOFError, KeyError, TypeError):
                continue  # not a readable snapshot: leave it out
            _append_index(d, e)
            seen.add(p.name)
            entries.append(e)
    # stable: reads saved in the same second keep their saving order
    return sorted(entries, key=lambda e: e["fetched_at"])


# ---- parsing

def _key(name):
    return re.sub(r"[^a-z]", "", str(name).lower())


def normalize(item):
    """One leaderboard row from any source, cut down to the stored fields."""
    row = {"rank": None, "team_id": None, "team_name": None, "score": None, "submission_date": None}
    for k, v in item.items():
        field = FIELDS.get(_key(k))
        if not field:
            continue
        if isinstance(v, str) and field != "team_name" and v.strip() in ("", "None"):
            v = None  # table and csv output print a missing value as None or nothing
        row[field] = v
    tid = row["team_id"]
    if isinstance(tid, str) and tid.strip().isdigit():
        row["team_id"] = int(tid)
    return row


def _table(header, border, body):
    # the dashed line under the header gives each column's span
    starts = [m.start() for m in re.finditer(r"-+", border)]
    spans = list(zip(starts, starts[1:] + [None]))
    names = [header[a:b].strip() for a, b in spans]
    return [dict(zip(names, (line[a:b].strip() for a, b in spans))) for line in body if line.strip()]


def parse_output(text):
    """(rows, next page token, format, rows dropped) of one leaderboard response.

    Reads the CLI's json, csv and table output, skipping notices before the data, and a downloaded
    board's CSV. Rows come back in board order without ranks; rows lacking a numeric team id are dropped.
    """
    text = text.lstrip("\ufeff")
    m = TOKEN_RE.search(text)
    token = m.group(1) if m else None
    lines = text.splitlines()
    items, fmt = None, None
    for i, line in enumerate(lines):
        s = line.strip()
        if s.startswith(("[", "{")):
            try:
                data, _ = json.JSONDecoder().raw_decode("\n".join(lines[i:]).strip())
            except ValueError:
                continue  # a notice in brackets, not the data
            items = data.get("submissions", []) if isinstance(data, dict) else data
            fmt = "json"
            break
        if "," in s:
            cols = [_key(c) for c in next(csv.reader([s]))]
            if "teamid" in cols:
                items = list(csv.DictReader(io.StringIO("\n".join(lines[i:]))))
                fmt = "download-csv" if "rank" in cols else "csv"
                break
        nxt = lines[i + 1] if i + 1 < len(lines) else ""
        if ("teamid" in _key(s) or "teamname" in _key(s)) and re.fullmatch(r"[- ]*-[- ]*", nxt.rstrip()):
            items, fmt = _table(line, nxt, lines[i + 2:]), "table"
            break
        if s == "No results found":
            items, fmt = [], "empty"
            break
    if items is None or not isinstance(items, list):
        raise LeaderboardError("no leaderboard rows in this output")
    rows = [normalize(x) for x in items if isinstance(x, dict)]
    kept = [r for r in rows if isinstance(r["team_id"], int)]
    return kept, token, fmt, len(items) - len(kept)


def build_snapshot(slug, pages, *, fetched_at, source, fmt, partial=False, first_rank=1, imported=False,
                   note=None, source_file=None, seconds=None):
    """A snapshot from pages of parsed rows in board order; rank is the 1-based position (None if unknown)."""
    rows, seen, pos, dupes = [], set(), 0, 0
    for page in pages:
        for r in page:
            pos += 1
            if r["team_id"] in seen:
                dupes += 1  # the board moved between page reads
                continue
            seen.add(r["team_id"])
            rows.append(dict(r, rank=first_rank + pos - 1 if first_rank else None))
    snap = {"schema": SCHEMA, "slug": slug, "fetched_at": fetched_at, "source": source, "format": fmt,
            "pages": len(pages), "row_count": len(rows), "partial": bool(partial), "imported": bool(imported)}
    extras = {"note": note, "source_file": source_file, "seconds": seconds, "duplicates": dupes or None}
    snap.update({k: v for k, v in extras.items() if v is not None})
    snap["rows"] = rows
    return snap


# ---- reading Kaggle

def _run(cmd, cwd):
    # KAGGLE_SHARE_QUIET: a monitoring read, so kaggle_share's activity stamp skips it
    # KAGGLE_LB_RECORD=0: this tool saves the whole board itself, so the gateway's tee must not save each page
    try:
        p = subprocess.run(cmd, cwd=str(cwd), stdout=subprocess.PIPE, text=True, encoding="utf-8",
                           errors="replace", timeout=300,
                           env={**os.environ, "KAGGLE_SHARE_QUIET": "1", ENV_RECORD: "0"})
    except subprocess.TimeoutExpired:
        return 124, "timed out"
    return p.returncode, p.stdout


def fetch_board(slug, gateway, root, *, page_size=PAGE_SIZE, max_pages=MAX_PAGES, run=_run, pause=PAGE_PAUSE):
    """Every page of the leaderboard through the gateway: (pages, partial, note)."""
    pages, token, tokens, notes = [], None, set(), []
    while True:
        args = ["competitions", "leaderboard", slug, "--show", "--format", "json", "--page-size", str(page_size)]
        if token:
            args += ["--page-token", token]
        code, out = run([sys.executable, str(gateway), *args], root)
        try:
            if code != 0:
                raise LeaderboardError(f"exit {code}: {out.strip()[-400:]}")
            rows, token, _fmt, dropped = parse_output(out)
        except LeaderboardError as e:
            if not pages:
                raise LeaderboardError(f"leaderboard read failed: {e}") from None
            notes.append(f"page {len(pages) + 1} failed ({str(e).splitlines()[0][:80]})")
            break
        pages.append(rows)
        if dropped:
            notes.append(f"page {len(pages)}: {dropped} rows without a team id")
        if not token:
            return pages, bool(notes), "; ".join(notes) or None
        if token in tokens or not rows:
            notes.append("pagination stalled")
            break
        if len(pages) >= max_pages:
            notes.append(f"stopped after {max_pages} pages")
            break
        tokens.add(token)
        time.sleep(pause)
    return pages, True, "; ".join(notes)


def take_snapshot(slug, *, gateway=None, directory=None, root=None, page_size=PAGE_SIZE, max_pages=MAX_PAGES,
                  run=_run, pause=PAGE_PAUSE):
    """Read the whole leaderboard and save it; returns (path, snapshot)."""
    check_slug(slug)
    root = Path(root) if root else find_root()
    d = store_dir(slug, directory, root)
    gw = find_gateway(gateway)
    t0, fetched_at = time.time(), utc_now()
    pages, partial, note = fetch_board(slug, gw, root or Path.cwd(), page_size=page_size, max_pages=max_pages,
                                       run=run, pause=pause)
    snap = build_snapshot(slug, pages, fetched_at=fetched_at, source="snapshot", fmt="json", partial=partial,
                          note=note, seconds=round(time.time() - t0, 1))
    return save_snapshot(d, snap), snap


def find_gateway(gateway=None):
    gw = Path(gateway).resolve() if gateway else Path(__file__).resolve().parent / "kaggle.py"
    if not gw.is_file():
        raise LeaderboardError(f"gateway not found: {gw} (pass --gateway)")
    return gw


def _known(v, *types):
    # null, or one of types (a bool counts as no number)
    return v is None or (isinstance(v, types) and not isinstance(v, bool))


def check_notebooks(slug, doc):
    """A --sdk notebooks read checked against its contract, each notebook cut down to NOTEBOOK_FIELDS."""
    try:
        when(doc["fetched_at"])
        nbs = [{k: n.get(k) for k in NOTEBOOK_FIELDS} for n in doc["notebooks"]]
        good = (doc["competition"] == slug and isinstance(doc["complete"], bool) and _known(doc.get("note"), str)
                and all(isinstance(n["ref"], str) and n["ref"].count("/") == 1 and _known(n["title"], str)
                        and _known(n["lastRunTime"], str) and _known(n["votes"], int)
                        and _known(n["score"], int, float) for n in nbs))
    except (KeyError, TypeError, ValueError, AttributeError):
        good = False
    if not good:
        raise LeaderboardError("notebooks read failed: the gateway's output is not a --sdk notebooks read; "
                               "nothing saved")
    return {"competition": slug, "fetched_at": doc["fetched_at"], "notebooks": nbs, "complete": doc["complete"],
            "note": doc.get("note")}


def fetch_notebooks(slug, gateway, root, *, max_n=None, run=_run):
    """The competition's public notebooks with their best public scores, through the gateway's --sdk read."""
    cmd = [sys.executable, str(gateway), "--sdk", "notebooks", slug]
    if max_n is not None:
        cmd += ["--max", str(max_n)]
    # stderr passes through: on a failure it carries the read's error line
    code, out = run(cmd, root)
    if code != 0:
        tail = out.strip()[-400:]
        raise LeaderboardError(f"notebooks read failed (exit {code}{': ' + tail if tail else ''}); nothing saved")
    try:
        doc = json.loads(out)
    except ValueError:
        doc = None
    return check_notebooks(slug, doc)


def _last_list(slug, d):
    # the newest readable notebooks list in d, or None: file names start with the UTC time
    for p in sorted(d.glob("*.json.gz"), reverse=True):
        try:
            return check_notebooks(slug, read_snapshot(p))
        except (OSError, ValueError, EOFError, LeaderboardError):
            continue
    return None


def take_notebooks(slug, *, gateway=None, directory=None, root=None, max_n=None, run=_run):
    """Read the public notebooks and save the list; returns (path, list, the previous list or None)."""
    check_slug(slug)
    root = Path(root) if root else find_root()
    d = store_dir(slug, directory, root, kind="notebooks")
    doc = fetch_notebooks(slug, find_gateway(gateway), root or Path.cwd(), max_n=max_n, run=run)
    d.mkdir(parents=True, exist_ok=True)
    stem = doc["fetched_at"].replace("-", "").replace(":", "") + ("" if doc["complete"] else "-incomplete")
    with locked(d):
        prev = _last_list(slug, d)
        path = _write_new(d, stem, doc)
    return path, doc, prev


def notebook_changes(prev, doc):
    """{lowercased ref: mark}: "new" when the previous list lacks it, else "old -> new" for a changed score."""
    was = {n["ref"].lower(): n["score"] for n in prev["notebooks"]}
    marks = {}
    for n in doc["notebooks"]:
        k = n["ref"].lower()
        if k not in was:
            marks[k] = "new"
        # null is unknown: no change is marked from or to it
        elif None not in (was[k], n["score"]) and was[k] != n["score"]:
            marks[k] = f"{was[k]} -> {n['score']}"
    return marks


# ---- saving reads made elsewhere

class _QuietParser(argparse.ArgumentParser):
    # raise instead of printing usage: the gateway must stay silent on arguments it only inspects
    def error(self, message):
        raise LeaderboardError(message)


def _lb_args(argv):
    """Options of a `competitions leaderboard` call, or None for any other command."""
    argv = [str(a) for a in argv]
    if len(argv) < 2 or argv[0] not in ("competitions", "c") or argv[1] != "leaderboard":
        return None
    if "-h" in argv or "--help" in argv:
        return None
    p = _QuietParser(add_help=False)
    p.add_argument("competition", nargs="?")
    p.add_argument("-c", "--competition", dest="competition_opt")
    p.add_argument("-s", "--show", dest="view", action="store_true")
    p.add_argument("-d", "--download", action="store_true")
    p.add_argument("-p", "--path")
    p.add_argument("-v", "--csv", action="store_true")
    p.add_argument("--format")
    p.add_argument("-q", "--quiet", action="store_true")
    p.add_argument("--page-size")
    p.add_argument("--page-token")
    try:
        ns, _rest = p.parse_known_args(argv[2:])
    except (LeaderboardError, argparse.ArgumentError, SystemExit):
        return None
    return ns


def leaderboard_slug(argv):
    """The competition of a `competitions leaderboard <slug> --show` call, else None."""
    ns = _lb_args(argv)
    if not ns or not ns.view:
        return None
    slug = ns.competition or ns.competition_opt
    return slug if slug and SLUG_RE.fullmatch(slug) else None


def leaderboard_read(argv):
    """The options of a `competitions leaderboard` call that shows or downloads the board (slug optional), else None."""
    ns = _lb_args(argv)
    if not ns or not (ns.view or ns.download):
        return None
    slug = ns.competition or ns.competition_opt
    return None if slug and not SLUG_RE.fullmatch(slug) else ns


def record_raw(slug, text, *, argv=None, later_page=False, fetched_at=None, directory=None, root=None,
               source="record-raw"):
    """Save one leaderboard response already fetched (any CLI output format); returns the snapshot path.

    A response with a next-page token is only part of the board, and one from a --page-token call has
    unknown ranks: both are saved flagged partial.
    """
    ns = _lb_args(argv) if argv is not None else None
    later_page = later_page or bool(ns and ns.page_token)
    rows, token, fmt, dropped = parse_output(text)
    notes = [n for n, on in (("more pages not fetched", token), ("a later page: ranks unknown", later_page),
                             (f"{dropped} rows without a team id", dropped)) if on]
    snap = build_snapshot(slug, [rows], fetched_at=fetched_at or utc_now(), source=source, fmt=fmt,
                          partial=bool(notes), first_rank=None if later_page else 1, note="; ".join(notes) or None)
    return save_snapshot(store_dir(slug, directory, root), snap)


def _download_target(ns, slug):
    """(folder, file pattern) of the zip a `leaderboard --download` writes; slug None matches any competition.

    -p <folder>, else $KAGGLE_PATH/competitions/<slug>/, else the working folder. A download folder set
    in the CLI's config file is never read, so a zip sent there is not found.
    """
    s = slug or "*"
    if ns.path:
        return Path(ns.path), f"{s}.zip"
    if os.environ.get(ENV_PATH):
        return Path(os.environ[ENV_PATH]) / "competitions", f"{s}/{s}.zip"
    return Path.cwd(), f"{s}.zip"


def _stat(path):
    # the CLI dates a download by the server's time, so a fresh one is told by any change, not by its mtime
    try:
        st = Path(path).stat()
    except OSError:
        return None
    return st.st_ino, st.st_size, st.st_mtime_ns, st.st_ctime_ns


def _zips_before(ns, slug):
    """{path: stat} of the zips a --download may overwrite, taken before the CLI runs; never raises."""
    try:
        folder, pattern = _download_target(ns, slug)
        return {p: _stat(p) for p in folder.glob(pattern)}
    except (OSError, ValueError):
        return {}


def record_download(slug, path, *, directory=None, root=None, source="gateway"):
    """Save a board the CLI downloaded (the `leaderboard --download` zip or its CSV) as a read.

    Timed by the UTC stamp in the CSV name. Returns the snapshot path, or None when that board file
    was saved before (a download or an import of it).
    """
    name, text = _board_file(Path(path))
    rows, token, fmt, dropped = parse_output(text)
    stamp = STAMP_RE.search(name)
    at = stamp.group(1) + "Z" if stamp else utc_now()
    d = store_dir(slug, directory, root)
    snap = build_snapshot(slug, [rows], fetched_at=at, source=source, fmt=fmt, partial=bool(token or dropped),
                          note=f"{dropped} rows without a team id" if dropped else None, source_file=name)
    if any(e["fetched_at"] == at and e["rows"] == snap["row_count"] and e.get("source") in (source, "import")
           for e in load_index(d)):
        return None
    return save_snapshot(d, snap)


def _save_read(ns, slug, text, argv, root, before):
    """Save what a leaderboard call just read: the --show output and the --download zip; notes go to stderr."""
    first = "\n".join(text.splitlines()[:5])
    m = USING_RE.search(first)
    slug = slug or (m.group(1) if m else None) or os.environ.get(ENV_COMPETITION)
    if not slug or not SLUG_RE.fullmatch(slug):
        print("kaggle_lb: leaderboard read not saved: no competition named (pass the slug; the CLI's default "
              "competition shows only without --quiet)", file=sys.stderr)
        return
    if ns.view:
        try:
            path = record_raw(slug, text, argv=argv, root=root, source="gateway")
            print(f"kaggle_lb: leaderboard read saved to {path}", file=sys.stderr)
        except Exception as e:
            print(f"kaggle_lb: leaderboard read not saved: {e}", file=sys.stderr)
    if ns.download:
        try:
            folder, pattern = _download_target(ns, slug)
            z = folder / pattern
            if not z.is_file() or before.get(z) == _stat(z):
                raise LeaderboardError(f"no new {z.name} in {folder} (a download folder set in the CLI's config "
                                       "is not read: pass -p <folder>)")
            path = record_download(slug, z, root=root)
            print(f"kaggle_lb: leaderboard download saved to {path}" if path
                  else "kaggle_lb: leaderboard download was saved before", file=sys.stderr)
        except Exception as e:
            print(f"kaggle_lb: leaderboard download not saved: {e}", file=sys.stderr)


def tee_leaderboard(cmd, argv, root):
    """Gateway hook: run `cmd + argv`; for a leaderboard read, pass stdout through and save the read.

    A --show read is saved from what the CLI printed, a --download from the zip it wrote. Returns None
    (the gateway runs everything else as before) or the CLI's exit code. Saving never changes the
    output or the exit code; KAGGLE_LB_RECORD=0 turns it off.
    """
    ns = leaderboard_read(argv)
    if not ns or os.environ.get(ENV_RECORD, "1") == "0":
        return None
    slug = ns.competition or ns.competition_opt
    before = _zips_before(ns, slug) if ns.download else {}
    proc = subprocess.Popen([*cmd, *argv], stdout=subprocess.PIPE)
    out = sys.stdout.buffer
    chunks = []
    for chunk in iter(lambda: proc.stdout.read1(65536), b""):
        out.write(chunk)
        out.flush()
        chunks.append(chunk)
    code = proc.wait()
    if code == 0:
        try:
            _save_read(ns, slug, b"".join(chunks).decode("utf-8", "replace"), argv, root, before)
        except Exception as e:  # a failed save must never fail the read
            print(f"kaggle_lb: leaderboard read not saved: {e}", file=sys.stderr)
    return code


def _board_file(path):
    """(name, text) of a downloaded board: the CSV itself or the CSV inside Kaggle's zip."""
    if path.suffix.lower() != ".zip":
        return path.name, path.read_text(encoding="utf-8-sig", errors="replace")
    with zipfile.ZipFile(path) as z:
        members = [m for m in z.infolist() if m.filename.lower().endswith(".csv")]
        if not members:
            raise LeaderboardError(f"no CSV inside {path}")
        if members[0].file_size > MAX_CSV_BYTES:
            raise LeaderboardError(f"CSV inside {path} is too large")
        return Path(members[0].filename).name, z.read(members[0]).decode("utf-8-sig", errors="replace")


def _mtime(path):
    return datetime.fromtimestamp(Path(path).stat().st_mtime, timezone.utc).strftime(ISO)


def import_files(slug, files, *, fetched_at=None, directory=None, root=None):
    """Backfill earlier reads as snapshots flagged imported; returns the saved paths.

    Each downloaded board (.zip or .csv) is one snapshot, timed by the UTC stamp in its CSV name (else
    the file time). Other files are saved CLI output, taken as consecutive pages of one read in the
    order given and timed by the earliest file. A read already imported at that time is skipped.
    """
    d = store_dir(slug, directory, root)
    files = [Path(f) for f in files]
    boards = [f for f in files if f.suffix.lower() in (".zip", ".csv")]
    pages = [f for f in files if f not in boards]
    reads = []
    for f in boards:
        name, text = _board_file(f)
        rows, token, fmt, dropped = parse_output(text)
        stamp = STAMP_RE.search(name)
        at = fetched_at or (stamp.group(1) + "Z" if stamp else _mtime(f))
        reads.append(([rows], at, fmt, bool(token or dropped), name))
    if pages:
        parsed = [parse_output(f.read_text(encoding="utf-8", errors="replace")) for f in pages]
        # whole only when every page but the last points to a next one
        partial = (any(p[3] for p in parsed) or any(not p[1] for p in parsed[:-1]) or bool(parsed[-1][1]))
        at = fetched_at or min(_mtime(f) for f in pages)
        reads.append(([p[0] for p in parsed], at, parsed[0][2], partial, pages[0].name))
    done = {e["fetched_at"] for e in load_index(d) if e.get("imported")}
    saved = []
    for pgs, at, fmt, partial, name in reads:
        if at in done:
            continue
        snap = build_snapshot(slug, pgs, fetched_at=at, source="import", fmt=fmt, partial=partial, imported=True,
                              source_file=name)
        saved.append(save_snapshot(d, snap))
        done.add(at)
    return saved


# ---- reading progress

def _full(entries):
    return [e for e in entries if not e["partial"]]


def _baseline(full, cutoff):
    """The newest full snapshot at or before cutoff, else the oldest; and whether it fell back."""
    before = [e for e in full if when(e["fetched_at"]) <= cutoff]
    return (before[-1], False) if before else (full[0], True)


def direction(rows):
    """+1 when a higher score ranks better on this board, -1 when lower does."""
    vals = [v for v in (score_value(r["score"]) for r in rows) if v is not None]
    return -1 if len(vals) > 1 and vals[0] < vals[-1] else 1


def movers(d, since_hours, top=10, now=None):
    """Largest score improvements and rank climbs from the board since_hours ago to the latest full one."""
    full = _full(load_index(d))
    if len(full) < 2:
        return None
    base, fallback = _baseline(full, (now or datetime.now(timezone.utc)) - timedelta(hours=since_hours))
    last = full[-1]
    if base["file"] == last["file"]:
        return {"base": base, "last": last, "fallback": fallback, "gains": [], "climbs": [], "improved": 0,
                "sign": 1}
    old = {r["team_id"]: r for r in read_snapshot(Path(d) / base["file"])["rows"]}
    rows = read_snapshot(Path(d) / last["file"])["rows"]
    sign = direction(rows)
    moves = []
    for r in rows:
        o = old.get(r["team_id"])
        if not o:
            continue
        a, b = score_value(o["score"]), score_value(r["score"])
        change = b - a if a is not None and b is not None else None
        climb = o["rank"] - r["rank"] if o["rank"] and r["rank"] else None
        moves.append(dict(r, was=o["rank"], was_score=o["score"], change=change, climb=climb,
                          better=sign * change if change is not None else None))
    gains = sorted((m for m in moves if m["better"] and m["better"] > 1e-12), key=lambda m: (-m["better"], m["rank"]))
    climbs = sorted((m for m in moves if m["climb"] and m["climb"] > 0), key=lambda m: (-m["climb"], m["rank"]))
    return {"base": base, "last": last, "fallback": fallback, "gains": gains[:top], "climbs": climbs[:top],
            "improved": len(gains), "sign": sign}


def new_teams(d, since_hours, now=None):
    """Teams on the latest full board that the board since_hours ago lacked, with when each first showed."""
    full = _full(load_index(d))
    if not full:
        return None
    base, fallback = _baseline(full, (now or datetime.now(timezone.utc)) - timedelta(hours=since_hours))
    last = full[-1]
    known = {r["team_id"] for r in read_snapshot(Path(d) / base["file"])["rows"]}
    first_seen = {}
    for e in full[full.index(base) + 1:]:
        for r in read_snapshot(Path(d) / e["file"])["rows"]:
            if r["team_id"] not in known:
                first_seen.setdefault(r["team_id"], e["fetched_at"])
    rows = read_snapshot(Path(d) / last["file"])["rows"] if base is not last else []
    new = [(first_seen.get(r["team_id"], last["fetched_at"]), r) for r in rows if r["team_id"] not in known]
    gone = len(known - {r["team_id"] for r in rows}) if rows else 0
    return {"base": base, "last": last, "fallback": fallback, "new": new, "gone": gone}


def resolve_team(d, entries, query):
    """(team id, current name) for a team name, part of one, or id; the newest full board is searched first."""
    q = query.strip()
    ordered = list(reversed(entries))
    full = _full(ordered)
    for scope in ((full or ordered)[:1], ordered):
        names = {}
        for e in scope:
            for r in read_snapshot(Path(d) / e["file"])["rows"]:
                names.setdefault(r["team_id"], r["team_name"] or "")
        if q.isdigit() and int(q) in names:
            return int(q), names[int(q)]
        hits = [t for t, n in names.items() if n.casefold() == q.casefold()]
        hits = hits or [t for t, n in names.items() if q.casefold() in n.casefold()]
        if len(hits) == 1:
            return hits[0], names[hits[0]]
        if hits:
            listed = "\n".join(f"  {t}  {names[t]}" for t in hits[:20])
            raise LeaderboardError(f"{len(hits)} teams match {query!r} - pass the team id:\n{listed}")
    raise LeaderboardError(f"no team matches {query!r}")


def team_history(d, query):
    """(team id, name, [(entry, row or None)]); a partial snapshot without the team is left out."""
    entries = load_index(d)
    if not entries:
        return None
    tid, name = resolve_team(d, entries, query)
    out = []
    for e in entries:
        row = next((r for r in read_snapshot(Path(d) / e["file"])["rows"] if r["team_id"] == tid), None)
        if row or not e["partial"]:
            out.append((e, row))
    return tid, name, out


def _pick(items, k):
    # up to k items spread evenly, always the first and the last
    if len(items) <= k:
        return list(items)
    return [items[round(i * (len(items) - 1) / (k - 1))] for i in range(k)]


def summarize(d, top=10, columns=6):
    entries = load_index(d)
    if not entries:
        return None
    full = _full(entries)
    cols = _pick(full, columns)
    snaps = [read_snapshot(Path(d) / e["file"])["rows"] for e in cols]
    return {"entries": entries, "full": full, "cols": cols, "snaps": snaps, "leaders": snaps[-1][:top] if snaps else []}


# ---- output

def _name(n, width=28):
    n = n or ""
    return n if len(n) <= width else n[:width - 1] + "~"


def _date(s):
    return (s or "")[:16].replace("T", " ")


def _change(change, *scores):
    # a score change with as many decimals as the board shows
    places = max((len(str(s).split(".")[1]) for s in scores if "." in str(s)), default=4)
    return f"{change:+.{places}f}"


def _rel(p):
    try:
        return os.path.relpath(p)
    except ValueError:
        return str(p)


def cmd_snapshot(a):
    path, snap = take_snapshot(a.slug, gateway=a.gateway, directory=a.dir, page_size=a.page_size,
                               max_pages=a.max_pages)
    state = f"partial: {snap.get('note')}" if snap["partial"] else "full"
    print(f"{a.slug}: {snap['row_count']} rows, {snap['pages']} pages ({state}), {local(snap['fetched_at'])}; "
          f"saved {_rel(path)}")
    return path, snap


def cmd_show(a):
    path, snap = cmd_snapshot(a)
    prev = [e for e in _full(load_index(path.parent)) if e["file"] != path.name
            and e["fetched_at"] <= snap["fetched_at"]]
    before = {}
    if prev:
        before = {r["team_id"]: r["rank"] for r in read_snapshot(path.parent / prev[-1]["file"])["rows"]}
        print(f"move: since {local(prev[-1]['fetched_at'])}")
    print(f"{'rank':>5}  {'move':>5}  {'score':<9} {'submitted (UTC)':<17} team")
    for r in snap["rows"][:a.top]:
        move = ""
        if prev:
            was = before.get(r["team_id"])
            move = "new" if was is None else ("=" if was == r["rank"] else f"{was - r['rank']:+d}")
        print(f"{r['rank'] or '?':>5}  {move:>5}  {str(r['score']):<9} {_date(r['submission_date']):<17} "
              f"{_name(r['team_name'], 40)}")
    return 0


def cmd_history(a):
    d = store_dir(a.slug, a.dir)
    res = team_history(d, a.team)
    if res is None:
        print(f"no snapshots yet in {_rel(d)}")
        return 1
    tid, name, rows = res
    print(f"{name} (team {tid}) on {a.slug}: {sum(1 for _, r in rows if r)} of {len(rows)} snapshots")
    print(f"{'fetched':<17} {'rank':>5}  {'score':<9} {'submitted (UTC)':<17} flags")
    for e, r in rows:
        flags = " ".join(f for f in ("partial", "imported") if e[f])
        if r:
            renamed = f" (as {r['team_name']})" if r["team_name"] != name else ""
            print(f"{local(e['fetched_at']):<17} {r['rank'] or '?':>5}  {str(r['score']):<9} "
                  f"{_date(r['submission_date']):<17} {flags}{renamed}")
        else:
            print(f"{local(e['fetched_at']):<17} {'-':>5}  {'-':<9} {'(not on the board)':<17} {flags}")
    return 0


def cmd_movers(a):
    d = store_dir(a.slug, a.dir)
    res = movers(d, a.since, a.top)
    if res is None:
        print(f"movers need two full snapshots; see summary for {_rel(d)}")
        return 1
    base, last = res["base"], res["last"]
    hours = (when(last["fetched_at"]) - when(base["fetched_at"])).total_seconds() / 3600
    note = " (history is shorter than the window)" if res["fallback"] else ""
    order = "higher" if res["sign"] > 0 else "lower"
    print(f"{a.slug}: {local(base['fetched_at'])} -> {local(last['fetched_at'])} ({hours:.1f} h){note}; "
          f"{order} scores rank better; {res['improved']} teams improved")
    if base["file"] == last["file"]:
        print("no full snapshot newer than the window start")
        return 0
    print("Score improvements:" if res["gains"] else "Score improvements: none")
    for m in res["gains"]:
        print(f"  rank {m['rank']:>5} (was {m['was']:>5})  {str(m['score']):<9} "
              f"({_change(m['change'], m['score'], m['was_score'])} from {m['was_score']})  "
              f"{_name(m['team_name'], 40)}")
    print("Rank climbs:" if res["climbs"] else "Rank climbs: none")
    for m in res["climbs"]:
        print(f"  rank {m['rank']:>5} (was {m['was']:>5}, {m['climb']:+d})  {str(m['score']):<9}  "
              f"{_name(m['team_name'], 40)}")
    return 0


def cmd_new_teams(a):
    d = store_dir(a.slug, a.dir)
    res = new_teams(d, a.since)
    if res is None:
        print(f"no full snapshots yet in {_rel(d)}")
        return 1
    base, last = res["base"], res["last"]
    note = " (history is shorter than the window)" if res["fallback"] else ""
    print(f"{a.slug}: {len(res['new'])} new, {res['gone']} gone between {local(base['fetched_at'])} "
          f"({base['rows']} rows) and {local(last['fetched_at'])} ({last['rows']} rows){note}")
    for seen, r in res["new"]:
        print(f"  first seen {local(seen):<17} rank {r['rank']:>5}  {str(r['score']):<9} "
              f"{_name(r['team_name'], 40)} ({r['team_id']})")
    return 0


def cmd_summary(a):
    d = store_dir(a.slug, a.dir)
    res = summarize(d, a.top)
    if res is None:
        print(f"no snapshots yet in {_rel(d)}")
        return 1
    ents, full, cols, snaps = res["entries"], res["full"], res["cols"], res["snaps"]
    span = (when(ents[-1]["fetched_at"]) - when(ents[0]["fetched_at"])).total_seconds() / 3600
    print(f"{a.slug}: {len(ents)} snapshots ({len(full)} full, {len(ents) - len(full)} partial; "
          f"{sum(1 for e in ents if e['imported'])} imported) from {local(ents[0]['fetched_at'])} "
          f"to {local(ents[-1]['fetched_at'])} ({span:.1f} h) in {_rel(d)}")
    if not cols:
        return 0
    tz = when(cols[-1]["fetched_at"]).astimezone().strftime("%Z")
    print(f"Top {len(res['leaders'])} of the latest full snapshot: rank and score over time ({tz})")
    print(f"  {'team':<28}" + "".join(f"{local(e['fetched_at'], '%m-%d %H:%M'):>14}" for e in cols))
    maps = [{r["team_id"]: r for r in rows} for rows in snaps]
    for lead in res["leaders"]:
        cells = []
        for m in maps:
            r = m.get(lead["team_id"])
            cells.append(f"{r['rank'] or '?':>5} {str(r['score']):<8}" if r else f"{'-':>5} {'':<8}")
        print(f"  {_name(lead['team_name']):<28}" + "".join(f"{c:>14}" for c in cells))
    print(f"  {'(rows)':<28}" + "".join(f"{len(rows):>14}" for rows in snaps))
    for n in (1, 10, 100):
        print(f"  {'(#' + str(n) + ' score)':<28}"
              + "".join(f"{str(rows[n - 1]['score']) if len(rows) >= n else '-':>14}" for rows in snaps))
    return 0


def cmd_record_raw(a):
    text = sys.stdin.read() if a.file == "-" else Path(a.file).read_text(encoding="utf-8", errors="replace")
    path = record_raw(a.slug, text, later_page=a.later_page, fetched_at=a.fetched_at, directory=a.dir)
    print(f"saved {_rel(path)}")
    return 0


def cmd_import(a):
    saved = import_files(a.slug, a.files, fetched_at=a.fetched_at, directory=a.dir)
    for p in saved:
        s = read_snapshot(p)
        state = "partial" if s["partial"] else "full"
        print(f"imported {s['row_count']} rows ({state}) as of {local(s['fetched_at'])}: {_rel(p)}")
    if not saved:
        print("nothing new to import")
    return 0


def cmd_notebooks(a):
    path, doc, prev = take_notebooks(a.slug, gateway=a.gateway, directory=a.dir, max_n=a.max)
    nbs = doc["notebooks"]
    state = "complete" if doc["complete"] else "incomplete" + (f": {doc['note']}" if doc["note"] else "")
    print(f"{a.slug}: {len(nbs)} notebooks, {sum(n['score'] is not None for n in nbs)} with a public score "
          f"({state}), {local(doc['fetched_at'])}; saved {_rel(path)}")
    marks = notebook_changes(prev, doc) if prev else {}
    if prev:
        new = sum(m == "new" for m in marks.values())
        print(f"since {local(prev['fetched_at'])}: {new} new, {len(marks) - new} with a changed score")
    else:
        print("no earlier list: changes show from the next read")
    # a blank score is unknown: none yet, or one the read could not get
    scores = ["" if n["score"] is None else str(n["score"]) for n in nbs]
    sw, rw = max([5, *map(len, scores)]), max([3, *(len(n["ref"]) for n in nbs)])
    print(f"{'score':<{sw}}  {'ref':<{rw}}  {'votes':>5}  {'last run (UTC)':<16}  change")
    for s, n in zip(scores, nbs):
        votes = "" if n["votes"] is None else n["votes"]
        print(f"{s:<{sw}}  {n['ref']:<{rw}}  {votes:>5}  {_date(n['lastRunTime']):<16}  "
              f"{marks.get(n['ref'].lower(), '')}".rstrip())
    return 0


def main(argv=None):
    p = argparse.ArgumentParser(prog="kaggle_lb.py", description="Leaderboard history for a Kaggle "
                                "competition: every read is saved as a snapshot.")
    sub = p.add_subparsers(dest="cmd", required=True)
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("slug", help="competition slug")
    common.add_argument("--dir", help="store base: snapshots go to <dir>/<slug>/, notebook lists to "
                        "<dir>/<slug>/notebooks/ (also KAGGLE_LB_DIR); default "
                        "<project>/__data/kaggle/<slug>/leaderboard/ and .../notebooks/")
    gw = argparse.ArgumentParser(add_help=False)
    gw.add_argument("--gateway", help="the kaggle.py gateway to call (default: the one beside this file)")
    fetch = argparse.ArgumentParser(add_help=False, parents=[gw])
    fetch.add_argument("--page-size", type=int, default=PAGE_SIZE)
    fetch.add_argument("--max-pages", type=int, default=MAX_PAGES)
    since = argparse.ArgumentParser(add_help=False)
    since.add_argument("--since", type=float, required=True, help="hours back")
    sub.add_parser("snapshot", parents=[common, fetch], help="read the whole leaderboard and save it")
    s = sub.add_parser("show", parents=[common, fetch], help="save a snapshot, then print the top rows")
    s.add_argument("--top", type=int, default=20)
    s = sub.add_parser("history", parents=[common], help="one team's rank and score across snapshots")
    s.add_argument("--team", required=True, help="team name, part of one, or team id")
    s = sub.add_parser("movers", parents=[common, since], help="largest score improvements and rank climbs")
    s.add_argument("--top", type=int, default=10)
    sub.add_parser("new-teams", parents=[common, since], help="teams that joined the board")
    s = sub.add_parser("summary", parents=[common], help="snapshots, time span and the top-N trajectory")
    s.add_argument("--top", type=int, default=10)
    s = sub.add_parser("record-raw", parents=[common], help="save a leaderboard response already fetched")
    s.add_argument("--file", required=True, help="saved CLI output; - reads stdin")
    s.add_argument("--later-page", action="store_true", help="it came from a --page-token call (ranks unknown)")
    s.add_argument("--fetched-at", type=iso_arg, help="when it was read, ISO 8601 (default now)")
    s = sub.add_parser("import", parents=[common], help="backfill earlier dumps as imported snapshots")
    s.add_argument("files", nargs="+", help="downloaded board .zip/.csv files, or saved CLI pages in page order")
    s.add_argument("--fetched-at", type=iso_arg, help="when they were read, ISO 8601 (default from the files)")
    s = sub.add_parser("notebooks", parents=[common, gw], help="save the public notebooks with their public "
                       "scores (in <slug>/notebooks/), then show them and what changed")
    s.add_argument("--max", type=int, help="notebooks to list in Kaggle's score order, 1..1000 (default 100)")
    a = p.parse_args(argv)
    commands = {"snapshot": cmd_snapshot, "show": cmd_show, "history": cmd_history, "movers": cmd_movers,
                "new-teams": cmd_new_teams, "summary": cmd_summary, "record-raw": cmd_record_raw,
                "import": cmd_import, "notebooks": cmd_notebooks}
    try:
        res = commands[a.cmd](a)
    except LeaderboardError as e:
        print(f"kaggle_lb: {e}", file=sys.stderr)
        return 1
    return res if isinstance(res, int) else 0


if __name__ == "__main__":
    sys.exit(main())
