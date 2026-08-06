"""Read-only composition of collection, evaluation, and change planning."""

from __future__ import annotations

import re
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable, Mapping, cast

from kitdev_sandboxes import __version__
from kitdev_sandboxes.collectors import (
    CollectionStatus,
    LinuxFacts,
    Ownership as CollectorOwnership,
    Probe,
    SystemdActiveState,
    collect_linux_facts,
    lstat_owned_path,
)
from kitdev_sandboxes.config import Configuration
from kitdev_sandboxes.planning import (
    ChangePlan,
    Confidence,
    IssueCode,
    IssueSeverity,
    ObservedState,
    Ownership,
    PlanIssue,
    PlanningFacts,
    ResourceFact,
    build_change_plan,
)
from kitdev_sandboxes.preflight import (
    CheckResult,
    CheckStatus,
    DoctorReport,
    FailureCategory,
    HostFacts,
    Severity,
    build_doctor_report,
    redact_report,
    safe_report_text,
)
from kitdev_sandboxes.runner import CommandRunner


_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_DIRECTORY_IDS = {
    "config": "directory.config",
    "install": "directory.install",
    "logs": "directory.logs",
    "runtime": "directory.runtime",
    "state": "directory.state",
}
_PLATFORM_GATE_IDS = frozenset(
    {
        "platform.release_lifecycle",
        "platform.architecture",
        "platform.systemd",
        "platform.cgroups_v2",
        "virtualization.cpu",
        "virtualization.kvm_device",
    }
)
_INSTALL_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{7,127}")
_MAX_RENDERED_PLAN_BYTES = 1_048_576


@dataclass(frozen=True)
class AuthenticatedDirectoryOwnership:
    """Directory ownership already authenticated against one install manifest."""

    installation_id: str
    manifest_path: Path
    owned_targets: frozenset[str]

    def __post_init__(self) -> None:
        if not _INSTALL_ID_RE.fullmatch(self.installation_id):
            raise ValueError("installation_id must be a bounded opaque identifier")
        if self.manifest_path not in {
            Path("/etc/kitdev-sandboxes/install-manifest.json"),
            Path("/var/lib/kitdev-sandboxes/install-manifest.json"),
        }:
            raise ValueError("manifest_path must be a declared installation manifest")
        if not all(Path(target).is_absolute() for target in self.owned_targets):
            raise ValueError("owned_targets must contain absolute paths")


@dataclass(frozen=True)
class InstallPlanReport:
    plan: ChangePlan
    exit_code: int

    def as_dict(self) -> dict[str, object]:
        plan = self.plan.as_dict()
        return redact_report(
            {
                "schema_version": 1,
                "project_release": __version__,
                "lifecycle_mode": plan["lifecycle_mode"],
                "command_mode": "install-dry-run",
                "dry_run": True,
                "blocking": plan["blocking"],
                "summary": plan["summary"],
                "actions": plan["actions"],
                "issues": plan["issues"],
            }
        )

    def to_json(self) -> str:
        import json

        rendered = json.dumps(self.as_dict(), sort_keys=True, separators=(",", ":"))
        if len(rendered.encode("utf-8")) > _MAX_RENDERED_PLAN_BYTES:
            raise ValueError("install plan output exceeds its global byte limit")
        return rendered


def collect_configured_linux_facts(config: Configuration) -> LinuxFacts:
    paths = tuple(Path(value) for value in _configured_paths(config).values())
    return collect_linux_facts(
        configured_paths=paths,
        runner=CommandRunner(),
        project_root=_PROJECT_ROOT,
    )


def _configured_paths(config: Configuration) -> Mapping[str, str]:
    return {
        "config": config.paths.config,
        "install": config.paths.install,
        "logs": config.paths.logs,
        "runtime": config.paths.runtime,
        "state": config.paths.state,
    }


