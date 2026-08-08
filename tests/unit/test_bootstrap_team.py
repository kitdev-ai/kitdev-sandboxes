from __future__ import annotations

import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "control-plane" / "bootstrap-team.sh"
SEED = ROOT / "scripts" / "control-plane" / "seed-local-template.sh"
LIFECYCLE = ROOT / "scripts" / "control-plane" / "lifecycle.sh"


class BootstrapTeamTests(unittest.TestCase):
    def setUp(self) -> None:
        self.text = SCRIPT.read_text(encoding="ascii")

    def test_is_executable_and_strict(self) -> None:
        self.assertTrue(SCRIPT.exists())
        self.assertEqual(SCRIPT.stat().st_mode & 0o111, 0o111)
        self.assertIn("set -Eeuo pipefail", self.text)

    def test_parses(self) -> None:
        completed = subprocess.run(
            ["bash", "-n", str(SCRIPT)], capture_output=True, text=True, check=False
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)

    def test_resolves_postgres_through_the_shared_resolver(self) -> None:
        # Must match a fresh Compose deployment, not only the legacy bare name.
        self.assertIn("control_plane_container postgres", self.text)
        self.assertNotIn("--filter name=", self.text)

    def test_is_idempotent_and_verifies_end_state(self) -> None:
        self.assertIn("ON CONFLICT (slug) DO NOTHING", self.text)
        self.assertIn("result=unchanged", self.text)
        # A pre-existing row that is blocked, banned or on the wrong tier must
        # fail rather than be reported usable.
        self.assertIn("is_blocked = FALSE AND is_banned = FALSE", self.text)
        self.assertIn("team_state_invalid", self.text)

    def test_check_mode_does_not_mutate(self) -> None:
        branch = self.text.split('if [[ "$mode" == check ]]; then', 1)[1].split("fi", 1)[0]
        for forbidden in ("INSERT", "COMMIT", "DELETE", "UPDATE"):
            self.assertNotIn(forbidden, branch)

    def test_slug_is_bounded(self) -> None:
        self.assertIn("^[a-z0-9][a-z0-9-]{0,62}$", self.text)
        self.assertIn("team_slug_invalid", self.text)


class SeedSkipTests(unittest.TestCase):
    def setUp(self) -> None:
        self.text = SEED.read_text(encoding="ascii")

    def test_absent_seed_source_skips_rather_than_failing(self) -> None:
        # The fixture only ever existed on the original lab host, so its absence
        # is the normal case and must not fail a fresh install.
        self.assertIn("result=skipped reason=no_seed_source", self.text)
        self.assertNotIn("source_template_missing", self.text)

    def test_untrusted_seed_source_still_refuses(self) -> None:
        self.assertIn("source_template_untrusted", self.text)
        self.assertIn("source_template_invalid", self.text)

    def test_skip_precedes_the_byte_exact_hash_assertions(self) -> None:
        # Otherwise a fresh host would still have to satisfy a hash captured on
        # one machine before it could skip a step it does not need.
        self.assertLess(
            self.text.index("no_seed_source"),
            self.text.index("copy_build_missing"),
        )


class InstallSequenceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.text = LIFECYCLE.read_text(encoding="ascii")

    def test_team_bootstrap_runs_after_schema_and_before_seed(self) -> None:
        install = self.text.split("install_control_plane() {", 1)[1].split("\n}", 1)[0]
        self.assertIn("bootstrap-team.sh", install)
        self.assertLess(install.index("up_control_plane"), install.index("bootstrap-team.sh"))
        self.assertLess(
            install.index("bootstrap-team.sh"), install.index("seed-local-template.sh")
        )

    def test_bootstrap_team_is_published_to_opt(self) -> None:
        assets = self.text.split("install_lifecycle_assets() {", 1)[1].split("\n}", 1)[0]
        self.assertIn("bootstrap-team.sh", assets)

    def test_install_reports_that_no_template_exists(self) -> None:
        self.assertIn("note=no-template-installed", self.text)


if __name__ == "__main__":
    unittest.main()
