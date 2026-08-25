---
name: ad-hoc-task
triggers: ["new task", "start a task", "work on tasks/<slug>", "resume task", "open tasks/<slug>", "research X", "set up <host/thing>", "ad-hoc: X"]
summary: Start/resume an ad-hoc engineering/system-setup/research task under tasks/<date>-<slug>/.
---

# ad-hoc-task <!-- omit in toc -->

1. [Start or Resume](#1-start-or-resume)
2. [Link to a Project (Optional)](#2-link-to-a-project-optional)
3. [Do the Work](#3-do-the-work)
4. [Capture + Close](#4-capture--close)
5. [Graduate (Optional)](#5-graduate-optional)

The command-center catch-all for work that is not a project. No ai-pack, no versioning - just a dated
folder with notes and scratch.

## 1. Start or Resume

Pick a `slug` (kebab) for the work. Folder: `tasks/<YYYY>/<MM>/<YYYY-MM-DD>-<slug>/` - tasks are filed
by year/month, and the leaf folder name keeps the full date prefix. When resuming or referencing, search
`tasks/*/*/*<slug>*` (tolerate a legacy flat `tasks/<YYYY-MM-DD>-<slug>/` if one still exists - file it
into its year/month on touch). If a recent matching folder exists, resume it; else create it (with the
year/month parents) holding a `notes.md`:

```
# <title>

<!-- Agent: this is an ad-hoc task. Load and follow the `ad-hoc-task` skill
     (solaris/skills/ad-hoc-task.skill.md) before working in this folder. -->

What / why: <one or two lines>
Project: <slug, or omit if not linked>

## Steps
## Findings
## Outcome
```

`notes.md` carries **no** `_Rev. N_` marker (it is per-task scratch, not a versioned framework file); once
it grows real content, keep a TOC per the docs convention (`uv run -m solaris.tools.toc --write <file>`).

## 2. Link to a Project (Optional)

A task can be marked as belonging to a project (at creation, e.g. "task for `<project>`", or later, e.g.
"link this task to `<project>`") - resolve `<slug>` the same way `develop-project` does (search
`projects/*/` then `projects/*/*/`; ask if ambiguous or absent). Record it as the `Project:` line in
`notes.md`'s header. On resume, read that line to detect an existing link.

When linked, layer the project's ai-pack **read-only** on top of this skill (do not adopt the engineer
persona or switch working directory - the task folder stays the write target, and `ai/engineer.agent.md`
itself is not read). First read `projects/<slug>/ai/manifest.json` for `mode` and any `workspaces`: in
`embedded` mode the ai-pack lives at `projects/<slug>/<repo>/ai/` instead - resolve every path below
against that root. Ignore workspaces (they scope where an *implementation* lands; a linked task never
writes into the project, so no workspace applies - if the request is workspace-specific, hand off to
`develop-project`). Then read, from the resolved `ai/` root: `engineer.instructions.md`, `spec.md`, every
`rules/*.rule.md`, every `skills/*.skill.md` (trigger-invoked), and every `plugins/<plugin>/` overlay
(`*.rule.md` always-on, `*.skill.md` trigger-invoked), following any `plugins/<name>.link.md` pointer to
its `shared/` rules and skills the same way `develop-project` step 2 does. Skip `ai/.memory/*`
(project-private) and, in `local`/`remote-code` mode, `source/AGENTS.md` (repo-carried rules for
implementation work, not relevant to a task that never touches the source tree).

Obey those rules/skills for this task's work, but this skill's file-location contract always wins: keep
all writes inside the task folder regardless of what a loaded project rule or skill says, and on any other
conflict between a loaded project rule and an ad-hoc-task instruction, this skill wins (the project layer
adds context and conventions, not authority - it never overrides how the task itself is run). If the user
asks to change project files (not just read them), stop and hand off to `develop-project` instead - this
skill never writes into `projects/<slug>/`.

## 3. Do the Work

- **Engineering / scratch:** write throwaway scripts and outputs inside the task folder. For Python scratch,
  a quick `uv run --no-project python ...` or a tiny local venv is fine - keep it inside the folder.
- **System setup:** when acting on a host, read it from `.memory/resources.md` (hosts table). Apply the
  safety rule: confirm before remote-mutating or destructive commands; show the command first.
- **Research:** use the `ctx7` CLI for up-to-date library docs (`ctx7 ...`); if it is not installed,
  suggest installing it (e.g. `npm i -g @upstash/context7`) and fall back to web/docs. Capture key
  findings in `notes.md`.

## 4. Capture + Close

Keep `notes.md` current (steps tried, findings, outcome) - it is the durable record. Append a line to
`.memory/interactions.jsonl`; if the task is linked to a project, append the same line to that project's
`ai/.memory/interactions.jsonl` too.

## 5. Graduate (Optional)

If the task turns into something durable, offer to promote it: a buildable thing -> `create-project` (or
`import-project`); a recurring domain workflow -> `import-plugin` (create). The task folder stays as history.

> Deferred for later versions (not v0): a hosts registry, a `run-remote` helper, dedicated `research` and
> `capture`/`recall` skills, and reusable `provision` recipes. For now, do these inline within a task.
