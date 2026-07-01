# visual-qa migrations

Plugin-scoped migrations adapt the materialized copy (`projects/<slug>/ai/visual-qa/`) when this plugin's
`version` (in `../manifest.json`) advances. Same shape as framework migrations: one `<to_version>.md` per
target version with frontmatter; no registry file. Applied via `install-plugin` (migrate), driven by
`update-project`.

There are no plugin migrations yet - 0.1.0 is the base version.
