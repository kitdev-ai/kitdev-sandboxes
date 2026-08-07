#!/usr/bin/env bash
set -Eeuo pipefail

readonly SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
# shellcheck source=common.sh
source "$SCRIPT_DIR/common.sh"

readonly BACKUP_ROOT="$KITDEV_STATE_ROOT/backups"
readonly COMPOSE_ROOT="$KITDEV_OPT_ROOT/compose/control-plane"
readonly COMPOSE_FILE="$COMPOSE_ROOT/compose.yaml"
readonly IMAGES_LOCK_FILE="$COMPOSE_ROOT/images.lock.json"
readonly BACKUP_LOCK=/run/kitdev-sandboxes/control-plane-lifecycle.lock
readonly RESTORE_JOURNAL="$KITDEV_STATE_ROOT/restore-journal"
readonly MANIFEST_HELPER="$SCRIPT_DIR/backup_manifest.py"
readonly -a COMPONENT_NAMES=(clickhouse loki postgres redis template-storage)
readonly -a COMPONENT_PATHS=(
  data/clickhouse
  data/loki
  data/postgres
  data/redis
  data/runtime/orchestrator/template-storage
)
publication_started=0
stage=''
prior_state=''

cleanup() {
  local status=$?
  trap - EXIT INT TERM HUP
  if [[ "$publication_started" == 0 && -n "$stage" && ! -L "$stage" && -d "$stage" ]]; then
    rm -rf --one-file-system -- "$stage"
  fi
  if [[ "$prior_state" == running ]]; then
    if ! "$SCRIPT_DIR/replay-compose.sh" up >/dev/null 2>&1 ||
      ! systemctl start kitdev-e2b-orchestrator.service >/dev/null 2>&1; then
      status=70
      printf 'status=error reason=backup_service_state_restore_failed\n' >&2
    fi
  fi
  exit "$status"
}

require_fixed_assets() {
  [[ ! -L "$MANIFEST_HELPER" && -f "$MANIFEST_HELPER" ]] ||
    control_plane_die backup_manifest_helper_invalid 65
  [[ ! -L "$COMPOSE_FILE" && -f "$COMPOSE_FILE" ]] ||
    control_plane_die installed_compose_invalid 65
  [[ ! -L "$IMAGES_LOCK_FILE" && -f "$IMAGES_LOCK_FILE" ]] ||
    control_plane_die installed_images_lock_invalid 65
  /usr/bin/python3 -I -B -S "$SCRIPT_DIR/private_env.py" verify >/dev/null
}

acquire_backup_lock() {
  install -d -o root -g root -m 0700 -- /run/kitdev-sandboxes
  if [[ ! -e "$BACKUP_LOCK" && ! -L "$BACKUP_LOCK" ]]; then
    install -o root -g root -m 0600 /dev/null "$BACKUP_LOCK"
  fi
  [[ ! -L "$BACKUP_LOCK" && -f "$BACKUP_LOCK" &&
    "$(stat -c '%u:%g:%a:%s:%h' -- "$BACKUP_LOCK")" == 0:0:600:0:1 ]] ||
    control_plane_die lifecycle_lock_invalid 65
  exec 9<>"$BACKUP_LOCK"
  [[ "$(stat -Lc '%d:%i' /proc/$$/fd/9)" == "$(stat -Lc '%d:%i' "$BACKUP_LOCK")" ]] ||
    control_plane_die lifecycle_lock_changed 65
  flock --nonblock 9 || control_plane_die lifecycle_operation_running 75
}

require_zero_sandboxes() {
  ! pgrep -x firecracker >/dev/null 2>&1 || control_plane_die active_sandboxes_present 69
}

running_compose_count() {
  docker ps --quiet --filter label=com.docker.compose.project=kitdev-control-plane |
    wc -l | tr -d ' '
}

classify_service_state() {
  local count
  count="$(running_compose_count)"
  if systemctl is-active --quiet kitdev-e2b-orchestrator.service; then
    [[ "$count" == 6 ]] || control_plane_die control_plane_partially_running 69
    "$SCRIPT_DIR/replay-compose.sh" verify >/dev/null ||
      control_plane_die control_plane_not_healthy 69
    printf running
  else
    [[ "$count" == 0 ]] || control_plane_die control_plane_partially_running 69
    printf stopped
  fi
}

quiesce_for_backup() {
  if [[ "$prior_state" == running ]]; then
    "$SCRIPT_DIR/replay-compose.sh" quiesce >/dev/null
    require_zero_sandboxes
    systemctl stop kitdev-e2b-orchestrator.service
    require_zero_sandboxes
    "$SCRIPT_DIR/replay-compose.sh" down >/dev/null
  fi
}

ensure_backup_root() {
  ensure_directory "$BACKUP_ROOT" root root 700
}

