_Rev. 3_

# Rule: Subagents (Bulk-Read Floor + Leveled Delegation) <!-- omit in toc -->

- [Bulk-Read Floor (Always-On)](#bulk-read-floor-always-on)
- [Posture Switch](#posture-switch)
- [The Posture](#the-posture)
- [Task Contract (Every Delegated Prompt)](#task-contract-every-delegated-prompt)
- [Model Tiering](#model-tiering)
- [What Stays Inline](#what-stays-inline)

What runs outside the main context, for all agent work on this project. Two layers: an always-on
**bulk-read floor** keeps oversized reads out of the main transcript, and a **leveled delegation
posture** on top lowers the bar to delegate-by-default. Sibling: `ai/rules/token-economy.rule.md`
(how much enters context, how fast it is re-sent); its resolved level drives this rule's `auto`.
Standalone-first: needs nothing beyond this pack.

## Bulk-Read Floor (Always-On)

Everything read into the main transcript is re-sent on every later round-trip; accumulated tool
results are the dominant cost of a long session. When a lookup is expected to pull more than ~20k
tokens of raw results (roughly: more than 3 full files, a multi-file sweep, a whole thread/log
dump) and the session continues afterward, run it in a subagent - read-only agent type for
sweeps, read-write only when it must write - returning the synthesized answer (named facts,
quotes, file:line pointers), never raw dumps. At economy level `full` the threshold tightens to
~10k. Floor tiering, regardless of posture: mechanical sweeps run on the cheapest tier at low
effort (names: `ai/info/model-tiers.md`); keep the session model for judgment-heavy synthesis.
Independent sweeps launch in one parallel batch (the batching floor in
`ai/rules/token-economy.rule.md`).

No subagent tool in this harness (see `ai/info/harnesses.md`)? Do not skip the lookup - run it
checkpointed inline: sliced/grepped reads within the read budget, notes accumulated in a scratch
file, only the conclusions restated in the reply. The posture still applies through this fallback:
at `quality`/`cost` checkpoint-inline delegable work by default, at `off` only floor-sized
lookups.

Not worth delegating: 1-3 known-small files or one sliced/grepped read (spin-up costs more than
it saves); a result that is itself the deliverable of a session that ends there.

## Posture Switch

`"subagents.level"` - read `ai/defaults.json` (committed team default), then
`ai/.memory/config.json` (private per-machine override; wins per key) - `off` / `auto` /
`quality` / `cost`; absent everywhere means `auto`. Aliases, accepted wherever the value is read:
`med`/`q` = `quality`; `full`/`save` = `cost`.

- **`off`**: leveled posture off; the bulk-read floor above still applies (it is never disabled).
- **`auto`** (default): derive from the resolved economy level
  (`ai/rules/token-economy.rule.md`): economy `off` -> `off`, `med` -> `quality`, `full` ->
  `cost`. One dial - a crunch tightens both.
- **`quality`**: posture in force, every tier choice one-upped (see table) - output quality over
  cost.
- **`cost`**: posture in force at the cheapest viable tier.

`quality` and `cost` differ only in WHICH model runs a delegated task - never in WHETHER to
delegate. Both delegate aggressively: anything delegable is delegated by default, parallel
subagents beat one long inline pass, and a close call goes to delegation. Frugality at `cost`
means a cheaper tier, not keeping the work inline.

**Per-request override:** `subagents: off|auto|quality|cost` (or an alias) anywhere in a user
message applies to that request only - acknowledge in one line, no config write. An unrecognized
value gets a one-line correction. **Config changes only on explicit persistence language**
("set/remember/from now on"): team-wide -> `ai/defaults.json`, this machine only ->
`ai/.memory/config.json`.

## The Posture

At `quality`/`cost`, any self-contained unit of work runs in a subagent by default: multi-file
reads and grep sweeps (even under the floor threshold, when 2+ round-trips are likely), "find
where X is defined/used" lookups, summarizing a document/thread/log,
per-entity context assembly, mechanical edits across files once the exact change is specified,
verification passes whose outcome is a short verdict (does it build, do tests pass, do links
resolve), research questions answerable from docs/web/MCP. Independent tasks launch in one
parallel batch. Before starting any multi-step lookup or mechanical task inline, ask "why is this
not a subagent?" - proceed inline only on a carve-out below. Repeatedly doing delegable work
inline is a defect - notice and correct it.

Delegation buys **context headroom**, not one-shot savings: raw reads die with the subagent,
which compounds across every later round-trip - but delegating short work measured 7-22% MORE
than inline (each spawn pays a fresh system prompt). Delegate for long-session headroom,
wall-clock parallelism, and tier arbitrage. For a mechanical sweep over greppable material, a
single batched shell call (`ai/rules/token-economy.rule.md`, batching floor) protects context
cheaper still - delegate when the work needs judgment per item, when raw volume would flood a
continuing session, or when independent sub-questions can run in parallel.

## Task Contract (Every Delegated Prompt)

A delegated task must be executable by a weaker model. Every subagent prompt carries:

1. **Exact scope** - the files, directories, queries, or ids to operate on; no "look around".
2. **Exact procedure** - which tools/commands, in what order, with the project's known invocations
   (from `ai/engineer.instructions.md`) spelled out, not rediscovered.
3. **Exact return shape** - named facts, file:line pointers, a verdict, a table; never raw dumps.
4. **Boundaries** - read-only vs write, what not to touch, any confidentiality rules in scope.
5. **Active modes restated** - subagents do not see this pack's always-on rules; restate any
   active mode that shapes the deliverable (the economy level, YAGNI, requested word counts,
   output conventions). Subagents follow the token-economy floor too - bulk reads in a helper are
   billed all the same.

If a task cannot be phrased this way, split it until it can - or keep it inline only when judgment
is genuinely inseparable from the reading.

## Model Tiering

Rules speak in abstract tiers - cheap (mechanical), mid (standard/moderate synthesis), high
(strong synthesis/review), frontier (hardest judgment). Concrete model names live in
`ai/info/model-tiers.md` (this pack's perishable data layer) - every tier choice reads that file,
never memory; there is no fallback. If the file is missing, the pack is broken: surface it and
repair (restore from git, or a project update under Solaris) instead of guessing. Match the task
class, then read the active posture's column:

| Task class | `cost` | `quality` |
|---|---|---|
| Mechanical (enumerate, extract, verify, apply a specified edit) | cheap, read-only agent type for sweeps | mid |
| Moderate synthesis (summarize, assemble a brief from named sources) | mid | high |
| Judgment-heavy (anything acted on directly) | session model, or inline | session model, never below high |

In doubt at `cost`, take the cheaper tier; in doubt at `quality`, the stronger one.

## What Stays Inline

- Single reads of known-small files, or one sliced/grepped read - spin-up costs more than it saves.
- Steps whose output the very next decision depends on, completing in one round-trip.
- Destructive / remote-mutating / outward actions and their confirmations (safety policy) - these
  never delegate.
- Work where the deliverable IS the reading (the user asked to see the file).
