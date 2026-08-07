#!/usr/bin/env python3
"""Build and validate the first offline backup format.

This helper deliberately has no dependency outside the Python standard library.
The shell coordinator owns service lifecycle; this module owns structured data,
archive/path validation, and atomic manifest publication.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import re
import stat
import sys
import tarfile
import tempfile
from collections.abc import Sequence
from contextlib import suppress
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import NoReturn

SCHEMA_VERSION = 1
FORMAT = "kitdev-offline-physical-v1"
UPSTREAM_INFRA_COMMIT = "882a3b4786755db9e94be3297de6827f9100ce5e"
PRIVATE_ENV_PATH = "/etc/kitdev-sandboxes/control-plane.env"
BACKUP_ID = re.compile(r"[0-9]{8}T[0-9]{6}Z-[0-9a-f]{16}")
SHA256 = re.compile(r"[0-9a-f]{64}")
UTC_TIMESTAMP = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z")
COMPONENTS = {
    "clickhouse": "data/clickhouse",
    "loki": "data/loki",
    "postgres": "data/postgres",
    "redis": "data/redis",
    "template-storage": "data/runtime/orchestrator/template-storage",
}
MAX_MANIFEST_BYTES = 65_536
MAX_ARCHIVE_MEMBERS = 2_000_000


class BackupError(ValueError):
    """A stable, non-secret backup validation failure."""


def fail(reason: str, code: int = 65) -> NoReturn:
    print(f"status=error reason={reason}", file=sys.stderr)
    raise SystemExit(code)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb", buffering=0) as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def fingerprint_tree(path: Path) -> tuple[str, int]:
    """Hash regular-file content plus restore-relevant tree metadata."""

    validate_source_tree(path)
    root = path.resolve(strict=True)
    digest = hashlib.sha256()
    entries = 0
    pending = [(root, PurePosixPath("."))]
    while pending:
        current, relative = pending.pop()
        metadata = current.lstat()
        kind = b"directory" if stat.S_ISDIR(metadata.st_mode) else b"file"
        fields = (
            kind,
            os.fsencode(relative.as_posix()),
            str(stat.S_IMODE(metadata.st_mode)).encode("ascii"),
            str(metadata.st_uid).encode("ascii"),
            str(metadata.st_gid).encode("ascii"),
            str(metadata.st_nlink).encode("ascii"),
            str(metadata.st_size if stat.S_ISREG(metadata.st_mode) else 0).encode("ascii"),
            sha256_file(current).encode("ascii") if stat.S_ISREG(metadata.st_mode) else b"",
        )
        for field in fields:
            digest.update(len(field).to_bytes(8, "big"))
            digest.update(field)
        entries += 1
        if stat.S_ISDIR(metadata.st_mode):
            children = sorted(Path(entry.path) for entry in os.scandir(current))
            for child in reversed(children):
                pending.append((child, relative / child.name))
    return digest.hexdigest(), entries


def _decode_mount_path(value: str) -> Path:
    for encoded, decoded in (("\\040", " "), ("\\011", "\t"), ("\\012", "\n"), ("\\134", "\\")):
        value = value.replace(encoded, decoded)
    return Path(value)


def mount_points() -> tuple[Path, ...]:
    points: list[Path] = []
    try:
        lines = Path("/proc/self/mountinfo").read_text(encoding="ascii").splitlines()
    except (FileNotFoundError, UnicodeDecodeError, OSError):
        return ()
    if len(lines) > 16_384:
        raise BackupError("mount_table_too_large")
    for line in lines:
        fields = line.split(" ")
        if len(fields) < 10 or "-" not in fields:
            raise BackupError("mount_table_invalid")
        points.append(_decode_mount_path(fields[4]))
    return tuple(points)


def validate_source_tree(path: Path) -> None:
    """Reject links, special files, and nested mount boundaries before tar runs."""

    root = path.resolve(strict=True)
    if Path(os.path.abspath(path)) != root or path.is_symlink() or not root.is_dir():
        raise BackupError("source_root_invalid")
    root_device = root.stat().st_dev
    mounts = mount_points()
    for mount in mounts:
        if mount != root and root in mount.parents:
            raise BackupError("source_nested_mount")
    pending = [root]
    while pending:
        directory = pending.pop()
        with os.scandir(directory) as entries:
            for entry in entries:
                metadata = entry.stat(follow_symlinks=False)
                mode = metadata.st_mode
                if metadata.st_dev != root_device:
                    raise BackupError("source_mount_boundary")
                if stat.S_ISLNK(mode):
                    raise BackupError("source_symlink_forbidden")
                if stat.S_ISDIR(mode):
                    pending.append(Path(entry.path))
                elif not stat.S_ISREG(mode):
                    raise BackupError("source_special_file_forbidden")


def _safe_member_name(value: str, expected_root: PurePosixPath) -> PurePosixPath:
    if not value or value.startswith("/") or "\x00" in value:
        raise BackupError("archive_path_invalid")
    name = PurePosixPath(value)
    if any(part in {"", ".", ".."} for part in name.parts):
        raise BackupError("archive_path_invalid")
    if name != expected_root and expected_root not in name.parents:
        raise BackupError("archive_path_escape")
    return name


def validate_archive(path: Path, expected_relative_path: str) -> int:
    """Validate member types and confinement without extracting the archive."""

    expected = PurePosixPath(expected_relative_path)
    count = 0
    roots = 0
    try:
        with tarfile.open(path, mode="r:") as archive:
            for member in archive:
                count += 1
                if count > MAX_ARCHIVE_MEMBERS:
                    raise BackupError("archive_member_limit")
                name = _safe_member_name(member.name, expected)
                if name == expected:
                    roots += 1
                if member.issym() or member.ischr() or member.isblk() or member.isfifo():
                    raise BackupError("archive_special_member_forbidden")
                if member.islnk():
                    _safe_member_name(member.linkname, expected)
                elif not (member.isdir() or member.isreg()):
                    raise BackupError("archive_member_type_invalid")
    except (OSError, tarfile.TarError) as error:
        raise BackupError("archive_unreadable") from error
    if count == 0 or roots != 1:
        raise BackupError("archive_root_invalid")
    return count


def _canonical_json(document: object) -> bytes:
    return (json.dumps(document, indent=2, sort_keys=True) + "\n").encode("ascii")


def _atomic_write(path: Path, payload: bytes) -> None:
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "wb", closefd=True) as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        parent = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(parent)
        finally:
            os.close(parent)
    finally:
        with suppress(FileNotFoundError):
            temporary.unlink()


def build_manifest(
    backup_dir: Path,
    backup_id: str,
    service_state: str,
    compose_path: Path,
    images_lock_path: Path,
    private_env_path: Path,
    state_root: Path,
) -> dict[str, object]:
    if not BACKUP_ID.fullmatch(backup_id):
        raise BackupError("backup_id_invalid")
    if service_state not in {"running", "stopped"}:
        raise BackupError("service_state_invalid")
    components: list[dict[str, object]] = []
    for name, relative_path in sorted(COMPONENTS.items()):
        archive_name = f"{name}.tar"
        archive_path = backup_dir / archive_name
        if archive_path.is_symlink() or not archive_path.is_file():
            raise BackupError("archive_missing")
        members = validate_archive(archive_path, relative_path)
        tree_sha256, tree_entries = fingerprint_tree(state_root / relative_path)
        components.append(
            {
                "archive": archive_name,
                "members": members,
                "name": name,
                "path": relative_path,
                "sha256": sha256_file(archive_path),
                "size_bytes": archive_path.stat().st_size,
                "tree_entries": tree_entries,
                "tree_sha256": tree_sha256,
            }
        )
    document: dict[str, object] = {
        "backup_id": backup_id,
        "compatibility": {
            "architecture": platform.machine(),
            "compose_sha256": sha256_file(compose_path),
            "images_lock_sha256": sha256_file(images_lock_path),
            "private_env_sha256": sha256_file(private_env_path),
            "upstream_infra_commit": UPSTREAM_INFRA_COMMIT,
        },
        "components": components,
        "created_at": datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "format": FORMAT,
        "schema_version": SCHEMA_VERSION,
        "secrets": {
            "included": False,
            "policy": "external-encrypted-backup-or-reissue",
            "required_path": PRIVATE_ENV_PATH,
        },
        "service_state_before": service_state,
        "status": "complete",
    }
    _atomic_write(backup_dir / "manifest.json", _canonical_json(document))
    return document


def load_manifest(path: Path) -> dict[str, object]:
    if path.is_symlink() or not path.is_file() or path.stat().st_size > MAX_MANIFEST_BYTES:
        raise BackupError("manifest_metadata_invalid")
    try:
        document = json.loads(path.read_text(encoding="ascii"))
    except (UnicodeDecodeError, OSError, json.JSONDecodeError) as error:
        raise BackupError("manifest_invalid") from error
    if not isinstance(document, dict):
        raise BackupError("manifest_invalid")
    return document


def validate_manifest(
    backup_dir: Path,
    compose_path: Path,
    images_lock_path: Path,
    private_env_path: Path,
) -> dict[str, object]:
    document = load_manifest(backup_dir / "manifest.json")
    if (
        set(document)
        != {
            "backup_id",
            "compatibility",
            "components",
            "created_at",
            "format",
            "schema_version",
            "secrets",
            "service_state_before",
            "status",
        }
        or document.get("backup_id") != backup_dir.name
        or document.get("schema_version") != SCHEMA_VERSION
        or document.get("format") != FORMAT
        or document.get("status") != "complete"
        or not isinstance(document.get("backup_id"), str)
        or not BACKUP_ID.fullmatch(str(document["backup_id"]))
        or not isinstance(document.get("created_at"), str)
        or not UTC_TIMESTAMP.fullmatch(str(document["created_at"]))
        or document.get("service_state_before") not in {"running", "stopped"}
    ):
        raise BackupError("manifest_contract_invalid")
    compatibility = document.get("compatibility")
    expected_compatibility = {
        "architecture": platform.machine(),
        "compose_sha256": sha256_file(compose_path),
        "images_lock_sha256": sha256_file(images_lock_path),
        "private_env_sha256": sha256_file(private_env_path),
        "upstream_infra_commit": UPSTREAM_INFRA_COMMIT,
    }
    if compatibility != expected_compatibility:
        raise BackupError("backup_release_incompatible")
    if document.get("secrets") != {
        "included": False,
        "policy": "external-encrypted-backup-or-reissue",
        "required_path": PRIVATE_ENV_PATH,
    }:
        raise BackupError("secret_policy_invalid")
    raw_components = document.get("components")
    if not isinstance(raw_components, list) or len(raw_components) != len(COMPONENTS):
        raise BackupError("component_set_invalid")
    expected_files = {"manifest.json"}
    observed_names: set[str] = set()
    for component in raw_components:
        if not isinstance(component, dict):
            raise BackupError("component_invalid")
        name = component.get("name")
        if not isinstance(name, str) or name not in COMPONENTS or name in observed_names:
            raise BackupError("component_invalid")
        observed_names.add(name)
        archive_name = f"{name}.tar"
        expected_files.add(archive_name)
        if (
            set(component)
            != {
                "archive",
                "members",
                "name",
                "path",
                "sha256",
                "size_bytes",
                "tree_entries",
                "tree_sha256",
            }
            or component.get("archive") != archive_name
            or component.get("path") != COMPONENTS[name]
            or not isinstance(component.get("size_bytes"), int)
            or not isinstance(component.get("members"), int)
            or not isinstance(component.get("tree_entries"), int)
            or component["members"] < 1
            or component["tree_entries"] < 1
            or not isinstance(component.get("tree_sha256"), str)
            or not SHA256.fullmatch(str(component["tree_sha256"]))
            or not isinstance(component.get("sha256"), str)
            or not SHA256.fullmatch(str(component["sha256"]))
        ):
            raise BackupError("component_invalid")
        archive_path = backup_dir / archive_name
        if archive_path.is_symlink() or not archive_path.is_file():
            raise BackupError("archive_metadata_invalid")
        if archive_path.stat().st_size != component["size_bytes"]:
            raise BackupError("archive_size_mismatch")
        if sha256_file(archive_path) != component["sha256"]:
            raise BackupError("archive_hash_mismatch")
        if validate_archive(archive_path, COMPONENTS[name]) != component["members"]:
            raise BackupError("archive_member_count_mismatch")
    if observed_names != set(COMPONENTS):
        raise BackupError("component_set_invalid")
    observed_files = {entry.name for entry in os.scandir(backup_dir)}
    if observed_files != expected_files:
        raise BackupError("backup_directory_entries_invalid")
    return document


def validate_component_tree(manifest_path: Path, name: str, path: Path) -> None:
    document = load_manifest(manifest_path)
    components = document.get("components")
    if not isinstance(components, list):
        raise BackupError("component_set_invalid")
    matches = [item for item in components if isinstance(item, dict) and item.get("name") == name]
    if len(matches) != 1:
        raise BackupError("component_invalid")
    expected = matches[0]
    digest, entries = fingerprint_tree(path)
    if digest != expected.get("tree_sha256") or entries != expected.get("tree_entries"):
        raise BackupError("component_tree_mismatch")


def parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest="command", required=True)
    source = commands.add_parser("validate-source")
    source.add_argument("paths", nargs="+")
    archive = commands.add_parser("validate-archive")
    archive.add_argument("archive", type=Path)
    archive.add_argument("relative_path")
    build = commands.add_parser("build-manifest")
    build.add_argument("backup_dir", type=Path)
    build.add_argument("backup_id")
    build.add_argument("service_state", choices=("running", "stopped"))
    build.add_argument("compose", type=Path)
    build.add_argument("images_lock", type=Path)
    build.add_argument("private_env", type=Path)
    build.add_argument("state_root", type=Path)
    validate = commands.add_parser("validate-manifest")
    validate.add_argument("backup_dir", type=Path)
    validate.add_argument("compose", type=Path)
    validate.add_argument("images_lock", type=Path)
    validate.add_argument("private_env", type=Path)
    tree = commands.add_parser("validate-tree")
    tree.add_argument("manifest", type=Path)
    tree.add_argument("component")
    tree.add_argument("path", type=Path)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    arguments = parse_args(sys.argv[1:] if argv is None else argv)
    try:
        if arguments.command == "validate-source":
            for value in arguments.paths:
                validate_source_tree(Path(value))
        elif arguments.command == "validate-archive":
            validate_archive(arguments.archive, arguments.relative_path)
        elif arguments.command == "build-manifest":
            build_manifest(
                arguments.backup_dir,
                arguments.backup_id,
                arguments.service_state,
                arguments.compose,
                arguments.images_lock,
                arguments.private_env,
                arguments.state_root,
            )
        elif arguments.command == "validate-manifest":
            validate_manifest(
                arguments.backup_dir,
                arguments.compose,
                arguments.images_lock,
                arguments.private_env,
            )
        else:
            validate_component_tree(arguments.manifest, arguments.component, arguments.path)
    except BackupError as error:
        fail(str(error))
    print(f"status=pass operation={arguments.command}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
