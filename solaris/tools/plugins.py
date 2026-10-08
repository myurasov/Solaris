# Copyright 2026 Mikhail Yurasov <me@yurasov.me>
# SPDX-License-Identifier: Apache-2.0

"""Plugin dependencies and plugin-provided project types (stdlib only).

**Dependencies.** A plugin's ``manifest.json`` may declare the plugins it relies on::

    "dependencies": {
      "required": ["reporting"],
      "optional": [{"name": "resource-sharing", "why": "share hosts with the owner's other projects"}]
    }

Each entry is a plugin name or an object with ``name`` and an optional ``why`` (keys starting with ``_`` are
comments). Required dependencies are attached with the plugin, transitively and dependencies first; optional ones
are suggested, only those present under ``plugins/``. A cycle or a required dependency missing from ``plugins/`` is
an error that names the chain (``a > b > c``). A plugin is a non-hidden folder of ``plugins/`` holding a
``manifest.json``; a symlinked private checkout counts.

**Project types.** Core types are ``solaris/templates/projects/<type>.md``. A plugin contributes a type with
``plugins/<plugin>/<type>.project.md``, offered as ``<plugin>:<type>``, and optionally an overlay folder under the
plugin's ``project-types/``: the ``template`` key (``project-types/<name>``), or ``project-types/<type>/`` when that
folder exists; with symlinks resolved it must stay there. The file opens with frontmatter, ``key: value`` lines
between ``---`` lines: a value that parses as JSON is JSON (it may continue on following lines indented by two or
more spaces) and any other value is a plain string (its continuation lines folded into one). Keys: ``type`` (the
file name's stem), ``title`` and ``summary`` (all three required), ``defaults`` (an object: ``mode``, ``primary``,
``roles``, ``workspaces``, ``pack``), ``plugins`` (an object: ``required`` and ``optional`` lists, entries as in
``dependencies``), ``template`` and ``questions`` (a list of objects: ``key`` in lower case, ``ask``, optional
``required``, ``default`` and ``choices``). The body has the sections ``## Structure``, ``## Setup Steps`` and ``##
Hand-Off``. ``type`` reports anything else as invalid, and also overlay placeholders that name no question or that
apply-type does not fill, and overlay files that would write a file the framework manages (the root ``AGENTS.md``,
the pack's ``manifest.json``, ``README.md``, primary persona, rule, skill and info masters, anything under
``<pack>/plugins/``), names compared case-folded since the default macOS filesystem ignores case.

**Overlay.** ``apply-type`` copies the overlay into the project root (embedded mode: the repo root). Paths and the
text of UTF-8 files get the create-project placeholders (``{{SLUG}}``, ``{{NAME}}``, ``{{TYPE}}``, ``{{MODE}}``,
``{{DESCRIPTION}}``, ``{{DATE}}``, ``{{FRAMEWORK_VERSION}}``, ``{{PACK}}``, ``{{PRIMARY}}``, ``{{PRIMARY_TITLE}}``)
plus ``{{ANSWER_<KEY>}}`` for each question (key upper-cased, ``-`` to ``_``); an optional question left unanswered
renders as its default (placeholders filled), or as an empty string when it has none, and an answer may itself use
the create-project placeholders. A ``.json`` overlay file must be valid JSON with its placeholders inside strings,
and each value is escaped for a JSON string, so the file stays JSON whatever the answers hold. A file whose name
ends in ``.append.md`` is appended, after a blank line, to the same path without ``.append`` (created when missing;
skipped when the text is already there); any other file is created, and an existing file is never overwritten (one
with the same content is left as it is). Targets that differ only in case count as one path, and a target that
leads out of the project through a symlinked folder or file is refused. Every check runs before the first write, so
a refusal writes nothing. The answers file is a JSON object of answers by question key plus the placeholders under
their names (``"SLUG"``, ``"PACK"``, ...); a placeholder it leaves out comes from the project's ai-pack manifest
(``DATE``: today, ``PRIMARY``: ``engineer``), and ``PACK`` must name the project's ai-pack folder. A type without an
overlay still has ``--dir`` and the required answers checked.

Run::

    uv run -m solaris.tools.plugins deps <plugin>... [--json]        # required (transitive) + optional
    uv run -m solaris.tools.plugins check --dir projects/<slug> [--json]
    uv run -m solaris.tools.plugins types [--json]
    uv run -m solaris.tools.plugins type <type|plugin:type> [--json]
    uv run -m solaris.tools.plugins apply-type <plugin:type> --dir projects/<slug> --answers FILE [--dry-run]

Exit codes: 0 fine; 3 a dependency problem (``deps``: a cycle, a required dependency missing from ``plugins/`` or
with a manifest that cannot be read, a malformed declaration; ``check``: an attached plugin's required dependency
not attached, a cycle, an unreadable manifest or a malformed declaration), an invalid type definition, or a refused
``apply-type`` (nothing written); 1 something named does not exist or cannot be read (a plugin named to ``deps``,
its manifest included, a type, the project or its ai-pack, the answers file) or a write failed; 2 bad usage.
"""

from __future__ import annotations

import argparse
import fnmatch
import json
import os
import re
import tomllib
from dataclasses import asdict, dataclass, field
from datetime import date
from pathlib import Path

from solaris.tools import pack as P
from solaris.tools.fileio import write_text_atomic

REPO_ROOT = Path(__file__).resolve().parents[2]
PROBLEM = 3        # a dependency problem, an invalid type, a refused apply-type
NOT_FOUND = 1
USAGE = 2
TYPE_SUFFIX = ".project.md"
APPEND_SUFFIX = ".append.md"
OVERLAY_HOME = "project-types"
TYPE_KEYS = ("type", "title", "summary", "defaults", "plugins", "template", "questions")
DEFAULT_KEYS = ("mode", "primary", "roles", "workspaces", "pack")
QUESTION_KEYS = ("key", "ask", "required", "default", "choices")
MODES = ("local", "remote-code", "embedded")
# body sections: (heading in lower case without punctuation, JSON key, heading as written)
SECTIONS = (("structure", "structure", "Structure"), ("setupsteps", "setup_steps", "Setup Steps"),
            ("handoff", "hand_off", "Hand-Off"))
PLACEHOLDERS = ("SLUG", "NAME", "TYPE", "MODE", "DESCRIPTION", "DATE", "FRAMEWORK_VERSION", "PACK", "PRIMARY",
                "PRIMARY_TITLE")
PLUGIN_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
TYPE_RE = re.compile(r"^[a-z][a-z0-9-]*$")
ROLE_RE = re.compile(r"^[a-z][a-z0-9-]*$")     # persona role names, as in solaris.tools.revs
KEY_RE = re.compile(r"^[a-z][a-z0-9_-]*$")      # lower case, so an answer key never meets a placeholder name
PLACEHOLDER_RE = re.compile(r"\{\{([A-Z][A-Z0-9_]*)\}\}")
SKIP_NAMES = {".DS_Store", "Thumbs.db", ".git", "__pycache__", ".stfolder", ".stversions"}
SKIP_GLOBS = (".solaris-tmp-*", "*.sync-conflict-*", "~syncthing~*", ".syncthing.*", "*.pyc")
_REV_RE = re.compile(r"\A_Rev\.\s*\d+_\s*\n+")
_KEY_LINE_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_-]*)\s*:(.*)$")
_FENCE_RE = re.compile(r"^\s*(```|~~~)")
_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")
_TOC_LINE_RE = re.compile(r"^\s*(?:-|\d+\.) \[.*\]\(#.*\)\s*$")


