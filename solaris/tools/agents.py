# Copyright 2026 Mikhail Yurasov <me@yurasov.me>
# SPDX-License-Identifier: Apache-2.0

"""Project personas: the renamable primary persona and the role personas beside it in the ai-pack (stdlib only).

The ai-pack is the project's pack folder, ``<pack>/`` below: ``aipack/`` by default, ``ai/`` in projects made
before 0.39.0, any plain folder name in general (solaris.tools.pack finds it). It has one **primary persona** -
``<pack>/<primary>.agent.md``, ``engineer`` by default, renamed through ``<pack>/manifest.json`` ->
``agents.primary`` - and any number of **role personas**, each a brief ``<pack>/<role>.agent.md`` right beside
it: a small YAML frontmatter (``description`` required; optional ``tier`` cheap|mid|high|frontier and
``access`` read-only|full) above the markdown brief itself. Every ``<pack>/*.agent.md`` other than the
primary's is a role. All personas read and maintain the one shared instructions store,
``<pack>/instructions.md`` (persistent, committable know-how: procedures, gotchas, lessons); short-term and
machine-local state lives in the pack's ``.memory/``. A role is used by telling a model to act as its brief
("act as ``<pack>/reviewer.agent.md``"), in a delegated subagent or as a session's opening instruction - there
is no per-harness agent format to keep in sync. Every role inherits the primary persona's policies. Briefs are
project content: no rev marker, never materialized from a template (stub:
``solaris/templates/agents/role.agent.md``).

``--rename-pack`` renames the pack folder itself: the folder moves, its manifest's ``revisions`` keys follow,
and the project-root AGENTS.md and CLAUDE.md are pointed at the new name. Every other file that still names
the old folder is listed for review, never edited.

Run::

    uv run -m solaris.tools.agents --dir projects/<slug>                    # --check (default): validate
    uv run -m solaris.tools.agents --dir projects/<slug> --rename-primary master
    uv run -m solaris.tools.agents --dir projects/<slug> --rename-pack aipack
"""

from __future__ import annotations

import argparse
import json
import os
import re
from dataclasses import dataclass
from pathlib import Path

from solaris.tools import pack as P
from solaris.tools import revs as R

ROLE_RE = R.ROLE_RE
TIERS = ("cheap", "mid", "high", "frontier")
ACCESS = ("read-only", "full")
ROLE_KEYS = {"description", "tier", "access"}
INSTRUCTIONS = "instructions.md"   # <pack>/instructions.md: the one shared instructions store
ENTRY_FILES = ("AGENTS.md", "CLAUDE.md")   # project-root files --rename-pack rewrites
IGNORE_FILES = (".gitignore", ".stignore")   # a stale pack path here can expose the private <pack>/.memory/
SKIP_DIRS = {".git", "node_modules", "__pycache__"}   # never scanned for old pack paths (nor .venv*)
SCAN_MAX_BYTES = 1 << 20   # bigger files are data, not references worth listing
LIST_MAX = 40   # leftover files --rename-pack prints before it points at grep for the rest


@dataclass
class Persona:
    name: str
    description: str
    tier: "str | None" = None
    access: str = "full"
    body: str = ""
    source: str = ""            # pack-relative path of the brief
    primary: bool = False


# ----------------------------------------------------------------- frontmatter

def _unquote(v: str) -> str:
    if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
        if v[0] == '"':
            try:
                return json.loads(v)   # honors \" and other escapes
            except ValueError:
                pass
        return v[1:-1]
    return v


def parse_frontmatter(text: str) -> "tuple[dict, str]":
    """(fields, body) of a markdown file that opens with a YAML frontmatter block.

    A deliberately small YAML subset (stdlib only): top-level ``key: value`` scalars. Quoted scalars are
    unquoted, ``#`` comment lines are skipped, and a leading ``_Rev. N_`` line is ignored.
    """
    text = re.sub(r"\A_Rev\.\s*\d+_\s*\n+", "", text.lstrip("﻿"))
    lines = text.split("\n")
    if not lines or lines[0].strip() != "---":
        raise ValueError("missing frontmatter: the file must start with a --- line")
    try:
        end = next(i for i in range(1, len(lines)) if lines[i].strip() == "---")
    except StopIteration:
        raise ValueError("unterminated frontmatter: no closing --- line") from None
    fields: dict = {}
    for raw in lines[1:end]:
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        line = raw.strip()
        if ":" not in line or raw[0] in " \t":
            raise ValueError(f"bad frontmatter line (expected a top-level key: value): {line!r}")
        key, _, val = line.partition(":")
        key = key.strip()
        if key in fields:
            raise ValueError(f"duplicate frontmatter key {key!r}")
        fields[key] = _unquote(val.strip())
    return fields, "\n".join(lines[end + 1:]).strip("\n")


