from __future__ import annotations

import hashlib
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
PATCH = ROOT / "patches" / "e2b-infra" / "882a3b4-host-admission.patch"
BUILD = ROOT / "scripts" / "control-plane" / "build-orchestrator.sh"
PREFLIGHT = ROOT / "scripts" / "control-plane" / "preflight-orchestrator.sh"
CONVERGE = ROOT / "scripts" / "control-plane" / "converge-admission-policy.sh"
ENVIRONMENT = ROOT / "systemd" / "orchestrator.env.template"


class HostAdmissionTests(unittest.TestCase):
    def test_runtime_profile_is_exact(self) -> None:
        values = dict(
            line.split("=", 1)
            for line in ENVIRONMENT.read_text(encoding="ascii").splitlines()
        )
        self.assertEqual(values["NBD_POOL_SIZE"], "4")
        self.assertEqual(values["KITDEV_MAX_LIVE_SANDBOXES"], "1")
        self.assertEqual(values["KITDEV_MAX_CONCURRENT_STARTS"], "1")
        self.assertEqual(values["KITDEV_MAX_CONCURRENT_BUILDS"], "1")
        self.assertEqual(values["KITDEV_MAX_VCPU"], "2")
        self.assertEqual(values["KITDEV_MAX_RAM_MB"], "8192")
        self.assertEqual(values["KITDEV_MAX_DISK_MB"], "25600")

    def test_patch_fails_closed_and_caps_remote_flags(self) -> None:
        source = PATCH.read_text(encoding="ascii")
        for name in (
            "KITDEV_MAX_LIVE_SANDBOXES",
            "KITDEV_MAX_CONCURRENT_STARTS",
            "KITDEV_MAX_CONCURRENT_BUILDS",
            "KITDEV_MAX_VCPU",
            "KITDEV_MAX_RAM_MB",
            "KITDEV_MAX_DISK_MB",
        ):
            self.assertIn(name, source)
        self.assertIn("required host admission setting %s is missing", source)
        self.assertIn(
            "min(s.featureFlags.IntFlag(ctx, featureflags.MaxSandboxesPerNode), "
            "s.admission.MaxLiveSandboxes)",
            source,
        )
        self.assertIn("min(flag, s.admission.MaxConcurrentStarts)", source)

    def test_patch_has_atomic_slot_and_upstream_go_tests(self) -> None:
        source = PATCH.read_text(encoding="ascii")
        self.assertIn("func Acquire(counter *atomic.Int64, limit int64)", source)
        self.assertIn("released.CompareAndSwap(false, true)", source)
        self.assertIn("TestAcquireHonorsBoundaryUnderContention", source)
        self.assertIn("TestReleaseIsIdempotent", source)
        self.assertIn("TestLoadRequiresEverySetting", source)
        self.assertIn("releaseBuildSlot, acquired := admission.Acquire", source)
        self.assertIn("defer backgroundRelease()", source)

    def test_build_isolated_patch_and_manifest_binding(self) -> None:
        source = BUILD.read_text(encoding="ascii")
        digest = hashlib.sha256(PATCH.read_bytes()).hexdigest()
        self.assertRegex(digest, r"^[0-9a-f]{64}$")
        self.assertIn('cp --archive --reflink=auto -- "$KITDEV_INFRA_ROOT/packages/."', source)
        self.assertIn("apply --no-index --check", source)
        self.assertIn('"schema_version": 2', source)
        self.assertIn('"patch_sha256": sys.argv[4]', source)
        self.assertIn('"max_concurrent_builds": 1', source)

    def test_preflight_binds_manifest_environment_and_capacity(self) -> None:
        source = PREFLIGHT.read_text(encoding="ascii")
        self.assertIn('document.get("schema_version") != 2', source)
        self.assertIn('hashlib.sha256(patch.read_bytes()).hexdigest()', source)
        self.assertIn('limits["KITDEV_MAX_LIVE_SANDBOXES"]', source)
        self.assertIn('2 * limits["KITDEV_MAX_CONCURRENT_BUILDS"]', source)
        self.assertIn('meminfo.get("HugePages_Free", 0) < required_pages', source)

    def test_team_policy_convergence_is_locked_and_bounded(self) -> None:
        source = CONVERGE.read_text(encoding="ascii")
        self.assertIn("control-plane-lifecycle.lock", source)
        self.assertIn("typescript-sdk-e2e.lock", source)
        self.assertIn("admission_firecracker_running", source)
        self.assertIn("status_group IN ('pending', 'in_progress')", source)
        self.assertIn("LEAST(max_vcpu, 2)", source)
        self.assertIn("LEAST(max_ram_mb, 8192)", source)
        self.assertIn("LEAST(max_disk_size_mb, 25600)", source)
        self.assertIn("redis-cli --raw --scan --pattern 'auth:team:*'", source)

    def test_shell_assets_parse(self) -> None:
        for script in (BUILD, PREFLIGHT, CONVERGE):
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
