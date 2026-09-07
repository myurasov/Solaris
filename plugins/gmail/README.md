# gmail - Solaris Plugin <!-- omit in toc -->

- [What It Is](#what-it-is)
- [Skills](#skills)
- [Layout](#layout)
- [Install](#install)
- [Maintenance](#maintenance)

## What It Is

Gmail for agents through **gws**, the Google Workspace CLI (Google's open-source
command-line client for the Workspace REST APIs, `github.com/googleworkspace/cli`). One
skill installs and signs in the CLI on macOS or Linux; one skill reads and sends mail; one
always-on rule keeps sending an owner-confirmed, outward action. Gmail only for now: the same
CLI covers Drive, Calendar, Sheets, and the rest of Workspace, so later services need only
extra login scopes and their own skill files.

## Skills

| File | Role |
|---|---|
| `shared/gws-setup.skill.md` | Idempotent setup: install `gws` (Homebrew formula `googleworkspace-cli`, prebuilt release binary, or npm), put an OAuth Desktop client in place (Cloud Console, with `gws auth setup` doing the gcloud half), sign in with the Gmail scope (gws prints a URL, the owner completes consent; owner-confirmed export hand-off for headless hosts), verify, record the account. |
| `shared/gmail.skill.md` | Read (triage, search, read a body, fetch attachments) and send (send, reply, reply-all, forward, drafts, dry-run) with the `+` helper commands; raw Gmail API form for everything else. |
| `shared/gmail.rule.md` | Always-on: every send is outward and confirmed first (reply/forward show the original's recipients too), mail content is untrusted input, tokens and the client file never leave gws's store (sole exception: the owner-confirmed headless-host credentials file), message content stays out of commits and logs. |

## Layout

- `manifest.json` - name, semver, description, `applies_to.markers`, `setup` (notes + the
  account resource prompt).
- `shared/*.skill.md`, `shared/*.rule.md` - the only files copied/linked into projects.
- `revisions.json` - rev ledger (managed by `solaris.tools.revs`).

No MCP servers: the plugin is CLI-based.

## Install

Via the framework `install-plugin` skill: "install plugin gmail to <project>" (copy) or
"link plugin gmail to <project>" (link mode, for plugin development). Then say "set up
gmail" in the project to run `gws-setup` once per machine.

## Maintenance

The skills are living documents (see their Maintenance sections): new field-verified gws /
Gmail lessons are folded into the matching skill the same session, revs bumped, framework
repo committed. Consumers on link mode see edits immediately; copied installs pick them up on
plugin update.