def load_role(path: Path, primary: str) -> Persona:
    """Parse + validate one <pack>/<role>.agent.md brief (ValueError carries a clean message)."""
    fname = Path(path).name
    where = f"{Path(path).parent.name}/{fname}"   # the brief sits in the pack folder
    if not fname.endswith(".agent.md"):
        raise ValueError(f"{where}: role briefs are named <role>.agent.md")
    name = fname[: -len(".agent.md")]
    if not ROLE_RE.match(name):
        raise ValueError(f"{where}: role name {name!r} must match {ROLE_RE.pattern}")
    if name == primary:
        raise ValueError(f"{where}: {name!r} is the primary persona, not a role brief")
    try:
        text = Path(path).read_text(encoding="utf-8")
    except OSError as exc:   # a directory or unreadable file named like a brief
        raise ValueError(f"{where}: cannot read the brief ({exc.strerror or exc})") from None
    try:
        fields, body = parse_frontmatter(text)
    except ValueError as exc:
        raise ValueError(f"{where}: {exc}") from None
    unknown = sorted(set(fields) - ROLE_KEYS)
    if unknown:
        raise ValueError(f"{where}: unknown frontmatter key(s) {', '.join(unknown)}; "
                         f"allowed: {', '.join(sorted(ROLE_KEYS))}")
    desc = fields.get("description", "")
    if not desc.strip():
        raise ValueError(f"{where}: 'description' is required (one line: when to use this persona)")
    tier = fields.get("tier") or None
    if tier is not None and tier not in TIERS:
        raise ValueError(f"{where}: tier must be one of {'|'.join(TIERS)}, got {tier!r}")
    access = fields.get("access") or "full"
    if access not in ACCESS:
        raise ValueError(f"{where}: access must be one of {'|'.join(ACCESS)}, got {access!r}")
    if not body.strip():
        raise ValueError(f"{where}: the brief body (below the frontmatter) is empty")
    return Persona(name=name, description=" ".join(desc.split()), tier=tier, access=access, body=body,
                   source=where)


# ----------------------------------------------------------------- pack reading

def load_pack(project_dir: "str | Path") -> "tuple[Path, dict]":
    """(pack folder, its manifest) for a project; ValueError with a clean message when the project has no
    ai-pack, or several."""
    project_dir = Path(project_dir)
    if not project_dir.is_dir():
        raise ValueError(f"{project_dir} not found: --dir must be a project root (embedded mode: the repo root)")
    try:
        pack = P.require_pack(project_dir)
    except P.PackError as exc:
        raise ValueError(str(exc)) from None
    return pack, json.loads((pack / "manifest.json").read_text(encoding="utf-8"))


def _layout_problems(pack: Path) -> list[str]:
    """Layout defects that do not stop the listing: the shared instructions store missing or empty, and
    leftovers of the pre-0.37 layout (per-persona instructions files, the <pack>/agents/ directory)."""
    p = pack.name
    problems: list[str] = []
    instr = pack / INSTRUCTIONS
    if not instr.exists():
        problems.append(f"{p}/{INSTRUCTIONS} not found - the shared instructions store every persona reads; "
                        f"seed it from solaris/templates/ai-pack/ai/{INSTRUCTIONS}")
    elif not instr.is_file():
        problems.append(f"{p}/{INSTRUCTIONS} is not a regular file (a directory?) - replace it with the shared "
                        "instructions store")
    else:
        try:
            blank = not instr.read_text(encoding="utf-8").strip()
        except (OSError, UnicodeDecodeError) as exc:
            blank, problems = False, problems + [f"{p}/{INSTRUCTIONS}: cannot read it ({exc})"]
        if blank:
            problems.append(f"{p}/{INSTRUCTIONS} is empty - every persona reads it; fill it in")
    legacy = sorted(f.name for f in pack.glob("*.instructions.md"))   # the shared file has no dot before it
    if legacy:
        problems.append("legacy per-persona instructions file(s) " + ", ".join(f"{p}/{n}" for n in legacy)
                        + f" - merge into {p}/{INSTRUCTIONS} and delete (migration 0.37.0)")
    if (pack / "agents").exists():
        problems.append(f"legacy {p}/agents/ directory - move its briefs to {p}/<role>.agent.md, merge any "
                        f"<role>.instructions.md into {p}/{INSTRUCTIONS}, then remove it (migration 0.37.0)")
    return problems


