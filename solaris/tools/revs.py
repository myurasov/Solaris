"""Per-file revisions + content hashing for Solaris (stdlib only).

Evolving framework files carry an integer **revision** marker, bumped +1 per edit. The **content hash**
excludes the marker, so a pure rev bump never changes the hash - only real content edits move it. Together
they drive sync/merge between the framework master copies and the materialized copies inside ai-setups,
independent of semantic versions (which are release-only; see version.py).

Marker by file type (placed at the top of the file):
  .md / .mdc : first line      ``_Rev. N_``
  .py        : first line      ``# rev. N``
  .json      : first field     ``"_rev": N``

Framework ledger: ``solaris/revisions.json`` (current rev+hash + short history per tracked file).
ai-setup baseline: the ``revisions`` map in a project's ``ai/manifest.json`` ({rel: {rev, hash}} recorded
at last materialization) - the merge base for detecting external user edits.

Run::

    uv run -m solaris.tools.revs bump <file>...
    uv run -m solaris.tools.revs hash <file>
    uv run -m solaris.tools.revs status               # framework files edited without a rev bump
    uv run -m solaris.tools.revs ledger                # rebuild solaris/revisions.json
    uv run -m solaris.tools.revs classify --dir <project>   # per materialized file: verdict
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
TEMPLATE_DIR = REPO_ROOT / "solaris" / "templates" / "ai-setup"
PLUGINS_DIR = REPO_ROOT / "plugins"
LEDGER_PATH = REPO_ROOT / "solaris" / "revisions.json"

# The materialized set: master files copied into ai-setups, where per-file rev/hash sync matters.
# (Other framework files evolve too, but they are not duplicated into projects, so they are versioned by
# git + semver rather than by revisions.)
TRACKED_GLOBS = [
    "solaris/templates/ai-setup/AGENTS.md",
    "solaris/templates/ai-setup/ai/developer.agent.md",
    "plugins/*/shared/*.md",
]

_MD_RE = re.compile(r"^_Rev\.\s+(\d+)_\s*$")
_PY_RE = re.compile(r"^#\s*rev\.\s+(\d+)\s*$")


def _ext(path: "str | Path") -> str:
    return Path(path).suffix.lower()


def _marker(ext: str, rev: int) -> str:
    if ext in (".md", ".mdc"):
        return f"_Rev. {rev}_"
    if ext == ".py":
        return f"# rev. {rev}"
    raise ValueError(f"no text marker for {ext}")


def read_rev(text: str, ext: str) -> "int | None":
    if ext == ".json":
        try:
            val = json.loads(text).get("_rev")
        except json.JSONDecodeError:
            return None
        return int(val) if isinstance(val, int) else None
    rx = _MD_RE if ext in (".md", ".mdc") else _PY_RE if ext == ".py" else None
    if rx is None:
        return None
    for line in text.splitlines():
        m = rx.match(line)
        if m:
            return int(m.group(1))
    return None


def canonical(text: str, ext: str) -> str:
    """Content with the rev marker removed, normalized for stable hashing."""
    if ext == ".json":
        obj = json.loads(text)
        obj.pop("_rev", None)
        return json.dumps(obj, sort_keys=True, indent=2) + "\n"
    rx = _MD_RE if ext in (".md", ".mdc") else _PY_RE if ext == ".py" else None
    lines = text.splitlines()
    if rx is not None:
        lines = [ln for ln in lines if not rx.match(ln)]
    # strip (not rstrip): removing a leading marker line must not leave a leading blank,
    # so the content hash is identical whether the marker is at the top or absent.
    return "\n".join(lines).strip("\n") + "\n"


def content_hash(text: str, ext: str) -> str:
    return hashlib.sha256(canonical(text, ext).encode("utf-8")).hexdigest()


def set_rev(text: str, ext: str, rev: int) -> str:
    if ext == ".json":
        obj = json.loads(text)
        obj.pop("_rev", None)
        return json.dumps({"_rev": rev, **obj}, indent=2) + "\n"  # _rev first
    body = canonical(text, ext).strip("\n")
    return f"{_marker(ext, rev)}\n\n{body}\n"


def bump_text(text: str, ext: str) -> "tuple[str, int]":
    new_rev = (read_rev(text, ext) or 0) + 1
    return set_rev(text, ext, new_rev), new_rev


def bump_file(path: Path) -> int:
    ext = _ext(path)
    text = path.read_text(encoding="utf-8")
    new_text, new_rev = bump_text(text, ext)
    path.write_text(new_text, encoding="utf-8")
    return new_rev


def file_rev_hash(path: Path) -> "tuple[int | None, str]":
    text = path.read_text(encoding="utf-8")
    ext = _ext(path)
    return read_rev(text, ext), content_hash(text, ext)


def iter_tracked(repo_root: Path = REPO_ROOT) -> list[Path]:
    out: list[Path] = []
    for pattern in TRACKED_GLOBS:
        out.extend(sorted(repo_root.glob(pattern)))
    # de-dup, stable order
    seen, uniq = set(), []
    for p in out:
        if p not in seen:
            seen.add(p)
            uniq.append(p)
    return uniq


# ----------------------------------------------------------------- ledger

def load_ledger(path: Path = LEDGER_PATH) -> dict:
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return {"_comment": "Managed by solaris.tools.revs; do not edit by hand.", "schema_version": 1, "files": {}}


def rebuild_ledger(repo_root: Path = REPO_ROOT, path: Path = LEDGER_PATH) -> dict:
    led = load_ledger(path)
    files = led.setdefault("files", {})
    for p in iter_tracked(repo_root):
        rel = str(p.relative_to(repo_root))
        rev, h = file_rev_hash(p)
        rev = rev if rev is not None else 1
        entry = files.get(rel, {"history": []})
        history = entry.get("history", [])
        if not history or history[-1].get("hash") != h:
            history.append({"rev": rev, "hash": h})
        entry.update({"rev": rev, "hash": h, "history": history[-10:]})
        files[rel] = entry
    path.write_text(json.dumps(led, indent=2) + "\n", encoding="utf-8")
    return led


def status(repo_root: Path = REPO_ROOT, path: Path = LEDGER_PATH) -> list[str]:
    """Files whose content changed since the ledger but whose rev was not bumped (a missing-bump warning)."""
    led = load_ledger(path).get("files", {})
    stale: list[str] = []
    for p in iter_tracked(repo_root):
        rel = str(p.relative_to(repo_root))
        rev, h = file_rev_hash(p)
        rec = led.get(rel)
        if rec and rec.get("hash") != h and rec.get("rev") == rev:
            stale.append(rel)
    return stale


# ----------------------------------------------------------------- project classification

def materialized_map(project_dir: Path, template_dir: Path = TEMPLATE_DIR,
                     plugins_dir: Path = PLUGINS_DIR) -> list[tuple[Path, Path, str]]:
    """(master_path, project_path, rel_in_project) for every file the framework materializes into a project."""
    pairs = [
        (template_dir / "AGENTS.md", project_dir / "AGENTS.md", "AGENTS.md"),
        (template_dir / "ai" / "developer.agent.md", project_dir / "ai" / "developer.agent.md",
         "ai/developer.agent.md"),
    ]
    manifest = project_dir / "ai" / "manifest.json"
    if manifest.exists():
        plugins = json.loads(manifest.read_text(encoding="utf-8")).get("plugins", [])
        for entry in plugins:
            name = entry.get("name") if isinstance(entry, dict) else entry
            shared = plugins_dir / name / "shared"
            if shared.is_dir():
                for f in sorted(shared.glob("*.md")):
                    pairs.append((f, project_dir / "ai" / name / f.name, f"ai/{name}/{f.name}"))
    return pairs


def _placeholder_subs(manifest: dict) -> dict:
    """Template placeholders resolved from a project's manifest (the rev marker is not a placeholder)."""
    p = manifest.get("project", {})
    return {
        "{{SLUG}}": p.get("slug", ""), "{{NAME}}": p.get("name", ""),
        "{{TYPE}}": p.get("type", ""), "{{MODE}}": p.get("mode", ""),
        "{{FRAMEWORK_VERSION}}": str(manifest.get("framework_version", "")),
        "{{DATE}}": str(manifest.get("created", "")),
    }


