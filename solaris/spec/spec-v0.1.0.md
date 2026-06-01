# Solaris v0.1.0 - Specification <!-- omit in toc -->

- [Overview](#overview)
- [Repository layout](#repository-layout)
- [Dual-IDE wiring](#dual-ide-wiring)
- [Execution model](#execution-model)
- [Projects and the ai-setup](#projects-and-the-ai-setup)
- [Project modes](#project-modes)
- [Plugins](#plugins)
- [Command center (tasks)](#command-center-tasks)
- [Memory and interaction logging](#memory-and-interaction-logging)
- [Migration engine and versioning](#migration-engine-and-versioning)
- [Tools](#tools)
- [Conventions](#conventions)
- [Validation (v0.1.0 acceptance)](#validation-v010-acceptance)
- [Deferred (not in v0.1.0)](#deferred-not-in-v010)

Authoritative description of what Solaris v0.1.0 is and how it works. The implementation plan (build order,
decision history) is in [`plan-v0.1.0.md`](plan-v0.1.0.md); the original brief is [`spec-v0.txt`](spec-v0.txt).

## Overview

Solaris is a minimal framework for running many coding projects from one place (a "command center"). For
each project it generates a standardized, **portable ai-setup** that also works when the project is opened
on its own. Employer/domain-specific ways of working are factored into **plugins**. Ad-hoc engineering,
system-setup, and research work that is not a project lives under `tasks/`.

Solaris targets **Cursor** and **Claude Code** equally, via a single canonical `AGENTS.md` with thin
per-IDE shims. Its own tooling is Python (>=3.14), stdlib-only at runtime, run through `uv`.

Design priorities: minimal, portable ai-setups, no lock-in to one IDE, safe by default, and easy to migrate
forward as the framework evolves.

## Repository layout

```
<root>/                         # the Solaris git repo
  AGENTS.md  CLAUDE.md          # canonical instructions + Claude loader
  mcp.json.example              # MCP template (playwright); copied to runtime configs
  pyproject.toml  uv.lock       # python >=3.14; runtime stdlib only; pytest for tests
  .cursor/rules/solaris.mdc     # Cursor shim -> AGENTS.md
  .cursor/hooks.json  .claude/settings.json   # interaction-log hook (both IDEs)
  .githooks/commit-msg          # commit-policy enforcement (opt-in)
  solaris/                      # the framework (python package: solaris, solaris.tools)
    solaris.agent.md            # orchestrator role
    spec/  skills/  rules/  migrations/  templates/  tools/  tests/
  plugins/                      # plugin sources (gitignored except .empty)
    nvidia-isaac-lab/
  memory/                       # framework memory: resources.md, credentials.md, interactions.jsonl
  projects/                     # user projects (gitignored)
  tasks/                        # ad-hoc work (gitignored)
```

Gitignored: `.venv`, `.tmp`, `.tools`, `.mcp.json`, `.cursor/mcp.json`, `projects/`, `tasks/`,
`plugins/*` (except `.empty`), `solaris/spec/references/`, `memory/credentials.md`, `memory/interactions.jsonl`.

## Dual-IDE wiring

`AGENTS.md` is the single canonical, always-on instruction file. Cursor loads it via
`.cursor/rules/solaris.mdc` (`alwaysApply: true` shim); Claude Code loads it via `CLAUDE.md` (`@AGENTS.md`).
Neither shim duplicates content. `AGENTS.md` is minimal: pointers to the orchestrator role, the rules, the
inline skill catalog, the memory read order, and the plugin scan.

MCP is configured by a committed `mcp.json.example` (default server: `playwright`). The user copies it to
both `.mcp.json` (Claude Code) and `.cursor/mcp.json` (Cursor); `solaris.tools.mcp_sync` keeps them in sync.
`context7` is used via its CLI (`ctx7`), not as an MCP server. Per-project wiring mirrors this shape.

## Execution model

There is one running agent. It adopts a **persona** by reading the active context:

- **Orchestrator** (`solaris/solaris.agent.md`) at the Solaris root: routes requests to skills; manages
  projects, plugins, and tasks; keeps framework memory; does not write project source itself.
- **Developer** (`projects/<slug>/ai/developer.agent.md`) inside a project, with the ai-setup and every
  `ai/<plugin>/` overlay loaded, plus `src/AGENTS.md` (if present) as project rules.

"Hand off" means switching which instruction set + working directory is active, not spawning a process.

## Projects and the ai-setup

Each project lives at `projects/<slug>/`. The **ai-setup** is `ai/`:

```
ai/
  developer.agent.md            # combined coder + planner + runner (embeds commit + safety policy)
  manifest.json                 # descriptor: project {name,slug,type,mode}, framework_version, plugins[]
  spec.md                       # current spec (the contract)
  memory/
    spec-v0.md                  # preserved initial spec
    developer.instructions.md   # editable learned how-to-develop notes
    resources.md                # hosts, deploy target, hardware, APIs
    credentials.md              # secrets (gitignored)
    interactions.jsonl          # interaction log
  <plugin>/                     # materialized plugin overlay(s) - copies of each plugin's shared/
```

Project-root wiring (`AGENTS.md`, `CLAUDE.md`, `.cursor/rules/<slug>.mdc`, `mcp.json.example`, `.gitignore`)
points at the ai-setup. The ai-setup is generated from `solaris/templates/ai-setup/` with placeholder
substitution (`{{SLUG}}`, `{{NAME}}`, `{{TYPE}}`, `{{MODE}}`, `{{DESCRIPTION}}`, `{{FRAMEWORK_VERSION}}`,
`{{DATE}}`). It is portable: opened on its own, the developer agent still has everything it needs.

Project types are descriptions under `solaris/templates/projects/*.md` (core: `web-service`, `python-cli`,
`ios-app`) plus any a plugin contributes (`plugins/<name>/<type>.project.md`). `create-project` merges both
lists; choosing a plugin-provided type auto-attaches that plugin.

## Project modes

- **local** (default): code lives in `projects/<slug>/src/` (its own git root). Run locally; deploy by
  rsync over SSH to a host in `ai/memory/resources.md` (excludes `.venv`/`.git`/secrets/artifacts; no
  `--delete` by default); optional Docker.
- **remote-code**: there is no `src/`; a `remote.json` pointer records `host` + `path` (e.g.
  `mishasdell2-tailscale:~/IsaacLab`). The code already lives on the remote; it is edited and run in place
  over the IDE's Remote-SSH session. No deploy by default; sync is opt-in.

The mode is recorded in `ai/manifest.json` (`project.mode`).

## Plugins

A plugin adapts Solaris for a domain/employer/repo-specific way of working. Sources live at
`plugins/<name>/` (gitignored). Layout is flat (only `migrations/` is a subfolder):

```
plugins/<name>/
  manifest.json                 # name, version, description, applies_to (markers)
  mcps.json                     # MCP servers to merge into a project on install
  install.skill.md              # lifecycle: install / update / migrate / repair (NOT copied)
  <type>.project.md             # optional project-type(s) this plugin contributes
  shared/                       # the ONLY files copied into a project's ai/<name>/
    *.skill.md  *.rule.md       # procedures + always-on conventions (domain knowledge inline)
  migrations/                   # <to_version>.md (plugin-scoped; no registry)
```

- **Opt-in per project**, listed in `ai/manifest.json` `plugins[]`. Consumption is per-project: the
  developer agent loads each `ai/<name>/*.rule.md` (always-on) and `*.skill.md` (trigger-invoked) and folds
  the plugin's MCP servers into the project's MCP config. Plugins are additive - they never weaken core
  safety or commit rules.
- **Materialization boundary:** only `shared/` is copied into `ai/<name>/`. `install.skill.md`,
  `manifest.json`, `mcps.json`, and `migrations/` stay source-side (Solaris). So a detached project runs on
  the copied rules/skills, while lifecycle management requires Solaris.
- **Lifecycle** runs through the plugin's `install.skill.md`: **install** (copy `shared/`, merge MCPs,
  prompt for resources, record version), **update** (pre-overwrite edit check, then wholesale re-copy),
  **migrate** (run the plugin's `migrations/`), **repair** (restore copied files, re-merge MCPs).
- **Authoring + capture:** edit the source `plugins/<name>/`; `import-plugin` either creates a plugin from a
  project's domain specifics or folds project-local edits back into `shared/` (used by the pre-overwrite
  check so updates never lose local edits).

The bundled `nvidia-isaac-lab` plugin carries the NVIDIA/Isaac-specific workflow (NVBugs prep/triage/
try-and-fix/handoff, fork->develop git + PR conventions, `isaaclab.sh` CI checks, review-bot replies) and
the NVBugs MCP.

## Command center (tasks)

Ad-hoc engineering, system-setup, and research that is not a project lives under
`tasks/<YYYY-MM-DD>-<slug>/` (gitignored) - a `notes.md` plus scratch files. No ai-setup, no versioning. A
task that turns durable can graduate into a project (`create-project`/`import-project`) or a plugin
(`import-plugin`). `doctor` gives the overview (default) and health checks (`--deep`).

Deferred for later (considered, not in v0.1.0): a hosts registry, a `run-remote` helper, dedicated
`research` and `capture`/`recall` skills, and reusable `provision` recipes.

## Memory and interaction logging

- Framework `memory/`: `resources.md` (hosts/hardware), `credentials.md` (secrets; gitignored),
  `interactions.jsonl` (log). ai-setups never read this directory; needed values are copied into a
  project's own `ai/memory/` at init/update.
- A prompt-submit hook (`.claude/settings.json`, `.cursor/hooks.json` -> `solaris.tools.log_interaction`)
  appends one JSON line per turn. It is fail-safe (short timeout, swallows errors, never blocks the turn,
  tolerates a missing venv) and routes by cwd: inside `projects/<slug>/` -> that project's log, else the
  framework log. Logs are append-only and unbounded in v0 (small/rare).

## Migration engine and versioning

- The framework version is the single source of truth in `pyproject.toml` (`[project].version`); v0.1.0 is
  the base. There are no `.version` files - an ai-setup records `framework_version` in `ai/manifest.json`,
  and each attached plugin's materialized version is recorded per-plugin there too. Plugin source versions
  live in `plugins/<name>/manifest.json`.
- Migrations adapt an existing ai-setup to a newer framework version; they never touch the project's `src/`
  code. They live at `solaris/migrations/<to_version>.md` (frontmatter + Summary / Pre-flight / Migrate /
  Validate / Revert), optionally with helper scripts under `<to_version>/`. There is no registry file -
  `solaris.tools.version` scans `*.md` frontmatter to compute the chain.
- `update-project` is the sole entry point: it applies framework migrations one at a time, refreshes
  project-root wiring from current templates (diffs shown), and runs each attached plugin's lifecycle
  (`install.skill.md`) to update/migrate `ai/<plugin>/`. Plugins version independently with their own
  `migrations/`.

## Tools

Stdlib only; run as modules (`uv run -m solaris.tools.<name>`):

- `version` - framework version, ai-setup version, migration chain, plugin versions (`current`, `aisetup`,
  `check`, `chain`, `set`, `plugin`, `check-plugins`).
- `mcp_sync` - detect/sync drift between `.mcp.json` and `.cursor/mcp.json` (`--check` default, `--sync`).
- `log_interaction` - the fail-safe prompt-submit hook (not called by hand).
- `toc` - generate/verify Markdown tables of contents (`--check`/`--write`, `--all`).

All have unit tests under `solaris/tests/` (run `uv run pytest`).

## Conventions

- **File formats:** human-facing docs are Markdown (`.md`, user-editable, format-tolerant). Machine state is
  JSON (`manifest.json`, `remote.json`, `mcps.json`) carrying a top-level `"_comment": "do not edit"`.
  Append-only logs are JSON Lines (`.jsonl`). No standalone YAML data files; markdown/MDC frontmatter
  (Cursor `*.mdc`, migration `<ver>.md`) is exempt.
- **Markdown TOC:** every `.md` with two or more level-2+ headers carries a TOC (the level-1 title is marked
  `<!-- omit in toc -->`), maintained by `solaris.tools.toc`.
- **Naming:** kebab-case. Skills `*.skill.md`, rules `*.rule.md` (framework and plugins alike); Cursor
  shims `*.mdc`.
- **Commits** (`rules/commits.rule.md`, embedded in each `developer.agent.md`): one ASCII sentence,
  imperative, no `--`, no emoji, no AI-authorship attribution, atomic; confirm via a numbered list unless
  the user grants autonomy or uses `commit!`. The `.githooks/commit-msg` hook enforces the mechanical cases.
- **Safety** (`rules/safety.rule.md`, embedded too): confirm before destructive, remote-mutating, or
  outward actions; show the command/diff first; never print or commit secrets.

## Validation (v0.1.0 acceptance)

1. `uv run pytest` green (tools + toc).
2. `uv run -m solaris.tools.version current` -> `0.1.0`; `mcp_sync --check` behaves; `toc --check --all` clean.
3. **Todo app** (web-service, local): `create-project todo` -> `develop-project` builds a FastAPI + vanilla
   UI -> runs locally.
4. **Isaac Lab** (remote-code + `nvidia-isaac-lab`): `import-project` from `mishasdell2-tailscale:~/IsaacLab`
   -> plugin installs, NVBugs workflow available, matching the prior `__ai` behavior.

## Deferred (not in v0.1.0)

A second `documenter` persona; splitting `developer.agent.md` into separate coder/planner/runner; a base
`nvidia` plugin under `nvidia-isaac-lab`; a hosts registry and `run-remote`/`research`/`capture`/`provision`
command-center skills; the `ios-app` build/run workflow. These are documented so the design is stable, not
because they ship now.
