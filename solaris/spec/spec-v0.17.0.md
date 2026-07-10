# Solaris v0.17.1 - Specification <!-- omit in toc -->

- [Overview](#overview)
- [Repository layout](#repository-layout)
- [Dual-IDE wiring](#dual-ide-wiring)
- [Execution model](#execution-model)
- [Projects and the ai-pack](#projects-and-the-ai-pack)
- [Project modes](#project-modes)
- [Plugins](#plugins)
- [Versioning: revisions + semver](#versioning-revisions--semver)
- [Command center (tasks)](#command-center-tasks)
- [Memory and interaction logging](#memory-and-interaction-logging)
- [Tools](#tools)
- [Conventions](#conventions)
- [Validation (acceptance)](#validation-acceptance)
- [Deferred](#deferred)

Authoritative description of Solaris v0.17.1. Supersedes the 0.1.0-0.16.0 specs (in git history; the latest prior snapshot is
[`spec-v0.16.0.md`](spec-v0.16.0.md)), alongside the original brief [`spec-v0.txt`](spec-v0.txt) and the v0.1.0
build plan [`plan-v0.1.0.md`](plan-v0.1.0.md). What changed in 0.17.1 (patch): documentation housekeeping - the agent files (orchestrator `solaris.agent.md` and the ai-pack `engineer.agent.md` template) are slimmed, with machine-local tooling notes relocated to the instructions layer (framework `memory/instructions.md`; per-project `ai/engineer.instructions.md`, where a project may freely edit or delete them); the ai-pack template's commit/safety section headers drop their parenthetical qualifiers; and headers and titles use Title Case across docs, rules, skills, and templates. Content-only; no ai-pack schema change. What changed in 0.17.0 (see [`../migrations/0.17.0.md`](../migrations/0.17.0.md)): a new bundled plugin **`plugins/aisee/`** ships with the framework - knowledge-only "eyes" for visual verification during development, backed by the standalone **AISee** service (github.com/myurasov/AISee: vision-language models served on a GPU host; `look` / `assert_visual` / `watch` queries). The plugin carries `shared/aisee.rule.md` (always-on conventions: when the visual leg runs, assert-over-look, evidence and media rules) and `shared/aisee.skill.md` (trigger-invoked procedure: reach server -> capture -> query -> report, preferring **MCP over streamable HTTP** with local media uploaded once to the server's content-addressed blob store and referenced as `sha256:<hex>`, with REST and CLI fallbacks), plus `mcps.json` (an `http`-type MCP entry whose placeholder URL is substituted at install from the `aisee_server` setup resource, alongside the idempotent `playwright` capture entry) and install-time setup resources (server URL; optional consumer bearer token, stored as a secret). Additive and opt-in per project; no ai-pack schema change. What changed in 0.16.0 (see [`../migrations/0.16.0.md`](../migrations/0.16.0.md)): a second, opt-in plugin install mode - **link**. Instead of copying a plugin's `shared/` into `ai/<name>/`, `install-plugin` ("link plugin X to Y") writes a single self-describing pointer file **`ai/<name>.link.md`** naming the live `plugins/<name>/` source; the manifest entry is `{name, "mode": "link"}` (**no** `version` - a linked plugin always runs the live source, so there is nothing to drift). MCP merge and `setup` run exactly as in a copy install. The revs tools (`classify` / `ff` / `baseline`) skip linked entries, and `version check-plugins` reports them as `linked, source <v> (live)` (a missing source is a hard break: there is no materialized fallback). Link mode is a machine-local development convenience - used while authoring a plugin so edits hit the source directly, with no `import-plugin` fold-back - and swaps in place with a copy install in either direction ("install plugin X to Y" / "link plugin X to Y"); "unlink/detach" removes the attachment. Canonical definition: `install-plugin` step 5. Additive ai-pack manifest extension (optional `plugins[]` `mode` key); templates `AGENTS.md` rev 8 + `engineer.agent.md` rev 17 teach the engineer to follow link files. What changed in 0.15.1 (patch): the ai-pack `AGENTS.md` template now surfaces the project **slug** alongside its name/type/mode (`Project **{{NAME}}** (slug `{{SLUG}}`) ...`), so a generated pointer file identifies which `projects/<slug>/` it belongs to. Template wording only; no ai-pack schema change. What changed in 0.15.0 (see [`../migrations/0.15.0.md`](../migrations/0.15.0.md)): `ai/memory/context.md` is **redefined** - from an append-only, model-facing context log (Standing context / newest-first Log / Previous History) to a **detailed summary of the current session's context**, rewritten **in place** at two save points: **before context compaction** (automatic or manual - save first so no detail is lost), and whenever the user says "save/remember/update/retain/keep context" or similar. The engineer reads it first at session start (and right after a compaction) to restore context; per-turn logging stays in `interactions.jsonl`, and durable knowledge routes to `engineer.instructions.md` / `resources.md` / `spec.md`. Content-only (new `context.md` template, `engineer.agent.md` rev 16, orchestrator + skill wording); no ai-pack schema change - existing projects convert their `context.md` by hand via the migration. What changed in 0.14.0 (see [`../migrations/0.14.0.md`](../migrations/0.14.0.md)): a new always-on **remote-footprint rule** joins core (and the ai-pack `engineer.agent.md` template): everything Solaris installs on a remote host (services, tools, config, model/data caches) lives under **`~/.solaris/<component>/`** so the footprint is discoverable, inventoriable, and removable in one place; every installer ships with an uninstaller, and each install (host + path) is recorded in the relevant `resources.md`. The bundled **visual-qa plugin advances to 0.2.0** (with its first plugin migration): serving moves from a single `serve.sh` to lifecycle scripts (`install.sh` / `start.sh` / `status.sh` / `stop.sh` / `uninstall.sh`) that follow the remote-footprint rule and support multiple resident model instances (one container + port + GPU-memory slice each; `PORT=random` picks and persists a free port); `eyes.py` gains a serving-instance registry (`use` / `pick`), native video ingestion (fps sampling, chunked re-encode), and a `watch` tool for temporal assertions; `models.json` is expanded and re-ranked; the plugin README is consolidated at the plugin root. Content-only; no ai-pack schema change. What changed in 0.13.0 (see [`../migrations/0.13.0.md`](../migrations/0.13.0.md)): a new bundled plugin **`plugins/visual-qa/`** ships with the framework - a GPU-agnostic "eyes" for visual end-to-end testing (a pluggable vision-language model behind an OpenAI-compatible endpoint; `look` / `assert_visual` tools as an MCP server + CLI in `shared/eyes.py`; and a GPU-aware model recommender that ranks VLMs by VRAM + architecture + task over `shared/models.json`), plus a `serving/` runbook for vLLM / NIM / Ollama verified on a DGX Spark GB10. The **plugin-tracking model** is also generalized: a plugin may be its own git repository (ignored via `plugins/.gitignore`, e.g. `nvidia-isaac-lab`) **or** bundled in-framework under `plugins/` (tracked, keeping its own `revisions.json`); the blanket `plugins/*` gitignore is retired. Additive; no ai-pack schema change. What changed in 0.12.1 (patch): **skills are now auto-loaded by a hook** - a new stdlib tool `solaris.tools.skill_loader` is wired to Claude Code's `UserPromptSubmit`, matching each prompt against every skill's declared `triggers` (minus optional `antitriggers`) and injecting the full body of any match (once per session, then a one-line reminder), so the right procedure is in context without being opened by hand; `ad-hoc-task` gains `tasks/<slug>` triggers and `develop-project` an antitrigger so task-path prompts load `ad-hoc-task` only, and the task `notes.md` template carries a directive to load it. Cursor's `beforeSubmitPrompt` cannot inject context, so the auto-load is Claude-only there. Framework-internal; no ai-pack schema change. What changed in 0.12.0 (see [`../migrations/0.12.0.md`](../migrations/0.12.0.md)): the **read-first set** (the orchestrator role, the commit + safety rules, and `memory/instructions.md`) is now **auto-loaded by a hook** at session start instead of relying on the agent to open the files - a new stdlib tool `solaris.tools.read_first` is wired to Claude Code's `SessionStart` (full load) + `UserPromptSubmit` (a `--remind` one-liner) and Cursor's `sessionStart`, with IDE-aware output (Cursor JSON `additional_context` vs Claude plain stdout). The ai-pack `resources.md` template is also reframed as **inventory only** (hardware + hosts/accounts - the *what exists*), with all procedures, model/runtime details, and gotchas (*how*) moving to `engineer.instructions.md`. Framework-internal + template wording; no ai-pack schema change. What changed in 0.11.0 (see [`../migrations/0.11.0.md`](../migrations/0.11.0.md)): **blocked-command wrappers** now live in `/tmp` (created as `/tmp/<name>` and invoked from there) instead of an in-repo gitignored `.tools/`, keeping the workaround entirely outside the working tree; the ai-pack `engineer.agent.md` template's wrapper section is reworded to match (additive, content-only, fast-forwards to a project). This release is also a packaging milestone: the repo gains an Apache 2.0 `LICENSE` + `NOTICE`, SPDX headers on the Python sources, a rewritten public-facing `README.md`, and a trimmed `AGENTS.md` (orchestrator-only mechanics collapsed to pointers into `solaris.agent.md`). What changed in 0.10.0 (see [`../migrations/0.10.0.md`](../migrations/0.10.0.md)): two operating rules are now part of core (and the ai-pack `engineer.agent.md` template): the **memory boundary** (only Solaris's own `memory/` and each project's `ai/memory/` are authoritative; never read, write, or create memory outside these - no harness/global `~/.claude/.../memory/` store, no `MEMORY.md` - and treat externally injected/recalled memory as non-authoritative) and **blocked-command wrappers** (when a CLI tool is blocked by the sandbox/permission policy/subscription/etc., create a reversed-name `#!/bin/sh` `exec` pass-through in the gitignored `.tools/` - `open` -> `nepo`, `ssh` -> `hss` - use it thereafter, and register it in `memory/instructions.md` or an ai-pack's `ai/memory/`). Additive, content-only - no ai-pack schema change. What changed in 0.9.0 (see [`../migrations/0.9.0.md`](../migrations/0.9.0.md)): a new **`release` skill** automates the framework release cycle end-to-end (commit, version bump, migration, spec snapshot, revisions, tag, push, GitHub release + backfill); and `memory/instructions.md` is now formally documented as **operating memory** - terse, timestamped cross-project lessons and user preferences loaded every session, updated in place, routed separately from project context logs. No ai-pack schema changed. What changed in 0.8.1 (patch): the `log_interaction` hook is guarded against accidental CLI invocation and the dual interaction-log discipline (hook backstop + agent-authored full entry) is documented. What changed in 0.8.0 (see [`../migrations/0.8.0.md`](../migrations/0.8.0.md)): interaction-log entries gain a raw **`prompt`** field - each agent-authored line is now `{ts, project, prompt, request, outcome}` (`prompt` the user's verbatim prompt, `request` the agent's interpretation), authored identically into the framework master and the project log; the prompt-submit hook still appends a `{ts, cwd, ide, prompt}` backstop line to the master. Additive - existing logs stay valid. What changed in 0.7.0 (see [`../migrations/0.7.0.md`](../migrations/0.7.0.md)): the private working-context file `ai/memory/info.md` is renamed to **`ai/memory/context.md`** and redefined as a **verbose, model-facing context log** - richer than `interactions.jsonl`, capturing the model's own answers/decisions/findings in prose, with a curated "Standing context" section that survives compaction, a newest-first "Log", and a "Previous History" of compacted older entries once Log grows past ~100KB; only the engineer and Solaris agents write it. What changed in 0.6.1: the **embedded** layout is clarified - the whole project repo (code + `ai/` + `AGENTS.md`/`README`/dotfiles + its own `.git`) lives at `projects/<slug>/<repo>/`; the slug folder is a non-git container for the repo plus non-repo aux; and the repo's `.gitignore` excludes `ai/memory/` **and** `.secrets.env`. What changed in 0.6.0 (see [`../migrations/0.6.0.md`](../migrations/0.6.0.md)): the local-mode code directory is renamed **`src/` -> `source/`** (`projects/<slug>/source/` - the engineer's working dir, what `--remote` rsyncs, and where the project's own `git init` runs; a nested `ui/src/` etc. is unaffected). A new **opt-in `embedded`
project mode** also lets the ai-pack live *inside* the source repo (`projects/<slug>/<repo>/ai/`, no separate
`source/`), chosen at create/import time. What changed in 0.5.0 (see [`../migrations/0.5.0.md`](../migrations/0.5.0.md)): `ai/manifest.json` holds only project metadata + versions - host/deploy/port/secret specifics live in `ai/memory/` (`resources.md` / `credentials.md`); the engineer **bootstraps `ai/memory/` interactively** when it is missing (a shared ai-pack); and each **plugin keeps its own** revision ledger at `plugins/<name>/revisions.json` (the framework `solaris/revisions.json` tracks only framework masters). What changed in 0.4.1: a minimal `CLAUDE.md` (`@AGENTS.md`)
shim is restored beside every `AGENTS.md` so **Claude Code** loads the canonical instructions (Cursor reads
`AGENTS.md` natively). What changed in 0.4.0 - a terminology + conventions release
(see [`../migrations/0.4.0.md`](../migrations/0.4.0.md)): the project persona **`developer` -> `engineer`**
(`developer.agent.md` -> `engineer.agent.md`, `developer.instructions.md` -> `engineer.instructions.md`) and
the **ai-setup -> ai-pack** (the `solaris/templates/ai-pack/` template dir, the `version` tool's `aisetup`
subcommand -> `aipack`, and the term throughout). Two conventions are now explicit: a project's `ai/spec.md`
is **self-sufficient** (reads standalone, references no other file), and **every change to a revisioned file
increments its rev**. Interaction logging is also clarified - each turn is one
`{ts, project, prompt, request, outcome}` record (`prompt` the raw user prompt, `request` the agent's
interpretation): the framework `memory/interactions.jsonl` is the master of every turn (incl handed-off
project work), a project's `ai/memory/interactions.jsonl` its relevant slice. What
changed in 0.3.0: `engineer.instructions.md` moved out of `ai/memory/` up to `ai/`
- the shareable, portable layer alongside `engineer.agent.md` and `spec.md` - leaving `ai/memory/` as the
private/local layer; see [`../migrations/0.3.0.md`](../migrations/0.3.0.md).

## Overview

Solaris is a minimal framework for running many coding projects from one place (a "command center"). For
each project it generates a standardized, **portable ai-pack** that also works opened on its own.
Employer/domain-specific ways of working are factored into **plugins**. Ad-hoc engineering, system-setup,
and research work that is not a project lives under `tasks/`.

Solaris targets **Cursor** and **Claude Code** equally via a single canonical `AGENTS.md`: Cursor reads it
natively, Claude Code via a one-line `CLAUDE.md` (`@AGENTS.md`) shim. Its own tooling is Python (>=3.14), stdlib-only at runtime, run through `uv`.

## Repository layout

```
<root>/                         # the Solaris git repo
  AGENTS.md                     # canonical, always-on instructions (Cursor reads it natively)
  CLAUDE.md                     # one-line @AGENTS.md shim so Claude Code loads AGENTS.md
  mcp.json.example              # MCP template (playwright); copied to runtime configs
  pyproject.toml  uv.lock       # python >=3.14; runtime stdlib only; pytest for tests
  .cursor/hooks.json  .claude/settings.json   # interaction-log + read-first loader hooks (both IDEs)
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

Every `AGENTS.md` has a sibling one-line `CLAUDE.md` (`@AGENTS.md`) so Claude Code loads it; there is **no
`.cursor/rules`** anywhere. Gitignored: `.venv`, `.tmp`, `.tools`,
`.mcp.json`, `.cursor/mcp.json`, `projects/`, `tasks/`, `solaris/spec/references/`, and `plugins/*` /
`memory/*` (each except its `.empty`). A `.empty` placeholder keeps those two fully-ignored dirs present on
a fresh clone; the first time a skill writes real content into one, it deletes that `.empty`.

## Dual-IDE wiring

`AGENTS.md` is the single canonical instruction file. **Cursor** reads it natively; **Claude Code** reads a
one-line `CLAUDE.md` shim (`@AGENTS.md`) that imports it - only `AGENTS.md` is authored, and both load it
every turn. MCP is configured by a committed `mcp.json.example` (default server: `playwright`); the user copies it
to `.mcp.json` (Claude Code) and `.cursor/mcp.json` (Cursor), and `solaris.tools.mcp_sync` keeps the two in
sync. `context7` is used via its CLI (`ctx7`), not as an MCP server. The interaction-log and read-first
loader hooks live in `.cursor/hooks.json` and `.claude/settings.json`.

## Execution model

One running agent adopts a **persona** by reading the active context: at the Solaris root, the
**orchestrator** (`solaris/solaris.agent.md`) routes to skills and manages projects/plugins/tasks; inside a
project, the **engineer** (`projects/<slug>/ai/engineer.agent.md`) loads the ai-pack, every
`ai/<plugin>/` overlay, and `source/AGENTS.md` if present. "Hand off" = switching the active instruction set +
working directory.

## Projects and the ai-pack

A project lives at `projects/<slug>/`. Its root carries `AGENTS.md` (Cursor) + a one-line `CLAUDE.md`
(`@AGENTS.md`, Claude Code) plus `ai/` and, in local mode, `source/` (which carries the same `AGENTS.md` +
`CLAUDE.md` pair when it has project rules). There is no `.cursor/`, `mcp.json.example`, or `.gitignore` - the
folder is not committed. Runtime `.mcp.json` and `.cursor/mcp.json` are generated (gitignored) so the IDE has MCP
servers; plugin servers are merged into them on install.

```
projects/<slug>/
  AGENTS.md                     # the authored root instructions (Cursor)
  CLAUDE.md                     # one-line @AGENTS.md shim (Claude Code)
  .mcp.json  .cursor/mcp.json   # runtime MCP (gitignored)
  ai/                           # shareable layer (engineer.agent.md + engineer.instructions.md + spec.md)
    engineer.agent.md          # combined coder + planner + runner (carries a rev marker)
    engineer.instructions.md   # shareable build/run/test commands + conventions (no host/secret specifics)
    manifest.json               # project {name,slug,type,mode}, framework_version, plugins[], revisions{}
    spec.md
    memory/                     # private/local layer (not for sharing): env-specific + sensitive bits
      spec-v0.md  resources.md  credentials.md  context.md  interactions.jsonl
    <plugin>/                   # materialized plugin overlay(s): copies of each plugin's shared/ (rev-marked)
    <plugin>.link.md            # OR a linked plugin: self-describing pointer to the live plugins/<name>/ source
  source/                          # local mode: code (own .git)    | remote-code: replaced by remote.json
```

In **embedded** mode the ai-pack lives *inside* the source repo instead of beside it. The repo (its own
`.git`) sits at `projects/<slug>/<repo>/` (name it `source` or after the repo) and holds **everything** - the
code, `ai/`, `AGENTS.md` + `CLAUDE.md`, `README`, and the repo's own dotfiles. The slug folder
`projects/<slug>/` is then a **non-git container**: the repo plus any non-repo local aux (e.g. `references/`,
`screenshots/`) that should not ship with the repo. The repo's `.gitignore` keeps the private layer out -
both `ai/memory/` and any `.secrets.env`:

```
projects/<slug>/                # container (not a git repo)
  references/  screenshots/      # non-repo local aux, kept outside <repo>
  <repo>/                        # THE repo (its own .git); e.g. "source"
    .gitignore  .secrets.env     # .gitignore excludes ai/memory/ + .secrets.env
    AGENTS.md  CLAUDE.md  README.md
    ai/                          # the ai-pack, embedded in the repo
      engineer.agent.md  engineer.instructions.md  spec.md  manifest.json  memory/  <plugin>/
    ...                          # the repo's own code + files
```

Tools that take `--dir` get `projects/<slug>/<repo>/` (the dir holding `ai/`) for an embedded project.

The ai-pack splits into a **shareable layer** (`ai/engineer.agent.md`, `ai/engineer.instructions.md`,
`ai/spec.md`, and the `ai/<plugin>/` overlays - portable, safe to share or hand off) and a **private/local
layer** (`ai/memory/`: hosts, secrets, internal URLs, the preserved spec, the session-context summary, logs). To share an ai-pack, drop
`ai/memory/`. It is materialized from `solaris/templates/ai-pack/` with placeholder substitution (`{{SLUG}}`,
`{{NAME}}`, `{{TYPE}}`, `{{MODE}}`, `{{DESCRIPTION}}`, `{{FRAMEWORK_VERSION}}`, `{{DATE}}`). Project types
come from core (`solaris/templates/projects/*.md`) plus plugin-provided `plugins/<name>/<type>.project.md`;
choosing a plugin-provided type auto-attaches that plugin.

## Project modes

- **local** (default): code in `projects/<slug>/source/` (own git root). Run locally; deploy by rsync over SSH
  (excludes `.venv`/`.git`/secrets/artifacts; no `--delete` by default); optional Docker.
- **remote-code**: no `source/`; a `remote.json` records `host` + `path`. The code lives on the remote; it is
  edited and run in place over Remote-SSH. No deploy by default. The mode is recorded in `ai/manifest.json`.
- **embedded** (opt-in): the ai-pack lives *inside* the source repo. `projects/<slug>/<repo>/` (e.g.
  `source`) is the **whole** repo (its own `.git`) - code, `ai/`, `AGENTS.md` + `CLAUDE.md`, `README`,
  dotfiles - and the slug folder above it is a non-git container for the repo plus non-repo aux
  (`references/`, `screenshots/`). The shareable layer commits and travels with the repo; the repo's
  `.gitignore` excludes `ai/memory/` **and `.secrets.env`**. Chosen explicitly at create/import time; tools
  take `--dir projects/<slug>/<repo>/`.

## Plugins

A plugin adapts Solaris for a domain/employer/repo-specific way of working. A plugin is either its **own
git repository** - `install-plugin` acquires one from a remote git URL, a local folder, or a source zip
into `plugins/<name>/`, and `plugins/.gitignore` ignores it so it is never nested-committed (e.g.
`nvidia-isaac-lab`) - **or authored in-place and bundled** in the framework repo under `plugins/` (tracked;
e.g. `visual-qa`). Either way `install-plugin` validates/repairs the plugin source and optionally attaches
it to a project, each plugin keeps its own `revisions.json`, and the framework ledger never tracks plugin
files. The layout is flat (only `migrations/` is a subfolder):

```
plugins/<name>/
  manifest.json                 # name, version (semver), description, applies_to, optional setup (install prompts/notes)
  mcps.json                     # MCP servers merged into a project's runtime MCP on install
  <type>.project.md             # optional project-type(s) this plugin contributes
  shared/                       # the ONLY files attached to a project: copied to ai/<name>/, or linked (each rev-marked)
    *.skill.md  *.rule.md
  migrations/                   # <to_version>.md for the plugin's own minor/major bumps
```

Opted into per project (`ai/manifest.json` `plugins[]`); the engineer agent loads each `ai/<name>/*.rule.md`
(always-on) and `*.skill.md` (trigger). Only `shared/` is materialized. Attachment comes in two modes:
**copy** (the default - `{name, version}` in `plugins[]`, `shared/` copied to `ai/<name>/`) and **link**
(`{name, "mode": "link"}`, no `version` - a self-describing pointer file `ai/<name>.link.md` names the live
`plugins/<name>/` source, which the engineer loads directly; the revs tools and plugin migrations skip
linked entries, and `version check-plugins` reports them as live). Link mode is a machine-local development
convenience for plugin authoring and swaps in place with a copy install in either direction; canonical
definition in `install-plugin` step 5. `install-plugin` installs (copy `shared/` or write the link file,
merge `mcps.json` into the runtime MCP, run the plugin's `setup` prompts), updates/repairs via the revision
sync below, and migrates on the plugin's own minor/major bumps. `import-plugin` authors a plugin
from a project's domain specifics or folds project-local edits back into `shared/`; `install-plugin`
acquires/repairs a plugin source (git/folder/zip) and attaches it to a project.

The bundled `nvidia-isaac-lab` plugin carries the NVIDIA/Isaac workflow (NVBugs prep/triage/try-and-fix/
handoff, fork->develop git + PR conventions, `isaaclab.sh` CI checks, review-bot replies) and the NVBugs MCP.
The bundled `visual-qa` plugin provides VLM-based visual end-to-end testing: a pluggable vision-language
model behind an OpenAI-compatible endpoint on any NVIDIA GPU, `look` / `assert_visual` tools (MCP + CLI), a
GPU-aware model recommender (by VRAM + architecture + task), and vLLM / NIM / Ollama serving runbooks.

## Versioning: revisions + semver

Two independent mechanisms.

**Per-file revisions** keep ai-packs in sync with framework/plugin master copies - this is the primary
sync mechanism (not version numbers). Every materialized framework/plugin file carries an integer rev
marker, bumped +1 per edit, and a **content hash that excludes the marker** (a pure rev bump never changes
the hash). Markers at the top of the file: `_Rev. N_` (md/mdc), `# rev. N` (py), a leading `"_rev": N` field (json). The
framework ledger `solaris/revisions.json` records current rev+hash + short history per tracked **framework**
file; each **plugin keeps its own** ledger at `plugins/<name>/revisions.json` (keys relative to the plugin),
so a plugin's rev history travels inside its own repo - never in the framework ledger. A project records its
baseline (`ai/manifest.json` -> `revisions`, `{rel: {rev, hash}}` at last sync). On
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
scans `migrations/*.md` to compute the chain (no registry file). Migrations adapt `ai/` only - never `source/`.

## Command center (tasks)

Ad-hoc work that is not a project lives under `tasks/<YYYY-MM-DD>-<slug>/` (gitignored): a `notes.md` plus
scratch. No ai-pack, no versioning. A task that turns durable can graduate into a project or a plugin.
`health-check` gives the overview (default: projects, revisions, versions, tasks, MCP) and health checks
(`--deep`); the orchestrator runs the overview to orient **before working on a project** (the first
`develop-project` of a session) and on request - not for ad-hoc tasks (per `AGENTS.md`).

## Memory and interaction logging

Framework `memory/`: `resources.md` (hardware + hosts/accounts inventory), `credentials.md` (secrets; gitignored),
`interactions.jsonl` (log), and `instructions.md` (operating memory; see below). ai-packs never read it; needed values are copied into a project's own
`ai/memory/` at init/update. **These two stores - the framework `memory/` and each project's `ai/memory/` -
are the only authoritative memory in Solaris.** Agents never read, write, or create memory outside them: not
a harness/global `~/.claude/.../memory/` store, not any `MEMORY.md` index (Solaris never creates one), and any
externally injected or recalled memory is treated as non-authoritative. A project's `ai/memory/` is its **private/local layer** (resources,
credentials, the preserved `spec-v0.md`, the session-context summary, interaction log); the **shareable** how-to-develop notes live one
level up in `ai/engineer.instructions.md`, and any host/secret/internal-URL detail that surfaces there is
relocated down into `ai/memory/` rather than dropped. Host/deploy targets, hardware, APIs, and secrets live
only in `ai/memory/` (`resources.md` / `credentials.md`), never in `ai/manifest.json` (which holds project
metadata, versions, plugins, and revisions only). **`resources.md` is inventory only** - hardware and
hosts/accounts (*what exists*); all procedures (build/run/deploy/restart), model/runtime details, and gotchas
(*how*) live in `ai/engineer.instructions.md` as generic patterns that reference `resources.md` for concrete
values. When an ai-pack is shared without its private layer
(`ai/memory/` dropped), the engineer detects the missing or empty `ai/memory/` on first run and **bootstraps
it interactively** - asking the user for hosts / deploy target / APIs / secrets and writing `resources.md`,
`credentials.md`, and a fresh `context.md` - before doing project work.

**Operating memory (`memory/instructions.md`).** Framework-level, cross-project working knowledge: terse,
**timestamped** entries (`- [YYYY-MM-DD] ...`) on how to work with hosts/tools, recurring gotchas, and the
user's durable preferences - distinct from any project's `ai/memory/context.md`. The Solaris agent loads it
every session and updates it **in place** (merge, never duplicate) whenever a reusable fact/preference/gotcha
surfaces, and **always** when the user says "remember it/this" or similar. Routing: cross-project/global goes
here; project-specific to that project's `context.md`; hosts/secrets to `resources.md`/`credentials.md`. It
is kept terse (context-cheap), carries a `solaris.tools.toc` TOC, and is compacted **oldest-first** (by
timestamp) once it passes ~100KB. `self-reflect` promotes important, reusable entries into the core framework
and then deletes them here. Private/local (gitignored); ai-packs never read it.

**Session-context summary (`ai/memory/context.md`).** A detailed summary of the **current session's**
context: the task(s) and their state, decisions with their reasons, findings, key file references, open
questions, and immediate next steps - everything a fresh session (or the same session after compaction)
needs to continue immediately. It complements `interactions.jsonl` (the terse per-turn machine record) and
is **rewritten in place** (its `## Session context` section replaced, not appended) at two save points:
**before context compaction** - automatically ahead of an auto-compaction, or when the user compacts
manually - so no detail is lost, and **on request**, whenever the user says "save/remember/update/retain/keep
context" or similar. The engineer reads it first at session start (and right after a compaction) to restore
context. Durable cross-session knowledge does not live here - it routes to `engineer.instructions.md` (how),
`resources.md` (what exists), or `spec.md` (the contract). **Only the project engineer and Solaris's own
agents (orchestrator + skills) write it** - plugins and subagents do not. It carries a `solaris.tools.toc`
table of contents like any other doc. The file is private/local and gitignored; on a shared ai-pack it is
bootstrapped fresh with the rest of `ai/memory/`.

**Interaction logs (prompt + request + outcome).** Each meaningful turn is recorded as one append-only JSON
line `{ts, project, prompt, request, outcome}`, where **`prompt`** is the user's verbatim raw prompt,
**`request`** is the agent's interpreted restatement of it, and **`outcome`** is what happened. The **agent**
authors this full entry into **both** the framework master `memory/interactions.jsonl` (the record of **all**
turns - orchestrator work and every handed-off project turn) **and**, for project work, the touched
**project's** `ai/memory/interactions.jsonl` (a subset of the master) - identical schema in both. Only the
agent can write it: it alone knows the interpreted request, the outcome, and the true project, since "hand
off" does not change the cwd. The prompt-submit hook (`log_interaction`) independently appends a raw-prompt
backstop line (`{ts, cwd, ide, prompt}`) to the framework master so a prompt is never lost; the master
therefore mixes these backstop lines with the agent's full entries. Both logs are fail-safe and unbounded in
v0.

## Tools

Stdlib only; run as modules (`uv run -m solaris.tools.<name>`):

- `version` - framework + ai-pack semver, migration chain, plugin versions.
- `revs` - per-file revisions + rev-excluded content hashes: `bump`, `hash`, `status`, `ledger`,
  `classify --dir`, `ff --dir`, `baseline --dir`.
- `mcp_sync` - detect/sync drift between `.mcp.json` and `.cursor/mcp.json`.
- `log_interaction` - the fail-safe prompt-submit hook (not called by hand).
- `read_first` - the fail-safe read-first loader hook (not called by hand): with no args it injects the
  AGENTS.md read-first set at session start (Claude `SessionStart` / Cursor `sessionStart`); `--remind`
  prints a one-line per-turn nudge (Claude `UserPromptSubmit` only). IDE-aware output (Cursor JSON vs Claude
  plain stdout).
- `toc` - generate/verify Markdown tables of contents (`--check`/`--write`, `--all`).

All have unit tests under `solaris/tests/` (`uv run pytest`).

## Conventions

- **File formats:** human-facing docs are Markdown (`.md`, user-editable). Machine state is JSON
  (`manifest.json`, `remote.json`, `mcps.json`, `revisions.json`) carrying `"_comment": "do not edit"`.
  Append-only logs are JSON Lines (`.jsonl`). No standalone YAML data files (markdown frontmatter exempt).
- **Markdown TOC:** every `.md` with two or more level-2+ headers carries a TOC (the H1 is marked
  `<!-- omit in toc -->`), maintained by `solaris.tools.toc`.
- **Machine-local tooling notes:** environment-specific tooling workarounds (and their registries) live in
  the instructions layer - the framework's `memory/instructions.md` and each project's
  `ai/engineer.instructions.md` (seeded from the template) - never in the agent files. A project may edit
  or delete its copy freely.
- **Revisions:** **every change to a revisioned file increments its rev.** After editing a tracked
  framework/plugin file (or any file carrying a rev marker), `revs bump` it and `revs ledger`; a pure rev
  bump leaves the content hash unchanged, and `revs status` flags a file changed without a bump.
- **Self-sufficient spec:** a project's `ai/spec.md` is that project's single source of truth and reads
  standalone - it references no other file (no links into `ai/memory/`, plugins, or external docs).
  Background or the initial draft may live in `ai/memory/`, but the spec never points at them.
- **Naming:** kebab-case. Skills `*.skill.md`, rules `*.rule.md`.
- **Commits** (`rules/commits.rule.md`, embedded in each `engineer.agent.md`): one ASCII sentence,
  imperative, no `--`, no emoji, no AI-authorship attribution, atomic; confirm via numbered list unless the
  user grants autonomy or uses `commit!`. The `.githooks/commit-msg` hook enforces the mechanical cases.
- **Safety** (`rules/safety.rule.md`, embedded too): confirm before destructive, remote-mutating, or
  outward actions; show the command/diff first; never print or commit secrets.

## Validation (acceptance)

1. `uv run pytest` green (tools + revs + toc).
2. `version current` -> `0.9.0`; `revs status` consistent; `revs classify`/`ff` behave on a project;
   `mcp_sync --check` and `toc --check --all` clean.
3. **Todo app** (web-service, local): `create-project todo` (AGENTS.md-only root, runtime MCP) ->
   `develop-project` builds a FastAPI + vanilla UI -> runs locally; app tests pass.
4. **Migration** `0.1.0 -> 0.2.0` authored and idempotent.

## Deferred

A second `documenter` persona; splitting `engineer.agent.md`; a base `nvidia` plugin; a hosts registry and
`run-remote`/`research`/`capture`/`provision` command-center skills; the `ios-app` build/run workflow;
extending the revision/merge system beyond the materialized set; true automatic 3-way text merge (today the
tool classifies and the agent merges).