def _render_master(master: Path, subs: dict) -> str:
    """Master content with template placeholders substituted (no-op for files without placeholders)."""
    text = master.read_text(encoding="utf-8")
    for k, v in subs.items():
        text = text.replace(k, v)
    return text


def classify(project_dir: Path, template_dir: Path = TEMPLATE_DIR,
             plugins_dir: Path = PLUGINS_DIR) -> list[dict]:
    """Per materialized file, a verdict: in-sync / fast-forward / merge-up / conflict / missing."""
    manifest_path = project_dir / "ai" / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.exists() else {}
    baseline = manifest.get("revisions", {})
    subs = _placeholder_subs(manifest)
    rows: list[dict] = []
    for master, proj, rel in materialized_map(project_dir, template_dir, plugins_dir):
        if master.exists():
            mtext = _render_master(master, subs)
            mext = _ext(master)
            mrev, mhash = read_rev(mtext, mext), content_hash(mtext, mext)
        else:
            mrev, mhash = None, None
        base = baseline.get(rel, {})
        if not proj.exists():
            verdict = "missing"
            prev, phash = None, None
        else:
            prev, phash = file_rev_hash(proj)
            if phash == mhash:
                verdict = "in-sync"
            elif base and phash == base.get("hash"):
                verdict = "fast-forward"          # user untouched; master advanced
            elif prev is not None and mrev is not None and prev > mrev:
                verdict = "merge-up"              # user advanced past master
            else:
                verdict = "conflict"              # both changed (or no base)
        rows.append({"rel": rel, "verdict": verdict, "master_rev": mrev, "proj_rev": prev,
                     "base_rev": base.get("rev")})
    return rows


