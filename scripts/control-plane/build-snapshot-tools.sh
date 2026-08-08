#!/usr/bin/env bash
set -Eeuo pipefail
readonly SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
# shellcheck source=common.sh
source "$SCRIPT_DIR/common.sh"

readonly BUILDER='docker.io/library/golang:1.26.5-bookworm@sha256:6c5605ab3a9a9fb3c4eafe5b3d63cdbf3881caf113262b67862547b54a9db599'
readonly COPY_SHA256=aaf516f7157c70be3be35b552d94fdf1dbd3b9739a8d03a0c978f96d03c45406
readonly COPY_SIZE=37908606
readonly RESUME_SHA256=d294e961a478f3ffa84ab9d10b10bb8fed723f844c5c49e891e70b7019df2ca9
readonly RESUME_SIZE=62084336
readonly ARTIFACT_ROOT="$KITDEV_DATA_ROOT/artifacts/bin"
readonly RUNTIME_ROOT="$KITDEV_RUNTIME_ROOT/orchestrator"

cleanup() {
  [[ -z "${stage:-}" ]] || rm -rf -- "$stage"
}

verify_tool() {
  local path="$1" owner="$2" group="$3" mode="$4" size="$5" digest="$6"
  [[ ! -L "$path" && -f "$path" ]] || return 1
  [[ "$(stat -c '%u:%g:%a:%s:%h' -- "$path")" == \
    "$(identity_uid "$owner"):$(identity_gid "$group"):$mode:$size:1" ]] || return 1
  [[ "$(sha256sum -- "$path" | awk '{digest=$1} END {print digest}')" == "$digest" ]]
}

verify_publication() {
  verify_tool "$ARTIFACT_ROOT/copy-build-$KITDEV_INFRA_SHORT_COMMIT" \
    root kitdev 750 "$COPY_SIZE" "$COPY_SHA256" &&
    verify_tool "$ARTIFACT_ROOT/resume-build-$KITDEV_INFRA_SHORT_COMMIT" \
      root kitdev 750 "$RESUME_SIZE" "$RESUME_SHA256" &&
    verify_tool "$RUNTIME_ROOT/copy-build" root root 750 "$COPY_SIZE" "$COPY_SHA256" &&
    verify_tool "$RUNTIME_ROOT/resume-build" root root 750 "$RESUME_SIZE" "$RESUME_SHA256"
}

