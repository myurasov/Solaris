# browserctl-asc - Solaris Plugin <!-- omit in toc -->

- [What It Is](#what-it-is)
- [Extension-Plugin Format](#extension-plugin-format)
- [Layout](#layout)
- [Install](#install)
- [Maintenance](#maintenance)

## What It Is

A **browser-control extension plugin**: site-specific skills built on the base `browserctl`
plugin. This one covers **App Store Connect** (and developer.apple.com) from the developer's
perspective - API-first over the ASC REST API where possible, browser automation for the
flows the API cannot reach (App Privacy questionnaire, EU DSA trader status, agreements, IAP
setup, API-key creation) - with field-tested flows, dialog technique, and pitfalls for iOS
and macOS apps. Merged from two field-tested project-local skills (an iOS app publish and a
macOS app publish).

## Extension-Plugin Format

This plugin is the reference instance of the standard for browser-control extensions:

- Plugin name: `browserctl-<site>` (e.g. `browserctl-asc`).
- Skills: `shared/browserctl.<site>.skill.md` - one skill (or a set) defining how to work a
  particular website; frontmatter `name` matches the file stem (`browserctl.asc`).
- The base `browserctl` plugin is a prerequisite in the same project; extension plugins ship
  no tooling of their own, only site knowledge.
- Generic browserctl technique stays in the base plugin's `browserctl.skill.md`; only
  site-specific material lives here.

## Layout

- `manifest.json` - name, semver, description, `applies_to.markers`, `setup` (notes + the
  browser-profile resource prompt).
- `shared/browserctl.asc.skill.md` - the ASC site skill (the only file copied/linked into
  projects).
- `revisions.json` - rev ledger (managed by `solaris.tools.revs`).

## Install

Via the framework `install-plugin` skill: "install plugin browserctl-asc to <project>"
(copy) or "link plugin browserctl-asc to <project>" (link mode, for plugin development).
Attach the base `browserctl` plugin first.

## Maintenance

The skill is a living document (see its Maintenance section): new ASC lessons are folded in
the same session they are field-verified, revs bumped, plugin repo committed. Consumers on
link mode see edits immediately; copied installs pick them up on plugin update.
