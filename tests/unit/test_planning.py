from __future__ import annotations

import unittest
from dataclasses import replace

from kitdev_sandboxes.config import (
    Configuration,
    DeploymentConfig,
    DeploymentProfile,
    FeaturesConfig,
    LifecycleMode,
    NetworkConfig,
    PathsConfig,
    SandboxConfig,
)
from kitdev_sandboxes.planning import (
    ActionCategory,
    ChangePlan,
    Confidence,
    IssueCode,
    IssueSeverity,
    ObservedState,
    Ownership,
    PlanIssue,
    PlannedAction,
    PlanningFacts,
    Privilege,
    ResourceFact,
    RestartImpact,
    Rollback,
    RollbackStrategy,
    build_change_plan,
)


def configuration(mode: LifecycleMode = LifecycleMode.PRODUCTION) -> Configuration:
    return Configuration(
        schema_version=1,
        deployment=DeploymentConfig(
            profile=DeploymentProfile.MINIMAL,
            lifecycle_mode=mode,
            listen_address="127.0.0.1",
            public_exposure=False,
            domain=None,
        ),
        paths=PathsConfig(
            install="/opt/kitdev-sandboxes",
            config="/etc/kitdev-sandboxes",
            state="/var/lib/kitdev-sandboxes",
            logs="/var/log/kitdev-sandboxes",
            runtime="/run/kitdev-sandboxes",
        ),
        sandbox=SandboxConfig(
            default_template="base",
            default_vcpus=2,
            default_memory_mib=2048,
            default_disk_mib=10240,
            default_timeout_seconds=900,
            default_ttl_seconds=3600,
            max_processes=512,
            max_output_bytes=10485760,
        ),
        network=NetworkConfig(
            ipv4_cidr="172.31.0.0/16",
            ipv6_enabled=False,
            dns_resolvers=(),
            allow_http_egress=True,
            allow_private_egress=False,
            private_egress_allowlist=(),
            sandbox_to_sandbox=False,
        ),
        features=FeaturesConfig(
            observability=False,
            persistence=False,
            backups=False,
            browser_template=False,
            desktop_template=False,
        ),
    )


def directory_facts(
    *,
    config: Configuration | None = None,
    state: ObservedState = ObservedState.PRESENT,
    ownership: Ownership = Ownership.PROJECT,
    confidence: Confidence = Confidence.HIGH,
) -> tuple[ResourceFact, ...]:
    if config is None:
        config = configuration()
    targets = {
        "config": config.paths.config,
        "install": config.paths.install,
        "logs": config.paths.logs,
        "runtime": config.paths.runtime,
        "state": config.paths.state,
    }
    return tuple(
        ResourceFact(f"directory.{name}", targets[name], state, ownership, confidence)
        for name in ("config", "install", "logs", "runtime", "state")
    )


def planning_facts(
    *,
    release: str | None = "26.04",
    resources: tuple[ResourceFact, ...] | None = None,
) -> PlanningFacts:
    return PlanningFacts(
        os_id="ubuntu" if release is not None else None,
        os_version_id=release,
        resources=directory_facts() if resources is None else resources,
    )


def planned_action(*, target: str = "/opt/kitdev-sandboxes") -> PlannedAction:
    return PlannedAction(
        action_id="directory.install.create",
        category=ActionCategory.DIRECTORY,
        target=target,
        current_state="absent",
        desired_state="present",
        reason="Create the project directory.",
        privilege=Privilege.ROOT,
        restart_impact=RestartImpact.NONE,
        reboot_required=False,
        rollback=Rollback(RollbackStrategy.REMOVE_CREATED, "Remove the created directory."),
        confidence=Confidence.HIGH,
        ownership=Ownership.PROJECT,
    )


