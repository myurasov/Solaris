# Solaris <!-- omit in toc -->

- [1. Architecture](#1-architecture)
- [2. AI-Packs](#2-ai-packs)
- [3. Workspaces](#3-workspaces)
- [4. Collaboration](#4-collaboration)
- [5. Skills](#5-skills)
- [6. Plugins](#6-plugins)
- [7. Memory \& Versioning](#7-memory--versioning)
- [8. Install \& Tools](#8-install--tools)
- [9. Specification](#9-specification)

Solaris:

- Runs many coding projects from one place, injecting a maintainable "ai-pack" that lets a project
  also be detached for standalone development. Each pack evolves with the project by remembering
  what it needs to know short- and long-term.
- Keeps ai-packs compatible with collaboration through git-based workflows: standalone-first,
  diffable files, reviewable through normal PRs - and improvements flow back into Solaris and out
  to every Solaris-based project.
- Supports plugins that add domain-specific workflows, and an ad-hoc mode for work that doesn't
  warrant a project.

## 1. Architecture

- **One agent, persona by location.** At the root it is the **orchestrator** (routes prompts to skills;
  manages `projects/`, `plugins/`, `tasks/`, memory). Inside `projects/<slug>/` it is that project's
  **engineer** (plans, builds, runs). Hand-off = switch the active instruction set + working dir.
- **Single instruction source.** `AGENTS.md` is canonical - Cursor reads it natively; Claude Code via a
  one-line `CLAUDE.md` (`@AGENTS.md`) shim.
- **Hooks inject context.** `read_first` (SessionStart) preloads the orchestrator role + commit/safety
  rules + operating memory; `skill_loader` (UserPromptSubmit) matches prompt `triggers`/`antitriggers` and
  injects the matching skill body; `log_interaction` appends a raw-prompt log backstop.

## 2. AI-Packs

Per-project bundle at `projects/<slug>/ai/` - **standalone-first**: a detached pack works with no
framework around it (the few Solaris-only conveniences are marked "under a Solaris checkout" and safely
skipped when working standalone).

- **Shareable** - `engineer.agent.md` (role), `engineer.instructions.md` (build/run/test), `spec.md`,
  `manifest.json` (type, mode, framework version, plugins, workspaces), and `init` / `refresh` skill
  stubs (one-time onboarding / updating a teammate's checkout).
- **Private** (`ai/.memory/`, drop to share) - `resources.md`, `credentials.md`, `context.md`,
  `interactions.jsonl`.

**Type**: `python-cli`, `web-service`, `ios-app`, or plugin-provided. **Mode**: `local` (code in
`source/`), `remote-code` (code on an SSH host), or `embedded` (ai-pack inside the source repo).

## 3. Workspaces

A project's code lives in one or more **workspaces** - self-contained top-level folders, each its own
track of work: own `setup.md` (from-scratch bring-up ending in verification), own `spec.md`, deps, and
scratch; no file references into siblings (shared inputs live outside, e.g. `data/`). `source/` is the
default workspace, and a flat project has just that one. The ai-pack is single and shared across all
workspaces.

## 4. Collaboration

- **Git-based workflows.** Committed ai files are diff-friendly (wrapped prose, stable headings,
  generated TOCs) and review cleanly in GitHub PRs. Merges stay mechanical: Solaris sync metadata
  (`_Rev. N_` markers, the manifest `revisions` map) is take-either-side, and committed `*.jsonl` logs
  union-merge via `.gitattributes`.
- **Improvements propagate.** Collaboration output is not a dead end: pack edits merge up into the
  framework templates (per-file revisions) and plugin edits fold back into the plugin source
  (`import-plugin`), so a refinement made on one project reaches every Solaris-based project on its
  next update.
- **Handoff-ready.** `publish-project` scrubs identities/internals, adds license/disclaimer, and
  verifies the detached pack stands alone.

## 5. Skills

Natural-language triggers route to Markdown procedures in `solaris/skills/*.skill.md`:

| Trigger | Skill | Action |
|---|---|---|
| "create / new project" | `create-project` | Scaffold a project + ai-pack (type / mode / plugins / workspaces). |
| "import / adopt `<path>`" | `import-project` | Adopt an existing codebase; derive its ai-pack. |
| "work on / develop `<project>`" | `develop-project` | Hand off to the engineer to plan/implement. |
| "update / migrate `<project>`" | `update-project` | Migrate an ai-pack + plugins to the current version. |
| "publish / share `<project>`" | `publish-project` | Scrub identities/internals, license, verify the pack stands alone. |
| "create / install / repair a plugin" | `import-plugin`, `install-plugin` | Author, acquire, validate, attach plugins. |
| "do a release" | `release` | Bump version, author migration, update docs, tag + publish. |
| "self-reflect", "new task / research X", "health-check" | `self-reflect`, `ad-hoc-task`, `health-check` | Improve the framework; ad-hoc work under `tasks/`; status overview. |

## 6. Plugins

A plugin packages a domain/employer workflow - `*.rule.md` (always-on), `*.skill.md` (trigger-invoked),
`mcps.json` (MCP servers), optional project types - opted into per project (`manifest.json` `plugins[]`) and
materialized into `ai/<name>/`. It is either **its own git repo** (acquired via `install-plugin` from a git
URL / folder / zip; ignored via `plugins/.gitignore`) or **bundled** under `plugins/`. Bundled:
`nvidia-isaac-lab` (NVBugs + Isaac workflow), `visual-qa` (VLM-based visual E2E testing), `aisee`
(AISee visual QA: rule + skill + MCP servers), `nvidia-brev` (autonomous Brev cloud-GPU run lifecycle).

## 7. Memory & Versioning

- **Memory boundary.** Only framework `.memory/` and each project's `ai/.memory/` are authoritative (no
  global/harness store). `.memory/instructions.md` is operating memory - terse timestamped cross-project
  lessons + preferences, loaded each session; turns log to `interactions.jsonl`.
- **Revisions** (`solaris.tools.revs`) keep materialized ai-pack files in sync with framework masters via
  `_Rev. N_` markers + a ledger. Markers appear only on files that materialize into packs (templates,
  plugin `shared/`); everything else is versioned by git + semver. **Release-only semver** gates
  **migrations** (`solaris/migrations/<version>.md`) that upgrade a project's `ai/` without touching its
  code; plugins carry their own.

## 8. Install & Tools

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

## 9. Specification

Full conventions, plugin contract, migration engine, project modes, and safety/commit policies:
[`solaris/spec/spec-v0.20.0.md`](solaris/spec/spec-v0.20.0.md). [Apache 2.0](LICENSE); Copyright 2026
Mikhail Yurasov <me@yurasov.me>.
