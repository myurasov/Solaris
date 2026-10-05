_Rev. 9_

# Info: Model Tiers <!-- omit in toc -->

- [Tier Ladder](#tier-ladder)
- [Per-Harness Mapping](#per-harness-mapping)
- [Effort + Thinking](#effort--thinking)
- [Keeping This Current](#keeping-this-current)

Perishable data layer for this pack's rules (see `{{PACK}}/rules/subagents.rule.md`): rules speak in
abstract tiers; the concrete model names live here and age. Tier choices come from this file -
not from memory or guesswork. Standalone-first: needs nothing beyond this pack.

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
| frontier | `opus` (Opus 5.5) at effort `max`; not Fable 5.x (see below) | Opus 5.5 at `max` (not Fable 5.1) | `gpt-6-astra` |

**Avoid Fable 5.x** (owner direction 2026-09-30, standing): Fable 5 and 5.1 are not used unless the owner explicitly
asks. The owner rates them less capable than Opus 5.5, and they cost more ($10/$50 vs $4/$20 per million tokens).
Frontier-tier work runs on Opus 5.5 at `xhigh` or `max` effort; in Claude Code pass `model: opus` on each
launch and run the session at that effort (Solaris keeps roles in harness-agnostic briefs, no agent definitions).

Notes: Anthropic's own guidance (overridden here by the owner: see Avoid Fable 5.x) is to start with Opus 5.5 for
most work and step up to Fable 5.1 for
demanding reasoning and long-horizon agentic work, or when Opus 5.5 at higher effort still falls
short; at 40% of Fable 5.1's base price ($4/$20 vs $10/$50 per million tokens; cache reads $0.20 vs
$0.25), in Claude Code the `opus` selector is the best price/performance pick for high-tier and
budget-frontier work. OpenAI positions GPT-6.1 Sol as near-Astra performance at a fifth of GPT-6
Astra's price ($2/$10 vs $10/$50). On Terminal-Bench 4.0 xAI's flagship Grok 4.7 ($2/$6) scores 37.6%,
level with GPT-5.6 Sol (Cursor's newest OpenAI model) and far below Opus 5.5 and Fable 5.1 (66.4% and
55.8%; vendor-reported), hence mid. In Claude Code, use the read-only `Explore` agent type for search
sweeps, and pass `model:` whenever the tier differs from the session model - the built-in agent types
otherwise run on the session model (`Explore` capped at Opus). Cursor's subagent tool also takes a
model per launch (see `{{PACK}}/info/harnesses.md`), so its column picks subagent models as well as the
session model. The OpenAI / xAI column gives API model ids for harnesses that take those APIs directly
(for example OpenCode, or Codex for the GPT models); Cursor does not list the GPT-6 models yet.

## Effort + Thinking

Where the harness takes a reasoning effort per delegated call (Cursor: a model-ID suffix,
`<model-id>[effort=high]`), match it to the tier: cheap -> `low`, mid -> the model's default, high ->
`high`, frontier -> `xhigh`/`max` (model-dependent; Haiku 4.5 has no effort setting). Claude Code has
no per-call knob: subagents run at the session's effort (set with `/effort`, `--effort` or the
`effortLevel` setting; Solaris uses no agent definitions to pin it); sessions on Opus 5.5 and Sonnet 5.5 default to `medium`
(Fable 5.1: `high`), so raise it for judgment-heavy work. Leave extended thinking ON wherever it is
available - never worth toggling off per task.

## Keeping This Current

Model lineups and tier placements shift often. Under a Solaris checkout this file syncs from the
framework master (`solaris/info/model-tiers.md`) on every project update. Standalone, if the "as of"
date looks stale, verify the mapping against the harness's own model list and update this file (a
pack `refresh` is a good moment).
