#!/usr/bin/env bash
set -Eeuo pipefail
readonly SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
# shellcheck source=../lib/common.sh
source "$SCRIPT_DIR/../lib/common.sh"
# OVH_LAB_STAGE_BODY

snapshot() {
  printf 'stage=10 apt_get=%s python3=%s git=%s curl=%s docker=%s ansible=%s\n' \
    "$(lab_command_state apt-get)" "$(lab_command_state python3)" \
    "$(lab_command_state git)" "$(lab_command_state curl)" \
    "$(lab_command_state docker)" "$(lab_command_state ansible-playbook)"
}

main() {
  local mode="${1:-}"
  lab_require_ack "$@"; lab_refuse_production; lab_require_supported_platform
  case "$mode" in
    before|after) snapshot ;;
    execute|rollback) lab_blocked pinned_package_bootstrap_not_approved ;;
    postconditions|rollback-postconditions)
      printf 'status=pass authorized_mutation=none recovery=reinstall\n'
      ;;
  esac
}
main "$@"
