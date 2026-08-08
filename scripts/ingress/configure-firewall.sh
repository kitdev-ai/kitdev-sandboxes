#!/usr/bin/env bash
set -Eeuo pipefail
umask 077
readonly SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
# shellcheck source=../control-plane/common.sh
source "$SCRIPT_DIR/../control-plane/common.sh"

readonly SOURCE_STATE="$SCRIPT_DIR/firewall_source_state.py"
readonly SOURCE_MANIFEST=/etc/kitdev-sandboxes/ingress/allowed-sources.json
readonly FIREWALL_LOCK=/run/kitdev-sandboxes/ingress-firewall.lock
readonly UFW_COMMENT='kitdev restricted ingress https'
readonly PUBLIC_UFW_COMMENT='kitdev public ingress https explicit'
readonly GUARD_CHAIN=KITDEV-INGRESS
readonly GUARD_COMMENT='kitdev restricted ingress docker guard'
readonly ALLOW_COMMENT='kitdev restricted ingress docker allow'
readonly HTTP_DENY_COMMENT='kitdev restricted ingress docker http deny'
readonly HTTPS_DENY_COMMENT='kitdev restricted ingress docker https deny'
readonly CONTROL_PLANE_FIREWALL="$SCRIPT_DIR/../control-plane/configure-firewall.sh"

source_count() {
  /usr/bin/python3 -I -B -S - "$1" <<'PY_SOURCE_COUNT'
import json
import sys

with open(sys.argv[1], "r", encoding="ascii") as stream:
    print(len(json.load(stream)["sources"]))
PY_SOURCE_COUNT
}

source_family_count() {
  /usr/bin/python3 -I -B -S - "$1" "$2" <<'PY_SOURCE_FAMILY_COUNT'
import ipaddress
import json
import sys

with open(sys.argv[1], "r", encoding="ascii") as stream:
    document = json.load(stream)
print(sum(ipaddress.ip_network(item["cidr"]).version == int(sys.argv[2]) for item in document["sources"]))
PY_SOURCE_FAMILY_COUNT
}

firewall_mode() {
  /usr/bin/python3 -I -B -S - "$1" <<'PY_FIREWALL_MODE'
import json
import sys

with open(sys.argv[1], "r", encoding="ascii") as stream:
    print(json.load(stream)["mode"])
PY_FIREWALL_MODE
}

outbound_interface() {
  local routes
  routes="$(ip -j -4 route show default)" || return 1
  /usr/bin/python3 -I -B -S - "$routes" <<'PY_DEFAULT_INTERFACE'
import json
import re
import sys

routes = json.loads(sys.argv[1])
interfaces = {route.get("dev") for route in routes if route.get("dst") == "default"}
if len(interfaces) != 1:
    raise SystemExit(1)
interface = interfaces.pop()
if not isinstance(interface, str) or re.fullmatch(r"[A-Za-z0-9_.:-]{1,15}", interface) is None:
    raise SystemExit(1)
print(interface)
PY_DEFAULT_INTERFACE
}

family_sources() {
  /usr/bin/python3 -I -B -S - "$1" "$2" <<'PY_FAMILY_SOURCES'
import ipaddress
import json
import sys

with open(sys.argv[1], "r", encoding="ascii") as stream:
    document = json.load(stream)
for item in document["sources"]:
    if ipaddress.ip_network(item["cidr"]).version == int(sys.argv[2]):
        print(item["cidr"])
PY_FAMILY_SOURCES
}

ssh_ports() {
  sshd -T | awk '$1 == "port" && $2 ~ /^[0-9]+$/ {print $2}' | sort -nu
}

verify_ufw_defaults() {
  local status
  status="$(ufw status verbose)" || return 1
  grep -Fx 'Status: active' <<<"$status" >/dev/null || return 1
  grep -Fx 'Default: deny (incoming), allow (outgoing), deny (routed)' <<<"$status" >/dev/null
}

