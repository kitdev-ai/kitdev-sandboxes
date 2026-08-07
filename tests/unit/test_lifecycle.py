from __future__ import annotations

import contextlib
import io
import json
import os
import subprocess
import unittest
from pathlib import Path
from unittest import mock

from kitdev_sandboxes.cli import main
from kitdev_sandboxes.config import load_configuration
from kitdev_sandboxes.lifecycle import LifecycleResult, run_lifecycle


ROOT = Path(__file__).resolve().parents[2]


class LifecycleCliTests(unittest.TestCase):
    def test_commands_dispatch_without_host_fact_collection(self) -> None:
        calls: list[tuple[str, str, bool, Path | None]] = []

        def forbidden():
            raise AssertionError("host collector must not run")

        def runner(
            operation,
            configuration,
            *,
            quiet,
            api_key_file=None,
            template_id_file=None,
        ):
            calls.append(
                (operation, configuration.deployment.lifecycle_mode.value, quiet, api_key_file)
            )
            return LifecycleResult(operation, 0)

        cases = (
            (["install"], "install", None),
            (["up"], "up", None),
            (["down"], "down", None),
            (["restart"], "restart", None),
            (["status"], "status", None),
            (["test", "core", "--api-key-file", "/run/key"], "test-core", Path("/run/key")),
        )
        for arguments, operation, key in cases:
            with self.subTest(arguments=arguments):
                self.assertEqual(
                    main(arguments, fact_collector=forbidden, lifecycle_runner=runner),
                    0,
                )
                self.assertEqual(calls[-1], (operation, "production", False, key))

    def test_lifecycle_dry_run_never_dispatches(self) -> None:
        def forbidden(*args, **kwargs):
            raise AssertionError("lifecycle runner must not run")

        for command in ("up", "down", "restart", "status"):
            output = io.StringIO()
            with self.subTest(command=command), contextlib.redirect_stdout(output):
                code = main([command, "--dry-run", "--json"], lifecycle_runner=forbidden)
            self.assertEqual(code, 0)
            self.assertEqual(json.loads(output.getvalue())["status"], "planned")

    def test_json_lifecycle_result_is_normalized(self) -> None:
        output = io.StringIO()

        def runner(
            operation,
            configuration,
            *,
            quiet,
            api_key_file=None,
            template_id_file=None,
        ):
            self.assertTrue(quiet)
            return LifecycleResult(operation, 69)

        with contextlib.redirect_stdout(output):
            code = main(["down", "--json"], lifecycle_runner=runner)

        self.assertEqual(code, 69)
        self.assertEqual(
            json.loads(output.getvalue()),
            {"schema_version": 1, "command": "down", "status": "fail", "exit_code": 69},
        )

    def test_profile_override_reaches_lifecycle_gate(self) -> None:
        observed = None

        def runner(
            operation,
            configuration,
            *,
            quiet,
            api_key_file=None,
            template_id_file=None,
        ):
            nonlocal observed
            observed = configuration.deployment.profile.value
            return LifecycleResult(operation, 68)

        self.assertEqual(main(["install", "--profile", "full"], lifecycle_runner=runner), 68)
        self.assertEqual(observed, "full")


class LifecycleRunnerTests(unittest.TestCase):
    def test_runner_uses_exact_argv_clean_environment_and_no_shell(self) -> None:
        configuration = load_configuration().configuration
        completed = subprocess.CompletedProcess([], 0)
        with mock.patch("kitdev_sandboxes.lifecycle.subprocess.run", return_value=completed) as run:
            with mock.patch.dict(os.environ, {"E2B_API_KEY": "must-not-leak"}):
                result = run_lifecycle(
                    "test-smoke",
                    configuration,
                    quiet=True,
                    api_key_file=Path("/run/kitdev/key"),
                    template_id_file=Path("/run/kitdev/template"),
                )

        self.assertEqual(result, LifecycleResult("test-smoke", 0))
        arguments, keywords = run.call_args
        self.assertEqual(
            arguments[0],
            ("/usr/bin/bash", str(ROOT / "scripts/control-plane/lifecycle.sh"), "test-smoke"),
        )
        self.assertEqual(keywords["cwd"], ROOT)
        self.assertIs(keywords["stdin"], subprocess.DEVNULL)
        self.assertIs(keywords["stdout"], subprocess.DEVNULL)
        self.assertIs(keywords["stderr"], subprocess.DEVNULL)
        self.assertNotIn("shell", keywords)
        self.assertNotIn("E2B_API_KEY", keywords["env"])
        self.assertEqual(keywords["env"]["KITDEV_E2E_API_KEY_FILE"], "/run/kitdev/key")
        self.assertEqual(
            keywords["env"]["KITDEV_E2E_TEMPLATE_ID_FILE"], "/run/kitdev/template"
        )
        self.assertEqual(keywords["env"]["KITDEV_LIFECYCLE"], "production")

    def test_runner_rejects_unknown_operation_and_relative_secret_path(self) -> None:
        configuration = load_configuration().configuration
        with self.assertRaises(ValueError):
            run_lifecycle("remove-everything", configuration, quiet=True)
        with self.assertRaises(ValueError):
            run_lifecycle(
                "test-core", configuration, quiet=True, api_key_file=Path("relative/key")
            )

    def test_status_parses_only_bounded_normalized_health(self) -> None:
        configuration = load_configuration().configuration
        output = (
            "status=pass orchestrator=active compose=running api=healthy "
            "proxy=healthy firecrackers=0\n"
        )
        completed = subprocess.CompletedProcess([], 0, stdout=output)
        with mock.patch("kitdev_sandboxes.lifecycle.subprocess.run", return_value=completed):
            result = run_lifecycle("status", configuration, quiet=True)

        self.assertEqual(result.exit_code, 0)
        self.assertEqual(
            dict(result.health),
            {
                "api": "healthy",
                "compose": "running",
                "firecrackers": "0",
                "orchestrator": "active",
                "proxy": "healthy",
            },
        )

    def test_status_rejects_unexpected_or_oversize_output(self) -> None:
        configuration = load_configuration().configuration
        for output in ("status=pass secret=value\n", "x" * 4_097):
            completed = subprocess.CompletedProcess([], 0, stdout=output)
            with self.subTest(size=len(output)), mock.patch(
                "kitdev_sandboxes.lifecycle.subprocess.run", return_value=completed
            ):
                self.assertEqual(run_lifecycle("status", configuration, quiet=True).exit_code, 70)


