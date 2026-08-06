#!/usr/bin/env bash
set -Eeuo pipefail
readonly SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
# shellcheck source=../lib/common.sh
source "$SCRIPT_DIR/../lib/common.sh"
# OVH_LAB_STAGE_BODY

baseline_snapshot() {
  local version_id=unknown
  local architecture=unknown
  local docker_state
  local firewall_probe=unavailable
  local ufw_state
  if [[ -r /etc/os-release ]]; then
    # shellcheck disable=SC1091
    source /etc/os-release
    version_id="${VERSION_ID:-unknown}"
  fi
  architecture="$(uname -m)"
  if command -v nft >/dev/null 2>&1; then
    if nft list ruleset >/dev/null 2>&1; then
      firewall_probe=readable
    else
      firewall_probe=error
    fi
  fi
  docker_state="$(lab_service_state docker.service)" || lab_die service_discovery_failed 1
  ufw_state="$(lab_service_state ufw.service)" || lab_die service_discovery_failed 1
  printf 'platform.version_id=%s\n' "$version_id"
  printf 'platform.architecture=%s\n' "$architecture"
  printf 'platform.systemd=%s\n' "$(lab_bool test "$(cat /proc/1/comm 2>/dev/null || true)" = systemd)"
  printf 'platform.cgroup_v2=%s\n' "$(lab_bool test "$(stat -fc %T /sys/fs/cgroup 2>/dev/null || true)" = cgroup2fs)"
  printf 'capacity.logical_cpus=%s\n' "$(getconf _NPROCESSORS_ONLN 2>/dev/null || printf unknown)"
  printf 'capacity.memory_bytes=%s\n' "$(awk '/^MemTotal:/ { print $2 * 1024; found=1 } END { if (!found) print "unknown" }' /proc/meminfo)"
  printf 'virtualization.kvm_device=%s\n' "$(lab_bool test -c /dev/kvm)"
  printf 'virtualization.kvm_root_rw=%s\n' "$(lab_bool test -r /dev/kvm -a -w /dev/kvm)"
  printf 'virtualization.tun_device=%s\n' "$(lab_bool test -c /dev/net/tun)"
  printf 'kernel.nbd_loaded=%s\n' "$(lab_bool test -d /sys/module/nbd)"
  printf 'kernel.hugepages_total=%s\n' "$(awk '/^HugePages_Total:/ { print $2; found=1 } END { if (!found) print "unknown" }' /proc/meminfo)"
  printf 'storage.block_devices=%s\n' "$(lsblk -dn -o TYPE 2>/dev/null | awk '$1 == "disk" { n++ } END { print n+0 }')"
  printf 'storage.md_arrays=%s\n' "$(awk '/^md[0-9]+[[:space:]]*:/ { n++ } END { print n+0 }' /proc/mdstat 2>/dev/null)"
  printf 'services.docker=%s\n' "$docker_state"
  printf 'services.ufw=%s\n' "$ufw_state"
  printf 'firewall.ruleset_probe=%s\n' "$firewall_probe"
  printf 'network.default_routes=%s\n' "$(ip route show default 2>/dev/null | wc -l | tr -d ' ')"
}

main() {
  local mode="${1:-}"
  lab_require_ack "$@"
  lab_refuse_production
  lab_require_supported_platform
  case "$mode" in
    before|execute|after)
      printf 'stage=00 mode=%s mutation=none\n' "$mode"
      baseline_snapshot
      ;;
    postconditions)
      [[ -r /etc/os-release ]] || lab_die os_release_unreadable 1
      # shellcheck disable=SC1091
      source /etc/os-release
      [[ "${VERSION_ID:-}" == 26.04 ]] || lab_die unsupported_lab_os 1
      [[ "$(uname -m)" == x86_64 ]] || lab_die unsupported_lab_architecture 1
      printf 'status=pass scope=read-only-baseline\n'
      ;;
    rollback|rollback-postconditions)
      printf 'status=pass mutation=none rollback=not-required\n'
      ;;
  esac
}

main "$@"
