"""Pure, deterministic host-change planning primitives.

The planner consumes validated configuration and normalized observations.  It
does not collect facts, inspect the host, acquire locks, or apply changes.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from enum import StrEnum
from pathlib import PurePosixPath
from typing import Any
from urllib.parse import unquote

from kitdev_sandboxes.config import Configuration, LifecycleMode


class ActionCategory(StrEnum):
    """Stable categories used by dry-run and installation manifests."""

    PACKAGE = "package"
    ACCOUNT = "account"
    DIRECTORY = "directory"
    MANAGED_FILE = "managed_file"
    SHARED_FILE_MERGE = "shared_file_merge"
    KERNEL_MODULE = "kernel_module"
    SYSCTL = "sysctl"
    NETWORK_FIREWALL = "network_firewall"
    SERVICE = "service"
    COMPOSE = "compose"
    ARTIFACT = "artifact"
    TEMPLATE = "template"
    VALIDATION = "validation"


class ObservedState(StrEnum):
    """Normalized current state; raw collector output does not enter a plan."""

    ABSENT = "absent"
    PRESENT = "present"
    UNKNOWN = "unknown"
    UNSUPPORTED = "unsupported"


class Ownership(StrEnum):
    """Observed or intended resource ownership."""

    PROJECT = "project"
    FOREIGN = "foreign"
    SHARED = "shared"
    UNOWNED = "unowned"
    UNKNOWN = "unknown"


class Confidence(StrEnum):
    UNKNOWN = "unknown"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class Privilege(StrEnum):
    UNPRIVILEGED = "unprivileged"
    ROOT = "root"


class RestartImpact(StrEnum):
    NONE = "none"
    SERVICE = "service"
    COMPOSE = "compose"
    HOST = "host"


class RollbackStrategy(StrEnum):
    REMOVE_CREATED = "remove_created"
    RESTORE_BACKUP = "restore_backup"
    REVERT_VALUE = "revert_value"
    DISABLE = "disable"
    MANUAL = "manual"
    NOT_APPLICABLE = "not_applicable"


class IssueCode(StrEnum):
    CONFLICT = "conflict"
    INVALID_FACT = "invalid_fact"
    LIFECYCLE = "lifecycle"
    UNKNOWN = "unknown"
    UNSUPPORTED = "unsupported"


class IssueSeverity(StrEnum):
    WARNING = "warning"
    BLOCKING = "blocking"


_STABLE_ID_RE = re.compile(r"^[a-z][a-z0-9_.-]*$")
_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f-\x9f]")
_SECRET_ASSIGNMENT_RE = re.compile(
    r"(?:authorization|cookie|password|secret|token|api[_-]?key|access[_-]?key|credential|"
    r"x-amz-(?:signature|credential|security-token))"
    r"\s*[:=]",
    re.IGNORECASE,
)
_MAX_ID_BYTES = 128
_MAX_TARGET_BYTES = 4_096
_MAX_TEXT_BYTES = 2_048
_MAX_RESOURCE_FACTS = 1_024
_MAX_PLAN_ACTIONS = 256
_MAX_PLAN_ISSUES = 256
_MAX_PLAN_JSON_BYTES = 1_048_576
_OWNED_DIRECTORY_ROOTS = {
    "directory.config": PurePosixPath("/etc/kitdev-sandboxes"),
    "directory.install": PurePosixPath("/opt/kitdev-sandboxes"),
    "directory.logs": PurePosixPath("/var/log/kitdev-sandboxes"),
    "directory.runtime": PurePosixPath("/run/kitdev-sandboxes"),
    "directory.state": PurePosixPath("/var/lib/kitdev-sandboxes"),
}
_OWNED_PATH_PART_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


def _require_enum(name: str, value: object, enum_type: type[StrEnum]) -> None:
    if not isinstance(value, enum_type):
        raise TypeError(f"{name} must be a {enum_type.__name__} instance")


def _require_bool(name: str, value: object) -> None:
    if type(value) is not bool:
        raise TypeError(f"{name} must be a bool")


def _require_schema_version(name: str, value: object) -> None:
    if type(value) is not int:
        raise TypeError(f"{name} must be an integer")
    if value != 1:
        raise ValueError(f"{name} must be 1")


def _require_text(
    name: str,
    value: object,
    *,
    maximum_bytes: int,
    stable_id: bool = False,
) -> None:
    if not isinstance(value, str):
        raise TypeError(f"{name} must be a string")
    if not value:
        raise ValueError(f"{name} must not be empty")
    if len(value.encode("utf-8")) > maximum_bytes:
        raise ValueError(f"{name} exceeds its UTF-8 byte limit")
    decoded = value
    for _ in range(2):
        next_value = unquote(decoded)
        if next_value == decoded:
            break
        decoded = next_value
    if _CONTROL_RE.search(decoded):
        raise ValueError(f"{name} contains terminal control characters")
    if _SECRET_ASSIGNMENT_RE.search(decoded) or "-----BEGIN " in decoded.upper():
        raise ValueError(f"{name} contains secret-shaped content")
    if stable_id and not _STABLE_ID_RE.fullmatch(value):
        raise ValueError(f"{name} must be a stable lowercase identifier")


def _require_tuple(name: str, value: object, item_type: type[object]) -> None:
    if not isinstance(value, tuple):
        raise TypeError(f"{name} must be a tuple")
    if not all(isinstance(item, item_type) for item in value):
        raise TypeError(f"every {name} item must be a {item_type.__name__}")


def _require_owned_directory_path(resource_id: str, value: str) -> None:
    _require_text("configured path", value, maximum_bytes=_MAX_TARGET_BYTES)
    path = PurePosixPath(value)
    root = _OWNED_DIRECTORY_ROOTS[resource_id]
    if not path.is_absolute() or str(path) != value:
        raise ValueError("configured path must be normalized and absolute")
    try:
        relative = path.relative_to(root)
    except ValueError as error:
        raise ValueError("configured path is outside its project-owned root") from error
    if any(
        part in {"", ".", ".."} or not _OWNED_PATH_PART_RE.fullmatch(part)
        for part in relative.parts
    ):
        raise ValueError("configured path contains an unsafe path component")


@dataclass(frozen=True)
class ResourceFact:
    """A secret-free, normalized observation for one exact resource ID."""

    resource_id: str
    target: str
    state: ObservedState
    ownership: Ownership
    confidence: Confidence

    def __post_init__(self) -> None:
        _require_text(
            "resource_id", self.resource_id, maximum_bytes=_MAX_ID_BYTES, stable_id=True
        )
        _require_text("target", self.target, maximum_bytes=_MAX_TARGET_BYTES)
        _require_enum("state", self.state, ObservedState)
        _require_enum("ownership", self.ownership, Ownership)
        _require_enum("confidence", self.confidence, Confidence)


@dataclass(frozen=True)
class PlanningFacts:
    """Normalized facts supplied by read-only collection and evaluation."""

    os_id: str | None
    os_version_id: str | None
    resources: tuple[ResourceFact, ...]
    schema_version: int = 1

    def __post_init__(self) -> None:
        _require_schema_version("planning facts schema_version", self.schema_version)
        if self.os_id is not None:
            _require_text("os_id", self.os_id, maximum_bytes=64)
        if self.os_version_id is not None:
            _require_text("os_version_id", self.os_version_id, maximum_bytes=64)
        _require_tuple("resources", self.resources, ResourceFact)
        if len(self.resources) > _MAX_RESOURCE_FACTS:
            raise ValueError("resources exceeds the global planning fact limit")


@dataclass(frozen=True)
class Rollback:
    strategy: RollbackStrategy
    description: str

    def __post_init__(self) -> None:
        _require_enum("strategy", self.strategy, RollbackStrategy)
        _require_text("description", self.description, maximum_bytes=_MAX_TEXT_BYTES)

    def as_dict(self) -> dict[str, str]:
        return {"strategy": self.strategy.value, "description": self.description}


@dataclass(frozen=True)
class PlannedAction:
    action_id: str
    category: ActionCategory
    target: str
    current_state: str
    desired_state: str
    reason: str
    privilege: Privilege
    restart_impact: RestartImpact
    reboot_required: bool
    rollback: Rollback
    confidence: Confidence
    ownership: Ownership

    def __post_init__(self) -> None:
        _require_text(
            "action_id", self.action_id, maximum_bytes=_MAX_ID_BYTES, stable_id=True
        )
        _require_enum("category", self.category, ActionCategory)
        _require_text("target", self.target, maximum_bytes=_MAX_TARGET_BYTES)
        _require_text("current_state", self.current_state, maximum_bytes=_MAX_TEXT_BYTES)
        _require_text("desired_state", self.desired_state, maximum_bytes=_MAX_TEXT_BYTES)
        _require_text("reason", self.reason, maximum_bytes=_MAX_TEXT_BYTES)
        _require_enum("privilege", self.privilege, Privilege)
        _require_enum("restart_impact", self.restart_impact, RestartImpact)
        _require_bool("reboot_required", self.reboot_required)
        if not isinstance(self.rollback, Rollback):
            raise TypeError("rollback must be a Rollback instance")
        _require_enum("confidence", self.confidence, Confidence)
        _require_enum("ownership", self.ownership, Ownership)

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.action_id,
            "category": self.category.value,
            "target": self.target,
            "current_state": self.current_state,
            "desired_state": self.desired_state,
            "reason": self.reason,
            "privilege": self.privilege.value,
            "restart_impact": self.restart_impact.value,
            "reboot_required": self.reboot_required,
            "rollback": self.rollback.as_dict(),
            "confidence": self.confidence.value,
            "ownership": self.ownership.value,
        }


@dataclass(frozen=True)
class PlanIssue:
    issue_id: str
    code: IssueCode
    severity: IssueSeverity
    fact_id: str
    explanation: str
    remediation: str

    def __post_init__(self) -> None:
        _require_text(
            "issue_id", self.issue_id, maximum_bytes=_MAX_ID_BYTES, stable_id=True
        )
        _require_enum("code", self.code, IssueCode)
        _require_enum("severity", self.severity, IssueSeverity)
        _require_text("fact_id", self.fact_id, maximum_bytes=_MAX_ID_BYTES, stable_id=True)
        _require_text("explanation", self.explanation, maximum_bytes=_MAX_TEXT_BYTES)
        _require_text("remediation", self.remediation, maximum_bytes=_MAX_TEXT_BYTES)

    @property
    def blocking(self) -> bool:
        return self.severity is IssueSeverity.BLOCKING

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.issue_id,
            "code": self.code.value,
            "severity": self.severity.value,
            "blocking": self.blocking,
            "fact_id": self.fact_id,
            "explanation": self.explanation,
            "remediation": self.remediation,
        }


@dataclass(frozen=True)
class ChangePlan:
    lifecycle_mode: LifecycleMode
    actions: tuple[PlannedAction, ...]
    issues: tuple[PlanIssue, ...]
    schema_version: int = 1

    def __post_init__(self) -> None:
        _require_enum("lifecycle_mode", self.lifecycle_mode, LifecycleMode)
        _require_tuple("actions", self.actions, PlannedAction)
        _require_tuple("issues", self.issues, PlanIssue)
        _require_schema_version("change plan schema_version", self.schema_version)
        if len(self.actions) > _MAX_PLAN_ACTIONS:
            raise ValueError("actions exceeds the global plan output limit")
        if len(self.issues) > _MAX_PLAN_ISSUES:
            raise ValueError("issues exceeds the global plan output limit")

    @property
    def blocking(self) -> bool:
        return any(issue.blocking for issue in self.issues)

    def as_dict(self) -> dict[str, Any]:
        blocking_issues = sum(issue.blocking for issue in self.issues)
        return {
            "schema_version": self.schema_version,
            "lifecycle_mode": self.lifecycle_mode.value,
            "blocking": self.blocking,
            "summary": {
                "actions": len(self.actions),
                "issues": len(self.issues),
                "blocking_issues": blocking_issues,
            },
            "actions": [action.as_dict() for action in self.actions],
            "issues": [issue.as_dict() for issue in self.issues],
        }

    def to_json(self) -> str:
        """Serialize without timestamps, host evidence, or environment-dependent data."""

        rendered = json.dumps(self.as_dict(), sort_keys=True, separators=(",", ":"))
        if len(rendered.encode("utf-8")) > _MAX_PLAN_JSON_BYTES:
            raise ValueError("serialized plan exceeds the global UTF-8 byte limit")
        return rendered


@dataclass(frozen=True)
class _DesiredDirectory:
    resource_id: str
    path: str
    purpose: str


def _issue(
    issue_id: str,
    code: IssueCode,
    fact_id: str,
    explanation: str,
    remediation: str,
    *,
    severity: IssueSeverity = IssueSeverity.BLOCKING,
) -> PlanIssue:
    return PlanIssue(issue_id, code, severity, fact_id, explanation, remediation)


def _lifecycle_issues(config: Configuration, facts: PlanningFacts) -> list[PlanIssue]:
    release = facts.os_version_id
    if facts.os_id is None or release is None:
        return [
            _issue(
                "platform.release.unknown",
                IssueCode.UNKNOWN,
                "platform.release",
                "The operating-system release is unknown, so no host changes can be planned.",
                "Collect a normalized Ubuntu release fact and repeat the dry-run.",
            )
        ]
    if facts.os_id != "ubuntu" or release not in {"25.04", "26.04"}:
        return [
            _issue(
                "platform.release.unsupported",
                IssueCode.UNSUPPORTED,
                "platform.release",
                "The operating-system release is outside the supported host matrix.",
                "Use Ubuntu 26.04, or Ubuntu 25.04 only for development or migration.",
            )
        ]
    if release == "25.04" and config.deployment.lifecycle_mode.value == "production":
        return [
            _issue(
                "platform.release.ubuntu_25_04.production",
                IssueCode.LIFECYCLE,
                "platform.release",
                "Ubuntu 25.04 is end-of-life and cannot receive a production change plan.",
                "Use Ubuntu 26.04 for production or explicitly select development/migration.",
            )
        ]
    if release == "25.04":
        return [
            _issue(
                "platform.release.ubuntu_25_04.eol",
                IssueCode.LIFECYCLE,
                "platform.release",
                "Ubuntu 25.04 is end-of-life and is eligible only for development or migration.",
                "Migrate the deployment to Ubuntu 26.04 before production use.",
                severity=IssueSeverity.WARNING,
            )
        ]
    return []


def _desired_directories(config: Configuration) -> tuple[_DesiredDirectory, ...]:
    paths = config.paths
    desired = (
        _DesiredDirectory("directory.config", paths.config, "project configuration"),
        _DesiredDirectory("directory.install", paths.install, "installed releases and tooling"),
        _DesiredDirectory("directory.logs", paths.logs, "project logs"),
        _DesiredDirectory("directory.runtime", paths.runtime, "transient runtime state"),
        _DesiredDirectory("directory.state", paths.state, "durable project state"),
    )
    for item in desired:
        _require_owned_directory_path(item.resource_id, item.path)
    return tuple(sorted(desired, key=lambda item: item.resource_id))


def _index_facts(
    facts: PlanningFacts, desired_ids: frozenset[str]
) -> tuple[dict[str, ResourceFact], list[PlanIssue]]:
    indexed: dict[str, ResourceFact] = {}
    duplicates: set[str] = set()
    for fact in facts.resources:
        if fact.resource_id not in desired_ids:
            continue
        if fact.resource_id in indexed:
            duplicates.add(fact.resource_id)
        else:
            indexed[fact.resource_id] = fact
    for resource_id in duplicates:
        indexed.pop(resource_id, None)
    issues = []
    if duplicates:
        issues.append(
            _issue(
                "facts.duplicate.desired_resources",
                IssueCode.INVALID_FACT,
                "planning.resources",
                "The normalized fact set contains duplicate desired-resource observations.",
                "Collect exactly one authoritative observation for each resource ID.",
            )
        )
    return indexed, issues


def _directory_result(
    desired: _DesiredDirectory, fact: ResourceFact | None
) -> tuple[PlannedAction | None, PlanIssue | None]:
    if fact is None:
        return None, _issue(
            f"facts.missing.{desired.resource_id}",
            IssueCode.UNKNOWN,
            desired.resource_id,
            "No normalized observation exists for the configured project directory.",
            "Collect the exact path type, existence, ownership, and confidence.",
        )
    if fact.target != desired.path:
        return None, _issue(
            f"facts.target_mismatch.{desired.resource_id}",
            IssueCode.INVALID_FACT,
            desired.resource_id,
            "The normalized observation does not describe the configured resource target.",
            "Collect a new observation for the exact configured path.",
        )
    if fact.state is ObservedState.UNKNOWN:
        return None, _issue(
            f"facts.unknown.{desired.resource_id}",
            IssueCode.UNKNOWN,
            desired.resource_id,
            "The configured directory state is unknown, so creating or adopting it is unsafe.",
            "Resolve the read-only collection failure and repeat the dry-run.",
        )
    if fact.state is ObservedState.UNSUPPORTED:
        return None, _issue(
            f"facts.unsupported.{desired.resource_id}",
            IssueCode.UNSUPPORTED,
            desired.resource_id,
            "The configured path cannot be managed as a project directory.",
            "Choose a supported project-owned path or remove the conflicting filesystem object.",
        )
    if fact.confidence is not Confidence.HIGH:
        return None, _issue(
            f"facts.confidence.{desired.resource_id}",
            IssueCode.UNKNOWN,
            desired.resource_id,
            "Directory ownership or existence was not established with high confidence.",
            "Resolve symlinks, mount boundaries, and ownership markers before planning changes.",
        )
    if fact.state is ObservedState.PRESENT:
        if fact.ownership is Ownership.PROJECT:
            return None, None
        return None, _issue(
            f"ownership.conflict.{desired.resource_id}",
            IssueCode.CONFLICT,
            desired.resource_id,
            "The configured path already exists without exact project ownership.",
            "Select a different project path or explicitly resolve the foreign resource.",
        )
    if fact.ownership is not Ownership.UNOWNED:
        return None, _issue(
            f"facts.inconsistent.{desired.resource_id}",
            IssueCode.INVALID_FACT,
            desired.resource_id,
            "An absent directory has a contradictory ownership classification.",
            "Correct the normalized observation before calculating a plan.",
        )
    return (
        PlannedAction(
            action_id=f"{desired.resource_id}.create",
            category=ActionCategory.DIRECTORY,
            target=desired.path,
            current_state=ObservedState.ABSENT.value,
            desired_state=ObservedState.PRESENT.value,
            reason=f"Create the dedicated directory for {desired.purpose}.",
            privilege=Privilege.ROOT,
            restart_impact=RestartImpact.NONE,
            reboot_required=False,
            rollback=Rollback(
                RollbackStrategy.REMOVE_CREATED,
                "Remove only when exact manifest ownership is proven and the directory is empty.",
            ),
            confidence=Confidence.HIGH,
            ownership=Ownership.PROJECT,
        ),
        None,
    )


def build_change_plan(config: Configuration, facts: PlanningFacts) -> ChangePlan:
    """Build a deterministic, zero-mutation plan from typed inputs only.

    This foundation plans only the configured project directory roots.  Other
    action categories are part of the stable model but require later approved
    desired-state contracts.  A blocking issue suppresses every candidate
    action so consumers cannot accidentally apply a partial speculative plan.
    """

    if not isinstance(config, Configuration):
        raise TypeError("config must be a Configuration instance")
    if not isinstance(facts, PlanningFacts):
        raise TypeError("facts must be a PlanningFacts instance")
    if not isinstance(config.deployment.lifecycle_mode, LifecycleMode):
        raise TypeError("configuration lifecycle_mode must be a LifecycleMode instance")

    desired_directories = _desired_directories(config)
    desired_ids = frozenset(item.resource_id for item in desired_directories)
    issues = _lifecycle_issues(config, facts)
    indexed, fact_issues = _index_facts(facts, desired_ids)
    issues.extend(fact_issues)
    candidate_actions: list[PlannedAction] = []

    for desired in desired_directories:
        action, issue = _directory_result(desired, indexed.get(desired.resource_id))
        if action is not None:
            candidate_actions.append(action)
        if issue is not None:
            issues.append(issue)

    sorted_issues = tuple(sorted(issues, key=lambda issue: issue.issue_id))
    if any(issue.blocking for issue in sorted_issues):
        actions: tuple[PlannedAction, ...] = ()
    else:
        actions = tuple(sorted(candidate_actions, key=lambda action: action.action_id))
    return ChangePlan(config.deployment.lifecycle_mode, actions, sorted_issues)
