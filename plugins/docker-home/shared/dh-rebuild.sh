#!/usr/bin/env bash
# rev. 1
# docker-home: rebuild the image (pulling a fresh base, so the harnesses update too), replace the
# container, keep the home directory. Use after editing the Dockerfile or dh.conf. Refuses while a
# tmux session is live inside unless --force; other flags pass through to dh-build.sh.

. "$(dirname "${BASH_SOURCE[0]}")/dh-common.sh"
dh_refuse_inside
dh_init
dh_need_docker

[ -f "$DH_CONF" ] || dh_die "no $DH_CONF yet; run dh-build.sh first"

force=0; pass=()
for a in "$@"; do
  case "$a" in --force|-f) force=1 ;; *) pass+=( "$a" ) ;; esac
done
[ "$force" = 1 ] || dh_refuse_live_session "dh-rebuild.sh"

if [ ${#pass[@]} -gt 0 ]; then
  bash "$DH_DIR/dh-build.sh" --pull --no-start "${pass[@]}"
else
  bash "$DH_DIR/dh-build.sh" --pull --no-start
fi
case "$(dh_container_state)" in
  absent) ;;
  *) docker rm -f "$DH_CONTAINER" >/dev/null; dh_info "old container removed (home kept at $DH_HOME)" ;;
esac
exec bash "$DH_DIR/dh-start.sh"
