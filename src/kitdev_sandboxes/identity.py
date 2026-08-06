"""Read-only identity discovery and deterministic identity-access planning."""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Callable

from kitdev_sandboxes import __version__
from kitdev_sandboxes.collectors import CollectionStatus, lstat_owned_path
from kitdev_sandboxes.config import Configuration, LifecycleMode
from kitdev_sandboxes.preflight import HostFacts, redact_report
from kitdev_sandboxes.runner import Command, CommandOutcome, CommandResult, CommandRunner


SERVICE_NAMES = ("kitdev-e2b", "kitdev-proxy", "kitdev-worker")
_ACCOUNT_RE = re.compile(r"[a-z_][a-z0-9_-]{0,30}")
_MAX_DATABASE_BYTES = 262_144
_MAX_REPORT_BYTES = 1_048_576


class FactStatus(StrEnum):
    OK = "ok"
    ABSENT = "absent"
    UNKNOWN = "unknown"


class IdentityOrigin(StrEnum):
    LOCAL = "local"
    NSS = "nss"
    NONE = "none"
    UNKNOWN = "unknown"


class GateState(StrEnum):
    VERIFIED = "verified"
    UNRESOLVED = "unresolved"
    FAILED = "failed"


@dataclass(frozen=True)
class AccountFact:
    name: str
    status: FactStatus
    origin: IdentityOrigin
    uid: int | None = None
    gid: int | None = None
    home: str | None = None
    shell: str | None = None
    supplementary_gids: tuple[int, ...] = ()


@dataclass(frozen=True)
class GroupFact:
    name: str
    status: FactStatus
    origin: IdentityOrigin
    gid: int | None = None
    members: tuple[str, ...] = ()


@dataclass(frozen=True)
class PathFact:
    path: str
    status: FactStatus
    kind: str | None = None


@dataclass(frozen=True)
class AllocationRange:
    status: FactStatus
    uid_min: int | None = None
    uid_max: int | None = None
    gid_min: int | None = None
    gid_max: int | None = None


@dataclass(frozen=True)
class LxdPrerequisite:
    non_use: GateState
    reason: str


@dataclass(frozen=True)
class IdentityFacts:
    accounts: tuple[AccountFact, ...]
    groups: tuple[GroupFact, ...]
    occupied_uids: tuple[int, ...]
    occupied_gids: tuple[int, ...]
    allocation_range: AllocationRange
    nologin: PathFact
    nonexistent: PathFact
    lxd: LxdPrerequisite


@dataclass(frozen=True)
class IdentityPrerequisites:
    host_key: GateState = GateState.UNRESOLVED
    recovery: GateState = GateState.UNRESOLVED
    second_session: GateState = GateState.UNRESOLVED
    sudo_policy: GateState = GateState.UNRESOLVED
    operator_ssh_key: GateState = GateState.UNRESOLVED
    bootstrap: GateState = GateState.UNRESOLVED
    journal: GateState = GateState.UNRESOLVED


@dataclass(frozen=True)
class IdentityAllocation:
    name: str
    uid: int
    gid: int

    def as_dict(self) -> dict[str, object]:
        return {"name": self.name, "uid": self.uid, "gid": self.gid}


@dataclass(frozen=True)
class IdentityAction:
    action_id: str
    category: str
    target: str
    desired_state: str
    privilege: str
    rollback: str

    def as_dict(self) -> dict[str, object]:
        return {
            "id": self.action_id,
            "category": self.category,
            "target": self.target,
            "desired_state": self.desired_state,
            "privilege": self.privilege,
            "rollback": self.rollback,
        }


@dataclass(frozen=True)
class IdentityIssue:
    issue_id: str
    code: str
    explanation: str

    def as_dict(self) -> dict[str, object]:
        return {"id": self.issue_id, "code": self.code, "explanation": self.explanation}


