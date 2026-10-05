# Copyright 2026 Mikhail Yurasov <me@yurasov.me>
# SPDX-License-Identifier: Apache-2.0

"""Tests for the hook commands in .claude/settings.json.

A hook runs in whatever folder the session's shell is in. Solaris is not an installed package
(pyproject.toml has package = false), so python -m solaris... resolves only from the repo root, and every
hook must pin it with uv run --directory.
"""

from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SETTINGS = REPO_ROOT / ".claude" / "settings.json"

# Hooks a run cannot make write to the real tree. log_interaction appends to the real interaction log, and
# read_first --part 4 and --remind sweep the real memory folders; no environment variable points them
# elsewhere, so those are checked as text only.
RUNNABLE = {
    ("solaris.tools.read_first", ()),
    ("solaris.tools.read_first", ("--part", "2")),
    ("solaris.tools.read_first", ("--part", "3")),
    ("solaris.tools.skill_loader", ()),
}


def hook_commands() -> list:
    try:
        settings = json.loads(SETTINGS.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return []
    return [hook["command"]
            for groups in settings.get("hooks", {}).values()
            for group in groups
            for hook in group.get("hooks", [])
            if hook.get("type") == "command"]


def module_and_args(command: str) -> tuple:
    tokens = shlex.split(command)
    if "-m" not in tokens[:-1]:
        return "", ()
    i = tokens.index("-m")
    return tokens[i + 1], tuple(tokens[i + 2:])


COMMANDS = hook_commands()
RUN = [c for c in COMMANDS if module_and_args(c) in RUNNABLE]


def test_settings_wire_hooks():
    assert COMMANDS, "no hook commands found in .claude/settings.json"
    assert RUN, "no hook command left that the test can run"


@pytest.mark.parametrize("command", COMMANDS)
def test_hook_pins_the_solaris_root(command):
    tokens = shlex.split(command)
    assert tokens[:2] == ["uv", "run"] and "--directory" in tokens and "-m" in tokens
    # After -m it would be an argument of the module, not of uv.
    assert tokens.index("--directory") < tokens.index("-m")
    assert "CLAUDE_PROJECT_DIR" in tokens[tokens.index("--directory") + 1]
    module, _ = module_and_args(command)
    assert module.startswith("solaris.tools.")
    assert (REPO_ROOT / (module.replace(".", "/") + ".py")).is_file(), module


@pytest.mark.skipif(shutil.which("uv") is None, reason="uv is not on PATH")
@pytest.mark.parametrize("command", RUN)
def test_hook_runs_from_a_nested_folder(command, tmp_path):
    # read_first renames a legacy memory/ folder on a session start: never in the real tree.
    if (REPO_ROOT / "memory").is_dir() and not (REPO_ROOT / ".memory").exists():
        pytest.skip("this checkout still has a legacy memory/ folder")
    # TMPDIR keeps any skill_loader session marker in tmp_path.
    env = dict(os.environ, CLAUDE_PROJECT_DIR=str(REPO_ROOT), TMPDIR=str(tmp_path))
    proc = subprocess.run(command, shell=True, cwd=REPO_ROOT / "solaris" / "tests", env=env,
                          input='{"session_id": "solaris-test-hooks"}', capture_output=True, text=True,
                          timeout=60)
    assert "No module named" not in proc.stdout + proc.stderr
    assert proc.returncode == 0, proc.stderr
    if module_and_args(command)[0] == "solaris.tools.read_first":
        assert "SOLARIS READ-FIRST" in proc.stdout
