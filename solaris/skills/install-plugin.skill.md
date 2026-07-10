---
name: install-plugin
triggers: ["install plugin <git url|folder|zip>", "install the <name> plugin", "repair plugin <name>", "add plugin <X> to <project>", "update plugin <X> in <project>", "link plugin <X> to <project>", "link the <name> plugin", "unlink plugin <X>", "detach plugin <X> from <project>"]
summary: The plugin lifecycle skill - acquire a plugin (its own repo) from git/folder/zip into plugins/, validate/repair it, and install (copy or link mode)/update/migrate/repair it in a project.
---

# install-plugin <!-- omit in toc -->

- [1. Inputs](#1-inputs)
- [2. Acquire the Source into plugins/](#2-acquire-the-source-into-plugins)
- [3. Validate and Repair the Plugin Source](#3-validate-and-repair-the-plugin-source)
- [4. Scope: Plugins-Only, or Attach to a Project](#4-scope-plugins-only-or-attach-to-a-project)
- [5. Link Mode (Development Installs)](#5-link-mode-development-installs)
- [6. Update, Migrate, Repair an Attached Plugin](#6-update-migrate-repair-an-attached-plugin)
- [7. Report](#7-report)

The single **plugin lifecycle** skill. It acquires a plugin (each plugin is its **own repository**) from a
git URL, a local folder, or a source zip into `plugins/<name>/`, validates/repairs it, and - for a named
project - installs / updates / migrates / repairs it. Installs come in two modes: **copy** (the default:
`shared/` is materialized into `ai/<name>/`) and **link** (a single `ai/<name>.link.md` points at the live
plugin source - used while developing a plugin, see step 5). There is **no per-plugin install skill**; this
generic skill drives every plugin, reading plugin-specific setup from the plugin's `manifest.json`
(`setup`). Distinct from `import-plugin`, which *authors* a new plugin or folds project edits back.

## 1. Inputs

- **source** (one of): a git URL (`https://...` / `git@...`), a local folder, or a `.zip` (e.g. a GitHub
  "Download ZIP" source archive). For repair/update of an installed plugin, the source is the existing
  `plugins/<name>/`.
- **project** (optional): a project slug to install into / update / repair.
- **name** (optional): derive from the git repo / folder / zip top-level dir, then confirm against the
  plugin's `manifest.json` `name`.
- **mode** (optional): `copy` (default) or `link` ("link plugin X to Y" / "link the X plugin"). Link mode
  attaches the live plugin source instead of copying it - see step 5.

## 2. Acquire the Source into plugins/

If `plugins/<name>/` does **not** exist, create `plugins/` if needed and materialize the source there (then
delete the `plugins/.empty` placeholder, since the directory now has real content):

- **git URL:** `git clone <url> plugins/<name>` (keeps its own history/remote).
- **local folder:** copy it to `plugins/<name>/` (keep `.git` unless the user wants a clean copy); never
  move/delete the original.
- **zip:** unzip to a temp dir; a GitHub source zip has one top-level `<repo>-<ref>/` - copy its contents
  into `plugins/<name>/`.

If `plugins/<name>/` **already exists**, do not clobber it: go to step 3, and for a git source offer
`git -C plugins/<name> pull`.

## 3. Validate and Repair the Plugin Source

A plugin repo's layout (flat; only `migrations/` is a subfolder): `manifest.json` (valid JSON, `name` +
`version`, optional `setup`), optional `mcps.json`, `shared/` with `*.skill.md` / `*.rule.md`, optional
`<type>.project.md`, optional `migrations/`. Then **repair** anything off (this is also the standalone
"repair a plugin already in `plugins/` but not referenced correctly" path):

- Every `shared/*` file carries a rev marker - else `uv run -m solaris.tools.revs bump <file>`.
- Refresh ledgers: `uv run -m solaris.tools.revs ledger` writes the plugin's **own** `plugins/<name>/revisions.json` (the framework `solaris/revisions.json` never tracks plugins).
- Fix missing `manifest.json` fields (ask for `name`/`version` if unknown).
- Ensure every `*.md` has a TOC: `uv run -m solaris.tools.toc --write plugins/<name>/**/*.md`.

## 4. Scope: Plugins-Only, or Attach to a Project

- **No project named:** stop after step 3 - the plugin source is in `plugins/<name>/`, available to attach
  later.
- **Project named, plugin already present in `plugins/`:** do **not** re-acquire. Run **`health-check`** to
  validate (`revs status`; for the project `revs classify --dir projects/<slug>`, `version check-plugins`,
  `mcp_sync --check`). Report problems + the fix. If valid but not yet attached, attach it (below).
- **Project named, plugin absent / not yet attached -> install:**
  1. Copy `shared/*` into `projects/<slug>/ai/<name>/` (**link mode:** write `ai/<name>.link.md` instead -
     step 5 - and skip the copy).
  2. Merge the plugin's `mcps.json` `mcpServers` into the project runtime MCP (`.mcp.json` +
     `.cursor/mcp.json`); verify `mcp_sync --check`.
  3. Run the plugin's **`setup`** (from `manifest.json`): surface each `setup.notes` line; for each
     `setup.resources` entry, prompt (`prompt`, with `default`) and write the answer into
     `ai/memory/resources.md` (or `credentials.md` if `secret: true`).
  4. Record `{name, version}` in `ai/manifest.json` -> `plugins` (link mode: `{name, "mode": "link"}` -
     **no** `version`: a linked plugin always runs the live source, so a recorded version would only go
     stale), then `uv run -m solaris.tools.revs baseline --dir projects/<slug>` (safe in both modes - the
     revs tools skip linked plugins).

## 5. Link Mode (Development Installs)

Link mode attaches a plugin **without copying it**: instead of `ai/<name>/`, the project gets a single
pointer file `ai/<name>.link.md` that tells the engineer agent to load the plugin's `shared/` files
directly from `plugins/<name>/`. Use it while **developing a plugin** - edits to the plugin source take
effect in the project immediately, with no copy-back-and-forth (no `import-plugin` fold-back, and no `revs`
drift: the revs tools skip `"mode": "link"` entries, so linked files are never expected in `ai/<name>/`).
MCP merge, `setup`, and the manifest record (with `"mode": "link"`, no `version`) still happen exactly as
in a copy install, so behavior is identical at runtime. This section is the **canonical definition** of
link mode - other skills and docs point here.

Write `ai/<name>.link.md` from this template (fill `<name>` and the path; the path is **relative to the
ai-pack root** - the directory holding `ai/`, one level **above** this file - and the rendered line must
say so):

```markdown
<!-- Linked plugin: managed by install-plugin (link mode). Machine-local development pointer. -->

# Linked Plugin: <name>

This project uses the **<name>** plugin in **link mode**: nothing is copied into `ai/<name>/`; the live
plugin source is loaded directly. On every turn, treat the plugin as if it were materialized here:

- **Plugin root:** `<path to plugins/<name>/>` - relative to this ai-pack's root, the directory **above**
  `ai/` (not to this file). Shared files are in `shared/` there; the live version is in its `manifest.json`.
- Load each `shared/*.rule.md` as always-on; treat each `shared/*.skill.md` as trigger-invoked.
- Edits to those files change the **plugin source** for every consumer - only edit them when the user is
  deliberately developing the plugin.

Link mode is a development convenience and is **not portable**: a shared or standalone ai-pack cannot
resolve the path. Convert to a real install ("install plugin <name> to <project>") before sharing.
```

Notes:

- The link file carries **no rev marker** (it is per-project generated content, like `remote.json`, not a
  framework/plugin master), and neither ledger tracks it.
- **Embedded** projects: add `ai/<name>.link.md` to the repo's `.gitignore` - the pointer is machine-local.
- `version check-plugins` reports linked plugins as `linked, source <v> (live)` - there is no recorded
  version to drift; a missing `plugins/<name>/` source is reported as a hard break (no materialized copy
  to fall back on).

**Converting and detaching** (swaps happen in place; MCP merge and recorded `setup` answers stay as they
are unless noted):

- **link -> copy** ("install plugin <name> to <project>" on a linked plugin): delete `ai/<name>.link.md`,
  copy `shared/*` into `ai/<name>/`, replace the manifest entry with `{name, version}` (the source's
  current version), then `revs baseline --dir projects/<slug>`.
- **copy -> link** ("link plugin <name> to <project>" on a copied install): first
  `revs classify --dir projects/<slug>` - fold any `merge-up`/`conflict` in `ai/<name>/` back into the
  plugin (`import-plugin`) so no project-local edit is lost; then delete `ai/<name>/` (confirm - this is
  destructive), write the link file, set the manifest entry to `{name, "mode": "link"}`, and
  `revs baseline --dir projects/<slug>` (it rebuilds the `revisions` map and drops the deleted files).
- **unlink / detach** ("unlink plugin <name>", "detach plugin <name> from <project>"): fully remove the
  attachment - delete `ai/<name>.link.md`, remove the plugin's entry from `ai/manifest.json` -> `plugins`,
  and remove its `mcps.json` servers from the project runtime MCP (unless another attached plugin also
  provides them); keep any `setup` answers already in `ai/memory/`. Confirm first (destructive). If the
  user instead wants the plugin kept but copied, that is **link -> copy** above - ask when ambiguous.

## 6. Update, Migrate, Repair an Attached Plugin

For a plugin already attached to a project (driven here or by `update-project`). **Linked** plugins (step
5) need none of this - they always run the live source, record no version, and migrations against
`ai/<name>/` do not apply since nothing is materialized:

- **update** (source advanced): `uv run -m solaris.tools.revs classify --dir projects/<slug>`. For any
  `ai/<name>/` file with verdict `merge-up` or `conflict`, resolve first (`import-plugin`
  update-from-project for `merge-up`; smart-merge + ask for `conflict`). Then `revs ff` the safe files,
  re-merge `mcps.json`, and bump the recorded plugin `version` only on a minor/major plugin release.
- **migrate** (plugin minor/major bump with `migrations/`): apply `plugins/<name>/migrations/<to>.md`
  against `ai/<name>/`, then update the recorded version.
- **repair** (attached but broken): `revs ff` restores missing files, re-merge `mcps.json`, then
  `revs baseline`.

## 7. Report

Summarize: source, name + version, what was repaired, and (if a project was named) the install mode
(copy / link) and the install / update / health-check result. Log one line to `memory/interactions.jsonl`.
