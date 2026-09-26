#!/usr/bin/env bash
# rev. 1
# docker-home: stop this project's container (the home directory and the image are kept).
# Refuses while a tmux session is live inside (an agent may be working) unless --force.

. "$(dirname "${BASH_SOURCE[0]}")/dh-common.sh"
dh_refuse_inside
dh_init
dh_need_docker

force=0
case "${1:-}" in
  --force|-f) force=1 ;;
  '') ;;
  -h|--help) echo "usage: dh-stop.sh [--force]"; exit 0 ;;
  *) dh_die "unknown option: $1" ;;
esac

case "$(dh_container_state)" in
  running|paused|restarting)
    [ "$force" = 1 ] || dh_refuse_live_session "dh-stop.sh"
    docker stop "$DH_CONTAINER" >/dev/null
    dh_info "$DH_CONTAINER stopped (home kept at $DH_HOME; start again with dh-start.sh)" ;;
  absent)
    dh_info "$DH_CONTAINER does not exist (nothing to stop)" ;;
  *)
    dh_info "$DH_CONTAINER is not running" ;;
esac
