# Copyright 2026 Mikhail Yurasov <me@yurasov.me>
# SPDX-License-Identifier: Apache-2.0

"""Prompt-submit hook: auto-load matching skill procedures into the agent's context. Stdlib only.

Skills (``solaris/skills/*.skill.md``) are invoked by trigger phrases, not slash commands - which means
loading the right one used to depend on the agent recognizing the trigger and opening the file by hand, the
exact step that gets skipped (e.g. "lets work on tasks/..." should pull in ``ad-hoc-task`` but easily
doesn't). This hook makes the load deterministic: on every prompt it matches the text against each skill's
declared ``triggers`` and emits the full body of any match, so the harness (not the model) puts the
procedure in context.

It mirrors ``read_first``'s full-load-once + remind shape, but per *skill* and gated on a *match*:

- First time a skill matches in a session -> emit its **full body** under an authoritative header.
- Later turns where the same skill matches again -> emit a one-line **reminder** that it is already active.

Session de-dup uses the harness ``session_id`` from the stdin payload and a small JSON marker file under the
OS temp dir; a missing/unwritable marker just means the full body may load more than once (harmless).

Trigger matching is data-driven (no per-skill code). Each trigger string becomes a regex: ``<...>`` spans and
bare ``X`` placeholders match one argument word (``\\S+``); literal words match on word boundaries. So
``"work on <project>"`` matches "work on auth", and ``"new task"`` matches "start a new task". Broad triggers
match broadly - tighten the phrase in the skill's frontmatter if a skill over-fires. Synthetic turns (task
notifications, command transcripts, system reminders) are skipped entirely: they quote skill names without
requesting them, and the harness fires this hook on them too.

The hook also injects **project overlay indexes**: when the prompt (or session cwd) targets a project
under ``projects/``, it emits a one-line-per-file index of that project's always-on overlay files
(``ai/rules/*.rule.md``, ``ai/<plugin>/*.rule.md``, ``ai/*.link.md``) once per session per project, so overlay
compliance does not depend on the agent walking the project's AGENTS.md by hand.

Output is IDE-aware (Cursor JSON ``additional_context`` vs plain stdout), but injection is effectively
Claude-only: Cursor's ``beforeSubmitPrompt`` cannot add context (its output is only ``{continue,
user_message}``), so this hook is wired to Claude Code's ``UserPromptSubmit`` alone; on Cursor the agent
falls back to recognizing triggers from the skills table.

Like the other hooks it is **fail-safe**: it never raises, always exits 0, and tolerates missing files / a
missing venv - a broken skill load must never block the user's turn. As a footgun guard it refuses to run as
a hand-called CLI (any args, or an interactive tty with no piped payload) instead of blocking on stdin.
"""

from __future__ import annotations

import json
import os
import re
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SKILLS_DIR = REPO_ROOT / "solaris" / "skills"

_PH = "\x00"  # sentinel standing in for a matched ``<...>`` span while tokenizing a trigger


def read_payload(stream) -> dict:
    """Parse the hook's JSON stdin payload; any problem yields ``{}`` (fail-safe)."""
    try:
        raw = stream.read()
        if not raw or not raw.strip():
            return {}
        data = json.loads(raw)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def detect_ide(env: "dict | None" = None) -> str:
    """Best-effort IDE detection; Claude wins when both families are present (it wants plain stdout)."""
    env = os.environ if env is None else env
    if any(k.startswith("CLAUDE") for k in env):
        return "claude"
    if any(k.startswith("CURSOR") for k in env):
        return "cursor"
    return "unknown"


def _str_list(front: str, key: str) -> list:
    """Parse a ``key: [...]`` JSON-array frontmatter line into a list of non-empty strings ([] if absent)."""
    m = re.search(r"^" + re.escape(key) + r":\s*(\[.*\])\s*$", front, re.MULTILINE)
    if not m:
        return []
    try:
        vals = json.loads(m.group(1))
    except Exception:
        return []
    if not isinstance(vals, list):
        return []
    return [v for v in vals if isinstance(v, str) and v.strip()]


