"""Shared fixtures for the offline tests of kaggle_presubmit.py, kaggle_submit.py and kaggle_hourly.py.

A project folder with the plugin's tools copied in as a copied install (<project>/aipack/plugins/kaggle/tools/), so
they find their project from their own place and kaggle_share.py never scans a real Solaris tree, and a stand-in
gateway that answers each call from a table and logs it. The tools run for real, as subprocesses where they call
one another; nothing reaches Kaggle.
"""

import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
import zlib
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

sys.dont_write_bytecode = True  # keep __pycache__ out of the plugin folder
TOOLS = Path(__file__).resolve().parents[1] / "shared" / "tools"
SLUG = "demo-competition"
# what makes a folder an ai-pack, whatever its name: a manifest with framework_version and a project object
PACK_MANIFEST = json.dumps({"framework_version": "0.39.0", "project": {"name": "demo"}})
# answers a call from responses.json beside it (the longest key the call starts with, after the delay it names, as a
# real read takes seconds), logs it to calls.jsonl with kaggle_submit.py's gateway mark, and writes the files an answer
# names into the -p folder, those matching --file-pattern when one is given
STANDIN = """\
import json, os, re, sys, time
here = os.path.dirname(os.path.abspath(__file__))
args = sys.argv[1:]
with open(os.path.join(here, "calls.jsonl"), "a") as f:
    f.write(json.dumps({"args": args, "quiet": os.environ.get("KAGGLE_SHARE_QUIET"),
                        "gated": os.environ.get("KAGGLE_SUBMIT_GATED")}) + "\\n")
with open(os.path.join(here, "responses.json")) as f:
    table = json.load(f)
call = " ".join(args)
keys = [k for k in table if call == k or call.startswith(k + " ")]
if not keys:
    sys.stderr.write("stand-in gateway: no answer for: " + call + "\\n")
    sys.exit(2)
r = table[max(keys, key=len)]
time.sleep(r.get("sleep", 0))
pattern = args[args.index("--file-pattern") + 1] if "--file-pattern" in args else ""
for name, text in (r.get("write") or {}).items():
    if re.search(pattern, name):
        path = os.path.join(args[args.index("-p") + 1], name)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            f.write(text)
sys.stdout.write(r.get("out", ""))
sys.stderr.write(r.get("err", ""))
sys.exit(r.get("code", 0))
"""


# one clock for every fixture: a comment keeps its time from one answer to the next, as on Kaggle
NOW = datetime.now(timezone.utc).replace(microsecond=0)


def utc(hours_ago=0.0):
    return NOW - timedelta(hours=hours_ago)


