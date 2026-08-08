#!/usr/bin/env bash
set -Eeuo pipefail
readonly SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
# shellcheck source=common.sh
source "$SCRIPT_DIR/common.sh"
umask 077

readonly CLIENT_DIR="$SCRIPT_DIR/e2e-typescript-sdk"
readonly STATE_TOOL="$SCRIPT_DIR/template-publication-state.py"
readonly NODE_IMAGE='docker.io/library/node:22.18.0-bookworm-slim@sha256:752ea8a2f758c34002a0461bd9f1cee4f9a3c36d48494586f60ffce1fc708e0e'
readonly SDK_LOCK_SHA256=490c2920ffce8e59f8edd9e9d7951b0f13f93521a851355e7c72e99ad134766c
readonly BROWSER_LOCK_SHA256=db5404269854f530b030d7c31b7ce8c0cd05e7182978af49c58b5e488f87c873
readonly HEAVY_PROFILE_SHA256=8b5b4bf0fb93361eceb30360b155b7ce2e6c92a65fe586ab34fcf696acae1c5b
readonly API_ROOT=http://127.0.0.1:3000
readonly JOURNAL_ROOT=/var/lib/kitdev-sandboxes/template-publication
readonly LIFECYCLE_LOCK=/run/kitdev-sandboxes/control-plane-lifecycle.lock
readonly SDK_LOCK=/run/kitdev-sandboxes/typescript-sdk-e2e.lock
stage=''

cleanup() {
  local status=$?
  trap - EXIT INT TERM
  [[ -z "$stage" ]] || rm -rf -- "$stage"
  exit "$status"
}

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
    if (not stat.S_ISREG(opened.st_mode) or opened.st_uid != 0 or opened.st_gid != 0
            or stat.S_IMODE(opened.st_mode) != 0o600 or opened.st_nlink != 1
            or (before.st_dev, before.st_ino) != (opened.st_dev, opened.st_ino)
            or opened.st_size > 45):
        raise SystemExit(1)
    data = os.read(descriptor, 46)
    if os.read(descriptor, 1):
        raise SystemExit(1)
finally:
    os.close(descriptor)
if not re.fullmatch(rb"e2b_[0-9a-f]{40}\n?", data):
    raise SystemExit(1)
payload = b'header = "X-API-Key: ' + data.rstrip(b"\n") + b'"\n'
output = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
try:
    os.write(output, payload)
    os.fsync(output)
finally:
    os.close(output)
PY_API_CONFIG
}

state_field() {
  /usr/bin/python3 -I -B -S -c \
    'import json,sys; value=json.load(sys.stdin).get(sys.argv[1]); print("" if value is None else value)' "$1"
}

postgres_identity() {
  local container="$1" user result
  for user in kitdev postgres; do
    result="$(docker exec -- "$container" psql --no-psqlrc --tuples-only --no-align \
      --username "$user" --dbname "$user" --command \
      "SELECT to_regclass('public.envs') IS NOT NULL AND to_regclass('public.env_aliases') IS NOT NULL;" \
      2>/dev/null)" || continue
    if [[ "$result" == t ]]; then
      printf '%s\n' "$user"
      return
    fi
  done
  return 1
}

