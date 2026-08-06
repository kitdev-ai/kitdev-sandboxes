"""Typed, read-only Linux fact collectors.

This module only observes host state.  It deliberately does not evaluate policy,
load kernel modules, create missing paths, or invoke a shell.
"""

from __future__ import annotations

import ipaddress
import json
import os
import re
import stat
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any, Generic, Iterable, Mapping, Protocol, TypeVar

from kitdev_sandboxes.preflight import safe_report_text
from kitdev_sandboxes.runner import Command, CommandResult


class CollectionStatus(StrEnum):
    OK = "ok"
    ABSENT = "absent"
    UNKNOWN = "unknown"
    PERMISSION_DENIED = "permission_denied"
    TIMEOUT = "timeout"
    TRUNCATED = "truncated"
    ERROR = "error"


T = TypeVar("T")


@dataclass(frozen=True)
class Probe(Generic[T]):
    status: CollectionStatus
    value: T | None = None
    raw: str | None = None
    source: str = ""
    elapsed_ms: int | None = None

    @classmethod
    def ok(
        cls,
        value: T,
        *,
        raw: str | None = None,
        source: str,
        elapsed_ms: int | None = None,
    ) -> Probe[T]:
        return cls(CollectionStatus.OK, value, _evidence(raw), source, elapsed_ms)

    @classmethod
    def degraded(
        cls,
        status: CollectionStatus,
        *,
        source: str,
        raw: str | None = None,
        elapsed_ms: int | None = None,
    ) -> Probe[T]:
        if status is CollectionStatus.OK:
            raise ValueError("degraded probe cannot have OK status")
        return cls(status, None, _evidence(raw), source, elapsed_ms)


class ReadText(Protocol):
    def __call__(self, path: Path, maximum_bytes: int) -> Probe[str]: ...


class StatPath(Protocol):
    def __call__(self, path: Path) -> Probe[os.stat_result]: ...


@dataclass(frozen=True)
class FilesystemStat:
    total_bytes: int
    available_bytes: int
    total_inodes: int
    available_inodes: int


class StatFilesystem(Protocol):
    def __call__(self, path: Path) -> Probe[FilesystemStat]: ...


class GlobPaths(Protocol):
    def __call__(self, pattern: str) -> Probe[tuple[Path, ...]]: ...


class RunCommands(Protocol):
    def run(self, command: Command) -> CommandResult: ...


@dataclass(frozen=True)
class NbdDevice:
    name: str
    in_use: Probe[bool]


@dataclass(frozen=True)
class NbdFacts:
    module_loaded: Probe[bool]
    max_devices: Probe[int]
    max_partitions: Probe[int]
    devices: Probe[tuple[NbdDevice, ...]]


@dataclass(frozen=True)
class HugePageFacts:
    size_kib: Probe[int]
    total: Probe[int]
    free: Probe[int]
    reserved: Probe[int]
    surplus: Probe[int]
    mounts: Probe[tuple[str, ...]]


@dataclass(frozen=True)
class MemoryFacts:
    total_bytes: Probe[int]
    available_bytes: Probe[int]
    swap_total_bytes: Probe[int]
    swap_free_bytes: Probe[int]


@dataclass(frozen=True)
class FilesystemFacts:
    configured_path: str
    containing_path: str | None
    total_bytes: Probe[int]
    available_bytes: Probe[int]
    total_inodes: Probe[int]
    available_inodes: Probe[int]
    filesystem_type: Probe[str]
    mount_options: Probe[tuple[str, ...]]


@dataclass(frozen=True)
class DeviceFacts:
    kvm_modules: Probe[tuple[str, ...]]
    nbd: NbdFacts
    huge_pages: HugePageFacts
    tun_exists: Probe[bool]
    tun_is_character_device: Probe[bool]


@dataclass(frozen=True)
class ToolFacts:
    present: Probe[bool]
    version: Probe[str]
    active: Probe[bool]


class Ownership(StrEnum):
    UNKNOWN = "unknown"
    SHARED = "shared"
    PROJECT = "project"


class SystemdActiveState(StrEnum):
    ACTIVE = "active"
    INACTIVE = "inactive"
    FAILED = "failed"
    ACTIVATING = "activating"
    DEACTIVATING = "deactivating"
    RELOADING = "reloading"
    MAINTENANCE = "maintenance"


class SystemdUnitFileState(StrEnum):
    ENABLED = "enabled"
    ENABLED_RUNTIME = "enabled-runtime"
    DISABLED = "disabled"
    STATIC = "static"
    INDIRECT = "indirect"
    ALIAS = "alias"
    MASKED = "masked"
    MASKED_RUNTIME = "masked-runtime"
    GENERATED = "generated"
    TRANSIENT = "transient"
    BAD = "bad"
    NOT_FOUND = "not-found"


class AddressFamily(StrEnum):
    IPV4 = "ipv4"
    IPV6 = "ipv6"


class BindScope(StrEnum):
    LOOPBACK = "loopback"
    WILDCARD = "wildcard"
    HOST = "host"


@dataclass(frozen=True)
class Listener:
    protocol: str
    family: AddressFamily
    bind_scope: BindScope
    port: int
    owner: str | None


@dataclass(frozen=True)
class NetworkInterface:
    name: str
    kind: str | None
    up: bool | None
    networks: tuple[str, ...]


@dataclass(frozen=True)
class Route:
    family: AddressFamily
    destination: str
    interface: str | None
    route_type: str | None


@dataclass(frozen=True)
class DnsFacts:
    resolvers: tuple[str, ...]
    search_domains: tuple[str, ...]


@dataclass(frozen=True)
class NetworkFacts:
    listeners: Probe[tuple[Listener, ...]]
    interfaces: Probe[tuple[NetworkInterface, ...]]
    routes: Probe[tuple[Route, ...]]
    dns: Probe[DnsFacts]
    ipv4_forwarding: Probe[bool]
    ipv6_forwarding: Probe[bool]


@dataclass(frozen=True)
class FirewallFacts:
    nftables: ToolFacts
    nftables_tables: Probe[tuple[str, ...]]
    ufw: ToolFacts


@dataclass(frozen=True)
class SecurityFacts:
    apparmor_enabled: Probe[bool]
    apparmor_service_active: Probe[SystemdActiveState]
    time_synchronized: Probe[bool]


@dataclass(frozen=True)
class ServiceFact:
    name: str
    active: Probe[SystemdActiveState]
    unit_file_state: Probe[SystemdUnitFileState]
    ownership: Ownership


@dataclass(frozen=True)
class VerifiedInstallationOwnership:
    """Ownership already verified against one installation manifest and ID."""

    installation_id: str
    manifest_path: Path
    service_units: frozenset[str]

    def __post_init__(self) -> None:
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{7,127}", self.installation_id):
            raise ValueError("installation_id must be a bounded opaque identifier")
        if not self.manifest_path.is_absolute():
            raise ValueError("manifest_path must be absolute")
        if (
            self.manifest_path not in PROJECT_MARKERS
            or self.manifest_path.name != "install-manifest.json"
        ):
            raise ValueError("manifest_path must identify a declared installation manifest")
        if not self.service_units.issubset(OWNED_UNITS):
            raise ValueError("verified service units must be declared project units")


