# kaggle migrations

Plugin-scoped migrations adapt the materialized copy (`projects/<slug>/<pack>/plugins/kaggle/`, where `<pack>` is
`ai` or a renamed pack folder) when this plugin's `version` (in `../manifest.json`) advances. Same shape as
framework migrations: one `<to_version>.md` per target version with frontmatter; no registry file. Applied via
`install-plugin` (migrate), driven by `update-project`.

Migrations: `0.2.0.md` (the gateway moved to `tools/kaggle.py`, the gateway skill renamed `kaggle-cli.skill.md`,
the leaderboard history and account sharing tools added).
