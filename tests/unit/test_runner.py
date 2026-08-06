from __future__ import annotations

import errno
import json
import os
import signal
import sys
import time
import unittest
from pathlib import Path
from unittest import mock

from kitdev_sandboxes.runner import (
    MAX_ARG_BYTES,
    MAX_NORMALIZED_EVIDENCE_BYTES,
    Command,
    CommandOutcome,
    CommandRunner,
)


PYTHON = Path(sys.executable)
SAFE_CWD = Path("/")


def python_command(
    source: str,
    *,
    timeout: float = 2.0,
    grace: float = 0.1,
    stdout_limit: int = 262_144,
    stderr_limit: int = 262_144,
) -> Command:
    return Command(
        (str(PYTHON), "-c", source),
        cwd=SAFE_CWD,
        timeout_seconds=timeout,
        termination_grace_seconds=grace,
        stdout_limit_bytes=stdout_limit,
        stderr_limit_bytes=stderr_limit,
    )


class CommandRunnerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.runner = CommandRunner()

    def test_success_captures_both_streams_and_closes_stdin(self) -> None:
        result = self.runner.run(
            python_command(
                "import os; "
                "print('stdin=' + repr(os.read(0, 1))); "
                "os.write(2, b'stderr-output')"
            )
        )

        self.assertIs(result.outcome, CommandOutcome.SUCCESS)
        self.assertTrue(result.succeeded)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout.text, "stdin=b''\n")
        self.assertEqual(result.stderr.text, "stderr-output")
        self.assertFalse(result.output_truncated)

    def test_arguments_are_never_interpreted_by_a_shell(self) -> None:
        adversarial_argument = "$(printf shell-expanded); echo second-command\nnext-line"
        result = self.runner.run(
            Command(("/bin/echo", adversarial_argument), cwd=SAFE_CWD, timeout_seconds=1)
        )

        self.assertIs(result.outcome, CommandOutcome.SUCCESS)
        self.assertEqual(
            result.stdout.text,
            "$(printf shell-expanded); echo second-command\nnext-line\n",
        )
        self.assertEqual(
            result.argv[1],
            "$(printf shell-expanded); echo second-command\\x0anext-line",
        )

    def test_environment_is_fixed_and_does_not_inherit_secrets(self) -> None:
        old_secret = os.environ.get("KITDEV_RUNNER_TEST_SECRET")
        os.environ["KITDEV_RUNNER_TEST_SECRET"] = "must-not-be-inherited"
        try:
            result = self.runner.run(
                python_command(
                    "import json, os; print(json.dumps(dict(os.environ), sort_keys=True))"
                )
            )
        finally:
            if old_secret is None:
                os.environ.pop("KITDEV_RUNNER_TEST_SECRET", None)
            else:
                os.environ["KITDEV_RUNNER_TEST_SECRET"] = old_secret

        environment = json.loads(result.stdout.text)
        self.assertNotIn("KITDEV_RUNNER_TEST_SECRET", environment)
        self.assertEqual(environment["LC_ALL"], "C")
        self.assertEqual(environment["LANG"], "C")
        self.assertEqual(environment["PATH"], "/usr/sbin:/usr/bin:/sbin:/bin")
        self.assertEqual(environment["HOME"], "/dev/null")
        self.assertEqual(environment["TMPDIR"], "/dev/null")

    def test_nonzero_and_signal_are_distinct(self) -> None:
        nonzero = self.runner.run(python_command("raise SystemExit(23)"))
        signaled = self.runner.run(
            python_command("import os, signal; os.kill(os.getpid(), signal.SIGTERM)")
        )

        self.assertIs(nonzero.outcome, CommandOutcome.NONZERO)
        self.assertEqual(nonzero.returncode, 23)
        self.assertIsNone(nonzero.termination_signal)
        self.assertIs(signaled.outcome, CommandOutcome.SIGNALED)
        self.assertEqual(signaled.termination_signal, signal.SIGTERM)
        self.assertFalse(signaled.timed_out)

    def test_missing_executable_is_a_bounded_result(self) -> None:
        result = self.runner.run(
            Command(
                ("/definitely/not/a/kitdev-executable",),
                cwd=SAFE_CWD,
                timeout_seconds=1,
            )
        )

        self.assertIs(result.outcome, CommandOutcome.MISSING)
        self.assertTrue(result.missing_executable)
        self.assertIsNone(result.returncode)
        self.assertEqual(result.error_message, "executable was not found")

    def test_missing_working_directory_is_not_misreported_as_missing_executable(self) -> None:
        result = self.runner.run(
            Command(
                (str(PYTHON), "-c", "pass"),
                cwd=Path("/definitely/missing/kitdev-runner-cwd"),
                timeout_seconds=1,
            )
        )

        self.assertIs(result.outcome, CommandOutcome.SPAWN_ERROR)
        self.assertFalse(result.missing_executable)
        self.assertEqual(result.error_message, "working directory was not found")

    def test_permission_denied_is_a_bounded_result(self) -> None:
        result = self.runner.run(
            Command(("/etc/hosts",), cwd=SAFE_CWD, timeout_seconds=1)
        )

        self.assertIs(result.outcome, CommandOutcome.PERMISSION_DENIED)
        self.assertTrue(result.permission_denied)
        self.assertIsNone(result.returncode)

    def test_timeout_terminates_a_sleeping_process_group(self) -> None:
        started = time.monotonic()
        result = self.runner.run(python_command("import time; time.sleep(60)", timeout=0.1))

        self.assertIs(result.outcome, CommandOutcome.TIMEOUT)
        self.assertTrue(result.timed_out)
        self.assertEqual(result.termination_signal, signal.SIGTERM)
        self.assertLess(time.monotonic() - started, 2)

    def test_timeout_escalates_when_process_ignores_term(self) -> None:
        source = (
            "import signal, time; "
            "signal.signal(signal.SIGTERM, signal.SIG_IGN); time.sleep(60)"
        )
        result = self.runner.run(python_command(source, timeout=0.15, grace=0.05))

        self.assertIs(result.outcome, CommandOutcome.TIMEOUT)
        self.assertEqual(result.termination_signal, signal.SIGKILL)
        self.assertLess(result.duration_seconds, 2)

    def test_descendant_holding_inherited_pipes_is_killed_at_timeout(self) -> None:
        descendant = (
            "import signal,time; "
            "signal.signal(signal.SIGTERM,signal.SIG_IGN); time.sleep(60)"
        )
        source = (
            "import subprocess, sys; "
            f"subprocess.Popen([sys.executable, '-c', {descendant!r}]); "
            "print('parent-exited')"
        )
        started = time.monotonic()
        result = self.runner.run(python_command(source, timeout=0.15, grace=0.05))

        self.assertIs(result.outcome, CommandOutcome.TIMEOUT)
        self.assertEqual(result.stdout.text, "parent-exited\n")
        self.assertLess(time.monotonic() - started, 2)

    def test_setsid_escape_cannot_hold_runner_pipes_past_hard_bound(self) -> None:
        descendant = "import time; time.sleep(1.6)"
        source = (
            "import subprocess, sys, time; "
            "child=subprocess.Popen("
            f"[sys.executable, '-c', {descendant!r}], start_new_session=True); "
            "print(child.pid, flush=True); time.sleep(60)"
        )
        result = self.runner.run(python_command(source, timeout=0.05, grace=0.05))

        self.assertIs(result.outcome, CommandOutcome.TIMEOUT)
        self.assertLess(result.duration_seconds, 1.4)
        escaped_pid = int(result.stdout.text.strip())
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            try:
                os.kill(escaped_pid, 0)
            except ProcessLookupError:
                break
            time.sleep(0.05)
        else:
            self.fail("short-lived setsid descendant remained after its deadline")

    def test_cleanup_signal_errors_are_bounded_results_not_exceptions(self) -> None:
        errors = (PermissionError("denied"), OSError(errno.EIO, "injected"))
        for error in errors:
            with self.subTest(error=type(error).__name__):
                with mock.patch("kitdev_sandboxes.runner.os.killpg", side_effect=error):
                    result = self.runner.run(
                        python_command("import time; time.sleep(0.2)", timeout=0.05, grace=0.02)
                    )

                self.assertIs(result.outcome, CommandOutcome.TIMEOUT)
                self.assertTrue(result.cleanup_error)
                self.assertLess(result.duration_seconds, 1)

    def test_simultaneous_floods_are_drained_and_independently_capped(self) -> None:
        source = (
            "import os, threading\n"
            "def write_all(fd, data):\n"
            " while data:\n"
            "  data=data[os.write(fd, data):]\n"
            "a=threading.Thread(target=write_all, args=(1, b'A'*524288))\n"
            "b=threading.Thread(target=write_all, args=(2, b'B'*393216))\n"
            "a.start(); b.start(); a.join(); b.join()\n"
        )
        result = self.runner.run(
            python_command(source, stdout_limit=1_001, stderr_limit=777)
        )

        self.assertIs(result.outcome, CommandOutcome.SUCCESS)
        self.assertEqual(result.stdout.bytes_captured, 1_001)
        self.assertEqual(result.stderr.bytes_captured, 777)
        self.assertEqual(result.stdout.bytes_discarded, 524_288 - 1_001)
        self.assertEqual(result.stderr.bytes_discarded, 393_216 - 777)
        self.assertTrue(result.stdout.truncated)
        self.assertTrue(result.stderr.truncated)

    def test_invalid_utf8_and_terminal_controls_are_deterministic_and_safe(self) -> None:
        source = (
            "import os; "
            "os.write(1, "
            "b'bad\\xff\\x00\\x1b[31mred\\rnext\\xc2\\x85'"
            "b'\\x1b]0;title\\x07\\xe2\\x80\\xaeend\\n')"
        )
        first = self.runner.run(python_command(source))
        second = self.runner.run(python_command(source))

        expected = (
            "bad\ufffd\\x00\\x1b[31mred\\x0dnext\\x85"
            "\\x1b]0;title\\x07\\u202eend\n"
        )
        self.assertEqual(first.stdout.text, expected)
        self.assertEqual(second.stdout.text, expected)
        self.assertNotIn("\x00", first.stdout.text)
        self.assertNotIn("\x1b", first.stdout.text)
        self.assertNotIn("\r", first.stdout.text)

    def test_zero_byte_limit_discards_without_buffering(self) -> None:
        result = self.runner.run(
            python_command("import os; os.write(1, b'x'*10000)", stdout_limit=0)
        )

        self.assertIs(result.outcome, CommandOutcome.SUCCESS)
        self.assertEqual(result.stdout.text, "")
        self.assertEqual(result.stdout.bytes_captured, 0)
        self.assertEqual(result.stdout.bytes_discarded, 10_000)
        self.assertTrue(result.stdout.truncated)

    def test_normalized_evidence_has_an_independent_rendered_byte_cap(self) -> None:
        result = self.runner.run(
            python_command(
                "import os; os.write(1, b'\\x00'*300000)",
                stdout_limit=300_000,
            )
        )

        self.assertIs(result.outcome, CommandOutcome.SUCCESS)
        self.assertEqual(result.stdout.bytes_captured, 300_000)
        self.assertEqual(result.stdout.bytes_discarded, 0)
        self.assertEqual(result.stdout.normalized_bytes, MAX_NORMALIZED_EVIDENCE_BYTES)
        self.assertEqual(
            len(result.stdout.text.encode("utf-8")),
            MAX_NORMALIZED_EVIDENCE_BYTES,
        )
        self.assertTrue(result.stdout.normalized_truncated)
        self.assertTrue(result.stdout.truncated)

    def test_stream_read_error_never_becomes_eof_success(self) -> None:
        injected = False

        def fail_once(file_descriptor: int) -> bytes:
            nonlocal injected
            if not injected:
                injected = True
                raise OSError(errno.EIO, "injected read failure")
            return os.read(file_descriptor, 65_536)

        with mock.patch("kitdev_sandboxes.runner._read_pipe", side_effect=fail_once):
            result = self.runner.run(python_command("print('untrusted-output')"))

        self.assertIs(result.outcome, CommandOutcome.IO_ERROR)
        self.assertTrue(result.io_error)
        self.assertTrue(result.stdout.read_error)
        self.assertTrue(result.stdout.truncated)
        self.assertEqual(result.error_message, "subprocess stream read failed")

    def test_command_policy_rejects_unbounded_or_ambiguous_inputs(self) -> None:
        with self.assertRaisesRegex(TypeError, "exact tuple"):
            Command(["true"], cwd=SAFE_CWD)  # type: ignore[arg-type]
        with self.assertRaisesRegex(ValueError, "argv must not be empty"):
            Command((), cwd=SAFE_CWD)
        with self.assertRaisesRegex(ValueError, r"argv\[0\]"):
            Command(("",), cwd=SAFE_CWD)
        with self.assertRaisesRegex(ValueError, "NUL"):
            Command(("bad\x00argument",), cwd=SAFE_CWD)
        with self.assertRaisesRegex(ValueError, "valid UTF-8"):
            Command(("bad\ud800argument",), cwd=SAFE_CWD)
        with self.assertRaisesRegex(ValueError, "absolute"):
            Command(("true",), cwd=Path("relative"))
        with self.assertRaisesRegex(ValueError, "finite"):
            Command(("true",), cwd=SAFE_CWD, timeout_seconds=float("inf"))
        with self.assertRaisesRegex(ValueError, "at most"):
            Command(("true",), cwd=SAFE_CWD, termination_grace_seconds=10)
        with self.assertRaisesRegex(ValueError, "at most"):
            Command(("x" * (MAX_ARG_BYTES + 1),), cwd=SAFE_CWD)


if __name__ == "__main__":
    unittest.main()