required_backup_bytes() {
  local index total=0 value
  for index in "${!COMPONENT_PATHS[@]}"; do
    value="$(du --summarize --block-size=1 -- "$KITDEV_STATE_ROOT/${COMPONENT_PATHS[$index]}" |
      awk '{print $1}')"
    [[ "$value" =~ ^[0-9]+$ ]] || control_plane_die backup_size_invalid 65
    total=$((total + value))
  done
  printf '%s' "$total"
}

require_free_bytes() {
  local directory="$1" required="$2" available reserve
  available="$(df --output=avail --block-size=1 -- "$directory" | tail -n 1 | tr -d ' ')"
  [[ "$available" =~ ^[0-9]+$ ]] || control_plane_die free_space_unreadable 65
  reserve=$((required / 20))
  (( reserve >= 1073741824 )) || reserve=1073741824
  (( available >= required + reserve )) || control_plane_die insufficient_backup_space 69
}

make_backup() {
  local backup_id final index name relative required
  require_zero_sandboxes
  [[ ! -e "$RESTORE_JOURNAL" && ! -L "$RESTORE_JOURNAL" ]] ||
    control_plane_die restore_journal_present 75
  prior_state="$(classify_service_state)"
  trap cleanup EXIT INT TERM HUP
  quiesce_for_backup

  /usr/bin/python3 -I -B -S "$MANIFEST_HELPER" validate-source \
    "${COMPONENT_PATHS[@]/#/$KITDEV_STATE_ROOT/}" >/dev/null ||
    control_plane_die backup_source_tree_invalid 65
  required="$(required_backup_bytes)"
  require_free_bytes "$BACKUP_ROOT" "$required"
  backup_id="$(date --utc +%Y%m%dT%H%M%SZ)-$(/usr/bin/python3 -I -B -S - <<'PY_ID'
import secrets
print(secrets.token_hex(8))
PY_ID
)"
  stage="$BACKUP_ROOT/.$backup_id.partial"
  final="$BACKUP_ROOT/$backup_id"
  [[ ! -e "$stage" && ! -L "$stage" && ! -e "$final" && ! -L "$final" ]] ||
    control_plane_die backup_destination_conflict 65
  install -d -o root -g root -m 0700 -- "$stage"
  for index in "${!COMPONENT_NAMES[@]}"; do
    name="${COMPONENT_NAMES[$index]}"
    relative="${COMPONENT_PATHS[$index]}"
    tar --create --file="$stage/$name.tar" --format=pax --numeric-owner \
      --acls --xattrs --sparse --one-file-system --directory="$KITDEV_STATE_ROOT" -- "$relative"
    chmod 0600 -- "$stage/$name.tar"
    sync -f -- "$stage/$name.tar"
  done
  /usr/bin/python3 -I -B -S "$MANIFEST_HELPER" build-manifest \
    "$stage" "$backup_id" "$prior_state" "$COMPOSE_FILE" "$IMAGES_LOCK_FILE" \
    "$KITDEV_PRIVATE_ENV" "$KITDEV_STATE_ROOT" >/dev/null
  mv -- "$stage" "$final"
  stage=''
  sync -f -- "$BACKUP_ROOT"
  if [[ "$prior_state" == running ]]; then
    "$SCRIPT_DIR/replay-compose.sh" up >/dev/null
    systemctl start kitdev-e2b-orchestrator.service
    "$SCRIPT_DIR/install-orchestrator-service.sh" verify >/dev/null
    "$SCRIPT_DIR/replay-compose.sh" verify >/dev/null
    prior_state=''
  fi
  trap - EXIT INT TERM HUP
  printf 'status=pass operation=backup backup_id=%s path=%s\n' "$backup_id" "$final"
}

