---
name: health-check
triggers: ["health-check", "health", "status", "doctor", "what's the state", "health-check --deep"]
summary: Command-center overview (default) + deeper health checks (--deep). Read-only; suggests fixes.
---

# health-check <!-- omit in toc -->

- [Default (Status Overview)](#default-status-overview)
- [--deep (Health Checks)](#--deep-health-checks)

Read-only. The default run is the **status summary**; `--deep` adds full health checks. Never applies a fix
itself - it prints the exact command to run. The orchestrator runs the overview to orient **before working
on a project** (the first `develop-project` of a session, per `AGENTS.md`); it is also available any time on
demand.

## Default (Status Overview)

1. **Projects** - enumerate with the two-depth scan (`projects/*/` and `projects/*/*/` - projects are
   grouped, e.g. `projects/nv/<slug>/`; a project folder is one holding `ai/manifest.json`, or
   `<repo>/ai/manifest.json` in embedded mode). For each: read `ai/manifest.json` and show `name`, `type`, `mode`,
   `framework_version`, attached plugins, and workspaces (`project.workspaces` when present). (An **embedded**-mode project keeps its ai-pack one level deeper, inside the repo - use the repo
   root `projects/<slug>/<repo>/` (the directory holding `ai/`) as `--dir` in the checks below; the
   tools resolve `ai/manifest.json` and `.version` relative to it.)
2. **Revisions** - `uv run -m solaris.tools.revs status` (framework files changed without a rev bump); per
   project, `uv run -m solaris.tools.revs classify --dir projects/<slug>` flags files needing sync/merge
   (fix: `update-project <slug>`).
3. **Versions** - `uv run -m solaris.tools.version check --dir projects/<slug>` flags any pending
   minor/major migration.
4. **Tasks** - the most recent `tasks/<YYYY>/<MM>/<date>-<slug>/` folders, with the first line of each
   `notes.md`.
5. **MCP** - `uv run -m solaris.tools.mcp_sync --check` at the root (fix: `mcp_sync --sync`).
6. **Framework** - `uv run -m solaris.tools.version current`.
7. **Interaction log** - spot-check the tail of `.memory/interactions.jsonl`: recent meaningful turns should
   carry agent-authored `{ts, project, prompt, request, outcome}` entries, not just the hook's
   `{ts, cwd, ide, prompt}` backstop lines. If full entries are missing for substantive turns, flag it and
   author them by hand (never run `log_interaction` as a CLI). Likewise nudge if `.memory/instructions.md`
   looks stale given recent lessons.

Print a compact table; end with any recommended actions.

## --deep (Health Checks)

Everything above, plus:

- **venv** - `.venv/` exists (else `uv sync`).
- **Docs** - `uv run -m solaris.tools.toc --check --all` (every Markdown file has a current TOC).
- **Per project** - `revs classify --dir projects/<slug>` (sync/merge drift); `mcp_sync --check --dir
  projects/<slug>`; `version check-plugins --dir projects/<slug>` (recorded vs source plugin semver);
  `version project --dir projects/<slug>` (root `.version` present + valid semver; missing = a pre-0.29
  pack - flag it, the 0.29.0 migration seeds it);
  confirm `AGENTS.md` exists and, for each attached plugin, `ai/plugins/<plugin>/` is present (legacy
  pre-0.28 packs: `ai/<plugin>/` until migrated) - or, for a
  **linked** plugin (`"mode": "link"`), `ai/plugins/<name>.link.md` exists and its path resolves to
  `plugins/<name>/`. (A project root carries only `AGENTS.md`, `.version`, and the runtime `.mcp.json`/`.cursor/mcp.json`.)
- **Plugins** - each attached plugin has a source under `plugins/<name>/` (else it cannot be updated;
  for a linked plugin a missing source is a hard break - it has no materialized copy to fall back on).
- **gitignore sanity** - `.mcp.json`, `.cursor/mcp.json`, `projects/`, `tasks/`, `.memory/*`,
  `plugins/*` (the last two except `.empty`) are ignored; confirm no `credentials.md` is tracked.

Report findings grouped as OK / warnings / suggested fixes. Apply nothing without the user asking.