def collect_directory_resource_facts(
    config: Configuration,
    *,
    stat_path: Callable[[Path], Probe[stat_result]] = lstat_owned_path,
    authenticated_ownership: AuthenticatedDirectoryOwnership | None = None,
) -> tuple[ResourceFact, ...]:
    """Observe exact configured roots without following any path component symlink."""

    facts: list[ResourceFact] = []
    for name, target in sorted(_configured_paths(config).items()):
        resource_id = _DIRECTORY_IDS[name]
        observed = stat_path(Path(target))
        if observed.status is CollectionStatus.ABSENT:
            state = ObservedState.ABSENT
            ownership = Ownership.UNOWNED
            confidence = Confidence.HIGH
        elif observed.status is CollectionStatus.OK and observed.value is not None:
            if stat.S_ISDIR(observed.value.st_mode):
                state = ObservedState.PRESENT
                ownership = (
                    Ownership.PROJECT
                    if authenticated_ownership is not None
                    and target in authenticated_ownership.owned_targets
                    else Ownership.UNKNOWN
                )
            else:
                state = ObservedState.UNSUPPORTED
                ownership = Ownership.UNKNOWN
            confidence = Confidence.HIGH
        else:
            state = ObservedState.UNKNOWN
            ownership = Ownership.UNKNOWN
            confidence = Confidence.UNKNOWN
        facts.append(ResourceFact(resource_id, target, state, ownership, confidence))
    return tuple(sorted(facts, key=lambda fact: fact.resource_id))


def _known(probes: Iterable[Probe[object]]) -> bool:
    return all(probe.status is CollectionStatus.OK and probe.value is not None for probe in probes)


def _result(
    check_id: str,
    status: CheckStatus,
    explanation: str,
    *,
    remediation: str | None = None,
    installer_can_remediate: bool = False,
    failure_category: FailureCategory | None = None,
) -> CheckResult:
    severity = (
        Severity.BLOCKING
        if status in {CheckStatus.FAIL, CheckStatus.UNKNOWN}
        else Severity.WARNING
        if status is CheckStatus.WARN
        else Severity.INFO
    )
    if status is CheckStatus.UNKNOWN and failure_category is None:
        failure_category = FailureCategory.UNKNOWN
    if status is CheckStatus.FAIL and failure_category is None:
        failure_category = FailureCategory.PLATFORM
    return CheckResult(
        check_id,
        status,
        severity,
        explanation,
        remediation,
        installer_can_remediate,
        failure_category,
    )


def _kernel_check(facts: LinuxFacts) -> CheckResult | None:
    nbd_devices = facts.devices.nbd.devices
    probes: list[Probe[object]] = [
        facts.devices.kvm_modules,
        facts.devices.nbd.module_loaded,
        facts.devices.nbd.max_devices,
        facts.devices.nbd.max_partitions,
        facts.devices.huge_pages.size_kib,
        facts.devices.huge_pages.total,
        facts.devices.huge_pages.free,
        facts.devices.huge_pages.reserved,
        facts.devices.huge_pages.surplus,
        facts.devices.huge_pages.mounts,
        facts.devices.tun_exists,
        facts.devices.tun_is_character_device,
        nbd_devices,
    ]
    if not _known(probes):
        return None
    assert nbd_devices.value is not None
    if not _known(device.in_use for device in nbd_devices.value):
        return None
    max_devices = facts.devices.nbd.max_devices.value
    max_partitions = facts.devices.nbd.max_partitions.value
    huge_total = facts.devices.huge_pages.total.value
    huge_free = facts.devices.huge_pages.free.value
    huge_reserved = facts.devices.huge_pages.reserved.value
    if not all(
        type(value) is int
        for value in (max_devices, max_partitions, huge_total, huge_free, huge_reserved)
    ):
        return _result(
            "scope.kernel_facilities",
            CheckStatus.UNKNOWN,
            "Kernel facility facts were complete but internally invalid.",
        )
    max_devices = cast(int, max_devices)
    max_partitions = cast(int, max_partitions)
    huge_total = cast(int, huge_total)
    huge_free = cast(int, huge_free)
    huge_reserved = cast(int, huge_reserved)
    if (
        max_devices < len(nbd_devices.value)
        or max_partitions < 0
        or huge_total < 0
        or not 0 <= huge_free <= huge_total
        or not 0 <= huge_reserved <= huge_total
    ):
        return _result(
            "scope.kernel_facilities",
            CheckStatus.UNKNOWN,
            "Kernel facility facts were complete but internally invalid.",
        )
    modules = facts.devices.kvm_modules.value or ()
    if "kvm" not in modules or not ({"kvm_amd", "kvm_intel"} & set(modules)):
        return _result(
            "scope.kernel_facilities",
            CheckStatus.FAIL,
            "KVM base and vendor module state is incomplete.",
            remediation="Prepare the supported KVM modules outside read-only doctor.",
        )
    if not facts.devices.tun_exists.value or not facts.devices.tun_is_character_device.value:
        return _result(
            "scope.kernel_facilities",
            CheckStatus.FAIL,
            "The TUN/TAP character device is unavailable.",
            remediation="Prepare /dev/net/tun outside read-only doctor.",
        )
    if not facts.devices.nbd.module_loaded.value:
        return _result(
            "scope.kernel_facilities",
            CheckStatus.WARN,
            "Kernel facilities were inventoried; NBD is not loaded.",
            remediation="Prepare NBD only through an approved host-change plan.",
            installer_can_remediate=True,
        )
    return _result(
        "scope.kernel_facilities",
        CheckStatus.PASS,
        "KVM, NBD, huge-page, and TUN/TAP facts were collected and are structurally valid.",
    )


