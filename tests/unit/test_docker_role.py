from __future__ import annotations

import subprocess
import sys
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
ROLE = ROOT / "ansible" / "roles" / "docker"
PREFLIGHT = ROOT / "ansible" / "roles" / "preflight"
VERIFIER = ROOT / "ansible" / "files" / "verify_openpgp_fingerprint.py"


class DockerRoleTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tasks = yaml.safe_load((ROLE / "tasks" / "main.yaml").read_text())
        self.defaults = yaml.safe_load((ROLE / "defaults" / "main.yaml").read_text())

    def test_every_package_is_pinned_to_an_exact_version(self) -> None:
        packages = self.defaults["kitdev_docker_packages"]
        names = [item["name"] for item in packages]
        # buildx and compose are required by the build steps and every
        # control-plane operation; omitting them passes a naive check and fails
        # later with no reason code.
        for required in ("docker-ce", "containerd.io", "docker-buildx-plugin", "docker-compose-plugin"):
            self.assertIn(required, names)
        for item in packages:
            self.assertRegex(item["version"], r"^[0-9].*|^5:[0-9].*")

    def test_foreign_docker_is_refused_unless_override_is_explicit(self) -> None:
        self.assertIs(self.defaults["kitdev_docker_override"], False)
        guard = next(t for t in self.tasks if t["name"].startswith("Refuse to adopt"))
        self.assertIn("kitdev_docker_override", str(guard))
        # The refusal must precede anything that mutates the host.
        names = [t["name"] for t in self.tasks]
        self.assertLess(
            names.index(guard["name"]),
            names.index("Install pinned Docker Engine packages"),
        )

    def test_key_fingerprint_is_verified_before_the_source_is_trusted(self) -> None:
        names = [t["name"] for t in self.tasks]
        self.assertLess(
            names.index("Verify the Docker signing key fingerprint"),
            names.index("Install the project-owned Docker APT source"),
        )
        self.assertRegex(self.defaults["kitdev_docker_key_fingerprint"], r"^[0-9A-F]{40}$")

    def test_project_owned_paths_only(self) -> None:
        # The host-change policy forbids editing files another package owns.
        for key in ("kitdev_docker_keyring_path", "kitdev_docker_source_path"):
            self.assertIn("kitdev-sandboxes", self.defaults[key])
        self.assertNotIn("daemon.json", (ROLE / "tasks" / "main.yaml").read_text())

    def test_packages_are_held_against_unattended_upgrades(self) -> None:
        self.assertTrue(
            any("dpkg_selections" in str(task) for task in self.tasks),
            "pins must be held or unattended upgrades will move them",
        )

    def test_installed_versions_are_verified_against_the_pins(self) -> None:
        self.assertTrue(any(t["name"].startswith("Confirm every installed") for t in self.tasks))


class StorageVerificationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tasks = yaml.safe_load((PREFLIGHT / "tasks" / "main.yaml").read_text())
        self.defaults = yaml.safe_load((PREFLIGHT / "defaults" / "main.yaml").read_text())

    def test_storage_is_verified_not_created(self) -> None:
        text = (PREFLIGHT / "tasks" / "main.yaml").read_text()
        # mkfs on a misidentified device is unrecoverable, so the operator
        # creates and mounts the filesystem and this role only checks it.
        for destructive in ("mkfs", "parted", "sgdisk", "wipefs", "fdisk"):
            self.assertNotIn(destructive, text)
        self.assertTrue(any("findmnt" in str(task) for task in self.tasks))

    def test_data_root_must_be_a_separate_device(self) -> None:
        guard = next(
            t for t in self.tasks if t["name"].startswith("Require the data filesystem")
        )
        conditions = str(guard["ansible.builtin.assert"]["that"])
        self.assertIn("kitdev_data_fs.source != kitdev_root_fs.source", conditions)
        # findmnt changed JSON key casing across util-linux releases, so the
        # output is normalised rather than read with one release's casing.
        text = (PREFLIGHT / "tasks" / "main.yaml").read_text()
        self.assertIn("map('lower')", text)
        self.assertNotIn(".SOURCE", text)

    def test_minimum_capacity_accepts_a_real_two_terabyte_disk(self) -> None:
        floor = int(self.defaults["kitdev_data_minimum_bytes"])
        # A nominal 2 TB disk reports about 1.97e12 bytes once formatted, so a
        # literal 2e12 floor would reject exactly the hardware it targets.
        self.assertLess(floor, 1_970_000_000_000)
        self.assertGreater(floor, 1_500_000_000_000)


class FingerprintVerifierTests(unittest.TestCase):
    def _run(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(VERIFIER), *args], capture_output=True, text=True, check=False
        )

    def test_rejects_a_non_key_file(self) -> None:
        import tempfile

        with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as handle:
            handle.write("not a key\n")
            path = handle.name
        try:
            result = self._run(path, "9DC858229FC7DD38854AE2D88D81803C0EBFCD88")
        finally:
            Path(path).unlink()
        self.assertEqual(result.returncode, 65)
        self.assertIn("key_not_ascii_armoured", result.stderr)

    def test_rejects_a_malformed_expected_fingerprint(self) -> None:
        result = self._run(str(VERIFIER), "not-a-fingerprint")
        self.assertEqual(result.returncode, 65)
        self.assertIn("expected_fingerprint_invalid", result.stderr)

    def test_requires_two_arguments(self) -> None:
        self.assertEqual(self._run(str(VERIFIER)).returncode, 65)


class AptSourceInteractionTests(unittest.TestCase):
    def test_our_own_docker_source_survives_reapply(self) -> None:
        # The validator runs first in the play. Without this the run passes on a
        # bare host and refuses on every reapply, rejecting a source the play
        # itself added.
        validator = (ROOT / "ansible" / "files" / "validate_apt_sources.py").read_text()
        self.assertIn("download.docker.com", validator)
        defaults = yaml.safe_load(
            (ROOT / "ansible" / "roles" / "docker" / "defaults" / "main.yaml").read_text()
        )
        self.assertEqual(defaults["kitdev_docker_repository_host"], "download.docker.com")


if __name__ == "__main__":
    unittest.main()
