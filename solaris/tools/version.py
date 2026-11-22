# Copyright 2026 Mikhail Yurasov <me@yurasov.me>
# SPDX-License-Identifier: Apache-2.0

"""Solaris versioning + migration-chain engine (stdlib only).

The framework version is the single source of truth in ``pyproject.toml`` (``[project].version``). An
ai-pack records the framework version it was written/updated at in ``<project>/<pack>/manifest.json``
(``framework_version``); ``<pack>`` is the project's pack folder (``aipack/`` by default, ``ai/`` in projects
made before 0.39.0), found by solaris.tools.pack. Plugins version independently in
``plugins/<name>/manifest.json``; the
materialized version is recorded per-plugin in the project's manifest. The project's own content is
versioned by a plain-text ``.version`` file (bare MAJOR.MINOR.PATCH) at the project root - the one
place a ``.version`` file exists; framework and plugin versions stay in their manifests.

Migration files live at ``solaris/migrations/<to_version>.md`` with simple frontmatter; there is no
registry file - this module scans them directly.

Run as a module::

    uv run -m solaris.tools.version current
    uv run -m solaris.tools.version check --dir projects/todo
    uv run -m solaris.tools.version chain --dir projects/todo
    uv run -m solaris.tools.version set --dir projects/todo 0.2.0
    uv run -m solaris.tools.version plugin --dir projects/isaac-lab --plugin nvidia-isaac-lab
    uv run -m solaris.tools.version set-plugin --dir projects/isaac-lab --plugin nvidia-isaac-lab [VERSION]
    uv run -m solaris.tools.version check-plugins --dir projects/isaac-lab
    uv run -m solaris.tools.version project --dir projects/todo
    uv run -m solaris.tools.version project-set --dir projects/todo 1.0.0
    uv run -m solaris.tools.version project-bump --dir projects/todo minor
    uv run -m solaris.tools.version update --dir projects/todo [--dry-run]

``update`` brings a project's ai-pack to the current release in one step, when no framework migration is needed
(a patch release, as a rule). It runs ``revs ff``, ``version set`` to the current framework version, ``version
set-plugin`` for each copied plugin that is behind its source, a second ``revs ff`` (it re-renders the files that
print those versions) and ``revs baseline``; then its checks: ``version check``, ``version check-plugins``,
``plugins check``, ``revs classify`` with every file in sync, ``agents --check``, and ``plugin files`` (every file
under a copied plugin's ``shared/`` that revs does not track is in the pack as its source has it; revs tracks .md,
.py, .js, .sh and .css files and a .json file that carries ``"_rev"``, and never copies another kind). It prints:

- the versions before and after (the framework and each plugin);
- every file it changed, with paths from the project root, for the project's own session to commit;
- each plugin migration file on the way (``plugins/<name>/migrations/<to_version>.md``; one whose version cannot
  be read is listed as ``(version unknown)``) as steps for the project's session;
- each file the pack's baseline names that no master ships any more and that is still in the pack (a plugin
  dropped it), before ``revs baseline`` forgets it: the project's session removes it;
- one line per check.

It writes only the project's managed files and ``<pack>/manifest.json``, and nothing at all when there is nothing
to update. It never merges a plugin's ``mcps.json`` into the project's MCP config (install-plugin does that). It
stops before any change, naming each reason and how to resolve it, when:

- ``revs classify`` shows a file that is not in sync, fast-forward or missing (a conflict or a local change);
- ``revs ff`` would replace the pack's ``init`` or ``refresh`` skill, which a project fills in (the stub check of
  the update-project skill comes first);
- a master of the project's managed files does not match its ledger entry (``solaris/revisions.json``, or
  ``plugins/<name>/revisions.json``): it has none, its revision or content differs, or the ledger cannot be read.
  That is work in progress in the checkout, and it is never copied into a project;
- the framework has a migration to run on the way (``version chain``), or the pack is newer than the framework;
- a plugin has no source under ``plugins/``, its source is older than the copy, or a ``plugins`` entry of the
  manifest is not an object with a name;
- a required plugin dependency is not attached (``plugins check``).

The plugin reasons need install-plugin, an unreleased master its release, the others the update-project skill.
When a ``revs ff`` skips a file (it changed after the plan was made, by the project's own session or a file sync,
and now needs a merge), the steps stop there: the baseline is not recorded, so the file keeps its verdict and no
later run overwrites it. ``--dry-run`` runs the same steps and checks on a scratch copy (the root ``AGENTS.md`` and
the pack folder without any ``.memory/``, in a temporary folder ``solaris-update-*`` that is removed afterwards),
prints the same report, and leaves the project as it is. It refuses a pack that holds a symlink (a write to the
copy would go through the link); a pack folder that is itself a link is fine.

Exit codes of ``update``: 0 updated (or nothing to update) and every check passed; 3 stopped before any change; 1
an error (no ai-pack, a manifest that cannot be read, a write that failed part way, a dry run that cannot be
made), a check that failed after the update, or a file that ``revs ff`` skipped (the output names it; with
``--dry-run``: it would). ``check`` exits 0 on a match, 1 when the pack is behind, 2 when it is ahead;
``check-plugins`` exits 1 when a copied plugin is not at its source's version or a source is missing; the other
commands exit 0, or 1 on an error.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import tempfile
import tomllib
from dataclasses import dataclass
from pathlib import Path

from solaris.tools import pack as P
from solaris.tools.fileio import write_text_atomic

REPO_ROOT = Path(__file__).resolve().parents[2]
PYPROJECT = REPO_ROOT / "pyproject.toml"
MIGRATIONS_DIR = REPO_ROOT / "solaris" / "migrations"
PLUGINS_DIR = REPO_ROOT / "plugins"
REFUSED = 3   # update: stopped before any change (the reasons are printed)
FILL_INS = ("init.skill.md", "refresh.skill.md")   # pack skills a project fills in: update never replaces one
SCRATCH_SKIP = (".memory", "__pycache__", ".git")   # never copied for a dry run, at any depth of the pack
# How to resolve each kind of reason to stop; update prints the ones that apply.
CURES = {
    "merge": "a merge, a skill the project fills in, a framework migration or a newer pack: the update-project "
             "skill (solaris/skills/update-project.skill.md)",
    "plugin": "a plugin's source, a dependency or a manifest entry: the install-plugin skill "
              "(solaris/skills/install-plugin.skill.md)",
    "master": "an unreleased master: it is work in progress in this Solaris checkout. Wait until whoever edits it "
              "has released it (its revision bumped, its ledger rebuilt); never rebuild a ledger to get past this",
}


# --------------------------------------------------------------------------- semver

@dataclass(frozen=True, order=True)
class Version:
    major: int
    minor: int
    patch: int

    def __str__(self) -> str:  # noqa: D105
        return f"{self.major}.{self.minor}.{self.patch}"


def parse(s: "str | Version") -> Version:
    if isinstance(s, Version):
        return s
    text = str(s).strip().lstrip("v")
    parts = text.split(".")
    if len(parts) != 3 or not all(p.isdigit() for p in parts):
        raise ValueError(f"not a MAJOR.MINOR.PATCH version: {s!r}")
    a, b, c = (int(p) for p in parts)
    return Version(a, b, c)


def compare(a: "str | Version", b: "str | Version") -> int:
    """Return -1, 0, or 1 for a < b, a == b, a > b."""
    va, vb = parse(a), parse(b)
    return (va > vb) - (va < vb)


def bump_kind(from_v: "str | Version", to_v: "str | Version") -> str:
    """One of: same, downgrade, major, minor, patch."""
    f, t = parse(from_v), parse(to_v)
    if f == t:
        return "same"
    if t < f:
        return "downgrade"
    if t.major != f.major:
        return "major"
    if t.minor != f.minor:
        return "minor"
    return "patch"


# --------------------------------------------------------------------------- framework version

def framework_version(pyproject: Path = PYPROJECT) -> str:
    with open(pyproject, "rb") as fh:
        data = tomllib.load(fh)
    return str(data["project"]["version"])


# --------------------------------------------------------------------------- manifest.json

def _manifest_path(project_dir: "str | Path") -> Path:
    """The ai-pack's manifest.json, whatever the pack folder is named (PackError: no pack, or several)."""
    return P.require_pack(project_dir) / "manifest.json"


