from __future__ import annotations

import json
import os
import stat
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from kitdev_sandboxes.collectors import (
    CollectionStatus,
    NbdDevice,
    Probe,
    lstat_owned_path,
)
from kitdev_sandboxes.composition import (
    AuthenticatedDirectoryOwnership,
    build_composed_doctor_report,
    build_install_plan_report,
    collect_directory_resource_facts,
)
from kitdev_sandboxes.config import LifecycleMode
from kitdev_sandboxes.planning import (
    Confidence,
    ObservedState,
    Ownership,
    ResourceFact,
)
from kitdev_sandboxes.preflight import CheckStatus, HostFacts
from tests.unit.test_collectors import collect_fixture
from tests.unit.test_planning import configuration, directory_facts


def host_facts(**overrides: object) -> HostFacts:
    values: dict[str, object] = {
        "os_id": "ubuntu",
        "os_name": "Ubuntu",
        "os_version_id": "26.04",
        "architecture": "x86_64",
        "pid1_comm": "systemd",
        "cgroup_v2": True,
        "cpu_virtualization": "vmx",
        "kvm_device_exists": True,
        "kvm_device_is_character": True,
        "kvm_device_accessible": True,
        "nested_guest_support": False,
    }
    values.update(overrides)
    return HostFacts(**values)  # type: ignore[arg-type]


def complete_linux_facts():
    facts, _runner, _host = collect_fixture()
    assert facts.devices.nbd.devices.value is not None
    devices = tuple(
        NbdDevice(device.name, Probe.ok(bool(index), source=f"nbd.{device.name}.pid"))
        for index, device in enumerate(facts.devices.nbd.devices.value)
    )
    nbd = replace(facts.devices.nbd, devices=Probe.ok(devices, source="nbd.devices"))
    device_facts = replace(facts.devices, nbd=nbd)
    filesystem = facts.filesystems[0]
    filesystems = tuple(
        replace(filesystem, configured_path=f"/project/path-{index}") for index in range(5)
    )
    return replace(facts, devices=device_facts, filesystems=filesystems)


def absent_directory_facts(config=None) -> tuple[ResourceFact, ...]:
    return directory_facts(
        config=config or configuration(),
        state=ObservedState.ABSENT,
        ownership=Ownership.UNOWNED,
        confidence=Confidence.HIGH,
    )


