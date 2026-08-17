# Rule: Token Economy <!-- omit in toc -->

- [Level Switch](#level-switch)
- [Always-On Floor](#always-on-floor)
- [Measures by Level](#measures-by-level)
- [Pacing](#pacing)
- [Hard Floors (Never Trimmed)](#hard-floors-never-trimmed)
- [Why (Measured)](#why-measured)

Governs how much enters the main context and how fast it is re-sent. Everything read into the main
transcript is re-billed on every later round-trip - the cheapest token is the one never loaded. Two
layers: an **always-on floor** (no quality trade-off at any level) and **graded measures** set by
the level. Delegation mechanics live in `subagents.rule.md`; this rule only sets its bulk-read
threshold (measure 9).

## Level Switch

`"economy.level"` in `.memory/config.json` (framework) - `off` / `med` / `full` / `auto`; key or
file absent means `med`.

- **`off`**: floor only, no graded measures.
- **`med`** (default): the standing posture.
- **`full`**: the budget-crunch bundle - maximum frugality within the hard floors. Measured 13-28%
  cheaper on heavy sessions at unchanged quality, but +5-8% on short light one-shots: for crunches
  and heavy sessions, not a free upgrade.
- **`auto`** (context-scaled): `med` while the session context is light; `full` once the session
  is provably heavy - current context past ~100k tokens, or any compaction having occurred.
  One-way ratchet within a session.

The subagents rule's `auto` posture follows the **resolved** level - the configured level, or what
`auto` resolves to right now.

**Per-request override:** `economy: off|med|full|auto` (alias `token-economy:`) anywhere in a user
message applies to that request only - acknowledge in one line, no config write. An unrecognized
value gets a one-line correction. **Config changes only on explicit persistence language**
("set/remember/from now on"). Pacing has its own override (`asap`, below).

## Always-On Floor

Applies at every level, including `off`:

- **Read budget.** For any file past ~200 lines, grep (or use its TOC/index) to locate the
  relevant section, then Read with offset/limit; whole-file reads are for known-small files or
  files whose full content the task genuinely needs. "Read X" in a skill means the relevant
  section of X. Hook-injected content (the read-first set, auto-loaded skill bodies) is already in
  context - never re-open those files unless editing them.
- **Unbounded files.** Bounded-by-design files (config, `.memory/instructions.md` at its ~100KB
  cap) may be read whole. Append-only stores are never read whole: tail-read the latest entries
  with a negative offset (`.memory/interactions.jsonl` - full scans only in `self-reflect`), grep
  for a known id, or filter through a tool. A new unbounded store gets its filtering access path
  decided before anything starts reading it.
- **Batching.** Independent tool calls (no data dependency, no decision between them, no shared
  mutated state) go in one parallel batch - never one call per message. A mechanical survey of 4+
  files you will summarize (not edit) is one batched shell sweep (`head -40 f1 f2 ...`; the
  `==> file <==` delimiters prevent misattribution), measured up to 3.7x cheaper than per-file
  Reads. Guardrails, never relaxed: Read (the tool) any file you are about to Edit - shell-read
  content is not registered for edits and you pay for the bytes twice; chunk sweeps past ~20 files
  so output caps cannot silently truncate the tail; prefer a targeted grep whenever the question
  allows early exit.
- **Prefix stability.** Time-varying fields (timestamps, counters) go at the END of always-loaded
  files, never the top; no timestamps in `AGENTS.md` or rule files. Edit an always-on file once
  per session with the change drafted fully first - never N incremental revisions of the same
  file.
- **Never re-read your own writes.** The Edit/Write result already proves the change landed; reuse
  evidence already in context instead of re-fetching it.

## Measures by Level

`off` = the floor alone; `med` and `full` add:

| # | Measure | `med` | `full` |
|---|---|---|---|
| 1 | Multi-file surveys | One shell sweep capped ~30-35 lines/file; ruled-out items get zero further reads | Same; metadata-only pre-triage allowed only past ~50 candidates |
| 2 | Read slicing | Grep-then-slice; slice via the Read tool when an Edit is likely | Med, plus shell slicing reserved for provably read-only surveys |
| 3 | Prior-art check | Grep the symptom / id / function name before any fix | Required before any fix or new artifact; named in the report |
| 4 | Schema learning | One sibling example over doc reads | Sibling example only; docs only when no example exists |
| 5 | Verification style | Counts where a count proves it (`grep -c`, exit codes) | Same, firmly - content verification whenever anything is ambiguous |
| 6 | Re-fetch discipline | Reuse in-context evidence over re-fetching | Med, plus prefer in-context knowledge for non-decision-critical data (flag staleness) |
| 7 | Enrichment sources | Skip when the primary material suffices - and say so | Skip by default - and say so |
| 8 | New-artifact size | Compact: trigger, imperative, one example | Minimal |
| 9 | Bulk-read delegation threshold (`subagents.rule.md`) | ~20k tokens | ~10k tokens |
| 10 | Round-trip discipline | Batch all independent calls; merge shell steps where safe | A round-trip only when its output gates the next step |
| 11 | Heavy command output | Output expected past ~2k tokens -> redirect to a log, read back a filtered slice (guardrails below). Commands only - NEVER an artifact you are writing | Default for every heavy command; open more of the log on any anomaly |
| 12 | Subagent return shape | Name the expected shape in every delegated prompt | Med, plus a hard size target ("under N lines"); schema-forced output where the harness supports it |

**Measure 11 guardrails** (mandatory when used): capture and echo the exit code first - never
infer success from a clean-looking tail; grep the log for error/warn/fail counts plus a bounded
tail; on any anomaly (odd exit code, nonzero error count, unexpected timing) open a larger slice
of the log before concluding - the redirect trades away incidental noticing, this buys it back;
unique log names in the session scratchpad (or /tmp); exempt when the output IS the deliverable;
if a sandbox makes the redirect cost an escalation, bounded inline output is cheaper.

## Pacing

Keep round-trips/min <= budget / current context tokens. Budget: `"economy.tokens_per_minute"` in
`.memory/config.json`, an integer or k/m shorthand (`700k`, `1m`); absent = 1m. At 1m that is
<=10/min at 100k context, <=5/min at 200k. When over, re-batch the remaining work into fewer,
larger calls or pause briefly - a pause beats a rate-limit retry loop. Never poll long-running
work on short intervals - use background monitors with filtered output and space out checks; every
check re-sends the whole context. Pacing is level-independent, and a pacing rule, not a hard cap:
correctness and data integrity win over speed - when in doubt, slow down rather than drop steps.
**Per-request override:** `asap` anywhere in the message - burst for that request only.

## Hard Floors (Never Trimmed)

Never save tokens by: skipping verification the safety rule requires (same-turn checks of action
claims); truncation-reading a rule, skill, or artifact you are about to modify or condense
(wording passes read the whole thing); skipping the interaction log or `context.md` save points;
weakening confirmation, secrets, or memory duties. Token savings are a tiebreaker among correct
approaches, never a reason to degrade correctness.

## Why (Measured)

2026-08 calibration (~200 graded runs plus headed follow-ups, three models): heavy-session spend
is dominated by context re-sends (~60% observed), so savings compound; `full` measured
Pareto-optimal on heavy real work on every model tested, with quality unaffected at every level.
Survey caps: ~30-35 lines/file covers most file summaries at ~57% of full-read cost; a 15-line cap
covered none and forced re-reads. Counts vs content for a yes/no check: ~2,000x cheaper (a
counts-ONLY mandate bought nothing - the preference stays, the mandate does not). Stronger models
apply frugality more intelligently - economy is what lets a frontier-tier main session with
cheap-tier delegation cost about what an un-economized mid-tier session does.
