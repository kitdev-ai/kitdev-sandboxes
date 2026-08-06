#!/usr/bin/env bash
set -Eeuo pipefail
readonly SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
# shellcheck source=../lib/common.sh
source "$SCRIPT_DIR/../lib/common.sh"
# OVH_LAB_STAGE_BODY

snapshot() {
  printf 'stage=70 git=%s go=%s docker=%s workspace_entries=%s\n' \
    "$(lab_command_state git)" "$(lab_command_state go)" "$(lab_command_state docker)" \
    "$(find "$OVH_LAB_WORKSPACE" -mindepth 1 -maxdepth 1 -print 2>/dev/null | wc -l | tr -d ' ')"
}

main() {
  local mode="${1:-}"
  lab_require_ack "$@"; lab_refuse_production; lab_require_supported_platform
  case "$mode" in
    before|after) snapshot ;;
    execute|rollback) lab_blocked upstream_build_graph_and_artifact_verification_not_approved ;;
    postconditions|rollback-postconditions)
      printf 'status=pass authorized_mutation=none recovery=reinstall\n'
      ;;
  esac
}
main "$@"