class PluginsError(Exception):
    """Something named does not exist or cannot be read (exit 1)."""


def _root(root=None) -> Path:
    # read at call time, so tests can point REPO_ROOT at a temporary tree
    return Path(root) if root is not None else REPO_ROOT


def _rel(path, root=None) -> str:
    """A path for messages: relative to the Solaris root when inside it, as seen through symlinked plugins."""
    p = Path(os.path.abspath(path))
    try:
        return p.relative_to(os.path.abspath(_root(root))).as_posix()
    except ValueError:
        return str(p)


def _ph(name: str) -> str:
    return "{{" + name + "}}"


def answer_placeholder(key: str) -> str:
    """The placeholder name of a question's answer: ANSWER_ plus the key upper-cased, - turned into _."""
    return "ANSWER_" + key.upper().replace("-", "_")


def _plain_folder(name) -> bool:
    # the folder-name rule of solaris.tools.agents --rename-pack
    return isinstance(name, str) and bool(name) and name[0] not in ".#!" and not re.search(r"[\s/\\*?\[]", name)


def _junk(name: str) -> bool:
    return name in SKIP_NAMES or any(fnmatch.fnmatch(name, g) for g in SKIP_GLOBS)


# Plugins and their dependencies

def plugin_names(root=None) -> list:
    """Every plugin under plugins/: a non-hidden folder (a symlinked private checkout counts) with a manifest.json."""
    base = _root(root) / "plugins"
    try:
        entries = sorted(base.iterdir())
    except OSError:
        return []
    return [d.name for d in entries if not d.name.startswith(".") and (d / "manifest.json").is_file()]


def read_manifest(name: str, root=None) -> dict:
    """A plugin's manifest.json as an object; PluginsError when it cannot be read."""
    path = _root(root) / "plugins" / name / "manifest.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise PluginsError(f"{_rel(path, root)}: cannot read ({exc})") from None
    if not isinstance(data, dict):
        raise PluginsError(f"{_rel(path, root)}: not a JSON object")
    return data


@dataclass(frozen=True)
class Dep:
    name: str
    why: str = ""


def _entries(value, where: str, problems: list) -> list:
    """Plugin entries (a name, or {name, why}) as Dep; a malformed entry becomes a problem."""
    if value is None:
        return []
    if not isinstance(value, list):
        problems.append(f"{where} must be a list of plugin names or {{name, why}} objects")
        return []
    out, seen = [], set()
    for i, entry in enumerate(value):
        name, why = None, ""
        if isinstance(entry, str):
            name = entry.strip()
        elif isinstance(entry, dict):
            extra = sorted(str(k) for k in entry if k not in ("name", "why") and not str(k).startswith("_"))
            if extra:
                problems.append(f"{where}[{i}]: unknown key(s) {', '.join(extra)}; allowed: name, why")
            why = entry.get("why") or ""
            if not isinstance(why, str):
                problems.append(f"{where}[{i}]: why must be a string")
                why = ""
            if isinstance(entry.get("name"), str):
                name = entry["name"].strip()
        if not name or not PLUGIN_RE.match(name):
            problems.append(f"{where}[{i}]: expected a plugin name or {{name, why}}, got {json.dumps(entry)}")
            continue
        if name not in seen:
            seen.add(name)
            out.append(Dep(name, " ".join(why.split())))
    return out


def dependencies(manifest: dict, where: str) -> tuple:
    """(required, optional, problems) from a plugin manifest's dependencies field."""
    deps = manifest.get("dependencies")
    if deps is None:
        return [], [], []
    if not isinstance(deps, dict):
        return [], [], [f"{where}: dependencies must be an object with required and optional lists"]
    problems: list = []
    extra = sorted(str(k) for k in deps if k not in ("required", "optional") and not str(k).startswith("_"))
    if extra:
        problems.append(f"{where}: dependencies: unknown key(s) {', '.join(extra)}; allowed: required, optional")
    required = _entries(deps.get("required"), f"{where}: dependencies.required", problems)
    optional = _entries(deps.get("optional"), f"{where}: dependencies.optional", problems)
    return required, optional, problems


@dataclass
class Resolution:
    roots: list
    attach: list = field(default_factory=list)    # every plugin to attach, dependencies first, roots included
    chains: dict = field(default_factory=dict)    # plugin -> the chain that first reached it
    unknown: list = field(default_factory=list)   # (name, chain): reached, but not under plugins/
    unreadable: list = field(default_factory=list)  # (name, chain): under plugins/, its manifest unreadable
    optional: list = field(default_factory=list)  # {name, why, by}: suggested, under plugins/
    absent: list = field(default_factory=list)    # {name, why, by}: wanted, but not under plugins/
    errors: list = field(default_factory=list)    # cycles, malformed declarations, unreadable manifests

    def missing(self) -> list:
        """Required dependencies not under plugins/ (a named plugin that is not there is only a problem)."""
        return [{"name": name, "chain": chain} for name, chain in self.unknown if len(chain) > 1]

    def problems(self, root=None) -> list:
        out = []
        for name, chain in self.unknown:
            if len(chain) == 1:
                known = ", ".join(plugin_names(root)) or "none"
                out.append(f"no plugin {name!r} under plugins/ (known: {known})")
            else:
                out.append(f"missing required dependency: {' > '.join(chain)} (no plugins/{name}/)")
        return out + self.errors


def resolve(roots, root=None) -> Resolution:
    """The plugins to attach for ``roots`` (their required dependencies, transitively, dependencies first) and
    the optional ones to suggest."""
    known = set(plugin_names(root))
    res = Resolution(roots=list(dict.fromkeys(roots)))
    state: dict = {}
    wanted: dict = {}

    def visit(name: str, path: list) -> None:
        chain = path + [name]
        if state.get(name) == "done":
            return
        if state.get(name) == "active":
            res.errors.append("dependency cycle: " + " > ".join(chain[chain.index(name):]))
            return
        state[name] = "done"
        if name not in known:
            res.unknown.append((name, chain))
            return
        try:
            manifest = read_manifest(name, root)
        except PluginsError as exc:
            res.unreadable.append((name, chain))
            res.errors.append(f"{exc} (reached by {' > '.join(chain)})")
            return
        required, optional, problems = dependencies(manifest, f"plugins/{name}/manifest.json")
        res.errors.extend(problems)
        state[name] = "active"
        for dep in required:
            visit(dep.name, chain)
        state[name] = "done"
        res.attach.append(name)
        res.chains[name] = chain
        for dep in optional:
            entry = wanted.setdefault(dep.name, {"name": dep.name, "why": dep.why, "by": []})
            entry["why"] = entry["why"] or dep.why
            if name not in entry["by"]:
                entry["by"].append(name)

    for name in res.roots:
        visit(name, [])
    taken = set(res.attach) | set(res.roots)
    for entry in wanted.values():
        if entry["name"] not in taken:
            (res.optional if entry["name"] in known else res.absent).append(entry)
    return res


