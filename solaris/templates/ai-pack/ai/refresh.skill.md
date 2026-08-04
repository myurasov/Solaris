---
name: refresh
triggers: ["refresh", "refresh project", "update my checkout", "pull latest", "sync with upstream", "get the new version"]
summary: Bring a team member's existing checkout of {{NAME}} up to date - pull from git, migrate the private layer and environment to any new layout/conventions, and report what changed.
---
_Rev. 5_

# Skill: refresh - Update and Migrate a Local Checkout <!-- omit in toc -->

1. [Pre-Flight](#1-pre-flight)
2. [Pull](#2-pull)
3. [Migrate the Local Copy](#3-migrate-the-local-copy)
4. [Verify + Report](#4-verify--report)

Run when a team member wants an existing clone brought up to date. Everything is local: the skill never
pushes, and never touches private `ai/.memory/` content beyond layout migrations (pure renames/moves).
(Template stub: add this project's real dependency and environment refresh steps.)

## 1. Pre-Flight

1. `git status` - if the tree is dirty, show the changes and ask: commit, stash, or abort. Never discard
   local work silently.
2. Note the current commit (`git rev-parse --short HEAD`) for the change report.

## 2. Pull

`git pull --ff-only` from the main remote. On failure (diverged history), stop and show the situation -
resolving a divergence is a user decision, not a refresh step. When resolving merges later: a conflict in
`ai/manifest.json` `revisions` is mechanical - take either side and move on (it is Solaris sync metadata;
the Solaris-side maintainer re-records it on their next sync); conflicts in committed `*.jsonl` logs are
avoided by `*.jsonl merge=union` in `.gitattributes` (add it if missing).

## 3. Migrate the Local Copy

Apply in order; each step is idempotent (skips itself when already done):

1. Any layout migrations announced in the pulled diff (`AGENTS.md` / `ai/*.md` convention changes) -
   apply the mechanical ones, surface the rest.
2. New/updated plugin overlays: compare `ai/manifest.json` `plugins[]` against the `ai/` overlays present;
   report anything new (overlays arrive via git - nothing to install by hand).
3. Dependencies: (e.g. re-run the installer when a lockfile changed in the pull).

## 4. Verify + Report

- `ai/.memory/` still has the user's `resources.md` / `credentials.md`; `git status` is clean (or the
  step-1 stash is restorable).
- Summarize: commit range pulled, migrations applied, dependency refreshes run, anything the user should
  read. Log the turn in `ai/.memory/interactions.jsonl`.