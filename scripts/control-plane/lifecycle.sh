#!/usr/bin/env bash
set -Eeuo pipefail
readonly SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
readonly INSTALLED_SCRIPT_DIR=/opt/kitdev-sandboxes/libexec/control-plane
readonly REQUESTED_OPERATION="${1:-}"
if [[ "$REQUESTED_OPERATION" != install && "$SCRIPT_DIR" != "$INSTALLED_SCRIPT_DIR" &&
  ! -L "$INSTALLED_SCRIPT_DIR/lifecycle.sh" && -f "$INSTALLED_SCRIPT_DIR/lifecycle.sh" ]]; then
  exec /usr/bin/bash "$INSTALLED_SCRIPT_DIR/lifecycle.sh" "$@"
fi
# shellcheck source=common.sh
source "$SCRIPT_DIR/common.sh"

readonly LIFECYCLE_LOCK=/run/kitdev-sandboxes/control-plane-lifecycle.lock

require_profile() {
  [[ "${KITDEV_PROFILE:-}" == minimal ]] || control_plane_die profile_not_implemented 68
}

require_prepared_host() {
  local command
  [[ "$KITDEV_LIFECYCLE" != production ]] ||
    control_plane_die production_template_install_not_implemented 68
  require_worker_identity
  getent group kitdev >/dev/null || control_plane_die kitdev_group_required 65
  for command in curl docker flock git ip pgrep sha256sum systemctl timeout ufw; do
    require_command "$command"
  done
  [[ -c /dev/kvm && -r /dev/kvm && -w /dev/kvm ]] || control_plane_die kvm_unusable 65
  [[ -c /dev/net/tun ]] || control_plane_die tun_unusable 65
  [[ -r /sys/module/nbd/parameters/nbds_max ]] || control_plane_die nbd_not_loaded 65
  [[ "$(cat /sys/module/nbd/parameters/nbds_max)" -ge 16 ]] ||
    control_plane_die nbd_pool_too_small 65
  [[ -b /dev/nbd0 ]] || control_plane_die nbd_device_missing 65
  [[ "$(sysctl -n net.ipv4.ip_forward)" == 1 ]] ||
    control_plane_die ipv4_forwarding_disabled 65
  grep -q '^Hugepagesize:[[:space:]]*2048 kB$' /proc/meminfo ||
    control_plane_die hugepage_size_mismatch 65
  awk '$1 == "HugePages_Free:" { found=1; exit !($2 >= 512) } END { if (!found) exit 1 }' \
    /proc/meminfo || control_plane_die hugepage_capacity_insufficient 65
  [[ "$(ufw status | sed -n '1p')" == 'Status: active' ]] || control_plane_die ufw_not_active 65
  systemctl is-active --quiet docker.service || control_plane_die docker_not_active 65
}

require_installed() {
  require_exact_directory "$KITDEV_ETC_ROOT" root root 700
  require_exact_directory "$KITDEV_OPT_ROOT" root root 755
  "$SCRIPT_DIR/bootstrap-network.sh" verify >/dev/null
  /usr/bin/python3 -I -B -S "$SCRIPT_DIR/private_env.py" verify >/dev/null
  "$SCRIPT_DIR/replay-compose.sh" validate >/dev/null
  "$SCRIPT_DIR/install-orchestrator-service.sh" verify-files >/dev/null
}

