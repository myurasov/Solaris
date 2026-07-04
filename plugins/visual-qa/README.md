# visual-qa — VLM "eyes" for end-to-end visual testing <!-- omit in toc -->

- [What this is](#what-this-is)
- [Architecture](#architecture)
- [MCP API](#mcp-api)
- [CLI quick reference](#cli-quick-reference)
- [Serving (GPU host)](#serving-gpu-host)
- [Model performance comparison (measured)](#model-performance-comparison-measured)
- [Gotchas (learned in production)](#gotchas-learned-in-production)
- [Repo layout](#repo-layout)

A Solaris plugin that gives projects **visual verification**: capture what a user would see (screenshot,
screen recording, rendered output), then have a **vision-language model** answer questions (`look`) or
return strict pass/fail verdicts (`assert_visual`) about it — including **whole-video watching** with a
configurable temporal resolution (`watch`, `--fps`). Works against any OpenAI-compatible VLM endpoint on
any NVIDIA GPU (DGX Spark, workstation RTX, datacenter, cloud).

## What this is

- **Client** — [`shared/eyes.py`](shared/eyes.py): zero-dependency HTTP client (PEP 723 script), usable as
  a CLI or as an **MCP stdio server**. Materialized into each project at `ai/visual-qa/eyes.py`.
- **Instance registry** — `ai/memory/visual-qa-endpoints.json` (project private layer): the installed
  serving instances. **One ACTIVE instance at a time**; every call routes to it unless `--model` overrides.
- **Serving** — [`serving/`](serving/): lifecycle scripts that run models as memory-capped docker/vLLM
  instances under `~/.solaris/visual-qa/` on the GPU host (Solaris remote-footprint convention).

## Architecture

```
project (Mac/anywhere)                      GPU host (e.g. DGX Spark)
┌──────────────────────────────┐            ~/.solaris/visual-qa/
│ agent / CLI / test scripts   │            ┌─────────────────────────────────┐
│   └─ eyes.py (CLI or MCP)    │──HTTP/v1──▶│ solaris-visual-qa-<model-slug>  │
│      routes via registry:    │            │   vLLM container, GPU_FRAC cap  │
│      ACTIVE instance         │            │   (+ more instances, stopped or │
│      (use <name> to switch)  │            │    memory-capped co-resident)   │
└──────────────────────────────┘            └─────────────────────────────────┘
```

Instance names are **slugs of the model name** (org prefix dropped): `Qwen/Qwen3-VL-32B-Instruct` →
`qwen3-vl-32b-instruct`; container `solaris-visual-qa-qwen3-vl-32b-instruct`, config
`config-qwen3-vl-32b-instruct.env`.

## MCP API

`mcps.json` registers the stdio server in each project (merged into `.mcp.json` at plugin install):
`uv run --no-project ai/visual-qa/eyes.py mcp`, env `VISUAL_QA_*` as fallback when no registry exists.

| Tool | Parameters (defaults) | Returns |
|---|---|---|
| `look_tool` | `media: list[str]`, `question: str`, `video=False`, `frames=8`, `fps=0`, `native=False`, `model=""` | free-text answer (str) |
| `assert_visual_tool` | `media: list[str]`, `expectation: str`, `video=False`, `frames=8`, `fps=0`, `native=False`, `model=""` | `{pass: bool, reason, evidence}` |
| `watch_tool` | `media: str`, one of `question`/`expectation`, `fps=1.0`, `chunk_seconds=0(auto)`, `native=True`, `scale=0`, `server_frames=16`, `model=""` | assert mode: `{pass, failing_ranges, reason, chunks[]}` · question mode: `{summary, chunks[]}` |
| `health_tool` | `endpoint=""` (empty = sweep ALL registered instances) | `{ok, active, instances[{name, model, ok, models, active}]}` |
| `use_model_tool` | `name: str` (instance slug) | `{active, model, endpoint}` — **switches the ACTIVE instance for all calls (persists)** |
| `pick_model_tool` | `task: ui-stills\|ocr\|ui-video\|physical-video` | **advisory** recommendation `{name, model, why, alternatives, active}` — does NOT switch |
| `recommend_models_tool` | `vram_gb`, `task`, `arch=""`, `top=4` | ranked catalog shortlist for a GPU (pre-serving) |

Semantics:
- **media paths** are file paths relative to the project root (the server's cwd) or absolute.
- **video handling**: `frames=N` samples N frames evenly across the whole clip (sent as images);
  `fps>0` samples at that rate instead; `native=True` sends the clip itself (server-side, timestamp-aware
  sampling; auto re-encoded to MJPEG for decoder compatibility). `watch_tool` chunks the clip
  (`server_frames/fps` seconds each) for **full coverage** of any length at any fps.
- **model=""** → the ACTIVE instance; `model="<slug-or-model-id>"` → one-off override.
- Verdict JSON is schema-stable across models, so swapping models never changes consumers.

## CLI quick reference

```bash
E=ai/visual-qa/eyes.py                      # in a project; add VISUAL_QA_TIMEOUT=300 for 32B-class models
uv run --no-project $E health               # all instances + which is active
uv run --no-project $E use cosmos-reason2-8b       # switch the ACTIVE instance (persists)
uv run --no-project $E pick --task ui-video        # advisory recommendation only
uv run --no-project $E look   --media shot.png --q "what's on screen?"
uv run --no-project $E assert --media shot.png --expect "a login form with email + password"
uv run --no-project $E assert --media run.mp4 --video --native --expect "no error dialogs appear"
uv run --no-project $E watch  --media run.mp4 --fps 1  --q "when does the scene load?"
uv run --no-project $E watch  --media run.mp4 --fps 15 --expect "no black or corrupted frames"
uv run --no-project $E detect                      # local GPU
uv run --no-project $E recommend --vram 110 --arch blackwell --task ui-stills
```

## Serving (GPU host)

**Default posture: ONE instance running.** Others stay installed-but-stopped; swap with
`stop.sh`/`start.sh` + `eyes.py use <slug>`. Everything lives under **`~/.solaris/visual-qa/`**
(config-`<slug>`.env per instance, shared `hf-cache/`, logs, the scripts themselves).

```bash
# install (creates/replaces the instance container; instance name = model slug)
GPU_FRAC=0.70 PORT=random ./serving/install.sh                     # qwen3-vl-32b-instruct
MODEL=nvidia/Cosmos-Reason2-8B REASONING=qwen3 GPU_FRAC=0.22 PORT=random ./serving/install.sh
MODEL=Hcompany/Holo1.5-7B GPU_FRAC=0.20 PORT=random ./serving/install.sh

./serving/status.sh                              # every instance: model, port, state, health
INSTANCE=<slug> ./serving/stop.sh                # unload (GPU freed; files/cache kept); no arg = all
INSTANCE=<slug> ./serving/start.sh               # reload (minutes; no re-download); no arg = all
INSTANCE=<slug> ./serving/uninstall.sh           # remove one; --all [--purge] = everything
```

Knobs (env): `MODEL`, `INSTANCE` (default: model slug), `PORT` (`random` picks a free high port once and
persists it), **`GPU_FRAC`** (vLLM `--gpu-memory-utilization` — must cover weights + overhead + KV; cap it
when co-locating so fractions sum well under 1.0), `MAX_LEN`, `QUANT` (only with a prequantized
checkpoint), `REASONING` (`qwen3` for Cosmos-Reason2), `VIDEO_FRAMES` (server per-video frame budget),
`EXTRA_ARGS` (e.g. `--enforce-eager`), `VLLM_IMAGE` (default `nvcr.io/nvidia/vllm:26.06-py3`),
`HF_TOKEN` (gated weights), `NGC_API_KEY` (nvcr.io login).

Alternative serving paths (no instance framework): **NIM** for Cosmos-Reason2-8B (verified GB10 recipe:
BF16 profile `NIM_MODEL_PROFILE=3266ed3ec2297386d2e4a94e6c84a5b0ba92244f787c538f33577c4c78c5aef2`,
`NIM_DISABLE_CUDA_GRAPH=1`, run as your UID against a user-owned cache — the auto-selected FP8 `dgx-spark`
profile crashes at CUDA-graph capture and emits garbage in eager mode) and **Ollama**
(`ollama pull qwen2.5vl:7b`, OpenAI `/v1` on :11434) — both expose OpenAI-compatible endpoints, so the
client is unchanged.

## Model performance comparison (measured)

DGX Spark GB10 (unified 128 GB, bandwidth-bound), vLLM 26.06, BF16, single stream. Latencies are
end-to-end per call from the client (incl. frame sampling + upload). Stills ~1500×950; video frames
800×600 unless noted. <!-- PERF_TABLE -->

| Metric | Qwen3-VL-32B | Cosmos-Reason2-8B | Holo1.5-7B | Nemotron-Nano-12B-VL (NVFP4) |
|---|---|---|---|---|
| Weights / GPU_FRAC | ~63 GB BF16 / 0.70 | ~17 GB BF16 / 0.22 | ~16 GB BF16 / 0.20 | **~11 GB FP4 / 0.22** |
| Extra serve flags | — | `--reasoning-parser qwen3` | `--enforce-eager` | `--enforce-eager --trust-remote-code` |
| Still assert | 24–45 s | 5–7 s | 5.6–8.6 s | **3.7–6.8 s** |
| Video assert (native) | 19–40 s ✅ | 4–13 s ✅ | ✗ (clip → one frame) | **6.7 s ✅** |
| OCR read / grounding look | 4 / 11.5 s | 8–24 s | 1.4 / 2.4 s | **1.0 / 1.5 s** |
| Negative control | ✅ rejects | ✅ rejects | ✅ rejects (3.4 s) | ✅ rejects (3.2 s) |
| OCR accuracy (article count) | `7,189,000+` ✓ | — | `7,189,000+` ✓ | `7,819,000+` ✗ (digit slip) |
| Strengths | precise UI reads, OCR, synthesis | temporal/physical judgments, speed | fastest; strong OCR + element grounding | **fast (FP4); stills + video in one; commercial license** |
| Weaknesses | slow on this box | embellishes UI detail; weak synthesis | not a video model; terse | occasional OCR digit error |

_Cosmos3-Nano runs on the GB10 via **`vllm/vllm-omni:cosmos3-aarch64`** (the nvcr `nvidia/vllm:26.06` image can't — it lacks the `cosmos3_omni` arch). Use `--hf-overrides '{"architectures":["Cosmos3ForConditionalGeneration"]}'` (the reasoner-only class name is NOT in this image) + `--enforce-eager --trust-remote-code`; expect a slow (~9 min) structure init before shards load. Benchmarked 4/4 asserts, correct OCR, native video ✅, ~7-10 s steady. The `nim/nvidia/cosmos3-reasoner` NIM is datacenter-GPU-only (no GB10 profile). See the project's `res/report-6` + `research-cosmos3-serving-0702.md`._

**Default model: `qwen3-vl-30b-a3b-instruct`** (MoE — 32B-class quality at ~5-7 s). Full 7-model matrix (adds Nemotron-NVFP4, Cosmos3-Nano, Qwen3-VL-30B-A3B, UI-TARS): see the project's `res/report-7-model-comparison.md`.

Earlier measured runs: `ai/memory/visual/dual-model-*` + `res/report-4-dual-model-visual-qa.md`
and `res/report-5-*` (Holo + system/browser scenarios).

## Gotchas (learned in production)

- **vLLM 26.06 container ships a broken `prometheus-fastapi-instrumentator`** (500s every request:
  `'_IncludedRouter' has no attribute 'path'`). `install.sh` patches it in-container post-start.
- **Reasoning models** (Cosmos-Reason2 behind `--reasoning-parser`) return `content: null` with the text in
  `message.reasoning`/`reasoning_content` — the client falls back automatically (rev 6+).
- **"No available memory for the cache blocks" crash-loop** → `GPU_FRAC` too small for weights + overhead +
  KV (any `UnicodeDecodeError` after it is shutdown noise). 32B BF16 needs ~0.70 on a 128 GB Spark.
- **Container hangs at "Starting to load model" with no shard progress and no error** (seen on GB10 with
  Holo1.5-7B) → vLLM's CUDA-graph capture stalls; add `EXTRA_ARGS="--enforce-eager"` at install (small
  latency cost, reliable load).
- **`ValidationError: … contains custom code … pass trust_remote_code=True`** (e.g. Nemotron-Nano-VL) → the
  model ships custom modeling code; add `--trust-remote-code` to `EXTRA_ARGS`.
- **`model type 'X' but Transformers does not recognize this architecture`** (e.g. Cosmos3 `cosmos3_omni`)
  → the checkpoint is newer than the container's vLLM/transformers; `--hf-overrides` on `architectures`
  won't fix an unrecognized *base model type*. Use a newer `VLLM_IMAGE` when one ships; the arch also needs
  vLLM model-runner support, not just a transformers config.
- **Prequantized NVFP4 checkpoints** (…-NVFP4/-NVFP4-QAD) auto-detect their quant — do **not** pass
  `--quantization`; leave `QUANT` empty. FP4 weights are ~half the BF16 footprint and faster on Blackwell.
- **HTTP 400 "At most N image(s)"** → server's `--limit-mm-per-prompt` (install default 8); keep
  `--frames` ≤ cap.
- **Serving container's OpenCV lacks H.264** ("Could not open video stream") → native video is auto
  re-encoded to MJPEG-AVI by the client.
- **HF-gated weights 403 "not in the authorized list"** → the token's *account* must accept the license on
  the model page; a valid token alone is not access.
- Keep concurrent vision streams ≤ 2 on bandwidth-bound boxes; downscale captures (`--scale`) to cut latency.

## Repo layout

| Path | Role |
|---|---|
| `manifest.json` | plugin manifest (`setup` consumed by install-plugin) |
| `mcps.json` | MCP servers merged into the project (`visual-qa`, `playwright`) |
| `shared/eyes.py` | the client (CLI + MCP); materialized to `ai/visual-qa/` |
| `shared/visual-qa.skill.md` / `.rule.md` | skill (trigger-invoked) + always-on conventions |
| `shared/models.json` | model catalog for the recommender |
| `serving/*.sh` | instance lifecycle on the GPU host |
| `migrations/` | plugin version migrations |