def parse_skill(text: str) -> "dict | None":
    """Extract ``{name, triggers, antitriggers, body}`` from a ``*.skill.md`` frontmatter; None if unparseable.

    ``antitriggers`` (optional) are compiled with the same phrase->regex rules as ``triggers``; if any matches
    the prompt the skill is suppressed even when a trigger also matched (e.g. ``develop-project`` excludes
    ``tasks/<slug>`` paths so "work on tasks/x" loads ``ad-hoc-task`` only).
    """
    # Tolerate the framework rev marker above the frontmatter ("_Rev. N_" is the first line of every
    # revisioned skill file); without this, rev-stamped skills silently never auto-load.
    text = re.sub(r"\A_Rev\.\s*\d+_\s*\n+", "", text)
    if not text.startswith("---"):
        return None
    parts = text.split("---", 2)
    if len(parts) < 3:
        return None
    front, body = parts[1], parts[2]
    name_m = re.search(r"^name:\s*(.+?)\s*$", front, re.MULTILINE)
    triggers = _str_list(front, "triggers")
    if not name_m or not triggers:
        return None
    return {
        "name": name_m.group(1).strip(),
        "triggers": triggers,
        "antitriggers": _str_list(front, "antitriggers"),
        "body": body.strip(),
    }


def discover_skills(skills_dir: Path = SKILLS_DIR) -> list:
    """All parseable skills under ``skills_dir``, sorted by name for stable output."""
    out = []
    try:
        files = sorted(skills_dir.glob("*.skill.md"))
    except Exception:
        return out
    for f in files:
        try:
            skill = parse_skill(f.read_text(encoding="utf-8"))
        except Exception:
            skill = None
        if skill:
            out.append(skill)
    return out


def trigger_to_regex(trigger: str) -> str:
    """Compile a trigger phrase into a regex source.

    ``<...>`` spans (even with internal spaces) and a bare ``X`` placeholder become ``\\S+`` (one argument
    word); every other word is matched literally. Word boundaries are added at the ends that are literal so
    e.g. ``"status"`` does not match "statuses".
    """
    protected = re.sub(r"<[^>]*>", _PH, trigger.strip())
    tokens = protected.split()
    if not tokens:
        return ""
    parts = []
    for tok in tokens:
        if tok == _PH or re.fullmatch(r"[A-Z]", tok):
            parts.append(r"\S+")
        else:
            parts.append("".join(r"\S+" if ch == _PH else re.escape(ch) for ch in tok))
    pattern = r"\s+".join(parts)
    if re.match(r"\w", tokens[0][0]):
        pattern = r"\b" + pattern
    if tokens[-1] != _PH and not re.fullmatch(r"[A-Z]", tokens[-1]) and re.search(r"\w$", tokens[-1]):
        pattern = pattern + r"\b"
    return pattern


def _any_match(phrases: list, prompt: str) -> bool:
    for ph in phrases:
        pat = trigger_to_regex(ph)
        if not pat:
            continue
        try:
            if re.search(pat, prompt, re.IGNORECASE):
                return True
        except re.error:
            continue
    return False


# Markers that identify a synthetic (harness-generated) turn rather than a human prompt: task
# notifications, command transcripts, and system reminders. Such turns freely quote skill names (an agent
# report saying "health-check" is not a request to run it), so matching them over-fires; skip them whole.
_SYNTHETIC_MARKERS = (
    "[SYSTEM NOTIFICATION",
    "<task-notification>",
    "<system-reminder>",
    "<command-name>",
    "<local-command-stdout>",
)


def is_synthetic_prompt(prompt: str) -> bool:
    """True when the payload is a harness-generated turn, not something the user typed."""
    return any(m in prompt for m in _SYNTHETIC_MARKERS)