class CompositionTests(unittest.TestCase):
    def test_doctor_replaces_only_complete_groups_and_keeps_port_policy_unknown(self) -> None:
        config = configuration()
        report = build_composed_doctor_report(config, host_facts(), complete_linux_facts())
        checks = {check.check_id: check for check in report.checks}

        self.assertIs(checks["scope.kernel_facilities"].status, CheckStatus.PASS)
        self.assertIs(checks["scope.capacity"].status, CheckStatus.WARN)
        self.assertIs(checks["scope.services"].status, CheckStatus.PASS)
        self.assertIs(checks["scope.security_posture"].status, CheckStatus.PASS)
        self.assertIs(checks["scope.network_conflicts"].status, CheckStatus.UNKNOWN)
        self.assertEqual(report.exit_code, 5)
        serialized = json.dumps(report.as_dict(verbose=True), sort_keys=True)
        for raw_group in ("listeners", "interfaces", "routes", "memory", "filesystems"):
            self.assertNotIn(f'"{raw_group}"', serialized)

    def test_incomplete_group_retains_conservative_unknown_sentinel(self) -> None:
        linux = complete_linux_facts()
        memory = replace(
            linux.memory,
            available_bytes=Probe.degraded(
                CollectionStatus.PERMISSION_DENIED, source="memory.available"
            ),
        )

        report = build_composed_doctor_report(
            configuration(), host_facts(), replace(linux, memory=memory)
        )
        capacity = next(
            check for check in report.checks if check.check_id == "scope.capacity"
        )

        self.assertIs(capacity.status, CheckStatus.UNKNOWN)
        self.assertIn("not yet implemented", capacity.explanation)

    def test_directory_observation_is_exact_type_aware_and_never_infers_ownership(self) -> None:
        config = configuration()
        modes = {
            config.paths.config: None,
            config.paths.install: stat.S_IFDIR | 0o755,
            config.paths.logs: stat.S_IFREG | 0o644,
            config.paths.runtime: stat.S_IFLNK | 0o777,
        }

        def observe(path: Path) -> Probe[os.stat_result]:
            mode = modes.get(str(path), "permission")
            if mode is None:
                return Probe.degraded(CollectionStatus.ABSENT, source=str(path))
            if mode == "permission":
                return Probe.degraded(
                    CollectionStatus.PERMISSION_DENIED, source=str(path)
                )
            return Probe.ok(
                os.stat_result((mode, 0, 0, 0, 0, 0, 0, 0, 0, 0)), source=str(path)
            )

        facts = {
            fact.resource_id: fact
            for fact in collect_directory_resource_facts(config, stat_path=observe)
        }

        self.assertIs(facts["directory.config"].state, ObservedState.ABSENT)
        self.assertIs(facts["directory.config"].ownership, Ownership.UNOWNED)
        self.assertIs(facts["directory.install"].state, ObservedState.PRESENT)
        self.assertIs(facts["directory.install"].ownership, Ownership.UNKNOWN)
        self.assertIs(facts["directory.logs"].state, ObservedState.UNSUPPORTED)
        self.assertIs(facts["directory.runtime"].state, ObservedState.UNSUPPORTED)
        self.assertIs(facts["directory.state"].state, ObservedState.UNKNOWN)

    def test_authenticated_manifest_input_is_required_for_project_ownership(self) -> None:
        config = configuration()

        def directory(path: Path) -> Probe[os.stat_result]:
            return Probe.ok(
                os.stat_result((stat.S_IFDIR | 0o755, 0, 0, 0, 0, 0, 0, 0, 0, 0)),
                source=str(path),
            )

        unauthenticated = collect_directory_resource_facts(config, stat_path=directory)
        authenticated = collect_directory_resource_facts(
            config,
            stat_path=directory,
            authenticated_ownership=AuthenticatedDirectoryOwnership(
                "install-01234567",
                Path("/var/lib/kitdev-sandboxes/install-manifest.json"),
                frozenset({config.paths.install}),
            ),
        )

        install_without = next(
            fact for fact in unauthenticated if fact.resource_id == "directory.install"
        )
        install_with = next(
            fact for fact in authenticated if fact.resource_id == "directory.install"
        )
        self.assertIs(install_without.ownership, Ownership.UNKNOWN)
        self.assertIs(install_with.ownership, Ownership.PROJECT)

    def test_real_owned_lstat_rejects_symlinked_parent_and_final_symlink(self) -> None:
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as temporary:
            root = Path(temporary)
            real = root / "real"
            real.mkdir()
            linked_parent = root / "linked-parent"
            linked_parent.symlink_to(real, target_is_directory=True)
            final_target = root / "target"
            final_target.mkdir()
            final_link = root / "final-link"
            final_link.symlink_to(final_target, target_is_directory=True)

            parent_result = lstat_owned_path(linked_parent / "missing")
            final_result = lstat_owned_path(final_link)

            self.assertIs(parent_result.status, CollectionStatus.ERROR)
            self.assertIs(final_result.status, CollectionStatus.OK)
            assert final_result.value is not None
            self.assertTrue(stat.S_ISLNK(final_result.value.st_mode))

    def test_supported_install_fixture_is_stable_but_port_policy_blocks(self) -> None:
        order: list[str] = []

        def linux(config):
            order.append("linux")
            return complete_linux_facts()

        def directories(config):
            order.append("directories")
            return absent_directory_facts(config)

        report = build_install_plan_report(
            configuration(),
            host_facts(),
            linux_facts_collector=linux,
            directory_facts_collector=directories,
        )
        fixture_path = (
            Path(__file__).resolve().parents[1]
            / "fixtures"
            / "plans"
            / "install-dry-run-supported-blocked.json"
        )

        self.assertEqual(order, ["linux", "directories"])
        self.assertEqual(report.exit_code, 5)
        self.assertEqual(report.as_dict(), json.loads(fixture_path.read_text(encoding="utf-8")))
        self.assertEqual(report.to_json(), report.to_json())

    def test_unsupported_platform_blocks_before_linux_or_directory_collection(self) -> None:
        calls: list[str] = []

        def forbidden(_config):
            calls.append("called")
            raise AssertionError("collector must not run")

        report = build_install_plan_report(
            configuration(),
            host_facts(os_id="debian", os_version_id="13"),
            linux_facts_collector=forbidden,
            directory_facts_collector=forbidden,
        )

        self.assertEqual(calls, [])
        self.assertEqual(report.exit_code, 3)
        self.assertTrue(report.plan.blocking)

    def test_platform_failure_precedes_unknown_platform_facts(self) -> None:
        report = build_install_plan_report(
            configuration(),
            host_facts(os_id="debian", os_version_id="13", architecture=None),
            linux_facts_collector=lambda _config: self.fail("collector must not run"),
            directory_facts_collector=lambda _config: self.fail("collector must not run"),
        )

        self.assertEqual(report.exit_code, 3)
        blocker = next(
            issue
            for issue in report.plan.issues
            if issue.issue_id == "platform.preflight.blocked"
        )
        self.assertEqual(blocker.code.value, "unsupported")

    def test_unknown_only_platform_gate_remains_exit_five(self) -> None:
        report = build_install_plan_report(
            configuration(),
            host_facts(architecture=None),
            linux_facts_collector=lambda _config: self.fail("collector must not run"),
            directory_facts_collector=lambda _config: self.fail("collector must not run"),
        )

        self.assertEqual(report.exit_code, 5)

    def test_ubuntu_2504_lifecycle_gate_for_production_development_and_migration(self) -> None:
        for mode in (LifecycleMode.DEVELOPMENT, LifecycleMode.MIGRATION):
            with self.subTest(mode=mode):
                config = configuration(mode)
                report = build_install_plan_report(
                    config,
                    host_facts(os_version_id="25.04"),
                    linux_facts_collector=lambda _config: complete_linux_facts(),
                    directory_facts_collector=absent_directory_facts,
                )
                self.assertEqual(report.exit_code, 5)
                self.assertIn(
                    "platform.release.ubuntu_25_04.eol",
                    {issue.issue_id for issue in report.plan.issues},
                )

        called = False

        def forbidden(_config):
            nonlocal called
            called = True
            raise AssertionError

        production = build_install_plan_report(
            configuration(LifecycleMode.PRODUCTION),
            host_facts(os_version_id="25.04"),
            linux_facts_collector=forbidden,
            directory_facts_collector=forbidden,
        )
        self.assertFalse(called)
        self.assertEqual(production.exit_code, 3)

    def test_directory_conflict_and_malformed_fact_block_with_stable_precedence(self) -> None:
        config = configuration()
        foreign = list(absent_directory_facts(config))
        foreign[0] = replace(
            foreign[0], state=ObservedState.PRESENT, ownership=Ownership.FOREIGN
        )
        conflict = build_install_plan_report(
            config,
            host_facts(),
            linux_facts_collector=lambda _config: complete_linux_facts(),
            directory_facts_collector=lambda _config: tuple(foreign),
        )
        self.assertEqual(conflict.exit_code, 4)
        self.assertEqual(conflict.plan.actions, ())

        malformed = list(absent_directory_facts(config))
        malformed[0] = replace(
            malformed[0],
            state=ObservedState.UNKNOWN,
            ownership=Ownership.UNKNOWN,
            confidence=Confidence.UNKNOWN,
        )
        unknown = build_install_plan_report(
            config,
            host_facts(),
            linux_facts_collector=lambda _config: complete_linux_facts(),
            directory_facts_collector=lambda _config: tuple(reversed(malformed)),
        )
        self.assertEqual(unknown.exit_code, 5)
        self.assertEqual(unknown.plan.actions, ())

    def test_install_plan_is_invariant_to_directory_fact_permutation(self) -> None:
        config = configuration()
        resources = absent_directory_facts(config)

        first = build_install_plan_report(
            config,
            host_facts(),
            linux_facts_collector=lambda _config: complete_linux_facts(),
            directory_facts_collector=lambda _config: resources,
        )
        second = build_install_plan_report(
            config,
            host_facts(),
            linux_facts_collector=lambda _config: complete_linux_facts(),
            directory_facts_collector=lambda _config: tuple(reversed(resources)),
        )

        self.assertEqual(first.to_json(), second.to_json())


if __name__ == "__main__":
    unittest.main()
