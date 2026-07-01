_Rev. 1_

# Rule: visual-qa conventions <!-- omit in toc -->

- [When the visual leg runs](#when-the-visual-leg-runs)
- [Captures + baselines](#captures--baselines)
- [Assertion schema](#assertion-schema)
- [Model + endpoint](#model--endpoint)
- [GPU capacity](#gpu-capacity)

Always-on while this plugin is attached. House rules for using the VLM "eyes" (see
[`visual-qa.skill.md`](visual-qa.skill.md)).

## When the visual leg runs

- During `verify` / the engineer's run workflow on any UI-affecting change, run at least one
  `assert_visual` against the affected screen before declaring the change done. A green test suite with a
  broken render is still broken.
- Treat the VLM verdict as advice, not gospel: a `pass:false` always warrants a human look; a `pass:true`
  on a vague expectation proves little. Write **precise** expectations.

## Captures + baselines

- Captures and baselines live under `ai/memory/visual/` in the project (or inside the active task folder for
  ad-hoc work). Name them `<area>-<state>-<YYYYMMDD>.png` / `.mov` (e.g. `checkout-success-20260622.png`).
- **Never auto-commit captures or recordings** - they are large and often transient. Reference them by path
  in the report; commit only a curated baseline when the user asks.
- For regression, compare a fresh capture against the stored baseline by asking the model to describe
  differences, not by pixel-diffing (anti-aliasing/timing make pixel diffs noisy).

## Assertion schema

`assert_visual` returns exactly:

```json
{ "pass": true, "reason": "<one or two sentences>", "evidence": "<what the model saw, quoted>" }
```

Consumers key off `pass` (boolean). Keep `expectation` a single concrete, checkable claim - split compound
checks into multiple calls so a failure is attributable.

## Model + endpoint

- The backend is pluggable via env: `VISUAL_QA_ENDPOINT` (OpenAI-compatible base URL),
  `VISUAL_QA_MODEL` (stills), `VISUAL_QA_VIDEO_MODEL` (temporal/flow).
- **Never hard-code a model id** in project code or skills - read it from config/env so the endpoint can
  be re-pointed (a different GPU box, a linked pair, or a cloud endpoint) without code changes.
- **Choose the model per GPU system, and offer the user a choice.** Pick by GPU memory + architecture +
  task via `eyes.py recommend` (skill step 0); present the top few options and let the user decide - do
  not silently auto-select. The quant follows the arch: Blackwell -> NVFP4, Hopper/Ada -> FP8,
  Ampere/older -> AWQ int4. Cosmos is for `physical-video` (and is a fair `ui-video` option); a UI-strong
  VLM (Qwen3-VL / Holo1.5) is the default for `ui-stills` / `ocr`.

## GPU capacity

- Match the model to the hardware: leave headroom for KV cache + vision tokens (the recommender already
  budgets ~10% + a task overhead). Sample video to the fewest frames that answer the question (8 is
  usually plenty for a short flow).
- On memory-bandwidth-bound boxes (e.g. DGX Spark, ~273 GB/s) keep **concurrent vision streams <= 2** at
  high vision-token budgets and prefer the efficient quant for the arch.