def deps_report(names, root=None) -> dict:
    """The deps command's report; exit 1 when a named plugin is not under plugins/ or its manifest cannot be
    read, else 3 on any problem."""
    res = resolve(names, root)
    problems = res.problems(root)
    named = any(len(chain) == 1 for _name, chain in res.unknown + res.unreadable)
    return {"plugins": res.roots, "attach": res.attach,
            "required": [{"name": n, "chain": res.chains[n]} for n in res.attach if n not in res.roots],
            "optional": res.optional, "unavailable": res.absent, "missing": res.missing(), "problems": problems,
            "exit": NOT_FOUND if named else PROBLEM if problems else 0}


def _load_project(project_dir) -> tuple:
    """(project dir, pack folder, pack manifest); PluginsError when the folder or its ai-pack is missing."""
    project_dir = Path(project_dir)
    if not project_dir.is_dir():
        raise PluginsError(f"{project_dir} not found: --dir must be a project root (embedded mode: the repo root)")
    try:
        pack = P.require_pack(project_dir)
        manifest = json.loads((pack / "manifest.json").read_text(encoding="utf-8"))
    except P.PackError as exc:
        raise PluginsError(str(exc)) from None
    except (OSError, ValueError) as exc:
        raise PluginsError(f"{project_dir}: cannot read the ai-pack manifest ({exc})") from None
    return project_dir, pack, manifest


def check(project_dir, root=None) -> dict:
    """A project's attached plugins against their dependencies: the required ones not attached (exit 3), the
    optional ones not attached (suggestions), and the attached ones whose source is not under plugins/."""
    project_dir, pack, manifest = _load_project(project_dir)
    entries = manifest.get("plugins") or []
    if not isinstance(entries, list):
        raise PluginsError(f"{pack / 'manifest.json'}: plugins must be a list")
    attached: list = []
    for entry in entries:
        name = entry.get("name") if isinstance(entry, dict) else entry
        if isinstance(name, str) and name and name not in attached:
            attached.append(name)
    res = resolve(attached, root)
    missing = [{"name": n, "chain": res.chains[n], "present": True} for n in res.attach if n not in attached]
    unchecked = []
    for name, chain in res.unknown:
        if name in attached:
            unchecked.append(name)
        else:
            missing.append({"name": name, "chain": chain, "present": False})
    return {"project": str(project_dir), "pack": pack.name, "attached": attached, "missing": missing,
            "suggest": res.optional, "unavailable": res.absent, "unchecked": unchecked, "problems": res.errors,
            "exit": PROBLEM if missing or res.errors else 0}


# Project types

@dataclass
class Frontmatter:
    values: dict     # key -> the JSON value, or the folded text when the value is not JSON
    texts: dict      # key -> the folded text as written
    errors: dict     # key -> why the value is not JSON
    body: str
    problems: list
    found: bool = True   # False: no frontmatter block, or one never closed


def parse_frontmatter(text: str) -> Frontmatter:
    """The ``key: value`` frontmatter of a type file: a value that parses as JSON is JSON and may continue on
    lines indented by two or more spaces; any other value is a plain string, continuation lines folded into one.
    A leading ``_Rev. N_`` line is skipped."""
    text = _REV_RE.sub("", text.lstrip("\ufeff"))
    lines = text.split("\n")
    if lines[0].strip() != "---":
        return Frontmatter({}, {}, {}, text, ["no frontmatter: the file must start with a --- line"], False)
    end = next((i for i in range(1, len(lines)) if lines[i].strip() == "---"), None)
    if end is None:
        return Frontmatter({}, {}, {}, "", ["unterminated frontmatter: no closing --- line"], False)
    parts: dict = {}
    problems: list = []
    key = None   # the key continuation lines belong to; "" swallows those of a rejected line
    for n in range(1, end):
        line = lines[n].rstrip()
        if not line.strip():
            continue
        if line.startswith(("  ", "\t")):
            if key:
                parts[key].append(line.strip())
            elif key is None:
                problems.append(f"frontmatter line {n + 1}: an indented line with no key above it")
            continue
        if line.startswith("#"):
            continue
        m = _KEY_LINE_RE.match(line)
        if not m:
            problems.append(f"frontmatter line {n + 1}: expected 'key: value' (continuation lines are indented "
                            f"by two or more spaces), got {line.strip()!r}")
            key = ""
            continue
        if m.group(1) in parts:
            problems.append(f"frontmatter line {n + 1}: duplicate key {m.group(1)!r}")
            key = ""
            continue
        key = m.group(1)
        parts[key] = [m.group(2).strip()]
    values, texts, errors = {}, {}, {}
    for name, chunks in parts.items():
        texts[name] = " ".join(c for c in chunks if c)
        try:
            values[name] = json.loads("\n".join(chunks))
        except ValueError as exc:
            values[name] = texts[name]
            errors[name] = getattr(exc, "msg", str(exc))
    return Frontmatter(values, texts, errors, "\n".join(lines[end + 1:]), problems)


def body_sections(body: str) -> list:
    """(title, text) of every ``##`` section of a markdown body, in order; headings in code fences are text."""
    out, title, buf, fence = [], None, [], False
    for line in body.split("\n"):
        if _FENCE_RE.match(line):
            fence = not fence
        elif not fence:
            m = _HEADING_RE.match(line)
            if m and len(m.group(1)) <= 2:
                if title is not None:
                    out.append((title, "\n".join(buf).strip()))
                title = re.sub(r"<!--.*?-->", "", m.group(2)).strip() if len(m.group(1)) == 2 else None
                buf = []
                continue
        if title is not None:
            buf.append(line)
    if title is not None:
        out.append((title, "\n".join(buf).strip()))
    return out


@dataclass
class ProjectType:
    name: str                 # "python-cli", or "kaggle:competition"
    kind: str                 # "core" or "plugin"
    file: Path
    plugin: str = ""
    type: str = ""
    title: str = ""
    summary: str = ""
    defaults: dict = field(default_factory=dict)
    required: list = field(default_factory=list)    # [Dep]: plugins.required as declared
    optional: list = field(default_factory=list)    # [Dep]: plugins.optional as declared
    template: str = ""                              # overlay folder relative to the plugin folder; "" = none
    overlay: "Path | None" = None
    questions: list = field(default_factory=list)
    sections: dict = field(default_factory=dict)    # structure, setup_steps, hand_off -> text
    resolution: "Resolution | None" = None
    problems: list = field(default_factory=list)

    @property
    def valid(self) -> bool:
        return not self.problems


def _core_dir(root=None) -> Path:
    return _root(root) / "solaris" / "templates" / "projects"


def core_type_names(root=None) -> list:
    """The core types: solaris/templates/projects/<type>.md with a kebab-case stem (a README.md is no type)."""
    try:
        files = sorted(_core_dir(root).iterdir())
    except OSError:
        return []
    return [f.name[:-3] for f in files if f.suffix == ".md" and TYPE_RE.match(f.name[:-3]) and f.is_file()]


def plugin_type_names(root=None) -> list:
    out = []
    for plugin in plugin_names(root):
        for f in sorted((_root(root) / "plugins" / plugin).glob("*" + TYPE_SUFFIX)):
            if f.is_file() and not f.name.startswith("."):
                out.append(f"{plugin}:{f.name[:-len(TYPE_SUFFIX)]}")
    return out


def _first_sentence(body: str) -> str:
    """The first sentence of the first paragraph below the title, past a table of contents and a rev marker."""
    para: list = []
    for line in body.split("\n"):
        s = line.strip()
        if not para and (not s or s.startswith("#") or s.startswith("_Rev.") or _TOC_LINE_RE.match(line)):
            continue
        if not s or s.startswith("#"):
            break
        para.append(s)
    text = " ".join(para)
    m = re.match(r"(.+?[.!?])(\s|$)", text)
    return m.group(1) if m else text