def _capacity_check(facts: LinuxFacts, expected_paths: int) -> CheckResult | None:
    probes: list[Probe[object]] = [
        facts.memory.total_bytes,
        facts.memory.available_bytes,
        facts.memory.swap_total_bytes,
        facts.memory.swap_free_bytes,
    ]
    if len(facts.filesystems) != expected_paths:
        return None
    for filesystem in facts.filesystems:
        probes.extend(
            [
                filesystem.total_bytes,
                filesystem.available_bytes,
                filesystem.total_inodes,
                filesystem.available_inodes,
                filesystem.filesystem_type,
                filesystem.mount_options,
            ]
        )
    if not _known(probes):
        return None
    numeric = [probe.value for probe in probes if type(probe.value) is int]
    if any(value < 0 for value in numeric):
        return _result(
            "scope.capacity",
            CheckStatus.UNKNOWN,
            "Capacity facts were complete but internally invalid.",
        )
    if not all(
        type(value) is int
        for value in (
            facts.memory.total_bytes.value,
            facts.memory.available_bytes.value,
            facts.memory.swap_total_bytes.value,
            facts.memory.swap_free_bytes.value,
        )
    ):
        return _result(
            "scope.capacity",
            CheckStatus.UNKNOWN,
            "Capacity facts were complete but internally invalid.",
        )
    memory_total = cast(int, facts.memory.total_bytes.value)
    memory_available = cast(int, facts.memory.available_bytes.value)
    swap_total = cast(int, facts.memory.swap_total_bytes.value)
    swap_free = cast(int, facts.memory.swap_free_bytes.value)
    if (
        memory_available > memory_total
        or swap_free > swap_total
    ):
        return _result(
            "scope.capacity",
            CheckStatus.UNKNOWN,
            "Capacity facts were complete but internally invalid.",
        )
    for filesystem in facts.filesystems:
        if not all(
            type(value) is int
            for value in (
                filesystem.total_bytes.value,
                filesystem.available_bytes.value,
                filesystem.total_inodes.value,
                filesystem.available_inodes.value,
            )
        ):
            return _result(
                "scope.capacity",
                CheckStatus.UNKNOWN,
                "Capacity facts were complete but internally invalid.",
            )
        filesystem_total = cast(int, filesystem.total_bytes.value)
        filesystem_available = cast(int, filesystem.available_bytes.value)
        inode_total = cast(int, filesystem.total_inodes.value)
        inode_available = cast(int, filesystem.available_inodes.value)
        if (
            filesystem_available > filesystem_total
            or inode_available > inode_total
        ):
            return _result(
                "scope.capacity",
                CheckStatus.UNKNOWN,
                "Capacity facts were complete but internally invalid.",
            )
    return _result(
        "scope.capacity",
        CheckStatus.WARN,
        "Memory, swap, filesystem, and inode inventories are complete; "
        "profile admission thresholds remain unapproved.",
        remediation="Approve versioned profile capacity thresholds before installation readiness.",
    )


