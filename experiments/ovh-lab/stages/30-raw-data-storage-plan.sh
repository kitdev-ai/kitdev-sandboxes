#!/usr/bin/env bash
set -Eeuo pipefail
readonly SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
# shellcheck source=../lib/common.sh
source "$SCRIPT_DIR/../lib/common.sh"
# OVH_LAB_STAGE_BODY

snapshot() {
  local rows
  rows="$(lsblk -b -dn -P -o TYPE,SIZE,FSTYPE,MOUNTPOINTS 2>/dev/null)" ||
    lab_die storage_discovery_failed 1
  printf 'stage=30 disk_count=%s raw_unmounted_disk_count=%s\n' \
    "$(printf '%s\n' "$rows" | awk '$0 ~ /TYPE="disk"/ { n++ } END { print n+0 }')" \
    "$(printf '%s\n' "$rows" | awk '$0 ~ /TYPE="disk"/ && $0 ~ /FSTYPE=""/ && $0 ~ /MOUNTPOINTS=""/ { n++ } END { print n+0 }')"
  printf '%s\n' "$rows" | awk '
    $0 ~ /TYPE="disk"/ && $0 ~ /FSTYPE=""/ && $0 ~ /MOUNTPOINTS=""/ {
      size="unknown"; if (match($0, /SIZE="[0-9]+"/)) { size=substr($0, RSTART+6, RLENGTH-7) }
      printf "storage.raw_candidate_size_bytes=%s\n", size
    }'
  printf 'storage.plan=discovery-only storage.format=forbidden storage.mount=forbidden\n'
}

main() {
  local mode="${1:-}"
  lab_require_ack "$@"; lab_refuse_production; lab_require_supported_platform
  case "$mode" in
    before|execute|after) snapshot ;;
    postconditions)
      printf 'status=pass scope=discovery-only format=forbidden mount=forbidden recovery=reinstall\n'
      ;;
    rollback|rollback-postconditions)
      printf 'status=pass mutation=none rollback=not-required\n'
      ;;
  esac
}
main "$@"
