"""Crash-consistent disposable-lab authorization reconciler for OVH Stage 05."""

from __future__ import annotations

import ctypes
import hashlib
import json
import os
import re
import stat
import subprocess
import sys
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from kitdev_sandboxes.journal import (
    JournalConflict,
    JournalCorrupt,
    JournalRecord,
    JournalSecurityError,
    JournalSecurityPolicy,
    JournalState,
    JournalStore,
    ResourceRecord,
)


INSTALL_ID = "ovh-lab-stage05-v1"
PLAN_ID = "ovh-lab-stage05-marker-workspace-v1"
STATE_ROOT = Path("/var/lib/kitdev-sandboxes")
JOURNAL_ROOT = STATE_ROOT / "journal"
CONFIG_ROOT = Path("/etc/kitdev-sandboxes")
MARKER_PATH = CONFIG_ROOT / "disposable-ovh-lab"
EXPERIMENTS_ROOT = STATE_ROOT / "experiments"
WORKSPACE = EXPERIMENTS_ROOT / "ovh-lab"
JOURNAL_NAME = f"{INSTALL_ID}.journal.json"
JOURNAL_PATH = JOURNAL_ROOT / JOURNAL_NAME
_MARKER_TEMP_RE = re.compile(r"^\.disposable-ovh-lab\.tmp\.[0-9a-f]{32}$")
_JOURNAL_TEMP_RE = re.compile(
    rf"^\.{re.escape(JOURNAL_NAME)}\.tmp\.[0-9a-f]{{32}}$"
)
_HASH_RE = re.compile(r"^[0-9a-f]{64}$")
_MAX_ENTRIES = 4_096
_MAX_ENTRY_BYTES = 1_048_576
_MAX_MARKER_BYTES = 4_096
_MAX_PLAN_BYTES = 65_536
_MAX_SYSTEMD_BYTES = 4_096
_PRODUCTION_PATHS = (
    Path("/etc/kitdev-sandboxes/production"),
    Path("/var/lib/kitdev-sandboxes/install-manifest.json"),
    Path("/etc/kitdev-sandboxes/install-manifest.json"),
    Path("/opt/kitdev-sandboxes"),
)
_UNIT_NAMES = (
    "kitdev-e2b-api.service",
    "kitdev-e2b-client-proxy.service",
    "kitdev-e2b-orchestrator.service",
)
_UNIT_ROOTS = (
    Path("/etc/systemd/system"),
    Path("/usr/lib/systemd/system"),
    Path("/etc/systemd/system/multi-user.target.wants"),
)
_DANGEROUS_ANCESTOR_XATTRS = {
    "system.posix_acl_access",
    "system.posix_acl_default",
    "security.capability",
}


