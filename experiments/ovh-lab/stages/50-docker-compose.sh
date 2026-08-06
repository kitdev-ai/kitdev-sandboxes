#!/usr/bin/env bash
set -Eeuo pipefail
readonly SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
# shellcheck source=../lib/common.sh
source "$SCRIPT_DIR/../lib/common.sh"
# OVH_LAB_STAGE_BODY

snapshot() {
  printf 'stage=50 docker_command=%s docker_service=%s compose_plugin=%s docker_socket=%s\n' \
    "$(lab_command_state docker)" "$(lab_service_state docker.service)" \
    "$(if docker compose version >/dev/null 2>&1; then printf present; else printf absent; fi)" \
    "$(lab_bool test -S /var/run/docker.sock)"
}

main() {
  local mode="${1:-}"
  lab_require_ack "$@"; lab_refuse_production; lab_require_supported_platform
  case "$mode" in
    before|after) snapshot ;;
    execute|rollback) lab_blocked docker_repository_and_package_pins_not_approved ;;
    postconditions|rollback-postconditions)
      printf 'status=pass authorized_mutation=none recovery=reinstall\n'
      ;;
  esac
}
main "$@"
