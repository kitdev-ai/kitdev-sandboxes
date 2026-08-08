from __future__ import annotations

import hashlib
import importlib.util
import tempfile
import unittest
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[2]
ANSIBLE = ROOT / "ansible"


def load_source_validator():
    path = ANSIBLE / "files" / "validate_apt_sources.py"
    spec = importlib.util.spec_from_file_location("validate_apt_sources", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_capacity_validator():
    path = ANSIBLE / "files" / "validate_host_capacity.py"
    spec = importlib.util.spec_from_file_location("validate_host_capacity", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class HostPrerequisiteAssetTests(unittest.TestCase):
    def test_playbooks_are_local_and_use_narrow_roles(self) -> None:
        site = yaml.safe_load((ANSIBLE / "site.yaml").read_text(encoding="utf-8"))[0]
        self.assertEqual(site["hosts"], "localhost")
        self.assertTrue(site["become"])
        roles = [entry["role"] for entry in site["roles"]]
        self.assertEqual(
            roles,
            ["preflight", "host_packages", "host_identity", "host_kernel", "host_manifest"],
        )
        removal = yaml.safe_load(
            (ANSIBLE / "remove-host-prerequisites.yaml").read_text(encoding="utf-8")
        )[0]
        self.assertEqual([entry["role"] for entry in removal["roles"]], ["preflight", "host_remove"])

    def test_platform_contract_excludes_ubuntu_24_04(self) -> None:
        defaults = yaml.safe_load(
            (ANSIBLE / "roles" / "preflight" / "defaults" / "main.yaml").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(
            defaults["kitdev_supported_platforms"],
            [
                {
                    "os_version": "26.04",
                    "lifecycle_modes": ["production", "development", "migration"],
                },
                {
                    "os_version": "25.04",
                    "lifecycle_modes": ["development", "migration"],
                },
            ],
        )
        contract_text = "\n".join(
            path.read_text(encoding="utf-8")
            for path in sorted((ANSIBLE / "roles" / "preflight").glob("**/*.yaml"))
        )
        self.assertNotIn("24.04", contract_text)

    def test_reserved_identities_are_high_range_and_worker_only_gets_kvm(self) -> None:
        defaults = yaml.safe_load(
            (ANSIBLE / "roles" / "preflight" / "defaults" / "main.yaml").read_text(
                encoding="utf-8"
            )
        )
        identities = defaults["kitdev_service_identities"]
        self.assertEqual([item["uid"] for item in identities], [61000, 61001, 61002])
        self.assertEqual([item["gid"] for item in identities], [61000, 61001, 61002])
        self.assertEqual(identities[0]["supplementary_groups"], [])
        self.assertEqual(identities[1]["supplementary_groups"], [])
        self.assertEqual(identities[2]["supplementary_groups"], ["kvm"])

    def test_shared_kitdev_group_is_converged(self) -> None:
        # Five control-plane scripts require this group and nothing created it,
        # so a fresh host died at the first install step with
        # kitdev_group_required. It must be created before the service users
        # that carry it as a supplementary group.
        defaults = yaml.safe_load(
            (ROOT / "ansible" / "roles" / "preflight" / "defaults" / "main.yaml").read_text()
        )
        shared = defaults["kitdev_shared_group"]
        self.assertEqual(shared["name"], "kitdev")
        self.assertGreaterEqual(shared["gid"], 61000)
        tasks = (
            ROOT / "ansible" / "roles" / "host_identity" / "tasks" / "main.yaml"
        ).read_text()
        self.assertIn("kitdev_shared_group.name", tasks)
        self.assertLess(
            tasks.index("kitdev_shared_group.name"),
            tasks.index("Converge locked non-login service identities"),
        )

    def test_package_removal_is_opt_in(self) -> None:
        # apt removes reverse dependencies too, so uninstalling iptables would
        # take the container runtime with it and report success.
        defaults = yaml.safe_load(
            (ROOT / "ansible" / "roles" / "preflight" / "defaults" / "main.yaml").read_text()
        )
        self.assertIs(defaults["kitdev_remove_packages"], False)
        remove = (ROOT / "ansible" / "roles" / "host_remove" / "tasks" / "main.yaml").read_text()
        self.assertIn("when: kitdev_remove_packages and", remove)
        self.assertIn("kitdev_removable_packages", remove)

    def test_worker_group_contract_matches_the_runtime_gate(self) -> None:
        # The two contracts drifted once and made every control-plane entry
        # point fail. require_worker_identity demands the worker be in exactly
        # two groups, so the converged supplementary set must stay at one entry.
        defaults = yaml.safe_load(
            (ROOT / "ansible" / "roles" / "preflight" / "defaults" / "main.yaml").read_text()
        )
        worker = next(
            item
            for item in defaults["kitdev_service_identities"]
            if item["name"] == "kitdev-worker"
        )
        self.assertEqual(len(worker["supplementary_groups"]), 1, "runtime gate allows exactly two")
        common = (ROOT / "scripts" / "control-plane" / "common.sh").read_text()
        self.assertIn('[[ "${#group_ids[@]}" == 2 ]]', common)
        # The shared group must NOT be granted to the worker; the consumers only
        # require that it exists.
        self.assertNotIn("kitdev", worker["supplementary_groups"])

    def test_apt_allowlist_carries_no_provider_mirror(self) -> None:
        # A provider mirror in the built-in set silently made this validator
        # refuse every host that was not on that provider.
        validator = (ROOT / "ansible" / "files" / "validate_apt_sources.py").read_text()
        allowed = validator.split("ALLOWED_HOSTS = {", 1)[1].split("}", 1)[0]
        for host in allowed.replace('"', "").split(","):
            host = host.strip()
            if host:
                self.assertTrue(
                    host.endswith("ubuntu.com"),
                    f"provider mirror in built-in allowlist: {host}",
                )
        self.assertIn("--allow-host", validator)
        defaults = yaml.safe_load(
            (ROOT / "ansible" / "roles" / "preflight" / "defaults" / "main.yaml").read_text()
        )
        self.assertEqual(defaults["kitdev_apt_additional_mirrors"], [])

    def test_orchestrator_runtime_commands_are_installed(self) -> None:
        # preflight-orchestrator.sh requires these on every orchestrator start,
        # and seed-local-template.sh requires rsync. The prepared-host gate
        # requires pgrep from procps.
        defaults = yaml.safe_load(
            (ROOT / "ansible" / "roles" / "preflight" / "defaults" / "main.yaml").read_text()
        )
        packages = defaults["kitdev_prerequisite_packages"]
        for required in ("iptables", "rsync", "procps", "ufw"):
            self.assertIn(required, packages)
        self.assertEqual(packages, sorted(packages), "keep the package list sorted")

    def test_shared_group_is_guarded_recorded_and_removable(self) -> None:
        # Creating a resource without recording prior state and without a
        # removal path leaves residue that apply/remove/apply cannot clean.
        preflight = (
            ROOT / "ansible" / "roles" / "preflight" / "tasks" / "main.yaml"
        ).read_text()
        self.assertIn("kitdev_shared_group_prior", preflight)
        self.assertIn("Reject a foreign occupant of the reserved shared group id", preflight)
        self.assertIn("Reject a shared group already present at a different id", preflight)

        prior = (
            ROOT / "ansible" / "roles" / "host_packages" / "templates" / "prior-state.json.j2"
        ).read_text()
        self.assertIn('"shared_group"', prior)
        self.assertIn("kitdev_shared_group_prior.rc == 0", prior)

        remove = (ROOT / "ansible" / "roles" / "host_remove" / "tasks" / "main.yaml").read_text()
        self.assertIn("Remove the shared group created by this installation", remove)
        # Absent prior state must mean "leave it alone", never "delete it".
        self.assertIn("kitdev_prior_state.shared_group.existed | default(true)", remove)

    def test_preflight_probes_run_in_check_mode(self) -> None:
        # Ansible skips command modules in check mode by default. Without
        # check_mode: false these probes register nothing, and every guard that
        # indexes their results fails on `--check` -- the documented first step.
        tasks = yaml.safe_load(
            (ROOT / "ansible" / "roles" / "preflight" / "tasks" / "main.yaml").read_text()
        )
        commands = [t for t in tasks if "ansible.builtin.command" in t]
        self.assertGreater(len(commands), 0)
        for task in commands:
            self.assertIs(
                task.get("check_mode"),
                False,
                f"read-only probe must run in check mode: {task.get('name')}",
            )
            self.assertIs(
                task.get("changed_when"),
                False,
                f"read-only probe must not report changed: {task.get('name')}",
            )

    def test_roles_do_not_use_shell_or_touch_unowned_security_policy(self) -> None:
        task_text = "\n".join(
            path.read_text(encoding="utf-8")
            for path in sorted((ANSIBLE / "roles").glob("*/tasks/*.yaml"))
        )
        self.assertNotIn("ansible.builtin.shell:", task_text)
        self.assertNotIn("/etc/ssh", task_text)
        self.assertNotIn("unattended-upgrades", task_text)
        self.assertIn("prior-state.json", task_text)
        self.assertIn("Refuse unowned service identity adoption", task_text)
        self.assertIn("Refuse unowned or modified managed host files", task_text)

    def test_dependency_lock_is_hash_bound_by_entrypoint(self) -> None:
        lock = (ROOT / "requirements.lock").read_bytes()
        digest = hashlib.sha256(lock).hexdigest()
        entrypoint = (ROOT / "scripts" / "host-prerequisites.sh").read_text(encoding="utf-8")
        self.assertIn(f"LOCK_SHA256='{digest}'", entrypoint)
        direct = (ROOT / "requirements.in").read_text(encoding="utf-8")
        self.assertIn("ansible-core==2.21.2", direct)
        self.assertIn("pip==26.1.1", direct)
        self.assertIn("--require-hashes", entrypoint)
        self.assertIn("pip check", entrypoint)
        self.assertIn("dpkg-query", entrypoint)
        self.assertIn("db:Status-Abbrev", entrypoint)
        self.assertIn("run this command through sudo", entrypoint)

    def test_apt_validator_accepts_ubuntu_deb822_and_rejects_foreign_origin(self) -> None:
        validator = load_source_validator()
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "ubuntu.sources"
            source.write_text(
                "Types: deb\n"
                "URIs: https://archive.ubuntu.com/ubuntu\n"
                "Suites: resolute resolute-updates resolute-security\n"
                "Components: main universe\n"
                "Signed-By: /usr/share/keyrings/ubuntu-archive-keyring.gpg\n",
                encoding="utf-8",
            )
            self.assertEqual(validator.validate_deb822(source, "resolute"), 1)
            source.write_text(
                "Types: deb\nURIs: https://packages.example.invalid/ubuntu\n"
                "Suites: resolute\nComponents: main\n"
                "Signed-By: /usr/share/keyrings/ubuntu-archive-keyring.gpg\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "non-Ubuntu APT origin"):
                validator.validate_deb822(source, "resolute")
            source.write_text(
                "Types: deb\nURIs: https://archive.ubuntu.com/ubuntu\n"
                "Suites: resolute\nComponents: main\nSigned-By: /tmp/foreign.gpg\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "unexpected APT signing key"):
                validator.validate_deb822(source, "resolute")

    def test_kernel_settings_are_parameterized_and_capacity_gated(self) -> None:
        defaults = yaml.safe_load(
            (ANSIBLE / "roles" / "preflight" / "defaults" / "main.yaml").read_text(
                encoding="utf-8"
            )
        )
        tasks = (ANSIBLE / "roles" / "preflight" / "tasks" / "main.yaml").read_text(
            encoding="utf-8"
        )
        self.assertEqual(defaults["kitdev_capacity_max_sandbox_memory_mib"], 8192)
        self.assertEqual(defaults["kitdev_capacity_concurrent_hugepage_sandboxes"], 2)
        self.assertEqual(defaults["kitdev_capacity_build_snapshot_headroom_mib"], 8192)
        self.assertEqual(defaults["kitdev_hugepages_min_available_mb_after"], 16384)
        self.assertEqual(defaults["kitdev_hugepages_max_ram_percent"], 50)
        self.assertIn("validate_host_capacity.py", tasks)
        self.assertIn("Publish derived hugepage capacity", tasks)
        self.assertIn("Refuse unsafe live NBD reconfiguration", tasks)

    def test_hugepage_capacity_is_derived_from_workload_profile(self) -> None:
        validator = load_capacity_validator()
        pool_mib, pages = validator.required_hugepages_2m(8192, 2, 8192)
        self.assertEqual(pool_mib, 24576)
        self.assertEqual(pages, 12288)

        with self.assertRaisesRegex(ValueError, "headroom"):
            validator.required_hugepages_2m(8192, 2, 4096)
        self.assertEqual(validator.required_hugepages_2m(512, 1, 512), (1024, 512))
        with self.assertRaisesRegex(ValueError, "maximum sandbox memory"):
            validator.required_hugepages_2m(256, 2, 8192)

    def test_default_hugepage_profile_preserves_host_reserve(self) -> None:
        validator = load_capacity_validator()
        _, pages = validator.required_hugepages_2m(8192, 2, 8192)
        values = {
            "MemTotal": 64 * 1024 * 1024,
            "MemAvailable": 48 * 1024 * 1024,
            "HugePages_Total": 0,
            "Hugepagesize": 2048,
        }
        validator.validate_capacity(values, pages, 50, 16384)

        with self.assertRaisesRegex(ValueError, "total-RAM budget"):
            validator.validate_capacity(
                {**values, "MemTotal": 32 * 1024 * 1024}, pages, 50, 16384
            )
        with self.assertRaisesRegex(ValueError, "currently available"):
            validator.validate_capacity(
                {**values, "MemAvailable": 32 * 1024 * 1024}, pages, 50, 16384
            )

    def test_hugepage_capacity_uses_available_and_current_pool_memory(self) -> None:
        validator = load_capacity_validator()
        values = {
            "MemTotal": 64 * 1024 * 1024,
            "MemAvailable": 8 * 1024 * 1024,
            "HugePages_Total": 256,
            "Hugepagesize": 2048,
        }
        validator.validate_capacity(values, 512, 25, 1024)
        with self.assertRaisesRegex(ValueError, "currently available"):
            validator.validate_capacity({**values, "MemAvailable": 1200 * 1024}, 512, 25, 1024)
        with self.assertRaisesRegex(ValueError, "total-RAM budget"):
            validator.validate_capacity(values, 16384, 25, 1024)

    def test_removal_rejects_sysctl_drift_and_marks_reboot_contract(self) -> None:
        removal = (ANSIBLE / "roles" / "host_remove" / "tasks" / "main.yaml").read_text(
            encoding="utf-8"
        )
        manifest = (
            ANSIBLE / "roles" / "host_manifest" / "templates" / "manifest.json.j2"
        ).read_text(encoding="utf-8")
        defaults = (
            ANSIBLE / "roles" / "preflight" / "defaults" / "main.yaml"
        ).read_text(encoding="utf-8")
        self.assertIn("Refuse removal over runtime sysctl drift", removal)
        self.assertIn("host-prerequisites-reboot-required", defaults)
        self.assertIn("kitdev_prerequisite_reboot_marker", removal)
        self.assertIn('"module_runtime_restore": "controlled-reboot"', manifest)
        self.assertIn('"max_sandbox_memory_mib"', manifest)
        self.assertIn('"concurrent_hugepage_sandboxes"', manifest)
        self.assertIn('"build_snapshot_headroom_mib"', manifest)
        self.assertIn('"hugepage_pool_mib"', manifest)


if __name__ == "__main__":
    unittest.main()
