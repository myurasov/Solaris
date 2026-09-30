---
name: aisee
triggers:
  - "visually verify <scope>" / "visually check <scope>"
  - "check the UI" / "does the UI look right"
  - "look at <screenshot|video>" / "what's on screen"
  - "watch this recording" / "watch the flow"
  - "visual regression" / "compare against baseline"
  - "OCR this" / "read the text on <screen>"
  - "transcribe <recording>" / "who said what"
  - "use aisee" / "aisee look" / "aisee assert" / "aisee watch" / "aisee transcribe"
  - "set up aisee"
antitriggers:
  - "develop "
  - "work on tasks/"
summary: Capture the UI (or take provided media), then have AISee's VLM eyes answer a question,
  assert an expectation, or watch a recording, or its audio models transcribe one - MCP first,
  REST/CLI fallback.
---
_Rev. 9_

# Skill: aisee - visual verification and transcription with AISee <!-- omit in toc -->

- [0. Reach the server](#0-reach-the-server)
- [1. Capture](#1-capture)
- [2. Query](#2-query)
  - [2a. MCP (preferred)](#2a-mcp-preferred)
  - [2b. REST fallback](#2b-rest-fallback)
  - [2c. CLI fallback](#2c-cli-fallback)
- [3. Report](#3-report)
- [Setting up a server](#setting-up-a-server)
- [Troubleshooting](#troubleshooting)

AISee is a tool that gives AI agents eyes and ears: `look` (free-form/OCR), `assert_visual`
(pass/fail verdicts), `watch` (chunked whole-video analysis), `transcribe` / `diarize`
(speaker-attributed transcripts / who spoke when). Conventions (query-kind choice,
evidence, media rules) are in [`aisee.rule.md`](aisee.rule.md) - they always apply.

## 0. Reach the server

1. Read `aisee_server` from `ai/.memory/resources.md` and the consumer token (if any) from
   `ai/.memory/credentials.md`.
2. Confirm liveness: the MCP `health` tool if the `aisee` MCP server is connected, else
   `GET <server>/v1/health` (open endpoint).
3. On the **first contact** with a server, read `GET <server>/v1/describe?flavor=mcp` - the
   server's own guide: tools, installed models with strengths/weaknesses/pitfalls, live
   serving config, and the exact media-upload recipe.
4. If the MCP tools are absent from your session, check the project MCP config for the
   `aisee` entry (type `http`, URL `<server>/mcp`, bearer header when auth is on) and reload
   the session; until then use the REST fallback. If the host itself is unreachable, see
   [Setting up a server](#setting-up-a-server).

## 1. Capture

Skip if the user already provided media.

- **Web:** browserctl `screenshot` (the `browserctl` plugin), or the Playwright MCP where a
  project still configures it (`browser_navigate`, `browser_take_screenshot`; record
  interactions as video if the check is temporal).
- **Native / TUI:** `screencapture` (macOS) / `import`/`grim` (Linux) for stills; ffmpeg or OS
  screen recording for video.
- **Mobile:** simulator/device screen recording.
- **Documents (rendered PDF reports):** one PNG per page with poppler,
  `pdftoppm -r 100 -png report.pdf <dir>/p`, then one assert per page, e.g. "No text on this
  page overlaps other text or graphics" (about 1-2 s per page on `qwen3-6-35b-a3b`).

Save per the evidence rule: `ai/.memory/visual/<area>-<state>-<YYYYMMDD>.png` (or the active
task folder).

## 2. Query

### 2a. MCP (preferred)

Tools: `look(media, question, ...)`, `assert_visual(media, expectation, ...)`,
`watch(video, question|expectation, fps?, chunk_seconds?, wait?, ...)`,
`transcribe(media, diarize?, min/max/num_speakers?, model?, diarize_model?, wait?)`,
`diarize(media, min/max/num_speakers?, model?, wait?)`, plus `list_models`, `list_tasks`,
`get_task`, `cancel_task`, `describe`, `health`. `look` and `assert_visual` take a list of
media entries; `watch`, `transcribe` and `diarize` take one entry (a string).

Media entries are AISee-host paths or `sha256:<hex>` blob refs. For a local file:

```bash
sha=$(shasum -a 256 shot.png | cut -d' ' -f1)     # sha256sum on Linux
curl -s <server>/v1/blobs/$sha \
     ${TOKEN:+-H "Authorization: Bearer $TOKEN"}   # {"exists": ...} - probe first
curl -s -X POST <server>/v1/blobs -F files=@shot.png \
     ${TOKEN:+-H "Authorization: Bearer $TOKEN"}   # only if exists=false
# then: assert_visual(media=["sha256:$sha"], expectation="...")
```

Query tools block until done (a cold model can take minutes - be patient, never resubmit).
For long jobs: pass `wait=false` (watch/transcribe/diarize) and poll `get_task` every few
seconds until `status` is terminal; `progress.pct` reports live completion where
computable. Transcribe results are PER LANE (`tracks` list: one entry per audio track and
per stereo channel; bit-identical lanes marked `duplicate_of`); word timings and rendered
`transcript[-<lane>].txt/.srt/.vtt` download from `GET /v1/tasks/{id}/artifacts/<name>`. Useful parameters: `model` (omit for default), `frames`
/ `fps` (video sampling; `watch` defaults to 2 fps), `chunk_seconds` (watch: shorter
chunks give each frame more detail), `native` (send the video itself - temporal reasoning,
video-capable models only), `context` (background the model cannot see in the pixels),
`max_tokens` (answer budget; truncation is flagged, never silent), `diarize` (transcribe:
add per-lane speaker attribution) / `diarize_model` (diarizer slug; pinned into the task) /
`min_speakers` / `max_speakers` / `num_speakers` (per-lane diarization hints), `thinking` (bool -
chain-of-thought on models whose describe entry says `Thinking: optional`; unset follows
the host's default, off unless the admin turned it on; pass `true` for hard questions -
thinking counts against `max_tokens`; always-on reasoning models ignore it - check each
model's `Thinking:` line in describe).

### 2b. REST fallback

```bash
curl -s -X POST <server>/v1/tasks -F files=@shot.png \
  -F 'params={"kind":"assert","expectation":"the Start button is visible and enabled"}' \
  ${TOKEN:+-H "Authorization: Bearer $TOKEN"}      # -> {"id": "..."}
curl -s <server>/v1/tasks/<id> ${TOKEN:+-H "Authorization: Bearer $TOKEN"}   # poll 2-5 s
```

Statuses: `queued -> model_loading (cold only) -> preparing_media -> running -> done`
(`failed`/`canceled` terminal). JSON submission takes `media_paths` (host paths or
`sha256:` refs) instead of multipart. `timings.total_s` reports wall-clock on finish.
REST (and the CLI's `--server-frames`) also takes `server_frames` on watch: frames per
chunk, default the model's frame budget - fewer means shorter chunks with more detail per
frame.

### 2c. CLI fallback

From a checkout of github.com/myurasov/AISee (`./aisee` bootstraps its own venv):

```bash
aisee assert shot.png -e "the Start button is visible" --server <server>   # exit code = verdict
aisee look shot.png -q "What error is shown?" --server <server>
aisee watch run.mp4 -e "the counter increases monotonically" --fps 8 --server <server>
aisee transcribe meeting.mp4 --diarize --max-speakers 5 --server <server>
```

The CLI uploads media itself (with the same dedup) and reads `AISEE_SERVER` /
`AISEE_API_TOKEN` from the environment. `--thinking` / `--no-thinking` switch
chain-of-thought for one call on thinking-toggle models.

## 3. Report

- Quote the verdict fields: `pass`, `reason`, `evidence` (or the `answer` for look; the
  synthesized answer / `failing_ranges` for watch).
- Link the capture paths used as evidence.
- One claim per assert; if a compound check was needed, list each sub-assert and its verdict.

## Setting up a server

If no AISee server is reachable and the user wants one: follow `aisee.admin.agent.md` (or
README.md) in github.com/myurasov/AISee on a Linux GPU host - in short:

```bash
git clone https://github.com/myurasov/AISee ~/aisee && cd ~/aisee
uv sync && ./aisee install            # checks docker/NVIDIA toolkit/ffmpeg, creates ~/.aisee
./aisee creds set HF_TOKEN            # gated model downloads
./aisee model install qwen3-6-35b-a3b   # recommended default
./aisee model install parakeet-tdt-0.6b-v3              # transcription (optional)
./aisee model install pyannote/speaker-diarization-3.1  # speakers (optional; HF-gated: accept 3 repo licenses)
./aisee api start                     # REST + MCP on 0.0.0.0:4444
```

**On a shared multi-GPU host** (another project uses the other GPUs):

- AISee (1.1.0b1, commit 876357d) starts model containers with `--gpus all` (two places in
  `src/dockerctl.py`) and has no GPU-selection setting, and its free-memory check reads only
  the first GPU. To stay on one GPU, patch both to `--gpus device=0` locally (GPU 0 is the one
  the memory check measures), record the patch with the server entry, and report the gap
  upstream.
- With no consumer token set, bind the API to localhost (`./aisee api start --host 127.0.0.1`)
  and reach it through an ssh tunnel: `ssh -N -L 14444:127.0.0.1:4444 <host>`, then
  `http://127.0.0.1:14444` (REST; MCP at `/mcp`).
- Sizing seen 2026-09-30: `qwen3-6-35b-a3b` on one 84 GiB GPU ran at `gpu_frac` 0.933 with a
  262k context; the weights (67 GB) and the vLLM image (38.4 GB) downloaded in about 4
  minutes, the first request waited 84 s for the model to load, then answers took about 1 s
  per photo.

Then record the URL in `ai/.memory/resources.md` (`aisee_server`), the consumer token (if
enabled) in `ai/.memory/credentials.md`, and update the `aisee` MCP entry in both `.mcp.json` and
`.cursor/mcp.json` (keep the two identical; under a Solaris checkout,
`uv run -m solaris.tools.mcp_sync --check` verifies).

## Troubleshooting

| Symptom | Likely cause / fix |
|---|---|
| 401 | consumer token missing/wrong - set the bearer header / `AISEE_API_TOKEN` |
| 403 | admin-only action (model install/start/stop) - not available over MCP; ask the server operator or use the CLI on the host |
| `unknown blob sha256:...` | blob expired (~24 h TTL) or never uploaded - re-upload via `POST /v1/blobs` |
| task stuck in `model_loading` | cold model load (first-ever use downloads tens of GB); keep polling `progress`, never resubmit |
| task `failed` with memory error | another model holds the GPU - `list_models`, ask operator to stop it, retry |
| model marked retired in describe | still served while installed; prefer the successor describe names (1.1 retired the Qwen3-VL and Nemotron entries) |
| MCP tools missing from session | project MCP config lacks the `aisee` entry or session predates it - fix the entry in both MCP configs, reload; use REST meanwhile |
| verdict looks wrong | read `reason`/`evidence`, tighten the expectation, add `context`, or retry with a stronger model from `describe` |
| a JSON answer does not parse | the model wrapped it in a ```` ```json ```` fence - strip the fence before parsing |
| model fills every GPU on a shared host | see "On a shared multi-GPU host" under Setting up a server |