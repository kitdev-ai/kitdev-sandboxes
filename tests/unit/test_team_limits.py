from __future__ import annotations

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "control-plane" / "set-team-limits.sh"


class TeamLimitsScriptTests(unittest.TestCase):
    def setUp(self) -> None:
        self.text = SCRIPT.read_text(encoding="ascii")

    def test_is_executable_and_strict(self) -> None:
        self.assertTrue(SCRIPT.exists())
        self.assertEqual(SCRIPT.stat().st_mode & 0o111, 0o111)
        self.assertIn("set -Eeuo pipefail", self.text)

    def test_serializes_against_both_lifecycle_locks(self) -> None:
        for lock in ("control-plane-lifecycle.lock", "typescript-sdk-e2e.lock"):
            self.assertIn(lock, self.text)
        self.assertIn("flock --nonblock 8", self.text)
        self.assertIn("flock --nonblock 9", self.text)
        self.assertIn("lifecycle_operation_running", self.text)

    def test_refuses_active_workload_before_mutating(self) -> None:
        apply_section = self.text.split('if [[ "$mode" == check ]]', 1)[1]
        self.assertIn("team_limits_firecracker_running", apply_section)
        self.assertIn("team_limits_build_running", apply_section)
        # Both guards must precede the write.
        self.assertLess(
            apply_section.index("team_limits_build_running"),
            apply_section.index("INSERT INTO public.project_limits"),
        )

    def test_hugepage_pool_bounds_worst_case_concurrency(self) -> None:
        # Concurrent sandbox memory comes from the persistent HugeTLB pool, so
        # the tool must refuse a worst case above it unless told explicitly.
        self.assertIn("hugepage_pool_mib", self.text)
        self.assertIn("worst=$((sandboxes * ram))", self.text)
        self.assertIn("team_limits_exceed_hugepage_pool", self.text)
        self.assertIn("--allow-oversubscription", self.text)

    def test_records_prior_state_create_once(self) -> None:
        self.assertIn("$PRIOR_DIR/$slug.prior", self.text)
        self.assertIn('if [[ ! -e "$PRIOR_DIR/$slug.prior" ]]', self.text)
        self.assertIn('ensure_directory "$PRIOR_DIR" root root 700', self.text)

    def test_invalidates_auth_cache_and_verifies(self) -> None:
        self.assertIn("auth:team:*", self.text)
        self.assertIn("auth_cache_invalidation_failed", self.text)
        self.assertIn("team_limits_verify_failed", self.text)
        self.assertLess(
            self.text.index("auth:team:*"),
            self.text.index("team_limits_verify_failed"),
        )

    def test_check_mode_does_not_mutate(self) -> None:
        check_branch = self.text.split('if [[ "$mode" == check ]]; then', 1)[1].split(
            "fi", 1
        )[0]
        for forbidden in ("INSERT", "UPDATE", "DEL ", "install -o root"):
            self.assertNotIn(forbidden, check_branch)

    def test_inputs_are_bounded(self) -> None:
        self.assertIn("^[a-z0-9][a-z0-9-]{0,62}$", self.text)
        self.assertIn("^[1-9][0-9]{0,6}$", self.text)
        self.assertIn("team_slug_not_unique", self.text)


if __name__ == "__main__":
    unittest.main()