def load_personas(project_dir: Path) -> "tuple[Persona, list[Persona], list[str]]":
    """(primary, roles, problems) for a project; raises ValueError on the first invalid brief."""
    pack, manifest = load_pack(project_dir)
    p = pack.name
    primary = R.primary_role(manifest)
    if not (pack / f"{primary}.agent.md").exists():
        raise ValueError(f"primary persona file {p}/{primary}.agent.md not found "
                         f"({p}/manifest.json agents.primary = {primary!r})")
    prim = Persona(name=primary, description="the primary persona", primary=True,
                   source=f"{p}/{primary}.agent.md")
    roles = [load_role(f, primary) for f in sorted(pack.glob("*.agent.md")) if f.name != f"{primary}.agent.md"]
    return prim, roles, _layout_problems(pack)


# ----------------------------------------------------------------- commands

def cmd_check(project_dir: Path) -> int:
    prim, roles, problems = load_personas(Path(project_dir))
    p = prim.source.rpartition("/")[0]   # the pack folder's name
    ok = ", all valid" if not problems else ""
    if not roles:
        print(f"agents: single persona ({prim.name}){ok}; role personas go beside it as {p}/<role>.agent.md")
    else:
        print(f"agents: primary {prim.name} + {len(roles)} role persona(s) in {p}/{ok}")
        for r in roles:
            print(f"  {r.name:<14} {r.tier or '-':<9} {r.access:<10} {r.description}")
    for p in problems:
        print(f"  PROBLEM: {p}")
    return 1 if problems else 0


