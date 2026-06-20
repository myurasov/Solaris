---
name: ad-hoc-task
triggers: ["new task", "start a task", "work on tasks/<slug>", "resume task", "open tasks/<slug>", "research X", "set up <host/thing>", "ad-hoc: X"]
summary: Start/resume an ad-hoc engineering/system-setup/research task under tasks/<date>-<slug>/.
---

# ad-hoc-task <!-- omit in toc -->

- [1. Start or resume](#1-start-or-resume)
- [2. Do the work](#2-do-the-work)
- [3. Capture + close](#3-capture--close)
- [4. Graduate (optional)](#4-graduate-optional)

The command-center catch-all for work that is not a project. No ai-pack, no versioning - just a dated
folder with notes and scratch.

## 1. Start or resume

Pick a `slug` (kebab) for the work. Folder: `tasks/<YYYY-MM-DD>-<slug>/`. If a recent matching folder
exists, resume it; else create it with a `notes.md`:

```
# <title>

<!-- Agent: this is an ad-hoc task. Load and follow the `ad-hoc-task` skill
     (solaris/skills/ad-hoc-task.skill.md) before working in this folder. -->

What / why: <one or two lines>

## Steps
## Findings
## Outcome
```

## 2. Do the work

- **Engineering / scratch:** write throwaway scripts and outputs inside the task folder. For Python scratch,
  a quick `uv run --no-project python ...` or a tiny local venv is fine - keep it inside the folder.
- **System setup:** when acting on a host, read it from `memory/resources.md` (hosts table). Apply the
  safety rule: confirm before remote-mutating or destructive commands; show the command first.
- **Research:** use the `ctx7` CLI for up-to-date library docs (`ctx7 ...`); if it is not installed,
  suggest installing it (e.g. `npm i -g @upstash/context7`) and fall back to web/docs. Capture key
  findings in `notes.md`.

## 3. Capture + close

Keep `notes.md` current (steps tried, findings, outcome) - it is the durable record. Append a line to
`memory/interactions.jsonl`.

## 4. Graduate (optional)

If the task turns into something durable, offer to promote it: a buildable thing -> `create-project` (or
`import-project`); a recurring domain workflow -> `import-plugin` (create). The task folder stays as history.

> Deferred for later versions (not v0): a hosts registry, a `run-remote` helper, dedicated `research` and
> `capture`/`recall` skills, and reusable `provision` recipes. For now, do these inline within a task.
