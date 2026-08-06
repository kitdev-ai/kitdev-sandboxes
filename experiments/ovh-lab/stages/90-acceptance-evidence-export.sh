#!/usr/bin/env bash
set -Eeuo pipefail
readonly SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
# shellcheck source=../lib/common.sh
source "$SCRIPT_DIR/../lib/common.sh"
# OVH_LAB_STAGE_BODY

snapshot() {
  printf 'stage=90 marker=%s os_26_04=%s architecture_x86_64=%s kvm_root_rw=%s api=%s proxy=%s orchestrator=%s\n' \
    "$(lab_bool test -f "$OVH_LAB_MARKER")" \
    "$(if grep -Eq '^VERSION_ID="?26\.04"?$' /etc/os-release 2>/dev/null; then printf yes; else printf no; fi)" \
    "$(lab_bool test "$(uname -m)" = x86_64)" \
    "$(lab_bool test -r /dev/kvm -a -w /dev/kvm)" \
    "$(lab_service_state kitdev-e2b-api.service)" \
    "$(lab_service_state kitdev-e2b-client-proxy.service)" \
    "$(lab_service_state kitdev-e2b-orchestrator.service)"
}

main() {
  local mode="${1:-}"
  lab_require_ack "$@"; lab_refuse_production; lab_require_supported_platform
  case "$mode" in
    before|after) snapshot ;;
    execute|rollback) lab_blocked acceptance_requires_completed_reinstallable_automation ;;
    postconditions)
      printf 'status=pass scope=lab-evidence-export final_acceptance=reinstall-then-kitdev-ansible\n'
      ;;
    rollback-postconditions)
      printf 'status=pass mutation=none rollback=not-required\n'
      ;;
  esac
}
main "$@"
