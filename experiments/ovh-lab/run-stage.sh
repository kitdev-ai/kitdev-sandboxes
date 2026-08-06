#!/usr/bin/env bash
set -Eeuo pipefail

readonly SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
readonly MANIFEST="$SCRIPT_DIR/stages.json"
readonly COMMON="$SCRIPT_DIR/lib/common.sh"
readonly ACK_EXPECTED="DISPOSABLE_OVH_LAB"
readonly REMOTE_TIMEOUT_SECONDS=90
readonly EVIDENCE_MAX_BYTES=1048576
readonly STAGE05_JOURNAL_SOURCE="$SCRIPT_DIR/../../src/kitdev_sandboxes/journal.py"
readonly STAGE05_RECONCILER_SOURCE="$SCRIPT_DIR/../../src/kitdev_sandboxes/stage05.py"
readonly STAGE10_RUNNER_SOURCE="$SCRIPT_DIR/../../src/kitdev_sandboxes/runner.py"
readonly STAGE10_RESOLVER_SOURCE="$SCRIPT_DIR/../../src/kitdev_sandboxes/stage10.py"

usage() {
  printf 'usage: OVH_LAB_TARGET=<ssh-alias> OVH_LAB_SSH_CONFIG=<private-absolute-path> OVH_LAB_KNOWN_HOSTS=<guarded-absolute-path> %s <stage-id> approval|approval-rollback|execute|rollback\n' "$0" >&2
  exit 64
}

die() {
  printf 'ovh-lab: %s\n' "$1" >&2
  exit "${2:-1}"
}

[[ $# -ge 1 && $# -le 2 ]] || usage
readonly STAGE_ID="$1"
readonly OPERATION="${2:-execute}"
readonly ACK="${DISPOSABLE_OVH_LAB:-}"
readonly TARGET="${OVH_LAB_TARGET:-}"
readonly SSH_CONFIG="${OVH_LAB_SSH_CONFIG:-}"
readonly KNOWN_HOSTS="${OVH_LAB_KNOWN_HOSTS:-}"

[[ "$TARGET" =~ ^[A-Za-z0-9_-]{1,64}$ ]] || die 'OVH_LAB_TARGET must be a configured SSH alias, not an endpoint' 64
[[ "$OPERATION" == approval || "$OPERATION" == approval-rollback || "$OPERATION" == execute || "$OPERATION" == rollback ]] || usage

manifest_record="$(python3 - "$MANIFEST" "$STAGE_ID" <<'PY'
import json, re, sys
with open(sys.argv[1], "r", encoding="utf-8") as handle:
    document = json.load(handle)
matches = [item for item in document.get("stages", []) if item.get("id") == sys.argv[2]]
if len(matches) != 1:
    raise SystemExit(64)
item = matches[0]
script = item.get("script")
if not isinstance(script, str) or not re.fullmatch(r"stages/[0-9]{2}-[a-z0-9-]+\.sh", script):
    raise SystemExit(64)
if not script.startswith("stages/" + sys.argv[2] + "-"):
    raise SystemExit(64)
if item.get("status") not in {"executable", "blocked"}:
    raise SystemExit(64)
if item.get("kind") not in {"read-only", "plan-only", "mutation"}:
    raise SystemExit(64)
print("\t".join((script, item.get("status", "invalid"), item.get("kind", "invalid"))))
PY
)" || die 'invalid stage manifest' 64
IFS=$'\t' read -r STAGE_RELATIVE STAGE_STATUS STAGE_KIND <<<"$manifest_record"
readonly STAGE_RELATIVE STAGE_STATUS STAGE_KIND
STAGE_SCRIPT="$SCRIPT_DIR/$STAGE_RELATIVE"
[[ -f "$STAGE_SCRIPT" && ! -L "$STAGE_SCRIPT" ]] || die 'manifest stage is not a regular file' 64
readonly STAGE_SCRIPT

bundle_stage() {
  cat -- "$COMMON"
  if [[ "$STAGE_ID" == 05 || "$STAGE_ID" == 10 ]]; then
    python3 - "$STAGE_ID" "$STAGE_SCRIPT" "$STAGE05_JOURNAL_SOURCE" \
      "$STAGE05_RECONCILER_SOURCE" "$STAGE10_RUNNER_SOURCE" \
      "$STAGE10_RESOLVER_SOURCE" <<'PY_EMBEDDED_BUNDLE'
import base64
import hashlib
import os
import stat
import sys

def stable_read(path, limit):
    before = os.lstat(path)
    if not stat.S_ISREG(before.st_mode) or stat.S_ISLNK(before.st_mode) or not hasattr(os, "O_NOFOLLOW"):
        raise SystemExit(64)
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0))
    try:
        opened = os.fstat(descriptor)
        if (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino):
            raise SystemExit(64)
        content = bytearray()
        while True:
            chunk = os.read(descriptor, min(65_536, limit + 1 - len(content)))
            if not chunk:
                break
            content.extend(chunk)
            if len(content) > limit:
                raise SystemExit(64)
        after = os.fstat(descriptor)
        published = os.stat(path, follow_symlinks=False)
        fields = (
            "st_mode", "st_uid", "st_gid", "st_nlink", "st_size", "st_dev", "st_ino",
            "st_mtime_ns", "st_ctime_ns",
        )
        if any(
            getattr(opened, field) != getattr(after, field)
            or getattr(after, field) != getattr(published, field)
            for field in fields
        ):
            raise SystemExit(64)
        return bytes(content)
    finally:
        os.close(descriptor)

