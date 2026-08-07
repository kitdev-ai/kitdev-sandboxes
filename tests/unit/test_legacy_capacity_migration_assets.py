from __future__ import annotations

import importlib.util
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[2]
ANSIBLE = ROOT / "ansible"
SCRIPT = ROOT / "scripts" / "legacy-capacity-migration.sh"


def load_verifier():
    path = ANSIBLE / "files" / "verify_hugepage_pool.py"
    spec = importlib.util.spec_from_file_location("verify_hugepage_pool", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_assignment_finder():
    path = ANSIBLE / "files" / "find_hugepage_assignments.py"
    spec = importlib.util.spec_from_file_location("find_hugepage_assignments", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class LegacyCapacityMigrationAssetTests(unittest.TestCase):
    def test_entrypoint_is_strict_executable_and_parses(self) -> None:
        text = SCRIPT.read_text(encoding="utf-8")
        self.assertTrue(SCRIPT.stat().st_mode & stat.S_IXUSR)
        self.assertIn("set -Eeuo pipefail", text)
        subprocess.run(["bash", "-n", str(SCRIPT)], check=True)

    def test_lock_semantics_distinguish_check_apply_and_remove(self) -> None:
        text = SCRIPT.read_text(encoding="utf-8")
        self.assertIn("check)", text)
        self.assertIn("apply)", text)
        self.assertIn("lifecycle_lock_required_for_removal", text)
        self.assertIn("flock --nonblock 8", text)
        self.assertIn("flock --nonblock 9", text)
        self.assertLess(text.index("flock --nonblock 8"), text.index("install -o root"))

    def test_playbook_is_local_and_uses_only_migration_role(self) -> None:
        play = yaml.safe_load(
            (ANSIBLE / "legacy-capacity-migration.yaml").read_text(encoding="utf-8")
        )[0]
        self.assertEqual(play["hosts"], "localhost")
        self.assertTrue(play["become"])
        self.assertEqual([entry["role"] for entry in play["roles"]], ["legacy_capacity_migration"])

    def test_profile_and_exact_legacy_inputs_are_explicit(self) -> None:
        defaults = yaml.safe_load(
            (
                ANSIBLE
                / "roles"
                / "legacy_capacity_migration"
                / "defaults"
                / "main.yaml"
            ).read_text(encoding="utf-8")
        )
        self.assertEqual(defaults["kitdev_capacity_max_sandbox_memory_mib"], 8192)
        self.assertEqual(defaults["kitdev_capacity_concurrent_hugepage_sandboxes"], 2)
        self.assertEqual(defaults["kitdev_capacity_build_snapshot_headroom_mib"], 8192)
        self.assertEqual(defaults["kitdev_hugepages_min_available_mb_after"], 16384)
        self.assertEqual(defaults["kitdev_legacy_hugepages_prior"], 2048)
        self.assertEqual(
            defaults["kitdev_legacy_hugepage_file"],
            "/etc/sysctl.d/90-kitdev-sandboxes-hugepages.conf",
        )

    def test_role_proves_services_database_idle_state_and_ownership(self) -> None:
        tasks = (
            ANSIBLE / "roles" / "legacy_capacity_migration" / "tasks" / "main.yaml"
        ).read_text(encoding="utf-8")
        self.assertIn("Count nonterminal template builds", tasks)
        self.assertIn("status_group NOT IN ('ready', 'failed')", tasks)
        self.assertIn("Refuse active Firecracker processes", tasks)
        self.assertIn("Require every exact legacy container to be running", tasks)
        self.assertIn("Require one exact legacy hugepage assignment", tasks)
        self.assertIn("find_hugepage_assignments.py", tasks)
        self.assertIn("nlink == 1", tasks)
        self.assertIn("kitdev-sandboxes/legacy-capacity-migration", tasks)

    def test_rollback_metadata_and_owned_removal_are_complete(self) -> None:
        prior = (
            ANSIBLE
            / "roles"
            / "legacy_capacity_migration"
            / "templates"
            / "prior-state.json.j2"
        ).read_text(encoding="utf-8")
        remove = (
            ANSIBLE / "roles" / "legacy_capacity_migration" / "tasks" / "remove.yaml"
        ).read_text(encoding="utf-8")
        self.assertIn('"content_b64"', prior)
        self.assertIn('"sha256"', prior)
        self.assertIn('"vm.nr_hugepages"', prior)
        self.assertIn("Restore the exact pre-adoption hugepage file", remove)
        self.assertIn("Restore the pre-adoption hugepage pool", remove)

    def test_apply_has_injectable_transactional_rollback(self) -> None:
        apply = (
            ANSIBLE / "roles" / "legacy_capacity_migration" / "tasks" / "apply.yaml"
        ).read_text(encoding="utf-8")
        self.assertIn("block:", apply)
        self.assertIn("rescue:", apply)
        self.assertIn("after_persistent_file", apply)
        self.assertIn("after_sysctl", apply)
        self.assertIn("Restore exact legacy file", apply)
        self.assertIn("Verify rollback after incomplete first apply", apply)
        self.assertIn("Remove incomplete migration manifest", apply)

    def test_assignment_finder_does_not_follow_symlinks(self) -> None:
        finder = load_assignment_finder()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            expected = root / "90-owned.conf"
            expected.write_text("vm.nr_hugepages = 2048\n", encoding="ascii")
            (root / "other.conf").write_text("net.ipv4.ip_forward = 1\n", encoding="ascii")
            self.assertEqual(finder.find_assignments(root), [str(expected)])
            (root / "99-linked.conf").symlink_to(expected)
            with self.assertRaisesRegex(ValueError, "symlink"):
                finder.find_assignments(root)

    def test_pool_verifier_requires_fully_free_pages_and_reserve(self) -> None:
        verifier = load_verifier()
        values = {
            "MemAvailable": 36 * 1024 * 1024,
            "HugePages_Total": 12288,
            "HugePages_Free": 12288,
            "HugePages_Rsvd": 0,
            "HugePages_Surp": 0,
            "Hugepagesize": 2048,
        }
        result = verifier.verify_pool(values, 12288, 16384)
        self.assertEqual(result["status"], "pass")
        self.assertEqual(result["mem_available_mib"], 36 * 1024)
        with self.assertRaisesRegex(ValueError, "fully free"):
            verifier.verify_pool({**values, "HugePages_Free": 12287}, 12288, 16384)
        with self.assertRaisesRegex(ValueError, "ordinary available"):
            verifier.verify_pool({**values, "MemAvailable": 15000 * 1024}, 12288, 16384)


if __name__ == "__main__":
    unittest.main()
