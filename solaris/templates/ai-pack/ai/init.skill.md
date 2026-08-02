_Rev. 3_

---
name: init
triggers: ["init project", "initialize the project", "initialize this project", "set up my environment", "onboard me", "getting started", "register resources"]
summary: One-time onboarding for a fresh checkout of {{NAME}} - collect environment resources, verify reachability, write the private ai/.memory layer, and bring the environment up.
---

# Skill: init - Environment Onboarding <!-- omit in toc -->

1. [Preconditions](#1-preconditions)
2. [Collect Resources](#2-collect-resources)
3. [Write the Private Layer](#3-write-the-private-layer)
4. [Bring Up + Verify](#4-bring-up--verify)
5. [Hand Off](#5-hand-off)

Run when a new engineer starts with this project (or an existing one moves machines). Everything this
skill writes is **private and gitignored** (`ai/.memory/*`, `.mcp.json`, `.cursor/`) - the repo itself
stays environment-free. Idempotent: re-running updates the same files; never overwrite silently - show
what exists and confirm. (This is a template stub: replace the fill-ins with this project's real
resources and setup steps, and delete what does not apply.)

## 1. Preconditions

- If `ai/.memory/resources.md` already has concrete values, this checkout is initialized - confirm the
  user wants to update it, otherwise stop.
- (any legacy-layout migrations or other checks go here)

## 2. Collect Resources

Ask only for what setup actually needs, in one batch; accept partial answers and record "TBD" rather than
blocking. Do **not** ask for secrets here - collect each secret the first time a step needs it.

- (resource 1: e.g. a dev host - ask for the ssh alias only; probe capabilities yourself over ssh)
- (resource 2: e.g. a service base URL)

## 3. Write the Private Layer

1. `ai/.memory/resources.md` - the inventory of answers (hosts, URLs, paths).
2. `ai/.memory/credentials.md` - created empty; secrets are added when first needed, never echoed back.
3. `.mcp.json` + `.cursor/mcp.json` - any project MCP servers (keep both in sync).

## 4. Bring Up + Verify

- (install / build steps from `engineer.instructions.md` - reference, do not duplicate)
- (an end-to-end verification the user can see: a health URL, a smoke test)
- Multi-workspace projects: ask which workspace(s) the user will work on, then follow each one's
  `setup.md` in full (the workspace table in `engineer.instructions.md` lists them) - a workspace can be
  skipped now and set up later from its own `setup.md`.

## 5. Hand Off

Point the user at `README.md`, `ai/spec.md`, and `ai/engineer.instructions.md` (in that order), plus any
live links just brought up. Log the init in `ai/.memory/interactions.jsonl`.
