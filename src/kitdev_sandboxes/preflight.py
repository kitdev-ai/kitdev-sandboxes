"""Read-only host fact collection and preflight evaluation."""

from __future__ import annotations

import hashlib
import os
import platform
import re
import stat
from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any, Callable, Mapping
from urllib.parse import unquote

from kitdev_sandboxes import __version__
from kitdev_sandboxes.config import LifecycleMode


class CheckStatus(StrEnum):
    PASS = "pass"
    WARN = "warn"
    FAIL = "fail"
    UNKNOWN = "unknown"
    SKIPPED = "skipped"


class Severity(StrEnum):
    INFO = "info"
    WARNING = "warning"
    BLOCKING = "blocking"


class FailureCategory(StrEnum):
    PLATFORM = "platform"
    CONFLICT = "conflict"
    UNKNOWN = "unknown"
    UNHEALTHY = "unhealthy"

    @property
    def exit_code(self) -> int:
        return {
            FailureCategory.PLATFORM: 3,
            FailureCategory.CONFLICT: 4,
            FailureCategory.UNKNOWN: 5,
            FailureCategory.UNHEALTHY: 6,
        }[self]


@dataclass(frozen=True)
class HostFacts:
    os_id: str | None
    os_name: str | None
    os_version_id: str | None
    architecture: str | None
    pid1_comm: str | None
    cgroup_v2: bool | None
    cpu_virtualization: str | None
    kvm_device_exists: bool
    kvm_device_is_character: bool
    kvm_device_accessible: bool
    nested_guest_support: bool | None
    evidence: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class CheckResult:
    check_id: str
    status: CheckStatus
    severity: Severity
    explanation: str
    remediation: str | None = None
    installer_can_remediate: bool = False
    failure_category: FailureCategory | None = None
    evidence: str | None = None

    def as_dict(self, *, verbose: bool = False) -> dict[str, Any]:
        result: dict[str, Any] = {
            "id": self.check_id,
            "status": self.status.value,
            "severity": self.severity.value,
            "explanation": self.explanation,
            "remediation": self.remediation,
            "installer_can_remediate": self.installer_can_remediate,
            "failure_category": (
                self.failure_category.value if self.failure_category is not None else None
            ),
        }
        if verbose:
            result["evidence"] = self.evidence
        return result


@dataclass(frozen=True)
class DoctorReport:
    generated_at: str
    lifecycle_mode: LifecycleMode
    dry_run: bool
    facts: HostFacts
    checks: tuple[CheckResult, ...]

    @property
    def exit_code(self) -> int:
        """Use the lowest documented code when independent failures coexist."""

        codes = [
            check.failure_category.exit_code
            for check in self.checks
            if check.failure_category is not None
            and check.status in {CheckStatus.FAIL, CheckStatus.UNKNOWN}
        ]
        return min(codes, default=0)

    def as_dict(self, *, verbose: bool = False) -> dict[str, Any]:
        counts = Counter(check.status.value for check in self.checks)
        fingerprint_input = "\0".join(
            value or "unknown"
            for value in (self.facts.os_id, self.facts.os_version_id, self.facts.architecture)
        )
        fingerprint = hashlib.sha256(fingerprint_input.encode("utf-8")).hexdigest()[:16]
        return redact_report({
            "schema_version": 1,
            "timestamp": self.generated_at,
            "project_release": __version__,
            "lifecycle_mode": self.lifecycle_mode.value,
            "command_mode": "read-only",
            "dry_run": self.dry_run,
            "host": {
                "platform_fingerprint": fingerprint,
                "os_id": self.facts.os_id,
                "os_version_id": self.facts.os_version_id,
                "architecture": self.facts.architecture,
            },
            "summary": {
                "pass": counts[CheckStatus.PASS.value],
                "warn": counts[CheckStatus.WARN.value],
                "fail": counts[CheckStatus.FAIL.value],
                "unknown": counts[CheckStatus.UNKNOWN.value],
                "skipped": counts[CheckStatus.SKIPPED.value],
            },
            "checks": [check.as_dict(verbose=verbose) for check in self.checks],
            "changes": [],
        })