def read_manifest(project_dir: "str | Path") -> dict:
    with open(_manifest_path(project_dir), encoding="utf-8") as fh:
        return json.load(fh)


def write_manifest(project_dir: "str | Path", data: dict) -> None:
    write_text_atomic(_manifest_path(project_dir), json.dumps(data, indent=2) + "\n")


def aipack_version(project_dir: "str | Path") -> str:
    return str(read_manifest(project_dir)["framework_version"])


def set_aipack_version(project_dir: "str | Path", version: str) -> None:
    parse(version)  # validate before writing
    manifest = read_manifest(project_dir)
    manifest["framework_version"] = str(version)
    write_manifest(project_dir, manifest)


# --------------------------------------------------------------------------- project version (.version)

VERSION_FILE = ".version"


def project_version_path(project_dir: "str | Path") -> Path:
    return Path(project_dir) / VERSION_FILE


def project_version(project_dir: "str | Path") -> str:
    """The project's own semver from its root .version file (normalized MAJOR.MINOR.PATCH)."""
    path = project_version_path(project_dir)
    if not path.is_file():
        raise FileNotFoundError(f"no {VERSION_FILE} at {path} (seed one: project-set --dir <project> 0.1.0)")
    return str(parse(path.read_text(encoding="utf-8")))


def set_project_version(project_dir: "str | Path", version: "str | Version") -> str:
    v = parse(version)  # validate before writing
    write_text_atomic(project_version_path(project_dir), f"{v}\n")
    return str(v)


def bump_project_version(project_dir: "str | Path", level: str) -> str:
    v = parse(project_version(project_dir))
    if level == "major":
        v = Version(v.major + 1, 0, 0)
    elif level == "minor":
        v = Version(v.major, v.minor + 1, 0)
    elif level == "patch":
        v = Version(v.major, v.minor, v.patch + 1)
    else:
        raise ValueError(f"bump level must be major, minor, or patch (got {level!r})")
    return set_project_version(project_dir, v)


def plugin_recorded_version(project_dir: "str | Path", name: str) -> "str | None":
    for entry in read_manifest(project_dir).get("plugins", []):
        if isinstance(entry, dict) and entry.get("name") == name:
            return entry.get("version")
    return None


def plugin_source_version(name: str, plugins_dir: "Path | None" = None) -> str:
    with open(Path(plugins_dir or PLUGINS_DIR) / name / "manifest.json", encoding="utf-8") as fh:
        return str(json.load(fh)["version"])


