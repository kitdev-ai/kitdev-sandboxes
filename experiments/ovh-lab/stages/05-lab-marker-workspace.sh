#!/usr/bin/env bash
set -Eeuo pipefail
readonly SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
# shellcheck source=../lib/common.sh
source "$SCRIPT_DIR/../lib/common.sh"
# OVH_LAB_STAGE_BODY

readonly STAGE05_JOURNAL_SHA256='__STAGE05_JOURNAL_SHA256__'
readonly STAGE05_JOURNAL_B64='__STAGE05_JOURNAL_B64__'
readonly STAGE05_RECONCILER_SHA256='__STAGE05_RECONCILER_SHA256__'
readonly STAGE05_RECONCILER_B64='__STAGE05_RECONCILER_B64__'

stage05_python() {
  local mode="$1"
  local acknowledgement="$2"
  local bundle_sha256="$3"
  printf 'journal %s %s\nreconciler %s %s\n' \
    "$STAGE05_JOURNAL_SHA256" "$STAGE05_JOURNAL_B64" \
    "$STAGE05_RECONCILER_SHA256" "$STAGE05_RECONCILER_B64" |
    /usr/bin/python3 -I -B -S -c '
import sys
if not ((3, 13) <= sys.version_info[:2] < (3, 15)):
    print("status=error reason=unsupported_python", file=sys.stderr)
    raise SystemExit(68)
try:
    import base64
    import hashlib
    import re
    import types
    raw = sys.stdin.buffer.read(2_000_001)
    if len(raw) > 2_000_000:
        raise ValueError
    lines = raw.splitlines()
    if len(lines) != 2:
        raise ValueError
    sources = {}
    for expected, line in zip((b"journal", b"reconciler"), lines, strict=True):
        parts = line.split(b" ")
        if len(parts) != 3 or parts[0] != expected or not re.fullmatch(b"[0-9a-f]{64}", parts[1]):
            raise ValueError
        source = base64.b64decode(parts[2], validate=True)
        if not source or len(source) > 1_000_000 or hashlib.sha256(source).hexdigest().encode() != parts[1]:
            raise ValueError
        sources[expected.decode()] = source
    package = types.ModuleType("kitdev_sandboxes")
    package.__path__ = []
    package.__package__ = "kitdev_sandboxes"
    sys.modules["kitdev_sandboxes"] = package
    journal = types.ModuleType("kitdev_sandboxes.journal")
    journal.__file__ = "<embedded-journal>"
    journal.__package__ = "kitdev_sandboxes"
    sys.modules[journal.__name__] = journal
    exec(compile(sources["journal"], journal.__file__, "exec"), journal.__dict__)
    reconciler = types.ModuleType("kitdev_sandboxes.stage05")
    reconciler.__file__ = "<embedded-stage05>"
    reconciler.__package__ = "kitdev_sandboxes"
    sys.modules[reconciler.__name__] = reconciler
    exec(compile(sources["reconciler"], reconciler.__file__, "exec"), reconciler.__dict__)
    entrypoint = reconciler.__dict__.get("main")
    if not callable(entrypoint):
        raise ValueError
except BaseException:
    print("status=error reason=embedded_module_invalid", file=sys.stderr)
    raise SystemExit(70)
raise SystemExit(entrypoint(list(sys.argv[1:])))
' "$mode" "$acknowledgement" "$bundle_sha256"
}

# Stage 05 replaces the legacy read-only shell probe with its descriptor-based collector.
lab_refuse_production() {
  stage05_python production-check "$2" "$3" >/dev/null
}

main() {
  local mode="${1:-}"
  lab_require_ack "$@"
  lab_require_supported_platform
  lab_refuse_production "$@"
  case "$mode" in
    before|execute|after|postconditions|rollback|rollback-postconditions)
      stage05_python "$mode" "$2" "$3"
      ;;
  esac
}

main "$@"
