#!/usr/bin/env bash
set -Eeuo pipefail
readonly SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
# shellcheck source=common.sh
source "$SCRIPT_DIR/common.sh"
umask 077

readonly TEAM_SLUG=kitdev-browser-heavy-team
created_key=0
db_committed=0
api_key_file=''

cleanup() {
  local status=$?
  trap - EXIT INT TERM
  if (( status != 0 && created_key == 1 && db_committed == 0 )); then
    rm -f -- "$api_key_file"
  fi
  exit "$status"
}

create_or_read_key_metadata() {
  /usr/bin/python3 -I -B -S - "$1" <<'PY_KEY'
import base64
import hashlib
import os
import re
import stat
import sys

path = sys.argv[1]
created = False
try:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
except FileExistsError:
    descriptor = None
else:
    created = True
    value = "e2b_" + os.urandom(20).hex()
    try:
        os.write(descriptor, (value + "\n").encode("ascii"))
        os.fsync(descriptor)
    finally:
        os.close(descriptor)

metadata = os.lstat(path)
if (
    not stat.S_ISREG(metadata.st_mode)
    or stat.S_ISLNK(metadata.st_mode)
    or metadata.st_uid != 0
    or metadata.st_gid != 0
    or stat.S_IMODE(metadata.st_mode) != 0o600
    or metadata.st_nlink != 1
    or metadata.st_size not in {44, 45}
):
    raise SystemExit(1)
raw = open(path, "rb").read()
if not re.fullmatch(rb"e2b_[0-9a-f]{40}\n?", raw):
    raise SystemExit(1)
hex_value = raw.rstrip(b"\n")[4:].decode("ascii")
digest = base64.b64encode(hashlib.sha256(bytes.fromhex(hex_value)).digest()).decode("ascii").rstrip("=")
print("1" if created else "0")
print("$sha256$" + digest)
print(hex_value[:2])
print(hex_value[-4:])
PY_KEY
}

