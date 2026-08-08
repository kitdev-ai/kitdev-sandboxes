#!/usr/bin/env bash
set -Eeuo pipefail
readonly SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
# shellcheck source=common.sh
source "$SCRIPT_DIR/common.sh"

readonly ORCHESTRATOR_ENV=/etc/kitdev-sandboxes/orchestrator.env
readonly EXPECTED_ORCHESTRATOR_ENV=/opt/kitdev-sandboxes/libexec/control-plane/orchestrator.env.expected
readonly ADMISSION_PATCH=/opt/kitdev-sandboxes/libexec/control-plane/882a3b4-host-admission.patch

verify_fixed_artifacts() {
  local kitdev_gid
  kitdev_gid="$(identity_gid kitdev)"
  [[ ! -L "$KITDEV_RUNTIME_ROOT/firecrackers/v1.14.1_431f1fc/amd64/firecracker" &&
    -f "$KITDEV_RUNTIME_ROOT/firecrackers/v1.14.1_431f1fc/amd64/firecracker" &&
    "$(stat -c '%u:%g:%a:%s:%h' -- "$KITDEV_RUNTIME_ROOT/firecrackers/v1.14.1_431f1fc/amd64/firecracker")" == '0:0:755:3566832:1' ]] ||
    control_plane_die firecracker_metadata_mismatch 65
  [[ ! -L "$KITDEV_RUNTIME_ROOT/kernels/vmlinux-6.1.158/amd64/vmlinux.bin" &&
    -f "$KITDEV_RUNTIME_ROOT/kernels/vmlinux-6.1.158/amd64/vmlinux.bin" &&
    "$(stat -c '%u:%g:%a:%s:%h' -- "$KITDEV_RUNTIME_ROOT/kernels/vmlinux-6.1.158/amd64/vmlinux.bin")" == '0:0:644:43638104:1' ]] ||
    control_plane_die kernel_metadata_mismatch 65
  [[ ! -L "$KITDEV_RUNTIME_ROOT/busybox/1.36.1/amd64/busybox" &&
    -f "$KITDEV_RUNTIME_ROOT/busybox/1.36.1/amd64/busybox" &&
    "$(stat -c '%u:%g:%a:%s:%h' -- "$KITDEV_RUNTIME_ROOT/busybox/1.36.1/amd64/busybox")" == '0:0:755:1210176:1' ]] ||
    control_plane_die busybox_metadata_mismatch 65
  [[ ! -L "$KITDEV_RUNTIME_ROOT/envd/envd" && -f "$KITDEV_RUNTIME_ROOT/envd/envd" &&
    "$(stat -c '%u:%g:%a:%s:%h' -- "$KITDEV_RUNTIME_ROOT/envd/envd")" == "0:$kitdev_gid:750:12927102:1" ]] ||
    control_plane_die envd_metadata_mismatch 65
  printf '%s  %s\n' \
    d81fd733be7e027406b4d5241442c447a2b5878b06dfa63dc236e68f3536d689 \
    "$KITDEV_RUNTIME_ROOT/firecrackers/v1.14.1_431f1fc/amd64/firecracker" \
    1982f8d5f1bc1680a36b0cdf126f605834b1633bba200d3281bccd53b86ff9ee \
    "$KITDEV_RUNTIME_ROOT/kernels/vmlinux-6.1.158/amd64/vmlinux.bin" \
    d7cce939adb09a41a22a5f846d22ba8d576b38dbb2b46a5c77a3a3e27ec52520 \
    "$KITDEV_RUNTIME_ROOT/busybox/1.36.1/amd64/busybox" \
    530d84dfbfd82c05181e0dc61ca842f3caaa349b0cc2f3f52d2d8eb9478aa67e \
    "$KITDEV_RUNTIME_ROOT/envd/envd" |
    sha256sum --check --strict --status || control_plane_die runtime_artifact_mismatch 65
}

