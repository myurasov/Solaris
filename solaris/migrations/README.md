# Framework migrations <!-- omit in toc -->

- [Layout](#layout)
- [Authoring a migration](#authoring-a-migration)
- [Rules](#rules)

Migrations adapt an existing project **ai-setup** (`projects/<slug>/ai/`) to a newer framework version.
They never touch the project's `src/` code. The user-facing entry point is the
[`update-project`](../skills/update-project.skill.md) skill; the policy is in
[`spec/spec-v0.2.0.md`](../spec/spec-v0.2.0.md) ("Versioning: revisions + semver"). Routine ai-setup file
changes are handled by per-file **revisions** (`solaris.tools.revs`); migrations are only for minor/major
semantic-version bumps that change structure.

## Layout

```
solaris/migrations/
  README.md            # this file
  template.md          # copy this to author a new migration
  <to_version>.md      # one per target version (e.g. 0.2.0.md), with frontmatter
  <to_version>/        # OPTIONAL: helper scripts (migrate.py / revert.py / validate.py)
```

There is **no registry file** - `solaris.tools.version` scans `*.md` frontmatter directly to compute the
chain (`README.md` and `template.md` are skipped). This is fine at v0's migration count; a generated index
can be reintroduced later if it grows.

## Authoring a migration

1. Bump `version` in `pyproject.toml` (a release). Author a migration only for **MINOR/MAJOR** bumps that
   change the ai-setup structure; **PATCH never needs one**, and routine file evolution is handled by
   revisions, not migrations.
2. Copy `template.md` to `<to_version>.md` and fill in the frontmatter + the Summary / Pre-flight / Migrate
   / Validate / Revert sections.
3. (If needed) add `<to_version>/migrate.py` (+ `revert.py`, `validate.py`) run via `uv run`.
4. Verify the chain: `uv run -m solaris.tools.version chain --dir <some-project>`.
5. Run the tests: `uv run pytest`.

## Rules

- One file per `to_version`; the skill applies them one at a time (chained).
- Idempotent: re-running over an already-migrated ai-setup is a no-op.
- Revertible by default; mark `revertible: false` only for genuinely irreversible changes.
- Migrations modify `ai/` only (excluding `ai/<plugin>/`, which each plugin's own `migrations/` owns).
- `touches:` lists every path the migration writes/creates/deletes.

The first migration is [`0.2.0.md`](0.2.0.md) (0.1.0 -> 0.2.0).