_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f-\x9f]")
_SENSITIVE_KEY_RE = re.compile(
    r"(?:password|secret|token|credential|authorization|cookie|api[_-]?key|access[_-]?key|"
    r"private[_-]?key|signature)",
    re.I,
)
_PRIVATE_PATH_RE = re.compile(r"/(?:home|Users)/[^\s]+")
_INLINE_SECRET_RE = re.compile(
    r"\b(authorization|password|secret|token|api[_-]?key|access[_-]?key|signature|"
    r"x-amz-(?:signature|credential|security-token))\s*[:=]\s*"
    r"(?:\"(?:\\.|[^\"\\])*\"|'(?:\\.|[^'\\])*'|"
    r"(?:(?:basic|bearer)\s+)?[^\r\n,;&]*)",
    re.I,
)
_COOKIE_HEADER_RE = re.compile(r"(?:cookie|set-cookie)\s*[:=][^\r\n]*", re.I)
_PRIVATE_KEY_RE = re.compile(
    r"-----BEGIN [^-]*PRIVATE KEY-----.*?-----END [^-]*PRIVATE KEY-----",
    re.I,
)


def safe_text(value: str | None) -> str:
    """Keep untrusted fact text on one terminal line."""

    if value is None:
        return "unknown"
    return _CONTROL_RE.sub("?", value)


def _decode_percent(value: str) -> str:
    decoded = value
    for _ in range(2):
        next_value = unquote(decoded)
        if next_value == decoded:
            break
        decoded = next_value
    return decoded


def safe_report_text(value: str | None) -> str:
    text = _decode_percent(value or "unknown")
    text = _COOKIE_HEADER_RE.sub("Cookie: [REDACTED]", text)
    text = safe_text(text)
    text = _PRIVATE_PATH_RE.sub("<redacted-path>", text)
    text = _PRIVATE_KEY_RE.sub("[REDACTED PRIVATE KEY]", text)
    return _INLINE_SECRET_RE.sub(lambda match: f"{match.group(1)}=[REDACTED]", text)