verify_orchestrator_build() {
  /usr/bin/python3 -I -B -S - "$KITDEV_RUNTIME_ROOT/orchestrator" "$ADMISSION_PATCH" <<'PY_VERIFY_BUILD'
import hashlib
import json
import os
import re
import stat
import sys
from pathlib import Path

root = Path(sys.argv[1])
patch = Path(sys.argv[2])
manifest = root / "build-manifest.json"
metadata = os.lstat(manifest)
if not stat.S_ISREG(metadata.st_mode) or metadata.st_uid != 0 or metadata.st_gid != 0 or stat.S_IMODE(metadata.st_mode) != 0o600 or metadata.st_nlink != 1:
    raise SystemExit(1)
document = json.loads(manifest.read_text(encoding="ascii"))
if document.get("schema_version") != 2 or document.get("source_commit") != "882a3b4786755db9e94be3297de6827f9100ce5e" or document.get("platform") != "linux/amd64":
    raise SystemExit(1)
patch_metadata = os.lstat(patch)
if not stat.S_ISREG(patch_metadata.st_mode) or patch_metadata.st_uid != 0 or patch_metadata.st_gid != 0 or stat.S_IMODE(patch_metadata.st_mode) != 0o644 or patch_metadata.st_nlink != 1:
    raise SystemExit(1)
admission = document.get("host_admission", {})
if admission != {
    "patch_sha256": hashlib.sha256(patch.read_bytes()).hexdigest(),
    "max_live_sandboxes": 1,
    "max_concurrent_starts": 1,
    "max_concurrent_builds": 1,
    "max_vcpu": 2,
    "max_ram_mb": 8192,
    "max_disk_mb": 25600,
}:
    raise SystemExit(1)
artifacts = document.get("artifacts", {})
if set(artifacts) != {"orchestrator", "clean-nfs-cache"}:
    raise SystemExit(1)
for name, record in artifacts.items():
    path = root / name
    metadata = os.lstat(path)
    if not stat.S_ISREG(metadata.st_mode) or metadata.st_uid != 0 or metadata.st_gid != 0 or stat.S_IMODE(metadata.st_mode) != 0o755 or metadata.st_nlink != 1 or metadata.st_size != record.get("size_bytes"):
        raise SystemExit(1)
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    if not re.fullmatch(r"[0-9a-f]{64}", record.get("sha256", "")) or digest != record["sha256"]:
        raise SystemExit(1)
PY_VERIFY_BUILD
}

verify_network_overlap() {
  /usr/bin/python3 -I -B -S - <<'PY_NETWORK_OVERLAP'
import ipaddress
import json
import subprocess

reserved = (ipaddress.ip_network("10.11.0.0/16"), ipaddress.ip_network("10.12.0.0/16"))
routes = json.loads(subprocess.check_output(["ip", "-j", "-4", "route", "show", "table", "all"], text=True))
for route in routes:
    destination = route.get("dst")
    if not destination or destination == "default":
        continue
    try:
        network = ipaddress.ip_network(destination, strict=False)
    except ValueError:
        continue
    if any(candidate.overlaps(network) for candidate in reserved):
        raise SystemExit(1)
identifiers = subprocess.check_output(["docker", "network", "ls", "--quiet"], text=True).split()
if identifiers:
    documents = json.loads(subprocess.check_output(["docker", "network", "inspect", *identifiers], text=True))
    for document in documents:
        # "Config" is present but null on the built-in host and none networks,
        # so a dict default never applies. See bootstrap-network.sh.
        for item in document.get("IPAM", {}).get("Config") or []:
            try:
                network = ipaddress.ip_network(item.get("Subnet", ""), strict=True)
            except ValueError:
                continue
            if any(candidate.overlaps(network) for candidate in reserved):
                raise SystemExit(1)
PY_NETWORK_OVERLAP
}

