#!/usr/bin/env bash
# rev. 1
# docker-home: show what exists for this project (image, container, state dir, options). Read-only;
# the one verb that also works from inside the container.

. "$(dirname "${BASH_SOURCE[0]}")/dh-common.sh"

if dh_inside_container; then
  echo "inside docker home: yes"
  echo "project mount:      ${DH_PROJECT:-unknown} (slug ${DH_SLUG:-unknown})"
  echo "host state:         not visible from inside; run dh-status.sh on the host"
  exit 0
fi

dh_init
echo "project:    $PROJECT_ROOT (slug $SLUG)"
if [ -d "$DH_STATE" ]; then
  echo "state dir:  $DH_STATE (home $(du -sh "$DH_HOME" 2>/dev/null | cut -f1 || echo '?'))"
else
  echo "state dir:  $DH_STATE (not created yet)"
fi
if [ -f "$DH_CONF" ]; then
  echo "options:    gpus=${DH_GPUS:-none} network=$DH_NETWORK shm=${DH_SHM_SIZE:-default} extra='${DH_EXTRA_ARGS}' base=$DH_BASE_IMAGE"
else
  echo "options:    none yet (dh-build.sh asks for them)"
fi
if ! command -v docker >/dev/null 2>&1; then
  echo "docker:     not installed"; exit 0
fi
if ! docker info >/dev/null 2>&1; then
  echo "docker:     daemon not reachable"; exit 0
fi
if dh_image_exists; then
  echo "image:      $DH_IMAGE (built $(docker image inspect -f '{{.Created}}' "$DH_IMAGE" | cut -c1-19))"
else
  echo "image:      $DH_IMAGE (not built)"
fi
state="$(dh_container_state)"
echo "container:  $DH_CONTAINER ($state)"
[ "$state" = running ] && dh_print_enter_hint
exit 0