def set_plugin_version(project_dir: "str | Path", name: str, version: "str | None" = None) -> str:
    """Record a copied plugin's version in the ai-pack manifest (default: its source's version); the version written.
    The owner approved, 2026-11-17: updating a copy (install-plugin, update-project) needs no hand edit. A linked
    plugin records no version (ValueError), nor does one that is not attached (KeyError)."""
    manifest = read_manifest(project_dir)
    entry = next((e for e in manifest.get("plugins", []) if isinstance(e, dict) and e.get("name") == name), None)
    if entry is None:
        raise KeyError(f"plugin {name!r} is not attached to {project_dir}")
    if entry.get("mode") == "link":
        raise ValueError(f"plugin {name!r} is linked: it runs the live source and records no version")
    value = str(parse(version if version is not None else plugin_source_version(name)))
    entry["version"] = value
    write_manifest(project_dir, manifest)
    return value


# --------------------------------------------------------------------------- migrations

def migration_frontmatter(path: "str | Path") -> dict:
    """Parse scalar ``key: value`` lines from the frontmatter block (between the first two ``---`` fences).

    Deliberately minimal (stdlib only): handles strings, quoted strings, and booleans. List values
    (e.g. ``touches:``) are ignored - the chain logic does not need them.
    """
    text = Path(path).read_text(encoding="utf-8")
    # tolerate a leading "_Rev. N_" marker above the frontmatter (same as the skill loader; without
    # this, a rev-stamped migration silently drops out of the migration chain)
    text = re.sub(r"\A_Rev\.\s*\d+_\s*\n+", "", text)
    if not text.startswith("---"):
        return {}
    body = text[3:]
    end = body.find("\n---")
    if end == -1:
        return {}
    out: dict = {}
    for raw in body[:end].splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or line.startswith("- "):
            continue
        if ":" not in line:
            continue
        key, _, val = line.partition(":")
        key, val = key.strip(), val.strip()
        if not key or not val:
            continue
        if len(val) >= 2 and val[0] == val[-1] and val[0] in "\"'":
            val = val[1:-1]
        low = val.lower()
        out[key] = (low == "true") if low in ("true", "false") else val
    return out


def scan_migrations(migrations_dir: Path = MIGRATIONS_DIR) -> list[dict]:
    """All migration entries (each with at least to_version/from_version/file), unordered."""
    directory = Path(migrations_dir)
    if not directory.exists():
        return []
    rows: list[dict] = []
    for path in sorted(directory.glob("*.md")):
        if path.name in ("README.md", "template.md"):
            continue
        fm = migration_frontmatter(path)
        if "to_version" in fm and "from_version" in fm:
            fm["file"] = path.name
            rows.append(fm)
    return rows


def find_chain(from_v: str, to_v: str, migrations_dir: Path = MIGRATIONS_DIR) -> list[dict]:
    """Ordered migrations advancing from_v -> to_v: every one with from_v < to_version <= to_v.

    Keyed on to_version alone; from_version is informational. Exact from_version linking used to
    break the chain whenever a patch release (which never authors a migration) sat between two
    migration points, silently reporting an empty chain.
    """
    if compare(from_v, to_v) >= 0:
        return []
    rows = [
        r for r in scan_migrations(migrations_dir)
        if compare(str(r["to_version"]), from_v) > 0 and compare(str(r["to_version"]), to_v) <= 0
    ]
    rows.sort(key=lambda r: parse(str(r["to_version"])))
    return rows


# --------------------------------------------------------------------------- update (no framework migration)

def plugin_migrations(name: str, from_v: "str | None", to_v: str, plugins_dir: "Path | None" = None) -> list[str]:
    """A plugin's migration files on the way from ``from_v`` (not included; None: from the start) to ``to_v``,
    oldest first, as paths from the Solaris root (``plugins/<name>/migrations/<to_version>.md``). A file's version
    is its frontmatter's ``to_version``, else its name. A file whose version cannot be read comes last, marked
    ``(version unknown)``: it may be a step too."""
    directory = Path(plugins_dir or PLUGINS_DIR) / name / "migrations"
    rows, unknown = [], []
    for path in sorted(directory.glob("*.md")) if directory.is_dir() else ():
        if path.name in ("README.md", "template.md"):
            continue
        rel = f"plugins/{name}/migrations/{path.name}"
        try:
            v = parse(str(migration_frontmatter(path).get("to_version") or path.stem))
        except (OSError, ValueError):
            unknown.append(f"{rel} (version unknown)")
            continue
        if (from_v is None or v > parse(from_v)) and v <= parse(to_v):
            rows.append((v, rel))
    return [rel for _v, rel in sorted(rows)] + unknown


def _from_root(path: "str | Path", root: Path) -> str:
    """A path for a message: from ``root`` when it is inside, else in full."""
    try:
        return Path(path).relative_to(root).as_posix()
    except ValueError:
        return str(path)


