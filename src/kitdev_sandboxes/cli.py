"""Command-line interface for kitdev-sandboxes."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Callable, Sequence

sys.dont_write_bytecode = True

from kitdev_sandboxes import __version__
from kitdev_sandboxes.api_keys import (
    DEFAULT_PRIVATE_ENV,
    ApiKeyOperationError,
    ApiKeyResult,
    run_api_key,
)
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
    DeploymentProfile,
    LifecycleMode,
    load_configuration,
)
from kitdev_sandboxes.firewall_sources import (
    FirewallSourceOperationError,
    FirewallSourceResult,
    run_firewall_mode_operation,
    run_firewall_source_operation,
)
from kitdev_sandboxes.lifecycle import LifecycleRunner, run_lifecycle
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
        help="converge the control plane on a prepared host, or calculate a dry-run plan",
        description="Converge reviewed control-plane assets after strict host prerequisite checks.",
    )
    install.add_argument(
        "--phase",
        choices=("identity-access",),
        default=None,
        help="calculate one explicitly selected installation phase",
    )
    install.add_argument(
        "--profile",
        choices=tuple(profile.value for profile in DeploymentProfile),
        default=argparse.SUPPRESS,
        help="deployment profile; only minimal is implemented for apply",
    )
    for name, help_text in (
        ("up", "start and verify installed control-plane services"),
        ("down", "quiesce and stop services without deleting state"),
        ("restart", "safely stop, start, and verify services"),
        ("status", "read installed service health without mutation"),
    ):
        commands.add_parser(name, parents=[common], help=help_text, description=help_text)
    test = commands.add_parser(
        "test",
        parents=[common],
        help="run an explicit mutating post-install test",
        description="Run a named post-install test. Smoke testing is disabled in production mode.",
    )
    test.add_argument("suite", choices=("core", "sdk", "smoke"))
    test.add_argument(
        "--api-key-file",
        type=Path,
        required=False,
        help="absolute root-owned mode-0600 E2B API key file",
    )
    test.add_argument(
        "--template-id-file",
        type=Path,
        required=False,
        help="absolute root-owned mode-0600 E2B template ID file",
    )
    api_key = commands.add_parser(
        "api-key",
        parents=[common],
        help="create, inspect, verify, or revoke project API keys",
    )
    api_key_actions = api_key.add_subparsers(dest="api_key_action", required=True)

    def add_admin_source(action: argparse.ArgumentParser) -> None:
        source = action.add_mutually_exclusive_group()
        source.add_argument(
            "--admin-token-file",
            type=Path,
            help="absolute root-owned mode-0600 file containing only the admin token",
        )
        source.add_argument(
            "--private-env-file",
            type=Path,
            help="absolute root-owned mode-0600 private environment file",
        )

    create = api_key_actions.add_parser("create", parents=[common])
    create_team = create.add_mutually_exclusive_group()
    create_team.add_argument("--team-id")
    create_team.add_argument("--team-slug")
    create.add_argument("--name", required=True)
    create.add_argument("--output", type=Path, required=True)
    create.add_argument("--metadata-file", type=Path)
    create.add_argument("--owner", default="root")
    create.add_argument("--group")
    add_admin_source(create)

    list_keys = api_key_actions.add_parser("list", parents=[common])
    list_team = list_keys.add_mutually_exclusive_group()
    list_team.add_argument("--team-id")
    list_team.add_argument("--team-slug")
    add_admin_source(list_keys)

    verify = api_key_actions.add_parser("verify", parents=[common])
    verify.add_argument("--key-file", type=Path, required=True)
    verify.add_argument("--metadata-file", type=Path)

    revoke = api_key_actions.add_parser("revoke", parents=[common])
    revoke_team = revoke.add_mutually_exclusive_group()
    revoke_team.add_argument("--team-id")
    revoke_team.add_argument("--team-slug")
    revoke.add_argument("--key-id", required=True)
    revoke.add_argument("--confirm-key-id", required=True)
    revoke.add_argument("--metadata-file", type=Path)
    revoke.add_argument(
        "--delete-key-file",
        action="store_true",
        help="delete the exact metadata-bound key file after durable revocation",
    )
    add_admin_source(revoke)
    api_key_actions.add_parser(
        "teams",
        parents=[common],
        help="list eligible local team IDs, slugs, and names",
    )
    firewall = commands.add_parser(
        "firewall",
        parents=[common],
        help="manage host firewall policy",
    )
    firewall_actions = firewall.add_subparsers(dest="firewall_action", required=True)
    source = firewall_actions.add_parser(
        "source",
        parents=[common],
        help="manage SDK HTTPS source CIDRs",
    )
    source_actions = source.add_subparsers(dest="firewall_source_action", required=True)
    add_source = source_actions.add_parser("add", parents=[common])
    add_source.add_argument("--cidr", required=True)
    add_source.add_argument("--allow-non-public", action="store_true")
    add_source.add_argument("--allow-broad-range", action="store_true")
    source_actions.add_parser("list", parents=[common])
    remove_source = source_actions.add_parser("remove", parents=[common])
    remove_source.add_argument("--cidr", required=True)
    mode = firewall_actions.add_parser(
        "mode",
        parents=[common],
        help="set public, source-restricted, or closed HTTPS policy",
    )
    mode.add_argument("mode_value", choices=("public", "restricted", "closed"))
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
    lifecycle_runner: LifecycleRunner = run_lifecycle,
    api_key_runner: Callable[..., ApiKeyResult] = run_api_key,
    firewall_source_runner: Callable[..., FirewallSourceResult] = run_firewall_source_operation,
    firewall_mode_runner: Callable[..., FirewallSourceResult] = run_firewall_mode_operation,
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
    if (
        arguments.command == "install"
        and getattr(arguments, "phase", None) is not None
        and not bool(getattr(arguments, "dry_run", False))
    ):
        _invocation_error(
            "install --phase is available only with --dry-run",
            json_output=json_output,
        )
        return 2
    overrides: dict[str, object] = {}
    lifecycle_override = getattr(arguments, "lifecycle_mode", None)
    deployment_overrides: dict[str, object] = {}
    if lifecycle_override is not None:
        deployment_overrides["lifecycle_mode"] = lifecycle_override
    profile_override = getattr(arguments, "profile", None)
    if profile_override is not None:
        deployment_overrides["profile"] = profile_override
    if deployment_overrides:
        overrides = {"deployment": deployment_overrides}
    try:
        loaded = load_configuration(
            config_path=getattr(arguments, "config", None),
            cli_overrides=overrides,
        )
    except ConfigurationError as error:
        _configuration_error(str(error), json_output=json_output)
        return 2

    if arguments.command == "api-key":
        action = str(arguments.api_key_action)
        if bool(getattr(arguments, "dry_run", False)):
            payload = {
                "schema_version": 1,
                "command": f"api-key {action}",
                "status": "planned",
                "changes": 0,
            }
            if json_output:
                print(json.dumps(payload, indent=2, sort_keys=True))
            else:
                print(f"command=api-key-{action} status=planned changes=0")
            return 0
        try:
            api_key_result = api_key_runner(
                action,
                loaded.configuration,
                team_id=getattr(arguments, "team_id", None),
                team_slug=getattr(arguments, "team_slug", None),
                name=getattr(arguments, "name", None),
                output=getattr(arguments, "output", None),
                metadata_file=getattr(arguments, "metadata_file", None),
                owner=getattr(arguments, "owner", "root"),
                group=getattr(arguments, "group", None),
                admin_token_file=getattr(arguments, "admin_token_file", None),
                private_env_file=(
                    getattr(arguments, "private_env_file", None) or DEFAULT_PRIVATE_ENV
                ),
                key_file=getattr(arguments, "key_file", None),
                key_id=getattr(arguments, "key_id", None),
                confirm_key_id=getattr(arguments, "confirm_key_id", None),
                delete_key_file=bool(getattr(arguments, "delete_key_file", False)),
            )
        except KeyboardInterrupt:
            if json_output:
                _emit_json_error("interrupted", "interrupted")
            else:
                _emit_human_error("interrupted")
            return 130
        except ApiKeyOperationError as error:
            if json_output:
                _emit_json_error(error.reason, "API-key operation failed")
            else:
                _emit_human_error("API-key operation failed", error.reason)
            return error.exit_code
        except (OSError, RuntimeError, ValueError) as error:
            if json_output:
                _emit_json_error("api_key_internal_error", "API-key operation failed internally")
            else:
                _emit_human_error("API-key operation failed internally", type(error).__name__)
            return 10
        try:
            if json_output:
                print(json.dumps(api_key_result.as_dict(), indent=2, sort_keys=True))
            else:
                print(api_key_result.render_text())
        except BrokenPipeError:
            return 0
        return 0

    if arguments.command == "firewall":
        is_mode = arguments.firewall_action == "mode"
        action = (
            str(arguments.mode_value)
            if is_mode
            else str(arguments.firewall_source_action)
        )
        command = f"firewall mode {action}" if is_mode else f"firewall source {action}"
        if bool(getattr(arguments, "dry_run", False)):
            payload = {
                "schema_version": 1,
                "command": command,
                "status": "planned",
                "changes": 0,
            }
            if json_output:
                print(json.dumps(payload, indent=2, sort_keys=True))
            else:
                print(f"command={command.replace(' ', '-')} status=planned changes=0")
            return 0
        try:
            if is_mode:
                firewall_result = firewall_mode_runner(action)
            else:
                firewall_result = firewall_source_runner(
                    action,
                    cidr=getattr(arguments, "cidr", None),
                    allow_non_public=bool(getattr(arguments, "allow_non_public", False)),
                    allow_broad_range=bool(getattr(arguments, "allow_broad_range", False)),
                )
        except KeyboardInterrupt:
            if json_output:
                _emit_json_error("interrupted", "interrupted")
            else:
                _emit_human_error("interrupted")
            return 130
        except FirewallSourceOperationError as error:
            if json_output:
                _emit_json_error(error.reason, "firewall source operation failed")
            else:
                _emit_human_error("firewall source operation failed", error.reason)
            return error.exit_code
        except (OSError, RuntimeError, ValueError) as error:
            if json_output:
                _emit_json_error(
                    "firewall_source_internal_error",
                    "firewall source operation failed internally",
                )
            else:
                _emit_human_error(
                    "firewall source operation failed internally",
                    type(error).__name__,
                )
            return 10
        try:
            if json_output:
                print(json.dumps(firewall_result.as_dict(), indent=2, sort_keys=True))
            else:
                print(firewall_result.render_text())
        except BrokenPipeError:
            return 0
        return 0

    lifecycle_commands = {"install", "up", "down", "restart", "status", "test"}
    is_install_dry_run = arguments.command == "install" and bool(
        getattr(arguments, "dry_run", False)
    )
    if arguments.command in lifecycle_commands and not is_install_dry_run:
        if bool(getattr(arguments, "dry_run", False)):
            payload = {
                "schema_version": 1,
                "command": arguments.command,
                "status": "planned",
                "changes": 0,
            }
            if json_output:
                print(json.dumps(payload, indent=2, sort_keys=True))
            else:
                print(f"command={arguments.command} status=planned changes=0")
            return 0
        operation = (
            f"test-{arguments.suite}" if arguments.command == "test" else arguments.command
        )
        api_key_file = getattr(arguments, "api_key_file", None)
        template_id_file = getattr(arguments, "template_id_file", None)
        try:
            result = lifecycle_runner(
                operation,
                loaded.configuration,
                quiet=json_output,
                api_key_file=api_key_file,
                template_id_file=template_id_file,
            )
        except KeyboardInterrupt:
            if json_output:
                _emit_json_error("interrupted", "interrupted")
            else:
                _emit_human_error("interrupted")
            return 130
        except (OSError, RuntimeError, ValueError) as error:
            if json_output:
                _emit_json_error("lifecycle_error", "lifecycle command could not be started")
            else:
                _emit_human_error("lifecycle command could not be started", type(error).__name__)
            return 10
        if json_output:
            print(json.dumps(result.as_dict(), indent=2, sort_keys=True))
        return result.exit_code

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
