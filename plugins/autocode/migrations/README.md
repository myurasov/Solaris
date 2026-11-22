# autocode migrations

Plugin-scoped migrations adapt the materialized copy (`projects/<slug>/<pack>/plugins/autocode/`, where `<pack>` is
the project's ai-pack folder: default `aipack/`, `ai/` in older projects, any name) when this plugin's `version` (in
`../manifest.json`) advances. Same shape as framework migrations: one `<to_version>.md` per target version with
frontmatter; no registry file. Applied via `install-plugin` (migrate), driven by `update-project`.

Migrations: `0.5.0.md` (the guard's check and watched background jobs in `tools/guard.py`, Start as a skill, zones
in `wakes.py`, the fleet agent bullet in the resource-sharing plugin's rule).
