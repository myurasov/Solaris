# {{NAME}} - context <!-- omit in toc -->

- [How to use this file](#how-to-use-this-file)
- [Session context](#session-context)

A **detailed summary of the current session's context** for {{NAME}}: what is being worked on, what was
decided and why, what was found, and where things stand - everything a fresh session (or this session after
compaction) needs to continue immediately. Complements `interactions.jsonl` (the terse
`{ts, project, prompt, request, outcome}` machine record). Private/local layer; companion to `../spec.md`
(the contract) and `../engineer.instructions.md` (build/run). Gitignored - never shared or committed.

## How to use this file

- **Who writes:** only this project's engineer agent and Solaris's own agents (the orchestrator + skills).
  Plugins and subagents do not write here.
- **When (save points):** rewrite `## Session context` **in place** (replace, don't append):
  1. **Before context compaction** - when the conversation context is about to be compacted, automatically
     (auto-compaction imminent) or manually (the user compacts) - save first, so no detail is lost.
  2. **On request** - whenever the user says "save context", "remember context", "update context",
     "retain context", "keep context", or similar.
- **What:** a detailed prose summary of the session so far: the task(s) and their current state, decisions
  with their reasons, findings, key file references, open questions, and immediate next steps. Durable
  cross-session knowledge does not live here - route it to `../engineer.instructions.md` (how),
  `resources.md` (what exists), or `../spec.md` (the contract).
- **Read:** at session start (and right after a compaction), read this file first to restore context.
- **TOC:** after structural edits, regenerate with `uv run -m solaris.tools.toc --write` on this file.

## Session context

<!-- rewritten in place at each save point; empty until the first save -->