def unreleased_masters(project_dir: "str | Path") -> list[str]:
    """One line per master of the project's managed files that is not a released revision: it has no entry in its
    ledger (``solaris/revisions.json``, or ``plugins/<name>/revisions.json`` for a plugin's shared file), its
    revision or content differs from that entry, or it or the ledger cannot be read. Such a master is work in
    progress in the checkout, and update never copies one into a project."""
    from solaris.tools import revs as R
    root, ledgers, out = R.REPO_ROOT, {}, []
    for master, _proj, _rel in R.materialized_map(Path(project_dir), R.TEMPLATE_DIR, R.PLUGINS_DIR):
        try:
            parts = master.relative_to(R.PLUGINS_DIR).parts
            ledger, key = R.PLUGINS_DIR / parts[0] / "revisions.json", "/".join(parts[1:])
        except ValueError:   # a framework master: the framework ledger, keyed from the Solaris root
            ledger, key = R.LEDGER_PATH, _from_root(master, root)
        if ledger not in ledgers:
            try:
                ledgers[ledger] = json.loads(ledger.read_text(encoding="utf-8"))["files"]
            except (OSError, ValueError, KeyError, TypeError):
                ledgers[ledger] = None
        shown, book = _from_root(master, root), _from_root(ledger, root)
        if not isinstance(ledgers[ledger], dict):
            out.append(f"unreleased master: {shown} (no readable ledger {book})")
            continue
        try:
            rev, digest = R.file_rev_hash(master)
        except (OSError, ValueError) as exc:
            out.append(f"unreleased master: {shown} (cannot read it: {exc})")
            continue
        entry = ledgers[ledger].get(key)
        if not isinstance(entry, dict):
            out.append(f"unreleased master: {shown} (no entry in {book})")
        elif entry.get("hash") != digest or entry.get("rev") != (1 if rev is None else rev):
            out.append(f"unreleased master: {shown} (differs from its entry in {book})")
    return out


def update_plan(project_dir: "str | Path") -> dict:
    """What ``update`` would do, read-only: the framework step, each attached plugin's step with the plugin
    migrations on the way, the files revs ff would copy (``pending``), the reasons to stop before any change
    (``blockers``; empty when it can run) and how to resolve them (``cures``)."""
    from solaris.tools import plugins as PL   # neither module imports version: no cycle
    from solaris.tools import revs as R
    project_dir = Path(project_dir)
    pack = P.require_pack(project_dir)
    manifest = read_manifest(project_dir)
    recorded, current = str(manifest["framework_version"]), framework_version()
    kind = bump_kind(recorded, current)
    blockers: list[str] = []
    cures: dict = {}   # kept in the order they came up
    plan = {"pack": pack.name, "framework": {"from": recorded, "to": current, "kind": kind}, "plugins": [],
            "pending": [], "blockers": blockers, "cures": cures}
    entries = manifest.get("plugins") or []
    for entry in entries if isinstance(entries, list) else ():
        if not isinstance(entry, dict) or not isinstance(entry.get("name"), str) or not entry["name"]:
            blockers.append(f"a plugins entry of {pack.name}/manifest.json is not an object with a name: {entry!r}")
            cures["plugin"] = CURES["plugin"]
    if blockers:
        return plan   # revs cannot map the managed files of such a manifest: nothing more can be read
    stubs = {f"{pack.name}/skills/{name}" for name in FILL_INS}
    # the module's paths, read now (tests point them at a temporary tree)
    for row in R.classify(project_dir, R.TEMPLATE_DIR, R.PLUGINS_DIR):
        rel, verdict = row["rel"], row["verdict"]
        if verdict not in ("in-sync", "fast-forward", "missing"):
            blockers.append(f"needs a merge ({verdict}): {rel}")
            cures["merge"] = CURES["merge"]
        elif verdict == "fast-forward" and rel in stubs:
            blockers.append(f"a skill the project fills in would be replaced: {rel} (the stub check of "
                            "update-project section 1 comes first)")
            cures["merge"] = CURES["merge"]
        elif verdict != "in-sync":
            plan["pending"].append((rel, verdict))
    masters = unreleased_masters(project_dir)
    if masters:
        blockers += masters
        cures["master"] = CURES["master"]
    if kind == "downgrade":
        blockers.append(f"the ai-pack records framework {recorded}, newer than this checkout's {current}: update "
                        "the framework checkout first")
        cures["merge"] = CURES["merge"]
    chain = find_chain(recorded, current, MIGRATIONS_DIR)
    if chain:
        files = ", ".join(f"solaris/migrations/{row['file']}" for row in chain)
        blockers.append(f"framework {recorded} -> {current} has {len(chain)} migration(s) to run: {files}")
        cures["merge"] = CURES["merge"]
    for entry in entries:
        name, linked = entry["name"], entry.get("mode") == "link"
        row = {"name": name, "from": None, "to": None, "status": "linked" if linked else "ok", "migrations": []}
        plan["plugins"].append(row)
        try:
            row["from"] = None if linked else str(parse(entry.get("version")))
        except ValueError:
            pass   # a copy with no readable version recorded: update records the source's
        try:
            row["to"] = str(parse(plugin_source_version(name)))
        except (OSError, KeyError, ValueError):   # no plugins/<name>/manifest.json, or no version in it
            row["status"] = "missing"
            blockers.append(f"plugin {name}: no source with a version under plugins/{name}/")
            cures["plugin"] = CURES["plugin"]
            continue
        if linked:
            continue
        step = -1 if row["from"] is None else compare(row["from"], row["to"])
        if step > 0:
            row["status"] = "ahead"
            blockers.append(f"plugin {name}: the ai-pack records {row['from']}, newer than the source's "
                            f"{row['to']}: update plugins/{name}/ first")
            cures["plugin"] = CURES["plugin"]
        elif step < 0:
            row["status"] = "behind"
            row["migrations"] = plugin_migrations(name, row["from"], row["to"])
    deps = PL.check(project_dir, PLUGINS_DIR.parent)
    for dep in deps["missing"]:
        blockers.append(f"a required plugin dependency is not attached: {' > '.join(dep['chain'])}")
    blockers += [f"plugin dependencies: {problem}" for problem in deps["problems"]]
    if deps["missing"] or deps["problems"]:
        cures["plugin"] = CURES["plugin"]
    return plan


