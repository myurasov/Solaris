---
name: develop-project
triggers: ["work on <project>", "develop <project>", "open <project>", "plan <project>", "implement X in <project>"]
summary: Hand off to a project's engineer agent (plan or implement) by loading its ai-pack + plugin overlays.
---

# develop-project <!-- omit in toc -->

- [1. Locate the project](#1-locate-the-project)
- [2. Load the engineer context](#2-load-the-engineer-context)
- [3. Act](#3-act)

Thin shim: switch the active persona to a project's **engineer** and carry out the user's request there.
This skill has no logic of its own beyond locating the project and loading its context.

On the first `develop-project` of a session, run the `health-check` overview first to orient (skip if it
already ran this session, or if the user says to skip), then proceed.

## 1. Locate the project

Resolve `<slug>` to `projects/<slug>/`. If absent, list `projects/*` and ask. (If the user described an
existing codebase that is not yet a project, suggest `import-project`; for something brand new, suggest
`create-project`.)

## 2. Load the engineer context

Read, in this order, and then obey them:

1. `projects/<slug>/ai/engineer.agent.md` - the project's combined coder + planner + runner persona
   (includes the embedded commit + safety policies).
2. `projects/<slug>/ai/manifest.json` - name/type/mode + attached plugins.
3. `projects/<slug>/ai/engineer.instructions.md` (shareable build/run/test + conventions),
   `ai/spec.md`, and `ai/memory/*` (private: `resources.md`, `credentials.md`).
4. Every `projects/<slug>/ai/<plugin>/` overlay: load each `*.rule.md` (always-on) and treat each
   `*.skill.md` as an additional trigger-invoked skill.
5. If `mode` is `local`: `projects/<slug>/src/AGENTS.md` (if present) as project rules. If `remote-code`:
   `projects/<slug>/remote.json` for the host/path; read the live `src/AGENTS.md` from the remote.

Set the working directory to `projects/<slug>/src/` (local) or operate over Remote-SSH against
`remote.json` (remote-code).

## 3. Act

Follow the engineer agent's workflows:

- **Plan** (user wants design/changes scoped first): update `ai/spec.md` through dialogue; keep
  `ai/memory/spec-v0.md` untouched. Hand to implementation only when the user approves.
- **Implement:** write code against the spec; run/test (locally or on the remote per mode); honor the
  embedded safety policy before any remote-mutating or outward action.
- **Learn:** when the user teaches a durable project preference, update `ai/engineer.instructions.md`
  (keep it shareable - put any host/secret/internal-URL specifics in `ai/memory/` instead, never dropped).
- **Log:** record the turn as one `{ts, project, request, outcome}` line in **both** the project's
  `ai/memory/interactions.jsonl` (every project-relevant request + its outcome) and the framework master
  `memory/interactions.jsonl` (all work).

If a plugin edit emerges (the user changes how a domain workflow should behave), point them at
`import-plugin` to fold it back into the plugin source.
