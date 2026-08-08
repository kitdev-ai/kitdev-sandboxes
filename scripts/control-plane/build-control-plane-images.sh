#!/usr/bin/env bash
set -Eeuo pipefail
readonly SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
# shellcheck source=common.sh
source "$SCRIPT_DIR/common.sh"

readonly GO_INDEX=sha256:0178a641fbb4858c5f1b48e34bdaabe0350a330a1b1149aabd498d0699ff5fb2
readonly ALPINE_INDEX=sha256:28bd5fe8b56d1bd048e5babf5b10710ebe0bae67db86916198a6eec434943f8b
readonly MIGRATION_TIMESTAMP=20260728163016
readonly MANIFEST="$KITDEV_RUNTIME_ROOT/control-plane/images.json"

cleanup() {
  [[ -z "${stage:-}" ]] || rm -rf -- "$stage"
}

image_id() {
  local value
  value="$(docker image inspect --format '{{.Id}}' -- "$1")" ||
    control_plane_die image_inspect_failed 65
  [[ "$value" =~ ^sha256:[0-9a-f]{64}$ ]] || control_plane_die image_id_invalid 65
  [[ "$(docker image inspect --format '{{.Os}}/{{.Architecture}}' -- "$value")" == linux/amd64 ]] ||
    control_plane_die image_platform_mismatch 65
  printf '%s' "$value"
}

record_images() {
  /usr/bin/python3 -I -B -S - "$MANIFEST" "$@" <<'PY_IMAGE_MANIFEST'
import json
import os
import stat
import sys
import tempfile
from pathlib import Path

path = Path(sys.argv[1])
api, database, clickhouse, proxy = sys.argv[2:]
document = {
    "schema_version": 1,
    "source_commit": "882a3b4786755db9e94be3297de6827f9100ce5e",
    "platform": "linux/amd64",
    "migration_timestamp": "20260728163016",
    "images": {
        "api": api,
        "db_migrator": database,
        "clickhouse_migrator": clickhouse,
        "client_proxy": proxy,
    },
}
payload = (json.dumps(document, sort_keys=True, separators=(",", ":")) + "\n").encode("ascii")
parent_metadata = os.lstat(path.parent)
if (
    not stat.S_ISDIR(parent_metadata.st_mode)
    or stat.S_ISLNK(parent_metadata.st_mode)
    or parent_metadata.st_uid != 0
    or parent_metadata.st_gid != 0
    or stat.S_IMODE(parent_metadata.st_mode) != 0o700
):
    raise SystemExit(1)


def require_existing():
    before = os.lstat(path)
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0))
    try:
        opened = os.fstat(descriptor)
        if (
            not stat.S_ISREG(opened.st_mode)
            or opened.st_uid != 0
            or opened.st_gid != 0
            or stat.S_IMODE(opened.st_mode) != 0o600
            or opened.st_nlink != 1
            or (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino)
        ):
            raise SystemExit(1)
        data = bytearray()
        while True:
            chunk = os.read(descriptor, min(65_536, 65_537 - len(data)))
            if not chunk:
                break
            data.extend(chunk)
            if len(data) > 65_536:
                raise SystemExit(1)
        after = os.fstat(descriptor)
        published = os.stat(path, follow_symlinks=False)
        fields = ("st_mode", "st_uid", "st_gid", "st_nlink", "st_size", "st_dev", "st_ino", "st_mtime_ns", "st_ctime_ns")
        if any(getattr(opened, field) != getattr(after, field) or getattr(after, field) != getattr(published, field) for field in fields):
            raise SystemExit(1)
        if bytes(data) != payload:
            raise SystemExit(1)
    finally:
        os.close(descriptor)


descriptor, name = tempfile.mkstemp(prefix=".images.", dir=path.parent)
temporary = Path(name)
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
    if path.exists() or path.is_symlink():
        require_existing()
        temporary.unlink()
    else:
        try:
            os.link(temporary, path, follow_symlinks=False)
        except FileExistsError:
            require_existing()
        temporary.unlink()
    parent = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(parent)
    finally:
        os.close(parent)
finally:
    if descriptor >= 0:
        os.close(descriptor)
    try:
        temporary.unlink()
    except FileNotFoundError:
        pass
