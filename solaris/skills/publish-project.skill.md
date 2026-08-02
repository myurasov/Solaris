---
name: publish-project
triggers: ["publish project", "share project", "publish <project>", "share <project>", "make <project> shareable", "prepare <project> for handoff", "hand off <project>", "de-personalize <project>", "publish-safety audit"]
summary: Prepare a project for external eyes - scrub identities/internals, add license/disclaimer, verify the detached ai-pack stands alone.
---

# publish-project <!-- omit in toc -->

1. [Scope the Handoff](#1-scope-the-handoff)
2. [Scrub Sweep](#2-scrub-sweep)
3. [Legal + Framing](#3-legal--framing)
4. [Containment Check (Detached ai-pack)](#4-containment-check-detached-ai-pack)
5. [Final Pass + Record](#5-final-pass--record)

Prepare a project (or any repo produced here) to be shared outside: with a customer, a third party, or the
public. Everything below is a checklist against the **tracked** content only - the local-only layers
(`__*/`, `ai/.memory/`) stay behind by design. `ai/engineer.instructions.md` is shareable and **ships**:
relocate any machine-local notes in it (wrapper registry, host specifics) to `ai/.memory/` as part of the
sweep. This skill
is read-mostly; every fix is shown as a diff and confirmed before it is made, and nothing is pushed or
published without explicit confirmation (safety rule).

## 1. Scope the Handoff

Establish with the user: **who** receives it (named partner vs public), **what** ships (whole repo, subset,
or a fresh clone), and **which history** (existing history can leak earlier, unscrubbed content - a
squashed or fresh-history copy is often safer; if fresh, the first commit is titled exactly
"Initial commit" per `commits.rule.md`).

## 2. Scrub Sweep

Grep the tracked files (all text formats: md, code, configs, notebooks) for each class; fix or
placeholder-ize every hit:

- **Identities + provenance:** internal people's names, meeting/recording provenance ("per the call
  with..."), internal team or partner-relationship framing (no "them/their" about the recipient).
- **Infrastructure:** internal hostnames, IPs (10.x / 192.168.x / Tailscale), internal URLs, usernames in
  paths. Replace with placeholders or env vars. Collapse incidental internal topology (e.g. multiple
  internal service instances) to one neutral mention.
- **Framework internals:** blocked-command wrappers (`hss`, `nepo`, `/tmp` wrapper registry), sandbox /
  permission workarounds, Solaris-root paths - none of these belong in shared content.
- **Secrets:** keys, tokens, credentials files; also check history if it ships (`git log -p` spot-check or
  a secrets scanner).

## 3. Legal + Framing

- **LICENSE** - ask which (or confirm "none, proprietary handoff"); add the file, not a README line.
- **DISCLAIMER / support expectations** - for PoC or research code, a short "provided as-is" note.
- **README** reads for an outsider: no internal context assumed, Title Case headings, TOC, setup steps a
  stranger can follow.

## 4. Containment Check (Detached ai-pack)

A shared ai-pack must behave with **only** the shipped folder present - no Solaris root, hooks, or
framework memory around it:

- Any **linked** plugin (`ai/<name>.link.md`) cannot resolve outside - convert to a copy install first
  (`install-plugin`, link -> copy).
- `AGENTS.md` / `ai/` contain no reference that must **resolve** outside the project root (no live
  `solaris/...`, `plugins/...`, framework-`.memory/...` paths; the template's conditional "when working
  under a Solaris checkout, tools are available" note is fine - it self-disables when detached).
- **Test from a clone outside the Solaris tree** (this is the only reliable leak test - running it inside
  the tree inherits Solaris hooks and read-first context): fresh clone to a neutral path, open it cold,
  and confirm the pack loads and the agent stays inside the folder.

## 5. Final Pass + Record

Re-run the step-2 greps clean, show the user a summary of every change made, and stop - pushing /
transferring is a separate, confirmed action. Log one line to `.memory/interactions.jsonl` (and the
project's `ai/.memory/interactions.jsonl`).
