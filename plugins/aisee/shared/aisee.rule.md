_Rev. 1_

# Rule: aisee conventions <!-- omit in toc -->

- [When the visual leg runs](#when-the-visual-leg-runs)
- [Pick the right query kind](#pick-the-right-query-kind)
- [Server, tokens, and media](#server-tokens-and-media)
- [Evidence](#evidence)
- [Models and patience](#models-and-patience)

Always-on while this plugin is attached. House rules for using AISee - the VLM "eyes" served
on a GPU host (see [`aisee.skill.md`](aisee.skill.md) for the procedure).

## When the visual leg runs

- During verify / the engineer's run workflow on any UI-affecting change, run at least one
  `assert_visual` against the affected screen before declaring the change done. A green test
  suite with a broken render is still broken.
- Treat the VLM verdict as advice, not gospel: a `pass:false` always warrants a human look; a
  `pass:true` on a vague expectation proves little. Keep each `expectation` a single concrete,
  checkable claim - split compound checks into multiple calls so a failure is attributable.

## Pick the right query kind

- `assert_visual` whenever you will branch on the outcome (tests, gates): returns
  `{pass, reason, evidence}`.
- `look` for open questions, OCR, descriptions, "where is X": returns text.
- `watch` for whole recordings (longer than ~1 minute) or time-localized checks: a question
  returns per-chunk findings + a synthesized answer; an expectation returns pass/fail plus the
  failing time ranges.

## Server, tokens, and media

- The server URL comes from `ai/memory/resources.md` (`aisee_server`); the consumer token, if
  any, from `ai/memory/credentials.md`. Never hardcode hosts, ports, or tokens in shared or
  committed files.
- Media entries resolve **on the AISee host**. A file local to this machine goes up once via
  the blob store - sha256 the bytes, probe `GET /v1/blobs/{sha}`, `POST /v1/blobs` if missing -
  and is then referenced as `sha256:<hex>` (uploads are content-deduplicated, ~24 h TTL
  refreshed on reuse). Paths already on the AISee host pass as-is.

## Evidence

- Captures live under `ai/memory/visual/` in the project (or inside the active task folder for
  ad-hoc work), named `<area>-<state>-<YYYYMMDD>.png` / `.mov`.
- **Never auto-commit captures or recordings.** Reference them by path in reports; commit only
  a curated baseline when the user asks.
- Quote the model's `reason`/`evidence` in reports next to the verdict.

## Models and patience

- Do not hardcode a model: omit it for the server default, or choose from `list_models` and the
  per-model strengths/weaknesses/pitfalls in `GET /v1/describe?flavor=mcp`.
- A cold or idle-unloaded model takes minutes to load; query tools block through it. Do not
  resubmit - that only queues more work. For long `watch` jobs pass `wait=false` and poll
  `get_task`.