def _load_core_type(name: str, root=None) -> ProjectType:
    path = _core_dir(root) / f"{name}.md"
    if not TYPE_RE.match(name) or not path.is_file():
        raise PluginsError(_no_type(name, root))
    try:
        body = _REV_RE.sub("", path.read_text(encoding="utf-8").lstrip("\ufeff"))
    except (OSError, ValueError) as exc:
        raise PluginsError(f"{_rel(path, root)}: cannot read ({exc})") from None
    texts: dict = {}
    if body.startswith("---"):
        fm = parse_frontmatter(body)
        texts, body = fm.texts, fm.body
    title = next((re.sub(r"<!--.*?-->", "", line[2:]).strip() for line in body.split("\n")
                  if line.startswith("# ")), "")
    title = re.sub(r"^project type:\s*", "", title, flags=re.IGNORECASE)
    return ProjectType(name=name, kind="core", file=path, type=name, title=texts.get("title") or title or name,
                       summary=texts.get("summary") or _first_sentence(body))


def _no_type(name: str, root=None) -> str:
    plugin = name.partition(":")[0]
    hint = [n for n in plugin_type_names(root) if n.startswith(f"{plugin}:")]
    also = f"; plugin {plugin} provides {', '.join(hint)}" if hint and ":" not in name else ""
    return (f"no project type {name!r} (core: {', '.join(core_type_names(root)) or 'none'}; plugin types: "
            f"{', '.join(plugin_type_names(root)) or 'none'}{also})")


def _json_value(fm: Frontmatter, key: str, kind, problems: list):
    """The JSON value of ``key`` when it is of ``kind`` (dict or list), None when absent; anything else is a
    problem."""
    if key not in fm.values:
        return None
    value = fm.values[key]
    if isinstance(value, kind):
        return value
    why = f" (it does not parse as JSON: {fm.errors[key]})" if key in fm.errors else ""
    problems.append(f"{key}: expected a JSON {'object' if kind is dict else 'list'}{why}")
    return None


def _check_defaults(fm: Frontmatter, problems: list) -> dict:
    d = _json_value(fm, "defaults", dict, problems)
    if d is None:
        return {}
    unknown = sorted(k for k in d if k not in DEFAULT_KEYS)
    if unknown:
        problems.append(f"defaults: unknown key(s) {', '.join(unknown)}; allowed: {', '.join(DEFAULT_KEYS)}")
    if "mode" in d and d["mode"] not in MODES:
        problems.append(f"defaults.mode must be one of {', '.join(MODES)}, got {json.dumps(d['mode'])}")
    if "primary" in d and not (isinstance(d["primary"], str) and ROLE_RE.match(d["primary"])):
        problems.append(f"defaults.primary must be a role name ({ROLE_RE.pattern}), got {json.dumps(d['primary'])}")
    for key, ok, what in (("roles", lambda r: isinstance(r, str) and ROLE_RE.match(r), "role names"),
                          ("workspaces", _plain_folder, "plain folder names")):
        if key in d and not (isinstance(d[key], list) and all(ok(x) for x in d[key])):
            problems.append(f"defaults.{key} must be a list of {what}, got {json.dumps(d[key])}")
    if isinstance(d.get("roles"), list) and d.get("primary", "engineer") in d["roles"]:
        problems.append("defaults.roles must not name the primary persona")
    if "pack" in d and not _plain_folder(d["pack"]):
        problems.append(f"defaults.pack must be a plain folder name, got {json.dumps(d['pack'])}")
    return d


def _check_type_plugins(fm: Frontmatter, problems: list, root=None) -> tuple:
    p = _json_value(fm, "plugins", dict, problems)
    if p is None:
        return [], []
    unknown = sorted(str(k) for k in p if k not in ("required", "optional") and not str(k).startswith("_"))
    if unknown:
        problems.append(f"plugins: unknown key(s) {', '.join(unknown)}; allowed: required, optional")
    required = _entries(p.get("required"), "plugins.required", problems)
    optional = _entries(p.get("optional"), "plugins.optional", problems)
    known = set(plugin_names(root))
    for dep in required:
        if dep.name not in known:
            problems.append(f"plugins.required: no plugin {dep.name!r} under plugins/")
    return required, optional


def _check_questions(fm: Frontmatter, problems: list) -> list:
    qs = _json_value(fm, "questions", list, problems)
    out: list = []
    seen: dict = {}
    for i, q in enumerate(qs or []):
        if not isinstance(q, dict):
            problems.append(f"questions[{i}]: expected an object with key and ask")
            continue
        unknown = sorted(str(k) for k in q if k not in QUESTION_KEYS)
        if unknown:
            problems.append(f"questions[{i}]: unknown key(s) {', '.join(unknown)}; "
                            f"allowed: {', '.join(QUESTION_KEYS)}")
        key = q.get("key")
        if not (isinstance(key, str) and KEY_RE.match(key)):
            problems.append(f"questions[{i}]: key must be lower case ({KEY_RE.pattern}), got {json.dumps(key)}")
            continue
        name = answer_placeholder(key)
        if name in seen:
            problems.append(f"questions[{i}]: key {key!r} gives the same placeholder {_ph(name)} as {seen[name]!r}")
            continue
        seen[name] = key
        ask, default, choices = q.get("ask"), q.get("default"), q.get("choices")
        if not (isinstance(ask, str) and ask.strip()):
            problems.append(f"question {key!r}: ask must be the question's text")
        if "required" in q and not isinstance(q["required"], bool):
            problems.append(f"question {key!r}: required must be true or false")
        if default is not None and not isinstance(default, (str, int, float)):
            problems.append(f"question {key!r}: default must be text, a number or true/false")
        if choices is not None and not (isinstance(choices, list) and choices
                                        and all(isinstance(c, str) and c.strip() for c in choices)):
            problems.append(f"question {key!r}: choices must be a list of strings")
        out.append({"key": key, "ask": ask if isinstance(ask, str) else "", "required": q.get("required") is True,
                    "default": default, "choices": choices, "placeholder": _ph(name)})
    return out


def _check_template(fm: Frontmatter, t: ProjectType, pdir: Path, problems: list) -> tuple:
    """(overlay folder relative to the plugin, its path): the template key, else project-types/<type>/ when it
    exists; ("", None) without one. The folder must sit under the plugin's project-types/ and stay there with
    symlinks resolved, so a type never copies the plugin's own files (its manifest, shared/) into a project."""
    if "template" not in fm.values:
        rel = f"{OVERLAY_HOME}/{t.type}"
        if not (pdir / rel).is_dir():
            return "", None
    else:
        value = fm.values["template"]
        rel = (value if isinstance(value, str) else fm.texts["template"]).strip().rstrip("/")
        parts = Path(rel).parts
        if Path(rel).is_absolute() or ".." in parts or len(parts) < 2 or parts[0] != OVERLAY_HOME:
            problems.append(f"template: {rel!r} must be a folder under {OVERLAY_HOME}/ in the plugin, such as "
                            f"{OVERLAY_HOME}/{t.type}")
            return rel, None
        rel = "/".join(parts)
        if not (pdir / rel).is_dir():
            problems.append(f"template: no overlay folder plugins/{t.plugin}/{rel}")
            return rel, None
    home = pdir / OVERLAY_HOME
    real = os.path.realpath(pdir / rel)
    if real == os.path.realpath(home) or not _inside(real, home) or not _inside(home, pdir):
        problems.append(f"template: plugins/{t.plugin}/{rel} leads out of the plugin's {OVERLAY_HOME}/ "
                        "through a symlink")
        return rel, None
    return rel, pdir / rel


