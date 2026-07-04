#!/usr/bin/env bash
# Install a visual-qa VLM serving INSTANCE on a GPU host (run ON the host).
# Multiple instances can be resident at once (one container + port + memory slice each,
# sharing one weights cache) so callers pick the best model per task.
# Solaris remote-footprint convention: everything lands under ~/.solaris/visual-qa/ .
# Pair: uninstall.sh (per instance / --all), status.sh (list + health).
#
# Usage (on the host):
#   ./install.sh                     # default model; instance = model slug (qwen3-vl-32b-instruct)
#   MODEL=nvidia/Cosmos-Reason2-8B REASONING=qwen3 GPU_FRAC=0.22 PORT=random ./install.sh
#                                    # -> instance cosmos-reason2-8b
#
# Env knobs:
#   INSTANCE      instance name                   (default: slug of the model name, org dropped,
#                                                  e.g. qwen3-vl-32b-instruct; container
#                                                  solaris-visual-qa-<name>, config config-<name>.env)
#   MODEL         HF model id                     (default Qwen/Qwen3-VL-32B-Instruct)
#   PORT          listen port                     (default 8000; `random` picks a free high port once
#                                                  per instance and persists it)
#   GPU_FRAC      vLLM --gpu-memory-utilization   (default 0.85; CAP IT when co-locating instances:
#                                                  the fractions of all resident instances must sum
#                                                  well under 1.0, e.g. 32B=0.62 + 8B=0.22)
#   MAX_LEN       --max-model-len                 (default 32768)
#   QUANT         --quantization                  (default empty = native BF16; set nvfp4
#                                                  ONLY with a prequantized NVFP4 checkpoint)
#   REASONING     --reasoning-parser              (qwen3 for Cosmos-Reason2 models)
#   VIDEO_FRAMES  frames sampled per video        (default 16)
#   EXTRA_ARGS    extra vllm serve args           (optional, e.g. "--enforce-eager")
#   VLLM_IMAGE    container image                 (default nvcr.io/nvidia/vllm:26.06-py3)
#   HF_TOKEN      HF token for gated weights      (optional; stored 600 in the instance env)
#   NGC_API_KEY   if set, docker-login to nvcr.io before pulling (optional)
set -euo pipefail

ROOT="$HOME/.solaris/visual-qa"
MODEL="${MODEL:-Qwen/Qwen3-VL-32B-Instruct}"
# instance name defaults to the slug of the model name (org prefix dropped)
_slug() { printf '%s' "${1##*/}" | tr '[:upper:]' '[:lower:]' | sed -E 's/[^a-z0-9]+/-/g; s/^-+//; s/-+$//'; }
INSTANCE="${INSTANCE:-$(_slug "$MODEL")}"
SUFFIX="-$INSTANCE"
NAME="solaris-visual-qa$SUFFIX"
CFG="$ROOT/config$SUFFIX.env"
PORT="${PORT:-8000}"
GPU_FRAC="${GPU_FRAC:-0.85}"
MAX_LEN="${MAX_LEN:-32768}"
QUANT="${QUANT:-}"
REASONING="${REASONING:-}"
VIDEO_FRAMES="${VIDEO_FRAMES:-16}"
VLLM_IMAGE="${VLLM_IMAGE:-nvcr.io/nvidia/vllm:26.06-py3}"

# PORT=random: pick a free high port once; on re-install reuse only a port that
# was itself randomly chosen (PORT_RANDOM=1 marker), never a stale explicit one
PORT_RANDOM=0
if [ "$PORT" = "random" ]; then
  PORT_RANDOM=1
  if [ -f "$CFG" ] && grep -q '^PORT_RANDOM=1' "$CFG"; then
    PORT="$(grep '^PORT=' "$CFG" | cut -d= -f2)"
    echo "PORT=random: reusing previously chosen port $PORT"
  else
    for _ in $(seq 1 50); do
      c=$(( (RANDOM % 29152) + 20000 ))   # 20000-49151
      if ! ss -tln 2>/dev/null | awk '{print $4}' | grep -q ":${c}\$"; then PORT="$c"; break; fi
    done
    [ "$PORT" = "random" ] && { echo "could not find a free port"; exit 1; }
    echo "PORT=random: chose free port $PORT"
  fi
fi

echo "== visual-qa install: instance=$INSTANCE -> $NAME (model=$MODEL port=$PORT gpu_frac=$GPU_FRAC quant=${QUANT:-bf16}) =="
mkdir -p "$ROOT/hf-cache" "$ROOT/logs"

cat > "$CFG" <<EOF
INSTANCE=$INSTANCE
MODEL=$MODEL
PORT=$PORT
PORT_RANDOM=$PORT_RANDOM
GPU_FRAC=$GPU_FRAC
MAX_LEN=$MAX_LEN
QUANT=$QUANT
REASONING=$REASONING
VIDEO_FRAMES=$VIDEO_FRAMES
VLLM_IMAGE=$VLLM_IMAGE
EXTRA_ARGS=${EXTRA_ARGS:-}
HF_TOKEN=${HF_TOKEN:-}
EOF
chmod 600 "$CFG"