stage_id = sys.argv[1]
stage = stable_read(sys.argv[2], 262_144).decode("utf-8", errors="strict")
body_marker = "# OVH_LAB_STAGE_BODY\n"
if stage.count(body_marker) != 1:
    raise SystemExit(64)
body = stage.split(body_marker, 1)[1]
journal = stable_read(sys.argv[3], 1_000_000)
stage05 = stable_read(sys.argv[4], 1_000_000)
if stage_id == "05":
    replacements = {
        "__STAGE05_JOURNAL_SHA256__": hashlib.sha256(journal).hexdigest(),
        "__STAGE05_JOURNAL_B64__": base64.b64encode(journal).decode("ascii"),
        "__STAGE05_RECONCILER_SHA256__": hashlib.sha256(stage05).hexdigest(),
        "__STAGE05_RECONCILER_B64__": base64.b64encode(stage05).decode("ascii"),
    }
elif stage_id == "10":
    runner = stable_read(sys.argv[5], 1_000_000)
    resolver = stable_read(sys.argv[6], 1_000_000)
    replacements = {
        "__STAGE10_RUNNER_SHA256__": hashlib.sha256(runner).hexdigest(),
        "__STAGE10_RUNNER_B64__": base64.b64encode(runner).decode("ascii"),
        "__STAGE10_JOURNAL_SHA256__": hashlib.sha256(journal).hexdigest(),
        "__STAGE10_JOURNAL_B64__": base64.b64encode(journal).decode("ascii"),
        "__STAGE10_STAGE05_SHA256__": hashlib.sha256(stage05).hexdigest(),
        "__STAGE10_STAGE05_B64__": base64.b64encode(stage05).decode("ascii"),
        "__STAGE10_RESOLVER_SHA256__": hashlib.sha256(resolver).hexdigest(),
        "__STAGE10_RESOLVER_B64__": base64.b64encode(resolver).decode("ascii"),
    }
else:
    raise SystemExit(64)
for marker, value in replacements.items():
    if body.count(marker) != 1:
        raise SystemExit(64)
    body = body.replace(marker, value)
sys.stdout.write(body)
PY_EMBEDDED_BUNDLE
  else
    awk 'body { print } /^# OVH_LAB_STAGE_BODY$/ { body=1 }' "$STAGE_SCRIPT"
  fi
}

