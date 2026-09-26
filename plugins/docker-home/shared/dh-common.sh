#!/usr/bin/env bash
# rev. 1
# docker-home: shared helpers, sourced by every dh-*.sh (never run directly).
# Everything is derived from where the overlay lives (<project>/ai/plugins/docker-home/), so the
# scripts work in a detached ai-pack and never reference a Solaris checkout.

set -euo pipefail

dh_info() { printf '[dh] %s\n' "$*"; }
dh_warn() { printf '[dh] warning: %s\n' "$*" >&2; }
dh_die()  { printf '[dh] error: %s\n' "$*" >&2; exit 1; }

DH_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"

# Inside the container the project is the whole world: management verbs make no sense there.
dh_inside_container() { [ -f /.dockerenv ] || [ -n "${DH_INSIDE:-}" ]; }
dh_refuse_inside() {
  if dh_inside_container; then
    dh_die "you are inside the docker home already; run this on the host (detach or exit the container first)"
  fi
}

# Project root = the ai-pack root (the folder holding ai/manifest.json). Resolution order:
# $DH_PROJECT_ROOT, the overlay's own location (copy install: <project>/ai/plugins/docker-home/),
# then a walk up from the current directory (link install, or when run from the plugin source).
dh_resolve_project() {
  local cand
  if [ -n "${DH_PROJECT_ROOT:-}" ]; then
    cand="$(cd "$DH_PROJECT_ROOT" 2>/dev/null && pwd -P)" || dh_die "DH_PROJECT_ROOT does not exist: $DH_PROJECT_ROOT"
    [ -f "$cand/ai/manifest.json" ] || dh_die "no ai/manifest.json under DH_PROJECT_ROOT=$cand"
    PROJECT_ROOT="$cand"; return
  fi
  cand="$(cd "$DH_DIR/../../.." 2>/dev/null && pwd -P || true)"
  if [ -n "$cand" ] && [ -f "$cand/ai/manifest.json" ]; then PROJECT_ROOT="$cand"; return; fi
  cand="$(pwd -P)"
  while [ "$cand" != "/" ]; do
    if [ -f "$cand/ai/manifest.json" ]; then PROJECT_ROOT="$cand"; return; fi
    cand="$(dirname "$cand")"
  done
  dh_die "cannot find the project root (a folder holding ai/manifest.json); run from inside the project or set DH_PROJECT_ROOT"
}

# Slug from ai/manifest.json (project.slug), else the folder name. The manifest is project content
# the container can edit, so the value is normalized to a safe docker name (lowercase [a-z0-9_-],
# no leading or trailing separators) and validated, never trusted.
dh_read_slug() {
  local slug=""
  if command -v python3 >/dev/null 2>&1; then
    slug="$(python3 - "$PROJECT_ROOT/ai/manifest.json" <<'PY' 2>/dev/null || true
import json, sys
m = json.load(open(sys.argv[1]))
print((m.get("project") or {}).get("slug") or "")
PY
)"
  fi
  [ -n "$slug" ] || slug="$(basename "$PROJECT_ROOT")"
  slug="$(printf '%s' "$slug" | tr 'A-Z' 'a-z' | sed -e 's/[^a-z0-9_-]/-/g' -e 's/^[-_]*//' -e 's/[-_]*$//')"
  [ -n "$slug" ] || dh_die "could not derive a usable project slug from $PROJECT_ROOT/ai/manifest.json"
  SLUG="$slug"
}

# docker run arguments that would breach the boundary, or execute code when dh.conf is sourced.
dh_check_extra_args() {
  case " $1 " in
    *privileged*|*docker.sock*|*--pid*|*--userns*|*--cap-add*|*--security-opt*|*--device*|*--user*|*' -u '*|*'$'*|*'`'*)
      dh_die "refusing extra docker args that would breach the boundary or execute code: $1" ;;
  esac
}

