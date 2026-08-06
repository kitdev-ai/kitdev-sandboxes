#!/usr/bin/env bash
set -Eeuo pipefail
export PATH=/usr/sbin:/usr/bin:/sbin:/bin
export LC_ALL=C
export LANG=C
unset BASH_ENV CDPATH ENV GLOBIGNORE

readonly SCRIPT_DIR="$(CDPATH= cd -- "$(/usr/bin/dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
readonly SCRIPT_PATH="$SCRIPT_DIR/${BASH_SOURCE[0]##*/}"
readonly REPO_ROOT="$(CDPATH= cd -- "$SCRIPT_DIR/../.." && pwd -P)"
readonly COMMON_PATH="$SCRIPT_DIR/lib/common.sh"
readonly BUNDLE_SHA256="$(
  /usr/bin/python3 - "$REPO_ROOT" \
    "$COMMON_PATH" \
    "$SCRIPT_PATH" \
    "$REPO_ROOT/src/kitdev_sandboxes/runner.py" \
    "$REPO_ROOT/src/kitdev_sandboxes/journal.py" \
    "$REPO_ROOT/src/kitdev_sandboxes/stage05.py" \
    "$REPO_ROOT/src/kitdev_sandboxes/stage10.py" <<'PY_BUNDLE'
import hashlib
import os
import stat
import struct
import sys
from pathlib import Path

root = Path(sys.argv[1])
paths = tuple(Path(value) for value in sys.argv[2:])
if not hasattr(os, "O_NOFOLLOW") or not root.is_absolute() or len(paths) != 6:
    raise SystemExit(1)

checked_directories = set()


def require_trusted_directory(directory):
    if directory in checked_directories:
        return
    metadata = os.lstat(directory)
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or stat.S_ISLNK(metadata.st_mode)
        or metadata.st_uid != 0
        or stat.S_IMODE(metadata.st_mode) & 0o022
    ):
        raise SystemExit(1)
    checked_directories.add(directory)


for directory in (Path("/"), *reversed(root.parents), root):
    require_trusted_directory(directory)

digest = hashlib.sha256()
for path in paths:
    try:
        relative = path.relative_to(root)
        label = relative.as_posix().encode("ascii", errors="strict")
    except (ValueError, UnicodeError):
        raise SystemExit(1)
    directory = root
    for component in relative.parts[:-1]:
        directory /= component
        require_trusted_directory(directory)
    before = os.lstat(path)
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0))
    try:
        opened = os.fstat(descriptor)
        if (
            not stat.S_ISREG(opened.st_mode)
            or opened.st_uid != 0
            or stat.S_IMODE(opened.st_mode) & 0o022
            or opened.st_nlink != 1
            or (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino)
        ):
            raise SystemExit(1)
        content = bytearray()
        while True:
            chunk = os.read(descriptor, min(65_536, 1_000_001 - len(content)))
            if not chunk:
                break
            content.extend(chunk)
            if len(content) > 1_000_000:
                raise SystemExit(1)
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
            raise SystemExit(1)
    finally:
        os.close(descriptor)
    digest.update(struct.pack(">Q", len(label)))
    digest.update(label)
    digest.update(struct.pack(">Q", len(content)))
    digest.update(content)
print(digest.hexdigest())
PY_BUNDLE
)"
[[ "$BUNDLE_SHA256" =~ ^[0-9a-f]{64}$ ]] || exit 64

# shellcheck source=lib/common.sh
source "$COMMON_PATH"
unset APT_CONFIG