def record_baseline(project_dir: Path, template_dir: Path = TEMPLATE_DIR,
                    plugins_dir: Path = PLUGINS_DIR) -> dict:
    """Record each present materialized file's current rev+hash into ai/manifest.json -> revisions."""
    project_dir = Path(project_dir)
    manifest_path = project_dir / "ai" / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    baseline: dict = {}
    for _master, proj, rel in materialized_map(project_dir, template_dir, plugins_dir):
        if proj.exists():
            rev, h = file_rev_hash(proj)
            baseline[rel] = {"rev": rev, "hash": h}
    manifest["revisions"] = baseline
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return baseline


def fast_forward(project_dir: Path, template_dir: Path = TEMPLATE_DIR,
                 plugins_dir: Path = PLUGINS_DIR) -> dict:
    """Apply safe verdicts (missing/fast-forward copied from master; in-sync reconciled); skip merges."""
    project_dir = Path(project_dir)
    verdicts = {r["rel"]: r["verdict"] for r in classify(project_dir, template_dir, plugins_dir)}
    manifest_path = project_dir / "ai" / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    revisions = manifest.setdefault("revisions", {})
    subs = _placeholder_subs(manifest)
    applied, skipped = [], []
    for master, proj, rel in materialized_map(project_dir, template_dir, plugins_dir):
        v = verdicts.get(rel)
        if v in ("missing", "fast-forward", "in-sync"):
            mtext = _render_master(master, subs)
            mext = _ext(master)
            if v in ("missing", "fast-forward"):
                proj.parent.mkdir(parents=True, exist_ok=True)
                proj.write_text(mtext, encoding="utf-8")
            revisions[rel] = {"rev": read_rev(mtext, mext), "hash": content_hash(mtext, mext)}
            applied.append((rel, v))
        elif v in ("merge-up", "conflict"):
            skipped.append((rel, v))
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return {"applied": applied, "skipped": skipped}


# ----------------------------------------------------------------- CLI

def _cmd_bump(args):
    for f in args.files:
        print(f"{f}: rev {bump_file(Path(f))}")
    return 0


def _cmd_hash(args):
    p = Path(args.file)
    rev, h = file_rev_hash(p)
    print(f"rev={rev} hash={h}")
    return 0


def _cmd_status(args):
    stale = status()
    if stale:
        print("revs: content changed without a rev bump (run `revs bump <file>`):")
        for rel in stale:
            print(f"  {rel}")
        return 1
    print(f"revs: {len(iter_tracked())} tracked file(s) consistent with ledger")
    return 0


def _cmd_ledger(args):
    led = rebuild_ledger()
    print(f"revs: ledger rebuilt for {len(led['files'])} file(s) -> {LEDGER_PATH.relative_to(REPO_ROOT)}")
    return 0


def _cmd_classify(args):
    rows = classify(Path(args.dir))
    rc = 0
    for r in rows:
        if r["verdict"] not in ("in-sync",):
            rc = 1
        print(f"{r['verdict']:<13} {r['rel']}  (master rev {r['master_rev']}, project rev {r['proj_rev']}, base {r['base_rev']})")
    return rc


def _cmd_baseline(args):
    b = record_baseline(Path(args.dir))
    print(f"revs: recorded baseline for {len(b)} file(s) in {args.dir}/ai/manifest.json")
    return 0


def _cmd_ff(args):
    res = fast_forward(Path(args.dir))
    print(f"revs: applied {len(res['applied'])} file(s)")
    for rel, v in res["applied"]:
        print(f"  {v:<12} {rel}")
    if res["skipped"]:
        print("revs: needs manual merge (update-project / import-plugin):")
        for rel, v in res["skipped"]:
            print(f"  {v:<12} {rel}")
    return 1 if res["skipped"] else 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="solaris.tools.revs", description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)

    sp = sub.add_parser("bump", help="increment the rev marker in one or more files")
    sp.add_argument("files", nargs="+")
    sp.set_defaults(func=_cmd_bump)

    sp = sub.add_parser("hash", help="print a file's rev + content hash (rev excluded)")
    sp.add_argument("file")
    sp.set_defaults(func=_cmd_hash)

    sub.add_parser("status", help="framework files changed without a rev bump").set_defaults(func=_cmd_status)
    sub.add_parser("ledger", help="rebuild solaris/revisions.json").set_defaults(func=_cmd_ledger)

    sp = sub.add_parser("classify", help="per materialized file verdict for a project")
    sp.add_argument("--dir", required=True)
    sp.set_defaults(func=_cmd_classify)

    sp = sub.add_parser("baseline", help="record per-file rev+hash baseline into ai/manifest.json")
    sp.add_argument("--dir", required=True)
    sp.set_defaults(func=_cmd_baseline)

    sp = sub.add_parser("ff", help="apply in-sync/fast-forward/missing files; report merges needed")
    sp.add_argument("--dir", required=True)
    sp.set_defaults(func=_cmd_ff)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