@dataclass(frozen=True)
class IdentityPlan:
    lifecycle_mode: LifecycleMode
    blocking: bool
    allocations: tuple[IdentityAllocation, ...]
    actions: tuple[IdentityAction, ...]
    issues: tuple[IdentityIssue, ...]
    plan_hash: str

    def as_dict(self) -> dict[str, object]:
        return redact_report(
            {
                "schema_version": 1,
                "project_release": __version__,
                "lifecycle_mode": self.lifecycle_mode.value,
                "command_mode": "install-dry-run",
                "phase": "identity-access",
                "dry_run": True,
                "blocking": self.blocking,
                "plan_hash": self.plan_hash,
                "summary": {
                    "actions": len(self.actions),
                    "allocations": len(self.allocations),
                    "issues": len(self.issues),
                },
                "allocations": [item.as_dict() for item in self.allocations],
                "actions": [item.as_dict() for item in self.actions],
                "issues": [item.as_dict() for item in self.issues],
            }
        )

    def to_json(self) -> str:
        rendered = json.dumps(self.as_dict(), sort_keys=True, separators=(",", ":"))
        if len(rendered.encode("utf-8")) > _MAX_REPORT_BYTES:
            raise ValueError("identity plan output exceeds its global byte limit")
        return rendered

    @property
    def exit_code(self) -> int:
        if any(issue.code == "unsupported" for issue in self.issues):
            return 3
        if any(issue.code == "conflict" for issue in self.issues):
            return 4
        return 5 if self.blocking else 0


def _run(runner: CommandRunner, *argv: str) -> CommandResult:
    return runner.run(
        Command(tuple(argv), timeout_seconds=5.0, stdout_limit_bytes=_MAX_DATABASE_BYTES)
    )


def _parse_passwd_line(line: str, expected: str | None = None) -> AccountFact | None:
    fields = line.rstrip("\n").split(":")
    if len(fields) != 7 or not _ACCOUNT_RE.fullmatch(fields[0]):
        return None
    if expected is not None and fields[0] != expected:
        return None
    try:
        uid, gid = int(fields[2]), int(fields[3])
    except ValueError:
        return None
    if uid < 0 or gid < 0 or uid > 2**32 - 2 or gid > 2**32 - 2:
        return None
    home, shell = fields[5], fields[6]
    if not home.startswith("/") or not shell.startswith("/"):
        return None
    return AccountFact(fields[0], FactStatus.OK, IdentityOrigin.NSS, uid, gid, home, shell)


def _parse_group_line(line: str, expected: str | None = None) -> GroupFact | None:
    fields = line.rstrip("\n").split(":")
    if len(fields) != 4 or not _ACCOUNT_RE.fullmatch(fields[0]):
        return None
    if expected is not None and fields[0] != expected:
        return None
    try:
        gid = int(fields[2])
    except ValueError:
        return None
    members = tuple(filter(None, fields[3].split(",")))
    if gid < 0 or gid > 2**32 - 2 or any(not _ACCOUNT_RE.fullmatch(item) for item in members):
        return None
    return GroupFact(fields[0], FactStatus.OK, IdentityOrigin.NSS, gid, tuple(sorted(members)))


def _account(runner: CommandRunner, name: str) -> AccountFact:
    effective = _run(runner, "/usr/bin/getent", "passwd", name)
    local = _run(runner, "/usr/bin/getent", "-s", "files", "passwd", name)
    if effective.outcome is CommandOutcome.NONZERO and not effective.stdout.text.strip():
        return AccountFact(name, FactStatus.ABSENT, IdentityOrigin.NONE)
    if not effective.succeeded or effective.output_truncated:
        return AccountFact(name, FactStatus.UNKNOWN, IdentityOrigin.UNKNOWN)
    parsed = _parse_passwd_line(effective.stdout.text, name)
    if parsed is None or "\n" in effective.stdout.text.rstrip("\n"):
        return AccountFact(name, FactStatus.UNKNOWN, IdentityOrigin.UNKNOWN)
    origin = IdentityOrigin.NSS
    if local.succeeded and not local.output_truncated:
        local_parsed = _parse_passwd_line(local.stdout.text, name)
        if local_parsed is None or local_parsed.uid != parsed.uid or local_parsed.gid != parsed.gid:
            return AccountFact(name, FactStatus.UNKNOWN, IdentityOrigin.UNKNOWN)
        origin = IdentityOrigin.LOCAL
    elif not (local.outcome is CommandOutcome.NONZERO and not local.stdout.text.strip()):
        return AccountFact(name, FactStatus.UNKNOWN, IdentityOrigin.UNKNOWN)
    groups = _run(runner, "/usr/bin/id", "-G", name)
    if not groups.succeeded or groups.output_truncated:
        return AccountFact(name, FactStatus.UNKNOWN, IdentityOrigin.UNKNOWN)
    try:
        gids = tuple(sorted({int(value) for value in groups.stdout.text.split()}))
    except ValueError:
        return AccountFact(name, FactStatus.UNKNOWN, IdentityOrigin.UNKNOWN)
    return AccountFact(name, FactStatus.OK, origin, parsed.uid, parsed.gid, parsed.home, parsed.shell, gids)


