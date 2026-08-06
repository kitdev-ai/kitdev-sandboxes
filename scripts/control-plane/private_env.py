#!/usr/bin/env python3
from __future__ import annotations

import ipaddress
import os
import re
import secrets
import stat
import sys
import tempfile
from pathlib import Path
from urllib.parse import quote


ENV_PATH = Path("/etc/kitdev-sandboxes/control-plane.env")
SECRET_KEYS = (
    "POSTGRES_PASSWORD",
    "POSTGRES_CONNECTION_STRING",
    "CLICKHOUSE_USER",
    "CLICKHOUSE_PASSWORD",
    "CLICKHOUSE_CONNECTION_STRING",
    "SANDBOX_ACCESS_TOKEN_HASH_SEED",
    "ADMIN_TOKEN",
)
IMAGE_KEYS = (
    "E2B_API_IMAGE_REF",
    "E2B_DB_MIGRATOR_IMAGE_REF",
    "E2B_CLICKHOUSE_MIGRATOR_IMAGE_REF",
    "E2B_CLIENT_PROXY_IMAGE_REF",
)
NETWORK_KEYS = ("KITDEV_CORE_SUBNET", "KITDEV_CORE_GATEWAY")
ALL_KEYS = SECRET_KEYS + IMAGE_KEYS + NETWORK_KEYS
HEX64 = re.compile(r"[0-9a-f]{64}")
IMAGE_REF = re.compile(r"sha256:[0-9a-f]{64}")


def fail(reason: str) -> "NoReturn":
    print(f"status=error reason={reason}", file=sys.stderr)
    raise SystemExit(65)


def require_root() -> None:
    if os.geteuid() != 0:
        fail("root_required")


def require_parent() -> None:
    try:
        metadata = os.lstat(ENV_PATH.parent)
    except FileNotFoundError:
        fail("private_env_parent_missing")
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or stat.S_ISLNK(metadata.st_mode)
        or metadata.st_uid != 0
        or metadata.st_gid != 0
        or stat.S_IMODE(metadata.st_mode) != 0o700
    ):
        fail("private_env_parent_conflict")


def read_document() -> dict[str, str] | None:
    require_parent()
    try:
        before = os.lstat(ENV_PATH)
    except FileNotFoundError:
        return None
    flags = os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
    try:
        descriptor = os.open(ENV_PATH, flags)
    except OSError:
        fail("private_env_open_failed")
    try:
        opened = os.fstat(descriptor)
        if (
            not stat.S_ISREG(opened.st_mode)
            or opened.st_uid != 0
            or opened.st_gid != 0
            or stat.S_IMODE(opened.st_mode) != 0o600
            or opened.st_nlink != 1
            or (before.st_dev, before.st_ino) != (opened.st_dev, opened.st_ino)
        ):
            fail("private_env_metadata_conflict")
        data = bytearray()
        while True:
            chunk = os.read(descriptor, min(65_536, 65_537 - len(data)))
            if not chunk:
                break
            data.extend(chunk)
            if len(data) > 65_536:
                fail("private_env_too_large")
        after = os.fstat(descriptor)
        published = os.stat(ENV_PATH, follow_symlinks=False)
        fields = ("st_mode", "st_uid", "st_gid", "st_nlink", "st_size", "st_dev", "st_ino", "st_mtime_ns", "st_ctime_ns")
        if any(
            getattr(opened, field) != getattr(after, field)
            or getattr(after, field) != getattr(published, field)
            for field in fields
        ):
            fail("private_env_changed_during_read")
    finally:
        os.close(descriptor)
    try:
        text = data.decode("ascii")
    except UnicodeDecodeError:
        fail("private_env_invalid_encoding")
    values: dict[str, str] = {}
    for line in text.splitlines():
        if not line or line.startswith("#") or "=" not in line:
            fail("private_env_invalid_line")
        key, value = line.split("=", 1)
        if key not in ALL_KEYS or key in values or not value:
            fail("private_env_invalid_schema")
        values[key] = value
    validate(values)
    return values


