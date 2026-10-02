_Rev. 12_

# Instructions - {{NAME}} <!-- omit in toc -->

- [Workspaces](#workspaces)
- [Build / Run / Test](#build--run--test)
- [Deploy](#deploy)
- [Local-Only Folders](#local-only-folders)
- [Remote Host Discipline](#remote-host-discipline)
- [Runtime Notes \& Gotchas](#runtime-notes--gotchas)
- [Conventions](#conventions)

Editable, project-specific notes on how to develop this project - the **one shared "how" layer for every
persona**: the primary persona and every role brief (`<role>.agent.md`) read it on start and keep it
current. Rewrite freely to keep the best version (not append-only). The commit and safety policies live in
`{{PRIMARY}}.agent.md`.

**This is the "how" layer.** All procedures and project knowledge live here: build/run/test, **deploy &
restart procedures**, **model/runtime details**, architecture/layers, and **gotchas** - including whatever
any one persona learns doing its job. A lesson lands here the moment it is learned, so no persona repeats
another's mistake and nothing is kept twice; a persona running without write access hands the update back
in its report for the primary persona to apply. Procedure that only one role runs (a reviewer's
checklist, a worker's job protocol) goes under a heading named for that role, still in this file. The only
things that do *not* live here are the inventory of *what exists* (hardware + hosts/accounts ->
`<pack>/.memory/resources.md`), secrets (`credentials.md`), and the session-context summary (`context.md`).

**Shareable layer.** This file sits in the project's ai-pack folder (`<pack>/`; default `aipack/` for new projects,
`ai/` in projects made before Solaris 0.39.0, any name) alongside `{{PRIMARY}}.agent.md` and `spec.md` - the portable,
shareable layer. Keep it free of anything environment-specific or sensitive: **no** hostnames, IPs,
internal/corporate URLs, concrete deploy targets, remote paths, or secrets - those are inventory and live in
`<pack>/.memory/resources.md` / `credentials.md`. Procedures still belong here, written as generic patterns
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

- (deploy + restart procedure as generic patterns; reference `<pack>/.memory/resources.md` for host/path/port)

## Local-Only Folders

Scratch that should never be tracked lives in `__`-prefixed folders, gitignored as one pattern (`__*/`):
`__research/` (working reports/visuals), `__history/` (archived/superseded content), `__out/` (pipeline
outputs) - add others as needed. **Durable conclusions get folded into this file or `<pack>/spec.md` before a
`__research/` report is considered done** - the folders are disposable, the lessons are not.

## Remote Host Discipline

(Delete this section if the project touches no remote hosts.)

- Concrete hosts/paths/ports live in `<pack>/.memory/resources.md`; procedures here reference them generically.
- Deploy with `rsync` (excludes per the safety policy: `.venv`, `.git`, secrets, build artifacts; no
  `--delete` by default). Create the remote parent first (`ssh <host> mkdir -p <parent>`) - some rsync
  builds (macOS openrsync) do not create nested remote dirs. To a root login, add `--no-owner --no-group`:
  `-a` copies this machine's owner and group onto the files, which then belong to some other user there.
- **Stream remote output live** (run in a tmux session and read the screen with `capture-pane`, or stream
  to the terminal) rather than redirecting to a file and polling it.
- **Detached jobs end with a per-run done marker** (a file the job writes as its last step); poll for that
  file. Over ssh, `pgrep -f <pattern>` matches the polling command's own command line, so it never reports
  the job finished, and `pkill -f <pattern>` kills its own shell; an END line in an appended log may be an
  earlier run's.
- **Shell traps:** `a && nohup b &` puts `a` in the background too, with empty stdin (a `cat > file` there
  writes an empty file) - write `a; nohup b > log 2>&1 &`, or run `a` in a call of its own. An ssh inside a
  loop or a heredoc script needs `-n`, or it swallows the rest of the input. Quote heredocs that write
  generated text (`<<'EOF'`): an unquoted one expands every `$` and runs every backticked command in it.
- Model servers, notebooks and container daemons listen on `127.0.0.1` only (publish container ports as
  `-p 127.0.0.1:<port>:<port>`; a bare `-p` opens every interface); reach them through an ssh tunnel
  (`ssh -L <port>:127.0.0.1:<port> <host>`), never a public port.
- **Bulk copies:** copy host to host rather than through this machine (forward a temporary
  `ssh-agent -t <secs>` that holds only the key the copy needs, and kill it afterwards); split big files
  into parallel ranges; start parallel ssh connections a few seconds apart - sshd starts dropping new ones
  once about 10 are still logging in (its default `MaxStartups`).
- Leave the host as you found it: stop what you started, and keep any footprint under one project dir.

## Runtime Notes & Gotchas

- (model/runtime details, performance notes, and gotchas worth never relearning)
- Sandbox ladder + name-block wrappers: policy lives in `{{PRIMARY}}.agent.md` (Sandboxed Harnesses); under
  a Solaris checkout, `solaris.agent.md` carries the framework-wide version with the current known-hard
  denial list. Harness specifics seen so far: Codex `approval_policy = "on-request"` grants per-command
  escalation; Cursor's auto-review classifier can route a full-access command to user approval, and its
  network allowlist is user-configurable (adding a domain unblocks shell access without escalation).
  To opt this project out of the `/tmp` wrapper mechanism, say so in a line here - do not edit the
  policy in `{{PRIMARY}}.agent.md`.
- If `uv` fails with a permission error on its home cache (`~/.cache/uv` / `uv cache dir`), the harness
  sandbox is denying home-dir writes: rerun once with `UV_CACHE_DIR=<writable scratch>/uv-cache` and say
  you did (some sandboxes redirect the cache automatically; others hard-deny - this is the portable fix).

## Conventions

- Default working style: terse responses; tables when comparing options; lead with an
  explicit recommendation; give the bare command first, then variants.
- **Third-party pickle files run code when loaded** (`*.pkl`, many model checkpoints): inspect one with
  `pickletools` first (it reads the opcodes, runs nothing), load it through an unpickler whose `find_class`
  admits only the classes it needs, and keep torch's `weights_only` on (allowlist a missing class with
  `torch.serialization.add_safe_globals` rather than turning it off).
- **Validate a config change by running the tool that consumes it**, not by reading the file back or
  probing an API by hand.
- (Optional) **AI spending limit:** the owner may set an approximate daily limit for this project, in
  dollars, as `"ai.daily_budget_usd"` in `<pack>/.memory/config.json`; under a Solaris checkout,
  `uv run -m solaris.tools.ai_spend` checks the day's spend against it.
- (add project-specific conventions here as you learn them)