def rename_primary(project_dir: Path, new: str) -> "tuple[list[str], list[str]]":
    """Rename the primary persona: move its file, point the manifest at the new name, fix the shared
    instructions' self-references, and re-render every managed pack file whose text carries the name.
    Returns (log lines, warnings)."""
    project_dir = Path(project_dir)
    if not ROLE_RE.match(new):
        raise ValueError(f"role name {new!r} must match {ROLE_RE.pattern}")
    pack, manifest = load_pack(project_dir)
    p = pack.name
    old = R.primary_role(manifest)
    if new == old:
        return [f"the primary persona is already {old!r}; nothing to do"], []
    old_agent, new_agent = pack / f"{old}.agent.md", pack / f"{new}.agent.md"
    if not old_agent.exists():
        raise ValueError(f"{p}/{old}.agent.md not found ({p}/manifest.json agents.primary = {old!r})")
    if new_agent.exists():
        raise ValueError(f"{p}/{new}.agent.md already exists (a role brief, or a stray file); the primary "
                         "persona cannot take that name")
    # the new name lands by re-rendering every managed file whose template carries it, so each of those
    # must be pristine (in sync with, or behind, its master) - a customized copy would keep the old name
    guarded = {rel for master, _proj, rel in R.materialized_map(project_dir)
               if master.exists() and "{{PRIMARY" in master.read_text(encoding="utf-8")}
    # keyed under the pack's real name, like revs reads them (a hand-renamed pack may keep ai/... keys)
    revisions = manifest["revisions"] = R._revisions(manifest, pack)
    for row in R.classify(project_dir):
        rel, v = row["rel"], row["verdict"]
        if rel not in guarded:
            continue
        if v in ("merge-up", "conflict"):
            raise ValueError(f"{rel} is {v} (customized, or never baselined): reconcile it with "
                             "update-project (revs classify / ff / baseline) first, then rename")
        if v == "in-sync" and rel not in revisions:
            rev, h = R.file_rev_hash(project_dir / rel)   # truthful base: identical to the master render
            revisions[rel] = {"rev": rev, "hash": h}
    log: list[str] = []
    warnings: list[str] = []
    old_agent.rename(new_agent)
    log.append(f"moved {p}/{old}.agent.md -> {p}/{new}.agent.md")
    instr = pack / INSTRUCTIONS
    if instr.is_file():
        text = instr.read_text(encoding="utf-8")
        fixed = text.replace(f"{old}.agent.md", f"{new}.agent.md")
        if fixed != text:
            instr.write_text(fixed, encoding="utf-8")
            log.append(f"{p}/{INSTRUCTIONS}: references to {old}.agent.md now name {new}.agent.md")
    else:
        warnings.append(f"{p}/{INSTRUCTIONS} is absent - every persona reads it; seed it from "
                        f"solaris/templates/ai-pack/ai/{INSTRUCTIONS}")
    manifest.setdefault("agents", {})["primary"] = new
    if f"{p}/{old}.agent.md" in revisions:
        revisions[f"{p}/{new}.agent.md"] = revisions.pop(f"{p}/{old}.agent.md")
    (pack / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    log.append(f"{p}/manifest.json: agents.primary = {new!r}")
    res = R.fast_forward(project_dir)
    log.append(f"re-rendered the managed pack files via revs ff ({len(res['applied'])} applied; any pending "
               "fast-forwards landed too)")
    guarded_new = {f"{p}/{new}.agent.md" if rel == f"{p}/{old}.agent.md" else rel for rel in guarded}
    for rel, v in res["skipped"]:
        if rel in guarded_new:
            warnings.append(f"{rel} is {v} and still carries the old name - merge it by hand (update-project)")
        else:
            log.append(f"{rel} is {v} (unrelated to the rename) - left for update-project")
    log.append(f"prose may still name the old role: grep -rn '{old}' {project_dir} --include='*.md' "
               "--exclude-dir=.git")
    return log, warnings


def _path_re(name: str) -> "re.Pattern[str]":
    """``<name>/`` opening a path segment, so never the tail of a longer name such as ``openai/``."""
    return re.compile(r"(?<![\w.-])" + re.escape(name) + "/")


def _files_naming(root: Path, rx: "re.Pattern[str]") -> list[str]:
    """Relative paths (shallow first) of the text files under ``root`` that match ``rx``. Skips SKIP_DIRS,
    virtualenvs, symlinks, non-regular files, files over SCAN_MAX_BYTES and anything that is not UTF-8."""
    hits: list[str] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS and not d.startswith(".venv")]
        for name in filenames:
            f = Path(dirpath) / name
            try:
                if f.is_symlink() or not f.is_file() or f.stat().st_size > SCAN_MAX_BYTES:
                    continue
                text = f.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            if rx.search(text):
                hits.append(f.relative_to(root).as_posix())
    return sorted(hits, key=lambda rel: (rel.count("/"), rel))