def match_skills(prompt: str, skills: list) -> list:
    """Skills whose any trigger matches ``prompt`` and no antitrigger matches (order preserved, de-duped)."""
    if not prompt or is_synthetic_prompt(prompt):
        return []
    matched = []
    for skill in skills:
        if not _any_match(skill.get("triggers", []), prompt):
            continue
        if _any_match(skill.get("antitriggers", []), prompt):
            continue  # suppressed: an exclude phrase matched (e.g. develop-project vs a tasks/ path)
        matched.append(skill)
    return matched


# --- Project overlay injection -------------------------------------------------------------------
#
# Always-on project rules (ai/rules/*.rule.md, ai/<plugin>/*.rule.md) and linked-plugin pointers
# (ai/<name>.link.md) reach the agent only if it walks the project's AGENTS.md by hand - the exact
# step that gets skipped (agent-bench 2026-08-08: an overlay canary rule was missed by default).
# So when a prompt (or the session cwd) targets a project, name that project's overlay files
# explicitly - names only, one line each, to stay far from any inline-size limit; full bodies are
# the agent's job to open. Injected once per session per project via the same marker state.

_PROJECT_PATH_RE = re.compile(r"\bprojects/[A-Za-z0-9._-]+(?:/[A-Za-z0-9._-]+)*")


def _project_root(path: Path, repo_root: Path) -> "Path | None":
    """Nearest ancestor of ``path`` (inclusive) that is a project folder under ``repo_root/projects``.

    A project folder holds ``ai/manifest.json`` directly, or ``<repo>/ai/manifest.json`` (embedded
    mode). Projects sit one or two levels below ``projects/`` (grouped layout).
    """
    try:
        path = path.resolve()
        projects = (repo_root / "projects").resolve()
        rel = path.relative_to(projects)
    except Exception:
        return None
    for depth in (2, 1):  # grouped projects/<group>/<slug> first, then flat legacy
        parts = rel.parts[:depth]
        if len(parts) < depth:
            continue
        cand = projects.joinpath(*parts)
        if (cand / "ai" / "manifest.json").is_file():
            return cand
        try:  # embedded mode: the pack lives one level down, inside the repo
            for sub in cand.iterdir():
                if (sub / "ai" / "manifest.json").is_file():
                    return sub
        except Exception:
            pass
    return None


def find_project_targets(prompt: str, cwd: str, repo_root: Path = REPO_ROOT) -> list:
    """Project roots the turn targets: every ``projects/...`` path in the prompt, plus the cwd."""
    roots, seen = [], set()
    for m in _PROJECT_PATH_RE.finditer(prompt or ""):
        root = _project_root(repo_root / m.group(0), repo_root)
        if root and str(root) not in seen:
            seen.add(str(root))
            roots.append(root)
    if cwd:
        root = _project_root(Path(cwd), repo_root)
        if root and str(root) not in seen:
            roots.append(root)
    return roots


def overlay_files(project_root: Path) -> list:
    """Relative paths (from the project root) of the always-on overlay files, sorted."""
    ai = project_root / "ai"
    out = []
    try:
        out += ai.glob("*.rule.md")
        out += ai.glob("*/*.rule.md")
        out += ai.glob("*.link.md")
    except Exception:
        return []
    return sorted(str(p.relative_to(project_root)) for p in out)


def render_overlays(roots: list, already: set, repo_root: Path = REPO_ROOT) -> "tuple[str, list]":
    """One short block per first-seen project naming its overlay files; (text, new_marker_keys)."""
    parts, fresh = [], []
    for root in roots:
        try:
            rel = str(root.relative_to(repo_root))
        except Exception:
            rel = str(root)
        key = "overlay:" + rel
        if key in already:
            continue
        files = overlay_files(root)
        if not files:
            continue
        lines = "".join(
            "  - " + f + (" (linked plugin - follow the pointer)" if f.endswith(".link.md") else "")
            + "\n"
            for f in files
        )
        parts.append(
            "\n[Solaris project overlays] " + rel + " has always-on overlay files - open and obey "
            "each before working there (rule files apply always; this is just the index, not the "
            "content):\n" + lines
        )
        fresh.append(key)
    return "".join(parts), fresh