def _tool_complete(present: Probe[bool], version: Probe[str], active: Probe[bool]) -> bool:
    if present.status is not CollectionStatus.OK or present.value is None:
        return False
    if present.value:
        return (
            version.status is CollectionStatus.OK
            and version.value is not None
            and active.status is CollectionStatus.OK
            and active.value is not None
        )
    return (
        version.status is CollectionStatus.ABSENT
        and active.status in {CollectionStatus.OK, CollectionStatus.ABSENT}
    )


def _services_check(facts: LinuxFacts) -> CheckResult | None:
    if not _tool_complete(facts.docker.present, facts.docker.version, facts.docker.active):
        return None
    if not _tool_complete(facts.compose.present, facts.compose.version, facts.compose.active):
        return None
    service_probes: list[Probe[object]] = []
    for service in (*facts.conflicting_services, *facts.installed.owned_services):
        service_probes.extend([service.active, service.unit_file_state])
    if not _known(service_probes):
        return None
    if facts.installed.markers.status is not CollectionStatus.OK:
        return None
    if facts.installed.upstream_lock_present.status is not CollectionStatus.OK:
        return None
    if not _known(facts.installed.templates.values()):
        return None
    markers = facts.installed.markers.value or ()
    if markers and facts.installed.installed_version.status is not CollectionStatus.OK:
        return None
    if not markers and facts.installed.installed_version.status not in {
        CollectionStatus.OK,
        CollectionStatus.ABSENT,
    }:
        return None
    if not facts.installed.upstream_lock_present.value:
        return _result(
            "scope.services",
            CheckStatus.FAIL,
            "The committed upstream version lock is missing or has the wrong type.",
        )
    if not facts.docker.present.value or not facts.compose.present.value:
        return _result(
            "scope.services",
            CheckStatus.WARN,
            "Service inventory is complete; Docker Engine or Compose is absent.",
            remediation="Plan package installation only after the host-change gate is approved.",
            installer_can_remediate=True,
        )
    if not facts.docker.active.value:
        return _result(
            "scope.services",
            CheckStatus.WARN,
            "Docker Engine is installed but inactive.",
            remediation="Review existing Docker ownership and service state before changes.",
        )
    if markers:
        if facts.installed.installed_version.status is not CollectionStatus.OK:
            return _result(
                "scope.services",
                CheckStatus.FAIL,
                "Project markers exist but the installed version is invalid or unreadable.",
                failure_category=FailureCategory.UNHEALTHY,
            )
        for service in facts.installed.owned_services:
            if service.ownership is not CollectorOwnership.PROJECT:
                continue
            if service.active.value is not SystemdActiveState.ACTIVE:
                return _result(
                    "scope.services",
                    CheckStatus.FAIL,
                    "A manifest-owned project service is unhealthy.",
                    failure_category=FailureCategory.UNHEALTHY,
                )
    return _result(
        "scope.services",
        CheckStatus.PASS,
        "Docker, Compose, service, installation-marker, lock, and template facts are complete.",
    )


