from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import subprocess
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from kitdev_sandboxes.cli import main
from kitdev_sandboxes.firewall_sources import (
    FirewallSourceOperationError,
    FirewallSourceResult,
    _default_runner,
    normalize_source_cidr,
    run_firewall_source_operation,
)

ROOT = Path(__file__).resolve().parents[2]
HELPER = ROOT / "scripts" / "ingress" / "firewall_source_state.py"


def backend_document(*sources: dict[str, object]) -> str:
    return json.dumps(
        {
            "schema_version": 1,
            "command": "firewall source list",
            "status": "pass",
            "sources": list(sources),
        }
    )


class FirewallSourceBackendTests(unittest.TestCase):
    def test_default_runner_has_fixed_environment_closed_stdin_and_output_bound(self) -> None:
        completed = _default_runner(
            [
                "/bin/sh",
                "-c",
                'IFS= read -r value || true; printf "%s|%s|%s" "$PATH" "$LC_ALL" "$value"',
            ]
        )
        self.assertEqual(completed.returncode, 0)
        self.assertEqual(completed.stdout, "/usr/sbin:/usr/bin:/sbin:/bin|C|")
        with self.assertRaises(FirewallSourceOperationError) as raised:
            _default_runner(["/bin/sh", "-c", "yes x | head -c 70000"])
        self.assertEqual(raised.exception.reason, "firewall_source_backend_output_too_large")

    def test_cidr_validation_requires_canonical_bounded_public_ranges(self) -> None:
        self.assertEqual(normalize_source_cidr("8.8.8.8/32"), "8.8.8.8/32")
        self.assertEqual(
            normalize_source_cidr("2606:4700:4700::/64"),
            "2606:4700:4700::/64",
        )
        for value, reason in (
            ("0.0.0.0/0", "source_cidr_not_canonical"),
            ("8.8.8.9/24", "source_cidr_invalid"),
            ("127.0.0.1/32", "source_cidr_non_public_requires_override"),
            ("169.254.0.0/24", "source_cidr_non_public_requires_override"),
            ("224.0.0.0/24", "source_cidr_non_public_requires_override"),
            ("8.8.8.0/23", "source_cidr_broad_range_requires_override"),
            ("2606:4700::/48", "source_cidr_broad_range_requires_override"),
        ):
            with self.subTest(value=value):
                with self.assertRaises(FirewallSourceOperationError) as raised:
                    normalize_source_cidr(value)
                self.assertEqual(raised.exception.reason, reason)
        self.assertEqual(
            normalize_source_cidr("10.0.0.0/24", allow_non_public=True),
            "10.0.0.0/24",
        )
        self.assertEqual(
            normalize_source_cidr("8.8.8.0/23", allow_broad_range=True),
            "8.8.8.0/23",
        )

    def test_backend_invocation_and_response_are_strict(self) -> None:
        calls: list[list[str]] = []

        def runner(arguments):
            calls.append(list(arguments))
            return subprocess.CompletedProcess(
                arguments,
                0,
                backend_document(
                    {
                        "cidr": "8.8.8.8/32",
                        "non_public_override": False,
                        "broad_range_override": False,
                    }
                ),
                "",
            )

        result = run_firewall_source_operation(
            "add",
            cidr="8.8.8.8/32",
            backend=Path("/fixed/backend"),
            runner=runner,
        )
        self.assertEqual(
            calls,
            [["/fixed/backend", "source-add", "--cidr", "8.8.8.8/32"]],
        )
        self.assertEqual(result.sources[0]["cidr"], "8.8.8.8/32")

        def malicious(arguments):
            return subprocess.CompletedProcess(
                arguments,
                0,
                backend_document(
                    {
                        "cidr": "8.8.8.8/32",
                        "non_public_override": False,
                        "broad_range_override": False,
                        "unexpected": "field",
                    }
                ),
                "",
            )

        with self.assertRaises(FirewallSourceOperationError) as raised:
            run_firewall_source_operation("list", runner=malicious)
        self.assertEqual(raised.exception.reason, "firewall_source_backend_invalid")

    def test_manifest_rejects_overlap_and_tracks_reviewed_overrides(self) -> None:
        specification = importlib.util.spec_from_file_location("firewall_state_test", HELPER)
        assert specification is not None and specification.loader is not None
        state = importlib.util.module_from_spec(specification)
        specification.loader.exec_module(state)
        with TemporaryDirectory() as directory:
            state.STATE = Path(directory) / "allowed-sources.json"
            first = state.candidate("add", "8.8.8.0/24", False, False)
            state.install_document(first)
            with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                state.candidate("add", "8.8.8.8/32", False, False)
            private = state.candidate("add", "10.0.0.0/24", True, False)
            private_entry = next(
                item for item in private["sources"] if item["cidr"] == "10.0.0.0/24"
            )
            self.assertTrue(private_entry["non_public_override"])
            self.assertFalse(private_entry["broad_range_override"])

    def test_result_rendering_has_stable_text_and_json(self) -> None:
        result = FirewallSourceResult(
            action="list",
            sources=(
                {
                    "cidr": "8.8.8.8/32",
                    "non_public_override": False,
                    "broad_range_override": False,
                },
            ),
        )
        self.assertEqual(result.as_dict()["command"], "firewall source list")
        self.assertIn("cidr=8.8.8.8/32", result.render_text())


class FirewallSourceCliTests(unittest.TestCase):
    def test_cli_dispatches_add_with_review_flags_and_json(self) -> None:
        calls: list[tuple[str, dict[str, object]]] = []

        def runner(action: str, **keywords: object) -> FirewallSourceResult:
            calls.append((action, keywords))
            return FirewallSourceResult(action, ())

        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            code = main(
                [
                    "firewall",
                    "source",
                    "add",
                    "--cidr",
                    "10.0.0.0/16",
                    "--allow-non-public",
                    "--allow-broad-range",
                    "--json",
                ],
                firewall_source_runner=runner,
            )
        self.assertEqual(code, 0)
        self.assertEqual(calls[0][0], "add")
        self.assertEqual(calls[0][1]["cidr"], "10.0.0.0/16")
        self.assertTrue(calls[0][1]["allow_non_public"])
        self.assertTrue(calls[0][1]["allow_broad_range"])
        self.assertEqual(json.loads(output.getvalue())["command"], "firewall source add")

    def test_cli_renders_backend_error_without_traceback(self) -> None:
        def runner(action: str, **keywords: object) -> FirewallSourceResult:
            raise FirewallSourceOperationError("source_cidr_invalid", 64)

        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            code = main(
                ["firewall", "source", "remove", "--cidr", "invalid", "--json"],
                firewall_source_runner=runner,
            )
        self.assertEqual(code, 64)
        self.assertEqual(json.loads(output.getvalue())["error"]["code"], "source_cidr_invalid")


if __name__ == "__main__":
    unittest.main()