verify_ufw_ipv6() {
  [[ ! -L /etc/default/ufw && -f /etc/default/ufw ]] || return 1
  grep -Fx 'IPV6=yes' /etc/default/ufw >/dev/null
}

verify_ufw_rules() {
  local policy="$1" sources_file="$2" rules ports
  rules="$(ufw show added)" || return 1
  ports="$(ssh_ports)" || return 1
  [[ -n "$ports" ]] || return 1
  /usr/bin/python3 -I -B -S - "$policy" "$sources_file" "$UFW_COMMENT" "$ports" \
    3<<<"$rules" <<'PY_VERIFY_INGRESS_UFW'
import ipaddress
import json
import os
import re
import shlex
import sys
from collections import Counter

policy, source_path, comment, ssh_text = sys.argv[1:]
with open(source_path, "r", encoding="ascii") as stream:
    document = json.load(stream)
sources = [item["cidr"] for item in document["sources"]]
if policy == "restricted":
    expected = Counter(
        ("ufw", "allow", "proto", "tcp", "from", source, "to", "any", "port", "443", "comment", comment)
        for source in sources
    )
elif policy == "public":
    expected = Counter({("ufw", "allow", "443/tcp", "comment", "kitdev public ingress https explicit"): 1})
elif policy in {"closed", "absent"}:
    expected = Counter()
else:
    raise SystemExit(1)
observed = Counter()
ssh_ports = {int(value) for value in ssh_text.splitlines()}
ssh_allowed = set()
sensitive = {3000, 3002, 3003, 3100, 5007, 5008, 5432, 6379, 8123, 9000}


def ranges(tokens):
    for token in tokens:
        match = re.fullmatch(r"([0-9]+)(?::([0-9]+))?(?:/tcp)?", token)
        if match:
            yield int(match.group(1)), int(match.group(2) or match.group(1))


for line in os.fdopen(3, encoding="utf-8"):
    line = line.strip()
    if not line.startswith("ufw "):
        continue
    tokens = shlex.split(line)
    unannotated = tokens[:tokens.index("comment")] if "comment" in tokens else tokens
    for first, last in ranges(unannotated):
        if first <= 80 <= last or first <= 443 <= last:
            observed[tuple(tokens)] += 1
        if tokens[1:2] == ["allow"]:
            ssh_allowed.update(port for port in ssh_ports if first <= port <= last)
        if any(first <= port <= last for port in sensitive):
            source = unannotated[unannotated.index("from") + 1] if "from" in unannotated else "any"
            try:
                network = ipaddress.ip_network(source)
            except ValueError:
                network = None
            if source == "any" or (network is not None and network.prefixlen == 0):
                raise SystemExit(1)
if ssh_allowed != ssh_ports:
    raise SystemExit(1)
valid = observed == expected
raise SystemExit(0 if valid else 1)
PY_VERIFY_INGRESS_UFW
}

verify_listeners() {
  /usr/bin/python3 -I -B -S - <<'PY_VERIFY_INGRESS_LISTENERS'
import subprocess

sensitive = {80, 3000, 3002, 3003, 3100, 5432, 6379, 8123, 9000}
public = {"0.0.0.0", "::", "*"}
output = subprocess.check_output(["ss", "-H", "-ltn"], text=True)
for line in output.splitlines():
    fields = line.split()
    if len(fields) < 4 or ":" not in fields[3]:
        raise SystemExit(1)
    address, port_text = fields[3].rsplit(":", 1)
    address = address.strip("[]")
    try:
        port = int(port_text)
    except ValueError:
        continue
    if port in sensitive and address in public:
        raise SystemExit(1)
PY_VERIFY_INGRESS_LISTENERS
}

