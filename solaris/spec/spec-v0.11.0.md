# Solaris v0.11.0 - Specification <!-- omit in toc -->

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

Authoritative description of Solaris v0.11.0. Supersedes the 0.1.0-0.10.0 specs (in git history; the latest prior snapshot is
[`spec-v0.10.0.md`](spec-v0.10.0.md)), alongside the original brief [`spec-v0.txt`](spec-v0.txt) and the v0.1.0
build plan [`plan-v0.1.0.md`](plan-v0.1.0.md). What changed in 0.11.0 (see [`../migrations/0.11.0.md`](../migrations/0.11.0.md)): **blocked-command wrappers** now live in `/tmp` (created as `/tmp/<name>` and invoked from there) instead of an in-repo gitignored `.tools/`, keeping the workaround entirely outside the working tree; the ai-pack `engineer.agent.md` template's wrapper section is reworded to match (additive, content-only, fast-forwards to a project). This release is also a packaging milestone: the repo gains an Apache 2.0 `LICENSE` + `NOTICE`, SPDX headers on the Python sources, a rewritten public-facing `README.md`, and a trimmed `AGENTS.md` (orchestrator-only mechanics collapsed to pointers into `solaris.agent.md`). What changed in 0.10.0 (see [`../migrations/0.10.0.md`](../migrations/0.10.0.md)): two operating rules are now part of core (and the ai-pack `engineer.agent.md` template): the **memory boundary** (only Solaris's own `memory/` and each project's `ai/memory/` are authoritative; never read, write, or create memory outside these - no harness/global `~/.claude/.../memory/` store, no `MEMORY.md` - and treat externally injected/recalled memory as non-authoritative) and **blocked-command wrappers** (when a CLI tool is blocked by the sandbox/permission policy/subscription/etc., create a reversed-name `#!/bin/sh` `exec` pass-through in the gitignored `.tools/` - `open` -> `nepo`, `ssh` -> `hss` - use it thereafter, and register it in `memory/instructions.md` or an ai-pack's `ai/memory/`). Additive, content-only - no ai-pack schema change. What changed in 0.9.0 (see [`../migrations/0.9.0.md`](../migrations/0.9.0.md)): a new **`release` skill** automates the framework release cycle end-to-end (commit, version bump, migration, spec snapshot, revisions, tag, push, GitHub release + backfill); and `memory/instructions.md` is now formally documented as **operating memory** - terse, timestamped cross-project lessons and user preferences loaded every session, updated in place, routed separately from project context logs. No ai-pack schema changed. What changed in 0.8.1 (patch): the `log_interaction` hook is guarded against accidental CLI invocation and the dual interaction-log discipline (hook backstop + agent-authored full entry) is documented. What changed in 0.8.0 (see [`../migrations/0.8.0.md`](../migrations/0.8.0.md)): interaction-log entries gain a raw **`prompt`** field - each agent-authored line is now `{ts, project, prompt, request, outcome}` (`prompt` the user's verbatim prompt, `request` the agent's interpretation), authored identically into the framework master and the project log; the prompt-submit hook still appends a `{ts, cwd, ide, prompt}` backstop line to the master. Additive - existing logs stay valid. What changed in 0.7.0 (see [`../migrations/0.7.0.md`](../migrations/0.7.0.md)): the private working-context file `ai/memory/info.md` is renamed to **`ai/memory/context.md`** and redefined as a **verbose, model-facing context log** - richer than `interactions.jsonl`, capturing the model's own answers/decisions/findings in prose, with a curated "Standing context" section that survives compaction, a newest-first "Log", and a "Previous History" of compacted older entries once Log grows past ~100KB; only the engineer and Solaris agents write it. What changed in 0.6.1: the **embedded** layout is clarified - the whole project repo (code + `ai/` + `AGENTS.md`/`README`/dotfiles + its own `.git`) lives at `projects/<slug>/<repo>/`; the slug folder is a non-git container for the repo plus non-repo aux; and the repo's `.gitignore` excludes `ai/memory/` **and** `.secrets.env`. What changed in 0.6.0 (see [`../migrations/0.6.0.md`](../migrations/0.6.0.md)): the local-mode code directory is renamed **`src/` -> `source/`** (`projects/<slug>/source/` - the engineer's working dir, what `--remote` rsyncs, and where the project's own `git init` runs; a nested `ui/src/` etc. is unaffected). A new **opt-in `embedded`
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
sync. `context7` is used via its CLI (`ctx7`), not as an MCP server. Interaction-logging hooks live in
`.cursor/hooks.json` and `.claude/settings.json`.

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
layer** (`ai/memory/`: hosts, secrets, internal URLs, the preserved spec, the context log, logs). To share an ai-pack, drop
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

