#!/usr/bin/env bash
# Stop visual-qa serving instance(s): models unload, GPU memory is freed, but EVERYTHING
# stays on disk (configs, shared weights cache, logs, the containers themselves).
# Restart later with start.sh — no re-download, just the minutes-long model load.
#   ./stop.sh                 # stop ALL running instances
#   INSTANCE=cosmos-reason2-8b ./stop.sh    # stop one (name = model slug)
# (A stopped container stays stopped across host reboots despite --restart unless-stopped;
#  full removal is uninstall.sh's job, not this script's.)
set -euo pipefail

stop_one() {
  local name="$1"
  if docker ps --format '{{.Names}}' | grep -qx "$name"; then
    docker stop "$name" >/dev/null && echo "stopped $name (GPU memory freed; files kept)"
  else
    echo "$name not running"
  fi
}

if [ -n "${INSTANCE:-}" ]; then
  stop_one "solaris-visual-qa-$INSTANCE"
else
  found=0
  for c in $(docker ps --format '{{.Names}}' | grep -E '^solaris-visual-qa(-|$)' || true); do
    found=1
    stop_one "$c"
  done
  [ "$found" = 1 ] || echo "no visual-qa instances running"
fi