PY_IMAGE_MANIFEST
}

main() {
  local stage='' latest manifest_output api_id database_id clickhouse_id proxy_id
  require_root
  require_lifecycle_platform
  require_command docker
  require_clean_infra_checkout
  /usr/bin/python3 -I -B -S "$SCRIPT_DIR/private_env.py" verify >/dev/null
  require_exact_directory "$KITDEV_RUNTIME_ROOT/control-plane" root root 700

  latest="$(find "$KITDEV_INFRA_ROOT/packages/db/migrations" -maxdepth 1 -type f -name '*.sql' \
    -exec basename -- {} \; | sed 's/_.*//' | sort | tail -n 1)"
  [[ "$latest" == "$MIGRATION_TIMESTAMP" ]] || control_plane_die migration_timestamp_mismatch 65

  if [[ -e "$MANIFEST" || -L "$MANIFEST" ]]; then
    manifest_output="$(/usr/bin/python3 -I -B -S - "$MANIFEST" <<'PY_READ_MANIFEST'
import json
import os
import re
import stat
import sys
from pathlib import Path

path = Path(sys.argv[1])
before = os.lstat(path)
descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0))
try:
    opened = os.fstat(descriptor)
    if (
        not stat.S_ISREG(opened.st_mode)
        or opened.st_uid != 0
        or opened.st_gid != 0
        or stat.S_IMODE(opened.st_mode) != 0o600
        or opened.st_nlink != 1
        or (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino)
    ):
        raise SystemExit(1)
    data = bytearray()
    while True:
        chunk = os.read(descriptor, min(65_536, 65_537 - len(data)))
        if not chunk:
            break
        data.extend(chunk)
        if len(data) > 65_536:
            raise SystemExit(1)
    after = os.fstat(descriptor)
    published = os.stat(path, follow_symlinks=False)
    fields = ("st_mode", "st_uid", "st_gid", "st_nlink", "st_size", "st_dev", "st_ino", "st_mtime_ns", "st_ctime_ns")
    if any(getattr(opened, field) != getattr(after, field) or getattr(after, field) != getattr(published, field) for field in fields):
        raise SystemExit(1)
finally:
    os.close(descriptor)
try:
    document = json.loads(data.decode("ascii"))
except (UnicodeDecodeError, json.JSONDecodeError):
    raise SystemExit(1)
canonical = (json.dumps(document, sort_keys=True, separators=(",", ":")) + "\n").encode("ascii")
if bytes(data) != canonical:
    raise SystemExit(1)
if set(document) != {"schema_version", "source_commit", "platform", "migration_timestamp", "images"}:
    raise SystemExit(1)
if document["schema_version"] != 1 or document["source_commit"] != "882a3b4786755db9e94be3297de6827f9100ce5e" or document["platform"] != "linux/amd64" or document["migration_timestamp"] != "20260728163016":
    raise SystemExit(1)
images = document["images"]
keys = ("api", "db_migrator", "clickhouse_migrator", "client_proxy")
if set(images) != set(keys):
    raise SystemExit(1)
for key in keys:
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", images[key]):
        raise SystemExit(1)
    print(images[key])
if len(set(images.values())) != len(keys):
    raise SystemExit(1)
PY_READ_MANIFEST
    )" || control_plane_die image_manifest_invalid 65
    mapfile -t ids <<<"$manifest_output"
    [[ "${#ids[@]}" == 4 ]] || control_plane_die image_manifest_invalid 65
    api_id="$(image_id "${ids[0]}")"
    database_id="$(image_id "${ids[1]}")"
    clickhouse_id="$(image_id "${ids[2]}")"
    proxy_id="$(image_id "${ids[3]}")"
    /usr/bin/python3 -I -B -S "$SCRIPT_DIR/private_env.py" set-images \
      "$api_id" "$database_id" "$clickhouse_id" "$proxy_id"
    printf 'status=pass operation=build-control-plane-images result=unchanged\n'
    return 0
  fi

  stage="$(mktemp -d "$KITDEV_DATA_ROOT/build-cache/control-plane-images.XXXXXXXX")"
  trap cleanup EXIT
  chmod 0700 -- "$stage"
  /usr/bin/python3 -I -B -S - "$KITDEV_INFRA_ROOT" "$stage" "$GO_INDEX" "$ALPINE_INDEX" <<'PY_DOCKERFILES'
