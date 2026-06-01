---
to_version: 0.0.0
from_version: 0.0.0
title: "One-line title shown to the user before applying"
breaking: false
revertible: true
estimated_duration: "<1 minute"
touches:
  - ai/manifest.json
---

- [Summary](#summary)
- [Pre-flight checks](#pre-flight-checks)
- [Migrate](#migrate)
- [Validate](#validate)
- [Revert](#revert)

<!--
Author guide:
- Replace every frontmatter value above. `to_version` = the version this migrates TO; `from_version` = the
  immediately preceding version. Mark breaking: true for a MAJOR bump or a behaviorally breaking change.
- Mark revertible: false ONLY for genuinely irreversible changes (data loss).
- List every ai-setup path the migration writes/creates/deletes in `touches`.
- Helper scripts (optional) live under solaris/migrations/<to_version>/ and run via `uv run`.
- Migrations modify projects/<slug>/ai/ only - never the project's src/ code, and never ai/<plugin>/
  (each plugin migrates itself via its own migrations/).
-->

## Summary

Why this migration exists, what it changes, and what risk it carries. 2-4 sentences - the user sees this
before consenting.

## Pre-flight checks

What must be true before applying (the skill aborts cleanly on any miss, naming the failed check):

- [ ] `ai/manifest.json` exists and is valid JSON.
- [ ] (other migration-specific checks)

## Migrate

Numbered, executable steps. Each is one of: a direct file operation (create/rename/delete/edit), a
`uv run solaris/migrations/<to_version>/migrate.py --dir <project>` invocation, or a precise JSON/Markdown
edit (state the file, the location, and the change).

1. ...

## Validate

How to confirm success (the skill and any `validate.py` both run these):

- [ ] (validation 1)

## Revert

Numbered steps that reverse Migrate in inverse order. If `revertible: false`, replace this section with a
paragraph explaining what is irreversibly lost and what to back up first.
