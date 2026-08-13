# Rule: Subagents (Leveled Delegation) <!-- omit in toc -->

- [Level Switch](#level-switch)
- [The Posture](#the-posture)
- [Task Contract (Every Delegated Prompt)](#task-contract-every-delegated-prompt)
- [Model Tiering](#model-tiering)
- [What Stays Inline](#what-stays-inline)

Default posture for all agent work: **delegate self-contained work to subagents; keep the main
context lean.** The main session's job is orchestration and judgment; subagents do the legwork.
Delegation also lets cheaper/faster models carry well-scoped tasks, cutting cost and latency.

## Level Switch

`"subagents.level"` in `.memory/config.json` (framework) - `off` / `med` / `full`; key or file
absent means `med`.

- **`off`**: no delegation posture; work inline, but still keep bulk reads out of the main
  transcript (summarize, slice, or grep instead of dumping).
- **`med`** (default): posture in force; every tier choice is one-upped (see table) - quality
  over cost.
- **`full`**: posture in force at the cheapest viable tier.

**Per-request override:** `subagents: off|med|full` anywhere in a user message applies to that
request only - acknowledge in one line, no config write. An unrecognized value gets a one-line
correction. **Config changes only on explicit persistence language** ("set/remember/from now on").

## The Posture

At `med`/`full`, any self-contained unit of work runs in a subagent by default: multi-file reads
and grep sweeps, "find where X is defined/used" lookups, summarizing a document/thread/log,
mechanical edits across files once the exact change is specified, verification passes whose outcome
is a short verdict, research questions answerable from docs/web/MCP. Independent tasks launch in
one parallel batch. Before starting any multi-step lookup or mechanical task inline, ask "why is
this not a subagent?" - proceed inline only on a carve-out below.

## Task Contract (Every Delegated Prompt)

A delegated task must be executable by a weaker model. Every subagent prompt carries:

1. **Exact scope** - the files, directories, queries, or ids to operate on; no "look around".
2. **Exact procedure** - which tools/commands, in what order, with known invocations spelled out.
3. **Exact return shape** - named facts, file:line pointers, a verdict, a table; never raw dumps.
4. **Boundaries** - read-only vs write, what not to touch, any confidentiality rules in scope.
5. **Active modes restated** - subagents do not see the always-on rules; restate any active mode
   that shapes the deliverable (YAGNI, requested word counts, output conventions).

If a task cannot be phrased this way, split it until it can - or keep it inline only when judgment
is genuinely inseparable from the reading.

## Model Tiering

Rules speak in abstract tiers; concrete model names live in `solaris/info/model-tiers.md` (the
perishable layer - check it, not memory). Match the task class, then read the level's column:

| Task class | `full` | `med` |
|---|---|---|
| Mechanical (enumerate, extract, verify, apply a specified edit) | cheap, read-only agent type for sweeps | mid |
| Moderate synthesis (summarize, assemble a brief from named sources) | mid | high |
| Judgment-heavy (anything acted on directly) | session model, or inline | session model, never below high |

In doubt at `full`, take the cheaper tier; in doubt at `med`, take the stronger one. A harness
with no subagent tool (see `solaris/info/harnesses.md`) falls back to `off` behavior regardless
of level.

## What Stays Inline

- Single reads of known-small files, or one sliced/grepped read - spin-up costs more than it saves.
- Steps whose output the very next decision depends on, completing in one round-trip.
- Destructive / remote-mutating / outward actions and their confirmations (safety rule) - these
  never delegate.
- Work where the deliverable IS the reading (the user asked to see the file).
