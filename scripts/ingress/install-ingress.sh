#!/usr/bin/env bash
set -Eeuo pipefail
readonly SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
readonly REPO_ROOT="$(CDPATH= cd -- "$SCRIPT_DIR/../.." && pwd -P)"
# shellcheck source=../control-plane/common.sh
source "$SCRIPT_DIR/../control-plane/common.sh"

readonly INSTALLED_DIR="$KITDEV_OPT_ROOT/libexec/ingress"
readonly COMPOSE_DIR="$KITDEV_OPT_ROOT/compose/ingress"
readonly INGRESS_ETC="$KITDEV_ETC_ROOT/ingress"
readonly UNIT_DIR=/etc/systemd/system

publish_assets() {
  local name
  ensure_directory "$KITDEV_ETC_ROOT" root root 700
  ensure_directory "$KITDEV_OPT_ROOT/libexec" root root 755
  ensure_directory "$KITDEV_OPT_ROOT/libexec/control-plane" root root 755
  ensure_directory "$INSTALLED_DIR" root root 755
  ensure_directory "$KITDEV_OPT_ROOT/compose" root root 755
  ensure_directory "$COMPOSE_DIR" root root 755
  ensure_directory "$INGRESS_ETC" root root 700
  publish_exact_file "$SCRIPT_DIR/../control-plane/common.sh" \
    "$KITDEV_OPT_ROOT/libexec/control-plane/common.sh" root root 755
  for name in acquire-artifacts.sh configure-firewall.sh manage-certificate.sh; do
    publish_exact_file "$SCRIPT_DIR/$name" "$INSTALLED_DIR/$name" root root 755
  done
  for name in ingress_config.py run_lego.py; do
    publish_exact_file "$SCRIPT_DIR/$name" "$INSTALLED_DIR/$name" root root 755
  done
  publish_exact_file "$REPO_ROOT/compose/ingress/compose.yaml" \
    "$COMPOSE_DIR/compose.yaml" root root 644
  publish_exact_file "$REPO_ROOT/config/ingress/nginx.conf" \
    "$INGRESS_ETC/nginx.conf" root root 644
  publish_exact_file "$REPO_ROOT/config/ingress/ingress.env.template" \
    "$INGRESS_ETC/ingress.env.example" root root 600
  publish_exact_file "$REPO_ROOT/config/ingress/acme-provider.env.example" \
    "$INGRESS_ETC/acme-provider.env.example" root root 600
  for name in kitdev-e2b-ingress.service kitdev-e2b-ingress-renew.service \
    kitdev-e2b-ingress-renew.timer; do
    publish_exact_file "$REPO_ROOT/systemd/$name" "$UNIT_DIR/$name" root root 644
  done
}

verify_assets() {
  local name
  require_exact_file "$SCRIPT_DIR/../control-plane/common.sh" \
    "$KITDEV_OPT_ROOT/libexec/control-plane/common.sh" root root 755
  for name in acquire-artifacts.sh configure-firewall.sh manage-certificate.sh; do
    require_exact_file "$SCRIPT_DIR/$name" "$INSTALLED_DIR/$name" root root 755
  done
  for name in ingress_config.py run_lego.py; do
    require_exact_file "$SCRIPT_DIR/$name" "$INSTALLED_DIR/$name" root root 755
  done
  require_exact_file "$REPO_ROOT/compose/ingress/compose.yaml" \
    "$COMPOSE_DIR/compose.yaml" root root 644
  require_exact_file "$REPO_ROOT/config/ingress/nginx.conf" \
    "$INGRESS_ETC/nginx.conf" root root 644
  require_exact_file "$REPO_ROOT/config/ingress/ingress.env.template" \
    "$INGRESS_ETC/ingress.env.example" root root 600
  require_exact_file "$REPO_ROOT/config/ingress/acme-provider.env.example" \
    "$INGRESS_ETC/acme-provider.env.example" root root 600
  for name in kitdev-e2b-ingress.service kitdev-e2b-ingress-renew.service \
    kitdev-e2b-ingress-renew.timer; do
    require_exact_file "$REPO_ROOT/systemd/$name" "$UNIT_DIR/$name" root root 644
  done
}

remove_exact_file() {
  local source="$1" target="$2" owner="$3" group="$4" mode="$5"
  if [[ -e "$target" || -L "$target" ]]; then
    require_exact_file "$source" "$target" "$owner" "$group" "$mode"
    rm -f -- "$target"
  fi
}

