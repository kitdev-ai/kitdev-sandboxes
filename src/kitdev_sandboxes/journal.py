"""Crash-consistent write-ahead journal primitives for installation phases.

The journal durably records caller-declared prior/desired states and lifecycle
transitions.  It does not inspect or reconcile resources itself: an apply or
rollback engine must verify every resource before and after mutation and only
then record the corresponding transition.
"""

from __future__ import annotations

import fcntl
import json
import os
import re
import secrets
import stat
import unicodedata
from contextlib import contextmanager
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Callable, Iterator
from urllib.parse import unquote


class JournalError(RuntimeError):
    """Base class for journal failures."""


class JournalConflict(JournalError):
    """The journal is locked, already exists, or does not match the plan."""


class JournalSecurityError(JournalError):
    """A path, ownership, mode, or file-type security check failed."""


class JournalCorrupt(JournalError):
    """Stored journal bytes do not conform to the journal contract."""


class JournalState(StrEnum):
    PLANNED = "planned"
    APPLYING = "applying"
    APPLIED = "applied"
    VALIDATED = "validated"
    ROLLING_BACK = "rolling_back"
    ROLLED_BACK = "rolled_back"
    FAILED = "failed"


_ID_RE = re.compile(r"^[a-z][a-z0-9_.-]{0,127}$")
_HASH_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f-\x9f]")
_SECRET_RE = re.compile(
    r"(?:authorization|cookie|password|secret|token|api[_-]?key|access[_-]?key|"
    r"credential|x-amz-(?:signature|credential|security-token))\s*[:=]",
    re.IGNORECASE,
)
_MAX_TEXT_BYTES = 4_096
_MAX_RESOURCES = 512
_MAX_TRANSITIONS = 4_096
_MAX_JOURNAL_BYTES = 1_048_576
_MAX_DIRECTORY_ENTRIES = 4_096
_FILE_MODE = 0o600
_DIRECTORY_MODE = 0o700
_ALLOWED_TRANSITIONS: dict[JournalState, frozenset[JournalState]] = {
    JournalState.PLANNED: frozenset({JournalState.APPLYING, JournalState.FAILED}),
    JournalState.APPLYING: frozenset(
        {JournalState.APPLIED, JournalState.ROLLING_BACK, JournalState.FAILED}
    ),
    JournalState.APPLIED: frozenset(
        {JournalState.VALIDATED, JournalState.ROLLING_BACK, JournalState.FAILED}
    ),
    JournalState.VALIDATED: frozenset({JournalState.ROLLING_BACK}),
    JournalState.ROLLING_BACK: frozenset(
        {JournalState.ROLLED_BACK, JournalState.FAILED}
    ),
    JournalState.ROLLED_BACK: frozenset(),
    JournalState.FAILED: frozenset({JournalState.ROLLING_BACK}),
}


def _stable_metadata(metadata: os.stat_result) -> tuple[int, ...]:
    return (
        metadata.st_mode,
        metadata.st_uid,
        metadata.st_gid,
        metadata.st_nlink,
        metadata.st_size,
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_mtime_ns,
        metadata.st_ctime_ns,
    )


def _text(name: str, value: object, *, stable_id: bool = False) -> str:
    if value.__class__ is not str:
        raise TypeError(f"{name} must be a string")
    try:
        size = len(value.encode("utf-8", errors="strict"))
    except UnicodeEncodeError as error:
        raise ValueError(f"{name} is not valid UTF-8") from error
    decoded = value
    for _ in range(2):
        next_value = unquote(decoded)
        if next_value == decoded:
            break
        decoded = next_value
    if (
        not value
        or size > _MAX_TEXT_BYTES
        or _CONTROL_RE.search(decoded)
        or any(unicodedata.category(character) == "Cf" for character in decoded)
        or _SECRET_RE.search(decoded)
        or "-----BEGIN " in decoded.upper()
    ):
        raise ValueError(
            f"{name} is empty, oversized, contains controls, or resembles secret material"
        )
    if stable_id and not _ID_RE.fullmatch(value):
        raise ValueError(f"{name} is not a stable identifier")
    return value


