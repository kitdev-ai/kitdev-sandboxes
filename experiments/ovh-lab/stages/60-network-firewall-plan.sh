#!/usr/bin/env bash
set -Eeuo pipefail
readonly SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
# shellcheck source=../lib/common.sh
source "$SCRIPT_DIR/../lib/common.sh"
# OVH_LAB_STAGE_BODY

snapshot() {
  local nft_probe=unavailable
  if command -v nft >/dev/null 2>&1; then
    if nft list ruleset >/dev/null 2>&1; then
      nft_probe=readable
    else
      nft_probe=error
    fi
  fi
  printf 'stage=60 interfaces_up=%s default_routes=%s nft_command=%s nft_ruleset_probe=%s ufw_service=%s\n' \
    "$(ip -o link show up 2>/dev/null | wc -l | tr -d ' ')" \
    "$(ip route show default 2>/dev/null | wc -l | tr -d ' ')" \
    "$(lab_command_state nft)" "$nft_probe" "$(lab_service_state ufw.service)"
}

main() {
  local mode="${1:-}"
  lab_require_ack "$@"; lab_refuse_production; lab_require_supported_platform
  case "$mode" in
    before|after) snapshot ;;
    execute|rollback) lab_blocked required_ports_bindings_and_firewall_policy_unresolved ;;
    postconditions|rollback-postconditions)
      printf 'status=pass authorized_mutation=none recovery=reinstall\n'
      ;;
  esac
}
main "$@"