def _check_sections(body: str, problems: list) -> dict:
    """The text of the Structure, Setup Steps and Hand-Off sections by JSON key; a missing, empty or repeated
    one is a problem."""
    wanted = {norm: (key, title) for norm, key, title in SECTIONS}
    found: dict = {}
    for heading, text in body_sections(body):
        norm = re.sub(r"[^a-z0-9]", "", heading.lower())
        if norm not in wanted:
            continue
        key, title = wanted[norm]
        if key in found:
            problems.append(f"section ## {title} appears more than once")
        else:
            found[key] = text
    for _norm, key, title in SECTIONS:
        if key not in found:
            problems.append(f"missing section ## {title}")
        elif not found[key]:
            problems.append(f"section ## {title} is empty")
    return {key: found.get(key, "") for _norm, key, _title in SECTIONS}


def overlay_files(overlay: Path) -> list:
    """Overlay-relative paths of every file under an overlay folder, sorted; OS and sync leftovers skipped."""
    out = []
    for dirpath, dirnames, filenames in os.walk(overlay):
        dirnames[:] = sorted(d for d in dirnames if not _junk(d))
        out.extend((Path(dirpath) / f).relative_to(overlay).as_posix() for f in filenames if not _junk(f))
    return sorted(out)


def _target_parts(rel: str) -> list:
    """The path parts an overlay file writes: an .append.md file names the same path without .append."""
    parts = rel.split("/")
    if parts[-1].endswith(APPEND_SUFFIX):
        parts[-1] = parts[-1][:-len(APPEND_SUFFIX)] + ".md"
    return parts


def _managed(parts: list, pack: str, primaries, root=None) -> str:
    """The framework-managed file a target path would write ("" when none): the root AGENTS.md, the pack's
    manifest.json and README.md, the primary persona, the pack's rule, skill and info masters, and anything
    under the pack's plugins/ (install-plugin's home). Names compare case-folded: the default macOS filesystem
    ignores case, so agents.md there is AGENTS.md."""
    rel = "/".join(parts)
    low = [p.casefold() for p in parts]
    if low == ["agents.md"]:
        return rel
    if len(low) < 2 or low[0] != pack.casefold():
        return ""
    inner = low[1:]
    own = {"manifest.json", "readme.md"} | {f"{p}.agent.md".casefold() for p in primaries}
    if inner[0] == "plugins" or (len(inner) == 1 and inner[0] in own):
        return rel
    if len(inner) == 2 and inner[0] in ("rules", "skills", "info"):
        try:
            masters = {f.name.casefold() for f in (_root(root) / "solaris" / "templates" / "ai-pack" / "ai"
                                                   / inner[0]).iterdir()}
        except OSError:
            masters = set()
        if inner[1] in masters:
            return rel
    return ""


def _read_text(path: Path) -> "str | None":
    """A file's UTF-8 text, or None for a binary or unreadable file."""
    try:
        return path.read_bytes().decode("utf-8")
    except (OSError, UnicodeDecodeError):
        return None


def _check_overlay(t: ProjectType, problems: list, root=None) -> None:
    answers = {q["placeholder"][2:-2] for q in t.questions}
    targets: dict = {}   # case-folded target -> the overlay file writing it
    for rel in overlay_files(t.overlay):
        text = _read_text(t.overlay / rel)
        used = set(PLACEHOLDER_RE.findall(rel)) | set(PLACEHOLDER_RE.findall(text or ""))
        for name in sorted(used):
            if name.startswith("ANSWER_") and name not in answers:
                problems.append(f"overlay {rel}: {_ph(name)} names no question")
            elif not name.startswith("ANSWER_") and name not in PLACEHOLDERS:
                problems.append(f"overlay {rel}: {_ph(name)} is not a placeholder apply-type fills")
        parts = _target_parts(rel)
        key = "/".join(parts).casefold()
        clash = next((k for k in targets if k == key or k.startswith(key + "/") or key.startswith(k + "/")), None)
        if clash is not None:
            problems.append(f"overlay {rel}: writes {'/'.join(parts)}, where {targets[clash]} writes too "
                            "(names that differ only in case are one path)")
        targets.setdefault(key, rel)
        managed = _managed(parts, _ph("PACK"), ("engineer", _ph("PRIMARY")), root)
        if managed:
            problems.append(f"overlay {rel}: writes {managed}, a file the framework manages")
        if len(parts) > 1 and parts[0].casefold() in (P.DEFAULT, P.LEGACY):
            problems.append(f"overlay {rel}: name the ai-pack folder {_ph('PACK')}, not {parts[0]}")
        if rel.endswith(APPEND_SUFFIX) and text is None:
            problems.append(f"overlay {rel}: an .append.md file must be UTF-8 text")
        if rel.endswith(".json") and text is not None:
            # placeholders inside strings keep the file JSON once their escaped values are filled in
            try:
                json.loads(text)
            except ValueError as exc:
                problems.append(f"overlay {rel}: not valid JSON with its placeholders inside strings "
                                f"({getattr(exc, 'msg', exc)})")


def _load_plugin_type(plugin: str, tname: str, root=None) -> ProjectType:
    if not PLUGIN_RE.match(plugin) or plugin not in plugin_names(root):
        known = ", ".join(plugin_names(root)) or "none"
        raise PluginsError(f"no plugin {plugin!r} under plugins/ (known: {known})")
    pdir = _root(root) / "plugins" / plugin
    path = pdir / f"{tname}{TYPE_SUFFIX}"
    if not tname or tname.startswith(".") or re.search(r"[/\\]", tname) or not path.is_file():
        raise PluginsError(_no_type(f"{plugin}:{tname}", root))
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, ValueError) as exc:
        raise PluginsError(f"{_rel(path, root)}: cannot read ({exc})") from None
    t = ProjectType(name=f"{plugin}:{tname}", kind="plugin", file=path, plugin=plugin, type=tname)
    probs = t.problems
    if not TYPE_RE.match(tname):
        probs.append(f"the type name {tname!r} must be kebab-case ({TYPE_RE.pattern})")
    fm = parse_frontmatter(text)
    probs.extend(fm.problems)
    unknown = sorted(k for k in fm.values if k not in TYPE_KEYS)
    if unknown:
        probs.append(f"unknown frontmatter key(s) {', '.join(unknown)}; allowed: {', '.join(TYPE_KEYS)}")

    def text_of(key: str) -> str:
        value = fm.values.get(key)
        return value.strip() if isinstance(value, str) else fm.texts.get(key, "").strip()

    for key in ("type", "title", "summary"):
        if not text_of(key):
            probs.append(f"{key}: required")
    if text_of("type") and text_of("type") != tname:
        probs.append(f"type: {text_of('type')!r} does not match the file name ({tname}{TYPE_SUFFIX})")
    t.title, t.summary = text_of("title"), text_of("summary")
    t.defaults = _check_defaults(fm, probs)
    t.required, t.optional = _check_type_plugins(fm, probs, root)
    t.questions = _check_questions(fm, probs)
    t.template, t.overlay = _check_template(fm, t, pdir, probs)
    # an unterminated frontmatter leaves no body to look for sections in: one problem, not four
    t.sections = _check_sections(fm.body, probs) if fm.found or fm.body else \
        {key: "" for _norm, key, _title in SECTIONS}
    t.resolution = resolve([plugin] + [d.name for d in t.required], root)
    probs.extend(f"plugins: {p}" for p in t.resolution.errors)
    probs.extend(f"plugins: missing required dependency: {' > '.join(chain)} (no plugins/{name}/)"
                 for name, chain in t.resolution.unknown if len(chain) > 1)
    # the overlay is checked against the questions, which an unreadable frontmatter does not give
    if t.overlay is not None and fm.found:
        _check_overlay(t, probs, root)
    return t