verify_docker_publications() {
  local ports
  ports="$(docker ps --format '{{.Ports}}')" || return 1
  /usr/bin/python3 -I -B -S - "$ports" <<'PY_VERIFY_DOCKER_PORTS'
import re
import sys

# Docker publishes ranges as "0.0.0.0:3002-3003->3002-3003/tcp". Matching only
# a single port silently skipped every range, so a datastore published as a
# range on a public address passed this check. That matters more here than
# anywhere else: published ports install nat DNAT rules that bypass ufw's INPUT
# chain entirely, so this function is the only thing standing between a
# misconfigured container and the Internet.
SENSITIVE = {80, 443, 3000, 3002, 3003, 3100, 5432, 6379, 8123, 9000}
PUBLIC = {"0.0.0.0", "::", "[::]", "*"}
PUBLICATION = re.compile(r"(.+):([0-9]+)(?:-([0-9]+))?->")

for entry in sys.argv[1].splitlines():
    for part in entry.split(", "):
        if "->" not in part:
            continue
        match = PUBLICATION.match(part.strip())
        if match is None:
            # An unparseable publication is not proof of safety.
            raise SystemExit(1)
        address, first, last = match.groups()
        if address not in PUBLIC:
            continue
        start = int(first)
        end = int(last) if last else start
        if end < start or any(start <= port <= end for port in SENSITIVE):
            raise SystemExit(1)
PY_VERIFY_DOCKER_PORTS
}

verify_control_plane_firewall() {
  # A manually assembled development lab has correctly scoped control-plane
  # rules that this automation did not install, so the managed-ownership proof
  # below cannot succeed there. Acknowledging that is deliberately explicit,
  # development-only, and gives up only the ownership proof: UFW defaults,
  # IPv6, listener scope, Docker publication scope, and the sensitive-port
  # source check inside verify_ufw_rules all still run and still fail closed.
  if [[ "${KITDEV_UNMANAGED_CONTROL_PLANE_FIREWALL:-}" == acknowledged ]]; then
    [[ "${KITDEV_LIFECYCLE:-}" == development ]] || return 1
    printf 'warning=unmanaged_control_plane_firewall lifecycle=development\n' >&2
    return 0
  fi
  [[ ! -L "$CONTROL_PLANE_FIREWALL" && -f "$CONTROL_PLANE_FIREWALL" &&
    -x "$CONTROL_PLANE_FIREWALL" ]] || return 1
  if [[ "$SCRIPT_DIR" == "$KITDEV_OPT_ROOT/libexec/ingress" ]]; then
    [[ "$(stat -c '%u:%g:%a:%h' -- "$CONTROL_PLANE_FIREWALL")" == 0:0:755:1 ]] || return 1
  fi
  "$CONTROL_PLANE_FIREWALL" verify >/dev/null
}

open_firewall_lock() {
  /usr/bin/python3 -I -B -S - "$FIREWALL_LOCK" <<'PY_CREATE_FIREWALL_LOCK'
import os
import stat
import sys

path = sys.argv[1]
flags = os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC
try:
    descriptor = os.open(path, flags, 0o600)
except FileExistsError:
    descriptor = None
else:
    os.close(descriptor)
metadata = os.lstat(path)
if (
    not stat.S_ISREG(metadata.st_mode)
    or stat.S_ISLNK(metadata.st_mode)
    or metadata.st_uid != 0
    or metadata.st_gid != 0
    or stat.S_IMODE(metadata.st_mode) != 0o600
    or metadata.st_nlink != 1
    or metadata.st_size != 0
):
    raise SystemExit(1)
PY_CREATE_FIREWALL_LOCK
  exec 9<>"$FIREWALL_LOCK"
  [[ "$(stat -Lc '%u:%g:%a:%s:%h' -- /proc/self/fd/9)" == 0:0:600:0:1 ]] || return 1
  [[ "$(stat -Lc '%d:%i' -- /proc/self/fd/9)" == "$(stat -c '%d:%i' -- "$FIREWALL_LOCK")" ]]
}

guard_required() {
  local tool="$1" sources_file="$2"
  if [[ "$tool" == iptables ]]; then
    return 0
  fi
  [[ "$(source_family_count "$sources_file" 6)" != 0 ]] && return 0
  "$tool" -S DOCKER-USER >/dev/null 2>&1
}