# keep the scripts with the install (self-contained footprint)
SELF_DIR="$(cd "$(dirname "$0")" && pwd)"
for f in install.sh uninstall.sh start.sh stop.sh status.sh; do
  [ -f "$SELF_DIR/$f" ] || continue
  [ "$SELF_DIR/$f" -ef "$ROOT/$f" ] 2>/dev/null || cp -f "$SELF_DIR/$f" "$ROOT/$f" 2>/dev/null || true
done

# optional NGC login (needed for nvcr.io pulls on a fresh box)
if [ -n "${NGC_API_KEY:-}" ]; then
  echo "$NGC_API_KEY" | docker login nvcr.io -u '$oauthtoken' --password-stdin >/dev/null
  echo "docker: logged in to nvcr.io"
fi

docker pull "$VLLM_IMAGE"

args=(vllm serve "$MODEL"
  --host 0.0.0.0 --port "$PORT"
  --gpu-memory-utilization "$GPU_FRAC"
  --max-model-len "$MAX_LEN"
  --limit-mm-per-prompt '{"image": 8, "video": 1}'
  --media-io-kwargs "{\"video\": {\"num_frames\": ${VIDEO_FRAMES}}}")
[ -n "$QUANT" ] && args+=(--quantization "$QUANT")
[ -n "$REASONING" ] && args+=(--reasoning-parser "$REASONING")
# shellcheck disable=SC2206 — intentional word-splitting of extra args
[ -n "${EXTRA_ARGS:-}" ] && args+=(${EXTRA_ARGS})

# replace any prior container for THIS instance (idempotent re-install; cache persists)
docker rm -f "$NAME" >/dev/null 2>&1 || true
docker run -d --name "$NAME" --restart unless-stopped \
  --gpus all --ipc=host --ulimit memlock=-1 --ulimit stack=67108864 \
  -e HF_HOME=/hf-cache ${HF_TOKEN:+-e HF_TOKEN -e HUGGING_FACE_HUB_TOKEN="$HF_TOKEN"} \
  -v "$ROOT/hf-cache:/hf-cache" \
  -p "${PORT}:${PORT}" \
  "$VLLM_IMAGE" "${args[@]}"

# vLLM 26.06 container bug: prometheus-fastapi-instrumentator 8.0.0 crashes on FastAPI
# routers without .path (AttributeError: '_IncludedRouter' has no attribute 'path'),
# 500-ing EVERY request. Patch it None-safe inside the container (survives restarts;
# reapplied here on every install since recreation resets the container fs).
PATCH_B64=$(base64 -w0 <<'PYEOF'
import pathlib
p = pathlib.Path("/usr/local/lib/python3.12/dist-packages/prometheus_fastapi_instrumentator/routing.py")
if p.exists():
    s = p.read_text()
    s2 = s.replace("route_name = route.path", 'route_name = getattr(route, "path", None)')
    s2 = s2.replace("route_name += child_route_name", 'route_name = (route_name or "") + child_route_name')
    if s2 != s:
        p.write_text(s2)
        print("instrumentator patched")
PYEOF
)
for _ in $(seq 1 30); do
  if docker exec "$NAME" python3 -c "import base64; exec(base64.b64decode('$PATCH_B64').decode())" 2>/dev/null; then
    docker restart "$NAME" >/dev/null   # reload so the APIServer imports the patched module
    break
  fi
  sleep 5
done

cat > "$ROOT/install-manifest$SUFFIX.txt" <<EOF
installed=$(date -Is)
instance=$INSTANCE
container=$NAME (docker, --restart unless-stopped)
image=$VLLM_IMAGE
model=$MODEL
gpu_frac=$GPU_FRAC
endpoint=http://$(hostname -I 2>/dev/null | awk '{print $1}'):$PORT/v1
files=$ROOT (config$SUFFIX.env, shared hf-cache/, logs/, install.sh, uninstall.sh, status.sh)
uninstall=INSTANCE=$INSTANCE $ROOT/uninstall.sh
EOF

echo "== container started; first run downloads the model into $ROOT/hf-cache =="
echo "   follow:  docker logs -f $NAME"
echo "   health:  curl -s http://localhost:$PORT/v1/models"
for i in $(seq 1 240); do
  if curl -sf "http://localhost:$PORT/v1/models" >/dev/null 2>&1; then
    echo "READY: http://localhost:$PORT/v1  ($MODEL, instance $INSTANCE)"; exit 0
  fi
  if [ "$(docker inspect -f '{{.State.Running}}' "$NAME" 2>/dev/null)" != "true" ]; then
    echo "FAILED: container exited - last log lines:"; docker logs --tail 40 "$NAME" || true; exit 1
  fi
  sleep 15
done
echo "TIMEOUT: not ready after 60 min (download may still be running) - check: docker logs -f $NAME"
exit 2
