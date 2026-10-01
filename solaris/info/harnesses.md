# Info: Harness Capabilities <!-- omit in toc -->

- [Capability Matrix](#capability-matrix)
- [Notes](#notes)
- [Keeping This Current](#keeping-this-current)

Perishable data layer: dated observations of what each agent harness actually provides, kept out of
the rules so the rules stay portable. Rules reference capabilities abstractly ("a harness with no
subagent tool"); this file says which harness that is today. Companion to
`solaris/info/model-tiers.md`.

## Capability Matrix

As of **2026-09-29**, for the two harnesses Solaris runs on:

| Capability | Claude Code | Cursor |
|---|---|---|
| Instruction auto-load | `CLAUDE.md` `@`-import of `AGENTS.md` | reads `AGENTS.md` natively |
| Session-start context injection | `SessionStart` hook stdout, **inline only up to 10k chars per hook invocation** - larger output is replaced by a file path plus a 2k-char preview (hence the split read-first load) | `sessionStart` hook JSON `additional_context`, no practical size limit |
| Per-prompt context injection | `UserPromptSubmit` hook stdout (same 10k limit) | none - `beforeSubmitPrompt` cannot inject (allow/block only) |
| Subagents | Agent tool (`general-purpose`, read-only `Explore` and `Plan`): per-call `model:`, else the session model (`Explore` capped at Opus); no per-call effort (subagents inherit the session's) | Task tool (2.4+; built-in Explore, Bash, Browser, plus custom agents): the parent names a model per launch, effort as a model-ID suffix; parallel |
| Parallel tool calls | yes | partial, model-dependent |
| Per-command sandbox escalation | permission prompt per command | approval card |
| MCP | `.mcp.json` | `.cursor/mcp.json` |

## Notes

- The 10k inline hook budget is why `read_first` loads in four parts (core set; delegation rule;
  token economy; YAGNI rule), each budgeted separately.
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