def load_type(name: str, root=None) -> ProjectType:
    """One project type: a core type by its file stem, or a plugin type as <plugin>:<type>; PluginsError when
    there is none. A plugin type comes validated: see its problems."""
    if ":" in name:
        plugin, _, tname = name.partition(":")
        return _load_plugin_type(plugin, tname, root)
    return _load_core_type(name, root)


def all_types(root=None) -> list:
    out = [_load_core_type(n, root) for n in core_type_names(root)]
    for name in plugin_type_names(root):
        try:
            out.append(load_type(name, root))
        except PluginsError as exc:   # an unreadable file is listed as invalid
            plugin, _, tname = name.partition(":")
            out.append(ProjectType(name=name, kind="plugin", file=_root(root) / "plugins" / plugin
                                   / f"{tname}{TYPE_SUFFIX}", plugin=plugin, type=tname, problems=[str(exc)]))
    return out


def suggestions(t: ProjectType, root=None) -> tuple:
    """(suggest, unavailable) for a plugin type: its optional plugins plus the optional dependencies of the
    plugins it attaches, not attached already; suggest holds those under plugins/."""
    res = t.resolution or Resolution(roots=[])
    merged: dict = {}
    for dep in t.optional:
        merged.setdefault(dep.name, {"name": dep.name, "why": dep.why, "by": [t.name]})
    for entry in res.optional + res.absent:
        m = merged.setdefault(entry["name"], {"name": entry["name"], "why": entry["why"], "by": []})
        m["why"] = m["why"] or entry["why"]
        m["by"].extend(b for b in entry["by"] if b not in m["by"])
    known, taken = set(plugin_names(root)), set(res.attach) | set(res.roots)
    rows = [m for m in merged.values() if m["name"] not in taken]
    return [m for m in rows if m["name"] in known], [m for m in rows if m["name"] not in known]


def type_json(t: ProjectType, root=None) -> dict:
    out = {"name": t.name, "kind": t.kind, "file": _rel(t.file, root), "title": t.title, "summary": t.summary}
    if t.kind == "plugin":
        res = t.resolution or Resolution(roots=[])
        suggest, unavailable = suggestions(t, root)
        overlay = [{"source": rel, "target": "/".join(_target_parts(rel)),
                    "action": "append" if rel.endswith(APPEND_SUFFIX) else "create"}
                   for rel in (overlay_files(t.overlay) if t.overlay is not None else [])]
        out.update({"plugin": t.plugin, "type": t.type, "defaults": t.defaults,
                    "plugins": {"required": [asdict(d) for d in t.required],
                                "optional": [asdict(d) for d in t.optional]},
                    "attach": [{"name": n, "chain": res.chains[n]} for n in res.attach],
                    "suggest": suggest, "unavailable": unavailable,
                    "template": t.template or None, "overlay": overlay, "questions": t.questions, **t.sections})
    out.update({"valid": t.valid, "problems": t.problems})
    return out


# Applying a plugin type's overlay

@dataclass
class Step:
    verb: str       # create, append, or unchanged (the target already holds it)
    target: str     # project-relative path
    source: str     # overlay-relative path
    data: bytes = b""
    mode: int = 0   # the overlay file's permission bits (create)


def _as_text(value) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (str, int, float)):
        return str(value)
    return json.dumps(value, ensure_ascii=False)


def render(text: str, values: dict, json_strings: bool = False) -> str:
    """Fill every {{NAME}} placeholder that has a value; anything else stays as written. With ``json_strings``
    (a .json file, where placeholders sit inside strings) each value is escaped for a JSON string."""
    if json_strings:
        values = {k: json.dumps(v, ensure_ascii=False)[1:-1] for k, v in values.items()}
    return PLACEHOLDER_RE.sub(lambda m: values.get(m.group(1), m.group(0)), text)


def _framework_version(root=None) -> "str | None":
    try:
        with open(_root(root) / "pyproject.toml", "rb") as fh:
            return str(tomllib.load(fh)["project"]["version"])
    except (OSError, ValueError, KeyError):
        return None


def placeholder_values(t: ProjectType, pack: Path, manifest: dict, answers: dict, root=None) -> tuple:
    """(values by placeholder name, refusals, notes): the answers file first, then the project's manifest."""
    project = manifest.get("project") if isinstance(manifest.get("project"), dict) else {}
    agents = manifest.get("agents") if isinstance(manifest.get("agents"), dict) else {}
    fallback = {"SLUG": project.get("slug"), "NAME": project.get("name"), "TYPE": project.get("type"),
                "MODE": project.get("mode"), "DESCRIPTION": project.get("description"),
                "DATE": manifest.get("created"), "FRAMEWORK_VERSION": manifest.get("framework_version"),
                "PACK": pack.name, "PRIMARY": agents.get("primary")}
    values: dict = {}
    for name in PLACEHOLDERS:
        value = answers.get(name)
        if value is None:
            value = fallback.get(name)
            if isinstance(value, str) and "{{" in value:   # a template value nobody filled
                value = None
        if value is not None:
            values[name] = _as_text(value)
    values.setdefault("TYPE", t.name)
    values.setdefault("DATE", date.today().isoformat())
    values.setdefault("PRIMARY", "engineer")
    values.setdefault("PRIMARY_TITLE", values["PRIMARY"].replace("-", " ").title())
    version = _framework_version(root)
    if "FRAMEWORK_VERSION" not in values and version:
        values["FRAMEWORK_VERSION"] = version
    refusals, notes = [], []
    if values["PACK"] != pack.name:
        refusals.append(f"PACK is {values['PACK']!r} in the answers, but the project's ai-pack folder is {pack.name}")
    core = dict(values)
    for q in t.questions:
        answer = answers.get(q["key"])
        # an optional question left unanswered takes its default; one without a default renders empty
        if answer is None and not q["required"]:
            answer = q["default"]
        text = render(_as_text(answer), core)
        left = sorted(set(PLACEHOLDER_RE.findall(text)) & set(PLACEHOLDERS))
        if left:
            refusals.append(f"the answer to {q['key']!r} uses {', '.join(map(_ph, left))}, which has no value")
        if q["required"] and not text.strip():
            refusals.append(f"question {q['key']!r} is required and has no answer")
        values[q["placeholder"][2:-2]] = text
    extra = sorted(k for k in answers if k not in PLACEHOLDERS and k not in {q["key"] for q in t.questions})
    if extra:
        notes.append(f"not asked by {t.name}, ignored: {', '.join(extra)}")
    return values, refusals, notes


