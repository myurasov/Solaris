# Solaris v0.2.0 - Specification <!-- omit in toc -->

- [Overview](#overview)
- [Repository layout](#repository-layout)
- [Dual-IDE wiring](#dual-ide-wiring)
- [Execution model](#execution-model)
- [Projects and the ai-setup](#projects-and-the-ai-setup)
- [Project modes](#project-modes)
- [Plugins](#plugins)
- [Versioning: revisions + semver](#versioning-revisions--semver)
- [Command center (tasks)](#command-center-tasks)
- [Memory and interaction logging](#memory-and-interaction-logging)
- [Tools](#tools)
- [Conventions](#conventions)
- [Validation (v0.2.0 acceptance)](#validation-v020-acceptance)
- [Deferred (not in v0.2.0)](#deferred-not-in-v020)

Authoritative description of Solaris v0.2.0. Supersedes [`spec-v0.1.0.md`](spec-v0.1.0.md) (kept as history,
alongside the original brief [`spec-v0.txt`](spec-v0.txt) and the v0.1.0 build plan
[`plan-v0.1.0.md`](plan-v0.1.0.md)). What changed in 0.2.0: per-file **revisions** drive ai-setup sync
(versions become release-only), `CLAUDE.md` and `.cursor/rules` shims are gone (both IDEs read `AGENTS.md`),
and project roots are trimmed to just `AGENTS.md`.

## Overview

Solaris is a minimal framework for running many coding projects from one place (a "command center"). For
each project it generates a standardized, **portable ai-setup** that also works opened on its own.
Employer/domain-specific ways of working are factored into **plugins**. Ad-hoc engineering, system-setup,
and research work that is not a project lives under `tasks/`.

Solaris targets **Cursor** and **Claude Code** equally via a single canonical `AGENTS.md` (both read it
natively). Its own tooling is Python (>=3.14), stdlib-only at runtime, run through `uv`.

## Repository layout

```
<root>/                         # the Solaris git repo
  AGENTS.md                     # canonical, always-on instructions (both IDEs read it natively)
  mcp.json.example              # MCP template (playwright); copied to runtime configs
  pyproject.toml  uv.lock       # python >=3.14; runtime stdlib only; pytest for tests
  .cursor/hooks.json  .claude/settings.json   # interaction-log hook (both IDEs)
  .githooks/commit-msg          # commit-policy enforcement (opt-in)
  solaris/                      # the framework (python package: solaris, solaris.tools)
    solaris.agent.md            # orchestrator role
    revisions.json              # rev + content-hash ledger for tracked framework files
    spec/  skills/  rules/  migrations/  templates/  tools/  tests/
  plugins/                      # plugin sources (gitignored except .empty)
  memory/                       # framework memory, gitignored except .empty (resources, credentials, interactions)
  projects/                     # user projects (gitignored)
  tasks/                        # ad-hoc work (gitignored)
```

There is **no `CLAUDE.md` and no `.cursor/rules`** anywhere. Gitignored: `.venv`, `.tmp`, `.tools`,
`.mcp.json`, `.cursor/mcp.json`, `projects/`, `tasks/`, `solaris/spec/references/`, and `plugins/*` /
`memory/*` (each except its `.empty`). A `.empty` placeholder keeps those two fully-ignored dirs present on
a fresh clone; the first time a skill writes real content into one, it deletes that `.empty`.

## Dual-IDE wiring

`AGENTS.md` is the single canonical instruction file, read every turn by both Cursor and Claude Code with no
shim. MCP is configured by a committed `mcp.json.example` (default server: `playwright`); the user copies it
to `.mcp.json` (Claude Code) and `.cursor/mcp.json` (Cursor), and `solaris.tools.mcp_sync` keeps the two in
sync. `context7` is used via its CLI (`ctx7`), not as an MCP server. Interaction-logging hooks live in
`.cursor/hooks.json` and `.claude/settings.json`.

## Execution model

One running agent adopts a **persona** by reading the active context: at the Solaris root, the
**orchestrator** (`solaris/solaris.agent.md`) routes to skills and manages projects/plugins/tasks; inside a
project, the **developer** (`projects/<slug>/ai/developer.agent.md`) loads the ai-setup, every
`ai/<plugin>/` overlay, and `src/AGENTS.md` if present. "Hand off" = switching the active instruction set +
working directory.

## Projects and the ai-setup

A project lives at `projects/<slug>/`. Its root carries only `AGENTS.md` (both IDEs read it) plus `ai/` and,
in local mode, `src/`. There is no `CLAUDE.md`, `.cursor/`, `mcp.json.example`, or `.gitignore` - the folder
is not committed. Runtime `.mcp.json` and `.cursor/mcp.json` are generated (gitignored) so the IDE has MCP
servers; plugin servers are merged into them on install.

```
projects/<slug>/
  AGENTS.md                     # the only authored root file
  .mcp.json  .cursor/mcp.json   # runtime MCP (gitignored)
  ai/
    developer.agent.md          # combined coder + planner + runner (carries a rev marker)
    manifest.json               # project {name,slug,type,mode}, framework_version, plugins[], revisions{}
    spec.md
    memory/  spec-v0.md  developer.instructions.md  resources.md  credentials.md  interactions.jsonl
    <plugin>/                   # materialized plugin overlay(s): copies of each plugin's shared/ (rev-marked)
  src/                          # local mode: code (own .git)    | remote-code: replaced by remote.json
```

The ai-setup is materialized from `solaris/templates/ai-setup/` with placeholder substitution (`{{SLUG}}`,
`{{NAME}}`, `{{TYPE}}`, `{{MODE}}`, `{{DESCRIPTION}}`, `{{FRAMEWORK_VERSION}}`, `{{DATE}}`). Project types
come from core (`solaris/templates/projects/*.md`) plus plugin-provided `plugins/<name>/<type>.project.md`;
choosing a plugin-provided type auto-attaches that plugin.

## Project modes

- **local** (default): code in `projects/<slug>/src/` (own git root). Run locally; deploy by rsync over SSH
  (excludes `.venv`/`.git`/secrets/artifacts; no `--delete` by default); optional Docker.
- **remote-code**: no `src/`; a `remote.json` records `host` + `path`. The code lives on the remote; it is
  edited and run in place over Remote-SSH. No deploy by default. The mode is recorded in `ai/manifest.json`.

## Plugins

A plugin adapts Solaris for a domain/employer/repo-specific way of working, and is its **own git
repository**. `install-plugin` acquires one from a remote git URL, a local folder, or a source zip into
`plugins/<name>/` (Solaris gitignores `plugins/*`, so plugin repos are never nested-committed), then
validates/repairs it and optionally attaches it to a project. The layout is flat (only `migrations/` is a
subfolder):

```
plugins/<name>/
  manifest.json                 # name, version (semver), description, applies_to, optional setup (install prompts/notes)
  mcps.json                     # MCP servers merged into a project's runtime MCP on install
  <type>.project.md             # optional project-type(s) this plugin contributes
  shared/                       # the ONLY files copied into a project's ai/<name>/ (each rev-marked)
    *.skill.md  *.rule.md
  migrations/                   # <to_version>.md for the plugin's own minor/major bumps
```

Opted into per project (`ai/manifest.json` `plugins[]`); the developer agent loads each `ai/<name>/*.rule.md`
(always-on) and `*.skill.md` (trigger). Only `shared/` is materialized. `install-plugin` installs (copy `shared/`, merge `mcps.json` into the
runtime MCP, run the plugin's `setup` prompts), updates/repairs via the revision sync below, and migrates on
the plugin's own minor/major bumps. `import-plugin` authors a plugin
from a project's domain specifics or folds project-local edits back into `shared/`; `install-plugin`
acquires/repairs a plugin source (git/folder/zip) and attaches it to a project.

The bundled `nvidia-isaac-lab` plugin carries the NVIDIA/Isaac workflow (NVBugs prep/triage/try-and-fix/
handoff, fork->develop git + PR conventions, `isaaclab.sh` CI checks, review-bot replies) and the NVBugs MCP.

## Versioning: revisions + semver

Two independent mechanisms.

**Per-file revisions** keep ai-setups in sync with framework/plugin master copies - this is the primary
sync mechanism (not version numbers). Every materialized framework/plugin file carries an integer rev
marker, bumped +1 per edit, and a **content hash that excludes the marker** (a pure rev bump never changes
the hash). Markers at the top of the file: `_Rev. N_` (md/mdc), `# rev. N` (py), a leading `"_rev": N` field (json). The
framework ledger `solaris/revisions.json` records current rev+hash + short history per tracked file; a
project records its baseline (`ai/manifest.json` -> `revisions`, `{rel: {rev, hash}}` at last sync). On
`update-project` / plugin update, `solaris.tools.revs classify` gives a per-file verdict:

| Verdict | Meaning | Action |
|---|---|---|
| in-sync | project hash == master hash | reconcile rev (no content change) |
| fast-forward | project == baseline, master advanced | overwrite from master (`revs ff`) |
| missing | not yet materialized | copy from master (`revs ff`) |
| merge-up | project rev > master rev | fold project edits up into master (`import-plugin` for plugins) |
| conflict | both changed since baseline | 3-way smart merge, asking the user per file/hunk |

**Semantic versions** are release-only. The framework version is in `pyproject.toml`; each plugin's in its
`manifest.json`. Bump on explicit request or when publishing to a public git remote. **Migrations
(`solaris/migrations/<to_version>.md`) are authored only for MINOR/MAJOR bumps; PATCH never requires one.**
`ai/manifest.json.framework_version` gates which migrations a project still needs; `solaris.tools.version`
scans `migrations/*.md` to compute the chain (no registry file). Migrations adapt `ai/` only - never `src/`.

## Command center (tasks)

Ad-hoc work that is not a project lives under `tasks/<YYYY-MM-DD>-<slug>/` (gitignored): a `notes.md` plus
scratch. No ai-setup, no versioning. A task that turns durable can graduate into a project or a plugin.
`health-check` gives the overview (default: projects, revisions, versions, tasks, MCP) and health checks
(`--deep`); the orchestrator runs the overview to orient **before working on a project** (the first
`develop-project` of a session) and on request - not for ad-hoc tasks (per `AGENTS.md`).

## Memory and interaction logging

Framework `memory/`: `resources.md` (hosts/hardware), `credentials.md` (secrets; gitignored),
`interactions.jsonl` (log). ai-setups never read it; needed values are copied into a project's own
`ai/memory/` at init/update. A prompt-submit hook (`log_interaction`) appends one JSON line per turn,
routed by cwd (inside `projects/<slug>/` -> that project's log, else framework). It is fail-safe and
unbounded in v0.

## Tools

Stdlib only; run as modules (`uv run -m solaris.tools.<name>`):

- `version` - framework + ai-setup semver, migration chain, plugin versions.
- `revs` - per-file revisions + rev-excluded content hashes: `bump`, `hash`, `status`, `ledger`,
  `classify --dir`, `ff --dir`, `baseline --dir`.
- `mcp_sync` - detect/sync drift between `.mcp.json` and `.cursor/mcp.json`.
- `log_interaction` - the fail-safe prompt-submit hook (not called by hand).
- `toc` - generate/verify Markdown tables of contents (`--check`/`--write`, `--all`).

All have unit tests under `solaris/tests/` (`uv run pytest`).

## Conventions

- **File formats:** human-facing docs are Markdown (`.md`, user-editable). Machine state is JSON
  (`manifest.json`, `remote.json`, `mcps.json`, `revisions.json`) carrying `"_comment": "do not edit"`.
  Append-only logs are JSON Lines (`.jsonl`). No standalone YAML data files (markdown frontmatter exempt).
- **Markdown TOC:** every `.md` with two or more level-2+ headers carries a TOC (the H1 is marked
  `<!-- omit in toc -->`), maintained by `solaris.tools.toc`.
- **Revisions:** after editing a tracked framework/plugin file, `revs bump` it and `revs ledger`.
- **Naming:** kebab-case. Skills `*.skill.md`, rules `*.rule.md`.
- **Commits** (`rules/commits.rule.md`, embedded in each `developer.agent.md`): one ASCII sentence,
  imperative, no `--`, no emoji, no AI-authorship attribution, atomic; confirm via numbered list unless the
  user grants autonomy or uses `commit!`. The `.githooks/commit-msg` hook enforces the mechanical cases.
- **Safety** (`rules/safety.rule.md`, embedded too): confirm before destructive, remote-mutating, or
  outward actions; show the command/diff first; never print or commit secrets.

## Validation (v0.2.0 acceptance)

1. `uv run pytest` green (tools + revs + toc).
2. `version current` -> `0.2.0`; `revs status` consistent; `revs classify`/`ff` behave on a project;
   `mcp_sync --check` and `toc --check --all` clean.
3. **Todo app** (web-service, local): `create-project todo` (AGENTS.md-only root, runtime MCP) ->
   `develop-project` builds a FastAPI + vanilla UI -> runs locally; app tests pass.
4. **Migration** `0.1.0 -> 0.2.0` authored and idempotent.

## Deferred (not in v0.2.0)

A second `documenter` persona; splitting `developer.agent.md`; a base `nvidia` plugin; a hosts registry and
`run-remote`/`research`/`capture`/`provision` command-center skills; the `ios-app` build/run workflow;
extending the revision/merge system beyond the materialized set; true automatic 3-way text merge (today the
tool classifies and the agent merges).
