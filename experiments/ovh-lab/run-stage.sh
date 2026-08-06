#!/usr/bin/env bash
set -Eeuo pipefail

readonly SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
readonly MANIFEST="$SCRIPT_DIR/stages.json"
readonly COMMON="$SCRIPT_DIR/lib/common.sh"
readonly ACK_EXPECTED="DISPOSABLE_OVH_LAB"
readonly REMOTE_TIMEOUT_SECONDS=90
readonly EVIDENCE_MAX_BYTES=1048576

usage() {
  printf 'usage: OVH_LAB_TARGET=<ssh-alias> %s <stage-id> approval|approval-rollback|execute|rollback\n' "$0" >&2
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
  awk 'body { print } /^# OVH_LAB_STAGE_BODY$/ { body=1 }' "$STAGE_SCRIPT"
}

[[ "$STAGE_STATUS" == executable ]] || die 'stage is blocked by the manifest' 20
[[ "$STAGE_KIND" == read-only || "$STAGE_KIND" == plan-only ]] ||
  die 'mutable stages are disabled in this revision' 20
BUNDLE_CONTENT="$(bundle_stage)"
readonly BUNDLE_CONTENT
BUNDLE_SHA256="$(printf '%s\n' "$BUNDLE_CONTENT" | python3 -c 'import hashlib, sys; print(hashlib.sha256(sys.stdin.buffer.read()).hexdigest())')"
readonly BUNDLE_SHA256
APPROVAL_OPERATION="$OPERATION"
[[ "$OPERATION" == approval ]] && APPROVAL_OPERATION=execute
[[ "$OPERATION" == approval-rollback ]] && APPROVAL_OPERATION=rollback
readonly APPROVAL_OPERATION
readonly APPROVAL_EXPECTED="$ACK_EXPECTED:$STAGE_ID:$APPROVAL_OPERATION:$TARGET:$BUNDLE_SHA256"
if [[ "$OPERATION" == approval || "$OPERATION" == approval-rollback ]]; then
  printf '%s\n' "$APPROVAL_EXPECTED"
  exit 0
fi
[[ "$ACK" == "$APPROVAL_EXPECTED" ]] || die 'stage/operation/target/bundle-bound disposable approval required' 64
[[ -f "$KNOWN_HOSTS" && ! -L "$KNOWN_HOSTS" ]] || die 'OVH_LAB_KNOWN_HOSTS must be a regular non-symlink file' 64

umask 077
readonly ARTIFACTS_ROOT="$SCRIPT_DIR/../../artifacts"
readonly RUN_ROOT="$ARTIFACTS_ROOT/ovh-lab"
[[ ! -L "$ARTIFACTS_ROOT" && ! -L "$RUN_ROOT" ]] || die 'evidence directory must not be a symlink' 73
mkdir -p -- "$RUN_ROOT"
readonly RUN_DIR="$(mktemp -d "$RUN_ROOT/run-${STAGE_ID}-XXXXXXXX")"
readonly LOG_FILE="$RUN_DIR/evidence.log"
readonly SUMMARY_FILE="$RUN_DIR/summary.txt"

redact() {
  sed -E \
    -e 's/([A-Za-z0-9._%+-]+)@([A-Za-z0-9.-]+)/[redacted-account]@[redacted-host]/g' \
    -e 's/([0-9]{1,3}\.){3}[0-9]{1,3}/[redacted-ipv4]/g' \
    -e 's/([[:xdigit:]]{0,4}:){2,}[[:xdigit:]:]{0,4}/[redacted-ipv6]/g' \
    -e 's/(connect to host|connection to|resolve host(name)?|host key for|hostname)[[:space:]]+[A-Za-z0-9._-]+/\1 [redacted-host]/Ig' \
    -e 's#((in|file)[[:space:]]+)/[^[:space:]:]+#\1[redacted-path]#Ig' \
    -e 's#/(Users|home|root)/[^[:space:]]+#[redacted-path]#g' \
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
    -o BatchMode=yes \
    -o ConnectTimeout=10 \
    -o ConnectionAttempts=1 \
    -o ControlMaster=no \
    -o ServerAliveCountMax=3 \
    -o ServerAliveInterval=5 \
    -o StrictHostKeyChecking=yes \
    -o "UserKnownHostsFile=$KNOWN_HOSTS" \
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

printf 'schema=1\nstage=%s\noperation=%s\nmanifest_status=%s\nkind=%s\nbundle_sha256=%s\n' \
  "$STAGE_ID" "$OPERATION" "$STAGE_STATUS" "$STAGE_KIND" "$BUNDLE_SHA256" >"$SUMMARY_FILE"
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
printf 'event=run_end operation_rc=%s after_rc=%s postconditions_rc=%s\n' \
  "$operation_rc" "$after_rc" "$post_rc" | tee -a "$LOG_FILE"

if (( operation_rc != 0 || after_rc != 0 || post_rc != 0 )); then
  result_rc="$operation_rc"
  (( result_rc != 0 )) || result_rc="$after_rc"
  (( result_rc != 0 )) || result_rc="$post_rc"
  die "stage did not pass; redacted evidence: $RUN_DIR" "$result_rc"
fi
printf 'ovh-lab: stage passed; redacted evidence: %s\n' "$RUN_DIR"
