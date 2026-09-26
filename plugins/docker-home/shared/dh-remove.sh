#!/usr/bin/env bash
# rev. 1
# docker-home: remove this project's container, image and (unless --keep-home) the state dir with the
# container's home directory. Destructive: asks y/N; --yes skips the question (an agent passes it
# only after the user confirmed in chat). Refuses while a tmux session is live inside unless --force.

. "$(dirname "${BASH_SOURCE[0]}")/dh-common.sh"
dh_refuse_inside
dh_init
dh_need_docker

yes=0; keep_home=0; force=0
while [ $# -gt 0 ]; do
  case "$1" in
    --yes|-y)    yes=1; shift ;;
    --keep-home) keep_home=1; shift ;;
    --force|-f)  force=1; shift ;;
    -h|--help)   echo "usage: dh-remove.sh [--yes] [--keep-home] [--force]"; exit 0 ;;
    *)           dh_die "unknown option: $1" ;;
  esac
done

state="$(dh_container_state)"
echo "This removes the docker home of project '$SLUG':"
[ "$state" != absent ] && echo "  - container $DH_CONTAINER ($state)"
dh_image_exists && echo "  - image     $DH_IMAGE"
if [ "$keep_home" = 0 ] && [ -d "$DH_STATE" ]; then
  echo "  - state dir $DH_STATE  (home directory: harness logins, sessions, caches, venvs; dh.conf)"
fi
echo "The project folder itself is never touched."

if [ "$yes" = 0 ]; then
  [ -t 0 ] || dh_die "no terminal to confirm on; pass --yes (only after the user confirmed)"
  printf 'Proceed? [y/N] '
  read -r answer || true
  case "$answer" in y|Y|yes|YES) ;; *) dh_info "aborted, nothing removed"; exit 0 ;; esac
fi
[ "$force" = 1 ] || dh_refuse_live_session "dh-remove.sh"

# Each step is a plain statement: under set -e a docker failure stops the script before rm -rf.
if [ "$state" != absent ]; then
  docker rm -f "$DH_CONTAINER" >/dev/null
  dh_info "container removed"
fi
if dh_image_exists; then
  docker rmi "$DH_IMAGE" >/dev/null
  dh_info "image removed"
fi
if [ "$keep_home" = 0 ] && [ -d "$DH_STATE" ]; then
  rm -rf "$DH_STATE"
  dh_info "state dir removed"
fi
dh_info "done"
