---
name: create-project
triggers: ["create project", "new project", "start a project named X", "scaffold a project"]
summary: Scaffold a new project + portable ai-pack (type, mode, plugins) from templates; stop before planning.
---

# create-project <!-- omit in toc -->

- [1. Gather Inputs](#1-gather-inputs)
- [2. Confirm the Plan](#2-confirm-the-plan)
- [3. Materialize the ai-pack Template](#3-materialize-the-ai-pack-template)
- [4. Wire the Code Location by Mode](#4-wire-the-code-location-by-mode)
- [5. Attach Plugins](#5-attach-plugins)
- [6. Seed Spec, Manifest, Revisions Baseline](#6-seed-spec-manifest-revisions-baseline)
- [7. Runtime MCP (Gitignored)](#7-runtime-mcp-gitignored)
- [8. Stop + Hand Off](#8-stop--hand-off)

Scaffold a new project under `projects/<slug>/` with a standardized ai-pack, then stop so the user starts
planning via `develop-project`. This skill does **not** write application source code.

## 1. Gather Inputs

Use the question tool (one batch) for anything not already given:

- **slug** - kebab-case, `^[a-z][a-z0-9-]*[a-z0-9]$` (e.g. `todo`).
- **name** - display name (default: Title Case of slug).
- **description** - one sentence.
- **type** - list the available types and let the user pick:
  - core: every `solaris/templates/projects/*.md` (filename stem = type).
  - plugin-provided: every `plugins/<name>/<type>.project.md` (offered as `<plugin>:<type>`).
  - choosing a plugin-provided type **auto-attaches that plugin** in step 5.
- **mode** - `local` (code in `source/`, default), `remote-code` (code on a remote; needs `host` + `path`),
  or `embedded` (opt-in: the ai-pack lives **inside** the source repo at `projects/<slug>/<repo>/`, no separate
  `source/`, so it commits with the repo). Offer `embedded` only when the user wants the pack to travel inside their repo.
- **plugins** - any additional plugins to attach (names under `plugins/`).

Read the chosen `templates/projects/<type>.md` for how that type is structured (it guides planning later).

## 2. Confirm the Plan

Print a one-screen summary (slug, name, type, mode, plugins, destination `projects/<slug>/`, host/path if
remote). Ask to proceed / edit / cancel. If `projects/<slug>/` exists and is non-empty, stop and say so.

## 3. Materialize the ai-pack Template

Create `projects/` if it does not exist (gitignored, lazily created), then copy
`solaris/templates/ai-pack/` -> `projects/<slug>/` and substitute placeholders in every copied text file:
`{{SLUG}}`, `{{NAME}}`, `{{TYPE}}`, `{{MODE}}`, `{{DESCRIPTION}}`, `{{DATE}}` (today, ISO), and
`{{FRAMEWORK_VERSION}}` (from `uv run -m solaris.tools.version current`).

The project root is intentionally minimal: `AGENTS.md` + a one-line `CLAUDE.md` (`@AGENTS.md`, copied from
the template) plus `ai/` and (local mode) `source/`. There is no `.cursor/`, no `mcp.json.example`, and no
`.gitignore` - the folder is not committed. Cursor reads `AGENTS.md` natively; Claude Code reads the
`CLAUDE.md` shim. If a project type adds a `source/AGENTS.md`, drop a sibling `source/CLAUDE.md` (`@AGENTS.md`) too.
Copied files keep their `_Rev. N_` rev markers (first line). For **embedded** mode the destination is the
repo root `projects/<slug>/<repo>/` (and the template's `source/` stub is dropped) - see step 4.

## 4. Wire the Code Location by Mode

- **local:** keep `source/`; `git init -b main` inside `source/` is deferred to the engineer agent (never commit
  yet).
- **remote-code:** delete `source/`; write `projects/<slug>/remote.json`:
  ```json
  { "_comment": "do not edit by hand", "mode": "remote-code", "host": "<HOST>", "path": "<REMOTE_PATH>",
    "deploy": false, "sync": { "excludes": [".venv", ".git", "__pycache__", "outputs/", "logs/"] } }
  ```
  Set `project.mode` to `remote-code` in `ai/manifest.json`.
- **embedded:** the code repo lives at `projects/<slug>/<repo>/` (e.g. `source`; an existing repo, or one
  you `git init` there) and holds the **whole** project - code, `ai/`, `AGENTS.md` + `CLAUDE.md`, `README`,
  dotfiles. Materialize `AGENTS.md` + `CLAUDE.md` + `ai/` at that repo's root (not at `projects/<slug>/`);
  keep any non-repo local aux (`references/`, `screenshots/`) at `projects/<slug>/` *outside* `<repo>`. Add
  `ai/memory/` **and `.secrets.env`** to the repo's `.gitignore` so no secrets/hosts are committed. Record
  `project.mode` `embedded` in `ai/manifest.json`; tools take `--dir projects/<slug>/<repo>/`.

## 5. Attach Plugins

For each chosen plugin (including any implied by a plugin-provided type), run `install-plugin` (install):
it copies the plugin's `shared/` into `ai/<name>/`, merges its `mcps.json` servers into the project runtime
MCP (step 7), runs the plugin's `setup` from `manifest.json` (prompts for resources -> `ai/memory/`), and
records `{name, version}` in `ai/manifest.json` -> `plugins`.

## 6. Seed Spec, Manifest, Revisions Baseline

- Short spec dialogue (purpose, components, constraints) -> `ai/spec.md`; copy it verbatim to
  `ai/memory/spec-v0.md`.
- The copied `ai/memory/` already includes a fresh `context.md` (the session-context summary, with
  `{{NAME}}` substituted); leave its `## Session Context` empty for the engineer to fill at a save point.
- Ensure `ai/manifest.json` has `project.{name,slug,type,mode}`, `framework_version`, and `plugins`.
- Record the **revisions baseline**: `uv run -m solaris.tools.revs baseline --dir projects/<slug>` writes
  the `revisions` map (per materialized file: rev + content hash), so future `update-project` runs can tell
  whether the user edited a file.

## 7. Runtime MCP (Gitignored)

Write the project's `.mcp.json` and `.cursor/mcp.json` from the framework root `mcp.json.example`
(`mcpServers`); plugin install (step 5) merges any plugin servers into both. Verify with
`uv run -m solaris.tools.mcp_sync --dir projects/<slug> --check`. Both runtime files are gitignored.

## 8. Stop + Hand Off

Print what was created. **Do not** generate source or enter planning. Tell the user:
"Run `develop-project <slug>` to plan and build." Append one line to `memory/interactions.jsonl`.