def _managed_state(project_dir: Path) -> dict:
    """{path from the project root: sha256 of its bytes, None when absent} for the files update may write: the
    managed files and the pack manifest."""
    from solaris.tools import revs as R
    pack = P.require_pack(project_dir)
    rels = [rel for _master, _proj, rel in R.materialized_map(project_dir, R.TEMPLATE_DIR, R.PLUGINS_DIR)]
    out = {}
    for rel in [f"{pack.name}/manifest.json", *rels]:
        try:
            out[rel] = hashlib.sha256((project_dir / rel).read_bytes()).hexdigest()
        except OSError:
            out[rel] = None
    return out


def _skipped_name(name: str) -> bool:
    """A file or folder name that is never a plugin's shared file: hidden, a cache, a sync leftover."""
    return (name.startswith(".") or name == "__pycache__" or name.endswith(".pyc") or name == "Thumbs.db"
            or name.startswith("~syncthing~") or ".sync-conflict-" in name)


def untracked_shared(project_dir: "str | Path") -> list[str]:
    """One line per file under a copied plugin's ``shared/`` that revs does not track (so revs ff never copies
    it) and whose copy in the pack is missing or differs from the source. revs tracks .md, .py, .js, .sh and .css
    files, and a .json file that carries a ``"_rev"`` field; install-plugin copies everything else."""
    from solaris.tools import revs as R
    project_dir = Path(project_dir)
    pack = P.require_pack(project_dir)
    out = []
    for entry in read_manifest(project_dir).get("plugins") or []:
        if not isinstance(entry, dict) or entry.get("mode") == "link" or not isinstance(entry.get("name"), str):
            continue
        name = entry["name"]
        shared = R.PLUGINS_DIR / name / "shared"
        tracked = set(R.iter_plugin_shared(R.PLUGINS_DIR / name))
        home = f"{pack.name}/{R._plugin_home(pack, name)}"   # where revs puts this plugin's files in the pack
        for source in sorted(shared.rglob("*")) if shared.is_dir() else ():
            sub = source.relative_to(shared)
            if source in tracked or not source.is_file() or any(_skipped_name(part) for part in sub.parts):
                continue
            rel, origin = f"{home}/{sub.as_posix()}", f"plugins/{name}/shared/{sub.as_posix()}"
            try:
                same = (project_dir / rel).read_bytes() == source.read_bytes()
            except FileNotFoundError:
                out.append(f"{rel} is missing (source {origin})")
                continue
            except OSError as exc:
                out.append(f"{rel} cannot be compared with {origin} ({exc})")
                continue
            if not same:
                out.append(f"{rel} differs from {origin}")
    return out


def update_checks(project_dir: "str | Path") -> list[tuple]:
    """The checks after an update, each (name, passed, detail): ``version check``, ``version check-plugins``,
    ``plugins check``, ``revs classify`` (every file in sync), ``agents --check``, and ``plugin files`` (the
    shared files revs does not track are in the pack as their sources have them: untracked_shared)."""
    from solaris.tools import agents as A
    from solaris.tools import plugins as PL
    from solaris.tools import revs as R
    project_dir = Path(project_dir)
    manifest = read_manifest(project_dir)
    recorded, current = str(manifest["framework_version"]), framework_version()
    checks = [("version check", recorded == current,
               f"at the framework's version ({current})" if recorded == current
               else f"ai-pack {recorded}, framework {current}")]
    off = []
    for entry in manifest.get("plugins") or []:
        name = entry.get("name") if isinstance(entry, dict) else entry
        try:
            source = plugin_source_version(name)
        except (OSError, KeyError, ValueError, TypeError):
            off.append(f"{name}: source missing")
            continue
        if isinstance(entry, dict) and entry.get("mode") == "link":
            continue   # a linked plugin runs the live source: no recorded version to compare
        try:
            same = compare(entry.get("version") if isinstance(entry, dict) else None, source) == 0
        except ValueError:
            same = False
        if not same:
            off.append(f"{name}: recorded {entry.get('version') if isinstance(entry, dict) else None}, "
                       f"source {source}")
    checks.append(("version check-plugins", not off,
                   "; ".join(off) or "every copied plugin at its source's version"))
    try:
        deps = PL.check(project_dir, PLUGINS_DIR.parent)
        bad = [" > ".join(dep["chain"]) + " not attached" for dep in deps["missing"]] + deps["problems"]
    except PL.PluginsError as exc:
        bad = [str(exc)]
    checks.append(("plugins check", not bad, "; ".join(bad) or "every required dependency is attached"))
    rows = R.classify(project_dir, R.TEMPLATE_DIR, R.PLUGINS_DIR)
    out = [f"{row['rel']} ({row['verdict']})" for row in rows if row["verdict"] != "in-sync"]
    checks.append(("revs classify", not out, "; ".join(out) or f"{len(rows)} file(s) in sync"))
    try:
        _primary, roles, problems = A.load_personas(project_dir)
    except (ValueError, OSError) as exc:
        roles, problems = [], [str(exc)]
    checks.append(("agents --check", not problems,
                   "; ".join(problems) or f"the primary persona and {len(roles)} role persona(s) valid"))
    loose = untracked_shared(project_dir)
    checks.append(("plugin files", not loose,
                   "not copied by revs (install-plugin copies them): " + "; ".join(loose) if loose
                   else "every shared file revs does not track is in the pack as its source has it"))
    return checks


