#!/usr/bin/env bash
set -Eeuo pipefail
readonly SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
# shellcheck source=common.sh
source "$SCRIPT_DIR/common.sh"

readonly LIFECYCLE_LOCK=/run/kitdev-sandboxes/control-plane-lifecycle.lock
readonly SDK_LOCK=/run/kitdev-sandboxes/typescript-sdk-e2e.lock
readonly PRIOR_DIR="$KITDEV_STATE_ROOT/team-limits"

usage() {
  cat >&2 <<'USAGE'
usage: set-team-limits.sh --team-slug <slug> [--check]
         [--concurrent-sandboxes N] [--concurrent-builds N]
         [--max-vcpu N] [--max-ram-mb N] [--max-length-hours N]
         [--free-disk-mb N] [--max-disk-mb N] [--allow-oversubscription]

Raises or lowers one team's effective limits. Without --check the change is
applied and the upstream authentication cache is invalidated. The first apply
for a team records its prior effective limits create-once for rollback.

Concurrent sandbox memory is served from the persistent HugeTLB pool. When the
worst case (concurrent sandboxes x max RAM) exceeds that pool,
--allow-oversubscription is required: individual sandbox starts will then fail
once the pool is exhausted, rather than the host overcommitting.
USAGE
  exit 64
}

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
    control_plane_die team_limits_lock_invalid 65
}

positive_integer() {
  [[ "$1" =~ ^[1-9][0-9]{0,6}$ ]] || control_plane_die team_limits_value_invalid 64
}

hugepage_pool_mib() {
  local total size
  total="$(awk '/^HugePages_Total:/ {print $2}' /proc/meminfo)"
  size="$(awk '/^Hugepagesize:/ {print $2}' /proc/meminfo)"
  [[ "$total" =~ ^[0-9]+$ && "$size" =~ ^[0-9]+$ ]] || control_plane_die hugepage_state_unreadable 65
  printf '%s' $(((total * size) / 1024))
}

# Read one team's effective limits as a stable pipe-separated row.
read_limits() {
  local container="$1" user="$2" slug="$3"
  docker exec -- "$container" psql --no-psqlrc --set=ON_ERROR_STOP=1 --tuples-only \
    --no-align --field-separator='|' --username "$user" --dbname "$user" --command "
SELECT l.concurrent_sandboxes, l.concurrent_template_builds, l.max_vcpu, l.max_ram_mb,
       l.disk_mb, l.default_free_disk_size_mb, l.max_disk_size_mb, l.max_length_hours
FROM public.team_limits l JOIN public.teams t ON t.id = l.id
WHERE t.slug = '$slug';"
}

