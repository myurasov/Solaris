"""Offline tests for shared/tools/kaggle_output.py (stdlib unittest; a stand-in gateway, no network).

    python3 -m unittest discover -s plugins/kaggle/tests

The stand-in lists a kernel's output in name order and serves one page from where a page token points, as Kaggle's
listing does: `kernels files --page-size 1` prints a token, `kernels output --page-token` downloads that page's files
matching --file-pattern.
"""

import base64
import contextlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.dont_write_bytecode = True  # keep __pycache__ out of the plugin folder
TOOLS = Path(__file__).resolve().parents[1] / "shared" / "tools"
sys.path.insert(0, str(TOOLS))
import kaggle_output as O  # noqa: E402

KERNEL = "alice/big-output"
SESSION = "kernels/123/sessions/456"
STANDIN = """\
import base64, json, os, re, sys
here = os.path.dirname(os.path.abspath(__file__))
cfg = json.load(open(os.path.join(here, "output.json")))
a = sys.argv[1:]
with open(os.path.join(here, "calls.jsonl"), "a") as f:
    f.write(json.dumps(a) + "\\n")
names = sorted(cfg["files"])
encode = base64.urlsafe_b64encode if cfg.get("urlsafe") else base64.b64encode
decode = base64.urlsafe_b64decode if cfg.get("urlsafe") else base64.b64decode
if a[:2] == ["kernels", "files"]:
    if cfg.get("files_code"):
        print("403 - Forbidden")
        sys.exit(cfg["files_code"])
    if len(names) > 1:
        tok = encode(json.dumps({"GcsPageToken": cfg["session"] + "/output/" + names[0], "v": 2}).encode()).decode()
        print("Next Page Token = " + tok.rstrip("="))
    print("name  size  creationDate")
    for n in names[:1]:
        print(n + "  1  2026-10-01")
elif a[:2] == ["kernels", "output"]:
    if cfg.get("output_code"):
        print("429 Client Error: Too Many Requests")
        sys.exit(cfg["output_code"])
    size = int(a[a.index("--page-size") + 1]) if "--page-size" in a else 20
    start = ""
    if "--page-token" in a:
        t = a[a.index("--page-token") + 1]
        doc = json.loads(decode(t + "=" * (-len(t) % 4)))
        assert doc.get("v") == 2, "the token lost a field"
        start = doc["GcsPageToken"].split("/output/")[1]
    pattern = a[a.index("--file-pattern") + 1]
    for n in [x for x in names if x > start][:size]:
        if re.search(pattern, n):
            p = os.path.join(a[a.index("-p") + 1], n)
            os.makedirs(os.path.dirname(p), exist_ok=True)
            with open(p, "w") as f:
                f.write("content of " + n)
else:
    sys.exit(2)
"""


