# Solaris v0.30.0 - Specification <!-- omit in toc -->

- [Overview](#overview)
- [Repository layout](#repository-layout)
- [Dual-IDE wiring](#dual-ide-wiring)
- [Execution model](#execution-model)
- [Projects and the ai-pack](#projects-and-the-ai-pack)
- [Project modes](#project-modes)
- [Plugins](#plugins)
- [Versioning: revisions + semver](#versioning-revisions--semver)
- [Command center (tasks)](#command-center-tasks)
- [Memory and interaction logging](#memory-and-interaction-logging)
- [Tools](#tools)
- [Conventions](#conventions)
- [Validation (acceptance)](#validation-acceptance)
- [Deferred](#deferred)

Authoritative description of Solaris v0.30.0. Supersedes the 0.1.0-0.29.0 specs (in git history; the latest prior snapshot is
[`spec-v0.29.0.md`](spec-v0.29.0.md)), alongside the original brief [`spec-v0.txt`](spec-v0.txt) and the v0.1.0
build plan [`plan-v0.1.0.md`](plan-v0.1.0.md). What changed in 0.30.4 (patch): the generated README's derived blocks are **legacy-layout aware** - on a pre-0.28.0 pack whose plugin overlays still live at `ai/<name>/` (or whose link pointer is still `ai/<name>.link.md`), `{{PLUGINS}}` and `{{SKILLS}}` now render their paths and links against that legacy home, so every README reference resolves even before the pack migrates; the detection lives in shared `_plugin_home`/`_link_ref` helpers that `materialized_map` reuses. Tool fix only; no migration (patch). What changed in 0.30.3 (patch): **the generated ai-pack README grows into a full front door** - the `How-To` section (renamed from "How-To: Everyday Workflows") gains Start a Work Session, Run and Test the Project, Save and Resume Context, Release a Project Version, and Manage Plugins walkthroughs; a **Quick Start** box opens the file; an **Available Skills** menu renders from a new derived `{{SKILLS}}` placeholder (trigger-invoked skills of the pack plus attached plugins, with trigger phrases parsed from skill frontmatter); a **Workspaces** list renders from `{{WORKSPACES}}`; the intro carries a project-description line (`{{DESCRIPTION}}`, resolved from a new `project.description` manifest field - template `manifest.json` rev 4, seeded by `create-project`/`import-project`); and file references are clickable links. Template `ai/README.md` rev 2. No migration (patch). What changed in 0.30.2 (patch): **every ai-pack carries a generated `ai/README.md`** - a short, human-readable pack overview (what an ai-pack is, what this particular one contains, the `defaults.json` configuration switches, and how-to walkthroughs for the everyday workflows: init, refresh, updating the pack, git collaboration), structured h2/h3 with a TOC. It is a rev-tracked template (`templates/ai-pack/ai/README.md`, rev 1) rendered through the existing revisions machinery via a new derived `{{PLUGINS}}` placeholder (the attached-plugins list from `ai/manifest.json`), so it re-renders whenever the pack syncs or its plugin set changes: `create-project`/`import-project` materialize it with `revs ff`, `install-plugin` refreshes it after any `plugins[]` change, and existing packs receive it on their next `update-project` (`revs ff` writes missing files). No migration (patch). What changed in 0.30.1 (patch): the bundled **nvidia-brev plugin (0.1.1)** fixes the `brev-setup` login procedure so an agent session can no longer hang on authentication: `brev login` runs as a **background task** (it blocks until the browser flow completes, so a foreground call hangs), the browser is allowed to auto-open - **never `--skip-browser`**, which does not make login non-interactive (it only prints the URL and still blocks; it stuck a real session), and `--email`/`--token` are no substitute for the browser step; an expired mid-project session re-enters the same procedure, and success is confirmed by polling `brev ls` (skill `brev-setup` rev 4). No framework or ai-pack change; no migration (patch). What changed in 0.30.0 (see [`../migrations/0.30.0.md`](../migrations/0.30.0.md)): **token economy joins the always-on rules** - a new `rules/token-economy.rule.md` (pack copy `ai/rules/token-economy.rule.md`, rev 1), distilled from field-calibrated co-sa practice, governs how much enters the main context and how fast it is re-sent: an always-on floor (grep-then-slice read budget past ~200 lines, unbounded-file discipline, parallel + batched-shell-read batching with guardrails, prefix stability, never re-read your own writes), 12 graded measures at `"economy.level"` `off`/`med`/`full`/`auto` (absent = `med`; `auto` = context-scaled one-way ratchet to `full` past ~100k tokens or a compaction), pacing (round-trips/min <= `"economy.tokens_per_minute"` over current context; `asap` override), and hard floors token savings never trim. The **subagents rule is rewritten around it** (pack rev 3): an always-on bulk-read floor (a lookup pulling >~20k tokens of raw results runs in a subagent, ~10k at economy `full`; a no-subagent-tool harness runs it checkpointed inline instead of falling back to `off`) plus postures `off`/`auto`/`quality`/`cost` (absent = `auto`, following the resolved economy level; old `med`/`full` stay as aliases; `quality`/`cost` pick the model tier - both delegate aggressively). `read_first` grows a third SessionStart part; `ai/defaults.json` gains `"economy.level"` (`med`) and defaults `"subagents.level"` to `auto`. Templates: engineer agent rev 39, harnesses info rev 2. Migration `0.30.0.md` (revs ff + defaults keys). What changed in 0.29.0 (see [`../migrations/0.29.0.md`](../migrations/0.29.0.md)): **every project carries its own semver** in a plain-text `.version` file at the project root (embedded mode: the repo root) - seeded by `create-project` (`0.1.0`) and `import-project` (adopt the repo's highest `v*` tag, else `1.0.0` for already-shipped work, else `0.1.0`), and for existing packs by the 0.29.0 migration (asking: `1.0.0` if the project has shipped to others, else `0.1.0`). The engineer **proposes** a bump when a milestone lands - never bumps silently; an explicit "bump/release the project" always works - and each approved bump is committed single-line and locally tagged `v<X.Y.Z>` when a git repo tracks the project root (tag pushes stay confirm-first per the safety policy). Tooling: `solaris.tools.version project|project-set|project-bump --dir <project>`; `health-check` verifies presence + validity. The project version is a third, fully independent mechanism next to per-file revisions and framework/plugin semvers: `.version` carries no rev marker, is never materialized from a template, and is never touched by `revs classify/ff/baseline`. Templates: engineer agent rev 38 (Project Version section), manifest stub rev 3, init stub rev 8 (seed check). What changed in 0.28.0 (see [`../migrations/0.28.0.md`](../migrations/0.28.0.md)): **ai-packs get a dedicated plugin home** - plugin shared files now materialize under `ai/plugins/<name>/` (and link-mode pointer files move to `ai/plugins/<name>.link.md`) instead of the pack root's `ai/<name>/`, so pack-owned dirs (`ai/rules/`, `ai/skills/`, `ai/info/`) and plugin overlays no longer share a namespace; `revs classify`/`ff` map plugin files to the new home (a not-yet-migrated pack that still has only the legacy dir classifies against it until moved), and the skill-loader hook indexes overlay files in both homes. The bundled **report plugin is renamed `reporting`** (plugin 0.2.0, with its own `migrations/0.2.0.md`): its materialized copy becomes `ai/plugins/reporting/` and report HTML sources link the shared stylesheet at the new path; `browserctl` (0.2.1) and `visual-qa` (0.2.1) update their self-referenced overlay paths, including the visual-qa MCP server command. What changed in 0.27.1 (patch): the bundled **report plugin (0.1.1)** stamps each PDF's page-1 byline with the **actual author** - `report.json`'s `prepared_by` is now an optional fixed override, and when unset the renderer derives "Prepared by <name> <email>" from the rendering developer's git identity (`git config user.name`/`user.email`), falling back to a plain "Rendered on ..." outside a git identity. No framework or ai-pack change; no migration (patch). What changed in 0.27.0 (see [`../migrations/0.27.0.md`](../migrations/0.27.0.md)): **ai-packs ship a git collaboration workflow** (`ai/rules/git-collab.rule.md`) - every developer works on a personal `<id>-develop` branch (`<id>` from the gh login, else the git email local-part, else the name slug; cached with its source email in `ai/.memory/config.json` and re-derived when the email changes), with an automatic switch/create **before any commit** that would land on `main`/`develop`; commits on the developer's own personal/feature branches are **automatic** under the commit policy's format rules, while any other branch (a colleague's, review checkouts, detached HEAD) keeps the confirm-first posture; **pushes are never automatic** - back-contribution happens only on an explicit ask ("create a PR / publish / push upstream") as a PR against the default branch, with a no-`gh` fallback (push + compare URL). Feature requests branch `feature-<descr>` from the current branch and `--no-ff` merge-commit back when done. Switches (committed `ai/defaults.json`, per-machine override in `ai/.memory/config.json`): `"git.developer_branches"` (off = main-developer mode: working on `main` is fine) and `"git.feature_branches"`, both default true. The init stub onboards the developer branch and gains a **skip-any-resource option** (e.g. proceed GPU-host-less to explore); the refresh stub pulls `main` and merges it back into the personal branch; `import-project` now seeds `ai/defaults.json`. Pack-only behavior (the framework's own repo keeps its usual workflow); templates: engineer agent rev 35, init rev 6, refresh rev 8. Migration `0.27.0.md` (revs ff + add-if-absent key merge). What changed in 0.26.0 (see [`../migrations/0.26.0.md`](../migrations/0.26.0.md)): a new bundled plugin **`plugins/report/`** brings findings-report authoring + rendering to any project: one self-contained HTML source per report (`reports/html/`, gitignored working files) styled by a tokenized shared stylesheet, rendered to tracked PDFs with **zero npm dependencies** (installed Google Chrome driven over the DevTools protocol; page-1 and pages-2+ header passes merged with poppler `pdfunite`; `$CHROME` overrides the binary). Theme and page furniture are **project-owned config** outside the plugin copy - `reports/theme.css` (CSS token overrides on `.viz-root`; defaults: Helvetica Neue body font, Solaris purple `#6A1B9A` accent; NVIDIA projects override to NVIDIA Sans + NVIDIA green `#76b900`) and `reports/report.json` (`prepared_by` byline, `watermark`, `furniture_font`) - so plugin updates never clobber project identity. Supporting it, `solaris.tools.revs` learns rev markers for `.js`/`.ts` (`// rev. N`), `.css` (`/* rev. N */`), and `.sh` (`# rev. N`, placed under the shebang so scripts stay directly executable), and plugin ledgers/materialized-file maps now track every marker-capable file **recursively** under `shared/` (previously flat `shared/*.md` only), so nested plugin assets sync per file like everything else. Additive; no ai-pack schema change; marker migration `0.26.0.md`. What changed in 0.25.2 (patch): **the migration chain spans patch gaps** - `solaris.tools.version`'s `find_chain` now returns every migration with `from_v < to_version <= to_v` in order, instead of walking exact `from_version` links; a patch release sitting between two migration points (e.g. 0.22.3, 0.23.1 - patches never author migrations) used to break the walk and silently report "no migrations" for a project that genuinely needed them (found updating a 0.22.2 project to 0.25.1). `from_version` frontmatter stays informational. Tool fix only; no migration (patch). What changed in 0.25.1 (patch): **packs carry the info layer as files** - the condensed model-tier table embedded in `ai/rules/subagents.rule.md` (now rev 2) moves into a new revision-tracked **`ai/info/`** pack folder (`model-tiers.md`, `harnesses.md` - adapted from the `solaris/info/` masters, framework plumbing left out); the pack rules hard-require the files (tier choices come from `ai/info/model-tiers.md`, never memory - a missing file means a broken pack to repair, not a fallback), `revs` tracks `ai/info/*.md` so `ff` materializes them into existing packs, and a test pins the framework and pack "as of" dates together (the `refresh`/`release` staleness checklists now name the pack copies). Templates: engineer agent rev 33, pack `AGENTS.md` rev 15. No migration (patch): `update-project`'s `revs ff` brings the files. What changed in 0.25.0 (see [`../migrations/0.25.0.md`](../migrations/0.25.0.md)): **ai-packs gain `ai/rules/` + `ai/skills/` folders and config defaults** - always-on pack rules live in `ai/rules/` (two new ones, ported in concept from field use and re-authored for Solaris: **subagents** - leveled delegation of self-contained work to tier-matched subagent models, levels `off`/`med`/`full` with `med` the default, a 5-point task contract for every delegated prompt, and a per-request `subagents: <level>` override; and **YAGNI mode** - opt-in deliver-exactly-what-was-asked in the smallest coherent form, `yagni: on|off` per request, with hard guardrails: trust-boundary validation, data-loss handling, security, and safety rules are never trimmed), trigger-invoked skills live in `ai/skills/` (the init/refresh stubs move there from flat `ai/`), and behavior switches read the committed `ai/defaults.json` overridden per key by the private `ai/.memory/config.json`. The framework carries both rules in `solaris/rules/` (switches in `.memory/config.json`) and auto-loads them as read-first **part 2** - a second SessionStart hook call, because Claude Code's 10,000-char inline threshold applies per hook call. A new **`solaris/info/`** layer holds perishable reference data (`model-tiers.md`, `harnesses.md`) that rules cite abstractly and the pack templates embed (synced via revisions; staleness checks in the `refresh` and `release` skills). `revs` now tracks pack rules and skills alongside `AGENTS.md` and the engineer agent, so `revs ff`/`classify` genuinely sync the stubs. Pack schema change (folder move); migration `0.25.0.md`. Templates: engineer agent rev 32, pack `AGENTS.md` rev 14. What changed in 0.24.0 (see [`../migrations/0.24.0.md`](../migrations/0.24.0.md)): **refresh flows learn rewritten upstream history** - both the ai-pack `refresh` skill (template rev 6) and a **new framework `refresh` skill** (`solaris/skills/refresh.skill.md`, triggers "refresh/update solaris") diagnose a failed fast-forward: when local-only commits are just pre-rewrite versions of what upstream now carries (a force-pushed history rewrite, e.g. an author/committer cleanup), they adopt the new history (`git reset --hard origin/<branch>` after confirmation, re-applying genuinely local work; tags refreshed with `--force`) instead of merging the old and new histories together; genuine divergence still stops for a user decision. The framework skill also resyncs the environment (`uv sync`, hook-change restart detection, MCP check), verifies (tests, version, `read_first --check`, revs), and flags projects needing `update-project` (`update-project` gains antitriggers so "update solaris" routes to it). The **structured release-notes style is codified** in the `release` skill and the engineer template's Commit Policy (rev 31): notes cover the full tag-to-tag diff, ~200 words, `##` sections with bold-led bullets, migration pointer, every path/placeholder backtick-quoted (GitHub strips unquoted angle-bracket tokens). Additive; no ai-pack schema change. What changed in 0.23.1 (patch): bundled-plugin refinements only. **browserctl** (plugin 0.2.0) moves its machine-local state root from `~/.browserctl/` to `~/.solaris/browserctl/` (per the remote/local footprint convention; `$BROWSERCTL_HOME` still overrides), launches through a branded `Solaris Browser` app bundle with a tinted icon (auto-built on first launch on macOS, `icon` command to refresh), defaults new profiles to the purple theme, and tightens tab hygiene in the drive commands. **aisee** documents the server's `transcribe` and `diarize` query kinds (lane contract, lane results with progress percent, `diarize_model` selection, long-video guidance), 256k-context models with GiB-based memory gating, and retry-later admission refusals. No framework or ai-pack change; no migration (patch). What changed in 0.23.0 (see [`../migrations/0.23.0.md`](../migrations/0.23.0.md)): projects are **grouped** one level below `projects/` (`projects/<group>/<slug>/` - current groups `nv/`, `my/`, `tmp/`; slugs resolve via a two-depth scan of `projects/*/` and `projects/*/*/`, and `projects/<slug>/` remains the docs shorthand) and ad-hoc tasks file by month under `tasks/<YYYY>/<MM>/<YYYY-MM-DD>-<slug>/`. A new always-on **interaction rule** (`rules/interaction.rule.md`, mirrored in the engineer template as an Interaction Policy) mandates answer-the-question-first replies (explicit answer in the first line, requested word counts honored, a mid-autonomous question is not a resume signal) and the writing style (brevity by default, no consultant buzzwords, jargon explained with a short parenthetical). The safety rule gains **Long-Running Remote Work** duties: verify a job's pace within its first iteration, re-verify external state after a harness restart, and confirm every remote delete/stop with a same-turn list. Interaction-log `ts` is UTC (`Z` suffix, from a real clock). The `skill_loader` hook now also injects a **per-project overlay index** (that project's `ai/*.rule.md`, `ai/<plugin>/*.rule.md`, `ai/*.link.md`, one line each, once per session) whenever a prompt or the session cwd targets a project - overlay compliance no longer depends on the agent walking AGENTS.md by hand. The `read_first` packer reserves pointer space so the inline payload can never exceed its budget (now 9.5KB), with the interaction rule third in inline priority. browserctl (skill rev 8) documents the real invocation paths (`ai/browserctl/` copy vs `plugins/browserctl/shared/` link) and `install-plugin` computes link-file depth for grouped layouts. Additive; no ai-pack schema change. What changed in 0.22.3 (patch): documentation - the README is reframed into numbered sections led by a "What is Solaris" overview, with clarified section headings and a proper ordered-list table of contents; and the ai-pack template's `init` / `refresh` skill stubs get their `_Rev. N_` markers repositioned after the YAML frontmatter (completing the 0.22.2 GitHub-rendering fix for the template skill files). What changed in 0.22.2 (patch): in files that open with YAML frontmatter (skill files), the `_Rev. N_` marker now sits **right after the closing `---`** instead of on line 1 - GitHub only renders frontmatter that starts the file, so marker-first skill files displayed as a broken horizontal rule + text blob. `revs` places (and migrates) the marker automatically, hash-neutrally; loaders and `toc` accept both positions. What changed in 0.22.1 (patch): the browserctl plugin's `slack-web` skill documents the client-side Web API recipe for **thread-reply file attachments** (invisible to Slack's search/fetch layers): boot the app.slack.com client URL, read the session token from `localStorage.localConfig_v2`, call `conversations.replies` on the app.slack.com API host, and fetch `url_private_download` through the browser context's cookies. What changed in 0.22.0 (see [`../migrations/0.22.0.md`](../migrations/0.22.0.md)): the engineer persona (template `engineer.agent.md` rev 25, mirrored in `develop-project`) gains a **knowledge-routing rule** - durable knowledge that is a **trigger-shaped, occasionally-run multi-step procedure** (onboarding, data staging, capture/import/release/deploy flows; signals: numbered start-to-finish steps, a screen or more, own preconditions/verification/guardrails, stale on ordinary turns) is **proposed as a project-local skill** (`ai/<name>.skill.md`, modeled on `ai/init.skill.md`, with a one-line pointer left in the instructions) instead of being inlined into the every-turn `ai/engineer.instructions.md`; the skill is created **only after the user agrees** (ask-first for now), while facts, commands, gotchas, and conventions keep living in the instructions file. Content-only; no ai-pack schema change. What changed in 0.21.0 (see [`../migrations/0.21.0.md`](../migrations/0.21.0.md)): a new bundled plugin **`plugins/browserctl/`** makes browser automation **CLI-based**, replacing the Playwright MCP server as the standard browser layer (the `playwright` entry is removed from `mcp.json.example`; no MCP servers ship by default). Playwright stays the engine: `shared/browserctl.py` (PEP 723 inline deps, run via `uv run`) launches the Playwright-managed Chromium directly, one persistent profile per purpose on a stable CDP port - profiles are namespaced **per project** (id auto-derived from the nearest `ai/manifest.json`), created **clean** on first use or project init (`init`), and **ephemeral on demand** (`launch --ephemeral` / `--fresh` siblings, `stop` deletes, `prune` sweeps, `persist` / `remove` manage exceptions). CLI drive commands (`tabs`/`navigate`/`snapshot`/`screenshot`/`eval`) cover the common MCP tools and an `attach()` helper exposes the full Playwright-Python API over CDP; state lives under `~/.browserctl/`, machine-local and disposable. The plugin also carries `shared/slack-web.skill.md` - a use-case skill for operating the Slack web client through browserctl (scroll-and-snapshot capture of virtualized panes, thread handling, attachment downloads via the authenticated session, and guarded react/post write actions). Additive and opt-in per project; no ai-pack schema change. What changed in 0.20.2 (patch): rev markers are scoped to materialized masters only (templates/ai-pack, templates/workspace, plugin shared/) - stripped from README, AGENTS.md, the orchestrator file, skills, the 0.18.0 migration, the current spec, and the Python tools, where they were inert metadata that had already caused two parser bugs; the scope rule is codified in solaris.agent.md, the release skill, and the spec. What changed in 0.20.1 (patch): the ai-pack is now
explicitly **standalone-first** - a shared/detached pack must work with no Solaris framework around it, so
every `solaris.tools` / framework-path reference in the templates (and bundled plugin overlays) is either
removed, given a tool-free alternative, or marked "under a Solaris checkout" (safely ignored standalone);
`_Rev. N_` markers and the manifest `revisions` map are declared Solaris sync metadata that standalone
collaborators leave as-is (merge conflicts in them: take either side; the Solaris-side maintainer
re-records the baseline). Content-only; no ai-pack schema change. What changed in 0.19.0 (see [`../migrations/0.19.0.md`](../migrations/0.19.0.md)): the framework's own memory folder moved from `memory/` to `.memory/` (matching the ai-pack's `ai/.memory/`; the `read_first` session hook auto-renames a legacy checkout on first access); a new **`publish-project`** skill prepares a project for external handoff (scrub sweep, license/disclaimer, detached-pack containment check); the ai-pack template gains `ai/init.skill.md` + `ai/refresh.skill.md` stubs (onboarding / update a teammate's checkout) and "Local-Only Folders" (`__*/`, gitignored) + "Remote Host Discipline" instruction sections; the commits rule extends to upstream contributions (PR/issue format, "Initial commit", single-line precedence) and the safety rule gains a git/gh **identity preflight**; `install-plugin` declares the canonical `ai/<name>/` overlay layout and link-vs-copy guidance; the skill loader now skips synthetic turns (task notifications) and tolerates rev-marked skill files (which previously never auto-loaded); `toc` handles rev-marker + frontmatter preambles and skips `.memory/`; the release skill bumps `solaris/__init__.py` and runs the test suite. What changed in 0.18.0 (see [`../migrations/0.18.0.md`](../migrations/0.18.0.md)): the ai-pack's private memory directory moved from `ai/memory/` to `ai/.memory/` (breaking), and the bundled **nvidia-brev** plugin was added (autonomous Brev cloud-GPU run lifecycle: brev-setup + brev-run skills over a pristine upstream brev-cli mirror). What changed in 0.17.2 (patch): a new precedence hard rule - the ai-pack (engineer agent, `ai/*` rules and instructions, plugin overlays) **strictly overrides** repo-carried conventions (`source/AGENTS.md`, `CLAUDE.md`, CONTRIBUTING): repo rules fill gaps only, and conflicts are flagged, never silently deferred to (templates `AGENTS.md` rev 9 + `engineer.agent.md` rev 19, orchestrator, `develop-project` skill). The `toc` tool's `--all` walker now skips `.venv*` variants and the content trees (`projects/`, `plugins/`, `tasks/`, `memory/`), scoping the docs check to framework files. No ai-pack schema change. What changed in 0.17.1 (patch): documentation housekeeping - the agent files (orchestrator `solaris.agent.md` and the ai-pack `engineer.agent.md` template) are slimmed, with machine-local tooling notes relocated to the instructions layer (framework `memory/instructions.md`; per-project `ai/engineer.instructions.md`, where a project may freely edit or delete them); the ai-pack template's commit/safety section headers drop their parenthetical qualifiers; and headers and titles use Title Case across docs, rules, skills, and templates. Content-only; no ai-pack schema change. What changed in 0.17.0 (see [`../migrations/0.17.0.md`](../migrations/0.17.0.md)): a new bundled plugin **`plugins/aisee/`** ships with the framework - knowledge-only "eyes" for visual verification during development, backed by the standalone **AISee** service (github.com/myurasov/AISee: vision-language models served on a GPU host; `look` / `assert_visual` / `watch` queries). The plugin carries `shared/aisee.rule.md` (always-on conventions: when the visual leg runs, assert-over-look, evidence and media rules) and `shared/aisee.skill.md` (trigger-invoked procedure: reach server -> capture -> query -> report, preferring **MCP over streamable HTTP** with local media uploaded once to the server's content-addressed blob store and referenced as `sha256:<hex>`, with REST and CLI fallbacks), plus `mcps.json` (an `http`-type MCP entry whose placeholder URL is substituted at install from the `aisee_server` setup resource, alongside the idempotent `playwright` capture entry) and install-time setup resources (server URL; optional consumer bearer token, stored as a secret). Additive and opt-in per project; no ai-pack schema change. What changed in 0.16.0 (see [`../migrations/0.16.0.md`](../migrations/0.16.0.md)): a second, opt-in plugin install mode - **link**. Instead of copying a plugin's `shared/` into `ai/<name>/`, `install-plugin` ("link plugin X to Y") writes a single self-describing pointer file **`ai/<name>.link.md`** naming the live `plugins/<name>/` source; the manifest entry is `{name, "mode": "link"}` (**no** `version` - a linked plugin always runs the live source, so there is nothing to drift). MCP merge and `setup` run exactly as in a copy install. The revs tools (`classify` / `ff` / `baseline`) skip linked entries, and `version check-plugins` reports them as `linked, source <v> (live)` (a missing source is a hard break: there is no materialized fallback). Link mode is a machine-local development convenience - used while authoring a plugin so edits hit the source directly, with no `import-plugin` fold-back - and swaps in place with a copy install in either direction ("install plugin X to Y" / "link plugin X to Y"); "unlink/detach" removes the attachment. Canonical definition: `install-plugin` step 5. Additive ai-pack manifest extension (optional `plugins[]` `mode` key); templates `AGENTS.md` rev 8 + `engineer.agent.md` rev 17 teach the engineer to follow link files. What changed in 0.15.1 (patch): the ai-pack `AGENTS.md` template now surfaces the project **slug** alongside its name/type/mode (`Project **{{NAME}}** (slug `{{SLUG}}`) ...`), so a generated pointer file identifies which `projects/<slug>/` it belongs to. Template wording only; no ai-pack schema change. What changed in 0.15.0 (see [`../migrations/0.15.0.md`](../migrations/0.15.0.md)): `ai/.memory/context.md` is **redefined** - from an append-only, model-facing context log (Standing context / newest-first Log / Previous History) to a **detailed summary of the current session's context**, rewritten **in place** at two save points: **before context compaction** (automatic or manual - save first so no detail is lost), and whenever the user says "save/remember/update/retain/keep context" or similar. The engineer reads it first at session start (and right after a compaction) to restore context; per-turn logging stays in `interactions.jsonl`, and durable knowledge routes to `engineer.instructions.md` / `resources.md` / `spec.md`. Content-only (new `context.md` template, `engineer.agent.md` rev 16, orchestrator + skill wording); no ai-pack schema change - existing projects convert their `context.md` by hand via the migration. What changed in 0.14.0 (see [`../migrations/0.14.0.md`](../migrations/0.14.0.md)): a new always-on **remote-footprint rule** joins core (and the ai-pack `engineer.agent.md` template): everything Solaris installs on a remote host (services, tools, config, model/data caches) lives under **`~/.solaris/<component>/`** so the footprint is discoverable, inventoriable, and removable in one place; every installer ships with an uninstaller, and each install (host + path) is recorded in the relevant `resources.md`. The bundled **visual-qa plugin advances to 0.2.0** (with its first plugin migration): serving moves from a single `serve.sh` to lifecycle scripts (`install.sh` / `start.sh` / `status.sh` / `stop.sh` / `uninstall.sh`) that follow the remote-footprint rule and support multiple resident model instances (one container + port + GPU-memory slice each; `PORT=random` picks and persists a free port); `eyes.py` gains a serving-instance registry (`use` / `pick`), native video ingestion (fps sampling, chunked re-encode), and a `watch` tool for temporal assertions; `models.json` is expanded and re-ranked; the plugin README is consolidated at the plugin root. Content-only; no ai-pack schema change. What changed in 0.13.0 (see [`../migrations/0.13.0.md`](../migrations/0.13.0.md)): a new bundled plugin **`plugins/visual-qa/`** ships with the framework - a GPU-agnostic "eyes" for visual end-to-end testing (a pluggable vision-language model behind an OpenAI-compatible endpoint; `look` / `assert_visual` tools as an MCP server + CLI in `shared/eyes.py`; and a GPU-aware model recommender that ranks VLMs by VRAM + architecture + task over `shared/models.json`), plus a `serving/` runbook for vLLM / NIM / Ollama verified on a DGX Spark GB10. The **plugin-tracking model** is also generalized: a plugin may be its own git repository (ignored via `plugins/.gitignore`, e.g. `nvidia-isaac-lab`) **or** bundled in-framework under `plugins/` (tracked, keeping its own `revisions.json`); the blanket `plugins/*` gitignore is retired. Additive; no ai-pack schema change. What changed in 0.12.1 (patch): **skills are now auto-loaded by a hook** - a new stdlib tool `solaris.tools.skill_loader` is wired to Claude Code's `UserPromptSubmit`, matching each prompt against every skill's declared `triggers` (minus optional `antitriggers`) and injecting the full body of any match (once per session, then a one-line reminder), so the right procedure is in context without being opened by hand; `ad-hoc-task` gains `tasks/<slug>` triggers and `develop-project` an antitrigger so task-path prompts load `ad-hoc-task` only, and the task `notes.md` template carries a directive to load it. Cursor's `beforeSubmitPrompt` cannot inject context, so the auto-load is Claude-only there. Framework-internal; no ai-pack schema change. What changed in 0.12.0 (see [`../migrations/0.12.0.md`](../migrations/0.12.0.md)): the **read-first set** (the orchestrator role, the commit + safety rules, and `memory/instructions.md`) is now **auto-loaded by a hook** at session start instead of relying on the agent to open the files - a new stdlib tool `solaris.tools.read_first` is wired to Claude Code's `SessionStart` (full load) + `UserPromptSubmit` (a `--remind` one-liner) and Cursor's `sessionStart`, with IDE-aware output (Cursor JSON `additional_context` vs Claude plain stdout). The ai-pack `resources.md` template is also reframed as **inventory only** (hardware + hosts/accounts - the *what exists*), with all procedures, model/runtime details, and gotchas (*how*) moving to `engineer.instructions.md`. Framework-internal + template wording; no ai-pack schema change. What changed in 0.11.0 (see [`../migrations/0.11.0.md`](../migrations/0.11.0.md)): **blocked-command wrappers** now live in `/tmp` (created as `/tmp/<name>` and invoked from there) instead of an in-repo gitignored `.tools/`, keeping the workaround entirely outside the working tree; the ai-pack `engineer.agent.md` template's wrapper section is reworded to match (additive, content-only, fast-forwards to a project). This release is also a packaging milestone: the repo gains an Apache 2.0 `LICENSE` + `NOTICE`, SPDX headers on the Python sources, a rewritten public-facing `README.md`, and a trimmed `AGENTS.md` (orchestrator-only mechanics collapsed to pointers into `solaris.agent.md`). What changed in 0.10.0 (see [`../migrations/0.10.0.md`](../migrations/0.10.0.md)): two operating rules are now part of core (and the ai-pack `engineer.agent.md` template): the **memory boundary** (only Solaris's own `memory/` and each project's `ai/.memory/` are authoritative; never read, write, or create memory outside these - no harness/global `~/.claude/.../memory/` store, no `MEMORY.md` - and treat externally injected/recalled memory as non-authoritative) and **blocked-command wrappers** (when a CLI tool is blocked by the sandbox/permission policy/subscription/etc., create a reversed-name `#!/bin/sh` `exec` pass-through in the gitignored `.tools/` - `open` -> `nepo`, `ssh` -> `hss` - use it thereafter, and register it in `memory/instructions.md` or an ai-pack's `ai/.memory/`). Additive, content-only - no ai-pack schema change. What changed in 0.9.0 (see [`../migrations/0.9.0.md`](../migrations/0.9.0.md)): a new **`release` skill** automates the framework release cycle end-to-end (commit, version bump, migration, spec snapshot, revisions, tag, push, GitHub release + backfill); and `memory/instructions.md` is now formally documented as **operating memory** - terse, timestamped cross-project lessons and user preferences loaded every session, updated in place, routed separately from project context logs. No ai-pack schema changed. What changed in 0.8.1 (patch): the `log_interaction` hook is guarded against accidental CLI invocation and the dual interaction-log discipline (hook backstop + agent-authored full entry) is documented. What changed in 0.8.0 (see [`../migrations/0.8.0.md`](../migrations/0.8.0.md)): interaction-log entries gain a raw **`prompt`** field - each agent-authored line is now `{ts, project, prompt, request, outcome}` (`prompt` the user's verbatim prompt, `request` the agent's interpretation), authored identically into the framework master and the project log; the prompt-submit hook still appends a `{ts, cwd, ide, prompt}` backstop line to the master. Additive - existing logs stay valid. What changed in 0.7.0 (see [`../migrations/0.7.0.md`](../migrations/0.7.0.md)): the private working-context file `ai/.memory/info.md` is renamed to **`ai/.memory/context.md`** and redefined as a **verbose, model-facing context log** - richer than `interactions.jsonl`, capturing the model's own answers/decisions/findings in prose, with a curated "Standing context" section that survives compaction, a newest-first "Log", and a "Previous History" of compacted older entries once Log grows past ~100KB; only the engineer and Solaris agents write it. What changed in 0.6.1: the **embedded** layout is clarified - the whole project repo (code + `ai/` + `AGENTS.md`/`README`/dotfiles + its own `.git`) lives at `projects/<slug>/<repo>/`; the slug folder is a non-git container for the repo plus non-repo aux; and the repo's `.gitignore` excludes `ai/.memory/` **and** `.secrets.env`. What changed in 0.6.0 (see [`../migrations/0.6.0.md`](../migrations/0.6.0.md)): the local-mode code directory is renamed **`src/` -> `source/`** (`projects/<slug>/source/` - the engineer's working dir, what `--remote` rsyncs, and where the project's own `git init` runs; a nested `ui/src/` etc. is unaffected). A new **opt-in `embedded`
project mode** also lets the ai-pack live *inside* the source repo (`projects/<slug>/<repo>/ai/`, no separate
`source/`), chosen at create/import time. What changed in 0.5.0 (see [`../migrations/0.5.0.md`](../migrations/0.5.0.md)): `ai/manifest.json` holds only project metadata + versions - host/deploy/port/secret specifics live in `ai/.memory/` (`resources.md` / `credentials.md`); the engineer **bootstraps `ai/.memory/` interactively** when it is missing (a shared ai-pack); and each **plugin keeps its own** revision ledger at `plugins/<name>/revisions.json` (the framework `solaris/revisions.json` tracks only framework masters). What changed in 0.4.1: a minimal `CLAUDE.md` (`@AGENTS.md`)
shim is restored beside every `AGENTS.md` so **Claude Code** loads the canonical instructions (Cursor reads
`AGENTS.md` natively). What changed in 0.4.0 - a terminology + conventions release
(see [`../migrations/0.4.0.md`](../migrations/0.4.0.md)): the project persona **`developer` -> `engineer`**
(`developer.agent.md` -> `engineer.agent.md`, `developer.instructions.md` -> `engineer.instructions.md`) and
the **ai-setup -> ai-pack** (the `solaris/templates/ai-pack/` template dir, the `version` tool's `aisetup`
subcommand -> `aipack`, and the term throughout). Two conventions are now explicit: a project's `ai/spec.md`
is **self-sufficient** (reads standalone, references no other file), and **every change to a revisioned file
increments its rev**. Interaction logging is also clarified - each turn is one
`{ts, project, prompt, request, outcome}` record (`prompt` the raw user prompt, `request` the agent's
interpretation): the framework `memory/interactions.jsonl` is the master of every turn (incl handed-off
project work), a project's `ai/.memory/interactions.jsonl` its relevant slice. What
changed in 0.3.0: `engineer.instructions.md` moved out of `ai/.memory/` up to `ai/`
- the shareable, portable layer alongside `engineer.agent.md` and `spec.md` - leaving `ai/.memory/` as the
private/local layer; see [`../migrations/0.3.0.md`](../migrations/0.3.0.md).

## Overview

**What changed in v0.25.1 (patch):** packs carry the info layer as files - a new revision-tracked `ai/info/` folder (`model-tiers.md`, `harnesses.md`, adapted from the `solaris/info/` masters) replaces the tier table embedded in the pack subagents rule (rev 2); pack rules hard-require the files (never memory, no fallback), `revs ff` materializes them into existing packs, and a test keeps the framework and pack "as of" dates matched. Templates: engineer agent rev 33, pack `AGENTS.md` rev 15; no migration.

**What changed in v0.25.0:** ai-packs restructured - always-on pack rules in `ai/rules/` (new leveled **subagents** delegation rule, default level `med`, and opt-in **YAGNI mode**, both with per-request `subagents:`/`yagni:` overrides), trigger-invoked skills in `ai/skills/`, behavior switches in committed `ai/defaults.json` + private `ai/.memory/config.json` (framework: `.memory/config.json`); the framework rules load as read-first part 2 (second SessionStart hook call, 10k inline threshold is per call); new `solaris/info/` perishable-data layer (`model-tiers.md`, `harnesses.md`); `revs` tracks pack rules/skills; migration `0.25.0.md` (folder move + re-baseline).

**What changed in v0.24.0:** refresh flows learn rewritten upstream history (adopt a force-pushed rewrite instead of merging it; ai-pack `refresh` rev 6 + a new framework `refresh` skill that also resyncs the env, verifies, and flags stale projects); the structured full-diff release-notes style is codified in the `release` skill and the engineer template (rev 31).

**What changed in v0.23.0:** grouped project folders (`projects/<group>/<slug>/`, two-depth slug resolution, `projects/<slug>/` stays the shorthand) and month-filed tasks (`tasks/<YYYY>/<MM>/<date>-<slug>/`); a new always-on interaction rule (answer-the-question-first + writing style, embedded in the engineer template); Long-Running Remote Work duties in the safety rule; UTC `Z` interaction-log timestamps; per-project overlay-index injection by the skill loader; overflow-proof read-first packing (9.5KB budget); browserctl invocation-path fixes and computed link-file depth.

**What changed in v0.22.0:** the engineer routes **trigger-shaped, occasionally-run procedures** to **project-local skills** (`ai/<name>.skill.md`) instead of growing the every-turn `engineer.instructions.md` - proposed to the user and created only on approval, with a pointer line left in the instructions; facts, commands, gotchas, and conventions stay in the instructions file.

**What changed in v0.21.0:** browser automation becomes **CLI-based** through the new bundled **`plugins/browserctl/`** plugin, retiring the Playwright MCP server as the standard (removed from `mcp.json.example`; no MCP servers ship by default). Playwright remains the engine - `browserctl.py` launches its Chromium directly, one persistent profile per purpose on a stable CDP port, with **per-project namespaces**, **clean first-use profiles**, and **ephemeral profiles on demand**; CLI drive commands plus an `attach()` Playwright-Python escape hatch replace the MCP tools, and a bundled `slack-web` skill captures/operates the Slack web client through it.

**What changed in v0.20.0:** projects gain first-class **workspaces** - one or more self-contained top-level work tracks per project (own `setup.md`/`spec.md`/deps, no file references into siblings; shared inputs live outside; `source/` is the default and a flat project has just that one; the single ai-pack is shared across all). Stubs live at `solaris/templates/workspace/`; the manifest records `project.workspaces` when a project has more than the default; create/import/develop and health-check are workspace-aware. The committed ai files also gain **git-collaboration conventions** (template `engineer.agent.md`, Authoring ai Files): hard-wrapped prose, stable headings, tool-generated TOCs, mechanical resolution for `ai/manifest.json` `revisions` conflicts (`revs baseline`), and `*.jsonl merge=union` in `.gitattributes` for committed append-only logs.

Solaris is a minimal framework for running many coding projects from one place (a "command center"). For
each project it generates a standardized, **portable ai-pack** that also works opened on its own.
Employer/domain-specific ways of working are factored into **plugins**. Ad-hoc engineering, system-setup,
and research work that is not a project lives under `tasks/`.

Solaris targets **Cursor** and **Claude Code** equally via a single canonical `AGENTS.md`: Cursor reads it
natively, Claude Code via a one-line `CLAUDE.md` (`@AGENTS.md`) shim. Its own tooling is Python (>=3.14), stdlib-only at runtime, run through `uv`.

## Repository layout

```
<root>/                         # the Solaris git repo
  AGENTS.md                     # canonical, always-on instructions (Cursor reads it natively)
  CLAUDE.md                     # one-line @AGENTS.md shim so Claude Code loads AGENTS.md
  mcp.json.example              # MCP template (no default servers); copied to runtime configs
  pyproject.toml  uv.lock       # python >=3.14; runtime stdlib only; pytest for tests
  .cursor/hooks.json  .claude/settings.json   # interaction-log + read-first loader hooks (both IDEs)
  .githooks/commit-msg          # commit-policy enforcement (opt-in)
  solaris/                      # the framework (python package: solaris, solaris.tools)
    solaris.agent.md            # orchestrator role
    revisions.json              # rev + content-hash ledger for tracked framework files
    spec/  skills/  rules/  info/  migrations/  templates/  tools/  tests/  # info/: perishable reference data (model tiers, harness capabilities)
  plugins/                      # plugin sources (gitignored except .empty)
  .memory/                      # framework memory, gitignored except .empty (resources, credentials, interactions)
  projects/                     # user projects (gitignored)
  tasks/                        # ad-hoc work (gitignored)
```

Every `AGENTS.md` has a sibling one-line `CLAUDE.md` (`@AGENTS.md`) so Claude Code loads it; there is **no
`.cursor/rules`** anywhere. Gitignored: `.venv`, `.tmp`, `.tools`,
`.mcp.json`, `.cursor/mcp.json`, `projects/`, `tasks/`, `solaris/spec/references/`, and `plugins/*` /
`.memory/*` (each except its `.empty`). A `.empty` placeholder keeps those two fully-ignored dirs present on
a fresh clone; the first time a skill writes real content into one, it deletes that `.empty`.

## Dual-IDE wiring

`AGENTS.md` is the single canonical instruction file. **Cursor** reads it natively; **Claude Code** reads a
one-line `CLAUDE.md` shim (`@AGENTS.md`) that imports it - only `AGENTS.md` is authored, and both load it
every turn. MCP is configured by a committed `mcp.json.example` (no servers by default - browser automation is CLI-based via the `browserctl` plugin); the user copies it
to `.mcp.json` (Claude Code) and `.cursor/mcp.json` (Cursor), and `solaris.tools.mcp_sync` keeps the two in
sync. `context7` is used via its CLI (`ctx7`), not as an MCP server. The interaction-log and read-first
loader hooks live in `.cursor/hooks.json` and `.claude/settings.json`; the skill-loader hook is Claude-only (`.claude/settings.json` `UserPromptSubmit`).

## Execution model

One running agent adopts a **persona** by reading the active context: at the Solaris root, the
**orchestrator** (`solaris/solaris.agent.md`) routes to skills and manages projects/plugins/tasks; inside a
project, the **engineer** (`projects/<slug>/ai/engineer.agent.md`) loads the ai-pack, every
`ai/plugins/<plugin>/` overlay, and `source/AGENTS.md` if present. "Hand off" = switching the active instruction set +
working directory.

## Projects and the ai-pack

A project lives at `projects/<group>/<slug>/` - projects are grouped one level below `projects/` (current groups: `nv/` NVIDIA work, `my/` personal, `tmp/` throwaway). A slug resolves by scanning `projects/*/` then `projects/*/*/` for a folder holding an ai-pack; `projects/<slug>/` throughout the docs is shorthand for the resolved path. The project root carries `AGENTS.md` (Cursor) + a one-line `CLAUDE.md`
(`@AGENTS.md`, Claude Code) plus `ai/` and, in local mode, `source/` (which carries the same `AGENTS.md` +
`CLAUDE.md` pair when it has project rules). There is no `.cursor/`, `mcp.json.example`, or `.gitignore` - the
folder is not committed. Runtime `.mcp.json` and `.cursor/mcp.json` are generated (gitignored) so the IDE has MCP
servers; plugin servers are merged into them on install.

```
projects/<slug>/
  AGENTS.md                     # the authored root instructions (Cursor)
  CLAUDE.md                     # one-line @AGENTS.md shim (Claude Code)
  .mcp.json  .cursor/mcp.json   # runtime MCP (gitignored)
  ai/                           # shareable layer (agent + instructions + spec + rules/ + skills/ + info/ + defaults.json)
    README.md                   # generated pack overview + how-to (rev-marked; {{PLUGINS}}/{{WORKSPACES}}/{{SKILLS}}/{{DESCRIPTION}} render from the manifest + skill files)
    engineer.agent.md          # combined coder + planner + runner (carries a rev marker)
    engineer.instructions.md   # shareable build/run/test commands + conventions (no host/secret specifics)
    manifest.json               # project {name,slug,type,mode,description}, framework_version, plugins[], revisions{}
    spec.md
    defaults.json               # committed behavior defaults (flat keys, e.g. "subagents.level", "economy.level", "yagni.enabled")
    rules/                      # always-on pack rules (rev-marked): subagents.rule.md  token-economy.rule.md  yagni.rule.md
    skills/                     # trigger-invoked skills (rev-marked): init.skill.md  refresh.skill.md  + project-local
    info/                       # perishable reference data (rev-marked): model-tiers.md  harnesses.md
    .memory/                    # private/local layer (not for sharing): env-specific + sensitive bits
      spec-v0.md  resources.md  credentials.md  context.md  interactions.jsonl  config.json
    plugins/                    # plugin home: materialized overlays + link-mode pointer files
      <plugin>/                 # materialized plugin overlay(s): copies of each plugin's shared/ (rev-marked)
      <plugin>.link.md          # OR a linked plugin: self-describing pointer to the live plugins/<name>/ source
  source/                          # local mode: code (own .git) - the DEFAULT workspace | remote-code: replaced by remote.json
  <workspace>/                     # optional additional workspaces: self-contained tracks (own setup.md + spec.md)
```

In **embedded** mode the ai-pack lives *inside* the source repo instead of beside it. The repo (its own
`.git`) sits at `projects/<slug>/<repo>/` (name it `source` or after the repo) and holds **everything** - the
code, `ai/`, `AGENTS.md` + `CLAUDE.md`, `README`, and the repo's own dotfiles. The slug folder
`projects/<slug>/` is then a **non-git container**: the repo plus any non-repo local aux (e.g. `references/`,
`screenshots/`) that should not ship with the repo. The repo's `.gitignore` keeps the private layer out -
both `ai/.memory/` and any `.secrets.env`:

```
projects/<slug>/                # container (not a git repo)
  references/  screenshots/      # non-repo local aux, kept outside <repo>
  <repo>/                        # THE repo (its own .git); e.g. "source"
    .gitignore  .secrets.env     # .gitignore excludes ai/.memory/ + .secrets.env
    AGENTS.md  CLAUDE.md  README.md
    ai/                          # the ai-pack, embedded in the repo
      README.md  engineer.agent.md  engineer.instructions.md  spec.md  manifest.json  defaults.json  rules/  skills/  info/  .memory/  plugins/<plugin>/
    ...                          # the repo's own code + files
```

Tools that take `--dir` get `projects/<slug>/<repo>/` (the dir holding `ai/`) for an embedded project.

**Workspaces.** A project's code lives in one or more workspaces - self-contained top-level folders, each
its own track of work: own `setup.md` (from-scratch bring-up ending in an end-to-end verification), own
`spec.md`, own deps and scratch (`__*/`); no imports, relative paths, or symlinks into a sibling workspace
(shared inputs - datasets, common assets - live outside workspaces, e.g. `data/`). `source/` is the
default workspace, and a flat project has just that one. The ai-pack is **single and shared** at the
project root - never per workspace; `ai/spec.md` stays the project-level spec and points at workspace
specs. Additional workspaces are materialized from `solaris/templates/workspace/{setup.md,spec.md}`
(`{{WORKSPACE}}` + `{{NAME}}` substitution), registered in the `engineer.instructions.md` workspace table
and, when a project has more than the default, in the manifest `project.workspaces` array. Canonical rules:
the ai-pack template `engineer.agent.md` (Workspaces).

The ai-pack splits into a **shareable layer** (`ai/README.md` (the generated pack overview), `ai/engineer.agent.md`, `ai/engineer.instructions.md`,
`ai/spec.md`, the always-on `ai/rules/*.rule.md` with their committed `ai/defaults.json`, the
`ai/skills/*.skill.md` stubs (`init`, `refresh`), the `ai/info/*.md` reference data, and the `ai/plugins/<plugin>/` overlays - portable, safe to share or hand off) and a **private/local
layer** (`ai/.memory/`: hosts, secrets, internal URLs, the per-machine `config.json` overrides, the preserved spec, the session-context summary, logs). To share an ai-pack, drop
`ai/.memory/`. It is materialized from `solaris/templates/ai-pack/` with placeholder substitution (`{{SLUG}}`,
`{{NAME}}`, `{{TYPE}}`, `{{MODE}}`, `{{DESCRIPTION}}`, `{{FRAMEWORK_VERSION}}`, `{{DATE}}`, plus the
derived blocks rendered into `ai/README.md`: `{{PLUGINS}}` (attached-plugins list from the manifest),
`{{WORKSPACES}}` (workspace list from the manifest), `{{SKILLS}}` (trigger-invoked skills menu from the
pack and attached plugins' skill files), and `{{DESCRIPTION}}` resolving there to a project-description
line from `project.description`). Project types
come from core (`solaris/templates/projects/*.md`) plus plugin-provided `plugins/<name>/<type>.project.md`;
choosing a plugin-provided type auto-attaches that plugin.

## Project modes

- **local** (default): code in `projects/<slug>/source/` (own git root). Run locally; deploy by rsync over SSH
  (excludes `.venv`/`.git`/secrets/artifacts; no `--delete` by default); optional Docker.
- **remote-code**: no `source/`; a `remote.json` records `host` + `path`. The code lives on the remote; it is
  edited and run in place over Remote-SSH. No deploy by default. The mode is recorded in `ai/manifest.json`.
- **embedded** (opt-in): the ai-pack lives *inside* the source repo. `projects/<slug>/<repo>/` (e.g.
  `source`) is the **whole** repo (its own `.git`) - code, `ai/`, `AGENTS.md` + `CLAUDE.md`, `README`,
  dotfiles - and the slug folder above it is a non-git container for the repo plus non-repo aux
  (`references/`, `screenshots/`). The shareable layer commits and travels with the repo; the repo's
  `.gitignore` excludes `ai/.memory/` **and `.secrets.env`**. Chosen explicitly at create/import time; tools
  take `--dir projects/<slug>/<repo>/`.

## Plugins

A plugin adapts Solaris for a domain/employer/repo-specific way of working. A plugin is either its **own
git repository** - `install-plugin` acquires one from a remote git URL, a local folder, or a source zip
into `plugins/<name>/`, and `plugins/.gitignore` ignores it so it is never nested-committed (e.g.
`nvidia-isaac-lab`) - **or authored in-place and bundled** in the framework repo under `plugins/` (tracked;
e.g. `visual-qa`). Either way `install-plugin` validates/repairs the plugin source and optionally attaches
it to a project, each plugin keeps its own `revisions.json`, and the framework ledger never tracks plugin
files. The layout is flat (only `migrations/` is a subfolder):

```
plugins/<name>/
  manifest.json                 # name, version (semver), description, applies_to, optional setup (install prompts/notes)
  mcps.json                     # MCP servers merged into a project's runtime MCP on install
  <type>.project.md             # optional project-type(s) this plugin contributes
  shared/                       # the ONLY files attached to a project: copied to ai/plugins/<name>/, or linked (each rev-marked)
    *.skill.md  *.rule.md
  migrations/                   # <to_version>.md for the plugin's own minor/major bumps
```

Opted into per project (`ai/manifest.json` `plugins[]`); the engineer agent loads each `ai/plugins/<name>/*.rule.md`
(always-on) and `*.skill.md` (trigger). Only `shared/` is materialized. Attachment comes in two modes:
**copy** (the default - `{name, version}` in `plugins[]`, `shared/` copied to `ai/plugins/<name>/`) and **link**
(`{name, "mode": "link"}`, no `version` - a self-describing pointer file `ai/plugins/<name>.link.md` names the live
`plugins/<name>/` source, which the engineer loads directly; the revs tools and plugin migrations skip
linked entries, and `version check-plugins` reports them as live). Link mode is a machine-local development
convenience for plugin authoring and swaps in place with a copy install in either direction; canonical
definition in `install-plugin` step 5. `install-plugin` installs (copy `shared/` or write the link file,
merge `mcps.json` into the runtime MCP, run the plugin's `setup` prompts), updates/repairs via the revision
sync below, and migrates on the plugin's own minor/major bumps. `import-plugin` authors a plugin
from a project's domain specifics or folds project-local edits back into `shared/`; `install-plugin`
acquires/repairs a plugin source (git/folder/zip) and attaches it to a project.

The bundled `nvidia-isaac-lab` plugin carries the NVIDIA/Isaac workflow (NVBugs prep/triage/try-and-fix/
handoff, fork->develop git + PR conventions, `isaaclab.sh` CI checks, review-bot replies) and the NVBugs MCP.
The bundled `visual-qa` plugin provides VLM-based visual end-to-end testing: a pluggable vision-language
model behind an OpenAI-compatible endpoint on any NVIDIA GPU, `look` / `assert_visual` tools (MCP + CLI), a
GPU-aware model recommender (by VRAM + architecture + task), and vLLM / NIM / Ollama serving runbooks.
The bundled `aisee` plugin is knowledge-only "eyes" for visual verification backed by the standalone AISee
service (rule + skill + MCP servers; see the 0.17.0 history blurb). The bundled `nvidia-brev` plugin drives
the full lifecycle of Brev cloud-GPU runs (brev-setup + brev-run skills, cost ledger, upstream brev-cli
mirror; see the 0.18.0 history blurb).

## Versioning: revisions + semver

Three independent mechanisms.

**Per-file revisions** keep ai-packs in sync with framework/plugin master copies - this is the primary
sync mechanism (not version numbers). Every materialized framework/plugin file carries an integer rev
marker, bumped +1 per edit, and a **content hash that excludes the marker** (a pure rev bump never changes
the hash). Markers at the top of the file: `_Rev. N_` (md/mdc), `# rev. N` (py), a leading `"_rev": N` field (json).
Markers appear ONLY on files that materialize into ai-packs (`templates/ai-pack/**`,
`templates/workspace/**`, plugin `shared/**`); all other framework files (README, agent files, skills,
rules, spec, migrations, tools) carry none - git + semver version those. The
framework ledger `solaris/revisions.json` records current rev+hash + short history per tracked **framework**
file; each **plugin keeps its own** ledger at `plugins/<name>/revisions.json` (keys relative to the plugin),
so a plugin's rev history travels inside its own repo - never in the framework ledger. A project records its
baseline (`ai/manifest.json` -> `revisions`, `{rel: {rev, hash}}` at last sync). On
`update-project` / plugin update, `solaris.tools.revs classify` gives a per-file verdict:

| Verdict | Meaning | Action |
|---|---|---|
| in-sync | project hash == master hash | reconcile rev (no content change) |
| fast-forward | project == baseline, master advanced | overwrite from master (`revs ff`) |
| missing | not yet materialized | copy from master (`revs ff`) |
| merge-up | project rev > master rev | fold project edits up into master (`import-plugin` for plugins) |
| conflict | both changed since baseline | 3-way smart merge, asking the user per file/hunk |

**Semantic versions** are release-only. The framework version is in `pyproject.toml`; each plugin's in its
`manifest.json`. Bump on explicit request or when publishing to a public git remote. **Migrations
(`solaris/migrations/<to_version>.md`) are authored only for MINOR/MAJOR bumps; PATCH never requires one.**
`ai/manifest.json.framework_version` gates which migrations a project still needs; `solaris.tools.version`
scans `migrations/*.md` to compute the chain (no registry file). Migrations adapt `ai/` only - never `source/`.

**Project versions** live in a plain-text `.version` file at each project's root (bare
`MAJOR.MINOR.PATCH`; embedded mode: the repo root) - the project content's own semver, independent of
`framework_version` and plugin versions. Seeded at create/import; bumped only with user approval (the
engineer proposes at milestones), each approved bump committed and locally tagged `v<X.Y.Z>` when a
git repo tracks the project root (tag pushes confirm-first). Tooling: `version project|project-set|project-bump`. Deliberately outside the
revisions mechanism: no rev marker, never materialized, never touched by `revs`.

## Command center (tasks)

Ad-hoc work that is not a project lives under `tasks/<YYYY>/<MM>/<YYYY-MM-DD>-<slug>/` (gitignored; filed by year/month, the leaf folder keeps the full date prefix): a `notes.md` plus
scratch. No ai-pack, no versioning. A task that turns durable can graduate into a project or a plugin.
`health-check` gives the overview (default: projects, revisions, versions, tasks, MCP) and health checks
(`--deep`); the orchestrator runs the overview to orient **before working on a project** (the first
`develop-project` of a session) and on request - not for ad-hoc tasks (per `AGENTS.md`).

## Memory and interaction logging

Framework `.memory/`: `resources.md` (hardware + hosts/accounts inventory), `credentials.md` (secrets; gitignored),
`interactions.jsonl` (log), and `instructions.md` (operating memory; see below). ai-packs never read it; needed values are copied into a project's own
`ai/.memory/` at init/update. **These two stores - the framework `memory/` and each project's `ai/.memory/` -
are the only authoritative memory in Solaris.** Agents never read, write, or create memory outside them: not
a harness/global `~/.claude/.../memory/` store, not any `MEMORY.md` index (Solaris never creates one), and any
externally injected or recalled memory is treated as non-authoritative. A project's `ai/.memory/` is its **private/local layer** (resources,
credentials, the preserved `spec-v0.md`, the session-context summary, interaction log); the **shareable** how-to-develop notes live one
level up in `ai/engineer.instructions.md`, and any host/secret/internal-URL detail that surfaces there is
relocated down into `ai/.memory/` rather than dropped. Host/deploy targets, hardware, APIs, and secrets live
only in `ai/.memory/` (`resources.md` / `credentials.md`), never in `ai/manifest.json` (which holds project
metadata, versions, plugins, and revisions only). **`resources.md` is inventory only** - hardware and
hosts/accounts (*what exists*); all procedures (build/run/deploy/restart), model/runtime details, and gotchas
(*how*) live in `ai/engineer.instructions.md` as generic patterns that reference `resources.md` for concrete
values. When an ai-pack is shared without its private layer
(`ai/.memory/` dropped), the engineer detects the missing or empty `ai/.memory/` on first run and **bootstraps
it interactively** - asking the user for hosts / deploy target / APIs / secrets and writing `resources.md`,
`credentials.md`, and a fresh `context.md` - before doing project work.

**Operating memory (`.memory/instructions.md`).** Framework-level, cross-project working knowledge: terse,
**timestamped** entries (`- [YYYY-MM-DD] ...`) on how to work with hosts/tools, recurring gotchas, and the
user's durable preferences - distinct from any project's `ai/.memory/context.md`. The Solaris agent loads it
every session and updates it **in place** (merge, never duplicate) whenever a reusable fact/preference/gotcha
surfaces, and **always** when the user says "remember it/this" or similar. Routing: cross-project/global goes
here; project-specific to that project's `context.md`; hosts/secrets to `resources.md`/`credentials.md`. It
is kept terse (context-cheap), carries a `solaris.tools.toc` TOC, and is compacted **oldest-first** (by
timestamp) once it passes ~100KB. `self-reflect` promotes important, reusable entries into the core framework
and then deletes them here. Private/local (gitignored); ai-packs never read it.

**Session-context summary (`ai/.memory/context.md`).** A detailed summary of the **current session's**
context: the task(s) and their state, decisions with their reasons, findings, key file references, open
questions, and immediate next steps - everything a fresh session (or the same session after compaction)
needs to continue immediately. It complements `interactions.jsonl` (the terse per-turn machine record) and
is **rewritten in place** (its `## Session context` section replaced, not appended) at two save points:
**before context compaction** - automatically ahead of an auto-compaction, or when the user compacts
manually - so no detail is lost, and **on request**, whenever the user says "save/remember/update/retain/keep
context" or similar. The engineer reads it first at session start (and right after a compaction) to restore
context. Durable cross-session knowledge does not live here - it routes to `engineer.instructions.md` (how),
`resources.md` (what exists), or `spec.md` (the contract). **Only the project engineer and Solaris's own
agents (orchestrator + skills) write it** - plugins and subagents do not. It carries a `solaris.tools.toc`
table of contents like any other doc. The file is private/local and gitignored; on a shared ai-pack it is
bootstrapped fresh with the rest of `ai/.memory/`.

**Interaction logs (prompt + request + outcome).** Each meaningful turn is recorded as one append-only JSON
line `{ts, project, prompt, request, outcome}`, where **`prompt`** is the user's verbatim raw prompt,
**`request`** is the agent's interpreted restatement of it, and **`outcome`** is what happened. The **agent**
authors this full entry into **both** the framework master `.memory/interactions.jsonl` (the record of **all**
turns - orchestrator work and every handed-off project turn) **and**, for project work, the touched
**project's** `ai/.memory/interactions.jsonl` (a subset of the master) - identical schema in both. Only the
agent can write it: it alone knows the interpreted request, the outcome, and the true project, since "hand
off" does not change the cwd. The prompt-submit hook (`log_interaction`) independently appends a raw-prompt
backstop line (`{ts, cwd, ide, prompt}`) to the framework master so a prompt is never lost; the master
therefore mixes these backstop lines with the agent's full entries. Both logs are fail-safe and unbounded in
v0.

## Tools

Stdlib only; run as modules (`uv run -m solaris.tools.<name>`):

- `version` - framework + ai-pack semver, migration chain, plugin versions.
- `revs` - per-file revisions + rev-excluded content hashes: `bump`, `hash`, `status`, `ledger`,
  `classify --dir`, `ff --dir`, `baseline --dir`.
- `mcp_sync` - detect/sync drift between `.mcp.json` and `.cursor/mcp.json`.
- `log_interaction` - the fail-safe prompt-submit hook (not called by hand).
- `read_first` - the fail-safe read-first loader hook (not called by hand): with no args it injects the
  AGENTS.md read-first set at session start (Claude `SessionStart` / Cursor `sessionStart`) in **three
  parts** - part 1 the core set (commit/safety/interaction rules, operating memory, orchestrator role),
  `--part 2` the subagents + YAGNI rules, `--part 3` the token-economy rule - each wired as its own
  hook entry because Claude Code's 10,000-char inline threshold applies per hook call; `--remind`
  prints a one-line per-turn nudge (Claude `UserPromptSubmit` only); `--check` reports per-file sizes
  and every part's payload vs the budget. Per part the payload is packed into a 9.5KB inline budget (larger hook stdout is spilled to a
  barely-previewed file): rules first and whole, then truncated-with-marker / pointer degradation, with
  pointer space reserved so the budget can never overflow; Cursor gets the full unbudgeted set.
  IDE-aware output (Cursor JSON vs Claude plain stdout).
- `skill_loader` - the fail-safe prompt-submit skill auto-loader hook (not called by hand; Claude
  `UserPromptSubmit` only): matches the prompt against every skill's `triggers`/`antitriggers` and injects
  the full body of any match (once per session, then a one-line reminder). When the prompt or session cwd
  targets a project, it also injects that project's **overlay index** - one line per `ai/rules/*.rule.md`,
  `ai/plugins/<plugin>/*.rule.md`, and `ai/plugins/*.link.md` file, once per session per project (grouped, flat, and
  embedded layouts). Tolerates a leading `_Rev. N_`
  marker above the skill frontmatter, and skips synthetic turns (task notifications, command transcripts,
  system reminders) entirely.
- `toc` - generate/verify Markdown tables of contents (`--check`/`--write`, `--all`). Preserves a leading
  rev marker and/or YAML frontmatter (either order) above the TOC; `--all` skips the content trees
  (`projects/`, `plugins/`, `tasks/`, `.memory/`).

All have unit tests under `solaris/tests/` (`uv run pytest`).

## Conventions

- **File formats:** human-facing docs are Markdown (`.md`, user-editable). Machine state is JSON
  (`manifest.json`, `remote.json`, `mcps.json`, `revisions.json`) carrying `"_comment": "do not edit"`.
  Append-only logs are JSON Lines (`.jsonl`). No standalone YAML data files (markdown frontmatter exempt).
- **Markdown TOC:** every `.md` with two or more level-2+ headers carries a TOC (the H1 is marked
  `<!-- omit in toc -->`), maintained by `solaris.tools.toc`.
- **Machine-local tooling notes:** environment-specific tooling workarounds (and their registries) live in
  the instructions layer - the framework's `.memory/instructions.md` and each project's
  `ai/engineer.instructions.md` (seeded from the template) - never in the agent files. A project may edit
  or delete its copy freely.
- **Revisions:** **every change to a revisioned file increments its rev.** After editing a tracked
  framework/plugin file (or any file carrying a rev marker), `revs bump` it and `revs ledger`; a pure rev
  bump leaves the content hash unchanged, and `revs status` flags a file changed without a bump.
- **Self-sufficient spec:** a project's `ai/spec.md` is that project's single source of truth and reads
  standalone - it references no other file (no links into `ai/.memory/`, plugins, or external docs).
  Background or the initial draft may live in `ai/.memory/`, but the spec never points at them.
- **Naming:** kebab-case. Skills `*.skill.md`, rules `*.rule.md`.
- **Commits** (`rules/commits.rule.md`, embedded in each `engineer.agent.md`): one ASCII sentence,
  imperative, no `--`, no emoji, no AI-authorship attribution, atomic; confirm via numbered list unless the
  user grants autonomy or uses `commit!`. The `.githooks/commit-msg` hook enforces the mechanical cases.
- **Safety** (`rules/safety.rule.md`, embedded too): confirm before destructive, remote-mutating, or
  outward actions; show the command/diff first; never print or commit secrets. Long-running remote work
  adds three duties: first-iteration pace check, post-restart external-state re-verify, same-turn
  delete/stop verification.
- **Subagents** (`rules/subagents.rule.md`; pack copy `ai/rules/subagents.rule.md`): two layers. The
  always-on **bulk-read floor**: a lookup expected to pull more than ~20k tokens of raw results, in a
  session that continues afterward, runs in a subagent and returns the synthesized answer, never raw
  dumps (~10k at economy `full`); a harness with no subagent tool (`solaris/info/harnesses.md`; pack:
  `ai/info/harnesses.md`) runs it checkpointed inline (sliced reads, notes to a scratch file, only
  conclusions restated). On top, the **delegate-by-default posture**, from `"subagents.level"` in
  `.memory/config.json` (pack: `ai/defaults.json` overridden by `ai/.memory/config.json`): `off` /
  `auto` (default: follows the resolved economy level - off -> off, med -> quality, full -> cost) /
  `quality` (one-up every tier) / `cost` (cheapest viable tier); aliases `med`/`q` = `quality`,
  `full`/`save` = `cost`; `subagents: <posture>` in a message overrides for that request only.
  `quality` and `cost` differ only in which tier runs a task, never in whether to delegate. Every
  delegated prompt carries the 5-point task contract (exact scope, procedure, return shape, boundaries,
  active modes restated). Abstract tiers (cheap/mid/high/frontier) map to concrete models in
  `solaris/info/model-tiers.md` (pack: `ai/info/model-tiers.md`, hard-required - never substituted from
  memory). Destructive, remote-mutating, and outward actions never delegate.
- **Token economy** (`rules/token-economy.rule.md`; pack copy `ai/rules/token-economy.rule.md`): governs
  how much enters the main context and how fast it is re-sent. Always-on floor: grep-then-slice read
  budget past ~200 lines, unbounded files never read whole (tail/grep/filter instead), independent tool
  calls batched (multi-file surveys as one batched shell sweep with guardrails), time-varying fields at
  the end of always-loaded files, never re-read your own writes. Twelve graded measures at
  `"economy.level"` (same config files): `off` (floor only) / `med` (default) / `full` (crunch bundle) /
  `auto` (context-scaled: `full` past ~100k tokens or a compaction, one-way per session) - covering
  surveys, slicing, prior-art checks, verification style, heavy-command output redirection, and the
  subagent bulk-read threshold. Pacing: round-trips/min <= `"economy.tokens_per_minute"` (default 1m)
  over current context tokens; `economy: <level>` and `asap` are per-request overrides. Hard floors:
  verification, whole-artifact reads before modifying, logging/context duties, and secrets are never
  traded for tokens.
- **YAGNI mode** (`rules/yagni.rule.md`; pack copy `ai/rules/yagni.rule.md`): opt-in
  (`"yagni.enabled"`, absent = off; `yagni: on|off` per request): deliver exactly what was asked in the
  smallest coherent form; bans unrequested features/abstractions/files/refactors. Guardrails: YAGNI
  shortens the solution, never the reading; trust-boundary input validation, data-loss handling,
  security, and the commit/safety/interaction rules are never trimmed.
- **Interaction + writing** (`rules/interaction.rule.md`, embedded as the template's Interaction Policy):
  a direct question gets its explicit answer in the reply's first line; requested word counts are honored;
  brevity by default; no consultant buzzwords; jargon explained with a ~10-15-word parenthetical.
- **Git collaboration on ai files:** committed ai files are written diff-friendly - prose hard-wrapped at
  ~100-120 columns, bullets/tables over paragraphs, stable heading order, tool-generated TOCs, no reflow of
  untouched text. `ai/manifest.json` `revisions` conflicts resolve mechanically (take either side, re-run
  `revs baseline`); committed append-only `*.jsonl` logs use `*.jsonl merge=union` via `.gitattributes`.

## Validation (acceptance)

1. `uv run pytest` green (tools + revs + toc).
2. `version current` -> the current framework version; `revs status` consistent; `revs classify`/`ff` behave on a project;
   `mcp_sync --check` and `toc --check --all` clean.
3. **Todo app** (web-service, local): `create-project todo` (AGENTS.md-only root, runtime MCP) ->
   `develop-project` builds a FastAPI + vanilla UI -> runs locally; app tests pass.
4. **Migration** `0.1.0 -> 0.2.0` authored and idempotent.

## Deferred

A second `documenter` persona; splitting `engineer.agent.md`; a base `nvidia` plugin; a hosts registry and
`run-remote`/`research`/`capture`/`provision` command-center skills; the `ios-app` build/run workflow;
extending the revision/merge system beyond the materialized set; true automatic 3-way text merge (today the
tool classifies and the agent merges).
