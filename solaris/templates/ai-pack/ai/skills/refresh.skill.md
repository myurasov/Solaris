---
name: refresh
triggers: ["refresh", "refresh project", "update my checkout", "pull latest", "sync with upstream", "get the new version"]
summary: Bring a team member's existing checkout of {{NAME}} up to date - auto-stash local edits, pull main, rebase the personal branch (agent-resolved conflicts), migrate the private layer and environment to any new layout/conventions, re-apply the stash, and report what changed.
---
_Rev. 9_

# Skill: refresh - Update and Migrate a Local Checkout <!-- omit in toc -->

1. [Pre-Flight](#1-pre-flight)
2. [Pull](#2-pull)
3. [Migrate the Local Copy](#3-migrate-the-local-copy)
4. [Verify + Report](#4-verify--report)

Run when a team member wants an existing clone brought up to date. Everything is local: the skill never
pushes, and never touches private `ai/.memory/` content beyond layout migrations (pure renames/moves).
(Template stub: add this project's real dependency and environment refresh steps.)

## 1. Pre-Flight

1. `git status` - if the tree is dirty, **stash automatically, do not ask**:
   `git stash push -u -m "refresh auto-stash"` (`-u` includes untracked files). The stash is
   re-applied in step 4; never `git clean`, never discard anything.
2. Note the current commit (`git rev-parse --short HEAD`) and branch for the change report.

## 2. Pull

When `"git.developer_branches"` is on (rule `ai/rules/git-collab.rule.md`) and the checkout
works on a personal branch: `git fetch origin`, `git switch main` + `git pull --ff-only`, then
`git switch` back to the personal branch and **rebase it automatically**: `git rebase main` -
never leave the personal branch behind main after a refresh. Resolve rebase conflicts
**yourself**, hunk by hunk, from context (the pulled changes plus the intent of the user's own
commits); ask the user only when genuinely in doubt (both sides changed the same lines with
incompatible intent) - then show the conflicting hunks and the options. Never abandon a
half-done rebase: if it cannot complete, `git rebase --abort` to restore the branch, then
surface why. A rebase rewrites the personal branch's history - if that branch was already
pushed, the next push (always on explicit ask, per the git-collab rule) needs
`--force-with-lease`; note that in the report. Otherwise (main-developer mode, or already on
`main`): `git pull --ff-only` from the main remote. On a pull failure (diverged history),
diagnose before asking:

- **Rewritten upstream (force-push)?** `git fetch`, then check `git rev-list --count origin/<branch>..HEAD`.
  If every local-only commit is the user's own work, stop and surface it. But when the local-only commits
  are just the **pre-rewrite versions** of what upstream now carries (same subjects/dates under a different
  identity or hash - the tell of a history rewrite, e.g. an author/committer cleanup), the fix is to adopt
  the rewritten history, NOT merge (a merge would weave the old and new histories back together):
  `git reset --hard origin/<branch>`. Confirm with the user first and re-check step 1's dirty-tree result -
  the reset discards local commits, so any genuinely local work must be stashed/rebased on top afterwards
  (`git stash` / `git cherry-pick` the user's own commits onto the new history).
- **Genuine divergence** (the user's own commits vs new upstream ones): stop and show the situation -
  resolving it is a user decision, not a refresh step.

When resolving conflicts (during the rebase or any merge): a conflict in
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

- Re-apply the step-1 auto-stash when one was made: `git stash pop`. Resolve any pop conflicts
  with the same policy (yourself first, ask only in genuine doubt); the stash entry must not be
  left behind silently - pop it, or tell the user why it stayed.
- `ai/.memory/` still has the user's `resources.md` / `credentials.md`; `git status` shows only
  the user's own re-applied changes.
- Summarize: commit range pulled, rebase result (conflicts resolved; force-with-lease note when
  the personal branch was already pushed), migrations applied, dependency refreshes run, anything
  the user should read. Log the turn in `ai/.memory/interactions.jsonl`.