@dataclass(frozen=True)
class InstalledFacts:
    markers: Probe[tuple[str, ...]]
    installed_version: Probe[str]
    upstream_lock_present: Probe[bool]
    owned_services: tuple[ServiceFact, ...]
    templates: Mapping[str, Probe[bool]]


@dataclass(frozen=True)
class LinuxFacts:
    devices: DeviceFacts
    memory: MemoryFacts
    filesystems: tuple[FilesystemFacts, ...]
    docker: ToolFacts
    compose: ToolFacts
    network: NetworkFacts
    firewall: FirewallFacts
    security: SecurityFacts
    conflicting_services: tuple[ServiceFact, ...]
    installed: InstalledFacts


PROJECT_MARKERS = (
    Path("/etc/kitdev-sandboxes/config.yaml"),
    Path("/etc/kitdev-sandboxes/install-manifest.json"),
    Path("/var/lib/kitdev-sandboxes/install-manifest.json"),
    Path("/opt/kitdev-sandboxes/VERSION"),
)
OWNED_UNITS = (
    "kitdev-e2b-api.service",
    "kitdev-e2b-client-proxy.service",
    "kitdev-e2b-orchestrator.service",
    "kitdev-e2b-maintenance.timer",
)
CONFLICTING_UNITS = (
    "docker.service",
    "display-manager.service",
    "gdm.service",
    "gnome-remote-desktop.service",
    "NetworkManager.service",
    "systemd-logind.service",
    "sleep.target",
    "suspend.target",
    "hibernate.target",
    "hybrid-sleep.target",
    "avahi-daemon.service",
    "cups.service",
    "ufw.service",
    "kitdev-vllm.service",
    "kitdev-vllm-firewall.service",
    "kitdev-vllm-backup.service",
    "docker-lan-only-firewall.service",
)
TEMPLATES = ("base", "coding", "browser", "desktop")
SHARED_UNITS = frozenset(
    {
        "docker.service",
        "display-manager.service",
        "gdm.service",
        "gnome-remote-desktop.service",
        "NetworkManager.service",
        "systemd-logind.service",
        "sleep.target",
        "suspend.target",
        "hibernate.target",
        "hybrid-sleep.target",
        "avahi-daemon.service",
        "cups.service",
        "ufw.service",
    }
)
_TRUSTED_RESOLV_CONF_TARGETS = frozenset(
    {
        "/run/systemd/resolve/stub-resolv.conf",
        "/run/systemd/resolve/resolv.conf",
        "/run/NetworkManager/resolv.conf",
        "/run/NetworkManager/no-stub-resolv.conf",
    }
)


def _evidence(value: str | None, maximum_bytes: int = 512) -> str | None:
    if value is None:
        return None
    # Evidence is always a redacted, one-line excerpt; command lines and stderr
    # are never propagated by collectors.
    encoded = safe_report_text(value).encode("utf-8")[:maximum_bytes]
    return encoded.decode("utf-8", errors="ignore")


_READ_FLAGS = os.O_RDONLY | os.O_NONBLOCK | getattr(os, "O_CLOEXEC", 0)
_NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)
_DIRECTORY = getattr(os, "O_DIRECTORY", 0)


def _read_open_descriptor(
    descriptor: int, path: Path, maximum_bytes: int
) -> Probe[str]:
    try:
        with os.fdopen(descriptor, "rb") as stream:
            if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                return Probe.degraded(CollectionStatus.ERROR, source=str(path))
            raw = stream.read(maximum_bytes + 1)
    except OSError:
        return Probe.degraded(CollectionStatus.ERROR, source=str(path))
    if len(raw) > maximum_bytes:
        return Probe.degraded(CollectionStatus.TRUNCATED, source=str(path))
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        return Probe.degraded(CollectionStatus.ERROR, source=str(path))
    return Probe.ok(text, raw=text, source=str(path))


def _open_error(path: Path, error: OSError) -> Probe[Any]:
    if isinstance(error, FileNotFoundError):
        return Probe.degraded(CollectionStatus.ABSENT, source=str(path))
    if isinstance(error, PermissionError):
        return Probe.degraded(CollectionStatus.PERMISSION_DENIED, source=str(path))
    return Probe.degraded(CollectionStatus.ERROR, source=str(path))


def _default_read(path: Path, maximum_bytes: int) -> Probe[str]:
    try:
        descriptor = os.open(path, _READ_FLAGS | _NOFOLLOW)
    except OSError as error:
        return _open_error(path, error)
    return _read_open_descriptor(descriptor, path, maximum_bytes)


def _strict_parent_descriptor(path: Path) -> tuple[int, str]:
    if not path.is_absolute() or path.name in {"", ".", ".."}:
        raise OSError("strict path must be an absolute file path")
    descriptor = os.open("/", _READ_FLAGS | _DIRECTORY)
    try:
        for component in path.parts[1:-1]:
            if component in {"", ".", ".."}:
                raise OSError("unsafe path component")
            next_descriptor = os.open(
                component,
                _READ_FLAGS | _DIRECTORY | _NOFOLLOW,
                dir_fd=descriptor,
            )
            os.close(descriptor)
            descriptor = next_descriptor
        return descriptor, path.name
    except BaseException:
        os.close(descriptor)
        raise


def _default_owned_read(path: Path, maximum_bytes: int) -> Probe[str]:
    try:
        parent, name = _strict_parent_descriptor(path)
        try:
            descriptor = os.open(name, _READ_FLAGS | _NOFOLLOW, dir_fd=parent)
        finally:
            os.close(parent)
    except OSError as error:
        return _open_error(path, error)
    return _read_open_descriptor(descriptor, path, maximum_bytes)


def _trusted_resolver_target(link_path: Path, target: str) -> Path | None:
    if not target or "\x00" in target:
        return None
    if os.path.isabs(target):
        normalized = os.path.normpath(target)
    else:
        normalized = os.path.normpath(str(link_path.parent / target))
    if normalized not in _TRUSTED_RESOLV_CONF_TARGETS:
        return None
    return Path(normalized)


def _default_resolver_read(path: Path, maximum_bytes: int) -> Probe[str]:
    try:
        path_stat = path.lstat()
    except OSError as error:
        return _open_error(path, error)
    if stat.S_ISREG(path_stat.st_mode):
        return _default_read(path, maximum_bytes)
    if not stat.S_ISLNK(path_stat.st_mode):
        return Probe.degraded(CollectionStatus.ERROR, source=str(path))
    if path != Path("/etc/resolv.conf"):
        return Probe.degraded(CollectionStatus.ERROR, source=str(path))
    try:
        target = _trusted_resolver_target(path, os.readlink(path))
    except OSError as error:
        return _open_error(path, error)
    if target is None:
        return Probe.degraded(CollectionStatus.ERROR, source=str(path))
    result = _default_read(target, maximum_bytes)
    return Probe(result.status, result.value, result.raw, str(path), result.elapsed_ms)


