#!/usr/bin/env bash
set -Eeuo pipefail
readonly SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
# shellcheck source=common.sh
source "$SCRIPT_DIR/common.sh"

# Create the default project team.
#
# Nothing else in this repository creates a team: `api-key create` resolves an
# existing slug, and provision-browser-heavy-profile.sh creates only its own
# dedicated team. On a fresh host that leaves no team, therefore no API key,
# therefore no template build and no sandboxes -- install completes into a
# system that cannot be used at all. The seed-template step used to hide this
# by shipping database rows alongside a pre-made template fixture.
#
# Upstream solves it in packages/local-dev/seed-local-database.go, which is not
# wired into this deployment. This is the equivalent, restricted to the one row
# that unblocks the documented next step.

readonly DEFAULT_SLUG=local-dev-team

usage() {
  cat >&2 <<'USAGE'
usage: bootstrap-team.sh [--slug <slug>] [--check]

Creates the default project team if it does not exist. Idempotent: an existing
team with the requested slug is verified and left untouched.
USAGE
  exit 64
}

postgres_identity() {
  local container="$1" user result
  for user in kitdev postgres; do
    result="$(docker exec -- "$container" psql --no-psqlrc --tuples-only --no-align \
      --username "$user" --dbname "$user" --command \
      "SELECT to_regclass('public.teams') IS NOT NULL;" 2>/dev/null)" || continue
    if [[ "$result" == t ]]; then
      printf '%s\n' "$user"
      return
    fi
  done
  return 1
}

main() {
  local slug="$DEFAULT_SLUG" mode=apply container user existing result
  while (($#)); do
    case "$1" in
      --slug) [[ $# -ge 2 ]] || usage; slug="$2"; shift 2 ;;
      --check) mode=check; shift ;;
      *) usage ;;
    esac
  done
  [[ "$slug" =~ ^[a-z0-9][a-z0-9-]{0,62}$ ]] || control_plane_die team_slug_invalid 64

  require_root
  require_lifecycle_platform
  require_command docker

  container="$(control_plane_container postgres)" ||
    control_plane_die postgres_container_invalid 65
  [[ "$container" =~ ^[0-9a-f]{64}$ ]] || control_plane_die postgres_container_invalid 65
  [[ "$(docker inspect --format '{{.State.Status}} {{.State.Health.Status}}' -- "$container")" == 'running healthy' ]] ||
    control_plane_die postgres_container_unhealthy 65
  user="$(postgres_identity "$container")" || control_plane_die postgres_identity_invalid 65

  existing="$(docker exec -- "$container" psql --no-psqlrc --set=ON_ERROR_STOP=1 \
    --tuples-only --no-align --username "$user" --dbname "$user" --command \
    "SELECT count(*) FROM public.teams WHERE slug = '$slug';")" ||
    control_plane_die team_query_failed 65
  [[ "$existing" =~ ^[0-9]+$ ]] || control_plane_die team_query_failed 65

  if [[ "$mode" == check ]]; then
    result=$([[ "$existing" == 0 ]] && printf create || printf unchanged)
    printf 'status=pass operation=bootstrap-team mode=check slug=%s result=%s\n' "$slug" "$result"
    return
  fi

  if [[ "$existing" == 0 ]]; then
    docker exec --interactive "$container" psql --no-psqlrc --set=ON_ERROR_STOP=1 \
      --username "$user" --dbname "$user" >/dev/null <<SQL_BOOTSTRAP_TEAM
BEGIN;
INSERT INTO public.teams (name, tier, email, slug)
VALUES ('Kitdev default team', 'base_v1', '$slug@localhost.invalid', '$slug')
ON CONFLICT (slug) DO NOTHING;
COMMIT;
SQL_BOOTSTRAP_TEAM
    result=created
  else
    result=unchanged
  fi

  # Verify the end state regardless of which branch ran, so a pre-existing row
  # that is blocked, banned or on an unexpected tier fails loudly rather than
  # being reported as usable.
  [[ "$(docker exec -- "$container" psql --no-psqlrc --set=ON_ERROR_STOP=1 \
    --tuples-only --no-align --username "$user" --dbname "$user" --command \
    "SELECT count(*) FROM public.teams
     WHERE slug = '$slug' AND tier = 'base_v1'
       AND is_blocked = FALSE AND is_banned = FALSE;")" == 1 ]] ||
    control_plane_die team_state_invalid 65

  printf 'status=pass operation=bootstrap-team slug=%s result=%s\n' "$slug" "$result"
}

main "$@"
