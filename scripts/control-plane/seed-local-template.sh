#!/usr/bin/env bash
set -Eeuo pipefail
readonly SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
# shellcheck source=common.sh
source "$SCRIPT_DIR/common.sh"
umask 077

readonly BUILD_ID=2d9a8389-f5f5-4449-b0eb-e1d364ee98ae
readonly FROM_BUILD_ID=6dfbb2b8-62a2-4a2b-a62a-cf94ffcdb5e5
readonly SOURCE_STORAGE="$KITDEV_RUNTIME_ROOT/local-build-smoke"
readonly DESTINATION_STORAGE="$KITDEV_RUNTIME_ROOT/orchestrator/template-storage"
readonly COPY_BUILD="$KITDEV_RUNTIME_ROOT/orchestrator/copy-build"
readonly COPY_SHA256=aaf516f7157c70be3be35b552d94fdf1dbd3b9739a8d03a0c978f96d03c45406

cleanup() {
  [[ -z "${stage:-}" ]] || rm -rf -- "$stage"
}

psql_query() {
  docker exec --interactive "$postgres_container" \
    psql --no-psqlrc --set=ON_ERROR_STOP=1 --tuples-only --no-align \
      --username kitdev --dbname kitdev "$@"
}

verify_database() {
  local assertion
  assertion="$(psql_query --command "
SELECT CASE WHEN
  (SELECT count(*) FROM teams WHERE id = '$team_id'::uuid AND slug = 'local-dev-team' AND tier = 'base_v1' AND is_blocked = FALSE AND is_banned = FALSE) = 1
  AND (SELECT count(*) FROM env_builds WHERE id = '$BUILD_ID'::uuid AND team_id = '$team_id'::uuid AND status = 'uploaded' AND status_group = 'ready' AND ram_mb = 1024 AND vcpu = 2 AND free_disk_size_mb = 1024 AND total_disk_size_mb = 3722 AND kernel_version = 'vmlinux-6.1.158' AND firecracker_version = 'v1.14.1_431f1fc' AND envd_version = '0.6.13') = 1
  AND (SELECT count(*) FROM env_build_assignments WHERE build_id = '$BUILD_ID'::uuid) = 1
  AND (SELECT count(*) FROM env_build_assignments a JOIN env_builds b ON b.id = a.build_id AND b.env_id = a.env_id JOIN envs e ON e.id = b.env_id WHERE a.build_id = '$BUILD_ID'::uuid AND a.tag = 'default' AND b.team_id = '$team_id'::uuid AND e.team_id = '$team_id'::uuid AND e.public = FALSE AND e.source = 'template' AND e.deleted_at IS NULL) = 1
THEN 'pass' ELSE 'fail' END;")" || return 1
  [[ "$assertion" == pass ]]
}