def _settled(project_dir: Path, plan: dict) -> bool:
    """True when the update has nothing to write: no file to copy, no version to set, and the pack manifest
    already holds, byte for byte, the baseline that revs baseline would record. Then no step runs, so the
    manifest is not rewritten with the same content (a file sync would see a changed file in a live project)."""
    from solaris.tools import revs as R
    if plan["pending"] or plan["framework"]["kind"] != "same" or any(r["status"] == "behind" for r in plan["plugins"]):
        return False
    text = _manifest_path(project_dir).read_text(encoding="utf-8")
    manifest, baseline = json.loads(text), {}
    for _master, proj, rel in R.materialized_map(project_dir, R.TEMPLATE_DIR, R.PLUGINS_DIR):
        if proj.exists():
            rev, digest = R.file_rev_hash(proj)
            baseline[rel] = {"rev": rev, "hash": digest}
    manifest["revisions"] = baseline
    return json.dumps(manifest, indent=2) + "\n" == text


def _dropped(project_dir: Path) -> list[str]:
    """The files the pack's baseline still names that no master ships any more (a plugin dropped a shared file, a
    plugin was detached) and that are still in the project. Read before revs baseline forgets them."""
    from solaris.tools import revs as R
    managed = {rel for _master, _proj, rel in R.materialized_map(project_dir, R.TEMPLATE_DIR, R.PLUGINS_DIR)}
    baseline = read_manifest(project_dir).get("revisions") or {}
    return [rel for rel in baseline if rel not in managed and (project_dir / rel).is_file()]


def apply_update(project_dir: "str | Path", plan: dict) -> dict:
    """Run the update's steps on ``project_dir`` (``update_plan`` found no blocker): revs ff, the framework
    version, each behind plugin's version, a second revs ff that re-renders the files printing those values, and
    revs baseline. When a revs ff skips a file (it changed after the plan was made and now needs a merge), the
    steps stop there and the baseline is not recorded, so the file keeps its verdict. Returns {"changed": [(path
    from the project root, was it new)], "skipped": [(path, verdict)], "dropped": _dropped(), "checks":
    update_checks()}."""
    from solaris.tools import revs as R
    project_dir = Path(project_dir)
    where = (project_dir, R.TEMPLATE_DIR, R.PLUGINS_DIR)
    before = _managed_state(project_dir)
    skipped, dropped = [], []
    if not _settled(project_dir, plan):
        skipped = list(R.fast_forward(*where)["skipped"])
        if not skipped:
            if plan["framework"]["kind"] != "same":
                set_aipack_version(project_dir, plan["framework"]["to"])
            for row in plan["plugins"]:
                if row["status"] == "behind":
                    set_plugin_version(project_dir, row["name"], row["to"])
            skipped = list(R.fast_forward(*where)["skipped"])
        if not skipped:
            dropped = _dropped(project_dir)
            R.record_baseline(*where)
    after = _managed_state(project_dir)
    changed = [(rel, before.get(rel) is None) for rel in after if after[rel] != before.get(rel)]
    return {"changed": changed, "skipped": skipped, "dropped": dropped, "checks": update_checks(project_dir)}


def _links(folder: "str | Path") -> list[str]:
    """The symlinks inside a folder, as paths from it; what the scratch copy leaves out is not looked at."""
    out = []
    for here, dirs, files in os.walk(folder):   # a linked folder is listed in dirs and not walked into
        dirs[:] = [d for d in dirs if d not in SCRATCH_SKIP]
        out += [os.path.relpath(os.path.join(here, name), folder) for name in dirs + files
                if os.path.islink(os.path.join(here, name))]
    return sorted(out)


def _scratch_copy(project_dir: Path, scratch: Path) -> Path:
    """A copy of all that update reads and writes in a project: the root AGENTS.md and the pack folder, without
    its .memory/ (private, and never read here). ValueError when the pack holds a symlink: the copy would keep
    the link, and a write to the copy would go through it to the real file."""
    pack = P.require_pack(project_dir)
    real = os.path.realpath(pack)   # the pack folder may itself be a link: its content is copied
    links = _links(real)
    if links:
        raise ValueError(f"the pack holds a symlink ({pack.name}/{links[0]}"
                         + (f" and {len(links) - 1} more" if len(links) > 1 else "")
                         + "): a dry run cannot copy it safely")
    target = scratch / "project"
    target.mkdir()
    if (project_dir / "AGENTS.md").is_file():
        shutil.copyfile(project_dir / "AGENTS.md", target / "AGENTS.md")   # a linked file is copied as a file
    shutil.copytree(real, target / pack.name, symlinks=True, ignore=shutil.ignore_patterns(*SCRATCH_SKIP))
    if _links(target / pack.name):   # a link that came between the look and the copy
        raise ValueError("the pack got a symlink while it was copied: a dry run cannot copy it safely")
    return target


# --------------------------------------------------------------------------- CLI

def _cmd_current(_args: argparse.Namespace) -> int:
    print(framework_version())
    return 0


def _cmd_aipack(args: argparse.Namespace) -> int:
    print(aipack_version(args.dir))
    return 0


def _cmd_check(args: argparse.Namespace) -> int:
    current, recorded = framework_version(), aipack_version(args.dir)
    kind = bump_kind(recorded, current)
    if kind == "same":
        print(f"up to date ({current})")
        return 0
    if kind == "downgrade":
        print(f"downgrade: ai-pack {recorded} is newer than framework {current}")
        return 2
    chain = find_chain(recorded, current)
    print(f"migrate needed: {recorded} -> {current} ({kind}); {len(chain)} migration(s) on disk")
    return 1


