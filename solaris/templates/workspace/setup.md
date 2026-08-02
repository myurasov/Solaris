_Rev. 1_

# {{WORKSPACE}} - Workspace Setup <!-- omit in toc -->

- [Prerequisites](#prerequisites)
- [Bring-Up](#bring-up)
- [Verify](#verify)

From-scratch bring-up for the `{{WORKSPACE}}/` workspace of **{{NAME}}**. Self-contained: every step works
without files from sibling workspaces (shared inputs are referenced at their locations outside this
folder). A new engineer following only this file must end with a verified, working workspace.

## Prerequisites

- (host/tooling requirements - reference `ai/.memory/resources.md` for concrete hosts, never hardcode)
- (shared inputs used, by location outside the workspace, e.g. `data/<set>` - and how to obtain them)

## Bring-Up

1. (dependency install - keep the venv/deps inside this workspace)
2. (build / configure steps)

## Verify

- (an end-to-end check a human can see: a command with expected output, a health URL, a smoke test)
