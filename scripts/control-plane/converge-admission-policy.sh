#!/usr/bin/env bash
set -Eeuo pipefail
readonly SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
# shellcheck source=common.sh
source "$SCRIPT_DIR/common.sh"

readonly LIFECYCLE_LOCK=/run/kitdev-sandboxes/control-plane-lifecycle.lock
readonly SDK_LOCK=/run/kitdev-sandboxes/typescript-sdk-e2e.lock

postgres_identity() {
  local container="$1" user result
  for user in kitdev postgres; do
    result="$(docker exec -- "$container" psql --no-psqlrc --tuples-only --no-align \
      --username "$user" --dbname "$user" --command \
      "SELECT to_regclass('public.project_limits') IS NOT NULL AND to_regclass('public.team_limits') IS NOT NULL;" \
      2>/dev/null)" || continue
    if [[ "$result" == t ]]; then
      printf '%s\n' "$user"
      return
    fi
  done
  return 1
}

ensure_lock() {
  local path="$1"
  if [[ ! -e "$path" && ! -L "$path" ]]; then
    install -o root -g root -m 0600 /dev/null "$path"
  fi
  [[ ! -L "$path" && -f "$path" &&
    "$(stat -c '%u:%g:%a:%s:%h' -- "$path")" == '0:0:600:0:1' ]] ||
    control_plane_die admission_lock_invalid 65
}

verify_policy() {
  local container="$1" user="$2" row total sandboxes builds vcpu ram disk free_disk max_disk
  row="$(docker exec -- "$container" psql --no-psqlrc --set=ON_ERROR_STOP=1 \
    --tuples-only --no-align --field-separator='|' --username "$user" --dbname "$user" --command "
SELECT count(*),
       count(*) FILTER (WHERE concurrent_sandboxes = 1),
       count(*) FILTER (WHERE concurrent_template_builds = 1),
       count(*) FILTER (WHERE max_vcpu <= 2),
       count(*) FILTER (WHERE max_ram_mb <= 8192),
       count(*) FILTER (WHERE disk_mb <= 16384),
       count(*) FILTER (WHERE default_free_disk_size_mb <= 16384),
       count(*) FILTER (WHERE max_disk_size_mb <= 25600)
FROM public.team_limits;")" || control_plane_die admission_policy_query_failed 65
  IFS='|' read -r total sandboxes builds vcpu ram disk free_disk max_disk <<<"$row"
  [[ "$total" =~ ^[0-9]+$ && "$total" -gt 0 && "$sandboxes" == "$total" &&
    "$builds" == "$total" && "$vcpu" == "$total" && "$ram" == "$total" &&
    "$disk" == "$total" && "$free_disk" == "$total" && "$max_disk" == "$total" ]] ||
    control_plane_die admission_policy_drift 65
  printf '%s' "$total"
}

main() {
  local mode postgres_container postgres_user redis_container team_count key deleted=0
  [[ $# == 1 && ("$1" == --check || "$1" == --apply) ]] || control_plane_die invalid_arguments 64
  mode="$1"
  require_root
  require_lifecycle_platform
  require_command docker
  require_command flock
  require_command pgrep
  ensure_directory /run/kitdev-sandboxes root root 700
  ensure_lock "$LIFECYCLE_LOCK"
  ensure_lock "$SDK_LOCK"
  exec 8<>"$LIFECYCLE_LOCK"
  flock --nonblock 8 || control_plane_die lifecycle_operation_running 75
  exec 9<>"$SDK_LOCK"
  flock --nonblock 9 || control_plane_die sdk_e2e_already_running 75

  postgres_container="$(control_plane_container postgres)"
  [[ "$postgres_container" =~ ^[0-9a-f]{64}$ ]] || control_plane_die postgres_container_invalid 65
  postgres_user="$(postgres_identity "$postgres_container")" || control_plane_die postgres_identity_invalid 65

  if [[ "$mode" == --apply ]]; then
    ! pgrep -x firecracker >/dev/null 2>&1 || control_plane_die admission_firecracker_running 65
    [[ "$(docker exec -- "$postgres_container" psql --no-psqlrc --set=ON_ERROR_STOP=1 \
      --tuples-only --no-align --username "$postgres_user" --dbname "$postgres_user" --command \
      "SELECT count(*) FROM public.env_builds WHERE status_group IN ('pending', 'in_progress');")" == 0 ]] ||
      control_plane_die admission_build_running 65
    docker exec --interactive "$postgres_container" psql --no-psqlrc --set=ON_ERROR_STOP=1 \
      --username "$postgres_user" --dbname "$postgres_user" <<'SQL_ADMISSION' >/dev/null
BEGIN;
INSERT INTO public.project_limits (
  team_id, max_length_hours, concurrent_sandboxes,
  concurrent_template_builds, max_vcpu, max_ram_mb, disk_mb,
  events_ttl_days, default_free_disk_size_mb, max_disk_size_mb
)
SELECT id, max_length_hours, 1, 1, LEAST(max_vcpu, 2), LEAST(max_ram_mb, 8192),
       LEAST(disk_mb, 16384), events_ttl_days,
       LEAST(default_free_disk_size_mb, 16384), LEAST(max_disk_size_mb, 25600)
FROM public.team_limits
ON CONFLICT (team_id) DO UPDATE SET
  max_length_hours = EXCLUDED.max_length_hours,
  concurrent_sandboxes = EXCLUDED.concurrent_sandboxes,
  concurrent_template_builds = EXCLUDED.concurrent_template_builds,
  max_vcpu = EXCLUDED.max_vcpu,
  max_ram_mb = EXCLUDED.max_ram_mb,
  disk_mb = EXCLUDED.disk_mb,
  events_ttl_days = EXCLUDED.events_ttl_days,
  default_free_disk_size_mb = EXCLUDED.default_free_disk_size_mb,
  max_disk_size_mb = EXCLUDED.max_disk_size_mb,
  updated_at = now();
COMMIT;
SQL_ADMISSION

    redis_container="$(control_plane_container redis)"
    [[ "$redis_container" =~ ^[0-9a-f]{64}$ ]] || control_plane_die redis_container_invalid 65
    while IFS= read -r key; do
      [[ "$key" == auth:team:* && "$key" != *$'\n'* ]] || control_plane_die auth_cache_key_invalid 65
      [[ "$(docker exec -- "$redis_container" redis-cli --raw DEL "$key")" == 1 ]] ||
        control_plane_die auth_cache_invalidation_failed 65
      deleted=$((deleted + 1))
    done < <(docker exec -- "$redis_container" redis-cli --raw --scan --pattern 'auth:team:*')
  fi

  team_count="$(verify_policy "$postgres_container" "$postgres_user")"
  printf 'status=pass operation=admission-policy mode=%s teams=%s cache_keys_deleted=%s\n' \
    "${mode#--}" "$team_count" "$deleted"
}

main "$@"
