from __future__ import annotations

import base64
import hashlib
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from kitdev_sandboxes.journal import JournalConflict, JournalStore
from kitdev_sandboxes.stage05 import CONFIG_ROOT, MARKER_PATH, Stage05Paths, Stage05Reconciler
from kitdev_sandboxes.stage10 import (
    DOCKER_CONFLICT_PACKAGES,
    DOCKER_KEY_PATH,
    DOCKER_SOURCE_BYTES,
    DOCKER_SOURCE_PATH,
    DPKG_STATUS_PATH,
    OS_RELEASE_PATH,
    PACKAGE_LOCK_PATHS,
    UBUNTU_ARCHIVE_KEYRING_PATH,
    CommandOutput,
    Stage10Error,
    Stage10Resolver,
)


BUNDLE_DIGEST = "a" * 64
STAGE05_DIGEST = "b" * 64


def temporary_trust_boundary(root: Path) -> Path:
    chain = (Path("/"),) + tuple(reversed(root.parents[:-1])) + (root,)
    for path in chain:
        metadata = path.stat()
        if (
            metadata.st_uid != 0
            or metadata.st_gid != 0
            or metadata.st_mode & 0o022
        ):
            return path
    return root


class FakePackages:
    def __init__(self) -> None:
        self.installed = {
            "ca-certificates": ("installed", "20240203", "all"),
            "curl": ("installed", "8.5.0-2ubuntu10", "amd64"),
            "ubuntu-keyring": ("installed", "2023.11.28.1", "all"),
        }
        self.candidates = {
            "ca-certificates": "20250419",
            "curl": "8.5.0-2ubuntu10",
        }
        self.holds: tuple[str, ...] = ("unrelated-package",)
        self.manual: tuple[str, ...] = ("ca-certificates", "curl", "ubuntu-keyring")
        self.automatic: tuple[str, ...] = ()
        self.simulation = (
            "Reading package lists...\n"
            "Building dependency tree...\n"
            "Inst ca-certificates (20250419 Ubuntu:26.04/resolute [all])\n"
            "Inst openssl-provider-legacy (3.5.0-1 Ubuntu:26.04/resolute [amd64])\n"
            "Conf ca-certificates (20250419 Ubuntu:26.04/resolute [all])\n"
        )
        self.calls: list[tuple[str, ...]] = []

    def __call__(self, argv: tuple[str, ...]) -> CommandOutput:
        self.calls.append(argv)
        executable = argv[0]
        if executable == "/usr/bin/dpkg":
            if argv[1] == "--audit":
                return CommandOutput(0, "", "", True)
            return CommandOutput(0, "amd64\n", "", True)
        if executable == "/usr/bin/apt-mark":
            values = {
                "showhold": self.holds,
                "showmanual": self.manual,
                "showauto": self.automatic,
            }[argv[1]]
            requested = set(argv[2:])
            selected = (
                values
                if not requested
                else tuple(name for name in values if name in requested)
            )
            return CommandOutput(0, "".join(f"{name}\n" for name in selected), "", True)
        if executable == "/usr/bin/apt-get":
            return CommandOutput(0, self.simulation, "", True)
        name = argv[-1]
        if executable == "/usr/bin/dpkg-query":
            if name not in self.installed:
                return CommandOutput(1, "", "not installed\n", False)
            status, version, architecture = self.installed[name]
            selection = "install" if status == "installed" else "deinstall"
            return CommandOutput(
                0,
                f"{selection}\tok\t{status}\t{version}\t{architecture}\n",
                "",
                True,
            )
        if executable == "/usr/bin/apt-cache":
            installed = self.installed.get(name)
            installed_version = (
                installed[1] if installed and installed[0] == "installed" else "(none)"
            )
            candidate = self.candidates.get(name, "(none)")
            return CommandOutput(
                0,
                f"{name}:\n  Installed: {installed_version}\n  Candidate: {candidate}\n",
                "",
                True,
            )
        raise AssertionError(f"unexpected command: {argv!r}")


