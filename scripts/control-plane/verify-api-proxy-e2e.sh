#!/usr/bin/env bash
set -Eeuo pipefail
readonly SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
# shellcheck source=common.sh
source "$SCRIPT_DIR/common.sh"
umask 077

readonly BUILDER='docker.io/library/golang:1.26.5-bookworm@sha256:6c5605ab3a9a9fb3c4eafe5b3d63cdbf3881caf113262b67862547b54a9db599'
readonly BUILD_ID=2d9a8389-f5f5-4449-b0eb-e1d364ee98ae
readonly CLIENT_SHA256=2e1e9947a3d553b7e8f92b00304361503a220bbbda9b321c88e4e4886ed35f11
readonly CLIENT_SIZE=10068094
readonly API_ROOT=http://127.0.0.1:3000
readonly ORCHESTRATOR_HEALTH=http://127.0.0.1:5008/health
readonly EXPECTED_ORCHESTRATOR_ENV="$KITDEV_OPT_ROOT/libexec/control-plane/orchestrator.env.expected"
stage=''
sandbox_id=''
create_attempted=no

cleanup() {
  local status=$? code attempt terminal=no no_create=no
  trap - EXIT INT TERM
  if [[ "$create_attempted" == yes && ! "${sandbox_id:-}" =~ ^i[a-z0-9]{20}$ &&
    -n "${stage:-}" &&
    -f "$stage/create-response.json" ]]; then
    sandbox_id="$(extract_sandbox_id "$stage/create-response.json" 2>/dev/null)" || sandbox_id=''
  fi
  if [[ "$create_attempted" == yes && ! "${sandbox_id:-}" =~ ^i[a-z0-9]{20}$ &&
    -n "${stage:-}" &&
    -f "$stage/api.curlrc" ]]; then
    for attempt in {1..120}; do
      code="$(curl_code "$stage/api.curlrc" "$stage/sandboxes.json" \
        -- "$API_ROOT/sandboxes" 2>/dev/null)" || code=''
      if [[ "$code" == 200 ]]; then
        sandbox_id="$(recover_target_sandbox_id 2>/dev/null)" || sandbox_id=''
        [[ "$sandbox_id" =~ ^i[a-z0-9]{20}$ ]] && break
      fi
      sleep 1
    done
    if [[ ! "$sandbox_id" =~ ^i[a-z0-9]{20}$ && "$code" == 200 ]] &&
      sandbox_list_is_empty && ! pgrep -x firecracker >/dev/null 2>&1; then
      no_create=yes
    fi
  fi
  if [[ "${sandbox_id:-}" =~ ^i[a-z0-9]{20}$ && -n "${stage:-}" &&
    -f "$stage/api.curlrc" ]]; then
    code=''
    for attempt in {1..5}; do
      code="$(curl --disable --config "$stage/api.curlrc" --silent --show-error \
        --output /dev/null --write-out '%{http_code}' --max-time 15 --max-filesize 1048576 \
        --request DELETE -- "$API_ROOT/sandboxes/$sandbox_id" 2>/dev/null)" || code=''
      [[ "$code" == 204 || "$code" == 404 ]] && break
      sleep 1
    done
    if [[ "$code" == 204 || "$code" == 404 ]]; then
      for attempt in {1..60}; do
        if terminal_state_ready; then
          terminal=yes
          break
        fi
        sleep 1
      done
    fi
    [[ "$terminal" == yes ]] || status=1
  elif [[ "$create_attempted" == yes && "$no_create" != yes ]]; then
    status=1
  fi
  [[ -z "${stage:-}" ]] || rm -rf -- "$stage"
  exit "$status"
}

curl_code() {
  local config="$1" output="$2"
  shift 2
  curl --disable --config "$config" --silent --show-error \
    --output "$output" --write-out '%{http_code}' --max-time 15 \
    --max-filesize 1048576 "$@"
}

curl_create_code() {
  curl --disable --config "$stage/api.curlrc" --silent --show-error \
    --output "$stage/create-response.json" --write-out '%{http_code}' \
    --max-time 180 --max-filesize 1048576 \
    --header 'Content-Type: application/json' --request POST \
    --data-binary "@$stage/create.json" -- "$API_ROOT/sandboxes"
}