class Tmp(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp()).resolve()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.gateway = self.tmp / "gw" / "kaggle.py"
        self.gateway.parent.mkdir()
        self.gateway.write_text(STANDIN)
        # thousands of files: every name sorts somewhere among them
        self.output([f"site/pkg{i:04d}.py" for i in range(3000)] + ["run_manifest.json", "submission.csv",
                                                                     "logs/train.txt"])

    def output(self, files, **cfg):
        (self.gateway.parent / "output.json").write_text(json.dumps({"files": files, "session": SESSION, **cfg}))

    def calls(self):
        log = self.gateway.parent / "calls.jsonl"
        return [json.loads(x) for x in log.read_text().splitlines()] if log.exists() else []

    def run_tool(self, *args):
        p = subprocess.run([sys.executable, str(TOOLS / "kaggle_output.py"), *args, "--gateway", str(self.gateway)],
                           cwd=self.tmp, capture_output=True, text=True, timeout=60,
                           env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"})
        return p.returncode, p.stdout, p.stderr


class TokenTests(unittest.TestCase):
    def test_the_page_starts_just_before_the_wanted_name_and_the_rest_of_the_token_stays(self):
        raw = base64.b64encode(json.dumps({"GcsPageToken": f"{SESSION}/output/a.txt", "v": 2}).encode()).decode()
        doc, prefix, encode = O.decode_token(raw.rstrip("="))
        self.assertEqual(prefix, f"{SESSION}/output/")
        tok = O.token_for(doc, prefix, encode, "submission.csv")
        self.assertEqual(json.loads(base64.b64decode(tok)), {"GcsPageToken": f"{SESSION}/output/submission.cs", "v": 2})

    def test_url_safe_tokens_and_tokens_of_another_shape(self):
        raw = base64.urlsafe_b64encode(json.dumps({"GcsPageToken": f"{SESSION}/output/~~~>>>"}).encode()).decode()
        self.assertIs(O.decode_token(raw)[2], base64.urlsafe_b64encode)
        for bad in ("bm90IGpzb24", base64.b64encode(b'{"other": "x"}').decode()):
            with self.assertRaises(O.OutputError):
                O.decode_token(bad)


class FetchTests(Tmp):
    def test_named_files_come_from_one_short_page_each(self):
        code, out, err = self.run_tool(KERNEL, "run_manifest.json", "logs/train.txt", "-p", "out")
        self.assertEqual(code, 0, err)
        self.assertEqual(out, "fetched run_manifest.json (28 bytes): out/run_manifest.json\n"
                              "fetched logs/train.txt (25 bytes): out/logs/train.txt\n"
                              f"2 of 2 files fetched from {KERNEL} into out\n")
        self.assertEqual(sorted(str(p.relative_to(self.tmp / "out")) for p in (self.tmp / "out").rglob("*.*")),
                         ["logs/train.txt", "run_manifest.json"])
        files, *outputs = self.calls()
        self.assertEqual(files, ["kernels", "files", KERNEL, "--page-size", "1"])
        for call, name in zip(outputs, ("run_manifest\\.json", "logs/train\\.txt")):
            self.assertEqual(call[:8], ["kernels", "output", KERNEL, "-p", "out", "--file-pattern", f"^{name}\\Z",
                                        "-o"])
            self.assertEqual(call[8:10], ["--page-size", "5"])

    def test_a_name_not_on_its_page_is_reported_and_an_old_copy_does_not_count(self):
        (self.tmp / "out").mkdir()
        (self.tmp / "out" / "missing.csv").write_text("an old copy")
        code, out, err = self.run_tool(KERNEL, "submission.csv", "missing.csv", "-p", "out")
        self.assertEqual(code, 1)
        self.assertIn("NOT FETCHED missing.csv: not on the page the token points at", err)
        self.assertIn("1 of 2 files fetched", out)
        self.assertEqual((self.tmp / "out" / "missing.csv").read_text(), "an old copy")

    def test_an_output_of_one_file_has_no_token_and_needs_none(self):
        self.output(["submission.csv"])
        code, out, _ = self.run_tool(KERNEL, "submission.csv", "-p", "out")
        self.assertEqual(code, 0, out)
        self.assertNotIn("--page-token", self.calls()[1])

    def test_url_safe_tokens_are_rebuilt_url_safe(self):
        self.output(["a.txt", "b.txt", "c.txt"], urlsafe=True)
        self.assertEqual(self.run_tool(KERNEL, "c.txt", "-p", "out")[0], 0)

    def test_failed_calls(self):
        self.output(["a.txt", "b.txt"], files_code=1)
        code, _, err = self.run_tool(KERNEL, "b.txt", "-p", "out")
        self.assertEqual(code, 1)
        self.assertIn(f"kaggle_output: kernels files {KERNEL} failed (exit 1): 403 - Forbidden", err)
        self.output(["a.txt", "b.txt"], output_code=1)
        code, _, err = self.run_tool(KERNEL, "b.txt", "-p", "out")
        self.assertEqual(code, 1)
        self.assertIn("NOT FETCHED b.txt: kernels output failed (exit 1): 429 Client Error", err)


class InputTests(unittest.TestCase):
    def test_names_stay_inside_the_folder_and_refs_are_owner_slug(self):
        for args in (["no-slash", "a.txt", "-p", "o"], [KERNEL, "/etc/passwd", "-p", "o"], [KERNEL, "../x", "-p", "o"],
                     [KERNEL, "a/../../x", "-p", "o"], [KERNEL, "a\\b", "-p", "o"], [KERNEL, "a//b", "-p", "o"],
                     [KERNEL, "a.txt"]):
            with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as cm:
                O.main(args)
            self.assertEqual(cm.exception.code, 2, args)


if __name__ == "__main__":
    unittest.main()
