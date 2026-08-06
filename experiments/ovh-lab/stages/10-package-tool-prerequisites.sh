#!/usr/bin/env bash
set -Eeuo pipefail
readonly SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
# shellcheck source=../lib/common.sh
source "$SCRIPT_DIR/../lib/common.sh"
# OVH_LAB_STAGE_BODY

readonly STAGE10_RUNNER_SHA256='__STAGE10_RUNNER_SHA256__'
readonly STAGE10_RUNNER_B64='__STAGE10_RUNNER_B64__'
readonly STAGE10_JOURNAL_SHA256='__STAGE10_JOURNAL_SHA256__'
readonly STAGE10_JOURNAL_B64='__STAGE10_JOURNAL_B64__'
readonly STAGE10_STAGE05_SHA256='__STAGE10_STAGE05_SHA256__'
readonly STAGE10_STAGE05_B64='__STAGE10_STAGE05_B64__'
readonly STAGE10_RESOLVER_SHA256='__STAGE10_RESOLVER_SHA256__'
readonly STAGE10_RESOLVER_B64='__STAGE10_RESOLVER_B64__'

stage10_python() {
  local mode="$1"
  local acknowledgement="$2"
  local bundle_sha256="$3"
  printf 'runner %s %s\njournal %s %s\nstage05 %s %s\nresolver %s %s\n' \
    "$STAGE10_RUNNER_SHA256" "$STAGE10_RUNNER_B64" \
    "$STAGE10_JOURNAL_SHA256" "$STAGE10_JOURNAL_B64" \
    "$STAGE10_STAGE05_SHA256" "$STAGE10_STAGE05_B64" \
    "$STAGE10_RESOLVER_SHA256" "$STAGE10_RESOLVER_B64" |
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
    raw = sys.stdin.buffer.read(4_000_001)
    if len(raw) > 4_000_000:
        raise ValueError
    lines = raw.splitlines()
    names = (b"runner", b"journal", b"stage05", b"resolver")
    if len(lines) != len(names):
        raise ValueError
    sources = {}
    for expected, line in zip(names, lines, strict=True):
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
    for source_name, module_name in (
        ("runner", "kitdev_sandboxes.runner"),
        ("journal", "kitdev_sandboxes.journal"),
        ("stage05", "kitdev_sandboxes.stage05"),
        ("resolver", "kitdev_sandboxes.stage10"),
    ):
        module = types.ModuleType(module_name)
        module.__file__ = "<embedded-" + source_name + ">"
        module.__package__ = "kitdev_sandboxes"
        sys.modules[module_name] = module
        exec(compile(sources[source_name], module.__file__, "exec"), module.__dict__)
    entrypoint = sys.modules["kitdev_sandboxes.stage10"].__dict__.get("main")
    if not callable(entrypoint):
        raise ValueError
except BaseException:
    print("status=error reason=embedded_module_invalid", file=sys.stderr)
    raise SystemExit(70)
raise SystemExit(entrypoint(list(sys.argv[1:])))
' "$mode" "$acknowledgement" "$bundle_sha256"
}

main() {
  local mode="${1:-}"
  lab_require_ack "$@"
  lab_refuse_production
  lab_require_supported_platform
  case "$mode" in
    before|execute|after|postconditions|rollback|rollback-postconditions)
      stage10_python "$mode" "$2" "$3"
      ;;
  esac
}

main "$@"
