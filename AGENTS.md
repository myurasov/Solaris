<!-- Canonical, always-on agent instructions for the Solaris framework. Minimal by design: pointers, not a manual. -->

# Solaris - Agent Instructions <!-- omit in toc -->

- [Read first (every session, and when starting a task)](#read-first-every-session-and-when-starting-a-task)
- [Execution model](#execution-model)
- [Skills](#skills)
- [Memory + logging](#memory--logging)
- [Conventions (pointers)](#conventions-pointers)

This is the **canonical, IDE-agnostic** instruction file, read on every turn by both Cursor and Claude
Code. Cursor reads `AGENTS.md` natively; Claude Code reads a one-line `CLAUDE.md` (`@AGENTS.md`) that imports
it (there is no `.cursor/rules` shim). Keep it minimal: it
is a set of pointers. The detail lives in the files it points to.

## Read first (every session, and when starting a task)

1. [`solaris/solaris.agent.md`](solaris/solaris.agent.md) - the framework agent role (orchestrator) and how Solaris is organized.
2. [`solaris/rules/commits.rule.md`](solaris/rules/commits.rule.md) - git commit policy (always applies).
3. [`solaris/rules/safety.rule.md`](solaris/rules/safety.rule.md) - confirm before destructive / remote-mutating / outward actions (always applies).

Run the `health-check` overview to orient **before you start working on a project** (the first
`develop-project` of a session) - surface only what needs attention (one line if all green). Otherwise run
it only on request; do **not** auto-run it for `ad-hoc-task` work or other prompts.

Full specification: [`solaris/spec/spec-v0.8.0.md`](solaris/spec/spec-v0.8.0.md).

## Execution model

One running agent adopts a **persona** by reading the active context:

- At the **Solaris root** (the command center) it is the **orchestrator** ([`solaris/solaris.agent.md`](solaris/solaris.agent.md)): it routes requests to skills, and manages projects under `projects/`, plugins under `plugins/`, and ad-hoc work under `tasks/`.
- Inside a **project** (`projects/<slug>/`) it is that project's **engineer** (`projects/<slug>/ai/engineer.agent.md`) plus the ai-pack (`ai/spec.md`, `ai/memory/*`) and every `ai/<plugin>/` overlay. It also reads `source/AGENTS.md` (if present) as project rules. In **embedded** mode the project root is the source repo at `projects/<slug>/<repo>/`, with `ai/` (and these `AGENTS.md`/`CLAUDE.md`) inside it - no separate `source/`.

"Hand off" means switching which instruction set + working directory is active - not spawning a separate process.

## Skills

Skills are markdown procedures in `solaris/skills/*.skill.md`, invoked by the trigger phrases below (no slash commands). Open the matching file and follow it in full.

| Skill | Trigger (examples) | Does |
|---|---|---|
| `create-project` | "create / new project" | Scaffold a new project + ai-pack (pick type / mode / plugins). |
| `import-project` | "import project", "adopt `<path or host:path>`" | Adopt an existing codebase; derive the ai-pack. |
| `import-plugin` | "create / update plugin", "make a plugin from `<project>`" | Author a plugin from a project, or fold project-local edits back into a plugin. |
| `install-plugin` | "install plugin `<git/folder/zip>`", "repair plugin `<name>`", "add plugin to `<project>`" | Acquire a plugin (its own repo) into `plugins/`, validate/repair it, optionally attach to a project. |
| `develop-project` | "work on / develop / open `<project>`" | Hand off to the project's engineer agent (plan or implement). |
| `update-project` | "update / migrate `<project>`" | Migrate an ai-pack + its plugins to the current framework version. |
| `self-reflect` | "self-reflect", "improve Solaris" | Review interaction logs; propose and (on approval) apply framework improvements. |
| `ad-hoc-task` | "new task", "research `<x>`", "set up `<host/thing>`" | Start / resume an ad-hoc task under `tasks/<date>-<slug>/`. |
| `health-check` | "health-check", "status", "health", "doctor" | Command-center overview (default) + health checks (`--deep`). |

When a project has plugins attached, also load and obey every `ai/<plugin>/*.rule.md` (always-on) and treat each `ai/<plugin>/*.skill.md` as an additional, trigger-invoked skill.

## Memory + logging

- Framework state is in `memory/`: `resources.md` (hosts, hardware), `credentials.md` (secrets; gitignored), `interactions.jsonl` (log). Project state is in each `projects/<slug>/ai/memory/` - including `context.md`, the engineer's verbose, model-facing context log (newest-first `## Log`, compacted into `## Previous History` past ~100KB; only the engineer + Solaris agents write it).
- ai-packs never read the framework `memory/`; needed values are copied into the project's own `ai/memory/` on init/update.
- A prompt-submit hook appends a raw-prompt backstop line to the master `memory/interactions.jsonl`; the engineer + Solaris agents append the authoritative `{ts, project, prompt, request, outcome}` line (`prompt` = raw prompt, `request` = its interpretation) to that master and, for project work, the same line to the project's `interactions.jsonl` plus a verbose `context.md` entry.

## Conventions (pointers)

- Python tools run as modules: `uv run -m solaris.tools.<name>` (`version`, `revs`, `mcp_sync`, `toc`). `log_interaction` is the prompt-submit **hook** only - never run it by hand (it reads stdin and will hang); you author the authoritative `{ts, project, prompt, request, outcome}` log lines yourself, in **both** `memory/interactions.jsonl` and the project's `ai/memory/interactions.jsonl`.
- Versioning: per-file **revisions** (`solaris.tools.revs`) keep ai-packs in sync with framework/plugin master copies (sync/merge by rev + content hash); semantic **versions** (pyproject / plugin `manifest.json`) are release-only - bumped on request or when publishing, with migrations only on minor/major bumps.
- File formats: human docs are Markdown (`.md`, user-editable); machine state is JSON (`*.json`, carrying `"_comment": "do not edit"`); append-only logs are JSON Lines (`.jsonl`). No standalone YAML (markdown/MDC frontmatter is exempt).
- Full conventions + architecture: [`solaris/spec/spec-v0.8.0.md`](solaris/spec/spec-v0.8.0.md).