def _security_check(facts: LinuxFacts) -> CheckResult | None:
    probes: list[Probe[object]] = [
        facts.security.apparmor_enabled,
        facts.security.apparmor_service_active,
        facts.security.time_synchronized,
        facts.firewall.nftables.present,
        facts.firewall.ufw.present,
    ]
    if not _known(probes):
        return None
    if not _tool_complete(
        facts.firewall.nftables.present,
        facts.firewall.nftables.version,
        facts.firewall.nftables.active,
    ):
        return None
    if not _tool_complete(
        facts.firewall.ufw.present,
        facts.firewall.ufw.version,
        facts.firewall.ufw.active,
    ):
        return None
    if facts.firewall.nftables.present.value:
        if facts.firewall.nftables_tables.status is not CollectionStatus.OK:
            return None
    elif facts.firewall.nftables_tables.status is not CollectionStatus.ABSENT:
        return None
    if not facts.security.apparmor_enabled.value:
        return _result(
            "scope.security_posture",
            CheckStatus.WARN,
            "Security inventory is complete; AppArmor is disabled.",
            remediation="Review AppArmor policy before untrusted sandbox execution.",
        )
    if not facts.security.time_synchronized.value:
        return _result(
            "scope.security_posture",
            CheckStatus.WARN,
            "Security inventory is complete; time synchronization is not confirmed.",
        )
    return _result(
        "scope.security_posture",
        CheckStatus.PASS,
        "AppArmor, time synchronization, and host firewall facts are complete.",
    )


def build_composed_doctor_report(
    config: Configuration,
    host_facts: HostFacts,
    linux_facts: LinuxFacts | None,
    *,
    dry_run: bool = False,
) -> DoctorReport:
    base = build_doctor_report(
        host_facts, config.deployment.lifecycle_mode, dry_run=dry_run
    )
    if linux_facts is None:
        return base
    replacements = {
        "scope.kernel_facilities": _kernel_check(linux_facts),
        "scope.capacity": _capacity_check(linux_facts, len(_configured_paths(config))),
        "scope.services": _services_check(linux_facts),
        "scope.security_posture": _security_check(linux_facts),
    }
    checks = tuple(
        replacements.get(check.check_id) or check
        if check.check_id in replacements
        else check
        for check in base.checks
    )
    return DoctorReport(
        base.generated_at,
        base.lifecycle_mode,
        base.dry_run,
        base.facts,
        checks,
    )


def _platform_gate(report: DoctorReport) -> tuple[CheckResult, ...]:
    return tuple(
        check
        for check in report.checks
        if check.check_id in _PLATFORM_GATE_IDS
        and check.status in {CheckStatus.FAIL, CheckStatus.UNKNOWN}
    )


def _platform_blocked_plan(
    config: Configuration, host_facts: HostFacts, failures: tuple[CheckResult, ...]
) -> ChangePlan:
    concrete_failure = any(check.status is CheckStatus.FAIL for check in failures)
    issue = PlanIssue(
        "platform.preflight.blocked",
        IssueCode.UNSUPPORTED if concrete_failure else IssueCode.UNKNOWN,
        IssueSeverity.BLOCKING,
        "platform.preflight",
        "Platform or lifecycle preflight blocks install planning.",
        "Resolve the platform preflight result before calculating host changes.",
    )
    lifecycle_plan = build_change_plan(
        config,
        PlanningFacts(host_facts.os_id, host_facts.os_version_id, ()),
    )
    warnings = tuple(item for item in lifecycle_plan.issues if not item.blocking)
    issues = tuple(sorted((*warnings, issue), key=lambda item: item.issue_id))
    return ChangePlan(config.deployment.lifecycle_mode, (), issues)


def _port_policy_issue() -> PlanIssue:
    return PlanIssue(
        "network.required_ports.policy",
        IssueCode.UNKNOWN,
        IssueSeverity.BLOCKING,
        "network.required_ports",
        "Pinned required-port ownership and bind policy is not yet approved.",
        "Approve the versioned port policy before producing an actionable install plan.",
    )


