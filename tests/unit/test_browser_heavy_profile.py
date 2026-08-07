from __future__ import annotations

import hashlib
import json
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
CLIENT = ROOT / "scripts" / "control-plane" / "e2e-typescript-sdk"
RUNNER = ROOT / "scripts" / "control-plane" / "verify-typescript-sdk-browser-template.sh"
PROVISIONER = ROOT / "scripts" / "control-plane" / "provision-browser-heavy-profile.sh"


class BrowserHeavyProfileTests(unittest.TestCase):
    def test_resource_profiles_are_exact_and_distinct(self) -> None:
        standard = json.loads(
            (CLIENT / "browser-resource-profiles" / "standard.json").read_text(encoding="ascii")
        )
        heavy = json.loads(
            (CLIENT / "browser-resource-profiles" / "heavy.json").read_text(encoding="ascii")
        )
        self.assertEqual(
            standard,
            {
                "schemaVersion": 1,
                "name": "standard",
                "cpuCount": 2,
                "memoryMB": 2048,
                "freeDiskSizeMB": 512,
                "minimumGuestAvailableDiskMB": 0,
            },
        )
        self.assertEqual(
            heavy,
            {
                "schemaVersion": 1,
                "name": "heavy",
                "cpuCount": 2,
                "memoryMB": 8192,
                "freeDiskSizeMB": 16384,
                "minimumGuestAvailableDiskMB": 15000,
            },
        )

    def test_runner_pins_profiles_and_keeps_standard_default(self) -> None:
        runner = RUNNER.read_text(encoding="ascii")
        for name in ("standard", "heavy"):
            profile = CLIENT / "browser-resource-profiles" / f"{name}.json"
            digest = hashlib.sha256(profile.read_bytes()).hexdigest()
            self.assertIn(f"={digest}", runner)
        self.assertIn("profile=standard", runner)
        self.assertIn('elif [[ $# == 4 && "$1" == --resource-profile', runner)
        self.assertIn("huge_total >= 12288", runner)
        self.assertIn("huge_free >= 12288", runner)
        self.assertIn("available_kib >= 16777216", runner)
        self.assertIn("kitdev-browser-heavy-team", runner)
        self.assertIn("2\\|8192\\|16384", runner)

    def test_template_consumes_profile_without_inventing_sdk_disk_option(self) -> None:
        source = (CLIENT / "browser-template.ts").read_text(encoding="ascii")
        self.assertIn('readFile("/run/config/browser-resource-profile.json"', source)
        self.assertIn("cpuCount: profile.cpuCount", source)
        self.assertIn("memoryMB: profile.memoryMB", source)
        self.assertNotIn("diskSizeMB: profile", source)
        self.assertIn("availableMB >= profile.minimumGuestAvailableDiskMB", source)

    def test_provisioner_uses_dedicated_exact_project_limits(self) -> None:
        source = PROVISIONER.read_text(encoding="ascii")
        self.assertIn("kitdev-browser-heavy-team", source)
        self.assertIn("VALUES (:'team_id'::uuid, 1, 1, 1, 2, 8192, 16384, 7, 16384, 25600)", source)
        self.assertIn("ON CONFLICT (team_id) DO UPDATE SET", source)
        self.assertIn("api_key_hash", source)
        self.assertNotIn("print(value)", source)

    def test_live_container_discovery_is_untruncated_and_legacy_database_aware(self) -> None:
        for path in (RUNNER, PROVISIONER):
            source = path.read_text(encoding="ascii")
            self.assertIn("docker ps --no-trunc --quiet --filter name='^/kitdev-postgres$'", source)
            self.assertIn("docker ps --no-trunc --quiet --filter name='^/kitdev-redis$'", source)
            self.assertIn("for user in kitdev postgres", source)
            self.assertIn("to_regclass('public.teams')", source)

    def test_shell_assets_parse(self) -> None:
        for script in (RUNNER, PROVISIONER):
            completed = subprocess.run(
                ["bash", "-n", str(script)],
                cwd=ROOT,
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)


if __name__ == "__main__":
    unittest.main()
