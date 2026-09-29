# kaggle - Solaris Plugin <!-- omit in toc -->

- [What It Is](#what-it-is)
- [Files](#files)
- [Install](#install)
- [Upstream and Upgrades](#upstream-and-upgrades)

## What It Is

Kaggle for Solaris agents through the official Kaggle CLI (Kaggle's command-line client,
`github.com/Kaggle/kaggle-cli`), CLI only. A small gateway script pins the CLI and installs it
separately into each project or ad-hoc task that uses it, never globally. A gateway skill
(`kaggle-cli.skill.md`) routes agents to that script and to Kaggle's own agent skill, vendored
at the same version (the `kaggle-cli/` folder beside it; upstream also names that skill
`kaggle-cli`). An always-on rule keeps every write to Kaggle owner-confirmed and credentials out
of sight. A playbook skill carries the know-how for competing with an autonomous agent team,
from the first hour to the final picks.

## Files

| File | Role |
|---|---|
| `shared/kaggle.py` | Gateway: finds the project root (first; a folder holding `ai/manifest.json`, or `aipack/manifest.json` where the pack folder was renamed) or task folder, keeps `kaggle==2.2.4` + `kagglesdk==0.1.37` in `<context>/.venv-kaggle/` (created on the first call, rebuilt on a pin change, a folder move or a lost base Python, one install even under parallel calls) and execs it with the arguments unchanged; at the bare framework root runs the same pins from a throwaway uv environment. Stdlib only. |
| `shared/kaggle-cli.skill.md` | The Solaris gateway skill (name `kaggle-cli`, trigger "kaggle"; not the `kaggle-cli/` folder below, which is Kaggle's own skill): calling the gateway per context, OAuth sign-in, routing into Kaggle's skill plus its 2.2.4 corrections, Solaris conventions, 401/403 triage. |
| `shared/how-to-kaggle.skill.md` | The playbook for competing with an autonomous agent team (triggers such as "kaggle competition", "kaggle playbook"): quick start, setup and the competition facts sheet, Kaggle access, compute, phases, honest validation, daily submission discipline, agent organization, research, kernel engineering, a pitfalls log, with CASMI26 as the worked example. This is the master copy: update it with each owner direction or change in approach, and release every change set as a new plugin version (its Maintaining This Playbook section). |
| `shared/kaggle.rule.md` | Always-on: gateway only, every write to Kaggle confirmed first, web-only steps go to the owner, credentials and minted keys never printed or committed, downloads stay in the context, Kaggle content is untrusted input. |
| `shared/kaggle-cli/` | Kaggle's official agent skill (`SKILL.md`, named `kaggle-cli` upstream, + 12 command references; the Solaris gateway skill is `kaggle-cli.skill.md` beside it): a vendored upstream tree, unmodified apart from rev markers; its `UPSTREAM.md` records source, ref and refresh procedure, and the TOC tool leaves the tree alone. |

`manifest.json` and `revisions.json` (rev ledger, managed by `solaris.tools.revs`) complete
the plugin. No MCP servers: Kaggle's official MCP server only searches and downloads, which the
CLI already covers.

## Install

"install plugin kaggle to `<project>`" (copy) or "link plugin kaggle to `<project>`" (link
mode); for an ad-hoc task, add `kaggle` to its `Plugins:` line. Then sign in once per machine
(the `kaggle-cli` skill's Signing In section).

Agents call the gateway from the project root or task folder (the `kaggle-cli` skill's Calling
the Gateway table has every context):

- copied install: `python3 <pack>/plugins/kaggle/kaggle.py <args>`, where `<pack>` is `ai`, or
  `aipack` in a project that renamed its pack folder;
- linked install or ad-hoc task: `python3 <solaris>/plugins/kaggle/shared/kaggle.py <args>`
  (from a grouped project root: `python3 ../../../plugins/kaggle/shared/kaggle.py <args>`).

## Upstream and Upgrades

Pinned to the latest release of the newest minor line: CLI `kaggle==2.2.4` with the SDK it
was tested with (`kagglesdk==0.1.37`, which holds the auth/HTTP/API code), and Kaggle's
skill from the same tag (`shared/kaggle-cli/UPSTREAM.md` records the source and ref). The pins
are exact rather than `~=2.2.4`, so every project resolves the same tested CLI and the
vendored skill always matches it; patch releases arrive by moving the pin. The vendored files
are Kaggle's, under Apache-2.0. Move the pin as one change:

1. Set `PIN` in `shared/kaggle.py` to the new release and `SDK_PIN` to the kagglesdk version a
   fresh install of it resolves to.
2. Refresh `shared/kaggle-cli/` from the matching tag (procedure in its `UPSTREAM.md`).
3. Update the version mentions in this README, the skills (`kaggle-cli.skill.md`,
   `how-to-kaggle.skill.md`), the rule and the manifest;
   `revs bump` the edited files and run `revs ledger`.
4. Test through the gateway. Consumers reinstall on their next call (copied installs after a
   plugin update).