query_database() {
  local query="$1" container user
  container="$(control_plane_container postgres)"
  [[ "$container" =~ ^[0-9a-f]{64}$ ]] || control_plane_die postgres_container_invalid 65
  user="$(postgres_identity "$container")" || control_plane_die postgres_identity_invalid 65
  docker exec -- "$container" psql --no-psqlrc --set=ON_ERROR_STOP=1 \
    --tuples-only --no-align --username "$user" --dbname "$user" --command "$query"
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

require_heavy_profile() {
  local api_key_file="$1" key_hash row
  local huge_free available_kib
  huge_free="$(awk '$1 == "HugePages_Free:" {print $2}' /proc/meminfo)"
  available_kib="$(awk '$1 == "MemAvailable:" {print $2}' /proc/meminfo)"
  [[ "$huge_free" =~ ^[0-9]+$ && "$available_kib" =~ ^[0-9]+$ ]] ||
    control_plane_die heavy_capacity_unknown 65
  (( huge_free >= 12288 )) || control_plane_die heavy_hugepages_free_insufficient 65
  (( available_kib >= 16777216 )) || control_plane_die heavy_normal_memory_insufficient 65
  key_hash="$(api_key_hash "$api_key_file")" || control_plane_die heavy_api_key_hash_failed 65
  [[ "$key_hash" =~ ^\$sha256\$[A-Za-z0-9+/]{43}$ ]] || control_plane_die heavy_api_key_hash_invalid 65
  row="$(query_database "
SELECT t.slug || '|' || l.concurrent_sandboxes || '|' || l.concurrent_template_builds ||
       '|' || l.max_vcpu || '|' || l.max_ram_mb || '|' || l.disk_mb ||
       '|' || l.default_free_disk_size_mb || '|' || l.max_disk_size_mb
FROM public.team_api_keys k JOIN public.teams t ON t.id=k.team_id
JOIN public.team_limits l ON l.id=t.id WHERE k.api_key_hash='$key_hash';")" ||
    control_plane_die heavy_team_query_failed 65
  [[ "$row" == 'kitdev-browser-heavy-team|1|1|2|8192|16384|16384|25600' ]] ||
    control_plane_die heavy_team_profile_invalid 65
}

ensure_lock() {
  local path="$1"
  if [[ ! -e "$path" && ! -L "$path" ]]; then
    install -o root -g root -m 0600 /dev/null "$path"
  fi
  [[ ! -L "$path" && -f "$path" &&
    "$(stat -c '%u:%g:%a:%s:%h' -- "$path")" == '0:0:600:0:1' ]] ||
    control_plane_die template_publication_lock_invalid 65
}

acquire_locks() {
  ensure_directory /run/kitdev-sandboxes root root 700
  ensure_lock "$LIFECYCLE_LOCK"
  ensure_lock "$SDK_LOCK"
  exec 8<>"$LIFECYCLE_LOCK"
  flock --nonblock 8 || control_plane_die lifecycle_operation_running 75
  exec 9<>"$SDK_LOCK"
  flock --nonblock 9 || control_plane_die sdk_e2e_already_running 75
}

definition_hash() {
  local product="$1"
  if [[ "$product" == coding ]]; then
    sha256sum -- "$CLIENT_DIR/package.json" "$CLIENT_DIR/package-lock.json" \
      "$CLIENT_DIR/coding-template.ts" | awk '{print $1}' | sha256sum | awk '{print $1}'
  else
    sha256sum -- "$CLIENT_DIR/package.json" "$CLIENT_DIR/package-lock.json" \
      "$CLIENT_DIR/browser-template.ts" \
      "$CLIENT_DIR/browser-resource-profiles/heavy.json" \
      "$CLIENT_DIR/browser-template-assets/acceptance.mjs" \
      "$CLIENT_DIR/browser-template-assets/package.json" \
      "$CLIENT_DIR/browser-template-assets/package-lock.json" \
      "$CLIENT_DIR/browser-template-assets/start-browser.mjs" |
      awk '{print $1}' | sha256sum | awk '{print $1}'
  fi
}

require_idle_legacy_host() {
  local active_builds orchestrators
  orchestrators="$({ pgrep -x orchestrator || true; } | wc -l | tr -d ' ')"
  [[ "$orchestrators" == 1 ]] || control_plane_die legacy_orchestrator_count_invalid 65
  [[ "$(curl --config /dev/null --silent --output /dev/null --write-out '%{http_code}' \
    --max-time 5 -- "$API_ROOT/health")" == 200 ]] || control_plane_die api_unhealthy 65
  ! pgrep -x firecracker >/dev/null 2>&1 || control_plane_die publication_firecracker_running 65
  active_builds="$(query_database "SELECT count(*) FROM public.env_builds WHERE status_group IN ('pending','in_progress');")"
  [[ "$active_builds" == 0 ]] || control_plane_die publication_build_running 65
}

verify_publication() {
  local alias="$1" version="$2" template_id="$3" build_id="$4" product="$5" row expected
  row="$(query_database "
WITH stable AS (
  SELECT build_id FROM public.env_build_assignments
  WHERE env_id='$template_id' AND tag='stable'
  ORDER BY created_at DESC, build_id DESC LIMIT 1
)
SELECT count(*) FROM public.envs e
WHERE e.id='$template_id' AND e.public IS TRUE AND e.deleted_at IS NULL AND e.source='template'
  AND EXISTS (SELECT 1 FROM public.env_aliases a WHERE a.env_id=e.id AND a.alias='$alias' AND a.namespace IS NULL)
  AND EXISTS (SELECT 1 FROM public.env_build_assignments a JOIN public.env_builds b ON b.id=a.build_id
              WHERE a.env_id=e.id AND a.tag='$version' AND a.build_id='$build_id'::uuid AND b.status_group='ready')
  AND (SELECT build_id FROM stable)='$build_id'::uuid;")" || control_plane_die publication_database_verify_failed 65
  [[ "$row" == 1 ]] || control_plane_die publication_database_drift 65
  if [[ "$product" == coding ]]; then
    expected='2|2048|ready'
  else
    expected='2|8192|16384|ready'
  fi
  row="$(query_database "SELECT vcpu || '|' || ram_mb ||
    CASE WHEN '$product'='coding' THEN '' ELSE '|' || free_disk_size_mb END || '|' || status_group
    FROM public.env_builds WHERE id='$build_id'::uuid AND env_id='$template_id';")" ||
    control_plane_die publication_build_verify_failed 65
  [[ "$row" == "$expected" ]] || control_plane_die publication_build_profile_drift 65
}

prepare_stage() {
  local product="$1" alias="$2" version="$3" profile
  stage="$(mktemp -d /run/kitdev-sandboxes/stable-template.XXXXXXXX)"
  chmod 0700 -- "$stage"
  install -d -o root -g root -m 0700 "$stage/client" "$stage/config" "$stage/state"
  install -o root -g root -m 0600 "$CLIENT_DIR/package.json" "$stage/client/package.json"
  install -o root -g root -m 0600 "$CLIENT_DIR/package-lock.json" "$stage/client/package-lock.json"
  printf '{"alias":"%s","schemaVersion":1,"version":"%s"}\n' "$alias" "$version" \
    >"$stage/config/template-publication.json"
  chmod 0600 -- "$stage/config/template-publication.json"
  if [[ "$product" == coding ]]; then
    install -o root -g root -m 0600 "$CLIENT_DIR/coding-template.ts" "$stage/client/coding-template.ts"
  else
    install -d -o root -g root -m 0700 "$stage/client/browser-template-assets"
    install -o root -g root -m 0600 "$CLIENT_DIR/browser-template.ts" "$stage/client/browser-template.ts"
    install -o root -g root -m 0600 "$CLIENT_DIR/browser-template-assets/"* "$stage/client/browser-template-assets/"
    profile="$CLIENT_DIR/browser-resource-profiles/heavy.json"
    install -o root -g root -m 0600 "$profile" "$stage/config/browser-resource-profile.json"
  fi
}

install_sdk() {
  docker pull --platform linux/amd64 "$NODE_IMAGE" >/dev/null
  docker run --rm --pull never --platform linux/amd64 --user 0:0 \
    --volume "$stage/client:/workspace" --workdir /workspace "$NODE_IMAGE" \
    npm ci --ignore-scripts --no-audit --no-fund >/dev/null
  [[ "$(docker run --rm --pull never --platform linux/amd64 --network none \
    --volume "$stage/client:/workspace:ro" --workdir /workspace "$NODE_IMAGE" \
    node -p "require('./node_modules/e2b/package.json').version")" == 2.38.0 ]] ||
    control_plane_die sdk_installed_version_invalid 65
}

run_client() {
  local product="$1" script
  [[ "$product" == coding ]] && script=coding-template.ts || script=browser-template.ts
  docker run --rm --pull never --platform linux/amd64 --network host --user 0:0 \
    --read-only --tmpfs /tmp:rw,nosuid,nodev,noexec,mode=1777,size=64m \
    --volume "$stage/client:/workspace:ro" \
    --volume "$api_key_file:/run/secrets/e2b-api-key:ro" \
    --volume "$stage/config:/run/config:ro" \
    --volume "$stage/state:/run/state" --workdir /workspace \
    "$NODE_IMAGE" node "$script"
}

read_state_value() {
  local path="$1" pattern="$2"
  [[ ! -L "$path" && -f "$path" && "$(stat -c '%u:%g:%a:%h' -- "$path")" == '0:0:600:1' ]] || return 1
  local value
  value="$(<"$path")"
  [[ "$value" =~ $pattern ]] || return 1
  printf '%s\n' "$value"
}

publish_template() {
  local product="$1" alias="$2" version="$3" journal="$4" digest="$5"
  local document state template_id build_id alias_count code
  local key_hash reclaim_count reclaim_dir debris_id team_count
  document="$("$STATE_TOOL" reserve --journal "$journal" --product "$product" \
    --version "$version" --definition-sha256 "$digest")" || control_plane_die publication_reserve_failed 65
  state="$(printf '%s' "$document" | state_field state)"
  template_id="$(printf '%s' "$document" | state_field template_id)"
  build_id="$(printf '%s' "$document" | state_field build_id)"
  if [[ "$state" == published ]]; then
    verify_publication "$alias" "$version" "$template_id" "$build_id" "$product"
    printf 'status=pass operation=publish-stable-template result=unchanged product=%s alias=%s:%s\n' \
      "$product" "$alias" "$version"
    return
  fi
  if [[ "$state" == qualified_private ]]; then
    :
  elif [[ "$state" == reserved ]]; then
    # Refuse an alias that is anyone else's, but not our own debris. A build
    # that fails leaves its env and alias behind, so demanding the alias not
    # exist at all meant one failed build permanently blocked every later
    # publish of that product -- recoverable only by deleting rows by hand.
    # An alias is reclaimable only when it is private, owned by this very API
    # key's team, and has no build that did not fail. Anything else still
    # refuses, including a previously published template.
    key_hash="$(api_key_hash "$api_key_file")" ||
      control_plane_die publication_api_key_hash_failed 65
    [[ "$key_hash" =~ ^\$sha256\$[A-Za-z0-9+/]{43}$ ]] ||
      control_plane_die publication_api_key_hash_invalid 65
    # Resolve the team first and fail closed if it does not. The guard below
    # compares against a scalar subquery, and if that subquery yields NULL the
    # whole NOT(...) is NULL, the row is dropped, and the count reads 0 -- so an
    # unresolvable key would silently permit the very thing the guard refuses.
    team_count="$(query_database \
      "SELECT count(*) FROM public.team_api_keys WHERE api_key_hash='$key_hash';")"
    [[ "$team_count" == 1 ]] || control_plane_die publication_api_key_team_unresolved 65
    alias_count="$(query_database "
SELECT count(*) FROM public.env_aliases a JOIN public.envs e ON e.id = a.env_id
WHERE a.alias='$alias' AND NOT (
  e.public = false
  AND e.team_id = (SELECT k.team_id FROM public.team_api_keys k
                   WHERE k.api_key_hash='$key_hash')
  AND NOT EXISTS (SELECT 1 FROM public.env_builds b
                  WHERE b.env_id = e.id AND b.status <> 'failed')
);")"
    [[ "$alias_count" == 0 ]] || control_plane_die publication_alias_not_owned 65
    # Debris is removed, not merely tolerated. The client's first assertion is
    # that the template does not exist, which is what makes a publish a genuine
    # first build rather than a silent overwrite -- so leaving the failed
    # attempt in place would trade one refusal for another. Deleting through the
    # authenticated API rather than SQL keeps the API's own ownership rules in
    # force, and the reclaim query above has already established the alias is
    # this team's private, never-successfully-built leftover.
    # Deleted by template id, never by alias. An alias can be repointed between
    # the read and the delete; an id cannot, and the rollback path already
    # deletes by id for the same reason.
    debris_id="$(query_database "
SELECT a.env_id FROM public.env_aliases a JOIN public.envs e ON e.id = a.env_id
WHERE a.alias='$alias' AND e.public = false
  AND e.team_id = (SELECT k.team_id FROM public.team_api_keys k
                   WHERE k.api_key_hash='$key_hash')
  AND NOT EXISTS (SELECT 1 FROM public.env_builds b
                  WHERE b.env_id = e.id AND b.status <> 'failed');")"
    if [[ -n "$debris_id" ]]; then
      [[ "$debris_id" =~ ^[a-z0-9]{16,32}$ ]] || control_plane_die publication_debris_id_invalid 65
      reclaim_dir="$(mktemp -d /run/kitdev-sandboxes/stable-template-reclaim.XXXXXXXX)"
      chmod 0700 -- "$reclaim_dir"
      write_api_config "$api_key_file" "$reclaim_dir/api.curlrc" || {
        rm -rf -- "$reclaim_dir"
        control_plane_die sdk_api_key_invalid 65
      }
      code="$(curl --disable --config "$reclaim_dir/api.curlrc" --silent --show-error \
        --output /dev/null --write-out '%{http_code}' --max-time 30 \
        --request DELETE -- "$API_ROOT/templates/$debris_id")" || {
        rm -rf -- "$reclaim_dir"
        control_plane_die publication_debris_delete_failed 65
      }
      rm -rf -- "$reclaim_dir"
      [[ "$code" == 200 || "$code" == 204 ]] ||
        control_plane_die publication_debris_delete_rejected 65
      reclaim_count="$(query_database \
        "SELECT count(*) FROM public.env_aliases WHERE alias='$alias';")"
      [[ "$reclaim_count" == 0 ]] || control_plane_die publication_debris_delete_incomplete 65
      printf 'note=publication-debris-reclaimed alias=%s template=%s\n' "$alias" "$debris_id"
    fi
    prepare_stage "$product" "$alias" "$version"
    write_api_config "$api_key_file" "$stage/api.curlrc" || control_plane_die sdk_api_key_invalid 65
    install_sdk
    run_client "$product"
    template_id="$(read_state_value "$stage/state/template-id" '^[a-z0-9]{16,32}$')" ||
      control_plane_die publication_template_id_invalid 65
    build_id="$(read_state_value "$stage/state/build-id" '^[0-9a-f-]{36}$')" ||
      control_plane_die publication_build_id_invalid 65
    "$STATE_TOOL" candidate --journal "$journal" --product "$product" --version "$version" \
      --definition-sha256 "$digest" --template-id "$template_id" --build-id "$build_id" >/dev/null ||
      control_plane_die publication_candidate_record_failed 65
  else
    control_plane_die publication_state_invalid 65
  fi
  [[ -n "$stage" ]] || {
    stage="$(mktemp -d /run/kitdev-sandboxes/stable-template.XXXXXXXX)"
    chmod 0700 -- "$stage"
    write_api_config "$api_key_file" "$stage/api.curlrc" || control_plane_die sdk_api_key_invalid 65
  }
  code="$(curl --disable --config "$stage/api.curlrc" --silent --show-error \
    --output /dev/null --write-out '%{http_code}' --max-time 30 \
    --header 'Content-Type: application/json' --request PATCH \
    --data-binary '{"public":true}' -- "$API_ROOT/templates/$alias")" ||
    control_plane_die publication_public_patch_failed 65
  [[ "$code" == 200 ]] || control_plane_die publication_public_patch_rejected 65
  verify_publication "$alias" "$version" "$template_id" "$build_id" "$product"
  "$STATE_TOOL" publish --journal "$journal" --product "$product" --version "$version" \
    --definition-sha256 "$digest" >/dev/null || control_plane_die publication_commit_failed 65
  printf 'status=pass operation=publish-stable-template result=published product=%s alias=%s:%s stable=%s:stable\n' \
    "$product" "$alias" "$version" "$alias"
}

rollback_template() {
  local product="$1" alias="$2" version="$3" journal="$4" digest="$5"
  local document state template_id build_id code
  document="$("$STATE_TOOL" show --journal "$journal" --product "$product" \
    --version "$version" --definition-sha256 "$digest")" || control_plane_die publication_journal_invalid 65
  state="$(printf '%s' "$document" | state_field state)"
  template_id="$(printf '%s' "$document" | state_field template_id)"
  build_id="$(printf '%s' "$document" | state_field build_id)"
  [[ "$state" != rolled_back ]] || {
    printf 'status=pass operation=rollback-stable-template result=unchanged product=%s\n' "$product"
    return
  }
  prepare_stage "$product" "$alias" "$version"
  write_api_config "$api_key_file" "$stage/api.curlrc" || control_plane_die sdk_api_key_invalid 65
  if [[ "$state" == qualified_private ]]; then
    code="$(curl --disable --config "$stage/api.curlrc" --silent --show-error \
      --output /dev/null --write-out '%{http_code}' --max-time 30 --request DELETE \
      -- "$API_ROOT/templates/$template_id")" || code=''
    [[ "$code" == 204 || "$code" == 404 ]] || control_plane_die candidate_delete_failed 65
  elif [[ "$state" == published ]]; then
    install -o root -g root -m 0600 "$CLIENT_DIR/template-publication-release.ts" \
      "$stage/client/template-publication-release.ts"
    install_sdk
    docker run --rm --pull never --platform linux/amd64 --network host --user 0:0 \
      --read-only --tmpfs /tmp:rw,nosuid,nodev,noexec,mode=1777,size=32m \
      --volume "$stage/client:/workspace:ro" \
      --volume "$api_key_file:/run/secrets/e2b-api-key:ro" \
      --volume "$stage/config:/run/config:ro" --workdir /workspace \
      "$NODE_IMAGE" node template-publication-release.ts remove-stable
  else
    control_plane_die rollback_state_invalid 65
  fi
  "$STATE_TOOL" rollback --journal "$journal" --product "$product" --version "$version" \
    --definition-sha256 "$digest" >/dev/null || control_plane_die rollback_journal_failed 65
  printf 'status=pass operation=rollback-stable-template product=%s template_id=%s build_id=%s\n' \
    "$product" "$template_id" "$build_id"
}

verify_consumer() {
  local product="$1" alias="$2" version="$3"
  prepare_stage "$product" "$alias" "$version"
  install -o root -g root -m 0600 "$CLIENT_DIR/stable-template-consumer.ts" \
    "$stage/client/stable-template-consumer.ts"
  install_sdk
  docker run --rm --pull never --platform linux/amd64 --network host --user 0:0 \
    --read-only --tmpfs /tmp:rw,nosuid,nodev,noexec,mode=1777,size=32m \
    --volume "$stage/client:/workspace:ro" \
    --volume "$api_key_file:/run/secrets/e2b-api-key:ro" \
    --volume "$stage/config:/run/config:ro" --workdir /workspace \
    "$NODE_IMAGE" node stable-template-consumer.ts
}

main() {
  local operation product version alias digest journal
  [[ $# == 7 && "$2" == --product && "$4" == --version && "$6" == --api-key-file ]] ||
    control_plane_die invalid_arguments 64
  operation="$1"
  product="$3"
  version="$5"
  api_key_file="$7"
  [[ "$operation" == publish || "$operation" == rollback || "$operation" == verify-consumer ]] ||
    control_plane_die invalid_operation 64
  [[ "$version" =~ ^v[1-9][0-9]{0,5}$ ]] || control_plane_die publication_version_invalid 64
  case "$product" in
    coding) alias=kitdev-coding ;;
    browser-heavy) alias=kitdev-browser-heavy ;;
    *) control_plane_die publication_product_invalid 64 ;;
  esac
  require_root
  require_lifecycle_platform
  [[ "$KITDEV_LIFECYCLE" != production ]] || control_plane_die legacy_publication_not_for_production 68
  for command in curl docker flock pgrep sha256sum; do require_command "$command"; done
  [[ "$(sha256sum -- "$CLIENT_DIR/package-lock.json" | awk '{print $1}')" == "$SDK_LOCK_SHA256" ]] ||
    control_plane_die sdk_lock_hash_invalid 65
  if [[ "$product" == browser-heavy ]]; then
    [[ "$(sha256sum -- "$CLIENT_DIR/browser-template-assets/package-lock.json" | awk '{print $1}')" == "$BROWSER_LOCK_SHA256" ]] ||
      control_plane_die browser_lock_hash_invalid 65
    [[ "$(sha256sum -- "$CLIENT_DIR/browser-resource-profiles/heavy.json" | awk '{print $1}')" == "$HEAVY_PROFILE_SHA256" ]] ||
      control_plane_die browser_resource_profile_hash_invalid 65
  fi
  [[ ! -L "$api_key_file" && -f "$api_key_file" &&
    "$(stat -c '%u:%g:%a:%h' -- "$api_key_file")" == '0:0:600:1' ]] ||
    control_plane_die sdk_api_key_file_invalid 65
  ensure_directory "$JOURNAL_ROOT" root root 700
  acquire_locks
  require_idle_legacy_host
  [[ "$product" != browser-heavy ]] || require_heavy_profile "$api_key_file"
  trap cleanup EXIT
  trap 'exit 130' INT TERM
  if [[ "$operation" == publish ]]; then
    digest="$(definition_hash "$product")"
    [[ "$digest" =~ ^[0-9a-f]{64}$ ]] || control_plane_die publication_definition_hash_invalid 65
    journal="$JOURNAL_ROOT/$product-$version.json"
    publish_template "$product" "$alias" "$version" "$journal" "$digest"
  elif [[ "$operation" == rollback ]]; then
    digest="$(definition_hash "$product")"
    [[ "$digest" =~ ^[0-9a-f]{64}$ ]] || control_plane_die publication_definition_hash_invalid 65
    journal="$JOURNAL_ROOT/$product-$version.json"
    rollback_template "$product" "$alias" "$version" "$journal" "$digest"
  else
    verify_consumer "$product" "$alias" "$version"
  fi
}

main "$@"