def state_path(session_id: str) -> Path:
    """Per-session marker file recording which skills were already fully loaded this session."""
    sid = re.sub(r"[^A-Za-z0-9._-]", "_", session_id or "nosession")[:128]
    return Path(tempfile.gettempdir()) / "solaris-skill-loader" / (sid + ".json")


def load_injected(session_id: str) -> set:
    try:
        data = json.loads(state_path(session_id).read_text(encoding="utf-8"))
        inj = data.get("injected", [])
        return set(inj) if isinstance(inj, list) else set()
    except Exception:
        return set()


def save_injected(session_id: str, names: set) -> None:
    try:
        p = state_path(session_id)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps({"injected": sorted(names)}), encoding="utf-8")
    except Exception:
        pass  # fail-safe: losing the marker only risks re-loading a body, never a broken turn


def _skill_block(skill: dict) -> str:
    name = skill["name"]
    return (
        "\n=== SOLARIS SKILL: " + name + " (auto-loaded by the skill_loader hook; your prompt matched a "
        "trigger) ===\n"
        "Follow this procedure in full for this work. Source: solaris/skills/" + name + ".skill.md\n\n"
        + skill["body"] + "\n"
    )


def render(matched: list, already: set) -> "tuple[str, list]":
    """Return (text, newly_loaded_names): full body for first-time matches, a one-liner for repeats."""
    fresh = [s for s in matched if s["name"] not in already]
    repeats = [s["name"] for s in matched if s["name"] in already]
    parts = [_skill_block(s) for s in fresh]
    if repeats:
        parts.append(
            "[Solaris skills] Already loaded this session (still in effect): "
            + ", ".join(repeats) + ".\n"
        )
    return "".join(parts), [s["name"] for s in fresh]


def emit(text: str, ide: str, stream=None) -> None:
    """Print context in the shape the calling IDE expects (Cursor JSON, else plain)."""
    if stream is None:
        stream = sys.stdout
    if not text:
        return
    if ide == "cursor":
        stream.write(json.dumps({"additional_context": text}))
    else:
        stream.write(text)


_NOT_A_CLI = (
    "solaris.tools.skill_loader is the prompt-submit skill-loader HOOK (it reads a JSON payload on "
    "stdin); it is not a command-line tool and takes no arguments. Do not call it by hand."
)


def main(argv: "list[str] | None" = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if argv:
        print(_NOT_A_CLI, file=sys.stderr)
        return 2
    try:
        if sys.stdin is None or sys.stdin.isatty():
            print(_NOT_A_CLI, file=sys.stderr)
            return 2
    except Exception:
        pass
    try:
        payload = read_payload(sys.stdin)
        prompt = payload.get("prompt") or payload.get("user_prompt") or ""
        if not isinstance(prompt, str):
            prompt = str(prompt)
        session_id = str(payload.get("session_id") or payload.get("sessionId") or "nosession")
        matched = match_skills(prompt, discover_skills())
        # Overlay targeting shares the synthetic-turn guard: harness-generated turns quote project
        # paths without working on them, exactly like they quote skill names.
        roots = [] if is_synthetic_prompt(prompt) else find_project_targets(
            prompt, str(payload.get("cwd") or payload.get("workspace_root") or ""))
        if not matched and not roots:
            return 0
        already = load_injected(session_id)
        text, fresh = render(matched, already)
        overlay_text, overlay_fresh = render_overlays(roots, already)
        emit(text + overlay_text, detect_ide())
        if fresh or overlay_fresh:
            save_injected(session_id, already | set(fresh) | set(overlay_fresh))
    except Exception:
        pass  # fail-safe: a context-loading hook must never break the user's turn
    return 0


if __name__ == "__main__":
    sys.exit(main())
