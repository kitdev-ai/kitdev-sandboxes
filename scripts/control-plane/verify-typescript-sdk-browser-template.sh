#!/usr/bin/env bash
set -Eeuo pipefail
readonly SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
# shellcheck source=common.sh
source "$SCRIPT_DIR/common.sh"
umask 077

readonly CLIENT_DIR="$SCRIPT_DIR/e2e-typescript-sdk"
readonly NODE_IMAGE='docker.io/library/node:22.18.0-bookworm-slim@sha256:752ea8a2f758c34002a0461bd9f1cee4f9a3c36d48494586f60ffce1fc708e0e'
readonly SDK_LOCK_SHA256=490c2920ffce8e59f8edd9e9d7951b0f13f93521a851355e7c72e99ad134766c
readonly BROWSER_LOCK_SHA256=db5404269854f530b030d7c31b7ce8c0cd05e7182978af49c58b5e488f87c873
readonly STANDARD_PROFILE_SHA256=6850c73171505bbd15fdf8eeef544797dd2e1fbcfafded33b1f216e55ee05377
readonly HEAVY_PROFILE_SHA256=8b5b4bf0fb93361eceb30360b155b7ce2e6c92a65fe586ab34fcf696acae1c5b
readonly API_ROOT=http://127.0.0.1:3000
stage=''
template_id=''
template_name=''
sandbox_id=''

write_api_config() {
  /usr/bin/python3 -I -B -S - "$1" "$2" <<'PY_API_CONFIG'
import os
import re
import stat
import sys

source, target = sys.argv[1:]
before = os.lstat(source)
descriptor = os.open(source, os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0))
try:
    opened = os.fstat(descriptor)
    if (
        not stat.S_ISREG(opened.st_mode)
        or opened.st_uid != 0
        or opened.st_gid != 0
        or stat.S_IMODE(opened.st_mode) != 0o600
        or opened.st_nlink != 1
        or (before.st_dev, before.st_ino) != (opened.st_dev, opened.st_ino)
        or opened.st_size > 45
    ):
        raise SystemExit(1)
    data = os.read(descriptor, 46)
    if os.read(descriptor, 1):
        raise SystemExit(1)
finally:
    os.close(descriptor)
if not re.fullmatch(rb"e2b_[0-9a-f]{40}\n?", data):
    raise SystemExit(1)
key = data.rstrip(b"\n")
payload = b'header = "X-API-Key: ' + key + b'"\n'
output = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
try:
    os.write(output, payload)
    os.fsync(output)
finally:
    os.close(output)
PY_API_CONFIG
}

read_state_id() {
  local path="$1" pattern="$2" size="$3"
  /usr/bin/python3 -I -B -S - "$path" "$pattern" "$size" <<'PY_STATE_ID'
import os
import re
import stat
import sys

path, pattern, size = sys.argv[1], sys.argv[2], int(sys.argv[3])
metadata = os.lstat(path)
if (
    not stat.S_ISREG(metadata.st_mode)
    or stat.S_ISLNK(metadata.st_mode)
    or metadata.st_uid != 0
    or metadata.st_gid != 0
    or stat.S_IMODE(metadata.st_mode) != 0o600
    or metadata.st_nlink != 1
    or metadata.st_size != size
):
    raise SystemExit(1)
value = open(path, encoding="ascii").read()
if not re.fullmatch(pattern, value):
    raise SystemExit(1)
print(value, end="")
PY_STATE_ID
}

