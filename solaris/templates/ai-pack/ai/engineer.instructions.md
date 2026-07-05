# Engineer instructions - {{NAME}} <!-- omit in toc -->

- [Build / run / test](#build--run--test)
- [Deploy](#deploy)
- [Runtime notes \& gotchas](#runtime-notes--gotchas)
- [Conventions](#conventions)

Editable, project-specific notes on how to develop this project. Rewrite freely to keep the best version
(not append-only). The commit and safety policies live in `engineer.agent.md`.

**This is the "how" layer.** All procedures and project knowledge live here: build/run/test, **deploy &
restart procedures**, **model/runtime details**, architecture/layers, and **gotchas**. The only things that do
*not* live here are the inventory of *what exists* (hardware + hosts/accounts -> `ai/memory/resources.md`),
secrets (`credentials.md`), and the session-context summary (`context.md`).

**Shareable layer.** This file sits in `ai/` alongside `engineer.agent.md` and `spec.md` - the portable,
shareable layer. Keep it free of anything environment-specific or sensitive: **no** hostnames, IPs,
internal/corporate URLs, concrete deploy targets, remote paths, or secrets - those are inventory and live in
`ai/memory/resources.md` / `credentials.md`. Procedures still belong here, written as generic patterns
(e.g. `rsync source/ <host>:<path>`, `--host <host> --port <port>`) that **reference** `resources.md` for the
concrete values - never drop the procedure, just keep the values out of it.

## Build / run / test

- install: (fill in)
- run: (fill in)
- test: (fill in)
- lint: (fill in)

## Deploy

- (deploy + restart procedure as generic patterns; reference `ai/memory/resources.md` for host/path/port)

## Runtime notes & gotchas

- (model/runtime details, performance notes, and gotchas worth never relearning)

## Conventions

- Default working style (carried by Solaris): terse responses; tables when comparing options; lead with an
  explicit recommendation; give the bare command first, then variants.
- (add project-specific conventions here as you learn them)