def _blocked(project_dir: Path, parts: list) -> str:
    """The first parent of a target that exists but is not a folder ("" when none)."""
    cur = project_dir
    for i, part in enumerate(parts[:-1]):
        cur = cur / part
        if (cur.exists() and not cur.is_dir()) or (cur.is_symlink() and not cur.exists()):
            return "/".join(parts[:i + 1])
    return ""


def _inside(path, folder) -> bool:
    """True when ``path``, every symlink on the way resolved, is ``folder`` (resolved) or lies under it."""
    real, base = os.path.realpath(path), os.path.realpath(folder)
    return real == base or real.startswith(base.rstrip(os.sep) + os.sep)


def plan_type(t: ProjectType, project_dir, answers: dict, root=None) -> tuple:
    """(steps, refusals, notes) for applying a plugin type's overlay to a project; writes nothing."""
    project_dir, pack, manifest = _load_project(project_dir)
    values, refusals, notes = placeholder_values(t, pack, manifest, answers, root)
    steps: list = []
    targets: dict = {}
    files = overlay_files(t.overlay) if t.overlay is not None else []
    for rel in files:
        src = t.overlay / rel
        raw_parts = rel.split("/")
        text = _read_text(src)
        used = set(PLACEHOLDER_RE.findall(rel)) | set(PLACEHOLDER_RE.findall(text or ""))
        unfilled = sorted(n for n in used if n in PLACEHOLDERS and n not in values)
        if unfilled:
            refusals.append(f"{rel}: no value for {', '.join(map(_ph, unfilled))} (give it in the answers file)")
            continue
        parts = [render(p, values) for p in raw_parts]
        append = raw_parts[-1].endswith(APPEND_SUFFIX)
        if append:
            parts[-1] = parts[-1][:-len(APPEND_SUFFIX)] + ".md"
        target = "/".join(parts)
        if any(p in ("", ".", "..") or re.search(r"[/\\\0]", p) for p in parts) or parts[-1] == ".md":
            refusals.append(f"{rel}: renders to {target!r}, not a plain path inside the project")
            continue
        # keyed case-folded: where the filesystem ignores case (the macOS default), Foo.md and foo.md are one file
        key = target.casefold()
        clash = next((k for k in targets if k == key or k.startswith(key + "/") or key.startswith(k + "/")), None)
        if clash is not None:
            other, other_rel = targets[clash]
            same = " (names that differ only in case are one file where the filesystem ignores case)" \
                if other != target and other.casefold() == key else ""
            refusals.append(f"{rel} writes {target}, which collides with {other} from {other_rel}{same}")
            continue
        targets[key] = (target, rel)
        managed = _managed(parts, pack.name, ("engineer", values["PRIMARY"]), root)
        if managed:
            refusals.append(f"{rel}: writes {managed}, a file the framework manages")
            continue
        blocked = _blocked(project_dir, parts)
        if blocked:
            refusals.append(f"{rel}: {blocked} exists and is not a folder, so {target} cannot be written")
            continue
        dest = project_dir.joinpath(*parts)
        if not _inside(dest, project_dir):
            refusals.append(f"{rel}: {target} leads outside the project through a symlink "
                            f"(to {os.path.realpath(dest)})")
            continue
        if append:
            if text is None:
                refusals.append(f"{rel}: an .append.md file must be UTF-8 text")
                continue
            addition = render(text, values).strip("\n")
            if not (dest.exists() or dest.is_symlink()):
                steps.append(Step("create", target, rel, (addition + "\n").encode("utf-8")))
                continue
            current = _read_text(dest) if dest.is_file() else None
            if current is None:
                refusals.append(f"{target} exists and is not a readable text file to append to")
            elif not addition or addition in current:
                steps.append(Step("unchanged", target, rel))
            else:
                head = current.rstrip("\n") + "\n\n" if current.strip() else ""
                steps.append(Step("append", target, rel, (head + addition + "\n").encode("utf-8")))
            continue
        try:
            data = render(text, values, target.endswith(".json")).encode("utf-8") if text is not None \
                else src.read_bytes()
            mode = src.stat().st_mode & 0o777
        except OSError as exc:
            refusals.append(f"{rel}: cannot read ({exc})")
            continue
        if dest.exists() or dest.is_symlink():
            try:
                same = dest.is_file() and dest.read_bytes() == data
            except OSError:
                same = False
            if same:
                steps.append(Step("unchanged", target, rel))
            else:
                refusals.append(f"{target} exists and differs (a created file never overwrites one)")
            continue
        steps.append(Step("create", target, rel, data, mode))
    return steps, refusals, notes


def apply_steps(project_dir, steps: list, done: "list | None" = None) -> list:
    """Write the create and append steps and return the targets written, also collected into ``done`` as they
    land (an OSError stops at the failing one)."""
    done = [] if done is None else done
    for step in steps:
        if step.verb not in ("create", "append"):
            continue
        dest = Path(project_dir).joinpath(*step.target.split("/"))
        dest.parent.mkdir(parents=True, exist_ok=True)
        # latin-1 maps every byte to one character, so any bytes pass through the text writer unchanged
        write_text_atomic(dest, step.data.decode("latin-1"), encoding="latin-1")
        if step.verb == "create" and step.mode & 0o111:
            # executable like the overlay file, for whoever may read it (the umask still decides that)
            mode = os.stat(dest).st_mode
            os.chmod(dest, mode | ((mode & 0o444) >> 2 & step.mode))
        done.append(step.target)
    return done


# CLI

def _problems_block(problems: list) -> list:
    return ["problems:", *(f"  {p}" for p in problems)] if problems else []


def _deps_text(rep: dict) -> str:
    lines = [f"attach, dependencies first: {', '.join(rep['attach']) or 'none'}"]
    lines += ["required:", *(f"  {r['name']} - {' > '.join(r['chain'])}" for r in rep["required"])] \
        if rep["required"] else ["required: none"]
    opt = [f"  {o['name']} - {o['why'] or 'no reason given'} ({', '.join(o['by'])})" for o in rep["optional"]]
    lines += ["optional (under plugins/):", *opt] if opt else ["optional: none"]
    if rep["unavailable"]:
        lines += ["optional, not under plugins/:",
                  *(f"  {o['name']} - {o['why'] or 'no reason given'} ({', '.join(o['by'])})"
                    for o in rep["unavailable"])]
    return "\n".join(lines + _problems_block(rep["problems"]))


def _check_text(rep: dict) -> str:
    lines = [f"project: {rep['project']} (ai-pack {rep['pack']}/)",
             f"attached: {', '.join(rep['attached']) or 'none'}"]
    if rep["missing"]:
        lines.append("required dependencies not attached (attach these, in this order):")
        for m in rep["missing"]:
            where = "" if m["present"] else " (not under plugins/: acquire it first)"
            lines.append(f"  {m['name']} - {' > '.join(m['chain'])}{where}")
    if rep["suggest"]:
        lines.append("optional dependencies not attached (suggestions):")
        lines += [f"  {o['name']} - {o['why'] or 'no reason given'} ({', '.join(o['by'])})" for o in rep["suggest"]]
    if rep["unavailable"]:
        lines.append(f"optional, not under plugins/: {', '.join(o['name'] for o in rep['unavailable'])}")
    if rep["unchecked"]:
        lines.append(f"not checked (no source under plugins/): {', '.join(rep['unchecked'])}")
    lines += _problems_block(rep["problems"])
    count = len(rep["missing"]) + len(rep["problems"])
    lines.append(f"result: {count} problem(s)" if count else "result: ok, every required dependency is attached")
    return "\n".join(lines)


