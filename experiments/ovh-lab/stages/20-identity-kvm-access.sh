#!/usr/bin/env bash
set -Eeuo pipefail
readonly SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
# shellcheck source=../lib/common.sh
source "$SCRIPT_DIR/../lib/common.sh"
# OVH_LAB_STAGE_BODY

snapshot() {
  local kvm_group=absent
  local worker=absent
  getent group kvm >/dev/null 2>&1 && kvm_group=present
  getent passwd kitdev-worker >/dev/null 2>&1 && worker=present
  printf 'stage=20 kvm_device=%s kvm_group=%s worker_identity=%s root_kvm_rw=%s\n' \
    "$(lab_bool test -c /dev/kvm)" "$kvm_group" "$worker" \
    "$(lab_bool test -r /dev/kvm -a -w /dev/kvm)"
}

main() {
  local mode="${1:-}"
  lab_require_ack "$@"; lab_refuse_production; lab_require_supported_platform
  case "$mode" in
    before|after) snapshot ;;
    execute|rollback) lab_blocked identity_plan_rejected_pending_journal_and_ansible ;;
    postconditions|rollback-postconditions)
      printf 'status=pass authorized_mutation=none recovery=reinstall\n'
      ;;
  esac
}
main "$@"