[[ "$STAGE_STATUS" == executable ]] || die 'stage is blocked by the manifest' 20
[[ "$STAGE_KIND" == read-only || "$STAGE_KIND" == plan-only || ( "$STAGE_ID" == 05 && "$STAGE_KIND" == mutation ) ]] ||
  die 'mutable stages are disabled in this revision' 20

validate_guarded_input() {
  python3 - "$1" "$2" "$3" <<'PY'
import hashlib
import os
import re
import stat
import sys

path = sys.argv[1]
snapshot_path = sys.argv[2]
input_kind = sys.argv[3]
if input_kind not in {"ssh_config", "known_hosts"}:
    raise SystemExit(64)
encoded_path = os.fsencode(path)
if (
    not path
    or not os.path.isabs(path)
    or len(encoded_path) > 4096
    or any(byte < 32 or byte == 127 for byte in encoded_path)
):
    raise SystemExit(64)
try:
    before = os.lstat(path)
except OSError:
    raise SystemExit(64)
if not stat.S_ISREG(before.st_mode) or stat.S_ISLNK(before.st_mode):
    raise SystemExit(64)

def mode_is_unsafe(metadata):
    mode = stat.S_IMODE(metadata.st_mode)
    if metadata.st_uid != os.geteuid() or not mode & stat.S_IRUSR:
        return True
    if input_kind == "ssh_config":
        return bool(mode & 0o077)
    return bool(mode & 0o7133)

if mode_is_unsafe(before):
    raise SystemExit(64)
if not hasattr(os, "O_NOFOLLOW"):
    raise SystemExit(64)
flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | os.O_NOFOLLOW
try:
    descriptor = os.open(path, flags)
except OSError:
    raise SystemExit(64)
try:
    opened_before = os.fstat(descriptor)
    if (opened_before.st_dev, opened_before.st_ino) != (before.st_dev, before.st_ino):
        raise SystemExit(64)
    if not stat.S_ISREG(opened_before.st_mode):
        raise SystemExit(64)
    if mode_is_unsafe(opened_before):
        raise SystemExit(64)
    content = bytearray()
    while True:
        chunk = os.read(descriptor, min(65536, 1048577 - len(content)))
        if not chunk:
            break
        content.extend(chunk)
        if len(content) > 1048576:
            raise SystemExit(64)
    opened_after = os.fstat(descriptor)
    stable_fields = ("st_dev", "st_ino", "st_size", "st_mode", "st_uid", "st_mtime_ns", "st_ctime_ns")
    if any(getattr(opened_before, field) != getattr(opened_after, field) for field in stable_fields):
        raise SystemExit(64)
finally:
    os.close(descriptor)

if input_kind == "ssh_config":
    for line in bytes(content).splitlines():
        stripped = line.lstrip(b" \t")
        if not stripped or stripped.startswith(b"#"):
            continue
        if re.match(br"(?i:include)(?:[ \t]|=|$)", stripped):
            raise SystemExit(64)

snapshot_identity = ("-", "-")
if snapshot_path:
    snapshot_flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
    try:
        snapshot = os.open(snapshot_path, snapshot_flags, 0o600)
    except OSError:
        raise SystemExit(64)
    try:
        view = memoryview(content)
        written = 0
        while written < len(view):
            count = os.write(snapshot, view[written:])
            if count <= 0:
                raise SystemExit(64)
            written += count
        os.fsync(snapshot)
        snapshot_stat = os.fstat(snapshot)
        if snapshot_stat.st_uid != os.geteuid() or stat.S_IMODE(snapshot_stat.st_mode) != 0o600:
            raise SystemExit(64)
        snapshot_identity = (str(snapshot_stat.st_dev), str(snapshot_stat.st_ino))
    finally:
        os.close(snapshot)

print("\t".join((
    hashlib.sha256(content).hexdigest(),
    str(opened_after.st_dev),
    str(opened_after.st_ino),
    *snapshot_identity,
)))
PY
}

