---
name: install-plugin
triggers: ["install plugin <git url|folder|zip>", "install the <name> plugin", "repair plugin <name>", "add plugin <X> to <project>", "update plugin <X> in <project>"]
summary: The plugin lifecycle skill - acquire a plugin (its own repo) from git/folder/zip into plugins/, validate/repair it, and install/update/migrate/repair it in a project.
---

# install-plugin <!-- omit in toc -->

- [1. Inputs](#1-inputs)
- [2. Acquire the source into plugins/](#2-acquire-the-source-into-plugins)
- [3. Validate and repair the plugin source](#3-validate-and-repair-the-plugin-source)
- [4. Scope: plugins-only, or attach to a project](#4-scope-plugins-only-or-attach-to-a-project)
- [5. Update, migrate, repair an attached plugin](#5-update-migrate-repair-an-attached-plugin)
- [6. Report](#6-report)

The single **plugin lifecycle** skill. It acquires a plugin (each plugin is its **own repository**) from a
git URL, a local folder, or a source zip into `plugins/<name>/`, validates/repairs it, and - for a named
project - installs / updates / migrates / repairs it. There is **no per-plugin install skill**; this
generic skill drives every plugin, reading plugin-specific setup from the plugin's `manifest.json`
(`setup`). Distinct from `import-plugin`, which *authors* a new plugin or folds project edits back.

## 1. Inputs

- **source** (one of): a git URL (`https://...` / `git@...`), a local folder, or a `.zip` (e.g. a GitHub
  "Download ZIP" source archive). For repair/update of an installed plugin, the source is the existing
  `plugins/<name>/`.
- **project** (optional): a project slug to install into / update / repair.
- **name** (optional): derive from the git repo / folder / zip top-level dir, then confirm against the
  plugin's `manifest.json` `name`.

## 2. Acquire the source into plugins/

If `plugins/<name>/` does **not** exist, create `plugins/` if needed and materialize the source there (then
delete the `plugins/.empty` placeholder, since the directory now has real content):

- **git URL:** `git clone <url> plugins/<name>` (keeps its own history/remote).
- **local folder:** copy it to `plugins/<name>/` (keep `.git` unless the user wants a clean copy); never
  move/delete the original.
- **zip:** unzip to a temp dir; a GitHub source zip has one top-level `<repo>-<ref>/` - copy its contents
  into `plugins/<name>/`.

If `plugins/<name>/` **already exists**, do not clobber it: go to step 3, and for a git source offer
`git -C plugins/<name> pull`.

## 3. Validate and repair the plugin source

A plugin repo's layout (flat; only `migrations/` is a subfolder): `manifest.json` (valid JSON, `name` +
`version`, optional `setup`), optional `mcps.json`, `shared/` with `*.skill.md` / `*.rule.md`, optional
`<type>.project.md`, optional `migrations/`. Then **repair** anything off (this is also the standalone
"repair a plugin already in `plugins/` but not referenced correctly" path):

- Every `shared/*` file carries a rev marker - else `uv run -m solaris.tools.revs bump <file>`.
- Refresh ledgers: `uv run -m solaris.tools.revs ledger` writes the plugin's **own** `plugins/<name>/revisions.json` (the framework `solaris/revisions.json` never tracks plugins).
- Fix missing `manifest.json` fields (ask for `name`/`version` if unknown).
- Ensure every `*.md` has a TOC: `uv run -m solaris.tools.toc --write plugins/<name>/**/*.md`.

## 4. Scope: plugins-only, or attach to a project

- **No project named:** stop after step 3 - the plugin source is in `plugins/<name>/`, available to attach
  later.
- **Project named, plugin already present in `plugins/`:** do **not** re-acquire. Run **`health-check`** to
  validate (`revs status`; for the project `revs classify --dir projects/<slug>`, `version check-plugins`,
  `mcp_sync --check`). Report problems + the fix. If valid but not yet attached, attach it (below).
- **Project named, plugin absent / not yet attached -> install:**
  1. Copy `shared/*` into `projects/<slug>/ai/<name>/`.
  2. Merge the plugin's `mcps.json` `mcpServers` into the project runtime MCP (`.mcp.json` +
     `.cursor/mcp.json`); verify `mcp_sync --check`.
  3. Run the plugin's **`setup`** (from `manifest.json`): surface each `setup.notes` line; for each
     `setup.resources` entry, prompt (`prompt`, with `default`) and write the answer into
     `ai/memory/resources.md` (or `credentials.md` if `secret: true`).
  4. Record `{name, version}` in `ai/manifest.json` -> `plugins`, then
     `uv run -m solaris.tools.revs baseline --dir projects/<slug>`.

## 5. Update, migrate, repair an attached plugin

For a plugin already attached to a project (driven here or by `update-project`):

- **update** (source advanced): `uv run -m solaris.tools.revs classify --dir projects/<slug>`. For any
  `ai/<name>/` file with verdict `merge-up` or `conflict`, resolve first (`import-plugin`
  update-from-project for `merge-up`; smart-merge + ask for `conflict`). Then `revs ff` the safe files,
  re-merge `mcps.json`, and bump the recorded plugin `version` only on a minor/major plugin release.
- **migrate** (plugin minor/major bump with `migrations/`): apply `plugins/<name>/migrations/<to>.md`
  against `ai/<name>/`, then update the recorded version.
- **repair** (attached but broken): `revs ff` restores missing files, re-merge `mcps.json`, then
  `revs baseline`.

## 6. Report

Summarize: source, name + version, what was repaired, and (if a project was named) the install / update /
health-check result. Log one line to `memory/interactions.jsonl`.
