from __future__ import annotations

import json
import re
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
LAB = ROOT / "experiments" / "ovh-lab"


class OvhLabFrameworkTests(unittest.TestCase):
    def setUp(self) -> None:
        self.manifest = json.loads((LAB / "stages.json").read_text(encoding="utf-8"))
        self.stage_files = sorted((LAB / "stages").glob("*.sh"))

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
            env={"OVH_LAB_TARGET": "reviewed-alias", "PATH": "/usr/bin:/bin"},
            capture_output=True,
            text=True,
            check=True,
        )
        self.assertRegex(
            result.stdout.strip(),
            r"^DISPOSABLE_OVH_LAB:00:execute:reviewed-alias:[0-9a-f]{64}$",
        )

    def test_manifest_blocked_stage_cannot_generate_approval(self) -> None:
        for stage in self.manifest["stages"]:
            if stage["status"] != "blocked":
                continue
            result = subprocess.run(
                [str(LAB / "run-stage.sh"), stage["id"], "approval"],
                env={"OVH_LAB_TARGET": "reviewed-alias", "PATH": "/usr/bin:/bin"},
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