load_guarded_inputs() {
  local config_record known_hosts_record
  config_record="$(validate_guarded_input "$SSH_CONFIG" "$1" ssh_config)" ||
    die 'OVH_LAB_SSH_CONFIG must be a stable, bounded, private single-file config' 64
  known_hosts_record="$(validate_guarded_input "$KNOWN_HOSTS" "$2" known_hosts)" ||
    die 'OVH_LAB_KNOWN_HOSTS must be stable, bounded, current-user-owned, and not writable by group or other' 64
  IFS=$'\t' read -r SSH_CONFIG_SHA256 SSH_CONFIG_DEV SSH_CONFIG_INO SSH_CONFIG_SNAPSHOT_DEV SSH_CONFIG_SNAPSHOT_INO <<<"$config_record"
  IFS=$'\t' read -r KNOWN_HOSTS_SHA256 KNOWN_HOSTS_DEV KNOWN_HOSTS_INO KNOWN_HOSTS_SNAPSHOT_DEV KNOWN_HOSTS_SNAPSHOT_INO <<<"$known_hosts_record"
  [[ "$SSH_CONFIG_DEV:$SSH_CONFIG_INO" != "$KNOWN_HOSTS_DEV:$KNOWN_HOSTS_INO" ]] ||
    die 'SSH config and known_hosts must be distinct files' 64
  if [[ -n "$1" && -n "$2" ]]; then
    [[ "$SSH_CONFIG_SNAPSHOT_DEV:$SSH_CONFIG_SNAPSHOT_INO" != "$KNOWN_HOSTS_SNAPSHOT_DEV:$KNOWN_HOSTS_SNAPSHOT_INO" ]] ||
      die 'guarded input snapshots must be distinct files' 64
  fi
}

BUNDLE_CONTENT="$(bundle_stage)"
readonly BUNDLE_CONTENT
BUNDLE_SHA256="$(printf '%s\n' "$BUNDLE_CONTENT" | python3 -c 'import hashlib, sys; print(hashlib.sha256(sys.stdin.buffer.read()).hexdigest())')"
readonly BUNDLE_SHA256
APPROVAL_OPERATION="$OPERATION"
[[ "$OPERATION" == approval ]] && APPROVAL_OPERATION=execute
[[ "$OPERATION" == approval-rollback ]] && APPROVAL_OPERATION=rollback
readonly APPROVAL_OPERATION
if [[ "$OPERATION" == approval || "$OPERATION" == approval-rollback ]]; then
  load_guarded_inputs '' ''
  readonly SSH_CONFIG_SHA256 KNOWN_HOSTS_SHA256
  readonly APPROVAL_EXPECTED="$ACK_EXPECTED:$STAGE_ID:$APPROVAL_OPERATION:$TARGET:$BUNDLE_SHA256:$SSH_CONFIG_SHA256:$KNOWN_HOSTS_SHA256"
  printf '%s\n' "$APPROVAL_EXPECTED"
  exit 0
fi

umask 077
readonly ARTIFACTS_ROOT="$SCRIPT_DIR/../../artifacts"
readonly RUN_ROOT="$ARTIFACTS_ROOT/ovh-lab"
[[ ! -L "$ARTIFACTS_ROOT" && ! -L "$RUN_ROOT" ]] || die 'evidence directory must not be a symlink' 73
mkdir -p -- "$RUN_ROOT"
readonly RUN_DIR="$(mktemp -d "$RUN_ROOT/run-${STAGE_ID}-XXXXXXXX")"
readonly LOG_FILE="$RUN_DIR/evidence.log"
readonly SUMMARY_FILE="$RUN_DIR/summary.txt"
readonly STAGE10_RESOLUTION_FILE="$RUN_DIR/stage10-resolution.json"
readonly SSH_CONFIG_SNAPSHOT="$RUN_DIR/private-ssh-config"
readonly KNOWN_HOSTS_SNAPSHOT="$RUN_DIR/private-known-hosts"

