#!/usr/bin/env bash
set -Eeuo pipefail

export PATH=/usr/sbin:/usr/bin:/sbin:/bin
export LC_ALL=C
export LANG=C
unset BASH_ENV CDPATH ENV GLOBIGNORE

readonly KITDEV_ETC_ROOT=/etc/kitdev-sandboxes
readonly KITDEV_OPT_ROOT=/opt/kitdev-sandboxes
readonly KITDEV_STATE_ROOT=/var/lib/kitdev-sandboxes
readonly KITDEV_DATA_ROOT="$KITDEV_STATE_ROOT/data"
readonly KITDEV_RUNTIME_ROOT="$KITDEV_DATA_ROOT/runtime"
readonly KITDEV_PRIVATE_ENV="$KITDEV_ETC_ROOT/control-plane.env"
readonly KITDEV_INFRA_ROOT="$KITDEV_OPT_ROOT/src/e2b-infra"
readonly KITDEV_INFRA_COMMIT=882a3b4786755db9e94be3297de6827f9100ce5e
readonly KITDEV_INFRA_SHORT_COMMIT=882a3b4

control_plane_die() {
  printf 'status=error reason=%s\n' "$1" >&2
  exit "${2:-1}"
}

require_root() {
  [[ "$(id -u)" == 0 ]] || control_plane_die root_required 77
}

require_lifecycle_platform() {
  local lifecycle="${KITDEV_LIFECYCLE:-}"
  local version_id
  case "$lifecycle" in
    production|development|migration) ;;
    *) control_plane_die lifecycle_required 64 ;;
  esac
  version_id="$(/usr/bin/python3 -I -B -S - <<'PY_OS_RELEASE'
from pathlib import Path

values = {}
content = Path("/etc/os-release").read_text(encoding="utf-8")
for line in content.splitlines():
    if not line or line.startswith("#") or "=" not in line:
        continue
    key, value = line.split("=", 1)
    if value.startswith(('"', "'")) and value.endswith(value[0]):
        value = value[1:-1]
    values[key] = value
if values.get("ID") != "ubuntu" or values.get("VERSION_ID") not in {"25.04", "26.04"}:
    raise SystemExit(1)
print(values["VERSION_ID"])
PY_OS_RELEASE
  )" || control_plane_die unsupported_host_os 68
  [[ "$(uname -m)" == x86_64 ]] || control_plane_die unsupported_host_architecture 68
  [[ "$(cat /proc/1/comm 2>/dev/null)" == systemd ]] || control_plane_die systemd_required 68
  [[ "$(stat -fc %T /sys/fs/cgroup 2>/dev/null)" == cgroup2fs ]] ||
    control_plane_die cgroup_v2_required 68
  if [[ "$version_id" == 25.04 && "$lifecycle" == production ]]; then
    control_plane_die ubuntu_25_04_not_production_eligible 68
  fi
}

require_command() {
  command -v -- "$1" >/dev/null 2>&1 || control_plane_die "missing_command_$1" 69
}

