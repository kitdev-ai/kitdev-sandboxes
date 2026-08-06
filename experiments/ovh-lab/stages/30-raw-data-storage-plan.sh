#!/usr/bin/env bash
set -Eeuo pipefail
readonly SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
# shellcheck source=../lib/common.sh
source "$SCRIPT_DIR/../lib/common.sh"
# OVH_LAB_STAGE_BODY

STORAGE_PARSER="$(cat <<'PY_STORAGE'
import json
import os
import re
import sys

MAX_JSON_BYTES = 1024 * 1024
PHYSICAL_TRANSPORTS = {"ata", "nvme", "sata", "scsi"}
SAFE_BLOCK_NAME = re.compile(r"[A-Za-z0-9._-]{1,128}")
MAX_TOPOLOGY_DEPTH = 64
MAX_TOPOLOGY_NODES = 4096
topology_nodes = 0


def fail() -> None:
    raise SystemExit(2)


def empty_text(value: object) -> bool:
    return value is None or value == ""


def unmounted(value: object) -> bool:
    if value is None or value == "":
        return True
    if not isinstance(value, list):
        return False
    return all(item is None or item == "" for item in value)


def empty_sysfs_directory(path: str) -> bool:
    try:
        with os.scandir(path) as entries:
            return next(entries, None) is None
    except OSError:
        fail()


def validate_node(node: object, depth: int = 0) -> None:
    global topology_nodes
    topology_nodes += 1
    if depth > MAX_TOPOLOGY_DEPTH or topology_nodes > MAX_TOPOLOGY_NODES:
        fail()
    if not isinstance(node, dict):
        fail()
    required = {"name", "type", "size", "fstype", "mountpoints", "pttype", "tran"}
    if not required.issubset(node):
        fail()
    if (
        not isinstance(node["name"], str)
        or node["name"] in {".", ".."}
        or SAFE_BLOCK_NAME.fullmatch(node["name"]) is None
    ):
        fail()
    if not isinstance(node["type"], str) or not node["type"]:
        fail()
    if isinstance(node["size"], bool) or not isinstance(node["size"], int) or node["size"] <= 0:
        fail()
    if not empty_text(node["fstype"]) and not isinstance(node["fstype"], str):
        fail()
    if not unmounted(node["mountpoints"]):
        if not isinstance(node["mountpoints"], list) or not all(
            item is None or isinstance(item, str) for item in node["mountpoints"]
        ):
            fail()
    if not empty_text(node["pttype"]) and not isinstance(node["pttype"], str):
        fail()
    if node["tran"] is not None and not isinstance(node["tran"], str):
        fail()
    children = node.get("children", [])
    if not isinstance(children, list):
        fail()
    for child in children:
        validate_node(child, depth + 1)


raw = sys.stdin.buffer.read(MAX_JSON_BYTES + 1)
if len(raw) > MAX_JSON_BYTES:
    fail()
try:
    document = json.loads(raw)
except (UnicodeDecodeError, json.JSONDecodeError, RecursionError):
    fail()
if not isinstance(document, dict) or set(document) != {"blockdevices"}:
    fail()
devices = document["blockdevices"]
if not isinstance(devices, list) or not devices:
    fail()
for device in devices:
    validate_node(device)

sysfs_root = sys.argv[1]
if not os.path.isabs(sysfs_root):
    fail()

disk_count = 0
candidates = []
for device in devices:
    if device["type"] != "disk":
        continue
    disk_count += 1
    name = device["name"]
    size = device["size"]
    transport = device["tran"]
    children = device.get("children", [])
    if not isinstance(transport, str) or transport not in PHYSICAL_TRANSPORTS:
        fail()
    if not isinstance(children, list):
        fail()
    if children:
        continue
    if not empty_text(device["fstype"]) or not unmounted(device["mountpoints"]):
        continue
    if not empty_text(device["pttype"]):
        continue
    sysfs_device = os.path.join(sysfs_root, name)
    if not os.path.isdir(sysfs_device):
        fail()
    if not empty_sysfs_directory(os.path.join(sysfs_device, "holders")):
        continue
    if not empty_sysfs_directory(os.path.join(sysfs_device, "slaves")):
        continue
    md_path = os.path.join(sysfs_device, "md")
    if os.path.exists(md_path) or os.path.islink(md_path):
        continue
    candidates.append(size)

if disk_count == 0 or len(candidates) != 1:
    fail()

print(f"stage=30 disk_count={disk_count} raw_unmounted_disk_count=1")
print(f"storage.raw_candidate_size_bytes={candidates[0]}")
print("storage.plan=discovery-only storage.format=forbidden storage.mount=forbidden")
PY_STORAGE
)"
readonly STORAGE_PARSER

snapshot() {
  /usr/bin/lsblk --json --bytes --tree \
    --output NAME,TYPE,SIZE,FSTYPE,MOUNTPOINTS,PTTYPE,TRAN 2>/dev/null |
    python3 -c "$STORAGE_PARSER" /sys/class/block ||
    lab_die storage_discovery_ambiguous 1
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