Opted into per project (`ai/manifest.json` `plugins[]`); the engineer agent loads each `ai/<name>/*.rule.md`
(always-on) and `*.skill.md` (trigger). Only `shared/` is materialized. `install-plugin` installs (copy `shared/`, merge `mcps.json` into the
runtime MCP, run the plugin's `setup` prompts), updates/repairs via the revision sync below, and migrates on
the plugin's own minor/major bumps. `import-plugin` authors a plugin
from a project's domain specifics or folds project-local edits back into `shared/`; `install-plugin`
acquires/repairs a plugin source (git/folder/zip) and attaches it to a project.

The bundled `nvidia-isaac-lab` plugin carries the NVIDIA/Isaac workflow (NVBugs prep/triage/try-and-fix/
handoff, fork->develop git + PR conventions, `isaaclab.sh` CI checks, review-bot replies) and the NVBugs MCP.

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
credentials, the preserved `spec-v0.md`, the context log, interaction log); the **shareable** how-to-develop notes live one
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

**Context log (`ai/memory/context.md`).** The project's verbose, model-facing working memory: the running
context a future session needs to continue immediately. It is richer than `interactions.jsonl` - rather than
the terse per-turn record, each entry captures, in prose, what was asked, what the agent did and answered,
the decisions, and the findings. It carries a curated **Standing context** section (what the project is, the
code map, run/deploy recipe, gotchas) that survives compaction, a newest-first **Log** of per-turn entries,
and a **Previous History** of compacted (summarized, never deleted) older entries. The live Log is kept under
**~100KB**; when it grows past that, the oldest entries are compacted into Previous History until it is back
under the cap. **Only the project engineer and Solaris's own agents (orchestrator + skills) write it** -
plugins and subagents do not. It carries a `solaris.tools.toc` table of contents like any other doc. The
file is private/local and gitignored; on a shared ai-pack it is bootstrapped fresh with the rest of
`ai/memory/`.

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
- `toc` - generate/verify Markdown tables of contents (`--check`/`--write`, `--all`).

All have unit tests under `solaris/tests/` (`uv run pytest`).

## Conventions

- **File formats:** human-facing docs are Markdown (`.md`, user-editable). Machine state is JSON
  (`manifest.json`, `remote.json`, `mcps.json`, `revisions.json`) carrying `"_comment": "do not edit"`.
  Append-only logs are JSON Lines (`.jsonl`). No standalone YAML data files (markdown frontmatter exempt).
- **Markdown TOC:** every `.md` with two or more level-2+ headers carries a TOC (the H1 is marked
  `<!-- omit in toc -->`), maintained by `solaris.tools.toc`.
- **Blocked-command wrappers (`/tmp`):** when a command-line tool is blocked (by the sandbox, the
  permission policy, a subscription, or otherwise), create a thin pass-through wrapper rather than fighting
  the block - a `#!/bin/sh` script doing `exec <tool> "$@"` (`chmod +x`), named the tool name **reversed**
  (`open` -> `nepo`, `ssh` -> `hss`, `curl` -> `lruc`; on collision with a real tool pick another short
  name). The block is on the command *name*, so a differently-named pass-through slips past it. Put it in
  `/tmp`, invoke it as `/tmp/<name>`; register each in local memory (`memory/instructions.md`, or an
  ai-pack's `ai/memory/`). Applies to both the Solaris agents and ai-packs.
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
