# Engineer instructions - {{NAME}} <!-- omit in toc -->

- [Build / run / test](#build--run--test)
- [Conventions](#conventions)

Editable, project-specific notes on how to develop this project. Rewrite freely to keep the best version
(not append-only). The commit and safety policies live in `engineer.agent.md`.

**Shareable layer.** This file sits in `ai/` alongside `engineer.agent.md` and `spec.md` - the portable,
shareable "how to develop this project" layer. Keep it free of anything environment-specific or sensitive:
**no** hostnames, IPs, internal/corporate URLs, deploy targets, remote paths, or secrets. Those live in the
private `ai/memory/` layer (`resources.md`, `credentials.md`, and the project context log `context.md`). When such a detail
would otherwise land here, **relocate it into the right `ai/memory/` file and reference it - never drop it**
(losslessly). Write commands as generic patterns (e.g. `--host <host> --port <port>`) and point at
`ai/memory/resources.md` for the concrete values.

## Build / run / test

- install: (fill in)
- run: (fill in)
- test: (fill in)
- lint: (fill in)

## Conventions

- Default working style (carried by Solaris): terse responses; tables when comparing options; lead with an
  explicit recommendation; give the bare command first, then variants.
- (add project-specific conventions here as you learn them)
