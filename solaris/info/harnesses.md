# Info: Harness Capabilities <!-- omit in toc -->

- [Capability Matrix](#capability-matrix)
- [Notes](#notes)
- [Keeping This Current](#keeping-this-current)

Perishable data layer: dated observations of what each agent harness actually provides, kept out of
the rules so the rules stay portable. Rules reference capabilities abstractly ("a harness with no
subagent tool"); this file says which harness that is today. Companion to
`solaris/info/model-tiers.md`.

## Capability Matrix

As of **2026-10-01**, for the two harnesses Solaris runs on:

| Capability | Claude Code | Cursor |
|---|---|---|
| Instruction auto-load | `CLAUDE.md` `@`-import of `AGENTS.md` | reads `AGENTS.md` natively |
| Session-start context injection | `SessionStart` hook stdout, **inline only up to 10k chars per hook invocation** - larger output is replaced by a file path plus a 2k-char preview (hence the split read-first load) | `sessionStart` hook JSON `additional_context`, no practical size limit |
| Per-prompt context injection | `UserPromptSubmit` hook stdout (same 10k limit) | none - `beforeSubmitPrompt` cannot inject (allow/block only) |
| Subagents | Agent tool (`general-purpose`, read-only `Explore` and `Plan`): per-call `model:`, else the session model (`Explore` capped at Opus); no per-call effort (subagents inherit the session's) | Task tool (2.4+; built-in Explore, Bash, Browser, plus custom agents): the parent names a model per launch, effort as a model-ID suffix; parallel |
| Reasoning effort | session-wide: the session runs at the effort the owner chose (`--effort`, `/effort` or the `effortLevel` setting) | per launch: a model-ID suffix |
| Parallel tool calls | yes | partial, model-dependent |
| Per-command sandbox escalation | permission prompt per command | approval card |
| MCP | `.mcp.json` | `.cursor/mcp.json` |

## Notes

- The 10k inline hook budget is why `read_first` loads in four parts, each budgeted separately:
  1 the commits and safety rules (plus pointers), 2 the subagents rule, 3 the token-economy rule,
  4 the interaction and YAGNI rules (plus the conflict-sweep note).
- Waking a session: a finished background command always wakes the session, while harness timers are
  unreliable (Claude Code's wake-up timers die when the session idles, and session crons did not fire in
  a Claude Code session hosted by the Cursor extension). So a scheduled wake-up is a background
  `uv run -m solaris.tools.session_clock --dir <project>`: it sleeps toward the next event in
  `<pack>/.memory/schedule.json`. Start it as a harness-tracked background command, alone in its own
  call, never with `&`, `nohup`, `disown` or a `timeout` wrapper: a detached clock wakes nobody.
- Background-command limits in Claude Code: interactive sessions (terminal, IDE extension, desktop app;
  2.1.288 and later) put no time limit on a background command, so start the clock there with `--cap 55`
  (a turn at least every 55 minutes keeps a one-hour prompt cache warm; `--cap 0` removes the cap).
  Unattended runs (`-p`, the SDK) stop a background command after 30 minutes unless it was started with a
  longer timeout (at most 2 hours), and say so, which wakes the session; keep the clock's default cap there
  (it re-arms below the limit, 25 minutes).
- Claude Code blocks a command that starts with `sleep N` followed by more commands: wait with a
  background command or an until-loop (`until <check>; do sleep 30; done`) instead.
- Claude Code's `Stop` hook (`solaris.tools.session_guard`, wired at the Solaris root) sends a project
  session back once when it ends a turn with no clock armed, and logs safety-classifier stops to
  `<pack>/.memory/alerts.jsonl`.
- Safety-classifier stops (Claude Code): Opus 5.5, Sonnet 5.5, Opus 5 and Fable models run classifiers.
  Cyber and biology flags fall back to another model automatically (`switchModelsOnFlag`); other
  categories (such as reasoning extraction, which fires on agent prompts and model outputs) have no
  fallback, so the session stays stopped and every wake-up is refused again. Only the owner can clear it:
  `/model claude-opus-4-8`, `/compact`, `/model opus`, then a "resume" message (a `/compact` on the
  refusing model is refused too). Prevention: the main session never prints agent prompts, model
  outputs, trajectories or long third-party text; workers and counting scripts read them.
- Claude Code subagents: a subagent is blocked from writing a file that looks like a report ("Subagents
  should return findings as text"), so name progress and findings files without "report" (for example
  `<job>-status.md`). Background subagents cut off when the Claude process exits come back as "stopped"
  in the next session, and a message to their agent ids resumes them from their transcripts; one the
  user stopped cannot be resumed that way: relaunch it with its full brief.
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
  may carry a variant (`provider/model#variant`; 2.0.27 dropped the config default's variant, so pass
  `--variant`); a 1.x config is migrated at load and a 1.x `opencode.db` in place at the first start
  (even `opencode --version` writes a log into the default data folder, so move 1.x data first);
  `opencode --port` is gone (the IDE extension needs 1.0.0 or later); one-shot `--standalone` calls race the
  config load (no models, "Integration not found"), so query a warm `opencode serve`; `opencode run`
  reads stdin, so give it `</dev/null` in scripts and heredocs; `session export` leaves out unfinished
  or empty assistant turns; `opencode service restart` kills tool commands running under the service.
- Both harnesses now have a subagent tool (Cursor since 2.4), so the subagents rule's
  checkpointed-inline fallback - the lookup still runs, sliced/grepped within the read budget, notes
  to a scratch file, only conclusions restated - applies only to harnesses without one (the bulk-read
  floor is never disabled).
- Skill auto-injection (`skill_loader`) is Claude-only for the same per-prompt-injection reason;
  on Cursor the agent opens the matching skill file itself.

## Keeping This Current

Harness capabilities shift with releases. Re-verify this table whenever the `refresh` skill runs
(and at every release); update this file, then sync the pack-adapted copy
`solaris/templates/ai-pack/ai/info/harnesses.md` in the same edit (bump its rev; the "as of" dates
must match - a test enforces it). Rules never carry these observations directly.
