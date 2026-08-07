"""Fixed dispatch boundary for reviewed host lifecycle operations."""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from kitdev_sandboxes.config import Configuration


PROJECT_ROOT = Path(__file__).resolve().parents[2]
LIFECYCLE_SCRIPT = PROJECT_ROOT / "scripts" / "control-plane" / "lifecycle.sh"
OPERATIONS = frozenset(
    {"install", "up", "down", "restart", "status", "test-core", "test-sdk", "test-smoke"}
)


@dataclass(frozen=True)
class LifecycleResult:
    """Normalized result without child output or secret-bearing evidence."""

    operation: str
    exit_code: int
    health: tuple[tuple[str, str], ...] = ()

    def as_dict(self) -> dict[str, object]:
        result: dict[str, object] = {
            "schema_version": 1,
            "command": self.operation,
            "status": "pass" if self.exit_code == 0 else "fail",
            "exit_code": self.exit_code,
        }
        if self.health:
            result["health"] = dict(self.health)
        return result


class LifecycleRunner(Protocol):
    def __call__(
        self,
        operation: str,
        configuration: Configuration,
        *,
        quiet: bool,
        api_key_file: Path | None = None,
        template_id_file: Path | None = None,
    ) -> LifecycleResult: ...


def run_lifecycle(
    operation: str,
    configuration: Configuration,
    *,
    quiet: bool,
    api_key_file: Path | None = None,
    template_id_file: Path | None = None,
) -> LifecycleResult:
    """Execute one allowlisted lifecycle operation without a shell or caller environment."""

    if operation not in OPERATIONS:
        raise ValueError("unsupported lifecycle operation")
    if not LIFECYCLE_SCRIPT.is_file() or LIFECYCLE_SCRIPT.is_symlink():
        raise RuntimeError("lifecycle entrypoint is unavailable")

    environment = {
        "PATH": "/usr/sbin:/usr/bin:/sbin:/bin",
        "LANG": "C",
        "LANGUAGE": "C",
        "LC_ALL": "C",
        "TZ": "UTC",
        "HOME": "/root",
        "KITDEV_LIFECYCLE": configuration.deployment.lifecycle_mode.value,
        "KITDEV_PROFILE": configuration.deployment.profile.value,
    }
    if api_key_file is not None:
        if not api_key_file.is_absolute() or any(
            ord(character) < 32 or ord(character) == 127 for character in str(api_key_file)
        ):
            raise ValueError("API key file path must be an absolute control-free path")
        environment["KITDEV_E2E_API_KEY_FILE"] = str(api_key_file)
    if template_id_file is not None:
        if not template_id_file.is_absolute() or any(
            ord(character) < 32 or ord(character) == 127
            for character in str(template_id_file)
        ):
            raise ValueError("template ID file path must be an absolute control-free path")
        environment["KITDEV_E2E_TEMPLATE_ID_FILE"] = str(template_id_file)
    capture_status = operation == "status"
    destination = subprocess.PIPE if capture_status else subprocess.DEVNULL if quiet else None
    completed = subprocess.run(  # noqa: S603 - exact reviewed script and argv.
        ("/usr/bin/bash", str(LIFECYCLE_SCRIPT), operation),
        cwd=PROJECT_ROOT,
        env=environment,
        stdin=subprocess.DEVNULL,
        stdout=destination,
        stderr=subprocess.DEVNULL if capture_status or quiet else None,
        check=False,
        text=capture_status,
    )
    code = completed.returncode
    if code < 0:
        code = 128 + abs(code)
    health: tuple[tuple[str, str], ...] = ()
    if capture_status:
        output = completed.stdout
        if not isinstance(output, str) or len(output.encode("utf-8")) > 4_096:
            return LifecycleResult(operation=operation, exit_code=70)
        fields: dict[str, str] = {}
        for token in output.strip().split(" "):
            if "=" not in token:
                return LifecycleResult(operation=operation, exit_code=70)
            key, value = token.split("=", 1)
            if not key or not value or key in fields or not key.replace("_", "").isalnum():
                return LifecycleResult(operation=operation, exit_code=70)
            fields[key] = value
        expected = {"status", "orchestrator", "compose", "api", "proxy", "firecrackers"}
        if (
            set(fields) != expected
            or fields["status"] not in {"pass", "degraded"}
            or fields["orchestrator"] not in {"active", "inactive"}
            or fields["compose"] not in {"running", "stopped"}
            or fields["api"] not in {"healthy", "unreachable"}
            or fields["proxy"] not in {"healthy", "unreachable"}
            or not fields["firecrackers"].isdigit()
            or len(fields["firecrackers"]) > 4
        ):
            return LifecycleResult(operation=operation, exit_code=70)
        health = tuple((key, fields[key]) for key in sorted(expected - {"status"}))
        if not quiet:
            print(output, end="" if output.endswith("\n") else "\n")
    return LifecycleResult(operation=operation, exit_code=min(code, 255), health=health)