main() {
  local key_metadata key_hash mask_prefix mask_suffix parent postgres_container redis_container row team_id
  [[ $# == 2 && "$1" == --api-key-file && "$2" == /* ]] || control_plane_die invalid_arguments 64
  api_key_file="$2"
  require_root
  require_lifecycle_platform
  [[ "$KITDEV_LIFECYCLE" != production ]] || control_plane_die profile_provision_not_for_production 68
  require_command docker
  require_command flock
  require_command pgrep

  parent="$(dirname -- "$api_key_file")"
  ensure_directory "$parent" root root 700
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
  ! pgrep -x firecracker >/dev/null 2>&1 || control_plane_die profile_preexisting_firecracker 65

  trap cleanup EXIT
  trap 'exit 130' INT TERM
  key_metadata="$(create_or_read_key_metadata "$api_key_file")" || control_plane_die profile_api_key_file_invalid 65
  mapfile -t fields <<<"$key_metadata"
  [[ "${#fields[@]}" == 4 ]] || control_plane_die profile_api_key_metadata_invalid 65
  created_key="${fields[0]}"
  key_hash="${fields[1]}"
  mask_prefix="${fields[2]}"
  mask_suffix="${fields[3]}"
  [[ "$created_key" =~ ^[01]$ && "$key_hash" =~ ^\$sha256\$[A-Za-z0-9+/]{43}$ &&
    "$mask_prefix" =~ ^[0-9a-f]{2}$ && "$mask_suffix" =~ ^[0-9a-f]{4}$ ]] ||
    control_plane_die profile_api_key_metadata_invalid 65

  postgres_container="$(docker ps --quiet \
    --filter label=com.docker.compose.project=kitdev-control-plane \
    --filter label=com.docker.compose.service=postgres)"
  [[ "$postgres_container" =~ ^[0-9a-f]{64}$ ]] || control_plane_die postgres_container_invalid 65
  docker exec --interactive "$postgres_container" \
    psql --no-psqlrc --set=ON_ERROR_STOP=1 --username kitdev --dbname kitdev \
      --set=key_hash="$key_hash" --set=mask_prefix="$mask_prefix" \
      --set=mask_suffix="$mask_suffix" <<'SQL_PROFILE' >/dev/null
BEGIN;

INSERT INTO public.teams (name, tier, email, slug)
VALUES ('Kitdev browser heavy qualification', 'base_v1',
        'browser-heavy@localhost.invalid', 'kitdev-browser-heavy-team')
ON CONFLICT (slug) DO NOTHING;

SELECT id AS team_id,
       name = 'Kitdev browser heavy qualification'
         AND tier = 'base_v1'
         AND email = 'browser-heavy@localhost.invalid'
         AND is_blocked = FALSE
         AND is_banned = FALSE AS team_valid
FROM public.teams WHERE slug = 'kitdev-browser-heavy-team'
\gset
\if :team_valid
\else
  \echo 'heavy team identity conflict' >&2
  \quit 1
\endif

SELECT NOT EXISTS (
  SELECT 1 FROM public.team_api_keys
  WHERE api_key_hash = :'key_hash' AND team_id <> :'team_id'::uuid
) AND NOT EXISTS (
  SELECT 1 FROM public.team_api_keys
  WHERE team_id = :'team_id'::uuid AND api_key_hash <> :'key_hash'
) AS key_valid
\gset
\if :key_valid
\else
  \echo 'heavy team API key conflict' >&2
  \quit 1
\endif

INSERT INTO public.project_limits (
  team_id, max_length_hours, concurrent_sandboxes,
  concurrent_template_builds, max_vcpu, max_ram_mb, disk_mb,
  events_ttl_days, default_free_disk_size_mb, max_disk_size_mb
) VALUES (:'team_id'::uuid, 1, 1, 1, 2, 8192, 16384, 7, 16384, 25600)
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

INSERT INTO public.team_api_keys (
  team_id, api_key_hash, api_key_prefix, api_key_length,
  api_key_mask_prefix, api_key_mask_suffix, name, updated_at
) VALUES (
  :'team_id'::uuid, :'key_hash', 'e2b_', 40,
  :'mask_prefix', :'mask_suffix', 'Kitdev browser heavy qualification', now()
) ON CONFLICT (api_key_hash) DO NOTHING;

COMMIT;
SQL_PROFILE
  db_committed=1

  row="$(docker exec --interactive "$postgres_container" \
    psql --no-psqlrc --set=ON_ERROR_STOP=1 --tuples-only --no-align \
      --field-separator='|' --username kitdev --dbname kitdev --command "
SELECT t.id, l.max_vcpu, l.max_ram_mb, l.disk_mb,
       l.default_free_disk_size_mb, l.max_disk_size_mb
FROM public.teams t JOIN public.team_limits l ON l.id = t.id
JOIN public.team_api_keys k ON k.team_id = t.id
WHERE t.slug = '$TEAM_SLUG' AND k.api_key_hash = '$key_hash';")" ||
    control_plane_die profile_verification_query_failed 65
  [[ "$row" =~ ^([0-9a-f-]{36})\|2\|8192\|16384\|16384\|25600$ ]] ||
    control_plane_die profile_verification_failed 65
  team_id="${BASH_REMATCH[1]}"

  redis_container="$(docker ps --quiet \
    --filter label=com.docker.compose.project=kitdev-control-plane \
    --filter label=com.docker.compose.service=redis)"
  [[ "$redis_container" =~ ^[0-9a-f]{64}$ ]] || control_plane_die redis_container_invalid 65
  [[ "$(docker exec -- "$redis_container" redis-cli --raw DEL \
    "auth:team:$key_hash" "auth:team:team-$team_id")" =~ ^[0-9]+$ ]] ||
    control_plane_die profile_auth_cache_invalidation_failed 65
  printf 'status=pass operation=provision-browser-heavy-profile team_id=%s\n' "$team_id"
}

main "$@"
