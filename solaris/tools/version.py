"""Solaris versioning + migration-chain engine (stdlib only).

The framework version is the single source of truth in ``pyproject.toml`` (``[project].version``). An
ai-setup records the framework version it was written/updated at in ``<project>/ai/manifest.json``
(``framework_version``). Plugins version independently in ``plugins/<name>/manifest.json``; the
materialized version is recorded per-plugin in the project's manifest. There are no ``.version`` files.

Migration files live at ``solaris/migrations/<to_version>.md`` with simple frontmatter; there is no
registry file - this module scans them directly.

Run as a module::

    uv run -m solaris.tools.version current
    uv run -m solaris.tools.version check --dir projects/todo
    uv run -m solaris.tools.version chain --dir projects/todo
    uv run -m solaris.tools.version set --dir projects/todo 0.2.0
    uv run -m solaris.tools.version plugin --dir projects/isaac-lab --plugin nvidia-isaac-lab
    uv run -m solaris.tools.version check-plugins --dir projects/isaac-lab
"""

from __future__ import annotations

import argparse
import json
import tomllib
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
PYPROJECT = REPO_ROOT / "pyproject.toml"
MIGRATIONS_DIR = REPO_ROOT / "solaris" / "migrations"
PLUGINS_DIR = REPO_ROOT / "plugins"


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
    return Path(project_dir) / "ai" / "manifest.json"


def read_manifest(project_dir: "str | Path") -> dict:
    with open(_manifest_path(project_dir), encoding="utf-8") as fh:
        return json.load(fh)


def write_manifest(project_dir: "str | Path", data: dict) -> None:
    path = _manifest_path(project_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2)
        fh.write("\n")


def aisetup_version(project_dir: "str | Path") -> str:
    return str(read_manifest(project_dir)["framework_version"])


def set_aisetup_version(project_dir: "str | Path", version: str) -> None:
    parse(version)  # validate before writing
    manifest = read_manifest(project_dir)
    manifest["framework_version"] = str(version)
    write_manifest(project_dir, manifest)


def plugin_recorded_version(project_dir: "str | Path", name: str) -> "str | None":
    for entry in read_manifest(project_dir).get("plugins", []):
        if isinstance(entry, dict) and entry.get("name") == name:
            return entry.get("version")
    return None


def plugin_source_version(name: str, plugins_dir: Path = PLUGINS_DIR) -> str:
    with open(Path(plugins_dir) / name / "manifest.json", encoding="utf-8") as fh:
        return str(json.load(fh)["version"])


# --------------------------------------------------------------------------- migrations

def migration_frontmatter(path: "str | Path") -> dict:
    """Parse scalar ``key: value`` lines from the frontmatter block (between the first two ``---`` fences).

    Deliberately minimal (stdlib only): handles strings, quoted strings, and booleans. List values
    (e.g. ``touches:``) are ignored - the chain logic does not need them.
    """
    text = Path(path).read_text(encoding="utf-8")
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
    """Ordered migrations advancing from_v -> to_v. Empty if not advancing or no linking migrations."""
    if compare(from_v, to_v) >= 0:
        return []
    by_from = {str(r["from_version"]): r for r in scan_migrations(migrations_dir)}
    chain: list[dict] = []
    cur, target, seen = str(parse(from_v)), str(parse(to_v)), set()
    while compare(cur, target) < 0:
        step = by_from.get(cur)
        if step is None or str(step["to_version"]) in seen:
            break
        seen.add(str(step["to_version"]))
        chain.append(step)
        cur = str(parse(step["to_version"]))
    return chain


# --------------------------------------------------------------------------- CLI

def _cmd_current(_args: argparse.Namespace) -> int:
    print(framework_version())
    return 0


def _cmd_aisetup(args: argparse.Namespace) -> int:
    print(aisetup_version(args.dir))
    return 0


def _cmd_check(args: argparse.Namespace) -> int:
    current, recorded = framework_version(), aisetup_version(args.dir)
    kind = bump_kind(recorded, current)
    if kind == "same":
        print(f"up to date ({current})")
        return 0
    if kind == "downgrade":
        print(f"downgrade: ai-setup {recorded} is newer than framework {current}")
        return 2
    chain = find_chain(recorded, current)
    print(f"migrate needed: {recorded} -> {current} ({kind}); {len(chain)} migration(s) on disk")
    return 1


def _cmd_chain(args: argparse.Namespace) -> int:
    current, recorded = framework_version(), aisetup_version(args.dir)
    chain = find_chain(recorded, current)
    if not chain:
        print(f"no migrations ({recorded} -> {current})")
        return 0
    for row in chain:
        title = row.get("title", "")
        print(f"{row['from_version']} -> {row['to_version']}  {title}  [{row['file']}]")
    return 0


def _cmd_set(args: argparse.Namespace) -> int:
    set_aisetup_version(args.dir, args.version)
    print(f"set framework_version = {args.version} in {args.dir}/ai/manifest.json")
    return 0


def _cmd_plugin(args: argparse.Namespace) -> int:
    version = plugin_recorded_version(args.dir, args.plugin)
    if version is None:
        print(f"plugin {args.plugin!r} is not attached to {args.dir}")
        return 1
    print(version)
    return 0


def _cmd_check_plugins(args: argparse.Namespace) -> int:
    plugins = read_manifest(args.dir).get("plugins", [])
    if not plugins:
        print("no plugins attached")
        return 0
    rc = 0
    for entry in plugins:
        name, recorded = entry.get("name"), entry.get("version")
        try:
            source = plugin_source_version(name)
        except FileNotFoundError:
            print(f"{name}: recorded {recorded}, source MISSING")
            rc = 1
            continue
        cmp = compare(recorded, source)
        status = "ok" if cmp == 0 else ("behind" if cmp < 0 else "ahead")
        if status != "ok":
            rc = 1
        print(f"{name}: recorded {recorded}, source {source} -> {status}")
    return rc


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="solaris.tools.version", description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("current", help="print the framework version").set_defaults(func=_cmd_current)

    def _with_dir(name: str, help_text: str):
        sp = sub.add_parser(name, help=help_text)
        sp.add_argument("--dir", required=True, help="project directory (contains ai/manifest.json)")
        return sp

    _with_dir("aisetup", "print the ai-setup's recorded framework version").set_defaults(func=_cmd_aisetup)
    _with_dir("check", "compare ai-setup vs framework (exit 0 match / 1 migrate / 2 downgrade)").set_defaults(func=_cmd_check)
    _with_dir("chain", "print the migration chain that would be applied").set_defaults(func=_cmd_chain)

    sp_set = _with_dir("set", "write framework_version into the ai-setup manifest")
    sp_set.add_argument("version", help="semver MAJOR.MINOR.PATCH")
    sp_set.set_defaults(func=_cmd_set)

    sp_plugin = _with_dir("plugin", "print a plugin's recorded version in the ai-setup")
    sp_plugin.add_argument("--plugin", required=True, help="plugin name")
    sp_plugin.set_defaults(func=_cmd_plugin)

    _with_dir("check-plugins", "compare each attached plugin's recorded version vs its source").set_defaults(func=_cmd_check_plugins)
    return parser


def main(argv: "list[str] | None" = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
