# visual-qa migrations

Plugin-scoped migrations adapt the materialized copy (`projects/<slug>/ai/visual-qa/`) when this plugin's
`version` (in `../manifest.json`) advances. Same shape as framework migrations: one `<to_version>.md` per
target version with frontmatter; no registry file. Applied via `install-plugin` (migrate), driven by
`update-project`.

Migrations: `0.2.0.md` (instance registry, native video, watch tool, serving lifecycle scripts).