cleanup_private_snapshots() {
  rm -f -- "$SSH_CONFIG_SNAPSHOT" "$KNOWN_HOSTS_SNAPSHOT"
}
trap cleanup_private_snapshots EXIT
load_guarded_inputs "$SSH_CONFIG_SNAPSHOT" "$KNOWN_HOSTS_SNAPSHOT"
readonly SSH_CONFIG_SHA256 KNOWN_HOSTS_SHA256
readonly APPROVAL_EXPECTED="$ACK_EXPECTED:$STAGE_ID:$APPROVAL_OPERATION:$TARGET:$BUNDLE_SHA256:$SSH_CONFIG_SHA256:$KNOWN_HOSTS_SHA256"
[[ "$ACK" == "$APPROVAL_EXPECTED" ]] || die 'stage/operation/target/config/host-key/bundle-bound disposable approval required' 64

redact() {
  sed -E \
    -e 's/([A-Za-z0-9._%+-]+)@([A-Za-z0-9.-]+)/[redacted-account]@[redacted-host]/g' \
    -e 's/([0-9]{1,3}\.){3}[0-9]{1,3}/[redacted-ipv4]/g' \
    -e 's/([[:xdigit:]]{0,4}:){2,}[[:xdigit:]:]{0,4}/[redacted-ipv6]/g' \
    -e 's/(connect to host|connection to|resolve host(name)?|host key for|hostname)[[:space:]]+[A-Za-z0-9._-]+/\1 [redacted-host]/Ig' \
    -e 's#((in|file)[[:space:]]+)/[^[:space:]:]+#\1[redacted-path]#Ig' \
    -e 's#(/[A-Za-z0-9._-]+)+(:[0-9]+)?#[redacted-path]#g' \
    -e 's/((token|secret|password|authorization|private[_-]?key)[[:space:]]*[=:][[:space:]]*)[^[:space:]]+/\1[redacted]/Ig' \
    -e 's/(ssh-(ed25519|rsa|ecdsa)[[:space:]]+)[A-Za-z0-9+\/=]+/\1[redacted-key]/g' \
    -e 's/SHA256:[A-Za-z0-9+\/=]+/SHA256:[redacted-fingerprint]/g'
}

run_remote() {
  local mode="$1"
  local rc
  local -a statuses
  printf '%s\n' "$BUNDLE_CONTENT" | python3 -c \
    'import os, signal, sys; signal.alarm(int(sys.argv[1])); os.execvp(sys.argv[2], sys.argv[2:])' \
    "$REMOTE_TIMEOUT_SECONDS" ssh \
    -F "$SSH_CONFIG_SNAPSHOT" \
    -o BatchMode=yes \
    -o ConnectTimeout=10 \
    -o ConnectionAttempts=1 \
    -o ControlMaster=no \
    -o GlobalKnownHostsFile=/dev/null \
    -o KnownHostsCommand=none \
    -o ServerAliveCountMax=3 \
    -o ServerAliveInterval=5 \
    -o StrictHostKeyChecking=yes \
    -o UpdateHostKeys=no \
    -o "UserKnownHostsFile=$KNOWN_HOSTS_SNAPSHOT" \
    -o VerifyHostKeyDNS=no \
    -- "$TARGET" /usr/bin/sudo -n /usr/bin/timeout --signal=TERM --kill-after=5s \
    "80s" /bin/bash -s -- "$mode" "$ACK_EXPECTED" "$BUNDLE_SHA256" \
    2>&1 | head -c "$EVIDENCE_MAX_BYTES" | redact | head -c "$EVIDENCE_MAX_BYTES" | tee -a "$LOG_FILE"
  statuses=("${PIPESTATUS[@]}")
  if (( statuses[1] != 0 )); then
    rc="${statuses[1]}"
  elif (( statuses[0] != 0 || statuses[2] != 0 || statuses[3] != 0 || statuses[4] != 0 || statuses[5] != 0 )); then
    rc=74
  else
    rc=0
  fi
  return "$rc"
}

