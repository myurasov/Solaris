# Solaris <!-- omit in toc -->

- [Architecture](#architecture)
- [AI-Packs](#ai-packs)
- [Skills](#skills)
- [Plugins](#plugins)
- [Memory \& Versioning](#memory--versioning)
- [Install \& Tools](#install--tools)
- [Specification](#specification)

Solaris:

- Runs many coding projects from one place, injecting a maintainable "ai-pack" that allows projects to also be detached for standalone development. Each "ai-pack" evolves with the project by remembering what it needs to know short and long-term.

- Supports plugins that add project-specific functionality and workflows.

- Can work in "ad-hoc" mode for tasks that don't warrant a complete project structure.
  
## Architecture

- **One agent, persona by location.** At the root it is the **orchestrator** (routes prompts to skills;
  manages `projects/`, `plugins/`, `tasks/`, memory). Inside `projects/<slug>/` it is that project's
  **engineer** (plans, builds, runs). Hand-off = switch the active instruction set + working dir.
- **Single instruction source.** `AGENTS.md` is canonical - Cursor reads it natively; Claude Code via a
  one-line `CLAUDE.md` (`@AGENTS.md`) shim.
- **Hooks inject context.** `read_first` (SessionStart) preloads the orchestrator role + commit/safety
  rules + operating memory; `skill_loader` (UserPromptSubmit) matches prompt `triggers`/`antitriggers` and
  injects the matching skill body; `log_interaction` appends a raw-prompt log backstop.

## AI-Packs

Per-project bundle at `projects/<slug>/ai/`:

- **Shareable** - `engineer.agent.md` (role), `engineer.instructions.md` (build/run/test), `spec.md`,
  `manifest.json` (type, mode, framework version, plugins).
- **Private** (`ai/memory/`, drop to share) - `resources.md`, `credentials.md`, `context.md`,
  `interactions.jsonl`.

**Type**: `python-cli`, `web-service`, `ios-app`, or plugin-provided. **Mode**: `local` (code in
`source/`), `remote-code` (code on an SSH host), or `embedded` (ai-pack inside the source repo).

## Skills

Natural-language triggers route to Markdown procedures in `solaris/skills/*.skill.md`:

| Trigger | Skill | Action |
|---|---|---|
| "create / new project" | `create-project` | Scaffold a project + ai-pack (type / mode / plugins). |
| "import / adopt `<path>`" | `import-project` | Adopt an existing codebase; derive its ai-pack. |
| "work on / develop `<project>`" | `develop-project` | Hand off to the engineer to plan/implement. |
| "update / migrate `<project>`" | `update-project` | Migrate an ai-pack + plugins to the current version. |
| "create / install / repair a plugin" | `import-plugin`, `install-plugin` | Author, acquire, validate, attach plugins. |
| "do a release" | `release` | Bump version, author migration, update docs, tag + publish. |
| "self-reflect", "new task / research X", "health-check" | `self-reflect`, `ad-hoc-task`, `health-check` | Improve the framework; ad-hoc work under `tasks/`; status overview. |

## Plugins

A plugin packages a domain/employer workflow - `*.rule.md` (always-on), `*.skill.md` (trigger-invoked),
`mcps.json` (MCP servers), optional project types - opted into per project (`manifest.json` `plugins[]`) and
materialized into `ai/<name>/`. It is either **its own git repo** (acquired via `install-plugin` from a git
URL / folder / zip; ignored via `plugins/.gitignore`) or **bundled** under `plugins/`. Bundled:
`nvidia-isaac-lab` (NVBugs + Isaac workflow), `visual-qa` (VLM-based visual E2E testing).

## Memory & Versioning

- **Memory boundary.** Only framework `memory/` and each project's `ai/memory/` are authoritative (no
  global/harness store). `memory/instructions.md` is operating memory - terse timestamped cross-project
  lessons + preferences, loaded each session; turns log to `interactions.jsonl`.
- **Revisions** (`solaris.tools.revs`) keep materialized ai-pack files in sync with framework masters via
  `_Rev. N_` markers + a ledger. **Release-only semver** gates **migrations**
  (`solaris/migrations/<version>.md`) that upgrade a project's `ai/` without touching its code; plugins
  carry their own.

## Install & Tools

Requires [uv](https://docs.astral.sh/uv/) (manages Python 3.14) + Cursor or Claude Code; Node.js only for
the optional Playwright MCP.

```bash
uv sync                                                    # deps + venv (Python 3.14)
cp mcp.json.example .mcp.json                              # runtime MCP (Claude Code)
mkdir -p .cursor && cp mcp.json.example .cursor/mcp.json   # runtime MCP (Cursor)
uv run -m solaris.tools.mcp_sync --check                   # configs match?
git config core.hooksPath .githooks                        # optional commit-policy hook
```

Open the repo root in Cursor or Claude Code and talk to the agent (e.g. *"create a new python-cli project
called pingpong"*). Stdlib-only tools run as modules - `version`, `revs`, `mcp_sync`, `toc` (+ `uv run
pytest`); `read_first`, `skill_loader`, `log_interaction` are hooks, never run by hand.

## Specification

Full conventions, plugin contract, migration engine, project modes, and safety/commit policies:
[`solaris/spec/spec-v0.17.0.md`](solaris/spec/spec-v0.17.0.md). [Apache 2.0](LICENSE); Copyright 2026
Mihail Yurasov <me@yurasov.me>.