class Stage10Tests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.root.chmod(0o700)
        for relative in (
            "var/lib",
            "etc/systemd/system/multi-user.target.wants",
            "usr/lib/systemd/system",
            "lib/systemd/system",
            "opt",
        ):
            path = self.root / relative
            path.mkdir(parents=True, exist_ok=True)
            current = path
            while current != self.root:
                current.chmod(0o755)
                current = current.parent
        self.paths = Stage05Paths(
            prefix=self.root,
            trusted_prefix=temporary_trust_boundary(self.root),
        )
        self.packages = FakePackages()
        self.write_managed(
            OS_RELEASE_PATH,
            b'ID=ubuntu\nVERSION_ID="26.04"\nVERSION_CODENAME=resolute\n',
        )
        self.write_managed(UBUNTU_ARCHIVE_KEYRING_PATH, b"test-ubuntu-archive-keyring")
        for lock_path in PACKAGE_LOCK_PATHS:
            self.write_managed(lock_path, b"")

    def resolver(self, **overrides: object) -> Stage10Resolver:
        arguments: dict[str, object] = {
            "paths": self.paths,
            "expected_uid": os.getuid(),
            "expected_gid": os.getegid(),
            "mount_id": lambda _descriptor: 7,
            "xattrs": lambda _descriptor: (),
            "service_state": lambda _unit: "absent",
            "command": self.packages,
            "authorization": lambda: STAGE05_DIGEST,
        }
        arguments.update(overrides)
        return Stage10Resolver(BUNDLE_DIGEST, **arguments)  # type: ignore[arg-type]

    def actual(self, canonical: Path) -> Path:
        return self.paths.actual(canonical)

    def write_managed(self, canonical: Path, content: bytes) -> Path:
        path = self.actual(canonical)
        path.parent.mkdir(mode=0o755, parents=True, exist_ok=True)
        path.write_bytes(content)
        path.chmod(0o644)
        return path

    def test_resolution_is_canonical_exact_and_simulation_only(self) -> None:
        before = tuple(self.root.rglob("*"))
        result = self.resolver().resolve("execute")
        after = tuple(self.root.rglob("*"))

        self.assertTrue(result.eligible)
        self.assertEqual(result.key_state, "absent")
        self.assertEqual(result.source_state, "absent")
        self.assertEqual(result.actions, ())
        self.assertEqual(before, after)
        self.assertEqual(
            hashlib.sha256(result.resolution_bytes).hexdigest(),
            result.resolution_hash.removeprefix("sha256:"),
        )
        document = json.loads(result.resolution_bytes)
        self.assertFalse(document["apply_authorized"])
        self.assertTrue(document["inventory_clean"])
        self.assertEqual(document["architecture"], "amd64")
        self.assertEqual(document["candidate_scope"], "host-cache-untrusted-for-apply")
        self.assertEqual(
            document["manual"], ["ca-certificates", "curl", "ubuntu-keyring"]
        )
        self.assertEqual(document["automatic"], [])
        self.assertEqual(document["apt_extended_states"], "absent")
        self.assertEqual(document["dpkg_status"], "absent")
        self.assertRegex(document["ubuntu_archive_keyring"], r"^sha256:[0-9a-f]{64}$")
        self.assertEqual([item["name"] for item in document["trust_packages"]], ["ubuntu-keyring"])
        self.assertEqual(document["foreign_docker_sources"], [])
        self.assertEqual(document["legacy_docker_keys"], [])
        self.assertEqual(document["stage"], "10-resolution")
        self.assertEqual(document["stage05_bundle_sha256"], "sha256:" + STAGE05_DIGEST)
        self.assertEqual(
            result.resolution_bytes,
            (
                json.dumps(document, sort_keys=True, separators=(",", ":")) + "\n"
            ).encode("ascii"),
        )
        self.assertFalse(any(call[0].endswith("apt-get") for call in self.packages.calls))
        evidence = result.evidence()
        self.assertIn("apply_authorized=no", evidence)
        encoded = evidence.split("resolution_b64url=", maxsplit=1)[1]
        self.assertEqual(
            base64.b64decode(encoded, altchars=b"-_", validate=True),
            result.resolution_bytes,
        )

    def test_official_conflict_inventory_is_complete_and_blocks_residue(self) -> None:
        self.assertEqual(
            DOCKER_CONFLICT_PACKAGES,
            (
                "containerd",
                "docker-buildx",
                "docker-compose",
                "docker-compose-v2",
                "docker-doc",
                "docker.io",
                "podman-docker",
                "runc",
            ),
        )
        self.packages.installed["docker.io"] = ("config-files", "28.0.1", "amd64")
        result = self.resolver().resolve("execute")
        docker = next(package for package in result.conflicts if package.name == "docker.io")
        self.assertEqual(docker.status, "config-files")
        self.assertFalse(result.eligible)

        self.packages.installed.pop("docker.io")
        self.packages.holds = ("docker.io",)
        self.assertFalse(self.resolver().resolve("execute").eligible)

        self.packages.holds = ()
        self.packages.installed.pop("ubuntu-keyring")
        self.assertFalse(self.resolver().resolve("execute").eligible)

    def test_installed_prerequisite_without_cached_candidate_is_a_no_op(self) -> None:
        self.packages.candidates["ca-certificates"] = "(none)"

        result = self.resolver().resolve("execute")

        package = next(item for item in result.packages if item.name == "ca-certificates")
        self.assertIsNone(package.candidate_version)
        self.assertEqual(result.actions, ())
        self.assertTrue(result.eligible)

    def test_missing_prerequisite_dependency_closure_is_resimulated_with_pins(self) -> None:
        self.packages.installed.pop("ca-certificates")

        result = self.resolver().resolve("execute")

        self.assertFalse(result.eligible)
        simulations = [call for call in self.packages.calls if call[0] == "/usr/bin/apt-get"]
        self.assertEqual(len(simulations), 2)
        self.assertEqual(simulations[0][-1:], ("ca-certificates=20250419",))
        self.assertEqual(
            simulations[1][-2:],
            ("ca-certificates=20250419", "openssl-provider-legacy=3.5.0-1"),
        )

    def test_existing_repository_files_are_observed_but_never_adopted(self) -> None:
        source = self.write_managed(DOCKER_SOURCE_PATH, DOCKER_SOURCE_BYTES)
        key = self.write_managed(DOCKER_KEY_PATH, b"foreign-key")

        result = self.resolver().resolve("execute")

        self.assertEqual(result.source_state, "exact")
        self.assertEqual(result.key_state, "conflict")
        self.assertFalse(result.eligible)
        self.assertEqual(source.read_bytes(), DOCKER_SOURCE_BYTES)
        self.assertEqual(key.read_bytes(), b"foreign-key")

    def test_legacy_key_and_duplicate_docker_source_block_without_disclosure(self) -> None:
        legacy = self.write_managed(
            Path("/usr/share/keyrings/docker-archive-keyring.gpg"), b"legacy"
        )
        duplicate = self.write_managed(
            Path("/etc/apt/sources.list.d/legacy-docker.list"),
            b"deb https://download.docker.com/linux/ubuntu resolute stable\n",
        )

        result = self.resolver().resolve("execute")

        self.assertFalse(result.eligible)
        self.assertEqual(result.legacy_docker_keys, ("legacy-key-2",))
        self.assertEqual(result.foreign_docker_sources, ("source-part-1",))
        self.assertNotIn("legacy-docker.list", result.evidence())
        self.assertEqual(legacy.read_bytes(), b"legacy")
        self.assertIn(b"download.docker.com", duplicate.read_bytes())

    def test_repository_symlink_hardlink_and_wrong_mode_fail_closed(self) -> None:
        source = self.write_managed(DOCKER_SOURCE_PATH, DOCKER_SOURCE_BYTES)
        source.chmod(0o600)
        with self.assertRaisesRegex(Stage10Error, "repository_state_conflict"):
            self.resolver().resolve("execute")

        source.chmod(0o644)
        hardlink = self.root / "source-hardlink"
        os.link(source, hardlink)
        with self.assertRaisesRegex(Stage10Error, "repository_state_conflict"):
            self.resolver().resolve("execute")
        hardlink.unlink()

        source.unlink()
        target = self.root / "outside-source"
        target.write_bytes(DOCKER_SOURCE_BYTES)
        source.symlink_to(target)
        with self.assertRaisesRegex(Stage10Error, "repository_state_unknown"):
            self.resolver().resolve("execute")

    def test_published_name_replacement_during_read_is_rejected(self) -> None:
        source = self.write_managed(DOCKER_SOURCE_PATH, DOCKER_SOURCE_BYTES)
        real_read = os.read
        replaced = False

        def replacing_read(descriptor: int, size: int) -> bytes:
            nonlocal replaced
            chunk = real_read(descriptor, size)
            if chunk and not replaced:
                replacement = source.parent / "replacement"
                replacement.write_bytes(DOCKER_SOURCE_BYTES)
                replacement.chmod(0o644)
                os.replace(replacement, source)
                replaced = True
            return chunk

        with patch("kitdev_sandboxes.stage10.os.read", side_effect=replacing_read):
            with self.assertRaisesRegex(Stage10Error, "repository_state_conflict"):
                self.resolver().resolve("execute")
        self.assertTrue(replaced)

    def test_candidate_simulation_removal_hold_and_malformed_output_block(self) -> None:
        cases = (
            ("The following packages will be REMOVED:\nRemv foreign\n", (), "conflict"),
            ("Inst malformed\n", (), "unknown"),
            (self.packages.simulation, ("ca-certificates",), None),
        )
        for simulation, holds, reason in cases:
            with self.subTest(reason=reason, holds=holds):
                self.packages.installed.pop("ca-certificates", None)
                self.packages.simulation = simulation
                self.packages.holds = holds
                if reason is None:
                    self.assertFalse(self.resolver().resolve("execute").eligible)
                else:
                    with self.assertRaisesRegex(
                        Stage10Error, f"package_resolution_{reason}"
                    ):
                        self.resolver().resolve("execute")

    def test_missing_candidate_architecture_and_inventory_mismatch_fail_closed(self) -> None:
        self.packages.installed.pop("curl")
        self.packages.candidates["curl"] = "(none)"
        with self.assertRaisesRegex(Stage10Error, "package_candidate_absent"):
            self.resolver().resolve("execute")

        self.packages.candidates["curl"] = "8.5.0-2ubuntu10"
        original = self.packages.__call__

        def wrong_architecture(argv: tuple[str, ...]) -> CommandOutput:
            if argv == ("/usr/bin/dpkg", "--print-architecture"):
                return CommandOutput(0, "arm64\n", "", True)
            return original(argv)

        with self.assertRaisesRegex(Stage10Error, "unsupported_lab_architecture"):
            self.resolver(command=wrong_architecture).resolve("execute")

        self.write_managed(
            OS_RELEASE_PATH,
            b'ID=ubuntu\nVERSION_ID="26.04"\nVERSION_CODENAME=plucky\n',
        )
        with self.assertRaisesRegex(Stage10Error, "unsupported_lab_os"):
            self.resolver().resolve("execute")

    def test_real_stage05_validated_state_is_required_and_reused(self) -> None:
        stage05 = Stage05Reconciler(
            STAGE05_DIGEST,
            paths=self.paths,
            expected_uid=os.getuid(),
            expected_gid=os.getegid(),
            mount_id=lambda _descriptor: 7,
            xattrs=lambda _descriptor: (),
            service_state=lambda _unit: "absent",
        )
        stage05.execute()
        lock_checked = False

        def checking_lock(argv: tuple[str, ...]) -> CommandOutput:
            nonlocal lock_checked
            if not lock_checked:
                store = JournalStore(
                    self.paths.actual(Path("/var/lib/kitdev-sandboxes/journal")),
                    stage05._journal_policy(),
                )
                with self.assertRaises(JournalConflict):
                    with store.locked():
                        pass
                lock_checked = True
            return self.packages(argv)

        resolver = self.resolver(authorization=None, command=checking_lock)

        result = resolver.resolve("before")

        self.assertEqual(result.stage05_bundle_hash, "sha256:" + STAGE05_DIGEST)
        self.assertTrue(lock_checked)
        self.assertEqual(stage05.observe("after").journal_state, "validated")

    def test_unvalidated_stage05_and_invalid_authorization_fail_before_commands(self) -> None:
        with self.assertRaisesRegex(Stage10Error, "stage05_authorization_conflict"):
            self.resolver(authorization=None).resolve("before")
        self.assertEqual(self.packages.calls, [])

        with self.assertRaisesRegex(Stage10Error, "stage05_authorization_conflict"):
            self.resolver(authorization=lambda: "not-a-digest").resolve("before")
        self.assertEqual(self.packages.calls, [])

    def test_semantic_package_state_change_during_simulation_is_rejected(self) -> None:
        status = self.write_managed(DPKG_STATUS_PATH, b"Package: baseline\n")
        self.packages.installed.pop("ca-certificates")
        original = self.packages.__call__

        def mutating_command(argv: tuple[str, ...]) -> CommandOutput:
            result = original(argv)
            if argv[0] == "/usr/bin/apt-get":
                status.write_bytes(b"Package: changed\n")
                status.chmod(0o644)
            return result

        with self.assertRaisesRegex(Stage10Error, "read_only_state_changed"):
            self.resolver(command=mutating_command).resolve("execute")

    def test_package_lock_path_replacement_is_rejected(self) -> None:
        lock = self.actual(PACKAGE_LOCK_PATHS[0])
        original = self.packages.__call__
        replaced = False

        def replacing_command(argv: tuple[str, ...]) -> CommandOutput:
            nonlocal replaced
            if not replaced:
                replacement = lock.parent / "replacement-lock"
                replacement.write_bytes(b"")
                replacement.chmod(0o644)
                os.replace(replacement, lock)
                replaced = True
            return original(argv)

        with self.assertRaisesRegex(Stage10Error, "package_lock_conflict"):
            self.resolver(command=replacing_command).resolve("execute")
        self.assertTrue(replaced)

    def test_stage05_marker_replacement_during_inventory_is_rejected(self) -> None:
        stage05 = Stage05Reconciler(
            STAGE05_DIGEST,
            paths=self.paths,
            expected_uid=os.getuid(),
            expected_gid=os.getegid(),
            mount_id=lambda _descriptor: 7,
            xattrs=lambda _descriptor: (),
            service_state=lambda _unit: "absent",
        )
        stage05.execute()
        marker = self.actual(MARKER_PATH)
        original = self.packages.__call__
        replaced = False

        def replacing_command(argv: tuple[str, ...]) -> CommandOutput:
            nonlocal replaced
            if not replaced:
                replacement = marker.parent / "replacement-marker"
                replacement.write_bytes(b"{}\n")
                replacement.chmod(0o600)
                os.replace(replacement, marker)
                replaced = True
            return original(argv)

        with self.assertRaisesRegex(Stage10Error, "stage05_authorization_conflict"):
            self.resolver(authorization=None, command=replacing_command).resolve("execute")
        self.assertTrue(replaced)

    def test_stage05_resource_change_during_inventory_is_rejected(self) -> None:
        stage05 = Stage05Reconciler(
            STAGE05_DIGEST,
            paths=self.paths,
            expected_uid=os.getuid(),
            expected_gid=os.getegid(),
            mount_id=lambda _descriptor: 7,
            xattrs=lambda _descriptor: (),
            service_state=lambda _unit: "absent",
        )
        stage05.execute()
        original = self.packages.__call__
        changed = False

        def changing_command(argv: tuple[str, ...]) -> CommandOutput:
            nonlocal changed
            if not changed:
                unexpected = self.actual(CONFIG_ROOT) / "unexpected"
                unexpected.write_bytes(b"changed")
                unexpected.chmod(0o600)
                changed = True
            return original(argv)

        with self.assertRaisesRegex(Stage10Error, "stage05_authorization_conflict"):
            self.resolver(authorization=None, command=changing_command).resolve("execute")
        self.assertTrue(changed)


if __name__ == "__main__":
    unittest.main()