def _inventory_issues(report: DoctorReport) -> tuple[PlanIssue, ...]:
    issues: list[PlanIssue] = []
    for check in report.checks:
        if check.check_id not in {
            "scope.kernel_facilities",
            "scope.capacity",
            "scope.services",
            "scope.security_posture",
        } or check.status not in {CheckStatus.FAIL, CheckStatus.UNKNOWN}:
            continue
        suffix = check.check_id.removeprefix("scope.")
        platform = check.failure_category is FailureCategory.PLATFORM
        unhealthy = check.failure_category is FailureCategory.UNHEALTHY
        issues.append(
            PlanIssue(
                f"host.inventory.{suffix}",
                IssueCode.UNSUPPORTED if check.status is CheckStatus.FAIL else IssueCode.UNKNOWN,
                IssueSeverity.BLOCKING,
                "platform.inventory"
                if platform
                else "installed.health"
                if unhealthy
                else "host.inventory",
                "A required normalized host fact group is invalid or incomplete.",
                "Resolve read-only host inventory failures before installation planning.",
            )
        )
    return tuple(issues)


def _plan_exit_code(plan: ChangePlan) -> int:
    codes: list[int] = []
    for issue in plan.issues:
        if not issue.blocking:
            continue
        if issue.fact_id.startswith("platform."):
            codes.append(3 if issue.code is not IssueCode.UNKNOWN else 5)
        elif issue.fact_id == "installed.health":
            codes.append(6)
        elif issue.code in {IssueCode.CONFLICT, IssueCode.INVALID_FACT, IssueCode.UNSUPPORTED}:
            codes.append(4)
        else:
            codes.append(5)
    return min(codes, default=0)


def build_install_plan_report(
    config: Configuration,
    host_facts: HostFacts,
    *,
    linux_facts_collector: Callable[[Configuration], LinuxFacts] = collect_configured_linux_facts,
    directory_facts_collector: Callable[
        [Configuration], tuple[ResourceFact, ...]
    ] = collect_directory_resource_facts,
) -> InstallPlanReport:
    platform_report = build_doctor_report(host_facts, config.deployment.lifecycle_mode)
    failures = _platform_gate(platform_report)
    if failures:
        plan = _platform_blocked_plan(config, host_facts, failures)
        return InstallPlanReport(plan, _plan_exit_code(plan))

    # Collection is intentionally sequenced after platform/lifecycle eligibility.
    linux_facts = linux_facts_collector(config)
    if not isinstance(linux_facts, LinuxFacts):
        raise TypeError("linux_facts_collector must return LinuxFacts")
    resources = directory_facts_collector(config)
    if not isinstance(resources, tuple) or not all(
        isinstance(resource, ResourceFact) for resource in resources
    ):
        raise TypeError("directory_facts_collector must return ResourceFact tuple")
    plan = build_change_plan(
        config,
        PlanningFacts(host_facts.os_id, host_facts.os_version_id, resources),
    )
    inventory_report = build_composed_doctor_report(config, host_facts, linux_facts)
    issues = tuple(
        sorted(
            (*plan.issues, *_inventory_issues(inventory_report), _port_policy_issue()),
            key=lambda item: item.issue_id,
        )
    )
    final = ChangePlan(config.deployment.lifecycle_mode, (), issues)
    return InstallPlanReport(final, _plan_exit_code(final))


def render_install_plan_text(report: InstallPlanReport) -> str:
    data = report.as_dict()
    lines = [
        "kitdev install --dry-run (read-only)",
        f"Lifecycle: {safe_report_text(str(data['lifecycle_mode']))}",
        f"Blocking: {'yes' if data['blocking'] else 'no'}",
        "",
    ]
    for issue in report.plan.issues:
        lines.append(
            f"[{issue.severity.value.upper():8}] {safe_report_text(issue.issue_id)}: "
            f"{safe_report_text(issue.explanation)}"
        )
    for action in report.plan.actions:
        lines.append(
            f"[ACTION  ] {safe_report_text(action.action_id)}: "
            f"{safe_report_text(action.target)}"
        )
    summary = data["summary"]
    assert isinstance(summary, dict)
    lines.extend(
        [
            "",
            f"Summary: {summary['actions']} actions, {summary['issues']} issues, "
            f"{summary['blocking_issues']} blocking",
            "No changes were made.",
        ]
    )
    rendered = "\n".join(lines)
    encoded = rendered.encode("utf-8")[:_MAX_RENDERED_PLAN_BYTES]
    return encoded.decode("utf-8", errors="ignore")