printf 'schema=1\nstage=%s\noperation=%s\nmanifest_status=%s\nkind=%s\nbundle_sha256=%s\nssh_config_sha256=%s\nknown_hosts_sha256=%s\n' \
  "$STAGE_ID" "$OPERATION" "$STAGE_STATUS" "$STAGE_KIND" "$BUNDLE_SHA256" "$SSH_CONFIG_SHA256" "$KNOWN_HOSTS_SHA256" >"$SUMMARY_FILE"
printf 'event=run_start stage=%s operation=%s\n' "$STAGE_ID" "$OPERATION" | tee -a "$LOG_FILE"

run_remote before || die "before snapshot failed; evidence: $RUN_DIR" 70

operation_rc=0
run_remote "$OPERATION" || operation_rc=$?

after_rc=0
run_remote after || after_rc=$?
post_rc=0
if [[ "$OPERATION" == rollback ]]; then
  run_remote rollback-postconditions || post_rc=$?
else
  run_remote postconditions || post_rc=$?
fi

printf 'operation_rc=%s\nafter_rc=%s\npostconditions_rc=%s\n' \
  "$operation_rc" "$after_rc" "$post_rc" >>"$SUMMARY_FILE"
if [[ "$STAGE_ID" == 05 && "$operation_rc" == 0 && "$after_rc" == 0 && "$post_rc" == 0 ]]; then
  STAGE05_PLAN_SHA256="$(python3 - "$LOG_FILE" <<'PY_STAGE05_EVIDENCE'
import re
import sys

with open(sys.argv[1], "rb") as handle:
    content = handle.read(4_194_305)
if len(content) > 4_194_304:
    raise SystemExit(1)
matches = re.findall(br"(?:^| )plan_sha256=(sha256:[0-9a-f]{64})(?: |$)", content, re.MULTILINE)
if not matches or len(set(matches)) != 1:
    raise SystemExit(1)
print(matches[0].decode("ascii"))
PY_STAGE05_EVIDENCE
)" || die "Stage 05 plan evidence invalid; redacted evidence: $RUN_DIR" 74
  readonly STAGE05_PLAN_SHA256
  printf 'stage05_plan_sha256=%s\n' "$STAGE05_PLAN_SHA256" >>"$SUMMARY_FILE"
fi
if [[ "$STAGE_ID" == 10 && "$operation_rc" == 0 && "$after_rc" == 0 && "$post_rc" == 0 ]]; then
  STAGE10_RESOLUTION_SHA256="$(python3 - "$LOG_FILE" "$STAGE10_RESOLUTION_FILE" \
    "$OPERATION" "$BUNDLE_SHA256" <<'PY_STAGE10_EVIDENCE'
import base64
import hashlib
import json
import os
import re
import sys

HASH_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
PACKAGE_RE = re.compile(r"^[a-z0-9][a-z0-9+.-]{0,127}(?::[a-z0-9][a-z0-9-]{0,31})?$")
VERSION_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9.+:~_-]{0,255}$")
ARCH_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,31}$")
STATUS_RE = re.compile(r"^[a-z][a-z-]{0,31}$")
EXPECTED_KEYS = {
    "actions", "apply_authorized", "architecture", "apt_extended_states", "automatic",
    "candidate_scope", "conflicts", "docker_key", "docker_source", "dpkg_status",
    "foreign_docker_sources", "holds", "inventory_clean", "legacy_docker_keys", "manual",
    "operation", "os_release_sha256", "packages", "resolver_bundle_sha256",
    "schema_version", "stage", "stage05_bundle_sha256", "trust_packages",
    "ubuntu_archive_keyring",
}
PACKAGE_KEYS = {
    "candidate_version", "error", "installed_architecture", "installed_version", "name",
    "selection", "status",
}

def exact_dict(value, keys):
    return value.__class__ is dict and set(value) == keys

def safe_string(value, pattern):
    return value.__class__ is str and pattern.fullmatch(value) is not None

def hash_or_absent(value):
    return value == "absent" or safe_string(value, HASH_RE)