def _default_stat(path: Path) -> Probe[os.stat_result]:
    try:
        return Probe.ok(path.lstat(), source=str(path))
    except OSError as error:
        return _open_error(path, error)


def _default_owned_stat(path: Path) -> Probe[os.stat_result]:
    try:
        parent, name = _strict_parent_descriptor(path)
        try:
            result = os.stat(name, dir_fd=parent, follow_symlinks=False)
        finally:
            os.close(parent)
        return Probe.ok(result, source=str(path))
    except OSError as error:
        return _open_error(path, error)


def lstat_owned_path(path: Path) -> Probe[os.stat_result]:
    """lstat an absolute path while refusing symlinks in every parent component."""

    return _default_owned_stat(path)


def _default_stat_filesystem(path: Path) -> Probe[FilesystemStat]:
    try:
        result = os.statvfs(path)
    except FileNotFoundError:
        return Probe.degraded(CollectionStatus.ABSENT, source=str(path))
    except PermissionError:
        return Probe.degraded(CollectionStatus.PERMISSION_DENIED, source=str(path))
    except OSError:
        return Probe.degraded(CollectionStatus.ERROR, source=str(path))
    value = FilesystemStat(
        result.f_blocks * result.f_frsize,
        result.f_bavail * result.f_frsize,
        result.f_files,
        result.f_favail,
    )
    return Probe.ok(value, source=str(path))


def _default_glob(pattern: str) -> Probe[tuple[Path, ...]]:
    try:
        return Probe.ok(tuple(sorted(Path("/").glob(pattern.lstrip("/")))), source=pattern)
    except PermissionError:
        return Probe.degraded(CollectionStatus.PERMISSION_DENIED, source=pattern)
    except OSError:
        return Probe.degraded(CollectionStatus.ERROR, source=pattern)


def _outcome_name(result: object) -> str:
    outcome = getattr(result, "outcome", "error")
    return str(getattr(outcome, "value", outcome)).lower()


def _stream(result: object, name: str) -> tuple[str, bool]:
    stream = getattr(result, name, "")
    if isinstance(stream, str):
        return stream, False
    return str(getattr(stream, "text", "")), bool(getattr(stream, "truncated", False))


def _duration_ms(result: object) -> int | None:
    value = getattr(result, "duration_seconds", None)
    if isinstance(value, (int, float)):
        return max(0, int(value * 1000))
    value = getattr(result, "elapsed_ms", None)
    return value if isinstance(value, int) and value >= 0 else None


def _command_text(runner: RunCommands, argv: tuple[str, ...], source: str) -> Probe[str]:
    result = runner.run(Command(argv))
    stdout, stdout_truncated = _stream(result, "stdout")
    _stderr, stderr_truncated = _stream(result, "stderr")
    elapsed = _duration_ms(result)
    outcome = _outcome_name(result)
    if stdout_truncated or stderr_truncated:
        return Probe.degraded(CollectionStatus.TRUNCATED, source=source, elapsed_ms=elapsed)
    status = {
        "success": CollectionStatus.OK,
        "ok": CollectionStatus.OK,
        "missing": CollectionStatus.ABSENT,
        "not_found": CollectionStatus.ABSENT,
        "permission_denied": CollectionStatus.PERMISSION_DENIED,
        "timeout": CollectionStatus.TIMEOUT,
        "spawn_error": CollectionStatus.ERROR,
        "signaled": CollectionStatus.ERROR,
        "nonzero": CollectionStatus.ERROR,
    }.get(outcome, CollectionStatus.ERROR)
    if status is CollectionStatus.OK:
        return Probe.ok(stdout, raw=stdout, source=source, elapsed_ms=elapsed)
    return Probe.degraded(status, source=source, elapsed_ms=elapsed)


def _mapped(source: Probe[Any], value: T) -> Probe[T]:
    return Probe(source.status, value, source.raw, source.source, source.elapsed_ms)


def _parse_int(text: Probe[str], *, multiplier: int = 1) -> Probe[int]:
    if text.status is not CollectionStatus.OK or text.value is None:
        return Probe(text.status, None, text.raw, text.source, text.elapsed_ms)
    value = text.value.strip()
    if not re.fullmatch(r"[0-9]+", value):
        return Probe.degraded(CollectionStatus.ERROR, source=text.source, raw=text.raw)
    return _mapped(text, int(value) * multiplier)


def _parse_bool(text: Probe[str]) -> Probe[bool]:
    if text.status is not CollectionStatus.OK or text.value is None:
        return Probe(text.status, None, text.raw, text.source, text.elapsed_ms)
    normalized = text.value.strip().lower()
    if normalized in {"1", "y", "yes", "true", "active", "enabled", "running"}:
        return _mapped(text, True)
    if normalized in {"0", "n", "no", "false", "inactive", "disabled", "stopped"}:
        return _mapped(text, False)
    return Probe.degraded(CollectionStatus.ERROR, source=text.source, raw=text.raw)


def _meminfo(read_text: ReadText) -> tuple[MemoryFacts, HugePageFacts]:
    probe = read_text(Path("/proc/meminfo"), 131_072)
    parsed: dict[str, int] = {}
    if probe.status is CollectionStatus.OK and probe.value is not None:
        for line in probe.value.splitlines():
            match = re.fullmatch(r"([A-Za-z_()]+):\s+([0-9]+)(?:\s+kB)?", line.strip())
            if match:
                parsed[match.group(1)] = int(match.group(2))

    def item(name: str, *, kib: bool = False) -> Probe[int]:
        if probe.status is not CollectionStatus.OK:
            return Probe(probe.status, None, probe.raw, probe.source, probe.elapsed_ms)
        if name not in parsed:
            return Probe.degraded(CollectionStatus.UNKNOWN, source=probe.source)
        return Probe.ok(parsed[name] * (1024 if kib else 1), source=probe.source)

    memory = MemoryFacts(
        item("MemTotal", kib=True),
        item("MemAvailable", kib=True),
        item("SwapTotal", kib=True),
        item("SwapFree", kib=True),
    )
    mounts_probe = read_text(Path("/proc/self/mounts"), 1_048_576)
    if mounts_probe.status is CollectionStatus.OK and mounts_probe.value is not None:
        mounts = tuple(
            sorted(
                fields[1].replace("\\040", " ")
                for fields in (line.split() for line in mounts_probe.value.splitlines())
                if len(fields) >= 3 and fields[2] == "hugetlbfs"
            )
        )
        mount_fact: Probe[tuple[str, ...]] = Probe.ok(mounts, source=mounts_probe.source)
    else:
        mount_fact = Probe(
            mounts_probe.status,
            None,
            mounts_probe.raw,
            mounts_probe.source,
            mounts_probe.elapsed_ms,
        )
    huge = HugePageFacts(
        item("Hugepagesize"),
        item("HugePages_Total"),
        item("HugePages_Free"),
        item("HugePages_Rsvd"),
        item("HugePages_Surp"),
        mount_fact,
    )
    return memory, huge


