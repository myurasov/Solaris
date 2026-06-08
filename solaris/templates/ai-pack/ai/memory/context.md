# {{NAME}} - context <!-- omit in toc -->

- [How to use this file](#how-to-use-this-file)
- [Standing context](#standing-context)
- [Log](#log)
- [Previous History](#previous-history)

Verbose, model-facing working memory for {{NAME}}: the running context a future session needs to continue
immediately. Richer than `interactions.jsonl` (which stays the terse `{ts, project, prompt, request, outcome}`
machine record) - here each entry captures what was asked, what the agent did and answered, the decisions,
and the findings, in prose. Private/local layer; companion to `../spec.md` (the contract) and
`../engineer.instructions.md` (build/run). Gitignored - never shared or committed.

## How to use this file

- **Who writes:** only this project's engineer agent and Solaris's own agents (the orchestrator + skills).
  Plugins and subagents do not write here.
- **When:** append one entry per meaningful turn - the same trigger as the `interactions.jsonl` line, but
  with the model's actual narrative (answers, reasoning, decisions, file references), not just the outcome.
- **Where:** newest entry at the top of `## Log` as one self-contained block, so concurrent sessions rarely
  collide (we usually run one engineer at a time).
- **Size:** keep `## Log` under ~100KB. When it grows past that, compact (summarize) the oldest entries into
  `## Previous History` until Log is back under the cap - never delete, always condense.
- **TOC:** after structural edits, regenerate with `uv run -m solaris.tools.toc --write` on this file.

## Standing context

Durable, curated orientation that survives compaction: what this project is, the code map, how to run and
deploy it, and the gotchas worth never relearning. Seeded on import; keep it tight and rewrite it in place.

## Log

<!-- newest first; one block per meaningful turn: "### YYYY-MM-DD HH:MM - <title>" then the verbose entry -->

## Previous History

<!-- compacted summaries of entries evicted from Log once it grows past ~100KB; condensed, never deleted -->
