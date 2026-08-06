from __future__ import annotations

import json
import os
import stat
import unittest
from datetime import UTC, datetime
from pathlib import Path

from kitdev_sandboxes.config import LifecycleMode
from kitdev_sandboxes.preflight import (
    CheckResult,
    CheckStatus,
    DoctorReport,
    FailureCategory,
    HostFacts,
    Severity,
    _parse_cpu_virtualization,
    _parse_nested_guest_support,
    _parse_os_release,
    build_doctor_report,
    collect_host_facts,
    evaluate_host,
    render_text,
    redact_report,
    safe_report_text,
)


def supported_facts(**overrides: object) -> HostFacts:
    values: dict[str, object] = {
        "os_id": "ubuntu",
        "os_name": "Ubuntu",
        "os_version_id": "26.04",
        "architecture": "x86_64",
        "pid1_comm": "systemd",
        "cgroup_v2": True,
        "cpu_virtualization": "svm",
        "kvm_device_exists": True,
        "kvm_device_is_character": True,
        "kvm_device_accessible": True,
        "nested_guest_support": False,
        "evidence": {},
    }
    values.update(overrides)
    return HostFacts(**values)  # type: ignore[arg-type]


class PreflightTests(unittest.TestCase):
    def test_ubuntu_2604_production_passes_lifecycle_but_incomplete_scope_blocks_success(self) -> None:
        report = build_doctor_report(supported_facts(), LifecycleMode.PRODUCTION)
        lifecycle = next(check for check in report.checks if check.check_id == "platform.release_lifecycle")

        self.assertIs(lifecycle.status, CheckStatus.PASS)
        self.assertEqual(report.exit_code, 5)
        self.assertGreater(report.as_dict()["summary"]["unknown"], 0)

    def test_ubuntu_2504_development_and_migration_warn(self) -> None:
        for mode in (LifecycleMode.DEVELOPMENT, LifecycleMode.MIGRATION):
            with self.subTest(mode=mode):
                checks = evaluate_host(supported_facts(os_version_id="25.04"), mode)
                lifecycle = checks[0]
                self.assertIs(lifecycle.status, CheckStatus.WARN)
                self.assertIn("end-of-life", lifecycle.explanation)

    def test_ubuntu_2504_production_is_non_overridable_platform_failure(self) -> None:
        report = build_doctor_report(
            supported_facts(os_version_id="25.04"), LifecycleMode.PRODUCTION
        )

        self.assertIs(report.checks[0].status, CheckStatus.FAIL)
        self.assertEqual(report.exit_code, 3)

    def test_kvm_path_must_be_character_device(self) -> None:
        checks = evaluate_host(supported_facts(kvm_device_is_character=False), LifecycleMode.PRODUCTION)
        kvm = next(check for check in checks if check.check_id == "virtualization.kvm_device")

        self.assertIs(kvm.status, CheckStatus.FAIL)

    def test_failure_exit_precedence_is_deterministic(self) -> None:
        checks = (
            CheckResult(
                "network.conflict",
                CheckStatus.FAIL,
                Severity.BLOCKING,
                "conflict",
                failure_category=FailureCategory.CONFLICT,
            ),
            CheckResult(
                "platform.release",
                CheckStatus.FAIL,
                Severity.BLOCKING,
                "platform",
                failure_category=FailureCategory.PLATFORM,
            ),
            CheckResult(
                "installed.health",
                CheckStatus.FAIL,
                Severity.BLOCKING,
                "health",
                failure_category=FailureCategory.UNHEALTHY,
            ),
        )
        report = DoctorReport("2026-08-06T00:00:00Z", LifecycleMode.PRODUCTION, False, supported_facts(), checks)

        self.assertEqual(report.exit_code, 3)

    def test_json_contract_has_no_config_sources_or_changes(self) -> None:
        report = build_doctor_report(
            supported_facts(),
            LifecycleMode.PRODUCTION,
            now=datetime(2026, 8, 6, tzinfo=UTC),
        ).as_dict(verbose=True)

        self.assertEqual(report["schema_version"], 1)
        self.assertEqual(report["command_mode"], "read-only")
        self.assertNotIn("sources", report)
        self.assertIn("platform_fingerprint", report["host"])
        self.assertEqual(report["changes"], [])
        schema_path = Path(__file__).resolve().parents[2] / "config" / "doctor-report.schema.json"
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
        self.assertEqual(set(report), set(schema["required"]))

    def test_json_and_text_redact_inline_credentials_private_paths_and_controls(self) -> None:
        secret = (
            "Authorization: Basic BASE64 Token=def Cookie: session=abc; second=def\n"
            "Password=hunter2 api_key=ghi X-Amz-Signature=jkl "
            "X-Amz-Credential=credential-value /Users/kit/private\nnext "
            "-----BEGIN PRIVATE KEY-----private-material-----END PRIVATE KEY-----"
        )
        check = CheckResult(
            "test.redaction",
            CheckStatus.WARN,
            Severity.WARNING,
            secret,
            evidence=secret,
        )
        report = DoctorReport(
            "2026-08-06T00:00:00Z",
            LifecycleMode.DEVELOPMENT,
            False,
            supported_facts(),
            (check,),
        )

        serialized = str(report.as_dict(verbose=True))
        rendered = render_text(report, verbose=True)
        for output in (serialized, rendered):
            self.assertNotIn("abc", output)
            self.assertNotIn("hunter2", output)
            self.assertNotIn("BASE64", output)
            self.assertNotIn("private-material", output)
            self.assertNotIn("second=def", output)
            self.assertNotIn("credential-value", output)
            self.assertNotIn("/Users/kit", output)
            self.assertNotIn("\nnext", output)

    def test_redaction_handles_c1_quoted_and_percent_encoded_secrets(self) -> None:
        evidence = (
            'note\x85password="two word secret" '
            "token='another quoted secret' "
            "https://example.invalid/?api%255Fkey%253Dencoded-secret%2526safe%253Dok"
        )

        redacted = safe_report_text(evidence)
        self.assertNotIn("\x85", redacted)
        self.assertNotIn("two word secret", redacted)
        self.assertNotIn("another quoted secret", redacted)
        self.assertNotIn("encoded-secret", redacted)
        self.assertIn("[REDACTED]", redacted)

    def test_double_encoded_sensitive_dictionary_key_is_redacted(self) -> None:
        structured = redact_report({"api%255Fkey": "raw-secret-value"})

        self.assertEqual(structured, {"api%255Fkey": "[REDACTED]"})

    def test_duplicate_os_release_identity_is_unknown(self) -> None:
        self.assertEqual(_parse_os_release("ID=ubuntu\nID=debian\nVERSION_ID=26.04\n"), {})

    def test_cookie_assignment_redacts_the_entire_value(self) -> None:
        redacted = safe_report_text("Cookie=session=abc; second=def")

        self.assertNotIn("session", redacted)
        self.assertNotIn("second", redacted)

    def test_cpu_virtualization_uses_only_named_flag_fields(self) -> None:
        self.assertIsNone(
            _parse_cpu_virtualization("model name: svm optimized processor\nbugs: vmx\n")
        )
        self.assertEqual(_parse_cpu_virtualization("flags: fpu vmx sse\n"), "vmx")
        self.assertEqual(_parse_cpu_virtualization("Features: fp svm aes\n"), "svm")
        self.assertEqual(_parse_cpu_virtualization("flags: fpu sse\n"), "none")

    def test_nested_parameter_models_guest_support_not_host_environment(self) -> None:
        self.assertIs(_parse_nested_guest_support("Y"), True)
        self.assertIs(_parse_nested_guest_support("0"), False)
        self.assertIsNone(_parse_nested_guest_support("unexpected"))

        enabled = evaluate_host(
            supported_facts(nested_guest_support=True), LifecycleMode.PRODUCTION
        )
        enabled_check = next(
            check for check in enabled if check.check_id == "virtualization.nested_guest_support"
        )
        self.assertIs(enabled_check.status, CheckStatus.PASS)
        self.assertIn("does not identify the host as nested", enabled_check.explanation)

        unknown = evaluate_host(
            supported_facts(nested_guest_support=None), LifecycleMode.PRODUCTION
        )
        unknown_check = next(
            check for check in unknown if check.check_id == "virtualization.nested_guest_support"
        )
        self.assertIs(unknown_check.status, CheckStatus.SKIPPED)

    def test_collector_uses_large_cpu_bound_and_character_device_stat(self) -> None:
        limits: dict[str, int] = {}

        def read_text(path: Path, limit: int) -> str | None:
            limits[str(path)] = limit
            values = {
                "/etc/os-release": "ID=ubuntu\nVERSION_ID=26.04\n",
                "/proc/1/comm": "systemd",
                "/proc/cpuinfo": "flags: svm",
            }
            return values.get(str(path))

        character_stat = os.stat_result((stat.S_IFCHR | 0o660, 0, 0, 1, 0, 0, 0, 0, 0, 0))
        facts = collect_host_facts(
            read_text=read_text,
            machine=lambda: "x86_64",
            access=lambda _path, _mode: True,
            stat_path=lambda path: character_stat if str(path) == "/dev/kvm" else None,
        )

        self.assertEqual(limits["/proc/cpuinfo"], 8_388_608)
        self.assertTrue(facts.kvm_device_is_character)


if __name__ == "__main__":
    unittest.main()
