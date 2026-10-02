# rev. 1

"""kaggle_output: fetch named files from a kernel's output, even an output of thousands of files.

`kernels output` lists a kernel's output a page at a time and pages through all
of it; on a kernel whose output holds thousands of files (one that writes a
whole Python environment, say) it answers 429 Too Many Requests from its first
page, every time and at any --page-size, while `kernels files` still pages.
This tool asks for one short page that starts at each wanted file instead: it
takes the page token of `kernels files <kernel> --page-size 1` (base64 of
{"GcsPageToken": "<session>/output/<name>"}), points it just before the wanted
name (the page starts after the name less its last character), and runs
`kernels output <kernel> --page-token <token> --file-pattern '^<name>\\Z'`
through the gateway: one call per file, plus one. An output of one file has no
page token, and is fetched with the same anchored pattern and no token.

    python3 <plugin-dir>/tools/kaggle_output.py <owner>/<kernel> <file> [<file> ...] -p <folder>

Run it from the project root or task folder; -p names the folder the files are
written to (keep it inside the context). Each <file> is a path inside the
kernel's output (run_manifest.json, logs/train.txt): never absolute, never "..",
so nothing lands outside -p. A file counts as fetched only when the call wrote
it anew; it reads the latest run's output (the CLI names no version). Read-only
on Kaggle, through the gateway (--gateway; default the kaggle.py beside this
file). Exit codes: 0 every file fetched, 1 a file not fetched or a failed call,
2 bad usage. Stdlib only.
"""

import argparse
import base64
import binascii
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

# a kernel ref as Kaggle prints it: owner/slug
REF_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*/[A-Za-z0-9][A-Za-z0-9._-]*")
# the CLI prints this ahead of the rows when more pages exist
TOKEN_RE = re.compile(r"^\s*next page token\s*[=:]\s*(\S+)\s*$", re.I | re.M)
KEY, MARK = "GcsPageToken", "/output/"
# the wanted file and a few after it: a name between the crafted start and the file still leaves it on the page
PAGE = 5
PAUSE = 1.0


class OutputError(Exception):
    pass


def kernel_ref(text):
    if not REF_RE.fullmatch(text):
        raise argparse.ArgumentTypeError(f"not a kernel ref owner/slug: {text!r}")
    return text


def output_name(text):
    """A file path inside the output: relative, no empty, "." or ".." parts, no backslash or control character."""
    parts = text.split("/")
    if (not text or text.startswith("/") or "\\" in text or any(ord(c) < 32 for c in text)
            or any(p in ("", ".", "..") for p in parts)):
        raise argparse.ArgumentTypeError(f"not a file path inside the output (relative, no '..'): {text!r}")
    return text


def _run(cmd, cwd):
    try:
        p = subprocess.run(cmd, cwd=str(cwd), stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                           encoding="utf-8", errors="replace", timeout=900)
    except subprocess.TimeoutExpired:
        return 124, "timed out"
    return p.returncode, p.stdout


def _tail(text):
    return " ".join(str(text or "").strip()[-300:].split()) or "no output"


def _stat(path):
    # a fresh download is told by any change: the CLI may date a file by the server's clock
    try:
        st = Path(path).stat()
    except OSError:
        return None
    return st.st_ino, st.st_size, st.st_mtime_ns, st.st_ctime_ns


def decode_token(token):
    """(the token's JSON, its '<session>/output/' prefix, the encoder that rebuilds it) of a page token."""
    raw = token + "=" * (-len(token) % 4)  # Kaggle may drop the padding
    for decode, encode in ((base64.b64decode, base64.b64encode), (base64.urlsafe_b64decode, base64.urlsafe_b64encode)):
        try:
            doc = json.loads(decode(raw).decode("utf-8"))
        except (ValueError, UnicodeDecodeError, binascii.Error):
            continue
        if isinstance(doc, dict) and isinstance(doc.get(KEY), str) and MARK in doc[KEY]:
            return doc, doc[KEY].split(MARK)[0] + MARK, encode
    raise OutputError(f"the page token of `kernels files` is not base64 of {{\"{KEY}\": \"<session>{MARK}<name>\"}} "
                      "(Kaggle changed its format?): fetch with `kernels output` and --file-pattern instead")


def token_for(doc, prefix, encode, name):
    """A page token whose page starts at name: everything in the token kept, but the place in the listing."""
    return encode(json.dumps(dict(doc, **{KEY: prefix + name[:-1]}), separators=(",", ":")).encode()).decode()


def find_gateway(gateway=None):
    gw = Path(gateway).resolve() if gateway else Path(__file__).resolve().parent / "kaggle.py"
    if not gw.is_file():
        raise OutputError(f"gateway not found: {gw} (pass --gateway)")
    return gw


def fetch(ref, names, folder, gateway, cwd, run=_run, pause=PAUSE):
    """Fetch each named output file of ref into folder; returns [(name, bytes written or None, why not)]."""
    code, out = run([sys.executable, str(gateway), "kernels", "files", ref, "--page-size", "1"], cwd)
    if code != 0:
        raise OutputError(f"kernels files {ref} failed (exit {code}): {_tail(out)}")
    m = TOKEN_RE.search(out)
    start = decode_token(m.group(1)) if m else None
    folder = Path(folder)
    done = []
    for n, name in enumerate(names):
        if n:
            time.sleep(pause)
        target = folder / name
        before = _stat(target)
        cmd = [sys.executable, str(gateway), "kernels", "output", ref, "-p", str(folder), "--file-pattern",
               "^" + re.escape(name) + r"\Z", "-o"]
        if start:
            cmd += ["--page-size", str(PAGE), "--page-token", token_for(*start, name)]
        code, out = run(cmd, cwd)
        after = _stat(target)
        if code != 0:
            done.append((name, None, f"kernels output failed (exit {code}): {_tail(out)}"))
        elif after is None or after == before:
            done.append((name, None, "not on the page the token points at: check the name with `kernels files`"))
        else:
            done.append((name, after[1], None))
    return done


def _rel(p):
    # relative to the working folder when inside it, else absolute
    try:
        rel = os.path.relpath(p)
    except ValueError:
        return str(p)
    return str(Path(p).resolve()) if rel.startswith("..") else rel


def main(argv=None):
    p = argparse.ArgumentParser(prog="kaggle_output.py", description="Fetch named files from a kernel's output "
                                "through the gateway, one short page per file (for outputs of thousands of files).")
    p.add_argument("kernel", type=kernel_ref, help="kernel ref owner/slug")
    p.add_argument("files", nargs="+", type=output_name, help="file paths inside the output, e.g. submission.csv")
    p.add_argument("-p", "--path", required=True, help="folder the files are written to (inside the context)")
    p.add_argument("--gateway", help="the kaggle.py gateway to call (default: the one beside this file)")
    a = p.parse_args(argv)
    names = list(dict.fromkeys(a.files))
    try:
        results = fetch(a.kernel, names, a.path, find_gateway(a.gateway), Path.cwd())
    except OutputError as e:
        print(f"kaggle_output: {e}", file=sys.stderr)
        return 1
    got = 0
    for name, size, why in results:
        if why:
            print(f"NOT FETCHED {name}: {why}", file=sys.stderr)
        else:
            got += 1
            print(f"fetched {name} ({size:,} bytes): {_rel(Path(a.path) / name)}")
    print(f"{got} of {len(results)} files fetched from {a.kernel} into {_rel(a.path)}")
    return 0 if got == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
