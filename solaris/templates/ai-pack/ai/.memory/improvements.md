# {{NAME}} - Framework Improvement Suggestions <!-- omit in toc -->

- [How to Use This File](#how-to-use-this-file)
- [Suggestions](#suggestions)

Suggestions from {{NAME}} for improving the Solaris framework or one of its plugins, and nothing else. This
file is private (`.memory/` is never committed). The project never edits framework or plugin files itself
unless the owner explicitly says so; under a Solaris checkout, the orchestrator reviews this file and
implements what the owner approves.

## How to Use This File

- **Only framework and plugin suggestions belong here.** This project's own lessons, facts, gotchas and
  decisions stay in `instructions.md`; owner directions stay in `directions.md`.
- **Write at once:** when a framework or plugin file should change, add a dated entry before moving on, so the
  suggestion survives compaction, restarts and a change of session.
- **One entry per suggestion:** `- [YYYY-MM-DD] <the change, worded for any project> | why: <what happened,
  with numbers> | target: <the framework or plugin file to change>`.
- **Keep it safe to share:** no secrets, host names, IPs, lease ids or internal URLs.
- **Share, don't copy:** you may tell another session about a suggestion; each session writes only its own
  project's file.
- **Append only:** check for an existing entry first; leave entries in place, and withdraw one with a later
  entry that names its date. The orchestrator records its decisions in its own notes.

## Suggestions
