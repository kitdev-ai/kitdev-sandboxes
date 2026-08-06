from __future__ import annotations

import json
import re
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
LAB = ROOT / "experiments" / "ovh-lab"


class OvhLabFrameworkTests(unittest.TestCase):
    def setUp(self) -> None:
        self.manifest = json.loads((LAB / "stages.json").read_text(encoding="utf-8"))
        self.stage_files = sorted((LAB / "stages").glob("*.sh"))
        self.private_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.private_directory.cleanup)
        self.ssh_config = Path(self.private_directory.name) / "ssh-config"
        self.ssh_config.write_text("Host reviewed-alias\n  BatchMode yes\n", encoding="utf-8")
        self.ssh_config.chmod(0o600)

    def approval_environment(self, config: Path | str | None = None) -> dict[str, str]:
        return {
            "OVH_LAB_TARGET": "reviewed-alias",
            "OVH_LAB_SSH_CONFIG": str(self.ssh_config if config is None else config),
            "PATH": "/usr/bin:/bin",
        }

    def test_manifest_has_the_fixed_complete_stage_sequence(self) -> None:
        stages = self.manifest["stages"]
        self.assertEqual(
            [stage["id"] for stage in stages],
            ["00", "05", "10", "20", "30", "40", "50", "60", "70", "80", "90"],
        )
        self.assertEqual(len(stages), len(self.stage_files))
        for stage in stages:
            self.assertTrue((LAB / stage["script"]).is_file())

    def test_all_shell_files_parse_and_use_strict_mode(self) -> None:
        shell_files = [LAB / "run-stage.sh", LAB / "lib" / "common.sh", *self.stage_files]
        for path in shell_files:
            with self.subTest(path=path.name):
                subprocess.run(["bash", "-n", str(path)], check=True, capture_output=True)
                text = path.read_text(encoding="utf-8")
                self.assertIn("set -Eeuo pipefail", text)

    def test_streamed_stage_bundles_parse(self) -> None:
        common = (LAB / "lib" / "common.sh").read_text(encoding="utf-8")
        for path in self.stage_files:
            text = path.read_text(encoding="utf-8")
            body = text.split("# OVH_LAB_STAGE_BODY", maxsplit=1)[1]
            with self.subTest(path=path.name):
                subprocess.run(
                    ["bash", "-n"],
                    input=common + body,
                    text=True,
                    check=True,
                    capture_output=True,
                )

    def test_each_stage_has_remote_bundle_boundary_and_ack_gate(self) -> None:
        for path in self.stage_files:
            with self.subTest(path=path.name):
                text = path.read_text(encoding="utf-8")
                self.assertEqual(text.count("# OVH_LAB_STAGE_BODY"), 1)
                self.assertIn('lab_require_ack "$@"', text)
                self.assertIn("lab_refuse_production", text)
                self.assertIn("lab_require_supported_platform", text)
                self.assertIn("postconditions", text)
                self.assertIn("rollback", text)

    def test_every_stage_rejects_missing_ack_before_other_work(self) -> None:
        for path in self.stage_files:
            with self.subTest(path=path.name):
                result = subprocess.run(["bash", str(path)], capture_output=True, text=True)
                self.assertEqual(result.returncode, 64)
                self.assertIn("acknowledgement_required", result.stderr)

    def test_blocked_stages_have_no_guessed_mutation_commands(self) -> None:
        blocked = {stage["id"] for stage in self.manifest["stages"] if stage["status"] == "blocked"}
        forbidden = (
            "apt-get install",
            "useradd",
            "usermod",
            "mkfs",
            "mount ",
            "modprobe",
            "sysctl -w",
            "docker run",
            "docker compose up",
            "nft add",
            "git clone",
            "systemctl enable",
            "systemctl start",
        )
        for stage_id in blocked:
            path = next(path for path in self.stage_files if path.name.startswith(stage_id + "-"))
            text = path.read_text(encoding="utf-8")
            with self.subTest(path=path.name):
                self.assertIn("lab_blocked", text)
                for token in forbidden:
                    self.assertNotIn(token, text)

    def test_runner_uses_verified_host_key_and_off_host_evidence(self) -> None:
        text = (LAB / "run-stage.sh").read_text(encoding="utf-8")
        self.assertIn("StrictHostKeyChecking=yes", text)
        self.assertIn("OVH_LAB_KNOWN_HOSTS", text)
        self.assertIn("OVH_LAB_SSH_CONFIG", text)
        self.assertIn('-F "$SSH_CONFIG_SNAPSHOT"', text)
        self.assertIn('ARTIFACTS_ROOT="$SCRIPT_DIR/../../artifacts"', text)
        self.assertIn('RUN_ROOT="$ARTIFACTS_ROOT/ovh-lab"', text)
        self.assertIn("redact()", text)
        self.assertIn("signal.alarm", text)
        self.assertIn("/usr/bin/timeout", text)
        self.assertIn("EVIDENCE_MAX_BYTES=1048576", text)
        self.assertIn("[redacted-fingerprint]", text)
        self.assertIn("[redacted-host]", text)
        self.assertNotIn("StrictHostKeyChecking=no", text)
        self.assertNotIn("accept-new", text)

    def test_approval_is_manifest_selected_and_bundle_bound(self) -> None:
        result = subprocess.run(
            [str(LAB / "run-stage.sh"), "00", "approval"],
            env=self.approval_environment(),
            capture_output=True,
            text=True,
            check=True,
        )
        self.assertRegex(
            result.stdout.strip(),
            r"^DISPOSABLE_OVH_LAB:00:execute:reviewed-alias:[0-9a-f]{64}:[0-9a-f]{64}$",
        )

    def test_ssh_config_rejects_relative_symlink_directory_open_mode_oversize_and_include(self) -> None:
        symlink = Path(self.private_directory.name) / "ssh-config-link"
        symlink.symlink_to(self.ssh_config)
        directory = Path(self.private_directory.name) / "config-directory"
        directory.mkdir()
        open_mode = Path(self.private_directory.name) / "ssh-config-open"
        open_mode.write_text("Host reviewed-alias\n", encoding="utf-8")
        open_mode.chmod(0o640)
        oversized = Path(self.private_directory.name) / "ssh-config-oversized"
        oversized.write_bytes(b"x" * 1048577)
        oversized.chmod(0o600)
        included = Path(self.private_directory.name) / "ssh-config-include"
        included.write_text("Include private-fragment\nHost reviewed-alias\n", encoding="utf-8")
        included.chmod(0o600)
        cases: tuple[Path | str, ...] = (
            self.ssh_config.name,
            symlink,
            directory,
            open_mode,
            oversized,
            included,
        )
        for config in cases:
            result = subprocess.run(
                [str(LAB / "run-stage.sh"), "00", "approval"],
                cwd=self.private_directory.name,
                env=self.approval_environment(config),
                capture_output=True,
                text=True,
            )
            with self.subTest(config=config):
                self.assertEqual(result.returncode, 64)
                self.assertIn("OVH_LAB_SSH_CONFIG must be a stable", result.stderr)
                self.assertEqual(result.stdout, "")

    def test_ssh_config_validation_checks_current_uid_and_no_follow_descriptor(self) -> None:
        text = (LAB / "run-stage.sh").read_text(encoding="utf-8")
        self.assertIn("before.st_uid != os.geteuid()", text)
        self.assertIn("opened_before.st_uid != os.geteuid()", text)
        self.assertIn('hasattr(os, "O_NOFOLLOW")', text)
        self.assertIn("os.O_NOFOLLOW", text)
        self.assertIn("stat.S_IMODE(opened_before.st_mode) & 0o077", text)
        self.assertIn("1048577 - len(content)", text)
        self.assertIn('re.match(br"(?i:include)', text)

    def test_execution_uses_one_private_config_snapshot(self) -> None:
        text = (LAB / "run-stage.sh").read_text(encoding="utf-8")
        run_remote = text.split("run_remote()", maxsplit=1)[1]
        self.assertIn('SSH_CONFIG_SNAPSHOT="$RUN_DIR/private-ssh-config"', text)
        self.assertIn("os.O_EXCL | os.O_NOFOLLOW", text)
        self.assertIn("stat.S_IMODE(snapshot_stat.st_mode) != 0o600", text)
        self.assertIn('-F "$SSH_CONFIG_SNAPSHOT"', run_remote)
        self.assertNotIn('-F "$SSH_CONFIG"', run_remote)

    def test_ssh_config_change_invalidates_prior_approval(self) -> None:
        first = subprocess.run(
            [str(LAB / "run-stage.sh"), "00", "approval"],
            env=self.approval_environment(),
            capture_output=True,
            text=True,
            check=True,
        ).stdout
        self.ssh_config.write_text("Host reviewed-alias\n  BatchMode yes\n  Compression no\n", encoding="utf-8")
        self.ssh_config.chmod(0o600)
        second = subprocess.run(
            [str(LAB / "run-stage.sh"), "00", "approval"],
            env=self.approval_environment(),
            capture_output=True,
            text=True,
            check=True,
        ).stdout
        self.assertNotEqual(first, second)

    def test_manifest_blocked_stage_cannot_generate_approval(self) -> None:
        for stage in self.manifest["stages"]:
            if stage["status"] != "blocked":
                continue
            result = subprocess.run(
                [str(LAB / "run-stage.sh"), stage["id"], "approval"],
                env=self.approval_environment(),
                capture_output=True,
                text=True,
            )
            with self.subTest(stage=stage["id"]):
                self.assertEqual(result.returncode, 20)
                self.assertIn("stage is blocked", result.stderr)
                self.assertEqual(result.stdout, "")

    def test_remote_phases_stream_one_immutable_approved_bundle(self) -> None:
        text = (LAB / "run-stage.sh").read_text(encoding="utf-8")
        run_remote = text.split("run_remote()", maxsplit=1)[1]
        self.assertIn("readonly BUNDLE_CONTENT", text)
        self.assertIn("BUNDLE_CONTENT", run_remote)
        self.assertNotIn("bundle_stage |", run_remote)

    def test_framework_contains_no_literal_endpoint_or_private_key(self) -> None:
        ipv4 = re.compile(r"(?<![0-9])(?:[0-9]{1,3}\.){3}[0-9]{1,3}(?![0-9])")
        private_key = "-----BEGIN " + "PRIVATE KEY-----"
        for path in [LAB / "README.md", LAB / "stages.json", LAB / "run-stage.sh", LAB / "lib" / "common.sh", *self.stage_files]:
            text = path.read_text(encoding="utf-8")
            with self.subTest(path=path.name):
                self.assertIsNone(ipv4.search(text))
                self.assertNotIn(private_key, text)


if __name__ == "__main__":
    unittest.main()