verify_guard_tool() {
  local tool="$1" family="$2" policy="$3" sources_file="$4" interface="$5"
  local source mode expected_count=2 observed_count references
  mode="$(firewall_mode "$sources_file")" || return 1
  mapfile -t family_sources < <(family_sources "$sources_file" "$family")
  if [[ "$policy" == absent ]]; then
    ! "$tool" -S "$GUARD_CHAIN" >/dev/null 2>&1 || return 1
    if "$tool" -S DOCKER-USER >/dev/null 2>&1; then
      ! "$tool" -S DOCKER-USER | grep -F -- "-j $GUARD_CHAIN" >/dev/null
    fi
    return
  fi
  "$tool" -S DOCKER-USER >/dev/null 2>&1 || return 1
  "$tool" -S "$GUARD_CHAIN" >/dev/null 2>&1 || return 1
  for port in 80 443; do
    "$tool" -C DOCKER-USER -i "$interface" -p tcp -m conntrack --ctorigdstport "$port" \
      -m comment --comment "$GUARD_COMMENT" -j "$GUARD_CHAIN" || return 1
  done
  references="$("$tool" -S DOCKER-USER | grep -Fc -- "-j $GUARD_CHAIN" || true)"
  [[ "$references" == 2 ]] || return 1
  if [[ "$mode" == public ]]; then
    "$tool" -C "$GUARD_CHAIN" -p tcp -m conntrack --ctorigdstport 443 \
      -m comment --comment "$ALLOW_COMMENT" -j RETURN || return 1
    ((expected_count += 1))
  elif [[ "$mode" == restricted ]]; then
    for source in "${family_sources[@]}"; do
      "$tool" -C "$GUARD_CHAIN" -s "$source" -p tcp -m conntrack --ctorigdstport 443 \
        -m comment --comment "$ALLOW_COMMENT" -j RETURN || return 1
      ((expected_count += 1))
    done
  fi
  "$tool" -C "$GUARD_CHAIN" -p tcp -m conntrack --ctorigdstport 80 \
    -m comment --comment "$HTTP_DENY_COMMENT" -j DROP || return 1
  "$tool" -C "$GUARD_CHAIN" -p tcp -m conntrack --ctorigdstport 443 \
    -m comment --comment "$HTTPS_DENY_COMMENT" -j DROP || return 1
  observed_count="$("$tool" -S "$GUARD_CHAIN" | grep -c "^-A $GUARD_CHAIN " || true)"
  [[ "$observed_count" == "$expected_count" ]]
}

verify_guards() {
  local policy="$1" sources_file="$2" interface="$3"
  verify_guard_tool iptables 4 "$policy" "$sources_file" "$interface" || return 1
  if guard_required ip6tables "$sources_file"; then
    verify_guard_tool ip6tables 6 "$policy" "$sources_file" "$interface" || return 1
  else
    verify_guard_tool ip6tables 6 absent "$sources_file" "$interface" || return 1
  fi
}

add_guard_tool() {
  local tool="$1" family="$2" sources_file="$3" interface="$4" source mode
  mode="$(firewall_mode "$sources_file")" || return 1
  mapfile -t family_sources < <(family_sources "$sources_file" "$family")
  "$tool" -S DOCKER-USER >/dev/null 2>&1 || return 1
  ! "$tool" -S "$GUARD_CHAIN" >/dev/null 2>&1 || return 1
  "$tool" -N "$GUARD_CHAIN" || return 1
  if [[ "$mode" == public ]]; then
    "$tool" -A "$GUARD_CHAIN" -p tcp -m conntrack --ctorigdstport 443 \
      -m comment --comment "$ALLOW_COMMENT" -j RETURN || return 1
  elif [[ "$mode" == restricted ]]; then
    for source in "${family_sources[@]}"; do
      "$tool" -A "$GUARD_CHAIN" -s "$source" -p tcp -m conntrack --ctorigdstport 443 \
        -m comment --comment "$ALLOW_COMMENT" -j RETURN || return 1
    done
  fi
  "$tool" -A "$GUARD_CHAIN" -p tcp -m conntrack --ctorigdstport 80 \
    -m comment --comment "$HTTP_DENY_COMMENT" -j DROP || return 1
  "$tool" -A "$GUARD_CHAIN" -p tcp -m conntrack --ctorigdstport 443 \
    -m comment --comment "$HTTPS_DENY_COMMENT" -j DROP || return 1
  for port in 443 80; do
    "$tool" -I DOCKER-USER 1 -i "$interface" -p tcp -m conntrack --ctorigdstport "$port" \
      -m comment --comment "$GUARD_COMMENT" -j "$GUARD_CHAIN" || return 1
  done
}

