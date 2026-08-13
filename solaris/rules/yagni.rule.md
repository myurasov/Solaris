# Rule: YAGNI Mode (Switchable, Off by Default) <!-- omit in toc -->

- [Switch](#switch)
- [The Rule](#the-rule)
- [Guardrails (Never Trimmed)](#guardrails-never-trimmed)

YAGNI ("You Aren't Gonna Need It", from Extreme Programming): build only what is actually needed
now. The plain instruction carries most of the effect - this rule stays short.

## Switch

`"yagni.enabled"` in `.memory/config.json` (framework) - `true`/`false`; key or file absent means
off. **Per-request override:** `yagni: on|off` anywhere in a user message applies to that request
only - acknowledge in one line, no config write. **Config changes only on explicit persistence
language** ("set/remember/from now on"). When on and delegating, restate the mode in every
subagent prompt it shapes (see the subagents rule's task contract).

## The Rule

While on: deliver exactly what was asked, in the smallest coherent form - fewest files, shortest
working diff, one solution (not variants). Before adding anything not literally requested, ask
"was this asked for?" - if no, drop it, or offer it in one line ("could also add X - say the
word"). Applies to code, framework artifacts, and documents alike. Banned while on: unrequested
features/options/flags; abstractions with a single caller; speculative generalization; unrequested
README/tests/examples/scaffolding; multi-variant deliverables; adjacent refactoring outside the
requested change; new infrastructure before a demonstrated need; defensive handling for failure
modes that cannot actually occur.

## Guardrails (Never Trimmed)

YAGNI shortens the **solution**, never the reading: full understanding of the problem, context
sweeps, and verification of the result are never skipped. Regardless of this mode, never simplify
away: input validation at trust boundaries (**user-supplied inputs - CLI args, paths, request
parameters - ARE a trust boundary**: a malformed input CAN occur; fail with a clean error, not a
traceback); error handling that prevents data loss; security and credential handling; the commit,
safety, and interaction rules. YAGNI trims features, not safety - and never justifies weakening
an existing rule, skill, or contract.