def package_records(value, expected_names, *, candidates):
    if value.__class__ is not list or len(value) != len(expected_names):
        return False
    if [item.get("name") for item in value if item.__class__ is dict] != expected_names:
        return False
    for item in value:
        if not exact_dict(item, PACKAGE_KEYS):
            return False
        if not safe_string(item["name"], PACKAGE_RE) or not safe_string(item["status"], STATUS_RE):
            return False
        if not safe_string(item["selection"], STATUS_RE) or item["error"] != "ok":
            return False
        version = item["installed_version"]
        architecture = item["installed_architecture"]
        if (version is None) != (architecture is None):
            return False
        if version is not None and not safe_string(version, VERSION_RE):
            return False
        if architecture is not None and not safe_string(architecture, ARCH_RE):
            return False
        if item["status"] == "absent" and version is not None:
            return False
        candidate = item["candidate_version"]
        if candidates:
            if candidate is None and item["status"] == "installed":
                pass
            elif not safe_string(candidate, VERSION_RE):
                return False
        elif candidate is not None:
            return False
    return True

def token_list(value, pattern, *, maximum=8192):
    return (
        value.__class__ is list
        and len(value) <= maximum
        and len(value) == len(set(value))
        and all(safe_string(item, pattern) for item in value)
    )

def valid_document(document, operation, bundle_digest):
    if not exact_dict(document, EXPECTED_KEYS):
        return False
    if (
        document["schema_version"].__class__ is not int
        or document["schema_version"] != 1
        or document["stage"] != "10-resolution"
        or document["operation"] != operation
        or document["apply_authorized"] is not False
        or document["architecture"] != "amd64"
        or document["candidate_scope"] != "host-cache-untrusted-for-apply"
        or document["resolver_bundle_sha256"] != "sha256:" + bundle_digest
        or not safe_string(document["stage05_bundle_sha256"], HASH_RE)
        or not safe_string(document["os_release_sha256"], HASH_RE)
        or not hash_or_absent(document["apt_extended_states"])
        or not hash_or_absent(document["dpkg_status"])
        or not safe_string(document["ubuntu_archive_keyring"], HASH_RE)
        or document["inventory_clean"].__class__ is not bool
    ):
        return False
    if not package_records(document["packages"], ["ca-certificates", "curl"], candidates=True):
        return False
    if not package_records(
        document["conflicts"],
        [
            "containerd", "docker-buildx", "docker-compose", "docker-compose-v2",
            "docker-doc", "docker.io", "podman-docker", "runc",
        ],
        candidates=False,
    ):
        return False
    if not package_records(document["trust_packages"], ["ubuntu-keyring"], candidates=False):
        return False
    if not all(
        token_list(document[key], PACKAGE_RE)
        for key in ("automatic", "holds", "manual")
    ):
        return False
    if set(document["automatic"]).intersection(document["manual"]):
        return False
    actions = document["actions"]
    if actions.__class__ is not list or len(actions) > 256:
        return False
    action_pairs = []
    for action in actions:
        if not exact_dict(action, {"name", "version"}):
            return False
        if not safe_string(action["name"], PACKAGE_RE) or not safe_string(
            action["version"], VERSION_RE
        ):
            return False
        action_pairs.append((action["name"], action["version"]))
    if action_pairs != sorted(action_pairs) or len({name for name, _ in action_pairs}) != len(actions):
        return False
    if not token_list(
        document["foreign_docker_sources"],
        re.compile(r"^(?:sources-list|source-part-[1-9][0-9]*)$"),
        maximum=4096,
    ):
        return False
    if not token_list(
        document["legacy_docker_keys"], re.compile(r"^legacy-key-[1-3]$"), maximum=3
    ):
        return False
    key = document["docker_key"]
    source = document["docker_source"]
    return (
        exact_dict(key, {"captured_sha256", "primary_fingerprint", "signing_fingerprint", "state"})
        and key["captured_sha256"]
        == "sha256:1500c1f56fa9e26b9b8f42452a553675796ade0807cdce11975eb98170b3a570"
        and key["primary_fingerprint"] == "9DC858229FC7DD38854AE2D88D81803C0EBFCD88"
        and key["signing_fingerprint"] == "D3306A018370199E527AE7997EA0A9C3F273FCD8"
        and key["state"] in {"absent", "captured-pin", "conflict"}
        and exact_dict(source, {"sha256", "state"})
        and source["sha256"]
        == "sha256:47be0f749c19273936c7e56fff5a29b9108bcce8137ee677cc736523fb876e71"
        and source["state"] in {"absent", "exact", "conflict"}
    )