delete_owned_guard() {
  local tool="$1" interface="$2"
  if "$tool" -S DOCKER-USER >/dev/null 2>&1; then
    for port in 80 443; do
      while "$tool" -C DOCKER-USER -i "$interface" -p tcp -m conntrack \
        --ctorigdstport "$port" -m comment --comment "$GUARD_COMMENT" \
        -j "$GUARD_CHAIN" >/dev/null 2>&1; do
        "$tool" -D DOCKER-USER -i "$interface" -p tcp -m conntrack \
          --ctorigdstport "$port" -m comment --comment "$GUARD_COMMENT" \
          -j "$GUARD_CHAIN" || return 1
      done
    done
  fi
  if "$tool" -S "$GUARD_CHAIN" >/dev/null 2>&1; then
    "$tool" -F "$GUARD_CHAIN" || return 1
    "$tool" -X "$GUARD_CHAIN" || return 1
  fi
}

add_system_rules() {
  local sources_file="$1" interface="$2" source mode
  mode="$(firewall_mode "$sources_file")" || return 1
  verify_ufw_rules absent "$sources_file" || return 1
  verify_guards absent "$sources_file" "$interface" || return 1
  if [[ "$mode" == public ]]; then
    ufw allow 443/tcp comment "$PUBLIC_UFW_COMMENT" || return 1
  elif [[ "$mode" == restricted ]]; then
    while IFS= read -r source; do
      ufw allow proto tcp from "$source" to any port 443 comment "$UFW_COMMENT" || return 1
    done < <(/usr/bin/python3 -I -B -S "$SOURCE_STATE" get-file "$sources_file")
  fi
  add_guard_tool iptables 4 "$sources_file" "$interface" || return 1
  if guard_required ip6tables "$sources_file"; then
    add_guard_tool ip6tables 6 "$sources_file" "$interface" || return 1
  fi
  verify_ufw_rules "$mode" "$sources_file" || return 1
  verify_guards exact "$sources_file" "$interface"
}

remove_system_rules() {
  local sources_file="$1" interface="$2" source mode
  mode="$(firewall_mode "$sources_file")" || return 1
  verify_ufw_rules "$mode" "$sources_file" || return 1
  verify_guards exact "$sources_file" "$interface" || return 1
  if [[ "$mode" == public ]]; then
    ufw --force delete allow 443/tcp comment "$PUBLIC_UFW_COMMENT" || return 1
  elif [[ "$mode" == restricted ]]; then
    while IFS= read -r source; do
      ufw --force delete allow proto tcp from "$source" to any port 443 \
        comment "$UFW_COMMENT" || return 1
    done < <(/usr/bin/python3 -I -B -S "$SOURCE_STATE" get-file "$sources_file")
  fi
  delete_owned_guard iptables "$interface" || return 1
  if guard_required ip6tables "$sources_file"; then
    delete_owned_guard ip6tables "$interface" || return 1
  fi
  verify_ufw_rules absent "$sources_file" || return 1
  verify_guards absent "$sources_file" "$interface"
}