readonly APPLY_ACK="DISPOSABLE_OVH_LAB:docker-bootstrap:$BUNDLE_SHA256"
readonly KEY_URL="https://download.docker.com/linux/ubuntu/gpg"
readonly KEY_PATH="/etc/apt/keyrings/docker.asc"
readonly KEY_SHA256="1500c1f56fa9e26b9b8f42452a553675796ade0807cdce11975eb98170b3a570"
readonly KEY_PRIMARY_FINGERPRINT="9DC858229FC7DD38854AE2D88D81803C0EBFCD88"
readonly KEY_SIGNING_FINGERPRINT="D3306A018370199E527AE7997EA0A9C3F273FCD8"
readonly SOURCE_PATH="/etc/apt/sources.list.d/docker.sources"
readonly SOURCE_SHA256="47be0f749c19273936c7e56fff5a29b9108bcce8137ee677cc736523fb876e71"
readonly DPKG_QUERY_FORMAT='${db:Status-Want}\t${db:Status-Eflag}\t'\
'${db:Status-Status}\t${Version}\t${Architecture}\n'

readonly -a BASELINE_PACKAGES=(
  'ca-certificates=20260601~26.04.1'
  'curl=8.18.0-1ubuntu2.3'
  'gnupg=2.4.8-4ubuntu3'
  'git=1:2.53.0-1ubuntu1'
  'jq=1.8.1-4ubuntu2'
  'make=4.4.1-3'
  'kmod=34.2-2ubuntu2'
  'iproute2=6.19.0-1ubuntu1.1'
  'iptables=1.8.11-2ubuntu3'
  'util-linux=2.41.3-3ubuntu2'
  'procps=2:4.0.4-9ubuntu1'
  'xz-utils=5.8.3-1'
)
readonly -a DOCKER_PACKAGES=(
  'docker-ce=5:29.7.2-1~ubuntu.26.04~resolute'
  'docker-ce-cli=5:29.7.2-1~ubuntu.26.04~resolute'
  'containerd.io=2.3.3-1~ubuntu.26.04~resolute'
  'docker-buildx-plugin=0.36.1-1~ubuntu.26.04~resolute'
  'docker-compose-plugin=5.4.0-1~ubuntu.26.04~resolute'
)
readonly -a DOCKER_CONFLICTS=(
  docker.io docker-compose docker-compose-v2 docker-doc docker-buildx
  podman-docker containerd runc
)
BOOTSTRAP_TEMPORARY=''
AUTHORIZATION_FD=''
AUTHORIZATION_PID=''

cleanup() {
  if [[ -n "$BOOTSTRAP_TEMPORARY" ]]; then
    rm -rf -- "$BOOTSTRAP_TEMPORARY"
  fi
}

usage() {
  printf 'usage: %s approval|apply|verify\n' "$0" >&2
  exit 64
}

stage05_authorization_python() {
  python3 -I -B -S /dev/fd/3 "$REPO_ROOT/src" "$1" 3<<'PY_AUTHORIZATION'
import sys

sys.path.insert(0, sys.argv[1])
try:
    from kitdev_sandboxes.stage10 import Stage10Resolver

    resolver = Stage10Resolver("0" * 64)
    resolver._platform_bytes()
    with resolver._authorization_session():
        if sys.argv[2] == "hold":
            print("ready", flush=True)
            if sys.stdin.buffer.read(1):
                raise ValueError
        elif sys.argv[2] != "check":
            raise ValueError
except BaseException:
    raise SystemExit(1)
PY_AUTHORIZATION
}

require_stage05_authorization() {
  stage05_authorization_python check
}

start_stage05_authorization() {
  local ready read_fd
  coproc STAGE05_AUTHORIZATION { stage05_authorization_python hold 2>/dev/null; }
  AUTHORIZATION_PID="$STAGE05_AUTHORIZATION_PID"
  AUTHORIZATION_FD="${STAGE05_AUTHORIZATION[1]}"
  read_fd="${STAGE05_AUTHORIZATION[0]}"
  if ! IFS= read -r ready <&"$read_fd" || [[ "$ready" != ready ]]; then
    lab_die stage05_authorization_invalid 65
  fi
  exec {read_fd}<&-
}

finish_stage05_authorization() {
  exec {AUTHORIZATION_FD}>&-
  wait "$AUTHORIZATION_PID" || lab_die stage05_authorization_changed 65
  AUTHORIZATION_PID=''
}

