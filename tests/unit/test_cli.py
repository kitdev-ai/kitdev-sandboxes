from __future__ import annotations

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from kitdev_sandboxes.cli import main
from kitdev_sandboxes.preflight import HostFacts


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
                ["doctor", "--json", "--dry-run", "--non-interactive", "--lifecycle-mode", "migration"],
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


if __name__ == "__main__":
    unittest.main()
