#!/usr/bin/env bash
set -Eeuo pipefail
readonly SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
# shellcheck source=../lib/common.sh
source "$SCRIPT_DIR/../lib/common.sh"
# OVH_LAB_STAGE_BODY

snapshot() {
  printf 'stage=80 api=%s proxy=%s orchestrator=%s\n' \
    "$(lab_service_state kitdev-e2b-api.service)" \
    "$(lab_service_state kitdev-e2b-client-proxy.service)" \
    "$(lab_service_state kitdev-e2b-orchestrator.service)"
}

main() {
  local mode="${1:-}"
  lab_require_ack "$@"; lab_refuse_production; lab_require_supported_platform
  case "$mode" in
    before|after) snapshot ;;
    execute|rollback) lab_blocked reviewed_units_and_service_contracts_not_implemented ;;
    postconditions|rollback-postconditions)
      printf 'status=pass authorized_mutation=none recovery=reinstall\n'
      ;;
  esac
}
main "$@"
