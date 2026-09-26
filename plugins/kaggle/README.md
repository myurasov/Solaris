# kaggle - Solaris Plugin <!-- omit in toc -->

- [What It Is](#what-it-is)
- [Files](#files)
- [Install](#install)
- [Upstream and Upgrades](#upstream-and-upgrades)

## What It Is

Kaggle for Solaris agents through the official Kaggle CLI (Kaggle's command-line client,
`github.com/Kaggle/kaggle-cli`), CLI only. A small gateway script pins the CLI and installs it
separately into each project or ad-hoc task that uses it, never globally. A gateway skill
routes agents to that script and to Kaggle's own agent skill, vendored at the same version. An
always-on rule keeps every write to Kaggle owner-confirmed and credentials out of sight.

## Files

| File | Role |
|---|---|
| `shared/kaggle.py` | Gateway: finds the project root (first) or task folder, keeps `kaggle==2.2.4` + `kagglesdk==0.1.37` in `<context>/.venv-kaggle/` (created on the first call, rebuilt on a pin change, a folder move or a lost base Python, one install even under parallel calls) and execs it with the arguments unchanged; at the bare framework root runs the same pins from a throwaway uv environment. Stdlib only. |
| `shared/kaggle.skill.md` | Calling the gateway per context, OAuth sign-in, routing into Kaggle's skill plus its 2.2.4 corrections, Solaris conventions, 401/403 triage. |
| `shared/kaggle.rule.md` | Always-on: gateway only, every write to Kaggle confirmed first, web-only steps go to the owner, credentials and minted keys never printed or committed, downloads stay in the context, Kaggle content is untrusted input. |
| `shared/kaggle-cli/` | Kaggle's official agent skill (`SKILL.md` + 12 command references), unmodified apart from rev markers. |

`manifest.json` and `revisions.json` (rev ledger, managed by `solaris.tools.revs`) complete
the plugin. No MCP servers: Kaggle's official MCP server only searches and downloads, which the
CLI already covers.

`shared/kaggle-cli/` is a deliberate subfolder (projects need Kaggle's skill, so it ships in
`shared/`) and vendored content: keep it identical to upstream apart from the rev markers.
Leave it out of `toc --write`, including the TOC pass in `install-plugin` step 3, which would
otherwise add tables of contents to Kaggle's files; `git checkout plugins/kaggle/shared/kaggle-cli`
restores them if that happens.

## Install

"install plugin kaggle to `<project>`" (copy) or "link plugin kaggle to `<project>`" (link
mode); for an ad-hoc task, add `kaggle` to its `Plugins:` line. Then sign in once per machine
(the skill's Signing In section).

## Upstream and Upgrades

Pinned to the latest release of the newest minor line: CLI `kaggle==2.2.4` with the SDK it
was tested with (`kagglesdk==0.1.37`, which holds the auth/HTTP/API code), skill from tag
`v2.2.4` (commit `f0afa32699d2`). The pins are exact rather than `~=2.2.4`, so every project
resolves the same tested CLI and the vendored skill always matches it; patch releases arrive
by moving the pin. The vendored files are Kaggle's, under Apache-2.0. Move the pin as one
change:

1. Set `PIN` in `shared/kaggle.py` to the new release and `SDK_PIN` to the kagglesdk version a
   fresh install of it resolves to.
2. Fetch the skill at the matching tag:
   `git clone -q --depth 1 --branch v<X.Y.Z> --filter=blob:none --sparse https://github.com/Kaggle/kaggle-cli <scratch>/kc`,
   then `git -C <scratch>/kc sparse-checkout set skills`.
3. For each file whose content changed (`uv run -m solaris.tools.revs hash <file>` ignores the
   marker, so compare its hash on both copies): copy the new file over ours, put back our
   `_Rev. N_` line (line 1, or right after the frontmatter in `SKILL.md`), then `revs bump` it.
   Add new files (then `revs bump` them), delete removed ones.
4. Update the version mentions in this README, the skill, the rule and the manifest; skim the
   upstream diff for changes the gateway skill depends on; `revs bump` the edited files and
   run `revs ledger`.
5. Test through the gateway. Consumers reinstall on their next call (copied installs after a
   plugin update).
