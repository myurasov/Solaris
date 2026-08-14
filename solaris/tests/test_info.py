# Copyright 2026 Mikhail Yurasov <me@yurasov.me>
# SPDX-License-Identifier: Apache-2.0

"""The solaris/info/ masters and their pack-adapted copies in the ai-pack template stay in sync."""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
PACK_INFO = REPO_ROOT / "solaris" / "templates" / "ai-pack" / "ai" / "info"
_AS_OF_RE = re.compile(r"As of \*\*(\d{4}-\d{2}-\d{2})\*\*")


def _as_of(path: Path) -> str:
    m = _AS_OF_RE.search(path.read_text(encoding="utf-8"))
    assert m, f"no 'As of **YYYY-MM-DD**' date in {path}"
    return m.group(1)


def test_pack_info_dates_match_framework_masters():
    # updating a framework info file without syncing the pack copy is the drift this guards against
    for name in ("model-tiers.md", "harnesses.md"):
        master = REPO_ROOT / "solaris" / "info" / name
        pack = PACK_INFO / name
        assert pack.exists(), f"pack copy missing: {pack}"
        assert _as_of(master) == _as_of(pack), f"'as of' dates differ for {name}"


def test_pack_info_files_carry_rev_markers():
    for f in sorted(PACK_INFO.glob("*.md")):
        assert f.read_text(encoding="utf-8").startswith("_Rev. "), f"{f} missing _Rev. marker"