require_worker_identity() {
  local passwd_record group_record kvm_record group_output
  local name password uid gid gecos home shell
  local group_name group_password group_gid group_members kvm_gid observed
  local -a group_ids
  declare -A seen_groups=()

  passwd_record="$(getent passwd -- kitdev-worker)" ||
    control_plane_die kitdev_worker_missing 65
  [[ "$passwd_record" != *$'\n'* ]] || control_plane_die kitdev_worker_ambiguous 65
  IFS=: read -r name password uid gid gecos home shell <<<"$passwd_record"
  [[ "$name" == kitdev-worker && "$uid" =~ ^[0-9]+$ && "$gid" =~ ^[0-9]+$ ]] ||
    control_plane_die kitdev_worker_invalid 65
  (( uid >= 61000 && uid <= 61999 && gid >= 61000 && gid <= 61999 )) ||
    control_plane_die kitdev_worker_reserved_range_required 65
  case "$uid:$gid" in
    101:*|999:*|10001:*|*:101|*:999|*:10001)
      control_plane_die kitdev_worker_container_identity_collision 65
      ;;
  esac

  group_record="$(getent group -- kitdev-worker)" ||
    control_plane_die kitdev_worker_primary_group_missing 65
  [[ "$group_record" != *$'\n'* ]] || control_plane_die kitdev_worker_primary_group_ambiguous 65
  IFS=: read -r group_name group_password group_gid group_members <<<"$group_record"
  [[ "$group_name" == kitdev-worker && "$group_gid" == "$gid" ]] ||
    control_plane_die kitdev_worker_primary_group_mismatch 65
  kvm_record="$(getent group -- kvm)" || control_plane_die kvm_group_missing 65
  [[ "$kvm_record" != *$'\n'* ]] || control_plane_die kvm_group_ambiguous 65
  IFS=: read -r group_name group_password kvm_gid group_members <<<"$kvm_record"
  [[ "$group_name" == kvm && "$kvm_gid" =~ ^[0-9]+$ && "$kvm_gid" != "$gid" ]] ||
    control_plane_die kvm_group_invalid 65

  group_output="$(id -G -- kitdev-worker)" || control_plane_die kitdev_worker_groups_unreadable 65
  read -r -a group_ids <<<"$group_output"
  [[ "${#group_ids[@]}" == 2 ]] || control_plane_die kitdev_worker_supplementary_groups_invalid 65
  for observed in "${group_ids[@]}"; do
    [[ "$observed" =~ ^[0-9]+$ ]] || control_plane_die kitdev_worker_supplementary_groups_invalid 65
    seen_groups["$observed"]=1
  done
  [[ "${#seen_groups[@]}" == 2 && -n "${seen_groups[$gid]:-}" && -n "${seen_groups[$kvm_gid]:-}" ]] ||
    control_plane_die kitdev_worker_supplementary_groups_invalid 65
  [[ "$(id -u -- kitdev-worker)" == "$uid" && "$(id -g -- kitdev-worker)" == "$gid" ]] ||
    control_plane_die kitdev_worker_identity_mismatch 65
}

identity_uid() {
  if [[ "$1" =~ ^[0-9]+$ ]]; then
    printf '%s' "$1"
  else
    id -u -- "$1" 2>/dev/null || control_plane_die unknown_file_owner 65
  fi
}

identity_gid() {
  local record name password gid members extra
  if [[ "$1" =~ ^[0-9]+$ ]]; then
    printf '%s' "$1"
  else
    record="$(getent group -- "$1")" || control_plane_die unknown_file_group 65
    [[ "$record" != *$'\n'* ]] || control_plane_die ambiguous_file_group 65
    IFS=: read -r name password gid members extra <<<"$record"
    [[ "$name" == "$1" && "$gid" =~ ^[0-9]+$ && -z "$extra" ]] ||
      control_plane_die invalid_file_group 65
    printf '%s' "$gid"
  fi
}

require_exact_directory() {
  local path="$1" owner="$2" group="$3" mode="$4"
  local owner_id group_id
  owner_id="$(identity_uid "$owner")"
  group_id="$(identity_gid "$group")"
  [[ ! -L "$path" && -d "$path" ]] || control_plane_die directory_state_conflict 65
  [[ "$(stat -c '%u:%g:%a' -- "$path")" == "$owner_id:$group_id:$mode" ]] ||
    control_plane_die directory_metadata_conflict 65
}

ensure_directory() {
  local path="$1" owner="$2" group="$3" mode="$4"
  if [[ ! -e "$path" && ! -L "$path" ]]; then
    install -d -o "$owner" -g "$group" -m "$mode" -- "$path"
  fi
  require_exact_directory "$path" "$owner" "$group" "$mode"
}

# Resolve exactly one control-plane datastore container.
#
# A fresh Compose deployment names containers <project>-<service>-N and carries
# Compose labels. The hand-assembled reference lab has bare names and no
# labels. Matching only the bare name worked on that lab and silently found
# nothing on a real Compose install, so accept either and require exactly one.
# The Python CLI already resolved it this way; the shell scripts did not.
#
# Note the Go template uses plain double quotes: inside a single-quoted shell
# string a backslash is literal and would reach Go as an invalid template.
control_plane_container() {
  local service="$1" ids id row project svc name
  local -a matches=()
  [[ "$service" =~ ^[a-z][a-z0-9-]{0,30}$ ]] || return 1
  ids="$(docker ps --no-trunc --quiet)" || return 1
  while IFS= read -r id; do
    [[ -n "$id" ]] || continue
    row="$(docker inspect --format '{{index .Config.Labels "com.docker.compose.project"}}|{{index .Config.Labels "com.docker.compose.service"}}|{{.Name}}' -- "$id")" || return 1
    IFS='|' read -r project svc name <<<"$row"
    name="${name#/}"
    if [[ "$project" == kitdev-control-plane && "$svc" == "$service" ]] ||
      [[ -z "$project" && -z "$svc" && "$name" == "kitdev-$service" ]]; then
      matches+=("$id")
    fi
  done <<<"$ids"
  [[ "${#matches[@]}" == 1 ]] || return 1
  [[ "${matches[0]}" =~ ^[0-9a-f]{64}$ ]] || return 1
  printf '%s\n' "${matches[0]}"
}