def _group(runner: CommandRunner, name: str) -> GroupFact:
    effective = _run(runner, "/usr/bin/getent", "group", name)
    local = _run(runner, "/usr/bin/getent", "-s", "files", "group", name)
    if effective.outcome is CommandOutcome.NONZERO and not effective.stdout.text.strip():
        return GroupFact(name, FactStatus.ABSENT, IdentityOrigin.NONE)
    if not effective.succeeded or effective.output_truncated:
        return GroupFact(name, FactStatus.UNKNOWN, IdentityOrigin.UNKNOWN)
    parsed = _parse_group_line(effective.stdout.text, name)
    if parsed is None or "\n" in effective.stdout.text.rstrip("\n"):
        return GroupFact(name, FactStatus.UNKNOWN, IdentityOrigin.UNKNOWN)
    origin = IdentityOrigin.NSS
    if local.succeeded and not local.output_truncated:
        local_parsed = _parse_group_line(local.stdout.text, name)
        if local_parsed is None or local_parsed.gid != parsed.gid:
            return GroupFact(name, FactStatus.UNKNOWN, IdentityOrigin.UNKNOWN)
        origin = IdentityOrigin.LOCAL
    elif not (local.outcome is CommandOutcome.NONZERO and not local.stdout.text.strip()):
        return GroupFact(name, FactStatus.UNKNOWN, IdentityOrigin.UNKNOWN)
    return GroupFact(name, FactStatus.OK, origin, parsed.gid, parsed.members)


def _database_ids(runner: CommandRunner, database: str) -> tuple[FactStatus, tuple[int, ...]]:
    result = _run(runner, "/usr/bin/getent", database)
    if not result.succeeded or result.output_truncated:
        return FactStatus.UNKNOWN, ()
    parser = _parse_passwd_line if database == "passwd" else _parse_group_line
    values: list[int] = []
    for line in result.stdout.text.splitlines():
        parsed = parser(line)
        if parsed is None:
            return FactStatus.UNKNOWN, ()
        value = parsed.uid if isinstance(parsed, AccountFact) else parsed.gid
        assert value is not None
        values.append(value)
    return FactStatus.OK, tuple(sorted(set(values)))


def _read_regular(path: Path, maximum: int = 65_536) -> str | None:
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
        try:
            metadata = os.fstat(descriptor)
            if not stat.S_ISREG(metadata.st_mode) or metadata.st_size > maximum:
                return None
            data = os.read(descriptor, maximum + 1)
        finally:
            os.close(descriptor)
    except OSError:
        return None
    if len(data) > maximum:
        return None
    try:
        return data.decode("utf-8", errors="strict")
    except UnicodeDecodeError:
        return None


