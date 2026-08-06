#!/usr/bin/env bash
# Run the repository's read-only verification suite in an ephemeral SSH directory.

set -euo pipefail
IFS=$'\n\t'
umask 077

readonly CONNECT_TIMEOUT_SECONDS=10
readonly COMMAND_TIMEOUT_SECONDS=300
readonly REMOTE_PREFIX='/tmp/kitdev-test-'

usage() {
    printf 'Usage: KITDEV_TEST_SSH_FINGERPRINT=SHA256:... %s [user@host]\n' "${0##*/}" >&2
    printf '       or set KITDEV_TEST_SSH_TARGET instead of passing user@host\n' >&2
}

die() {
    printf 'remote test harness: %s\n' "$1" >&2
    exit 2
}

target="${1:-${KITDEV_TEST_SSH_TARGET:-}}"
expected_fingerprint="${KITDEV_TEST_SSH_FINGERPRINT:-}"

if (($# > 1)); then
    usage
    exit 2
fi
[[ "$target" =~ ^[a-z_][a-z0-9_-]*@[A-Za-z0-9]([A-Za-z0-9.-]*[A-Za-z0-9])?$ ]] || {
    usage
    die 'SSH target is missing or unsafe'
}
[[ "$expected_fingerprint" =~ ^SHA256:[A-Za-z0-9+/]{43}$ ]] ||
    die 'KITDEV_TEST_SSH_FINGERPRINT is missing or malformed'

repo_root="$(git rev-parse --show-toplevel 2>/dev/null)" || die 'run this script in a Git worktree'
[[ -n "$repo_root" && "$repo_root" != / ]] || die 'unsafe repository root'
cd "$repo_root"

local_tmp="$(mktemp -d "${TMPDIR:-/tmp}/kitdev-remote-test.XXXXXX")"
[[ -n "$local_tmp" && "$local_tmp" != / ]] || die 'unsafe local temporary path'
known_hosts="$local_tmp/known_hosts"
archive="$local_tmp/worktree.tar"
remote_output="$local_tmp/remote-output.txt"

remote_suffix="$(python3 - <<'PY'
import secrets
import time

print(f"{int(time.time())}-{secrets.token_hex(8)}")
PY
)"
remote_dir="${REMOTE_PREFIX}${remote_suffix}"
[[ "$remote_dir" =~ ^/tmp/kitdev-test-[A-Za-z0-9_-]+$ ]] || die 'unsafe remote temporary path'

remote_created=0

ssh_options=(
    -o BatchMode=yes
    -o "ConnectTimeout=${CONNECT_TIMEOUT_SECONDS}"
    -o ConnectionAttempts=1
    -o ControlMaster=no
    -o ControlPath=none
    -o ControlPersist=no
    -o GlobalKnownHostsFile=/dev/null
    -o "UserKnownHostsFile=${known_hosts}"
    -o StrictHostKeyChecking=yes
    -o CheckHostIP=no
    -o LogLevel=ERROR
)

cleanup_remote() {
    if [[ "$remote_created" == 1 && "$remote_dir" =~ ^/tmp/kitdev-test-[A-Za-z0-9_-]+$ ]]; then
        timeout 30 ssh "${ssh_options[@]}" "$target" /bin/bash -s -- "$remote_dir" >/dev/null 2>&1 <<'REMOTE_CLEANUP' || true
set -eu
path="$1"
[[ "$path" =~ ^/tmp/kitdev-test-[A-Za-z0-9_-]+$ ]] || exit 2
if [ -d "$path" ] && [ ! -L "$path" ]; then
    find "$path" -xdev -depth -delete
elif [ -e "$path" ] || [ -L "$path" ]; then
    exit 2
fi
REMOTE_CLEANUP
    fi
}

cleanup_local() {
    if [[ -n "${local_tmp:-}" && "$local_tmp" != / && -d "$local_tmp" && ! -L "$local_tmp" ]]; then
        find "$local_tmp" -xdev -depth -delete
    fi
}

cleanup_all() {
    cleanup_remote
    cleanup_local
}
trap cleanup_all EXIT HUP INT TERM

host="${target#*@}"
keyscan_output="$(timeout 20 ssh-keyscan -T "$CONNECT_TIMEOUT_SECONDS" -t ed25519 -- "$host" 2>/dev/null)" ||
    die 'SSH host-key scan failed'
[[ -n "$keyscan_output" ]] || die 'SSH host-key scan returned no ED25519 key'
actual_fingerprint="$(printf '%s\n' "$keyscan_output" | ssh-keygen -E sha256 -lf - 2>/dev/null | awk 'NR == 1 { print $2 }')"
[[ "$actual_fingerprint" == "$expected_fingerprint" ]] || die 'SSH host-key fingerprint mismatch'
printf '%s\n' "$keyscan_output" >"$known_hosts"

python3 - "$archive" <<'PY'
from __future__ import annotations

import os
from pathlib import Path, PurePosixPath
import stat
import subprocess
import sys
import tarfile

output = Path(sys.argv[1])
listed = subprocess.run(
    ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
    check=True,
    stdout=subprocess.PIPE,
).stdout
paths = sorted(filter(None, listed.decode("utf-8", "strict").split("\0")))
if not paths:
    raise SystemExit("refusing to create an empty worktree archive")

with tarfile.open(output, "w", format=tarfile.PAX_FORMAT) as archive:
    for raw in paths:
        relative = PurePosixPath(raw)
        if relative.is_absolute() or ".." in relative.parts or str(relative) != raw:
            raise SystemExit("unsafe repository path")
        source = Path(raw)
        mode = source.lstat().st_mode
        if not (stat.S_ISREG(mode) or stat.S_ISLNK(mode)):
            raise SystemExit("unsupported repository entry type")
        if stat.S_ISLNK(mode):
            link = PurePosixPath(os.readlink(source))
            if link.is_absolute() or ".." in link.parts:
                raise SystemExit("unsafe repository symlink")
        info = archive.gettarinfo(str(source), arcname=raw)
        info.uid = 0
        info.gid = 0
        info.uname = "root"
        info.gname = "root"
        info.mtime = 0
        if info.isfile():
            with source.open("rb") as stream:
                archive.addfile(info, stream)
        else:
            archive.addfile(info)
PY

timeout 30 ssh "${ssh_options[@]}" "$target" /bin/bash -s -- "$remote_dir" <<'REMOTE_CREATE'
set -euo pipefail
path="$1"
[[ "$path" =~ ^/tmp/kitdev-test-[A-Za-z0-9_-]+$ ]] || exit 2
[ ! -e "$path" ] && [ ! -L "$path" ]
mkdir -m 700 -- "$path"
REMOTE_CREATE
remote_created=1

timeout 60 scp -q "${ssh_options[@]}" -- "$archive" "${target}:${remote_dir}/worktree.tar"

set +e
timeout "$COMMAND_TIMEOUT_SECONDS" ssh "${ssh_options[@]}" "$target" /bin/bash -s -- "$remote_dir" >"$remote_output" <<'REMOTE_RUN'
set -euo pipefail
IFS=$'\n\t'
umask 077
export PYTHONDONTWRITEBYTECODE=1
export PYTHONUTF8=1
export LC_ALL=C.UTF-8
export LANG=C.UTF-8

remote_dir="$1"
[[ "$remote_dir" =~ ^/tmp/kitdev-test-[A-Za-z0-9_-]+$ ]] || {
    printf 'HARNESS_ERROR=unsafe_remote_path\n'
    exit 2
}
[ -d "$remote_dir" ] && [ ! -L "$remote_dir" ] || exit 2

cleanup() {
    if [ -n "${remote_dir:-}" ] && [ "$remote_dir" != / ] && [ -d "$remote_dir" ] && [ ! -L "$remote_dir" ]; then
        find "$remote_dir" -xdev -depth -delete
    fi
}
trap cleanup EXIT HUP INT TERM

repo="$remote_dir/repo"
results="$remote_dir/results"
mkdir -m 700 -- "$repo" "$results"
[ -f "$remote_dir/worktree.tar" ] && [ ! -L "$remote_dir/worktree.tar" ] || exit 2
tar -xf "$remote_dir/worktree.tar" -C "$repo" --no-same-owner --no-same-permissions

surface_snapshot() {
    output="$1"
    : >"$output"
    for path in \
        /etc/kitdev-sandboxes \
        /opt/kitdev-sandboxes \
        /var/lib/kitdev-sandboxes \
        /var/log/kitdev-sandboxes
    do
        if [ -e "$path" ] || [ -L "$path" ]; then
            stat -c '%n|present|%F|%a|%u|%g|%s|%Y|%Z' -- "$path" >>"$output"
        else
            printf '%s|absent\n' "$path" >>"$output"
        fi
    done
}

tree_snapshot() {
    (
        cd "$repo"
        find . -xdev -type d -printf 'd|%P|%m\n' | LC_ALL=C sort
        find . -xdev -type l -printf 'l|%P|%l\n' | LC_ALL=C sort
        find . -xdev -type f -print0 | LC_ALL=C sort -z | xargs -0 -r sha256sum
    ) >"$1"
}

surface_snapshot "$results/surface-before"
tree_snapshot "$results/tree-before"

python_version="$(python3 -c 'import platform; print(platform.python_version())')"

set +e
(
    cd "$repo"
    PYTHONPATH=src python3 -m unittest discover -s tests/unit -v
) >"$results/unit.stdout" 2>"$results/unit.stderr"
unit_exit=$?
set -e
unit_count="$(sed -n 's/^Ran \([0-9][0-9]*\) tests.*$/\1/p' "$results/unit.stderr" | tail -n 1)"
[ -n "$unit_count" ] || unit_count=unknown

run_cli() {
    name="$1"
    shift
    set +e
    (cd "$repo" && ./kitdev "$@") >"$results/${name}.json" 2>"$results/${name}.stderr"
    status=$?
    set -e
    python3 -m json.tool "$results/${name}.json" >/dev/null
    [ ! -s "$results/${name}.stderr" ]
    printf '%s' "$status"
}

doctor_exit="$(run_cli doctor doctor --json --non-interactive)"
install_exit="$(run_cli install install --dry-run --json --non-interactive)"
identity_exit="$(run_cli identity install --phase identity-access --dry-run --json --non-interactive)"

surface_snapshot "$results/surface-after"
tree_snapshot "$results/tree-after"

surface_unchanged=no
tree_unchanged=no
pycache_absent=no
cmp -s "$results/surface-before" "$results/surface-after" && surface_unchanged=yes
cmp -s "$results/tree-before" "$results/tree-after" && tree_unchanged=yes
if ! find "$repo" -xdev \( -type d -name __pycache__ -o -type f -name '*.py[co]' \) -print -quit | grep -q .; then
    pycache_absent=yes
fi

printf 'PYTHON_VERSION=%s\n' "$python_version"
printf 'UNIT_TEST_COUNT=%s\n' "$unit_count"
printf 'UNIT_TEST_EXIT=%s\n' "$unit_exit"
printf 'DOCTOR_JSON_EXIT=%s\n' "$doctor_exit"
printf 'INSTALL_DRY_RUN_JSON_EXIT=%s\n' "$install_exit"
printf 'IDENTITY_DRY_RUN_JSON_EXIT=%s\n' "$identity_exit"
printf 'PROJECT_SURFACE_BEFORE_BEGIN\n'
cat "$results/surface-before"
printf 'PROJECT_SURFACE_BEFORE_END\n'
printf 'PROJECT_SURFACE_AFTER_BEGIN\n'
cat "$results/surface-after"
printf 'PROJECT_SURFACE_AFTER_END\n'
printf 'PROJECT_SURFACE_UNCHANGED=%s\n' "$surface_unchanged"
printf 'WORKTREE_UNCHANGED=%s\n' "$tree_unchanged"
printf 'PYCACHE_ABSENT=%s\n' "$pycache_absent"
printf 'REMOTE_TRAP_ARMED=yes\n'

[ "$unit_exit" -eq 0 ]
[ "$doctor_exit" -eq 5 ]
[ "$install_exit" -eq 5 ]
[ "$identity_exit" -eq 5 ]
[ "$surface_unchanged" = yes ]
[ "$tree_unchanged" = yes ]
[ "$pycache_absent" = yes ]
REMOTE_RUN
remote_status=$?
set -e

# The remote EXIT trap should already have removed the directory. Verify it
# independently before disabling the local fail-safe cleanup.
set +e
timeout 30 ssh "${ssh_options[@]}" "$target" /bin/bash -s -- "$remote_dir" >/dev/null 2>&1 <<'REMOTE_VERIFY'
set -eu
path="$1"
[[ "$path" =~ ^/tmp/kitdev-test-[A-Za-z0-9_-]+$ ]] || exit 2
[ ! -e "$path" ] && [ ! -L "$path" ]
REMOTE_VERIFY
cleanup_status=$?
set -e
if [[ "$cleanup_status" == 0 ]]; then
    remote_created=0
fi

cat "$remote_output"
printf 'REMOTE_COMMAND_EXIT=%s\n' "$remote_status"
if [[ "$cleanup_status" == 0 ]]; then
    printf 'REMOTE_CLEANUP_VERIFIED=yes\n'
else
    printf 'REMOTE_CLEANUP_VERIFIED=no\n'
fi

[[ "$remote_status" == 0 ]] || exit "$remote_status"
[[ "$cleanup_status" == 0 ]] || exit 1