verify_artifacts() {
  local root="$1" mode="$2"
  /usr/bin/python3 -I -B -S - "$root" \
    "$BUILD_ID" "$FROM_BUILD_ID" "$(identity_gid kitdev)" "$mode" <<'PY_VERIFY_TEMPLATE'
import hashlib
import json
import os
import stat
import sys
from pathlib import Path

root = Path(sys.argv[1])
build_id, from_build_id = sys.argv[2:4]
kitdev_gid = int(sys.argv[4])
mode = sys.argv[5]
if mode not in {"source", "complete", "compatible"}:
    raise SystemExit(1)
directory_mode = 0o2755 if mode == "source" else 0o2700
parent_metadata = os.lstat(root.parent)
if (
    not stat.S_ISDIR(parent_metadata.st_mode)
    or stat.S_ISLNK(parent_metadata.st_mode)
    or parent_metadata.st_uid != 0
    or parent_metadata.st_gid != kitdev_gid
    or stat.S_IMODE(parent_metadata.st_mode) != directory_mode
):
    raise SystemExit(1)
if mode == "source":
    storage_metadata = os.lstat(root.parent.parent)
    if (
        not stat.S_ISDIR(storage_metadata.st_mode)
        or stat.S_ISLNK(storage_metadata.st_mode)
        or storage_metadata.st_uid != 0
        or storage_metadata.st_gid != kitdev_gid
        or stat.S_IMODE(storage_metadata.st_mode) != 0o2755
    ):
        raise SystemExit(1)
expected = {
    "memfile": (169_869_312, "6d885842a01e5edce27a8d2072eaafc9177b28e7fae41de6b989015ec9206081"),
    "memfile.header": (344, "26ce713cd4203889c60a03341b0e7772821c6b60c65c5a5feb96ca44d0952ff7"),
    "metadata.json": (1_337, "8e1c4f750700d3c15333ef1898659cde5f56e96ab563b40e24bf14cf6337c90e"),
    "rootfs.ext4": (5_992_448, "b06ca653990f1dc842bbb3488957878aff9e4d2e55d67d6db7f4343908ca6e4e"),
    "rootfs.ext4.header": (47_344, "81664b9101c98b99ac41f79193a71c701383a37263c1602ea9470d9c15492bd6"),
    "snapfile": (30_080, "2f59dcc9a0c9eae469faf1233c27f66cca66f3962080daf0d4bddac1bb60834e"),
}
if mode == "compatible" and not root.exists():
    root_metadata = None
else:
    root_metadata = os.lstat(root)
if (
    root_metadata is not None
    and (
        not stat.S_ISDIR(root_metadata.st_mode)
        or stat.S_ISLNK(root_metadata.st_mode)
        or root_metadata.st_uid != 0
        or root_metadata.st_gid != kitdev_gid
        or stat.S_IMODE(root_metadata.st_mode) != directory_mode
        or root_metadata.st_nlink != 2
    )
):
    raise SystemExit(1)
observed = set() if root_metadata is None else {item.name for item in root.iterdir()}
if root_metadata is not None and observed != set(expected):
    raise SystemExit(1)
for name, (size, expected_hash) in expected.items():
    if root_metadata is None:
        continue
    metadata = os.lstat(root / name)
    if (
        not stat.S_ISREG(metadata.st_mode)
        or stat.S_ISLNK(metadata.st_mode)
        or metadata.st_uid != 0
        or metadata.st_gid != kitdev_gid
        or stat.S_IMODE(metadata.st_mode) != 0o644
        or metadata.st_nlink != 1
        or metadata.st_size != size
    ):
        raise SystemExit(1)
    digest = hashlib.sha256()
    with (root / name).open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    if digest.hexdigest() != expected_hash:
        raise SystemExit(1)
if "metadata.json" in observed:
    document = json.loads((root / "metadata.json").read_text(encoding="ascii"))
    if (
        document.get("version") != 2
        or document.get("template", {}).get("build_id") != build_id
        or document.get("template", {}).get("kernel_version") != "vmlinux-6.1.158"
        or document.get("template", {}).get("firecracker_version") != "v1.14.1_431f1fc"
        or document.get("from_template", {}).get("build_id") != from_build_id
        or document.get("context", {}).get("user") != "user"
    ):
        raise SystemExit(1)

ancestors = {
    "d757f43f-4871-4828-9d16-a54da5291f00": (2_531_328, "eab0cb327228384ec58ca3e087e3f6df2c605d623c23e22e0cc9610a6e5e8b9c"),
    "6dfbb2b8-62a2-4a2b-a62a-cf94ffcdb5e5": (6_467_584, "3fb9e84587adb78c0fcbe6a4dd41e7d402eb68e45c7279627c52839ab159977b"),
    "5f25449a-464b-4e10-83ba-e021db8b9b8e": (16_023_552, "ec9c4ac7e1cd01eeacec3e50597e7bf7de09a92fd038a7fed1530e7796497add"),
    "b2e8d4fb-f5ea-4e24-aec8-9af4fbe77c50": (1_462_272, "e206cf1e356ea1a0eb36718f24503bd34c583f6eaf1a0b4a90c98b0f14aa2996"),
    "6be65ea2-c917-43f5-8a56-fc8daa66fca4": (1_423_519_744, "155b8acd5a6318136884acae6777364ddc3c687986283da05b70851686356baa"),
}
for ancestor, (size, expected_hash) in ancestors.items():
    ancestor_root = root.parent / ancestor
    if mode == "compatible" and not ancestor_root.exists():
        continue
    directory = os.lstat(ancestor_root)
    if (
        not stat.S_ISDIR(directory.st_mode)
        or stat.S_ISLNK(directory.st_mode)
        or directory.st_uid != 0
        or directory.st_gid != kitdev_gid
        or stat.S_IMODE(directory.st_mode) != directory_mode
        or directory.st_nlink != 2
    ):
        raise SystemExit(1)
    ancestor_entries = {item.name for item in ancestor_root.iterdir()}
    if mode == "source":
        if "rootfs.ext4" not in ancestor_entries:
            raise SystemExit(1)
    elif mode == "compatible":
        if ancestor_entries != {"rootfs.ext4"}:
            raise SystemExit(1)
    elif ancestor_entries != {"rootfs.ext4"}:
        raise SystemExit(1)
    path = ancestor_root / "rootfs.ext4"
    metadata = os.lstat(path)
    if (
        not stat.S_ISREG(metadata.st_mode)
        or stat.S_ISLNK(metadata.st_mode)
        or metadata.st_uid != 0
        or metadata.st_gid != kitdev_gid
        or stat.S_IMODE(metadata.st_mode) != 0o644
        or metadata.st_nlink != 1
        or metadata.st_size != size
    ):
        raise SystemExit(1)
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    if digest.hexdigest() != expected_hash:
        raise SystemExit(1)
PY_VERIFY_TEMPLATE
}