def _types_text(types: list) -> str:
    core = [t for t in types if t.kind == "core"]
    plugin = [t for t in types if t.kind == "plugin"]
    width = max([len(t.name) for t in types] + [4])
    lines = ["core types (solaris/templates/projects/):" + ("" if core else " none")]
    lines += [f"  {t.name:<{width}}  {t.summary}" for t in core]
    lines.append("plugin types (plugins/<plugin>/<type>.project.md):" + ("" if plugin else " none"))
    for t in plugin:
        about = f"{t.title}: {t.summary}" if t.valid else \
            f"INVALID ({len(t.problems)} problem(s); see: plugins type {t.name})"
        lines.append(f"  {t.name:<{width}}  {about}")
    return "\n".join(lines)


def _type_text(t: ProjectType, root=None) -> str:
    data = type_json(t, root)
    lines = [f"{t.name} - {t.title or '(no title)'} ({data['file']})", f"summary: {t.summary}"]
    if t.kind == "plugin":
        if t.defaults:
            shown = {k: ", ".join(map(_as_text, v)) if isinstance(v, list) else _as_text(v)
                     for k, v in t.defaults.items()}
            lines.append("defaults: " + "; ".join(f"{k} {v}" for k, v in shown.items()))
        lines.append(f"attach, dependencies first: {', '.join(a['name'] for a in data['attach']) or 'none'}")
        if data["suggest"]:
            lines.append("suggest: " + ", ".join(f"{s['name']} ({s['why']})" if s["why"] else s["name"]
                                                 for s in data["suggest"]))
        if data["unavailable"]:
            lines.append(f"optional, not under plugins/: {', '.join(s['name'] for s in data['unavailable'])}")
        if t.questions:
            lines.append("questions:")
            for q in t.questions:
                extra = (" (required)" if q["required"] else "") + \
                    (f" [default {_as_text(q['default'])}]" if q["default"] is not None else "") + \
                    (f" [choices {', '.join(q['choices'])}]" if isinstance(q["choices"], list) else "")
                lines.append(f"  {q['key']}{extra}: {q['ask']} -> {q['placeholder']}")
        if data["overlay"]:
            lines.append(f"overlay (plugins/{t.plugin}/{t.template}):")
            lines += [f"  {o['action']} {o['target']}" for o in data["overlay"]]
        lines.append("sections: " + (", ".join(title for _norm, key, title in SECTIONS if t.sections.get(key))
                                     or "none"))
    lines += _problems_block(t.problems)
    lines.append("valid" if t.valid else f"INVALID: {len(t.problems)} problem(s)")
    return "\n".join(lines)


def _cmd_deps(args) -> int:
    # a plugins/ prefix and a trailing slash are fine, as in solaris.tools.revs
    names = [n.strip().rstrip("/") for n in args.plugins]
    rep = deps_report([n[len("plugins/"):] if n.startswith("plugins/") else n for n in names])
    print(json.dumps(rep, indent=2) if args.json else _deps_text(rep))
    return rep["exit"]


def _cmd_check(args) -> int:
    rep = check(args.dir)
    print(json.dumps(rep, indent=2) if args.json else _check_text(rep))
    return rep["exit"]


def _cmd_types(args) -> int:
    types = all_types()
    if args.json:
        print(json.dumps({"core": [type_json(t) for t in types if t.kind == "core"],
                          "plugin": [type_json(t) for t in types if t.kind == "plugin"]}, indent=2))
    else:
        print(_types_text(types))
    return 0


def _cmd_type(args) -> int:
    t = load_type(args.name)
    print(json.dumps(type_json(t), indent=2) if args.json else _type_text(t))
    return 0 if t.valid else PROBLEM


def _cmd_apply_type(args) -> int:
    if ":" not in args.name:
        print(f"plugins: apply-type takes a plugin type (<plugin>:<type>); {args.name!r} is not one, and core "
              "types have no overlay")
        return USAGE
    t = load_type(args.name)
    if not t.valid:
        print(f"refused: {t.name} is not a valid type (see: plugins type {t.name}):")
        print("\n".join(f"  {p}" for p in t.problems))
        return PROBLEM
    try:
        answers = json.loads(Path(args.answers).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise PluginsError(f"--answers {args.answers}: cannot read ({exc})") from None
    if not isinstance(answers, dict):
        raise PluginsError(f"--answers {args.answers}: must hold a JSON object of answers by key")
    # planned even without an overlay: --dir and the required answers are checked all the same
    steps, refusals, notes = plan_type(t, args.dir, answers)
    for note in notes:
        print(f"note: {note}")
    if refusals:
        print(*(f"refused: {r}" for r in refusals), sep="\n")
        print("nothing written")
        return PROBLEM
    if t.overlay is None:
        print(f"{t.name} has no overlay: nothing to apply")
        return 0
    for step in steps:
        print(f"{step.verb} {step.target}")
    n = {v: sum(1 for s in steps if s.verb == v) for v in ("create", "append", "unchanged")}
    if args.dry_run:
        print(f"dry run, nothing written: {n['create']} to create, {n['append']} to append, "
              f"{n['unchanged']} unchanged")
        return 0
    done: list = []
    try:
        apply_steps(args.dir, steps, done)
    except OSError as exc:
        print(f"plugins: write failed ({exc}); written before it: {', '.join(done) or 'nothing'}")
        return NOT_FOUND
    print(f"applied {t.name}: {n['create']} created, {n['append']} appended, {n['unchanged']} unchanged")
    return 0


COMMANDS = {"deps": _cmd_deps, "check": _cmd_check, "types": _cmd_types, "type": _cmd_type,
            "apply-type": _cmd_apply_type}


def main(argv: "list[str] | None" = None) -> int:
    parser = argparse.ArgumentParser(prog="solaris.tools.plugins", description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("deps", help="the required (transitive, dependencies first) and optional dependencies")
    p.add_argument("plugins", nargs="+", metavar="plugin")
    p.add_argument("--json", action="store_true", help="print JSON")
    p = sub.add_parser("check", help="a project's attached plugins against their dependencies (3: one missing)")
    p.add_argument("--dir", required=True, help="the project root (embedded mode: the repo root)")
    p.add_argument("--json", action="store_true", help="print JSON")
    p = sub.add_parser("types", help="list the core and plugin-provided project types")
    p.add_argument("--json", action="store_true", help="print JSON")
    p = sub.add_parser("type", help="one project type, parsed and validated (3: invalid)")
    p.add_argument("name", help="a core type, or <plugin>:<type>")
    p.add_argument("--json", action="store_true", help="print JSON")
    p = sub.add_parser("apply-type", help="copy a plugin type's overlay into a project (3: refused, nothing written)")
    p.add_argument("name", help="<plugin>:<type>")
    p.add_argument("--dir", required=True, help="the project root (embedded mode: the repo root)")
    p.add_argument("--answers", required=True, metavar="FILE",
                   help="JSON object: answers by question key, plus placeholders by name (SLUG, PACK, ...)")
    p.add_argument("--dry-run", action="store_true", help="print what would be written, write nothing")
    args = parser.parse_args(argv)
    try:
        return COMMANDS[args.command](args)
    except PluginsError as exc:
        print(f"plugins: {exc}")
        return NOT_FOUND


if __name__ == "__main__":
    raise SystemExit(main())
