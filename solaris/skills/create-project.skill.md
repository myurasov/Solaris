---
name: create-project
triggers: ["create project", "new project", "start a project named X", "scaffold a project"]
summary: Scaffold a new project + portable ai-pack (type, mode, plugins) from templates; a plugin-provided type adds its questions, files and setup steps; stop before planning.
---

# create-project <!-- omit in toc -->

1. [Gather Inputs](#1-gather-inputs)
2. [Confirm the Plan](#2-confirm-the-plan)
3. [Materialize the ai-pack Template](#3-materialize-the-ai-pack-template)
4. [Wire the Code Location by Mode](#4-wire-the-code-location-by-mode)
5. [Attach Plugins](#5-attach-plugins)
6. [Seed Spec, Manifest, Revisions Baseline](#6-seed-spec-manifest-revisions-baseline)
7. [Runtime MCP (Gitignored)](#7-runtime-mcp-gitignored)
8. [Run the Type's Setup Steps](#8-run-the-types-setup-steps)
9. [Stop + Hand Off](#9-stop--hand-off)

Scaffold a new project under `projects/<group>/<slug>/` with a standardized ai-pack, then stop so the user
starts planning via `develop-project`. This skill does **not** write application source code (a
plugin-provided type adds only the starting files and setup it defines: steps 3 and 8).
`projects/<slug>/` below is shorthand for the grouped destination, and `<pack>/` is the project's ai-pack
folder (default `aipack/`, `ai/` in projects made before 0.39.0, any name). `uv run -m solaris.tools.plugins`
lists and reads the project types and resolves the plugins' dependencies.

## 1. Gather Inputs

Use the question tool (one batch) for anything not already given:

- **group** - which `projects/` subfolder (current: `nv/` NVIDIA, `my/` personal, `tmp/` throwaway;
  suggest from the request's context).
- **slug** - kebab-case, `^[a-z][a-z0-9-]*[a-z0-9]$` (e.g. `todo`).
- **name** - display name (default: Title Case of slug).
- **description** - one sentence.
- **type** - list the available types with `uv run -m solaris.tools.plugins types` and let the user pick:
  - core: every `solaris/templates/projects/*.md` (filename stem = type).
  - plugin-provided: every `plugins/<name>/<type>.project.md` (offered as `<plugin>:<type>`; one listed as
    INVALID is not offered - `plugins type <plugin:type>` says why).
  - choosing a plugin-provided type **auto-attaches that plugin** in step 5, with the plugins it requires.
- **mode** - `local` (code in `source/`, default), `remote-code` (code on a remote; needs `host` + `path`),
  or `embedded` (opt-in: the ai-pack lives **inside** the source repo at `projects/<slug>/<repo>/`, no separate
  `source/`, so it commits with the repo). Offer `embedded` only when the user wants the pack to travel inside their repo.
- **plugins** - any additional plugins to attach (names under `plugins/`; a plugin type brings its own, below).
- **pack folder** (optional) - the name of the ai-pack folder: `aipack` unless the user wants another
  plain folder name that no other folder at the project root uses. Solaris finds the pack by its
  manifest, not its name: the pack is the one direct child folder of the project root (embedded: the
  repo root) whose `manifest.json` is an ai-pack manifest (it has `framework_version` and a `project`
  object; plugin manifests do not). Hidden folders are never packs, and more than one is an error.
- **primary persona** (optional) - the role name of the project's primary agent: `engineer` unless the
  user wants a project-specific one (e.g. `master`); `^[a-z][a-z0-9-]*$`.
- **role personas** (optional) - names for additional persona briefs beside the primary
  (`<pack>/<role>.agent.md`, e.g. `reviewer`, `worker`); offer this only when the user describes distinct
  agent roles. All personas share the one `<pack>/instructions.md`.
- **workspaces** (optional) - names of additional self-contained work tracks beyond the default
  (`source/`). Most projects start flat (just `source/`, the default workspace) and add workspaces later;
  offer this only when the user describes multiple parallel tracks.

**A plugin-provided type** shapes the batch, so settle the type first: when the request names a plugin (a
new project "for `<plugin>`") take that plugin's type, and ask a single question only when it provides
several or the request names none. Read the type with `uv run -m solaris.tools.plugins type <plugin:type> --json`
(exit 3: the definition is invalid - report its `problems` and stop), then fold it into the one batch:

- its `questions` join the batch: `ask` is the question, `choices` the options, `default` the recommended
  answer with its placeholders filled (`{{SLUG}}` is the slug); a `required` one must be answered;
- its `defaults` (`mode`, `primary`, `roles`, and any `workspaces` or `pack`) are the recommended answers of
  those inputs - offer the role personas it names even when the request described no roles;
- its plugins: `attach` (the providing plugin, `plugins.required` and their required dependencies, in order)
  are attached without asking; `suggest` (optional plugins present under `plugins/`, each with its `why`)
  is one multi-select question in the batch; `unavailable` ones (not under `plugins/`) are only mentioned.

Read how the chosen type is structured (it guides planning later): a core type's
`templates/projects/<type>.md`, a plugin type's `## Structure` section (`structure` in the JSON).

## 2. Confirm the Plan

Print a one-screen summary (slug, name, type, mode, plugins, pack folder, destination `projects/<slug>/`,
host/path if remote; for a plugin type also its answers and the files its overlay adds, `overlay` in the
JSON). Resolve the plugin set instead of guessing it: `uv run -m solaris.tools.plugins deps <plugin>...`
over every plugin chosen (a plugin type's `attach` list, the optional ones picked, any additional ones)
gives the attach order, dependencies first, with the chain that brought each one in - show that list - and
the optional dependencies of those plugins not chosen yet, which the owner may add as an edit here. Exit 3
(a dependency cycle, or a required plugin missing from `plugins/`) stops the plan: say which. Ask to
proceed / edit / cancel. If `projects/<slug>/` exists and is non-empty, stop and say so.

## 3. Materialize the ai-pack Template

Create `projects/` if it does not exist (gitignored, lazily created). The template keeps its pack in
`solaris/templates/ai-pack/ai/`; the project's copy goes to `projects/<slug>/<pack>/`:

1. Write `<pack>/manifest.json` first, from the template's, with its placeholders filled (for a plugin type,
   then run step 4's `apply-type` with `--dry-run` at once, so an overlay clash stops creation before the rest
   of the pack is written) - the manifest is
   what makes the folder the pack.
2. Copy the files `revs` never renders - `<pack>/instructions.md`, `spec.md`, `defaults.json` and
   `.memory/*` (including the private owner-directions log `.memory/directions.md` and
   `.memory/improvements.md`, the project's suggestions for the framework and plugins, which it never
   edits itself), the root `CLAUDE.md`, and the `source/` stub -
   and substitute placeholders in each:
   `{{SLUG}}`, `{{NAME}}`, `{{TYPE}}` (a core type's name, or `<plugin>:<type>` for a plugin type),
   `{{MODE}}`, `{{DESCRIPTION}}`, `{{DATE}}` (today, ISO),
   `{{FRAMEWORK_VERSION}}` (from `uv run -m solaris.tools.version current`), `{{PACK}}` (the pack folder
   name from step 1, `aipack` by default), and `{{PRIMARY}}` / `{{PRIMARY_TITLE}}` (the chosen primary
   role and its Title Case - `engineer` / `Engineer` by default - so seeded-only files such as
   `<pack>/.memory/context.md` and `source/README.md` carry the right name from the start). Text that
   says `<pack>/` stays as it is.
3. Do **not** copy or hand-fill the managed files - the root `AGENTS.md`, `<pack>/engineer.agent.md`,
   `<pack>/README.md`, and `<pack>/rules/`, `skills/`, `info/`: step 6's `revs ff` writes them with every
   placeholder rendered, `{{PACK}}` included, and a hand-filled copy that differs from its output by one
   byte classifies as a conflict. The primary persona arrives as `<pack>/engineer.agent.md`; step 6
   renames it when step 1 chose another role, never by hand.
4. **A plugin type's overlay** (skip for a core type, or when `overlay` is empty): write the answers as one
   JSON object to a temporary file outside the project (`mktemp`) - each question's `key` with its answer
   (leave out an optional one the owner skipped: it renders its default, or an empty string when it has
   none) plus the placeholders above
   under their names, `SLUG`, `NAME`, `TYPE`, `MODE`, `DESCRIPTION`, `DATE`, `FRAMEWORK_VERSION`, `PACK`,
   `PRIMARY` and `PRIMARY_TITLE` (the role chosen in step 1, though step 6 renames the persona later) - and run
   `uv run -m solaris.tools.plugins apply-type <plugin:type> --dir projects/<slug> --answers <file>`
   (embedded: `--dir` is the repo root). It copies the type's overlay into the project root with the
   placeholders and each `{{ANSWER_<KEY>}}` filled, appends every `*.append.md` file to the same path
   without `.append`, after a blank line (`{{PACK}}/instructions.append.md` to `<pack>/instructions.md`), and
   prints each file it creates or appends. It refuses (exit 3) before writing anything when a file it
   creates would overwrite one, or a required answer is missing: report what it printed and stop.

The project root is intentionally minimal: `AGENTS.md` (from `revs ff`) + a one-line `CLAUDE.md`
(`@AGENTS.md`, copied from the template) plus `<pack>/` and (local mode) `source/`, and whatever a plugin
type's overlay adds. There is no `.cursor/`,
no `mcp.json.example`, and no `.gitignore` - the folder is not committed, unless a plugin type's overlay and
Setup Steps say otherwise (a type may make the project root its git repo). Cursor reads `AGENTS.md`
natively; Claude Code reads the `CLAUDE.md` shim. If a project type adds a `source/AGENTS.md`, drop a sibling `source/CLAUDE.md` (`@AGENTS.md`) too.
Copied files keep their `_Rev. N_` rev markers (line 1; in files with YAML frontmatter, right after the closing ---). For **embedded** mode the destination is the
repo root `projects/<slug>/<repo>/` (and the template's `source/` stub is dropped) - see step 4.

## 4. Wire the Code Location by Mode

- **local:** keep `source/`; `git init -b main` inside `source/` is deferred to the engineer agent unless a
  plugin type's Setup Steps set up the repository themselves (never commit
  yet; when it happens, seed the repo's `.gitignore` with `__*/` - the local-only-folders convention in
  `<pack>/instructions.md`).
- **workspaces** (any mode): `source/` is the **default workspace**. For each additional workspace named in
  step 1, create `<name>/` beside it (embedded: at the repo root) and materialize
  `solaris/templates/workspace/{setup.md,spec.md}` into it, substituting `{{WORKSPACE}}` (the folder name)
  and `{{NAME}}`; register each in the manifest `project.workspaces` array and in the
  `<pack>/instructions.md` workspace table. Workspaces are self-contained (own setup/deps, no file
  references into siblings; shared inputs live outside) - the canonical rules are in the template
  `ai/engineer.agent.md` (Workspaces).
- **remote-code:** delete `source/`; write `projects/<slug>/remote.json`:
  ```json
  { "_comment": "do not edit by hand", "mode": "remote-code", "host": "<HOST>", "path": "<REMOTE_PATH>",
    "deploy": false, "sync": { "excludes": [".venv", ".git", "__pycache__", "outputs/", "logs/"] } }
  ```
  Set `project.mode` to `remote-code` in `<pack>/manifest.json`.
- **embedded:** the code repo lives at `projects/<slug>/<repo>/` (e.g. `source`; an existing repo, or one
  you `git init` there) and holds the **whole** project - code, `<pack>/`, `AGENTS.md` + `CLAUDE.md`, `README`,
  dotfiles. Materialize `AGENTS.md` + `CLAUDE.md` + `<pack>/` at that repo's root (not at `projects/<slug>/`);
  keep any non-repo local aux (`references/`, `screenshots/`) at `projects/<slug>/` *outside* `<repo>`. Add
  `<pack>/.memory/`, **`.secrets.env`**, and `__*/` (local-only folders) to the repo's `.gitignore` so no
  secrets/hosts or scratch are committed, and seed a `.gitattributes` with `*.jsonl merge=union` (committed
  append-only logs then merge cleanly across collaborators). Record
  `project.mode` `embedded` in `<pack>/manifest.json`; tools take `--dir projects/<slug>/<repo>/`.

## 5. Attach Plugins

Attach the plugin set of step 2 in its order, dependencies first: for a plugin type its `attach` list (the
providing plugin, `plugins.required` and their required dependencies), then the optional ones the owner
picked and any additional ones, each after its own required dependencies. For each, run `install-plugin`
(install): it copies the plugin's `shared/` into `<pack>/plugins/<name>/`, merges its `mcps.json` servers into the project runtime
MCP (step 7), runs the plugin's `setup` from `manifest.json` (prompts for resources -> `<pack>/.memory/`), and
records `{name, version}` in `<pack>/manifest.json` -> `plugins`. Its dependency questions were answered by
the step-2 plan, so do not ask them again. Then `uv run -m solaris.tools.plugins check --dir projects/<slug>`
must pass (exit 0); exit 3 names a required dependency still missing - attach it before going on.

## 6. Seed Spec, Manifest, Revisions Baseline

- Short spec dialogue (purpose, components, constraints) -> `<pack>/spec.md`; for a plugin type, skip what its
  questions already answered and what its Setup Steps write into the spec. Copy the spec verbatim to
  `<pack>/.memory/spec-v0.md` (for a plugin type, after step 8).
- The copied `<pack>/.memory/` already includes a fresh `context.md` (the session-context summary, with
  `{{NAME}}` substituted); leave its `## Session Context` empty for the engineer to fill at a save point.
- Ensure `<pack>/manifest.json` has `project.{name,slug,type,mode,description}` (the one-line description
  feeds the pack README's `{{DESCRIPTION}}` render; `type` is `<plugin>:<type>` for a plugin type),
  `framework_version`, and `plugins`; when
  the project has workspaces beyond the default, also `project.workspaces` (array of folder names,
  `source` included).
- Seed the project's own version: `uv run -m solaris.tools.version project-set --dir projects/<slug> 0.1.0`
  (a plain-text `.version` at the project root; the engineer proposes bumps at milestones - see the
  template's Project Version section).
- Materialize the managed pack files: `uv run -m solaris.tools.revs ff --dir projects/<slug>` - writes
  the root `AGENTS.md`, `<pack>/engineer.agent.md`, `<pack>/rules/`, `skills/`, `info/` and
  `<pack>/README.md` (the generated pack overview + how-to; its attached-plugins list renders from the
  manifest, so run this after step 5), every placeholder rendered.
- Record the **revisions baseline**: `uv run -m solaris.tools.revs baseline --dir projects/<slug>` writes
  the `revisions` map (per materialized file: rev + content hash), so future `update-project` runs can tell
  whether the user edited a file.
- Personas (only when chosen in step 1): a non-default primary ->
  `uv run -m solaris.tools.agents --rename-primary <role> --dir projects/<slug>` (after the baseline; it
  moves `<pack>/engineer.agent.md` to `<pack>/<role>.agent.md`, sets the manifest's `agents.primary`, fixes the
  references in `<pack>/instructions.md`, and re-renders the managed files). Each role persona -> copy
  `solaris/templates/agents/role.agent.md` (or, for a worker or a reviewer, the ready-made
  `worker.agent.md` / `reviewer.agent.md` beside it) to `<pack>/<role>.agent.md` (beside the primary; a brief
  the type's overlay already wrote stays as it is), fill the
  frontmatter and brief with the user, put any starting know-how into the shared `<pack>/instructions.md`
  (under a heading named for the role when only that role uses it), validate
  (`uv run -m solaris.tools.agents --check --dir projects/<slug>`), then re-run `revs ff` + `revs baseline`
  so the pack README lists them.

## 7. Runtime MCP (Gitignored)

Write the project's `.mcp.json` and `.cursor/mcp.json` from the framework root `mcp.json.example`
(`mcpServers`); plugin install (step 5) merges any plugin servers into both. Verify with
`uv run -m solaris.tools.mcp_sync --dir projects/<slug> --check`. Both runtime files are gitignored.

## 8. Run the Type's Setup Steps

Plugin types only; a core type goes straight to step 9. First read `<pack>/instructions.md` and load every
attached plugin's always-on `<pack>/plugins/<plugin>/*.rule.md` (and its skills as their triggers fire): the
steps act under those rules. Then follow the type's `## Setup Steps` (`setup_steps`
in the `plugins type <plugin:type> --json` output) in order, from the project root and with the step-1
answers: they are the type's own initialization (sign-ins, first data, first reads), run now that its
plugins are attached and the pack is rendered. The safety rule holds inside them: confirm each destructive,
remote-mutating or outward action first, and hand the owner what only the owner can do (a browser sign-in,
accepting terms on a website). When a step fails or waits on the owner, stop there, say which step and why, and record the
remaining steps under "Next actions" in `<pack>/.memory/context.md`, so `develop-project` resumes them. End with the type's `## Hand-Off` text (`hand_off`), placeholders and answers filled: it
is the closing message of step 9.

## 9. Stop + Hand Off

Print what was created. **Do not** generate source or enter planning (beyond what a plugin type's Setup Steps
do, such as a first plan). Tell the user:
"Run `develop-project <slug>` to plan and build." (for a plugin type, the Hand-Off text of step 8, which says
what comes next). Log the turn with `uv run -m solaris.tools.interactions add`.
If this checkout is a Syncthing folder (`.stfolder` present), the root `.stglobalignore` already
excludes `__data/`, `__out/`, and `.git`; do not add a nested Syncthing folder for the project.