class LifecycleAssetTests(unittest.TestCase):
    def test_install_sequence_is_ordered_and_prepared_host_gated(self) -> None:
        script = (ROOT / "scripts/control-plane/lifecycle.sh").read_text(encoding="ascii")
        body = script.split("install_control_plane() {", 1)[1].split("\n}", 1)[0]
        expected = (
            "require_prepared_host",
            '"$SCRIPT_DIR/prepare-layout.sh"',
            '"$SCRIPT_DIR/bootstrap-private-env.sh"',
            '"$SCRIPT_DIR/bootstrap-network.sh" ensure',
            '"$SCRIPT_DIR/acquire-source.sh"',
            '"$SCRIPT_DIR/build-control-plane-images.sh"',
            '"$SCRIPT_DIR/replay-compose.sh" install',
            "install_lifecycle_assets",
            '"$SCRIPT_DIR/install-runtime-artifacts.sh"',
            '"$SCRIPT_DIR/build-envd.sh"',
            '"$SCRIPT_DIR/build-snapshot-tools.sh"',
            '"$SCRIPT_DIR/build-orchestrator.sh"',
            '"$SCRIPT_DIR/configure-firewall.sh" apply',
            '"$SCRIPT_DIR/install-orchestrator-service.sh" install',
            "up_control_plane",
            '"$SCRIPT_DIR/seed-local-template.sh"',
        )
        positions = [body.index(item) for item in expected]
        self.assertEqual(positions, sorted(positions))
        prepared = script.split("require_prepared_host() {", 1)[1].split("\n}", 1)[0]
        self.assertLess(
            prepared.index("production_template_install_not_implemented"),
            prepared.index("require_worker_identity"),
        )

    def test_down_quiesces_admission_and_refuses_active_firecracker(self) -> None:
        script = (ROOT / "scripts/control-plane/lifecycle.sh").read_text(encoding="ascii")
        body = script.split("down_control_plane() {", 1)[1].split("\n}", 1)[0]
        first_guard = body.index("active_sandboxes_present")
        quiesce = body.index('replay-compose.sh" quiesce')
        second_guard = body.index("sandbox_started_during_quiesce")
        orchestrator_stop = body.index("systemctl stop kitdev-e2b-orchestrator.service")
        compose_down = body.index('replay-compose.sh" down')
        self.assertLess(first_guard, quiesce)
        self.assertLess(quiesce, second_guard)
        self.assertLess(second_guard, orchestrator_stop)
        self.assertLess(orchestrator_stop, compose_down)

    def test_status_path_does_not_take_lock_or_mutate(self) -> None:
        script = (ROOT / "scripts/control-plane/lifecycle.sh").read_text(encoding="ascii")
        main_body = script.split("main() {", 1)[1].split("\n}", 1)[0]
        self.assertIn('if [[ "$operation" != status ]]', main_body)
        status = script.split("status_control_plane() {", 1)[1].split("\n}", 1)[0]
        for forbidden in ("systemctl start", "systemctl stop", "docker compose", "install -d", "ufw "):
            self.assertNotIn(forbidden, status)

    def test_installed_day_two_operations_reexec_published_assets(self) -> None:
        script = (ROOT / "scripts/control-plane/lifecycle.sh").read_text(encoding="ascii")
        prefix = script.split('source "$SCRIPT_DIR/common.sh"', 1)[0]
        self.assertIn('REQUESTED_OPERATION" != install', prefix)
        self.assertIn('exec /usr/bin/bash "$INSTALLED_SCRIPT_DIR/lifecycle.sh"', prefix)
        publisher = script.split("install_lifecycle_assets() {", 1)[1].split("\n}", 1)[0]
        for required in (
            "lifecycle.sh",
            "replay-compose.sh",
            "verify-api-proxy-e2e.sh",
            "verify-typescript-sdk-e2e.sh",
            "orchestrator.env.template",
            "orchestrator.service.expected",
        ):
            self.assertIn(required, publisher)
        self.assertIn('"$SCRIPT_DIR/e2e-typescript-sdk"/*', publisher)
        self.assertIn("sdk_source_name_invalid", publisher)
        self.assertIn("sdk_file_count <= 64", publisher)


if __name__ == "__main__":
    unittest.main()