build_process_client() {
  local digest client_source
  mkdir -m 0700 -- "$stage/source" "$stage/out" "$stage/go-mod-cache" "$stage/go-build-cache"
  git -C "$KITDEV_INFRA_ROOT" archive --format=tar "$KITDEV_INFRA_COMMIT" \
    --output "$stage/source.tar"
  tar --extract --file "$stage/source.tar" --directory "$stage/source" \
    --no-same-owner --no-same-permissions
  rm -f -- "$stage/source.tar"
  client_source="$stage/source/packages/shared/cmd/kitdev-e2e-process/main.go"
  [[ ! -e "$client_source" && ! -L "$client_source" ]] ||
    control_plane_die e2e_client_source_conflict 65
  install -D -o root -g root -m 0600 -- "$SCRIPT_DIR/e2e-process-client/main.go" \
    "$client_source"
  docker run --rm --pull always --platform linux/amd64 --user 0:0 \
    --volume "$stage/source:/src" \
    --volume "$stage/go-mod-cache:/go/pkg/mod" \
    --volume "$stage/go-build-cache:/root/.cache/go-build" \
    --workdir /src "$BUILDER" go mod download
  docker run --rm --pull never --platform linux/amd64 --network none --user 0:0 \
    --volume "$stage/source:/src:ro" \
    --volume "$stage/out:/out" \
    --volume "$stage/go-mod-cache:/go/pkg/mod:ro" \
    --volume "$stage/go-build-cache:/root/.cache/go-build" \
    --workdir /src "$BUILDER" \
    bash -Eeuo pipefail -c \
      'CGO_ENABLED=0 GOOS=linux GOARCH=amd64 go build -trimpath -buildvcs=false \
       -ldflags "-s -w -buildid=" -o /out/e2e-process-client \
       ./packages/shared/cmd/kitdev-e2e-process'
  chmod 0700 -- "$stage/out/e2e-process-client"
  [[ "$(stat -c '%u:%g:%a:%s:%h' -- "$stage/out/e2e-process-client")" == \
    "0:0:700:$CLIENT_SIZE:1" ]] ||
    control_plane_die e2e_client_metadata_invalid 65
  digest="$(sha256sum -- "$stage/out/e2e-process-client" | awk '{print $1}')"
  [[ "$digest" == "$CLIENT_SHA256" ]] || control_plane_die e2e_client_hash_invalid 65
}

parse_ready_node() {
  /usr/bin/python3 -I -B -S - "$1" "$node_id" <<'PY_READY_NODE'
import json
import sys

document = json.load(open(sys.argv[1], encoding="utf-8"))
if not isinstance(document, list):
    raise SystemExit(1)
nodes = document
ready = [
    node
    for node in nodes
    if isinstance(node, dict) and node.get("id") == sys.argv[2] and node.get("status") == "ready"
]
if len(nodes) != 1 or len(ready) != 1:
    raise SystemExit(1)
print(ready[0]["id"])
PY_READY_NODE
}

template_is_ready() {
  /usr/bin/python3 -I -B -S - "$1" "$BUILD_ID" <<'PY_TEMPLATE_READY'
import json
import re
import sys

document = json.load(open(sys.argv[1], encoding="utf-8"))
if not isinstance(document, list):
    raise SystemExit(1)
matches = [item for item in document if isinstance(item, dict) and item.get("buildID") == sys.argv[2]]
if len(matches) != 1:
    raise SystemExit(1)
item = matches[0]
if (
    item.get("buildStatus") != "ready"
    or item.get("envdVersion") != "0.6.13"
    or item.get("cpuCount") != 2
    or item.get("memoryMB") != 1024
    or item.get("diskSizeMB") != 3722
    or item.get("public") is not False
    or not isinstance(item.get("templateID"), str)
    or not re.fullmatch(r"[a-z0-9]{16,32}", item["templateID"])
):
    raise SystemExit(1)
PY_TEMPLATE_READY
}

extract_sandbox_id() {
  /usr/bin/python3 -I -B -S - "$1" <<'PY_EXTRACT_SANDBOX_ID'
import json
import re
import sys

identifier = json.load(open(sys.argv[1], encoding="utf-8")).get("sandboxID")
if not isinstance(identifier, str) or not re.fullmatch(r"i[a-z0-9]{20}", identifier):
    raise SystemExit(1)
print(identifier)
PY_EXTRACT_SANDBOX_ID
}

validate_create_response() {
  /usr/bin/python3 -I -B -S - "$1" "$sandbox_id" <<'PY_VALIDATE_CREATE'
import json
import sys

document = json.load(open(sys.argv[1], encoding="utf-8"))
if (
    document.get("sandboxID") != sys.argv[2]
    or document.get("templateID") != "2d9a8389-f5f5-4449-b0eb-e1d364ee98ae"
    or not isinstance(document.get("clientID"), str)
    or not document["clientID"]
    or document.get("envdVersion") != "0.6.13"
):
    raise SystemExit(1)
PY_VALIDATE_CREATE
}

