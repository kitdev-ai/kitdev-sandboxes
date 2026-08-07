#!/usr/bin/env bash
set -Eeuo pipefail

readonly SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
readonly REPOSITORY_ROOT="$(CDPATH= cd -- "$SCRIPT_DIR/.." && pwd -P)"
readonly VENV="$REPOSITORY_ROOT/.venv-ansible"
readonly LOCK="$REPOSITORY_ROOT/requirements.lock"
readonly LOCK_SHA256='90b6f801535af4751945ae569de7f20f13e8494006505c5f72e607df17692b6f'
readonly RUNTIME_DIR=/run/kitdev-sandboxes
readonly LIFECYCLE_LOCK="$RUNTIME_DIR/control-plane-lifecycle.lock"
readonly SDK_LOCK="$RUNTIME_DIR/typescript-sdk-e2e.lock"

die() {
  printf 'legacy-capacity-migration: %s\n' "$1" >&2
  exit "${2:-64}"
}

require_exact_file() {
  local path="$1"
  [[ ! -L "$path" && -f "$path" && "$(stat -c '%u:%g:%a:%s:%h' -- "$path")" == '0:0:600:0:1' ]] ||
    die "unsafe lock metadata: $(basename -- "$path")" 65
}

require_controller() {
  local actual
  actual="$(sha256sum -- "$LOCK" | cut -d' ' -f1)"
  [[ "$actual" == "$LOCK_SHA256" ]] || die dependency_lock_digest_mismatch 65
  [[ ! -L "$VENV" && -x "$VENV/bin/python" && -x "$VENV/bin/ansible-playbook" ]] ||
    die invalid_ansible_venv 65
  [[ "$("$VENV/bin/ansible-playbook" --version | head -n1)" == 'ansible-playbook [core 2.21.2]' ]] ||
    die ansible_version_mismatch 65
  "$VENV/bin/python" -I -B -m pip check >/dev/null || die ansible_dependency_check_failed 65
}

require_idle_runtime() {
  ! pgrep -x firecracker >/dev/null 2>&1 || die active_firecracker_present 69
  ! pgrep -x template-manager >/dev/null 2>&1 || die active_template_manager_present 69
}

acquire_safety_locks() {
  local operation="$1" lifecycle_created=0
  [[ ! -L "$RUNTIME_DIR" && -d "$RUNTIME_DIR" &&
    "$(stat -c '%u:%g:%a' -- "$RUNTIME_DIR")" == '0:0:700' ]] ||
    die runtime_directory_invalid 65
  require_exact_file "$SDK_LOCK"
  exec 8<>"$SDK_LOCK"
  flock --nonblock 8 || die sdk_operation_running 75
  require_idle_runtime

  if [[ ! -e "$LIFECYCLE_LOCK" && ! -L "$LIFECYCLE_LOCK" ]]; then
    case "$operation" in
      check)
        export KITDEV_LEGACY_LIFECYCLE_LOCK_CREATED=1
        return 0
        ;;
      apply)
        install -o root -g root -m 0600 /dev/null "$LIFECYCLE_LOCK"
        lifecycle_created=1
        ;;
      *) die lifecycle_lock_required_for_removal 65 ;;
    esac
  fi
  require_exact_file "$LIFECYCLE_LOCK"
  exec 9<>"$LIFECYCLE_LOCK"
  flock --nonblock 9 || die lifecycle_operation_running 75
  require_idle_runtime
  export KITDEV_LEGACY_LIFECYCLE_LOCK_CREATED="$lifecycle_created"
}

main() {
  local operation="${1:-}"
  [[ $# == 1 ]] || die 'usage: scripts/legacy-capacity-migration.sh {check|apply|remove-check|remove}'
  [[ "$EUID" -eq 0 ]] || die 'run this command through sudo' 77
  case "$operation" in check|apply|remove-check|remove) ;;
    *) die 'usage: scripts/legacy-capacity-migration.sh {check|apply|remove-check|remove}' ;;
  esac
  for command in cut flock head install pgrep sha256sum stat; do
    command -v "$command" >/dev/null || die "required command missing: $command" 69
  done
  require_controller
  acquire_safety_locks "$operation"
  export KITDEV_LIFECYCLE_MODE=development
  export KITDEV_LEGACY_CAPACITY_ACTION="$([[ "$operation" == remove* ]] && printf remove || printf apply)"
  cd "$REPOSITORY_ROOT"
  if [[ "$operation" == check || "$operation" == remove-check ]]; then
    exec "$VENV/bin/ansible-playbook" --check --diff ansible/legacy-capacity-migration.yaml
  fi
  exec "$VENV/bin/ansible-playbook" --diff ansible/legacy-capacity-migration.yaml
}

main "$@"
