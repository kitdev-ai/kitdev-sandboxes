#!/usr/bin/env bash
set -Eeuo pipefail
readonly SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
# shellcheck source=common.sh
source "$SCRIPT_DIR/common.sh"

readonly BUILDER='docker.io/library/golang:1.26.5-bookworm@sha256:6c5605ab3a9a9fb3c4eafe5b3d63cdbf3881caf113262b67862547b54a9db599'
readonly ENVD_SHA256=530d84dfbfd82c05181e0dc61ca842f3caaa349b0cc2f3f52d2d8eb9478aa67e
readonly ENVD_SIZE=12927102
readonly ARTIFACT_PATH="$KITDEV_DATA_ROOT/artifacts/bin/envd-$KITDEV_INFRA_SHORT_COMMIT"
readonly RUNTIME_PATH="$KITDEV_RUNTIME_ROOT/envd/envd"

cleanup() {
  [[ -z "${stage:-}" ]] || rm -rf -- "$stage"
}

verify_envd() {
  local path="$1"
  [[ ! -L "$path" && -f "$path" ]] || return 1
  [[ "$(stat -c '%u:%g:%a:%s:%h' -- "$path")" == \
    "0:$(identity_gid kitdev):750:$ENVD_SIZE:1" ]] || return 1
  [[ "$(sha256sum -- "$path" | awk '{digest=$1} END {print digest}')" == "$ENVD_SHA256" ]]
}

main() {
  local stage=''
  require_root
  require_lifecycle_platform
  require_command docker
  require_command git
  require_command sha256sum
  require_command tar
  require_clean_infra_checkout
  require_exact_directory "$KITDEV_DATA_ROOT/artifacts/bin" root kitdev 750
  require_exact_directory "$KITDEV_RUNTIME_ROOT/envd" root kitdev 750

  if [[ -e "$ARTIFACT_PATH" || -L "$ARTIFACT_PATH" ]]; then
    verify_envd "$ARTIFACT_PATH" || control_plane_die envd_artifact_conflict 65
    if [[ ! -e "$RUNTIME_PATH" && ! -L "$RUNTIME_PATH" ]]; then
      publish_exact_file "$ARTIFACT_PATH" "$RUNTIME_PATH" root kitdev 750
    fi
    verify_envd "$RUNTIME_PATH" || control_plane_die envd_runtime_conflict 65
    printf 'status=pass operation=build-envd result=unchanged sha256=%s\n' "$ENVD_SHA256"
    return 0
  fi
  [[ ! -e "$RUNTIME_PATH" && ! -L "$RUNTIME_PATH" ]] ||
    control_plane_die envd_partial_state_conflict 65

  stage="$(mktemp -d "$KITDEV_DATA_ROOT/build-cache/envd.XXXXXXXX")"
  trap cleanup EXIT
  chmod 0700 -- "$stage"
  mkdir -m 0700 -- "$stage/source"
  git -C "$KITDEV_INFRA_ROOT" archive --format=tar "$KITDEV_INFRA_COMMIT" \
    --output "$stage/source.tar"
  tar --extract --file "$stage/source.tar" --directory "$stage/source" \
    --no-same-owner --no-same-permissions
  rm -f -- "$stage/source.tar"
  docker run --rm --pull always --platform linux/amd64 \
    --user 0:0 \
    --env CGO_ENABLED=0 --env GOOS=linux --env GOARCH=amd64 \
    --volume "$stage/source:/src" \
    --volume "$stage:/out" \
    --workdir /src/packages/envd \
    "$BUILDER" \
    go build -trimpath -buildvcs=false -a -o /out/envd \
      -ldflags "-X=main.commitSHA=$KITDEV_INFRA_SHORT_COMMIT -s -w -buildid=" .
  [[ ! -L "$stage/envd" && -f "$stage/envd" ]] || control_plane_die envd_build_missing 65
  [[ "$(stat -c %s -- "$stage/envd")" == "$ENVD_SIZE" ]] ||
    control_plane_die envd_build_size_mismatch 65
  [[ "$(sha256sum -- "$stage/envd" | awk '{digest=$1} END {print digest}')" == "$ENVD_SHA256" ]] ||
    control_plane_die envd_build_hash_mismatch 65
  publish_exact_file "$stage/envd" "$ARTIFACT_PATH" root kitdev 750
  publish_exact_file "$stage/envd" "$RUNTIME_PATH" root kitdev 750
  verify_envd "$ARTIFACT_PATH" && verify_envd "$RUNTIME_PATH" ||
    control_plane_die envd_publication_failed 65
  printf 'status=pass operation=build-envd result=created sha256=%s\n' "$ENVD_SHA256"
}

main "$@"
