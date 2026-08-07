#!/usr/bin/env bash
set -Eeuo pipefail
readonly SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
# shellcheck source=common.sh
source "$SCRIPT_DIR/common.sh"

readonly BUILDER_INDEX=sha256:6c5605ab3a9a9fb3c4eafe5b3d63cdbf3881caf113262b67862547b54a9db599
readonly DESTINATION="$KITDEV_RUNTIME_ROOT/orchestrator"
if [[ -f "$SCRIPT_DIR/../../patches/e2b-infra/882a3b4-host-admission.patch" ]]; then
  readonly ADMISSION_PATCH="$SCRIPT_DIR/../../patches/e2b-infra/882a3b4-host-admission.patch"
else
  readonly ADMISSION_PATCH="$SCRIPT_DIR/882a3b4-host-admission.patch"
fi

cleanup() {
  [[ -z "${stage:-}" ]] || rm -rf -- "$stage"
}

main() {
  local stage='' dockerfile ldd_output orchestrator_hash cleaner_hash manifest patch_hash
  require_root
  require_lifecycle_platform
  require_command docker
  require_command git
  require_command ldd
  require_command sha256sum
  require_clean_infra_checkout
  require_exact_directory "$DESTINATION" root root 700
  [[ ! -L "$ADMISSION_PATCH" && -f "$ADMISSION_PATCH" ]] ||
    control_plane_die admission_patch_invalid 65

  stage="$(mktemp -d "$KITDEV_DATA_ROOT/build-cache/orchestrator.XXXXXXXX")"
  trap cleanup EXIT
  chmod 0700 -- "$stage"
  mkdir -m 0700 -- "$stage/context"
  cp --archive --reflink=auto -- "$KITDEV_INFRA_ROOT/packages/." "$stage/context/"
  git -C "$stage/context" apply --no-index --check -- "$ADMISSION_PATCH"
  git -C "$stage/context" apply --no-index -- "$ADMISSION_PATCH"
  patch_hash="$(sha256sum -- "$ADMISSION_PATCH" | awk '{print $1}')"
  [[ "$patch_hash" =~ ^[0-9a-f]{64}$ ]] || control_plane_die admission_patch_hash_invalid 65
  dockerfile="$stage/orchestrator.Dockerfile"
  /usr/bin/python3 -I -B -S - "$stage/context/orchestrator/Dockerfile" \
    "$dockerfile" "$BUILDER_INDEX" <<'PY_ORCHESTRATOR_DOCKERFILE'
import sys
from pathlib import Path

source_path, target_path = map(Path, sys.argv[1:3])
digest = sys.argv[3]
source = source_path.read_text(encoding="utf-8")
expected = "FROM golang:${GOLANG_VERSION}-${DEBIAN_VERSION} AS builder"
if source.count(expected) != 1:
    raise SystemExit(1)
source = source.replace(expected, f"FROM docker.io/library/golang:1.26.5-bookworm@{digest} AS builder")
target_path.write_text(source, encoding="utf-8")
PY_ORCHESTRATOR_DOCKERFILE
  docker buildx build --pull --platform linux/amd64 \
    --file "$dockerfile" \
    --build-arg "COMMIT_SHA=$KITDEV_INFRA_SHORT_COMMIT" \
    --output "type=local,dest=$stage/out" \
    "$stage/context"
  for binary in orchestrator clean-nfs-cache; do
    [[ ! -L "$stage/out/$binary" && -x "$stage/out/$binary" ]] ||
      control_plane_die orchestrator_build_output_missing 65
  done
  ldd_output="$(ldd -- "$stage/out/orchestrator")" || control_plane_die orchestrator_ldd_failed 65
  [[ "$ldd_output" != *'not found'* ]] || control_plane_die orchestrator_dependency_missing 65
  orchestrator_hash="$(sha256sum -- "$stage/out/orchestrator" | awk '{digest=$1} END {print digest}')"
  cleaner_hash="$(sha256sum -- "$stage/out/clean-nfs-cache" | awk '{digest=$1} END {print digest}')"
  [[ "$orchestrator_hash" =~ ^[0-9a-f]{64}$ && "$cleaner_hash" =~ ^[0-9a-f]{64}$ ]] ||
    control_plane_die orchestrator_hash_invalid 65
  publish_exact_file "$stage/out/orchestrator" "$DESTINATION/orchestrator" root root 755
  publish_exact_file "$stage/out/clean-nfs-cache" "$DESTINATION/clean-nfs-cache" root root 755

  manifest="$stage/orchestrator-build.json"
  /usr/bin/python3 -I -B -S - "$manifest" "$orchestrator_hash" "$cleaner_hash" "$patch_hash" \
    "$(stat -c %s -- "$stage/out/orchestrator")" \
    "$(stat -c %s -- "$stage/out/clean-nfs-cache")" <<'PY_ORCHESTRATOR_MANIFEST'
import json
import sys
from pathlib import Path

path = Path(sys.argv[1])
document = {
    "schema_version": 2,
    "source_commit": "882a3b4786755db9e94be3297de6827f9100ce5e",
    "platform": "linux/amd64",
    "builder": "docker.io/library/golang:1.26.5-bookworm@sha256:6c5605ab3a9a9fb3c4eafe5b3d63cdbf3881caf113262b67862547b54a9db599",
    "host_admission": {
        "patch_sha256": sys.argv[4],
        "max_live_sandboxes": 1,
        "max_concurrent_starts": 1,
        "max_concurrent_builds": 1,
        "max_vcpu": 2,
        "max_ram_mb": 8192,
        "max_disk_mb": 25600,
    },
    "artifacts": {
        "orchestrator": {"sha256": sys.argv[2], "size_bytes": int(sys.argv[5])},
        "clean-nfs-cache": {"sha256": sys.argv[3], "size_bytes": int(sys.argv[6])},
    },
}
path.write_text(json.dumps(document, sort_keys=True, separators=(",", ":")) + "\n", encoding="ascii")
PY_ORCHESTRATOR_MANIFEST
  publish_exact_file "$manifest" "$DESTINATION/build-manifest.json" root root 600
  printf 'status=pass operation=build-orchestrator orchestrator_sha256=%s cleaner_sha256=%s\n' \
    "$orchestrator_hash" "$cleaner_hash"
}

main "$@"