def rename_pack(project_dir: Path, new: str) -> "tuple[list[str], list[str]]":
    """Rename the project's ai-pack folder: move it, carry its manifest's revisions keys along, and point the
    project-root AGENTS.md and CLAUDE.md at the new name. Other files that still name the old folder are
    listed, never edited. Returns (log lines, warnings)."""
    project_dir = Path(project_dir)
    if not new or new != new.strip() or "/" in new or "\\" in new or new.startswith("."):
        raise ValueError(f"pack name {new!r} must be a plain folder name (no slash, not hidden, not . or ..)")
    pack, manifest = load_pack(project_dir)
    old = pack.name
    if new == old:
        return [f"the ai-pack folder is already {old}/; nothing to do"], []
    target = project_dir / new
    if target.exists() or target.is_symlink():
        raise ValueError(f"{target} already exists; pick another name")
    rx = _path_re(old)
    revisions = R._revisions(manifest, pack)   # legacy ai/... keys of a hand-renamed pack count as <old>/...
    # an entry file still at its recorded baseline stays pristine through the rewrite below, so revs ff can
    # fast-forward it later; a customized one keeps its old baseline and stays a merge
    pristine = {name for name in ENTRY_FILES if (project_dir / name).is_file()
                and isinstance(revisions.get(name), dict)
                and R.file_rev_hash(project_dir / name)[1] == revisions[name].get("hash")}
    try:
        pack.rename(target)
    except OSError as exc:
        raise ValueError(f"cannot move {old}/ to {new}/: {exc.strerror or exc}") from None
    log = [f"moved {old}/ -> {new}/"]
    rebased: list[str] = []
    for name in ENTRY_FILES:
        f = project_dir / name
        if not f.is_file():
            continue
        fixed, n = rx.subn(lambda _m: f"{new}/", f.read_text(encoding="utf-8"))
        if not n:
            continue
        f.write_text(fixed, encoding="utf-8")
        log.append(f"{name}: {n} reference(s) to {old}/ now name {new}/")
        if name in pristine:
            rev, h = R.file_rev_hash(f)
            revisions[name] = {"rev": rev, "hash": h}
            rebased.append(name)
    moved = sum(k.startswith(f"{old}/") for k in revisions)
    if moved or rebased:
        manifest["revisions"] = {(f"{new}/{k[len(old) + 1:]}" if k.startswith(f"{old}/") else k): v
                                 for k, v in revisions.items()}
        (target / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
        log.append(f"{new}/manifest.json: {moved} revisions key(s) moved under {new}/"
                   + (f"; the baseline of {', '.join(rebased)} follows the rewrite" if rebased else ""))
    others = _files_naming(project_dir, rx)
    if others:
        log.append(f"{len(others)} other file(s) still name {old}/ - not edited (revs ff below re-renders the "
                   "unedited managed pack files); review the rest by hand:")
        log += [f"  {rel}" for rel in others[:LIST_MAX]]
        if len(others) > LIST_MAX:
            log.append(f"  ... and {len(others) - LIST_MAX} more: grep -rlE '(^|[^[:alnum:]_.-]){old}/' "
                       f"{project_dir} --exclude-dir=.git")
    else:
        log.append(f"no other file under {project_dir} names {old}/")
    warnings = [f"{rel} still names {old}/ - its patterns miss the moved pack, so private {new}/.memory/ files "
                "may get committed or synced; fix it before the next commit"
                for rel in others if Path(rel).name in IGNORE_FILES]
    log.append(f"next: uv run -m solaris.tools.revs ff --dir {project_dir}")
    log.append(f"then: uv run -m solaris.tools.revs baseline --dir {project_dir} (once anything ff reports "
               "is merged)")
    return log, warnings


def _report(log: list[str], warnings: list[str]) -> int:
    for line in log:
        print(line if line.startswith("  ") else f"agents: {line}")   # indented lines continue a list
    for line in warnings:
        print(f"agents: WARNING {line}")
    return 1 if warnings else 0


def main(argv: "list[str] | None" = None) -> int:
    parser = argparse.ArgumentParser(prog="solaris.tools.agents", description=__doc__.splitlines()[0])
    parser.add_argument("--dir", required=True,
                        help="project root holding the ai-pack folder (embedded mode: the repo root)")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--check", action="store_true",
                      help="validate the primary, the role briefs beside it, and the shared "
                           "<pack>/instructions.md (default)")
    mode.add_argument("--rename-primary", metavar="ROLE",
                      help="rename the primary persona (its file, the manifest, the rendered pack files)")
    mode.add_argument("--rename-pack", metavar="NAME",
                      help="rename the ai-pack folder (the folder, its revisions keys, the root AGENTS.md and "
                           "CLAUDE.md) and list the other files that still name the old one")
    args = parser.parse_args(argv)
    project_dir = Path(args.dir)
    try:
        if args.rename_primary is not None:
            return _report(*rename_primary(project_dir, args.rename_primary))
        if args.rename_pack is not None:
            return _report(*rename_pack(project_dir, args.rename_pack))
        return cmd_check(project_dir)
    except (ValueError, P.PackError) as exc:   # revs raises PackError too: still one clean line
        print(f"agents: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