sandbox_absent() {
  local code redis_container
  [[ "$sandbox_id" =~ ^i[a-z0-9]{20}$ ]] || return 0
  code="$(curl --disable --config "$stage/api.curlrc" --silent --show-error \
    --output "$stage/sandboxes.json" --write-out '%{http_code}' --max-time 15 \
    --max-filesize 1048576 -- "$API_ROOT/sandboxes" 2>/dev/null)" || return 1
  [[ "$code" == 200 ]] || return 1
  /usr/bin/python3 -I -B -S - "$stage/sandboxes.json" "$sandbox_id" <<'PY_ABSENT' || return 1
import json
import sys

document = json.load(open(sys.argv[1], encoding="utf-8"))
if not isinstance(document, list):
    raise SystemExit(1)
raise SystemExit(any(isinstance(item, dict) and item.get("sandboxID") == sys.argv[2] for item in document))
PY_ABSENT
  ! pgrep -x firecracker >/dev/null 2>&1 || return 1
  redis_container="$(docker ps --no-trunc --quiet --filter name='^/kitdev-redis$')"
  [[ "$redis_container" =~ ^[0-9a-f]{64}$ ]] || return 1
  ! timeout 10 docker exec -- "$redis_container" redis-cli --raw --scan \
    --pattern "*$sandbox_id*" 2>/dev/null | head -n 1 | grep -q .
}

template_absent() {
  local code
  [[ "$template_name" =~ ^kitdev-browser-template-[0-9a-f]{12}$ ]] || return 0
  code="$(curl --disable --config "$stage/api.curlrc" --silent --show-error \
    --output /dev/null --write-out '%{http_code}' --max-time 15 \
    -- "$API_ROOT/templates/aliases/$template_name" 2>/dev/null)" || return 1
  [[ "$code" == 404 ]]
}

api_key_hash() {
  /usr/bin/python3 -I -B -S - "$1" <<'PY_API_KEY_HASH'
import base64
import hashlib
import sys

raw = open(sys.argv[1], "rb").read().rstrip(b"\n")
value = bytes.fromhex(raw.removeprefix(b"e2b_").decode("ascii"))
print("$sha256$" + base64.b64encode(hashlib.sha256(value).digest()).decode("ascii").rstrip("="))
PY_API_KEY_HASH
}

postgres_identity() {
  local container="$1" user database result
  for user in kitdev postgres; do
    database="$user"
    result="$(docker exec -- "$container" psql --no-psqlrc --tuples-only --no-align \
      --username "$user" --dbname "$database" --command \
      "SELECT to_regclass('public.teams') IS NOT NULL AND to_regclass('public.team_api_keys') IS NOT NULL;" \
      2>/dev/null)" || continue
    if [[ "$result" == t ]]; then
      printf '%s|%s\n' "$user" "$database"
      return
    fi
  done
  return 1
}

require_heavy_capacity() {
  local available_kib huge_free huge_total
  huge_total="$(awk '$1 == "HugePages_Total:" {print $2}' /proc/meminfo)"
  huge_free="$(awk '$1 == "HugePages_Free:" {print $2}' /proc/meminfo)"
  available_kib="$(awk '$1 == "MemAvailable:" {print $2}' /proc/meminfo)"
  [[ "$huge_total" =~ ^[0-9]+$ && "$huge_free" =~ ^[0-9]+$ && "$available_kib" =~ ^[0-9]+$ ]] ||
    control_plane_die heavy_capacity_unknown 65
  (( huge_total >= 12288 )) || control_plane_die heavy_hugepages_total_insufficient 65
  (( huge_free >= 12288 )) || control_plane_die heavy_hugepages_free_insufficient 65
  (( available_kib >= 16777216 )) || control_plane_die heavy_normal_memory_insufficient 65
}

