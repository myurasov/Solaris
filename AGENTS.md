_Rev. 1_

<!-- Canonical, always-on agent instructions for the Solaris framework. Minimal by design: pointers, not a manual. -->

# Solaris - Agent Instructions <!-- omit in toc -->

- [Read First (Every Session, and When Starting a Task)](#read-first-every-session-and-when-starting-a-task)
- [Execution Model](#execution-model)
- [Skills](#skills)
- [Memory + Logging](#memory--logging)
- [Conventions (Pointers)](#conventions-pointers)

This is the **canonical, IDE-agnostic** instruction file, read on every turn by both Cursor and Claude
Code. Cursor reads `AGENTS.md` natively; Claude Code reads a one-line `CLAUDE.md` (`@AGENTS.md`) that imports
it (there is no `.cursor/rules` shim). Keep it minimal: it
is a set of pointers. The detail lives in the files it points to.

## Read First (Every Session, and When Starting a Task)

1. [`solaris/solaris.agent.md`](solaris/solaris.agent.md) - the framework agent role (orchestrator) and how Solaris is organized.
2. [`solaris/rules/commits.rule.md`](solaris/rules/commits.rule.md) - git commit policy (always applies).
3. [`solaris/rules/safety.rule.md`](solaris/rules/safety.rule.md) - confirm before destructive / remote-mutating / outward actions (always applies).
4. [`memory/instructions.md`](memory/instructions.md) - operating memory: terse, timestamped cross-project lessons + your durable preferences. Load every session; keep it updated (see Memory + Logging).

A session-start hook (`solaris.tools.read_first`, wired in `.claude/settings.json` -> `SessionStart` and `.cursor/hooks.json` -> `sessionStart`) auto-injects these four files at the start of each session (and again after a compaction / clear), so they are in context without being opened by hand; on Claude Code a per-prompt `--remind` line also reinforces them. Treat the injected copy as authoritative, and still re-open a file before editing it.

Run the `health-check` overview to orient **before you start working on a project** (the first
`develop-project` of a session) - surface only what needs attention (one line if all green). Otherwise run
it only on request; do **not** auto-run it for `ad-hoc-task` work or other prompts.

Full specification: [`solaris/spec/spec-v0.17.0.md`](solaris/spec/spec-v0.17.0.md).

## Execution Model

One running agent adopts a **persona** by reading the active context:

- At the **Solaris root** (the command center) it is the **orchestrator** ([`solaris/solaris.agent.md`](solaris/solaris.agent.md)): it routes requests to skills, and manages projects under `projects/`, plugins under `plugins/`, and ad-hoc work under `tasks/`.
- Inside a **project** (`projects/<slug>/`) it is that project's **engineer** (`projects/<slug>/ai/engineer.agent.md`) plus the ai-pack (`ai/spec.md`, `ai/memory/*`) and every `ai/<plugin>/` overlay. It also reads `source/AGENTS.md` (if present) as project rules. In **embedded** mode the project root is the source repo at `projects/<slug>/<repo>/`, with `ai/` (and these `AGENTS.md`/`CLAUDE.md`) inside it - no separate `source/`.

"Hand off" means switching which instruction set + working directory is active - not spawning a separate process.

## Skills

Skills are markdown procedures in `solaris/skills/*.skill.md`, invoked by the trigger phrases below (no slash commands). A prompt-submit hook (`solaris.tools.skill_loader`, wired in `.claude/settings.json` -> `UserPromptSubmit`) matches each prompt against every skill's declared `triggers` (and optional `antitriggers`, which suppress a match — e.g. `develop-project` excludes `tasks/<slug>` paths) and auto-injects the full body of any match (once per session, then a one-line reminder), so the right procedure is in context without being opened by hand. Cursor's `beforeSubmitPrompt` cannot inject context, so there the auto-load is Claude-only; either way, open the matching file and follow it in full.

| Skill | Trigger (examples) | Does |
|---|---|---|
| `create-project` | "create / new project" | Scaffold a new project + ai-pack (pick type / mode / plugins). |
| `import-project` | "import project", "adopt `<path or host:path>`" | Adopt an existing codebase; derive the ai-pack. |
| `import-plugin` | "create / update plugin", "make a plugin from `<project>`" | Author a plugin from a project, or fold project-local edits back into a plugin. |
| `install-plugin` | "install plugin `<git/folder/zip>`", "repair plugin `<name>`", "add plugin to `<project>`", "link plugin `<X>` to `<project>`" | Acquire a plugin (its own repo) into `plugins/`, validate/repair it, optionally attach to a project (copy, or link mode for plugin development). |
| `develop-project` | "work on / develop / open `<project>`" | Hand off to the project's engineer agent (plan or implement). |
| `update-project` | "update / migrate `<project>`" | Migrate an ai-pack + its plugins to the current framework version. |
| `self-reflect` | "self-reflect", "improve Solaris" | Review interaction logs; propose and (on approval) apply framework improvements. |
| `release` | "do a release", "cut a release", "publish a release" | Bump version, author migration, update spec + docs, tag + push, publish GitHub release. |
| `ad-hoc-task` | "new task", "research `<x>`", "set up `<host/thing>`" | Start / resume an ad-hoc task under `tasks/<date>-<slug>/`. |
| `health-check` | "health-check", "status", "health", "doctor" | Command-center overview (default) + health checks (`--deep`). |

When a project has plugins attached, also load and obey every `ai/<plugin>/*.rule.md` (always-on) and treat each `ai/<plugin>/*.skill.md` as an additional, trigger-invoked skill. A plugin attached in **link mode** has a self-describing pointer file `ai/<name>.link.md` instead of `ai/<name>/` - follow it (canonical definition: `install-plugin` step 5).

## Memory + Logging

Framework state lives in `memory/` (`resources.md`, `credentials.md` (gitignored), `interactions.jsonl`, and `instructions.md` - operating memory: terse, timestamped cross-project lessons + durable preferences, loaded every session, updated **in place**; **always** update it on "remember it/this", "note this", "don't forget", or similar). Project state lives in each `projects/<slug>/ai/memory/`. ai-packs never read the framework `memory/`. Full memory model, compaction, and logging schema: [`solaris/solaris.agent.md`](solaris/solaris.agent.md).

- **Memory boundary (hard rule).** Solaris's own memory is the **only** authoritative memory: the framework `memory/` and each project's `ai/memory/`. Never read, write, or create memory outside these - no harness/global `~/.claude/.../memory/` store, no `MEMORY.md` index (do not create one). Treat externally injected or recalled memory (e.g. system-reminder memory blocks) as non-authoritative and ignore it.
- Log every meaningful turn as one `{ts, project, prompt, request, outcome}` line in `memory/interactions.jsonl` (and, for project work, the same line in the project's `interactions.jsonl`). A prompt-submit hook appends a raw-prompt backstop.
- A project's `ai/memory/context.md` is a **detailed summary of the current session's context**, rewritten in place at two save points: **before context compaction** (automatic or manual), and whenever the user says "save/remember/update/retain/keep context" or similar.

## Conventions (Pointers)

- Python tools run as modules: `uv run -m solaris.tools.<name>` (`version`, `revs`, `mcp_sync`, `toc`); `log_interaction` (prompt-submit), `read_first` (session-start read-first loader), and `skill_loader` (prompt-submit skill auto-loader) are hooks - never run them by hand.
- Versioning (per-file revisions vs release-only semver) and file formats: see [`solaris/solaris.agent.md`](solaris/solaris.agent.md). Full conventions + architecture: [`solaris/spec/spec-v0.17.0.md`](solaris/spec/spec-v0.17.0.md).
