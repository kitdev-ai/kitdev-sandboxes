#!/usr/bin/env bash
set -Eeuo pipefail
readonly SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
# shellcheck source=../control-plane/common.sh
source "$SCRIPT_DIR/../control-plane/common.sh"

verify_rules() {
  local policy="$1" rules
  rules="$(ufw show added)" || control_plane_die ufw_rules_unreadable 65
  /usr/bin/python3 -I -B -S - "$policy" 3<<<"$rules" <<'PY_VERIFY_INGRESS_UFW'
import os
import re
import shlex
import sys
from collections import Counter

policy = sys.argv[1]
expected = Counter({
    ("ufw", "allow", "80/tcp", "comment", "kitdev public ingress http"): 1,
    ("ufw", "allow", "443/tcp", "comment", "kitdev public ingress https"): 1,
})
observed = Counter()
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
    value = tuple(tokens)
    for first, last in ranges(unannotated):
        if first <= 80 <= last or first <= 443 <= last:
            observed[value] += 1
        if any(first <= port <= last for port in sensitive):
            source = unannotated[unannotated.index("from") + 1] if "from" in unannotated else "any"
            if source == "any":
                raise SystemExit(1)
if policy == "exact":
    valid = observed == expected
elif policy == "absent":
    valid = not observed
else:
    valid = False
raise SystemExit(0 if valid else 1)
PY_VERIFY_INGRESS_UFW
}

verify_listeners() {
  /usr/bin/python3 -I -B -S - <<'PY_VERIFY_INGRESS_LISTENERS'
import subprocess

sensitive = {3000, 3002, 3003, 3100, 5007, 5008, 5432, 6379, 8123, 9000}
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

main() {
  local mode="${1:-}"
  case "$mode" in apply|verify|remove) ;;
    *) control_plane_die invalid_operation 64 ;;
  esac
  require_root
  require_lifecycle_platform
  require_command ss
  require_command ufw
  [[ "$(ufw status | awk 'NR == 1 {print; exit}')" == 'Status: active' ]] ||
    control_plane_die ufw_not_active 65
  if [[ "$mode" != remove ]]; then
    verify_listeners || control_plane_die public_internal_listener_detected 65
  fi
  case "$mode" in
    apply)
      if verify_rules exact; then
        :
      elif verify_rules absent; then
        trap "ufw --force delete allow 80/tcp comment 'kitdev public ingress http' >/dev/null 2>&1 || true; ufw --force delete allow 443/tcp comment 'kitdev public ingress https' >/dev/null 2>&1 || true" ERR
        ufw allow 80/tcp comment 'kitdev public ingress http'
        ufw allow 443/tcp comment 'kitdev public ingress https'
        trap - ERR
      else
        control_plane_die ingress_ufw_rule_conflict 65
      fi
      verify_rules exact || control_plane_die ingress_ufw_rule_mismatch 65
      ;;
    verify)
      verify_rules exact || control_plane_die ingress_ufw_rule_mismatch 65
      ;;
    remove)
      if verify_rules absent; then
        :
      elif verify_rules exact; then
        ufw --force delete allow 80/tcp comment 'kitdev public ingress http'
        ufw --force delete allow 443/tcp comment 'kitdev public ingress https'
      else
        control_plane_die ingress_ufw_rule_conflict 65
      fi
      verify_rules absent || control_plane_die ingress_ufw_rule_cleanup_failed 65
      ;;
  esac
  printf 'status=pass operation=%s-ingress-firewall\n' "$mode"
}

main "$@"
