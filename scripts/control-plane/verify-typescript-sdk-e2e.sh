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

read_sandbox_id() {
  /usr/bin/python3 -I -B -S - "${1:-$stage/state/sandbox-id}" <<'PY_SANDBOX_ID'
import os
import re
import stat
import sys

path = sys.argv[1]
metadata = os.lstat(path)
if (
    not stat.S_ISREG(metadata.st_mode)
    or stat.S_ISLNK(metadata.st_mode)
    or metadata.st_uid != 0
    or metadata.st_gid != 0
    or stat.S_IMODE(metadata.st_mode) != 0o600
    or metadata.st_nlink != 1
    or metadata.st_size != 22
):
    raise SystemExit(1)
value = open(path, encoding="ascii").read()
if not re.fullmatch(r"i[a-z0-9]{20}\n", value):
    raise SystemExit(1)
print(value, end="")
PY_SANDBOX_ID
}

cleanup_snapshot_audit_key() {
  local action attempt redis_container source_id
  [[ -f "$stage/state/snapshot-id" ]] || return 0
  source_id="$(read_sandbox_id "$stage/state/sandbox-id")" || return 1
  redis_container="$(control_plane_container redis)"
  [[ "$redis_container" =~ ^[0-9a-f]{64}$ ]] || return 1
  rm -f -- "$stage/snapshot-audit-keys" "$stage/snapshot-audit-keys.previous"
  for attempt in {1..60}; do
    if [[ -f "$stage/snapshot-audit-keys" ]]; then
      mv -- "$stage/snapshot-audit-keys" "$stage/snapshot-audit-keys.previous"
    fi
    timeout 10 docker exec -- "$redis_container" redis-cli --raw EVAL \
      "local keys=redis.call('KEYS',ARGV[1]); table.sort(keys); local out={}; for _,key in ipairs(keys) do table.insert(out,{key=key,type=redis.call('TYPE',key)['ok'],pttl=redis.call('PTTL',key)}) end; return cjson.encode(out)" \
      0 "*$source_id*" >"$stage/snapshot-audit-keys" 2>/dev/null || return 1
    chmod 0600 -- "$stage/snapshot-audit-keys"
    action="$(/usr/bin/python3 -I -B -S - "$stage/snapshot-audit-keys" "$source_id" \
      "$stage/snapshot-audit-keys.previous" <<'PY_SNAPSHOT_AUDIT'
import json
import os
import re
import sys

rows = json.load(open(sys.argv[1], encoding="ascii"))
expected = "snapshot:last:" + sys.argv[2]
uuid = r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
transition = re.compile(
    rf"sandbox:storage:\{{{uuid}\}}:transition:{re.escape(sys.argv[2])}:{uuid}"
)
previous = {}
if os.path.exists(sys.argv[3]):
    previous = {
        row["key"]: row["pttl"]
        for row in json.load(open(sys.argv[3], encoding="ascii"))
        if isinstance(row, dict) and transition.fullmatch(str(row.get("key", "")))
    }
if not rows:
    print("absent")
else:
    keys = []
    transitions = []
    for row in rows:
        if not isinstance(row, dict) or set(row) != {"key", "type", "pttl"}:
            raise SystemExit(1)
        key, kind, pttl = row["key"], row["type"], row["pttl"]
        if not isinstance(key, str) or kind != "string" or not isinstance(pttl, int):
            raise SystemExit(1)
        keys.append(key)
        if key == expected:
            if pttl <= 0:
                raise SystemExit(1)
        elif transition.fullmatch(key):
            if not 0 < pttl <= 60_000:
                raise SystemExit(1)
            if key in previous and pttl > previous[key]:
                raise SystemExit(1)
            transitions.append(key)
        else:
            raise SystemExit(1)
    if len(keys) != len(set(keys)) or keys.count(expected) != 1:
        raise SystemExit(1)
    print("wait" if transitions else "delete")
PY_SNAPSHOT_AUDIT
    )" || return 1
    if [[ "$action" == wait ]]; then
      sleep 1
      continue
    fi
    rm -f -- "$stage/snapshot-audit-keys" "$stage/snapshot-audit-keys.previous"
    if [[ "$action" == delete ]]; then
      [[ "$(docker exec -- "$redis_container" redis-cli --raw DEL \
        "snapshot:last:$source_id" 2>/dev/null)" == 1 ]] || return 1
    else
      [[ "$action" == absent ]] || return 1
    fi
    return 0
  done
  return 1
}