require_heavy_team_profile() {
  local api_key_file="$1" identity key_hash postgres_container postgres_database postgres_user
  local redis_container row team_id
  key_hash="$(api_key_hash "$api_key_file")" || control_plane_die heavy_api_key_hash_failed 65
  [[ "$key_hash" =~ ^\$sha256\$[A-Za-z0-9+/]{43}$ ]] || control_plane_die heavy_api_key_hash_invalid 65
  postgres_container="$(docker ps --no-trunc --quiet --filter name='^/kitdev-postgres$')"
  [[ "$postgres_container" =~ ^[0-9a-f]{64}$ ]] || control_plane_die postgres_container_invalid 65
  identity="$(postgres_identity "$postgres_container")" || control_plane_die postgres_identity_invalid 65
  IFS='|' read -r postgres_user postgres_database <<<"$identity"
  row="$(docker exec --interactive "$postgres_container" \
    psql --no-psqlrc --set=ON_ERROR_STOP=1 --tuples-only --no-align \
      --field-separator='|' --username "$postgres_user" --dbname "$postgres_database" --command "
SELECT t.id, t.slug, l.max_vcpu, l.max_ram_mb, l.disk_mb,
       l.default_free_disk_size_mb, l.max_disk_size_mb
FROM public.team_api_keys k
JOIN public.teams t ON t.id = k.team_id
JOIN public.team_limits l ON l.id = t.id
WHERE k.api_key_hash = '$key_hash';")" || control_plane_die heavy_team_query_failed 65
  [[ "$row" =~ ^([0-9a-f-]{36})\|kitdev-browser-heavy-team\|2\|8192\|16384\|16384\|25600$ ]] ||
    control_plane_die heavy_team_profile_invalid 65
  team_id="${BASH_REMATCH[1]}"
  redis_container="$(docker ps --no-trunc --quiet --filter name='^/kitdev-redis$')"
  [[ "$redis_container" =~ ^[0-9a-f]{64}$ ]] || control_plane_die redis_container_invalid 65
  [[ "$(docker exec -- "$redis_container" redis-cli --raw DEL \
    "auth:team:$key_hash" "auth:team:team-$team_id")" =~ ^[0-9]+$ ]] ||
    control_plane_die heavy_auth_cache_invalidation_failed 65
}

verify_heavy_build_metadata() {
  local build_id identity postgres_container postgres_database postgres_user row
  build_id="$(read_state_id "$stage/state/build-id" '[0-9a-f-]{36}\n' 37)" ||
    control_plane_die heavy_build_id_invalid 65
  postgres_container="$(docker ps --no-trunc --quiet --filter name='^/kitdev-postgres$')"
  [[ "$postgres_container" =~ ^[0-9a-f]{64}$ ]] || control_plane_die postgres_container_invalid 65
  identity="$(postgres_identity "$postgres_container")" || control_plane_die postgres_identity_invalid 65
  IFS='|' read -r postgres_user postgres_database <<<"$identity"
  row="$(docker exec --interactive "$postgres_container" \
    psql --no-psqlrc --set=ON_ERROR_STOP=1 --tuples-only --no-align \
      --field-separator='|' --username "$postgres_user" --dbname "$postgres_database" --command "
SELECT vcpu, ram_mb, free_disk_size_mb, total_disk_size_mb, status_group
FROM public.env_builds WHERE id = '$build_id'::uuid;")" ||
    control_plane_die heavy_build_query_failed 65
  [[ "$row" =~ ^2\|8192\|16384\|([0-9]+)\|ready$ ]] ||
    control_plane_die heavy_build_metadata_invalid 65
  (( BASH_REMATCH[1] >= 16384 && BASH_REMATCH[1] <= 25600 )) ||
    control_plane_die heavy_build_total_disk_invalid 65
}

