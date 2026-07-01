# Serving the visual-qa VLM (any NVIDIA GPU) <!-- omit in toc -->

- [Prerequisites](#prerequisites)
- [0. Pick a model for your GPU](#0-pick-a-model-for-your-gpu)
- [Path A - vLLM, Qwen3-VL (default)](#path-a---vllm-qwen3-vl-default)
- [Path B - Cosmos for temporal / flow video](#path-b---cosmos-for-temporal--flow-video)
- [Path C - Ollama (easiest, no key; great on ARM/Spark)](#path-c---ollama-easiest-no-key-great-on-armspark)
- [Verify](#verify)
- [Keep it running](#keep-it-running)
- [Capacity reminder](#capacity-reminder)

The "eyes" client (`shared/eyes.py`) needs an **OpenAI-compatible** chat endpoint exposing a
vision-language model. This works on **any NVIDIA GPU system** - a DGX Spark, a workstation RTX, a
datacenter A100/H100, or a cloud endpoint. One endpoint, swappable model:

- **Stills / UI**: a UI-strong VLM - **Qwen3-VL** / **Holo1.5** - via **vLLM**. Best OCR + element
  grounding for web/native/mobile assertions.
- **Temporal / flow video**: a NVIDIA Cosmos reasoning VLM - **Cosmos 3 Nano reasoner** (newest, 2026-06,
  served standalone via vLLM at ~17 GB) or **Cosmos-Reason2-8B** (turnkey via NIM).

Which exact model + size depends on your GPU - **start at step 0**.

> Run remote-mutating steps with confirmation (Solaris safety rule). Reach a remote box with the `hss`
> SSH wrapper. Exact image tags / model repo ids drift - verify before pulling (e.g. the
> [vLLM DGX Spark blog](https://vllm.ai/blog/2026-06-01-vllm-dgx-spark) for Blackwell/`sm_121`).

## Prerequisites

- A container runtime + NVIDIA Container Toolkit (or a Python env with vLLM installed).
- A CUDA/driver new enough for your arch (Blackwell `sm_120/121` needs CUDA 13.x; the Cosmos-Reason2 NIM
  needs CUDA 13.0).
- Credentials as needed: an NGC API key for NIM / `nvcr.io` pulls; an HF token for gated weights
  (Qwen3-VL, Cosmos3-Nano). Keep these in `memory/credentials.md` (gitignored), by name.
- The serving port (default `8000`) reachable from where the client runs.

## 0. Pick a model for your GPU

Don't guess - let the recommender rank options by GPU memory + architecture + task, then choose:

```bash
# detect the local GPU (name / arch / VRAM), or read nvidia-smi on a remote host
uv run --no-project ../shared/eyes.py detect
# rank models for, e.g., a 24 GB Ada card doing UI stills
uv run --no-project ../shared/eyes.py recommend --vram 24 --arch ada --task ui-stills
```

The quant follows the architecture (the recommender applies this automatically):

| Arch | Examples | Quant | Notes |
|---|---|---|---|
| Blackwell | DGX Spark GB10, RTX 50, B200 | **NVFP4** | native FP4; best fit/speed |
| Hopper | H100, H200 | **FP8** | |
| Ada | RTX 40, L40/L4 | **FP8** | |
| Ampere | A100, A40, RTX 30 | **AWQ int4** | no FP8 hardware |
| Turing/older | T4, RTX 20 | **AWQ int4** / BF16 | smallest models only |

Plug the chosen model id into the commands below (and set `VISUAL_QA_MODEL` / `VISUAL_QA_VIDEO_MODEL`).
The examples use Qwen3-VL-8B and a Blackwell/Spark NVFP4 setup; adjust `--quantization` for your arch.

## Path A - vLLM, Qwen3-VL (default)

`serve.sh` wraps the container run. In short:

```bash
# on your GPU box
vllm serve Qwen/Qwen3-VL-8B-Instruct \
  --port 8000 \
  --quantization nvfp4 \
  --max-model-len 32768 \
  --limit-mm-per-prompt '{"image": 8, "video": 1}' \
  --media-io-kwargs '{"video": {"num_frames": 16}}'
```

Notes:
- Prefer a prequantized **NVFP4** checkpoint if available for the chosen model; otherwise drop
  `--quantization nvfp4` and run BF16/FP8 (fits in 128 GB at this size, just slower).
- `--max-model-len` trades context for KV-cache memory; 32k is comfortable for screenshots.
- Cosmos-Reason2 on vLLM additionally wants `--reasoning-parser qwen3` (it is Qwen3-VL-based):
  `vllm serve nvidia/Cosmos-Reason2-8B --reasoning-parser qwen3 --port 8000 ...`.

## Path B - Cosmos for temporal / flow video

Use this when an assertion is about motion/time (animation glitch, spinner that never resolves,
a multi-step flow). Both options expose an OpenAI-compatible `/v1`, so `eyes.py` is unchanged -
just point `--model` / `VISUAL_QA_VIDEO_MODEL` at the served name.

**B1 - Cosmos 3 Nano reasoner via vLLM (newest).** Cosmos 3 (2026-06-01) is a unified omni-model;
extract just its reasoner tower (the VLM, ~17 GB) so generation/action weights don't load:

```bash
# needs an HF token for the gated nvidia/Cosmos3-Nano repo; ~30 GB download
vllm serve nvidia/Cosmos3-Nano \
  --hf-overrides '{"architectures": ["Cosmos3ReasonerForConditionalGeneration"]}' \
  --reasoning-parser qwen3 --port 8000 --max-model-len 32768
```

> Pre-release verification builds note that behavior/APIs may shift; verify the architecture
> string + repo id against the [Cosmos 3 HF blog](https://huggingface.co/blog/nvidia/cosmos-3-for-physical-ai)
> and the [DGX Spark usage map](https://dev.classmethod.jp/en/articles/dgx-spark-cosmos3-family-usecase-map/).

**B2 - Cosmos-Reason2-8B via NIM (stable, already cached).** The image is present on both Sparks.
The command below is **verified working on a DGX Spark GB10** - the flags matter:

```bash
# needs NGC_API_KEY at runtime to fetch the model engine (docker login nvcr.io is not enough)
mkdir -p ~/nim-cache                          # user-owned cache (NIM runs non-root)
docker run -d --name visual-qa-nim --gpus all \
  --ipc=host --ulimit memlock=-1 --ulimit stack=67108864 \
  -u $(id -u):$(id -g) \
  -e NGC_API_KEY \
  -e NIM_DISABLE_CUDA_GRAPH=1 \
  -e NIM_MODEL_PROFILE=3266ed3ec2297386d2e4a94e6c84a5b0ba92244f787c538f33577c4c78c5aef2 \
  -v ~/nim-cache:/opt/nim/.cache \
  -p 8000:8000 \
  nvcr.io/nim/nvidia/cosmos-reason2-8b:1.6.0
```

> **DGX Spark / GB10 gotchas (learned the hard way):**
> - Run as your UID (`-u $(id -u):$(id -g)`) against a **user-owned** cache dir, else NIM hits
>   `PermissionError` on `/opt/nim/.cache` (Docker creates root-owned mount dirs).
> - The auto-selected **FP8 `dgx-spark` profile crashes** at CUDA-graph capture
>   (`cudaErrorStreamCaptureUnsupported` via the FlashInfer autotuner) and, in eager mode, emits
>   **garbage tokens**. Pin the **BF16 profile** instead (the `NIM_MODEL_PROFILE` hash above; ~16 GB,
>   fits easily) and set `NIM_DISABLE_CUDA_GRAPH=1`. List profiles with
>   `docker run --rm -e NGC_API_KEY <image> list-model-profiles`.
> - First run downloads the engine (several minutes); `--ipc=host` + the ulimits silence the SHMEM
>   warning and matter for the VLM NIM.

## Path C - Ollama (easiest, no key; great on ARM/Spark)

For UI-strong models without wrestling vLLM-on-ARM, Ollama is the simplest path and exposes an
OpenAI-compatible `/v1` on `:11434`. **Verified working on a DGX Spark GB10** with Qwen2.5-VL:

```bash
ollama pull qwen2.5vl:7b          # ~6 GB, ungated, no key
# served at http://localhost:11434/v1 ; point the client at it:
VISUAL_QA_ENDPOINT=http://localhost:11434/v1 VISUAL_QA_MODEL=qwen2.5vl:7b \
  python3 eyes.py assert --media shot.png --expect "..."
```

> Ollama sizes its memory estimate from the model's full context window - if it reports
> `requires more system memory than is available`, free GPU memory (stop other servers) or lower the
> context. On a Spark, don't run a big vLLM/NIM and a large Ollama model at the same time.

## Verify

```bash
curl http://<gpu-host>:8000/v1/models                       # lists the served model
uv run --no-project ../shared/eyes.py health --endpoint http://<gpu-host>:8000/v1
```

## Keep it running

Wrap the chosen command in a `systemd` unit (or `docker run --restart unless-stopped`) so the
endpoint survives reboots. Record the final endpoint + model in `memory/resources.md`.

## Capacity reminder

Memory-bandwidth bound: keep **concurrent vision streams ≤ 2** at high vision-token budgets, prefer
NVFP4 weights, and sample video to the fewest frames that answer the question.
