_Rev. 1_

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

As of **2026-08-13** (re-verify per "Keeping This Current"):

| Tier | Claude Code (Agent tool `model:`) | Cursor |
|---|---|---|
| cheap | `haiku` (Haiku 4.5) | Composer-class fast model |
| mid | `sonnet` (Sonnet 5) | Sonnet 5 |
| high | `opus` (Opus 5) | Opus 4.8 / GPT-5.6-class |
| frontier | `fable` or the session model | Fable 5 / Opus 5 |

Notes: Opus 5 benchmarks near-frontier at roughly half the frontier price - in Claude Code the `opus`
selector is the best price/performance pick for high-tier and budget-frontier work. In Claude Code,
use the read-only `Explore` agent type for search sweeps. Cursor exposes no subagent tool (see
`ai/info/harnesses.md`), so its column matters only for choosing the *session* model there.

## Effort + Thinking

Where the harness exposes a reasoning-effort knob (Claude Code Agent tool: `effort:`), match it to the
tier: cheap -> `low`, mid -> inherit, high -> `high`, frontier -> `xhigh`/`max` (model-dependent).
Leave extended thinking ON wherever it is available - never worth toggling off per task.

## Keeping This Current

Model lineups and tier placements shift often. Under a Solaris checkout this file syncs from the
framework master (`solaris/info/model-tiers.md`) on every project update. Standalone, if the "as of"
date looks stale, verify the mapping against the harness's own model list and update this file (a
pack `refresh` is a good moment).