def _hash(name: str, value: object) -> str:
    if value.__class__ is not str or not _HASH_RE.fullmatch(value):
        raise ValueError(f"{name} must be a lowercase sha256 digest")
    return value


def _safe_path_component(component: str) -> bool:
    try:
        encoded = component.encode("utf-8", errors="strict")
    except UnicodeEncodeError:
        return False
    return (
        component not in {"", ".", ".."}
        and len(encoded) <= 255
        and not _CONTROL_RE.search(component)
        and not any(unicodedata.category(character) == "Cf" for character in component)
    )


@dataclass(frozen=True, order=True)
class ResourceRecord:
    """Exact secret-free caller observation for one named resource.

    These strings are durable evidence, not host reconciliation.  The caller
    must compare the real resource with ``prior_state`` before mutation and
    with ``desired_state`` before recording APPLIED or VALIDATED.
    """

    resource_id: str
    target: str
    prior_state: str
    desired_state: str

    def __post_init__(self) -> None:
        _text("resource_id", self.resource_id, stable_id=True)
        _text("target", self.target)
        decoded_target = unquote(unquote(self.target))
        if (
            decoded_target.startswith("//")
            or "//" in decoded_target
            or (decoded_target != "/" and decoded_target.endswith("/"))
            or any(component in {".", ".."} for component in decoded_target.split("/"))
        ):
            raise ValueError("target contains an ambiguous or traversing path component")
        _text("prior_state", self.prior_state)
        _text("desired_state", self.desired_state)

    def as_dict(self) -> dict[str, str]:
        return {
            "id": self.resource_id,
            "target": self.target,
            "prior_state": self.prior_state,
            "desired_state": self.desired_state,
        }


@dataclass(frozen=True)
class JournalRecord:
    install_id: str
    plan_id: str
    plan_hash: str
    resources: tuple[ResourceRecord, ...]
    transitions: tuple[JournalState, ...] = (JournalState.PLANNED,)
    schema_version: int = 1

    def __post_init__(self) -> None:
        if self.schema_version != 1 or self.schema_version.__class__ is not int:
            raise ValueError("journal schema_version must be 1")
        _text("install_id", self.install_id, stable_id=True)
        _text("plan_id", self.plan_id, stable_id=True)
        _hash("plan_hash", self.plan_hash)
        if self.resources.__class__ is not tuple or not all(
            item.__class__ is ResourceRecord for item in self.resources
        ):
            raise TypeError("resources must be an exact tuple of ResourceRecord values")
        if not self.resources or len(self.resources) > _MAX_RESOURCES:
            raise ValueError("resources is empty or exceeds its bound")
        if tuple(sorted(self.resources)) != self.resources:
            raise ValueError("resources must be sorted canonically")
        ids = tuple(item.resource_id for item in self.resources)
        if len(set(ids)) != len(ids):
            raise ValueError("resource identifiers must be unique")
        if self.transitions.__class__ is not tuple or not all(
            item.__class__ is JournalState for item in self.transitions
        ):
            raise TypeError("transitions must be an exact tuple of JournalState values")
        if not self.transitions or self.transitions[0] is not JournalState.PLANNED:
            raise ValueError("the first transition must be planned")
        if len(self.transitions) > _MAX_TRANSITIONS:
            raise ValueError("transition history exceeds its bound")
        for previous, current in zip(self.transitions, self.transitions[1:], strict=False):
            if current not in _ALLOWED_TRANSITIONS[previous]:
                raise ValueError(f"invalid journal transition {previous.value}->{current.value}")

    @property
    def state(self) -> JournalState:
        return self.transitions[-1]

    def as_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "install_id": self.install_id,
            "plan_id": self.plan_id,
            "plan_hash": self.plan_hash,
            "resources": [resource.as_dict() for resource in self.resources],
            "state": self.state.value,
            "transitions": [state.value for state in self.transitions],
        }


