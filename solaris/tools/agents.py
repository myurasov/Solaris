# Copyright 2026 Mikhail Yurasov <me@yurasov.me>
# SPDX-License-Identifier: Apache-2.0

"""Project personas: the renamable primary persona and the role personas beside it in ai/ (stdlib only).

An ai-pack has one **primary persona** - ``ai/<primary>.agent.md``, ``engineer`` by default, renamed through
``ai/manifest.json`` -> ``agents.primary`` - and any number of **role personas**, each a brief
``ai/<role>.agent.md`` right beside it: a small YAML frontmatter (``description`` required; optional ``tier``
cheap|mid|high|frontier and ``access`` read-only|full) above the markdown brief itself. Every ``ai/*.agent.md``
other than the primary's is a role. All personas read and maintain the one shared instructions store,
``ai/instructions.md`` (persistent, committable know-how: procedures, gotchas, lessons); short-term and
machine-local state lives in the pack's ``ai/.memory/``. A role is used by telling a model to act as its
brief ("act as ``ai/reviewer.agent.md``"), in a delegated subagent or as a session's opening instruction -
there is no per-harness agent format to keep in sync. Every role inherits the primary persona's policies.
Briefs are project content: no rev marker, never materialized from a template (stub:
``solaris/templates/agents/role.agent.md``).

Run::

    uv run -m solaris.tools.agents --dir projects/<slug>                    # --check (default): validate
    uv run -m solaris.tools.agents --dir projects/<slug> --rename-primary master
"""

from __future__ import annotations

import argparse
import json
import re
from dataclasses import dataclass
from pathlib import Path

from solaris.tools import revs as R

ROLE_RE = R.ROLE_RE
TIERS = ("cheap", "mid", "high", "frontier")
ACCESS = ("read-only", "full")
ROLE_KEYS = {"description", "tier", "access"}
INSTRUCTIONS = "instructions.md"   # ai/instructions.md: the one shared instructions store


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
    """Parse + validate one ai/<role>.agent.md brief (ValueError carries a clean message)."""
    fname = Path(path).name
    where = f"ai/{fname}"
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

def read_manifest(project_dir: "str | Path") -> dict:
    path = Path(project_dir) / "ai" / "manifest.json"
    if not path.exists():
        raise ValueError(f"{path} not found: --dir must be a project holding ai/ "
                         "(embedded mode: the repo root)")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"{path} is not valid JSON: {exc}") from None
    if not isinstance(data, dict):
        raise ValueError(f"{path}: the top level must be a JSON object")
    return data


def _layout_problems(ai: Path) -> list[str]:
    """Layout defects that do not stop the listing: the shared instructions store missing or empty, and
    leftovers of the pre-0.37 layout (per-persona instructions files, the ai/agents/ directory)."""
    problems: list[str] = []
    instr = ai / INSTRUCTIONS
    if not instr.exists():
        problems.append(f"ai/{INSTRUCTIONS} not found - the shared instructions store every persona reads; "
                        f"seed it from solaris/templates/ai-pack/ai/{INSTRUCTIONS}")
    elif not instr.is_file():
        problems.append(f"ai/{INSTRUCTIONS} is not a regular file (a directory?) - replace it with the shared "
                        "instructions store")
    else:
        try:
            blank = not instr.read_text(encoding="utf-8").strip()
        except (OSError, UnicodeDecodeError) as exc:
            blank, problems = False, problems + [f"ai/{INSTRUCTIONS}: cannot read it ({exc})"]
        if blank:
            problems.append(f"ai/{INSTRUCTIONS} is empty - every persona reads it; fill it in")
    legacy = sorted(p.name for p in ai.glob("*.instructions.md"))   # the shared file itself has no dot before it
    if legacy:
        problems.append("legacy per-persona instructions file(s) " + ", ".join(f"ai/{n}" for n in legacy)
                        + f" - merge into ai/{INSTRUCTIONS} and delete (migration 0.37.0)")
    if (ai / "agents").exists():
        problems.append("legacy ai/agents/ directory - move its briefs to ai/<role>.agent.md, merge any "
                        f"<role>.instructions.md into ai/{INSTRUCTIONS}, then remove it (migration 0.37.0)")
    return problems


