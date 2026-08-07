#!/usr/bin/env bash
set -Eeuo pipefail

readonly SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
readonly REPOSITORY_ROOT="$(CDPATH= cd -- "$SCRIPT_DIR/.." && pwd -P)"
readonly VENV="$REPOSITORY_ROOT/.venv-ansible"
readonly LOCK="$REPOSITORY_ROOT/requirements.lock"
readonly LOCK_SHA256='90b6f801535af4751945ae569de7f20f13e8494006505c5f72e607df17692b6f'

die() {
  printf 'host-prerequisites: %s\n' "$1" >&2
  exit "${2:-64}"
}

platform_gate() {
  local lifecycle="$1"
  /usr/bin/python3 -I -B -S - "$lifecycle" <<'PY'
import pathlib
import re
import sys

values = {}
for line in pathlib.Path("/etc/os-release").read_text(encoding="utf-8").splitlines():
    if "=" not in line:
        continue
    key, value = line.split("=", 1)
    if re.fullmatch(r"[A-Z][A-Z0-9_]*", key):
        values[key] = value.strip().strip('"')
lifecycle = sys.argv[1]
if values.get("ID") != "ubuntu" or values.get("VERSION_ID") not in {"25.04", "26.04"}:
    raise SystemExit(3)
if lifecycle not in {"production", "development", "migration"}:
    raise SystemExit(2)
if values["VERSION_ID"] == "25.04" and lifecycle == "production":
    raise SystemExit(3)
PY
}

verify_lock() {
  local actual
  actual="$(/usr/bin/sha256sum "$LOCK" | /usr/bin/cut -d' ' -f1)"
  [[ "$actual" == "$LOCK_SHA256" ]] || die dependency_lock_digest_mismatch 65
}

bootstrap() {
  command -v /usr/bin/python3 >/dev/null || die system_python_missing 69
  if ! /usr/bin/python3 -m venv --help >/dev/null 2>&1; then
    [[ "${EUID}" -eq 0 ]] || die 'python3-venv is missing; rerun bootstrap through sudo' 77
    /usr/bin/apt-get update
    /usr/bin/env DEBIAN_FRONTEND=noninteractive /usr/bin/apt-get install \
      --no-install-recommends --yes python3-venv
  fi
  if [[ ! -e "$VENV" ]]; then
    /usr/bin/python3 -m venv "$VENV"
  fi
  [[ -x "$VENV/bin/python" && -x "$VENV/bin/pip" ]] || die invalid_ansible_venv 65
  "$VENV/bin/python" -m pip install --disable-pip-version-check \
    --require-hashes --no-deps --requirement "$LOCK"
  verify_controller
}

verify_controller() {
  [[ ! -L "$VENV" && -x "$VENV/bin/python" && -x "$VENV/bin/ansible-playbook" ]] ||
    die invalid_ansible_venv 65
  [[ "$("$VENV/bin/ansible-playbook" --version | /usr/bin/head -n1)" == 'ansible-playbook [core 2.21.2]' ]] ||
    die ansible_version_mismatch 65
  "$VENV/bin/python" -I -B -m pip check >/dev/null || die ansible_dependency_check_failed 65
}

main() {
  local operation="${1:-}"
  local lifecycle="${2:-production}"
  [[ "${EUID}" -eq 0 ]] || die 'run this command through sudo' 77
  case "$operation" in
    bootstrap)
      platform_gate "$lifecycle"
      verify_lock
      bootstrap
      ;;
    check|apply|remove-check|remove)
      platform_gate "$lifecycle"
      verify_lock
      verify_controller
      export KITDEV_LIFECYCLE_MODE="$lifecycle"
      cd "$REPOSITORY_ROOT"
      if [[ "$operation" == check ]]; then
        exec "$VENV/bin/ansible-playbook" --check --diff ansible/site.yaml
      fi
      if [[ "$operation" == remove-check ]]; then
        exec "$VENV/bin/ansible-playbook" --check --diff ansible/remove-host-prerequisites.yaml
      fi
      if [[ "$operation" == remove ]]; then
        exec "$VENV/bin/ansible-playbook" --diff ansible/remove-host-prerequisites.yaml
      fi
      exec "$VENV/bin/ansible-playbook" --diff ansible/site.yaml
      ;;
    *) die 'usage: scripts/host-prerequisites.sh {bootstrap|check|apply|remove-check|remove} [production|development|migration]' ;;
  esac
}

main "$@"