def redact_report(value: Any, *, key: str = "") -> Any:
    """Apply a recursive safety guard before report data reaches a renderer."""

    if _SENSITIVE_KEY_RE.search(_decode_percent(key)):
        return "[REDACTED]"
    if isinstance(value, dict):
        return {item_key: redact_report(item, key=str(item_key)) for item_key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [redact_report(item, key=key) for item in value]
    if isinstance(value, str):
        return safe_report_text(value)
    return value


def _read_text(path: Path, maximum_bytes: int = 65_536) -> str | None:
    try:
        with path.open("rb") as stream:
            raw = stream.read(maximum_bytes + 1)
    except OSError:
        return None
    if len(raw) > maximum_bytes:
        return None
    try:
        return raw.decode("utf-8").strip()
    except UnicodeDecodeError:
        return None


def _parse_os_release(text: str | None) -> dict[str, str]:
    if text is None:
        return {}
    result: dict[str, str] = {}
    for line in text.splitlines():
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        if not re.fullmatch(r"[A-Z][A-Z0-9_]*", key):
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        if key in result:
            return {}
        result[key] = safe_text(value)
    return result


def _parse_cpu_virtualization(cpuinfo: str | None) -> str | None:
    if cpuinfo is None:
        return None
    flags_found = False
    virtualization: set[str] = set()
    for line in cpuinfo.splitlines():
        if ":" not in line:
            continue
        field, value = line.split(":", 1)
        if field.strip().lower() not in {"flags", "features"}:
            continue
        flags_found = True
        flags = set(re.findall(r"[A-Za-z0-9_]+", value.lower()))
        virtualization.update(flags & {"vmx", "svm"})
    if "vmx" in virtualization:
        return "vmx"
    if "svm" in virtualization:
        return "svm"
    return "none" if flags_found else None


def _parse_nested_guest_support(value: str | None) -> bool | None:
    if value is None:
        return None
    normalized = value.strip().lower()
    if normalized in {"1", "y", "yes", "true"}:
        return True
    if normalized in {"0", "n", "no", "false"}:
        return False
    return None


def _stat_path(path: Path) -> os.stat_result | None:
    try:
        return path.stat()
    except OSError:
        return None


def collect_host_facts(
    *,
    read_text: Callable[[Path, int], str | None] = _read_text,
    machine: Callable[[], str] = platform.machine,
    access: Callable[[Path, int], bool] = os.access,
    stat_path: Callable[[Path], os.stat_result | None] = _stat_path,
) -> HostFacts:
    """Collect bounded facts from files and stdlib APIs without subprocesses or mutation."""

    os_release = _parse_os_release(read_text(Path("/etc/os-release"), 65_536))
    architecture = safe_text(machine()) or None
    pid1 = read_text(Path("/proc/1/comm"), 4_096)
    cgroup_root = Path("/sys/fs/cgroup")
    controllers_path = cgroup_root / "cgroup.controllers"
    cgroup_controllers = read_text(controllers_path, 65_536)
    if not cgroup_root.exists():
        cgroup_v2: bool | None = None
    elif controllers_path.exists() and cgroup_controllers is None:
        cgroup_v2 = None
    else:
        cgroup_v2 = cgroup_controllers is not None

    cpu_virtualization = _parse_cpu_virtualization(
        read_text(Path("/proc/cpuinfo"), 8_388_608)
    )

    nested_text = read_text(Path("/sys/module/kvm_intel/parameters/nested"), 4_096)
    if nested_text is None:
        nested_text = read_text(Path("/sys/module/kvm_amd/parameters/nested"), 4_096)
    nested_guest_support = _parse_nested_guest_support(nested_text)

    kvm_path = Path("/dev/kvm")
    kvm_stat = stat_path(kvm_path)
    kvm_exists = kvm_stat is not None
    return HostFacts(
        os_id=os_release.get("ID"),
        os_name=os_release.get("NAME"),
        os_version_id=os_release.get("VERSION_ID"),
        architecture=architecture or None,
        pid1_comm=safe_text(pid1) if pid1 is not None else None,
        cgroup_v2=cgroup_v2,
        cpu_virtualization=cpu_virtualization,
        kvm_device_exists=kvm_exists,
        kvm_device_is_character=kvm_stat is not None and stat.S_ISCHR(kvm_stat.st_mode),
        kvm_device_accessible=kvm_exists and access(kvm_path, os.R_OK | os.W_OK),
        nested_guest_support=nested_guest_support,
        evidence={
            "os_release": "/etc/os-release",
            "pid1": "/proc/1/comm",
            "cgroups": "/sys/fs/cgroup/cgroup.controllers",
            "cpu": "/proc/cpuinfo",
            "kvm": "/dev/kvm",
        },
    )


def _check(
    check_id: str,
    status: CheckStatus,
    explanation: str,
    *,
    remediation: str | None = None,
    installer_can_remediate: bool = False,
    failure_category: FailureCategory | None = None,
    evidence: str | None = None,
) -> CheckResult:
    if status is CheckStatus.FAIL or status is CheckStatus.UNKNOWN:
        severity = Severity.BLOCKING
    elif status is CheckStatus.WARN:
        severity = Severity.WARNING
    else:
        severity = Severity.INFO
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
        evidence,
    )