require_fixed_host() {
  [[ "$(id -u)" == 0 ]] || lab_die root_required 77
  lab_refuse_production
  lab_require_supported_platform
  [[ "$(dpkg --print-architecture)" == amd64 ]] ||
    lab_die unsupported_lab_architecture 68
}

package_version() {
  local output rc
  if output="$(
    dpkg-query --show \
      --showformat="$DPKG_QUERY_FORMAT" \
      -- "$1" 2>/dev/null
  )"; then
    rc=0
  else
    rc=$?
  fi
  if (( rc == 1 )) && [[ -z "$output" ]]; then
    printf 'absent'
    return 0
  fi
  (( rc == 0 )) || lab_die package_inventory_unknown 65
  [[ "$output" =~ ^[a-z-]+$'\t'[a-z-]+$'\t'[a-z-]+$'\t'[A-Za-z0-9.+:~_-]+$'\t'(all|amd64)$ ]] ||
    lab_die package_inventory_unknown 65
  [[ "$output" == *$'\tok\t'* ]] || lab_die package_status_error 65
  printf '%s' "$output"
}

require_conflicts_absent() {
  local package state
  for package in "${DOCKER_CONFLICTS[@]}"; do
    state="$(package_version "$package")"
    [[ "$state" == absent ]] || lab_die docker_conflict_present 65
  done
}

