_Rev. 9_

# Engineer Instructions - {{NAME}} <!-- omit in toc -->

- [Workspaces](#workspaces)
- [Build / Run / Test](#build--run--test)
- [Deploy](#deploy)
- [Local-Only Folders](#local-only-folders)
- [Remote Host Discipline](#remote-host-discipline)
- [Runtime Notes \& Gotchas](#runtime-notes--gotchas)
- [Conventions](#conventions)

Editable, project-specific notes on how to develop this project. Rewrite freely to keep the best version
(not append-only). The commit and safety policies live in `engineer.agent.md`.

**This is the "how" layer.** All procedures and project knowledge live here: build/run/test, **deploy &
restart procedures**, **model/runtime details**, architecture/layers, and **gotchas**. The only things that do
*not* live here are the inventory of *what exists* (hardware + hosts/accounts -> `ai/.memory/resources.md`),
secrets (`credentials.md`), and the session-context summary (`context.md`).

**Shareable layer.** This file sits in `ai/` alongside `engineer.agent.md` and `spec.md` - the portable,
shareable layer. Keep it free of anything environment-specific or sensitive: **no** hostnames, IPs,
internal/corporate URLs, concrete deploy targets, remote paths, or secrets - those are inventory and live in
`ai/.memory/resources.md` / `credentials.md`. Procedures still belong here, written as generic patterns
(e.g. `rsync source/ <host>:<path>`, `--host <host> --port <port>`) that **reference** `resources.md` for the
concrete values - never drop the procedure, just keep the values out of it.

## Workspaces

(Delete this section in a flat single-workspace project.) Each workspace is a self-contained top-level
folder - own `setup.md` (from-scratch bring-up ending in verification), no file references into siblings;
shared inputs live outside workspaces. New workspaces must ship a `setup.md` and be added here:

| Workspace | Purpose | Setup |
|---|---|---|
| `source/` | (default workspace) | `source/setup.md` |

## Build / Run / Test

- install: (fill in)
- run: (fill in)
- test: (fill in)
- lint: (fill in)

## Deploy

- (deploy + restart procedure as generic patterns; reference `ai/.memory/resources.md` for host/path/port)

## Local-Only Folders

Scratch that should never be tracked lives in `__`-prefixed folders, gitignored as one pattern (`__*/`):
`__research/` (working reports/visuals), `__history/` (archived/superseded content), `__out/` (pipeline
outputs) - add others as needed. **Durable conclusions get folded into this file or `ai/spec.md` before a
`__research/` report is considered done** - the folders are disposable, the lessons are not.

## Remote Host Discipline

(Delete this section if the project touches no remote hosts.)

- Concrete hosts/paths/ports live in `ai/.memory/resources.md`; procedures here reference them generically.
- Deploy with `rsync` (excludes per the safety policy: `.venv`, `.git`, secrets, build artifacts; no
  `--delete` by default). Create the remote parent first (`ssh <host> mkdir -p <parent>`) - some rsync
  builds (macOS openrsync) do not create nested remote dirs.
- **Stream remote output live** (run in a tmux session and read the screen with `capture-pane`, or stream
  to the terminal) rather than redirecting to a file and polling it.
- Leave the host as you found it: stop what you started, and keep any footprint under one project dir.

## Runtime Notes & Gotchas

- (model/runtime details, performance notes, and gotchas worth never relearning)
- Sandbox ladder + name-block wrappers: policy lives in `engineer.agent.md` (Sandboxed Harnesses); under
  a Solaris checkout, `solaris.agent.md` carries the framework-wide version with the current known-hard
  denial list. Harness specifics seen so far: Codex `approval_policy = "on-request"` grants per-command
  escalation; Cursor's auto-review classifier can route a full-access command to user approval, and its
  network allowlist is user-configurable (adding a domain unblocks shell access without escalation).
  To opt this project out of the `/tmp` wrapper mechanism, say so in a line here - do not edit the
  policy in `engineer.agent.md`.
- If `uv` fails with a permission error on its home cache (`~/.cache/uv` / `uv cache dir`), the harness
  sandbox is denying home-dir writes: rerun once with `UV_CACHE_DIR=<writable scratch>/uv-cache` and say
  you did (some sandboxes redirect the cache automatically; others hard-deny - this is the portable fix).

## Conventions

- Default working style: terse responses; tables when comparing options; lead with an
  explicit recommendation; give the bare command first, then variants.
- (add project-specific conventions here as you learn them)
