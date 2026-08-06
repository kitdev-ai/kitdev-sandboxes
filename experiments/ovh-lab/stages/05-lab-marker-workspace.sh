#!/usr/bin/env bash
set -Eeuo pipefail
readonly SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
# shellcheck source=../lib/common.sh
source "$SCRIPT_DIR/../lib/common.sh"
# OVH_LAB_STAGE_BODY

marker_snapshot() {
  printf 'stage=05 marker=%s workspace=%s\n' \
    "$(lab_bool test -f "$OVH_LAB_MARKER")" \
    "$(lab_bool test -d "$OVH_LAB_WORKSPACE")"
}

main() {
  local mode="${1:-}"
  lab_require_ack "$@"
  lab_refuse_production
  lab_require_supported_platform
  case "$mode" in
    before|after) marker_snapshot ;;
    execute|rollback) lab_blocked crash_consistent_lab_provenance_not_implemented ;;
    postconditions|rollback-postconditions)
      printf 'status=pass authorized_mutation=none recovery=reinstall\n'
      ;;
  esac
}

main "$@"
