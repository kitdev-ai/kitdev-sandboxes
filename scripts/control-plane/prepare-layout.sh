#!/usr/bin/env bash
set -Eeuo pipefail
readonly SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
# shellcheck source=common.sh
source "$SCRIPT_DIR/common.sh"

main() {
  require_root
  require_lifecycle_platform
  require_worker_identity
  getent group kitdev >/dev/null || control_plane_die kitdev_group_required 65

  ensure_directory "$KITDEV_ETC_ROOT" root root 700
  ensure_directory "$KITDEV_OPT_ROOT" root root 755
  ensure_directory "$KITDEV_OPT_ROOT/src" root root 755
  ensure_directory "$KITDEV_STATE_ROOT" root root 755
  ensure_directory "$KITDEV_DATA_ROOT" root root 750

  ensure_directory "$KITDEV_DATA_ROOT/postgres" 999 0 700
  ensure_directory "$KITDEV_DATA_ROOT/redis" 999 0 750
  ensure_directory "$KITDEV_DATA_ROOT/clickhouse" 101 101 750
  ensure_directory "$KITDEV_DATA_ROOT/loki" 10001 10001 750
  ensure_directory "$KITDEV_DATA_ROOT/build-cache" root root 700
  ensure_directory "$KITDEV_DATA_ROOT/artifacts" root root 755
  ensure_directory "$KITDEV_DATA_ROOT/artifacts/bin" root kitdev 750

  ensure_directory "$KITDEV_RUNTIME_ROOT" root root 755
  ensure_directory "$KITDEV_RUNTIME_ROOT/firecrackers" root root 755
  ensure_directory "$KITDEV_RUNTIME_ROOT/kernels" root root 755
  ensure_directory "$KITDEV_RUNTIME_ROOT/busybox" root root 755
  ensure_directory "$KITDEV_RUNTIME_ROOT/envd" root kitdev 750
  ensure_directory "$KITDEV_RUNTIME_ROOT/orchestrator" root root 700
  ensure_directory "$KITDEV_RUNTIME_ROOT/orchestrator/template-storage" root root 700
  ensure_directory "$KITDEV_RUNTIME_ROOT/orchestrator/build-cache" root root 700
  ensure_directory "$KITDEV_RUNTIME_ROOT/sandbox-vms" root root 700
  ensure_directory "$KITDEV_RUNTIME_ROOT/snapshot-cache" root root 700
  ensure_directory "$KITDEV_RUNTIME_ROOT/sandbox-cache" root root 700
  ensure_directory "$KITDEV_RUNTIME_ROOT/template-cache" root root 700
  ensure_directory "$KITDEV_RUNTIME_ROOT/build-templates" root root 700
  ensure_directory "$KITDEV_RUNTIME_ROOT/shared-chunk-cache" root root 700
  ensure_directory "$KITDEV_RUNTIME_ROOT/control-plane" root root 700

  printf 'status=pass operation=prepare-layout lifecycle=%s\n' "$KITDEV_LIFECYCLE"
}

main "$@"