# Names + defaults; dh.conf (written by dh-build.sh) overrides the defaults per host.
dh_init() {
  dh_resolve_project
  dh_read_slug
  DH_STATE_ROOT="${DH_STATE_ROOT:-$HOME/.solaris/docker-home}"
  DH_STATE="$DH_STATE_ROOT/$SLUG"
  DH_HOME="$DH_STATE/home"
  DH_CONF="$DH_STATE/dh.conf"
  DH_IMAGE="solaris-dh-$SLUG"
  DH_CONTAINER="dh-$SLUG"
  DH_BASE_IMAGE="ubuntu:24.04"
  DH_GPUS=""
  DH_NETWORK="bridge"
  DH_SHM_SIZE=""
  DH_EXTRA_ARGS=""
  DH_MOUNT_SSH="1"
  DH_MOUNT_GITCONFIG="1"
  DH_TMUX_SESSION="agent"
  DH_OWNER_ROOT=""
  if [ -f "$DH_CONF" ]; then
    # shellcheck disable=SC1090
    . "$DH_CONF"
  fi
  DH_MOUNT_PATH="/home/dev/$SLUG"
  # State dir and container are keyed by slug only: never touch ones that belong to another project
  # folder (a slug collision between groups, a spoofed manifest, or a moved project).
  if [ -n "$DH_OWNER_ROOT" ] && [ "$DH_OWNER_ROOT" != "$PROJECT_ROOT" ]; then
    dh_die "state dir $DH_STATE belongs to project $DH_OWNER_ROOT (slug collision or moved project): use a unique slug, or move that state dir away / fix DH_OWNER_ROOT in $DH_CONF"
  fi
  if command -v docker >/dev/null 2>&1; then
    local owner
    owner="$(docker container inspect -f '{{index .Config.Labels "dh.project"}}' "$DH_CONTAINER" 2>/dev/null | tr -d '[:space:]' || true)"
    if [ -n "$owner" ] && [ "$owner" != "$PROJECT_ROOT" ]; then
      dh_die "container $DH_CONTAINER belongs to project $owner (slug collision or moved project): use a unique slug, or remove that container first (docker rm -f $DH_CONTAINER)"
    fi
  fi
}

dh_need_docker() {
  command -v docker >/dev/null 2>&1 || dh_die "docker is not installed (or not on PATH)"
  docker info >/dev/null 2>&1 || dh_die "the docker daemon is not reachable (is Docker running? on Linux, is your user in the docker group?)"
  if docker info -f '{{.SecurityOptions}}' 2>/dev/null | grep -q rootless; then
    dh_die "rootless docker is not supported: files the agent writes into the project would not be owned by you"
  fi
}

# Prints: running | exited | created | paused | restarting | dead | absent
dh_container_state() {
  local s
  # a missing container yields an error plus an empty line on some docker versions: normalize
  s="$(docker container inspect -f '{{.State.Status}}' "$DH_CONTAINER" 2>/dev/null | tr -d '[:space:]' || true)"
  [ -n "$s" ] && echo "$s" || echo absent
}
dh_image_exists() { docker image inspect "$DH_IMAGE" >/dev/null 2>&1; }

# A tmux session inside (attached or detached) may be running an agent: stop, rebuild and remove
# refuse to kill it unless the verb is forced. $1 names the verb for the message.
dh_refuse_live_session() {
  [ "$(dh_container_state)" = running ] || return 0
  if docker exec "$DH_CONTAINER" tmux has-session -t "$DH_TMUX_SESSION" 2>/dev/null; then
    dh_die "tmux session '$DH_TMUX_SESSION' is live inside $DH_CONTAINER (an agent may be working): end it first, or pass --force to $1 anyway"
  fi
}

# Word-split DH_EXTRA_ARGS without globbing into DH_EXTRA (bash 3.2: test DH_EXTRA_COUNT before use).
dh_split_extra() {
  set -f
  # shellcheck disable=SC2086
  set -- $DH_EXTRA_ARGS
  set +f
  DH_EXTRA_COUNT=$#
  DH_EXTRA=("$@")
}

dh_write_conf() {
  mkdir -p "$DH_STATE"
  cat > "$DH_CONF" <<EOF
# docker-home per-host options for project '$SLUG' (sourced by the dh-*.sh scripts).
# Edit by hand and run dh-rebuild.sh, or delete this file to be asked again on the next dh-build.sh.
# Values are shell strings: keep them free of double quotes. DH_OWNER_ROOT ties this state dir to
# one project folder; DH_MOUNT_SSH is 1 (copy ssh config + known_hosts in, forward the agent socket),
# keys (mount the whole ~/.ssh read-only instead) or 0 (nothing).
DH_OWNER_ROOT="$PROJECT_ROOT"
DH_IMAGE="$DH_IMAGE"
DH_CONTAINER="$DH_CONTAINER"
DH_BASE_IMAGE="$DH_BASE_IMAGE"
DH_GPUS="$DH_GPUS"
DH_NETWORK="$DH_NETWORK"
DH_SHM_SIZE="$DH_SHM_SIZE"
DH_EXTRA_ARGS="$DH_EXTRA_ARGS"
DH_MOUNT_SSH="$DH_MOUNT_SSH"
DH_MOUNT_GITCONFIG="$DH_MOUNT_GITCONFIG"
DH_TMUX_SESSION="$DH_TMUX_SESSION"
EOF
}

dh_print_enter_hint() {
  dh_info "enter with: bash \"$DH_DIR/dh-enter.sh\"  (tmux session '$DH_TMUX_SESSION'; detach with Ctrl-b d, the session keeps running)"
}
