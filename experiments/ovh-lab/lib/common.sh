#!/usr/bin/env bash
set -Eeuo pipefail

export PATH=/usr/sbin:/usr/bin:/sbin:/bin
export LC_ALL=C
export LANG=C
unset BASH_ENV CDPATH ENV GLOBIGNORE

readonly OVH_LAB_ACK="DISPOSABLE_OVH_LAB"
readonly OVH_LAB_MARKER="/etc/kitdev-sandboxes/disposable-ovh-lab"
readonly OVH_LAB_WORKSPACE="/var/lib/kitdev-sandboxes/experiments/ovh-lab"
readonly OVH_LAB_PRODUCTION_MARKER="/etc/kitdev-sandboxes/production"
readonly OVH_LAB_INSTALL_MANIFEST="/var/lib/kitdev-sandboxes/install-manifest.json"
readonly OVH_LAB_CONFIG_MANIFEST="/etc/kitdev-sandboxes/install-manifest.json"
readonly OVH_LAB_INSTALL_ROOT="/opt/kitdev-sandboxes"

lab_die() {
  printf 'status=error reason=%s\n' "$1" >&2
  exit "${2:-1}"
}

lab_require_ack() {
  [[ "${2:-}" == "$OVH_LAB_ACK" ]] || lab_die acknowledgement_required 64
  [[ "${3:-}" =~ ^[0-9a-f]{64}$ ]] || lab_die bundle_digest_required 64
  case "${1:-}" in
    before|execute|after|postconditions|rollback|rollback-postconditions) ;;
    *) lab_die invalid_mode 64 ;;
  esac
}

lab_require_supported_platform() {
  local version_id=unknown
  [[ -r /etc/os-release ]] || lab_die os_release_unreadable 68
  # shellcheck disable=SC1091
  source /etc/os-release
  version_id="${VERSION_ID:-unknown}"
  [[ "$version_id" == 26.04 ]] || lab_die unsupported_lab_os 68
  [[ "$(uname -m)" == x86_64 ]] || lab_die unsupported_lab_architecture 68
  [[ "$(cat /proc/1/comm 2>/dev/null)" == systemd ]] || lab_die systemd_required 68
  [[ "$(stat -fc %T /sys/fs/cgroup 2>/dev/null)" == cgroup2fs ]] || lab_die cgroup_v2_required 68
}

lab_refuse_production() {
  [[ ! -e "$OVH_LAB_PRODUCTION_MARKER" && ! -L "$OVH_LAB_PRODUCTION_MARKER" ]] ||
    lab_die production_marker_present 65
  [[ ! -e "$OVH_LAB_INSTALL_MANIFEST" && ! -L "$OVH_LAB_INSTALL_MANIFEST" ]] ||
    lab_die installation_manifest_present 65
  [[ ! -e "$OVH_LAB_CONFIG_MANIFEST" && ! -L "$OVH_LAB_CONFIG_MANIFEST" ]] ||
    lab_die configuration_manifest_present 65
  [[ ! -e "$OVH_LAB_INSTALL_ROOT" && ! -L "$OVH_LAB_INSTALL_ROOT" ]] ||
    lab_die production_install_root_present 65
  local unit_dir unit
  for unit_dir in /etc/systemd/system /usr/lib/systemd/system /lib/systemd/system /etc/systemd/system/multi-user.target.wants; do
    for unit in kitdev-e2b-api.service kitdev-e2b-client-proxy.service kitdev-e2b-orchestrator.service; do
      [[ ! -e "$unit_dir/$unit" && ! -L "$unit_dir/$unit" ]] ||
        lab_die production_service_unit_present 65
    done
  done
}

lab_blocked() {
  printf 'status=blocked reason=%s\n' "$1"
  exit 20
}

lab_bool() {
  if "$@" >/dev/null 2>&1; then
    printf 'yes'
  else
    printf 'no'
  fi
}

lab_command_state() {
  if command -v -- "$1" >/dev/null 2>&1; then
    printf 'present'
  else
    printf 'absent'
  fi
}

lab_service_state() {
  local unit="$1"
  if ! command -v systemctl >/dev/null 2>&1; then
    printf 'systemctl-absent'
  elif systemctl is-active --quiet "$unit" 2>/dev/null; then
    printf 'active'
  elif systemctl list-unit-files -- "$unit" 2>/dev/null | grep -Fq -- "$unit"; then
    printf 'inactive'
  elif ! systemctl list-unit-files -- "$unit" >/dev/null 2>&1; then
    printf 'error'
    return 1
  else
    printf 'absent'
  fi
}
