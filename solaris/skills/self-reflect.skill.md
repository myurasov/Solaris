---
name: self-reflect
triggers: ["self-reflect", "improve Solaris", "what should we improve", "tailor Solaris"]
summary: Review interaction logs, surface ranked framework improvements, and (on approval) apply them.
---

# self-reflect <!-- omit in toc -->

- [1. Gather signal](#1-gather-signal)
- [2. Propose (ranked)](#2-propose-ranked)
- [3. Apply on approval](#3-apply-on-approval)
- [4. Record](#4-record)

Lightweight, propose-only review of the framework itself. The only path by which the orchestrator edits core
framework files for self-improvement. No separate tailor/coder split.

## 1. Gather signal

Read `memory/interactions.jsonl` (framework) and, if relevant, recent `projects/*/ai/memory/interactions.jsonl`
and `tasks/*/notes.md`. Look for: repeated friction, the same manual fix done more than once, skills that
were hard to follow, missing capabilities the user reached for, and stale or contradictory instructions.

## 2. Propose (ranked)

Present a short ranked list. For each: the observation (with evidence - which interactions), the suggested
change, the exact files it would touch (`solaris/...`), and effort. Distinguish:

- **Framework changes** - generic, benefit any project: edit `solaris/` (agent, skills, rules, templates,
  tools, spec).
- **Project-specific** - belongs in that project's `ai/memory/developer.instructions.md`, not the framework.
- **Plugin-worthy** - a domain workflow that recurs: suggest `import-plugin` (create) or extending an
  existing plugin.

## 3. Apply on approval

For approved framework changes: make the edit, show the diff, and follow `rules/commits.rule.md`. Keep
changes minimal and consistent with surrounding style. After editing a revisioned framework/plugin file,
`revs bump` it and rebuild the ledger (`revs ledger`). Do not touch a project's `src/` or another user's
data. If a change alters the ai-setup schema or templates in a breaking way, that is a **minor/major**
release: author a migration under `solaris/migrations/` (see `migrations/template.md`) and bump the semver
in `pyproject.toml`. Routine content edits need only a rev bump, not a version bump.

## 4. Record

Append a line to `memory/interactions.jsonl` summarizing what was changed and why.