def _module_names(read_text: ReadText) -> Probe[tuple[str, ...]]:
    probe = read_text(Path("/proc/modules"), 2_097_152)
    if probe.status is not CollectionStatus.OK or probe.value is None:
        return Probe(probe.status, None, probe.raw, probe.source, probe.elapsed_ms)
    modules = tuple(
        sorted(
            fields[0]
            for fields in (line.split() for line in probe.value.splitlines())
            if fields and fields[0] in {"kvm", "kvm_amd", "kvm_intel"}
        )
    )
    return Probe.ok(modules, source=probe.source)


def _collect_nbd(read_text: ReadText, glob_paths: GlobPaths) -> NbdFacts:
    modules = read_text(Path("/proc/modules"), 2_097_152)
    if modules.status is CollectionStatus.OK and modules.value is not None:
        loaded = Probe.ok(
            any(
                fields[0] == "nbd"
                for fields in (line.split(maxsplit=1) for line in modules.value.splitlines())
                if fields
            ),
            source=modules.source,
        )
    else:
        loaded = Probe(modules.status, None, modules.raw, modules.source, modules.elapsed_ms)
    max_devices = _parse_int(read_text(Path("/sys/module/nbd/parameters/nbds_max"), 4_096))
    max_parts = _parse_int(read_text(Path("/sys/module/nbd/parameters/max_part"), 4_096))
    paths = glob_paths("/sys/block/nbd*")
    if paths.status is CollectionStatus.OK and paths.value is not None:
        devices: list[NbdDevice] = []
        for path in paths.value:
            pid = read_text(path / "pid", 4_096)
            if pid.status is CollectionStatus.OK and pid.value is not None:
                in_use = Probe.ok(bool(pid.value.strip()), source=f"nbd.{path.name}.pid")
            elif pid.status is CollectionStatus.ABSENT:
                in_use = Probe.ok(False, source=f"nbd.{path.name}.pid")
            else:
                in_use = Probe[bool](pid.status, None, None, f"nbd.{path.name}.pid")
            devices.append(NbdDevice(path.name, in_use))
        device_probe = Probe.ok(tuple(devices), source=paths.source)
    else:
        device_probe = Probe(paths.status, None, paths.raw, paths.source, paths.elapsed_ms)
    return NbdFacts(loaded, max_devices, max_parts, device_probe)


def _nearest_existing(path: Path, stat_path: StatPath) -> tuple[Path | None, CollectionStatus]:
    candidate = path
    while True:
        result = stat_path(candidate)
        if result.status is CollectionStatus.OK:
            if result.value is None or not stat.S_ISDIR(result.value.st_mode):
                return None, CollectionStatus.ERROR
            return candidate, CollectionStatus.OK
        if result.status not in {CollectionStatus.ABSENT}:
            return None, result.status
        if candidate == candidate.parent:
            return None, CollectionStatus.ABSENT
        candidate = candidate.parent


_MOUNT_OPTION_RE = re.compile(r"[A-Za-z0-9._+-]{1,64}")
_SAFE_MOUNT_VALUE_KEYS = frozenset({"barrier", "commit", "data", "discard", "errors"})
_SAFE_MOUNT_VALUE_RE = re.compile(r"[A-Za-z0-9._+-]{1,64}")
_MAX_MOUNT_OPTIONS = 256
_MAX_MOUNT_OPTION_BYTES = 256


def _normalize_mount_options(raw_options: str) -> tuple[str, ...]:
    raw_items = tuple(filter(None, raw_options.split(",")))
    if len(raw_items) > _MAX_MOUNT_OPTIONS:
        raise ValueError("too many mount options")
    normalized: list[str] = []
    for raw_item in raw_items:
        if len(raw_item.encode("utf-8")) > _MAX_MOUNT_OPTION_BYTES:
            raise ValueError("mount option exceeds byte limit")
        key, separator, value = raw_item.partition("=")
        if not _MOUNT_OPTION_RE.fullmatch(key):
            raise ValueError("invalid mount option key")
        if not separator:
            normalized.append(key)
        elif (
            key in _SAFE_MOUNT_VALUE_KEYS
            and _SAFE_MOUNT_VALUE_RE.fullmatch(value) is not None
        ):
            normalized.append(f"{key}={value}")
        else:
            normalized.append(f"{key}=[REDACTED]")
    return tuple(sorted(set(normalized)))


def _findmnt(runner: RunCommands, path: Path) -> tuple[Probe[str], Probe[tuple[str, ...]]]:
    result = _command_text(
        runner,
        ("findmnt", "--json", "--target", str(path), "--output", "FSTYPE,OPTIONS"),
        "findmnt.target",
    )
    if result.status is not CollectionStatus.OK or result.value is None:
        missing_type: Probe[str] = Probe(
            result.status, None, result.raw, result.source, result.elapsed_ms
        )
        missing_options: Probe[tuple[str, ...]] = Probe(
            result.status, None, result.raw, result.source, result.elapsed_ms
        )
        return missing_type, missing_options
    try:
        payload = json.loads(result.value)
        entry = payload["filesystems"][0]
        if not isinstance(entry, dict):
            raise TypeError("findmnt entry must be an object")
        filesystem_type = entry["fstype"]
        raw_options = entry["options"]
        if not isinstance(filesystem_type, str) or not filesystem_type:
            raise TypeError("findmnt fstype must be a nonempty string")
        if not re.fullmatch(r"[A-Za-z0-9._+-]{1,64}", filesystem_type):
            raise ValueError("findmnt fstype is invalid")
        if not isinstance(raw_options, str):
            raise TypeError("findmnt options must be a string")
        options = _normalize_mount_options(raw_options)
    except (KeyError, IndexError, TypeError, ValueError, json.JSONDecodeError):
        error_type: Probe[str] = Probe.degraded(CollectionStatus.ERROR, source=result.source)
        error_options: Probe[tuple[str, ...]] = Probe.degraded(
            CollectionStatus.ERROR, source=result.source
        )
        return error_type, error_options
    return (
        Probe.ok(filesystem_type, source=result.source, elapsed_ms=result.elapsed_ms),
        Probe.ok(options, source=result.source, elapsed_ms=result.elapsed_ms),
    )


