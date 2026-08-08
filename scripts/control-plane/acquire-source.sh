#!/usr/bin/env bash
set -Eeuo pipefail
readonly SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
# shellcheck source=common.sh
source "$SCRIPT_DIR/common.sh"

cleanup() {
  [[ -z "${temporary:-}" ]] || rm -rf -- "$temporary"
}

main() {
  local temporary=''
  require_root
  require_lifecycle_platform
  require_command git
  require_exact_directory "$KITDEV_OPT_ROOT" root root 755
  require_exact_directory "$KITDEV_OPT_ROOT/src" root root 755
  if [[ -e "$KITDEV_INFRA_ROOT" || -L "$KITDEV_INFRA_ROOT" ]]; then
    require_clean_infra_checkout
    printf 'status=pass operation=acquire-source result=unchanged commit=%s\n' "$KITDEV_INFRA_COMMIT"
    return 0
  fi

  temporary="$(mktemp -d "$KITDEV_OPT_ROOT/src/.e2b-infra.XXXXXXXX")"
  trap cleanup EXIT
  chmod 0700 -- "$temporary"
  env -i \
    PATH="$PATH" LC_ALL=C LANG=C HOME=/nonexistent GIT_CONFIG_NOSYSTEM=1 \
    git -c core.hooksPath=/dev/null -c protocol.file.allow=never \
      clone --no-checkout --filter=blob:none -- \
      https://github.com/e2b-dev/infra.git "$temporary"
  env -i \
    PATH="$PATH" LC_ALL=C LANG=C HOME=/nonexistent GIT_CONFIG_NOSYSTEM=1 \
    git -C "$temporary" -c core.hooksPath=/dev/null -c protocol.file.allow=never \
      checkout --detach "$KITDEV_INFRA_COMMIT"
  chmod -R go-w -- "$temporary"
  git -C "$temporary" fsck --strict
  [[ "$(git -C "$temporary" rev-parse HEAD)" == "$KITDEV_INFRA_COMMIT" ]] ||
    control_plane_die infra_commit_mismatch 65
  [[ -z "$(git -C "$temporary" status --porcelain=v1 --untracked-files=all)" ]] ||
    control_plane_die infra_checkout_dirty 65
  [[ ! -e "$KITDEV_INFRA_ROOT" && ! -L "$KITDEV_INFRA_ROOT" ]] ||
    control_plane_die infra_checkout_concurrent_creation 65
  mv -T -- "$temporary" "$KITDEV_INFRA_ROOT"
  temporary=''
  require_clean_infra_checkout
  printf 'status=pass operation=acquire-source result=created commit=%s\n' "$KITDEV_INFRA_COMMIT"
}

main "$@"