def evaluate_host(facts: HostFacts, lifecycle_mode: LifecycleMode) -> tuple[CheckResult, ...]:
    """Evaluate required first-slice platform facts independently of collection."""

    checks: list[CheckResult] = []
    os_evidence = f"ID={safe_text(facts.os_id)} VERSION_ID={safe_text(facts.os_version_id)}"
    if facts.os_id is None or facts.os_version_id is None:
        checks.append(
            _check(
                "platform.release_lifecycle",
                CheckStatus.UNKNOWN,
                "The operating-system release could not be identified.",
                remediation="Provide a readable /etc/os-release on the target host.",
                evidence=os_evidence,
            )
        )
    elif facts.os_id != "ubuntu" or facts.os_version_id not in {"25.04", "26.04"}:
        checks.append(
            _check(
                "platform.release_lifecycle",
                CheckStatus.FAIL,
                "Only Ubuntu 26.04, or Ubuntu 25.04 in development/migration mode, is supported.",
                remediation="Use a supported Ubuntu release and lifecycle mode.",
                evidence=os_evidence,
            )
        )
    elif facts.os_version_id == "25.04" and lifecycle_mode is LifecycleMode.PRODUCTION:
        checks.append(
            _check(
                "platform.release_lifecycle",
                CheckStatus.FAIL,
                "Ubuntu 25.04 is end-of-life and cannot be used in production mode.",
                remediation="Install Ubuntu 26.04 or select development/migration mode.",
                evidence=os_evidence,
            )
        )
    elif facts.os_version_id == "25.04":
        checks.append(
            _check(
                "platform.release_lifecycle",
                CheckStatus.WARN,
                "Ubuntu 25.04 is end-of-life and is eligible only for development or migration.",
                remediation="Move production workloads to Ubuntu 26.04.",
                evidence=os_evidence,
            )
        )
    else:
        checks.append(
            _check(
                "platform.release_lifecycle",
                CheckStatus.PASS,
                "Ubuntu 26.04 is eligible for the selected lifecycle mode.",
                evidence=os_evidence,
            )
        )

    if facts.architecture is None:
        checks.append(_check("platform.architecture", CheckStatus.UNKNOWN, "Architecture is unknown."))
    elif facts.architecture != "x86_64":
        checks.append(
            _check(
                "platform.architecture",
                CheckStatus.FAIL,
                f"Architecture {safe_text(facts.architecture)} is unsupported; x86_64 is required.",
            )
        )
    else:
        checks.append(_check("platform.architecture", CheckStatus.PASS, "Architecture is x86_64."))

    if facts.pid1_comm is None:
        checks.append(_check("platform.systemd", CheckStatus.UNKNOWN, "PID 1 could not be identified."))
    elif facts.pid1_comm != "systemd":
        checks.append(
            _check("platform.systemd", CheckStatus.FAIL, "PID 1 is not systemd.", evidence=facts.pid1_comm)
        )
    else:
        checks.append(_check("platform.systemd", CheckStatus.PASS, "PID 1 is systemd."))

    if facts.cgroup_v2 is None:
        checks.append(_check("platform.cgroups_v2", CheckStatus.UNKNOWN, "Cgroup mode is unknown."))
    elif not facts.cgroup_v2:
        checks.append(_check("platform.cgroups_v2", CheckStatus.FAIL, "Cgroups v2 is not active."))
    else:
        checks.append(_check("platform.cgroups_v2", CheckStatus.PASS, "Cgroups v2 is active."))

    if facts.cpu_virtualization is None:
        checks.append(
            _check("virtualization.cpu", CheckStatus.UNKNOWN, "CPU virtualization flags are unknown.")
        )
    elif facts.cpu_virtualization == "none":
        checks.append(
            _check("virtualization.cpu", CheckStatus.FAIL, "Neither Intel VMX nor AMD SVM is exposed.")
        )
    else:
        checks.append(
            _check(
                "virtualization.cpu",
                CheckStatus.PASS,
                f"CPU exposes {safe_text(facts.cpu_virtualization).upper()} virtualization.",
            )
        )

    if not facts.kvm_device_exists:
        checks.append(
            _check(
                "virtualization.kvm_device",
                CheckStatus.FAIL,
                "/dev/kvm is absent; doctor does not load KVM modules.",
                remediation="Enable KVM in firmware and prepare the host outside read-only doctor.",
            )
        )
    elif not facts.kvm_device_is_character:
        checks.append(
            _check(
                "virtualization.kvm_device",
                CheckStatus.FAIL,
                "/dev/kvm exists but is not a character device.",
                remediation="Remove the conflicting path and expose the kernel KVM device.",
            )
        )
    elif not facts.kvm_device_accessible:
        checks.append(
            _check(
                "virtualization.kvm_device",
                CheckStatus.WARN,
                "/dev/kvm exists but is not accessible to the current identity.",
                remediation="Grant the future worker identity explicit KVM access during approved preparation.",
                installer_can_remediate=True,
            )
        )
    else:
        checks.append(_check("virtualization.kvm_device", CheckStatus.PASS, "/dev/kvm is accessible."))

    if facts.nested_guest_support is True:
        checks.append(
            _check(
                "virtualization.nested_guest_support",
                CheckStatus.PASS,
                "The KVM module allows nested guests; this does not identify the host as nested.",
            )
        )
    elif facts.nested_guest_support is False:
        checks.append(
            _check(
                "virtualization.nested_guest_support",
                CheckStatus.SKIPPED,
                "Nested-guest support is disabled and is not required for bare-metal operation.",
            )
        )
    else:
        checks.append(
            _check(
                "virtualization.nested_guest_support",
                CheckStatus.SKIPPED,
                "The KVM nested-guest parameter was absent or had an unrecognized value.",
            )
        )

    checks.extend(
        [
            _check(
                "scope.kernel_facilities",
                CheckStatus.UNKNOWN,
                "NBD, huge pages, TUN/TAP, namespaces, and mount facilities are not yet collected.",
            ),
            _check(
                "scope.capacity",
                CheckStatus.UNKNOWN,
                "Profile capacity and filesystem headroom checks are not yet implemented.",
            ),
            _check(
                "scope.services",
                CheckStatus.UNKNOWN,
                "Host package, service, and partial-install checks are not yet implemented.",
            ),
            _check(
                "scope.network_conflicts",
                CheckStatus.UNKNOWN,
                "Listener, route, subnet, DNS, and firewall conflict checks are not yet implemented.",
            ),
            _check(
                "scope.security_posture",
                CheckStatus.UNKNOWN,
                "The required host security-posture checks are not yet implemented.",
            ),
            _check(
                "scope.firecracker_probe",
                CheckStatus.SKIPPED,
                "Booting a Firecracker microVM is intentionally excluded from read-only doctor.",
            ),
        ]
    )
    return tuple(checks)