def _collect_filesystems(
    paths: Iterable[Path],
    runner: RunCommands,
    stat_path: StatPath,
    stat_filesystem: StatFilesystem,
) -> tuple[FilesystemFacts, ...]:
    facts: list[FilesystemFacts] = []
    for configured in paths:
        containing, containing_status = _nearest_existing(configured, stat_path)
        if containing is None:
            unknown_int = Probe[int](containing_status, None, None, "path.ancestor")
            unknown_str = Probe[str](containing_status, None, None, "path.ancestor")
            unknown_opts = Probe[tuple[str, ...]](
                containing_status, None, None, "path.ancestor"
            )
            facts.append(
                FilesystemFacts(
                    str(configured), None, unknown_int, unknown_int, unknown_int, unknown_int,
                    unknown_str, unknown_opts,
                )
            )
            continue
        filesystem = stat_filesystem(containing)
        if filesystem.status is CollectionStatus.OK and filesystem.value is not None:
            total = Probe.ok(filesystem.value.total_bytes, source=filesystem.source)
            available = Probe.ok(filesystem.value.available_bytes, source=filesystem.source)
            total_inodes = Probe.ok(filesystem.value.total_inodes, source=filesystem.source)
            available_inodes = Probe.ok(filesystem.value.available_inodes, source=filesystem.source)
        else:
            total = Probe[int](filesystem.status, None, None, filesystem.source)
            available = Probe[int](filesystem.status, None, None, filesystem.source)
            total_inodes = Probe[int](filesystem.status, None, None, filesystem.source)
            available_inodes = Probe[int](filesystem.status, None, None, filesystem.source)
        fs_type, options = _findmnt(runner, containing)
        facts.append(
            FilesystemFacts(
                str(configured), str(containing), total, available, total_inodes,
                available_inodes, fs_type, options,
            )
        )
    return tuple(facts)


def _version_tool(
    runner: RunCommands,
    argv: tuple[str, ...],
    source: str,
    *,
    active: Probe[bool] | None = None,
) -> ToolFacts:
    result = _command_text(runner, argv, source)
    if result.status is CollectionStatus.ABSENT:
        absent = Probe.ok(False, source=source)
        return ToolFacts(
            absent,
            Probe.degraded(CollectionStatus.ABSENT, source=source),
            active or absent,
        )
    present = Probe.ok(True, source=source) if result.status is CollectionStatus.OK else Probe(
        result.status, None, None, source, result.elapsed_ms
    )
    if result.status is CollectionStatus.OK and result.value is not None:
        version = Probe.ok(
            safe_report_text(" ".join(result.value.split()))[:160], source=source
        )
    else:
        version = Probe(result.status, None, None, source, result.elapsed_ms)
    return ToolFacts(
        active=active or Probe.degraded(CollectionStatus.UNKNOWN, source=source),
        present=present,
        version=version,
    )


def _systemd_output(
    runner: RunCommands, unit: str, verb: str
) -> tuple[Probe[str], str | None]:
    source = f"systemd.{verb}.{unit}"
    result = runner.run(Command(("systemctl", verb, unit)))
    stdout, stdout_truncated = _stream(result, "stdout")
    _stderr, stderr_truncated = _stream(result, "stderr")
    elapsed = _duration_ms(result)
    if stdout_truncated or stderr_truncated:
        return (
            Probe.degraded(CollectionStatus.TRUNCATED, source=source, elapsed_ms=elapsed),
            None,
        )
    outcome = _outcome_name(result)
    if outcome not in {"success", "ok", "nonzero"}:
        status = {
            "missing": CollectionStatus.ABSENT,
            "not_found": CollectionStatus.ABSENT,
            "permission_denied": CollectionStatus.PERMISSION_DENIED,
            "timeout": CollectionStatus.TIMEOUT,
            "spawn_error": CollectionStatus.ERROR,
            "signaled": CollectionStatus.ERROR,
        }.get(outcome, CollectionStatus.ERROR)
        return Probe.degraded(status, source=source, elapsed_ms=elapsed), None
    return (
        Probe.ok(stdout, raw=stdout, source=source, elapsed_ms=elapsed),
        stdout.strip().lower(),
    )


def _systemd_active_state(
    runner: RunCommands, unit: str
) -> Probe[SystemdActiveState]:
    output, normalized = _systemd_output(runner, unit, "is-active")
    if output.status is not CollectionStatus.OK or normalized is None:
        return Probe(output.status, None, output.raw, output.source, output.elapsed_ms)
    try:
        state = SystemdActiveState(normalized)
    except ValueError:
        return Probe.degraded(CollectionStatus.UNKNOWN, source=output.source)
    return Probe.ok(state, source=output.source, elapsed_ms=output.elapsed_ms)


def _systemd_unit_file_state(
    runner: RunCommands, unit: str
) -> Probe[SystemdUnitFileState]:
    output, normalized = _systemd_output(runner, unit, "is-enabled")
    if output.status is not CollectionStatus.OK or normalized is None:
        return Probe(output.status, None, output.raw, output.source, output.elapsed_ms)
    try:
        state = SystemdUnitFileState(normalized)
    except ValueError:
        return Probe.degraded(CollectionStatus.UNKNOWN, source=output.source)
    return Probe.ok(state, source=output.source, elapsed_ms=output.elapsed_ms)


def _active_bool(state: Probe[SystemdActiveState]) -> Probe[bool]:
    if state.status is not CollectionStatus.OK or state.value is None:
        return Probe(state.status, None, state.raw, state.source, state.elapsed_ms)
    if state.value is SystemdActiveState.ACTIVE:
        return Probe.ok(True, source=state.source, elapsed_ms=state.elapsed_ms)
    if state.value in {SystemdActiveState.INACTIVE, SystemdActiveState.FAILED}:
        return Probe.ok(False, source=state.source, elapsed_ms=state.elapsed_ms)
    return Probe.degraded(CollectionStatus.UNKNOWN, source=state.source)


def _service(runner: RunCommands, unit: str, ownership: Ownership) -> ServiceFact:
    return ServiceFact(
        unit,
        _systemd_active_state(runner, unit),
        _systemd_unit_file_state(runner, unit),
        ownership,
    )


def _parse_endpoint(
    endpoint: str, family_hint: AddressFamily
) -> tuple[AddressFamily, BindScope, int] | None:
    endpoint = endpoint.strip()
    if endpoint.startswith("[") and "]:" in endpoint:
        address, port_text = endpoint[1:].rsplit("]:", 1)
    elif ":" in endpoint:
        address, port_text = endpoint.rsplit(":", 1)
    else:
        return None
    if not port_text.isdigit() or not 0 <= int(port_text) <= 65535:
        return None
    normalized = address.split("%", 1)[0]
    if normalized == "*":
        return family_hint, BindScope.WILDCARD, int(port_text)
    if normalized == "0.0.0.0":
        return AddressFamily.IPV4, BindScope.WILDCARD, int(port_text)
    if normalized in {"::", "[::]"}:
        return AddressFamily.IPV6, BindScope.WILDCARD, int(port_text)
    try:
        ip = ipaddress.ip_address(normalized)
    except ValueError:
        return None
    family = AddressFamily.IPV4 if ip.version == 4 else AddressFamily.IPV6
    scope = BindScope.LOOPBACK if ip.is_loopback else BindScope.HOST
    return family, scope, int(port_text)


