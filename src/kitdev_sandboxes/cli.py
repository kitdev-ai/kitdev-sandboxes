"""Command-line interface for kitdev-sandboxes."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Callable, Sequence

sys.dont_write_bytecode = True

from kitdev_sandboxes import __version__
from kitdev_sandboxes.config import ConfigurationError, LifecycleMode, load_configuration
from kitdev_sandboxes.preflight import (
    HostFacts,
    build_doctor_report,
    collect_host_facts,
    render_text,
    safe_report_text,
)


class InvocationError(ValueError):
    """Raised instead of letting argparse print an unstructured error."""


class ArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        raise InvocationError(message)


def _add_common_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--config", type=Path, default=argparse.SUPPRESS, help="operator config file")
    parser.add_argument(
        "--lifecycle-mode",
        choices=tuple(mode.value for mode in LifecycleMode),
        default=argparse.SUPPRESS,
        help="explicit deployment lifecycle intent",
    )
    parser.add_argument("--json", action="store_true", default=argparse.SUPPRESS, dest="json_output")
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
    return parser


def _configuration_error(message: str, *, json_output: bool) -> None:
    if json_output:
        payload = {
            "schema_version": 1,
            "error": {
                "code": "invalid_configuration",
                "message": "configuration could not be loaded or validated",
            },
        }
        print(json.dumps(payload, sort_keys=True))
    else:
        print(f"kitdev: configuration error: {safe_report_text(message)}", file=sys.stderr)


def _invocation_error(message: str, *, json_output: bool) -> None:
    if json_output:
        print(
            json.dumps(
                {
                    "schema_version": 1,
                    "error": {"code": "invalid_invocation", "message": "invalid command invocation"},
                },
                sort_keys=True,
            )
        )
    else:
        print(f"kitdev: invocation error: {safe_report_text(message)}", file=sys.stderr)


def main(
    argv: Sequence[str] | None = None,
    *,
    fact_collector: Callable[[], HostFacts] = collect_host_facts,
) -> int:
    parser = build_parser()
    raw_arguments = list(argv) if argv is not None else sys.argv[1:]
    json_requested = "--json" in raw_arguments
    report = None
    try:
        arguments = parser.parse_args(raw_arguments)
    except InvocationError as error:
        _invocation_error(str(error), json_output=json_requested)
        return 2
    json_output = bool(getattr(arguments, "json_output", False))
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
        report = build_doctor_report(
            facts,
            loaded.configuration.deployment.lifecycle_mode,
            dry_run=bool(getattr(arguments, "dry_run", False)),
        )
        if json_output:
            print(
                json.dumps(
                    report.as_dict(verbose=bool(getattr(arguments, "verbose", False))),
                    indent=2,
                    sort_keys=True,
                )
            )
        else:
            print(render_text(report, verbose=bool(getattr(arguments, "verbose", False))))
        return report.exit_code
    except BrokenPipeError:
        return report.exit_code if report is not None else 10
    except KeyboardInterrupt:
        if json_output:
            print(
                json.dumps(
                    {"schema_version": 1, "error": {"code": "interrupted", "message": "interrupted"}},
                    sort_keys=True,
                )
            )
        else:
            print("kitdev: interrupted", file=sys.stderr)
        return 130
    except Exception as error:  # Defensive CLI boundary: never expose host evidence in a traceback.
        if json_output:
            print(
                json.dumps(
                    {
                        "schema_version": 1,
                        "error": {"code": "internal_error", "message": "doctor failed internally"},
                    },
                    sort_keys=True,
                )
            )
        else:
            print(f"kitdev: doctor failed internally: {type(error).__name__}", file=sys.stderr)
        return 10


if __name__ == "__main__":
    raise SystemExit(main())