def _encode(record: JournalRecord) -> bytes:
    encoded = (
        json.dumps(record.as_dict(), sort_keys=True, separators=(",", ":"), ensure_ascii=True)
        + "\n"
    ).encode("ascii")
    if len(encoded) > _MAX_JOURNAL_BYTES:
        raise ValueError("journal exceeds its global byte limit")
    return encoded


def _decode(raw: bytes) -> JournalRecord:
    if not raw or len(raw) > _MAX_JOURNAL_BYTES:
        raise JournalCorrupt("journal is empty or oversized")
    try:
        payload = json.loads(raw.decode("utf-8", errors="strict"))
        if payload.__class__ is not dict or set(payload) != {
            "schema_version", "install_id", "plan_id", "plan_hash", "resources",
            "state", "transitions",
        }:
            raise ValueError("invalid top-level shape")
        raw_resources = payload["resources"]
        if raw_resources.__class__ is not list:
            raise ValueError("resources is not a list")
        resources = tuple(
            ResourceRecord(
                resource_id=item["id"],
                target=item["target"],
                prior_state=item["prior_state"],
                desired_state=item["desired_state"],
            )
            for item in raw_resources
            if item.__class__ is dict
            and set(item) == {"id", "target", "prior_state", "desired_state"}
        )
        if len(resources) != len(raw_resources):
            raise ValueError("invalid resource shape")
        raw_transitions = payload["transitions"]
        if raw_transitions.__class__ is not list:
            raise ValueError("transitions is not a list")
        record = JournalRecord(
            install_id=payload["install_id"],
            plan_id=payload["plan_id"],
            plan_hash=payload["plan_hash"],
            resources=resources,
            transitions=tuple(JournalState(item) for item in raw_transitions),
            schema_version=payload["schema_version"],
        )
        if payload["state"] != record.state.value or _encode(record) != raw:
            raise ValueError("journal is not canonical or state is inconsistent")
        return record
    except (KeyError, TypeError, ValueError, UnicodeDecodeError) as error:
        raise JournalCorrupt("journal content is invalid") from error


@dataclass(frozen=True)
class JournalSecurityPolicy:
    expected_owner_uid: int = 0
    directory_mode: int = _DIRECTORY_MODE
    file_mode: int = _FILE_MODE
    stat_validator: Callable[[os.stat_result, bool], bool] | None = None
    trusted_prefix: Path | None = None
    expected_owner_gid: int = field(default_factory=os.getegid)

    def __post_init__(self) -> None:
        if self.expected_owner_uid.__class__ is not int or self.expected_owner_uid < 0:
            raise ValueError("expected_owner_uid must be a non-negative integer")
        if self.expected_owner_gid.__class__ is not int or self.expected_owner_gid < 0:
            raise ValueError("expected_owner_gid must be a non-negative integer")
        for name, mode in (
            ("directory_mode", self.directory_mode),
            ("file_mode", self.file_mode),
        ):
            if mode.__class__ is not int or not 0 <= mode <= 0o7777:
                raise ValueError(f"{name} must be a valid mode")
        if self.trusted_prefix is not None:
            if not isinstance(self.trusted_prefix, Path):
                raise TypeError("trusted_prefix must be a pathlib.Path")
            rendered = str(self.trusted_prefix)
            if (
                not self.trusted_prefix.is_absolute()
                or rendered == "/"
                or rendered.startswith("//")
                or any(
                    not _safe_path_component(component)
                    for component in self.trusted_prefix.parts[1:]
                )
            ):
                raise ValueError("trusted_prefix must be a safe non-root absolute path")

    def accepts(
        self,
        metadata: os.stat_result,
        directory: bool,
        *,
        allowed_nlinks: frozenset[int] = frozenset({1}),
    ) -> bool:
        expected_type = stat.S_ISDIR if directory else stat.S_ISREG
        expected_mode = self.directory_mode if directory else self.file_mode
        accepted = (
            expected_type(metadata.st_mode)
            and metadata.st_uid == self.expected_owner_uid
            and metadata.st_gid == self.expected_owner_gid
            and stat.S_IMODE(metadata.st_mode) == expected_mode
            and (directory or metadata.st_nlink in allowed_nlinks)
        )
        return accepted and (
            self.stat_validator is None or self.stat_validator(metadata, directory)
        )

    def accepts_ancestor(self, metadata: os.stat_result, *, below_trusted: bool) -> bool:
        """Validate one opened directory in the root ancestry.

        Production defaults require root ownership throughout.  A deliberately
        injected trusted prefix permits tests to cross platform-managed
        temporary-directory ancestry; below that boundary, the configured test
        owner must own every component and group/other writes remain forbidden.
        """

        if not stat.S_ISDIR(metadata.st_mode):
            return False
        owner = self.expected_owner_uid if below_trusted else 0
        group = self.expected_owner_gid if below_trusted else 0
        return (
            metadata.st_uid == owner
            and metadata.st_gid == group
            and not (stat.S_IMODE(metadata.st_mode) & 0o022)
        )