terminal_state_ready() {
  local code redis_container candidate id_file
  [[ -n "$sandbox_id" ]] || return 0
  code="$(curl --disable --config "$stage/api.curlrc" --silent --show-error \
    --output "$stage/sandboxes.json" --write-out '%{http_code}' --max-time 15 \
    --max-filesize 1048576 -- "$API_ROOT/sandboxes" 2>/dev/null)" || return 1
  [[ "$code" == 200 ]] || return 1
  /usr/bin/python3 -I -B -S - "$stage/sandboxes.json" <<'PY_ABSENT' || return 1
import json
import sys

document = json.load(open(sys.argv[1], encoding="utf-8"))
raise SystemExit(0 if isinstance(document, list) and not document else 1)
PY_ABSENT
  ! pgrep -x firecracker >/dev/null 2>&1 || return 1
  redis_container="$(control_plane_container redis)"
  [[ "$redis_container" =~ ^[0-9a-f]{64}$ ]] || return 1
  for id_file in "$stage/state/sandbox-id" "$stage/state/secondary-sandbox-id"; do
    if [[ -f "$id_file" ]]; then
      candidate="$(read_sandbox_id "$id_file")" || return 1
    else
      candidate="$sandbox_id"
    fi
    ! timeout 10 docker exec -- "$redis_container" redis-cli --raw --scan \
      --pattern "*$candidate*" 2>/dev/null | head -n 1 | grep -q . || return 1
  done
}

verify_terminal_state() {
  local attempt
  for attempt in {1..60}; do
    terminal_state_ready && return 0
    sleep 1
  done
  return 1
}

run_sdk_group() {
  local source="$1"
  sandbox_id=''
  [[ ! -e "$stage/state/sandbox-id" && ! -L "$stage/state/sandbox-id" ]] ||
    control_plane_die sdk_stale_sandbox_state 65
  docker run --rm --pull never --platform linux/amd64 --network host --user 0:0 \
    --read-only --tmpfs /tmp:rw,nosuid,nodev,noexec,mode=1777,size=16m \
    --volume "$stage/client:/workspace:ro" \
    --volume "$api_key_file:/run/secrets/e2b-api-key:ro" \
    --volume "$template_id_file:/run/config/e2b-template-id:ro" \
    --volume "$stage/state:/run/state" --workdir /workspace "$NODE_IMAGE" node "$source"
  sandbox_id="$(read_sandbox_id)" || control_plane_die sdk_sandbox_state_invalid 65
  cleanup_snapshot_audit_key || control_plane_die sdk_snapshot_audit_cleanup_invalid 65
  verify_terminal_state || control_plane_die sdk_terminal_state_invalid 65
  rm -f -- "$stage/state/sandbox-id" "$stage/state/secondary-sandbox-id" \
    "$stage/state/snapshot-id"
}

cleanup() {
  local status=$? code attempt candidate id_file
  trap - EXIT INT TERM
  if [[ -n "$stage" && -f "$stage/api.curlrc" ]]; then
    for id_file in "$stage/state/sandbox-id" "$stage/state/secondary-sandbox-id"; do
      if [[ -f "$id_file" ]]; then
        candidate="$(read_sandbox_id "$id_file" 2>/dev/null)" || candidate='invalid'
      else
        candidate="$sandbox_id"
      fi
      [[ "$candidate" =~ ^i[a-z0-9]{20}$ ]] || continue
      sandbox_id="$candidate"
      for attempt in {1..5}; do
        code="$(curl --disable --config "$stage/api.curlrc" --silent --show-error \
          --output /dev/null --write-out '%{http_code}' --max-time 15 \
          --request DELETE -- "$API_ROOT/sandboxes/$sandbox_id" 2>/dev/null)" || code=''
        [[ "$code" == 204 || "$code" == 404 ]] && break
        sleep 1
      done
      [[ "$code" == 204 || "$code" == 404 ]] || status=1
    done
    if [[ -f "$stage/state/snapshot-id" && -f "$stage/client/cleanup.ts" ]]; then
      docker run --rm --pull never --platform linux/amd64 --network host --user 0:0 \
        --read-only --tmpfs /tmp:rw,nosuid,nodev,noexec,mode=1777,size=16m \
        --volume "$stage/client:/workspace:ro" \
        --volume "$api_key_file:/run/secrets/e2b-api-key:ro" \
        --volume "$template_id_file:/run/config/e2b-template-id:ro" \
        --volume "$stage/state:/run/state:ro" --workdir /workspace "$NODE_IMAGE" \
        node cleanup.ts >/dev/null 2>&1 || status=1
    fi
    cleanup_snapshot_audit_key || status=1
    for attempt in {1..60}; do
      terminal_state_ready && break
      sleep 1
    done
    terminal_state_ready || status=1
  fi
  [[ -z "$stage" ]] || rm -rf -- "$stage"
  exit "$status"
}