cleanup_candidate_rules() {
  local sources_file="$1" interface="$2" source
  while IFS= read -r source; do
    ufw --force delete allow proto tcp from "$source" to any port 443 \
      comment "$UFW_COMMENT" >/dev/null 2>&1 || true
  done < <(/usr/bin/python3 -I -B -S "$SOURCE_STATE" get-file "$sources_file")
  ufw --force delete allow 443/tcp comment "$PUBLIC_UFW_COMMENT" >/dev/null 2>&1 || true
  delete_owned_guard iptables "$interface" >/dev/null 2>&1 || true
  delete_owned_guard ip6tables "$interface" >/dev/null 2>&1 || true
}

verify_system_rules() {
  local sources_file="$1" interface="$2" policy="${3:-exact}" mode
  if [[ "$policy" == absent ]]; then
    verify_ufw_rules absent "$sources_file" && verify_guards absent "$sources_file" "$interface"
  else
    mode="$(firewall_mode "$sources_file")" || return 1
    verify_ufw_rules "$mode" "$sources_file" && verify_guards exact "$sources_file" "$interface"
  fi
}

transition_system_rules() {
  local old_file="$1" new_file="$2" interface="$3" old_policy="${4:-exact}"
  verify_system_rules "$old_file" "$interface" "$old_policy" || return 1
  if [[ "$old_policy" == exact ]] && ! remove_system_rules "$old_file" "$interface"; then
    cleanup_candidate_rules "$old_file" "$interface"
    add_system_rules "$old_file" "$interface" || return 2
    return 1
  fi
  if ! add_system_rules "$new_file" "$interface"; then
    cleanup_candidate_rules "$new_file" "$interface"
    if [[ "$old_policy" == exact ]]; then
      add_system_rules "$old_file" "$interface" || return 2
    fi
    return 1
  fi
  verify_system_rules "$new_file" "$interface"
}

mutate_sources() {
  local action="$1" cidr="$2" allow_non_public="$3" allow_broad="$4" interface="$5"
  local old_file new_file transition_status old_policy=exact
  local -a arguments
  old_file="$(mktemp /run/kitdev-sandboxes/ingress-sources-old.XXXXXXXX)"
  new_file="$(mktemp /run/kitdev-sandboxes/ingress-sources-new.XXXXXXXX)"
  trap "/usr/bin/unlink -- '$old_file' >/dev/null 2>&1 || true; /usr/bin/unlink -- '$new_file' >/dev/null 2>&1 || true" RETURN EXIT
  /usr/bin/python3 -I -B -S "$SOURCE_STATE" export >"$old_file"
  [[ -e "$SOURCE_MANIFEST" || -L "$SOURCE_MANIFEST" ]] || old_policy=absent
  if [[ "$action" == add ]]; then
    arguments=(candidate-add "$cidr")
    [[ "$allow_non_public" == yes ]] && arguments+=(--allow-non-public)
    [[ "$allow_broad" == yes ]] && arguments+=(--allow-broad-range)
  else
    arguments=(candidate-remove "$cidr")
  fi
  /usr/bin/python3 -I -B -S "$SOURCE_STATE" "${arguments[@]}" >"$new_file"
  if ! cmp --silent -- "$old_file" "$new_file"; then
    if transition_system_rules "$old_file" "$new_file" "$interface" "$old_policy"; then
      :
    else
      transition_status="$?"
      if [[ "$transition_status" == 2 ]]; then
        control_plane_die source_firewall_rollback_failed 70
      fi
      control_plane_die source_firewall_transaction_failed 70
    fi
    if ! /usr/bin/python3 -I -B -S "$SOURCE_STATE" install-file "$new_file"; then
      transition_system_rules "$new_file" "$old_file" "$interface" ||
        control_plane_die source_firewall_rollback_failed 70
      control_plane_die source_manifest_commit_failed 70
    fi
  else
    verify_system_rules "$old_file" "$interface" || control_plane_die source_firewall_mismatch 65
  fi
  /usr/bin/python3 -I -B -S "$SOURCE_STATE" list
}

