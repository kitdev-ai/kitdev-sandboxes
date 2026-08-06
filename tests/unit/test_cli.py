from __future__ import annotations

import contextlib
import copy
import io
import json
import tempfile
import unittest
from pathlib import Path

from kitdev_sandboxes.cli import main
from kitdev_sandboxes.preflight import HostFacts
from tests.schema_assertions import assert_schema_conforms
from tests.unit.test_composition import absent_directory_facts, complete_linux_facts


def facts() -> HostFacts:
    return HostFacts(
        os_id="ubuntu",
        os_name="Ubuntu",
        os_version_id="26.04",
        architecture="x86_64",
        pid1_comm="systemd",
        cgroup_v2=True,
        cpu_virtualization="vmx",
        kvm_device_exists=True,
        kvm_device_is_character=True,
        kvm_device_accessible=True,
        nested_guest_support=False,
    )


class CliTests(unittest.TestCase):
    def test_doctor_flags_after_command_and_json_output(self) -> None:
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            code = main(
                [
                    "doctor",
                    "--json",
                    "--dry-run",
                    "--non-interactive",
                    "--lifecycle-mode",
                    "migration",
                ],
                fact_collector=facts,
            )
        payload = json.loads(output.getvalue())

        self.assertEqual(code, 5)
        self.assertEqual(payload["lifecycle_mode"], "migration")
        self.assertTrue(payload["dry_run"])
        self.assertEqual(payload["changes"], [])
        self.assertNotIn("sources", payload)

    def test_invalid_deep_configuration_returns_two_without_traceback_or_path_in_json(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / "deep.yaml"
            config.write_text(
                "\n".join(f"{'  ' * index}key{index}:" for index in range(1_100)),
                encoding="utf-8",
            )
            output = io.StringIO()
            errors = io.StringIO()
            with contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
                code = main(["doctor", "--json", "--config", str(config)], fact_collector=facts)

        payload = json.loads(output.getvalue())
        self.assertEqual(code, 2)
        self.assertEqual(payload["error"]["code"], "invalid_configuration")
        self.assertNotIn(directory, output.getvalue())
        self.assertNotIn("Traceback", errors.getvalue())

    def test_noninteractive_doctor_never_reads_input(self) -> None:
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            code = main(["doctor", "--non-interactive"], fact_collector=facts)

        self.assertEqual(code, 5)
        self.assertIn("doctor never mutates", output.getvalue())

    def test_internal_error_is_bounded(self) -> None:
        def broken() -> HostFacts:
            raise RuntimeError("Token=should-not-leak")

        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            code = main(["doctor", "--json"], fact_collector=broken)

        self.assertEqual(code, 10)
        self.assertNotIn("should-not-leak", output.getvalue())

    def test_human_internal_error_is_redacted_and_utf8_bounded(self) -> None:
        huge_error = type("X" * 200_000, (RuntimeError,), {})

        def broken() -> HostFacts:
            raise huge_error("Token=should-not-leak")

        errors = io.StringIO()
        with contextlib.redirect_stderr(errors):
            code = main(["doctor"], fact_collector=broken)

        rendered = errors.getvalue()
        self.assertEqual(code, 10)
        self.assertLessEqual(len(rendered.encode("utf-8")), 4_096)
        self.assertTrue(rendered.endswith("...[truncated]\n"))
        self.assertNotIn("should-not-leak", rendered)

    def test_invalid_command_with_json_returns_structured_error(self) -> None:
        output = io.StringIO()
        errors = io.StringIO()
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
            code = main(["--json", "invalid-command"], fact_collector=facts)

        self.assertEqual(code, 2)
        payload = json.loads(output.getvalue())
        self.assertEqual(payload["error"]["code"], "invalid_invocation")
        schema = json.loads(
            (Path(__file__).resolve().parents[2] / "config" / "cli-error.schema.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(set(payload), set(schema["required"]))
        self.assertEqual(errors.getvalue(), "")

    def test_keyboard_interrupt_has_deliberate_exit(self) -> None:
        def interrupted() -> HostFacts:
            raise KeyboardInterrupt

        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            code = main(["doctor", "--json"], fact_collector=interrupted)

        self.assertEqual(code, 130)
        self.assertEqual(json.loads(output.getvalue())["error"]["code"], "interrupted")

    def test_broken_pipe_does_not_convert_blocked_doctor_to_success(self) -> None:
        class BrokenWriter(io.StringIO):
            def write(self, value: str) -> int:
                raise BrokenPipeError

        with contextlib.redirect_stdout(BrokenWriter()):
            code = main(["doctor", "--json"], fact_collector=facts)

        self.assertEqual(code, 5)

    def test_human_invocation_error_neutralizes_controls_and_cookie_values(self) -> None:
        errors = io.StringIO()
        with contextlib.redirect_stderr(errors):
            code = main(["invalid\nCookie: session=secret"], fact_collector=facts)

        self.assertEqual(code, 2)
        self.assertNotIn("\nsession", errors.getvalue())
        self.assertNotIn("secret", errors.getvalue())

    def test_oversize_human_invocation_and_configuration_errors_are_bounded(self) -> None:
        cases = (
            ["X" * 200_000 + "\nCookie: session=invocation-secret"],
            [
                "doctor",
                "--config",
                "X" * 200_000 + "\nCookie: session=configuration-secret",
            ],
        )

        for arguments in cases:
            with self.subTest(command=arguments[0]):
                errors = io.StringIO()
                with contextlib.redirect_stderr(errors):
                    code = main(arguments, fact_collector=facts)
                rendered = errors.getvalue()

                self.assertEqual(code, 2)
                self.assertLessEqual(len(rendered.encode("utf-8")), 4_096)
                self.assertEqual(rendered.count("\n"), 1)
                self.assertTrue(rendered.endswith("...[truncated]\n"))
                self.assertNotIn("invocation-secret", rendered)
                self.assertNotIn("configuration-secret", rendered)

    def test_json_invocation_errors_ignore_closed_stdout_and_return_two(self) -> None:
        class BrokenWriter(io.StringIO):
            def write(self, value: str) -> int:
                raise BrokenPipeError

        def forbidden() -> HostFacts:
            raise AssertionError("collector must not run")

        for arguments in (
            ["--json", "invalid-command"],
            ["install", "--json"],
            ["doctor", "--json", "--config", "/definitely/missing/config.yaml"],
        ):
            with self.subTest(arguments=arguments), contextlib.redirect_stdout(
                BrokenWriter()
            ):
                code = main(arguments, fact_collector=forbidden)
            self.assertEqual(code, 2)

    def test_integrated_doctor_uses_complete_linux_groups_without_raw_facts(self) -> None:
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            code = main(
                ["doctor", "--json"],
                fact_collector=facts,
                linux_fact_collector=lambda _config: complete_linux_facts(),
            )
        payload = json.loads(output.getvalue())
        checks = {check["id"]: check for check in payload["checks"]}

        self.assertEqual(code, 5)
        self.assertEqual(checks["scope.kernel_facilities"]["status"], "pass")
        self.assertEqual(checks["scope.capacity"]["status"], "warn")
        self.assertEqual(checks["scope.network_conflicts"]["status"], "unknown")
        for key in ("listeners", "interfaces", "routes", "memory", "filesystems"):
            self.assertNotIn(f'"{key}"', output.getvalue())

    def test_bare_install_and_apply_like_invocation_reject_before_collection(self) -> None:
        calls = 0

        def forbidden() -> HostFacts:
            nonlocal calls
            calls += 1
            raise AssertionError("collector must not run")

        for arguments in (["install"], ["install", "--apply"]):
            with self.subTest(arguments=arguments):
                output = io.StringIO()
                errors = io.StringIO()
                with contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
                    code = main(arguments, fact_collector=forbidden)
                self.assertEqual(code, 2)
        self.assertEqual(calls, 0)

    def test_install_dry_run_json_matches_schema_and_fixture_contract(self) -> None:
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            code = main(
                ["install", "--dry-run", "--json"],
                fact_collector=facts,
                linux_fact_collector=lambda _config: complete_linux_facts(),
                directory_fact_collector=absent_directory_facts,
            )
        payload = json.loads(output.getvalue())
        root = Path(__file__).resolve().parents[2]
        schema = json.loads(
            (root / "config" / "install-plan.schema.json").read_text(encoding="utf-8")
        )
        fixture = json.loads(
            (
                root
                / "tests"
                / "fixtures"
                / "plans"
                / "install-dry-run-supported-blocked.json"
            ).read_text(encoding="utf-8")
        )

        self.assertEqual(code, 5)
        self.assertEqual(payload, fixture)
        assert_schema_conforms(payload, schema)
        assert_schema_conforms(fixture, schema)
        self.assertNotIn("timestamp", payload)

    def test_install_plan_schema_assertion_rejects_each_enforced_constraint(self) -> None:
        root = Path(__file__).resolve().parents[2]
        schema = json.loads(
            (root / "config" / "install-plan.schema.json").read_text(encoding="utf-8")
        )
        fixture_path = (
            root
            / "tests"
            / "fixtures"
            / "plans"
            / "install-dry-run-supported-blocked.json"
        )
        fixture = json.loads(fixture_path.read_text(encoding="utf-8"))
        invalid: list[tuple[str, object]] = []

        candidate = copy.deepcopy(fixture)
        del candidate["dry_run"]
        invalid.append(("required", candidate))
        candidate = copy.deepcopy(fixture)
        candidate["summary"]["extra"] = 0
        invalid.append(("additionalProperties", candidate))
        candidate = copy.deepcopy(fixture)
        candidate["command_mode"] = "install"
        invalid.append(("const", candidate))
        candidate = copy.deepcopy(fixture)
        candidate["lifecycle_mode"] = "preview"
        invalid.append(("enum", candidate))
        candidate = copy.deepcopy(fixture)
        candidate["blocking"] = "true"
        invalid.append(("type", candidate))
        candidate = copy.deepcopy(fixture)
        candidate["actions"] = [False]
        invalid.append(("array items", candidate))
        candidate = copy.deepcopy(fixture)
        candidate["summary"]["actions"] = True
        invalid.append(("integer versus bool", candidate))
        candidate = copy.deepcopy(fixture)
        candidate["summary"]["issues"] = 257
        invalid.append(("range", candidate))
        candidate = copy.deepcopy(fixture)
        candidate["issues"][0]["id"] = "INVALID ID"
        invalid.append(("pattern", candidate))

        for constraint, candidate in invalid:
            with self.subTest(constraint=constraint), self.assertRaises(AssertionError):
                assert_schema_conforms(candidate, schema)

    def test_install_dry_run_with_injected_collectors_creates_no_cwd_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            sentinel = root / "sentinel"
            sentinel.write_bytes(b"unchanged")
            calls: list[str] = []

            def linux(config):
                calls.append("linux")
                return complete_linux_facts()

            def directories(config):
                calls.append("directories")
                return absent_directory_facts(config)

            def snapshot() -> tuple[tuple[str, str, bytes | None], ...]:
                entries: list[tuple[str, str, bytes | None]] = []
                for path in sorted(root.rglob("*")):
                    kind = "directory" if path.is_dir() else "file"
                    content = path.read_bytes() if path.is_file() else None
                    entries.append((str(path.relative_to(root)), kind, content))
                return tuple(entries)

            before = snapshot()
            output = io.StringIO()
            with contextlib.chdir(root), contextlib.redirect_stdout(output):
                code = main(
                    ["install", "--dry-run", "--non-interactive"],
                    fact_collector=facts,
                    linux_fact_collector=linux,
                    directory_fact_collector=directories,
                )
            after = snapshot()

        self.assertEqual(code, 5)
        self.assertEqual(calls, ["linux", "directories"])
        self.assertEqual(before, after)
        self.assertIn("No changes were made.", output.getvalue())

    def test_install_mixed_platform_failure_and_unknown_returns_three(self) -> None:
        mixed = HostFacts(
            os_id="debian",
            os_name="Debian",
            os_version_id="13",
            architecture=None,
            pid1_comm="systemd",
            cgroup_v2=True,
            cpu_virtualization="vmx",
            kvm_device_exists=True,
            kvm_device_is_character=True,
            kvm_device_accessible=True,
            nested_guest_support=False,
        )

        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            code = main(
                ["install", "--dry-run", "--json"],
                fact_collector=lambda: mixed,
                linux_fact_collector=lambda _config: self.fail("collector must not run"),
                directory_fact_collector=lambda _config: self.fail("collector must not run"),
            )

        self.assertEqual(code, 3)
        payload = json.loads(output.getvalue())
        blocker = next(
            issue
            for issue in payload["issues"]
            if issue["id"] == "platform.preflight.blocked"
        )
        self.assertEqual(blocker["code"], "unsupported")

    def test_install_broken_pipe_preserves_blocking_exit(self) -> None:
        class BrokenWriter(io.StringIO):
            def write(self, value: str) -> int:
                raise BrokenPipeError

        with contextlib.redirect_stdout(BrokenWriter()):
            code = main(
                ["install", "--dry-run", "--json"],
                fact_collector=facts,
                linux_fact_collector=lambda _config: complete_linux_facts(),
                directory_fact_collector=absent_directory_facts,
            )

        self.assertEqual(code, 5)


if __name__ == "__main__":
    unittest.main()
