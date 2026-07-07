# aisee - Solaris plugin <!-- omit in toc -->

- [What it is](#what-it-is)
- [What it ships](#what-it-ships)
- [Install](#install)
- [Requirements](#requirements)

## What it is

Gives a project's engineer agent **eyes** via [AISee](https://github.com/myurasov/AISee) - a
standalone tool that serves vision-language models on a GPU host and answers questions about
images and video: `look` (free-form / OCR), `assert_visual` (machine-checkable pass/fail
verdicts), `watch` (chunked whole-video analysis with time-localized findings).

Access order: **MCP** (streamable HTTP at `<server>/mcp`; local media uploaded once via
`POST /v1/blobs` and referenced as `sha256:<hex>`), then plain **REST**, then the **aisee
CLI**. The plugin ships knowledge only - AISee itself runs as an external service.

## What it ships

| File | Role |
|---|---|
| `shared/aisee.rule.md` | always-on conventions: when the visual leg runs, query-kind choice, evidence rules, media/blob rules |
| `shared/aisee.skill.md` | trigger-invoked procedure: reach server -> capture -> query (MCP/REST/CLI) -> report; server setup; troubleshooting |
| `mcps.json` | the `aisee` MCP entry (placeholder URL, substituted at install) + `playwright` for web capture |
| `manifest.json` | plugin metadata + install-time setup (server URL, optional consumer token) |

## Install

At the Solaris root: `install plugin aisee to <project>` (or `link plugin aisee to <project>`
while developing the plugin). Setup prompts for the AISee server URL and an optional consumer
token; answers land in the project's `ai/memory/resources.md` / `credentials.md`, and the
merged MCP entry gets the real URL.

## Requirements

A reachable AISee server (Linux GPU host with docker + NVIDIA Container Toolkit). To stand
one up, follow `aisee.admin.agent.md` in the [AISee repo](https://github.com/myurasov/AISee);
the skill's "Setting up a server" section has the short version.
