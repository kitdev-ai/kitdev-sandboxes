#!/usr/bin/env bash
set -Eeuo pipefail
readonly SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
# shellcheck source=../control-plane/common.sh
source "$SCRIPT_DIR/../control-plane/common.sh"

readonly LEGO_VERSION=5.3.1
readonly LEGO_ARCHIVE=lego_v5.3.1_linux_amd64.tar.gz
readonly LEGO_URL=https://github.com/go-acme/lego/releases/download/v5.3.1/lego_v5.3.1_linux_amd64.tar.gz
readonly LEGO_SHA256=b3c71b122ee1947eacfe0b809b955647f6377239fe4bfc49f73b1a091ae1252a
readonly LEGO_SIZE=21110571
readonly NGINX_IMAGE=docker.io/library/nginx:1.29.6-alpine3.23@sha256:08fe94b0d1e72fc687840f5696f6e107a85c327b1bcb8a7acc22f8c100227c67

main() {
  local mode="${1:-}" stage observed
  case "$mode" in apply|verify) ;;
    *) control_plane_die invalid_operation 64 ;;
  esac
  require_root
  require_lifecycle_platform
  for command in curl docker sha256sum stat tar; do require_command "$command"; done
  ensure_directory "$KITDEV_OPT_ROOT/bin" root root 755

  if [[ "$mode" == apply && ! -e "$KITDEV_OPT_ROOT/bin/lego" ]]; then
    stage="$(mktemp -d /run/kitdev-sandboxes/ingress-artifacts.XXXXXXXX)"
    trap 'rm -rf -- "${stage:-}"' EXIT
    curl --fail --silent --show-error --location --proto '=https' --tlsv1.2 \
      --output "$stage/$LEGO_ARCHIVE" "$LEGO_URL"
    [[ "$(stat -c %s -- "$stage/$LEGO_ARCHIVE")" == "$LEGO_SIZE" ]] ||
      control_plane_die lego_archive_size_mismatch 65
    printf '%s  %s\n' "$LEGO_SHA256" "$stage/$LEGO_ARCHIVE" | sha256sum --check --status - ||
      control_plane_die lego_archive_hash_mismatch 65
    tar --extract --gzip --file "$stage/$LEGO_ARCHIVE" --directory "$stage" --no-same-owner lego
    [[ "$("$stage/lego" --version)" == "lego version $LEGO_VERSION linux/amd64" ]] ||
      control_plane_die lego_version_mismatch 65
    publish_exact_file "$stage/lego" "$KITDEV_OPT_ROOT/bin/lego" root root 755
  fi

  [[ ! -L "$KITDEV_OPT_ROOT/bin/lego" && -f "$KITDEV_OPT_ROOT/bin/lego" ]] ||
    control_plane_die lego_binary_missing 65
  [[ "$("$KITDEV_OPT_ROOT/bin/lego" --version)" == "lego version $LEGO_VERSION linux/amd64" ]] ||
    control_plane_die lego_version_mismatch 65

  if [[ "$mode" == apply ]]; then
    docker image inspect "$NGINX_IMAGE" >/dev/null 2>&1 ||
      docker pull --platform linux/amd64 "$NGINX_IMAGE" >/dev/null
  fi
  observed="$(docker image inspect --format '{{.Os}}/{{.Architecture}}' "$NGINX_IMAGE" 2>/dev/null)" ||
    control_plane_die nginx_image_missing 65
  [[ "$observed" == linux/amd64 ]] || control_plane_die nginx_image_platform_mismatch 65
  printf 'status=pass operation=%s-ingress-artifacts\n' "$mode"
}

main "$@"