def _allocation_range(read: Callable[[Path], str | None] = _read_regular) -> AllocationRange:
    text = read(Path("/etc/adduser.conf"))
    if text is None:
        return AllocationRange(FactStatus.UNKNOWN)
    values: dict[str, int] = {}
    for line in text.splitlines():
        clean = line.split("#", 1)[0].strip()
        match = re.fullmatch(r"(FIRST_SYSTEM_UID|LAST_SYSTEM_UID|FIRST_SYSTEM_GID|LAST_SYSTEM_GID)\s*=\s*([0-9]+)", clean)
        if match:
            values[match.group(1)] = int(match.group(2))
    required = {"FIRST_SYSTEM_UID", "LAST_SYSTEM_UID", "FIRST_SYSTEM_GID", "LAST_SYSTEM_GID"}
    if values.keys() < required:
        return AllocationRange(FactStatus.UNKNOWN)
    result = AllocationRange(
        FactStatus.OK,
        values["FIRST_SYSTEM_UID"],
        values["LAST_SYSTEM_UID"],
        values["FIRST_SYSTEM_GID"],
        values["LAST_SYSTEM_GID"],
    )
    if not (0 < result.uid_min <= result.uid_max < 2**31 and 0 < result.gid_min <= result.gid_max < 2**31):
        return AllocationRange(FactStatus.UNKNOWN)
    return result


def _path_fact(path: Path) -> PathFact:
    observed = lstat_owned_path(path)
    if observed.status is CollectionStatus.ABSENT:
        return PathFact(str(path), FactStatus.ABSENT)
    if observed.status is not CollectionStatus.OK or observed.value is None:
        return PathFact(str(path), FactStatus.UNKNOWN)
    metadata = observed.value
    kind = "regular" if stat.S_ISREG(metadata.st_mode) else "directory" if stat.S_ISDIR(metadata.st_mode) else "other"
    return PathFact(str(path), FactStatus.OK, kind)


def _lxd_prerequisite(runner: CommandRunner) -> LxdPrerequisite:
    result = _run(runner, "/usr/bin/snap", "list", "lxd")
    if result.succeeded:
        return LxdPrerequisite(GateState.UNRESOLVED, "installed LXD requires complete resource and socket inventory")
    # Snap absence alone says nothing about distro packages, installer state,
    # units, sockets, processes, or retained LXD resources. A future normalized
    # collector may construct VERIFIED only after all of those probes complete.
    return LxdPrerequisite(GateState.UNRESOLVED, "Complete LXD absence or non-use evidence is unavailable")


def collect_identity_facts(config: Configuration, *, runner: CommandRunner | None = None) -> IdentityFacts:
    """Collect allowlisted account/group facts without privilege or shadow access."""

    active_runner = runner or CommandRunner()
    operator = config.identity.operator
    account_names = SERVICE_NAMES + ((operator,) if operator is not None else ())
    group_names = SERVICE_NAMES + ("kvm", "sudo", "lxd")
    accounts = tuple(sorted((_account(active_runner, name) for name in account_names), key=lambda item: item.name))
    groups = tuple(sorted((_group(active_runner, name) for name in group_names), key=lambda item: item.name))
    uid_status, uids = _database_ids(active_runner, "passwd")
    gid_status, gids = _database_ids(active_runner, "group")
    allocation = _allocation_range()
    if uid_status is not FactStatus.OK or gid_status is not FactStatus.OK:
        allocation = AllocationRange(FactStatus.UNKNOWN)
    return IdentityFacts(
        accounts,
        groups,
        uids,
        gids,
        allocation,
        _path_fact(Path("/usr/sbin/nologin")),
        _path_fact(Path("/nonexistent")),
        _lxd_prerequisite(active_runner),
    )


def _allocate(first: int, last: int, occupied: set[int], count: int) -> tuple[int, ...] | None:
    available: list[int] = []
    for candidate in range(first, last + 1):
        if candidate not in occupied:
            available.append(candidate)
            if len(available) == count:
                return tuple(available)
    return None


def _bounded_text(value: object, maximum: int = 512) -> bool:
    if value.__class__ is not str:
        return False
    try:
        encoded = value.encode("utf-8", errors="strict")
    except UnicodeEncodeError:
        return False
    return 0 < len(encoded) <= maximum and not any(
        ord(character) < 32 or ord(character) == 127 for character in value
    )