def _cmd_chain(args: argparse.Namespace) -> int:
    current, recorded = framework_version(), aipack_version(args.dir)
    chain = find_chain(recorded, current)
    if not chain:
        print(f"no migrations ({recorded} -> {current})")
        return 0
    for row in chain:
        title = row.get("title", "")
        print(f"{row['from_version']} -> {row['to_version']}  {title}  [{row['file']}]")
    return 0


def _rerender(project_dir: "str | Path") -> None:
    """Re-render the managed files that print manifest values (the pack README's plugin list and framework version,
    the primary persona's version), so a setter never leaves them stale (the orchestrator's report, 2026-11-17).
    This is revs ff: it records the baseline of each file it writes, and never overwrites a project's own edit (it
    skips it, named here, and its baseline stays, so the conflict still shows; no revs baseline here)."""
    from solaris.tools import revs as R   # revs never imports version: no cycle
    # the module's paths, read now (tests point them at a temporary tree)
    result = R.fast_forward(Path(project_dir), R.TEMPLATE_DIR, R.PLUGINS_DIR)
    written = [rel for rel, v in result["applied"] if v in ("fast-forward", "missing")]
    if written:
        print("re-rendered: " + ", ".join(written) + " (the pack README among them when its values changed)")
    for rel, v in result["skipped"]:
        print(f"needs a merge ({v}): {rel} - update-project section 1")


def _cmd_set(args: argparse.Namespace) -> int:
    set_aipack_version(args.dir, args.version)
    print(f"set framework_version = {args.version} in {_manifest_path(args.dir)}")
    _rerender(args.dir)
    return 0


def _cmd_plugin(args: argparse.Namespace) -> int:
    version = plugin_recorded_version(args.dir, args.plugin)
    if version is None:
        print(f"plugin {args.plugin!r} is not attached to {args.dir}")
        return 1
    print(version)
    return 0


def _cmd_set_plugin(args: argparse.Namespace) -> int:
    try:
        value = set_plugin_version(args.dir, args.plugin, args.version)
    except FileNotFoundError:
        print(f"plugin {args.plugin!r}: no source in plugins/ to read its version from; give one")
        return 1
    except KeyError as e:
        print(e.args[0])
        return 1
    except ValueError as e:
        print(e)
        return 1
    print(f"set plugin {args.plugin} = {value} in {_manifest_path(args.dir)}")
    _rerender(args.dir)
    return 0


def _cmd_check_plugins(args: argparse.Namespace) -> int:
    plugins = read_manifest(args.dir).get("plugins", [])
    if not plugins:
        print("no plugins attached")
        return 0
    rc = 0
    for entry in plugins:
        name, recorded = entry.get("name"), entry.get("version")
        linked = entry.get("mode") == "link"
        try:
            source = plugin_source_version(name)
        except FileNotFoundError:
            if linked:
                # a linked plugin has no materialized fallback - losing the source is a hard break
                print(f"{name}: linked, source MISSING (hard break: no materialized copy)")
            else:
                print(f"{name}: recorded {recorded}, source MISSING")
            rc = 1
            continue
        if linked:
            # link mode always runs the live source; no recorded version to drift
            print(f"{name}: linked, source {source} (live)")
            continue
        cmp = compare(recorded, source)
        status = "ok" if cmp == 0 else ("behind" if cmp < 0 else "ahead")
        if status != "ok":
            rc = 1
        print(f"{name}: recorded {recorded}, source {source} -> {status}")
    return rc


def _cmd_project(args: argparse.Namespace) -> int:
    try:
        print(project_version(args.dir))
    except (OSError, ValueError) as e:
        print(e)
        return 1
    return 0


def _cmd_project_set(args: argparse.Namespace) -> int:
    try:
        v = set_project_version(args.dir, args.version)
    except (OSError, ValueError) as e:
        print(e)
        return 1
    print(f"set project version = {v} in {args.dir}/{VERSION_FILE}")
    return 0


def _cmd_project_bump(args: argparse.Namespace) -> int:
    try:
        v = bump_project_version(args.dir, args.level)
    except (OSError, ValueError) as e:
        print(e)
        return 1
    print(v)
    return 0