main() {
  api_key_file=''
  template_id_file=''
  [[ $# == 4 && "$1" == --api-key-file && "$3" == --template-id-file ]] ||
    control_plane_die invalid_arguments 64
  api_key_file="$2"
  template_id_file="$4"
  require_root
  require_lifecycle_platform
  [[ "$KITDEV_LIFECYCLE" != production ]] || control_plane_die e2e_not_for_production 68
  require_command curl
  require_command docker
  require_command flock
  require_command pgrep
  require_command sha256sum
  require_command timeout
  [[ "$(sha256sum -- "$CLIENT_DIR/package-lock.json" | awk '{print $1}')" == "$LOCK_SHA256" ]] ||
    control_plane_die sdk_lock_hash_invalid 65
  [[ ! -L "$template_id_file" && -f "$template_id_file" ]] ||
    control_plane_die sdk_template_file_invalid 65
  [[ "$(stat -c '%u:%g:%a:%h' -- "$template_id_file")" == 0:0:600:1 ]] ||
    control_plane_die sdk_template_file_metadata_invalid 65
  [[ "$(tr -d '\n' <"$template_id_file")" =~ ^[a-z0-9]{16,32}$ ]] ||
    control_plane_die sdk_template_id_invalid 65

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
  [[ "$(stat -Lc '%d:%i' /proc/$$/fd/9)" == \
    "$(stat -Lc '%d:%i' /run/kitdev-sandboxes/typescript-sdk-e2e.lock)" ]] ||
    control_plane_die sdk_e2e_lock_changed 65
  flock --nonblock 9 || control_plane_die sdk_e2e_already_running 75
  trap cleanup EXIT
  trap 'exit 130' INT TERM
  stage="$(mktemp -d /run/kitdev-sandboxes/typescript-sdk-e2e.XXXXXXXX)"
  chmod 0700 -- "$stage"
  install -d -o root -g root -m 0700 "$stage/client" "$stage/state"
  install -o root -g root -m 0600 "$CLIENT_DIR/package.json" "$stage/client/package.json"
  install -o root -g root -m 0600 "$CLIENT_DIR/package-lock.json" "$stage/client/package-lock.json"
  install -o root -g root -m 0600 "$CLIENT_DIR/smoke.ts" "$stage/client/smoke.ts"
  install -o root -g root -m 0600 "$CLIENT_DIR/harness.ts" "$stage/client/harness.ts"
  install -o root -g root -m 0600 "$CLIENT_DIR/commands.ts" "$stage/client/commands.ts"
  install -o root -g root -m 0600 "$CLIENT_DIR/files.ts" "$stage/client/files.ts"
  install -o root -g root -m 0600 "$CLIENT_DIR/pty.ts" "$stage/client/pty.ts"
  install -o root -g root -m 0600 "$CLIENT_DIR/lifecycle.ts" "$stage/client/lifecycle.ts"
  install -o root -g root -m 0600 "$CLIENT_DIR/pause.ts" "$stage/client/pause.ts"
  install -o root -g root -m 0600 "$CLIENT_DIR/snapshot.ts" "$stage/client/snapshot.ts"
  install -o root -g root -m 0600 "$CLIENT_DIR/cleanup.ts" "$stage/client/cleanup.ts"
  write_api_config "$api_key_file" "$stage/api.curlrc" ||
    control_plane_die sdk_api_key_invalid 65
  ! pgrep -x firecracker >/dev/null 2>&1 || control_plane_die sdk_preexisting_firecracker 65

  docker pull --platform linux/amd64 "$NODE_IMAGE" >/dev/null
  docker run --rm --pull never --platform linux/amd64 --user 0:0 \
    --volume "$stage/client:/workspace" --workdir /workspace "$NODE_IMAGE" \
    npm ci --ignore-scripts --no-audit --no-fund >/dev/null
  [[ "$(docker run --rm --pull never --platform linux/amd64 --network none \
    --volume "$stage/client:/workspace:ro" --workdir /workspace "$NODE_IMAGE" \
    node -p "require('./node_modules/e2b/package.json').version")" == 2.38.0 ]] ||
    control_plane_die sdk_installed_version_invalid 65

  run_sdk_group smoke.ts
  run_sdk_group commands.ts
  run_sdk_group files.ts
  run_sdk_group pty.ts
  run_sdk_group lifecycle.ts
  run_sdk_group pause.ts
  run_sdk_group snapshot.ts
  printf 'status=pass operation=verify-typescript-sdk-e2e\n'
}

main "$@"
