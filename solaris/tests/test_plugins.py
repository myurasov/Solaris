# Copyright 2026 Mikhail Yurasov <me@yurasov.me>
# SPDX-License-Identifier: Apache-2.0

"""Every plugin that ships with the repo has a sound manifest, loadable skills, and the tools its docs name."""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

import pytest

from solaris.tools import revs

REPO_ROOT = Path(__file__).resolve().parents[2]
_SEMVER_RE = re.compile(r"^\d+\.\d+\.\d+$")
# a backticked path that starts directly with tools/; placeholders such as tools/<x> do not match
_TOOL_REF_RE = re.compile(r"`(tools/[\w./-]+)")


def _plugins() -> list:
    # symlinked or untracked plugin folders are private checkouts that exist only on one machine;
    # without git (or outside a checkout) only the symlinks are skipped
    try:
        out = subprocess.run(["git", "ls-files", "plugins"], cwd=REPO_ROOT, capture_output=True,
                             text=True, check=True).stdout
        tracked = {line.split("/")[1] for line in out.splitlines()}
    except (OSError, subprocess.CalledProcessError):
        tracked = None
    return sorted(d for d in (REPO_ROOT / "plugins").iterdir()
                  if (d / "manifest.json").is_file() and not d.is_symlink()
                  and (tracked is None or d.name in tracked))


PLUGINS = _plugins()


@pytest.mark.parametrize("plugin", PLUGINS, ids=lambda d: d.name)
def test_manifest_names_its_folder_with_semver(plugin):
    manifest = json.loads((plugin / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["name"] == plugin.name
    assert _SEMVER_RE.match(manifest["version"]), f"version {manifest['version']!r} is not X.Y.Z"


@pytest.mark.parametrize("plugin", PLUGINS, ids=lambda d: d.name)
def test_skills_parse_with_name_and_triggers(plugin):
    # plugin skills are read by revs for the pack README, which takes JSON or YAML-list triggers
    bad = []
    for f in sorted((plugin / "shared").glob("**/*.skill.md")):
        name, triggers = revs._skill_triggers(f)
        if not (name and triggers):
            bad.append(f.name)
    assert not bad, f"no triggers in the frontmatter of {bad}"


@pytest.mark.parametrize("plugin", PLUGINS, ids=lambda d: d.name)
def test_tools_named_in_docs_exist(plugin):
    shared = plugin / "shared"
    missing = [f"{f.name}: {ref}" for f in sorted(shared.glob("*.md"))
               for ref in _TOOL_REF_RE.findall(f.read_text(encoding="utf-8")) if not (shared / ref).exists()]
    assert not missing, f"tools/ paths named in docs but missing under {shared / 'tools'}: {missing}"
