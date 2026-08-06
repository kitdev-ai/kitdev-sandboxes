"""Command-line interface for kitdev-sandboxes."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Callable, Sequence

sys.dont_write_bytecode = True

from kitdev_sandboxes import __version__
from kitdev_sandboxes.collectors import LinuxFacts
from kitdev_sandboxes.composition import (
    InstallPlanReport,
    build_composed_doctor_report,
    build_install_plan_report,
    collect_configured_linux_facts,
    collect_directory_resource_facts,
    render_install_plan_text,
)
from kitdev_sandboxes.config import (
    Configuration,
    ConfigurationError,
    LifecycleMode,
    load_configuration,
)
from kitdev_sandboxes.planning import ResourceFact
from kitdev_sandboxes.identity import (
    IdentityFacts,
    IdentityPlan,
    IdentityPrerequisites,
    build_identity_plan,
    collect_identity_facts,
    render_identity_plan_text,
)
from kitdev_sandboxes.preflight import (
    DoctorReport,
    HostFacts,
    collect_host_facts,
    render_text,
    safe_report_text,
)


class InvocationError(ValueError):
    """Raised instead of letting argparse print an unstructured error."""


class ArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        raise InvocationError(message)


_MAX_ERROR_OUTPUT_BYTES = 4_096
_TRUNCATION_MARKER = "...[truncated]"


def _bounded_utf8(value: str, maximum_bytes: int) -> str:
    encoded = value.encode("utf-8")
    if len(encoded) <= maximum_bytes:
        return value
    marker = _TRUNCATION_MARKER.encode("utf-8")
    prefix = encoded[: maximum_bytes - len(marker)].decode("utf-8", errors="ignore")
    return prefix + _TRUNCATION_MARKER


def _emit_json_error(code: str, message: str) -> None:
    rendered = json.dumps(
        {"schema_version": 1, "error": {"code": code, "message": message}},
        sort_keys=True,
    )
    if len(rendered.encode("utf-8")) + 1 > _MAX_ERROR_OUTPUT_BYTES:
        raise ValueError("static JSON error envelope exceeds output bound")
    try:
        print(rendered)
    except BrokenPipeError:
        pass


def _emit_human_error(label: str, message: str | None = None) -> None:
    rendered = f"kitdev: {label}"
    if message is not None:
        rendered += f": {safe_report_text(message)}"
    rendered = _bounded_utf8(rendered, _MAX_ERROR_OUTPUT_BYTES - 1)
    try:
        print(rendered, file=sys.stderr)
    except BrokenPipeError:
        pass


def _add_common_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--config",
        type=Path,
        default=argparse.SUPPRESS,
        help="operator config file",
    )
    parser.add_argument(
        "--lifecycle-mode",
        choices=tuple(mode.value for mode in LifecycleMode),
        default=argparse.SUPPRESS,
        help="explicit deployment lifecycle intent",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        default=argparse.SUPPRESS,
        dest="json_output",
    )
    parser.add_argument("--verbose", action="store_true", default=argparse.SUPPRESS)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=argparse.SUPPRESS,
        help="calculate only; doctor is read-only regardless",
    )
    parser.add_argument(
        "--non-interactive",
        action="store_true",
        default=argparse.SUPPRESS,
        help="never prompt; doctor never prompts regardless",
    )


def build_parser() -> argparse.ArgumentParser:
    common = ArgumentParser(add_help=False)
    _add_common_arguments(common)
    parser = ArgumentParser(
        prog="kitdev",
        description="Single-host E2B-compatible sandbox deployment tooling",
        parents=[common],
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser(
        "doctor",
        parents=[common],
        help="run strictly read-only host qualification checks",
        description="Collect and evaluate host facts without mutation or privilege acquisition.",
    )
    install = commands.add_parser(
        "install",
        parents=[common],
        help="calculate an installation plan; apply is not implemented",
        description="Calculate a read-only plan. The --dry-run flag is mandatory.",
    )
    install.add_argument(
        "--phase",
        choices=("identity-access",),
        default=None,
        help="calculate one explicitly selected installation phase",
    )
    return parser


def _configuration_error(message: str, *, json_output: bool) -> None:
    if json_output:
        _emit_json_error(
            "invalid_configuration",
            "configuration could not be loaded or validated",
        )
    else:
        _emit_human_error("configuration error", message)


def _invocation_error(message: str, *, json_output: bool) -> None:
    if json_output:
        _emit_json_error("invalid_invocation", "invalid command invocation")
    else:
        _emit_human_error("invocation error", message)


def main(
    argv: Sequence[str] | None = None,
    *,
    fact_collector: Callable[[], HostFacts] = collect_host_facts,
    linux_fact_collector: Callable[[Configuration], LinuxFacts] | None = None,
    directory_fact_collector: Callable[[Configuration], tuple[ResourceFact, ...]] | None = None,
    identity_fact_collector: Callable[[Configuration], IdentityFacts] | None = None,
    identity_prerequisites: IdentityPrerequisites = IdentityPrerequisites(),
) -> int:
    parser = build_parser()
    raw_arguments = list(argv) if argv is not None else sys.argv[1:]
    json_requested = "--json" in raw_arguments
    report: DoctorReport | InstallPlanReport | IdentityPlan | None = None
    try:
        arguments = parser.parse_args(raw_arguments)
    except InvocationError as error:
        _invocation_error(str(error), json_output=json_requested)
        return 2
    json_output = bool(getattr(arguments, "json_output", False))
    if arguments.command == "install" and not bool(getattr(arguments, "dry_run", False)):
        _invocation_error(
            "install requires --dry-run; applying changes is not implemented",
            json_output=json_output,
        )
        return 2
    overrides: dict[str, object] = {}
    lifecycle_override = getattr(arguments, "lifecycle_mode", None)
    if lifecycle_override is not None:
        overrides = {"deployment": {"lifecycle_mode": lifecycle_override}}
    try:
        loaded = load_configuration(
            config_path=getattr(arguments, "config", None),
            cli_overrides=overrides,
        )
    except ConfigurationError as error:
        _configuration_error(str(error), json_output=json_output)
        return 2

    try:
        facts = fact_collector()
        if arguments.command == "doctor":
            if linux_fact_collector is not None:
                linux_facts = linux_fact_collector(loaded.configuration)
            elif fact_collector is collect_host_facts:
                linux_facts = collect_configured_linux_facts(loaded.configuration)
            else:
                linux_facts = None
            report = build_composed_doctor_report(
                loaded.configuration,
                facts,
                linux_facts,
                dry_run=bool(getattr(arguments, "dry_run", False)),
            )
        elif getattr(arguments, "phase", None) == "identity-access":
            identity_facts = (identity_fact_collector or collect_identity_facts)(
                loaded.configuration
            )
            report = build_identity_plan(
                loaded.configuration,
                facts,
                identity_facts,
                identity_prerequisites,
            )
        else:
            report = build_install_plan_report(
                loaded.configuration,
                facts,
                linux_facts_collector=linux_fact_collector or collect_configured_linux_facts,
                directory_facts_collector=(
                    directory_fact_collector or collect_directory_resource_facts
                ),
            )
        if json_output:
            if isinstance(report, DoctorReport):
                payload = report.as_dict(verbose=bool(getattr(arguments, "verbose", False)))
                print(json.dumps(payload, indent=2, sort_keys=True))
            elif isinstance(report, InstallPlanReport):
                print(report.to_json())
            else:
                print(report.to_json())
        elif isinstance(report, DoctorReport):
            print(render_text(report, verbose=bool(getattr(arguments, "verbose", False))))
        elif isinstance(report, InstallPlanReport):
            print(render_install_plan_text(report))
        else:
            print(render_identity_plan_text(report))
        return report.exit_code
    except BrokenPipeError:
        return report.exit_code if report is not None else 10
    except KeyboardInterrupt:
        if json_output:
            _emit_json_error("interrupted", "interrupted")
        else:
            _emit_human_error("interrupted")
        return 130
    except Exception as error:  # Defensive CLI boundary: never expose host evidence in a traceback.
        if json_output:
            _emit_json_error("internal_error", "command failed internally")
        else:
            _emit_human_error("command failed internally", type(error).__name__)
        return 10


if __name__ == "__main__":
    raise SystemExit(main())