cleanup() {
  local status=$? code attempt
  trap - EXIT INT TERM
  if [[ -n "$stage" && -f "$stage/state/sandbox-id" ]]; then
    sandbox_id="$(read_state_id "$stage/state/sandbox-id" 'i[a-z0-9]{20}\n' 22 2>/dev/null)" || sandbox_id='invalid'
  fi
  if [[ "$sandbox_id" =~ ^i[a-z0-9]{20}$ && -f "$stage/api.curlrc" ]]; then
    for attempt in {1..5}; do
      code="$(curl --disable --config "$stage/api.curlrc" --silent --show-error \
        --output /dev/null --write-out '%{http_code}' --max-time 15 \
        --request DELETE -- "$API_ROOT/sandboxes/$sandbox_id" 2>/dev/null)" || code=''
      [[ "$code" == 204 || "$code" == 404 ]] && break
      sleep 1
    done
    [[ "$code" == 204 || "$code" == 404 ]] || status=1
    for attempt in {1..60}; do
      sandbox_absent && break
      sleep 1
    done
    sandbox_absent || status=1
  fi
  if [[ -n "$stage" && -f "$stage/state/template-id" ]]; then
    template_id="$(read_state_id "$stage/state/template-id" '[a-z0-9]{16,32}\n' 21 2>/dev/null)" || template_id='invalid'
  fi
  if [[ -n "$stage" && -f "$stage/config/e2b-template-name" ]]; then
    template_name="$(read_state_id "$stage/config/e2b-template-name" 'kitdev-browser-template-[0-9a-f]{12}\n' 37 2>/dev/null)" || template_name='invalid'
  fi
  if [[ "$template_id" =~ ^[a-z0-9]{16,32}$ && -f "$stage/api.curlrc" ]]; then
    for attempt in {1..5}; do
      code="$(curl --disable --config "$stage/api.curlrc" --silent --show-error \
        --output /dev/null --write-out '%{http_code}' --max-time 30 \
        --request DELETE -- "$API_ROOT/templates/$template_id" 2>/dev/null)" || code=''
      [[ "$code" == 204 || "$code" == 404 ]] && break
      sleep 1
    done
    [[ "$code" == 204 || "$code" == 404 ]] || status=1
  elif [[ "$template_name" =~ ^kitdev-browser-template-[0-9a-f]{12}$ && -f "$stage/api.curlrc" ]]; then
    for attempt in {1..5}; do
      code="$(curl --disable --config "$stage/api.curlrc" --silent --show-error \
        --output /dev/null --write-out '%{http_code}' --max-time 30 \
        --request DELETE -- "$API_ROOT/templates/$template_name" 2>/dev/null)" || code=''
      [[ "$code" == 204 || "$code" == 404 ]] && break
      sleep 1
    done
    [[ "$code" == 204 || "$code" == 404 ]] || status=1
  fi
  if [[ "$template_name" =~ ^kitdev-browser-template-[0-9a-f]{12}$ && -f "$stage/api.curlrc" ]]; then
    for attempt in {1..30}; do
      template_absent && break
      sleep 1
    done
    template_absent || status=1
  fi
  [[ -z "$stage" ]] || rm -rf -- "$stage"
  exit "$status"
}

