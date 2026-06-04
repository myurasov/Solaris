---
name: import-plugin
triggers: ["create plugin", "make a plugin from <project>", "update plugin from my edits", "import plugin"]
summary: Author a plugin from a project's domain specifics, or fold project-local edits back into a plugin.
---

# import-plugin <!-- omit in toc -->

- [Mode A - update-from-project](#mode-a---update-from-project)
- [Mode B - create-from-aipack](#mode-b---create-from-aipack)

Two modes. Both keep the source plugin at `plugins/<name>/` as the editing source of truth; the materialized
copy in `ai/<name>/` is disposable (wholesale-overwritten on update).

## Mode A - update-from-project

The user edited the materialized copy `projects/<slug>/ai/<name>/`. Capture it:

1. Diff `projects/<slug>/ai/<name>/` against `plugins/<name>/shared/`.
2. Show the changes; confirm with the user.
3. Apply them into `plugins/<name>/shared/` and `revs bump` each changed shared file (the rev tracks the
   change; the plugin `version` semver is bumped only at release/publish, not per edit). Mirror any
   rename/removal in `shared/`, then `revs ledger`.
4. Re-record the project's revisions baseline: `uv run -m solaris.tools.revs baseline --dir projects/<slug>`.

This is the mechanism behind the **pre-overwrite check**: `install-plugin` (update) calls Mode A before it
overwrites `ai/<name>/`, so local edits are never lost.

## Mode B - create-from-aipack

Build a **new** `plugins/<name>/` from domain-specific content in an existing project's ai-pack (or an
external `__ai/`-style setup):

1. Read the source (e.g. an imported project's `ai/`, or files the user points at). Separate **domain /
   employer / repo-specific** material (NVBugs workflow, house git/PR conventions, CI specifics, a domain
   MCP) from **generic** dev preferences (which stay in `engineer.instructions.md`).
2. Show the proposed split and the plugin name; confirm.
3. Create `plugins/` if needed, write the plugin to `plugins/<name>/`, then delete the `plugins/.empty`
   placeholder (the directory now has content). Flat; only `migrations/` may be a subfolder:
   - `manifest.json` - `name`, `version` (semver), `description`, `applies_to.markers`, and an optional
     `setup` (notes + resource prompts that `install-plugin` runs on attach). No per-plugin install skill -
     the framework `install-plugin` drives the whole lifecycle.
   - `mcps.json` - any MCP servers the workflow needs (merged into a project on install).
   - `shared/*.skill.md` (procedures) and `shared/*.rule.md` (always-on conventions, domain knowledge
     folded in). `shared/` is the only part copied into a project. Give every new `shared/*` file a rev
     marker (`revs bump`), then rebuild the ledger (`revs ledger`).
   - `<type>.project.md` (optional) - project-type descriptions this plugin contributes to `create-project`.
   - `migrations/` (created when the plugin first needs one).
4. Offer to attach it to the current project via `install-plugin` (install).

Invoked standalone, or by `import-project` step 4 when a codebase shows domain markers no existing plugin
covers.
