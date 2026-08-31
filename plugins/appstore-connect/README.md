# appstore-connect - Solaris Plugin <!-- omit in toc -->

- [What It Is](#what-it-is)
- [Skills](#skills)
- [Layout](#layout)
- [Install](#install)
- [Maintenance](#maintenance)

## What It Is

Everything field-tested about operating **App Store Connect** (and developer.apple.com) from
the developer's perspective, for iOS and macOS apps - merged from two real app publishes.
API-first: the ASC REST API is the default path; the browser (via the base `browserctl`
plugin) covers only what the API cannot reach.

## Skills

| File | Role |
|---|---|
| `shared/asc-api.skill.md` | The default entry point: ASC REST API with a team key - listings, screenshots, builds, pricing, age rating, review submission, TestFlight; review-time editability; policy quirks. |
| `shared/browserctl.asc.skill.md` | Browser flows the API cannot reach: App Privacy questionnaire, EU DSA trader status, agreements, IAP setup, API-key creation - drive loop, dialog technique, upload pitfalls. Follows the `browserctl.<site>` extension-skill format (base `browserctl` plugin required). |
| `shared/asc-upload.skill.md` | Archive and upload a build from the command line: archive-time signing overrides, `ExportOptions` with `destination upload`, `-allowProvisioningUpdates`; pitfalls (build numbers, wrong team, cloud-synced paths). |
| `shared/asc.rule.md` | Always-on: API-first routing, outward-action confirmation, secrets/session discipline, verify-by-re-reading, living-documents duty. |

The skills cross-reference each other; new ASC areas get their own skill files as they are
field-tested.

## Layout

- `manifest.json` - name, semver, description, `applies_to.markers`, `setup` (notes + the
  browser-profile resource prompt).
- `shared/*.skill.md` - the skills (the only files copied/linked into projects).
- `revisions.json` - rev ledger (managed by `solaris.tools.revs`).

## Install

Via the framework `install-plugin` skill: "install plugin appstore-connect to <project>"
(copy) or "link plugin appstore-connect to <project>" (link mode, for plugin development).
Attach the base `browserctl` plugin first.

## Maintenance

The skills are living documents (see their Maintenance sections): new ASC lessons are folded
into the matching skill the same session they are field-verified, revs bumped, framework repo
committed. Consumers on link mode see edits immediately; copied installs pick them up on
plugin update.