_OWNER_RE = re.compile(r'users:\(\("([^"\\]{1,80})"')


def _parse_listeners(
    text: str, family_hint: AddressFamily
) -> tuple[Listener, ...] | None:
    listeners: set[Listener] = set()
    meaningful_lines = 0
    malformed_lines = 0
    for line in text.splitlines():
        if not line.strip():
            continue
        meaningful_lines += 1
        fields = line.split()
        if len(fields) < 5:
            malformed_lines += 1
            continue
        protocol = fields[0].lower()
        if protocol not in {"tcp", "udp"}:
            malformed_lines += 1
            continue
        endpoint = _parse_endpoint(fields[4], family_hint)
        if endpoint is None:
            malformed_lines += 1
            continue
        family, scope, port = endpoint
        owner_match = _OWNER_RE.search(line)
        owner = safe_report_text(owner_match.group(1)) if owner_match else None
        listeners.add(Listener(protocol, family, scope, port, owner))
    if meaningful_lines and (not listeners or malformed_lines):
        return None
    return tuple(
        sorted(
            listeners,
            key=lambda item: (item.protocol, item.family, item.port, item.bind_scope),
        )
    )


def _collect_listeners(runner: RunCommands) -> Probe[tuple[Listener, ...]]:
    combined: list[Listener] = []
    elapsed = 0
    for family_arg, family in (("-4", AddressFamily.IPV4), ("-6", AddressFamily.IPV6)):
        result = _command_text(
            runner,
            ("ss", "-H", "-lntup", family_arg),
            f"socket.listeners.{family_arg[1:]}",
        )
        if result.status is not CollectionStatus.OK or result.value is None:
            return Probe(result.status, None, result.raw, result.source, result.elapsed_ms)
        parsed = _parse_listeners(result.value, family)
        if parsed is None:
            return Probe.degraded(CollectionStatus.ERROR, source=result.source)
        combined.extend(parsed)
        elapsed += result.elapsed_ms or 0
    return Probe.ok(
        tuple(
            sorted(
                set(combined),
                key=lambda item: (item.protocol, item.family, item.port, item.bind_scope),
            )
        ),
        source="socket.listeners",
        elapsed_ms=elapsed,
    )


def _parse_ip_json(
    ipv4: Probe[str], ipv6: Probe[str]
) -> tuple[Probe[tuple[NetworkInterface, ...]], Probe[tuple[Route, ...]]]:
    if ipv4.status is not CollectionStatus.OK or ipv4.value is None:
        missing_interfaces: Probe[tuple[NetworkInterface, ...]] = Probe(
            ipv4.status, None, None, ipv4.source, ipv4.elapsed_ms
        )
        missing_routes: Probe[tuple[Route, ...]] = Probe(
            ipv4.status, None, None, ipv4.source, ipv4.elapsed_ms
        )
        return missing_interfaces, missing_routes
    if ipv6.status is not CollectionStatus.OK or ipv6.value is None:
        missing_interfaces = Probe(ipv6.status, None, None, ipv6.source, ipv6.elapsed_ms)
        missing_routes = Probe(ipv6.status, None, None, ipv6.source, ipv6.elapsed_ms)
        return missing_interfaces, missing_routes
    try:
        payload4 = json.loads(ipv4.value)
        payload6 = json.loads(ipv6.value)
        address_items4 = payload4["addresses"]
        address_items6 = payload6["addresses"]
        route_items4 = payload4["routes"]
        route_items6 = payload6["routes"]
        if not all(
            isinstance(items, list)
            for items in (address_items4, address_items6, route_items4, route_items6)
        ):
            raise TypeError("ip JSON collections must be lists")
        addresses = address_items4 + address_items6
        routes_payload = route_items4 + route_items6
    except (KeyError, TypeError, json.JSONDecodeError):
        error_interfaces: Probe[tuple[NetworkInterface, ...]] = Probe.degraded(
            CollectionStatus.ERROR, source="ip.json"
        )
        error_routes: Probe[tuple[Route, ...]] = Probe.degraded(
            CollectionStatus.ERROR, source="ip.json"
        )
        return error_interfaces, error_routes
    interfaces: list[NetworkInterface] = []
    try:
        by_name: dict[str, dict[str, Any]] = {}
        for entry in addresses:
            if not isinstance(entry, dict) or not isinstance(entry.get("ifname"), str):
                raise TypeError("invalid interface entry")
            name = safe_report_text(entry["ifname"])
            link_info = entry.get("linkinfo", {})
            kind = (
                link_info.get("info_kind")
                if isinstance(link_info, dict)
                else entry.get("link_type")
            )
            current = by_name.setdefault(
                name,
                {"kind": kind or entry.get("link_type"), "up": None, "networks": set()},
            )
            flags = entry.get("flags", [])
            addresses_for_interface = entry.get("addr_info", [])
            if not isinstance(flags, list) or not isinstance(addresses_for_interface, list):
                raise TypeError("invalid interface state")
            current["up"] = "UP" in flags
            for address in addresses_for_interface:
                if not isinstance(address, dict):
                    raise TypeError("invalid interface address")
                local = address.get("local")
                prefixlen = address.get("prefixlen")
                if not isinstance(local, str) or type(prefixlen) is not int:
                    raise TypeError("invalid interface address fields")
                current["networks"].add(
                    str(ipaddress.ip_network(f"{local}/{prefixlen}", strict=False))
                )
        for name, item in by_name.items():
            interfaces.append(
                NetworkInterface(
                    name,
                    item["kind"],
                    item["up"],
                    tuple(sorted(item["networks"])),
                )
            )
        routes: list[Route] = []
        for entry in routes_payload:
            if not isinstance(entry, dict) or not isinstance(entry.get("dst"), str):
                raise TypeError("invalid route entry")
            destination = entry["dst"]
            interface = entry.get("dev")
            route_type = entry.get("type")
            if interface is not None and not isinstance(interface, str):
                raise TypeError("invalid route interface")
            if route_type is not None and not isinstance(route_type, str):
                raise TypeError("invalid route type")
            interface = safe_report_text(interface) if interface is not None else None
            route_type = safe_report_text(route_type) if route_type is not None else None
            family = (
                AddressFamily.IPV6
                if ":" in destination or entry.get("family") == "inet6"
                else AddressFamily.IPV4
            )
            if destination != "default":
                destination = str(ipaddress.ip_network(destination, strict=False))
            routes.append(Route(family, destination, interface, route_type))
    except (KeyError, TypeError, ValueError):
        return (
            Probe.degraded(CollectionStatus.ERROR, source="ip.json"),
            Probe.degraded(CollectionStatus.ERROR, source="ip.json"),
        )
    return (
        Probe.ok(tuple(sorted(interfaces, key=lambda item: item.name)), source="ip.address"),
        Probe.ok(
            tuple(
                sorted(
                    routes,
                    key=lambda item: (item.family, item.destination, item.interface or ""),
                )
            ),
            source="ip.route",
        ),
    )


