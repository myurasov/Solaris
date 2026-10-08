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

Trigger matching is data-driven (no per-skill code). Each trigger string becomes a regex; literal words match
on word boundaries, so ``"new task"`` matches "start a new task". Placeholders:

- ``<project>`` names a real project: its slug or ``<group>/<slug>`` (folders ``projects/<group>/<slug>/``
  holding an ai-pack, or ``projects/<slug>/`` in the older flat layout), also written as a path that ends at
  the project (``projects/<slug>``, ``projects/my/<slug>/``), the nouns "project" / "repo" ("this project",
  "the shop repo"), or "pack" / "ai-pack" after an optional article ("update the pack", but never "the battery
  pack"). So ``"work on <project>"`` matches "work on web-shop" when that project exists, but not "work on
  those improvements"; a skill's ``antitriggers`` veto the contexts where the nouns mislead.
- ``<path>`` takes a path-like word (``~/code/app``, ``./app``, ``src/app``) and ``<host:path>`` a word with a
  colon (``box:/srv/app``, a URL), so "adopt a pdf style" adopts no codebase.
- Any other ``<...>`` span and a bare ``X`` stand for one argument word (``\\S+``) that is not a function
  word, after an optional article ("set up the box"): "research and experiments" names nothing to research.

Broad triggers still match broadly - tighten the phrase in the skill's frontmatter if a skill over-fires.
Turns the owner did not type are skipped entirely: harness wrappers (task notifications, system reminders,
command transcripts, subagent and peer-session messages), compaction summaries, interrupt markers, and a
subagent's report handed back as a plain turn. They quote skill names and projects without requesting them,
and the harness fires this hook on them too. An IDE's context block ahead of the owner's words (the file open
in the editor) is skipped, and the words after it are matched.

The hook also injects **project overlay indexes**: when the prompt (or session cwd) targets a project
under ``projects/``, it emits a one-line-per-file index of that project's always-on overlay files
(``<pack>/rules/*.rule.md``, ``<pack>/plugins/<plugin>/*.rule.md``, ``<pack>/plugins/*.link.md`` - plus the
legacy pre-0.28 locations ``<pack>/<plugin>/*.rule.md`` / ``<pack>/*.link.md``) once per session per project,
so overlay compliance does not depend on the agent walking the project's AGENTS.md by hand. ``<pack>`` is the
project's ai-pack folder, whatever its name (``aipack/`` by default, ``ai/`` in older projects), found by
solaris.tools.pack.

Output is IDE-aware (Cursor JSON ``additional_context`` vs plain stdout), but injection is effectively
Claude-only: Cursor's ``beforeSubmitPrompt`` cannot add context (its output is only ``{continue,
user_message}``), so this hook is wired to Claude Code's ``UserPromptSubmit`` alone; on Cursor the agent
falls back to recognizing triggers from the skills table.

Like the other hooks it is **fail-safe**: it never raises, always exits 0, and tolerates missing files / a
missing venv - a broken skill load must never block the user's turn. As a footgun guard it refuses to run as
a hand-called CLI (any args, or an interactive tty with no piped payload) instead of blocking on stdin.
"""

from __future__ import annotations

import functools
import json
import os
import re
import sys
import tempfile
from pathlib import Path

from solaris.tools import pack as P

REPO_ROOT = Path(__file__).resolve().parents[2]
SKILLS_DIR = REPO_ROOT / "solaris" / "skills"

# Sentinels standing in for ``<...>`` spans while tokenizing a trigger: a generic argument, ``<project>``,
# ``<path>`` and ``<host:path>``.
_PH, _PPH, _PATH_PH, _HOST_PH = "\x00", "\x01", "\x02", "\x03"
_SENTINELS = (_PH, _PPH, _PATH_PH, _HOST_PH)
_KIND = {"project": _PPH, "path": _PATH_PH, "host:path": _HOST_PH}


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


# Function words never stand for a placeholder's argument: "research and experiments", "set up and
# utilized" and "update it" name nothing to research, set up or update.
_FUNCTION_WORDS = frozenset((
    "a an the this that these those my our your his her its their some any each every all no both either "
    "neither such i me we us you he him she it they them myself yourself itself ourselves themselves and or "
    "but nor so yet to of in on at for from with by about as into onto over under after before during "
    "through via per without within between against toward towards across around upon is are was were be "
    "been being am do does did have has had can could will would shall should may might must not there here "
    "same"
).split())
# Words a placeholder may follow ("set up the box" sets up the box).
_ARTICLES = ("the", "a", "an", "this", "that", "these", "those", "my", "our", "your", "some")
_PROJECT_ARTICLES = ("the", "this", "that", "my", "our", "your")
# The nouns a <project> placeholder takes besides a real project's name ("publish this project", "the shop
# repo"), and the pack nouns, which take no name word before them ("update the pack", "the ai-pack"), so "the
# battery pack" names no project.
_PROJECT_NOUNS = ("project", "repo")
_PACK_NOUN = r"(?:ai[- ]?)?pack"


def _alt(words) -> str:
    """A non-capturing alternation of literal words, longest first."""
    return "(?:" + "|".join(re.escape(w) for w in sorted(words, key=len, reverse=True)) + ")"


_NOT_FUNCTION_WORD = r"(?!" + _alt(_FUNCTION_WORDS) + r"(?![\w'’-]))"
_ARG = r"(?:" + _alt(_ARTICLES) + r"\s+)?" + _NOT_FUNCTION_WORD + r"\S+"
# <path> takes a word that looks like a path (~, ./, /, C:\ or a slash inside), <host:path> one with a colon
# (host:/dir, user@host:dir, a URL): "adopt a pdf style" adopts no codebase.
_OPENERS = r"[`'\"(\[]*"
_PATH_ARG = _OPENERS + r"(?:~|\.{1,2}/|/|[A-Za-z]:[\\/]|[\w.@-]+/)\S*"
_HOST_PATH_ARG = _OPENERS + r"[\w.@-]+:\S+"


def _holds_pack(folder: Path) -> bool:
    """True when ``folder`` holds an ai-pack, directly or inside one repo folder (embedded mode)."""
    if _pack_of(folder):
        return True
    try:
        return any(sub.is_dir() and not sub.name.startswith(".") and _pack_of(sub) for sub in folder.iterdir())
    except OSError:
        return False


@functools.lru_cache(maxsize=4)
def _discover_project_names(root: str) -> frozenset:
    base = Path(root) / "projects"
    names = set()
    try:
        tops = sorted(d for d in base.iterdir() if d.is_dir() and not d.name.startswith("."))
    except OSError:
        return frozenset()
    for top in tops:
        if _pack_of(top):  # the older flat layout: projects/<slug>/ holds the pack itself
            names.add(top.name)
            continue
        try:
            subs = sorted(d for d in top.iterdir() if d.is_dir() and not d.name.startswith("."))
        except OSError:
            continue
        for sub in subs:
            if _holds_pack(sub):
                names.update((sub.name, top.name + "/" + sub.name))
    return frozenset(n.lower() for n in names)


def project_names(repo_root: "Path | None" = None) -> frozenset:
    """Lowercased names a prompt can give a real project: each slug and its ``<group>/<slug>`` path.

    A project is ``projects/<group>/<slug>/`` holding an ai-pack (directly, or inside its repo folder in
    embedded mode), or ``projects/<slug>/`` holding one in the older flat layout. Discovered once per process.
    """
    return _discover_project_names(str(repo_root or REPO_ROOT))


def _clean_names(names) -> frozenset:
    out = set()
    for n in names or ():
        n = str(n).strip().strip("/").lower()
        if n:
            out.add(n)
    return frozenset(out)


def _project_regex(names) -> str:
    """Regex source for a ``<project>`` placeholder: an optional article, then a real project's name (alone,
    or as a path that ends at the project, maybe quoted), the noun "project" / "repo" (maybe after one name
    word: "the shop repo"), or "pack" / "ai-pack" / "ai pack" / "aipack"."""
    noun_end = r"(?![^\s.,;:!?)\]\"`])"  # the noun itself: not "projects", "repo's" or "pack/"
    alts = []
    if names:
        alts.append(_OPENERS + r"(?:(?:[^\s`'\"]*/)?projects/)?" + _alt(names) + r"/?[`'\")\].,;:!?]*(?!\S)")
    alts.append(r"(?:" + _NOT_FUNCTION_WORD + r"[\w.-]+(?:['’]s)?\s+)?" + _alt(_PROJECT_NOUNS) + noun_end)
    alts.append(_PACK_NOUN + noun_end)
    return r"(?:" + _alt(_PROJECT_ARTICLES) + r"\s+)?(?:" + "|".join(alts) + ")"


def trigger_to_regex(trigger: str, projects=None) -> str:
    """Compile a trigger phrase into a regex source.

    A ``<project>`` span becomes a real project's name or a noun: "project", "repo", "pack" (module docstring);
    ``projects`` is the set of names it accepts, discovered under ``projects/`` when None. ``<path>`` takes a
    path-like word and ``<host:path>`` a word with a colon. Any other ``<...>`` span (even with internal
    spaces) and a bare ``X`` become one argument word that is not a function word, after an optional
    article. Inside a longer token (``tasks/<slug>``) a span is any ``\\S+``. Every other word is matched
    literally. Word boundaries are added at the ends that are literal so e.g. ``"status"`` does not match
    "statuses".
    """
    protected = re.sub(r"<([^>]*)>", lambda m: _KIND.get(m.group(1).strip().lower(), _PH), trigger.strip())
    tokens = protected.split()
    if not tokens:
        return ""
    parts = []
    for tok in tokens:
        if tok == _PPH:
            parts.append(_project_regex(project_names() if projects is None else _clean_names(projects)))
        elif tok == _PATH_PH:
            parts.append(_PATH_ARG)
        elif tok == _HOST_PH:
            parts.append(_HOST_PATH_ARG)
        elif tok == _PH or re.fullmatch(r"[A-Z]", tok):
            parts.append(_ARG)
        else:
            parts.append("".join(r"\S+" if ch in _SENTINELS else re.escape(ch) for ch in tok))
    pattern = r"\s+".join(parts)
    if re.match(r"\w", tokens[0][0]):
        pattern = r"\b" + pattern
    last = tokens[-1]
    if last not in _SENTINELS and not re.fullmatch(r"[A-Z]", last) and re.search(r"\w$", last):
        pattern = pattern + r"\b"
    return pattern


def _any_match(phrases: list, prompt: str, projects=None) -> bool:
    for ph in phrases:
        try:
            pat = trigger_to_regex(ph, projects)
            if pat and re.search(pat, prompt, re.IGNORECASE):
                return True
        except re.error:
            continue
    return False


# Turns the harness makes, not the owner, open with one of these wrapper tags (background-task
# notifications, system reminders, command transcripts, shell-mode input and output, subagent and
# peer-session messages) or plain markers (a notification header, a compaction summary, an interrupt note,
# a local-command caveat); a subagent hand-back names itself near its start. They quote skill names and
# project paths without requesting them (an agent report saying "health-check" is not a request to run it),
# so matching them over-fires; skip them whole.
_HARNESS_TAGS = (
    "task-notification", "system-reminder", "command-name", "command-message", "command-args",
    "local-command-stdout", "local-command-stderr", "local-command-caveat", "bash-input", "bash-stdout",
    "bash-stderr", "agent-message", "cross-session-message",
)
_HARNESS_TAG_RE = re.compile(r"<(?:" + "|".join(map(re.escape, _HARNESS_TAGS)) + r")(?=[\s>/])", re.I)
_HARNESS_PREFIXES = (
    "[SYSTEM NOTIFICATION",
    "This session is being continued from a previous conversation",
    "[Request interrupted",
    "Caveat: The messages below were generated by the user while running local commands",
)
_HANDBACK_MARK = "[Subagent hand-back]"
# A subagent's final report delivered as a plain turn: a long text whose first line is a heading about a
# report or a result. Owner prompts rarely open that way, and rarely run this long.
_REPORT_HEADING_RE = re.compile(
    r"(?:#{1,6}[ \t]+|\*\*)[^\n]*?\b(?:report|summary|results?|findings|hand-?back|outcome|done|completed?|"
    r"finished)\b", re.IGNORECASE)
_REPORT_MIN_CHARS = 1000
# Context blocks an IDE puts ahead of the owner's words (the file open in the editor, a selection).
_IDE_BLOCK_RE = re.compile(r"<(ide_[a-z_]+)\b[^>]*>.*?</\1>\s*", re.IGNORECASE | re.DOTALL)


def _after_ide_blocks(prompt: str) -> str:
    text = prompt.lstrip("\ufeff \t\r\n")
    m = _IDE_BLOCK_RE.match(text)
    while m:
        text = text[m.end():]
        m = _IDE_BLOCK_RE.match(text)
    return text


def is_synthetic_prompt(prompt: str) -> bool:
    """True when the turn was not typed by the owner: it opens with a harness wrapper tag or marker, names a
    subagent hand-back near its start, or is a long text whose first line is a report heading."""
    if not isinstance(prompt, str):
        return False
    text = _after_ide_blocks(prompt)
    return bool(_HARNESS_TAG_RE.match(text) or text.startswith(_HARNESS_PREFIXES)
                or _HANDBACK_MARK in text[:300]
                or (len(text) >= _REPORT_MIN_CHARS and _REPORT_HEADING_RE.match(text)))


def owner_text(prompt: str) -> str:
    """The owner's words in ``prompt`` (after any IDE context block), or "" when the turn is not the owner's."""
    if not isinstance(prompt, str) or is_synthetic_prompt(prompt):
        return ""
    return _after_ide_blocks(prompt).strip()


def match_skills(prompt: str, skills: list, projects=None) -> list:
    """Skills whose any trigger matches the owner's words in ``prompt`` and no antitrigger matches (order
    preserved). ``projects``: the names a ``<project>`` placeholder accepts (default: the real projects)."""
    text = owner_text(prompt)
    if not text:
        return []
    matched = []
    for skill in skills:
        if not _any_match(skill.get("triggers", []), text, projects):
            continue
        if _any_match(skill.get("antitriggers", []), text, projects):
            continue  # suppressed: an exclude phrase matched (e.g. develop-project vs a tasks/ path)
        matched.append(skill)
    return matched


# --- Project overlay injection -------------------------------------------------------------------
#
# Always-on project rules (<pack>/rules/*.rule.md, <pack>/plugins/<plugin>/*.rule.md) and linked-plugin
# pointers (<pack>/plugins/<name>.link.md) reach the agent only if it walks the project's AGENTS.md by hand -
# the exact step that gets skipped (agent-bench 2026-08-08: an overlay canary rule was missed by default).
# So when a prompt (or the session cwd) targets a project, name that project's overlay files
# explicitly - names only, one line each, to stay far from any inline-size limit; full bodies are
# the agent's job to open. Injected once per session per project via the same marker state.

_PROJECT_PATH_RE = re.compile(r"\bprojects/[A-Za-z0-9._-]+(?:/[A-Za-z0-9._-]+)*")


def _pack_of(folder: Path) -> "Path | None":
    """``folder``'s ai-pack, or None when it has none - or several, which leaves the project ambiguous."""
    try:
        return P.find_pack(folder)
    except Exception:
        return None


def _project_root(path: Path, repo_root: Path) -> "Path | None":
    """Nearest ancestor of ``path`` (inclusive) that is a project folder under ``repo_root/projects``.

    A project folder holds an ai-pack (a child folder with an ai-pack manifest.json, whatever its name)
    directly, or inside ``<repo>/`` (embedded mode). Projects sit one or two levels below ``projects/``
    (grouped layout).
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
        if _pack_of(cand):
            return cand
        try:  # embedded mode: the pack lives one level down, inside the repo
            for sub in cand.iterdir():
                if sub.is_dir() and _pack_of(sub):
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
    """Relative paths (from the project root) of the always-on overlay files in its ai-pack, sorted."""
    pack = _pack_of(project_root)
    if pack is None:
        return []
    out = []
    try:
        out += pack.glob("*.rule.md")
        out += pack.glob("*/*.rule.md")  # <pack>/rules/ + legacy pre-0.28 <pack>/<plugin>/ overlays
        out += pack.glob("plugins/*/*.rule.md")
        out += pack.glob("*.link.md")  # legacy pre-0.28 link-file location
        out += pack.glob("plugins/*.link.md")
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
