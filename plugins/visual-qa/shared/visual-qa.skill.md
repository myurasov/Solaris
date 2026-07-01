_Rev. 1_

---
name: visual-qa
triggers:
  - "visually verify <scope>" / "visually check <scope>"
  - "check the UI" / "does the UI look right"
  - "watch the flow" / "watch this recording"
  - "look at <screenshot|video>" / "what's on screen"
  - "visual regression" / "compare against baseline"
antitriggers:
  - "develop "
  - "work on tasks/"
summary: Capture the running UI (web / native / mobile), then have the VLM 'eyes' answer or assert against it - the visual leg of verification.
---

# visual-qa <!-- omit in toc -->

- [What this is](#what-this-is)
- [0. Choose a model (per GPU system)](#0-choose-a-model-per-gpu-system)
- [1. Capture](#1-capture)
- [2. Ask or assert](#2-ask-or-assert)
- [3. Report + evidence](#3-report--evidence)
- [Images vs. video](#images-vs-video)
- [Troubleshooting](#troubleshooting)

## What this is

The "eyes" for end-to-end verification: drive the running app, capture what a user would see, and have a
vision-language model **answer questions** or **assert expectations** about it. Use it as the visual leg of
`verify` / the engineer's run workflow - not a replacement for unit tests, but the check that the thing
actually *renders and behaves* right. Conventions (where captures live, the assertion JSON schema, the
Spark stream limit) are in [`visual-qa.rule.md`](visual-qa.rule.md).

The model is **pluggable and GPU-agnostic**: the client (`eyes.py`) talks to an OpenAI-compatible
endpoint (`VISUAL_QA_ENDPOINT`) with a configurable model (`VISUAL_QA_MODEL`). It runs against **any
NVIDIA GPU system** - a DGX Spark, a workstation RTX, a datacenter A100/H100, or a cloud endpoint - not
just the Spark. To stand an endpoint up, see [`serving/README.md`](../serving/README.md).

## 0. Choose a model (per GPU system)

Do this once per target system (or whenever the GPU or task changes) - **never hard-code a model**. The
best VLM depends on three things: **how much GPU memory** is available, the **GPU architecture** (decides
the quant: Blackwell -> NVFP4, Hopper/Ada -> FP8, Ampere/older -> AWQ int4), and the **task type**:

| Task | Use it for | Leans toward |
|---|---|---|
| `ui-stills` | screenshots, layout, element grounding | Qwen3-VL / Holo1.5 (UI-strong) |
| `ocr` | dense text extraction | Qwen3-VL / InternVL / MiniCPM-V |
| `ui-video` | short UI recordings / multi-step flows | Qwen3-VL (video) or a Cosmos reasoner |
| `physical-video` | real-world / camera / robotics video | NVIDIA Cosmos (Reason2 / Cosmos 3) |

Procedure:

1. **Detect the GPU.** Locally: `uv run --no-project ai/visual-qa/eyes.py detect`. On a remote host, run
   `nvidia-smi --query-gpu=name,memory.total --format=csv,noheader,nounits` there (via the `hss` wrapper)
   and read off the name + MiB. Unified-memory parts (e.g. GB10) report memory as `N/A` - use the usable
   budget instead (the Spark is ~110 GB).
2. **Get ranked options** for the detected `vram`/`arch` and the task:
   `uv run --no-project ai/visual-qa/eyes.py recommend --vram <GB> --arch <arch> --task <task>`
   (or `--detect` to read the local GPU). It returns a ranked shortlist with estimated VRAM, the quant,
   serving backend, and license. The catalog is editable data in [`models.json`](models.json).
3. **Ask the user to choose.** Present the top few options (name, size, est. VRAM, why) and let them pick -
   do not silently auto-select. Record the choice into `ai/memory/resources.md` and set
   `VISUAL_QA_MODEL` / `VISUAL_QA_VIDEO_MODEL` (and the serving command) accordingly.

## 1. Capture

Pick the capture path for the target (set at install via `capture_mode`):

- **Web** -> the **Playwright MCP**: navigate, interact, then `browser_take_screenshot` (full page or an
  element) and/or a trace for a flow. Save PNGs under the captures dir (see the rule).
- **Native / desktop / TUI** -> `screencapture` (macOS) for a still, or `ffmpeg`/`screencapture -v` for a
  short recording of the window. For a TUI, a terminal screenshot works.
- **Mobile** -> simulator/device screen recording (`xcrun simctl io booted recordVideo out.mov` for iOS
  Simulator; `adb exec-out screencap` / `adb shell screenrecord` for Android), or a still screenshot.

Keep recordings short (a few seconds) and frame-sample sparingly - vision tokens are the cost driver, and
the Spark is bandwidth-bound.

## 2. Ask or assert

Call the **`visual-qa` MCP** tools (or run `eyes.py` directly via Bash):

- **`look(media, question)`** -> free-text answer. Use to explore ("what's on this screen?", "is there an
  error state visible?").
- **`assert_visual(media, expectation)`** -> structured `{pass, reason, evidence}`. Use in verification:
  the `expectation` is a precise claim ("a login form with email and password fields and a blue Sign in
  button"); `pass` is the verdict, `reason` explains it, `evidence` quotes what the model saw.

CLI equivalents (for scripting / health checks):

```
uv run --no-project ai/visual-qa/eyes.py health
uv run --no-project ai/visual-qa/eyes.py look   --media shot.png  --q "what's on screen?"
uv run --no-project ai/visual-qa/eyes.py assert --media shot.png  --expect "login form with email + password"
uv run --no-project ai/visual-qa/eyes.py assert --media flow.mov --frames 8 --video --expect "checkout completes and a success toast appears"
```

`--media` accepts an image, multiple images (repeat the flag), or a video (`--video`, sampled to `--frames`
frames). Endpoint/model come from env or `--endpoint` / `--model`.

## 3. Report + evidence

Surface the verdict inline with the screenshot/recording as evidence (path + the model's `reason`). On a
failure, keep the capture so it can be diffed against a later run. For an engineer-driven verify, fold the
pass/fail into the run summary; do not auto-commit captures (see the rule).

## Images vs. video

- **Stills** (most UI assertions): default model `VISUAL_QA_MODEL` (a UI-strong VLM - Qwen3-VL) for the best
  OCR + element grounding.
- **Temporal / flow** ("did the animation glitch", "did the spinner ever resolve", "watch the multi-step
  flow"): pass `--video` and prefer the video backend `VISUAL_QA_VIDEO_MODEL` via `--model` - it reasons
  over space/time. The backend is a NVIDIA Cosmos reasoning VLM: **Cosmos 3 Nano reasoner** (newest) or
  the cached, turnkey **Cosmos-Reason2-8B** (see [`serving/README.md`](../serving/README.md)). This is the
  one place the "physical/temporal video" backend earns its keep for UIs.

## Troubleshooting

- **Connection refused** -> the endpoint isn't up; `eyes.py health` and check the Spark serving (vLLM/NIM).
- **OOM / very slow** -> reduce `--frames`, downscale the screenshot, or keep concurrent streams <= 2 on the
  Spark.
- **Wrong/hallucinated reads of small text** -> downscale less / crop to the region, or enable the
  OmniParser pre-pass if configured.