publish_artifacts() {
  local staged_storage="$1"

  verify_artifacts "$DESTINATION_STORAGE/templates/$BUILD_ID" compatible ||
    control_plane_die destination_template_conflict 65
  /usr/bin/python3 -I -B -S "$SCRIPT_DIR/publish-template-dirs.py" \
    "$staged_storage/templates" "$DESTINATION_STORAGE/templates" >/dev/null ||
    control_plane_die destination_publish_failed 65
  verify_artifacts "$DESTINATION_STORAGE/templates/$BUILD_ID" complete ||
    control_plane_die published_template_invalid 65
}

main() {
  local stage='' staged_storage team_rows existing adc template_id
  require_root
  require_lifecycle_platform
  [[ "$KITDEV_LIFECYCLE" != production ]] || control_plane_die e2e_seed_not_for_production 68
  require_command docker
  require_command rsync
  require_command sha256sum
  [[ ! -L "$COPY_BUILD" && -f "$COPY_BUILD" ]] || control_plane_die copy_build_missing 65
  [[ "$(stat -c '%u:%g:%a:%s:%h' -- "$COPY_BUILD")" == '0:0:750:37908606:1' ]] ||
    control_plane_die copy_build_metadata_mismatch 65
  [[ "$(sha256sum -- "$COPY_BUILD" | awk '{digest=$1} END {print digest}')" == "$COPY_SHA256" ]] ||
    control_plane_die copy_build_hash_mismatch 65
  [[ ! -L "$SOURCE_STORAGE" && -d "$SOURCE_STORAGE/templates/$BUILD_ID" ]] ||
    control_plane_die source_template_missing 65
  require_exact_directory "$DESTINATION_STORAGE" root root 700
  require_exact_directory "$DESTINATION_STORAGE/templates" root kitdev 2700
  verify_artifacts "$SOURCE_STORAGE/templates/$BUILD_ID" source ||
    control_plane_die source_template_invalid 65

  postgres_container="$(docker ps --quiet \
    --filter label=com.docker.compose.project=kitdev-control-plane \
    --filter label=com.docker.compose.service=postgres)"
  [[ "$postgres_container" =~ ^[0-9a-f]{64}$ ]] || control_plane_die postgres_container_invalid 65
  [[ "$(docker inspect --format '{{.State.Status}} {{.State.Health.Status}}' -- "$postgres_container")" == 'running healthy' ]] ||
    control_plane_die postgres_container_unhealthy 65
  team_rows="$(psql_query --command "SELECT id FROM teams WHERE slug = 'local-dev-team' AND tier = 'base_v1' AND is_blocked = FALSE AND is_banned = FALSE ORDER BY id;")" ||
    control_plane_die local_team_query_failed 65
  mapfile -t teams <<<"$team_rows"
  [[ "${#teams[@]}" == 1 && "${teams[0]}" =~ ^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$ ]] ||
    control_plane_die local_team_not_unique 65
  team_id="${teams[0]}"

  existing="$(psql_query --command "SELECT count(*) FROM env_builds WHERE id = '$BUILD_ID'::uuid;")" ||
    control_plane_die existing_build_query_failed 65
  case "$existing" in
    0) ;;
    1)
      verify_database || control_plane_die template_seed_verification_failed 65
      verify_artifacts "$DESTINATION_STORAGE/templates/$BUILD_ID" complete ||
        control_plane_die existing_template_invalid 65
      printf 'status=pass operation=seed-local-template build_id=%s state=existing\n' "$BUILD_ID"
      return 0
      ;;
    *) control_plane_die existing_build_ambiguous 65 ;;
  esac

  stage="$(mktemp -d "$DESTINATION_STORAGE/.seed-stage.XXXXXXXX")"
  trap cleanup EXIT
  chmod 0700 -- "$stage"
  staged_storage="$stage"
  install -d -o root -g kitdev -m 2700 -- "$staged_storage/templates"
  for template_id in \
    "$BUILD_ID" \
    d757f43f-4871-4828-9d16-a54da5291f00 \
    "$FROM_BUILD_ID" \
    5f25449a-464b-4e10-83ba-e021db8b9b8e \
    b2e8d4fb-f5ea-4e24-aec8-9af4fbe77c50 \
    6be65ea2-c917-43f5-8a56-fc8daa66fca4; do
    install -d -o root -g kitdev -m 2700 -- "$staged_storage/templates/$template_id"
  done

  adc="$stage/application-default-credentials.json"
  printf '%s\n' '{"client_id":"unused","client_secret":"unused","refresh_token":"unused","type":"authorized_user"}' >"$adc"
  chmod 0600 -- "$adc"
  GOOGLE_APPLICATION_CREDENTIALS="$adc" "$COPY_BUILD" \
    -build "$BUILD_ID" -from "$SOURCE_STORAGE" -to "$staged_storage" \
    -team "$team_id" -envd-version 0.6.13 -vcpu 2 -memory 1024 -disk 1024 -tag default \
    >"$stage/upstream.sql" 2>"$stage/copy.log" || control_plane_die template_copy_failed 65
  chmod 0600 -- "$stage/upstream.sql" "$stage/copy.log"
  verify_artifacts "$staged_storage/templates/$BUILD_ID" complete ||
    control_plane_die staged_template_invalid 65
  /usr/bin/python3 -I -B -S "$SCRIPT_DIR/normalize-copy-sql.py" \
    "$stage/upstream.sql" "$stage/normalized.sql" "$BUILD_ID" "$team_id" ||
    control_plane_die upstream_copy_sql_invalid 65
  publish_artifacts "$staged_storage"
  psql_query <"$stage/normalized.sql" >/dev/null || control_plane_die template_seed_failed 65
  verify_database || control_plane_die template_seed_verification_failed 65
  printf 'status=pass operation=seed-local-template build_id=%s state=created\n' "$BUILD_ID"
}

main "$@"