def _collect_ip(
    runner: RunCommands,
) -> tuple[Probe[tuple[NetworkInterface, ...]], Probe[tuple[Route, ...]]]:
    combined: list[dict[str, Any]] = []
    for family, flag in (("inet", "-4"), ("inet6", "-6")):
        addresses = _command_text(
            runner,
            ("ip", "-details", "-j", flag, "address", "show"),
            f"ip.address.{family}",
        )
        routes = _command_text(
            runner,
            ("ip", "-j", flag, "route", "show", "table", "all"),
            f"ip.route.{family}",
        )
        if addresses.status is not CollectionStatus.OK:
            return (
                Probe(addresses.status, None, None, addresses.source, addresses.elapsed_ms),
                Probe(addresses.status, None, None, addresses.source, addresses.elapsed_ms),
            )
        if routes.status is not CollectionStatus.OK:
            return (
                Probe(routes.status, None, None, routes.source, routes.elapsed_ms),
                Probe(routes.status, None, None, routes.source, routes.elapsed_ms),
            )
        try:
            route_items = json.loads(routes.value or "[]")
            address_items = json.loads(addresses.value or "[]")
            if not isinstance(route_items, list) or not isinstance(address_items, list):
                raise ValueError("ip output must be JSON arrays")
            for route_item in route_items:
                if isinstance(route_item, dict):
                    route_item.setdefault("family", family)
            combined.append(
                {
                    "family": family,
                    "addresses": address_items,
                    "routes": route_items,
                }
            )
        except (json.JSONDecodeError, ValueError):
            return (
                Probe.degraded(CollectionStatus.ERROR, source="ip.json"),
                Probe.degraded(CollectionStatus.ERROR, source="ip.json"),
            )
    left = Probe.ok(
        json.dumps(
            {"addresses": combined[0]["addresses"], "routes": combined[0]["routes"]}
        ),
        source="ip.json",
    )
    right = Probe.ok(
        json.dumps(
            {"addresses": combined[1]["addresses"], "routes": combined[1]["routes"]}
        ),
        source="ip.json",
    )
    return _parse_ip_json(left, right)


def _collect_dns(read_text: ReadText) -> Probe[DnsFacts]:
    result = read_text(Path("/etc/resolv.conf"), 65_536)
    if result.status is not CollectionStatus.OK or result.value is None:
        return Probe(result.status, None, result.raw, result.source, result.elapsed_ms)
    resolvers: set[str] = set()
    domains: set[str] = set()
    meaningful_lines = 0
    recognized_lines = 0
    try:
        for line in result.value.splitlines():
            fields = re.split(r"[#;]", line, maxsplit=1)[0].split()
            if not fields:
                continue
            meaningful_lines += 1
            if fields[0] == "nameserver":
                if len(fields) != 2:
                    raise ValueError("invalid nameserver entry")
                resolvers.add(str(ipaddress.ip_address(fields[1].split("%", 1)[0])))
                recognized_lines += 1
            elif fields[0] in {"search", "domain"}:
                if len(fields) < 2:
                    raise ValueError("empty resolver domain entry")
                for field in fields[1:]:
                    normalized = field.lower().rstrip(".")
                    if not re.fullmatch(r"[a-z0-9_.-]{1,253}", normalized):
                        raise ValueError("invalid resolver domain")
                    domains.add(normalized)
                recognized_lines += 1
            elif fields[0] in {"options", "sortlist"}:
                recognized_lines += 1
    except ValueError:
        return Probe.degraded(CollectionStatus.ERROR, source=result.source)
    if meaningful_lines and not recognized_lines:
        return Probe.degraded(CollectionStatus.ERROR, source=result.source)
    return Probe.ok(
        DnsFacts(tuple(sorted(resolvers)), tuple(sorted(domains))), source=result.source
    )


def _nft_tables(runner: RunCommands) -> Probe[tuple[str, ...]]:
    result = _command_text(runner, ("nft", "--json", "list", "tables"), "nftables.tables")
    if result.status is not CollectionStatus.OK or result.value is None:
        return Probe(result.status, None, result.raw, result.source, result.elapsed_ms)
    try:
        payload = json.loads(result.value)
        if not isinstance(payload, dict) or not isinstance(payload.get("nftables"), list):
            raise TypeError("invalid nftables result envelope")
        table_names: list[str] = []
        for item in payload["nftables"]:
            if not isinstance(item, dict):
                raise TypeError("invalid nftables item")
            if "metainfo" in item:
                if not isinstance(item["metainfo"], dict):
                    raise TypeError("invalid nftables metadata")
                continue
            table = item.get("table")
            if not isinstance(table, dict):
                raise TypeError("unexpected nftables item")
            family = table.get("family")
            name = table.get("name")
            if not isinstance(family, str) or not isinstance(name, str):
                raise TypeError("invalid nftables table")
            table_names.append(safe_report_text(f"{family}:{name}"))
        tables = tuple(
            sorted(table_names)
        )
    except (KeyError, TypeError, json.JSONDecodeError):
        return Probe.degraded(CollectionStatus.ERROR, source=result.source)
    return Probe.ok(tables, source=result.source)


def _ufw_state(runner: RunCommands) -> Probe[bool]:
    result = _command_text(runner, ("ufw", "status"), "ufw.status")
    if result.status is not CollectionStatus.OK or result.value is None:
        return Probe(result.status, None, None, result.source, result.elapsed_ms)
    match = re.search(r"(?im)^status:\s*(active|inactive)\s*$", result.value)
    if match is None:
        return Probe.degraded(CollectionStatus.ERROR, source=result.source)
    return Probe.ok(
        match.group(1).lower() == "active",
        source=result.source,
        elapsed_ms=result.elapsed_ms,
    )


def _collect_markers(stat_path: StatPath) -> Probe[tuple[str, ...]]:
    markers: list[str] = []
    degraded: CollectionStatus | None = None
    for path in PROJECT_MARKERS:
        result = stat_path(path)
        if (
            result.status is CollectionStatus.OK
            and result.value is not None
            and stat.S_ISREG(result.value.st_mode)
        ):
            markers.append(str(path))
        elif result.status is CollectionStatus.OK:
            degraded = CollectionStatus.ERROR
        elif result.status is not CollectionStatus.ABSENT:
            degraded = result.status
    return Probe(degraded or CollectionStatus.OK, tuple(markers), None, "project.markers")


