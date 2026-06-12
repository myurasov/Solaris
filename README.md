# Solaris <!-- omit in toc -->

- [Requirements](#requirements)
- [Setup](#setup)
- [Using Solaris](#using-solaris)
- [Layout](#layout)
- [Development](#development)

Run many coding projects from one command center. Solaris generates a standardized, portable **ai-pack**
for each project (works in Cursor and Claude Code), supports domain **plugins**, deploys to remote hosts,
migrates project setups forward as the framework evolves, and keeps a lightweight `tasks/` area for ad-hoc
engineering, system-setup, and research.

Authoritative spec: [`solaris/spec/spec-v0.10.0.md`](solaris/spec/spec-v0.10.0.md).

## Requirements

- [uv](https://docs.astral.sh/uv/) (manages Python 3.14 + the venv).
- Cursor or Claude Code.
- Node.js (only for the optional Playwright MCP / `npx`).

## Setup

```bash
# 1. dependencies + venv (Python 3.14)
uv sync

# 2. MCP config: copy the template to both runtime configs, then keep them in sync
cp mcp.json.example .mcp.json
mkdir -p .cursor && cp mcp.json.example .cursor/mcp.json
uv run -m solaris.tools.mcp_sync --check

# 3. (optional) enable the commit-policy git hook
git config core.hooksPath .githooks
```

`.venv/`, `.tmp/`, `.tools/`, and the runtime MCP configs are created lazily / gitignored. Open the repo
root in your IDE - `AGENTS.md` drives the agent in both Cursor and Claude Code (Cursor reads it natively; Claude Code reads a one-line
`CLAUDE.md` that imports it via `@AGENTS.md`).

## Using Solaris

Talk to the agent in natural language; it routes to a skill. Triggers:

| Ask for | Skill |
|---|---|
| "create a project ..." | `create-project` |
| "import / adopt `<path or host:path>`" | `import-project` |
| "create / update a plugin" | `import-plugin` |
| "install / repair a plugin (git/folder/zip)" | `install-plugin` |
| "work on / develop `<project>`" | `develop-project` |
| "update / migrate `<project>`" | `update-project` |
| "self-reflect / improve Solaris" | `self-reflect` |
| "new task / research X / set up Y" | `ad-hoc-task` |
| "health-check / status" | `health-check` |

Projects land under `projects/<slug>/` (gitignored); ad-hoc work under `tasks/` (gitignored).

## Layout

| Path | What |
|---|---|
| `AGENTS.md` | canonical instructions (both IDEs read it natively) |
| `.cursor/hooks.json`, `.claude/settings.json` | interaction-log hooks |
| `solaris/solaris.agent.md` | orchestrator role |
| `solaris/skills/` | skills (`*.skill.md`) |
| `solaris/rules/` | always-on rules (commits, safety) |
| `solaris/templates/` | ai-pack + project-type templates |
| `solaris/migrations/` | framework migrations |
| `solaris/tools/` | Python tools (`version`, `revs`, `mcp_sync`, `log_interaction`, `toc`) |
| `solaris/tests/` | pytest suite |
| `plugins/` | plugin sources (gitignored; ships `nvidia-isaac-lab`) |
| `memory/` | framework memory (resources, credentials, interactions) |

## Development

```bash
uv run pytest                              # run the tool tests
uv run -m solaris.tools.version current    # -> 0.8.0
uv run -m solaris.tools.revs status        # framework files vs the revision ledger
uv run -m solaris.tools.toc --check --all  # verify every Markdown TOC
```

Conventions, the plugin contract, the migration engine, and the safety/commit policies are all specified in
[`solaris/spec/spec-v0.10.0.md`](solaris/spec/spec-v0.10.0.md).
