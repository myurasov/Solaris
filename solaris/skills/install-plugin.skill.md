---
name: install-plugin
triggers: ["install plugin <git url|folder|zip>", "install the <name> plugin", "repair plugin <name>", "add plugin <X> to <project>", "add plugin <X> to this task", "update plugin <X> in <project>", "link plugin <X> to <project>", "link the <name> plugin", "unlink plugin <X>", "detach plugin <X> from <project>"]
summary: The plugin lifecycle skill - acquire a plugin (its own repo) from git/folder/zip into plugins/, validate/repair it, and install (copy or link mode, required dependencies first, optional ones offered)/update/migrate/repair it in a project (an ad-hoc task attaches per the ad-hoc-task skill).
---

# install-plugin <!-- omit in toc -->

1. [Inputs](#1-inputs)
2. [Acquire the Source into plugins/](#2-acquire-the-source-into-plugins)
3. [Validate and Repair the Plugin Source](#3-validate-and-repair-the-plugin-source)
4. [Scope: Plugins-Only, or Attach to a Project](#4-scope-plugins-only-or-attach-to-a-project)
5. [Link Mode (Development Installs)](#5-link-mode-development-installs)
6. [Update, Migrate, Repair an Attached Plugin](#6-update-migrate-repair-an-attached-plugin)
7. [Report](#7-report)

The single **plugin lifecycle** skill. It acquires a plugin (each plugin is its **own repository**) from a
git URL, a local folder, or a source zip into `plugins/<name>/`, validates/repairs it, and - for a named
project - installs / updates / migrates / repairs it. Installs come in two modes: **copy** (the default:
`shared/` is materialized into `<pack>/plugins/<name>/`) and **link** (a single
`<pack>/plugins/<name>.link.md` points at the live plugin source - used while developing a plugin, see
step 5); `<pack>/` is the project's ai-pack folder (default `aipack/`, `ai/` in projects made before
0.39.0, any name). There is **no per-plugin install skill**; this
generic skill drives every plugin, reading plugin-specific setup from the plugin's `manifest.json`
(`setup`, and `dependencies` for the plugins it relies on). Distinct from `import-plugin`, which *authors* a
new plugin or folds project edits back.

## 1. Inputs

- **source** (one of): a git URL (`https://...` / `git@...`), a local folder, or a `.zip` (e.g. a GitHub
  "Download ZIP" source archive). For repair/update of an installed plugin, the source is the existing
  `plugins/<name>/`.
- **project** (optional): a project slug to install into / update / repair.
- **name** (optional): derive from the git repo / folder / zip top-level dir, then confirm against the
  plugin's `manifest.json` `name`.
- **mode** (optional): `copy` (default) or `link` ("link plugin X to Y" / "link the X plugin"). Link mode
  attaches the live plugin source instead of copying it - see step 5. **Choosing:** `link` only while
  actively developing the plugin itself; `copy` for everything else, and always before a project is
  shared or detached (a link cannot resolve outside the Solaris tree).

**Canonical overlay layout:** `<pack>/plugins/` is the pack-side home for plugin shared files. A copy install
always materializes into the directory form `<pack>/plugins/<name>/<file>` - never flat rule/skill files at
the top of `<pack>/` or `<pack>/plugins/`. (The skill-loader overlay index tolerates flat rule files at the pack
root when *reading*, for hand-rolled project-local rules; plugin installs and the revs classifier use the
directory form only. Do not move files between the two layouts - it churns revisions for no gain.)

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

A plugin repo's layout (flat; only `migrations/` is a subfolder, plus any vendored upstream tree - a
folder of third-party files kept identical to upstream, marked by an `UPSTREAM.md` that names its source,
ref, and refresh procedure; inside `shared/` its rev markers are the only local change): `manifest.json` (valid JSON, `name` +
`version`, optional `setup`, optional `dependencies`), optional `mcps.json`, `shared/` with `*.skill.md` / `*.rule.md`, optional
`<type>.project.md` (a project type `create-project` offers as `<plugin>:<type>`) with its optional overlay
folder `project-types/<type>/`, optional `migrations/`.

The manifest's `dependencies` names the plugins this one relies on, each entry a plugin name or an object
with `name` and an optional `why` (what the dependency adds):
`"dependencies": {"required": ["<plugin>"], "optional": [{"name": "<plugin>", "why": "<what it adds>"}]}`.
Required dependencies are attached with the plugin, transitively and first (step 4); optional ones are
offered then, only those present under `plugins/`. Declare only what the plugin's own docs or tools rely on;
a cycle, or a required dependency missing from `plugins/`, is an error.

Then **repair** anything off (this is also the standalone
"repair a plugin already in `plugins/` but not referenced correctly" path):

- Every `shared/*` file carries a rev marker - else `uv run -m solaris.tools.revs bump <file>`.
- Refresh ledgers: `uv run -m solaris.tools.revs ledger --plugin <name>` rewrites only the plugin's **own** `plugins/<name>/revisions.json` (plain `revs ledger` rewrites every ledger, the framework's included; the framework `solaris/revisions.json` never tracks plugins).
- Fix missing `manifest.json` fields (ask for `name`/`version` if unknown).
- Dependencies: `uv run -m solaris.tools.plugins deps <name>` - every required dependency, transitively, must
  be under `plugins/`. Exit 3 names each missing one with its chain (or a cycle, a bug to report to the
  plugin's author): tell the owner which are missing and offer to acquire them the same way (steps 2-3).
  Optional ones not under `plugins/` are only mentioned.
- Project types: each `<type>.project.md` passes `uv run -m solaris.tools.plugins type <name>:<type>` (exit
  3 lists the problems: unknown keys, a missing section, a required plugin or an overlay folder that does
  not exist).
- Ensure every `*.md` has a TOC, apart from the type overlays (project files, copied as they are):
  `find -H plugins/<name> -name '*.md' -not -path '*/project-types/*' -exec uv run -m solaris.tools.toc --write {} +`
  (`-H` follows a symlinked private plugin; the tool leaves vendored trees untouched).

## 4. Scope: Plugins-Only, or Attach to a Project

- **No project named:** stop after step 3 - the plugin source is in `plugins/<name>/`, available to attach
  later.
- **Ad-hoc task named instead of a project** ("add plugin `<X>` to this task"): acquire/validate here
  (steps 2-3), then attach per the `ad-hoc-task` skill's "Use Plugins" section - a `Plugins:` line in the
  task's `notes.md`, shared files loaded live from `plugins/<name>/shared/` (nothing copied, no manifest,
  no revs; MCP merge and `setup` adapt as defined there), its required dependencies on the same line
  (`plugins deps <name>`). Steps 5-6 below are project-only.
- **Project named, plugin already present in `plugins/`:** do **not** re-acquire. Run **`health-check`** to
  validate (`revs status`; for the project `revs classify --dir projects/<slug>`, `version check-plugins`,
  `mcp_sync --check`). Report problems + the fix. If valid but not yet attached, attach it (below).
- **Project named, plugin absent / not yet attached -> install:**
  1. **Dependencies first:** `uv run -m solaris.tools.plugins deps <name>` lists the plugins `<name>`
     requires, transitively and dependencies first, and the optional ones of `<name>` and of those (exit 3: a
     cycle, or a required one missing from `plugins/` - acquire it first, steps 2-3, on the owner's yes, or
     stop). Attach each required one not yet in `<pack>/manifest.json` -> `plugins`, in that order, through
     sub-steps 2-5 (copy mode unless the owner says otherwise), and tell the owner which were added and what
     required them (the chain, such as `<name> > <dependency>`). Then offer the optional ones present under
     `plugins/` and not attached in one multi-select question, each with its `why`, and attach each one
     picked with its own required dependencies (`plugins deps <picked>`) the same way, asking nothing more.
     When `create-project` drives the install, its plan has settled both already: ask nothing here.
  2. Copy `shared/*` into `projects/<slug>/<pack>/plugins/<name>/`, creating `<pack>/plugins/` on the project's
     first plugin attach (**link mode:** write `<pack>/plugins/<name>.link.md` instead - step 5 - and skip
     the copy).
  3. Merge the plugin's `mcps.json` `mcpServers` into the project runtime MCP (`.mcp.json` +
     `.cursor/mcp.json`), replacing every `<pack>` in the merged entries (e.g. a command path
     `<pack>/plugins/<name>/...`) with the project's actual pack folder name and keeping any project path
     prefix the entry already uses; verify `mcp_sync --check`. Every later re-merge does the same.
  4. Run the plugin's **`setup`** (from `manifest.json`): surface each `setup.notes` line; for each
     `setup.resources` entry, prompt (`prompt`, with `default`) and write the answer into
     `<pack>/.memory/resources.md` (or `credentials.md` if `secret: true`).
  5. Record `{name, version}` in `<pack>/manifest.json` -> `plugins` (link mode: `{name, "mode": "link"}` -
     **no** `version`: a linked plugin always runs the live source, so a recorded version would only go
     stale). Then `uv run -m solaris.tools.revs ff --dir projects/<slug>` - it re-renders
     `<pack>/README.md`'s attached-plugins list from the manifest (run it after ANY change to the
     `plugins` array: attach, detach, link/copy conversion) - and
     `uv run -m solaris.tools.revs baseline --dir projects/<slug>` (both safe in both modes - the
     revs tools skip linked plugins). Last, `uv run -m solaris.tools.plugins check --dir projects/<slug>`
     must pass (exit 0: every attached plugin's required dependencies are attached).

## 5. Link Mode (Development Installs)

Link mode attaches a plugin **without copying it**: instead of `<pack>/plugins/<name>/`, the project gets a single
pointer file `<pack>/plugins/<name>.link.md` that tells the engineer agent to load the plugin's `shared/` files
directly from `plugins/<name>/`. Use it while **developing a plugin** - edits to the plugin source take
effect in the project immediately, with no copy-back-and-forth (no `import-plugin` fold-back, and no `revs`
drift: the revs tools skip `"mode": "link"` entries, so linked files are never expected in `<pack>/plugins/<name>/`).
MCP merge, `setup`, and the manifest record (with `"mode": "link"`, no `version`) still happen exactly as
in a copy install, so behavior is identical at runtime. This section is the **canonical definition** of
link mode - other skills and docs point here.

Write `<pack>/plugins/<name>.link.md` from this template (fill `<name>`, `<pack>` (the pack folder's name)
and the path; the path is **relative to the ai-pack root** - the directory holding `<pack>/`, two levels
**above** this file - and the rendered line must say so). **Compute the depth, do not copy it:** count the
levels from the ai-pack root up to the Solaris root - a grouped project `projects/<group>/<slug>/` needs
`../../../plugins/<name>/` (embedded mode adds one more `../` for `<repo>/`); verify the rendered path resolves (`ls <ai-pack root>/<path>`) before
finishing. A link file breaks silently if the project folder later moves - re-verify it on
`update-project`:

```markdown
<!-- Linked plugin: managed by install-plugin (link mode). Machine-local development pointer. -->

# Linked Plugin: <name>

This project uses the **<name>** plugin in **link mode**: nothing is copied into `<pack>/plugins/<name>/`; the live
plugin source is loaded directly. On every turn, treat the plugin as if it were materialized here:

- **Plugin root:** `<path to plugins/<name>/>` - relative to this ai-pack's root, the directory **above**
  `<pack>/` (not to this file). Shared files are in `shared/` there; the live version is in its `manifest.json`.
- Load each `shared/*.rule.md` as always-on; treat each `shared/*.skill.md` as trigger-invoked.
- Edits to those files change the **plugin source** for every consumer - edit them only when the owner
  explicitly asks you to develop the plugin; otherwise suggest changes in `<pack>/.memory/improvements.md`.

Link mode is a development convenience and is **not portable**: a shared or standalone ai-pack cannot
resolve the path. Convert to a real install ("install plugin <name> to <project>") before sharing.
```

Notes:

- The link file carries **no rev marker** (it is per-project generated content, like `remote.json`, not a
  framework/plugin master), and neither ledger tracks it.
- **Embedded** projects: add `<pack>/plugins/<name>.link.md` to the repo's `.gitignore` - the pointer is machine-local.
- `version check-plugins` reports linked plugins as `linked, source <v> (live)` - there is no recorded
  version to drift; a missing `plugins/<name>/` source is reported as a hard break (no materialized copy
  to fall back on).

**Converting and detaching** (swaps happen in place; MCP merge and recorded `setup` answers stay as they
are unless noted):

- **link -> copy** ("install plugin <name> to <project>" on a linked plugin): delete `<pack>/plugins/<name>.link.md`,
  copy `shared/*` into `<pack>/plugins/<name>/`, replace the manifest entry with `{name, version}` (the source's
  current version), then `revs baseline --dir projects/<slug>`.
- **copy -> link** ("link plugin <name> to <project>" on a copied install): first
  `revs classify --dir projects/<slug>` - fold any `merge-up`/`conflict` in `<pack>/plugins/<name>/` back into the
  plugin (`import-plugin`, on the owner's yes) so no project-local edit is lost; then delete `<pack>/plugins/<name>/` (confirm - this is
  destructive), write the link file, set the manifest entry to `{name, "mode": "link"}`, and
  `revs baseline --dir projects/<slug>` (it rebuilds the `revisions` map and drops the deleted files).
- **unlink / detach** ("unlink plugin <name>", "detach plugin <name> from <project>"): fully remove the
  attachment - delete `<pack>/plugins/<name>.link.md`, remove the plugin's entry from `<pack>/manifest.json` -> `plugins`,
  and remove its `mcps.json` servers from the project runtime MCP (unless another attached plugin also
  provides them); keep any `setup` answers already in `<pack>/.memory/`. Confirm first (destructive), and
  say so when another attached plugin requires it (`plugins check --dir projects/<slug>` would then fail,
  naming it). If the
  user instead wants the plugin kept but copied, that is **link -> copy** above - ask when ambiguous.

## 6. Update, Migrate, Repair an Attached Plugin

For a plugin already attached to a project (driven here or by `update-project`). **Linked** plugins (step
5) need none of this - they always run the live source, record no version, and migrations against
`<pack>/plugins/<name>/` do not apply since nothing is materialized:

- **update** (source advanced): `uv run -m solaris.tools.revs classify --dir projects/<slug>`. For any
  `<pack>/plugins/<name>/` file with verdict `merge-up` or `conflict`, resolve first (`import-plugin`
  update-from-project for `merge-up`, on the owner's yes; smart-merge + ask for `conflict`). Then `revs ff` the safe files,
  re-merge `mcps.json`, and set the recorded plugin `version` to the source's on every release, patch
  included (`version check-plugins` wants an exact match).
- **migrate** (plugin minor/major bump with `migrations/`): apply `plugins/<name>/migrations/<to>.md`
  against `<pack>/plugins/<name>/`, then update the recorded version.
- **repair** (attached but broken): `revs ff` restores missing files, re-merge `mcps.json`, then
  `revs baseline`.

After any of these, and for linked plugins too (a new version may declare new dependencies),
`uv run -m solaris.tools.plugins check --dir projects/<slug>` must pass: attach each required dependency it
names (step 4), and offer only the optional ones the new version added (compare with the old manifest's
`dependencies`), never again those the owner declined.

## 7. Report

Summarize: source, name + version, what was repaired, any required dependency missing from `plugins/`,
and (if a project was named) the install mode (copy / link), the dependencies attached with it and the
optional ones offered, and the install / update / health-check result. Log the turn with
`uv run -m solaris.tools.interactions add` (plus `--dir projects/<slug>` when a project was named).