def _installed_version(read_text: ReadText) -> Probe[str]:
    result = read_text(Path("/opt/kitdev-sandboxes/VERSION"), 4_096)
    if result.status is not CollectionStatus.OK or result.value is None:
        return Probe(result.status, None, result.raw, result.source, result.elapsed_ms)
    version = result.value.strip()
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9.+_-]{0,127}", version):
        return Probe.degraded(CollectionStatus.ERROR, source=result.source)
    return Probe.ok(version, source=result.source)


def _template_facts(stat_path: StatPath) -> Mapping[str, Probe[bool]]:
    result: dict[str, Probe[bool]] = {}
    for name in TEMPLATES:
        path = Path("/var/lib/kitdev-sandboxes/templates") / name / "manifest.json"
        probe = stat_path(path)
        if (
            probe.status is CollectionStatus.OK
            and probe.value is not None
            and stat.S_ISREG(probe.value.st_mode)
        ):
            result[name] = Probe.ok(True, source=f"template.{name}")
        elif probe.status is CollectionStatus.OK:
            result[name] = Probe.degraded(
                CollectionStatus.ERROR, source=f"template.{name}"
            )
        elif probe.status is CollectionStatus.ABSENT:
            result[name] = Probe.ok(False, source=f"template.{name}")
        else:
            result[name] = Probe(probe.status, None, None, f"template.{name}")
    return result


def collect_linux_facts(
    *,
    configured_paths: Iterable[Path],
    runner: RunCommands,
    project_root: Path,
    read_text: ReadText = _default_read,
    stat_path: StatPath = _default_stat,
    stat_filesystem: StatFilesystem = _default_stat_filesystem,
    glob_paths: GlobPaths = _default_glob,
    resolver_read_text: ReadText | None = None,
    owned_read_text: ReadText | None = None,
    owned_stat_path: StatPath | None = None,
    verified_ownership: VerifiedInstallationOwnership | None = None,
) -> LinuxFacts:
    """Collect normalized Linux facts using only bounded, fixed-argv reads."""

    effective_resolver_read = resolver_read_text or (
        _default_resolver_read if read_text is _default_read else read_text
    )
    effective_owned_read = owned_read_text or (
        _default_owned_read if read_text is _default_read else read_text
    )
    effective_owned_stat = owned_stat_path or (
        _default_owned_stat if stat_path is _default_stat else stat_path
    )

    memory, huge_pages = _meminfo(read_text)
    nbd = _collect_nbd(read_text, glob_paths)
    kvm_modules = _module_names(read_text)
    tun_stat = stat_path(Path("/dev/net/tun"))
    if tun_stat.status is CollectionStatus.OK:
        tun_exists = Probe.ok(True, source="/dev/net/tun")
        tun_character = Probe.ok(
            bool(tun_stat.value and stat.S_ISCHR(tun_stat.value.st_mode)), source="/dev/net/tun"
        )
    elif tun_stat.status is CollectionStatus.ABSENT:
        tun_exists = Probe.ok(False, source="/dev/net/tun")
        tun_character = Probe.degraded(CollectionStatus.ABSENT, source="/dev/net/tun")
    else:
        tun_exists = Probe(tun_stat.status, None, None, "/dev/net/tun")
        tun_character = Probe(tun_stat.status, None, None, "/dev/net/tun")

    docker_active = _active_bool(_systemd_active_state(runner, "docker.service"))
    docker = _version_tool(
        runner,
        ("docker", "version", "--format", "{{.Server.Version}}"),
        "docker.version",
        active=docker_active,
    )
    compose = _version_tool(
        runner,
        ("docker", "compose", "version", "--short"),
        "docker.compose.version",
    )
    compose = ToolFacts(compose.present, compose.version, docker_active)

    listeners = _collect_listeners(runner)
    interfaces, routes = _collect_ip(runner)
    dns = _collect_dns(effective_resolver_read)
    ipv4_forwarding = _parse_bool(read_text(Path("/proc/sys/net/ipv4/ip_forward"), 4_096))
    ipv6_forwarding = _parse_bool(read_text(Path("/proc/sys/net/ipv6/conf/all/forwarding"), 4_096))

    nft = _version_tool(runner, ("nft", "--version"), "nftables.version")
    nft_tables = _nft_tables(runner)
    if nft_tables.status is CollectionStatus.OK and nft_tables.value is not None:
        nft_active = Probe.ok(bool(nft_tables.value), source="nftables.tables")
    else:
        nft_active = Probe(nft_tables.status, None, None, "nftables.tables")
    nft = ToolFacts(nft.present, nft.version, nft_active)
    ufw_active = _ufw_state(runner)
    ufw = _version_tool(runner, ("ufw", "--version"), "ufw.version", active=ufw_active)

    apparmor_enabled = _parse_bool(
        read_text(Path("/sys/module/apparmor/parameters/enabled"), 4_096)
    )
    apparmor_active = _systemd_active_state(runner, "apparmor.service")
    synchronized = _parse_bool(
        _command_text(
            runner,
            ("timedatectl", "show", "--property=NTPSynchronized", "--value"),
            "time.ntp_synchronized",
        )
    )

    conflicting = tuple(
        _service(
            runner,
            unit,
            Ownership.SHARED if unit in SHARED_UNITS else Ownership.UNKNOWN,
        )
        for unit in CONFLICTING_UNITS
    )
    owned = tuple(
        _service(
            runner,
            unit,
            Ownership.PROJECT
            if verified_ownership is not None and unit in verified_ownership.service_units
            else Ownership.UNKNOWN,
        )
        for unit in OWNED_UNITS
    )
    lock_stat = stat_path(project_root / "versions.lock.yaml")
    if (
        lock_stat.status is CollectionStatus.OK
        and lock_stat.value is not None
        and stat.S_ISREG(lock_stat.value.st_mode)
    ):
        lock_present = Probe.ok(
            True,
            source="upstream.version_lock",
        )
    elif lock_stat.status is CollectionStatus.OK:
        lock_present = Probe.degraded(
            CollectionStatus.ERROR, source="upstream.version_lock"
        )
    elif lock_stat.status is CollectionStatus.ABSENT:
        lock_present = Probe.ok(False, source="upstream.version_lock")
    else:
        lock_present = Probe(lock_stat.status, None, None, "upstream.version_lock")

    return LinuxFacts(
        DeviceFacts(kvm_modules, nbd, huge_pages, tun_exists, tun_character),
        memory,
        _collect_filesystems(configured_paths, runner, stat_path, stat_filesystem),
        docker,
        compose,
        NetworkFacts(
            listeners, interfaces, routes, dns, ipv4_forwarding, ipv6_forwarding
        ),
        FirewallFacts(nft, nft_tables, ufw),
        SecurityFacts(apparmor_enabled, apparmor_active, synchronized),
        conflicting,
        InstalledFacts(
            _collect_markers(effective_owned_stat),
            _installed_version(effective_owned_read),
            lock_present,
            owned,
            _template_facts(effective_owned_stat),
        ),
    )