def _runtime_inputs_valid(
    host: HostFacts,
    facts: IdentityFacts,
    prerequisites: IdentityPrerequisites,
) -> bool:
    if host.__class__ is not HostFacts or facts.__class__ is not IdentityFacts:
        return False
    if prerequisites.__class__ is not IdentityPrerequisites:
        return False
    if any(value.__class__ is not GateState for value in prerequisites.__dict__.values()):
        return False
    if not all(
        value is None or value.__class__ is str
        for value in (host.os_id, host.os_version_id, host.architecture)
    ):
        return False
    if facts.accounts.__class__ is not tuple or facts.groups.__class__ is not tuple:
        return False
    for item in facts.accounts:
        if (
            item.__class__ is not AccountFact
            or not _bounded_text(item.name, 31)
            or item.status.__class__ is not FactStatus
            or item.origin.__class__ is not IdentityOrigin
            or item.supplementary_gids.__class__ is not tuple
            or any(value.__class__ is not int or not 0 <= value < 2**32 for value in item.supplementary_gids)
            or any(value is not None and (value.__class__ is not int or not 0 <= value < 2**32) for value in (item.uid, item.gid))
            or any(value is not None and not _bounded_text(value, 4096) for value in (item.home, item.shell))
        ):
            return False
    for item in facts.groups:
        if (
            item.__class__ is not GroupFact
            or not _bounded_text(item.name, 31)
            or item.status.__class__ is not FactStatus
            or item.origin.__class__ is not IdentityOrigin
            or (item.gid is not None and (item.gid.__class__ is not int or not 0 <= item.gid < 2**32))
            or item.members.__class__ is not tuple
            or any(not _bounded_text(value, 31) for value in item.members)
        ):
            return False
    if (
        facts.occupied_uids.__class__ is not tuple
        or facts.occupied_gids.__class__ is not tuple
        or any(value.__class__ is not int or not 0 <= value < 2**32 for value in facts.occupied_uids + facts.occupied_gids)
        or facts.allocation_range.__class__ is not AllocationRange
        or facts.allocation_range.status.__class__ is not FactStatus
        or facts.nologin.__class__ is not PathFact
        or facts.nonexistent.__class__ is not PathFact
        or facts.lxd.__class__ is not LxdPrerequisite
        or facts.lxd.non_use.__class__ is not GateState
    ):
        return False
    for item in (facts.nologin, facts.nonexistent):
        if item.status.__class__ is not FactStatus or not _bounded_text(item.path, 4096):
            return False
        if item.kind is not None and item.kind not in {"regular", "directory", "other"}:
            return False
    allocation_values = (
        facts.allocation_range.uid_min,
        facts.allocation_range.uid_max,
        facts.allocation_range.gid_min,
        facts.allocation_range.gid_max,
    )
    return all(value is None or (value.__class__ is int and 0 < value < 2**31) for value in allocation_values)


def _invalid_input_plan(config: Configuration) -> IdentityPlan:
    issue = IdentityIssue(
        "identity.discovery.invalid",
        "unknown",
        "Identity discovery or prerequisite facts are malformed.",
    )
    digest = hashlib.sha256(b"identity-access:invalid-input:v1").hexdigest()
    return IdentityPlan(
        config.deployment.lifecycle_mode,
        True,
        (),
        (),
        (issue,),
        "sha256:" + digest,
    )


