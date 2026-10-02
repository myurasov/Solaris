---
name: update-project
triggers: ["update <project>", "sync <project>", "migrate <project>", "update-project <slug>"]
antitriggers: ["update solaris", "sync solaris", "refresh solaris"]
summary: Sync an ai-pack with framework/plugin master copies by per-file revision; run minor/major migrations.
---

# update-project <!-- omit in toc -->

1. [Classify + Sync Files by Revision](#1-classify--sync-files-by-revision)
2. [Apply Minor/Major Migrations](#2-apply-minormajor-migrations)
3. [Update Plugins](#3-update-plugins)
4. [Rename the Pack Folder (Optional)](#4-rename-the-pack-folder-optional)
5. [Summary + Revert](#5-summary--revert)

Bring a project's ai-pack in sync with the current framework + plugin master copies. **Routine sync is
per-file (revisions); semantic-version migrations run only for minor/major framework bumps.** Never touches
the project's `source/` code. `<pack>/` is the project's ai-pack folder (default `aipack/`, `ai/` in projects
made before 0.39.0, any name).

## 1. Classify + Sync Files by Revision

`uv run -m solaris.tools.revs classify --dir projects/<slug>` gives a per-materialized-file verdict (the
template's `engineer.agent.md` maps onto the project's primary persona file `<pack>/<primary>.agent.md`, per the
manifest's `agents.primary`):

- **in-sync / fast-forward / missing** -> `uv run -m solaris.tools.revs ff --dir projects/<slug>` applies
  them (copies master -> project, updates the baseline). Files the user never touched just move forward.
- **merge-up** (user rev > master): the project changed the materialized copy (projects should suggest such
  changes in `<pack>/.memory/improvements.md` instead). Show the diff and fold it up only on the owner's yes: for a
  plugin file, run `import-plugin` (update-from-project) to fold it into the plugin source and bump the master;
  for a core template file, copy the change up into `solaris/templates/ai-pack/...` and `revs bump` it.
  Re-run ff. Without that yes, ask before overwriting the project's version.
  Exception: a **project-customized pack stub** (an `init.skill.md`/`refresh.skill.md` filled in with that
  project's real resources and steps) classifies merge-up permanently - that is correct and safe (ff never
  touches it); keep the project side and do NOT fold project-specific content into the template.
- **conflict** (both changed): show a 3-way view (baseline / master / project) and ask the user, per file or
  hunk, which side wins; write the merged result; `revs bump` the master if it changed.

If `<pack>/directions.md` exists (packs made before the directions log became private), move it to
`<pack>/.memory/directions.md`, appending it to an existing one; the project's own session then folds the
long-standing entries into `<pack>/instructions.md` and commits the removal.

If `<pack>/.memory/improvements.md` is missing (packs made before it existed), move a stray
`<pack>/improvements.md` or `<pack>/lessons.md` there if one exists (the project's own session then commits
the removal), else seed it from the template's `.memory/improvements.md` with `{{NAME}}` filled; never
overwrite an existing one, which is the project's own record.

Finish by re-recording the baseline: `uv run -m solaris.tools.revs baseline --dir projects/<slug>`.

## 2. Apply Minor/Major Migrations

`uv run -m solaris.tools.version check --dir projects/<slug>` (0 = up to date, 1 = migrate, 2 = downgrade).
A migration exists only when a **minor/major** framework bump needed one (patch never does). If so,
`version chain` lists the steps; apply each `solaris/migrations/<to_version>.md` in order (Pre-flight /
Migrate / Validate), then `version set --dir projects/<slug> <to_version>`. On failure, stop at the last
good step and surface its Revert. Migrations written before 0.39.0 say `ai/`; read that as the project's
`<pack>/`.

## 3. Update Plugins

Step 1's revisions sync already reconciled each `<pack>/plugins/<plugin>/`. Additionally, for any plugin with a
minor/major bump that shipped `migrations/`, run `install-plugin` (migrate) to apply
`plugins/<name>/migrations/` and record the new plugin version in `<pack>/manifest.json`.

**Linked** plugins (`"mode": "link"` entries) need no sync or migration - they always run the live source;
see `install-plugin` step 5 (the canonical link-mode definition). The revs tools skip them in step 1
automatically.

## 4. Rename the Pack Folder (Optional)

Only on request: nothing renames a pack automatically, and a project made before 0.39.0 may keep `ai/`.
The new name is any plain folder name not already used at the project root (no slash, backslash,
whitespace, `*`, `?` or `[`, and not starting with `.`, `#` or `!`).

1. Bring the pack in sync first, so the baseline is current:
   `uv run -m solaris.tools.revs ff --dir projects/<slug>`, resolve anything it reports as in section 1,
   then `uv run -m solaris.tools.revs baseline --dir projects/<slug>`.
2. Add ignore entries for the **new** folder name (in `.gitignore`, `.stignore` and `.git/info/exclude`,
   wherever the old name has one - such as `<name>/.memory/` beside `<pack>/.memory/`), and keep the old
   entries until the rename is done.
3. `uv run -m solaris.tools.agents --rename-pack <name> --dir projects/<slug>` moves `<pack>/` to
   `<name>/`, rewrites the pack paths in the project-root `AGENTS.md` and `CLAUDE.md`, and lists other files
   that still mention the old name - review that list and update the references that mean the pack. It
   refuses (exit 1), moving nothing, while the new folder's `.memory/` would not be ignored (the private
   files, credentials included, would reach git), and likewise for anything else git ignores in the pack
   now, for files git keeps now that a rule would start ignoring, and for a `.stignore` that names only the
   old folder; if a step after the move fails, it puts everything back.
4. `uv run -m solaris.tools.revs ff --dir projects/<slug>` re-renders `{{PACK}}` in the managed files;
   merge anything it reports (`merge-up` / `conflict`, resolved as in section 1 above), then
   `uv run -m solaris.tools.revs baseline --dir projects/<slug>`.
5. Remove the old ignore entries (the rename's last output line lists the ones it found).

## 5. Summary + Revert

Report what synced, what merged, and any versions set. Run
`uv run -m solaris.tools.agents --check --dir projects/<slug>` too (personas and the shared
`<pack>/instructions.md`; it flags pre-0.37 layout leftovers). `revs ff` is idempotent; migrations
revert via their Revert section. Log the turn with
`uv run -m solaris.tools.interactions add --dir projects/<slug> ...`.
