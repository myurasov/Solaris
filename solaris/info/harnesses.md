# Info: Harness Capabilities <!-- omit in toc -->

- [Capability Matrix](#capability-matrix)
- [Notes](#notes)
- [Keeping This Current](#keeping-this-current)

Perishable data layer: dated observations of what each agent harness actually provides, kept out of
the rules so the rules stay portable. Rules reference capabilities abstractly ("a harness with no
subagent tool"); this file says which harness that is today. Companion to
`solaris/info/model-tiers.md`.

## Capability Matrix

As of **2026-08-13**, for the two harnesses Solaris runs on:

| Capability | Claude Code | Cursor |
|---|---|---|
| Instruction auto-load | `CLAUDE.md` `@`-import of `AGENTS.md` | reads `AGENTS.md` natively |
| Session-start context injection | `SessionStart` hook stdout, **inline only up to 10k chars per hook invocation** - larger spills to a barely-previewed file (hence the split read-first load) | `sessionStart` hook JSON `additional_context`, no practical size limit |
| Per-prompt context injection | `UserPromptSubmit` hook stdout (same 10k limit) | none - `beforeSubmitPrompt` cannot inject (log-only) |
| Subagents | Agent tool (`general-purpose`, read-only `Explore`; `model:`/`effort:` params) | none |
| Parallel tool calls | yes | partial, model-dependent |
| Per-command sandbox escalation | permission prompt per command | approval card |
| MCP | `.mcp.json` | `.cursor/mcp.json` |

## Notes

- The 10k inline hook budget is why `read_first` loads in two parts (core set + delegation/YAGNI
  rules), each budgeted separately.
- No subagent tool in Cursor means the subagents rule's posture falls back to `off` behavior
  there: work inline, keep bulk material out of the transcript (slice/grep/summarize).
- Skill auto-injection (`skill_loader`) is Claude-only for the same per-prompt-injection reason;
  on Cursor the agent opens the matching skill file itself.

## Keeping This Current

Harness capabilities shift with releases. Re-verify this table whenever the `refresh` skill runs
(and at every release); update this file, then sync any embedded copies in
`solaris/templates/ai-pack/` (bump revs). Rules never carry these observations directly.