class PlanningTests(unittest.TestCase):
    def test_all_documented_categories_have_stable_schema_values(self) -> None:
        self.assertEqual(
            {category.value for category in ActionCategory},
            {
                "package",
                "account",
                "directory",
                "managed_file",
                "shared_file_merge",
                "kernel_module",
                "sysctl",
                "network_firewall",
                "service",
                "compose",
                "artifact",
                "template",
                "validation",
            },
        )

    def test_converged_input_has_zero_actions(self) -> None:
        plan = build_change_plan(configuration(), planning_facts())

        self.assertFalse(plan.blocking)
        self.assertEqual(plan.actions, ())
        self.assertEqual(plan.as_dict()["summary"]["actions"], 0)

    def test_unknown_required_fact_is_blocking_and_suppresses_all_actions(self) -> None:
        resources = list(
            directory_facts(state=ObservedState.ABSENT, ownership=Ownership.UNOWNED)
        )
        resources[0] = ResourceFact(
            "directory.config",
            configuration().paths.config,
            ObservedState.UNKNOWN,
            Ownership.UNKNOWN,
            Confidence.UNKNOWN,
        )

        plan = build_change_plan(configuration(), planning_facts(resources=tuple(resources)))

        self.assertTrue(plan.blocking)
        self.assertEqual(plan.actions, ())
        self.assertTrue(any(issue.code is IssueCode.UNKNOWN for issue in plan.issues))

    def test_absent_project_roots_plan_only_exact_configured_paths(self) -> None:
        config = configuration()
        facts = planning_facts(
            resources=directory_facts(
                state=ObservedState.ABSENT,
                ownership=Ownership.UNOWNED,
            )
        )

        plan = build_change_plan(config, facts)

        self.assertFalse(plan.blocking)
        self.assertEqual(
            {action.target for action in plan.actions},
            {
                config.paths.install,
                config.paths.config,
                config.paths.state,
                config.paths.logs,
                config.paths.runtime,
            },
        )
        self.assertEqual(
            [action.action_id for action in plan.actions],
            sorted(action.action_id for action in plan.actions),
        )
        for action in plan.actions:
            self.assertIs(action.category, ActionCategory.DIRECTORY)
            self.assertIs(action.ownership, Ownership.PROJECT)
            self.assertFalse(action.reboot_required)
            self.assertNotIn("..", action.target)
        serialized_action = plan.as_dict()["actions"][0]
        self.assertEqual(
            set(serialized_action),
            {
                "id",
                "category",
                "target",
                "current_state",
                "desired_state",
                "reason",
                "privilege",
                "restart_impact",
                "reboot_required",
                "rollback",
                "confidence",
                "ownership",
            },
        )
        self.assertEqual(set(serialized_action["rollback"]), {"strategy", "description"})

    def test_existing_foreign_resource_blocks_without_adoption(self) -> None:
        resources = list(directory_facts())
        resources[1] = ResourceFact(
            "directory.install",
            configuration().paths.install,
            ObservedState.PRESENT,
            Ownership.FOREIGN,
            Confidence.HIGH,
        )

        plan = build_change_plan(configuration(), planning_facts(resources=tuple(resources)))

        self.assertTrue(plan.blocking)
        self.assertEqual(plan.actions, ())
        conflict = next(issue for issue in plan.issues if issue.code is IssueCode.CONFLICT)
        self.assertEqual(conflict.fact_id, "directory.install")

    def test_fact_for_a_different_target_cannot_authorize_an_action(self) -> None:
        resources = list(
            directory_facts(state=ObservedState.ABSENT, ownership=Ownership.UNOWNED)
        )
        resources[1] = ResourceFact(
            "directory.install",
            "/opt/a-different-project",
            ObservedState.ABSENT,
            Ownership.UNOWNED,
            Confidence.HIGH,
        )

        plan = build_change_plan(configuration(), planning_facts(resources=tuple(resources)))

        self.assertTrue(plan.blocking)
        self.assertEqual(plan.actions, ())
        self.assertIn(
            "facts.target_mismatch.directory.install", {issue.issue_id for issue in plan.issues}
        )

    def test_unsupported_required_fact_is_blocking_and_never_an_action(self) -> None:
        resources = list(
            directory_facts(state=ObservedState.ABSENT, ownership=Ownership.UNOWNED)
        )
        resources[4] = ResourceFact(
            "directory.state",
            configuration().paths.state,
            ObservedState.UNSUPPORTED,
            Ownership.UNKNOWN,
            Confidence.HIGH,
        )

        plan = build_change_plan(configuration(), planning_facts(resources=tuple(resources)))

        self.assertTrue(plan.blocking)
        self.assertEqual(plan.actions, ())
        issue = next(issue for issue in plan.issues if issue.code is IssueCode.UNSUPPORTED)
        self.assertEqual(issue.fact_id, "directory.state")

    def test_ubuntu_2504_production_blocks_before_any_action(self) -> None:
        facts = planning_facts(
            release="25.04",
            resources=directory_facts(
                state=ObservedState.ABSENT,
                ownership=Ownership.UNOWNED,
            ),
        )

        plan = build_change_plan(configuration(LifecycleMode.PRODUCTION), facts)

        self.assertTrue(plan.blocking)
        self.assertEqual(plan.actions, ())
        self.assertIn("platform.release.ubuntu_25_04.production", {i.issue_id for i in plan.issues})

    def test_ubuntu_2504_development_warns_but_can_plan(self) -> None:
        facts = planning_facts(release="25.04")

        plan = build_change_plan(configuration(LifecycleMode.DEVELOPMENT), facts)

        self.assertFalse(plan.blocking)
        self.assertEqual(plan.actions, ())
        self.assertEqual([issue.severity.value for issue in plan.issues], ["warning"])

    def test_ubuntu_2504_migration_warns_but_can_plan(self) -> None:
        facts = planning_facts(release="25.04")

        plan = build_change_plan(configuration(LifecycleMode.MIGRATION), facts)

        self.assertFalse(plan.blocking)
        self.assertEqual(plan.actions, ())
        self.assertEqual(plan.lifecycle_mode, LifecycleMode.MIGRATION)
        self.assertEqual([issue.severity for issue in plan.issues], [IssueSeverity.WARNING])

    def test_invalid_enum_instances_raise_before_they_can_authorize_actions(self) -> None:
        config = configuration()
        invalid_values = (
            ("state", "absent", Ownership.UNOWNED, Confidence.HIGH),
            ("ownership", ObservedState.ABSENT, "unowned", Confidence.HIGH),
            ("confidence", ObservedState.ABSENT, Ownership.UNOWNED, "high"),
        )
        for field, state, ownership, confidence in invalid_values:
            with self.subTest(field=field):
                with self.assertRaises(TypeError):
                    ResourceFact(
                        "directory.install",
                        config.paths.install,
                        state,  # type: ignore[arg-type]
                        ownership,  # type: ignore[arg-type]
                        confidence,  # type: ignore[arg-type]
                    )

    def test_every_public_plan_dataclass_rejects_wrong_runtime_field_types(self) -> None:
        with self.assertRaises(TypeError):
            PlanningFacts("ubuntu", "26.04", list(directory_facts()))  # type: ignore[arg-type]
        with self.assertRaises(TypeError):
            Rollback("remove_created", "description")  # type: ignore[arg-type]
        with self.assertRaises(TypeError):
            PlannedAction(
                "directory.install.create",
                "directory",  # type: ignore[arg-type]
                "/opt/kitdev-sandboxes",
                "absent",
                "present",
                "reason",
                Privilege.ROOT,
                RestartImpact.NONE,
                False,
                Rollback(RollbackStrategy.REMOVE_CREATED, "description"),
                Confidence.HIGH,
                Ownership.PROJECT,
            )
        with self.assertRaises(TypeError):
            PlanIssue(
                "facts.invalid",
                IssueCode.INVALID_FACT,
                "blocking",  # type: ignore[arg-type]
                "planning.resources",
                "explanation",
                "remediation",
            )
        with self.assertRaises(TypeError):
            ChangePlan("production", (), ())  # type: ignore[arg-type]

    def test_unrelated_duplicates_never_create_issues_or_serialize_identifiers(self) -> None:
        private_value = "private-caller-value-937"
        unrelated = ResourceFact(
            f"unrelated.{private_value}",
            f"/foreign/{private_value}",
            ObservedState.PRESENT,
            Ownership.FOREIGN,
            Confidence.HIGH,
        )
        resources = directory_facts() + (unrelated, unrelated)

        plan = build_change_plan(configuration(), planning_facts(resources=resources))
        serialized = plan.to_json()

        self.assertFalse(plan.blocking)
        self.assertEqual(plan.issues, ())
        self.assertNotIn(private_value, serialized)
        self.assertNotIn("unrelated", serialized)

    def test_duplicate_desired_fact_uses_generic_issue_without_echoing_target(self) -> None:
        private_value = "private-duplicate-target-219"
        resources = directory_facts() + (
            ResourceFact(
                "directory.install",
                f"/foreign/{private_value}",
                ObservedState.PRESENT,
                Ownership.FOREIGN,
                Confidence.HIGH,
            ),
        )

        plan = build_change_plan(configuration(), planning_facts(resources=resources))
        serialized = plan.to_json()

        self.assertTrue(plan.blocking)
        self.assertIn(
            "facts.duplicate.desired_resources", {issue.issue_id for issue in plan.issues}
        )
        duplicate = next(
            issue
            for issue in plan.issues
            if issue.issue_id == "facts.duplicate.desired_resources"
        )
        self.assertEqual(duplicate.fact_id, "planning.resources")
        self.assertNotIn(private_value, serialized)

    def test_duplicate_and_unrelated_permutations_produce_identical_output(self) -> None:
        duplicate = ResourceFact(
            "directory.install",
            "/foreign/permutation-a",
            ObservedState.UNKNOWN,
            Ownership.UNKNOWN,
            Confidence.UNKNOWN,
        )
        unrelated = ResourceFact(
            "unrelated.permutation",
            "/foreign/permutation-b",
            ObservedState.PRESENT,
            Ownership.FOREIGN,
            Confidence.LOW,
        )
        first_resources = directory_facts() + (duplicate, unrelated)
        second_resources = (unrelated, duplicate) + tuple(reversed(directory_facts()))

        first = build_change_plan(
            configuration(), planning_facts(resources=first_resources)
        ).to_json()
        second = build_change_plan(
            configuration(), planning_facts(resources=second_resources)
        ).to_json()

        self.assertEqual(first, second)

    def test_malicious_control_secret_and_oversize_inputs_are_rejected(self) -> None:
        cases = (
            ("bad\nresource", "/foreign/path"),
            ("unrelated.secret", "/foreign/token=raw-secret"),
            ("unrelated.encoded", "/foreign/token%253Draw-secret"),
            ("unrelated.control", "/foreign/value%250Ainjected"),
            ("u" + "a" * 128, "/foreign/path"),
            ("unrelated.large", "/" + "x" * 4_096),
            ("unrelated.unicode", "/" + "e\u0301" * 2_048),
        )
        for resource_id, target in cases:
            with self.subTest(resource_id=resource_id[:30]):
                with self.assertRaises(ValueError):
                    ResourceFact(
                        resource_id,
                        target,
                        ObservedState.PRESENT,
                        Ownership.FOREIGN,
                        Confidence.HIGH,
                    )

        bad_config = replace(
            configuration(),
            paths=replace(
                configuration().paths,
                install="/opt/kitdev-sandboxes/token=configuration-secret",
            ),
        )
        with self.assertRaises(ValueError):
            build_change_plan(bad_config, planning_facts())

    def test_schema_version_requires_an_integer_instance(self) -> None:
        with self.assertRaises(TypeError):
            PlanningFacts("ubuntu", "26.04", (), schema_version=True)
        with self.assertRaises(TypeError):
            ChangePlan(
                LifecycleMode.PRODUCTION,
                (),
                (),
                schema_version="1",  # type: ignore[arg-type]
            )

    def test_unsafe_configuration_paths_cannot_authorize_root_actions(self) -> None:
        unsafe_paths = (
            "relative/path",
            "/etc",
            "/opt/kitdev-sandboxes/../outside",
            "/var/lib/kitdev-sandboxes//nested",
            "/opt/another-project",
        )
        for unsafe_path in unsafe_paths:
            with self.subTest(path=unsafe_path):
                config = replace(
                    configuration(),
                    paths=replace(configuration().paths, install=unsafe_path),
                )
                resources = tuple(
                    ResourceFact(
                        fact.resource_id,
                        unsafe_path if fact.resource_id == "directory.install" else fact.target,
                        ObservedState.ABSENT,
                        Ownership.UNOWNED,
                        Confidence.HIGH,
                    )
                    for fact in directory_facts()
                )
                with self.assertRaises(ValueError):
                    build_change_plan(config, planning_facts(resources=resources))

    def test_global_fact_action_and_json_limits_are_enforced(self) -> None:
        repeated = ResourceFact(
            "unrelated.limit",
            "/foreign/limit",
            ObservedState.PRESENT,
            Ownership.FOREIGN,
            Confidence.HIGH,
        )
        with self.assertRaises(ValueError):
            PlanningFacts("ubuntu", "26.04", (repeated,) * 1_025)
        action = planned_action()
        with self.assertRaises(ValueError):
            ChangePlan(LifecycleMode.PRODUCTION, (action,) * 257, ())

        large_action = planned_action(target="/" + "x" * 4_095)
        large_plan = ChangePlan(
            LifecycleMode.PRODUCTION, (large_action,) * 256, ()
        )
        with self.assertRaises(ValueError):
            large_plan.to_json()

    def test_unrelated_fact_content_is_not_inferred_or_serialized(self) -> None:
        secret = "token.secret-value"
        resources = directory_facts() + (
            ResourceFact(
                secret,
                "/foreign/secret-value",
                ObservedState.PRESENT,
                Ownership.FOREIGN,
                Confidence.HIGH,
            ),
        )

        serialized = build_change_plan(
            configuration(), planning_facts(resources=resources)
        ).to_json()

        self.assertNotIn(secret, serialized)
        self.assertNotIn("secret-value", serialized)

    def test_repeat_plans_and_serialization_are_byte_identical(self) -> None:
        config = configuration()
        facts = planning_facts(
            resources=tuple(reversed(directory_facts(ownership=Ownership.PROJECT)))
        )

        first = build_change_plan(config, facts)
        second = build_change_plan(config, facts)

        self.assertEqual(first, second)
        self.assertEqual(first.as_dict(), second.as_dict())
        self.assertEqual(first.to_json(), second.to_json())


if __name__ == "__main__":
    unittest.main()