install_lifecycle_assets() {
  local name source sdk_file_count=0
  ensure_directory "$KITDEV_OPT_ROOT/libexec" root root 755
  ensure_directory "$INSTALLED_SCRIPT_DIR" root root 755
  ensure_directory "$INSTALLED_SCRIPT_DIR/e2e-process-client" root root 755
  ensure_directory "$INSTALLED_SCRIPT_DIR/e2e-typescript-sdk" root root 755
  for name in \
    acquire-source.sh backup-restore.sh bootstrap-network.sh bootstrap-private-env.sh build-control-plane-images.sh \
    build-envd.sh build-orchestrator.sh build-snapshot-tools.sh common.sh configure-firewall.sh \
    install-orchestrator-service.sh install-runtime-artifacts.sh lifecycle.sh \
    preflight-orchestrator.sh prepare-layout.sh replay-compose.sh seed-local-template.sh \
    verify-api-proxy-e2e.sh verify-typescript-sdk-e2e.sh; do
    publish_exact_file "$SCRIPT_DIR/$name" "$INSTALLED_SCRIPT_DIR/$name" root root 755
  done
  for name in backup_manifest.py normalize-copy-sql.py private_env.py publish-template-dirs.py; do
    publish_exact_file "$SCRIPT_DIR/$name" "$INSTALLED_SCRIPT_DIR/$name" root root 755
  done
  publish_exact_file "$SCRIPT_DIR/e2e-process-client/main.go" \
    "$INSTALLED_SCRIPT_DIR/e2e-process-client/main.go" root root 644
  for source in "$SCRIPT_DIR/e2e-typescript-sdk"/*; do
    [[ ! -L "$source" && -f "$source" ]] || control_plane_die sdk_source_entry_invalid 65
    name="$(basename -- "$source")"
    case "$name" in
      package.json|package-lock.json) ;;
      *)
        [[ "$name" =~ ^[a-z][a-z0-9-]{0,63}\.ts$ ]] ||
          control_plane_die sdk_source_name_invalid 65
        ;;
    esac
    sdk_file_count=$((sdk_file_count + 1))
    (( sdk_file_count <= 64 )) || control_plane_die sdk_source_count_invalid 65
    publish_exact_file "$source" \
      "$INSTALLED_SCRIPT_DIR/e2e-typescript-sdk/$name" root root 644
  done
  (( sdk_file_count >= 3 )) || control_plane_die sdk_source_count_invalid 65
  publish_exact_file "$SCRIPT_DIR/../../systemd/orchestrator.env.template" \
    "$INSTALLED_SCRIPT_DIR/orchestrator.env.template" root root 644
  publish_exact_file "$SCRIPT_DIR/../../systemd/kitdev-e2b-orchestrator.service" \
    "$INSTALLED_SCRIPT_DIR/orchestrator.service.expected" root root 644
}

acquire_lock() {
  install -d -o root -g root -m 0700 -- /run/kitdev-sandboxes
  if [[ ! -e "$LIFECYCLE_LOCK" && ! -L "$LIFECYCLE_LOCK" ]]; then
    install -o root -g root -m 0600 /dev/null "$LIFECYCLE_LOCK"
  fi
  [[ ! -L "$LIFECYCLE_LOCK" && -f "$LIFECYCLE_LOCK" &&
    "$(stat -c '%u:%g:%a:%s:%h' -- "$LIFECYCLE_LOCK")" == 0:0:600:0:1 ]] ||
    control_plane_die lifecycle_lock_invalid 65
  exec 9<>"$LIFECYCLE_LOCK"
  [[ "$(stat -Lc '%d:%i' /proc/$$/fd/9)" == "$(stat -Lc '%d:%i' "$LIFECYCLE_LOCK")" ]] ||
    control_plane_die lifecycle_lock_changed 65
  flock --nonblock 9 || control_plane_die lifecycle_operation_running 75
}

install_control_plane() {
  require_prepared_host
  "$SCRIPT_DIR/prepare-layout.sh"
  "$SCRIPT_DIR/bootstrap-private-env.sh"
  "$SCRIPT_DIR/bootstrap-network.sh" ensure
  "$SCRIPT_DIR/acquire-source.sh"
  "$SCRIPT_DIR/build-control-plane-images.sh"
  "$SCRIPT_DIR/replay-compose.sh" install
  install_lifecycle_assets
  "$SCRIPT_DIR/install-runtime-artifacts.sh"
  "$SCRIPT_DIR/build-envd.sh"
  "$SCRIPT_DIR/build-snapshot-tools.sh"
  "$SCRIPT_DIR/build-orchestrator.sh"
  "$SCRIPT_DIR/configure-firewall.sh" apply
  "$SCRIPT_DIR/install-orchestrator-service.sh" install
  up_control_plane
  "$SCRIPT_DIR/seed-local-template.sh"
  printf 'status=pass operation=install-control-plane\n'
}

up_control_plane() {
  require_installed
  "$SCRIPT_DIR/replay-compose.sh" up
  systemctl start kitdev-e2b-orchestrator.service
  "$SCRIPT_DIR/install-orchestrator-service.sh" verify
  "$SCRIPT_DIR/replay-compose.sh" verify
  printf 'status=pass operation=up-control-plane\n'
}

down_control_plane() {
  require_installed
  ! pgrep -x firecracker >/dev/null 2>&1 || control_plane_die active_sandboxes_present 69
  "$SCRIPT_DIR/replay-compose.sh" quiesce
  if pgrep -x firecracker >/dev/null 2>&1; then
    "$SCRIPT_DIR/replay-compose.sh" up >/dev/null || true
    control_plane_die sandbox_started_during_quiesce 69
  fi
  if ! systemctl stop kitdev-e2b-orchestrator.service; then
    "$SCRIPT_DIR/replay-compose.sh" up >/dev/null || true
    control_plane_die orchestrator_stop_failed 70
  fi
  if pgrep -x firecracker >/dev/null 2>&1; then
    systemctl start kitdev-e2b-orchestrator.service || true
    "$SCRIPT_DIR/replay-compose.sh" up >/dev/null || true
    control_plane_die firecracker_cleanup_incomplete 70
  fi
  if ! "$SCRIPT_DIR/replay-compose.sh" down; then
    "$SCRIPT_DIR/replay-compose.sh" up >/dev/null || true
    systemctl start kitdev-e2b-orchestrator.service || true
    control_plane_die control_plane_stop_failed 70
  fi
  printf 'status=pass operation=down-control-plane\n'
}

status_control_plane() {
  local orchestrator=inactive compose=stopped api=unreachable proxy=unreachable
  local firecrackers code
  systemctl is-active --quiet kitdev-e2b-orchestrator.service && orchestrator=active
  if docker ps --quiet --filter label=com.docker.compose.project=kitdev-control-plane |
    grep -q .; then
    compose=running
  fi
  code="$(curl --config /dev/null --silent --output /dev/null --write-out '%{http_code}' \
    --max-time 2 -- http://127.0.0.1:3000/health 2>/dev/null || true)"
  [[ "$code" == 200 ]] && api=healthy
  code="$(curl --config /dev/null --silent --output /dev/null --write-out '%{http_code}' \
    --max-time 2 -- http://127.0.0.1:3003/health 2>/dev/null || true)"
  [[ "$code" == 200 ]] && proxy=healthy
  firecrackers="$({ pgrep -x firecracker || true; } | wc -l | tr -d ' ')"
  printf 'status=%s orchestrator=%s compose=%s api=%s proxy=%s firecrackers=%s\n' \
    "$([[ "$orchestrator:$compose:$api:$proxy" == active:running:healthy:healthy ]] &&
      printf pass || printf degraded)" \
    "$orchestrator" "$compose" "$api" "$proxy" "$firecrackers"
  [[ "$orchestrator:$compose:$api:$proxy" == active:running:healthy:healthy ]]
}

test_control_plane() {
  local suite="$1"
  require_installed
  [[ "$KITDEV_LIFECYCLE" != production ]] || control_plane_die e2e_not_for_production 68
  [[ -n "${KITDEV_E2E_API_KEY_FILE:-}" ]] || control_plane_die e2e_api_key_file_required 64
  if [[ "$suite" == core || "$suite" == smoke ]]; then
    "$SCRIPT_DIR/verify-api-proxy-e2e.sh" --api-key-file "$KITDEV_E2E_API_KEY_FILE"
  fi
  if [[ "$suite" == sdk || "$suite" == smoke ]]; then
    [[ -n "${KITDEV_E2E_TEMPLATE_ID_FILE:-}" ]] ||
      control_plane_die e2e_template_id_file_required 64
    "$SCRIPT_DIR/verify-typescript-sdk-e2e.sh" \
      --api-key-file "$KITDEV_E2E_API_KEY_FILE" \
      --template-id-file "$KITDEV_E2E_TEMPLATE_ID_FILE"
  fi
  printf 'status=pass operation=test-%s-control-plane\n' "$suite"
}

main() {
  local operation="${1:-}"
  [[ $# == 1 ]] || control_plane_die invalid_arguments 64
  case "$operation" in install|up|down|restart|status|test-core|test-sdk|test-smoke) ;;
    *) control_plane_die invalid_operation 64 ;;
  esac
  require_root
  require_lifecycle_platform
  require_profile
  for command in curl docker grep pgrep systemctl; do
    require_command "$command"
  done
  if [[ "$operation" != status ]]; then
    acquire_lock
  fi
  case "$operation" in
    install) install_control_plane ;;
    up) up_control_plane ;;
    down) down_control_plane ;;
    restart)
      down_control_plane
      up_control_plane
      printf 'status=pass operation=restart-control-plane\n'
      ;;
    status) status_control_plane ;;
    test-core) test_control_plane core ;;
    test-sdk) test_control_plane sdk ;;
    test-smoke) test_control_plane smoke ;;
  esac
}

main "$@"