repository_file() {
  python3 - "$1" "$2" "$3" "${4:--}" <<'PY_REPOSITORY_FILE'
import errno
import ctypes
import hashlib
import os
import secrets
import stat
import sys
from pathlib import Path

operation, target_value, expected, source_value = sys.argv[1:]
target = Path(target_value)
if (
    operation not in {"check", "require", "publish"}
    or not target.is_absolute()
    or len(expected) != 64
    or any(character not in "0123456789abcdef" for character in expected)
    or not hasattr(os, "O_NOFOLLOW")
):
    raise SystemExit(1)
if operation == "publish":
    os.umask(0o022)

uid = os.geteuid()
gid = os.getegid()
directory_flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
parent = os.open("/", directory_flags)
try:
    components = target.parent.parts[1:]
    for index, component in enumerate(components):
        try:
            child = os.open(component, directory_flags, dir_fd=parent)
        except FileNotFoundError:
            if operation == "check" and index == len(components) - 1:
                print("absent")
                raise SystemExit(0)
            if operation != "publish" or index != len(components) - 1:
                raise
            os.mkdir(component, 0o755, dir_fd=parent)
            os.fsync(parent)
            child = os.open(component, directory_flags, dir_fd=parent)
            os.fchmod(child, 0o755)
            os.fsync(child)
        metadata = os.fstat(child)
        if (
            not stat.S_ISDIR(metadata.st_mode)
            or metadata.st_uid not in {0, uid}
            or stat.S_IMODE(metadata.st_mode) & 0o022
            or (
                index == len(components) - 1
                and (
                    metadata.st_uid != uid
                    or metadata.st_gid != gid
                    or stat.S_IMODE(metadata.st_mode) != 0o755
                )
            )
        ):
            os.close(child)
            raise SystemExit(1)
        os.close(parent)
        parent = child

    def read_published():
        try:
            descriptor = os.open(
                target.name,
                os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0),
                dir_fd=parent,
            )
        except FileNotFoundError:
            return None
        try:
            before = os.fstat(descriptor)
            if (
                not stat.S_ISREG(before.st_mode)
                or before.st_uid != uid
                or before.st_gid != gid
                or stat.S_IMODE(before.st_mode) != 0o644
                or before.st_nlink != 1
                or os.listxattr(descriptor)
            ):
                raise SystemExit(1)
            content = bytearray()
            while True:
                chunk = os.read(descriptor, min(65_536, 65_537 - len(content)))
                if not chunk:
                    break
                content.extend(chunk)
                if len(content) > 65_536:
                    raise SystemExit(1)
            after = os.fstat(descriptor)
            published = os.stat(target.name, dir_fd=parent, follow_symlinks=False)
            fields = (
                "st_mode", "st_uid", "st_gid", "st_nlink", "st_size", "st_dev",
                "st_ino", "st_mtime_ns", "st_ctime_ns",
            )
            if any(
                getattr(before, field) != getattr(after, field)
                or getattr(after, field) != getattr(published, field)
                for field in fields
            ):
                raise SystemExit(1)
            if hashlib.sha256(content).hexdigest() != expected:
                raise SystemExit(1)
            return bytes(content)
        finally:
            os.close(descriptor)

    existing = read_published()
    if existing is not None:
        print("present")
        raise SystemExit(0)
    if operation == "check":
        print("absent")
        raise SystemExit(0)
    if operation == "require":
        raise SystemExit(1)

    source = os.open(
        source_value,
        os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0),
    )
    try:
        source_metadata = os.fstat(source)
        if (
            not stat.S_ISREG(source_metadata.st_mode)
            or source_metadata.st_uid != uid
            or source_metadata.st_gid != gid
            or source_metadata.st_nlink != 1
            or source_metadata.st_size > 65_536
        ):
            raise SystemExit(1)
        content = bytearray()
        while True:
            chunk = os.read(source, min(65_536, 65_537 - len(content)))
            if not chunk:
                break
            content.extend(chunk)
            if len(content) > 65_536:
                raise SystemExit(1)
        if hashlib.sha256(content).hexdigest() != expected:
            raise SystemExit(1)
    finally:
        os.close(source)

    temporary_name = f".{target.name}.tmp.{secrets.token_hex(16)}"
    descriptor = None
    try:
        descriptor = os.open(
            temporary_name,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0),
            0o600,
            dir_fd=parent,
        )
        view = memoryview(content)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                raise SystemExit(1)
            view = view[written:]
        os.fchmod(descriptor, 0o644)
        os.fsync(descriptor)
        libc = ctypes.CDLL(None, use_errno=True)
        renameat2 = getattr(libc, "renameat2", None)
        if renameat2 is None:
            raise SystemExit(1)
        renameat2.argtypes = (
            ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint
        )
        renameat2.restype = ctypes.c_int
        if renameat2(
            parent,
            os.fsencode(temporary_name),
            parent,
            os.fsencode(target.name),
            1,
        ) != 0:
            raise SystemExit(1)
        temporary_name = ""
        os.fsync(parent)
        published = os.stat(target.name, dir_fd=parent, follow_symlinks=False)
        after = os.fstat(descriptor)
        if (
            (published.st_dev, published.st_ino) != (after.st_dev, after.st_ino)
            or after.st_nlink != 1
            or stat.S_IMODE(after.st_mode) != 0o644
        ):
            raise SystemExit(1)
        print("published")
    finally:
        if descriptor is not None:
            os.close(descriptor)
        if temporary_name:
            try:
                os.unlink(temporary_name, dir_fd=parent)
            except OSError as error:
                if error.errno != errno.ENOENT:
                    raise
finally:
    os.close(parent)
PY_REPOSITORY_FILE
}