main() {
  local stage='' needs_build=no result=unchanged
  require_root
  require_lifecycle_platform
  require_command docker
  require_command git
  require_command sha256sum
  require_command tar
  require_clean_infra_checkout
  require_exact_directory "$ARTIFACT_ROOT" root kitdev 750
  require_exact_directory "$RUNTIME_ROOT" root root 700

  if [[ -e "$ARTIFACT_ROOT/copy-build-$KITDEV_INFRA_SHORT_COMMIT" ||
    -L "$ARTIFACT_ROOT/copy-build-$KITDEV_INFRA_SHORT_COMMIT" ]]; then
    verify_tool "$ARTIFACT_ROOT/copy-build-$KITDEV_INFRA_SHORT_COMMIT" \
      root kitdev 750 "$COPY_SIZE" "$COPY_SHA256" || control_plane_die copy_build_artifact_conflict 65
  else
    needs_build=yes
  fi
  if [[ -e "$ARTIFACT_ROOT/resume-build-$KITDEV_INFRA_SHORT_COMMIT" ||
    -L "$ARTIFACT_ROOT/resume-build-$KITDEV_INFRA_SHORT_COMMIT" ]]; then
    verify_tool "$ARTIFACT_ROOT/resume-build-$KITDEV_INFRA_SHORT_COMMIT" \
      root kitdev 750 "$RESUME_SIZE" "$RESUME_SHA256" || control_plane_die resume_build_artifact_conflict 65
  else
    needs_build=yes
  fi
  if [[ -e "$RUNTIME_ROOT/copy-build" || -L "$RUNTIME_ROOT/copy-build" ]]; then
    verify_tool "$RUNTIME_ROOT/copy-build" root root 750 "$COPY_SIZE" "$COPY_SHA256" ||
      control_plane_die copy_build_runtime_conflict 65
  fi
  if [[ -e "$RUNTIME_ROOT/resume-build" || -L "$RUNTIME_ROOT/resume-build" ]]; then
    verify_tool "$RUNTIME_ROOT/resume-build" root root 750 "$RESUME_SIZE" "$RESUME_SHA256" ||
      control_plane_die resume_build_runtime_conflict 65
  fi

  if [[ "$needs_build" == yes ]]; then
    result=created
    stage="$(mktemp -d "$KITDEV_DATA_ROOT/build-cache/snapshot-tools.XXXXXXXX")"
    trap cleanup EXIT
    chmod 0700 -- "$stage"
    mkdir -m 0700 -- "$stage/source" "$stage/out" "$stage/go-mod-cache" "$stage/go-build-cache"
    git -C "$KITDEV_INFRA_ROOT" archive --format=tar "$KITDEV_INFRA_COMMIT" \
      --output "$stage/source.tar"
    tar --extract --file "$stage/source.tar" --directory "$stage/source" \
      --no-same-owner --no-same-permissions
    rm -f -- "$stage/source.tar"
    docker run --rm --pull always --platform linux/amd64 \
      --user 0:0 \
      --volume "$stage/source:/src" \
      --volume "$stage/go-mod-cache:/go/pkg/mod" \
      --volume "$stage/go-build-cache:/root/.cache/go-build" \
      --workdir /src \
      "$BUILDER" \
      bash -Eeuo pipefail -c '
        go mod download
        go mod download golang.org/x/term@v0.44.0
      '
    docker run --rm --pull never --platform linux/amd64 --network none \
      --user 0:0 \
      --volume "$stage/source:/src" \
      --volume "$stage/out:/out" \
      --volume "$stage/go-mod-cache:/go/pkg/mod" \
      --volume "$stage/go-build-cache:/root/.cache/go-build" \
      --workdir /src \
      "$BUILDER" \
      bash -Eeuo pipefail -c '
      CGO_ENABLED=0 GOOS=linux GOARCH=amd64 go build \
        -trimpath -buildvcs=false -o /out/copy-build \
        -ldflags "-s -w -buildid=" ./packages/orchestrator/cmd/copy-build
      CGO_ENABLED=1 GOOS=linux GOARCH=amd64 go build \
        -trimpath -buildvcs=false -o /out/resume-build \
        -ldflags "-s -w -buildid=" ./packages/orchestrator/cmd/resume-build
      '
    [[ "$(stat -c %s -- "$stage/out/copy-build")" == "$COPY_SIZE" &&
      "$(sha256sum -- "$stage/out/copy-build" | awk '{digest=$1} END {print digest}')" == "$COPY_SHA256" ]] ||
      control_plane_die copy_build_reproduction_mismatch 65
    [[ "$(stat -c %s -- "$stage/out/resume-build")" == "$RESUME_SIZE" &&
      "$(sha256sum -- "$stage/out/resume-build" | awk '{digest=$1} END {print digest}')" == "$RESUME_SHA256" ]] ||
      control_plane_die resume_build_reproduction_mismatch 65
  fi

  if [[ ! -e "$ARTIFACT_ROOT/copy-build-$KITDEV_INFRA_SHORT_COMMIT" &&
    ! -L "$ARTIFACT_ROOT/copy-build-$KITDEV_INFRA_SHORT_COMMIT" ]]; then
    publish_exact_file "$stage/out/copy-build" \
      "$ARTIFACT_ROOT/copy-build-$KITDEV_INFRA_SHORT_COMMIT" root kitdev 750
  fi
  if [[ ! -e "$ARTIFACT_ROOT/resume-build-$KITDEV_INFRA_SHORT_COMMIT" &&
    ! -L "$ARTIFACT_ROOT/resume-build-$KITDEV_INFRA_SHORT_COMMIT" ]]; then
    publish_exact_file "$stage/out/resume-build" \
      "$ARTIFACT_ROOT/resume-build-$KITDEV_INFRA_SHORT_COMMIT" root kitdev 750
  fi
  if [[ ! -e "$RUNTIME_ROOT/copy-build" && ! -L "$RUNTIME_ROOT/copy-build" ]]; then
    publish_exact_file "$ARTIFACT_ROOT/copy-build-$KITDEV_INFRA_SHORT_COMMIT" \
      "$RUNTIME_ROOT/copy-build" root root 750
    result=created
  fi
  if [[ ! -e "$RUNTIME_ROOT/resume-build" && ! -L "$RUNTIME_ROOT/resume-build" ]]; then
    publish_exact_file "$ARTIFACT_ROOT/resume-build-$KITDEV_INFRA_SHORT_COMMIT" \
      "$RUNTIME_ROOT/resume-build" root root 750
    result=created
  fi
  verify_publication || control_plane_die snapshot_tools_publication_failed 65
  printf 'status=pass operation=build-snapshot-tools result=%s copy_sha256=%s resume_sha256=%s\n' \
    "$result" "$COPY_SHA256" "$RESUME_SHA256"
}

main "$@"
