#!/usr/bin/env bash
set -Eeuo pipefail
readonly SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
# shellcheck source=common.sh
source "$SCRIPT_DIR/common.sh"

main() {
  local mode="${1:-}"
  local facts
  [[ "$mode" == ensure || "$mode" == verify || "$mode" == adopt-unlabeled ]] ||
    control_plane_die invalid_operation 64
  require_root
  require_lifecycle_platform
  require_command docker
  require_command ip
  /usr/bin/python3 -I -B -S "$SCRIPT_DIR/private_env.py" verify >/dev/null
  facts="$(/usr/bin/python3 -I -B -S - "$mode" \
    "$KITDEV_RUNTIME_ROOT/control-plane/network-adoption.json" <<'PY_NETWORK'
import ipaddress
import json
import os
import re
import stat
import subprocess
import sys

mode = sys.argv[1]
adoption_path = sys.argv[2]
name = "kitdev-core"
reserved = (ipaddress.ip_network("10.11.0.0/16"), ipaddress.ip_network("10.12.0.0/16"))
allowed_labels = {
    "io.kitdev-sandboxes.component": "control-plane",
    "io.kitdev-sandboxes.deployment": "single-host",
}


def run(arguments, *, check=True):
    return subprocess.run(arguments, check=check, capture_output=True, text=True)


def inspect_network():
    result = run(["docker", "network", "inspect", name], check=False)
    if result.returncode != 0:
        return None
    try:
        document = json.loads(result.stdout)
    except json.JSONDecodeError:
        raise SystemExit(1)
    if not isinstance(document, list) or len(document) != 1:
        raise SystemExit(1)
    return document[0]


def occupied_networks():
    occupied = list(reserved)
    routes = json.loads(run(["ip", "-j", "-4", "route", "show", "table", "all"]).stdout)
    for route in routes:
        destination = route.get("dst")
        if not destination or destination == "default":
            continue
        try:
            occupied.append(ipaddress.ip_network(destination, strict=False))
        except ValueError:
            pass
    identifiers = run(["docker", "network", "ls", "--quiet"]).stdout.split()
    if identifiers:
        documents = json.loads(run(["docker", "network", "inspect", *identifiers]).stdout)
        for document in documents:
            # Docker emits "Config": null for the built-in host and none
            # networks, so a dict default never applies -- the key is present
            # and holds null. Every host has those two networks.
            for item in document.get("IPAM", {}).get("Config") or []:
                try:
                    occupied.append(ipaddress.ip_network(item.get("Subnet", ""), strict=True))
                except ValueError:
                    pass
    return occupied


def validate_adoption(document, subnet, gateway):
    try:
        metadata = os.lstat(adoption_path)
    except FileNotFoundError:
        return False
    if not stat.S_ISREG(metadata.st_mode) or metadata.st_uid != 0 or metadata.st_gid != 0 or stat.S_IMODE(metadata.st_mode) != 0o600 or metadata.st_nlink != 1:
        raise SystemExit(1)
    try:
        adoption = json.loads(open(adoption_path, encoding="ascii").read())
    except (OSError, json.JSONDecodeError):
        raise SystemExit(1)
    return adoption == {
        "schema_version": 1,
        "network_id": document["Id"],
        "subnet": str(subnet),
        "gateway": str(gateway),
        "adopted_unlabeled": True,
    }


def write_adoption(document, subnet, gateway):
    import tempfile
    payload = (json.dumps({
        "schema_version": 1,
        "network_id": document["Id"],
        "subnet": str(subnet),
        "gateway": str(gateway),
        "adopted_unlabeled": True,
    }, sort_keys=True, separators=(",", ":")) + "\n").encode("ascii")
    parent = os.path.dirname(adoption_path)
    metadata = os.lstat(parent)
    if not stat.S_ISDIR(metadata.st_mode) or metadata.st_uid != 0 or metadata.st_gid != 0 or stat.S_IMODE(metadata.st_mode) != 0o700:
        raise SystemExit(1)
    descriptor, temporary = tempfile.mkstemp(prefix=".network-adoption.", dir=parent)
    try:
        os.fchmod(descriptor, 0o600)
        view = memoryview(payload)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                raise SystemExit(1)
            view = view[written:]
        os.fsync(descriptor)
        os.close(descriptor)
        descriptor = -1
        try:
            os.link(temporary, adoption_path, follow_symlinks=False)
        except FileExistsError:
            if not validate_adoption(document, subnet, gateway):
                raise SystemExit(1)
        os.unlink(temporary)
        temporary = ""
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        if temporary:
            try:
                os.unlink(temporary)
            except FileNotFoundError:
                pass


def validate(document):
    if (
        document.get("Name") != name
        or document.get("Driver") != "bridge"
        or document.get("Scope") != "local"
        or document.get("Internal") is not False
        or document.get("Ingress") is not False
        or document.get("EnableIPv6") is not False
    ):
        raise SystemExit(1)
    labels = document.get("Labels") or {}
    options = document.get("Options") or {}
    if set(options) - {"com.docker.network.bridge.name"}:
        raise SystemExit(1)
    configurations = document.get("IPAM", {}).get("Config") or []
    if len(configurations) != 1:
        raise SystemExit(1)
    configuration = configurations[0]
    if set(configuration) - {"Subnet", "Gateway"}:
        raise SystemExit(1)
    try:
        subnet = ipaddress.ip_network(configuration["Subnet"], strict=True)
        gateway = ipaddress.ip_address(configuration["Gateway"])
    except (KeyError, ValueError):
        raise SystemExit(1)
    if subnet.version != 4 or not subnet.is_private or gateway not in subnet:
        raise SystemExit(1)
    if any(subnet.overlaps(candidate) for candidate in reserved):
        raise SystemExit(1)
    identifier = document.get("Id", "")
    if not re.fullmatch(r"[0-9a-f]{64}", identifier):
        raise SystemExit(1)
    bridge = options.get("com.docker.network.bridge.name") or f"br-{identifier[:12]}"
    if not re.fullmatch(r"[A-Za-z0-9_.+-]{1,15}", bridge):
        raise SystemExit(1)
    if labels != allowed_labels:
        if labels or not validate_adoption(document, subnet, gateway):
            if mode != "adopt-unlabeled" or labels:
                raise SystemExit(1)
            write_adoption(document, subnet, gateway)
    run(["ip", "link", "show", "dev", bridge])
    return str(subnet), str(gateway), bridge


document = inspect_network()
if document is None:
    if mode != "ensure":
        raise SystemExit(1)
    occupied = occupied_networks()
    selected = None
    for index in range(18, 32):
        candidate = ipaddress.ip_network(f"172.{index}.0.0/16")
        if not any(candidate.overlaps(item) for item in occupied):
            selected = candidate
            break
    if selected is None:
        raise SystemExit(1)
    gateway = next(selected.hosts())
    created = run([
        "docker", "network", "create", "--driver", "bridge",
        "--subnet", str(selected), "--gateway", str(gateway),
        "--label", "io.kitdev-sandboxes.component=control-plane",
        "--label", "io.kitdev-sandboxes.deployment=single-host", name,
    ], check=False)
    if created.returncode != 0 and inspect_network() is None:
        raise SystemExit(1)
    document = inspect_network()
subnet, gateway, bridge = validate(document)
print(subnet)
print(gateway)
print(bridge)
PY_NETWORK
  )" || control_plane_die core_network_conflict 65
  mapfile -t values <<<"$facts"
  [[ "${#values[@]}" == 3 ]] || control_plane_die core_network_invalid 65
  if [[ "$mode" == ensure || "$mode" == adopt-unlabeled ]]; then
    /usr/bin/python3 -I -B -S "$SCRIPT_DIR/private_env.py" set-network \
      "${values[0]}" "${values[1]}" >/dev/null
  else
    expected="$(/usr/bin/python3 -I -B -S "$SCRIPT_DIR/private_env.py" get-network)" ||
      control_plane_die private_network_missing 65
    [[ "$expected" == "${values[0]}"$'\n'"${values[1]}" ]] ||
      control_plane_die core_network_env_mismatch 65
  fi
  printf 'status=pass operation=%s-core-network\n' "$mode"
}

main "$@"
