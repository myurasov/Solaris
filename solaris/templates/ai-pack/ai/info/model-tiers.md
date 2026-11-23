_Rev. 12_

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

As of **2026-11-22** (re-verify per "Keeping This Current"):

| Tier | Claude Code (Agent tool `model:`) | Cursor | OpenAI / xAI (API model id) |
|---|---|---|---|
| cheap | `haiku` (Haiku 5.5) | Haiku 5.5, or Composer 2.5 standard (`composer-2.5[fast=false]`; Fast costs 6x) | `gpt-6-luna` |
| mid | `sonnet` (Sonnet 5.5) | Sonnet 5.5 / Grok 4.7 | `gpt-6.1-terra` / `grok-4.7` |
| high | `opus` (Opus 5.5) | Opus 5.5 | `gpt-6.2-sol` |
| frontier | `fable` (Fable 5.2); `opus` (Opus 5.5) on a budget | Fable 5.2; Opus 5.5 on a budget | `gpt-6.2-astra` |

OpenCode 2 names a model `provider/model#variant` (the variant is the effort). Its Claude Agent provider takes the
Claude Code column's aliases (`claude-agent/haiku`, `claude-agent/sonnet`, `claude-agent/opus`,
`claude-agent/fable`); other providers carry the same models under ids of their own, so read the id from the
session's model list (`opencode models`), never from memory. OpenCode 1.x writes `provider/model` without the
variant, and Codex takes a model id of the OpenAI column (`-m`).

Notes:

- **Anthropic.** Its guidance is to start with Opus 5.5 for most work and step up to Fable 5.2 (2026-11-09) for
  demanding reasoning and long-horizon agentic work, or when Opus 5.5 at higher effort still falls short. Fable is
  an option again (owner, 2026-11-22: the 2026-09-30 direction to avoid Fable 5.x is withdrawn). At half of Fable
  5.2's price ($4/$20 vs $8/$40 per million tokens; cache reads $0.20 for both), the `opus` selector stays the
  best price/performance pick for high-tier and budget-frontier work. Haiku 5.5 (2026-10-07) took over `haiku` at
  a tenth of Haiku 4.5's price: $0.10/$0.50 while the prompt is at most 100k tokens (cache reads and writes
  count), $0.50/$2.50 for the whole request above that, so keep cheap-tier briefs and their reads small.
- **OpenAI / xAI.** OpenAI's lineup since 2026-11-17: GPT-6.2 Astra ($8/$40) for the hardest work, GPT-6.2 Sol
  ($2/$10, its "go-to model", at the price of GPT-6.1 Sol before it), GPT-6.1 Terra ($1/$5) and GPT-6 Luna
  ($0.10/$0.50); a prompt over 272k tokens costs more. xAI's flagship is still Grok 4.7 ($2/$6). The column gives
  API model ids for harnesses that take those APIs directly (for example OpenCode, or Codex for the GPT models).
  Cursor lists no GPT-6 model yet (its newest OpenAI models are GPT-5.6 Sol, Terra and Luna); it does list Fable
  5.2 and 5.1.
- **Benchmarks** (Terminal-Bench 4.0, vendor-reported; the tables differ in harness and effort). Anthropic's
  launch tables: Sonnet 5.5 70.6%, Opus 5.5 66.4% (at `xhigh`), Fable 5.1 55.8%, Haiku 5.5 39.2%. xAI's: Grok 4.7
  37.6%, Fable 5.1 57.9% (both tables list it, and they roughly agree). The benchmark's own board, seen only
  second-hand, all at `max`: Opus 5.5 and Fable 5.2 65.8%, Sonnet 5.5 63.9%, GPT-6.2 Astra 62.1%, GPT-6.2 Sol
  61.5%, Grok 4.7 38.8%. Fable 5.2's launch post has no Terminal-Bench 4.0 score; on FrontierCode 2.0 it gives
  Fable 5.2 49.1% and Opus 5.5 45.5%. Grok 4.7 scores level with the cheap-tier Haiku 5.5 here, but level with
  Fable 5.1 on xAI's own DeepSWE v1.1 (71.0 vs 70.0): hence mid, behind Sonnet 5.5. No coding score was found for
  GPT-6.1 Terra: it is placed by its position in OpenAI's lineup (between Luna and Sol) and its price.
- **Passing the model.** In Claude Code, use the read-only `Explore` agent type for search sweeps, and pass
  `model:` whenever the tier differs from the session model - the built-in agent types otherwise run on the
  session model (in a Fable session `Explore` runs on Opus), and the per-call value is an alias, not a full model
  id. The subagent tools of Cursor, OpenCode 2 and Codex also take a model per launch (see
  `{{PACK}}/info/harnesses.md`), so the Cursor column picks subagent models as well as the session model.

Environment pins such as `ANTHROPIC_DEFAULT_SONNET_MODEL` override what an alias like `sonnet` runs, and on
Bedrock and Foundry the aliases resolve to older models (`sonnet` to Sonnet 4.5, `haiku` to Haiku 4.5), so a
brief's tier can change silently: check the model that actually ran.

## Effort + Thinking

The effort level is the owner's choice (owner, 2026-10-08): no rule, tool or file fixes one. Most harnesses
take a reasoning effort per delegated call: Claude Code as the Agent tool's `effort` parameter (2.1.292 and
later), Cursor as a model-ID suffix (`<model-id>[effort=high]`), OpenCode 2 as the model's variant
(`provider/model#high`), Codex per subagent launch (else `model_reasoning_effort`); OpenCode 1.x takes it only
per run (`--variant`) or agent definition. Pass the effort the owner chose; when he chose none, leave it out.
In Claude Code a subagent on the session's model then runs at the session's effort (set with `/effort`,
`--effort` or a setting; Solaris uses no agent definitions to pin it), and one on another model at that model's
own default (2.1.317 and later). The levels are `low`, `medium`, `high`, `xhigh` and `max`, and Haiku 5.5 takes
them too; Claude Code sessions on Opus 5.5, Sonnet 5.5, Haiku 5.5 and Fable 5.2 all default to `medium` (on the
API, Sonnet 5.5 and Fable 5.2 default to `high`); the owner picks the level. Leave extended thinking ON wherever
it is available - never worth toggling off per task.

## Keeping This Current

Model lineups and tier placements shift often. Under a Solaris checkout this file syncs from the
framework master (`solaris/info/model-tiers.md`) on every project update. Standalone, if the "as of"
date looks stale, verify the mapping against the harness's own model list and update this file (a
pack `refresh` is a good moment).
