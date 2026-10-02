---
name: self-reflect
triggers: ["self-reflect", "improve Solaris", "what should we improve", "tailor Solaris", "review project improvements"]
summary: Review interaction logs and the projects' improvement suggestions, surface ranked framework improvements, and (on approval) apply them.
---

# self-reflect <!-- omit in toc -->

1. [Gather Signal](#1-gather-signal)
2. [Propose (Ranked)](#2-propose-ranked)
3. [Apply on Approval](#3-apply-on-approval)
4. [Record](#4-record)

Lightweight, propose-only review of the framework itself. The only path by which the orchestrator edits core
framework files for self-improvement. No separate tailor/coder split.

## 1. Gather Signal

Read `.memory/instructions.md` (the accumulated operating lessons + user preferences - the **primary** source)
and the interaction logs, every machine's file merged by time: `uv run -m solaris.tools.interactions show
--last 0 --since <ISO date> --json` (framework) and, if relevant, the same with `--dir projects/<group>/<slug>`
for a project (projects are grouped one level below `projects/`; `<pack>/` is the project's ai-pack folder -
default `aipack/`, `ai/` in projects made before 0.39.0, any name) and `tasks/*/*/*/notes.md` (tasks live under
`tasks/<YYYY>/<MM>/`). Look for: repeated friction, the same manual fix done more than once, skills that
were hard to follow, missing capabilities the user reached for, and stale or contradictory instructions.

Read every project's `<pack>/.memory/improvements.md` too: every entry not yet listed in
`.memory/improvements-review.md`. Projects never edit framework or plugin files themselves (AGENTS.md,
non-negotiable 7), so these files are how their suggestions reach the framework. On "review project
improvements", review only these.

## 2. Propose (Ranked)

Present a short ranked list. For each: the observation (with evidence - which interactions), the suggested
change, the exact files it would touch (`solaris/...`), and effort. Distinguish:

- **Framework changes** - generic, benefit any project: edit `solaris/` (agent, skills, rules, templates,
  tools, spec).
- **Project-specific** - belongs in that project's `<pack>/instructions.md`, not the framework.
- **Plugin-worthy** - a domain workflow that recurs: suggest `import-plugin` (create) or extending an
  existing plugin.
- **Project improvement suggestions** - for each new `improvements.md` entry: adopt (name the framework or
  plugin file and how it would read, worded for any project), adapt, or decline with the reason. Merge
  entries that say the same thing across projects.

**Promote operating memory.** Treat every entry in `.memory/instructions.md` as a candidate: anything important
and reusable should be **promoted into the core framework** (spec / skills / rules / templates / tools). For
each, name the target file and the `.memory/instructions.md` entry it would retire.

## 3. Apply on Approval

For approved framework changes: make the edit, show the diff, and follow `rules/commits.rule.md`. Keep
changes minimal and consistent with surrounding style. After editing a revisioned framework/plugin file,
`revs bump` it and rebuild the ledger (`revs ledger`). Do not touch a project's `source/` or another user's
data. If a change alters the ai-pack schema or templates in a breaking way, that is a **minor/major**
release: author a migration under `solaris/migrations/` (see `migrations/template.md`) and bump the semver
in `pyproject.toml`. Routine content edits need only a rev bump, not a version bump. When an applied change
promotes an item from `.memory/instructions.md` into core, **delete that entry from `.memory/instructions.md`**
(it now lives in the framework).

## 4. Record

Log a turn summarizing what was changed and why: `uv run -m solaris.tools.interactions add --project solaris ...`.
When project suggestions were reviewed, update `.memory/improvements-review.md`: per project, each entry reviewed (its
date and first words), and whether it was adopted (target file and revision), adapted, or declined (why). Never
edit the projects' `improvements.md` files; they stay the projects' own record.