main() {
  local api_key_file profile profile_path profile_sha256 uuid
  profile=standard
  if [[ $# == 2 && "$1" == --api-key-file ]]; then
    api_key_file="$2"
  elif [[ $# == 4 && "$1" == --resource-profile && "$3" == --api-key-file ]]; then
    profile="$2"
    api_key_file="$4"
  else
    control_plane_die invalid_arguments 64
  fi
  case "$profile" in
    standard)
      profile_sha256="$STANDARD_PROFILE_SHA256"
      ;;
    heavy)
      profile_sha256="$HEAVY_PROFILE_SHA256"
      ;;
    *) control_plane_die browser_resource_profile_invalid 64 ;;
  esac
  profile_path="$CLIENT_DIR/browser-resource-profiles/$profile.json"
  require_root
  require_lifecycle_platform
  [[ "$KITDEV_LIFECYCLE" != production ]] || control_plane_die e2e_not_for_production 68
  require_command curl
  require_command docker
  require_command flock
  require_command pgrep
  require_command sha256sum
  require_command timeout
  [[ "$(sha256sum -- "$CLIENT_DIR/package-lock.json" | awk '{print $1}')" == "$SDK_LOCK_SHA256" ]] ||
    control_plane_die sdk_lock_hash_invalid 65
  [[ "$(sha256sum -- "$CLIENT_DIR/browser-template-assets/package-lock.json" | awk '{print $1}')" == "$BROWSER_LOCK_SHA256" ]] ||
    control_plane_die browser_lock_hash_invalid 65
  [[ "$(sha256sum -- "$profile_path" | awk '{print $1}')" == "$profile_sha256" ]] ||
    control_plane_die browser_resource_profile_hash_invalid 65
  [[ ! -L "$api_key_file" && -f "$api_key_file" ]] || control_plane_die sdk_api_key_file_invalid 65
  [[ "$(stat -c '%u:%g:%a:%h' -- "$api_key_file")" == 0:0:600:1 ]] ||
    control_plane_die sdk_api_key_file_metadata_invalid 65

  ensure_directory /run/kitdev-sandboxes root root 700
  if [[ ! -e /run/kitdev-sandboxes/typescript-sdk-e2e.lock &&
    ! -L /run/kitdev-sandboxes/typescript-sdk-e2e.lock ]]; then
    install -o root -g root -m 0600 /dev/null /run/kitdev-sandboxes/typescript-sdk-e2e.lock
  fi
  [[ ! -L /run/kitdev-sandboxes/typescript-sdk-e2e.lock &&
    -f /run/kitdev-sandboxes/typescript-sdk-e2e.lock &&
    "$(stat -c '%u:%g:%a:%s:%h' /run/kitdev-sandboxes/typescript-sdk-e2e.lock)" == '0:0:600:0:1' ]] ||
    control_plane_die sdk_e2e_lock_metadata_invalid 65
  exec 9<>/run/kitdev-sandboxes/typescript-sdk-e2e.lock
  flock --nonblock 9 || control_plane_die sdk_e2e_already_running 75
  ! pgrep -x firecracker >/dev/null 2>&1 || control_plane_die sdk_preexisting_firecracker 65
  if [[ "$profile" == heavy ]]; then
    require_heavy_capacity
    require_heavy_team_profile "$api_key_file"
  fi

  trap cleanup EXIT
  trap 'exit 130' INT TERM
  stage="$(mktemp -d /run/kitdev-sandboxes/typescript-sdk-browser-template.XXXXXXXX)"
  chmod 0700 -- "$stage"
  install -d -o root -g root -m 0700 \
    "$stage/client" "$stage/client/browser-template-assets" "$stage/config" "$stage/state"
  install -o root -g root -m 0600 "$CLIENT_DIR/package.json" "$stage/client/package.json"
  install -o root -g root -m 0600 "$CLIENT_DIR/package-lock.json" "$stage/client/package-lock.json"
  install -o root -g root -m 0600 "$CLIENT_DIR/browser-template.ts" "$stage/client/browser-template.ts"
  install -o root -g root -m 0600 "$profile_path" \
    "$stage/config/browser-resource-profile.json"
  install -o root -g root -m 0600 "$CLIENT_DIR/browser-template-assets/"* \
    "$stage/client/browser-template-assets/"
  uuid="$(</proc/sys/kernel/random/uuid)"
  uuid="${uuid//-/}"
  template_name="kitdev-browser-template-${uuid:0:12}"
  printf '%s\n' "$template_name" >"$stage/config/e2b-template-name"
  chmod 0600 -- "$stage/config/e2b-template-name"
  write_api_config "$api_key_file" "$stage/api.curlrc" || control_plane_die sdk_api_key_invalid 65

  docker pull --platform linux/amd64 "$NODE_IMAGE" >/dev/null
  docker run --rm --pull never --platform linux/amd64 --user 0:0 \
    --volume "$stage/client:/workspace" --workdir /workspace "$NODE_IMAGE" \
    npm ci --ignore-scripts --no-audit --no-fund >/dev/null
  [[ "$(docker run --rm --pull never --platform linux/amd64 --network none \
    --volume "$stage/client:/workspace:ro" --workdir /workspace "$NODE_IMAGE" \
    node -p "require('./node_modules/e2b/package.json').version")" == 2.38.0 ]] ||
    control_plane_die sdk_installed_version_invalid 65

  docker run --rm --pull never --platform linux/amd64 --network host --user 0:0 \
    --read-only --tmpfs /tmp:rw,nosuid,nodev,noexec,mode=1777,size=64m \
    --volume "$stage/client:/workspace:ro" \
    --volume "$api_key_file:/run/secrets/e2b-api-key:ro" \
    --volume "$stage/config:/run/config:ro" \
    --volume "$stage/state:/run/state" --workdir /workspace \
    "$NODE_IMAGE" node browser-template.ts
  [[ "$profile" != heavy ]] || verify_heavy_build_metadata
  printf 'status=pass operation=verify-typescript-sdk-browser-template\n'
}

main "$@"