require_backup_metadata() {
  local backup="$1" entry identifier
  identifier="$(basename -- "$backup")"
  [[ "$(dirname -- "$backup")" == "$BACKUP_ROOT" &&
    "$identifier" =~ ^[0-9]{8}T[0-9]{6}Z-[0-9a-f]{16}$ &&
    "$backup" != *$'\n'* && "$backup" != *$'\r'* ]] ||
    control_plane_die backup_path_invalid 65
  [[ ! -L "$backup" && -d "$backup" ]] || control_plane_die backup_directory_invalid 65
  [[ "$(stat -c '%u:%g:%a' -- "$backup")" == 0:0:700 ]] ||
    control_plane_die backup_directory_metadata_invalid 65
  for entry in "$backup"/*; do
    [[ ! -L "$entry" && -f "$entry" ]] || control_plane_die backup_entry_invalid 65
    [[ "$(stat -c '%u:%g:%a:%h' -- "$entry")" == 0:0:600:1 ]] ||
      control_plane_die backup_entry_metadata_invalid 65
  done
}

require_empty_target() {
  local target="$1" expected="$2"
  [[ ! -L "$target" && -d "$target" ]] || control_plane_die restore_target_invalid 65
  [[ "$(stat -c '%u:%g:%a' -- "$target")" == "$expected" ]] ||
    control_plane_die restore_target_metadata_invalid 65
  [[ -z "$(find "$target" -mindepth 1 -maxdepth 1 -print -quit)" ]] ||
    control_plane_die restore_target_not_clean 69
}

require_extracted_metadata() {
  [[ "$(stat -c '%u:%g:%a' -- "$stage/data/clickhouse")" == 101:101:750 ]] ||
    control_plane_die restore_clickhouse_metadata_invalid 65
  [[ "$(stat -c '%u:%g:%a' -- "$stage/data/loki")" == 10001:10001:750 ]] ||
    control_plane_die restore_loki_metadata_invalid 65
  [[ "$(stat -c '%u:%g:%a' -- "$stage/data/postgres")" == 999:0:700 ]] ||
    control_plane_die restore_postgres_metadata_invalid 65
  [[ "$(stat -c '%u:%g:%a' -- "$stage/data/redis")" == 999:0:750 ]] ||
    control_plane_die restore_redis_metadata_invalid 65
  [[ "$(stat -c '%u:%g:%a' -- \
    "$stage/data/runtime/orchestrator/template-storage")" == 0:0:700 ]] ||
    control_plane_die restore_template_storage_metadata_invalid 65
}

require_staged_component_integrity() {
  local backup="$1" index
  for index in "${!COMPONENT_NAMES[@]}"; do
    /usr/bin/python3 -I -B -S "$MANIFEST_HELPER" validate-tree \
      "$backup/manifest.json" "${COMPONENT_NAMES[$index]}" \
      "$stage/${COMPONENT_PATHS[$index]}" >/dev/null ||
      control_plane_die restore_staged_component_mismatch 65
  done
}

write_restore_journal() {
  local backup="$1" temporary
  temporary="$(mktemp "$KITDEV_STATE_ROOT/.restore-journal.XXXXXXXX")"
  chmod 0600 -- "$temporary"
  printf 'schema_version=1\nbackup=%s\nstage=%s\n' "$backup" "$stage" >"$temporary"
  sync -f -- "$temporary"
  ln -- "$temporary" "$RESTORE_JOURNAL" || control_plane_die restore_journal_publish_failed 70
  rm -f -- "$temporary"
  sync -f -- "$KITDEV_STATE_ROOT"
}

require_matching_journal() {
  local backup="$1" expected
  [[ ! -L "$RESTORE_JOURNAL" && -f "$RESTORE_JOURNAL" ]] ||
    control_plane_die restore_journal_invalid 65
  [[ "$(stat -c '%u:%g:%a:%h' -- "$RESTORE_JOURNAL")" == 0:0:600:1 ]] ||
    control_plane_die restore_journal_metadata_invalid 65
  expected="$(printf 'schema_version=1\nbackup=%s\nstage=%s\n' "$backup" "$stage")"
  [[ "$(cat -- "$RESTORE_JOURNAL")" == "$expected" ]] ||
    control_plane_die restore_journal_mismatch 65
}

publish_components() {
  local backup="$1" index name relative source target
  publication_started=1
  for index in "${!COMPONENT_PATHS[@]}"; do
    name="${COMPONENT_NAMES[$index]}"
    relative="${COMPONENT_PATHS[$index]}"
    source="$stage/$relative"
    target="$KITDEV_STATE_ROOT/$relative"
    if [[ ! -e "$source" && ! -L "$source" && ! -L "$target" && -d "$target" ]]; then
      /usr/bin/python3 -I -B -S "$MANIFEST_HELPER" validate-tree \
        "$backup/manifest.json" "$name" "$target" >/dev/null ||
        control_plane_die restored_component_mismatch 70
      continue
    fi
    [[ ! -L "$source" && -d "$source" ]] || control_plane_die restore_stage_component_invalid 70
    [[ ! -L "$target" && -d "$target" &&
      -z "$(find "$target" -mindepth 1 -maxdepth 1 -print -quit)" ]] ||
      control_plane_die restore_publish_state_invalid 70
    rmdir -- "$target"
    mv -- "$source" "$target"
    sync -f -- "$(dirname -- "$target")"
    /usr/bin/python3 -I -B -S "$MANIFEST_HELPER" validate-tree \
      "$backup/manifest.json" "$name" "$target" >/dev/null ||
      control_plane_die restored_component_mismatch 70
  done
  /usr/bin/python3 -I -B -S "$MANIFEST_HELPER" validate-source \
    "${COMPONENT_PATHS[@]/#/$KITDEV_STATE_ROOT/}" >/dev/null ||
    control_plane_die restored_tree_invalid 70
  rm -f -- "$RESTORE_JOURNAL"
  rm -rf --one-file-system -- "$stage"
  sync -f -- "$KITDEV_STATE_ROOT"
  publication_started=0
  stage=''
}

restore_backup() {
  local backup="$1" required=0 archive index
  require_zero_sandboxes
  [[ "$(classify_service_state)" == stopped ]] || control_plane_die restore_requires_stopped_services 69
  require_backup_metadata "$backup"
  /usr/bin/python3 -I -B -S "$MANIFEST_HELPER" validate-manifest \
    "$backup" "$COMPOSE_FILE" "$IMAGES_LOCK_FILE" "$KITDEV_PRIVATE_ENV" >/dev/null ||
    control_plane_die backup_validation_failed 65
  /usr/bin/python3 -I -B -S "$SCRIPT_DIR/private_env.py" verify >/dev/null
  for archive in "$backup"/*.tar; do
    required=$((required + $(stat -c %s -- "$archive")))
  done

  stage="$KITDEV_DATA_ROOT/.restore-$(basename -- "$backup").partial"
  if [[ -e "$RESTORE_JOURNAL" || -L "$RESTORE_JOURNAL" ]]; then
    require_matching_journal "$backup"
    [[ ! -L "$stage" && -d "$stage" ]] || control_plane_die restore_stage_invalid 70
    trap cleanup EXIT INT TERM HUP
    publish_components "$backup"
    trap - EXIT INT TERM HUP
    printf 'status=pass operation=restore backup_id=%s state=stopped resumed=true\n' \
      "$(basename -- "$backup")"
    return 0
  fi
  require_free_bytes "$KITDEV_DATA_ROOT" "$required"

  require_empty_target "$KITDEV_DATA_ROOT/clickhouse" 101:101:750
  require_empty_target "$KITDEV_DATA_ROOT/loki" 10001:10001:750
  require_empty_target "$KITDEV_DATA_ROOT/postgres" 999:0:700
  require_empty_target "$KITDEV_DATA_ROOT/redis" 999:0:750
  require_empty_target "$KITDEV_RUNTIME_ROOT/orchestrator/template-storage" 0:0:700

  if [[ ! -e "$RESTORE_JOURNAL" && ! -L "$RESTORE_JOURNAL" &&
    ! -L "$stage" && -d "$stage" ]]; then
    rm -rf --one-file-system -- "$stage"
  fi
  [[ ! -e "$stage" && ! -L "$stage" && ! -e "$RESTORE_JOURNAL" && ! -L "$RESTORE_JOURNAL" ]] ||
    control_plane_die restore_residue_present 75
  install -d -o root -g root -m 0700 -- "$stage"
  trap cleanup EXIT INT TERM HUP
  for index in "${!COMPONENT_NAMES[@]}"; do
    tar --extract --file="$backup/${COMPONENT_NAMES[$index]}.tar" --numeric-owner \
      --same-owner --same-permissions --acls --xattrs --directory="$stage"
  done
  /usr/bin/python3 -I -B -S "$MANIFEST_HELPER" validate-source \
    "${COMPONENT_PATHS[@]/#/$stage/}" >/dev/null ||
    control_plane_die restore_extracted_tree_invalid 65
  require_extracted_metadata
  require_staged_component_integrity "$backup"
  [[ "$(stat -c %d -- "$stage")" == "$(stat -c %d -- "$KITDEV_DATA_ROOT")" ]] ||
    control_plane_die restore_stage_filesystem_mismatch 65
  publication_started=1
  write_restore_journal "$backup"
  require_matching_journal "$backup"
  publish_components "$backup"
  trap - EXIT INT TERM HUP
  printf 'status=pass operation=restore backup_id=%s state=stopped\n' "$(basename -- "$backup")"
}

main() {
  local operation="${1:-}" backup=''
  case "$operation" in
    backup)
      [[ $# == 1 ]] || control_plane_die invalid_arguments 64
      ;;
    restore)
      [[ $# == 3 && "$2" == --backup ]] || control_plane_die invalid_arguments 64
      backup="$3"
      ;;
    *) control_plane_die invalid_operation 64 ;;
  esac
  require_root
  require_lifecycle_platform
  require_fixed_assets
  for command in \
    awk basename cat chmod date df dirname docker du find flock install ln mktemp mv pgrep rm \
    rmdir stat sync systemctl tail tar tr wc; do
    require_command "$command"
  done
  acquire_backup_lock
  ensure_backup_root
  case "$operation" in
    backup) make_backup ;;
    restore) restore_backup "$backup" ;;
  esac
}

main "$@"