require_exact_file() {
  local path="$1" source="$2" owner="$3" group="$4" mode="$5"
  local owner_id group_id
  owner_id="$(identity_uid "$owner")"
  group_id="$(identity_gid "$group")"
  [[ ! -L "$path" && -f "$path" ]] || control_plane_die file_state_conflict 65
  [[ "$(stat -c '%u:%g:%a:%h' -- "$path")" == "$owner_id:$group_id:$mode:1" ]] ||
    control_plane_die file_metadata_conflict 65
  cmp --silent -- "$source" "$path" || control_plane_die file_content_conflict 65
}

publish_exact_file() {
  local source="$1" target="$2" owner="$3" group="$4" mode="$5"
  local parent temporary
  parent="$(dirname -- "$target")"
  [[ ! -L "$source" && -f "$source" ]] || control_plane_die source_file_invalid 65
  if [[ -e "$target" || -L "$target" ]]; then
    require_exact_file "$target" "$source" "$owner" "$group" "$mode"
    return 0
  fi
  temporary="$(mktemp "$parent/.kitdev-publish.XXXXXXXX")"
  install -o "$owner" -g "$group" -m "$mode" -- "$source" "$temporary"
  sync -f -- "$temporary"
  if ! ln -- "$temporary" "$target" 2>/dev/null; then
    require_exact_file "$target" "$source" "$owner" "$group" "$mode"
  fi
  rm -f -- "$temporary"
  temporary=''
  require_exact_file "$target" "$source" "$owner" "$group" "$mode"
}

require_clean_infra_checkout() {
  local output
  [[ ! -L "$KITDEV_INFRA_ROOT" && -d "$KITDEV_INFRA_ROOT/.git" ]] ||
    control_plane_die infra_checkout_missing 65
  [[ "$(git -C "$KITDEV_INFRA_ROOT" rev-parse HEAD)" == "$KITDEV_INFRA_COMMIT" ]] ||
    control_plane_die infra_commit_mismatch 65
  [[ "$(git -C "$KITDEV_INFRA_ROOT" remote get-url origin)" == https://github.com/e2b-dev/infra.git ]] ||
    control_plane_die infra_remote_mismatch 65
  output="$(git -C "$KITDEV_INFRA_ROOT" status --porcelain=v1 --untracked-files=all)"
  [[ -z "$output" ]] || control_plane_die infra_checkout_dirty 65
  if ! /usr/bin/python3 -I -B -S - "$KITDEV_INFRA_ROOT" <<'PY_TRUSTED_TREE'
import os
import stat
import sys

root_text = sys.argv[1]
root = os.fsencode(root_text)


def require_directory(path):
    metadata = os.lstat(path)
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or stat.S_ISLNK(metadata.st_mode)
        or metadata.st_uid != 0
        or stat.S_IMODE(metadata.st_mode) & 0o022
    ):
        raise SystemExit(1)


current = "/"
require_directory(current)
for component in os.path.normpath(root_text).split(os.sep)[1:]:
    current = os.path.join(current, component)
    require_directory(current)

for current, directories, files in os.walk(root, topdown=True, followlinks=False):
    entries = [os.path.join(current, name) for name in directories + files]
    for path in entries:
        metadata = os.lstat(path)
        if (
            stat.S_ISLNK(metadata.st_mode)
            or metadata.st_uid != 0
            or (
                (stat.S_ISDIR(metadata.st_mode) or stat.S_ISREG(metadata.st_mode))
                and stat.S_IMODE(metadata.st_mode) & 0o022
            )
            or not (stat.S_ISDIR(metadata.st_mode) or stat.S_ISREG(metadata.st_mode))
        ):
            raise SystemExit(1)
PY_TRUSTED_TREE
  then
    control_plane_die infra_checkout_untrusted 65
  fi
}
