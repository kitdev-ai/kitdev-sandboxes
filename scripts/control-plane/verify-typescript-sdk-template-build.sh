#!/usr/bin/env bash
set -Eeuo pipefail
readonly SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
# shellcheck source=common.sh
source "$SCRIPT_DIR/common.sh"
umask 077

readonly CLIENT_DIR="$SCRIPT_DIR/e2e-typescript-sdk"
readonly NODE_IMAGE='docker.io/library/node:22.18.0-bookworm-slim@sha256:752ea8a2f758c34002a0461bd9f1cee4f9a3c36d48494586f60ffce1fc708e0e'
readonly LOCK_SHA256=490c2920ffce8e59f8edd9e9d7951b0f13f93521a851355e7c72e99ad134766c
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
  fi
  if [[ -n "$stage" && -f "$stage/state/template-id" ]]; then
    template_id="$(read_state_id "$stage/state/template-id" '[a-z0-9]{16,32}\n' 21 2>/dev/null)" || template_id='invalid'
  fi
  if [[ -n "$stage" && -f "$stage/config/e2b-template-name" ]]; then
    template_name="$(read_state_id "$stage/config/e2b-template-name" 'kitdev-sdk-template-[0-9a-f]{12}\n' 33 2>/dev/null)" || template_name='invalid'
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
  elif [[ "$template_name" =~ ^kitdev-sdk-template-[0-9a-f]{12}$ && -f "$stage/api.curlrc" ]]; then
    for attempt in {1..5}; do
      code="$(curl --disable --config "$stage/api.curlrc" --silent --show-error \
        --output /dev/null --write-out '%{http_code}' --max-time 30 \
        --request DELETE -- "$API_ROOT/templates/$template_name" 2>/dev/null)" || code=''
      [[ "$code" == 204 || "$code" == 404 ]] && break
      sleep 1
    done
    [[ "$code" == 204 || "$code" == 404 ]] || status=1
  fi
  for attempt in {1..60}; do
    ! pgrep -x firecracker >/dev/null 2>&1 && break
    sleep 1
  done
  ! pgrep -x firecracker >/dev/null 2>&1 || status=1
  [[ -z "$stage" ]] || rm -rf -- "$stage"
  exit "$status"
}

main() {
  local api_key_file uuid
  [[ $# == 2 && "$1" == --api-key-file ]] || control_plane_die invalid_arguments 64
  api_key_file="$2"
  require_root
  require_lifecycle_platform
  [[ "$KITDEV_LIFECYCLE" != production ]] || control_plane_die e2e_not_for_production 68
  require_command curl
  require_command docker
  require_command flock
  require_command pgrep
  require_command sha256sum
  [[ "$(sha256sum -- "$CLIENT_DIR/package-lock.json" | awk '{print $1}')" == "$LOCK_SHA256" ]] ||
    control_plane_die sdk_lock_hash_invalid 65
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

  trap cleanup EXIT
  trap 'exit 130' INT TERM
  stage="$(mktemp -d /run/kitdev-sandboxes/typescript-sdk-template-build.XXXXXXXX)"
  chmod 0700 -- "$stage"
  install -d -o root -g root -m 0700 "$stage/client" "$stage/config" "$stage/state"
  install -o root -g root -m 0600 "$CLIENT_DIR/package.json" "$stage/client/package.json"
  install -o root -g root -m 0600 "$CLIENT_DIR/package-lock.json" "$stage/client/package-lock.json"
  install -o root -g root -m 0600 "$CLIENT_DIR/template-build.ts" "$stage/client/template-build.ts"
  uuid="$(</proc/sys/kernel/random/uuid)"
  uuid="${uuid//-/}"
  template_name="kitdev-sdk-template-${uuid:0:12}"
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
    --read-only --tmpfs /tmp:rw,nosuid,nodev,noexec,mode=1777,size=32m \
    --volume "$stage/client:/workspace:ro" \
    --volume "$api_key_file:/run/secrets/e2b-api-key:ro" \
    --volume "$stage/config:/run/config:ro" \
    --volume "$stage/state:/run/state" --workdir /workspace \
    "$NODE_IMAGE" node template-build.ts
  printf 'status=pass operation=verify-typescript-sdk-template-build\n'
}

main "$@"