class Stage05Error(RuntimeError):
    """A fail-closed Stage 05 result with a stable public reason code."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


class Stage05Crash(BaseException):
    """Test-only abrupt interruption that bypasses caught-error transitions."""


@dataclass(frozen=True)
class Stage05Paths:
    """Fixed canonical paths mapped beneath an explicit hermetic test prefix."""

    prefix: Path = Path("/")
    trusted_prefix: Path | None = None

    def __post_init__(self) -> None:
        if not self.prefix.is_absolute() or str(self.prefix).startswith("//"):
            raise ValueError("Stage 05 prefix must be an absolute path")
        if self.prefix != Path("/") and self.trusted_prefix is None:
            raise ValueError("a non-production prefix requires an explicit trust boundary")
        if self.trusted_prefix is not None and (
            not self.trusted_prefix.is_absolute()
            or self.trusted_prefix == Path("/")
            or self.trusted_prefix not in (self.prefix, *self.prefix.parents)
        ):
            raise ValueError("trusted prefix must contain the injected Stage 05 root")

    def actual(self, canonical: Path) -> Path:
        if not canonical.is_absolute() or canonical == Path("/"):
            raise ValueError("Stage 05 canonical resource path is invalid")
        return self.prefix / canonical.relative_to("/")

    @classmethod
    def production(cls) -> Stage05Paths:
        return cls()


@dataclass(frozen=True)
class Stage05Plan:
    bundle_hash: str
    marker_bytes: bytes
    marker_hash: str
    plan_bytes: bytes
    plan_hash: str
    resources: tuple[ResourceRecord, ...]
    record: JournalRecord


@dataclass(frozen=True)
class Stage05Result:
    operation: str
    journal_root: str
    journal_state: str
    transition_count: int
    plan_hash: str
    bundle_hash: str
    marker_hash: str
    resource_state: str
    workspace_empty: bool
    retained_provenance: bool
    next_action: str

    def evidence(self) -> str:
        return (
            f"stage=05 operation={self.operation} status=pass "
            f"journal_root={self.journal_root} journal_state={self.journal_state} "
            f"transitions={self.transition_count} resource_state={self.resource_state} "
            f"workspace_empty={'yes' if self.workspace_empty else 'no'} "
            f"retained_provenance={'yes' if self.retained_provenance else 'no'} "
            f"next_action={self.next_action} plan_sha256={self.plan_hash} "
            f"bundle_sha256={self.bundle_hash} marker_sha256={self.marker_hash}"
        )


def _canonical_json(value: object, limit: int) -> bytes:
    encoded = (
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True) + "\n"
    ).encode("ascii")
    if not encoded or len(encoded) > limit:
        raise Stage05Error("illegal_recovery_state")
    return encoded


def build_plan(bundle_digest: str) -> Stage05Plan:
    if bundle_digest.__class__ is not str or not _HASH_RE.fullmatch(bundle_digest):
        raise Stage05Error("bundle_digest_required")
    bundle_hash = f"sha256:{bundle_digest}"
    marker = {
        "authorization_scope": "disposable-ovh-lab",
        "bundle_sha256": bundle_hash,
        "install_id": INSTALL_ID,
        "plan_id": PLAN_ID,
        "schema_version": 1,
    }
    marker_bytes = _canonical_json(marker, _MAX_MARKER_BYTES)
    marker_hash = "sha256:" + hashlib.sha256(marker_bytes).hexdigest()
    resources = (
        ResourceRecord(
            "directory.config",
            str(CONFIG_ROOT),
            "absent",
            "directory:uid=0:gid=0:mode=0755",
        ),
        ResourceRecord(
            "directory.experiments",
            str(EXPERIMENTS_ROOT),
            "absent",
            "directory:uid=0:gid=0:mode=0700",
        ),
        ResourceRecord(
            "directory.workspace",
            str(WORKSPACE),
            "absent",
            "directory:uid=0:gid=0:mode=0700",
        ),
        ResourceRecord(
            "file.authorization",
            str(MARKER_PATH),
            "absent",
            (
                "file:uid=0:gid=0:mode=0600:nlink=1:"
                f"sha256:{hashlib.sha256(marker_bytes).hexdigest()}:"
                f"bundle_sha256={bundle_hash}"
            ),
        ),
    )
    plan_document = {
        "bundle_sha256": bundle_hash,
        "install_id": INSTALL_ID,
        "operations": [
            "create-directory.config",
            "create-directory.experiments",
            "create-directory.workspace",
            "publish-file.authorization",
        ],
        "plan_id": PLAN_ID,
        "resources": [resource.as_dict() for resource in resources],
        "schema_version": 1,
        "stage": "05",
    }
    plan_bytes = _canonical_json(plan_document, _MAX_PLAN_BYTES)
    plan_hash = "sha256:" + hashlib.sha256(plan_bytes).hexdigest()
    return Stage05Plan(
        bundle_hash,
        marker_bytes,
        marker_hash,
        plan_bytes,
        plan_hash,
        resources,
        JournalRecord(INSTALL_ID, PLAN_ID, plan_hash, resources),
    )


class _StatxTimestamp(ctypes.Structure):
    _fields_ = (("tv_sec", ctypes.c_int64), ("tv_nsec", ctypes.c_uint32), ("reserved", ctypes.c_int32))


class _Statx(ctypes.Structure):
    _fields_ = (
        ("mask", ctypes.c_uint32),
        ("blksize", ctypes.c_uint32),
        ("attributes", ctypes.c_uint64),
        ("nlink", ctypes.c_uint32),
        ("uid", ctypes.c_uint32),
        ("gid", ctypes.c_uint32),
        ("mode", ctypes.c_uint16),
        ("spare0", ctypes.c_uint16),
        ("ino", ctypes.c_uint64),
        ("size", ctypes.c_uint64),
        ("blocks", ctypes.c_uint64),
        ("attributes_mask", ctypes.c_uint64),
        ("atime", _StatxTimestamp),
        ("btime", _StatxTimestamp),
        ("ctime", _StatxTimestamp),
        ("mtime", _StatxTimestamp),
        ("rdev_major", ctypes.c_uint32),
        ("rdev_minor", ctypes.c_uint32),
        ("dev_major", ctypes.c_uint32),
        ("dev_minor", ctypes.c_uint32),
        ("mnt_id", ctypes.c_uint64),
        ("dio_mem_align", ctypes.c_uint32),
        ("dio_offset_align", ctypes.c_uint32),
        ("spare3", ctypes.c_uint64 * 12),
    )


def linux_mount_id(descriptor: int) -> int:
    try:
        libc = ctypes.CDLL(None, use_errno=True)
        statx = libc.statx
    except (AttributeError, OSError) as error:
        raise Stage05Error("mount_boundary") from error
    metadata = _Statx()
    statx.argtypes = (
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_int,
        ctypes.c_uint,
        ctypes.POINTER(_Statx),
    )
    statx.restype = ctypes.c_int
    if statx(descriptor, b"", 0x1000 | 0x800, 0x1000, ctypes.byref(metadata)) != 0:
        raise Stage05Error("mount_boundary")
    if not metadata.mask & 0x1000:
        raise Stage05Error("mount_boundary")
    return int(metadata.mnt_id)


def _default_xattrs(descriptor: int) -> tuple[str, ...]:
    try:
        names = os.listxattr(descriptor)
    except (AttributeError, OSError) as error:
        raise Stage05Error("resource_desired_mismatch") from error
    return tuple(sorted(names))


class SecureTree:
    """Descriptor-relative fixed-path operations for the Stage 05 resources."""

    def __init__(
        self,
        paths: Stage05Paths,
        expected_uid: int,
        expected_gid: int,
        *,
        mount_id: Callable[[int], int] = linux_mount_id,
        xattrs: Callable[[int], tuple[str, ...]] = _default_xattrs,
        fault: Callable[[str], None] | None = None,
    ) -> None:
        self.paths = paths
        self.uid = expected_uid
        self.gid = expected_gid
        self.mount_id = mount_id
        self.xattrs = xattrs
        self.fault = fault or (lambda _point: None)

    def _hit(self, point: str) -> None:
        self.fault(point)

    def _actual(self, canonical: Path) -> Path:
        return self.paths.actual(canonical)

    def _below_trust(self, opened: tuple[str, ...]) -> bool:
        trusted = self.paths.trusted_prefix
        return (
            trusted is not None
            and len(opened) >= len(trusted.parts)
            and opened[: len(trusted.parts)] == trusted.parts
        )

    def _validate_ancestor(self, descriptor: int, opened: tuple[str, ...]) -> None:
        metadata = os.fstat(descriptor)
        if not stat.S_ISDIR(metadata.st_mode):
            raise Stage05Error("unsafe_ancestry")
        trusted = self.paths.trusted_prefix
        if trusted is not None and opened == trusted.parts:
            return
        owner = self.uid if self._below_trust(opened) else 0
        group = self.gid if self._below_trust(opened) else 0
        if metadata.st_uid != owner or metadata.st_gid != group or stat.S_IMODE(metadata.st_mode) & 0o022:
            raise Stage05Error("unsafe_ancestry")
        if _DANGEROUS_ANCESTOR_XATTRS.intersection(self.xattrs(descriptor)):
            raise Stage05Error("unsafe_ancestry")

    def open_directory(self, canonical: Path, mode: int | None = None) -> int:
        actual = self._actual(canonical)
        flags = os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open("/", flags)
        opened = ("/",)
        try:
            self._validate_ancestor(descriptor, opened)
            for component in actual.parts[1:]:
                next_descriptor = os.open(component, flags, dir_fd=descriptor)
                os.close(descriptor)
                descriptor = next_descriptor
                opened = (*opened, component)
                self._validate_ancestor(descriptor, opened)
            if mode is not None:
                metadata = os.fstat(descriptor)
                if (
                    metadata.st_uid != self.uid
                    or metadata.st_gid != self.gid
                    or stat.S_IMODE(metadata.st_mode) != mode
                    or self.xattrs(descriptor)
                ):
                    raise Stage05Error("resource_desired_mismatch")
            return descriptor
        except OSError as error:
            os.close(descriptor)
            raise Stage05Error("symlink_or_type_conflict") from error
        except Exception:
            os.close(descriptor)
            raise

    def _open_parent(self, canonical: Path) -> tuple[int, str]:
        return self.open_directory(canonical.parent), canonical.name

    def path_state(self, canonical: Path) -> str:
        actual = self._actual(canonical)
        flags = os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
        parent = os.open("/", flags)
        opened = ("/",)
        try:
            self._validate_ancestor(parent, opened)
            for component in actual.parts[1:-1]:
                try:
                    next_descriptor = os.open(component, flags, dir_fd=parent)
                except FileNotFoundError:
                    return "absent"
                os.close(parent)
                parent = next_descriptor
                opened = (*opened, component)
                self._validate_ancestor(parent, opened)
            try:
                metadata = os.stat(actual.name, dir_fd=parent, follow_symlinks=False)
            except FileNotFoundError:
                return "absent"
            if stat.S_ISLNK(metadata.st_mode):
                raise Stage05Error("symlink_or_type_conflict")
            return "present"
        except OSError as error:
            raise Stage05Error("unsafe_ancestry") from error
        finally:
            os.close(parent)

    def entries(self, descriptor: int) -> tuple[str, ...]:
        entries: list[str] = []
        total = 0
        try:
            with os.scandir(descriptor) as iterator:
                for directory_entry in iterator:
                    entry = directory_entry.name
                    try:
                        encoded = entry.encode("utf-8", errors="strict")
                    except UnicodeEncodeError as error:
                        raise Stage05Error("unexpected_entry") from error
                    total += len(encoded)
                    entries.append(entry)
                    if (
                        len(entries) > _MAX_ENTRIES
                        or total > _MAX_ENTRY_BYTES
                        or entry in {"", ".", ".."}
                        or len(encoded) > 255
                        or any(
                            ord(character) < 32 or ord(character) == 127
                            for character in entry
                        )
                        or any(
                            unicodedata.category(character) == "Cf"
                            for character in entry
                        )
                    ):
                        raise Stage05Error("unexpected_entry")
        except OSError as error:
            raise Stage05Error("unexpected_entry") from error
        return tuple(sorted(entries))

    def create_directory(self, canonical: Path, mode: int, point: str) -> None:
        parent, name = self._open_parent(canonical)
        descriptor: int | None = None
        try:
            self._hit(f"before_create_{point}")
            os.mkdir(name, 0o700, dir_fd=parent)
            self._hit(f"after_mkdir_{point}")
            flags = os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
            descriptor = os.open(name, flags, dir_fd=parent)
            metadata = os.fstat(descriptor)
            if (
                metadata.st_uid != self.uid
                or metadata.st_gid != self.gid
                or stat.S_IMODE(metadata.st_mode) != 0o700
                or self.xattrs(descriptor)
                or self.mount_id(descriptor) != self.mount_id(parent)
            ):
                raise Stage05Error("mount_boundary")
            if mode != 0o700:
                self._hit(f"before_fchmod_{point}")
                os.fchmod(descriptor, mode)
                self._hit(f"after_fchmod_{point}")
            self._hit(f"before_directory_fsync_{point}")
            os.fsync(descriptor)
            self._hit(f"after_directory_fsync_{point}")
            self._hit(f"before_parent_fsync_{point}")
            os.fsync(parent)
            self._hit(f"after_parent_fsync_{point}")
            self._hit(f"after_create_{point}")
            self.validate_directory(canonical, mode, ())
        except FileExistsError as error:
            raise Stage05Error("resource_prior_mismatch") from error
        finally:
            if descriptor is not None:
                os.close(descriptor)
            os.close(parent)

    def validate_directory(
        self, canonical: Path, mode: int, allowed_entries: tuple[str, ...] | None
    ) -> tuple[str, ...]:
        parent, name = self._open_parent(canonical)
        descriptor: int | None = None
        try:
            descriptor = self.open_directory(canonical, mode)
            opened = os.fstat(descriptor)
            published = os.stat(name, dir_fd=parent, follow_symlinks=False)
            if (
                (opened.st_dev, opened.st_ino) != (published.st_dev, published.st_ino)
                or self.mount_id(descriptor) != self.mount_id(parent)
            ):
                raise Stage05Error("mount_boundary")
            entries = self.entries(descriptor)
            if allowed_entries is not None and entries != tuple(sorted(allowed_entries)):
                raise Stage05Error("unexpected_entry")
            if self.xattrs(descriptor):
                raise Stage05Error("resource_desired_mismatch")
            return entries
        finally:
            if descriptor is not None:
                os.close(descriptor)
            os.close(parent)

    def sync_directory(
        self,
        canonical: Path,
        mode: int,
        allowed_entries: tuple[str, ...] | None,
        point: str,
    ) -> None:
        parent, name = self._open_parent(canonical)
        descriptor: int | None = None
        try:
            descriptor = self.open_directory(canonical, mode)
            before = os.fstat(descriptor)
            published = os.stat(name, dir_fd=parent, follow_symlinks=False)
            if (
                (before.st_dev, before.st_ino) != (published.st_dev, published.st_ino)
                or self.mount_id(descriptor) != self.mount_id(parent)
                or (
                    allowed_entries is not None
                    and self.entries(descriptor) != tuple(sorted(allowed_entries))
                )
            ):
                raise Stage05Error("illegal_recovery_state")
            self._hit(f"before_recovery_directory_fsync_{point}")
            os.fsync(descriptor)
            self._hit(f"after_recovery_directory_fsync_{point}")
            self._hit(f"before_recovery_parent_fsync_{point}")
            os.fsync(parent)
            self._hit(f"after_recovery_parent_fsync_{point}")
            after = os.fstat(descriptor)
            republished = os.stat(name, dir_fd=parent, follow_symlinks=False)
            if (
                (after.st_dev, after.st_ino) != (before.st_dev, before.st_ino)
                or (republished.st_dev, republished.st_ino)
                != (before.st_dev, before.st_ino)
                or stat.S_IMODE(after.st_mode) != mode
                or after.st_uid != self.uid
                or after.st_gid != self.gid
                or self.xattrs(descriptor)
            ):
                raise Stage05Error("illegal_recovery_state")
        finally:
            if descriptor is not None:
                os.close(descriptor)
            os.close(parent)

    def sync_marker(self, expected: bytes) -> None:
        self.read_file(MARKER_PATH, expected)
        parent, name = self._open_parent(MARKER_PATH)
        descriptor: int | None = None
        try:
            flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
            descriptor = os.open(name, flags, dir_fd=parent)
            before = os.fstat(descriptor)
            published = os.stat(name, dir_fd=parent, follow_symlinks=False)
            if (
                (before.st_dev, before.st_ino) != (published.st_dev, published.st_ino)
                or self.mount_id(descriptor) != self.mount_id(parent)
            ):
                raise Stage05Error("marker_content_mismatch")
            self._hit("before_recovery_file_fsync_marker")
            os.fsync(descriptor)
            self._hit("after_recovery_file_fsync_marker")
            self._hit("before_recovery_parent_fsync_marker")
            os.fsync(parent)
            self._hit("after_recovery_parent_fsync_marker")
            after = os.fstat(descriptor)
            republished = os.stat(name, dir_fd=parent, follow_symlinks=False)
            if (
                (after.st_dev, after.st_ino) != (before.st_dev, before.st_ino)
                or (republished.st_dev, republished.st_ino)
                != (before.st_dev, before.st_ino)
            ):
                raise Stage05Error("marker_content_mismatch")
        finally:
            if descriptor is not None:
                os.close(descriptor)
            os.close(parent)
        self.read_file(MARKER_PATH, expected)

    def recover_provisional_directory(
        self,
        canonical: Path,
        final_mode: int,
        allowed_entries: tuple[str, ...],
        point: str,
    ) -> None:
        parent, name = self._open_parent(canonical)
        descriptor: int | None = None
        try:
            descriptor = self.open_directory(canonical)
            before = os.fstat(descriptor)
            published = os.stat(name, dir_fd=parent, follow_symlinks=False)
            if (
                before.st_uid != self.uid
                or before.st_gid != self.gid
                or stat.S_IMODE(before.st_mode) != 0o700
                or before.st_nlink != 2
                or self.xattrs(descriptor)
                or self.entries(descriptor) != tuple(sorted(allowed_entries))
                or self.mount_id(descriptor) != self.mount_id(parent)
                or (before.st_dev, before.st_ino)
                != (published.st_dev, published.st_ino)
            ):
                raise Stage05Error("illegal_recovery_state")
            self._hit(f"before_recover_fchmod_{point}")
            os.fchmod(descriptor, final_mode)
            os.fsync(descriptor)
            os.fsync(parent)
            self._hit(f"after_recover_fchmod_{point}")
            after = os.fstat(descriptor)
            republished = os.stat(name, dir_fd=parent, follow_symlinks=False)
            if (
                after.st_uid != self.uid
                or after.st_gid != self.gid
                or stat.S_IMODE(after.st_mode) != final_mode
                or after.st_nlink != 2
                or self.xattrs(descriptor)
                or self.entries(descriptor) != tuple(sorted(allowed_entries))
                or self.mount_id(descriptor) != self.mount_id(parent)
                or (after.st_dev, after.st_ino) != (before.st_dev, before.st_ino)
                or (republished.st_dev, republished.st_ino)
                != (before.st_dev, before.st_ino)
            ):
                raise Stage05Error("illegal_recovery_state")
        finally:
            if descriptor is not None:
                os.close(descriptor)
            os.close(parent)

    def validate_provisional_directory(
        self, canonical: Path, allowed_entries: tuple[str, ...]
    ) -> None:
        parent, name = self._open_parent(canonical)
        descriptor: int | None = None
        try:
            descriptor = self.open_directory(canonical)
            before = os.fstat(descriptor)
            published = os.stat(name, dir_fd=parent, follow_symlinks=False)
            if (
                before.st_uid != self.uid
                or before.st_gid != self.gid
                or stat.S_IMODE(before.st_mode) != 0o700
                or before.st_nlink != 2
                or self.xattrs(descriptor)
                or self.entries(descriptor) != tuple(sorted(allowed_entries))
                or self.mount_id(descriptor) != self.mount_id(parent)
                or (before.st_dev, before.st_ino)
                != (published.st_dev, published.st_ino)
            ):
                raise Stage05Error("illegal_recovery_state")
        finally:
            if descriptor is not None:
                os.close(descriptor)
            os.close(parent)

    def read_file(self, canonical: Path, expected: bytes) -> None:
        parent, name = self._open_parent(canonical)
        descriptor: int | None = None
        try:
            flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
            descriptor = os.open(name, flags, dir_fd=parent)
            before = os.fstat(descriptor)
            initial_xattrs = self.xattrs(descriptor)
            if (
                not stat.S_ISREG(before.st_mode)
                or before.st_uid != self.uid
                or before.st_gid != self.gid
                or stat.S_IMODE(before.st_mode) != 0o600
                or before.st_nlink != 1
                or initial_xattrs
                or self.mount_id(descriptor) != self.mount_id(parent)
            ):
                raise Stage05Error("marker_content_mismatch")
            chunks: list[bytes] = []
            remaining = _MAX_MARKER_BYTES + 1
            while remaining:
                chunk = os.read(descriptor, min(4096, remaining))
                if not chunk:
                    break
                chunks.append(chunk)
                remaining -= len(chunk)
            after = os.fstat(descriptor)
            published = os.stat(name, dir_fd=parent, follow_symlinks=False)
            stable = (
                "st_mode",
                "st_uid",
                "st_gid",
                "st_nlink",
                "st_size",
                "st_dev",
                "st_ino",
                "st_mtime_ns",
                "st_ctime_ns",
            )
            if any(
                getattr(before, item) != getattr(after, item)
                or getattr(after, item) != getattr(published, item)
                for item in stable
            ) or self.xattrs(descriptor) != initial_xattrs:
                raise Stage05Error("marker_content_mismatch")
            if b"".join(chunks) != expected:
                raise Stage05Error("marker_content_mismatch")
        except FileNotFoundError as error:
            raise Stage05Error("resource_desired_mismatch") from error
        except OSError as error:
            raise Stage05Error("symlink_or_type_conflict") from error
        finally:
            if descriptor is not None:
                os.close(descriptor)
            os.close(parent)

    def publish_marker(self, content: bytes) -> None:
        parent = self.open_directory(CONFIG_ROOT, 0o755)
        temp_name = f".disposable-ovh-lab.tmp.{os.urandom(16).hex()}"
        descriptor: int | None = None
        try:
            self._recover_marker_temp(parent, content)
            flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
            self._hit("before_write_marker")
            descriptor = os.open(temp_name, flags, 0o600, dir_fd=parent)
            offset = 0
            while offset < len(content):
                written = os.write(descriptor, content[offset:])
                if written <= 0:
                    raise Stage05Error("marker_content_mismatch")
                offset += written
            self._hit("after_write_marker")
            self._hit("before_file_fsync_marker")
            os.fsync(descriptor)
            self._hit("after_file_fsync_marker")
            metadata = os.fstat(descriptor)
            if (
                not stat.S_ISREG(metadata.st_mode)
                or metadata.st_uid != self.uid
                or metadata.st_gid != self.gid
                or stat.S_IMODE(metadata.st_mode) != 0o600
                or metadata.st_nlink != 1
                or self.xattrs(descriptor)
            ):
                raise Stage05Error("marker_content_mismatch")
            os.close(descriptor)
            descriptor = None
            self._hit("before_publish_marker")
            os.link(temp_name, MARKER_PATH.name, src_dir_fd=parent, dst_dir_fd=parent, follow_symlinks=False)
            self._hit("after_link_marker")
            self._hit("before_publish_parent_fsync_marker")
            os.fsync(parent)
            self._hit("after_publish_parent_fsync_marker")
            self._hit("before_temp_unlink_marker")
            os.unlink(temp_name, dir_fd=parent)
            self._hit("after_temp_unlink_marker")
            self._hit("before_cleanup_parent_fsync_marker")
            os.fsync(parent)
            self._hit("after_cleanup_parent_fsync_marker")
            self.read_file(MARKER_PATH, content)
        except FileExistsError as error:
            raise Stage05Error("resource_prior_mismatch") from error
        except Exception:
            try:
                os.unlink(temp_name, dir_fd=parent)
                os.fsync(parent)
            except OSError:
                pass
            raise
        finally:
            if descriptor is not None:
                os.close(descriptor)
            os.close(parent)

    def _recover_marker_temp(self, parent: int, expected: bytes) -> None:
        candidates = [name for name in self.entries(parent) if name.startswith(".disposable-ovh-lab.tmp.")]
        if not candidates:
            return
        if len(candidates) != 1 or not _MARKER_TEMP_RE.fullmatch(candidates[0]):
            raise Stage05Error("unexpected_entry")
        candidate = candidates[0]
        marker_exists = True
        try:
            marker_stat = os.stat(MARKER_PATH.name, dir_fd=parent, follow_symlinks=False)
        except FileNotFoundError:
            marker_exists = False
            marker_stat = None
        self._read_named_marker(
            parent,
            candidate,
            expected,
            frozenset({2}) if marker_exists else frozenset({1}),
        )
        temp_stat = os.stat(candidate, dir_fd=parent, follow_symlinks=False)
        if marker_exists and (marker_stat.st_dev, marker_stat.st_ino) != (temp_stat.st_dev, temp_stat.st_ino):
            raise Stage05Error("marker_content_mismatch")
        os.unlink(candidate, dir_fd=parent)
        os.fsync(parent)

    def recover_marker_residue(self, expected: bytes) -> None:
        if self.path_state(CONFIG_ROOT) != "present":
            return
        parent = self.open_directory(CONFIG_ROOT)
        try:
            self._recover_marker_temp(parent, expected)
        finally:
            os.close(parent)

    def validate_marker_residue(self, expected: bytes) -> str | None:
        if self.path_state(CONFIG_ROOT) != "present":
            return None
        parent = self.open_directory(CONFIG_ROOT)
        try:
            candidates = [
                name
                for name in self.entries(parent)
                if name.startswith(".disposable-ovh-lab.tmp.")
            ]
            if not candidates:
                return None
            if len(candidates) != 1 or not _MARKER_TEMP_RE.fullmatch(candidates[0]):
                raise Stage05Error("unexpected_entry")
            candidate = candidates[0]
            self._read_named_marker(parent, candidate, expected, frozenset({1, 2}))
            temp_stat = os.stat(candidate, dir_fd=parent, follow_symlinks=False)
            try:
                marker_stat = os.stat(
                    MARKER_PATH.name, dir_fd=parent, follow_symlinks=False
                )
            except FileNotFoundError:
                if temp_stat.st_nlink != 1:
                    raise Stage05Error("marker_content_mismatch")
            else:
                if (
                    temp_stat.st_nlink != 2
                    or (marker_stat.st_dev, marker_stat.st_ino)
                    != (temp_stat.st_dev, temp_stat.st_ino)
                ):
                    raise Stage05Error("marker_content_mismatch")
            return candidate
        finally:
            os.close(parent)

    def _read_named_marker(self, parent: int, name: str, expected: bytes, links: frozenset[int]) -> None:
        flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(name, flags, dir_fd=parent)
        try:
            before = os.fstat(descriptor)
            initial_xattrs = self.xattrs(descriptor)
            if (
                not stat.S_ISREG(before.st_mode)
                or before.st_uid != self.uid
                or before.st_gid != self.gid
                or stat.S_IMODE(before.st_mode) != 0o600
                or before.st_nlink not in links
                or before.st_size > _MAX_MARKER_BYTES
                or initial_xattrs
                or self.mount_id(descriptor) != self.mount_id(parent)
            ):
                raise Stage05Error("marker_content_mismatch")
            chunks: list[bytes] = []
            remaining = _MAX_MARKER_BYTES + 1
            while remaining:
                chunk = os.read(descriptor, min(4096, remaining))
                if not chunk:
                    break
                chunks.append(chunk)
                remaining -= len(chunk)
            after = os.fstat(descriptor)
            if (
                b"".join(chunks) != expected
                or any(
                    getattr(before, item) != getattr(after, item)
                    for item in (
                        "st_mode",
                        "st_uid",
                        "st_gid",
                        "st_nlink",
                        "st_size",
                        "st_dev",
                        "st_ino",
                        "st_mtime_ns",
                        "st_ctime_ns",
                    )
                )
                or self.xattrs(descriptor) != initial_xattrs
            ):
                raise Stage05Error("marker_content_mismatch")
        finally:
            os.close(descriptor)

    def remove_marker(self, expected: bytes) -> None:
        parent, name = self._open_parent(MARKER_PATH)
        descriptor: int | None = None
        try:
            self.read_file(MARKER_PATH, expected)
            flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
            descriptor = os.open(name, flags, dir_fd=parent)
            opened = os.fstat(descriptor)
            published = os.stat(name, dir_fd=parent, follow_symlinks=False)
            if (opened.st_dev, opened.st_ino) != (published.st_dev, published.st_ino):
                raise Stage05Error("marker_content_mismatch")
            self._hit("before_remove_marker")
            os.unlink(name, dir_fd=parent)
            self._hit("after_unlink_marker")
            self._hit("before_remove_parent_fsync_marker")
            os.fsync(parent)
            self._hit("after_remove_marker")
        finally:
            if descriptor is not None:
                os.close(descriptor)
            os.close(parent)

    def remove_directory(self, canonical: Path, mode: int, point: str) -> None:
        parent, name = self._open_parent(canonical)
        descriptor: int | None = None
        try:
            descriptor = self.open_directory(canonical, mode)
            if self.entries(descriptor):
                raise Stage05Error("rollback_foreign_state")
            opened = os.fstat(descriptor)
            published = os.stat(name, dir_fd=parent, follow_symlinks=False)
            if (opened.st_dev, opened.st_ino) != (published.st_dev, published.st_ino):
                raise Stage05Error("rollback_foreign_state")
            if self.mount_id(descriptor) != self.mount_id(parent):
                raise Stage05Error("mount_boundary")
            self._hit(f"before_remove_{point}")
            os.rmdir(name, dir_fd=parent)
            self._hit(f"after_rmdir_{point}")
            self._hit(f"before_remove_parent_fsync_{point}")
            os.fsync(parent)
            self._hit(f"after_remove_{point}")
        finally:
            if descriptor is not None:
                os.close(descriptor)
            os.close(parent)


class Stage05Reconciler:
    def __init__(
        self,
        bundle_digest: str,
        *,
        paths: Stage05Paths | None = None,
        expected_uid: int = 0,
        expected_gid: int = 0,
        mount_id: Callable[[int], int] = linux_mount_id,
        xattrs: Callable[[int], tuple[str, ...]] = _default_xattrs,
        service_state: Callable[[str], str] | None = None,
        fault: Callable[[str], None] | None = None,
    ) -> None:
        self.paths = paths or Stage05Paths.production()
        self.uid = expected_uid
        self.gid = expected_gid
        self.plan = build_plan(bundle_digest)
        self.tree = SecureTree(
            self.paths,
            expected_uid,
            expected_gid,
            mount_id=mount_id,
            xattrs=xattrs,
            fault=fault,
        )
        self.service_state = service_state or self._systemd_state

    def _systemd_state(self, unit: str) -> str:
        try:
            result = subprocess.run(
                [
                    "/usr/bin/systemctl",
                    "show",
                    "--no-pager",
                    "--property=LoadState",
                    "--value",
                    "--",
                    unit,
                ],
                stdin=subprocess.DEVNULL,
                capture_output=True,
                timeout=5,
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            return "unknown"
        if result.returncode != 0 or result.stderr or len(result.stdout) > _MAX_SYSTEMD_BYTES:
            return "unknown"
        value = result.stdout.decode("ascii", errors="strict").strip()
        if value == "not-found":
            return "absent"
        if value in {"loaded", "masked", "merged", "stub"}:
            return "present"
        return "unknown"

    def refuse_production(self) -> None:
        candidates = (*_PRODUCTION_PATHS, *(root / unit for root in _UNIT_ROOTS for unit in _UNIT_NAMES))
        for path in candidates:
            try:
                first = self.tree.path_state(path)
                second = self.tree.path_state(path)
            except Stage05Error as error:
                raise Stage05Error("production_state_unknown") from error
            if first != second:
                raise Stage05Error("production_state_unknown")
            if first == "present":
                raise Stage05Error("production_state_present")
        for unit in _UNIT_NAMES:
            try:
                state = self.service_state(unit)
            except Exception as error:
                raise Stage05Error("production_state_unknown") from error
            if state == "present":
                raise Stage05Error("production_state_present")
            if state != "absent":
                raise Stage05Error("production_state_unknown")

    def _journal_policy(self) -> JournalSecurityPolicy:
        return JournalSecurityPolicy(
            expected_owner_uid=self.uid,
            expected_owner_gid=self.gid,
            trusted_prefix=self.paths.trusted_prefix,
        )

    def _validate_existing_roots(self) -> None:
        state_entries = self.tree.validate_directory(STATE_ROOT, 0o755, None)
        if any(entry not in {"journal", "experiments"} for entry in state_entries):
            raise Stage05Error("journal_root_conflict")
        journal_entries = self.tree.validate_directory(JOURNAL_ROOT, 0o700, None)
        if any(
            entry != JOURNAL_NAME and not _JOURNAL_TEMP_RE.fullmatch(entry)
            for entry in journal_entries
        ):
            raise Stage05Error("journal_root_conflict")
        self._validate_journal_entries(journal_entries)

    def _validate_journal_entries(self, entries: tuple[str, ...]) -> None:
        parent = self.tree.open_directory(JOURNAL_ROOT, 0o700)
        try:
            for name in entries:
                descriptor: int | None = None
                try:
                    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
                    descriptor = os.open(name, flags, dir_fd=parent)
                    opened = os.fstat(descriptor)
                    published = os.stat(name, dir_fd=parent, follow_symlinks=False)
                    if (
                        not stat.S_ISREG(opened.st_mode)
                        or opened.st_uid != self.uid
                        or opened.st_gid != self.gid
                        or stat.S_IMODE(opened.st_mode) != 0o600
                        or opened.st_nlink not in {1, 2}
                        or (opened.st_dev, opened.st_ino)
                        != (published.st_dev, published.st_ino)
                        or self.tree.xattrs(descriptor)
                        or self.tree.mount_id(descriptor) != self.tree.mount_id(parent)
                    ):
                        raise Stage05Error("journal_root_conflict")
                except OSError as error:
                    raise Stage05Error("journal_root_conflict") from error
                finally:
                    if descriptor is not None:
                        os.close(descriptor)
        finally:
            os.close(parent)

    def _allocate_roots(self) -> str:
        state = self.tree.path_state(STATE_ROOT)
        if state == "absent":
            self.tree.create_directory(STATE_ROOT, 0o755, "state_root")
            classification = "bootstrap-residue"
        else:
            classification = "exact"
            try:
                entries = self.tree.validate_directory(STATE_ROOT, 0o755, None)
            except Stage05Error as original_error:
                descriptor = self.tree.open_directory(STATE_ROOT)
                try:
                    provisional = stat.S_IMODE(os.fstat(descriptor).st_mode) == 0o700
                finally:
                    os.close(descriptor)
                if not provisional:
                    raise original_error
                self.tree.recover_provisional_directory(
                    STATE_ROOT, 0o755, (), "state_root"
                )
                entries = ()
                classification = "bootstrap-residue"
            if any(entry not in {"journal", "experiments"} for entry in entries):
                raise Stage05Error("journal_root_conflict")
        journal = self.tree.path_state(JOURNAL_ROOT)
        if journal == "absent":
            state_entries = self.tree.validate_directory(STATE_ROOT, 0o755, None)
            if state_entries:
                raise Stage05Error("journal_root_conflict")
            self.tree.sync_directory(STATE_ROOT, 0o755, (), "state_root")
            self.tree.create_directory(JOURNAL_ROOT, 0o700, "journal_root")
            classification = "bootstrap-residue"
        else:
            entries = self.tree.validate_directory(JOURNAL_ROOT, 0o700, None)
            if any(entry != JOURNAL_NAME and not _JOURNAL_TEMP_RE.fullmatch(entry) for entry in entries):
                raise Stage05Error("journal_root_conflict")
        return classification

    def _sync_bootstrap_roots(self) -> None:
        self.tree.sync_directory(JOURNAL_ROOT, 0o700, None, "journal_root")
        self.tree.sync_directory(STATE_ROOT, 0o755, (JOURNAL_ROOT.name,), "state_root")

    def _sync_prefix(self, length: int) -> None:
        if length >= 1:
            config_entries = (MARKER_PATH.name,) if length == 4 else ()
            self.tree.sync_directory(CONFIG_ROOT, 0o755, config_entries, "config")
        if length >= 2:
            experiment_entries = (WORKSPACE.name,) if length >= 3 else ()
            self.tree.sync_directory(
                EXPERIMENTS_ROOT, 0o700, experiment_entries, "experiments"
            )
        if length >= 3:
            self.tree.sync_directory(WORKSPACE, 0o700, (), "workspace")
        if length == 4:
            self.tree.sync_marker(self.plan.marker_bytes)

    def _resource_presence(self) -> tuple[bool, bool, bool, bool]:
        return tuple(
            self.tree.path_state(path) == "present"
            for path in (CONFIG_ROOT, EXPERIMENTS_ROOT, WORKSPACE, MARKER_PATH)
        )  # type: ignore[return-value]

    def _raw_prefix_length(self) -> int:
        prefixes = {
            (False, False, False, False): 0,
            (True, False, False, False): 1,
            (True, True, False, False): 2,
            (True, True, True, False): 3,
            (True, True, True, True): 4,
        }
        present = self._resource_presence()
        if present not in prefixes:
            raise Stage05Error("illegal_recovery_state")
        return prefixes[present]

    def _prefix_length(
        self,
        *,
        strict_entries: bool,
        allow_config_provisional: bool = False,
        repair_config_provisional: bool = True,
        marker_residue: str | None = None,
    ) -> int:
        length = self._raw_prefix_length()
        if length >= 1:
            allowed = (MARKER_PATH.name,) if length == 4 else ()
            if marker_residue is not None:
                allowed = (*allowed, marker_residue)
            try:
                self.tree.validate_directory(
                    CONFIG_ROOT, 0o755, allowed if strict_entries else None
                )
            except Stage05Error:
                if not allow_config_provisional or length != 1 or not strict_entries:
                    raise
                if repair_config_provisional:
                    self.tree.recover_provisional_directory(
                        CONFIG_ROOT, 0o755, (), "config"
                    )
                else:
                    self.tree.validate_provisional_directory(CONFIG_ROOT, ())
        if length >= 2:
            allowed = (WORKSPACE.name,) if length >= 3 else ()
            self.tree.validate_directory(EXPERIMENTS_ROOT, 0o700, allowed if strict_entries else None)
        if length >= 3:
            self.tree.validate_directory(WORKSPACE, 0o700, () if strict_entries else None)
        if length == 4 and marker_residue is None:
            self.tree.read_file(MARKER_PATH, self.plan.marker_bytes)
        return length

    def _assert_record_exact(self, record: JournalRecord) -> JournalRecord:
        if (
            record.install_id != INSTALL_ID
            or record.plan_id != PLAN_ID
            or record.plan_hash != self.plan.plan_hash
            or record.resources != self.plan.resources
        ):
            raise Stage05Error("journal_conflict")
        return record

    def _load_exact(self, session: object, *, inflight: bool = False) -> JournalRecord:
        try:
            if inflight:
                record = session.load_inflight(INSTALL_ID)  # type: ignore[attr-defined]
            else:
                record = session.load(INSTALL_ID)  # type: ignore[attr-defined]
        except JournalCorrupt as error:
            raise Stage05Error("journal_corrupt") from error
        except JournalSecurityError as error:
            raise Stage05Error("journal_conflict") from error
        except JournalConflict as error:
            raise Stage05Error("transaction_busy") from error
        self._assert_record_exact(record)
        link_count = self._validate_journal_metadata(
            allowed_nlinks=frozenset({1, 2}) if inflight else frozenset({1})
        )
        if inflight and link_count == 2 and record != self.plan.record:
            raise Stage05Error("journal_conflict")
        return record

    def _load_unpublished_exact(self, session: object) -> JournalRecord:
        try:
            record = session.load_unpublished(INSTALL_ID)  # type: ignore[attr-defined]
        except JournalCorrupt as error:
            raise Stage05Error("journal_corrupt") from error
        except (JournalSecurityError, JournalConflict) as error:
            raise Stage05Error("journal_conflict") from error
        self._assert_record_exact(record)
        if record != self.plan.record:
            raise Stage05Error("journal_conflict")
        return record

    def _validate_journal_metadata(
        self, *, allowed_nlinks: frozenset[int] = frozenset({1})
    ) -> int:
        parent = self.tree.open_directory(JOURNAL_ROOT, 0o700)
        descriptor: int | None = None
        try:
            flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
            descriptor = os.open(JOURNAL_NAME, flags, dir_fd=parent)
            metadata = os.fstat(descriptor)
            if (
                not stat.S_ISREG(metadata.st_mode)
                or metadata.st_uid != self.uid
                or metadata.st_gid != self.gid
                or stat.S_IMODE(metadata.st_mode) != 0o600
                or metadata.st_nlink not in allowed_nlinks
                or self.tree.xattrs(descriptor)
                or self.tree.mount_id(descriptor) != self.tree.mount_id(parent)
            ):
                raise Stage05Error("journal_conflict")
        except OSError as error:
            raise Stage05Error("journal_conflict") from error
        finally:
            if descriptor is not None:
                os.close(descriptor)
            os.close(parent)
        return metadata.st_nlink

    def _create_or_load(self, session: object) -> JournalRecord:
        try:
            record = session.create(self.plan.record)  # type: ignore[attr-defined]
            self._validate_journal_metadata()
            return record
        except JournalConflict:
            return self._load_exact(session)
        except JournalCorrupt as error:
            raise Stage05Error("journal_corrupt") from error
        except JournalSecurityError as error:
            raise Stage05Error("journal_conflict") from error

    @staticmethod
    def _record_after(record: JournalRecord, state: JournalState) -> JournalRecord:
        return JournalRecord(
            record.install_id,
            record.plan_id,
            record.plan_hash,
            record.resources,
            record.transitions + (state,),
        )

    def _prepare_existing_record(
        self, session: object, operation: str
    ) -> JournalRecord:
        if operation not in {"execute", "rollback"}:
            raise ValueError("invalid Stage 05 operation")
        try:
            record, link_count, residues = session.inspect_residue(  # type: ignore[attr-defined]
                INSTALL_ID
            )
        except JournalCorrupt as error:
            raise Stage05Error("journal_corrupt") from error
        except (JournalSecurityError, JournalConflict) as error:
            raise Stage05Error("journal_conflict") from error
        self._assert_record_exact(record)
        self._validate_journal_metadata(allowed_nlinks=frozenset({1, 2}))
        next_states = {
            JournalState.PLANNED: (JournalState.APPLYING, JournalState.FAILED),
            JournalState.APPLYING: (
                JournalState.APPLIED,
                JournalState.ROLLING_BACK,
                JournalState.FAILED,
            ),
            JournalState.APPLIED: (
                JournalState.VALIDATED,
                JournalState.ROLLING_BACK,
                JournalState.FAILED,
            ),
            JournalState.VALIDATED: (JournalState.ROLLING_BACK,),
            JournalState.FAILED: (JournalState.ROLLING_BACK,),
            JournalState.ROLLING_BACK: (
                JournalState.ROLLED_BACK,
                JournalState.FAILED,
            ),
            JournalState.ROLLED_BACK: (),
        }
        outbound = tuple(
            self._record_after(record, state) for state in next_states[record.state]
        )
        if link_count == 2:
            if record != self.plan.record:
                raise Stage05Error("journal_conflict")
            return self._load_exact(session)
        if any(residue not in outbound for residue in residues):
            raise Stage05Error("journal_conflict")
        if not residues:
            return record
        try:
            session.cleanup_temps(  # type: ignore[attr-defined]
                INSTALL_ID, (record, *outbound)
            )
        except (JournalConflict, JournalCorrupt, JournalSecurityError) as error:
            raise Stage05Error("journal_conflict") from error
        return self._load_exact(session)

    def _transition(self, session: object, state: JournalState) -> JournalRecord:
        try:
            self.tree._hit(f"before_journal_transition_{state.value}")
            record = session.transition(  # type: ignore[attr-defined]
                INSTALL_ID, PLAN_ID, self.plan.plan_hash, self.plan.resources, state
            )
            self.tree._hit(f"after_journal_transition_{state.value}")
            self._validate_journal_metadata()
            return record
        except (JournalConflict, JournalCorrupt, JournalSecurityError) as error:
            raise Stage05Error("journal_conflict") from error

    def _result(self, operation: str, root: str, record: JournalRecord, length: int) -> Stage05Result:
        return Stage05Result(
            operation=operation,
            journal_root=root,
            journal_state=record.state.value,
            transition_count=len(record.transitions),
            plan_hash=self.plan.plan_hash,
            bundle_hash=self.plan.bundle_hash,
            marker_hash=self.plan.marker_hash,
            resource_state="exact" if length in {0, 4} else "prefix",
            workspace_empty=length < 3 or not self.tree.validate_directory(WORKSPACE, 0o700, None),
            retained_provenance=True,
            next_action="reinstall" if record.state is JournalState.ROLLED_BACK else "stage-specific-approval",
        )

    def execute(self) -> Stage05Result:
        self._require_execution_identity()
        self.refuse_production()
        root_class = self._allocate_roots()
        store = JournalStore(self.paths.actual(JOURNAL_ROOT), self._journal_policy())
        try:
            with store.locked() as session:
                self._validate_existing_roots()
                self.refuse_production()
                journal_present = self.tree.path_state(JOURNAL_PATH) == "present"
                raw_length = self._raw_prefix_length()
                if not journal_present and raw_length != 0:
                    raise Stage05Error("resource_prior_mismatch")
                if not journal_present:
                    journal_entries = self.tree.validate_directory(
                        JOURNAL_ROOT, 0o700, None
                    )
                    if journal_entries:
                        self._load_unpublished_exact(session)
                    self._sync_bootstrap_roots()
                record = (
                    self._prepare_existing_record(session, "execute")
                    if journal_present
                    else self._create_or_load(session)
                )
                if record.state is JournalState.APPLYING:
                    self.refuse_production()
                    self.tree.recover_marker_residue(self.plan.marker_bytes)
                    length = self._prefix_length(
                        strict_entries=True,
                        allow_config_provisional=True,
                    )
                    self._sync_prefix(length)
                else:
                    length = self._prefix_length(strict_entries=True)
                if record.state is JournalState.VALIDATED:
                    if length != 4:
                        raise Stage05Error("resource_desired_mismatch")
                    self.refuse_production()
                    return self._result("execute", root_class, record, length)
                if record.state in {JournalState.FAILED, JournalState.ROLLING_BACK, JournalState.ROLLED_BACK}:
                    raise Stage05Error("illegal_recovery_state")
                if record.state is JournalState.PLANNED:
                    if length != 0:
                        raise Stage05Error("resource_prior_mismatch")
                    self.refuse_production()
                    record = self._transition(session, JournalState.APPLYING)
                elif record.state is JournalState.APPLYING:
                    self.refuse_production()
                elif record.state is JournalState.APPLIED:
                    if length != 4:
                        raise Stage05Error("resource_desired_mismatch")
                    self.refuse_production()
                    record = self._transition(session, JournalState.VALIDATED)
                    return self._result("execute", root_class, record, length)
                try:
                    for index, (path, mode, point) in enumerate(
                        (
                            (CONFIG_ROOT, 0o755, "config"),
                            (EXPERIMENTS_ROOT, 0o700, "experiments"),
                            (WORKSPACE, 0o700, "workspace"),
                        ),
                        start=1,
                    ):
                        if length < index:
                            self.tree.create_directory(path, mode, point)
                            length = index
                    if length < 4:
                        self.tree.publish_marker(self.plan.marker_bytes)
                        length = 4
                    if self._prefix_length(strict_entries=True) != 4:
                        raise Stage05Error("resource_desired_mismatch")
                    record = self._transition(session, JournalState.APPLIED)
                    self.refuse_production()
                    if self._prefix_length(strict_entries=True) != 4:
                        raise Stage05Error("resource_desired_mismatch")
                    record = self._transition(session, JournalState.VALIDATED)
                    if self._prefix_length(strict_entries=True) != 4:
                        raise Stage05Error("resource_desired_mismatch")
                    return self._result("execute", root_class, record, 4)
                except Exception:
                    self._record_failed_if_stable(session)
                    raise
        except JournalConflict as error:
            raise Stage05Error("transaction_busy") from error

    def rollback(self) -> Stage05Result:
        self._require_execution_identity()
        self.refuse_production()
        if self.tree.path_state(JOURNAL_ROOT) != "present":
            raise Stage05Error("journal_root_conflict")
        store = JournalStore(self.paths.actual(JOURNAL_ROOT), self._journal_policy())
        try:
            with store.locked() as session:
                self.refuse_production()
                try:
                    self._validate_existing_roots()
                except Stage05Error as error:
                    raise Stage05Error("rollback_foreign_state") from error
                record = self._prepare_existing_record(session, "rollback")
                if record.state in {JournalState.APPLYING, JournalState.ROLLING_BACK}:
                    self.refuse_production()
                    self.tree.recover_marker_residue(self.plan.marker_bytes)
                try:
                    length = self._prefix_length(
                        strict_entries=True,
                        allow_config_provisional=record.state is JournalState.APPLYING,
                    )
                except Stage05Error as error:
                    raise Stage05Error("rollback_foreign_state") from error
                if record.state is JournalState.ROLLED_BACK:
                    if length != 0:
                        raise Stage05Error("rollback_foreign_state")
                    return self._result("rollback", "exact", record, 0)
                self.refuse_production()
                if record.state is JournalState.PLANNED:
                    if length != 0:
                        raise Stage05Error("rollback_foreign_state")
                    record = self._transition(session, JournalState.FAILED)
                if record.state in {
                    JournalState.APPLYING,
                    JournalState.APPLIED,
                    JournalState.VALIDATED,
                    JournalState.FAILED,
                }:
                    self.refuse_production()
                    record = self._transition(session, JournalState.ROLLING_BACK)
                if record.state is not JournalState.ROLLING_BACK:
                    raise Stage05Error("illegal_recovery_state")
                if length == 4:
                    self.tree.remove_marker(self.plan.marker_bytes)
                    length = 3
                for current, path, mode, point in (
                    (3, WORKSPACE, 0o700, "workspace"),
                    (2, EXPERIMENTS_ROOT, 0o700, "experiments"),
                    (1, CONFIG_ROOT, 0o755, "config"),
                ):
                    if length == current:
                        self.tree.remove_directory(path, mode, point)
                        length -= 1
                if self._prefix_length(strict_entries=True) != 0:
                    raise Stage05Error("rollback_foreign_state")
                record = self._transition(session, JournalState.ROLLED_BACK)
                return self._result("rollback", "exact", record, 0)
        except JournalConflict as error:
            raise Stage05Error("transaction_busy") from error

    def _record_failed_if_stable(self, session: object) -> None:
        try:
            current = self._load_exact(session)
            if current.state is not JournalState.APPLYING:
                return
            self.tree.recover_marker_residue(self.plan.marker_bytes)
            self._prefix_length(
                strict_entries=True,
                allow_config_provisional=True,
            )
            self._transition(session, JournalState.FAILED)
        except Exception:
            return

    def _require_execution_identity(self) -> None:
        if self.paths == Stage05Paths.production() and (
            os.geteuid() != 0 or os.getegid() != 0
        ):
            raise Stage05Error("unsafe_ancestry")

    def observe(self, operation: str) -> Stage05Result:
        self._require_execution_identity()
        self.refuse_production()
        state_first = self.tree.path_state(STATE_ROOT)
        first = self.tree.path_state(JOURNAL_ROOT)
        state_second = self.tree.path_state(STATE_ROOT)
        second = self.tree.path_state(JOURNAL_ROOT)
        if first != second or state_first != state_second:
            raise Stage05Error("transaction_busy")
        if first == "absent":
            root_class = "absent"
            if state_first == "present":
                try:
                    self.tree.validate_directory(STATE_ROOT, 0o755, ())
                except Stage05Error:
                    try:
                        self.tree.validate_provisional_directory(STATE_ROOT, ())
                    except Stage05Error as error:
                        raise Stage05Error("journal_root_conflict") from error
                root_class = "bootstrap-residue"
            return Stage05Result(
                operation,
                root_class,
                "absent",
                0,
                self.plan.plan_hash,
                self.plan.bundle_hash,
                self.plan.marker_hash,
                "absent",
                True,
                False,
                "execute",
            )
        store = JournalStore(self.paths.actual(JOURNAL_ROOT), self._journal_policy())
        try:
            with store.locked(exclusive=False) as session:
                self._validate_existing_roots()
                if self.tree.path_state(JOURNAL_PATH) == "absent":
                    journal_entries = self.tree.validate_directory(
                        JOURNAL_ROOT, 0o700, None
                    )
                    if not journal_entries:
                        return Stage05Result(
                            operation,
                            "bootstrap-residue",
                            "absent",
                            0,
                            self.plan.plan_hash,
                            self.plan.bundle_hash,
                            self.plan.marker_hash,
                            "absent",
                            True,
                            True,
                            "execute",
                        )
                    if (
                        len(journal_entries) != 1
                        or not _JOURNAL_TEMP_RE.fullmatch(journal_entries[0])
                        or self._raw_prefix_length() != 0
                    ):
                        raise Stage05Error("journal_root_conflict")
                    record = self._load_unpublished_exact(session)
                    return Stage05Result(
                        operation,
                        "bootstrap-residue",
                        "in-flight",
                        len(record.transitions),
                        self.plan.plan_hash,
                        self.plan.bundle_hash,
                        self.plan.marker_hash,
                        "unknown",
                        True,
                        True,
                        "execute",
                    )
                record = self._load_exact(session, inflight=True)
                marker_residue = None
                if record.state in {JournalState.APPLYING, JournalState.ROLLING_BACK}:
                    marker_residue = self.tree.validate_marker_residue(
                        self.plan.marker_bytes
                    )
                length = self._prefix_length(
                    strict_entries=True,
                    allow_config_provisional=record.state is JournalState.APPLYING,
                    repair_config_provisional=False,
                    marker_residue=marker_residue,
                )
                if record.state is JournalState.VALIDATED and length != 4:
                    raise Stage05Error("resource_desired_mismatch")
                if record.state is JournalState.ROLLED_BACK and length != 0:
                    raise Stage05Error("rollback_foreign_state")
                return self._result(operation, "exact", record, length)
        except JournalConflict as error:
            raise Stage05Error("transaction_busy") from error


def main(arguments: list[str] | None = None) -> int:
    values = list(sys.argv[1:] if arguments is None else arguments)
    if len(values) != 3 or values[0] not in {
        "production-check",
        "before",
        "execute",
        "after",
        "postconditions",
        "rollback",
        "rollback-postconditions",
    }:
        print("status=error reason=invalid_mode", file=sys.stderr)
        return 64
    if values[1] != "DISPOSABLE_OVH_LAB":
        print("status=error reason=acknowledgement_required", file=sys.stderr)
        return 64
    if not ((3, 13) <= sys.version_info[:2] < (3, 15)):
        print("status=error reason=unsupported_python", file=sys.stderr)
        return 68
    mode, _acknowledgement, digest = values
    os.umask(0o077)
    try:
        reconciler = Stage05Reconciler(digest)
        if mode == "production-check":
            reconciler._require_execution_identity()
            reconciler.refuse_production()
            return 0
        if mode == "execute":
            result = reconciler.execute()
        elif mode == "rollback":
            result = reconciler.rollback()
        else:
            result = reconciler.observe(mode)
    except Stage05Error as error:
        print(f"status=error reason={error.reason}", file=sys.stderr)
        return 65
    except (JournalConflict, JournalCorrupt, JournalSecurityError):
        print("status=error reason=journal_conflict", file=sys.stderr)
        return 65
    except Exception:
        print("status=error reason=internal_error", file=sys.stderr)
        return 70
    print(result.evidence())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
