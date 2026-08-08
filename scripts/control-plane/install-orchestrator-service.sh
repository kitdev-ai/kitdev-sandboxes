#!/usr/bin/env bash
set -Eeuo pipefail
readonly SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
readonly REPO_ROOT="$(CDPATH= cd -- "$SCRIPT_DIR/../.." && pwd -P)"
# shellcheck source=common.sh
source "$SCRIPT_DIR/common.sh"

if [[ "$SCRIPT_DIR" == "$KITDEV_OPT_ROOT/libexec/control-plane" ]]; then
  readonly ORCHESTRATOR_ENV_TEMPLATE="$SCRIPT_DIR/orchestrator.env.template"
  readonly ORCHESTRATOR_UNIT_SOURCE="$SCRIPT_DIR/orchestrator.service.expected"
else
  readonly ORCHESTRATOR_ENV_TEMPLATE="$REPO_ROOT/systemd/orchestrator.env.template"
  readonly ORCHESTRATOR_UNIT_SOURCE="$REPO_ROOT/systemd/kitdev-e2b-orchestrator.service"
fi

# The body is a subshell, not a brace block, so the EXIT trap below fires while
# `stage` is still in scope. Under a brace body the trap outlives the function:
# it runs when the shell exits, after the locals are gone, so `$stage` expanded
# to unset and `set -u` failed the script -- after `status=pass` had already
# been printed and with the staging directory leaked. replay-compose.sh uses
# this same subshell form for the same reason.
main() (
  local mode="${1:-}" stage network_values core_gateway
  [[ "$mode" == install || "$mode" == install-start || "$mode" == verify ||
    "$mode" == verify-files ]] ||
    control_plane_die invalid_operation 64
  require_root
  require_lifecycle_platform
  require_exact_directory "$KITDEV_OPT_ROOT" root root 755
  if [[ "$mode" == verify ]]; then
    require_exact_directory "$KITDEV_OPT_ROOT/libexec" root root 755
    require_exact_directory "$KITDEV_OPT_ROOT/libexec/control-plane" root root 755
  elif [[ "$mode" == verify-files ]]; then
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
    "$ORCHESTRATOR_ENV_TEMPLATE" >"$stage/orchestrator.env"
  [[ "$(grep -c '@KITDEV_LIFECYCLE@' "$ORCHESTRATOR_ENV_TEMPLATE")" == 1 &&
    "$(grep -c '@KITDEV_CORE_GATEWAY@' "$ORCHESTRATOR_ENV_TEMPLATE")" == 2 ]] ||
    control_plane_die orchestrator_template_invalid 65
  [[ "$({ grep -c '@' "$stage/orchestrator.env" || true; })" == 0 ]] ||
    control_plane_die orchestrator_template_render_failed 65

  if [[ "$mode" != verify ]]; then
    # verify-files reaches here too, and must stay read-only: publish_exact_file
    # creates what is missing and otherwise only verifies. The install modes
    # converge instead, so a second install from a newer revision replaces the
    # scripts it owns rather than dying on file_content_conflict.
    local publisher=publish_exact_file
    if [[ "$mode" == install || "$mode" == install-start ]]; then
      publisher=update_exact_file
    fi
    "$publisher" "$SCRIPT_DIR/common.sh" \
      "$KITDEV_OPT_ROOT/libexec/control-plane/common.sh" root root 755
    "$publisher" "$SCRIPT_DIR/bootstrap-network.sh" \
      "$KITDEV_OPT_ROOT/libexec/control-plane/bootstrap-network.sh" root root 755
    "$publisher" "$SCRIPT_DIR/configure-firewall.sh" \
      "$KITDEV_OPT_ROOT/libexec/control-plane/configure-firewall.sh" root root 755
    "$publisher" "$SCRIPT_DIR/private_env.py" \
      "$KITDEV_OPT_ROOT/libexec/control-plane/private_env.py" root root 755
    "$publisher" "$SCRIPT_DIR/preflight-orchestrator.sh" \
      "$KITDEV_OPT_ROOT/libexec/control-plane/preflight-orchestrator.sh" root root 755
    "$publisher" "$stage/orchestrator.env" \
      /etc/kitdev-sandboxes/orchestrator.env root root 600
    "$publisher" "$stage/orchestrator.env" \
      "$KITDEV_OPT_ROOT/libexec/control-plane/orchestrator.env.expected" root root 600
    "$publisher" "$ORCHESTRATOR_UNIT_SOURCE" \
      /etc/systemd/system/kitdev-e2b-orchestrator.service root root 644
    systemctl daemon-reload
    systemctl enable kitdev-e2b-orchestrator.service
    if [[ "$mode" == install-start ]]; then
      systemctl start kitdev-e2b-orchestrator.service
    fi
  fi
  # The helper stats its FIRST argument, so the installed target must come
  # first. Passing the release tree first validated the checkout's mode instead
  # of the installed file's, which fails for any source whose mode legitimately
  # differs -- private_env.py is 644 in Git and installed 755.
  require_exact_file "$KITDEV_OPT_ROOT/libexec/control-plane/common.sh" \
    "$SCRIPT_DIR/common.sh" root root 755
  require_exact_file "$KITDEV_OPT_ROOT/libexec/control-plane/bootstrap-network.sh" \
    "$SCRIPT_DIR/bootstrap-network.sh" root root 755
  require_exact_file "$KITDEV_OPT_ROOT/libexec/control-plane/configure-firewall.sh" \
    "$SCRIPT_DIR/configure-firewall.sh" root root 755
  require_exact_file "$KITDEV_OPT_ROOT/libexec/control-plane/private_env.py" \
    "$SCRIPT_DIR/private_env.py" root root 755
  require_exact_file "$KITDEV_OPT_ROOT/libexec/control-plane/preflight-orchestrator.sh" \
    "$SCRIPT_DIR/preflight-orchestrator.sh" root root 755
  require_exact_file /etc/kitdev-sandboxes/orchestrator.env \
    "$stage/orchestrator.env" root root 600
  require_exact_file "$KITDEV_OPT_ROOT/libexec/control-plane/orchestrator.env.expected" \
    "$stage/orchestrator.env" root root 600
  require_exact_file /etc/systemd/system/kitdev-e2b-orchestrator.service \
    "$ORCHESTRATOR_UNIT_SOURCE" root root 644
  systemctl is-enabled --quiet kitdev-e2b-orchestrator.service ||
    control_plane_die orchestrator_service_not_enabled 65
  if [[ "$mode" == install-start || "$mode" == verify ]]; then
    systemctl is-active --quiet kitdev-e2b-orchestrator.service ||
      control_plane_die orchestrator_service_not_active 65
  fi
  printf 'status=pass operation=%s-orchestrator-service\n' "$mode"
)

main "$@"
