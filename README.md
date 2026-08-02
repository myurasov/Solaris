# Solaris <!-- omit in toc -->

1. [Architecture](#1-architecture)
2. [AI-Packs](#2-ai-packs)
3. [Workspaces](#3-workspaces)
4. [Collaboration](#4-collaboration)
5. [Skills](#5-skills)
6. [Plugins](#6-plugins)
7. [Ad-Hoc Tasks](#7-ad-hoc-tasks)
8. [Memory \& Versioning](#8-memory--versioning)
9. [Install \& Tools](#9-install--tools)
10. [Specification](#10-specification)

Featues:

- Solaris serves as command center that runs many projects from one place, injecting an *AP-Pack* that can be updated from master version at any time with fresh improvements.
- Memory system on Solaris projects is split into short- (local-specific) and long-term (what can be shared).
- A project can be completely detached for standalone development and sharing with customer or public if needed.
- Solaris *AI-Packs* are compatible with collaboration through git-based workflows. Long-term memories such as instructions, specs, and conventions can be committed/reviewed through PRs and shared.
- Above makes Solaris *AI-Packs* and plugins evolving over time: improvements from one project flow back into Solaris, and from there into all the other Solaris-based projects.
- Solaris *Plugins* contain domain-specific skills/tools/etc and can also evolve from collaboration and project-specific developments.
- *Ad-Hoc Tasks* are supported for one-off work such as research, system setup, and quick prototyping.


## 1. Architecture

One agent runs everything; it adopts a persona from wherever it is working.

- At the repo root it is the **orchestrator**: it routes prompts to *skills* (markdown procedures),
  and manages `projects/`, `plugins/`, `tasks/`, and the framework memory.
- Inside `projects/<slug>/` it is that project's **engineer**: it plans, builds, and runs the
  project against the project's own *ai-pack*.
- "Hand-off" between the two just means switching the active instruction set and working directory,
  not spawning another process.

Instructions come from one canonical file per scope: `AGENTS.md`. Cursor reads it natively; Claude
Code reads it through a one-line `CLAUDE.md` (`@AGENTS.md`) shim.

Hooks make context loading deterministic instead of hoping the agent opens the right files:

- `read_first` (session start) injects the orchestrator role, the commit and safety rules, and the
  operating memory.
- `skill_loader` (prompt submit) matches each prompt against every skill's trigger phrases and
  injects the matching procedure.
- `log_interaction` (prompt submit) appends a raw-prompt backstop line to the interaction log.

## 2. AI-Packs

An *ai-pack* is the per-project bundle at `projects/<slug>/ai/` that tells an AI agent how to
develop that project: the engineer role, instructions, spec, and memory. It is created by the
`create-project` skill (new project) or `import-project` (existing codebase), and it is
**standalone-first**: a detached pack works with no framework around it - the few Solaris-only
conveniences are marked "under a Solaris checkout" and safely skipped. A pack can be refreshed from
the framework master at any time ("update `<project>`") to pick up the latest improvements.

The pack splits into two layers - the project's long- and short-term memory:

- **Shareable, long-term** (commits with the repo): `engineer.agent.md` (the role),
  `engineer.instructions.md` (build/run/test and conventions), `spec.md` (the contract),
  `manifest.json` (type, mode, framework version, plugins, workspaces), and `init` / `refresh`
  skill stubs (one-time onboarding and updating a teammate's checkout).
- **Private, short-term / machine-local** (`ai/.memory/`, gitignored - drop it to share):
  `resources.md` (hosts, hardware), `credentials.md` (secrets), `context.md` (session-context
  summary), `interactions.jsonl` (log).

Projects have a **type** (`python-cli`, `web-service`, `ios-app`, or plugin-provided) and a
**mode**:

- `local` - code lives in `source/` beside the pack.
- `remote-code` - code lives on an SSH host; the project keeps only a `remote.json` pointer.
- `embedded` - the pack lives *inside* the source repo and commits with it.

## 3. Workspaces

A *workspace* is a self-contained top-level folder holding one track of work within a project.
`source/` is the default workspace; a flat project has just that one, while a larger project can
hold several (say, a data pipeline, a UI, and an evaluation track) side by side.

Self-contained means each workspace can be brought up and run on its own:

- Its own `setup.md` - a from-scratch bring-up that ends in an end-to-end verification.
- Its own `spec.md`, dependencies, and scratch folders.
- No file references into sibling workspaces; shared inputs (datasets, common assets) live outside
  all of them, e.g. in `data/`.

The *ai-pack* stays single and shared at the project root - never per workspace. To add a
workspace: create the folder with `setup.md` + `spec.md` (mirror an existing workspace, or use the
stubs in `solaris/templates/workspace/`), then register it in the `engineer.instructions.md`
workspace table and the manifest's `project.workspaces`.

## 4. Collaboration

An *ai-pack* is built to be worked on by a team through ordinary git hosting (GitHub and the like),
with most collaborators never running Solaris at all.

- **Standalone teammates.** A collaborator clones the repo and works: the pack's `init` skill
  onboards them (collects their environment into the private layer), and `refresh` updates an
  existing checkout after a pull. Solaris-only notes are marked and skippable.
- **Reviewable changes.** The long-term memories (instructions, specs, conventions) are committed
  as diff-friendly files - wrapped prose, stable headings, generated TOCs - so pack changes review
  like any code change in a PR.
- **Mechanical merges.** Solaris sync metadata (`_Rev. N_` markers, the manifest `revisions` map)
  is take-either-side in a conflict; committed `*.jsonl` logs union-merge via `.gitattributes`.
- **Improvements propagate.** Pack edits merge up into the framework templates (per-file
  revisions) and plugin edits fold back into the plugin source (`import-plugin`), so a refinement
  made on one project reaches every Solaris-based project on its next update.
- **Handoff.** A project can be completely detached and shared with a customer or the public: the
  `publish-project` skill scrubs identities and internals, adds license/disclaimer, and verifies
  the detached pack stands alone.

## 5. Skills

A *skill* is a markdown procedure in `solaris/skills/*.skill.md` that the orchestrator follows for
a recurring operation. There are no slash commands - natural phrases trigger them (a prompt-submit
hook injects the matching procedure automatically):

| Say | Skill | What happens |
|---|---|---|
| "create / new project" | `create-project` | Scaffold a project + ai-pack (type / mode / plugins / workspaces). |
| "import / adopt `<path>`" | `import-project` | Adopt an existing codebase; derive its ai-pack. |
| "work on / develop `<project>`" | `develop-project` | Hand off to the project's engineer to plan/implement. |
| "update / migrate `<project>`" | `update-project` | Sync an ai-pack + plugins to the current framework version. |
| "publish / share `<project>`" | `publish-project` | Scrub identities/internals, license, verify the pack stands alone. |
| "create / install / repair a plugin" | `import-plugin`, `install-plugin` | Author, acquire, validate, attach plugins. |
| "do a release" | `release` | Bump version, author migration, update docs, tag + publish. |
| "self-reflect" | `self-reflect` | Review interaction logs; propose framework improvements. |
| "new task / research X" | `ad-hoc-task` | Start/resume dated ad-hoc work under `tasks/`. |
| "health-check" / "status" | `health-check` | Command-center overview; `--deep` for full checks. |

Projects can carry their own pack-level skills (`ai/*.skill.md`, e.g. `init`, `refresh`), and
attached plugins add theirs.

## 6. Plugins

A *plugin* packages a domain or employer workflow so any project can opt into it: always-on
`*.rule.md`, trigger-invoked `*.skill.md`, `mcps.json` (MCP servers), and optional project types.

- **Attach:** `install-plugin` copies the plugin's `shared/` into the project's `ai/<name>/`,
  merges its MCP servers, runs its setup prompts, and records `{name, version}` in the manifest.
  **Link mode** attaches the live source via a pointer file instead of a copy - for plugin
  development.
- **Author:** `import-plugin` creates a plugin from a project's ways of working, or folds
  project-local edits back into the plugin source.
- **Evolve:** plugins improve the same way packs do - project-local refinements and collaborator
  edits fold back into the plugin source and reach every project that attaches it.
- **Source:** a plugin is either its own git repo (acquired from a URL / folder / zip; ignored via
  `plugins/.gitignore`) or bundled under `plugins/`. Bundled: `nvidia-isaac-lab` (NVBugs + Isaac
  workflow), `visual-qa` (VLM-based visual E2E testing), `aisee` (AISee visual QA: rule + skill +
  MCP servers), `nvidia-brev` (autonomous Brev cloud-GPU run lifecycle).

## 7. Ad-Hoc Tasks

A *task* is lightweight dated folder (`tasks/<YYYY-MM-DD>-<slug>/`) for one-off work that does not
warrant a project: research, system setup, quick prototyping. Its `notes.md` (steps, findings,
outcome) is the durable record; scratch scripts live beside it. Say "new task ..." to start or
resume one, and if the work turns into something durable, it graduates into a project or plugin.

## 8. Memory & Versioning

Memory is file-based and strictly scoped - Solaris never uses a global or harness-provided store:

- **Framework memory** (`.memory/`, gitignored): `instructions.md` is the operating memory - terse,
  timestamped cross-project lessons and preferences, loaded every session and updated in place;
  `resources.md`, `credentials.md`, and `interactions.jsonl` hold inventory, secrets, and the log.
- **Project memory** (`ai/.memory/` in each pack): same shape, project-scoped. Packs never read the
  framework memory. The committed pack files (instructions, spec, conventions) are the project's
  shareable long-term memory; `ai/.memory/` is the short-term, machine-local layer.

Two versioning mechanisms keep packs current:

- **Per-file revisions** (`_Rev. N_` markers + a ledger) sync materialized pack files with their
  framework masters: untouched files fast-forward, user-improved files merge up, true conflicts ask.
  Markers appear only on files that materialize into packs (templates, plugin `shared/`);
  everything else is versioned by git + semver.
- **Release-only semver** gates **migrations** (`solaris/migrations/<version>.md`) that upgrade a
  project's `ai/` across framework versions without touching its code; plugins carry their own.
  Run "update `<project>`" to apply both.

## 9. Install & Tools

Requires [uv](https://docs.astral.sh/uv/) (manages Python 3.14) + Cursor or Claude Code; Node.js only for
the optional Playwright MCP.

```bash
uv sync                                                    # deps + venv (Python 3.14)
cp mcp.json.example .mcp.json                              # runtime MCP (Claude Code)
mkdir -p .cursor && cp mcp.json.example .cursor/mcp.json   # runtime MCP (Cursor)
uv run -m solaris.tools.mcp_sync --check                   # configs match?
git config core.hooksPath .githooks                        # optional commit-policy hook
```

Open the repo root in Cursor or Claude Code and talk to the agent (e.g. *"create a new python-cli
project called pingpong"*).

Stdlib-only tools run as modules (`uv run -m solaris.tools.<name>`): `version` (semver + migration
chain), `revs` (per-file revisions), `mcp_sync` (MCP config drift), `toc` (tables of contents), plus
`uv run pytest` for the test suite. `read_first`, `skill_loader`, and `log_interaction` are hooks -
never run by hand.

## 10. Specification

Full conventions, plugin contract, migration engine, project modes, and safety/commit policies:
[`solaris/spec/spec-v0.20.0.md`](solaris/spec/spec-v0.20.0.md). [Apache 2.0](LICENSE); Copyright 2026
Mikhail Yurasov <me@yurasov.me>.
