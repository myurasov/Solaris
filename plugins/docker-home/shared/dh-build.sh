#!/usr/bin/env bash
# rev. 1
# docker-home: build this project's container image, then start the container (unless --no-start).
# Runs only on explicit request. Per-host options are asked once and stored in dh.conf.

. "$(dirname "${BASH_SOURCE[0]}")/dh-common.sh"

usage() {
  cat <<'EOF'
usage: dh-build.sh [--gpus all|none] [--network host|bridge] [--shm-size SIZE] [--extra "DOCKER RUN ARGS"]
                   [--base-image IMAGE] [--non-interactive] [--pull] [--no-start]
Builds this project's docker-home image with your uid/gid baked in. On the first build the per-host
options are asked (or taken from the flags / detected defaults with --non-interactive) and stored in
~/.solaris/docker-home/<slug>/dh.conf; later builds reuse that file unless flags override it.
EOF
}

dh_refuse_inside
dh_init
dh_need_docker

interactive=1; pull=""; start=1
have_gpus=0; have_network=0; have_shm=0; have_extra=0; have_base=0
opt_gpus=""; opt_network=""; opt_shm=""; opt_extra=""; opt_base=""
while [ $# -gt 0 ]; do
  case "$1" in
    --gpus)        [ $# -ge 2 ] || dh_die "--gpus needs a value"; opt_gpus="$2"; have_gpus=1; shift 2 ;;
    --network)     [ $# -ge 2 ] || dh_die "--network needs a value"; opt_network="$2"; have_network=1; shift 2 ;;
    --shm-size)    [ $# -ge 2 ] || dh_die "--shm-size needs a value"; opt_shm="$2"; have_shm=1; shift 2 ;;
    --extra)       [ $# -ge 2 ] || dh_die "--extra needs a value"; opt_extra="$2"; have_extra=1; shift 2 ;;
    --base-image)  [ $# -ge 2 ] || dh_die "--base-image needs a value"; opt_base="$2"; have_base=1; shift 2 ;;
    --non-interactive) interactive=0; shift ;;
    --pull)        pull="--pull"; shift ;;
    --no-start)    start=0; shift ;;
    -h|--help)     usage; exit 0 ;;
    *)             usage >&2; dh_die "unknown option: $1" ;;
  esac
done

detect_gpus() {
  if command -v nvidia-smi >/dev/null 2>&1 || docker info --format '{{json .Runtimes}}' 2>/dev/null | grep -q nvidia; then
    echo all
  else
    echo none
  fi
}
detect_network() { case "$(uname -s)" in Linux) echo host ;; *) echo bridge ;; esac; }

# ask VAR "prompt" default  (only when interactive and stdin is a terminal; else the default)
ask() {
  local var="$1" prompt="$2" def="$3" ans=""
  if [ "$interactive" = 1 ] && [ -t 0 ]; then
    printf '%s [%s]: ' "$prompt" "$def"
    read -r ans || true
  fi
  [ -n "$ans" ] || ans="$def"
  printf -v "$var" '%s' "$ans"
}

first_time=1; [ -f "$DH_CONF" ] && first_time=0
[ "$first_time" = 0 ] && dh_info "options from $DH_CONF (flags override; delete the file to be asked again)"

if [ "$have_gpus" = 1 ]; then DH_GPUS="$opt_gpus"; elif [ "$first_time" = 1 ]; then ask DH_GPUS "GPUs to pass through (all / none)" "$(detect_gpus)"; fi
[ "$DH_GPUS" = none ] && DH_GPUS=""
if [ "$have_network" = 1 ]; then DH_NETWORK="$opt_network"; elif [ "$first_time" = 1 ]; then ask DH_NETWORK "Container network (host / bridge)" "$(detect_network)"; fi
case "$DH_NETWORK" in host|bridge) ;; *) dh_die "--network must be host or bridge (got '$DH_NETWORK')" ;; esac
if [ "$have_shm" = 1 ]; then DH_SHM_SIZE="$opt_shm"; elif [ "$first_time" = 1 ]; then
  def_shm=""; [ -n "$DH_GPUS" ] && def_shm="8g"
  ask DH_SHM_SIZE "Shared memory size for the container (e.g. 8g; empty = docker default)" "$def_shm"
fi
[ "$DH_SHM_SIZE" = none ] && DH_SHM_SIZE=""
if [ "$have_extra" = 1 ]; then DH_EXTRA_ARGS="$opt_extra"; elif [ "$first_time" = 1 ]; then
  ask DH_EXTRA_ARGS "Extra docker run args (e.g. -v /data:/data; empty = none)" ""
fi
[ "$DH_EXTRA_ARGS" = none ] && DH_EXTRA_ARGS=""
if [ "$have_base" = 1 ]; then DH_BASE_IMAGE="$opt_base"; fi
case "$DH_EXTRA_ARGS" in *\"*) dh_die "--extra must not contain double quotes (dh.conf is a shell file)" ;; esac
dh_check_extra_args "$DH_EXTRA_ARGS"

dh_write_conf
dh_info "options: gpus=${DH_GPUS:-none} network=$DH_NETWORK shm=${DH_SHM_SIZE:-default} extra='${DH_EXTRA_ARGS}' base=$DH_BASE_IMAGE"
dh_info "building $DH_IMAGE (uid $(id -u), gid $(id -g)) from $DH_DIR/Dockerfile"
# shellcheck disable=SC2086
docker build $pull \
  --build-arg "DH_BASE_IMAGE=$DH_BASE_IMAGE" \
  --build-arg "DH_UID=$(id -u)" \
  --build-arg "DH_GID=$(id -g)" \
  -t "$DH_IMAGE" -f "$DH_DIR/Dockerfile" "$DH_DIR"
dh_info "image $DH_IMAGE built"

if [ "$start" = 1 ]; then
  exec bash "$DH_DIR/dh-start.sh"
fi
dh_info "not started (--no-start); start with: bash \"$DH_DIR/dh-start.sh\""
