_Rev. 4_

# Info: Harness Capabilities <!-- omit in toc -->

- [Capability Matrix](#capability-matrix)
- [Notes](#notes)
- [Keeping This Current](#keeping-this-current)

Perishable data layer: dated observations of what each agent harness actually provides, kept out
of this pack's rules so the rules stay portable. Rules reference capabilities abstractly ("a
harness with no subagent tool"); this file says which harness that is today. Companion to
`{{PACK}}/info/model-tiers.md`. Standalone-first: needs nothing beyond this pack.

## Capability Matrix

As of **2026-09-29**, for the harnesses this pack is tested on:

| Capability | Claude Code | Cursor |
|---|---|---|
| Instruction auto-load | `CLAUDE.md` `@`-import of `AGENTS.md` | reads `AGENTS.md` natively |
| Subagents | Agent tool (`general-purpose`, read-only `Explore` and `Plan`): per-call `model:`, else the session model (`Explore` capped at Opus); no per-call effort (subagents inherit the session's) | Task tool (2.4+; built-in Explore, Bash, Browser, plus custom agents): the parent names a model per launch, effort as a model-ID suffix; parallel |
| Parallel tool calls | yes | partial, model-dependent |
| Per-command sandbox escalation | permission prompt per command | approval card |
| MCP config | `.mcp.json` | `.cursor/mcp.json` |

## Notes

- Both harnesses have a subagent tool (Cursor since 2.4).
- Any other harness: check its own docs for the same capabilities. Without a subagent tool the
  subagents rule runs its checkpointed-inline contract: the lookup still runs, sliced/grepped within
  the read budget, notes to a scratch file, only conclusions restated (the bulk-read floor is never
  disabled).

## Keeping This Current

Harness capabilities shift with releases. Under a Solaris checkout this file syncs from the
framework master (`solaris/info/harnesses.md`, which carries the fuller framework-side matrix) on
every project update. Standalone, if the "as of" date looks stale, re-verify against the harness's
release notes and update this file (a pack `refresh` is a good moment).