import sys
from pathlib import Path

root, output = map(Path, sys.argv[1:3])
go_digest, alpine_digest = sys.argv[3:]
builder = "FROM golang:${GOLANG_VERSION}-alpine${ALPINE_VERSION} AS builder"
runtime = "FROM alpine:${ALPINE_VERSION}"
for component in ("api", "db", "clickhouse", "client-proxy"):
    source = (root / "packages" / component / "Dockerfile").read_text(encoding="utf-8")
    if source.count(builder) != 1 or source.count(runtime) != 1:
        raise SystemExit(1)
    source = source.replace(builder, f"FROM docker.io/library/golang:1.26.5-alpine3.24@{go_digest} AS builder")
    source = source.replace(runtime, f"FROM docker.io/library/alpine:3.24@{alpine_digest}")
    (output / f"{component}.Dockerfile").write_text(source, encoding="utf-8")
PY_DOCKERFILES

  docker buildx build --pull --platform linux/amd64 --load \
    --file "$stage/api.Dockerfile" \
    --build-arg "COMMIT_SHA=$KITDEV_INFRA_SHORT_COMMIT" \
    --build-arg "EXPECTED_MIGRATION_TIMESTAMP=$MIGRATION_TIMESTAMP" \
    --label "org.opencontainers.image.revision=$KITDEV_INFRA_COMMIT" \
    --tag "kitdev/e2b-api:$KITDEV_INFRA_SHORT_COMMIT" "$KITDEV_INFRA_ROOT/packages"
  docker buildx build --pull --platform linux/amd64 --load \
    --file "$stage/db.Dockerfile" \
    --label "org.opencontainers.image.revision=$KITDEV_INFRA_COMMIT" \
    --tag "kitdev/e2b-db-migrator:$KITDEV_INFRA_SHORT_COMMIT" "$KITDEV_INFRA_ROOT/packages"
  docker buildx build --pull --platform linux/amd64 --load \
    --file "$stage/clickhouse.Dockerfile" \
    --label "org.opencontainers.image.revision=$KITDEV_INFRA_COMMIT" \
    --tag "kitdev/e2b-clickhouse-migrator:$KITDEV_INFRA_SHORT_COMMIT" \
    "$KITDEV_INFRA_ROOT/packages/clickhouse"
  docker buildx build --pull --platform linux/amd64 --load \
    --file "$stage/client-proxy.Dockerfile" \
    --build-arg "COMMIT_SHA=$KITDEV_INFRA_SHORT_COMMIT" \
    --label "org.opencontainers.image.revision=$KITDEV_INFRA_COMMIT" \
    --tag "kitdev/e2b-client-proxy:$KITDEV_INFRA_SHORT_COMMIT" "$KITDEV_INFRA_ROOT/packages"

  api_id="$(image_id "kitdev/e2b-api:$KITDEV_INFRA_SHORT_COMMIT")"
  database_id="$(image_id "kitdev/e2b-db-migrator:$KITDEV_INFRA_SHORT_COMMIT")"
  clickhouse_id="$(image_id "kitdev/e2b-clickhouse-migrator:$KITDEV_INFRA_SHORT_COMMIT")"
  proxy_id="$(image_id "kitdev/e2b-client-proxy:$KITDEV_INFRA_SHORT_COMMIT")"
  [[ "$api_id" != "$database_id" && "$api_id" != "$clickhouse_id" &&
    "$api_id" != "$proxy_id" && "$database_id" != "$clickhouse_id" &&
    "$database_id" != "$proxy_id" && "$clickhouse_id" != "$proxy_id" ]] ||
    control_plane_die image_ids_not_distinct 65
  record_images "$api_id" "$database_id" "$clickhouse_id" "$proxy_id" ||
    control_plane_die image_manifest_conflict 65
  /usr/bin/python3 -I -B -S "$SCRIPT_DIR/private_env.py" set-images \
    "$api_id" "$database_id" "$clickhouse_id" "$proxy_id"
  printf 'status=pass operation=build-control-plane-images result=created\n'
}

main "$@"
