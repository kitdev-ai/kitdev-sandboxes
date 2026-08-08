#!/usr/bin/env bash
set -Eeuo pipefail
readonly SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
# shellcheck source=../control-plane/common.sh
source "$SCRIPT_DIR/../control-plane/common.sh"

verify_certificate() {
  local certificate="$1" key="$2" domain="$3" cert_public key_public
  [[ ! -L "$certificate" && -f "$certificate" && ! -L "$key" && -f "$key" ]] || return 1
  openssl x509 -in "$certificate" -noout -checkend 2592000 >/dev/null || return 1
  openssl x509 -in "$certificate" -noout -ext subjectAltName | tr ',' '\n' |
    sed -e 's/^[[:space:]]*//' | grep -Fx "DNS:*.$domain" >/dev/null || return 1
  cert_public="$(openssl x509 -in "$certificate" -pubkey -noout | sha256sum | cut -d ' ' -f1)" || return 1
  key_public="$(openssl pkey -in "$key" -pubout 2>/dev/null | sha256sum | cut -d ' ' -f1)" || return 1
  [[ "$cert_public" == "$key_public" ]]
}

main() {
  local mode="${1:-}" values domain email provider server state certificate key tls_dir temporary
  case "$mode" in issue-staging|issue|renew|verify) ;;
    *) control_plane_die invalid_operation 64 ;;
  esac
  require_root
  require_lifecycle_platform
  for command in cut docker grep openssl sed sha256sum tr; do require_command "$command"; done
  mapfile -t values < <(/usr/bin/python3 -I -B -S "$SCRIPT_DIR/ingress_config.py" get)
  [[ "${#values[@]}" == 4 ]] || control_plane_die ingress_config_invalid 65
  domain="${values[0]}"; email="${values[1]}"; provider="${values[2]}"; server="${values[3]}"
  tls_dir="$KITDEV_ETC_ROOT/ingress/tls"

  if [[ "$mode" == verify ]]; then
    require_exact_directory "$tls_dir" root root 700
    verify_certificate "$tls_dir/wildcard.$domain.crt" "$tls_dir/wildcard.$domain.key" "$domain" ||
      control_plane_die ingress_certificate_invalid 65
    [[ "$(stat -c '%u:%g:%a:%h' -- "$tls_dir/wildcard.$domain.crt")" == 0:0:644:1 ]] ||
      control_plane_die ingress_certificate_metadata_invalid 65
    [[ "$(stat -c '%u:%g:%a:%h' -- "$tls_dir/wildcard.$domain.key")" == 0:0:600:1 ]] ||
      control_plane_die ingress_key_metadata_invalid 65
    printf 'status=pass operation=verify-ingress-certificate\n'
    return
  fi

  "$SCRIPT_DIR/acquire-artifacts.sh" verify >/dev/null
  if [[ "$mode" == issue-staging ]]; then
    [[ "$server" == https://acme-staging-v02.api.letsencrypt.org/directory ]] ||
      control_plane_die staging_acme_server_required 65
    state="$KITDEV_STATE_ROOT/acme-staging"
  else
    [[ "$server" == https://acme-v02.api.letsencrypt.org/directory ]] ||
      control_plane_die production_acme_server_required 65
    state="$KITDEV_STATE_ROOT/acme"
  fi
  ensure_directory "$state" root root 700
  /usr/bin/python3 -I -B -S "$SCRIPT_DIR/run_lego.py" \
    "$provider" "$email" "$server" "$state" "$domain" \
    "$([[ "$mode" == renew ]] && printf renew || printf run)"
  certificate="$state/certificates/_.$domain.crt"
  key="$state/certificates/_.$domain.key"
  verify_certificate "$certificate" "$key" "$domain" ||
    control_plane_die issued_certificate_invalid 65

  if [[ "$mode" == issue-staging ]]; then
    printf 'status=pass operation=issue-staging-certificate\n'
    return
  fi
  ensure_directory "$tls_dir" root root 700
  temporary="$(mktemp "$tls_dir/.wildcard.$domain.XXXXXXXX")"
  install -o root -g root -m 644 -- "$certificate" "$temporary"
  mv -f -- "$temporary" "$tls_dir/wildcard.$domain.crt"
  temporary="$(mktemp "$tls_dir/.wildcard.$domain.XXXXXXXX")"
  install -o root -g root -m 600 -- "$key" "$temporary"
  mv -f -- "$temporary" "$tls_dir/wildcard.$domain.key"
  sync -f -- "$tls_dir"
  verify_certificate "$tls_dir/wildcard.$domain.crt" "$tls_dir/wildcard.$domain.key" "$domain" ||
    control_plane_die installed_certificate_invalid 65
  if [[ "$(docker inspect --format '{{.State.Running}} {{index .Config.Labels "com.docker.compose.project"}} {{index .Config.Labels "com.docker.compose.service"}}' kitdev-ingress 2>/dev/null || true)" == \
    'true kitdev-ingress ingress' ]]; then
    docker kill --signal HUP kitdev-ingress >/dev/null
  fi
  printf 'status=pass operation=%s-certificate\n' "$mode"
}

main "$@"
