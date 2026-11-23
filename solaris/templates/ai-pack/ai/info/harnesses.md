_Rev. 9_

# Info: Harness Capabilities <!-- omit in toc -->

- [Capability Matrix](#capability-matrix)
- [Notes](#notes)
- [Keeping This Current](#keeping-this-current)

Perishable data layer: dated observations of what each agent harness actually provides, kept out
of this pack's rules so the rules stay portable. Rules reference capabilities abstractly ("a
harness with no subagent tool"); this file says which harness that is today. Companion to
`{{PACK}}/info/model-tiers.md`. Standalone-first: needs nothing beyond this pack.

## Capability Matrix

As of **2026-11-22**, for the harnesses this pack is tested on. Claude Code 2.1.325, Cursor 3.24, OpenCode 2.3.0
and Codex 0.170.0 were checked as installed and against their docs, OpenCode 1.x against its 1.19.6 docs only:

| Capability | Claude Code | Cursor | OpenCode 2 | OpenCode 1.x | Codex |
|---|---|---|---|---|---|
| Instruction auto-load | `CLAUDE.md` `@`-import of `AGENTS.md` (2.1.277 and later read `AGENTS.md` themselves, but only where no `CLAUDE.md` exists) | reads `AGENTS.md` natively | reads `AGENTS.md` natively | reads `AGENTS.md` natively (`CLAUDE.md` only where there is none) | reads `AGENTS.md` natively: one file per folder from the project root down, 32 KiB in all (`project_doc_max_bytes`) |
| Subagents | Agent tool (`general-purpose`, read-only `Explore` and `Plan`): per-call `model:` (an alias), else the session model (in a Fable session `Explore` runs on Opus); per-call `effort` (2.1.292 and later) | Task tool (2.4+; built-in Explore and Browser, plus custom agents): the parent names a model per launch, effort as a model-ID suffix; parallel | subagent tool (`explore`, `general`): per-launch model as `provider/model#variant`, else the session's; always in the background, the result comes back as a message; parallel | Task tool (`general`, `explore`, plus custom agents): no per-launch model in its docs - a subagent runs on its agent definition's `model`, else the invoking agent's; parallel | `spawn_agent` (`default`, `worker`, `explorer`, plus custom agents): model and effort per launch, else the `[agents]` defaults, else the parent's; parallel; it delegates only when asked (or when `AGENTS.md` or a skill says to) |
| Reasoning effort | per launch (`effort`), else the session's: the session runs at the effort the owner chose (`--effort`, `/effort` or a setting) | per launch: a model-ID suffix | per launch: the model's variant (`#high`) | per run (`opencode run --variant`) or agent definition (`reasoningEffort`), not per launch | per launch, else `model_reasoning_effort` (config, or `-c` on the command line) |
| Parallel tool calls | yes | partial, model-dependent | yes | not stated in its docs | not stated in its docs |
| Per-command sandbox escalation | permission prompt per command | approval card | permission prompt per command (in the owner's Inbox for an Autocode agent) | permission prompt per command where a rule says `ask` (most tools default to allow) | approval prompt per command under `approval_policy = "on-request"`; `codex exec` runs read-only unless given `--sandbox workspace-write` |
| MCP config | `.mcp.json` | `.cursor/mcp.json` | OpenCode's own config (`opencode mcp add`) | the `mcp` key of `opencode.json` | `[mcp_servers.<name>]` in `~/.codex/config.toml`, or in a trusted project's `.codex/config.toml` (`codex mcp add`) |

## Notes

- All five harnesses have a subagent tool (Cursor since 2.4).
- Waking a session: a finished background command always wakes the session, while harness timers are
  unreliable (Claude Code's wake-up timers die when the session idles, and session crons did not fire in
  a Claude Code session hosted by the Cursor extension). Under a Solaris checkout,
  `uv run -m solaris.tools.session_clock --dir <project>` run in the background is such a wake-up: it
  sleeps toward the next event in `{{PACK}}/.memory/schedule.json`. Start it as a harness-tracked
  background command, alone in its own call, never with `&`, `nohup`, `disown` or a `timeout` wrapper: a
  detached clock wakes nobody. An OpenCode session registered with Autocode is woken by Autocode's wakes
  instead: never start the clock there (the `autocode` plugin's rule).
- Background-command limits in Claude Code: interactive sessions (terminal, IDE extension, desktop app;
  2.1.288 and later) put no time limit on a background command, so start the clock there with `--cap 55`
  (a turn at least every 55 minutes keeps a one-hour prompt cache warm; `--cap 0` removes the cap).
  Unattended runs (`-p`, the SDK, CI, cloud sessions) stop a background command after 30 minutes unless it was
  started with a longer timeout (at most 2 hours), and say so, which wakes the session; keep the clock's default
  cap there (it re-arms below the limit, 25 minutes). A `-p` run given its prompt as text has a 10-minute default
  instead of 30, so a longer timeout is needed there.
- Claude Code blocks a command that starts with `sleep N` followed by more commands (observed; the docs do not
  state it, and 2.1.310 notes that long foreground sleeps are blocked): wait with a background command or an
  until-loop (`until <check>; do sleep 30; done`) instead.
- Under a Solaris checkout, Claude Code's `Stop` hook (`solaris.tools.session_guard`, wired at the
  Solaris root) sends a project session back once when it ends a turn with no clock armed, and logs
  safety-classifier stops to `{{PACK}}/.memory/alerts.jsonl`.
- Safety-classifier stops (Claude Code): Opus 5.5, Sonnet 5.5, Opus 5 and Fable models run classifiers.
  A cyber or biology flag re-runs the request on an older model automatically (from Fable and Opus 5.5: biology
  on Opus 5, cyber on Opus 4.8; `switchModelsOnFlag: false` asks first), and the session stays on that model;
  other categories (such as reasoning extraction, which fires on agent prompts and model outputs) have no
  fallback, so the session stays stopped and every wake-up is refused again. Only the owner can clear it:
  `/model claude-opus-4-8`, `/compact`, `/model opus`, then a "resume" message (a `/compact` on the
  refusing model is refused too). Prevention: the main session never prints agent prompts, model
  outputs, trajectories or long third-party text; workers and counting scripts read them.
- Claude Code subagents: a subagent is blocked from writing a file that looks like a report ("Subagents
  should return findings as text"), so name progress and findings files without "report" (for example
  `<job>-status.md`). Background subagents cut off when the Claude process exits come back as "stopped"
  in the next session, and a message to their agent ids resumes them from their transcripts; one the
  user stopped cannot be resumed that way: relaunch it with its full brief. The desktop app and the VS Code
  extension always run a main-conversation subagent in the background (2.1.302 and later).
- Claude Code in the Cursor or VS Code extension: text written before a tool call may not be shown, so
  give the answer after the last tool call of the turn.
- OpenCode: saved worker transcripts survive a server restart, so after one, recover them and inspect external
  jobs before relaunching a worker. Include the model variant on control and steering messages to a worker too:
  an omitted one can reset the worker to its provider default.
- OpenCode Task subagents doing long research sometimes return an empty or cut-off final message (a retry
  may write nothing): keep each reply short (about 40 lines at most) with the details in a findings file
  the brief names, split big research into small briefs, and finish the work inline after two empty
  returns (a review of a large draft after one).
- OpenCode 2.x: 2.0.29 is the minimum version (owner, 2026-10-14). Changes from 1.x: a model reference
  may carry a variant (`provider/model#variant`; 2.0.27 dropped the config default's variant, so name it:
  in 2.3.0 inside `opencode run --model`, which has no separate `--variant` flag); a 1.x config is migrated
  at load and a 1.x `opencode.db` in place at the first start
  (even `opencode --version` writes a log into the default data folder, so move 1.x data first);
  `opencode --port` is gone (the IDE extension needs 1.0.0 or later); one-shot `--standalone` calls race the
  config load (no models, "Integration not found"), so query a warm `opencode serve`; `opencode run`
  reads stdin, so give it `</dev/null` in scripts and heredocs; `session export` leaves out unfinished
  or empty assistant turns; `opencode service restart` kills tool commands running under the service;
  `opencode api METHOD PATH` (2.3.0) calls the running service without handling its password.
- OpenCode 2.x compaction: the model has no compaction tool and `/compact` is a TUI action, while the automatic
  compaction fires near the window's limit, wherever the turn is, with nothing saved first. Every tool shell
  carries `OPENCODE_SESSION_ID`, and `POST /api/session/<id>/compact` (on the background service recorded in
  `~/.local/state/opencode/service.json`) queues a compaction and returns at once, so a session can ask for its
  own: `{"delivery": "queue"}` runs it after the turn, `"steer"` at the next step boundary (the route's default
  when no delivery is given). Under a Solaris checkout a session compacts itself that way, following the
  framework's `opencode2-selfcompact` skill: once a compaction is due (or the owner asks: `--force`), it rewrites
  `{{PACK}}/.memory/context.md`, its checkpoint, then runs `uv run -m solaris.tools.session_compact request --dir
  <project>` (exit 5: not due). A program started from a tool shell (another agent CLI, a script) inherits
  `OPENCODE_SESSION_ID` and would act for that session: start it with `env -u OPENCODE_SESSION_ID`.
- OpenCode 1.x is still released next to 2.x (1.19.6 on 2026-11-17); opencode.ai/docs describes 2.x, and the 1.x
  docs are in the repository at its v1 tags. Where it differs from the 2.x notes above: `opencode run --variant`
  and `opencode --port` exist, and the server compacts a session through `POST /session/<id>/summarize`, not the
  2.x compact route, so the self-compaction above is for 2.x only.
- Codex (0.170.0 installed; 0.171.0, 2026-11-20, makes GPT-6.2 Sol its default model): hooks are on by default,
  with events named like Claude Code's (`SessionStart`, `UserPromptSubmit`, `Stop` and others), set in
  `hooks.json` or `[hooks]` in `config.toml` under `~/.codex` or a trusted project's `.codex`; a new or changed
  hook runs only after it is trusted (`/hooks`). The CLI has no wake-up or cron feature: `/goal`, `codex queue`
  and resuming (`codex resume --last`, `codex exec resume`) only.
- Any other harness: check its own docs for the same capabilities. Without a subagent tool the
  subagents rule runs its checkpointed-inline contract: the lookup still runs, sliced/grepped within
  the read budget, notes to a scratch file, only conclusions restated (the bulk-read floor is never
  disabled).

## Keeping This Current

Harness capabilities shift with releases. Under a Solaris checkout this file syncs from the
framework master (`solaris/info/harnesses.md`, which carries the fuller framework-side matrix) on
every project update. Standalone, if the "as of" date looks stale, re-verify against the harness's
release notes and update this file (a pack `refresh` is a good moment).