def _cmd_update(args: argparse.Namespace) -> int:
    from solaris.tools import plugins as PL
    project_dir = Path(args.dir)
    try:
        plan = update_plan(project_dir)
    except (ValueError, KeyError, TypeError, OSError, PL.PluginsError) as exc:   # an unreadable or malformed manifest
        print(f"update: cannot read the project's state ({exc}); nothing changed")
        return 1
    fw = plan["framework"]
    step = f"{fw['from']} (no new version)" if fw["kind"] == "same" else f"{fw['from']} -> {fw['to']} ({fw['kind']})"
    print(f"update {project_dir}{' (dry run)' if args.dry_run else ''}: framework {step}")
    for row in plan["plugins"]:
        if row["status"] == "linked":
            print(f"  plugin {row['name']}: linked, live source {row['to']}")
        elif row["status"] == "missing":
            print(f"  plugin {row['name']}: {row['from'] or 'no version recorded'}, source missing")
        elif row["status"] == "ok":
            print(f"  plugin {row['name']}: {row['from']} (no new version)")
        else:
            print(f"  plugin {row['name']}: {row['from'] or 'no version recorded'} -> {row['to']}"
                  + (" (the source is older)" if row["status"] == "ahead" else ""))
    if plan["blockers"]:
        print("stopped, nothing changed:")
        for line in plan["blockers"]:
            print(f"  {line}")
        print("resolve, then run this again:")
        for line in plan["cures"].values():
            print(f"  {line}")
        return REFUSED
    try:
        if args.dry_run:
            with tempfile.TemporaryDirectory(prefix="solaris-update-") as scratch:
                result = apply_update(_scratch_copy(project_dir, Path(scratch)), plan)
        else:
            result = apply_update(project_dir, plan)
    except (ValueError, KeyError, TypeError, OSError) as exc:
        print(f"update: the dry run could not be made ({exc}); nothing was done" if args.dry_run else
              f"update: failed part way ({exc}); the files written so far stay. Remove the cause, see the pack's "
              f"state with revs classify --dir {project_dir}, then run this again")
        return 1
    changed, skipped = result["changed"], result["skipped"]
    would = "would change" if args.dry_run else "changed"
    if changed:
        print(f"files {would} ({len(changed)}; paths from the project root, for the project's own session to "
              "commit):")
        for rel, new in changed:
            print(f"  {rel}{' (new)' if new else ''}")
    else:
        print(f"files {would}: none")
    if skipped:
        print("changed while the update ran, needs a merge (the baseline was not recorded, so each keeps its "
              "verdict):")
        for rel, verdict in skipped:
            print(f"  {rel} ({verdict})")
    if result["dropped"]:
        print("no longer shipped by the framework or a plugin, still in the pack (the project's own session "
              "removes them):")
        for rel in result["dropped"]:
            print(f"  {rel}")
    steps = [rel for row in plan["plugins"] for rel in row["migrations"]]
    if steps:
        print("steps for the project's session (plugin migrations on the way; the copied files and the recorded "
              "versions are updated here, the rest of each file is for that session to apply):")
        for rel in steps:
            print(f"  {rel}")
    print("checks" + (" (on a scratch copy)" if args.dry_run else "") + ":")
    failed = 0
    for name, passed, detail in result["checks"]:
        failed += not passed
        print(f"  {name}: {'ok' if passed else 'FAILED'} - {detail}")
    if skipped:
        print(("result: dry run, the project is unchanged; the update would stop" if args.dry_run else
               "result: stopped") + f" part way: {len(skipped)} file(s) need a merge (update-project section 1), "
              "then run this again")
    elif args.dry_run:
        print("result: dry run, the project is unchanged; "
              + (f"{failed} check(s) would FAIL" if failed else "every check would pass"))
    else:
        print(f"result: {'updated' if changed else 'nothing to update'}, "
              + (f"{failed} check(s) FAILED" if failed else "every check passed"))
    return 1 if failed or skipped else 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="solaris.tools.version", description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("current", help="print the framework version").set_defaults(func=_cmd_current)

    def _with_dir(name: str, help_text: str):
        sp = sub.add_parser(name, help=help_text)
        sp.add_argument("--dir", required=True, help="project directory (the folder holding the ai-pack)")
        return sp

    _with_dir("aipack", "print the ai-pack's recorded framework version").set_defaults(func=_cmd_aipack)
    _with_dir("check", "compare ai-pack vs framework (exit 0 match / 1 migrate / 2 downgrade)").set_defaults(func=_cmd_check)
    _with_dir("chain", "print the migration chain that would be applied").set_defaults(func=_cmd_chain)

    sp_set = _with_dir("set", "write framework_version into the ai-pack manifest (re-renders the managed files)")
    sp_set.add_argument("version", help="semver MAJOR.MINOR.PATCH")
    sp_set.set_defaults(func=_cmd_set)

    sp_plugin = _with_dir("plugin", "print a plugin's recorded version in the ai-pack")
    sp_plugin.add_argument("--plugin", required=True, help="plugin name")
    sp_plugin.set_defaults(func=_cmd_plugin)

    sp_setp = _with_dir("set-plugin", "record a copied plugin's version in the ai-pack manifest (default: its "
                        "source's; re-renders the managed files)")
    sp_setp.add_argument("--plugin", required=True, help="plugin name")
    sp_setp.add_argument("version", nargs="?", help="semver MAJOR.MINOR.PATCH (default: the plugin source's version)")
    sp_setp.set_defaults(func=_cmd_set_plugin)

    _with_dir("check-plugins", "compare each attached plugin's recorded version vs its source").set_defaults(func=_cmd_check_plugins)

    _with_dir("project", "print the project's own version (root .version file)").set_defaults(func=_cmd_project)

    sp_pset = _with_dir("project-set", "write the project's own version into its root .version file")
    sp_pset.add_argument("version", help="semver MAJOR.MINOR.PATCH")
    sp_pset.set_defaults(func=_cmd_project_set)

    sp_pbump = _with_dir("project-bump", "increment the project's own version and print the result")
    sp_pbump.add_argument("level", choices=["major", "minor", "patch"], help="which component to bump")
    sp_pbump.set_defaults(func=_cmd_project_bump)

    sp_update = _with_dir("update", "bring the ai-pack to the current release when no framework migration is needed "
                          "(exit 0 done / 3 stopped, nothing changed / 1 an error, a failed check or a file "
                          "that needs a merge)")
    sp_update.add_argument("--dry-run", action="store_true",
                           help="print the same report from a scratch copy; the project is not changed")
    sp_update.set_defaults(func=_cmd_update)
    return parser


def main(argv: "list[str] | None" = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except P.PackError as exc:   # --dir holds no ai-pack, or several: one clean line, never a traceback
        print(exc)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