def kaggle_time(hours_ago):
    # as Kaggle's reads print a time: naive UTC with a fraction
    return utc(hours_ago).strftime("%Y-%m-%dT%H:%M:%S.250000")


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class Project(unittest.TestCase):
    """A project with the tools copied in, a stand-in gateway and helpers that set what Kaggle answers."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp()).resolve()
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.root = self.tmp / "proj"
        (self.root / "aipack").mkdir(parents=True)
        (self.root / "aipack" / "manifest.json").write_text(PACK_MANIFEST)
        self.tools = self.root / "aipack" / "plugins" / "kaggle" / "tools"
        self.tools.mkdir(parents=True)
        for f in TOOLS.iterdir():
            if f.suffix in (".py", ".js"):
                shutil.copyfile(f, self.tools / f.name)
        # the forum watch waits a second between topic reads, to spare Kaggle; the stand-in needs no wait
        forum = self.tools / "kaggle_forum.py"
        forum.write_text(forum.read_text().replace("\nPAUSE = 1.0\n", "\nPAUSE = 0.0\n", 1))
        gw = self.tmp / "gw"
        gw.mkdir()
        self.gateway = gw / "kaggle.py"
        self.gateway.write_text(STANDIN)
        self.share = self.tmp / "share"
        self.env = {k: v for k, v in os.environ.items() if not k.startswith("KAGGLE_")}
        self.env.update(KAGGLE_SHARE_DIR=str(self.share), PYTHONDONTWRITEBYTECODE="1")
        patcher = mock.patch.dict(os.environ, self.env, clear=True)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.table = {}
        self.forum([])
        self.pages({"rules": "Rule 1.\nRule 2.\n"})
        self.notebooks([("alice/base", 0.80)])
        self.board([("Alpha", 0.90), ("Beta", 0.85)])

    # ---- what the stand-in answers

    def answer(self, key, out="", code=0, err="", write=None, sleep=0):
        self.table[key] = {"out": out, "code": code, "err": err, "write": write or {}, "sleep": sleep}
        (self.gateway.parent / "responses.json").write_text(json.dumps(self.table, indent=1))

    def forum(self, topics):
        """topics: [{id, title, posted (hours ago), author, post, comments: [(author, hours ago, text, replies)]}]."""
        def comment(i, c):
            author, ago, text, *rest = c
            return {"id": i, "authorName": author, "postDate": kaggle_time(ago), "votes": 0,
                    "content": f"<p>{text}</p>",
                    "replies": [comment(i * 10 + n, r) for n, r in enumerate(rest[0] if rest else [], 1)]}

        def count(cs):
            return sum(1 + count(c["replies"]) for c in cs)

        rows = []
        for t in topics:
            cs = [comment(t["id"] * 100 + n, c) for n, c in enumerate(t.get("comments", []), 1)]
            n = t.get("listed", count(cs))
            rows.append({"id": t["id"], "title": t["title"], "authorName": "", "commentCount": n, "votes": 1,
                         "postDate": kaggle_time(t.get("posted", 1))})
            if t.get("unfetchable"):
                self.table.pop(f"--sdk topic {t['id']}", None)
            else:
                self.answer(f"--sdk topic {t['id']}", json.dumps(
                    {"topic": {"id": t["id"], "title": t["title"], "authorName": t.get("author", "Someone"),
                               "postDate": kaggle_time(t.get("posted", 1)), "votes": 1, "commentCount": count(cs),
                               "content": f"<p>{t.get('post', 'A post.')}</p>"}, "comments": cs}))
        self.answer(f"competitions topics list {SLUG} --sort-by recent --format json -p 1", json.dumps(rows))

    def pages(self, pages, code=0):
        self.answer(f"competitions pages {SLUG} list {SLUG} --content --format json",
                    json.dumps([{"name": n, "content": c} for n, c in pages.items()]), code=code)

    def notebooks(self, nbs, hours_ago=0.0, code=0):
        doc = {"competition": SLUG, "fetched_at": utc(hours_ago).strftime("%Y-%m-%dT%H:%M:%SZ"),
               "notebooks": [{"ref": ref, "title": ref.split("/")[1], "lastRunTime": kaggle_time(1), "votes": 1,
                              "score": score} for ref, score in nbs], "complete": True, "note": None}
        self.answer(f"--sdk notebooks {SLUG}", json.dumps(doc) if not code else "", code=code)

    def board(self, rows, code=0):
        self.answer(f"competitions leaderboard {SLUG} --show --format json --page-size 200", json.dumps(
            [{"teamId": zlib.crc32(name.encode()) % 10 ** 6 + 1, "teamName": name,
              "submissionDate": "2026-09-30T00:00:00",
              "score": str(score)} for name, score in rows]) if not code else "403 - Forbidden", code=code)

    def calls(self):
        log = self.gateway.parent / "calls.jsonl"
        return [json.loads(x) for x in log.read_text().splitlines()] if log.exists() else []

    # ---- running the tools

    def tool(self, name, *args, cwd=None):
        p = subprocess.run([sys.executable, str(self.tools / name), *args], cwd=cwd or self.root,
                           capture_output=True, text=True, env=self.env, timeout=120)
        return p.returncode, p.stdout, p.stderr

    def module(self, name):
        return load(self.tools / f"{name}.py", f"standin_{name}_{id(self)}")

    def store(self, *parts):
        return self.root / "__data" / "kaggle" / SLUG / Path(*parts)
