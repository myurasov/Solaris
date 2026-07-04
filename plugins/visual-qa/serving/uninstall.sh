#!/usr/bin/env bash
# Uninstall visual-qa serving instance(s) installed by install.sh (run ON the host).
#   INSTANCE=fast ./uninstall.sh          # remove one instance (container + its config)
#   ./uninstall.sh --all                  # remove every instance
#   ./uninstall.sh --all --purge          # ... and delete ~/.solaris/visual-qa entirely
#                                         #     (shared weights cache included)
set -euo pipefail

ROOT="$HOME/.solaris/visual-qa"
ALL=0; PURGE=0
for a in "$@"; do
  case "$a" in
    --all) ALL=1 ;;
    --purge) PURGE=1 ;;
  esac
done

remove_instance() {
  local name="$1" cfg="$2"
  if docker ps -a --format '{{.Names}}' 2>/dev/null | grep -qx "$name"; then
    docker rm -f "$name" >/dev/null
    echo "removed container $name"
  else
    echo "container $name not present"
  fi
  rm -f "$cfg" "${cfg%.env}"-manifest.txt 2>/dev/null || true
}

if [ "$ALL" = 1 ]; then
  for c in $(docker ps -a --format '{{.Names}}' 2>/dev/null | grep -E '^solaris-visual-qa(-|$)' || true); do
    docker rm -f "$c" >/dev/null && echo "removed container $c"
  done
  rm -f "$ROOT"/config*.env "$ROOT"/install-manifest*.txt
  if [ "$PURGE" = 1 ]; then
    rm -rf "$ROOT"
    echo "purged $ROOT (configs + shared model cache)"
  else
    echo "kept $ROOT (shared model cache) - rerun with --all --purge to delete"
  fi
else
  [ -n "${INSTANCE:-}" ] || { echo "set INSTANCE=<name> or use --all"; exit 1; }
  remove_instance "solaris-visual-qa-$INSTANCE" "$ROOT/config-$INSTANCE.env"
  rm -f "$ROOT/install-manifest-$INSTANCE.txt"
  echo "kept shared cache + other instances (use --all [--purge] for everything)"
fi
