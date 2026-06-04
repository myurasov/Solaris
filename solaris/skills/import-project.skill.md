---
name: import-project
triggers: ["import project", "adopt <path>", "adopt <host:path>", "bring <repo> into Solaris"]
summary: Adopt an existing codebase; derive the ai-pack (and offer to factor domain specifics into a plugin).
---

# import-project <!-- omit in toc -->

- [1. Inputs](#1-inputs)
- [2. Land the code](#2-land-the-code)
- [3. Detect type + toolchain (read-only)](#3-detect-type--toolchain-read-only)
- [4. Detect + attach plugins (uses import-plugin)](#4-detect--attach-plugins-uses-import-plugin)
- [5. Derive the ai-pack (best effort)](#5-derive-the-ai-pack-best-effort)
- [6. Ask on ambiguity](#6-ask-on-ambiguity)
- [7. Confirm + summary](#7-confirm--summary)

Reverse of `create-project`: ingest existing code, then derive the ai-pack. Import never modifies the code
- it only creates `ai/` + a minimal project root (+ `remote.json` in remote-code mode).

## 1. Inputs

`source` (local path or `host:path`), target `slug`, and `mode`. Ask for whatever is missing.

## 2. Land the code

Create `projects/` if it does not exist (gitignored, lazily created).

- **local:** if `source` already is `projects/<slug>/src/`, adopt in place. Otherwise copy/rsync `source`
  -> `projects/<slug>/src/`, excluding `.venv`, `.git` caches, `__pycache__`, `node_modules`, build
  artifacts. Preserve an existing `.git` only after confirming with the user. Never move or delete the
  original source.
- **remote-code:** do **not** copy. Write `remote.json` (`host`, `path`, `deploy: false`). Read the remote
  tree over SSH (`ssh <host> 'ls / cat ...'`) for the detection steps.

## 3. Detect type + toolchain (read-only)

Match a `templates/projects/<type>.md` from signals: `pyproject.toml`/`setup.py` -> python;
`package.json` -> node; `*.xcodeproj` -> ios; FastAPI imports -> web-service; a console entry point ->
python-cli. Read README, dependency manifests, entry points, test/build config, Dockerfile/compose, and any
committed `AGENTS.md` (or `CLAUDE.md`) in the source - treat it as project rules and leave it in place.

## 4. Detect + attach plugins (uses import-plugin)

Scan for domain markers and propose plugins. Examples: NVBugs / `isaaclab.sh` / `__nvbugs/` / a NVBugs MCP
-> suggest `nvidia-isaac-lab`. If markers clearly indicate a domain that no existing plugin covers (e.g. a
bespoke `__ai/` setup), offer `import-plugin` (create mode) to factor it into a new plugin first. For each
confirmed plugin, run `install-plugin` (install). Domain-specific knowledge maps into the plugin, **not**
into the generic `engineer.instructions.md`.

## 5. Derive the ai-pack (best effort)

- `ai/spec.md` + `ai/memory/spec-v0.md` - reconstruct the spec from code + README.
- `ai/engineer.instructions.md` - inferred **generic, shareable** build/run/test/lint commands +
  conventions (host/secret/internal-URL specifics go in `ai/memory/resources.md`/`credentials.md`, not here;
  plugins carry the domain-specific ones).
- `ai/memory/resources.md` - deploy/host hints (Dockerfile, CI, `.env.example`, remote host); else stubs.
  `ai/memory/credentials.md` - placeholders only; never copy real secrets out of the source.
- Seed `ai/memory/interactions.jsonl` (empty). Write `ai/manifest.json`
  (`project.{name,slug,type,mode}`, `framework_version` from `version current`, `plugins`). Write the
  minimal project root (`AGENTS.md` only - no CLAUDE.md / `.cursor/` / `mcp.json.example` / `.gitignore`),
  the gitignored runtime MCP (`.mcp.json` + `.cursor/mcp.json` from the framework root `mcp.json.example`
  plus any plugin servers), and the revisions baseline
  (`uv run -m solaris.tools.revs baseline --dir projects/<slug>`).

## 6. Ask on ambiguity

Batch questions wherever inference is uncertain: type, mode, primary entry point, exact run/test commands,
in/out-of-scope dirs, remote host, plugins to attach, whether to keep existing git history. Never guess
load-bearing details.

## 7. Confirm + summary

Report detected vs assumed vs needs-your-eyes; point at `ai/engineer.agent.md`; suggest
`develop-project <slug>`. Log one line to `memory/interactions.jsonl`.