def build_identity_plan(
    config: Configuration,
    host: HostFacts,
    facts: IdentityFacts,
    prerequisites: IdentityPrerequisites = IdentityPrerequisites(),
) -> IdentityPlan:
    """Build a pure plan. Any uncertainty suppresses the complete action set."""

    if not _runtime_inputs_valid(host, facts, prerequisites):
        return _invalid_input_plan(config)

    issues: list[IdentityIssue] = []
    if host.os_id != "ubuntu" or host.architecture not in {"x86_64", "amd64"}:
        issues.append(IdentityIssue("identity.platform", "unsupported", "Identity phase requires supported Ubuntu x86-64."))
    if host.os_version_id != "26.04" or config.deployment.lifecycle_mode is not LifecycleMode.PRODUCTION:
        issues.append(IdentityIssue("identity.lifecycle", "unsupported", "Identity apply is supported only on Ubuntu 26.04 production hosts."))
    if config.identity.operator is None:
        issues.append(IdentityIssue("identity.operator.configured", "unknown", "An explicit non-root operator must be configured."))

    for name, value in (
        ("host-key", prerequisites.host_key),
        ("recovery", prerequisites.recovery),
        ("second-session", prerequisites.second_session),
        ("sudo-policy", prerequisites.sudo_policy),
        ("operator-ssh-key", prerequisites.operator_ssh_key),
        ("bootstrap", prerequisites.bootstrap),
        ("journal", prerequisites.journal),
    ):
        if value is not GateState.VERIFIED:
            issues.append(IdentityIssue(f"identity.prerequisite.{name}", "unknown" if value is GateState.UNRESOLVED else "conflict", f"The {name} prerequisite is not verified."))
    if facts.lxd.non_use is not GateState.VERIFIED:
        issues.append(IdentityIssue("identity.prerequisite.lxd-non-use", "unknown", "Normalized LXD absence or non-use evidence is incomplete."))

    accounts: dict[str, AccountFact] = {}
    groups: dict[str, GroupFact] = {}
    duplicate_accounts: set[str] = set()
    duplicate_groups: set[str] = set()
    relevant_accounts = set(SERVICE_NAMES) | ({config.identity.operator} if config.identity.operator else set())
    relevant_groups = set(SERVICE_NAMES) | {"kvm", "sudo", "lxd"}
    for item in facts.accounts:
        if item.name not in relevant_accounts:
            continue
        if item.name in accounts:
            duplicate_accounts.add(item.name)
        else:
            accounts[item.name] = item
    for item in facts.groups:
        if item.name not in relevant_groups:
            continue
        if item.name in groups:
            duplicate_groups.add(item.name)
        else:
            groups[item.name] = item
    for name in sorted(duplicate_accounts | duplicate_groups):
        issues.append(IdentityIssue(f"identity.discovery.duplicate.{name}", "unknown", "Duplicate desired identity observations are invalid."))
    for service in SERVICE_NAMES:
        account = accounts.get(service)
        group = groups.get(service)
        account_valid = (
            account is not None
            and account.name == service
            and account.status.__class__ is FactStatus
            and account.origin.__class__ is IdentityOrigin
        )
        group_valid = (
            group is not None
            and group.name == service
            and group.status.__class__ is FactStatus
            and group.origin.__class__ is IdentityOrigin
        )
        if (
            not account_valid
            or not group_valid
            or account.status is FactStatus.UNKNOWN
            or group.status is FactStatus.UNKNOWN
        ):
            issues.append(IdentityIssue(f"identity.discovery.{service}", "unknown", "A desired identity observation is missing or malformed."))
        elif account.status is FactStatus.OK or group.status is FactStatus.OK:
            issues.append(IdentityIssue(f"identity.collision.{service}", "conflict", "A desired identity already exists without authenticated manifest ownership."))
    operator = accounts.get(config.identity.operator or "")
    sudo = groups.get("sudo")
    lxd = groups.get("lxd")
    kvm = groups.get("kvm")
    if config.identity.operator is not None:
        if operator is None or operator.status is not FactStatus.OK or operator.origin is not IdentityOrigin.LOCAL or operator.uid == 0:
            issues.append(IdentityIssue("identity.operator.local", "conflict", "The configured operator is not an exact local non-root account."))
        if sudo is None or sudo.status is not FactStatus.OK or sudo.gid is None or operator is None or sudo.gid not in operator.supplementary_gids:
            issues.append(IdentityIssue("identity.operator.sudo", "conflict", "The configured operator's retained sudo membership is not proven."))
    if kvm is None or kvm.status is not FactStatus.OK or kvm.origin is not IdentityOrigin.LOCAL:
        issues.append(IdentityIssue("identity.group.kvm", "conflict", "The existing local kvm group is not proven."))
    if facts.nologin.status is not FactStatus.OK or facts.nologin.kind != "regular":
        issues.append(IdentityIssue("identity.path.nologin", "conflict", "The nologin path is absent, unknown, or not a regular file."))
    if facts.nonexistent.status is not FactStatus.ABSENT:
        issues.append(IdentityIssue("identity.path.nonexistent", "conflict", "The /nonexistent home path must be absent."))
    allocation = facts.allocation_range
    allocations: tuple[IdentityAllocation, ...] = ()
    if allocation.status is not FactStatus.OK or None in (allocation.uid_min, allocation.uid_max, allocation.gid_min, allocation.gid_max):
        issues.append(IdentityIssue("identity.allocation.range", "unknown", "The validated host system UID/GID range is unavailable."))
    else:
        assert allocation.uid_min is not None and allocation.uid_max is not None
        assert allocation.gid_min is not None and allocation.gid_max is not None
        uids = _allocate(allocation.uid_min, allocation.uid_max, set(facts.occupied_uids), len(SERVICE_NAMES))
        gids = _allocate(allocation.gid_min, allocation.gid_max, set(facts.occupied_gids), len(SERVICE_NAMES))
        if uids is None or gids is None:
            issues.append(IdentityIssue("identity.allocation.exhausted", "conflict", "The validated system UID/GID range cannot satisfy the phase."))
        else:
            allocations = tuple(IdentityAllocation(name, uid, gid) for name, uid, gid in zip(SERVICE_NAMES, uids, gids, strict=True))

    blocking = bool(issues)
    actions: tuple[IdentityAction, ...] = ()
    if not blocking:
        built: list[IdentityAction] = []
        for item in allocations:
            built.append(IdentityAction(f"identity.group.{item.name}", "account_group", item.name, f"system group gid={item.gid}", "root", "delete only when journal-owned and unused"))
            supplementary = "kvm only" if item.name == "kitdev-worker" else "none"
            built.append(IdentityAction(f"identity.user.{item.name}", "account", item.name, f"locked nologin uid={item.uid} gid={item.gid}; supplementary={supplementary}", "root", "delete only when journal-owned and unused"))
        assert config.identity.operator is not None
        assert operator is not None and lxd is not None
        if lxd.status is FactStatus.OK and lxd.gid in operator.supplementary_gids:
            built.append(IdentityAction("identity.operator.lxd", "group_membership", "configured-operator", "absent from lxd; retained in sudo", "root", "restore prior membership from write-ahead journal"))
        actions = tuple(sorted(built, key=lambda action: action.action_id))

    issues_tuple = tuple(sorted(issues, key=lambda issue: issue.issue_id))
    hash_input = {
        "phase": "identity-access",
        "lifecycle": config.deployment.lifecycle_mode.value,
        "operator": config.identity.operator,
        "allocations": [item.as_dict() for item in allocations],
        "actions": [item.as_dict() for item in actions],
        "issues": [item.as_dict() for item in issues_tuple],
        "preconditions": [
            "revalidate exact UID/GID vacancies immediately before mutation",
            "acquire exclusive installation lock",
            "write and fsync prior state before each mutation",
            "refuse partial apply when plan hash or observed state changes",
        ],
    }
    plan_hash = "sha256:" + hashlib.sha256(json.dumps(hash_input, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
    return IdentityPlan(config.deployment.lifecycle_mode, blocking, allocations, actions, issues_tuple, plan_hash)


def render_identity_plan_text(plan: IdentityPlan) -> str:
    lines = [
        "kitdev install dry-run",
        "phase: identity-access",
        f"lifecycle mode: {plan.lifecycle_mode.value}",
        f"blocking: {'yes' if plan.blocking else 'no'}",
        f"plan hash: {plan.plan_hash}",
        f"actions: {len(plan.actions)}",
        f"allocations: {len(plan.allocations)}",
        f"issues: {len(plan.issues)}",
    ]
    for issue in plan.issues:
        lines.append(f"[{issue.code}] {issue.issue_id}: {issue.explanation}")
    lines.append("No changes were made.")
    rendered = "\n".join(lines)
    if len(rendered.encode("utf-8")) > _MAX_REPORT_BYTES:
        raise ValueError("identity plan text exceeds its global byte limit")
    return rendered
