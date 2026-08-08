#!/usr/bin/env bash
set -Eeuo pipefail
readonly SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
# shellcheck source=common.sh
source "$SCRIPT_DIR/common.sh"

cleanup() {
  [[ -z "${temporary:-}" ]] || rm -f -- "$temporary"
}

install_artifact() {
  local target="$1" url="$2" digest="$3" size="$4" mode="$5"
  local temporary=''
  if [[ -e "$target" || -L "$target" ]]; then
    [[ ! -L "$target" && -f "$target" ]] || control_plane_die artifact_state_conflict 65
    [[ "$(stat -c '%u:%g:%a:%s:%h' -- "$target")" == "0:0:$mode:$size:1" ]] ||
      control_plane_die artifact_metadata_conflict 65
    [[ "$(sha256sum -- "$target" | awk '{digest=$1} END {print digest}')" == "$digest" ]] ||
      control_plane_die artifact_hash_mismatch 65
    return 0
  fi
  temporary="$(mktemp "$(dirname -- "$target")/.download.XXXXXXXX")"
  trap cleanup EXIT
  chmod 0600 -- "$temporary"
  curl --config /dev/null --fail --location --proto '=https' --tlsv1.2 \
    --silent --show-error --output "$temporary" -- "$url"
  [[ "$(stat -c %s -- "$temporary")" == "$size" ]] ||
    control_plane_die artifact_size_mismatch 65
  [[ "$(sha256sum -- "$temporary" | awk '{digest=$1} END {print digest}')" == "$digest" ]] ||
    control_plane_die artifact_hash_mismatch 65
  publish_exact_file "$temporary" "$target" root root "$mode"
  rm -f -- "$temporary"
  temporary=''
  trap - EXIT
}

main() {
  require_root
  require_lifecycle_platform
  require_command curl
  require_command sha256sum
  require_exact_directory "$KITDEV_RUNTIME_ROOT" root root 755

  ensure_directory "$KITDEV_RUNTIME_ROOT/firecrackers/v1.14.1_431f1fc" root root 755
  ensure_directory "$KITDEV_RUNTIME_ROOT/firecrackers/v1.14.1_431f1fc/amd64" root root 755
  ensure_directory "$KITDEV_RUNTIME_ROOT/kernels/vmlinux-6.1.158" root root 755
  ensure_directory "$KITDEV_RUNTIME_ROOT/kernels/vmlinux-6.1.158/amd64" root root 755
  ensure_directory "$KITDEV_RUNTIME_ROOT/busybox/1.36.1" root root 755
  ensure_directory "$KITDEV_RUNTIME_ROOT/busybox/1.36.1/amd64" root root 755

  install_artifact \
    "$KITDEV_RUNTIME_ROOT/firecrackers/v1.14.1_431f1fc/amd64/firecracker" \
    https://storage.googleapis.com/e2b-artifact-binaries/firecrackers/v1.14.1_431f1fc/amd64/firecracker \
    d81fd733be7e027406b4d5241442c447a2b5878b06dfa63dc236e68f3536d689 \
    3566832 755
  install_artifact \
    "$KITDEV_RUNTIME_ROOT/kernels/vmlinux-6.1.158/amd64/vmlinux.bin" \
    https://storage.googleapis.com/e2b-artifact-binaries/kernels/vmlinux-6.1.158/amd64/vmlinux.bin \
    1982f8d5f1bc1680a36b0cdf126f605834b1633bba200d3281bccd53b86ff9ee \
    43638104 644
  install_artifact \
    "$KITDEV_RUNTIME_ROOT/busybox/1.36.1/amd64/busybox" \
    https://storage.googleapis.com/e2b-artifact-binaries/busybox/1.36.1/amd64/busybox \
    d7cce939adb09a41a22a5f846d22ba8d576b38dbb2b46a5c77a3a3e27ec52520 \
    1210176 755

  printf 'status=pass operation=install-runtime-artifacts envd=source-build-required\n'
}

main "$@"
