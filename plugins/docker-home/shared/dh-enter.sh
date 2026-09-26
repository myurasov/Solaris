#!/usr/bin/env bash
# rev. 1
# docker-home: enter this project's container. No arguments: attach to (or create) the tmux session,
# so a long-lived agent run survives you detaching (Ctrl-b d) or losing the ssh connection.
# With arguments: run that command inside instead (e.g. dh-enter.sh claude --version).

. "$(dirname "${BASH_SOURCE[0]}")/dh-common.sh"
dh_refuse_inside
dh_init
dh_need_docker

state="$(dh_container_state)"
[ "$state" = absent ] && dh_die "no container $DH_CONTAINER yet; run dh-build.sh first"
if [ "$state" != running ]; then
  bash "$DH_DIR/dh-start.sh" >/dev/null
fi

tty_flags="-i"; [ -t 0 ] && [ -t 1 ] && tty_flags="-it"
if [ $# -gt 0 ]; then
  exec docker exec $tty_flags -w "$DH_MOUNT_PATH" "$DH_CONTAINER" "$@"
fi
[ "$tty_flags" = "-it" ] || dh_die "no terminal attached; pass a command to run instead of the tmux session"
exec docker exec -it -w "$DH_MOUNT_PATH" "$DH_CONTAINER" tmux new-session -A -s "$DH_TMUX_SESSION"