class JournalStore:
    """A no-follow journal store rooted in an existing trusted directory."""

    def __init__(self, root: Path, policy: JournalSecurityPolicy | None = None):
        if not isinstance(root, Path):
            raise TypeError("journal root must be a pathlib.Path")
        self._root = root
        self._policy = policy if policy is not None else JournalSecurityPolicy()

    def _open_root(self) -> int:
        if not self._root.is_absolute():
            raise JournalSecurityError("journal root must be absolute")
        rendered = str(self._root)
        parts = self._root.parts
        if (
            not rendered.startswith("/")
            or rendered == "/"
            or rendered.startswith("//")
            or any(not _safe_path_component(component) for component in parts[1:])
        ):
            raise JournalSecurityError("journal root must be a safe non-root path")
        flags = os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_CLOEXEC", 0)
        descriptor = os.open("/", flags)
        try:
            trusted_parts = (
                self._policy.trusted_prefix.parts
                if self._policy.trusted_prefix is not None
                else None
            )
            if trusted_parts is not None and parts[: len(trusted_parts)] != trusted_parts:
                raise JournalSecurityError("trusted prefix is not an ancestor of journal root")
            if not self._policy.accepts_ancestor(
                os.fstat(descriptor), below_trusted=trusted_parts == ("/",)
            ):
                raise JournalSecurityError("journal root ancestry is not securely owned")
            opened_parts = ["/"]
            for component in parts[1:]:
                next_descriptor = os.open(
                    component, flags | getattr(os, "O_NOFOLLOW", 0), dir_fd=descriptor
                )
                os.close(descriptor)
                descriptor = next_descriptor
                opened_parts.append(component)
                below_trusted = (
                    trusted_parts is not None
                    and tuple(opened_parts[: len(trusted_parts)]) == trusted_parts
                    and len(opened_parts) >= len(trusted_parts)
                )
                if trusted_parts is not None and tuple(opened_parts) == trusted_parts:
                    # The boundary is trusted explicitly; its descendants are
                    # still checked against the injected owner and write bits.
                    continue
                if not self._policy.accepts_ancestor(
                    os.fstat(descriptor), below_trusted=below_trusted
                ):
                    raise JournalSecurityError("journal root ancestry is not securely owned")
            if not self._policy.accepts(os.fstat(descriptor), True):
                raise JournalSecurityError("journal root owner, mode, or type is unsafe")
            return descriptor
        except JournalSecurityError:
            os.close(descriptor)
            raise
        except OSError as error:
            os.close(descriptor)
            raise JournalSecurityError("journal root cannot be opened without symlinks") from error

    @staticmethod
    def _filename(install_id: str) -> str:
        _text("install_id", install_id, stable_id=True)
        return f"{install_id}.journal.json"

    def _read_at(
        self,
        root_fd: int,
        filename: str,
        *,
        allowed_nlinks: frozenset[int] = frozenset({1}),
    ) -> tuple[JournalRecord, os.stat_result]:
        flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
        try:
            descriptor = os.open(filename, flags, dir_fd=root_fd)
        except OSError as error:
            raise JournalSecurityError("journal cannot be opened safely") from error
        try:
            before = os.fstat(descriptor)
            if not self._policy.accepts(
                before, False, allowed_nlinks=allowed_nlinks
            ):
                raise JournalSecurityError("journal owner, mode, or type is unsafe")
            chunks: list[bytes] = []
            remaining = _MAX_JOURNAL_BYTES + 1
            while remaining:
                chunk = os.read(descriptor, min(65_536, remaining))
                if not chunk:
                    break
                chunks.append(chunk)
                remaining -= len(chunk)
            after = os.fstat(descriptor)
            try:
                published = os.stat(filename, dir_fd=root_fd, follow_symlinks=False)
            except OSError as error:
                raise JournalSecurityError(
                    "journal name changed while it was being read"
                ) from error
            if (
                _stable_metadata(before) != _stable_metadata(after)
                or _stable_metadata(after) != _stable_metadata(published)
                or not self._policy.accepts(
                    published, False, allowed_nlinks=allowed_nlinks
                )
            ):
                raise JournalSecurityError("journal changed while it was being read")
            return _decode(b"".join(chunks)), after
        finally:
            os.close(descriptor)

    def _load_at(self, root_fd: int, filename: str, *, recover: bool) -> JournalRecord:
        record, metadata = self._read_at(
            root_fd, filename, allowed_nlinks=frozenset({1, 2})
        )
        if metadata.st_nlink == 1:
            return record
        if not recover:
            raise JournalSecurityError("journal has an unresolved hard-link residue")
        self._recover_linked_create(root_fd, filename, record, metadata)
        recovered, _ = self._read_at(root_fd, filename)
        return recovered

    def _recover_linked_create(
        self,
        root_fd: int,
        filename: str,
        expected: JournalRecord,
        final_metadata: os.stat_result,
    ) -> None:
        candidate = self._validate_linked_create(
            root_fd, filename, expected, final_metadata
        )
        os.unlink(candidate, dir_fd=root_fd)
        os.fsync(root_fd)

    def _validate_linked_create(
        self,
        root_fd: int,
        filename: str,
        expected: JournalRecord,
        final_metadata: os.stat_result,
    ) -> str:
        prefix = f".{filename}.tmp."
        entries = self._directory_entries(root_fd)
        candidates = sorted(name for name in entries if name.startswith(prefix))
        if len(candidates) != 1 or not re.fullmatch(
            re.escape(prefix) + r"[0-9a-f]{32}", candidates[0]
        ):
            raise JournalSecurityError("journal has an unexplained hard link")
        residue, residue_metadata = self._read_at(
            root_fd, candidates[0], allowed_nlinks=frozenset({2})
        )
        if (
            residue != expected
            or residue_metadata.st_dev != final_metadata.st_dev
            or residue_metadata.st_ino != final_metadata.st_ino
        ):
            raise JournalSecurityError("journal hard-link residue is not canonical")
        return candidates[0]

    def _inspect_residue_at(
        self, root_fd: int, filename: str
    ) -> tuple[JournalRecord, int, tuple[JournalRecord, ...]]:
        record, metadata = self._read_at(
            root_fd, filename, allowed_nlinks=frozenset({1, 2})
        )
        prefix = f".{filename}.tmp."
        candidates = sorted(
            name for name in self._directory_entries(root_fd) if name.startswith(prefix)
        )
        if metadata.st_nlink == 2:
            self._validate_linked_create(root_fd, filename, record, metadata)
            return record, 2, (record,)
        if not candidates:
            return record, 1, ()
        if len(candidates) != 1 or not re.fullmatch(
            re.escape(prefix) + r"[0-9a-f]{32}", candidates[0]
        ):
            raise JournalSecurityError("journal has unexplained temporary residue")
        residue, _ = self._read_at(
            root_fd, candidates[0], allowed_nlinks=frozenset({1})
        )
        return record, 1, (residue,)

    def _load_inflight_at(self, root_fd: int, filename: str) -> JournalRecord:
        record, link_count, residues = self._inspect_residue_at(root_fd, filename)
        if link_count == 1 and residues:
            raise JournalSecurityError("journal has unexplained temporary residue")
        return record

    def _load_unpublished_at(self, root_fd: int, filename: str) -> JournalRecord:
        try:
            os.stat(filename, dir_fd=root_fd, follow_symlinks=False)
        except FileNotFoundError:
            pass
        except OSError as error:
            raise JournalSecurityError("journal final name cannot be classified") from error
        else:
            raise JournalSecurityError("journal final name is already published")
        prefix = f".{filename}.tmp."
        candidates = sorted(
            name for name in self._directory_entries(root_fd) if name.startswith(prefix)
        )
        if len(candidates) != 1 or not re.fullmatch(
            re.escape(prefix) + r"[0-9a-f]{32}", candidates[0]
        ):
            raise JournalSecurityError("journal unpublished residue is not unique")
        record, _ = self._read_at(
            root_fd, candidates[0], allowed_nlinks=frozenset({1})
        )
        return record

    @staticmethod
    def _directory_entries(root_fd: int) -> list[str]:
        entries: list[str] = []
        total_bytes = 0
        try:
            with os.scandir(root_fd) as iterator:
                for entry in iterator:
                    name = entry.name
                    if not _safe_path_component(name):
                        raise JournalSecurityError(
                            "journal directory contains an unsafe entry name"
                        )
                    total_bytes += len(name.encode("utf-8", errors="strict"))
                    entries.append(name)
                    if (
                        len(entries) > _MAX_DIRECTORY_ENTRIES
                        or total_bytes > _MAX_JOURNAL_BYTES
                    ):
                        raise JournalSecurityError(
                            "journal directory contains too many entries"
                        )
        except OSError as error:
            raise JournalSecurityError(
                "journal directory cannot be enumerated safely"
            ) from error
        return entries

    def _new_temp(self, root_fd: int, filename: str, record: JournalRecord) -> str:
        payload = _encode(record)
        flags = (
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0)
        )
        descriptor: int | None = None
        temp_name = ""
        for _ in range(8):
            temp_name = f".{filename}.tmp.{secrets.token_hex(16)}"
            try:
                descriptor = os.open(
                    temp_name, flags, self._policy.file_mode, dir_fd=root_fd
                )
                break
            except FileExistsError:
                continue
        if descriptor is None:
            raise JournalConflict("could not allocate an exclusive journal temporary file")
        try:
            if not self._policy.accepts(os.fstat(descriptor), False):
                raise JournalSecurityError("temporary journal owner, mode, or type is unsafe")
            offset = 0
            while offset < len(payload):
                written = os.write(descriptor, payload[offset:])
                if written <= 0:
                    raise JournalError("journal write made no progress")
                offset += written
            os.fsync(descriptor)
        except Exception:
            try:
                os.unlink(temp_name, dir_fd=root_fd)
            except OSError:
                pass
            raise
        finally:
            os.close(descriptor)
        return temp_name

    def _cleanup_stale_temps(
        self, root_fd: int, filename: str, expected: tuple[JournalRecord, ...]
    ) -> None:
        prefix = f".{filename}.tmp."
        entries = self._directory_entries(root_fd)
        candidates = sorted(name for name in entries if name.startswith(prefix))
        if len(candidates) > 1:
            raise JournalSecurityError("multiple journal temporary artifacts exist")
        changed = False
        for name in candidates:
            if not re.fullmatch(re.escape(prefix) + r"[0-9a-f]{32}", name):
                raise JournalSecurityError("suspicious journal temporary artifact exists")
            try:
                residue, residue_metadata = self._read_at(
                    root_fd, name, allowed_nlinks=frozenset({1, 2})
                )
            except JournalError:
                raise JournalSecurityError(
                    "journal temporary artifact is not owned canonical residue"
                ) from None
            if residue not in expected:
                raise JournalConflict("journal temporary artifact belongs to another plan state")
            if residue_metadata.st_nlink == 2:
                try:
                    final_metadata = os.stat(
                        filename, dir_fd=root_fd, follow_symlinks=False
                    )
                except OSError as error:
                    raise JournalSecurityError(
                        "linked journal residue has no published record"
                    ) from error
                if (
                    final_metadata.st_dev != residue_metadata.st_dev
                    or final_metadata.st_ino != residue_metadata.st_ino
                ):
                    raise JournalSecurityError(
                        "linked journal residue does not match the published record"
                    )
            os.unlink(name, dir_fd=root_fd)
            changed = True
        if changed:
            os.fsync(root_fd)

    def _atomic_create(self, root_fd: int, filename: str, record: JournalRecord) -> None:
        temp_name = self._new_temp(root_fd, filename, record)
        try:
            try:
                os.link(
                    temp_name,
                    filename,
                    src_dir_fd=root_fd,
                    dst_dir_fd=root_fd,
                    follow_symlinks=False,
                )
            except FileExistsError as error:
                raise JournalConflict("journal already exists") from error
            os.fsync(root_fd)
            os.unlink(temp_name, dir_fd=root_fd)
            os.fsync(root_fd)
        except Exception:
            try:
                os.unlink(temp_name, dir_fd=root_fd)
            except OSError:
                pass
            raise

    def _atomic_replace(self, root_fd: int, filename: str, record: JournalRecord) -> None:
        temp_name = self._new_temp(root_fd, filename, record)
        try:
            os.replace(temp_name, filename, src_dir_fd=root_fd, dst_dir_fd=root_fd)
            os.fsync(root_fd)
        except Exception:
            try:
                os.unlink(temp_name, dir_fd=root_fd)
            except OSError:
                pass
            raise

    def _create_at(self, root_fd: int, record: JournalRecord) -> JournalRecord:
        if record.state is not JournalState.PLANNED or record.transitions != (
            JournalState.PLANNED,
        ):
            raise JournalConflict("new journal must contain only the initial planned state")
        filename = self._filename(record.install_id)
        self._cleanup_stale_temps(root_fd, filename, (record,))
        try:
            os.stat(filename, dir_fd=root_fd, follow_symlinks=False)
        except FileNotFoundError:
            pass
        else:
            raise JournalConflict("journal already exists")
        self._atomic_create(root_fd, filename, record)
        return self._load_at(root_fd, filename, recover=True)

    def create(self, record: JournalRecord) -> JournalRecord:
        with self.locked() as session:
            return session.create(record)

    def load(self, install_id: str) -> JournalRecord:
        with self.locked() as session:
            return session.load(install_id)

    @staticmethod
    def _assert_exact(
        record: JournalRecord,
        plan_id: str,
        plan_hash: str,
        resources: tuple[ResourceRecord, ...],
    ) -> None:
        _text("plan_id", plan_id, stable_id=True)
        _hash("plan_hash", plan_hash)
        if (
            record.plan_id != plan_id
            or record.plan_hash != plan_hash
            or record.resources != resources
        ):
            raise JournalConflict("journal does not exactly match the requested plan and resources")

    def resume(
        self,
        install_id: str,
        plan_id: str,
        plan_hash: str,
        resources: tuple[ResourceRecord, ...],
    ) -> JournalRecord:
        with self.locked() as session:
            return session.resume(install_id, plan_id, plan_hash, resources)

    def transition(
        self,
        install_id: str,
        plan_id: str,
        plan_hash: str,
        resources: tuple[ResourceRecord, ...],
        state: JournalState,
    ) -> JournalRecord:
        with self.locked() as session:
            return session.transition(
                install_id, plan_id, plan_hash, resources, state
            )

    def _transition_at(
        self,
        root_fd: int,
        install_id: str,
        plan_id: str,
        plan_hash: str,
        resources: tuple[ResourceRecord, ...],
        state: JournalState,
    ) -> JournalRecord:
        if state.__class__ is not JournalState:
            raise TypeError("state must be a JournalState")
        filename = self._filename(install_id)
        record = self._load_at(root_fd, filename, recover=True)
        self._assert_exact(record, plan_id, plan_hash, resources)
        if state not in _ALLOWED_TRANSITIONS[record.state]:
            raise JournalConflict(
                f"transition {record.state.value}->{state.value} is not allowed"
            )
        updated = JournalRecord(
            record.install_id,
            record.plan_id,
            record.plan_hash,
            record.resources,
            record.transitions + (state,),
        )
        self._cleanup_stale_temps(root_fd, filename, (record, updated))
        self._atomic_replace(root_fd, filename, updated)
        return self._load_at(root_fd, filename, recover=True)

    @contextmanager
    def locked(self, *, exclusive: bool = True) -> Iterator[JournalSession]:
        if exclusive.__class__ is not bool:
            raise TypeError("exclusive must be a bool")
        root_fd = self._open_root()
        operation = fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH
        try:
            try:
                fcntl.flock(root_fd, operation | fcntl.LOCK_NB)
            except BlockingIOError as error:
                raise JournalConflict("journal directory is locked") from error
            session = JournalSession(self, root_fd, exclusive=exclusive)
            try:
                yield session
            finally:
                session._close()
        finally:
            os.close(root_fd)


