#!/usr/bin/env bash
# List visual-qa serving instances on this host: name, model, port, container state, health.
set -uo pipefail
ROOT="$HOME/.solaris/visual-qa"

found=0
for cfg in "$ROOT"/config-*.env; do
  [ -f "$cfg" ] || continue
  found=1
  # shellcheck disable=SC1090
  INSTANCE=$(grep '^INSTANCE=' "$cfg" | cut -d= -f2)
  MODEL=$(grep '^MODEL=' "$cfg" | cut -d= -f2)
  PORT=$(grep '^PORT=' "$cfg" | cut -d= -f2)
  GPU_FRAC=$(grep '^GPU_FRAC=' "$cfg" | cut -d= -f2)
  NAME="solaris-visual-qa-$INSTANCE"
  STATE=$(docker inspect -f '{{.State.Status}}' "$NAME" 2>/dev/null || echo "absent")
  if curl -sf -m 4 "http://localhost:${PORT}/v1/models" >/dev/null 2>&1; then H="healthy"; else H="no-response"; fi
  echo "instance=$INSTANCE model=$MODEL port=$PORT gpu_frac=${GPU_FRAC:--} container=$STATE health=$H"
done
[ "$found" = 1 ] || echo "no instances configured under $ROOT"