require_no_foreign_docker_sources() {
  local path
  local -a candidates=(/etc/apt/sources.list)
  shopt -s nullglob
  candidates+=(/etc/apt/sources.list.d/*.list /etc/apt/sources.list.d/*.sources)
  shopt -u nullglob
  for path in "${candidates[@]}"; do
    [[ "$path" == "$SOURCE_PATH" ]] && continue
    [[ ! -L "$path" ]] || lab_die repository_state_conflict 65
    if [[ -f "$path" ]] && grep -qiF 'download.docker.com' -- "$path"; then
      lab_die duplicate_docker_source 65
    fi
  done
  for path in \
    /etc/apt/keyrings/docker.gpg \
    /usr/share/keyrings/docker-archive-keyring.gpg \
    /usr/share/keyrings/docker.gpg; do
    [[ ! -e "$path" && ! -L "$path" ]] || lab_die legacy_docker_key_present 65
  done
}

require_exact_candidates() {
  local specification package expected candidate
  for specification in "$@"; do
    package="${specification%%=*}"
    expected="${specification#*=}"
    candidate="$(
      apt-cache policy -- "$package" |
        awk '$1 == "Candidate:" {candidate=$2; count++}
             END {if (count == 1) print candidate; else exit 1}'
    )" || lab_die package_candidate_unknown 65
    [[ "$candidate" == "$expected" ]] || lab_die package_candidate_mismatch 65
  done
}

require_exact_installed() {
  local specification package expected state prefix suffix
  for specification in "$@"; do
    package="${specification%%=*}"
    expected="${specification#*=}"
    state="$(package_version "$package")"
    [[ "$state" != absent ]] || lab_die package_version_mismatch 65
    prefix=$'install\tok\tinstalled\t'
    suffix="${state##*$'\t'}"
    [[ "$state" == "$prefix$expected"$'\t'"$suffix" ]] ||
      lab_die package_version_mismatch 65
  done
}

require_manual_marks() {
  local specification package
  for specification in "$@"; do
    package="${specification%%=*}"
    [[ "$(apt-mark showmanual "$package")" == "$package" ]] ||
      lab_die package_manual_mark_mismatch 65
  done
}

verify_key_fingerprints() {
  local key="$1"
  local fingerprint primary_seen=no signing_seen=no
  while IFS= read -r fingerprint; do
    if [[ "$fingerprint" == "$KEY_PRIMARY_FINGERPRINT" ]]; then
      primary_seen=yes
    fi
    if [[ "$fingerprint" == "$KEY_SIGNING_FINGERPRINT" ]]; then
      signing_seen=yes
    fi
  done < <(
    GNUPGHOME="$2" gpg --batch --no-options --with-colons --show-keys -- "$key" |
      awk -F: '$1 == "fpr" {print $10}'
  )
  [[ "$primary_seen" == yes && "$signing_seen" == yes ]] ||
    lab_die docker_key_fingerprint_mismatch 65
}

write_source_fixture() {
  cat >"$1" <<'EOF_DOCKER_SOURCE'
Types: deb
URIs: https://download.docker.com/linux/ubuntu
Suites: resolute
Components: stable
Architectures: amd64
Signed-By: /etc/apt/keyrings/docker.asc
EOF_DOCKER_SOURCE
  chmod 0600 -- "$1"
}

verify_runtime() {
  local driver cgroup_driver cgroup_version
  require_conflicts_absent
  require_no_foreign_docker_sources
  require_exact_installed "${BASELINE_PACKAGES[@]}" "${DOCKER_PACKAGES[@]}"
  require_manual_marks "${BASELINE_PACKAGES[@]}" "${DOCKER_PACKAGES[@]}"
  repository_file require "$KEY_PATH" "$KEY_SHA256" >/dev/null ||
    lab_die repository_state_conflict 65
  repository_file require "$SOURCE_PATH" "$SOURCE_SHA256" >/dev/null ||
    lab_die repository_state_conflict 65
  systemctl is-active --quiet docker.service || lab_die docker_service_inactive 65
  systemctl is-enabled --quiet docker.service || lab_die docker_service_disabled 65
  systemctl is-active --quiet containerd.service || lab_die containerd_service_inactive 65
  systemctl is-enabled --quiet containerd.service || lab_die containerd_service_disabled 65
  driver="$(docker info --format '{{.Driver}}')"
  cgroup_driver="$(docker info --format '{{.CgroupDriver}}')"
  cgroup_version="$(docker info --format '{{.CgroupVersion}}')"
  [[ "$driver" == overlayfs ]] || lab_die docker_storage_driver_mismatch 65
  [[ "$cgroup_driver" == systemd ]] || lab_die docker_cgroup_driver_mismatch 65
  [[ "$cgroup_version" == 2 ]] || lab_die docker_cgroup_version_mismatch 65
  [[ -z "$(docker container ls --all --quiet)" ]] || lab_die docker_containers_present 65
  [[ -z "$(docker image ls --all --quiet)" ]] || lab_die docker_images_present 65
  printf '%s' \
    'status=pass operation=verify docker_packages=exact services=active-enabled '
  printf '%s\n' \
    'driver=overlayfs cgroup_driver=systemd cgroup_version=2 containers=0 images=0'
}

apply_bootstrap() {
  local key source key_state source_state
  require_conflicts_absent
  key_state="$(repository_file check "$KEY_PATH" "$KEY_SHA256")" ||
    lab_die repository_state_conflict 65
  source_state="$(repository_file check "$SOURCE_PATH" "$SOURCE_SHA256")" ||
    lab_die repository_state_conflict 65
  [[ "$source_state" == absent || "$key_state" == present ]] ||
    lab_die repository_state_conflict 65
  require_no_foreign_docker_sources

  DEBIAN_FRONTEND=noninteractive apt-get update
  DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends --no-remove \
    "${BASELINE_PACKAGES[@]}"
  require_exact_installed "${BASELINE_PACKAGES[@]}"
  require_manual_marks "${BASELINE_PACKAGES[@]}"

  if [[ "$key_state" == absent || "$source_state" == absent ]]; then
    BOOTSTRAP_TEMPORARY="$(mktemp -d /tmp/kitdev-docker-bootstrap.XXXXXXXX)"
    chmod 0700 -- "$BOOTSTRAP_TEMPORARY"
    trap cleanup EXIT
  fi
  if [[ "$key_state" == absent ]]; then
    key="$BOOTSTRAP_TEMPORARY/docker.asc"
    mkdir -m 0700 -- "$BOOTSTRAP_TEMPORARY/gnupg"
    curl --fail --location --proto '=https' --tlsv1.2 --silent --show-error \
      --output "$key" -- "$KEY_URL"
    [[ "$(sha256sum -- "$key" | awk '{value=$1} END {print value}')" == "$KEY_SHA256" ]] ||
      lab_die docker_key_hash_mismatch 65
    verify_key_fingerprints "$key" "$BOOTSTRAP_TEMPORARY/gnupg"
    repository_file publish "$KEY_PATH" "$KEY_SHA256" "$key" >/dev/null ||
      lab_die repository_publication_conflict 65
  fi
  if [[ "$source_state" == absent ]]; then
    source="$BOOTSTRAP_TEMPORARY/docker.sources"
    write_source_fixture "$source"
    [[ "$(sha256sum -- "$source" | awk '{value=$1} END {print value}')" == "$SOURCE_SHA256" ]] ||
      lab_die docker_source_hash_mismatch 65
    repository_file publish "$SOURCE_PATH" "$SOURCE_SHA256" "$source" >/dev/null ||
      lab_die repository_publication_conflict 65
  fi
  repository_file require "$KEY_PATH" "$KEY_SHA256" >/dev/null ||
    lab_die repository_state_conflict 65
  repository_file require "$SOURCE_PATH" "$SOURCE_SHA256" >/dev/null ||
    lab_die repository_state_conflict 65

  DEBIAN_FRONTEND=noninteractive apt-get update
  require_exact_candidates "${DOCKER_PACKAGES[@]}"
  DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends --no-remove \
    "${DOCKER_PACKAGES[@]}"
  systemctl enable --now docker.service containerd.service
}

main() {
  [[ $# == 1 ]] || usage
  case "$1" in
    approval) printf '%s\n' "$APPLY_ACK" ;;
    apply)
      [[ "${DISPOSABLE_OVH_LAB:-}" == "$APPLY_ACK" ]] ||
        lab_die acknowledgement_required 64
      require_fixed_host
      start_stage05_authorization
      apply_bootstrap
      finish_stage05_authorization
      verify_runtime
      ;;
    verify)
      require_fixed_host
      require_stage05_authorization || lab_die stage05_authorization_invalid 65
      verify_runtime
      ;;
    *) usage ;;
  esac
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
  main "$@"
fi
