---
name: update-project
triggers: ["update <project>", "sync <project>", "migrate <project>", "update-project <slug>"]
summary: Sync an ai-setup with framework/plugin master copies by per-file revision; run minor/major migrations.
---

# update-project <!-- omit in toc -->

- [1. Classify + sync files by revision](#1-classify--sync-files-by-revision)
- [2. Apply minor/major migrations](#2-apply-minormajor-migrations)
- [3. Update plugins](#3-update-plugins)
- [4. Summary + revert](#4-summary--revert)

Bring a project's ai-setup in sync with the current framework + plugin master copies. **Routine sync is
per-file (revisions); semantic-version migrations run only for minor/major framework bumps.** Never touches
the project's `src/` code.

## 1. Classify + sync files by revision

`uv run -m solaris.tools.revs classify --dir projects/<slug>` gives a per-materialized-file verdict:

- **in-sync / fast-forward / missing** -> `uv run -m solaris.tools.revs ff --dir projects/<slug>` applies
  them (copies master -> project, updates the baseline). Files the user never touched just move forward.
- **merge-up** (user rev > master): the user improved the materialized copy. For a plugin file, run
  `import-plugin` (update-from-project) to fold it into the plugin source and bump the master; for a core
  template file, copy the improvement up into `solaris/templates/ai-setup/...` and `revs bump` it. Re-run ff.
- **conflict** (both changed): show a 3-way view (baseline / master / project) and ask the user, per file or
  hunk, which side wins; write the merged result; `revs bump` the master if it changed.

Finish by re-recording the baseline: `uv run -m solaris.tools.revs baseline --dir projects/<slug>`.

## 2. Apply minor/major migrations

`uv run -m solaris.tools.version check --dir projects/<slug>` (0 = up to date, 1 = migrate, 2 = downgrade).
A migration exists only when a **minor/major** framework bump needed one (patch never does). If so,
`version chain` lists the steps; apply each `solaris/migrations/<to_version>.md` in order (Pre-flight /
Migrate / Validate), then `version set --dir projects/<slug> <to_version>`. On failure, stop at the last
good step and surface its Revert.

## 3. Update plugins

Step 1's revisions sync already reconciled each `ai/<plugin>/`. Additionally, for any plugin with a
minor/major bump that shipped `migrations/`, run `install-plugin` (migrate) to apply
`plugins/<name>/migrations/` and record the new plugin version in `ai/manifest.json`.

## 4. Summary + revert

Report what synced, what merged, and any versions set. `revs ff` is idempotent; migrations revert via their
Revert section. Log one line to `memory/interactions.jsonl`.