def load_personas(project_dir: Path) -> "tuple[Persona, list[Persona], list[str]]":
    """(primary, roles, problems) for a project; raises ValueError on the first invalid brief."""
    project_dir = Path(project_dir)
    manifest = read_manifest(project_dir)
    primary = R.primary_role(manifest)
    ai = project_dir / "ai"
    if not (ai / f"{primary}.agent.md").exists():
        raise ValueError(f"primary persona file ai/{primary}.agent.md not found "
                         f"(ai/manifest.json agents.primary = {primary!r})")
    prim = Persona(name=primary, description="the primary persona", primary=True,
                   source=f"ai/{primary}.agent.md")
    roles = [load_role(f, primary) for f in sorted(ai.glob("*.agent.md")) if f.name != f"{primary}.agent.md"]
    return prim, roles, _layout_problems(ai)


# ----------------------------------------------------------------- commands

def cmd_check(project_dir: Path) -> int:
    prim, roles, problems = load_personas(Path(project_dir))
    ok = ", all valid" if not problems else ""
    if not roles:
        print(f"agents: single persona ({prim.name}){ok}; role personas go beside it as ai/<role>.agent.md")
    else:
        print(f"agents: primary {prim.name} + {len(roles)} role persona(s) in ai/{ok}")
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
    manifest = read_manifest(project_dir)
    old = R.primary_role(manifest)
    if new == old:
        return [f"the primary persona is already {old!r}; nothing to do"], []
    ai = project_dir / "ai"
    old_agent, new_agent = ai / f"{old}.agent.md", ai / f"{new}.agent.md"
    if not old_agent.exists():
        raise ValueError(f"ai/{old}.agent.md not found (ai/manifest.json agents.primary = {old!r})")
    if new_agent.exists():
        raise ValueError(f"ai/{new}.agent.md already exists (a role brief, or a stray file); the primary "
                         "persona cannot take that name")
    # the new name lands by re-rendering every managed file whose template carries it, so each of those
    # must be pristine (in sync with, or behind, its master) - a customized copy would keep the old name
    guarded = {rel for master, _proj, rel in R.materialized_map(project_dir)
               if master.exists() and "{{PRIMARY" in master.read_text(encoding="utf-8")}
    revisions = manifest.setdefault("revisions", {})
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
    log.append(f"moved ai/{old}.agent.md -> ai/{new}.agent.md")
    instr = ai / INSTRUCTIONS
    if instr.is_file():
        text = instr.read_text(encoding="utf-8")
        fixed = text.replace(f"{old}.agent.md", f"{new}.agent.md")
        if fixed != text:
            instr.write_text(fixed, encoding="utf-8")
            log.append(f"ai/{INSTRUCTIONS}: references to {old}.agent.md now name {new}.agent.md")
    else:
        warnings.append(f"ai/{INSTRUCTIONS} is absent - every persona reads it; seed it from "
                        f"solaris/templates/ai-pack/ai/{INSTRUCTIONS}")
    manifest.setdefault("agents", {})["primary"] = new
    if f"ai/{old}.agent.md" in revisions:
        revisions[f"ai/{new}.agent.md"] = revisions.pop(f"ai/{old}.agent.md")
    (ai / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    log.append(f"ai/manifest.json: agents.primary = {new!r}")
    res = R.fast_forward(project_dir)
    log.append(f"re-rendered the managed pack files via revs ff ({len(res['applied'])} applied; any pending "
               "fast-forwards landed too)")
    guarded_new = {f"ai/{new}.agent.md" if rel == f"ai/{old}.agent.md" else rel for rel in guarded}
    for rel, v in res["skipped"]:
        if rel in guarded_new:
            warnings.append(f"{rel} is {v} and still carries the old name - merge it by hand (update-project)")
        else:
            log.append(f"{rel} is {v} (unrelated to the rename) - left for update-project")
    log.append(f"prose may still name the old role: grep -rn '{old}' {project_dir} --include='*.md' "
               "--exclude-dir=.git")
    return log, warnings


def main(argv: "list[str] | None" = None) -> int:
    parser = argparse.ArgumentParser(prog="solaris.tools.agents", description=__doc__.splitlines()[0])
    parser.add_argument("--dir", required=True, help="project dir holding ai/ (embedded mode: the repo root)")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--check", action="store_true",
                      help="validate the primary, the role briefs beside it, and the shared ai/instructions.md "
                           "(default)")
    mode.add_argument("--rename-primary", metavar="ROLE",
                      help="rename the primary persona (its file, the manifest, the rendered pack files)")
    args = parser.parse_args(argv)
    project_dir = Path(args.dir)
    try:
        if args.rename_primary is not None:
            log, warnings = rename_primary(project_dir, args.rename_primary)
            for line in log:
                print(f"agents: {line}")
            for line in warnings:
                print(f"agents: WARNING {line}")
            return 1 if warnings else 0
        return cmd_check(project_dir)
    except ValueError as exc:
        print(f"agents: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