class JournalSession:
    """Caller-held journal lock and validated root descriptor."""

    def __init__(self, store: JournalStore, root_fd: int, *, exclusive: bool):
        self._store = store
        self._root_fd = root_fd
        self._exclusive = exclusive
        self._closed = False

    def _require_open(self) -> None:
        if self._closed:
            raise JournalConflict("journal session is closed")

    def _require_exclusive(self) -> None:
        self._require_open()
        if not self._exclusive:
            raise JournalConflict("journal mutation requires an exclusive session")

    def _close(self) -> None:
        self._closed = True

    def create(self, record: JournalRecord) -> JournalRecord:
        self._require_exclusive()
        return self._store._create_at(self._root_fd, record)

    def load(self, install_id: str) -> JournalRecord:
        self._require_open()
        return self._store._load_at(
            self._root_fd,
            self._store._filename(install_id),
            recover=self._exclusive,
        )

    def load_inflight(self, install_id: str) -> JournalRecord:
        """Read a canonical linked-create residue without mutating it."""

        self._require_open()
        return self._store._load_inflight_at(
            self._root_fd,
            self._store._filename(install_id),
        )

    def inspect_residue(
        self, install_id: str
    ) -> tuple[JournalRecord, int, tuple[JournalRecord, ...]]:
        """Read the final and one canonical temp without repairing either."""

        self._require_open()
        return self._store._inspect_residue_at(
            self._root_fd,
            self._store._filename(install_id),
        )

    def load_unpublished(self, install_id: str) -> JournalRecord:
        """Read one canonical unpublished create residue without mutating it."""

        self._require_open()
        return self._store._load_unpublished_at(
            self._root_fd,
            self._store._filename(install_id),
        )

    def cleanup_temps(
        self, install_id: str, expected: tuple[JournalRecord, ...]
    ) -> None:
        """Remove only canonical residue matching caller-approved journal states."""

        self._require_exclusive()
        if expected.__class__ is not tuple or not expected:
            raise TypeError("expected must be a nonempty tuple")
        self._store._cleanup_stale_temps(
            self._root_fd,
            self._store._filename(install_id),
            expected,
        )

    def resume(
        self,
        install_id: str,
        plan_id: str,
        plan_hash: str,
        resources: tuple[ResourceRecord, ...],
    ) -> JournalRecord:
        record = self.load(install_id)
        self._store._assert_exact(record, plan_id, plan_hash, resources)
        if record.state in {JournalState.VALIDATED, JournalState.ROLLED_BACK}:
            raise JournalConflict("completed journal cannot be resumed")
        return record

    def transition(
        self,
        install_id: str,
        plan_id: str,
        plan_hash: str,
        resources: tuple[ResourceRecord, ...],
        state: JournalState,
    ) -> JournalRecord:
        self._require_exclusive()
        return self._store._transition_at(
            self._root_fd, install_id, plan_id, plan_hash, resources, state
        )