verify_environment() {
  /usr/bin/python3 -I -B -S - "$ORCHESTRATOR_ENV" "$EXPECTED_ORCHESTRATOR_ENV" <<'PY_ORCHESTRATOR_ENV'
import os
import stat
import sys

paths = sys.argv[1:]
contents = []
for path in paths:
    metadata = os.lstat(path)
    if not stat.S_ISREG(metadata.st_mode) or metadata.st_uid != 0 or metadata.st_gid != 0 or stat.S_IMODE(metadata.st_mode) != 0o600 or metadata.st_nlink != 1:
        raise SystemExit(1)
    with open(path, "rb") as stream:
        contents.append(stream.read(65_537))
if any(len(content) > 65_536 for content in contents) or contents[0] != contents[1]:
    raise SystemExit(1)
values = {}
for line in contents[0].decode("ascii").splitlines():
    if "=" not in line:
        raise SystemExit(1)
    key, value = line.split("=", 1)
    if key in values or not value:
        raise SystemExit(1)
    values[key] = value
if set(values) != {
    "KITDEV_LIFECYCLE", "NODE_ID", "NODE_IP", "ENVIRONMENT", "ORCHESTRATOR_SERVICES",
    "USE_LOCAL_NAMESPACE_STORAGE", "GRPC_PORT", "PROXY_PORT", "NBD_POOL_SIZE",
    "KITDEV_MAX_LIVE_SANDBOXES", "KITDEV_MAX_CONCURRENT_STARTS",
    "KITDEV_MAX_CONCURRENT_BUILDS", "KITDEV_MAX_VCPU", "KITDEV_MAX_RAM_MB",
    "KITDEV_MAX_DISK_MB",
    "ORCHESTRATOR_LOCK_PATH", "ORCHESTRATOR_BASE_PATH", "SANDBOX_DIR", "SANDBOX_CACHE_DIR",
    "SNAPSHOT_CACHE_DIR", "TEMPLATE_CACHE_DIR", "TEMPLATES_DIR", "SHARED_CHUNK_CACHE_PATH",
    "TEMPLATE_STORAGE_URL", "BUILD_CACHE_STORAGE_URL", "LOCAL_UPLOAD_BASE_URL", "PROVIDER",
    "ARTIFACTS_REGISTRY_PROVIDER", "TARGET_ARCH", "DEFAULT_KERNEL_VERSION",
    "DEFAULT_FIRECRACKER_VERSION", "FIRECRACKER_VERSIONS_DIR", "HOST_KERNELS_DIR",
    "BUSYBOX_VERSION", "HOST_BUSYBOX_DIR", "HOST_ENVD_PATH", "SANDBOX_ORCHESTRATOR_IP",
    "SANDBOX_HYPERLOOP_PROXY_PORT", "SANDBOX_NFS_PROXY_PORT", "SANDBOX_PORTMAPPER_PORT",
    "SANDBOX_TCP_FIREWALL_HTTP_PORT", "SANDBOX_TCP_FIREWALL_TLS_PORT",
    "SANDBOX_TCP_FIREWALL_OTHER_PORT", "SANDBOXES_HOST_NETWORK_CIDR",
    "SANDBOXES_VRT_NETWORK_CIDR",
}:
    raise SystemExit(1)
integer_names = {
    "NBD_POOL_SIZE", "KITDEV_MAX_LIVE_SANDBOXES", "KITDEV_MAX_CONCURRENT_STARTS",
    "KITDEV_MAX_CONCURRENT_BUILDS", "KITDEV_MAX_VCPU", "KITDEV_MAX_RAM_MB",
    "KITDEV_MAX_DISK_MB",
}
try:
    limits = {name: int(values[name]) for name in integer_names}
except ValueError:
    raise SystemExit(1)
if any(value < 1 for value in limits.values()):
    raise SystemExit(1)
if limits["KITDEV_MAX_CONCURRENT_STARTS"] > limits["KITDEV_MAX_LIVE_SANDBOXES"]:
    raise SystemExit(1)
if limits["NBD_POOL_SIZE"] < limits["KITDEV_MAX_LIVE_SANDBOXES"] + limits["KITDEV_MAX_CONCURRENT_BUILDS"]:
    raise SystemExit(1)

meminfo = {}
with open("/proc/meminfo", encoding="ascii") as stream:
    for line in stream:
        fields = line.split()
        if len(fields) >= 2 and fields[1].isdigit():
            meminfo[fields[0].rstrip(":")] = int(fields[1])
if meminfo.get("Hugepagesize") != 2048:
    raise SystemExit(1)
required_mib = limits["KITDEV_MAX_RAM_MB"] * (
    limits["KITDEV_MAX_LIVE_SANDBOXES"] + 2 * limits["KITDEV_MAX_CONCURRENT_BUILDS"]
)
required_pages = (required_mib * 1024 + meminfo["Hugepagesize"] - 1) // meminfo["Hugepagesize"]
if meminfo.get("HugePages_Total", 0) < required_pages or meminfo.get("HugePages_Free", 0) < required_pages:
    raise SystemExit(1)
PY_ORCHESTRATOR_ENV
}

main() {
  local command
  require_root
  require_lifecycle_platform
  require_worker_identity
  for command in bash docker unshare mount umount ip iptables e2fsck resize2fs tune2fs debugfs rsync du sha256sum; do
    require_command "$command"
  done
  [[ -c /dev/kvm && -r /dev/kvm && -w /dev/kvm ]] || control_plane_die kvm_unusable 65
  [[ -c /dev/net/tun ]] || control_plane_die tun_unusable 65
  [[ -r /sys/module/nbd/parameters/nbds_max ]] || control_plane_die nbd_not_loaded 65
  [[ "$(cat /sys/module/nbd/parameters/nbds_max)" -ge 16 ]] || control_plane_die nbd_pool_too_small 65
  [[ -b /dev/nbd0 ]] || control_plane_die nbd_device_missing 65
  grep -qw cpu /sys/fs/cgroup/cgroup.controllers || control_plane_die cgroup_cpu_missing 65
  grep -qw memory /sys/fs/cgroup/cgroup.controllers || control_plane_die cgroup_memory_missing 65
  ip -4 route show default | awk 'NF {found=1} END {exit !found}' || control_plane_die default_route_missing 65
  [[ "$(sysctl -n net.ipv4.ip_forward)" == 1 ]] || control_plane_die ipv4_forwarding_disabled 65
  grep -q '^Hugepagesize:[[:space:]]*2048 kB$' /proc/meminfo || control_plane_die hugepage_size_mismatch 65
  verify_fixed_artifacts
  verify_orchestrator_build || control_plane_die orchestrator_build_invalid 65
  verify_network_overlap || control_plane_die sandbox_network_overlap 65
  verify_environment || control_plane_die orchestrator_environment_invalid 65
  "$SCRIPT_DIR/bootstrap-network.sh" verify >/dev/null
  "$SCRIPT_DIR/configure-firewall.sh" verify >/dev/null
  printf 'status=pass operation=preflight-orchestrator\n'
}

main "$@"
