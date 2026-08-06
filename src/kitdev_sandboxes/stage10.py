"""Read-only cached package inventory for disposable-lab Stage 10."""

from __future__ import annotations

import base64
import fcntl
import hashlib
import json
import os
import re
import stat
import sys
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterator

from kitdev_sandboxes.journal import (
    JournalConflict,
    JournalSecurityError,
    JournalSecurityPolicy,
    JournalState,
    JournalStore,
)
from kitdev_sandboxes.runner import Command, CommandRunner
from kitdev_sandboxes.stage05 import (
    JOURNAL_PATH,
    JOURNAL_ROOT,
    MARKER_PATH,
    SecureTree,
    Stage05Error,
    Stage05Paths,
    Stage05Reconciler,
    linux_mount_id,
)


DOCKER_KEY_PATH = Path("/etc/apt/keyrings/docker.asc")
DOCKER_SOURCE_PATH = Path("/etc/apt/sources.list.d/docker.sources")
APT_EXTENDED_STATES_PATH = Path("/var/lib/apt/extended_states")
DPKG_STATUS_PATH = Path("/var/lib/dpkg/status")
OS_RELEASE_PATH = Path("/usr/lib/os-release")
UBUNTU_ARCHIVE_KEYRING_PATH = Path("/usr/share/keyrings/ubuntu-archive-keyring.gpg")
APT_SOURCES_LIST = Path("/etc/apt/sources.list")
APT_SOURCE_PARTS = Path("/etc/apt/sources.list.d")
PACKAGE_LOCK_PATHS = (
    Path("/var/lib/dpkg/lock-frontend"),
    Path("/var/lib/dpkg/lock"),
    Path("/var/lib/apt/lists/lock"),
    Path("/var/cache/apt/archives/lock"),
)
LEGACY_DOCKER_KEY_PATHS = (
    Path("/etc/apt/keyrings/docker.gpg"),
    Path("/usr/share/keyrings/docker-archive-keyring.gpg"),
    Path("/usr/share/keyrings/docker.gpg"),
)
DOCKER_KEY_SHA256 = "1500c1f56fa9e26b9b8f42452a553675796ade0807cdce11975eb98170b3a570"
DOCKER_KEY_PRIMARY_FINGERPRINT = "9DC858229FC7DD38854AE2D88D81803C0EBFCD88"
DOCKER_KEY_SIGNING_FINGERPRINT = "D3306A018370199E527AE7997EA0A9C3F273FCD8"
DOCKER_SOURCE_BYTES = (
    b"Types: deb\n"
    b"URIs: https://download.docker.com/linux/ubuntu\n"
    b"Suites: resolute\n"
    b"Components: stable\n"
    b"Architectures: amd64\n"
    b"Signed-By: /etc/apt/keyrings/docker.asc\n"
)
PREREQUISITE_PACKAGES = ("ca-certificates", "curl")
TRUST_PACKAGES = ("ubuntu-keyring",)
DOCKER_CONFLICT_PACKAGES = (
    "containerd",
    "docker-buildx",
    "docker-compose",
    "docker-compose-v2",
    "docker-doc",
    "docker.io",
    "podman-docker",
    "runc",
)

