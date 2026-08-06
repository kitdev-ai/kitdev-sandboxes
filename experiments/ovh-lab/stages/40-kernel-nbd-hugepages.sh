#!/usr/bin/env bash
set -Eeuo pipefail
readonly SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
# shellcheck source=../lib/common.sh
source "$SCRIPT_DIR/../lib/common.sh"
# OVH_LAB_STAGE_BODY

snapshot() {
  printf 'stage=40 nbd_loaded=%s nbd_devices=%s hugepages_total=%s hugepages_free=%s\n' \
    "$(lab_bool test -d /sys/module/nbd)" \
    "$(find /sys/class/block -maxdepth 1 -type l -name 'nbd*' 2>/dev/null | wc -l | tr -d ' ')" \
    "$(awk '/^HugePages_Total:/ { print $2 }' /proc/meminfo)" \
    "$(awk '/^HugePages_Free:/ { print $2 }' /proc/meminfo)"
}

main() {
  local mode="${1:-}"
  lab_require_ack "$@"; lab_refuse_production; lab_require_supported_platform
  case "$mode" in
    before|after) snapshot ;;
    execute|rollback) lab_blocked nbd_and_hugepage_values_not_approved ;;
    postconditions|rollback-postconditions)
      printf 'status=pass authorized_mutation=none recovery=reinstall\n'
      ;;
  esac
}
main "$@"
