#!/usr/bin/env bash
set -Eeuo pipefail
readonly SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
readonly REPO_ROOT="$(CDPATH= cd -- "$SCRIPT_DIR/../.." && pwd -P)"
# shellcheck source=common.sh
source "$SCRIPT_DIR/common.sh"

main() {
  local mode="${1:-}" stage network_values core_gateway
  [[ "$mode" == install || "$mode" == install-start || "$mode" == verify ]] ||
    control_plane_die invalid_operation 64
  require_root
  require_lifecycle_platform
  require_exact_directory "$KITDEV_OPT_ROOT" root root 755
  if [[ "$mode" == verify ]]; then
    require_exact_directory "$KITDEV_OPT_ROOT/libexec" root root 755
    require_exact_directory "$KITDEV_OPT_ROOT/libexec/control-plane" root root 755
  else
    ensure_directory "$KITDEV_OPT_ROOT/libexec" root root 755
    ensure_directory "$KITDEV_OPT_ROOT/libexec/control-plane" root root 755
  fi
  require_exact_directory "$KITDEV_ETC_ROOT" root root 700
  "$SCRIPT_DIR/bootstrap-network.sh" verify >/dev/null
  network_values="$(/usr/bin/python3 -I -B -S "$SCRIPT_DIR/private_env.py" get-network)" ||
    control_plane_die private_network_missing 65
  mapfile -t network_parts <<<"$network_values"
  [[ "${#network_parts[@]}" == 2 ]] || control_plane_die private_network_invalid 65
  core_gateway="${network_parts[1]}"

  stage="$(mktemp -d /tmp/kitdev-orchestrator-service.XXXXXXXX)"
  trap 'rm -rf -- "$stage"' EXIT
  chmod 0700 -- "$stage"
  sed -e "s/@KITDEV_LIFECYCLE@/$KITDEV_LIFECYCLE/" \
    -e "s/@KITDEV_CORE_GATEWAY@/$core_gateway/g" \
    "$REPO_ROOT/systemd/orchestrator.env.template" >"$stage/orchestrator.env"
  [[ "$(grep -c '@KITDEV_LIFECYCLE@' "$REPO_ROOT/systemd/orchestrator.env.template")" == 1 &&
    "$(grep -c '@KITDEV_CORE_GATEWAY@' "$REPO_ROOT/systemd/orchestrator.env.template")" == 2 ]] ||
    control_plane_die orchestrator_template_invalid 65
  [[ "$({ grep -c '@' "$stage/orchestrator.env" || true; })" == 0 ]] ||
    control_plane_die orchestrator_template_render_failed 65

  if [[ "$mode" != verify ]]; then
    publish_exact_file "$SCRIPT_DIR/common.sh" \
      "$KITDEV_OPT_ROOT/libexec/control-plane/common.sh" root root 755
    publish_exact_file "$SCRIPT_DIR/bootstrap-network.sh" \
      "$KITDEV_OPT_ROOT/libexec/control-plane/bootstrap-network.sh" root root 755
    publish_exact_file "$SCRIPT_DIR/configure-firewall.sh" \
      "$KITDEV_OPT_ROOT/libexec/control-plane/configure-firewall.sh" root root 755
    publish_exact_file "$SCRIPT_DIR/private_env.py" \
      "$KITDEV_OPT_ROOT/libexec/control-plane/private_env.py" root root 755
    publish_exact_file "$SCRIPT_DIR/preflight-orchestrator.sh" \
      "$KITDEV_OPT_ROOT/libexec/control-plane/preflight-orchestrator.sh" root root 755
    publish_exact_file "$stage/orchestrator.env" \
      /etc/kitdev-sandboxes/orchestrator.env root root 600
    publish_exact_file "$stage/orchestrator.env" \
      "$KITDEV_OPT_ROOT/libexec/control-plane/orchestrator.env.expected" root root 600
    publish_exact_file "$REPO_ROOT/systemd/kitdev-e2b-orchestrator.service" \
      /etc/systemd/system/kitdev-e2b-orchestrator.service root root 644
    systemctl daemon-reload
    systemctl enable kitdev-e2b-orchestrator.service
    if [[ "$mode" == install-start ]]; then
      systemctl start kitdev-e2b-orchestrator.service
    fi
  fi
  require_exact_file "$SCRIPT_DIR/common.sh" \
    "$KITDEV_OPT_ROOT/libexec/control-plane/common.sh" root root 755
  require_exact_file "$SCRIPT_DIR/bootstrap-network.sh" \
    "$KITDEV_OPT_ROOT/libexec/control-plane/bootstrap-network.sh" root root 755
  require_exact_file "$SCRIPT_DIR/configure-firewall.sh" \
    "$KITDEV_OPT_ROOT/libexec/control-plane/configure-firewall.sh" root root 755
  require_exact_file "$SCRIPT_DIR/private_env.py" \
    "$KITDEV_OPT_ROOT/libexec/control-plane/private_env.py" root root 755
  require_exact_file "$SCRIPT_DIR/preflight-orchestrator.sh" \
    "$KITDEV_OPT_ROOT/libexec/control-plane/preflight-orchestrator.sh" root root 755
  require_exact_file "$stage/orchestrator.env" \
    /etc/kitdev-sandboxes/orchestrator.env root root 600
  require_exact_file "$stage/orchestrator.env" \
    "$KITDEV_OPT_ROOT/libexec/control-plane/orchestrator.env.expected" root root 600
  require_exact_file "$REPO_ROOT/systemd/kitdev-e2b-orchestrator.service" \
    /etc/systemd/system/kitdev-e2b-orchestrator.service root root 644
  systemctl is-enabled --quiet kitdev-e2b-orchestrator.service ||
    control_plane_die orchestrator_service_not_enabled 65
  if [[ "$mode" == install-start || "$mode" == verify ]]; then
    systemctl is-active --quiet kitdev-e2b-orchestrator.service ||
      control_plane_die orchestrator_service_not_active 65
  fi
  printf 'status=pass operation=%s-orchestrator-service\n' "$mode"
}

main "$@"
