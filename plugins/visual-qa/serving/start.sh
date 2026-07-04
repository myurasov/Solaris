#!/usr/bin/env bash
# Start installed visual-qa serving instance(s) — containers created by install.sh.
# Counterpart of stop.sh (which stops + frees GPU memory but keeps everything on disk).
#   ./start.sh                 # start ALL configured instances
#   INSTANCE=cosmos-reason2-8b ./start.sh   # start one (name = model slug)
# Model load takes minutes after start; check readiness with status.sh.
set -euo pipefail
ROOT="$HOME/.solaris/visual-qa"

start_one() {
  local inst="$1" cfg name port state
  cfg="$ROOT/config-$inst.env"; name="solaris-visual-qa-$inst"
  [ -f "$cfg" ] || { echo "$inst: no config ($cfg) - run install.sh"; return 1; }
  port=$(grep '^PORT=' "$cfg" | cut -d= -f2)
  state=$(docker inspect -f '{{.State.Status}}' "$name" 2>/dev/null || echo absent)
  case "$state" in
    running) echo "$inst: already running (:$port)" ;;
    absent)  echo "$inst: container missing - re-run install.sh (INSTANCE=$inst; weights cache is kept, so it is fast)"; return 1 ;;
    *)       docker start "$name" >/dev/null
             echo "$inst: starting (:$port) - model load takes minutes; watch: docker logs -f $name" ;;
  esac
}

if [ -n "${INSTANCE:-}" ]; then
  start_one "$INSTANCE"
else
  any=0
  for cfg in "$ROOT"/config-*.env; do
    [ -f "$cfg" ] || continue
    any=1
    start_one "$(grep '^INSTANCE=' "$cfg" | cut -d= -f2)" || true
  done
  [ "$any" = 1 ] || echo "no instances configured under $ROOT"
fi