with open(sys.argv[1], "rb") as handle:
    content = handle.read(4_194_305)
if len(content) > 4_194_304:
    raise SystemExit(1)
requested_operation = sys.argv[3]
bundle_digest = sys.argv[4]
if requested_operation not in {"execute", "rollback"} or not re.fullmatch(r"[0-9a-f]{64}", bundle_digest):
    raise SystemExit(1)
expected_operations = (
    ["before", "execute", "after", "postconditions"]
    if requested_operation == "execute"
    else ["before", "rollback", "after", "rollback-postconditions"]
)
matches = re.findall(
    br"^stage=10 operation=([a-z-]+) [^\r\n]* "
    br"resolution_sha256=(sha256:[0-9a-f]{64}) resolution_b64url=([A-Za-z0-9_=-]+)$",
    content,
    re.MULTILINE,
)
if len(matches) != 4 or [item[0].decode("ascii") for item in matches] != expected_operations:
    raise SystemExit(1)
observed = []
for encoded_operation, claimed, encoded in matches:
    operation = encoded_operation.decode("ascii")
    try:
        resolution = base64.b64decode(encoded, altchars=b"-_", validate=True)
        document = json.loads(resolution.decode("ascii", errors="strict"))
    except (ValueError, UnicodeError):
        raise SystemExit(1)
    canonical = (
        json.dumps(document, sort_keys=True, separators=(",", ":"), ensure_ascii=True) + "\n"
    ).encode("ascii")
    actual = b"sha256:" + hashlib.sha256(resolution).hexdigest().encode("ascii")
    if (
        not resolution
        or len(resolution) > 65_536
        or resolution != canonical
        or claimed != actual
        or not valid_document(document, operation, bundle_digest)
    ):
        raise SystemExit(1)
    comparison = dict(document)
    comparison.pop("operation")
    observed.append((operation, comparison, resolution, actual))
if any(item[1] != observed[0][1] for item in observed[1:]):
    raise SystemExit(1)
selected = next(item for item in observed if item[0] == requested_operation)
resolution = selected[2]
actual = selected[3]
flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_CLOEXEC", 0)
descriptor = os.open(sys.argv[2], flags, 0o600)
try:
    view = memoryview(resolution)
    while view:
        written = os.write(descriptor, view)
        if written <= 0:
            raise SystemExit(1)
        view = view[written:]
    os.fsync(descriptor)
finally:
    os.close(descriptor)
print(actual.decode("ascii"))
PY_STAGE10_EVIDENCE
)" || die "Stage 10 resolution evidence invalid; redacted evidence: $RUN_DIR" 74
  readonly STAGE10_RESOLUTION_SHA256
  printf 'stage10_resolution_sha256=%s\n' "$STAGE10_RESOLUTION_SHA256" >>"$SUMMARY_FILE"
fi
printf 'event=run_end operation_rc=%s after_rc=%s postconditions_rc=%s\n' \
  "$operation_rc" "$after_rc" "$post_rc" | tee -a "$LOG_FILE"

if (( operation_rc != 0 || after_rc != 0 || post_rc != 0 )); then
  result_rc="$operation_rc"
  (( result_rc != 0 )) || result_rc="$after_rc"
  (( result_rc != 0 )) || result_rc="$post_rc"
  die "stage did not pass; redacted evidence: $RUN_DIR" "$result_rc"
fi
printf 'ovh-lab: stage passed; redacted evidence: %s\n' "$RUN_DIR"
