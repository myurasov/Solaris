---
name: refresh
triggers: ["refresh solaris", "update solaris", "sync solaris", "pull latest solaris", "refresh the framework"]
summary: Bring this Solaris checkout up to date - pull from git (handling rewritten upstream history), resync the environment, verify, and flag projects that now need update-project.
---

# refresh <!-- omit in toc -->

1. [Pre-Flight](#1-pre-flight)
2. [Pull](#2-pull)
3. [Resync the Environment](#3-resync-the-environment)
4. [Verify](#4-verify)
5. [Flag Stale Projects + Report](#5-flag-stale-projects--report)

Update the **framework checkout itself** (this repo) on a machine that is not the one where the changes
were made - the command-center counterpart of the ai-pack `refresh` skill. Local only: never pushes.
The gitignored content trees (`projects/`, `tasks/`, `.memory/`, `plugins/` clones) are untouched by any
git step here - only framework files move.

## 1. Pre-Flight

1. `git status` - if framework files are dirty, show the changes and ask: commit, stash, or abort. Never
   discard local work silently. (Dirt in gitignored trees is irrelevant to the pull.)
2. Note the current commit (`git rev-parse --short HEAD`) and version
   (`uv run -m solaris.tools.version current`) for the change report.

## 2. Pull

`git fetch origin`, then `git merge --ff-only origin/<branch>`. On failure (diverged history), diagnose:

- **Rewritten upstream (force-push)?** If the local-only commits are just pre-rewrite versions of what
  upstream now carries (same subjects/dates under a different identity or hash - the tell of a history
  rewrite, e.g. an author/committer cleanup), adopt the rewritten history, do NOT merge (a merge weaves
  the old and new histories back together): confirm with the user, then `git reset --hard origin/<branch>`
  and re-apply any genuinely local commits on top (`git cherry-pick`). Tags may have moved too:
  `git fetch --tags --force`.
- **Genuine divergence** (local commits of real work vs new upstream ones): stop and show the situation -
  resolving it is a user decision, not a refresh step.

## 3. Resync the Environment

1. `uv sync` - dependencies follow `uv.lock`.
2. If hook wiring or hook tools changed in the pulled diff (`.claude/settings.json`, `.cursor/hooks.json`,
   `solaris/tools/read_first.py` / `skill_loader.py` / `log_interaction.py`): tell the user to restart the
   agent session afterwards - hooks are read at session start.
3. `uv run -m solaris.tools.mcp_sync --check` (fix with `--sync` if the pull changed MCP config).

## 4. Verify

- `uv run -m pytest solaris/tests -q` - the pulled framework must be green.
- `uv run -m solaris.tools.version current` - report the version change (old -> new).
- `uv run -m solaris.tools.read_first --check` - the read-first payload still fits inline.
- `uv run -m solaris.tools.revs status` - tracked files consistent.

## 5. Flag Stale Projects + Report

1. For each project (two-depth scan, `projects/*/` then `projects/*/*/`):
   `uv run -m solaris.tools.version check --dir <project>` - list any that now need `update-project`
   (do not run the migrations here; that is per-project work on request).
2. Summarize: commit range pulled, version old -> new (with the release notes' one-liners for any
   releases in between), environment steps run, verification results, stale projects flagged, and
   whether a session restart is needed. Log the turn to `.memory/interactions.jsonl`.
