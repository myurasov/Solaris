_Rev. 2_

# Info: Model Tiers <!-- omit in toc -->

- [Tier Ladder](#tier-ladder)
- [Per-Harness Mapping](#per-harness-mapping)
- [Effort + Thinking](#effort--thinking)
- [Keeping This Current](#keeping-this-current)

Perishable data layer for this pack's rules (see `ai/rules/subagents.rule.md`): rules speak in
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

As of **2026-09-29** (re-verify per "Keeping This Current"):

| Tier | Claude Code (Agent tool `model:`) | Cursor |
|---|---|---|
| cheap | `haiku` (Haiku 4.5) | Composer 2.5 standard (`composer-2.5[fast=false]`; Fast costs 6x) |
| mid | `sonnet` (Sonnet 5.5) | Sonnet 5.5 |
| high | `opus` (Opus 5.5) | Opus 5.5 / GPT-5.6 Sol |
| frontier | `fable` (Fable 5.1) or the session model | Fable 5.1 (may need data-retention approval; else Opus 5.5 at `xhigh`) |

Notes: Anthropic's own guidance is to start with Opus 5.5 for most work and step up to Fable 5.1 for
demanding reasoning and long-horizon agentic work, or when Opus 5.5 at higher effort still falls
short; at 40% of Fable 5.1's base price ($4/$20 vs $10/$50 per million tokens; cache reads $0.20 vs
$0.25), in Claude Code the `opus` selector is the best price/performance pick for high-tier and
budget-frontier work. In Claude Code, use the read-only `Explore` agent type for search sweeps, and
pass `model:` whenever the tier differs from the session model - the built-in agent types otherwise
run on the session model (`Explore` capped at Opus). Cursor's subagent tool also takes a model per
launch (see `ai/info/harnesses.md`), so its column picks subagent models as well as the session model.

## Effort + Thinking

Where the harness takes a reasoning effort per delegated call (Cursor: a model-ID suffix,
`<model-id>[effort=high]`), match it to the tier: cheap -> `low`, mid -> the model's default, high ->
`high`, frontier -> `xhigh`/`max` (model-dependent; Haiku 4.5 has no effort setting). Claude Code has
no per-call knob: subagents run at the session's effort (set with `/effort` or `--effort`) unless
their agent definition sets `effort:`; sessions on Opus 5.5 and Sonnet 5.5 default to `medium`
(Fable 5.1: `high`), so raise it for judgment-heavy work. Leave extended thinking ON wherever it is
available - never worth toggling off per task.

## Keeping This Current

Model lineups and tier placements shift often. Under a Solaris checkout this file syncs from the
framework master (`solaris/info/model-tiers.md`) on every project update. Standalone, if the "as of"
date looks stale, verify the mapping against the harness's own model list and update this file (a
pack `refresh` is a good moment).