remove_assets() {
  local name
  remove_exact_file "$REPO_ROOT/compose/ingress/compose.yaml" "$COMPOSE_DIR/compose.yaml" root root 644
  remove_exact_file "$REPO_ROOT/config/ingress/nginx.conf" "$INGRESS_ETC/nginx.conf" root root 644
  remove_exact_file "$REPO_ROOT/config/ingress/ingress.env.template" \
    "$INGRESS_ETC/ingress.env.example" root root 600
  remove_exact_file "$REPO_ROOT/config/ingress/acme-provider.env.example" \
    "$INGRESS_ETC/acme-provider.env.example" root root 600
  for name in acquire-artifacts.sh configure-firewall.sh manage-certificate.sh; do
    remove_exact_file "$SCRIPT_DIR/$name" "$INSTALLED_DIR/$name" root root 755
  done
  for name in ingress_config.py run_lego.py; do
    remove_exact_file "$SCRIPT_DIR/$name" "$INSTALLED_DIR/$name" root root 755
  done
  for name in kitdev-e2b-ingress.service kitdev-e2b-ingress-renew.service \
    kitdev-e2b-ingress-renew.timer; do
    remove_exact_file "$REPO_ROOT/systemd/$name" "$UNIT_DIR/$name" root root 644
  done
  rmdir --ignore-fail-on-non-empty "$INSTALLED_DIR" "$COMPOSE_DIR" 2>/dev/null || true
}

main() {
  local mode="${1:-}" had_firewall=no
  case "$mode" in stage|apply|verify|remove) ;;
    *) control_plane_die invalid_operation 64 ;;
  esac
  require_root
  require_lifecycle_platform
  require_command docker
  require_command systemctl
  if [[ "$mode" == remove ]]; then
    verify_assets
    systemctl disable --now kitdev-e2b-ingress-renew.timer kitdev-e2b-ingress.service 2>/dev/null || true
    "$SCRIPT_DIR/configure-firewall.sh" remove
    remove_assets
    systemctl daemon-reload
    printf 'status=pass operation=remove-ingress\n'
    return
  fi
  if [[ "$mode" == verify ]]; then
    verify_assets
    "$SCRIPT_DIR/acquire-artifacts.sh" verify
  else
    publish_assets
    systemctl daemon-reload
    "$SCRIPT_DIR/acquire-artifacts.sh" apply
  fi
  if [[ "$mode" == stage ]]; then
    printf 'status=pass operation=stage-ingress provider-input-required=yes\n'
    return
  fi
  /usr/bin/python3 -I -B -S "$SCRIPT_DIR/ingress_config.py" verify >/dev/null
  "$SCRIPT_DIR/manage-certificate.sh" verify >/dev/null
  verify_assets
  docker compose --file "$REPO_ROOT/compose/ingress/compose.yaml" config --quiet
  if [[ "$mode" == apply ]]; then
    if "$SCRIPT_DIR/configure-firewall.sh" verify >/dev/null 2>&1; then had_firewall=yes; fi
    trap 'if [[ "$had_firewall" == no ]]; then "$SCRIPT_DIR/configure-firewall.sh" remove >/dev/null || true; fi' ERR
    "$SCRIPT_DIR/configure-firewall.sh" apply >/dev/null
    systemctl enable --now kitdev-e2b-ingress.service kitdev-e2b-ingress-renew.timer
    trap - ERR
  else
    "$SCRIPT_DIR/configure-firewall.sh" verify >/dev/null
  fi
  systemctl is-active --quiet kitdev-e2b-ingress.service
  systemctl is-enabled --quiet kitdev-e2b-ingress.service
  systemctl is-active --quiet kitdev-e2b-ingress-renew.timer
  systemctl is-enabled --quiet kitdev-e2b-ingress-renew.timer
  [[ "$(docker inspect --format '{{.State.Running}} {{.State.Health.Status}} {{index .Config.Labels \"com.docker.compose.project\"}} {{index .Config.Labels \"com.docker.compose.service\"}}' kitdev-ingress)" == \
    'true healthy kitdev-ingress ingress' ]] || control_plane_die ingress_container_invalid 65
  "$SCRIPT_DIR/configure-firewall.sh" verify >/dev/null
  printf 'status=pass operation=%s-ingress\n' "$mode"
}

main "$@"
