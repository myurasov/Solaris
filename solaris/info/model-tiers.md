# Info: Model Tiers <!-- omit in toc -->

- [Tier Ladder](#tier-ladder)
- [Per-Harness Mapping](#per-harness-mapping)
- [Effort + Thinking](#effort--thinking)
- [Keeping This Current](#keeping-this-current)

Perishable data layer for `solaris/rules/subagents.rule.md` (and the ai-pack copy): rules speak in
abstract tiers; the concrete model names live here and age. This file is the source of truth - the
ai-pack template carries an adapted copy at `solaris/templates/ai-pack/ai/info/model-tiers.md`, so
when this file changes, update that copy in the same edit and `revs bump` it (the `refresh` and
`release` skills carry this as a checklist item; a test asserts the "as of" dates match).

## Tier Ladder

Four abstract tiers, matched to task complexity, harness-independent:

| Tier | Use for |
|---|---|
| **cheap** | Mechanical work: enumerate, filter, extract, verify, apply a fully specified edit |
| **mid** | Standard work and moderate synthesis: summarize a document/thread, assemble a brief from named sources |
| **high** | Strong synthesis and review: multi-source analysis, code review, refactoring judgment |
| **frontier** | Hardest judgment: ambiguous triage, design decisions, anything acted on directly without review |

## Per-Harness Mapping

As of **2026-09-30** (re-verify per "Keeping This Current"):

| Tier | Claude Code (Agent tool `model:`) | Cursor | OpenAI / xAI (API model id) |
|---|---|---|---|
| cheap | `haiku` (Haiku 4.5) | Composer 2.5 standard (`composer-2.5[fast=false]`; Fast costs 6x) | `gpt-6-luna` |
| mid | `sonnet` (Sonnet 5.5) | Sonnet 5.5 / Grok 4.7 | `grok-4.7` |
| high | `opus` (Opus 5.5) | Opus 5.5 | `gpt-6.1-sol` |
| frontier | `opus` (Opus 5.5); not Fable 5.x (see below) | Opus 5.5 (not Fable 5.1) | `gpt-6-astra` |

**Avoid Fable 5.x** (owner direction 2026-09-30, standing): Fable 5 and 5.1 are not used unless the owner explicitly
asks. The owner rates them less capable than Opus 5.5, and they cost more ($10/$50 vs $4/$20 per million tokens).
Frontier-tier work runs on Opus 5.5 at the effort the owner chose; in Claude Code pass `model: opus` on each
launch (the session's effort applies; Solaris keeps roles in harness-agnostic briefs, no agent definitions).

Notes: Anthropic's own guidance (overridden here by the owner: see Avoid Fable 5.x) is to start with Opus 5.5 for
most work and step up to Fable 5.1 for
demanding reasoning and long-horizon agentic work, or when Opus 5.5 at higher effort still falls
short; at 40% of Fable 5.1's base price ($4/$20 vs $10/$50 per million tokens; cache reads $0.20 vs
$0.25), in Claude Code the `opus` selector is the best price/performance pick for high-tier and
budget-frontier work. OpenAI positions GPT-6.1 Sol as near-Astra performance at a fifth of GPT-6
Astra's price ($2/$10 vs $10/$50). On Terminal-Bench 4.0 xAI's flagship Grok 4.7 ($2/$6) scores 37.6%,
level with GPT-5.6 Sol (Cursor's newest OpenAI model) and far below Opus 5.5 and Fable 5.1 (66.4% and
55.8%; vendor-reported), hence mid. Pass `model:` whenever the tier differs from the session model -
Claude Code's built-in agent types (`general-purpose`, `Explore`, `Plan`) otherwise run on the session
model (`Explore` capped at Opus). Cursor's subagent tool also takes a model per launch (see
`solaris/info/harnesses.md`), so its column picks subagent models as well as the session model. The
OpenAI / xAI column gives API model ids for harnesses that take those APIs directly (for example
OpenCode, or Codex for the GPT models); Cursor does not list the GPT-6 models yet.

## Effort + Thinking

The effort level is the owner's choice (owner, 2026-10-08): no rule, tool or file fixes one. Where the harness
takes a reasoning effort per delegated call (Cursor: a model-ID suffix, `<model-id>[effort=high]`), pass the
effort the owner chose; when he chose none, leave it out and the model's default applies (Haiku 4.5 has no
effort setting). Claude Code has
no per-call knob: subagents run at the session's effort (set with `/effort`, `--effort` or the
`effortLevel` setting; Solaris uses no agent definitions to pin it); sessions on Opus 5.5 and Sonnet 5.5 default to `medium`
(Fable 5.1: `high`); the owner picks the level. Leave extended thinking ON wherever it is
available - never worth toggling off per task.

## Keeping This Current

Model lineups and tier placements shift often. Re-verify this table whenever the `refresh` skill runs
(and at every release), against current benchmark data; update this file, then sync the pack copy
`solaris/templates/ai-pack/ai/info/model-tiers.md` in the same edit (bump its rev; the "as of" dates
must match). Never bake concrete model names into rules - they belong here only.
