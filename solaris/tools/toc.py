# Copyright 2026 Mihail Yurasov <me@yurasov.me>
# SPDX-License-Identifier: Apache-2.0

"""Generate/maintain a Markdown table of contents (stdlib only).

Lists level-2-and-deeper headers; the level-1 title is omitted and marked ``<!-- omit in toc -->``. Output
matches the GitHub / "Markdown All in One" style: a nested bullet list of ``- [Heading](#anchor)`` links
placed right after the H1. Idempotent - re-running produces the same file. Headers inside fenced code blocks
and headers carrying ``<!-- omit in toc -->`` are ignored. Files with no level-2 headers are left unchanged.
A leading ``_Rev. N_`` revision marker (from solaris.tools.revs) is preserved above the TOC.

Run::

    uv run -m solaris.tools.toc --check <file.md> [...]   # exit 1 if any file's TOC is missing/stale
    uv run -m solaris.tools.toc --write <file.md> [...]   # insert/refresh the TOC in place
    uv run -m solaris.tools.toc --check --all             # walk the repo (skips .venv, references, etc.)
"""

from __future__ import annotations

import argparse
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
OMIT = "<!-- omit in toc -->"
_FENCE = re.compile(r"^\s*(```|~~~)")
_ATX = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")
_TOC_LINE = re.compile(r"^\s*- \[.*\]\(#.*\)\s*$")
_REV = re.compile(r"^_Rev\.\s+\d+_\s*$")  # leading revision marker (solaris.tools.revs), kept above the TOC
_SKIP_DIRS = {".venv", ".git", ".tmp", ".tools", "node_modules", "__pycache__", "references"}


def slug(text: str) -> str:
    """GitHub-style anchor: drop links/backticks/comments, lowercase, keep [a-z0-9 _-], spaces -> '-'."""
    t = re.sub(r"!?\[([^\]]*)\]\([^)]*\)", r"\1", text)   # [label](url) -> label
    t = re.sub(r"<!--.*?-->", "", t)                        # inline comments
    t = t.replace("`", "").strip().lower()
    kept = "".join(ch for ch in t if ch.isalnum() or ch in (" ", "-", "_"))
    return kept.strip().replace(" ", "-")


def _display(text: str) -> str:
    # Keep the heading text as-is (backticks included) but escape '&' to match the common
    # "Markdown All in One" rendering, so regenerating does not churn existing TOCs.
    return re.sub(r"<!--.*?-->", "", text).strip().replace("&", "\\&")


def split_frontmatter(lines: list[str]) -> tuple[int, int]:
    """Return (frontmatter_end_index_exclusive, body_start). Body starts after a leading --- ... --- block."""
    if lines and lines[0].strip() == "---":
        for i in range(1, len(lines)):
            if lines[i].strip() == "---":
                return i + 1, i + 1
    return 0, 0


def parse_headers(body: list[str]) -> list[tuple[int, str]]:
    headers: list[tuple[int, str]] = []
    in_fence = False
    for line in body:
        if _FENCE.match(line):
            in_fence = not in_fence
            continue
        if in_fence or OMIT in line:
            continue
        m = _ATX.match(line)
        if m:
            headers.append((len(m.group(1)), m.group(2)))
    return headers


def build_toc(headers: list[tuple[int, str]]) -> list[str]:
    deep = [(lvl, txt) for lvl, txt in headers if lvl >= 2]
    if len(deep) < 2:
        return []  # a table of contents needs at least two entries; a lone section gets none
    minlvl = min(lvl for lvl, _ in deep)
    seen: dict[str, int] = {}
    out: list[str] = []
    for lvl, txt in deep:
        base = slug(txt)
        n = seen.get(base, 0)
        seen[base] = n + 1
        anchor = base if n == 0 else f"{base}-{n}"
        out.append(f"{'  ' * (lvl - minlvl)}- [{_display(txt)}](#{anchor})")
    return out


def render(text: str) -> str:
    """Return the file content with an up-to-date TOC (or unchanged if no level-2 headers)."""
    newline = "\n"
    lines = text.split("\n")
    fm_end, body_start = split_frontmatter(lines)
    # keep a leading rev marker (e.g. "_Rev. 3_") as preamble, above the TOC
    if body_start < len(lines) and _REV.match(lines[body_start]):
        body_start += 1
        if body_start < len(lines) and lines[body_start].strip() == "":
            body_start += 1
    body = lines[body_start:]

    toc = build_toc(parse_headers(body))
    if not toc:
        return text  # nothing to list; leave the file alone

    # locate the first H1 in the body
    h1_idx = next((i for i, ln in enumerate(body) if re.match(r"^#\s+", ln)), None)
    if h1_idx is None:
        # no H1: place the TOC at the very top of the body
        head, tail = [], body
        insert_at = 0
    else:
        if OMIT not in body[h1_idx]:
            body[h1_idx] = body[h1_idx].rstrip() + " " + OMIT
        insert_at = h1_idx + 1
        head, tail = body[:insert_at], body[insert_at:]

    # drop a leading blank + an existing contiguous TOC block in `tail`
    j = 0
    while j < len(tail) and tail[j].strip() == "":
        j += 1
    k = j
    while k < len(tail) and _TOC_LINE.match(tail[k]):
        k += 1
    if k > j:  # existing TOC found -> remove it (and following blanks)
        while k < len(tail) and tail[k].strip() == "":
            k += 1
        tail = tail[k:]
    else:
        tail = tail[j:]

    new_body = head + [""] + toc + [""] + tail
    return newline.join(lines[:body_start] + new_body)


def process(path: Path, write: bool) -> str:
    """Return 'current' (TOC up to date or n/a), 'written' (changed on disk), or 'stale' (would change)."""
    original = path.read_text(encoding="utf-8")
    updated = render(original)
    if updated == original:
        return "current"
    if write:
        path.write_text(updated, encoding="utf-8")
        return "written"
    return "stale"


def iter_all_md() -> list[Path]:
    out: list[Path] = []
    for p in REPO_ROOT.rglob("*.md"):
        if any(part in _SKIP_DIRS for part in p.relative_to(REPO_ROOT).parts):
            continue
        out.append(p)
    return sorted(out)


def _rel(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(REPO_ROOT))
    except ValueError:
        return str(path)


def main(argv: "list[str] | None" = None) -> int:
    parser = argparse.ArgumentParser(prog="solaris.tools.toc", description=__doc__.splitlines()[0])
    parser.add_argument("files", nargs="*", type=Path, help="markdown files (or use --all)")
    parser.add_argument("--all", action="store_true", help="process every *.md under the repo (skips .venv, references, ...)")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--check", action="store_true", help="report stale/missing TOCs (default)")
    mode.add_argument("--write", action="store_true", help="insert/refresh TOCs in place")
    args = parser.parse_args(argv)

    files = iter_all_md() if args.all else list(args.files)
    if not files:
        parser.error("no files given (pass paths or --all)")

    results = {path: process(path, write=args.write) for path in files}

    if args.write:
        written = [p for p, r in results.items() if r == "written"]
        print(f"toc: updated {len(written)} of {len(files)} file(s)")
        for path in written:
            print(f"  {_rel(path)}")
        return 0

    stale = [p for p, r in results.items() if r == "stale"]
    if stale:
        print("toc: stale or missing TOC in:")
        for path in stale:
            print(f"  {_rel(path)}")
        print("fix with: uv run -m solaris.tools.toc --write <file> (or --all)")
        return 1
    print(f"toc: {len(files)} file(s) OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