def build_doctor_report(
    facts: HostFacts,
    lifecycle_mode: LifecycleMode,
    *,
    dry_run: bool = False,
    now: datetime | None = None,
) -> DoctorReport:
    timestamp = (now or datetime.now(UTC)).astimezone(UTC).isoformat().replace("+00:00", "Z")
    return DoctorReport(timestamp, lifecycle_mode, dry_run, facts, evaluate_host(facts, lifecycle_mode))


def render_text(report: DoctorReport, *, verbose: bool = False) -> str:
    data = report.as_dict(verbose=verbose)
    host = data["host"]
    lines = [
        "kitdev doctor (read-only)",
        f"Lifecycle: {safe_text(report.lifecycle_mode.value)}",
        (
            "Host: "
            f"{safe_text(host['os_id'])} {safe_text(host['os_version_id'])} "
            f"({safe_text(host['architecture'])})"
        ),
        "",
    ]
    for check in report.checks:
        sanitized = redact_report(check.as_dict(verbose=True))
        lines.append(
            f"[{check.status.value.upper():7}] {safe_report_text(str(sanitized['id']))}: "
            f"{safe_report_text(str(sanitized['explanation']))}"
        )
        if sanitized["remediation"]:
            lines.append(f"          Remediation: {safe_report_text(str(sanitized['remediation']))}")
        if verbose and sanitized.get("evidence"):
            lines.append(f"          Evidence: {safe_report_text(str(sanitized['evidence']))}")
    summary = data["summary"]
    lines.extend(
        [
            "",
            (
                "Summary: "
                f"{summary['pass']} pass, {summary['warn']} warn, {summary['fail']} fail, "
                f"{summary['unknown']} unknown, {summary['skipped']} skipped"
            ),
            "Proposed changes: 0 (doctor never mutates the host)",
        ]
    )
    return "\n".join(lines)