mutate_mode() {
  local mode="$1" interface="$2" old_file new_file old_policy=exact transition_status
  old_file="$(mktemp /run/kitdev-sandboxes/ingress-mode-old.XXXXXXXX)"
  new_file="$(mktemp /run/kitdev-sandboxes/ingress-mode-new.XXXXXXXX)"
  trap "/usr/bin/unlink -- '$old_file' >/dev/null 2>&1 || true; /usr/bin/unlink -- '$new_file' >/dev/null 2>&1 || true" RETURN EXIT
  /usr/bin/python3 -I -B -S "$SOURCE_STATE" export >"$old_file"
  [[ -e "$SOURCE_MANIFEST" || -L "$SOURCE_MANIFEST" ]] || old_policy=absent
  /usr/bin/python3 -I -B -S "$SOURCE_STATE" candidate-mode "$mode" >"$new_file"
  if ! cmp --silent -- "$old_file" "$new_file" || [[ "$old_policy" == absent ]]; then
    if transition_system_rules "$old_file" "$new_file" "$interface" "$old_policy"; then
      :
    else
      transition_status="$?"
      [[ "$transition_status" != 2 ]] || control_plane_die source_firewall_rollback_failed 70
      control_plane_die source_firewall_transaction_failed 70
    fi
    if ! /usr/bin/python3 -I -B -S "$SOURCE_STATE" install-file "$new_file"; then
      transition_system_rules "$new_file" "$old_file" "$interface" exact ||
        control_plane_die source_firewall_rollback_failed 70
      control_plane_die source_manifest_commit_failed 70
    fi
  else
    verify_system_rules "$old_file" "$interface" || control_plane_die source_firewall_mismatch 65
  fi
  /usr/bin/python3 -I -B -S "$SOURCE_STATE" list
}