wait_for_readiness() {
  local attempt code observed_node=''
  for attempt in {1..90}; do
    if [[ "$(systemctl is-active kitdev-e2b-orchestrator.service 2>/dev/null || true)" == active ]]; then
      code="$(curl --disable --silent --show-error --output /dev/null \
        --write-out '%{http_code}' --max-time 5 --max-filesize 1024 \
        -- "$ORCHESTRATOR_HEALTH" 2>/dev/null)" || code=''
      if [[ "$code" == 200 ]]; then
        code="$(curl_code "$stage/admin.curlrc" "$stage/nodes.json" -- "$API_ROOT/nodes" 2>/dev/null)" ||
          code=''
        if [[ "$code" == 200 ]]; then
          observed_node="$(parse_ready_node "$stage/nodes.json" 2>/dev/null)" || observed_node=''
          if [[ -n "$observed_node" ]]; then
            code="$(curl_code "$stage/api.curlrc" "$stage/templates.json" \
              -- "$API_ROOT/templates" 2>/dev/null)" || code=''
            if [[ "$code" == 200 ]] && template_is_ready "$stage/templates.json"; then
              return 0
            fi
          fi
        fi
      fi
    fi
    sleep 2
  done
  control_plane_die e2e_readiness_timeout 65
}

sandbox_list_state() {
  local expected="$1"
  /usr/bin/python3 -I -B -S - "$stage/sandboxes.json" "$sandbox_id" "$expected" <<'PY_SANDBOX_LIST'
import json
import sys

document = json.load(open(sys.argv[1], encoding="utf-8"))
if not isinstance(document, list):
    raise SystemExit(1)
identifiers = {
    item.get("sandboxID")
    for item in document
    if isinstance(item, dict)
}
present = sys.argv[2] in identifiers
raise SystemExit(0 if present == (sys.argv[3] == "present") else 1)
PY_SANDBOX_LIST
}

sandbox_list_is_empty() {
  /usr/bin/python3 -I -B -S - "$stage/sandboxes.json" <<'PY_SANDBOX_LIST_EMPTY'
import json
import sys

document = json.load(open(sys.argv[1], encoding="utf-8"))
raise SystemExit(0 if isinstance(document, list) and not document else 1)
PY_SANDBOX_LIST_EMPTY
}

recover_target_sandbox_id() {
  /usr/bin/python3 -I -B -S - "$stage/sandboxes.json" "$BUILD_ID" <<'PY_RECOVER_TARGET'
import json
import re
import sys

document = json.load(open(sys.argv[1], encoding="utf-8"))
if not isinstance(document, list):
    raise SystemExit(1)
matches = [
    item.get("sandboxID")
    for item in document
    if isinstance(item, dict)
    and item.get("templateID") == sys.argv[2]
    and isinstance(item.get("sandboxID"), str)
    and re.fullmatch(r"i[a-z0-9]{20}", item["sandboxID"])
]
if len(matches) != 1:
    raise SystemExit(1)
print(matches[0])
PY_RECOVER_TARGET
}

fetch_sandbox_list() {
  local code
  code="$(curl_code "$stage/api.curlrc" "$stage/sandboxes.json" -- "$API_ROOT/sandboxes")" ||
    control_plane_die e2e_sandbox_list_unreachable 65
  [[ "$code" == 200 ]] || control_plane_die e2e_sandbox_list_failed 65
}

terminal_state_ready() {
  local code redis_container
  code="$(curl_code "$stage/api.curlrc" "$stage/sandboxes.json" \
    -- "$API_ROOT/sandboxes" 2>/dev/null)" || return 1
  [[ "$code" == 200 ]] && sandbox_list_state absent || return 1
  ! pgrep -x firecracker >/dev/null 2>&1 || return 1
  redis_container="$(docker ps --quiet \
    --filter label=com.docker.compose.project=kitdev-control-plane \
    --filter label=com.docker.compose.service=redis)"
  [[ "$redis_container" =~ ^[0-9a-f]{64}$ ]] || return 1
  : >"$stage/redis-keys"
  (
    local -a statuses
    set +e
    timeout 10 docker exec -- "$redis_container" \
      redis-cli --raw --scan --pattern "*$sandbox_id*" 2>/dev/null |
      head -c 4097 >"$stage/redis-keys"
    statuses=("${PIPESTATUS[@]}")
    set -e
    [[ "${statuses[1]}" == 0 &&
      ("${statuses[0]}" == 0 || "${statuses[0]}" == 141) ]]
  ) || return 1
  [[ "$(stat -c %s -- "$stage/redis-keys")" -le 4096 && ! -s "$stage/redis-keys" ]]
}

verify_terminal_state() {
  local attempt
  for attempt in {1..60}; do
    terminal_state_ready && return 0
    sleep 1
  done
  control_plane_die e2e_terminal_state_timeout 65
}

