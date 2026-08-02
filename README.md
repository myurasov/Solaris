# Solaris <!-- omit in toc -->

1. [Getting Started](#1-getting-started)
2. [How Solaris Works](#2-how-solaris-works)
3. [Ways to Use Solaris](#3-ways-to-use-solaris)
4. [Start a Project](#4-start-a-project)
5. [Develop](#5-develop)
6. [Collaborate and Share](#6-collaborate-and-share)
7. [Extend with Plugins](#7-extend-with-plugins)
8. [Keep Projects Current](#8-keep-projects-current)
9. [Skills Reference](#9-skills-reference)
10. [Specification](#10-specification)

Featues:

- Solaris serves as command center that runs many projects from one place, injecting an *AP-Pack* that can be updated from master version at any time with fresh improvements.
- Memory system on Solaris projects is split into short- (local-specific) and long-term (what can be shared).
- A project can be completely detached for standalone development and sharing with customer or public if needed.
- Solaris *AI-Packs* are compatible with collaboration through git-based workflows. Long-term memories such as instructions, specs, and conventions can be committed/reviewed through PRs and shared.
- Above makes Solaris *AI-Packs* and plugins evolving over time: improvements from one project flow back into Solaris, and from there into all the other Solaris-based projects.
- Solaris *Plugins* contain domain-specific skills/tools/etc and can also evolve from collaboration and project-specific developments.
- *Ad-Hoc Tasks* are supported for one-off work such as research, system setup, and quick prototyping.

## 1. Getting Started

You need [uv](https://docs.astral.sh/uv/) (it manages Python 3.14) and Cursor or Claude Code.

```bash
git clone https://github.com/myurasov/Solaris && cd Solaris
uv sync                                                    # deps + venv
cp mcp.json.example .mcp.json                              # runtime MCP (Claude Code)
mkdir -p .cursor && cp mcp.json.example .cursor/mcp.json   # runtime MCP (Cursor)
git config core.hooksPath .githooks                        # optional commit-policy hook
```

Open the repo root in Cursor or Claude Code and just talk to the agent - there are no slash
commands. Try:

- *"create a new python-cli project called pingpong"*
- *"import project ~/code/my-app"*
- *"new task: research local LLM serving options"*
- *"health-check"*

Everything else in this guide happens through phrases like these; the full list is in the
[Skills Reference](#9-skills-reference).

## 2. How Solaris Works

One agent runs everything; it adopts a persona from wherever it is working:

- At the repo root it is the **orchestrator** - it routes your prompts to *skills* (markdown
  procedures) and manages `projects/`, `plugins/`, and `tasks/`.
- Inside `projects/<slug>/` it is that project's **engineer** - it plans, builds, and runs the
  project against the project's *ai-pack*.

You do not manage this switch; saying *"work on `<project>`"* hands off automatically.

Under the hood, hooks make context loading deterministic: `read_first` injects the rules and
operating memory at session start, `skill_loader` injects the matching skill when your prompt
triggers one, and `log_interaction` keeps a backstop log of every prompt. Instructions live in one
canonical `AGENTS.md` per scope (Cursor reads it natively; Claude Code through a one-line
`CLAUDE.md` shim).

## 3. Ways to Use Solaris

Solaris supports several modes of use; each has its own workflow, and they mix freely.

- **Command center** (the full setup, this repo): you run many projects from one place.
  Workflow: create or import a project -> develop through the engineer -> keep packs current with
  *"update `<project>`"* -> publish or hand off when a project leaves the nest.
- **Standalone pack** (no Solaris at all): a teammate or customer works on a single project repo
  that carries its *ai-pack*. Workflow: clone -> say *"init"* to onboard -> develop with any AI
  tool (the pack loads through `AGENTS.md`) -> *"refresh"* after pulls -> propose pack changes
  through normal PRs.
- **Ad-hoc mode**: no project at all - *"new task: ..."* gives a dated folder under `tasks/` for
  research, setup, or prototyping, with `notes.md` as the record; promising tasks graduate into
  projects or plugins.
- **Plugin development**: attach a plugin in **link mode** so a real project exercises the live
  plugin source. Workflow: *"link plugin `<name>` to `<project>`"* -> iterate in place -> version
  the plugin -> consumers pick it up on their next update.

## 4. Start a Project

Say *"create a new project"* (the agent will ask for what it needs) or *"import project
`<path>`"* to adopt an existing codebase. Either way you get an *ai-pack* at
`projects/<slug>/ai/` - the bundle that tells any AI agent how to develop that project.

A project can be anything - a service, a research codebase, a firmware tree, a content pipeline,
a doc site; nothing limits you to the bundled kinds. Choices you make at creation:

- **Type**: just a starting template that seeds structure and planning hints. `python-cli`,
  `web-service`, and `ios-app` ship with Solaris, plugins can provide more, and anything else
  works fine as the nearest type (or an import) plus your own spec.
- **Mode**: `local` (code in `source/` beside the pack), `remote-code` (code stays on an SSH host),
  or `embedded` (the pack lives inside your source repo and commits with it).
- **Plugins** to attach and, for multi-track projects, **workspaces** (see below).

What lands in the pack:

- **Shareable, long-term** (commits with the repo): `engineer.agent.md` (the role),
  `engineer.instructions.md` (build/run/test + conventions), `spec.md` (the contract),
  `manifest.json`, and `init` / `refresh` skill stubs for teammates.
- **Private, short-term / machine-local** (`ai/.memory/`, gitignored): hosts, secrets, session
  context, logs. Drop this layer to share the project.

The pack is **standalone-first**: it works with no Solaris around it, and can be refreshed from
the framework master at any time (*"update `<project>`"*) to pick up the latest improvements.

## 5. Develop

Say *"work on `<project>`"* and make requests; the engineer plans against `ai/spec.md`,
implements, runs, and remembers what it learns in the pack.

**Workspaces.** When a project grows parallel tracks (say a data pipeline, a UI, and an evaluation
track), split it into *workspaces* - self-contained top-level folders. Each has its own `setup.md`
(a from-scratch bring-up ending in verification), `spec.md`, dependencies, and scratch; none
references files in a sibling, and shared inputs (e.g. `data/`) live outside all of them.
`source/` is the default workspace; the ai-pack stays single and shared. To add one, ask the
engineer - or create the folder from `solaris/templates/workspace/` and register it in the
instructions' workspace table.

**Ad-hoc tasks.** For work that does not warrant a project - research, system setup, quick
prototyping - say *"new task: ..."*. You get a dated folder under `tasks/` whose `notes.md` (steps,
findings, outcome) is the durable record. If a task turns into something real, it graduates into a
project or plugin.

## 6. Collaborate and Share

An *ai-pack* is built for teamwork through ordinary git hosting - most collaborators never run
Solaris at all.

- **Onboard a teammate.** They clone the repo and say *"init"* (or "onboard me") in their AI tool:
  the pack's `init` skill collects their environment into the private layer and brings the project
  up. After a pull, *"refresh"* migrates their checkout to any new conventions.
- **Review pack changes like code.** The long-term memories (instructions, specs, conventions) are
  committed as diff-friendly files - wrapped prose, stable headings, generated TOCs - so they
  review cleanly in PRs.
- **Merge without fear.** Solaris sync metadata (`_Rev. N_` markers, the manifest `revisions` map)
  is take-either-side in a conflict; committed `*.jsonl` logs union-merge via `.gitattributes`.
- **Feed improvements back.** Pack edits merge up into the framework templates and plugin edits
  fold back into the plugin source (*"update plugin `<name>` from `<project>`"*), so a refinement
  made on one project reaches every Solaris-based project on its next update.
- **Hand off or publish.** Say *"prepare `<project>` for handoff"*: the `publish-project` skill
  scrubs identities and internals, adds license/disclaimer, and verifies the detached pack stands
  alone - ready for a customer or the public.

## 7. Extend with Plugins

A *plugin* packages a domain or employer workflow - always-on rules, trigger-invoked skills, MCP
servers, optional project types - so any project can opt into it.

- **Attach one:** *"install plugin `<git url | folder | zip>`"*, then *"add plugin `<name>` to
  `<project>`"*. Its `shared/` files are copied into the project's `ai/<name>/` and its MCP servers
  merged. While developing a plugin, use **link mode** (*"link plugin `<name>` to `<project>`"*) -
  a pointer to the live source instead of a copy.
- **Author one:** *"make a plugin from `<project>`"* factors that project's ways of working into a
  reusable plugin; the same skill folds later project-local edits back into the plugin source.
- **Bundled plugins:** `browserctl` (CLI browser automation on per-project Chromium profiles -
  the standard browser layer, replacing the Playwright MCP; includes a `slack-web` skill),
  `nvidia-isaac-lab` (NVBugs + Isaac workflow), `visual-qa` (VLM-based visual E2E testing),
  `aisee` (AISee visual QA: rule + skill + MCP servers), `nvidia-brev` (autonomous Brev
  cloud-GPU run lifecycle).

## 8. Keep Projects Current

Say *"update `<project>`"* whenever you want a pack to catch up with the framework. Two mechanisms
work together:

- **Per-file revisions** (`_Rev. N_` markers + a ledger) sync materialized pack files with their
  masters: files you never touched fast-forward, files you improved merge up into the master, true
  conflicts ask you. Markers appear only on files that materialize into packs; everything else is
  versioned by git + semver.
- **Migrations** (`solaris/migrations/<version>.md`) carry a pack across framework releases
  without touching your code; plugins carry their own.

Memory stays scoped while all this happens: the framework's `.memory/` (operating lessons,
inventory, logs) and each pack's `ai/.memory/` are the only stores, and packs never read the
framework's. Run *"health-check"* any time for a one-screen status of projects, revisions,
versions, and tasks.

## 9. Skills Reference

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

Stdlib-only tools back the skills and run as modules (`uv run -m solaris.tools.<name>`): `version`,
`revs`, `mcp_sync`, `toc`, plus `uv run pytest` for the test suite. `read_first`, `skill_loader`,
and `log_interaction` are hooks - never run by hand.

## 10. Specification

Full conventions, plugin contract, migration engine, project modes, and safety/commit policies:
[`solaris/spec/spec-v0.22.0.md`](solaris/spec/spec-v0.22.0.md). [Apache 2.0](LICENSE); Copyright 2026
Mikhail Yurasov <me@yurasov.me>.