main() {
  local mode="${1:-}" cidr='' allow_non_public=no allow_broad=no interface state_file
  case "$mode" in apply|verify|remove|source-add|source-list|source-remove|mode) ;;
    *) control_plane_die invalid_operation 64 ;;
  esac
  shift || true
  if [[ "$mode" == mode ]]; then
    [[ $# == 1 && "$1" =~ ^(closed|public|restricted)$ ]] || control_plane_die firewall_mode_invalid 64
    cidr="$1"; shift
  elif [[ "$mode" == source-add || "$mode" == source-remove ]]; then
    while (( $# )); do
      case "$1" in
        --cidr) [[ $# -ge 2 && -z "$cidr" ]] || control_plane_die invalid_operation 64; cidr="$2"; shift 2 ;;
        --allow-non-public) [[ "$mode" == source-add && "$allow_non_public" == no ]] || control_plane_die invalid_operation 64; allow_non_public=yes; shift ;;
        --allow-broad-range) [[ "$mode" == source-add && "$allow_broad" == no ]] || control_plane_die invalid_operation 64; allow_broad=yes; shift ;;
        *) control_plane_die invalid_operation 64 ;;
      esac
    done
    [[ -n "$cidr" ]] || control_plane_die source_cidr_required 64
  else
    (( $# == 0 )) || control_plane_die invalid_operation 64
  fi
  require_root
  require_lifecycle_platform
  for command in cmp docker flock ip ip6tables iptables sshd ss ufw; do require_command "$command"; done
  ensure_directory /run/kitdev-sandboxes root root 700
  open_firewall_lock || control_plane_die ingress_firewall_lock_untrusted 65
  flock -x 9
  verify_ufw_defaults || control_plane_die ufw_default_policy_mismatch 65
  verify_ufw_ipv6 || control_plane_die ufw_ipv6_required 65
  verify_control_plane_firewall || control_plane_die control_plane_firewall_mismatch 65
  verify_listeners || control_plane_die public_internal_listener_detected 65
  verify_docker_publications || control_plane_die public_docker_ingress_detected 65
  interface="$(outbound_interface)" || control_plane_die outbound_interface_invalid 65
  if [[ "$mode" == mode ]]; then
    mutate_mode "$cidr" "$interface"
    return
  fi
  if [[ "$mode" == source-list ]]; then
    state_file="$(mktemp /run/kitdev-sandboxes/ingress-sources.XXXXXXXX)"
    trap "/usr/bin/unlink -- '$state_file' >/dev/null 2>&1 || true" EXIT
    /usr/bin/python3 -I -B -S "$SOURCE_STATE" export >"$state_file"
    if [[ -e "$SOURCE_MANIFEST" || -L "$SOURCE_MANIFEST" ]]; then
      verify_system_rules "$state_file" "$interface" || control_plane_die source_firewall_mismatch 65
    else
      verify_system_rules "$state_file" "$interface" absent || control_plane_die source_firewall_mismatch 65
    fi
    /usr/bin/python3 -I -B -S "$SOURCE_STATE" list
    return
  fi
  if [[ "$mode" == source-add ]]; then
    mutate_sources add "$cidr" "$allow_non_public" "$allow_broad" "$interface"
    return
  fi
  if [[ "$mode" == source-remove ]]; then
    mutate_sources remove "$cidr" no no "$interface"
    return
  fi
  state_file="$(mktemp /run/kitdev-sandboxes/ingress-sources.XXXXXXXX)"
  trap "/usr/bin/unlink -- '$state_file' >/dev/null 2>&1 || true" EXIT
  /usr/bin/python3 -I -B -S "$SOURCE_STATE" export >"$state_file"
  case "$mode" in
    apply)
      if [[ ! -e "$SOURCE_MANIFEST" && ! -L "$SOURCE_MANIFEST" ]]; then
        if ! add_system_rules "$state_file" "$interface"; then
          cleanup_candidate_rules "$state_file" "$interface"
          control_plane_die ingress_firewall_conflict 65
        fi
        /usr/bin/python3 -I -B -S "$SOURCE_STATE" install-file "$state_file" ||
          control_plane_die source_manifest_commit_failed 70
      elif verify_system_rules "$state_file" "$interface"; then
        :
      elif verify_system_rules "$state_file" "$interface" absent; then
        if ! add_system_rules "$state_file" "$interface"; then
          cleanup_candidate_rules "$state_file" "$interface"
          control_plane_die ingress_firewall_conflict 65
        fi
      else
        # Partially applied. A reboot produces exactly this: ufw persists its
        # rules to /etc/ufw while the DOCKER-USER guards are runtime iptables
        # state and vanish. Refusing here left the host unable to serve and
        # unable to repair itself, because remove refused the same state.
        #
        # Reconcile instead. cleanup_candidate_rules only deletes rules
        # carrying this project's own comment tags and its owned guards, so
        # foreign rules are never touched; requiring the absent state
        # afterwards keeps the refusal for anything this project does not own.
        cleanup_candidate_rules "$state_file" "$interface"
        verify_system_rules "$state_file" "$interface" absent ||
          control_plane_die ingress_firewall_conflict 65
        if ! add_system_rules "$state_file" "$interface"; then
          cleanup_candidate_rules "$state_file" "$interface"
          control_plane_die ingress_firewall_conflict 65
        fi
      fi
      verify_system_rules "$state_file" "$interface" || control_plane_die ingress_firewall_mismatch 65
      ;;
    verify)
      verify_system_rules "$state_file" "$interface" || control_plane_die ingress_firewall_mismatch 65
      ;;
    remove)
      if [[ -e "$SOURCE_MANIFEST" || -L "$SOURCE_MANIFEST" ]]; then
        # remove_system_rules verifies the complete rule set is present before
        # deleting anything, so a partially applied state was unremovable.
        # Fall back to clearing this project's own tagged remnants, then
        # require the end state to be absent so a foreign rule still refuses.
        if ! remove_system_rules "$state_file" "$interface"; then
          cleanup_candidate_rules "$state_file" "$interface"
        fi
      fi
      verify_system_rules "$state_file" "$interface" absent ||
        control_plane_die ingress_firewall_conflict 65
      ;;
  esac
  printf 'status=pass operation=%s-ingress-firewall\n' "$mode"
}

main "$@"