_HASH_RE = re.compile(r"^[0-9a-f]{64}$")
_PACKAGE_RE = re.compile(r"^[a-z0-9][a-z0-9+.-]{0,127}(?::[a-z0-9][a-z0-9-]{0,31})?$")
_VERSION_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9.+:~_-]{0,255}$")
_ARCH_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,31}$")
_STATUS_RE = re.compile(r"^[a-z][a-z-]{0,31}$")
_OS_RELEASE_KEY_RE = re.compile(r"^[A-Z][A-Z0-9_]{0,63}$")
_POLICY_FIELD_RE = re.compile(r"^  (Installed|Candidate): (\S+)$")
_INSTALL_RE = re.compile(
    r"^Inst (?P<package>\S+)(?: \[[^\]\r\n]+\])? "
    r"\((?P<version>\S+)(?: [^\r\n()]*)?\)$"
)
_MAX_FILE_BYTES = 65_536
_MAX_STATE_BYTES = 16_777_216
_MAX_RESOLUTION_BYTES = 65_536
_MAX_ACTIONS = 256
_MAX_MARKS = 8_192
_MARK_QUERY_SIZE = 48
_FILE_FIELDS = (
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


class Stage10Error(RuntimeError):
    """A fail-closed Stage 10 result with a stable public reason code."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True)
class CommandOutput:
    returncode: int | None
    stdout: str
    stderr: str
    succeeded: bool


@dataclass(frozen=True, order=True)
class PackageState:
    name: str
    selection: str
    error: str
    status: str
    installed_version: str | None
    installed_architecture: str | None
    candidate_version: str | None

    def as_dict(self) -> dict[str, object]:
        return {
            "candidate_version": self.candidate_version,
            "error": self.error,
            "installed_architecture": self.installed_architecture,
            "installed_version": self.installed_version,
            "name": self.name,
            "selection": self.selection,
            "status": self.status,
        }


@dataclass(frozen=True, order=True)
class PackageAction:
    name: str
    version: str

    def as_dict(self) -> dict[str, str]:
        return {"name": self.name, "version": self.version}


@dataclass(frozen=True)
class Stage10Result:
    operation: str
    resolver_bundle_hash: str
    stage05_bundle_hash: str
    key_state: str
    source_state: str
    packages: tuple[PackageState, ...]
    conflicts: tuple[PackageState, ...]
    trust_packages: tuple[PackageState, ...]
    holds: tuple[str, ...]
    manual: tuple[str, ...]
    automatic: tuple[str, ...]
    foreign_docker_sources: tuple[str, ...]
    legacy_docker_keys: tuple[str, ...]
    actions: tuple[PackageAction, ...]
    resolution_bytes: bytes
    resolution_hash: str
    eligible: bool

    def evidence(self) -> str:
        encoded = base64.urlsafe_b64encode(self.resolution_bytes).decode("ascii")
        return (
            f"stage=10 operation={self.operation} status=pass mode=resolution-only "
            "apply_authorized=no next_action=isolated-refresh-and-lock "
            f"inventory_clean={'yes' if self.eligible else 'no'} "
            f"prerequisite_count={len(self.packages)} trust_count={len(self.trust_packages)} "
            f"conflict_count={len(self.conflicts)} "
            f"hold_count={len(self.holds)} manual_count={len(self.manual)} "
            f"automatic_count={len(self.automatic)} action_count={len(self.actions)} "
            f"foreign_source_count={len(self.foreign_docker_sources)} "
            f"legacy_key_count={len(self.legacy_docker_keys)} "
            f"docker_key={self.key_state} docker_source={self.source_state} "
            f"resolution_sha256={self.resolution_hash} resolution_b64url={encoded}"
        )


def _canonical_json(value: object) -> bytes:
    encoded = (
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True) + "\n"
    ).encode("ascii")
    if not encoded or len(encoded) > _MAX_RESOLUTION_BYTES:
        raise Stage10Error("resolution_oversized")
    return encoded


def _safe_token(pattern: re.Pattern[str], value: str, reason: str) -> str:
    if value.__class__ is not str or not pattern.fullmatch(value):
        raise Stage10Error(reason)
    return value


class Stage10Resolver:
    """Inventory a cached exact transaction without changing host APT state."""

    def __init__(
        self,
        resolver_bundle_digest: str,
        *,
        paths: Stage05Paths | None = None,
        expected_uid: int = 0,
        expected_gid: int = 0,
        mount_id: Callable[[int], int] = linux_mount_id,
        xattrs: Callable[[int], tuple[str, ...]] | None = None,
        service_state: Callable[[str], str] | None = None,
        command: Callable[[tuple[str, ...]], CommandOutput] | None = None,
        authorization: Callable[[], str] | None = None,
    ) -> None:
        if not _HASH_RE.fullmatch(resolver_bundle_digest):
            raise Stage10Error("bundle_digest_required")
        self.resolver_bundle_digest = resolver_bundle_digest
        self.paths = paths or Stage05Paths.production()
        self.uid = expected_uid
        self.gid = expected_gid
        self.mount_id = mount_id
        self.xattrs = xattrs or self._default_xattrs
        self.service_state = service_state
        self.tree = SecureTree(
            self.paths,
            self.uid,
            self.gid,
            mount_id=self.mount_id,
            xattrs=self.xattrs,
        )
        self.command = command or self._run_command
        self.authorization = authorization

    @staticmethod
    def _default_xattrs(descriptor: int) -> tuple[str, ...]:
        try:
            return tuple(sorted(os.listxattr(descriptor)))
        except OSError as error:
            raise Stage10Error("repository_state_unknown") from error

    @staticmethod
    def _run_command(argv: tuple[str, ...]) -> CommandOutput:
        result = CommandRunner().run(
            Command(
                argv,
                timeout_seconds=15,
                termination_grace_seconds=0.5,
                stdout_limit_bytes=262_144,
                stderr_limit_bytes=65_536,
            )
        )
        if result.output_truncated or result.io_error or result.cleanup_error:
            raise RuntimeError("bounded command result unavailable")
        return CommandOutput(
            result.returncode,
            result.stdout.text,
            result.stderr.text,
            result.succeeded,
        )

    @staticmethod
    def _stable(metadata: os.stat_result) -> tuple[object, ...]:
        return tuple(getattr(metadata, field) for field in _FILE_FIELDS)

    def _read_file(self, path: Path, mode: int, limit: int) -> bytes | None:
        try:
            state = self.tree.path_state(path)
        except Stage05Error as error:
            raise Stage10Error("repository_state_unknown") from error
        if state == "absent":
            return None
        parent: int | None = None
        descriptor: int | None = None
        try:
            parent = self.tree.open_directory(path.parent)
            descriptor = os.open(
                path.name,
                os.O_RDONLY
                | getattr(os, "O_CLOEXEC", 0)
                | getattr(os, "O_NOFOLLOW", 0),
                dir_fd=parent,
            )
            before = os.fstat(descriptor)
            if (
                not stat.S_ISREG(before.st_mode)
                or before.st_uid != self.uid
                or before.st_gid != self.gid
                or stat.S_IMODE(before.st_mode) != mode
                or before.st_nlink != 1
                or self.xattrs(descriptor)
                or self.mount_id(descriptor) != self.mount_id(parent)
            ):
                raise Stage10Error("repository_state_conflict")
            content = bytearray()
            while True:
                chunk = os.read(descriptor, min(65_536, limit + 1 - len(content)))
                if not chunk:
                    break
                content.extend(chunk)
                if len(content) > limit:
                    raise Stage10Error("repository_state_conflict")
            after = os.fstat(descriptor)
            published = os.stat(path.name, dir_fd=parent, follow_symlinks=False)
            if self._stable(before) != self._stable(after) or self._stable(after) != self._stable(
                published
            ):
                raise Stage10Error("repository_state_conflict")
            return bytes(content)
        except Stage10Error:
            raise
        except (OSError, UnicodeError) as error:
            raise Stage10Error("repository_state_conflict") from error
        finally:
            if descriptor is not None:
                os.close(descriptor)
            if parent is not None:
                os.close(parent)

    def _marker_digest(self) -> str:
        raw = self._read_file(MARKER_PATH, 0o600, _MAX_FILE_BYTES)
        if raw is None:
            raise Stage10Error("stage05_authorization_absent")
        try:
            document = json.loads(raw.decode("ascii", errors="strict"))
            if document.__class__ is not dict or set(document) != {
                "authorization_scope",
                "bundle_sha256",
                "install_id",
                "plan_id",
                "schema_version",
            }:
                raise ValueError
            if (
                document["authorization_scope"] != "disposable-ovh-lab"
                or document["install_id"] != "ovh-lab-stage05-v1"
                or document["plan_id"] != "ovh-lab-stage05-marker-workspace-v1"
                or document["schema_version"] != 1
                or document["schema_version"].__class__ is not int
                or _canonical_json(document) != raw
            ):
                raise ValueError
            bundle_hash = document["bundle_sha256"]
            if bundle_hash.__class__ is not str or not bundle_hash.startswith("sha256:"):
                raise ValueError
            digest = bundle_hash.removeprefix("sha256:")
            return _safe_token(_HASH_RE, digest, "stage05_authorization_conflict")
        except (UnicodeError, ValueError, TypeError, json.JSONDecodeError) as error:
            raise Stage10Error("stage05_authorization_conflict") from error

    @contextmanager
    def _authorization_session(self) -> Iterator[str]:
        if self.authorization is not None:
            yield self.authorization()
            return
        policy = JournalSecurityPolicy(
            expected_owner_uid=self.uid,
            expected_owner_gid=self.gid,
            trusted_prefix=self.paths.trusted_prefix,
        )
        store = JournalStore(self.paths.actual(JOURNAL_ROOT), policy)
        try:
            with store.locked(exclusive=False) as session:
                digest = self._marker_digest()
                reconciler = Stage05Reconciler(
                    digest,
                    paths=self.paths,
                    expected_uid=self.uid,
                    expected_gid=self.gid,
                    mount_id=self.mount_id,
                    xattrs=self.xattrs,
                    service_state=self.service_state,
                )
                reconciler._require_execution_identity()
                reconciler.refuse_production()
                reconciler._validate_existing_roots()
                if reconciler.tree.path_state(JOURNAL_PATH) != "present":
                    raise Stage10Error("stage05_authorization_conflict")
                record = reconciler._load_exact(session, inflight=True)
                if record.state is not JournalState.VALIDATED:
                    raise Stage10Error("stage05_authorization_conflict")
                if reconciler._prefix_length(strict_entries=True) != 4:
                    raise Stage10Error("stage05_authorization_conflict")
                reconciler.refuse_production()
                yield digest
                if self._marker_digest() != digest:
                    raise Stage10Error("stage05_authorization_conflict")
                reconciler._require_execution_identity()
                reconciler.refuse_production()
                reconciler._validate_existing_roots()
                record = reconciler._load_exact(session, inflight=True)
                if (
                    record.state is not JournalState.VALIDATED
                    or reconciler._prefix_length(strict_entries=True) != 4
                ):
                    raise Stage10Error("stage05_authorization_conflict")
                reconciler.refuse_production()
        except JournalConflict as error:
            raise Stage10Error("transaction_busy") from error
        except (JournalSecurityError, Stage05Error) as error:
            raise Stage10Error("stage05_authorization_conflict") from error

    def _file_state(self, path: Path, expected: bytes) -> str:
        raw = self._read_file(path, 0o644, _MAX_FILE_BYTES)
        if raw is None:
            return "absent"
        return "exact" if raw == expected else "conflict"

    def _key_state(self) -> str:
        raw = self._read_file(DOCKER_KEY_PATH, 0o644, _MAX_FILE_BYTES)
        if raw is None:
            return "absent"
        return (
            "captured-pin"
            if hashlib.sha256(raw).hexdigest() == DOCKER_KEY_SHA256
            else "conflict"
        )

    def _legacy_key_states(self) -> tuple[str, ...]:
        present: list[str] = []
        for index, path in enumerate(LEGACY_DOCKER_KEY_PATHS):
            try:
                state = self.tree.path_state(path)
            except Stage05Error as error:
                raise Stage10Error("repository_state_unknown") from error
            if state == "present":
                present.append(f"legacy-key-{index + 1}")
        return tuple(present)

    def _foreign_docker_sources(self) -> tuple[str, ...]:
        conflicts: list[str] = []
        main = self._read_file(APT_SOURCES_LIST, 0o644, _MAX_FILE_BYTES)
        if main is not None and b"download.docker.com" in main.lower():
            conflicts.append("sources-list")
        try:
            state = self.tree.path_state(APT_SOURCE_PARTS)
        except Stage05Error as error:
            raise Stage10Error("repository_state_unknown") from error
        if state == "absent":
            return tuple(conflicts)
        descriptor: int | None = None
        try:
            descriptor = self.tree.open_directory(APT_SOURCE_PARTS)
            entries = self.tree.entries(descriptor)
        except Stage05Error as error:
            raise Stage10Error("repository_state_unknown") from error
        finally:
            if descriptor is not None:
                os.close(descriptor)
        for index, name in enumerate(
            entry for entry in entries if entry.endswith((".list", ".sources"))
        ):
            path = APT_SOURCE_PARTS / name
            raw = self._read_file(path, 0o644, _MAX_FILE_BYTES)
            if (
                raw is not None
                and path != DOCKER_SOURCE_PATH
                and b"download.docker.com" in raw.lower()
            ):
                conflicts.append(f"source-part-{index + 1}")
        return tuple(conflicts)

    def _invoke(self, argv: tuple[str, ...], reason: str) -> CommandOutput:
        try:
            result = self.command(argv)
        except Stage10Error:
            raise
        except Exception as error:
            raise Stage10Error(reason) from error
        if result.stdout.__class__ is not str or result.stderr.__class__ is not str:
            raise Stage10Error(reason)
        return result

    def _package_state(
        self, name: str, *, candidate: bool, probe_index: int
    ) -> PackageState:
        if not 1 <= probe_index <= 99:
            raise Stage10Error("package_probe_index_invalid")
        _safe_token(_PACKAGE_RE, name, "package_inventory_unknown")
        query = self._invoke(
            (
                "/usr/bin/dpkg-query",
                "--show",
                "--showformat=${db:Status-Want}\\t${db:Status-Eflag}\\t"
                "${db:Status-Status}\\t${Version}\\t${Architecture}\\n",
                name,
            ),
            "package_inventory_unknown",
        )
        status = "absent"
        selection = "unknown"
        error_state = "ok"
        installed_version: str | None = None
        architecture: str | None = None
        if query.succeeded:
            fields = query.stdout.rstrip("\n").split("\t")
            if len(fields) != 5 or query.stderr:
                raise Stage10Error("package_inventory_unknown")
            selection = _safe_token(_STATUS_RE, fields[0], "package_inventory_unknown")
            error_state = _safe_token(_STATUS_RE, fields[1], "package_inventory_unknown")
            status = _safe_token(_STATUS_RE, fields[2], "package_inventory_unknown")
            installed_version = _safe_token(
                _VERSION_RE, fields[3], "package_inventory_unknown"
            )
            architecture = _safe_token(_ARCH_RE, fields[4], "package_inventory_unknown")
            if error_state != "ok":
                raise Stage10Error(f"package_status_error_probe_{probe_index:02d}")
        elif query.returncode != 1 or query.stdout:
            raise Stage10Error("package_inventory_unknown")

        candidate_version: str | None = None
        if candidate:
            policy = self._invoke(
                ("/usr/bin/apt-cache", "policy", name),
                "package_resolution_unknown",
            )
            if not policy.succeeded or policy.stderr:
                raise Stage10Error("package_resolution_unknown")
            fields: dict[str, str] = {}
            for line in policy.stdout.splitlines():
                match = _POLICY_FIELD_RE.fullmatch(line)
                if match:
                    if match.group(1) in fields:
                        raise Stage10Error("package_resolution_unknown")
                    fields[match.group(1)] = match.group(2)
            if set(fields) != {"Installed", "Candidate"}:
                raise Stage10Error("package_resolution_unknown")
            if fields["Candidate"] != "(none)":
                candidate_version = _safe_token(
                    _VERSION_RE, fields["Candidate"], "package_resolution_unknown"
                )
            policy_installed = fields["Installed"]
            expected_installed = installed_version if status == "installed" else None
            if (policy_installed == "(none)") != (expected_installed is None):
                raise Stage10Error("package_inventory_unknown")
            if expected_installed is not None and policy_installed != expected_installed:
                raise Stage10Error("package_inventory_unknown")
        return PackageState(
            name,
            selection,
            error_state,
            status,
            installed_version,
            architecture,
            candidate_version,
        )

    def _dpkg_audit(self, phase: str) -> None:
        if phase not in {"pre", "post"}:
            raise Stage10Error("dpkg_audit_phase_invalid")
        result = self._invoke(
            ("/usr/bin/dpkg", "--audit"), f"dpkg_audit_unavailable_{phase}"
        )
        if not result.succeeded:
            raise Stage10Error(f"dpkg_audit_failed_{phase}")
        if result.stdout or result.stderr:
            raise Stage10Error(f"dpkg_audit_dirty_{phase}")

    @contextmanager
    def _package_locks(self) -> Iterator[None]:
        parents: list[int] = []
        locked: list[tuple[int, int, str, os.stat_result]] = []
        try:
            for canonical in PACKAGE_LOCK_PATHS:
                if not hasattr(os, "O_NOFOLLOW"):
                    raise Stage10Error("package_lock_conflict")
                parent = self.tree.open_directory(canonical.parent)
                parents.append(parent)
                descriptor = os.open(
                    canonical.name,
                    os.O_RDWR
                    | getattr(os, "O_CLOEXEC", 0)
                    | os.O_NOFOLLOW,
                    dir_fd=parent,
                )
                metadata = os.fstat(descriptor)
                locked.append((descriptor, parent, canonical.name, metadata))
                if (
                    not stat.S_ISREG(metadata.st_mode)
                    or metadata.st_uid != self.uid
                    or metadata.st_gid != self.gid
                    or metadata.st_nlink != 1
                ):
                    raise Stage10Error("package_lock_conflict")
                fcntl.lockf(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            yield
            for descriptor, parent, name, before in locked:
                after = os.fstat(descriptor)
                published = os.stat(name, dir_fd=parent, follow_symlinks=False)
                if self._stable(before) != self._stable(after) or self._stable(
                    after
                ) != self._stable(published):
                    raise Stage10Error("package_lock_conflict")
        except BlockingIOError as error:
            raise Stage10Error("package_transaction_busy") from error
        except Stage10Error:
            raise
        except OSError as error:
            raise Stage10Error("package_lock_conflict") from error
        finally:
            for descriptor, _parent, _name, _metadata in reversed(locked):
                os.close(descriptor)
            for parent in reversed(parents):
                os.close(parent)

    def _holds(self) -> tuple[str, ...]:
        result = self._invoke(
            ("/usr/bin/apt-mark", "showhold"), "package_inventory_unknown"
        )
        if not result.succeeded or result.stderr:
            raise Stage10Error("package_inventory_unknown")
        holds = tuple(sorted(result.stdout.splitlines()))
        if len(holds) > _MAX_ACTIONS or len(set(holds)) != len(holds):
            raise Stage10Error("package_inventory_unknown")
        for name in holds:
            _safe_token(_PACKAGE_RE, name, "package_inventory_unknown")
        return holds

    def _marks(self, names: tuple[str, ...]) -> tuple[tuple[str, ...], tuple[str, ...]]:
        requested = tuple(sorted(set(names)))
        if len(requested) > _MAX_MARKS:
            raise Stage10Error("package_inventory_unknown")
        observed: dict[str, tuple[str, ...]] = {}
        for mark in ("showmanual", "showauto"):
            values: list[str] = []
            for offset in range(0, len(requested), _MARK_QUERY_SIZE):
                chunk = requested[offset : offset + _MARK_QUERY_SIZE]
                result = self._invoke(
                    ("/usr/bin/apt-mark", mark, *chunk),
                    "package_inventory_unknown",
                )
                if not result.succeeded or result.stderr:
                    raise Stage10Error("package_inventory_unknown")
                values.extend(result.stdout.splitlines())
            ordered = tuple(sorted(values))
            if (
                len(ordered) > _MAX_MARKS
                or len(set(ordered)) != len(ordered)
                or not set(ordered).issubset(requested)
            ):
                raise Stage10Error("package_inventory_unknown")
            for name in ordered:
                _safe_token(_PACKAGE_RE, name, "package_inventory_unknown")
            observed[mark] = ordered
        if set(observed["showmanual"]).intersection(observed["showauto"]):
            raise Stage10Error("package_inventory_unknown")
        return observed["showmanual"], observed["showauto"]

    def _state_file_hash(self, path: Path) -> str:
        raw = self._read_file(path, 0o644, _MAX_STATE_BYTES)
        return "absent" if raw is None else "sha256:" + hashlib.sha256(raw).hexdigest()

    def _platform_bytes(self) -> bytes:
        raw = self._read_file(OS_RELEASE_PATH, 0o644, _MAX_FILE_BYTES)
        if raw is None:
            raise Stage10Error("unsupported_lab_os")
        try:
            text = raw.decode("utf-8", errors="strict")
        except UnicodeError as error:
            raise Stage10Error("unsupported_lab_os") from error
        fields: dict[str, str] = {}
        for line in text.splitlines():
            if not line or line.startswith("#"):
                continue
            if "=" not in line:
                raise Stage10Error("unsupported_lab_os")
            key, value = line.split("=", 1)
            if not _OS_RELEASE_KEY_RE.fullmatch(key) or key in fields:
                raise Stage10Error("unsupported_lab_os")
            value = value.strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
                value = value[1:-1]
            if (
                not value
                or len(value.encode("utf-8")) > 4_096
                or "\\" in value
                or any(ord(character) < 32 or ord(character) == 127 for character in value)
            ):
                raise Stage10Error("unsupported_lab_os")
            fields[key] = value
        codenames = {
            value
            for key in ("VERSION_CODENAME", "UBUNTU_CODENAME")
            if (value := fields.get(key)) is not None
        }
        if (
            fields.get("ID") != "ubuntu"
            or fields.get("VERSION_ID") != "26.04"
            or codenames != {"resolute"}
        ):
            raise Stage10Error("unsupported_lab_os")
        return raw

    def _simulation_actions(
        self, specifications: tuple[str, ...]
    ) -> tuple[PackageAction, ...]:
        result = self._invoke(
            (
                "/usr/bin/apt-get",
                "--simulate",
                "--no-remove",
                "--no-install-recommends",
                "install",
                *specifications,
            ),
            "package_resolution_unknown",
        )
        if not result.succeeded or result.stderr:
            raise Stage10Error("package_resolution_unknown")
        forbidden = (
            "The following packages will be REMOVED:",
            "The following held packages will be changed:",
            "The following packages will be DOWNGRADED:",
            "WARNING: The following packages cannot be authenticated!",
        )
        if any(value in result.stdout or value in result.stderr for value in forbidden):
            raise Stage10Error("package_resolution_conflict")
        actions: list[PackageAction] = []
        for line in result.stdout.splitlines():
            if line.startswith("Remv "):
                raise Stage10Error("package_resolution_conflict")
            if not line.startswith("Inst "):
                continue
            match = _INSTALL_RE.fullmatch(line)
            if match is None:
                raise Stage10Error("package_resolution_unknown")
            action = PackageAction(
                _safe_token(_PACKAGE_RE, match.group("package"), "package_resolution_unknown"),
                _safe_token(_VERSION_RE, match.group("version"), "package_resolution_unknown"),
            )
            actions.append(action)
            if len(actions) > _MAX_ACTIONS:
                raise Stage10Error("package_resolution_oversized")
        ordered = tuple(sorted(actions))
        if len(set(action.name for action in ordered)) != len(ordered):
            raise Stage10Error("package_resolution_unknown")
        return ordered

    def _simulate(self, packages: tuple[PackageState, ...]) -> tuple[PackageAction, ...]:
        requested = tuple(package for package in packages if package.status != "installed")
        if any(package.candidate_version is None for package in requested):
            raise Stage10Error("package_candidate_absent")
        if not requested:
            return ()
        specifications = tuple(
            f"{package.name}={package.candidate_version}" for package in requested
        )
        ordered = self._simulation_actions(specifications)
        selected = {action.name: action.version for action in ordered}
        for package in requested:
            if selected.get(package.name) != package.candidate_version:
                raise Stage10Error("package_resolution_unknown")
        locked = self._simulation_actions(
            tuple(f"{action.name}={action.version}" for action in ordered)
        )
        if locked != ordered:
            raise Stage10Error("package_resolution_unstable")
        return ordered

    def _resolve_authorized(self, operation: str, stage05_digest: str) -> Stage10Result:
        if operation not in {
            "before",
            "execute",
            "after",
            "postconditions",
            "rollback",
            "rollback-postconditions",
        }:
            raise Stage10Error("invalid_mode")
        _safe_token(_HASH_RE, stage05_digest, "stage05_authorization_conflict")
        key_state = self._key_state()
        source_state = self._file_state(DOCKER_SOURCE_PATH, DOCKER_SOURCE_BYTES)
        legacy_docker_keys = self._legacy_key_states()
        foreign_docker_sources = self._foreign_docker_sources()
        platform_bytes = self._platform_bytes()
        ubuntu_archive_keyring = self._state_file_hash(UBUNTU_ARCHIVE_KEYRING_PATH)
        extended_states_hash = self._state_file_hash(APT_EXTENDED_STATES_PATH)
        dpkg_status_hash = self._state_file_hash(DPKG_STATUS_PATH)
        self._dpkg_audit("pre")
        architecture = self._invoke(
            ("/usr/bin/dpkg", "--print-architecture"), "package_inventory_unknown"
        )
        if not architecture.succeeded or architecture.stderr or architecture.stdout != "amd64\n":
            raise Stage10Error("unsupported_lab_architecture")

        packages = tuple(
            self._package_state(name, candidate=True, probe_index=index)
            for index, name in enumerate(PREREQUISITE_PACKAGES, start=1)
        )
        conflicts = tuple(
            self._package_state(name, candidate=False, probe_index=index)
            for index, name in enumerate(DOCKER_CONFLICT_PACKAGES, start=3)
        )
        trust_packages = tuple(
            self._package_state(name, candidate=False, probe_index=index)
            for index, name in enumerate(TRUST_PACKAGES, start=11)
        )
        holds = self._holds()
        actions = self._simulate(packages)
        marked_names = tuple(
            sorted(
                {
                    *PREREQUISITE_PACKAGES,
                    *TRUST_PACKAGES,
                    *DOCKER_CONFLICT_PACKAGES,
                    *(action.name.partition(":")[0] for action in actions),
                }
            )
        )
        manual, automatic = self._marks(marked_names)
        if (
            self._key_state() != key_state
            or self._file_state(DOCKER_SOURCE_PATH, DOCKER_SOURCE_BYTES) != source_state
            or self._legacy_key_states() != legacy_docker_keys
            or self._foreign_docker_sources() != foreign_docker_sources
            or self._state_file_hash(APT_EXTENDED_STATES_PATH) != extended_states_hash
            or self._state_file_hash(DPKG_STATUS_PATH) != dpkg_status_hash
            or self._platform_bytes() != platform_bytes
            or self._state_file_hash(UBUNTU_ARCHIVE_KEYRING_PATH)
            != ubuntu_archive_keyring
        ):
            raise Stage10Error("read_only_state_changed")
        self._dpkg_audit("post")
        installed_conflicts = tuple(
            package.name for package in conflicts if package.status != "absent"
        )
        prerequisites_ready = all(
            package.status == "installed"
            and package.selection in {"install", "hold"}
            and package.error == "ok"
            for package in packages
        )
        trust_ready = ubuntu_archive_keyring != "absent" and all(
            package.status == "installed"
            and package.selection in {"install", "hold"}
            and package.error == "ok"
            for package in trust_packages
        )
        action_names = {action.name.partition(":")[0] for action in actions}
        blocking_holds = (
            set(PREREQUISITE_PACKAGES)
            | set(TRUST_PACKAGES)
            | set(DOCKER_CONFLICT_PACKAGES)
            | action_names
        )
        eligible = (
            not installed_conflicts
            and prerequisites_ready
            and trust_ready
            and key_state == "absent"
            and source_state == "absent"
            and not legacy_docker_keys
            and not foreign_docker_sources
            and not set(holds).intersection(blocking_holds)
        )
        document = {
            "actions": [action.as_dict() for action in actions],
            "apply_authorized": False,
            "architecture": "amd64",
            "apt_extended_states": extended_states_hash,
            "automatic": list(automatic),
            "candidate_scope": "host-cache-untrusted-for-apply",
            "conflicts": [package.as_dict() for package in conflicts],
            "docker_key": {
                "captured_sha256": f"sha256:{DOCKER_KEY_SHA256}",
                "primary_fingerprint": DOCKER_KEY_PRIMARY_FINGERPRINT,
                "signing_fingerprint": DOCKER_KEY_SIGNING_FINGERPRINT,
                "state": key_state,
            },
            "docker_source": {
                "sha256": "sha256:" + hashlib.sha256(DOCKER_SOURCE_BYTES).hexdigest(),
                "state": source_state,
            },
            "dpkg_status": dpkg_status_hash,
            "inventory_clean": eligible,
            "foreign_docker_sources": list(foreign_docker_sources),
            "holds": list(holds),
            "manual": list(manual),
            "legacy_docker_keys": list(legacy_docker_keys),
            "operation": operation,
            "os_release_sha256": "sha256:" + hashlib.sha256(platform_bytes).hexdigest(),
            "packages": [package.as_dict() for package in packages],
            "resolver_bundle_sha256": f"sha256:{self.resolver_bundle_digest}",
            "schema_version": 1,
            "stage": "10-resolution",
            "stage05_bundle_sha256": f"sha256:{stage05_digest}",
            "trust_packages": [package.as_dict() for package in trust_packages],
            "ubuntu_archive_keyring": ubuntu_archive_keyring,
        }
        resolution_bytes = _canonical_json(document)
        resolution_hash = "sha256:" + hashlib.sha256(resolution_bytes).hexdigest()
        return Stage10Result(
            operation,
            f"sha256:{self.resolver_bundle_digest}",
            f"sha256:{stage05_digest}",
            key_state,
            source_state,
            packages,
            conflicts,
            trust_packages,
            holds,
            manual,
            automatic,
            foreign_docker_sources,
            legacy_docker_keys,
            actions,
            resolution_bytes,
            resolution_hash,
            eligible,
        )

    def resolve(self, operation: str) -> Stage10Result:
        with self._authorization_session() as stage05_digest:
            with self._package_locks():
                return self._resolve_authorized(operation, stage05_digest)


def main(arguments: list[str] | None = None) -> int:
    values = list(sys.argv[1:] if arguments is None else arguments)
    if len(values) != 3 or values[0] not in {
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
        result = Stage10Resolver(digest).resolve(mode)
    except Stage10Error as error:
        print(f"status=error reason={error.reason}", file=sys.stderr)
        return 65
    except Exception:
        print("status=error reason=internal_error", file=sys.stderr)
        return 70
    print(result.evidence())
    return 0
