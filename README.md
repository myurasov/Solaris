# Solaris <!-- omit in toc -->

- [Why It Exists](#why-it-exists)
- [What You Get](#what-you-get)
- [Core Concepts](#core-concepts)
- [Getting Started](#getting-started)
- [Skills](#skills)
- [Development](#development)
- [Specification](#specification)
- [License](#license)

**A command center for running many coding projects with an AI agent.**

Drive it in natural language from one repo. For each project Solaris generates a portable **ai-pack** -
agent instructions, a living spec, and structured memory - so a coding agent (Cursor or Claude Code) plans,
builds, runs, deploys, remembers, and logs the same way everywhere. It's deliberately minimal: Markdown
instructions and a stdlib-only Python toolset run through `uv`, with no runtime service.

## Why It Exists

AI assistants are powerful per session but forgetful across them. Without structure you hit the same walls:

- **Repeated setup** - conventions and build/run/test commands get retyped per project and drift apart.
- **No durable memory** - hosts, decisions, and gotchas vanish when the context window rolls.
- **Scattered context** - juggling many repos means re-establishing each one every time.
- **Trapped workflows** - domain or employer conventions get reinvented instead of packaged and reused.
- **IDE lock-in** - instructions for one assistant don't carry to another.

## What You Get

- **One agent, both IDEs** - a single `AGENTS.md` drives Cursor and Claude Code; author once.
- **Spec-driven** - every project plans and builds against a living `spec.md`.
- **Persistent memory** - per-project resources/credentials/context plus cross-project lessons and
  preferences the agent loads each session.
- **Built-in workflows** - scope, implement, run/test locally, and deploy to a remote over SSH.
- **Portable ai-packs** - work standalone, handed off, or driven from the command center.
- **Reusable plugins** - package a domain workflow as its own repo and opt in per project.
- **Versioned + migratable** - upgrades migrate existing projects forward, never stranding them.
- **Safety + commit policies** - confirms destructive/outward actions; enforces commit-message rules.

## Core Concepts

- **Command Center** - the Solaris repo you run everything from; your `projects/` and `tasks/` stay
  gitignored and separate from the framework.
- **ai-pack** - the per-project bundle (`projects/<slug>/ai/`): a **shareable layer** (agent role,
  build/run/test instructions, spec) and a **private layer** (`ai/memory/`: hosts, secrets, logs). Drop
  `ai/memory/` to share it.
- **Personas** - one agent, role by location: **orchestrator** at the root (routes to skills, manages
  projects/plugins/tasks), **engineer** inside a project (plans, builds, runs).
- **Project Types & Modes** - pick a type (`python-cli`, `web-service`, `ios-app`, or plugin-provided) and a
  mode: **local**, **remote-code**, or **embedded** (ai-pack lives inside the source repo).
- **Plugins** - each its own git repo of rules, skills, MCP servers, and project types; install from a git
  URL, folder, or zip and attach per project.
- **Memory Boundary** - the framework `memory/` and each project's `ai/memory/` are the only authoritative
  stores; no external/global memory.
- **Versioning & Migrations** - per-file revisions keep ai-packs in sync with masters; release-only semantic
  versions gate migrations that upgrade `ai/` without touching your code.

## Getting Started

**Requirements**

- [uv](https://docs.astral.sh/uv/) - manages Python 3.14 and the venv.
- **Cursor** or **Claude Code**.
- Node.js - only for the optional Playwright MCP server (`npx`).

**Install**

```bash
# dependencies + venv (Python 3.14)
uv sync

# MCP config: copy the template to both runtime configs, then check sync
cp mcp.json.example .mcp.json
mkdir -p .cursor && cp mcp.json.example .cursor/mcp.json
uv run -m solaris.tools.mcp_sync --check

# (optional) enable the commit-policy git hook
git config core.hooksPath .githooks
```

**Use** - open the repo root in Cursor or Claude Code and talk to the agent - e.g. *"create a new python-cli
project called pingpong"*. `AGENTS.md` drives both IDEs (Claude Code via a one-line `CLAUDE.md` that imports
it). `.venv/`, `.tmp/`, and runtime MCP configs are created lazily and gitignored.

## Skills

The agent routes natural-language requests to a skill:

| Ask for | Skill | What it does |
|---|---|---|
| "create / new project" | `create-project` | Scaffold a new project + ai-pack (pick type / mode / plugins). |
| "import / adopt `<path>`" | `import-project` | Adopt an existing codebase and derive its ai-pack. |
| "create / update a plugin" | `import-plugin` | Author a plugin from a project, or fold edits back in. |
| "install / repair a plugin" | `install-plugin` | Acquire, validate, and attach a plugin to a project. |
| "work on / develop `<project>`" | `develop-project` | Hand off to the project's engineer to plan or implement. |
| "update / migrate `<project>`" | `update-project` | Migrate an ai-pack and its plugins to the current version. |
| "self-reflect / improve Solaris" | `self-reflect` | Review logs and propose framework improvements. |
| "do a release" | `release` | Bump version, author the migration, update docs, tag and publish. |
| "new task / research X" | `ad-hoc-task` | Start or resume ad-hoc work under `tasks/<date>-<slug>/`. |
| "health-check / status" | `health-check` | Command-center overview; `--deep` for full checks. |

## Development

```bash
uv run pytest                              # run the tool tests
uv run -m solaris.tools.version current    # -> 0.11.0
uv run -m solaris.tools.revs status        # framework files vs the revision ledger
uv run -m solaris.tools.toc --check --all  # verify every Markdown TOC
```

Tools run as modules (`uv run -m solaris.tools.<name>`): `version`, `revs`, `mcp_sync`, `toc`.

## Specification

Full conventions, the plugin contract, the migration engine, project modes, and the safety/commit policies
live in [`solaris/spec/spec-v0.11.0.md`](solaris/spec/spec-v0.11.0.md).

## License

Licensed under the [Apache License 2.0](LICENSE). Copyright 2026 Mihail Yurasov <me@yurasov.me>.
