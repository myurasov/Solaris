---
name: health-check
triggers: ["health-check", "health", "status", "doctor", "what's the state", "health-check --deep"]
summary: Command-center overview (default) + deeper health checks (--deep). Read-only; suggests fixes.
---

# health-check <!-- omit in toc -->

- [Default (status overview)](#default-status-overview)
- [--deep (health checks)](#--deep-health-checks)

Read-only. The default run is the **status summary**; `--deep` adds full health checks. Never applies a fix
itself - it prints the exact command to run. The orchestrator runs the overview to orient **before working
on a project** (the first `develop-project` of a session, per `AGENTS.md`); it is also available any time on
demand.

## Default (status overview)

1. **Projects** - for each `projects/<slug>/`: read `ai/manifest.json` and show `name`, `type`, `mode`,
   `framework_version`, attached plugins. (An **embedded**-mode project keeps its ai-pack one level deeper at
   `projects/<slug>/<repo>/ai/` - use that path as `--dir` in the checks below.)
2. **Revisions** - `uv run -m solaris.tools.revs status` (framework files changed without a rev bump); per
   project, `uv run -m solaris.tools.revs classify --dir projects/<slug>` flags files needing sync/merge
   (fix: `update-project <slug>`).
3. **Versions** - `uv run -m solaris.tools.version check --dir projects/<slug>` flags any pending
   minor/major migration.
4. **Tasks** - the most recent `tasks/<date>-<slug>/` folders, with the first line of each `notes.md`.
5. **MCP** - `uv run -m solaris.tools.mcp_sync --check` at the root (fix: `mcp_sync --sync`).
6. **Framework** - `uv run -m solaris.tools.version current`.
7. **Interaction log** - spot-check the tail of `memory/interactions.jsonl`: recent meaningful turns should
   carry agent-authored `{ts, project, prompt, request, outcome}` entries, not just the hook's
   `{ts, cwd, ide, prompt}` backstop lines. If full entries are missing for substantive turns, flag it and
   author them by hand (never run `log_interaction` as a CLI). Likewise nudge if `memory/instructions.md`
   looks stale given recent lessons.

Print a compact table; end with any recommended actions.

## --deep (health checks)

Everything above, plus:

- **venv** - `.venv/` exists (else `uv sync`).
- **Docs** - `uv run -m solaris.tools.toc --check --all` (every Markdown file has a current TOC).
- **Per project** - `revs classify --dir projects/<slug>` (sync/merge drift); `mcp_sync --check --dir
  projects/<slug>`; `version check-plugins --dir projects/<slug>` (recorded vs source plugin semver);
  confirm `AGENTS.md` exists and `ai/<plugin>/` is present for each attached plugin. (A project root carries
  only `AGENTS.md` plus the runtime `.mcp.json`/`.cursor/mcp.json`.)
- **Plugins** - each attached plugin has a source under `plugins/<name>/` (else it cannot be updated).
- **gitignore sanity** - `.mcp.json`, `.cursor/mcp.json`, `projects/`, `tasks/`, `memory/*`,
  `plugins/*` (the last two except `.empty`) are ignored; confirm no `credentials.md` is tracked.

Report findings grouped as OK / warnings / suggested fixes. Apply nothing without the user asking.
