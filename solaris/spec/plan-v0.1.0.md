# Solaris v0 — Implementation Plan <!-- omit in toc -->

- [1. Goal](#1-goal)
- [2. Decisions locked (from Q\&A)](#2-decisions-locked-from-qa)
- [3. Directory layout](#3-directory-layout)
  - [3.1 Framework repo (`<root>/`, this is the Solaris `.git` root)](#31-framework-repo-root-this-is-the-solaris-git-root)
  - [3.2 Generated project (`projects/<p>/`, e.g. `todo`)](#32-generated-project-projectsp-eg-todo)
  - [3.3 Project modes of operation](#33-project-modes-of-operation)
  - [3.4 Tasks area (ad-hoc command center)](#34-tasks-area-ad-hoc-command-center)
- [4. Components](#4-components)
  - [4.1 Dual-IDE wiring (root + every project)](#41-dual-ide-wiring-root--every-project)
  - [4.2 `mcp_sync.py` tool](#42-mcp_syncpy-tool)
  - [4.3 Framework agent — `solaris.agent.md`](#43-framework-agent--solarisagentmd)
  - [4.4 Skills (`solaris/skills/*.skill.md`)](#44-skills-solarisskillsskillmd)
    - [4.4.1 `import-project` (adopt an existing codebase)](#441-import-project-adopt-an-existing-codebase)
    - [4.4.2 `import-plugin` (author or update a plugin)](#442-import-plugin-author-or-update-a-plugin)
  - [4.5 ai-setup template (`solaris/templates/ai-setup/`)](#45-ai-setup-template-solaristemplatesai-setup)
  - [4.6 Migration engine](#46-migration-engine)
  - [4.7 `version.py` tool](#47-versionpy-tool)
  - [4.8 Memory, interaction logging, commit policy](#48-memory-interaction-logging-commit-policy)
  - [4.9 Plugin system](#49-plugin-system)
    - [4.9.1 The `nvidia-isaac-lab` plugin](#491-the-nvidia-isaac-lab-plugin)
- [5. Project-type templates (`solaris/templates/projects/`)](#5-project-type-templates-solaristemplatesprojects)
- [6. Validation (v0 acceptance)](#6-validation-v0-acceptance)
- [7. Build order (milestones)](#7-build-order-milestones)
- [8. Conventions](#8-conventions)
- [9. Open questions (to confirm next iteration)](#9-open-questions-to-confirm-next-iteration)

Status: draft, iterating. Source spec: [spec-v0.txt](spec-v0.txt). References (gitignored): [references/](references/).

## 1. Goal

Build **Solaris v0**: a minimal framework to run many coding projects from one central point. Solaris generates a standardized, self-contained **ai-setup** per project. Cursor + Claude Code supported on day one. It's also a **command center** for ad-hoc engineering, system-setup, and research work that isn't a project, via a lightweight `tasks/` area (3.4).

Projects run in one of two **modes**: *local* (code lives locally in `src/`, source of truth, deployed to a remote to run/test/debug) or *remote-code* (code lives on a remote; a `remote.json` pointer replaces `src/`; not deployed by default). Domain/employer-specific behavior is factored into **plugins** (gitignored overlays), opted into per project and materialized into the project's `ai/`.

Validate v0 with: a **todo app** (FastAPI + vanilla JS/HTML, local mode), a **python-cli** (standalone trivial CLI, uv-based), and **Isaac Lab** (remote-code mode) using a `nvidia-isaac-lab` plugin that carries the NVIDIA/Isaac-specific parts of the current `__ai` setup.

Keep it lean. Port only what's load-bearing from Co-SA / Superagent.

## 2. Decisions locked (from Q&A)

| # | Decision |
|---|---|
| Remote deploy | SSH + rsync; optional Docker when a project ships a Dockerfile. |
| Todo stack | FastAPI JSON API + single-page vanilla HTML/JS UI. |
| python-cli toy | Standalone trivial CLI, uv-based (own `pyproject.toml`); modeled on Co-SA's `bootstrap-ext-project`. |
| Project layout | `ai/` sibling to `src/`; IDE wiring at project root; `src/` is the code `.git` root; IDE opens `projects/<p>/`. |
| Project modes | **local** (code in `src/`, deploy to remote) and **remote-code** (no `src/`; `remote.json` points at code on a remote; edited/run in place via IDE Remote-SSH; not deployed by default). |
| File formats | Human-facing docs = Markdown (`.md`, user-editable, format-tolerant). Machine state = JSON (`manifest.json`, `remote.json`, `mcps.json`) with a top `"_comment": "do not edit"`. Append-only logs = JSON Lines (`.jsonl`). **No YAML.** |
| Versions | Live in `manifest.json` files (framework + each plugin); no `.version` files. |
| Plugins | Per-project opt-in (listed in `ai/manifest.json`). A plugin's `shared/` is **copied** into `ai/<plugin>/` (the only files materialized); `install.skill.md`, `mcps.json`, `manifest.json`, `migrations/` stay source-side. `install.skill.md` handles install/update/migrate/repair; updates overwrite `ai/<plugin>/` wholesale, but Solaris first checks for local edits and offers `import-plugin` to capture them. Resources only in the ai-setup. Gitignored. |
| Project types | `create-project` offers core types (`templates/projects/*.md`) **plus** any provided by plugins (`plugins/<name>/<type>.project.md`); picking a plugin type auto-attaches that plugin. |
| Plugin authoring | `import-plugin` skill: update a plugin from a project's local edits, or create a new plugin from an existing ai-setup. Runs during `import-project` too. |
| Documenter persona | Deferred; single `developer.agent.md` in v0. |
| Self-reflect | Lightweight, propose-only: edits **core framework files directly** (no learned-instructions file at framework level). |
| Per-project agent | **One** combined `developer.agent.md` = coder + planner + runner. Splittable later. |
| Per-project memory | Editable `developer.instructions.md` (per project), plus `resources.md` / `credentials.md` (copied in on init/update; ai-setups never reference Solaris's `memory/`). |
| create-project handoff | After scaffold, **stop**; user starts planning via `develop-project`. |
| Command center | Lightweight `tasks/` area (gitignored, dated folders) for ad-hoc engineering/system-setup/research; framework skills `task` and `doctor` (default output is the status summary; `--deep` for full health). Deferred (considered, not v0): hosts registry, `run-remote`, `research`, `capture`/`recall`, `provision`. |
| Safety | `solaris/rules/safety.rule.md` (baked into `developer.agent.md` too): confirm before destructive, remote-mutating, or outward actions — `ssh` writes, `rsync --delete`, deploy, `git push`. Show the command/diff first. |
| Credentials | Raw secrets in gitignored `credentials.md` (framework + per-project); simplest, matches Co-SA/Superagent. The gitignore entry is the only protection. |
| Robustness | `solaris/tests/` (version/mcp_sync/manifest); interaction hook is fail-safe (timeout, swallows errors, never blocks a turn); `rsync` deploy excludes `.venv`/`.git`/secrets/artifacts and uses no `--delete` by default. |
| `ai/` in git | Left outside git by default (user may VC separately). |
| Skills | Listed inline in `AGENTS.md` (no skills manifest). Framework skills/rules use `*.skill.md` / `*.rule.md` (same as plugins). |
| Setup | No setup skill. `AGENTS.md`/`CLAUDE.md`/`plugins/` ship committed (obtained on clone). `.venv`/`.tmp`/`.tools` created lazily. README documents `mcp.json.example`. |
| MCP servers | `playwright` (npx stdio) is the only default MCP. `context7` is used via its **CLI** (`ctx7`), not MCP — the agent suggests installing it if absent. NVBugs MCP ships in the `nvidia-isaac-lab` plugin only. |
| MCP config | Single committed `mcp.json.example`; user copies to `.mcp.json` (Claude) and `.cursor/mcp.json` (Cursor). `mcp_sync.py` detects divergence and offers to sync. |
| Migration engine | In v0; ported from Co-SA, simplified; migrates project ai-setups; `update-project` is the entry point. No migration registry file — `version.py` scans `migrations/*.md` directly. |
| Python | `requires-python = ">=3.14"`. `solaris/` is a package; tools run as modules: `uv run -m solaris.tools.<name>`. |

## 3. Directory layout

### 3.1 Framework repo (`<root>/`, this is the Solaris `.git` root)

```
<root>/
  AGENTS.md                 # minimal pointer to framework files (committed)
  CLAUDE.md                 # one line: @AGENTS.md (committed)
  mcp.json.example          # committed MCP template (playwright only); user copies + edits
  README.md                 # clone -> setup steps (copy mcp.json.example, etc.)
  pyproject.toml  uv.lock    # python >=3.14; runtime stdlib only (pytest for tests); packages = solaris (+ solaris.tools)
  .gitignore
  .claude/settings.json      # committed; interaction-log hook
  .cursor/
    rules/solaris.mdc   # committed Cursor shim -> AGENTS.md
    hooks.json               # committed; same interaction-log hook
  .githooks/commit-msg       # committed; enforces commit policy (opt-in install)
  solaris/                   # python package (has __init__.py) so tools run as `uv run -m solaris.tools.<name>`
    __init__.py
    solaris.agent.md         # framework agent role (orchestrator)
    spec/                    # spec-v0.txt, plan.md, references/ (references gitignored)
    skills/                  # create-project, import-project, import-plugin, update-project, develop-project, self-reflect, task, doctor (*.skill.md)
    rules/commits.rule.md    # commit policy (framework + seed for ai-setup)
    rules/safety.rule.md     # confirm before destructive/remote/outward actions (seed for ai-setup)
    migrations/              # template.md, README.md, <version>.md (+ optional <version>/ scripts) - no registry file
    templates/
      ai-setup/              # copied into projects/<p>/ by create-project
      projects/              # web-service.md, python-cli.md, ios-app.md (core type descriptions)
    tools/                   # __init__.py + version.py, mcp_sync.py, log_interaction.py
    tests/                   # pytest: version, mcp_sync, manifest, migration-chain
  plugins/                   # plugin overlays (gitignored except the placeholder)
    .empty                   # committed placeholder
    nvidia-isaac-lab/        # example plugin: manifest.json, mcps.json, install.skill.md, shared/, migrations/
  memory/
    interactions.jsonl       # framework-level interaction log (machine-written)
    resources.md             # framework-level shared resources (hosts, hardware) - user-editable
    credentials.md           # framework-level secrets - user-editable, gitignored
  projects/                  # gitignored (user projects)
  tasks/                     # gitignored; dated ad-hoc task folders (see 3.4)
  # lazily created, gitignored: .venv/ .tmp/ .tools/  .mcp.json  .cursor/mcp.json
```

ai-setups never read Solaris's `memory/` — `create-project` / plugin `install.skill.md` copy whatever a project needs into that project's own `ai/memory/`.

### 3.2 Generated project (`projects/<p>/`, e.g. `todo`)

```
projects/todo/
  AGENTS.md                 # points to ai/developer.agent.md + ai/spec.md; loads ai/<plugin>/ overlays
  CLAUDE.md                 # @AGENTS.md
  mcp.json.example
  .cursor/rules/todo.mdc  # Cursor shim
  .gitignore                # ignores credentials.md, runtime mcp files, venvs
  ai/                       # the ai-setup (not in any git by default)
    developer.agent.md      # combined coder + planner + runner
    manifest.json           # ai-setup descriptor: project name/type/mode + framework_version + plugins(+versions) + info bits  ("_comment": do not edit)
    spec.md                 # current spec (updated each iteration)
    memory/
      spec-v0.md            # initial spec, preserved
      developer.instructions.md  # editable learned how-to-develop-this-project
      resources.md          # remote hosts, run/deploy procedure, hardware, APIs (copied in as needed)
      credentials.md        # secrets (gitignored)
      interactions.jsonl    # query / interaction / outcome log (machine-written)
    # no plugin folders: todo uses none. Attached plugins land at ai/<plugin>/
  src/                      # .git root; application code ONLY (local mode)
    # todo: pyproject.toml (fastapi, uvicorn), app/, static/, tests/
```

**Remote-code mode** swaps `src/` for a pointer (see 3.3):

```
projects/isaac-lab/
  AGENTS.md  CLAUDE.md  .cursor/rules/isaac-lab.mdc  mcp.json.example  .gitignore
  remote.json               # host + path of the code on the remote; deploy:false (user-editable)
  ai/
    developer.agent.md
    manifest.json           # mode: remote-code; framework_version; plugins: [{nvidia-isaac-lab: <ver>}]; info bits
    spec.md
    memory/
      spec-v0.md  developer.instructions.md  resources.md  credentials.md  interactions.jsonl
    nvidia-isaac-lab/       # materialized plugin = COPY of the source plugin's shared/ only
      nvbugs.skill.md
      nvbugs.rule.md  git-workflow.rule.md  ci-checks.rule.md  bot-replies.rule.md
  # install.skill.md, mcps.json, manifest.json (version), migrations/ stay in plugins/nvidia-isaac-lab/ (not copied)
  # no src/ ; code at mishasdell2-tailscale:~/IsaacLab (keeps its own committed AGENTS.md)
```

`.git` boundaries: Solaris repo at `<root>/` (ignores `projects/`, `references/`, `plugins/*` contents). In local mode each project's `src/` is its own repo; in remote-code mode the code repo lives on the remote. `ai/` is outside git by default.

### 3.3 Project modes of operation

Each project picks a mode at create/import time; recorded in `ai/manifest.json`.

| Mode | Code location | Pointer | Deploy/run |
|---|---|---|---|
| `local` (default) | `projects/<p>/src/` (own `.git`) | — | run locally; rsync `src/` → remote over SSH; run/test/debug there; optional Docker. |
| `remote-code` | on a remote host (e.g. `mishasdell2-tailscale:~/IsaacLab`) | `remote.json` (replaces `src/`) | code already lives remote → **no deploy by default**; edited and run in place via **IDE Remote-SSH** over the Tailscale host. Local sync/deploy is opt-in. |

`remote.json` records: `host` (SSH alias), `path` (remote repo root), optional `sync` excludes, `deploy: false` by default. Richer run/test/deploy procedures live in `ai/memory/resources.md`; `remote.json` is just the code-location + connection pointer. The developer agent opens the project locally for the `ai/` overlay and drives the remote checkout through the IDE's Remote-SSH session.

### 3.4 Tasks area (ad-hoc command center)

Ad-hoc engineering, system-setup, and research work that isn't a project lives under `tasks/` (gitignored) — no ai-setup, no versioning.

```
tasks/
  <YYYY-MM-DD>-<slug>/
    notes.md        # what/why, steps tried, findings, outcome (user-editable)
    ...             # scratch scripts, outputs, logs
```

The `task` skill starts/resumes a dated folder and logs the run to `memory/interactions.jsonl`; `doctor` gives the command-center overview (default) plus health checks (`--deep`) (4.4). A `tasks/` item that recurs and turns durable can graduate into a project (`create-project`/`import-project`) or a plugin (`import-plugin`). Deferred for later (considered, not v0): a hosts registry, `run-remote`, `research`, `capture`/`recall`, `provision` recipes.

## 4. Components

**Execution model.** One running agent (Cursor or Claude Code) adopts a *persona* by reading the active context: at the Solaris root it's the orchestrator (`solaris/solaris.agent.md`); inside a project it's that project's `ai/developer.agent.md` plus the ai-setup (`ai/spec.md`, `ai/memory/*`) and any `ai/<plugin>/` overlays. "Hand off" means switching which instruction set + working directory is active, not spawning a separate process.

### 4.1 Dual-IDE wiring (root + every project)

Pattern from Co-SA/Superagent: `AGENTS.md` is the single canonical instruction file; per-IDE shims delegate to it.

- **`AGENTS.md`** — **minimal** and canonical: short pointers only — to `solaris/solaris.agent.md` (role), `solaris/rules/*.rule.md` (commit policy etc.), the inline skill catalog (name + trigger + one-liner), the memory read order, and the plugin-overlay scan. No duplicated content; the pointed-to files hold the detail.
- **`CLAUDE.md`** — one line, `@AGENTS.md` (plus a one-line header). Nothing else.
- **`.cursor/rules/<name>.mdc`** — frontmatter `alwaysApply: true` + body "read AGENTS.md every turn". No body duplication.
- **MCP**: ship one committed `mcp.json.example` (identical content works for both IDEs). User copies to `.mcp.json` and `.cursor/mcp.json` (both gitignored). Default server (npx stdio):
  ```json
  { "mcpServers": {
      "playwright": { "command": "npx", "args": ["@playwright/mcp@latest"] }
  } }
  ```
  `context7` is **not** an MCP server here — the agent shells out to the `ctx7` CLI for library docs and suggests installing it (e.g. `npm i -g @upstash/context7`) if missing.
- Project shims are identical in shape, scoped to that project (open at `projects/<p>/`).

### 4.2 `mcp_sync.py` tool

`uv run -m solaris.tools.mcp_sync [--dir PATH] [--check|--sync]`.
- `--check`: compare `.mcp.json` vs `.cursor/mcp.json` (and vs `mcp.json.example`); report divergence (exit nonzero on drift).
- `--sync`: write the chosen source to both runtime files.
- `--dir`: operate on any project root, not just `<root>/`.
- Used by the agent (suggest sync on detected drift) and runnable manually.

### 4.3 Framework agent — `solaris.agent.md`

Orchestrator role. Responsibilities: route user requests to the skills; know the project registry under `projects/`; manage **plugins** (list what's available under `plugins/`, attach/materialize on request, author/update via `import-plugin`, re-sync on update — see 4.9); append to `memory/interactions.jsonl`; hand project work to the project's `developer.agent.md` via `develop-project`. Self-improvement is direct: the user (via `self-reflect`) edits core framework files — there is no framework-level learned-instructions file. Plugin *consumption* is per-project (the developer agent loads `ai/<plugin>/` overlays), never global. Beyond projects, the framework agent also runs ad-hoc `tasks/` work and the `doctor` overview/health (3.4).

### 4.4 Skills (`solaris/skills/*.skill.md`)

Plain markdown, trigger-word invoked from `AGENTS.md`. **No** `.claude/skills/**/SKILL.md`, **no** slash commands.

| Skill | Job |
|---|---|
| `create-project` | Ask name/type/**mode**/description + any **plugins** to attach. Create `projects/<slug>/` with `ai/` (from `templates/ai-setup/`) + project-level IDE wiring. Pick a project type — core (`templates/projects/*.md`) or plugin-provided (`plugins/<name>/<type>.project.md`); choosing a plugin type auto-attaches that plugin. Local mode: init `src/` git; remote-code mode: write `remote.json` instead of `src/`. For each chosen plugin run its `install.skill.md` (install). Write `ai/memory/spec-v0.md` + `ai/spec.md` from a short spec dialogue. Write `ai/manifest.json`. **Stop** — the user starts planning via `develop-project`. |
| `import-project` | Adopt an existing codebase. See 4.4.1. |
| `import-plugin` | Author or update a plugin. See 4.4.2. |
| `update-project` | Migration entry point. Read versions from `ai/manifest.json` (framework + each plugin), compare via `version.py`, show plan; apply framework migrations to `ai/`, then invoke each attached plugin's `install.skill.md` (update/migrate/repair) to refresh `ai/<plugin>/` and run plugin migrations; write new versions back to `ai/manifest.json`. Also refresh project-root wiring (`AGENTS.md`, `CLAUDE.md`, `.cursor/rules/*.mdc`, `mcp.json.example`) from current templates, showing diffs and never clobbering user edits silently. Revert supported. |
| `develop-project` | Thin shim: hand off a user prompt to `projects/<p>/ai/developer.agent.md` (plan or implement). No logic beyond locating the project and loading its ai-setup. |
| `self-reflect` | Lightweight, propose-only. Read `memory/interactions.jsonl` (+ project logs), surface friction + ranked improvement suggestions; after approval, edit core framework files directly. No tailor/coder split. |
| `task` | Start/resume a dated ad-hoc task under `tasks/<YYYY-MM-DD>-<slug>/` (engineering/system-setup/research). Seed `notes.md`, capture scratch + outputs, log the run to `memory/interactions.jsonl`. No ai-setup. |
| `doctor` | Command-center overview + health. Default (no args): a status summary — projects (mode/version), recent `tasks/`, drift (mcp config sync via `mcp_sync.py`, framework/plugin version drift via `version.py`). `--deep`: full checks — `.venv` present, `mcp.json` ↔ `.cursor/mcp.json` in sync, per-project version/plugin drift, gitignore + wiring sanity. Read-only; suggests fixes, applies none silently. |

#### 4.4.1 `import-project` (adopt an existing codebase)

Reverse of `create-project`: ingest existing code, then derive the ai-setup. Steps:

1. **Inputs**: source (local path *or* `host:path` for remote-code) + target slug + **mode**. Ask if missing.
2. **Land the code**:
   - *local mode*: if source already is `projects/<slug>/src/`, adopt in place; else copy/rsync → `projects/<slug>/src/`, excluding venvs/caches/build artifacts/node_modules. Preserve an existing `.git` only with the user's OK. Never move/delete the original.
   - *remote-code mode*: do **not** copy; write `remote.json` (host, path, `deploy: false`). Read the remote code over SSH for detection.
3. **Detect type + toolchain** (read-only): match a `templates/projects/<type>.md` from signals (`pyproject.toml`/`setup.py` → python; `package.json` → node; `*.xcodeproj` → ios; FastAPI imports → web-service; CLI entrypoints → python-cli). Read README, dep manifests, entrypoints, test/build config, Dockerfile/compose, and any existing repo `AGENTS.md`/`CLAUDE.md` (treated as project rules, left in place).
4. **Detect + attach plugins** (runs `import-plugin` as needed): scan for domain markers and propose plugins (e.g. NVBugs / `isaaclab.sh` / `__nvbugs/` / NVBugs MCP → suggest `nvidia-isaac-lab`). For a marker set that matches no existing plugin but clearly carries domain specifics (e.g. an existing `__ai/`), offer `import-plugin` (create mode) to factor it into a new plugin. On confirm, run the plugin's `install.skill.md` (install): copy `shared/` → `ai/<plugin>/`, merge `mcps.json`, prompt for resources, record plugin + version in `ai/manifest.json`. Domain-specific knowledge maps to the plugin, **not** into the generic `developer.instructions.md`.
5. **Derive the ai-setup to best ability**:
   - `ai/spec.md` + `ai/memory/spec-v0.md` — reconstructed spec from code + README.
   - `ai/memory/developer.instructions.md` — inferred **generic** build/run/test/lint commands + conventions (the plugin carries domain-specific ones).
   - `ai/memory/resources.md` — deploy/host hints (Dockerfile, CI, `.env.example`, remote host); else stubs. `ai/memory/credentials.md` — placeholders only.
   - `ai/memory/interactions.jsonl` seeded; `ai/manifest.json` written; project IDE wiring written.
6. **Ask** wherever inference is ambiguous: type, mode, entrypoint, run/test commands, in/out-of-scope dirs, remote host, plugins to attach, whether to keep existing git. Batch the questions; never guess load-bearing details.
7. **Confirm + summary**: detected vs assumed vs needs-your-eyes; point at `ai/developer.agent.md`. Import never modifies the code — it only creates `ai/` + wiring (+ `remote.json` in remote-code mode).

#### 4.4.2 `import-plugin` (author or update a plugin)

Two modes; both keep the source plugin at `plugins/<name>/` as the editing source of truth.

- **update-from-project**: the user edited the materialized copy `ai/<plugin>/`. Diff it against the source `plugins/<name>/shared/`, show the changes, and on confirm fold them back into `shared/` and bump the plugin `version` in `plugins/<name>/manifest.json`. This is the mechanism behind the **pre-overwrite check**: before `install.skill.md` (update) overwrites `ai/<plugin>/` wholesale, Solaris detects local modifications and offers `import-plugin` (update-from-project) first so edits are not lost.
- **create-from-aisetup**: build a **new** `plugins/<name>/` from domain-specific content in an existing project's ai-setup (or an external `__ai`-style setup). Heuristically separate domain-specific skills/rules/MCP from generic ones, ask the user to confirm the split, then write `manifest.json`, `mcps.json`, `install.skill.md`, and `shared/*.skill.md` / `*.rule.md`. Invoked standalone or by `import-project` step 4.

### 4.5 ai-setup template (`solaris/templates/ai-setup/`)

Copied into `projects/<p>/` on create. Contains:
- `developer.agent.md` — combined **coder + planner + runner**. Sections: identity/role; planning workflow (update `spec.md` in dialogue, preserve `spec-v0.md`); coding workflow (implement against spec); **run/deploy workflow** (local run; rsync `src/` to remote over SSH — excludes `.venv`/`.git`/secrets/build artifacts, no `--delete` by default so remote outputs are not clobbered, pulling artifacts back is explicit; ssh exec to run/test/debug; optional `docker build/run`; in remote-code mode operate in place via Remote-SSH against `remote.json`, no deploy unless opted in; reads host + app procedure from `memory/resources.md`); commit + safety policies (baked in, see 4.8); when to update `memory/developer.instructions.md`; append to `memory/interactions.jsonl`.
- `spec.md`, `memory/spec-v0.md` — seeded from create-project dialogue.
- `memory/developer.instructions.md` — editable learned how-to-develop notes.
- `memory/resources.md` — remote host(s), ssh user/path, run command, app deploy steps, hardware/APIs (populated on init/update; copied from Solaris `memory/resources.md` as needed).
- `memory/credentials.md` — secrets (gitignored at project level).
- `memory/interactions.jsonl` — seeded empty (machine-written).
- `manifest.json` — ai-setup descriptor (supersedes the old plugins-list + `.version`): `project` (`name`/`type`/`mode`), `framework_version`, `plugins` (each with materialized version), and other info bits. Carries `"_comment": "do not edit"`. No plugin folders until one is attached, then each lands flat at `ai/<plugin>/` (a copy of that plugin's `shared/`).
- Minimal project-level `AGENTS.md`, `CLAUDE.md`, `.cursor/rules/<slug>.mdc`, `mcp.json.example`, `.gitignore` (with `<placeholder>` substitution). The project `AGENTS.md` is a thin pointer that tells the agent to load the ai-setup **plus** any `ai/<plugin>/` overlays additively, and to read `src/AGENTS.md` (if present) as project rules.

The shipped `developer.agent.md` + `developer.instructions.md` **seed the user's generic working preferences** distilled from the current `__ai`: commit style (one sentence, ASCII, no `--`/emoji/AI-attribution, atomic, incremental, confirm via numbered list), generic PR flow (ask before submitting, draft first), response style (tables for options, terse, explicit recommendations, bare command first), code taste (`set -euo pipefail`, ASCII-only comments that anticipate the next maintainer's question). Everything NVIDIA/Isaac-specific lives in the plugin (4.9.1).

### 4.6 Migration engine

Ported from Co-SA versioning contract, simplified to operate on **project ai-setups**.
- Framework version = `pyproject.toml [project].version` (semver).
- The ai-setup records the framework version it was written/updated at in `ai/manifest.json` (`framework_version`) — no `.version` files anywhere.
- `solaris/migrations/`: `template.md` (authoring template), `README.md`, `<to_version>.md` files (frontmatter: `to_version`, `from_version`, `title`, `breaking`, `revertible`, `estimated_duration`, `touches`; body: Summary / Pre-flight / Migrate / Validate / Revert), optional `<to_version>/migrate.py|revert.py|validate.py`. **No registry file** — `version.py` builds the chain by scanning `*.md` frontmatter directly.
- Framework migrations modify the project `ai/` only (excluding `ai/<plugin>/`, owned by plugins) — never the project's `src/` code.
- Project-root wiring (`AGENTS.md`/`CLAUDE.md`/`.cursor/rules/*.mdc`/`mcp.json.example`) is refreshed from current templates by `update-project` (diffs shown; user edits never clobbered silently) — separate from `ai/` migrations.
- **Plugins version independently**: each plugin's version lives in `plugins/<name>/manifest.json`, with its own `plugins/<name>/migrations/` (same `*.md` shape, no registry). The materialized version is recorded per-plugin in `ai/manifest.json`. Plugin lifecycle (install/update/migrate/repair) runs through the plugin's `install.skill.md`. `update-project` applies framework migrations to `ai/`, then invokes each attached plugin's `install.skill.md` to migrate and refresh `ai/<plugin>/` (wholesale re-copy of `shared/`, after the pre-overwrite edit check in 4.4.2) and bump its recorded version.
- `update-project` is the sole user-facing entry; `version.py` only inspects/computes chains.

### 4.7 `version.py` tool

`uv run -m solaris.tools.version <cmd>`: `current` (framework version from `pyproject.toml`), `aisetup --dir <project>` (read `framework_version` from `ai/manifest.json`), `check --dir <project>` (0 match / 1 migrate / 2 downgrade), `chain --dir <project>` (scan `solaris/migrations/*.md` for the ordered chain), `set --dir <project> VERSION` (write into `ai/manifest.json`). Plugin-aware: `plugin --dir <project> --plugin <name>` (read the plugin's version from `ai/manifest.json`), `check-plugins --dir <project>` (compare each attached plugin's recorded version to its source `plugins/<name>/manifest.json`). Hand-rolled semver (stdlib only).

### 4.8 Memory, interaction logging, commit policy

- **Interaction logs** (`.jsonl`, append-only, machine-written): framework `memory/interactions.jsonl`; per-project `ai/memory/interactions.jsonl`. Wired via `.claude/settings.json` (UserPromptSubmit hook) and `.cursor/hooks.json` (beforeSubmitPrompt) → `uv run -m solaris.tools.log_interaction`. The hook routes by cwd: inside a `projects/<p>/` → that project's `ai/memory/interactions.jsonl`; otherwise (root, `tasks/`) → the framework log. The hook is **fail-safe**: short timeout, swallows errors, never blocks the turn, tolerates a missing `.venv` (first run). Append-only and **unbounded in v0** (entries are small/rare; no rotation yet).
- **Framework `memory/`** holds `resources.md` + `credentials.md` (user-editable, `credentials.md` gitignored) and `interactions.jsonl`. **No `solaris.instructions.md`** — the user improves Solaris by editing core framework files directly (assisted by `self-reflect`). ai-setups never read this directory; needed resources/credentials are copied into the project's own `ai/memory/` on init/update.
- **`*.instructions.md`** (per project) are editable (rewrite to keep the best version), not append-only.
- **Commit policy** (`solaris/rules/commits.rule.md`, also baked into the ai-setup `developer.agent.md` so detached projects keep it): one-sentence brief subject; ASCII only; no `--`; no emojis; no AI-authorship attribution; atomic; same rules for code comments. Optional `.githooks/commit-msg` enforces the common cases (`git config core.hooksPath .githooks`).
- **Safety policy** (`solaris/rules/safety.rule.md`, also baked into `developer.agent.md`): confirm before destructive, remote-mutating, or outward actions — remote/`ssh` writes, `rsync --delete`, deploy, `git push`, anything that leaves the local machine. Show the exact command/diff first. A durable "work autonomously until X" instruction waives per-step confirmation for that task's duration (format/safety rules still apply).

### 4.9 Plugin system

A **plugin** adapts Solaris for a domain/employer/repo-specific way of working (e.g. "NVIDIA Isaac Lab developer"). Plugins live under `plugins/<name>/` (gitignored except the placeholder), are **opted into per project**, and are **materialized** (copied) into the project's `ai/<name>/`.

Plugin layout is **flat** — no subfolders except `migrations/`:

```
plugins/<name>/
  manifest.json         # name, version, description, applies_to (suggestion hints).  "_comment": do not edit
  mcps.json             # MCP server entries this plugin needs (merged into the project MCP config at install)
  install.skill.md      # lifecycle: install / update / migrate / repair (NOT copied into the ai-setup)
  <type>.project.md     # OPTIONAL: project-type description(s) this plugin contributes to create-project (flat; not copied)
  shared/               # the ONLY files copied into ai/<name>/ (curated, runtime-necessary)
    <topic>.skill.md    #   procedures, one file per topic (*.skill.md)
    <topic>.rule.md     #   always-on conventions + folded-in domain knowledge (*.rule.md)
  migrations/           # the only other subfolder: <to_version>.md (+ optional <to_version>/ scripts); no registry file
```

- **No `resources.md`, no `instructions/`, no `provides` list**: resources live only in the ai-setup (`ai/memory/resources.md`), populated by `install.skill.md`; what a plugin ships is just the files in `shared/` + `mcps.json` (no need to enumerate them in the manifest); domain knowledge is folded into `shared/*.rule.md` / `*.skill.md`.
- **`shared/` is the materialization boundary**: only `shared/` is copied into the project. `install.skill.md`, `manifest.json`, `mcps.json`, `migrations/` stay source-side (Solaris). The developer agent runs detached on the copied rules/skills; lifecycle management needs Solaris.
- **Project types**: a plugin may ship `<type>.project.md` files (flat, source-side, not materialized). `create-project` lists them alongside core types; selecting one auto-attaches the plugin.
- **Activation**: `ai/manifest.json` lists the plugins it uses + their versions (see 4.5).
- **Lifecycle via `install.skill.md`** — one skill, four modes: **install** (copy `shared/` → `ai/<name>/`, merge `mcps.json` into the project MCP config, prompt for resources/credentials → `ai/memory/`, record plugin + version in `ai/manifest.json`); **update** (overwrite `ai/<name>/` **wholesale** by re-copying `shared/`; first run the pre-overwrite check — if `ai/<name>/` was locally modified, offer `import-plugin` update-from-project to capture edits into source); **migrate** (run pending `migrations/` against `ai/<name>/` + the ai-setup); **repair** (restore missing/corrupted copied files, re-merge MCPs). Invoked by `create-project`/`import-project` (install) and `update-project` (update/migrate/repair).
- **Consumption** (per-project): the developer agent loads each `ai/<name>/*.rule.md` (always-on) and `*.skill.md` (trigger-word) additively, and uses the merged MCP set. Additive only — never weakens core safety/commit rules. On a same-named collision the plugin entry is announced and applied as an addendum.
- **Authoring**: edit the source `plugins/<name>/`; capture project-local edits with `import-plugin` (4.4.2). Versions live in `manifest.json` (no `.version`).

#### 4.9.1 The `nvidia-isaac-lab` plugin

Carved from the current `~/IsaacLab/__ai`: generic preferences go to Solaris core (4.5); everything NVIDIA/Isaac-specific lands here. Flat files:

```
plugins/nvidia-isaac-lab/
  manifest.json         # version + descriptor (NOT copied)
  mcps.json             # NVBugs MCP entry (merged at install; NOT copied):
                        #   { "nvbugs": { "type": "http",
                        #     "url": "https://maas.prd.astra.nvidia.com/maas/nvbugs/mcp" } }  (per Co-SA; NVIDIA-internal)
  install.skill.md      # install/update/migrate/repair; asks for GPU host / fork remote / paths -> ai/memory/{resources,credentials}.md
  shared/               # copied into ai/nvidia-isaac-lab/
    nvbugs.skill.md       # merged prep / triage / try-and-fix / handoff + __nvbugs/ folder + info.txt/resolution.txt formats
    nvbugs.rule.md        # never NVBugs URLs (bare id only); NVBugs etiquette
    git-workflow.rule.md  # branch naming (my-*, my-nvbugs-<n>), fork `my` -> origin/develop, draft PRs, PR template, [nvbug-<id>] subject
    ci-checks.rule.md     # ./isaaclab.sh -f precommit; install_ci naming+markers; github-actions; isaaclab.sh reference (-d docs, training presets, _src notes)
    bot-replies.rule.md   # Dear <Bot>!; Greptile vs Isaac Lab Review Bot re-review asymmetry
  migrations/             # <to_version>.md as needed
```

The current `my_coder.agent.md` NVBugs workflows collapse into `shared/nvbugs.skill.md`; the scattered conventions/learnings from `my_coder.instructions.md` fold into the four `shared/*.rule.md` files (domain knowledge inline, no separate instructions file). Scope: IsaacLab-only for now (a separate base `nvidia` plugin can be split out later if other NVIDIA repos appear).

The Isaac Lab project runs in **remote-code mode** (`remote.json` → `mishasdell2-tailscale:~/IsaacLab`) with `ai/manifest.json` listing `nvidia-isaac-lab`. The repo's own committed `AGENTS.md` ("IsaacLab Guidelines") stays in place and is read as project rules; it is never duplicated into the ai-setup.

## 5. Project-type templates (`solaris/templates/projects/`)

Descriptions (guide create-project), not literal scaffolds.

| Type | Content |
|---|---|
| `web-service.md` | FastAPI JSON API + vanilla HTML/JS UI; uvicorn; `src/` layout with `app/`, `static/`, `tests/`; uv project (own venv, separate from Solaris's). Deploy: rsync + ssh run, optional Docker. |
| `python-cli.md` | Standalone trivial CLI, uv-based; modeled on Co-SA's `bootstrap-ext-project` python-cli: `src/`-layout package, single-entry helper script, Result+ExitCode+JSON envelope, pytest + ruff clean, Apache-2.0 + headers, `.gitignore`. |
| `ios-app.md` | Description-only stub for v0 (not exercised). |

Types come from two places: **core** (`solaris/templates/projects/*.md`) and **plugins** (`plugins/<name>/<type>.project.md`). `create-project` merges both lists; on a name collision core wins and the plugin's is shown as `<plugin>:<type>`. Choosing a plugin-provided type auto-attaches that plugin.

## 6. Validation (v0 acceptance)

1. **Todo app** (web-service): `create-project todo` → `develop-project` plans → implement FastAPI + vanilla UI → run locally → deploy to a remote over SSH and run/test there.
2. **python-cli**: `create-project <cli>` (standalone, uv) → scaffold runs clean (tests + lint green) → exercise CLI.
3. **Import**: `import-project` on an existing small repo → code lands at `projects/<slug>/src/`, ai-setup derived, ambiguities surfaced as questions, `develop-project` works against it.
4. **Migration**: bump framework version, author a no-op migration, run `update-project` on a project, confirm `framework_version` in `ai/manifest.json` advances + revert works.
5. **Dual IDE**: projects load correctly under Cursor and Claude Code; `mcp_sync.py --check` clean after copying `mcp.json.example`.
6. **Isaac Lab (plugin + remote-code)**: build the `nvidia-isaac-lab` plugin from the current `__ai` (via `import-plugin` create-from-aisetup); `import-project` IsaacLab in remote-code mode (`remote.json` → dell, Remote-SSH); confirm its `install.skill.md` copies `shared/` into `ai/nvidia-isaac-lab/`, installs the NVBugs MCP + captures resources, the `*.rule.md`/`*.skill.md` load, and a NVBugs workflow runs against the remote checkout — matching current `__ai` behavior. Then edit `ai/nvidia-isaac-lab/` locally and confirm `import-plugin` update-from-project folds the edit back into the source.

## 7. Build order (milestones)

> Keep this `plan.md` as the living design doc throughout implementation — update it as decisions change; never delete it.

1. **Repo skeleton**: `pyproject.toml` (py>=3.14; packages `solaris`, `solaris.tools`), `uv.lock`, `.gitignore`, README, root `AGENTS.md`/`CLAUDE.md`/`.cursor/rules/solaris.mdc`/`mcp.json.example`, `solaris/__init__.py`, `plugins/.empty`, `memory/` seeds (`resources.md`, `credentials.md`).
2. **Tools** (package `solaris.tools`, run via `uv run -m`): `version.py`, `mcp_sync.py`, `log_interaction.py` (fail-safe) + `.claude/settings.json` + `.cursor/hooks.json`; `solaris/tests/` (version/mcp_sync/manifest).
3. **Framework agent + rules**: `solaris.agent.md`, `rules/commits.rule.md`, `rules/safety.rule.md`, `.githooks/commit-msg`.
4. **ai-setup template**: `templates/ai-setup/**` incl. combined `developer.agent.md`.
5. **Project-type templates**: `web-service.md`, `python-cli.md`, `ios-app.md`.
6. **Skills**: `create-project` + `import-project`, `develop-project`, then `update-project`, `self-reflect`, `import-plugin`; command-center `task` / `doctor` (+ `tasks/` area).
7. **Migration engine**: `migrations/` (`template.md`, `README.md`; `*.md` scanned by `version.py`) + wire `update-project`.
8. **Plugin system**: flat `plugins/` layout (`manifest.json` holds version, `shared/` copy boundary, `mcps.json`, per-plugin `migrations/`); `install.skill.md` lifecycle (install/update/migrate/repair) with pre-overwrite edit check; wire install into create/import and update/migrate into `update-project`; build the `nvidia-isaac-lab` plugin from `__ai`.
9. **Validate**: todo app + python-cli (local) and Isaac Lab (remote-code + plugin); exercise deploy, migration, plugin install/update/import, dual-IDE.

## 8. Conventions

- All Solaris Python runs via `uv run` against the root `.venv` (lazily created by `uv sync`). Project app code uses its own venv.
- **File formats**: human-facing files are Markdown (`.md`) — user-editable, tolerant of exact structure (`*.agent.md`, `*.skill.md`, `*.rule.md`, `spec.md`, `*.instructions.md`, `resources.md`, `credentials.md`, migration `<ver>.md`). Machine state is JSON (`manifest.json`, `remote.json`, `mcps.json`); Solaris-maintained state JSON carries a top-level `"_comment": "do not edit"`. Append-only logs are JSON Lines (`.jsonl`). No standalone YAML data/config files — but markdown/MDC **frontmatter** (Cursor `*.mdc` shims, migration `<ver>.md`) is exempt: it's metadata inside a doc, not a YAML data file.
- **File naming**: kebab-case. Skills `*.skill.md`, rules `*.rule.md` (framework and plugins alike). Cursor shims `*.mdc`.
- **AGENTS.md / CLAUDE.md** (root and per project) are minimal pointers — they delegate to the framework / ai-setup files and never duplicate their content.
- Tools: a `solaris.tools` package (stdlib only), invoked as `uv run -m solaris.tools.<name>`; `--dir`/`--workspace` args; human-readable stdout, `--json` where useful.
- `.venv`, `.tmp`, `.tools`, `.mcp.json`, `.cursor/mcp.json`, `projects/`, `tasks/`, `plugins/*` (except `.empty`), `solaris/spec/references/`, `memory/credentials.md` all gitignored.

## 9. Open questions (to confirm next iteration)

None open for v0. Recently resolved: context7 via its `ctx7` CLI (not an MCP server); `import-plugin` generic/domain split is heuristic + user-confirm (accepted); interaction logs left unbounded in v0 (small/rare).