def validate(values: dict[str, str]) -> None:
    if any(key not in values for key in SECRET_KEYS):
        fail("private_env_missing_secret")
    for key in ("POSTGRES_PASSWORD", "CLICKHOUSE_PASSWORD", "SANDBOX_ACCESS_TOKEN_HASH_SEED", "ADMIN_TOKEN"):
        if not HEX64.fullmatch(values[key]):
            fail("private_env_invalid_secret")
    if values["CLICKHOUSE_USER"] != "kitdev":
        fail("private_env_invalid_clickhouse_user")
    postgres_password = quote(values["POSTGRES_PASSWORD"], safe="")
    clickhouse_password = quote(values["CLICKHOUSE_PASSWORD"], safe="")
    expected_postgres = f"postgres://kitdev:{postgres_password}@postgres:5432/kitdev?sslmode=disable"
    expected_clickhouse = f"clickhouse://kitdev:{clickhouse_password}@clickhouse:9000/default"
    if values["POSTGRES_CONNECTION_STRING"] != expected_postgres:
        fail("private_env_postgres_url_mismatch")
    if values["CLICKHOUSE_CONNECTION_STRING"] != expected_clickhouse:
        fail("private_env_clickhouse_url_mismatch")
    present_images = [key in values for key in IMAGE_KEYS]
    if any(present_images) and not all(present_images):
        fail("private_env_partial_image_refs")
    for key in IMAGE_KEYS:
        if key in values and not IMAGE_REF.fullmatch(values[key]):
            fail("private_env_invalid_image_ref")
    present_network = [key in values for key in NETWORK_KEYS]
    if any(present_network) and not all(present_network):
        fail("private_env_partial_network")
    if all(present_network):
        try:
            subnet = ipaddress.ip_network(values["KITDEV_CORE_SUBNET"], strict=True)
            gateway = ipaddress.ip_address(values["KITDEV_CORE_GATEWAY"])
        except ValueError:
            fail("private_env_invalid_network")
        if subnet.version != 4 or not subnet.is_private or gateway not in subnet or gateway in {subnet.network_address, subnet.broadcast_address}:
            fail("private_env_invalid_network")
        for reserved in (ipaddress.ip_network("10.11.0.0/16"), ipaddress.ip_network("10.12.0.0/16")):
            if subnet.overlaps(reserved):
                fail("private_env_network_overlap")


def write_document(values: dict[str, str], *, replace: bool) -> None:
    validate(values)
    ENV_PATH.parent.mkdir(mode=0o700, parents=False, exist_ok=True)
    require_parent()
    payload = "".join(f"{key}={values[key]}\n" for key in ALL_KEYS if key in values).encode("ascii")
    descriptor, name = tempfile.mkstemp(prefix=".control-plane.env.", dir=ENV_PATH.parent)
    temporary = Path(name)
    try:
        os.fchmod(descriptor, 0o600)
        view = memoryview(payload)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                fail("private_env_write_failed")
            view = view[written:]
        os.fsync(descriptor)
        os.close(descriptor)
        descriptor = -1
        if replace:
            os.replace(temporary, ENV_PATH)
        else:
            try:
                os.link(temporary, ENV_PATH, follow_symlinks=False)
            except FileExistsError:
                fail("private_env_concurrent_creation")
            temporary.unlink()
        parent = os.open(ENV_PATH.parent, os.O_RDONLY | os.O_DIRECTORY)
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


def bootstrap() -> None:
    existing = read_document()
    if existing is not None:
        print("status=pass operation=bootstrap-private-env result=unchanged")
        return
    postgres_password = secrets.token_hex(32)
    clickhouse_password = secrets.token_hex(32)
    values = {
        "POSTGRES_PASSWORD": postgres_password,
        "POSTGRES_CONNECTION_STRING": f"postgres://kitdev:{quote(postgres_password, safe='')}@postgres:5432/kitdev?sslmode=disable",
        "CLICKHOUSE_USER": "kitdev",
        "CLICKHOUSE_PASSWORD": clickhouse_password,
        "CLICKHOUSE_CONNECTION_STRING": f"clickhouse://kitdev:{quote(clickhouse_password, safe='')}@clickhouse:9000/default",
        "SANDBOX_ACCESS_TOKEN_HASH_SEED": secrets.token_hex(32),
        "ADMIN_TOKEN": secrets.token_hex(32),
    }
    write_document(values, replace=False)
    read_document()
    print("status=pass operation=bootstrap-private-env result=created")


def update(keys: tuple[str, ...], arguments: list[str], operation: str) -> None:
    if len(arguments) != len(keys):
        fail("invalid_arguments")
    values = read_document()
    if values is None:
        fail("private_env_missing")
    updated = dict(values)
    updated.update(zip(keys, arguments, strict=True))
    if updated != values:
        write_document(updated, replace=True)
    read_document()
    print(f"status=pass operation={operation} result={'unchanged' if updated == values else 'updated'}")


def main() -> None:
    require_root()
    if len(sys.argv) < 2:
        fail("operation_required")
    operation = sys.argv[1]
    if operation == "bootstrap" and len(sys.argv) == 2:
        bootstrap()
    elif operation == "verify" and len(sys.argv) == 2:
        if read_document() is None:
            fail("private_env_missing")
        print("status=pass operation=verify-private-env")
    elif operation == "get-network" and len(sys.argv) == 2:
        values = read_document()
        if values is None or any(key not in values for key in NETWORK_KEYS):
            fail("private_env_network_missing")
        print(values["KITDEV_CORE_SUBNET"])
        print(values["KITDEV_CORE_GATEWAY"])
    elif operation == "get-images" and len(sys.argv) == 2:
        values = read_document()
        if values is None or any(key not in values for key in IMAGE_KEYS):
            fail("private_env_images_missing")
        for key in IMAGE_KEYS:
            print(values[key])
    elif operation == "set-images":
        update(IMAGE_KEYS, sys.argv[2:], operation)
    elif operation == "set-network":
        update(NETWORK_KEYS, sys.argv[2:], operation)
    else:
        fail("invalid_operation")


if __name__ == "__main__":
    main()