main() {
  local slug='' mode=apply allow_over=no container user prior row deleted=0 pool worst
  local sandboxes='' builds='' vcpu='' ram='' hours='' free_disk='' max_disk=''
  while (($#)); do
    case "$1" in
      --team-slug) [[ $# -ge 2 ]] || usage; slug="$2"; shift 2 ;;
      --check) mode=check; shift ;;
      --allow-oversubscription) allow_over=yes; shift ;;
      --concurrent-sandboxes) [[ $# -ge 2 ]] || usage; sandboxes="$2"; shift 2 ;;
      --concurrent-builds) [[ $# -ge 2 ]] || usage; builds="$2"; shift 2 ;;
      --max-vcpu) [[ $# -ge 2 ]] || usage; vcpu="$2"; shift 2 ;;
      --max-ram-mb) [[ $# -ge 2 ]] || usage; ram="$2"; shift 2 ;;
      --max-length-hours) [[ $# -ge 2 ]] || usage; hours="$2"; shift 2 ;;
      --free-disk-mb) [[ $# -ge 2 ]] || usage; free_disk="$2"; shift 2 ;;
      --max-disk-mb) [[ $# -ge 2 ]] || usage; max_disk="$2"; shift 2 ;;
      *) usage ;;
    esac
  done
  [[ "$slug" =~ ^[a-z0-9][a-z0-9-]{0,62}$ ]] || control_plane_die team_slug_invalid 64
  for value in "$sandboxes" "$builds" "$vcpu" "$ram" "$hours" "$free_disk" "$max_disk"; do
    [[ -z "$value" ]] || positive_integer "$value"
  done

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

  container="$(control_plane_container postgres)"
  [[ "$container" =~ ^[0-9a-f]{64}$ ]] || control_plane_die postgres_container_invalid 65
  user="$(postgres_identity "$container")" || control_plane_die postgres_identity_invalid 65

  prior="$(read_limits "$container" "$user" "$slug")" || control_plane_die team_limits_query_failed 65
  [[ "$(grep -c . <<<"$prior")" == 1 ]] || control_plane_die team_slug_not_unique 65
  printf 'prior=%s\n' "$prior"

  IFS='|' read -r p_sandboxes p_builds p_vcpu p_ram p_disk p_free p_max p_hours <<<"$prior"
  sandboxes="${sandboxes:-$p_sandboxes}"
  builds="${builds:-$p_builds}"
  vcpu="${vcpu:-$p_vcpu}"
  ram="${ram:-$p_ram}"
  hours="${hours:-$p_hours}"
  free_disk="${free_disk:-$p_free}"
  max_disk="${max_disk:-$p_max}"

  pool="$(hugepage_pool_mib)"
  worst=$((sandboxes * ram))
  printf 'hugepage_pool_mib=%s worst_case_mib=%s\n' "$pool" "$worst"
  if ((worst > pool)) && [[ "$allow_over" != yes ]]; then
    control_plane_die team_limits_exceed_hugepage_pool 65
  fi

  if [[ "$mode" == check ]]; then
    printf 'status=pass operation=set-team-limits mode=check team=%s desired=%s|%s|%s|%s|%s|%s|%s\n' \
      "$slug" "$sandboxes" "$builds" "$vcpu" "$ram" "$free_disk" "$max_disk" "$hours"
    return
  fi

  ! pgrep -x firecracker >/dev/null 2>&1 || control_plane_die team_limits_firecracker_running 65
  [[ "$(docker exec -- "$container" psql --no-psqlrc --set=ON_ERROR_STOP=1 --tuples-only \
    --no-align --username "$user" --dbname "$user" --command \
    "SELECT count(*) FROM public.env_builds WHERE status_group IN ('pending', 'in_progress');")" == 0 ]] ||
    control_plane_die team_limits_build_running 65

  ensure_directory "$PRIOR_DIR" root root 700
  if [[ ! -e "$PRIOR_DIR/$slug.prior" ]]; then
    install -o root -g root -m 0600 /dev/null "$PRIOR_DIR/$slug.prior"
    printf '%s\n' "$prior" >"$PRIOR_DIR/$slug.prior"
  fi

  docker exec --interactive "$container" psql --no-psqlrc --set=ON_ERROR_STOP=1 \
    --username "$user" --dbname "$user" >/dev/null <<SQL_TEAM_LIMITS
BEGIN;
INSERT INTO public.project_limits (
  team_id, max_length_hours, concurrent_sandboxes, concurrent_template_builds,
  max_vcpu, max_ram_mb, disk_mb, events_ttl_days,
  default_free_disk_size_mb, max_disk_size_mb
)
SELECT t.id, $hours, $sandboxes, $builds, $vcpu, $ram, l.disk_mb, l.events_ttl_days,
       $free_disk, $max_disk
FROM public.teams t JOIN public.team_limits l ON l.id = t.id
WHERE t.slug = '$slug'
ON CONFLICT (team_id) DO UPDATE SET
  max_length_hours = EXCLUDED.max_length_hours,
  concurrent_sandboxes = EXCLUDED.concurrent_sandboxes,
  concurrent_template_builds = EXCLUDED.concurrent_template_builds,
  max_vcpu = EXCLUDED.max_vcpu,
  max_ram_mb = EXCLUDED.max_ram_mb,
  default_free_disk_size_mb = EXCLUDED.default_free_disk_size_mb,
  max_disk_size_mb = EXCLUDED.max_disk_size_mb,
  updated_at = now();
COMMIT;
SQL_TEAM_LIMITS

  # Cached authentication carries the old limits; drop it so the next request
  # reloads them.
  local redis key
  redis="$(control_plane_container redis)"
  [[ "$redis" =~ ^[0-9a-f]{64}$ ]] || control_plane_die redis_container_invalid 65
  while IFS= read -r key; do
    [[ "$key" == auth:team:* && "$key" != *$'\n'* ]] || control_plane_die auth_cache_key_invalid 65
    [[ "$(docker exec -- "$redis" redis-cli --raw DEL "$key")" == 1 ]] ||
      control_plane_die auth_cache_invalidation_failed 65
    deleted=$((deleted + 1))
  done < <(docker exec -- "$redis" redis-cli --raw --scan --pattern 'auth:team:*')

  row="$(read_limits "$container" "$user" "$slug")"
  [[ "$row" == "$sandboxes|$builds|$vcpu|$ram|$p_disk|$free_disk|$max_disk|$hours" ]] ||
    control_plane_die team_limits_verify_failed 65
  printf 'status=pass operation=set-team-limits team=%s applied=%s cache_keys_deleted=%s\n' \
    "$slug" "$row" "$deleted"
}

main "$@"
