---
name: develop-project
triggers: ["work on <project>", "develop <project>", "open <project>", "plan <project>", "implement X in <project>"]
antitriggers: ["tasks/<slug>"]
summary: Hand off to a project's engineer agent (plan or implement) by loading its ai-pack + plugin overlays.
---

# develop-project <!-- omit in toc -->

1. [Locate the Project](#1-locate-the-project)
2. [Load the Engineer Context](#2-load-the-engineer-context)
3. [Act](#3-act)

Thin shim: switch the active persona to a project's **engineer** and carry out the user's request there.
This skill has no logic of its own beyond locating the project and loading its context.

On the first `develop-project` of a session, run the `health-check` overview first to orient (skip if it
already ran this session, or if the user says to skip), then proceed.

## 1. Locate the Project

Resolve `<slug>` to its project folder: search `projects/*/` then `projects/*/*/` (projects are grouped,
e.g. `projects/nv/<slug>/`, `projects/my/<slug>/`; `projects/<slug>/` below is shorthand for the resolved
path). If absent, list the projects from the same two-depth scan and ask. (If the user described an
existing codebase that is not yet a project, suggest `import-project`; for something brand new, suggest
`create-project`.)

`<pack>/` below is the project's ai-pack folder (default `aipack/`, `ai/` in projects made before 0.39.0,
any name): the one direct child folder of the project root whose `manifest.json` is an ai-pack manifest
(it has `framework_version` and a `project` object; plugin manifests do not).

## 2. Load the Engineer Context

Read, in this order, and then obey them:

1. `projects/<slug>/<pack>/<primary>.agent.md` - the project's **primary persona**, a combined coder + planner +
   runner (includes the embedded commit + safety policies). `<primary>` is `engineer` unless
   `<pack>/manifest.json` -> `agents.primary` renames it; every `engineer.*` name below means that file.
2. `projects/<slug>/<pack>/manifest.json` - name/type/mode + attached plugins.
3. `projects/<slug>/<pack>/instructions.md` (the one shared know-how store every persona reads and maintains:
   build/run/test, conventions, gotchas, lessons), `<pack>/spec.md`, and `<pack>/.memory/*` (private:
   `resources.md`, `credentials.md`).
4. Every `projects/<slug>/<pack>/rules/*.rule.md` - always-on pack rules (token economy, subagents delegation, YAGNI mode);
   their switches read `<pack>/defaults.json` overridden per key by `<pack>/.memory/config.json`, and the
   perishable reference data they point at (model tiers, harness capabilities) lives in `<pack>/info/*.md` -
   read the pointed-at file, never substitute memory. Treat each `<pack>/skills/*.skill.md` as
   trigger-invoked.
5. Every `projects/<slug>/<pack>/plugins/<plugin>/` overlay: load each `*.rule.md` (always-on) and treat each
   `*.skill.md` as an additional trigger-invoked skill. Follow every `<pack>/plugins/<name>.link.md` (a **linked**
   plugin - see `install-plugin` step 5): load the plugin's `shared/` rules and skills from the path it
   names, the same way.
6. If `mode` is `local`: `projects/<slug>/source/AGENTS.md` (if present) as gap-filling project rules
   (the ai-pack strictly overrides repo-carried rules on any conflict - flag, never silently defer). If `remote-code`:
   `projects/<slug>/remote.json` for the host/path; read the live `source/AGENTS.md` from the remote.
7. Every other `projects/<slug>/<pack>/<role>.agent.md` role brief, if present: personas the primary delegates
   to (or runs a whole session as) by telling the model to act as that file, at the brief's `tier` and
   read-only when its `access` says so; they inherit the primary persona's policies and share the same
   `<pack>/instructions.md` - when a read-only role returns lessons in its report, the primary writes them
   there.

**Embedded mode** (manifest `mode: embedded`): the ai-pack + `AGENTS.md` live *inside* the repo, so read the
context above from `projects/<slug>/<repo>/` (e.g. `projects/<slug>/<repo>/<pack>/engineer.agent.md`); there is no
separate `source/`.

Set the working directory to `projects/<slug>/source/` (local), `projects/<slug>/<repo>/` (embedded), or
operate over Remote-SSH against `remote.json` (remote-code).

**One machine at a time:** run `uv run -m solaris.tools.interactions who --dir projects/<slug>`. Exit 3 means
another machine logged work on this project within the last hour (it names the machine and when; the
reading is as of the last Syncthing sync). Then tell the owner and ask before writing anything under
`<pack>/.memory/` (`context.md`, schedules, job notes): two machines rewriting one file is what Syncthing
turns into conflict copies. Offer a handover (the session there saves its context and stops, following the
pack's `handover` skill) over running both; reading the project meanwhile is fine.

**Workspaces:** when the project has more than one workspace (manifest `project.workspaces`, or the
workspace table in `<pack>/instructions.md`), determine which one the request targets - from the
prompt, or ask when ambiguous - and work inside that folder, honoring the self-containment rules in
`<pack>/engineer.agent.md` (Workspaces): no file references into sibling workspaces; that workspace's
`setup.md`/`spec.md` are part of the deliverable.

## 3. Act

Follow the engineer agent's workflows:

- **Plan** (user wants design/changes scoped first): update `<pack>/spec.md` through dialogue; keep
  `<pack>/.memory/spec-v0.md` untouched. Hand to implementation only when the user approves.
- **Implement:** write code against the spec; run/test (locally or on the remote per mode); honor the
  embedded safety policy before any remote-mutating or outward action.
- **Learn:** when the user teaches a durable project preference, or any persona learns a durable lesson,
  update `<pack>/instructions.md` (keep it shareable - put any host/secret/internal-URL specifics in
  `<pack>/.memory/` instead, never dropped).
  When the knowledge is a trigger-shaped, occasionally-run multi-step procedure, **propose a project-local
  skill** (`<pack>/skills/<name>.skill.md`) instead of growing the instructions - create it only after the user
  agrees; the routing criteria live in the template `ai/engineer.agent.md` (Memory).
- **Personas:** when the user wants a new or changed role, create or edit `<pack>/<role>.agent.md` beside the
  primary (stub: `solaris/templates/agents/role.agent.md`; its know-how goes into the shared
  `<pack>/instructions.md`), validate with
  `uv run -m solaris.tools.agents --check --dir projects/<slug>`, and run `revs ff` so `<pack>/README.md` lists
  it; renaming the primary persona is `agents --rename-primary <role>`.
- **Log:** record the turn as one `{ts, project, prompt, request, outcome}` line (`prompt` the raw user
  prompt, `request` your interpretation, `outcome` the result) with
  `uv run -m solaris.tools.interactions add --dir projects/<slug> --project <slug> --prompt ... --request ...
  --outcome ...`: one call writes **both** this machine's file in the project's `<pack>/.memory/interactions/`
  and the framework master log (all work).
- **Save context:** keep the project's `<pack>/.memory/context.md` (the detailed session-context summary,
  rewritten in place) current at its save points: before context compaction (automatic or manual), and
  whenever the user says "save/remember/update/retain/keep context" or similar.

If a plugin or framework change emerges (the user changes how a domain workflow should behave), record it as
a dated suggestion in `<pack>/.memory/improvements.md` instead of editing the plugin or framework file: project work
never edits those unless the owner explicitly says so (AGENTS.md, non-negotiable 7). The orchestrator
implements approved suggestions later (`self-reflect`, "review project improvements"; `import-plugin` for
plugin files).
