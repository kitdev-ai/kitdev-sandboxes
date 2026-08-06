#!/usr/bin/env bash
set -Eeuo pipefail
readonly SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
# shellcheck source=common.sh
source "$SCRIPT_DIR/common.sh"

network_facts() {
  /usr/bin/python3 -I -B -S - "$1" "$2" <<'PY_FIREWALL_FACTS'
import json
import subprocess
import sys

subnet, gateway = sys.argv[1:]
document = json.loads(subprocess.check_output(["docker", "network", "inspect", "kitdev-core"], text=True))[0]
options = document.get("Options") or {}
bridge = options.get("com.docker.network.bridge.name") or "br-" + document["Id"][:12]
config = document["IPAM"]["Config"]
if len(config) != 1 or config[0].get("Subnet") != subnet or config[0].get("Gateway") != gateway:
    raise SystemExit(1)
routes = json.loads(subprocess.check_output(["ip", "-j", "-4", "route", "show", "default"], text=True))
candidates = [(int(route.get("metric", 0)), route.get("dev")) for route in routes if route.get("dev")]
if not candidates:
    raise SystemExit(1)
best_metric = min(metric for metric, _ in candidates)
devices = {device for metric, device in candidates if metric == best_metric}
if len(devices) != 1:
    raise SystemExit(1)
print(subnet)
print(gateway)
print(bridge)
print(devices.pop())
PY_FIREWALL_FACTS
}

verify_rules() {
  local include_build="$1" policy="$2" rules
  rules="$(ufw show added)" || control_plane_die ufw_rules_unreadable 65
  /usr/bin/python3 -I -B -S - "$core_subnet" "$core_gateway" "$core_bridge" \
    "$outbound_interface" "$include_build" "$policy" 3<<<"$rules" <<'PY_VERIFY_UFW'
import ipaddress
import os
import re
import shlex
import sys

subnet, gateway, bridge, outbound, include_build, policy = sys.argv[1:]
expected = {
    ("ufw", "allow", "in", "on", bridge, "from", subnet, "to", gateway, "port", "5008", "proto", "tcp"),
    ("ufw", "allow", "in", "on", "veth+", "from", "10.11.0.0/16", "to", "any", "port", "5010:5012", "proto", "tcp"),
    ("ufw", "allow", "in", "on", "veth+", "from", "10.11.0.0/16", "to", "any", "port", "5016:5018", "proto", "tcp"),
    ("ufw", "route", "allow", "in", "on", "veth+", "out", "on", outbound, "from", "10.11.0.0/16", "to", "any"),
}
build = ("ufw", "allow", "in", "on", "veth+", "from", "10.11.0.0/16", "to", "any", "port", "5516:5518", "proto", "tcp")
if include_build == "yes":
    expected.add(build)
protected = {"5008", "5010:5012", "5016:5018", "5516:5518", "10.11.0.0/16"}
observed = set()


def port_overlap(tokens):
    for token in tokens:
        match = re.fullmatch(r"([0-9]+)(?::([0-9]+))?(?:/tcp)?", token)
        if not match:
            continue
        first = int(match.group(1))
        last = int(match.group(2) or match.group(1))
        if first > last:
            first, last = last, first
        if any(first <= port <= last for port in range(5007, 5019)) or any(first <= port <= last for port in range(5516, 5519)):
            return True
    return False


def guest_source_overlap(tokens):
    if "from" not in tokens:
        return False
    index = tokens.index("from") + 1
    if index >= len(tokens) or tokens[index] == "any":
        return True
    try:
        source = ipaddress.ip_network(tokens[index], strict=False)
    except ValueError:
        return False
    return source.overlaps(ipaddress.ip_network("10.11.0.0/16"))


for line in os.fdopen(3, encoding="utf-8"):
    line = line.strip()
    if not line.startswith("ufw "):
        continue
    tokens = shlex.split(line)
    if "comment" in tokens:
        tokens = tokens[:tokens.index("comment")]
    value = tuple(tokens)
    if protected.intersection(tokens) or port_overlap(tokens) or guest_source_overlap(tokens):
        observed.add(value)
if policy == "subset":
    valid = observed <= expected
elif policy == "exact":
    valid = observed == expected
else:
    valid = False
if not valid:
    raise SystemExit(1)
PY_VERIFY_UFW
}

main() {
  local mode="${1:-}" facts status network_values
  case "$mode" in apply|verify|build-open|build-close|verify-build) ;;
    *) control_plane_die invalid_operation 64 ;;
  esac
  require_root
  require_lifecycle_platform
  require_command docker
  require_command ip
  require_command ufw
  /usr/bin/python3 -I -B -S "$SCRIPT_DIR/private_env.py" verify >/dev/null
  "$SCRIPT_DIR/bootstrap-network.sh" verify >/dev/null
  network_values="$(/usr/bin/python3 -I -B -S "$SCRIPT_DIR/private_env.py" get-network)" ||
    control_plane_die firewall_network_facts_invalid 65
  mapfile -t private_network <<<"$network_values"
  [[ "${#private_network[@]}" == 2 ]] || control_plane_die firewall_network_facts_invalid 65
  facts="$(network_facts "${private_network[0]}" "${private_network[1]}")" ||
    control_plane_die firewall_network_facts_invalid 65
  mapfile -t values <<<"$facts"
  [[ "${#values[@]}" == 4 ]] || control_plane_die firewall_network_facts_invalid 65
  core_subnet="${values[0]}"
  core_gateway="${values[1]}"
  core_bridge="${values[2]}"
  outbound_interface="${values[3]}"
  status="$(ufw status | awk 'NR == 1 {print; next} {next}')"
  [[ "$status" == 'Status: active' ]] || control_plane_die ufw_not_active 65

  case "$mode" in
    apply)
      verify_rules no subset || control_plane_die ufw_rule_conflict 65
      ufw allow in on "$core_bridge" from "$core_subnet" to "$core_gateway" \
        port 5008 proto tcp comment 'kitdev core to orchestrator'
      ufw allow in on veth+ from 10.11.0.0/16 to any \
        port 5010:5012 proto tcp comment 'kitdev guest internal services'
      ufw allow in on veth+ from 10.11.0.0/16 to any \
        port 5016:5018 proto tcp comment 'kitdev guest firewall proxies'
      ufw route allow in on veth+ out on "$outbound_interface" \
        from 10.11.0.0/16 to any comment 'kitdev guest egress'
      verify_rules no exact || control_plane_die ufw_rule_mismatch 65
      ;;
    verify) verify_rules no exact || control_plane_die ufw_rule_mismatch 65 ;;
    build-open)
      verify_rules no exact || control_plane_die ufw_rule_conflict 65
      ufw allow in on veth+ from 10.11.0.0/16 to any \
        port 5516:5518 proto tcp comment 'kitdev temporary build proxies'
      verify_rules yes exact || control_plane_die ufw_build_rule_mismatch 65
      ;;
    build-close)
      if verify_rules no exact; then
        :
      elif verify_rules yes exact; then
        ufw --force delete allow in on veth+ from 10.11.0.0/16 to any \
          port 5516:5518 proto tcp
      else
        control_plane_die ufw_build_rule_conflict 65
      fi
      verify_rules no exact || control_plane_die ufw_build_rule_cleanup_failed 65
      ;;
    verify-build) verify_rules yes exact || control_plane_die ufw_build_rule_mismatch 65 ;;
  esac
  printf 'status=pass operation=%s-firewall\n' "$mode"
}

main "$@"