main() {
  local api_key_file='' code attempt node_rows firecracker_count
  [[ "${1:-}" == --api-key-file && $# == 2 ]] || control_plane_die invalid_arguments 64
  api_key_file="$2"
  require_root
  require_lifecycle_platform
  [[ "$KITDEV_LIFECYCLE" != production ]] || control_plane_die e2e_not_for_production 68
  require_command curl
  require_command docker
  require_command flock
  require_command git
  require_command head
  require_command pgrep
  require_command sha256sum
  require_command systemctl
  require_command tar
  require_command timeout
  require_clean_infra_checkout

  ensure_directory /run/kitdev-sandboxes root root 700
  if [[ ! -e /run/kitdev-sandboxes/api-proxy-e2e.lock &&
    ! -L /run/kitdev-sandboxes/api-proxy-e2e.lock ]]; then
    install -o root -g root -m 0600 /dev/null /run/kitdev-sandboxes/api-proxy-e2e.lock
  fi
  [[ ! -L /run/kitdev-sandboxes/api-proxy-e2e.lock &&
    -f /run/kitdev-sandboxes/api-proxy-e2e.lock &&
    "$(stat -c '%u:%g:%a:%s:%h' /run/kitdev-sandboxes/api-proxy-e2e.lock)" == '0:0:600:0:1' ]] ||
    control_plane_die e2e_lock_metadata_invalid 65
  exec 9<>/run/kitdev-sandboxes/api-proxy-e2e.lock
  [[ "$(stat -Lc '%d:%i' /proc/$$/fd/9)" == \
    "$(stat -Lc '%d:%i' /run/kitdev-sandboxes/api-proxy-e2e.lock)" ]] ||
    control_plane_die e2e_lock_changed 65
  flock --nonblock 9 || control_plane_die e2e_already_running 75
  trap cleanup EXIT
  trap 'exit 130' INT TERM
  stage="$(mktemp -d /run/kitdev-sandboxes/api-proxy-e2e.XXXXXXXX)"
  chmod 0700 -- "$stage"
  "$SCRIPT_DIR/install-orchestrator-service.sh" verify >/dev/null
  "$SCRIPT_DIR/configure-firewall.sh" verify >/dev/null
  require_exact_file "$EXPECTED_ORCHESTRATOR_ENV" /etc/kitdev-sandboxes/orchestrator.env \
    root root 600
  node_rows="$(sed -n 's/^NODE_ID=//p' "$EXPECTED_ORCHESTRATOR_ENV")"
  [[ "$node_rows" != *$'\n'* && "$node_rows" =~ ^[a-z0-9-]{1,64}$ ]] ||
    control_plane_die e2e_node_id_invalid 65
  node_id="$node_rows"
  /usr/bin/python3 -I -B -S "$SCRIPT_DIR/private_env.py" \
    write-e2e-curl-configs "$api_key_file" "$stage" >/dev/null
  "$SCRIPT_DIR/seed-local-template.sh" >/dev/null
  "$SCRIPT_DIR/replay-compose.sh" verify >/dev/null
  ! pgrep -x firecracker >/dev/null 2>&1 || control_plane_die e2e_preexisting_firecracker 65
  build_process_client
  wait_for_readiness
  fetch_sandbox_list
  sandbox_list_is_empty || control_plane_die e2e_sandbox_baseline_not_empty 65
  ! pgrep -x firecracker >/dev/null 2>&1 || control_plane_die e2e_firecracker_baseline_not_empty 65

  printf '%s\n' "{\"templateID\":\"$BUILD_ID\",\"timeout\":600}" >"$stage/create.json"
  chmod 0600 -- "$stage/create.json"
  create_attempted=yes
  code="$(curl_create_code)" ||
    control_plane_die e2e_sandbox_create_unreachable 65
  chmod 0600 -- "$stage/create-response.json"
  [[ "$code" == 201 ]] || control_plane_die e2e_sandbox_create_failed 65
  sandbox_id="$(extract_sandbox_id "$stage/create-response.json")" ||
    control_plane_die e2e_sandbox_id_invalid 65
  validate_create_response "$stage/create-response.json" ||
    control_plane_die e2e_sandbox_response_invalid 65

  for attempt in {1..30}; do
    fetch_sandbox_list
    sandbox_list_state present && break
    sleep 1
  done
  sandbox_list_state present || control_plane_die e2e_sandbox_not_listed 65
  firecracker_count="$({ pgrep -x firecracker || true; } | wc -l | tr -d ' ')"
  [[ "$firecracker_count" == 1 ]] || control_plane_die e2e_firecracker_count_invalid 65
  "$stage/out/e2e-process-client" "$sandbox_id" >/dev/null ||
    control_plane_die e2e_proxy_command_failed 65
  code="$(curl_code "$stage/api.curlrc" /dev/null --request DELETE \
    -- "$API_ROOT/sandboxes/$sandbox_id")" || control_plane_die e2e_sandbox_delete_unreachable 65
  [[ "$code" == 204 ]] || control_plane_die e2e_sandbox_delete_failed 65
  verify_terminal_state
  sandbox_id=''
  create_attempted=no
  printf 'status=pass operation=verify-api-proxy-e2e\n'
}

main "$@"
