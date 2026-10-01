# Copyright 2026 Mikhail Yurasov <me@yurasov.me>
# SPDX-License-Identifier: Apache-2.0

"""Find a project's ai-pack folder, whatever its name.

The pack folder name is the project's choice: new projects default to ``aipack/``, projects made before
0.39.0 use ``ai/``, and any other name works. A project's pack is the one direct child folder of the
project root whose ``manifest.json`` is an ai-pack manifest (it carries ``framework_version`` and a
``project`` object; plugin manifests do not). Hidden folders are never packs. Stdlib only.
"""

from __future__ import annotations

import json
from pathlib import Path

DEFAULT = "aipack"  # name for new projects
LEGACY = "ai"  # name used by projects made before 0.39.0


class PackError(Exception):
    """The project has no ai-pack, or more than one."""


def is_pack_manifest(path: Path) -> bool:
    """True when ``path`` is a readable ai-pack manifest.json."""
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    return isinstance(data, dict) and "framework_version" in data and isinstance(data.get("project"), dict)


def find_pack(project_dir) -> "Path | None":
    """The project's pack folder, or None when it has none; PackError when it has more than one."""
    root = Path(project_dir)
    try:
        children = sorted(p for p in root.iterdir() if p.is_dir() and not p.name.startswith("."))
    except OSError:
        return None
    hits = [c for c in children if is_pack_manifest(c / "manifest.json")]
    if len(hits) > 1:
        raise PackError(f"{root}: more than one ai-pack ({', '.join(h.name for h in hits)})")
    return hits[0] if hits else None


def require_pack(project_dir) -> Path:
    """Like find_pack, but a project without a pack is an error too."""
    pack = find_pack(project_dir)
    if pack is None:
        raise PackError(f"{project_dir}: no ai-pack (no child folder holds an ai-pack manifest.json)")
    return pack
