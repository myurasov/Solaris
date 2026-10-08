# kaggle migrations

Plugin-scoped migrations adapt the materialized copy (`projects/<slug>/<pack>/plugins/kaggle/`, where `<pack>` is
the project's ai-pack folder: default `aipack/`, `ai/` in older projects, any name) when this plugin's `version` (in
`../manifest.json`) advances. Same shape as framework migrations: one `<to_version>.md` per target version with
frontmatter; no registry file. Applied via `install-plugin` (migrate), driven by `update-project`.

Migrations: `0.2.0.md` (the gateway moved to `tools/kaggle.py`, the gateway skill renamed `kaggle-cli.skill.md`,
the leaderboard history and account sharing tools added); `0.7.0.md` (from the live plan and the phase progress
reports to the single status page, `reports/status.pdf`); `0.8.0.md` (the playbook split into a core, kind skills and
a kernel skill; the hourly STATUS block and its flags).
