_Rev. 8_

# Rule: aisee conventions <!-- omit in toc -->

- [When the visual leg runs](#when-the-visual-leg-runs)
- [Pick the right query kind](#pick-the-right-query-kind)
- [Server, tokens, and media](#server-tokens-and-media)
- [Evidence](#evidence)
- [Models and patience](#models-and-patience)

Always-on while this plugin is attached. House rules for using AISee - the VLM "eyes" and
audio "ears" served on a GPU host (see [`aisee.skill.md`](aisee.skill.md) for the procedure).

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
- `transcribe` for speech: a word-timestamped transcript of EVERY audio lane of a recording
  (a lane = one audio track, or one channel of a stereo/multi-channel track - stereo often
  encodes two feeds); pass `diarize: true` for per-lane speaker attribution, or use the
  `diarize` kind for who-spoke-when only. AISee never interprets or merges lanes - deciding
  what mic/system/per-participant lanes mean and combining them is the CONSUMER'S job;
  bit-identical lanes (equal decoded PCM) come back marked `duplicate_of`. Segment timestamps are absolute seconds
  in the recording; rendered per-lane transcripts (`transcript[-<lane>].txt/.srt/.vtt`, plus
  full-word JSON) download from `GET /v1/tasks/{id}/artifacts/<name>`. Pass speaker-count
  hints (`min/max/num_speakers`, applied per lane) when roughly known - long multi-party
  audio over-splits, and `suspicious_speaker_count: true` means re-run with a `max_speakers`
  hint.

## Server, tokens, and media

- The server URL comes from `ai/.memory/resources.md` (`aisee_server`); the consumer token, if
  any, from `ai/.memory/credentials.md`. Never hardcode hosts, ports, or tokens in shared or
  committed files.
- Media entries resolve **on the AISee host**. A file local to this machine goes up once via
  the blob store - sha256 the bytes, probe `GET /v1/blobs/{sha}`, `POST /v1/blobs` if missing -
  and is then referenced as `sha256:<hex>` (uploads are content-deduplicated, ~24 h TTL
  refreshed on reuse). Paths already on the AISee host pass as-is.

## Evidence

- Captures live under `ai/.memory/visual/` in the project (or inside the active task folder for
  ad-hoc work), named `<area>-<state>-<YYYYMMDD>.png` / `.mov`.
- **Never auto-commit captures or recordings.** Reference them by path in reports; commit only
  a curated baseline when the user asks.
- Quote the model's `reason`/`evidence` in reports next to the verdict.

## Models and patience

- Do not hardcode a model: omit it for the server default, or choose from `list_models` and the
  per-model strengths/weaknesses/pitfalls in `GET /v1/describe?flavor=mcp`.
- Mind each model's `Thinking:` line in describe: reasoning models always think (slower,
  deeper; cannot be disabled), thinking-toggle models think by default but take
  `thinking: false` for fast direct answers, and plain models never think. Thinking counts
  against the answer budget (`max_tokens`) - give thinking calls headroom.
- A cold or idle-unloaded model takes minutes to load; query tools block through it. Do not
  resubmit - that only queues more work. For long `watch`/`transcribe`/`diarize` jobs pass
  `wait=false` and poll `get_task` (ASR runs at roughly realtime/30 or faster once loaded).
  While a task runs, `progress` carries a `pct` (0-100) wherever completion is computable
  (watch chunks, audio lanes; interpolated inside long engine calls) - read it instead of
  guessing from elapsed time.
- Long meeting videos: `look`/`assert` with native video are safe at any length (the server
  re-encodes down to the model's frame budget), but detail-over-time questions still belong
  to `watch`. On unified-memory hosts avoid hour-scale transcriptions while a large VLM is
  resident - the audio engine is sacrificed by design and the task fails with a clear
  "engine connection failed" (retry after the VLM idle-unloads).
- Contexts are large (256k tokens on the Qwen3-VL / Cosmos family, 128k elsewhere - see
  `max_model_len` in describe) and image budgets are sized to fill them (`max_images`,
  often ~100+ stills per request), so prefer one big batched call over many small ones.
- Models state their GPU-memory need in GiB (`mem_gib` in describe/models). A start that
  does not fit next to the running models is refused up front with a GiB-denominated
  message - that is your cue to stop a resident model (admin) or use the default instead,
  not to retry. Two refusals ARE retry-later cases: "audio job(s) in progress" (a large
  model is held back while transcription/diarization runs on a unified host - retry when
  they finish) and a start right after a big job ends (memory takes a minute to settle).
