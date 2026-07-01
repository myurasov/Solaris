#!/usr/bin/env bash
# Serve a vision-language model on DGX Spark via vLLM (OpenAI-compatible /v1 on :8000).
# Run ON the GPU host (e.g. a DGX Spark). Verify image tags against the DGX Spark playbooks first.
#
# Usage:
#   ./serve.sh                      # Qwen3-VL-8B-Instruct (NVFP4), default
#   MODEL=nvidia/Cosmos-Reason2-8B REASONING=qwen3 ./serve.sh   # temporal/video backend
#
# Env knobs:
#   MODEL        HF/NGC model id            (default Qwen/Qwen3-VL-8B-Instruct)
#   PORT         listen port               (default 8000)
#   MAX_LEN      --max-model-len           (default 32768)
#   QUANT        --quantization            (default nvfp4; set empty to disable)
#   REASONING    --reasoning-parser        (set to qwen3 for Cosmos-Reason2)
#   VIDEO_FRAMES default frames per video  (default 16)
#   HF_TOKEN     for gated model pulls
set -euo pipefail

MODEL="${MODEL:-Qwen/Qwen3-VL-8B-Instruct}"
PORT="${PORT:-8000}"
MAX_LEN="${MAX_LEN:-32768}"
QUANT="${QUANT:-nvfp4}"
REASONING="${REASONING:-}"
VIDEO_FRAMES="${VIDEO_FRAMES:-16}"

args=(serve "$MODEL"
  --port "$PORT"
  --max-model-len "$MAX_LEN"
  --limit-mm-per-prompt "{\"image\": 8, \"video\": 1}"
  --media-io-kwargs "{\"video\": {\"num_frames\": ${VIDEO_FRAMES}}}")
[ -n "$QUANT" ] && args+=(--quantization "$QUANT")
[ -n "$REASONING" ] && args+=(--reasoning-parser "$REASONING")

echo "Starting vLLM: model=$MODEL port=$PORT quant=${QUANT:-none} reasoning=${REASONING:-none}"

# If vllm is on PATH (venv/conda), run it directly; otherwise use the DGX Spark vLLM container.
if command -v vllm >/dev/null 2>&1; then
  exec vllm "${args[@]}"
else
  echo "vllm not on PATH - falling back to container (edit the image tag as needed)."
  exec docker run --rm --gpus all -p "${PORT}:${PORT}" \
    ${HF_TOKEN:+-e HF_TOKEN="$HF_TOKEN"} \
    -v "${HOME}/.cache/huggingface:/root/.cache/huggingface" \
    nvcr.io/nvidia/vllm:dgx-spark \
    vllm "${args[@]}"
fi
