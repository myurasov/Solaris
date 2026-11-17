---
name: opencode2-selfcompact
triggers: ["self-compact", "selfcompact", "compact yourself", "compact your context", "compact the session", "compact this session", "opencode2-selfcompact"]
antitriggers: ["log file", "log files", "docs", "documentation", "selfcompact.", "self-compact skill", "selfcompact skill"]
summary: Let a top-level OpenCode 2 session compact itself before its context window fills - reach a stable point, save a checkpoint, then queue the compaction with solaris.tools.session_compact.
---

# opencode2-selfcompact <!-- omit in toc -->

- [When to Use](#when-to-use)
- [Settings](#settings)
- [Steps](#steps)
- [The Checkpoint](#the-checkpoint)
- [What Request Checks](#what-request-checks)
- [After the Compaction](#after-the-compaction)
- [Exit Codes](#exit-codes)

A top-level OpenCode 2 session compacts itself once its context passes a set size, at a moment it picks, after
saving what a summary could lose. OpenCode 2 gives the model no compaction tool (its `/compact` is a TUI action),
and its automatic compaction fires near the window's limit, wherever the turn happens to be, with nothing saved
first: on a 1M-token model that is late, costly and lossy. `uv run -m solaris.tools.session_compact` measures the
session and queues the compaction through the OpenCode 2 API, which runs it after the turn (or at the next step
boundary) without blocking the shell that asked. OpenCode's automatic compaction stays on as the backstop.

## When to Use

- **A due line.** In an OpenCode 2 session, `interactions add` (the turn's log call) prints one more line when a
  compaction is due: `self-compaction: 612k of 500k context tokens (122.4%), DUE: ...`. Run the steps below in
  the same turn. `DUE but blocked` names what stands in the way: a git command holding `index.lock` (wait for it;
  never delete the lock, and tell the owner if it persists) or an API call that failed (it retries next time).
  Write no checkpoint yet; the next log call checks again. `Warnings` name state to settle before the turn ends.
- **The owner asks** you to compact this session ("compact yourself", "self-compact now"): run the steps and
  request with `--force`, which skips the enabled, limit and interval gates and none of the others. A question
  or a task about self-compaction (its docs, settings or code) is not such a request.
- **Anywhere else** (Claude Code, Cursor, OpenCode 1.x, a subagent session) the tool says "not applicable" and
  changes nothing: save `context.md` or the task notes, and let the harness compact (in Claude Code the owner
  runs `/compact`).

To look without logging: `uv run -m solaris.tools.session_compact check --brief [--dir <project>]` (exits 0; 2
for a `--dir` without an ai-pack). A program you start from a tool shell (another agent CLI, a script that runs
Solaris tools) inherits `OPENCODE_SESSION_ID` and would act for this session: start it with
`env -u OPENCODE_SESSION_ID`.

## Settings

All optional. A project's `<pack>/.memory/config.json`, then its `<pack>/defaults.json`, win over the framework's
`.memory/config.json`, key by key; a bad value falls back to its default and shows as `config_error`.

| Key | Default | Meaning |
|---|---|---|
| `selfcompact.enabled` | `true` (`false` with the `autocode` plugin attached) | `false` turns the due line off (`--force` still works) |
| `selfcompact.limit` | `500k` | context tokens, an integer or k/m shorthand; floor 100k |
| `selfcompact.min_interval_minutes` | `30` | the shortest time between two compactions |

A project with the `autocode` plugin attached (copied, in its manifest's `plugins`, or linked through
`<pack>/plugins/autocode.link.md`) has self-compaction off: autocode compacts its sessions. Only the project's own
`selfcompact.enabled` (its `.memory/config.json` or `defaults.json`) turns it back on; the framework's file does not.

One session can run on its own limit: `session_compact set-limit 333k` (`--clear` drops it), and `check` then
reports `limit_source: session`. `set-limit off` turns self-compaction off for that session alone: an autocode agent
whose compaction autocode runs (the autocode plugin's start step does this), so the two never both compact. "Context" is the prompt of the session's newest finished model step: fresh input
plus cache reads and writes. On a model whose window is below the limit, OpenCode's own compaction comes first;
set a lower limit for that session. A session still near the limit right after compacting (a large kept recent
history) needs a higher limit; meanwhile the minimum interval keeps it from compacting in a loop.

## Steps

In project work pass `--dir <project>` to every call (the due line's command carries it when the log call's
`--project` names that project; an ad-hoc task linked to a project keeps its checkpoint in the framework).

1. **Reach a stable point.** Never request in the middle of a multi-file edit; a commit, release or push sequence
   (staged changes, a tag not pushed, a half-published release); a confirmation the owner has not answered; or
   running subagents - wait for them and act on their results first. When one runs long, a queued request may go
   ahead if the checkpoint names its session id and what to do with its result; `--now` always waits. When the
   work cannot reach a stable point this turn, finish the unit and let the next check catch it.
2. **Persist durable state** the normal way: lessons in `instructions.md` (the framework's or the pack's), the
   turn's interaction log line, `schedule.json` events, plans and notes in their own files.
3. **Write the checkpoint** (below).
4. **Request:** `uv run -m solaris.tools.session_compact request [--dir <project>]`. The default queues the
   compaction to run right after this turn's final message: the answer arrives intact and the session waits,
   compacted, for the next prompt. Use it almost always. `--now` compacts at the next step boundary and the turn
   goes on with the compacted context: only for a long autonomous run, at a phase boundary. `--dry-run` runs
   every gate and queues nothing.
5. **Say so in the final message,** in one line: the session compacts after this reply, and the checkpoint keeps
   the details.

## The Checkpoint

- **Project work:** the project's `<pack>/.memory/context.md`, its standing pre-compaction save point. Rewrite its
  `## Session Context` in place as the persona describes (Now, Running jobs, Next actions, Open questions,
  Decisions).
- **Orchestrator and ad-hoc task sessions:** `.memory/selfcompact/<session id>.md` (the due line names it), one
  file per session, rewritten each time and deleted by the tool after 30 days. Outline: `# Session Checkpoint -
  <time>`, `## Now` (the task and its state), `## Running Jobs` (subagent session ids, PIDs, hosts, done
  markers), `## Next Actions`, `## Open Questions` (asked and not yet answered), `## Decisions` (with reasons),
  `## Key References` (exact ids, paths, commands, URLs and numbers a summary could blur).

Write what a successor needs to go on without asking; never credentials. `request` refuses a checkpoint that is
missing, last written over 20 minutes ago, or holds under 400 bytes of state (in `context.md`, the text below
`## Session Context` when that heading exists); a session checkpoint also needs `## Now` and `## Next Actions`.

## What Request Checks

1. The context is over the limit, outside the minimum interval, and the setting is on (`--force` skips these
   three); no compaction is queued already (then it exits 0 with `pending`).
2. The checkpoint passes (above).
3. Every running subagent session of this session is named in the checkpoint; with `--now`, none may run.
4. No git command holds `index.lock` in the project's repositories (with `--dir`) or the Solaris checkout
   (without). Staged changes and an unfinished merge, rebase, cherry-pick or revert refuse `--now` and are
   warnings for a queued request: finish your own before the turn ends, and leave another session's alone.

## After the Compaction

When this session ran `request` in the turn before the compaction and it answered `"requested": true` or
`"pending": true`, read the checkpoint its `next` field names before anything else (that output stays in the
recent conversation the compaction keeps), then go on from its next actions. If the request exited 3 after the
call went out, `check` shows whether a compaction is queued (`pending`). After any other compaction (OpenCode's
automatic one, or the owner's `/compact`), do not act on a session checkpoint, which may be hours old and could
repeat finished work (a project's `context.md` is read as usual, with its age in mind).

## Exit Codes

| Exit | Meaning | What to do |
|---|---|---|
| 0 | Queued, already queued (`pending`), a dry run, or a `check` | Nothing more |
| 2 | Bad arguments, or no ai-pack at `--dir` | Fix the call |
| 3 | Not applicable, or the API failed | Skip; the harness's own compaction applies |
| 4 | A gate refused (`refused` lists each reason) | Fix each, then request again |
| 5 | Not due: under the limit, inside the interval, off, or compacted since the newest step | Nothing; `--force` only when the owner asks |
