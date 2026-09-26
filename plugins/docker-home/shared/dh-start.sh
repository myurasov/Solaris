#!/usr/bin/env bash
# rev. 1
# docker-home: start this project's container (create it on first start, seeding the home directory).
# Mounts: the project folder (rw), the container's home (rw), this overlay re-mounted read-only on
# top of the project so the container can never rewrite the scripts the host executes, the optional
# read-only git identity, and the ssh agent socket. Nothing else.

. "$(dirname "${BASH_SOURCE[0]}")/dh-common.sh"
dh_refuse_inside
dh_init
dh_need_docker

[ -f "$DH_CONF" ] || dh_die "no $DH_CONF yet; run dh-build.sh first"
dh_check_extra_args "$DH_EXTRA_ARGS"

state="$(dh_container_state)"
case "$state" in
  running)
    dh_info "$DH_CONTAINER is already running"; dh_print_enter_hint; exit 0 ;;
  exited|created|paused|dead)
    if dh_image_exists; then
      cur="$(docker image inspect -f '{{.Id}}' "$DH_IMAGE" 2>/dev/null || true)"
      old="$(docker container inspect -f '{{.Image}}' "$DH_CONTAINER" 2>/dev/null || true)"
      [ "$cur" = "$old" ] || dh_warn "image $DH_IMAGE changed since this container was created; run dh-rebuild.sh to apply it"
    fi
    docker start "$DH_CONTAINER" >/dev/null
    dh_info "$DH_CONTAINER started"; dh_print_enter_hint; exit 0 ;;
  restarting)
    dh_info "$DH_CONTAINER is restarting; try again in a moment"; exit 0 ;;
esac
dh_image_exists || dh_die "image $DH_IMAGE not found; run dh-build.sh first"

# First start: seed the home directory (existing files are never overwritten). The project mountpoint
# inside the home is pre-created as the user so docker does not create it root-owned (Linux).
mkdir -p "$DH_HOME/.claude" "$DH_HOME/.local/bin" "$DH_HOME/.ssh" "$DH_HOME/$SLUG"
chmod 700 "$DH_HOME/.ssh"
if [ ! -f "$DH_HOME/.claude/settings.json" ]; then
  # Solaris memory doctrine: the pack's ai/.memory/ is the only memory; no harness auto memory.
  printf '{\n  "autoMemoryEnabled": false\n}\n' > "$DH_HOME/.claude/settings.json"
fi
if [ ! -f "$DH_HOME/.bashrc" ]; then
  sed "s|__SLUG__|$SLUG|g" > "$DH_HOME/.bashrc" <<'EOF'
# seeded by docker-home; edit freely (this file lives in the host state dir, not in the image)
export PATH="$HOME/.local/bin:$PATH"
case $- in *i*) export PS1='[dh:__SLUG__] \w\$ ' ;; esac
EOF
fi
if [ ! -f "$DH_HOME/.profile" ]; then
  printf '# seeded by docker-home: login shells (tmux) read .bashrc too\n[ -f "$HOME/.bashrc" ] && . "$HOME/.bashrc"\n' > "$DH_HOME/.profile"
fi
# ssh: client config and known hosts are copied in (writable; private keys stay on the host, the
# forwarded agent signs). DH_MOUNT_SSH=keys mounts the whole ~/.ssh read-only instead; 0 does nothing.
if [ "$DH_MOUNT_SSH" = 1 ]; then
  for f in config known_hosts; do
    if [ -f "$HOME/.ssh/$f" ] && [ ! -e "$DH_HOME/.ssh/$f" ]; then cp "$HOME/.ssh/$f" "$DH_HOME/.ssh/$f"; fi
  done
fi

args=( run -d --init --name "$DH_CONTAINER" --hostname "$DH_CONTAINER"
       --label "dh.project=$PROJECT_ROOT"
       -e "DH_SLUG=$SLUG" -e "DH_PROJECT=$DH_MOUNT_PATH"
       -v "$PROJECT_ROOT:$DH_MOUNT_PATH" -v "$DH_HOME:/home/dev" -w "$DH_MOUNT_PATH" )
# In a copy install this overlay (scripts, Dockerfile, rule, skill) sits inside the project: re-mount
# it read-only on top so the container cannot rewrite what the host later runs.
case "$DH_DIR" in
  "$PROJECT_ROOT"/*) args+=( -v "$DH_DIR:$DH_MOUNT_PATH${DH_DIR#"$PROJECT_ROOT"}:ro" ) ;;
esac
[ -n "$DH_GPUS" ]     && args+=( --gpus "$DH_GPUS" )
[ -n "$DH_NETWORK" ]  && args+=( --network "$DH_NETWORK" )
# host networking shares the host's interfaces: no raw sockets (sniffing) for the container
[ "$DH_NETWORK" = host ] && args+=( --cap-drop NET_RAW )
[ -n "$DH_SHM_SIZE" ] && args+=( --shm-size "$DH_SHM_SIZE" )
if [ "$DH_MOUNT_SSH" = keys ] && [ -d "$HOME/.ssh" ]; then args+=( -v "$HOME/.ssh:/home/dev/.ssh:ro" ); fi
if [ "$DH_MOUNT_GITCONFIG" = 1 ] && [ -f "$HOME/.gitconfig" ]; then args+=( -v "$HOME/.gitconfig:/home/dev/.gitconfig:ro" ); fi
if [ "$DH_MOUNT_SSH" != 0 ]; then
  # Docker Desktop exposes the host agent at a fixed path; Linux passes the current socket through
  # (a new login gets a new socket: recreate the container if forwarding stops working).
  if [ "$(uname -s)" = Darwin ]; then
    args+=( -v /run/host-services/ssh-auth.sock:/run/host-services/ssh-auth.sock -e SSH_AUTH_SOCK=/run/host-services/ssh-auth.sock )
  elif [ -n "${SSH_AUTH_SOCK:-}" ] && [ -S "$SSH_AUTH_SOCK" ]; then
    args+=( -v "$SSH_AUTH_SOCK:/ssh-agent" -e SSH_AUTH_SOCK=/ssh-agent )
  fi
fi
dh_split_extra
if [ "$DH_EXTRA_COUNT" -gt 0 ]; then args+=( "${DH_EXTRA[@]}" ); fi
args+=( "$DH_IMAGE" sleep infinity )

docker "${args[@]}" >/dev/null
dh_info "$DH_CONTAINER created and running"
dh_info "  project  $PROJECT_ROOT  ->  $DH_MOUNT_PATH"
dh_info "  home     $DH_HOME  ->  /home/dev"
dh_info "  options  gpus=${DH_GPUS:-none} network=$DH_NETWORK shm=${DH_SHM_SIZE:-default} extra='${DH_EXTRA_ARGS}' ssh=$DH_MOUNT_SSH"
dh_print_enter_hint
