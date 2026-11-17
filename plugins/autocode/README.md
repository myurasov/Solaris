# Autocode Plugin <!-- omit in toc -->

- [What It Does](#what-it-does)
- [Files](#files)
- [Starting a Session](#starting-a-session)

## What It Does

Runs a project's primary session as an **autocode agent** under OpenCode 2. Autocode (the owner's agent server)
gives the session wakes, messages, locks, jobs, approvals and compaction; this plugin's rule maps the persona's
hook- and clock-based duties onto them, so a Solaris project runs unattended without Claude Code hooks or
`session_clock`. Outside an autocode session the rule is dormant.

## Files

| File | What it is |
|---|---|
| `shared/autocode.rule.md` | Always-on rule: start, clock, guard, compaction, workers, messages, shared resources, logging, notices. |
| `shared/tools/wakes.py` | Prints autocode wake specs from `<pack>/.memory/schedule.json`; `--show NAME` prints one event's note. |

## Starting a Session

On the machine that runs the project, open OpenCode 2 in the project folder (a TUI, or `opencode run` against the
background service) and give it: "Act as the project's primary persona and run as autocode agent `<id>`: follow
`<pack>/plugins/autocode/autocode.rule.md`, Start." The session registers, loads the read-first set, converts the
schedule into wakes on its first start and stops the old clock. Attaching the plugin (copied or linked) turns Solaris
self-compaction off for the project, as autocode compacts its sessions; after a compaction, autocode's
`[autocode] compaction done` tells the session to go on with what is left (autocode 1.0.0-rc4 and later).
