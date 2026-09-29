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
of sight. Two tools extend the gateway: a leaderboard history that saves every leaderboard read,
so competitors' progress can be followed over time, and account sharing, which splits one Kaggle
account's sessions and weekly GPU hours between the projects using it. A playbook skill carries
the know-how for competing with an autonomous agent team, from the first hour to the final picks.

## Files

| File | Role |
|---|---|
| `shared/tools/kaggle.py` | Gateway (`shared/tools/` holds the plugin's scripts): finds the project root (first; a folder holding `ai/manifest.json`, or `aipack/manifest.json` where the pack folder was renamed) or task folder, keeps `kaggle==2.2.4` + `kagglesdk==0.1.37` in `<context>/.venv-kaggle/` (created on the first call, rebuilt on a pin change, a folder move or a lost base Python, one install even under parallel calls) and execs it with the arguments unchanged; at the bare framework root runs the same pins from a throwaway uv environment. Inside a project or task two hooks run first and never block or change a command: an activity stamp for `kaggle_share.py` (`KAGGLE_SHARE_QUIET=1` skips it) and a tee that saves `competitions leaderboard <slug> --show` reads through `kaggle_lb.py` (`KAGGLE_LB_RECORD=0` turns it off). Stdlib only. |
| `shared/tools/kaggle_lb.py` | Leaderboard history: `snapshot`/`show` save a gzip JSON snapshot per read under `<context>/__data/kaggle/<slug>/leaderboard/`, never overwritten, partial reads flagged, `--dir`/`KAGGLE_LB_DIR`; `history`, `movers`, `new-teams`, `summary`; `record-raw` and `tee_leaderboard` (the gateway hook); `import`. Public leaderboard fields only. Stdlib only. |
| `shared/tools/kaggle_share.py` | Account sharing: active projects from account-wide runs and quota plus local gateway stamps; an equal or configured split of concurrent sessions and weekly GPU hours; leases with borrowing; flock-guarded state in `~/.solaris/kaggle/` (never `~/.kaggle/`). Stdlib only. |
| `shared/kaggle-cli.skill.md` | The Solaris gateway skill (name `kaggle-cli`, trigger "kaggle"; not the `kaggle-cli/` folder below, which is Kaggle's own skill): calling the gateway per context, its hooks, OAuth sign-in, routing into Kaggle's skill plus its 2.2.4 corrections, Solaris conventions, 401/403 triage. |
| `shared/kaggle-leaderboard.skill.md` | Leaderboard history (`kaggle_lb.py`): what is stored and why, the commands, an hourly cadence, privacy, reading progress over time. |
| `shared/kaggle-sharing.skill.md` | Account sharing (`kaggle_share.py`): what is shared, detection, the split and the user's directions, the commands, the agent routine around each kernel run. |
| `shared/how-to-kaggle.skill.md` | The playbook for competing with an autonomous agent team (triggers such as "kaggle competition", "kaggle playbook"): quick start, setup and the competition facts sheet, Kaggle access, compute, phases, honest validation, daily submission discipline, agent organization, research, kernel engineering, a pitfalls log, each rule with its evidence as a generic example; nothing specific to one competition. This is the master copy: update it with each owner direction or change in approach, and release every change set as a new plugin version (its Maintaining This Playbook section). |
| `shared/kaggle.rule.md` | Always-on: gateway only, every write to Kaggle confirmed first, a sharing lease around each kernel run, web-only steps go to the owner, credentials and minted keys never printed or committed, downloads stay in the context, every leaderboard read saved and kept local, Kaggle content is untrusted input. |
| `shared/kaggle-cli/` | Kaggle's official agent skill (`SKILL.md`, named `kaggle-cli` upstream, + 12 command references; the Solaris gateway skill is `kaggle-cli.skill.md` beside it): a vendored upstream tree, unmodified apart from rev markers; its `UPSTREAM.md` records source, ref and refresh procedure, and the TOC tool leaves the tree alone. |
| `tests/test_kaggle_gateway.py` | The gateway offline: context detection (both pack names, copied installs, tasks) and both hooks, including hooks that are missing, fail to import, or fail after the CLI ran (it never runs twice), with a stand-in CLI in a prepared venv. |
| `tests/test_kaggle_lb.py` | `kaggle_lb.py` offline, from fixture pages. |
| `tests/test_kaggle_share.py` | `kaggle_share.py` offline, with a fake account and project tree. |
| `migrations/` | Steps for copied installs when the plugin version advances (`0.2.0.md`: the moved gateway and the renamed skill). |

`manifest.json` and `revisions.json` (rev ledger, managed by `solaris.tools.revs`) complete
the plugin. No MCP servers: Kaggle's official MCP server only searches and downloads, which the
CLI already covers. Run the tests from the Solaris root (stdlib, offline):
`python3 -m unittest discover -s plugins/kaggle/tests`.

## Install

"install plugin kaggle to `<project>`" (copy) or "link plugin kaggle to `<project>`" (link
mode); for an ad-hoc task, add `kaggle` to its `Plugins:` line. Then sign in once per machine
(the `kaggle-cli` skill's Signing In section).

Agents call the gateway from the project root or task folder (the `kaggle-cli` skill's Calling
the Gateway table has every context):

- copied install: `python3 <pack>/plugins/kaggle/tools/kaggle.py <args>`, where `<pack>` is
  `ai`, or `aipack` in a project that renamed its pack folder;
- linked install or ad-hoc task: `python3 <solaris>/plugins/kaggle/shared/tools/kaggle.py <args>`
  (from a grouped project root: `python3 ../../../plugins/kaggle/shared/tools/kaggle.py <args>`).

The other tools sit beside the gateway (`tools/kaggle_lb.py`, `tools/kaggle_share.py`) and are
called the same way.

## Upstream and Upgrades

Pinned to the latest release of the newest minor line: CLI `kaggle==2.2.4` with the SDK it
was tested with (`kagglesdk==0.1.37`, which holds the auth/HTTP/API code), and Kaggle's
skill from the same tag (`shared/kaggle-cli/UPSTREAM.md` records the source and ref). The pins
are exact rather than `~=2.2.4`, so every project resolves the same tested CLI and the
vendored skill always matches it; patch releases arrive by moving the pin. The vendored files
are Kaggle's, under Apache-2.0. Move the pin as one change:

1. Set `PIN` in `shared/tools/kaggle.py` to the new release and `SDK_PIN` to the kagglesdk version a
   fresh install of it resolves to.
2. Refresh `shared/kaggle-cli/` from the matching tag (procedure in its `UPSTREAM.md`).
3. Update the version mentions in this README, the skills (`kaggle-cli.skill.md`,
   `how-to-kaggle.skill.md`), the rule and the manifest;
   `revs bump` the edited files and run `revs ledger`.
4. Test through the gateway. Consumers reinstall on their next call (copied installs after a
   plugin update).

Upgrading a copied install from 0.1.0: the gateway moved to `tools/kaggle.py` and the gateway
skill became `kaggle-cli.skill.md`, so an update must also remove the old files and repoint
every call, script and allow rule that names the old gateway path - `migrations/0.2.0.md`.
