---
name: kaggle-kind-llm-agents
triggers: ["agent competition", "llm competition", "llm-judged competition", "llm judge", "sampled scorer"]
summary: Kind notes for Kaggle competitions whose scorer runs a model or an agent - score bands, agent families, repeats, new evaluation hosts and GPU types, zero-weight adapter controls, local judges, per-run time limits, a one-pending-entry limit, structural fixes, model text kept out of the master. Read once the facts sheet records this kind.
---
_Rev. 3_

# Skill: kaggle-kind-llm-agents - Agent and LLM Scorers <!-- omit in toc -->

- [When This Applies](#when-this-applies)
- [Validation](#validation)
- [Submissions](#submissions)
- [Pitfalls](#pitfalls)

## When This Applies

The scorer runs a model or an agent (agents solving tasks under a harness, or a language model judging or generating
answers), so scores are sampled and depend on the scorer's environment. Read this once the facts sheet records the
kind (a contest can mix kinds: read each that fits), on top of the playbook. *General practice* marks widely known
practice for the kind, without evidence of our own yet.

## Validation

- **When scoring runs a sampled model or agent, identical submissions differ too:** one byte-identical public
  package, resubmitted by several teams, scored anywhere in a band that spanned the board's whole top group (about 5%
  of the public split). Measure that band from identical resubmissions (public copies of one package, found by parent
  and by attached dataset, pulled and diffed byte for byte, give it free), treat gaps inside it as ties, and rank
  candidates by many local runs, not by one board score. Where many teams resubmit one public package, their ranks
  track how many draws each took as much as any difference in quality.
- **Local solve rates can rank whole agent families backwards:** two local sets can agree that one family solves
  clearly more while the board ranks it lower. When families differ in per-task limits (time, turns, tool calls),
  measure how often their local solves end near those limits before trusting the order: hidden tasks that run longer
  get cut off, so a limit that costs nothing locally can cost many tasks on the board. Read the family difference on
  the board with one slot before building further on either family.
- **Pick among many variants with repeats.** The best of several runs of one family on the same tasks sits about one
  standard deviation above that family's mean; require a repeat run and a non-negative holdout read before claiming
  one variant beats another, and compare task by task (identical totals can hide many differing tasks). Judge a
  variant that should send the same requests by its behaviour (the requests and replies), not by solve counts.
- **Trust or dismiss a new evaluation host only after several paired runs** of the same packages on it and on an
  established host: two new hosts trailed by 5-10 of about a hundred items on their first two runs, then matched over
  the next four pairs, with every input (model files, server command line, environment, GPU health) identical. Before
  blaming a host, compare what it does on identical work: the verdicts on the same outputs, the results of the same
  calls.
- **Check a new GPU type's numerics on realistic inputs** before evaluating a model on a type the scorer does not
  use: compare its outputs with a reference GPU's on realistic prompts (the model's chat format with its
  beginning-of-sequence token, typical lengths); it passes when drift stays within the reference's own spread
  (loaded against idle) and greedy replies match. Raw text without that token is no fidelity test (it amplifies
  any change in reduction order: even two GPUs of one type disagree on it). Cold starts (empty inference and
  compile caches) reproduce each other exactly; a warm restart can differ as much as another GPU type. Such drift
  is far below sampling noise at a nonzero temperature: it matters for exact reproduction (start cold, like a fresh
  scoring session), not for comparisons on one host.
- **Read an adapter against a zero-weight adapter on the same serving path,** not against the bare base model:
  serving through an adapter changes speed and memory before any learned weight acts (the adapter path alone cost
  8.8% speed on one GPU type against 3.2% on the scorer's, and a timing gate run on the first type charged that
  gap to every adapter). Time the gap on the scorer's GPU type, and judge the trained adapter only by what it
  adds over the zero-weight one.
- *General practice:* where a model judges, build a local judge from the hosts' published rubric, prompt and judge
  model, and fit it to public notebooks' board scores. Judges tend to favour longer answers and their own style:
  know whether a gain is quality or bias.

## Submissions

- For agent competitions judged under a per-run time limit, microbenchmarks misjudged the scorer's speed: measure the
  local-to-scorer time factor from real traces under realistic load, time the finalist on a twin of the scoring
  machine, and plan for the worst case (every task at its cap). An overrun can score nothing.
- **Under a one-pending-entry limit with long scorer queues, plan the day's entry early:** when scoring takes many
  hours and Kaggle refuses a new entry while one is pending, an entry left for late in the day can find the slot
  blocked until the reset (two of ten slots were lost so). Have the day's entry built and checked a day ahead,
  submit it early in the Kaggle day, and retry each refusal at every check until the reset (the playbook core,
  Daily Submission Discipline).

## Pitfalls

- Prompt rules against a mid-size model's loops, call budgets or tool-call formatting slips were ignored: fixes that
  worked were structural (fewer or bounded tools, output caps, hard budgets in the harness's own config).
- **Model inputs and outputs stay in workers and counting scripts:** agent prompts, trajectories, smoke-run traces
  and model replies are read by a worker or a script that returns counts, hashes, diff stats and neutral summaries;
  the master reads only those (the kaggle rule). A master that printed part of an agent's system prompt into its own
  context, while looking for a build script, was stopped by the harness's safety classifier for 5 hours, which only
  the owner could clear; its work queues drained meanwhile. Build prompt variants in a worker too.
