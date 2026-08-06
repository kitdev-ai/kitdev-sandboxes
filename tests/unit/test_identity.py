from __future__ import annotations

import json
import contextlib
import io
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from kitdev_sandboxes.config import IdentityConfig, LifecycleMode
from kitdev_sandboxes.cli import main
from kitdev_sandboxes.identity import (
    AccountFact,
    AllocationRange,
    CONTAINER_RUNTIME_IDS,
    FactStatus,
    GateState,
    GroupFact,
    IdentityFacts,
    IdentityAllocation,
    IdentityOrigin,
    IdentityPrerequisites,
    LxdPrerequisite,
    PathFact,
    SERVICE_ID_MAX,
    SERVICE_ID_MIN,
    SERVICE_NAMES,
    _lxd_prerequisite,
    _path_fact,
    build_identity_plan,
)
from tests.unit.test_composition import host_facts
from tests.unit.test_planning import configuration
from tests.schema_assertions import assert_schema_conforms


def configured(mode: LifecycleMode = LifecycleMode.PRODUCTION):
    return replace(configuration(mode), identity=IdentityConfig(operator="ubuntu"))


def discovered(*, accounts=None, groups=None, lxd=GateState.VERIFIED) -> IdentityFacts:
    service_accounts = tuple(
        AccountFact(name, FactStatus.ABSENT, IdentityOrigin.NONE) for name in SERVICE_NAMES
    )
    service_groups = tuple(
        GroupFact(name, FactStatus.ABSENT, IdentityOrigin.NONE) for name in SERVICE_NAMES
    )
    base_groups = service_groups + (
        GroupFact("kvm", FactStatus.OK, IdentityOrigin.LOCAL, 108),
        GroupFact("sudo", FactStatus.OK, IdentityOrigin.LOCAL, 27, ("ubuntu",)),
        GroupFact("lxd", FactStatus.OK, IdentityOrigin.LOCAL, 110, ("ubuntu",)),
    )
    return IdentityFacts(
        accounts=accounts or service_accounts + (
            AccountFact(
                "ubuntu",
                FactStatus.OK,
                IdentityOrigin.LOCAL,
                1000,
                1000,
                "/home/ubuntu",
                "/bin/bash",
                (27, 110),
            ),
        ),
        groups=groups or base_groups,
        occupied_uids=(0, 100, 101, 1000),
        occupied_gids=(0, 100, 102, 1000),
        allocation_range=AllocationRange(
            FactStatus.OK,
            SERVICE_ID_MIN,
            SERVICE_ID_MAX,
            SERVICE_ID_MIN,
            SERVICE_ID_MAX,
        ),
        nologin=PathFact("/usr/sbin/nologin", FactStatus.OK, "regular"),
        nonexistent=PathFact("/nonexistent", FactStatus.ABSENT),
        lxd=LxdPrerequisite(lxd, "fixture"),
    )


def verified() -> IdentityPrerequisites:
    return IdentityPrerequisites(*([GateState.VERIFIED] * 7))


class IdentityPlanningTests(unittest.TestCase):
    def test_unresolved_bootstrap_journal_host_key_and_recovery_suppress_actions(self) -> None:
        plan = build_identity_plan(configured(), host_facts(), discovered())

        self.assertTrue(plan.blocking)
        self.assertEqual(plan.actions, ())
        self.assertEqual(plan.exit_code, 5)
        ids = {issue.issue_id for issue in plan.issues}
        self.assertIn("identity.prerequisite.bootstrap", ids)
        self.assertIn("identity.prerequisite.journal", ids)
        self.assertIn("identity.prerequisite.host-key", ids)
        self.assertIn("identity.prerequisite.recovery", ids)

        root = Path(__file__).resolve().parents[2]
        schema = json.loads((root / "config" / "identity-access-plan.schema.json").read_text(encoding="utf-8"))
        fixture = json.loads((root / "tests" / "fixtures" / "plans" / "identity-access-dry-run-blocked.json").read_text(encoding="utf-8"))
        self.assertEqual(plan.as_dict(), fixture)
        assert_schema_conforms(plan.as_dict(), schema)

    def test_verified_plan_allocates_deterministically_and_only_worker_gets_kvm(self) -> None:
        plan = build_identity_plan(configured(), host_facts(), discovered(), verified())

        self.assertFalse(plan.blocking)
        self.assertEqual([(item.name, item.uid, item.gid) for item in plan.allocations], [
            ("kitdev-e2b", 61000, 61000),
            ("kitdev-proxy", 61001, 61001),
            ("kitdev-worker", 61002, 61002),
        ])
        user_states = {
            action.target: action.desired_state
            for action in plan.actions
            if action.category == "account"
        }
        self.assertEqual(set(user_states), set(SERVICE_NAMES))
        self.assertIn("supplementary=kvm;", user_states["kitdev-worker"])
        self.assertTrue(all("supplementary=none" in user_states[name] for name in SERVICE_NAMES[:2]))
        self.assertNotIn("datastore", user_states["kitdev-worker"])
        self.assertNotIn("supplementary=kitdev", user_states["kitdev-worker"])
        self.assertIn("identity.operator.lxd", {action.action_id for action in plan.actions})
        self.assertEqual(plan.to_json(), plan.to_json())
        self.assertRegex(plan.plan_hash, r"^sha256:[0-9a-f]{64}$")

    def test_first_free_project_ids_skip_occupied_values_deterministically(self) -> None:
        facts = replace(
            discovered(),
            occupied_uids=(0, 101, 999, 10_001, 61_000, 61_002),
            occupied_gids=(0, 101, 999, 10_001, 61_000, 61_001),
        )

        plan = build_identity_plan(configured(), host_facts(), facts, verified())

        self.assertEqual(
            [(item.uid, item.gid) for item in plan.allocations],
            [(61_001, 61_002), (61_003, 61_003), (61_004, 61_004)],
        )
        self.assertTrue(
            all(
                SERVICE_ID_MIN <= value <= SERVICE_ID_MAX
                and value not in CONTAINER_RUNTIME_IDS
                for item in plan.allocations
                for value in (item.uid, item.gid)
            )
        )

    def test_authenticated_manifest_mapping_is_normalized_and_honored(self) -> None:
        mapping = (
            IdentityAllocation("kitdev-worker", 61_112, 61_212),
            IdentityAllocation("kitdev-e2b", 61_110, 61_210),
            IdentityAllocation("kitdev-proxy", 61_111, 61_211),
        )

        plan = build_identity_plan(
            configured(),
            host_facts(),
            discovered(),
            verified(),
            manifest_allocations=mapping,
        )

        self.assertFalse(plan.blocking)
        self.assertEqual(
            [(item.name, item.uid, item.gid) for item in plan.allocations],
            [
                ("kitdev-e2b", 61_110, 61_210),
                ("kitdev-proxy", 61_111, 61_211),
                ("kitdev-worker", 61_112, 61_212),
            ],
        )
        self.assertTrue(
            all(
                f"manifest-mapping={item.name}:{item.uid}:{item.gid}" in action.desired_state
                for item in plan.allocations
                for action in plan.actions
                if action.target == item.name
            )
        )

    def test_invalid_or_occupied_manifest_mapping_blocks_the_whole_phase(self) -> None:
        valid = tuple(
            IdentityAllocation(name, 61_100 + index, 61_200 + index)
            for index, name in enumerate(SERVICE_NAMES)
        )
        cases = {
            "partial": valid[:2],
            "out-of-band": (
                IdentityAllocation("kitdev-e2b", 101, 61_200),
                *valid[1:],
            ),
            "occupied": valid,
        }
        for name, mapping in cases.items():
            facts = discovered()
            if name == "occupied":
                facts = replace(facts, occupied_uids=facts.occupied_uids + (61_100,))
            with self.subTest(name=name):
                plan = build_identity_plan(
                    configured(),
                    host_facts(),
                    facts,
                    verified(),
                    manifest_allocations=mapping,
                )
                self.assertTrue(plan.blocking)
                self.assertEqual(plan.actions, ())
                if name != "occupied":
                    self.assertEqual(plan.allocations, ())
                self.assertTrue(
                    any(issue.issue_id.startswith("identity.manifest.") for issue in plan.issues)
                )

    def test_manifest_owned_worker_must_have_exactly_kvm_supplementary_group(self) -> None:
        mapping = tuple(
            IdentityAllocation(name, 61_100 + index, 61_100 + index)
            for index, name in enumerate(SERVICE_NAMES)
        )
        service_accounts = tuple(
            AccountFact(
                item.name,
                FactStatus.OK,
                IdentityOrigin.LOCAL,
                item.uid,
                item.gid,
                "/nonexistent",
                "/usr/sbin/nologin",
                (item.gid, 108) if item.name == "kitdev-worker" else (item.gid,),
            )
            for item in mapping
        )
        operator = next(item for item in discovered().accounts if item.name == "ubuntu")
        service_groups = tuple(
            GroupFact(item.name, FactStatus.OK, IdentityOrigin.LOCAL, item.gid)
            for item in mapping
        )
        base = discovered(
            accounts=service_accounts + (operator,),
            groups=service_groups
            + (
                GroupFact("kvm", FactStatus.OK, IdentityOrigin.LOCAL, 108, ("kitdev-worker",)),
                GroupFact("sudo", FactStatus.OK, IdentityOrigin.LOCAL, 27, ("ubuntu",)),
                GroupFact("lxd", FactStatus.OK, IdentityOrigin.LOCAL, 110, ("ubuntu",)),
            ),
        )
        base = replace(
            base,
            occupied_uids=tuple(item.uid for item in mapping) + (1000,),
            occupied_gids=tuple(item.gid for item in mapping) + (27, 108, 110, 1000),
        )

        accepted = build_identity_plan(
            configured(),
            host_facts(),
            base,
            verified(),
            manifest_allocations=mapping,
        )
        self.assertFalse(accepted.blocking)

        extra_group = replace(
            base,
            accounts=tuple(
                replace(item, supplementary_gids=item.supplementary_gids + (999,))
                if item.name == "kitdev-worker"
                else item
                for item in base.accounts
            ),
        )
        rejected = build_identity_plan(
            configured(),
            host_facts(),
            extra_group,
            verified(),
            manifest_allocations=mapping,
        )
        self.assertTrue(rejected.blocking)
        self.assertEqual(rejected.actions, ())
        self.assertIn(
            "identity.collision.kitdev-worker",
            {item.issue_id for item in rejected.issues},
        )

    def test_permutation_does_not_change_plan_or_hash(self) -> None:
        facts = discovered()
        reversed_facts = replace(
            facts,
            accounts=tuple(reversed(facts.accounts)),
            groups=tuple(reversed(facts.groups)),
            occupied_uids=tuple(reversed(facts.occupied_uids)),
            occupied_gids=tuple(reversed(facts.occupied_gids)),
        )
        first = build_identity_plan(configured(), host_facts(), facts, verified())
        second = build_identity_plan(configured(), host_facts(), reversed_facts, verified())

        self.assertEqual(first.to_json(), second.to_json())

    def test_foreign_collision_and_malformed_runtime_enum_block(self) -> None:
        facts = discovered()
        collision = replace(
            facts,
            accounts=tuple(
                AccountFact(item.name, FactStatus.OK, IdentityOrigin.NSS, 500, 500, "/x", "/bin/sh")
                if item.name == "kitdev-e2b" else item
                for item in facts.accounts
            ),
        )
        malformed = replace(
            facts,
            accounts=tuple(
                replace(item, status="absent") if item.name == "kitdev-e2b" else item
                for item in facts.accounts
            ),
        )

        self.assertEqual(build_identity_plan(configured(), host_facts(), collision, verified()).actions, ())
        malformed_plan = build_identity_plan(configured(), host_facts(), malformed, verified())
        self.assertEqual(malformed_plan.actions, ())
        self.assertIn("identity.discovery.invalid", {issue.issue_id for issue in malformed_plan.issues})

    def test_snap_absence_alone_never_verifies_lxd_non_use(self) -> None:
        class Result:
            succeeded = False
            outcome = "nonzero"
            stdout = type("Stream", (), {"text": ""})()
            stderr = type("Stream", (), {"text": "error: no matching snaps installed"})()

        class Runner:
            def __init__(self) -> None:
                self.argv = None

            def run(self, command):
                self.argv = command.argv
                return Result()

        runner = Runner()
        lxd = _lxd_prerequisite(runner)  # type: ignore[arg-type]
        plan = build_identity_plan(
            configured(),
            host_facts(),
            replace(discovered(), lxd=lxd),
            verified(),
        )

        self.assertEqual(runner.argv, ("/usr/bin/snap", "list", "lxd"))
        self.assertIs(lxd.non_use, GateState.UNRESOLVED)
        self.assertEqual(plan.actions, ())
        self.assertIn(
            "identity.prerequisite.lxd-non-use",
            {issue.issue_id for issue in plan.issues},
        )

    def test_identity_path_fact_rejects_symlinked_parent(self) -> None:
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as directory:
            root = Path(directory)
            real = root / "real"
            real.mkdir()
            target = real / "nologin"
            target.write_text("exit 1\n", encoding="utf-8")
            linked = root / "linked"
            linked.symlink_to(real, target_is_directory=True)

            observed = _path_fact(linked / "nologin")

        self.assertIs(observed.status, FactStatus.UNKNOWN)

    def test_duplicate_desired_fact_blocks_independent_of_order(self) -> None:
        facts = discovered()
        duplicate = AccountFact("kitdev-e2b", FactStatus.ABSENT, IdentityOrigin.NONE)
        first = replace(facts, accounts=facts.accounts + (duplicate,))
        second = replace(facts, accounts=(duplicate,) + facts.accounts)

        left = build_identity_plan(configured(), host_facts(), first, verified())
        right = build_identity_plan(configured(), host_facts(), second, verified())
        self.assertEqual(left.to_json(), right.to_json())
        self.assertEqual(left.actions, ())

    def test_lxd_non_use_unknown_blocks_operator_removal_and_whole_phase(self) -> None:
        plan = build_identity_plan(
            configured(), host_facts(), discovered(lxd=GateState.UNRESOLVED), verified()
        )
        self.assertTrue(plan.blocking)
        self.assertEqual(plan.actions, ())

    def test_ubuntu_2504_is_never_identity_apply_eligible(self) -> None:
        for mode in LifecycleMode:
            with self.subTest(mode=mode):
                plan = build_identity_plan(
                    configured(mode), host_facts(os_version_id="25.04"), discovered(), verified()
                )
                self.assertEqual(plan.exit_code, 3)
                self.assertEqual(plan.actions, ())

    def test_report_redacts_explicit_operator_and_is_bounded(self) -> None:
        plan = build_identity_plan(configured(), host_facts(), discovered(), verified())
        payload = json.loads(plan.to_json())
        self.assertNotIn("ubuntu", plan.to_json())
        self.assertEqual(payload["phase"], "identity-access")
        self.assertLess(len(plan.to_json().encode("utf-8")), 1_048_576)

    def test_phase_cli_is_read_only_and_does_not_run_base_collectors(self) -> None:
        calls: list[str] = []
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config_path = root / "operator.yaml"
            config_path.write_text("identity:\n  operator: ubuntu\n", encoding="utf-8")
            sentinel = root / "sentinel"
            sentinel.write_bytes(b"unchanged")
            before = sorted((path.name, path.read_bytes()) for path in root.iterdir())

            def identity(config):
                calls.append(config.identity.operator or "missing")
                return discovered()

            output = io.StringIO()
            with contextlib.chdir(root), contextlib.redirect_stdout(output):
                code = main(
                    [
                        "install",
                        "--phase",
                        "identity-access",
                        "--dry-run",
                        "--json",
                        "--config",
                        str(config_path),
                    ],
                    fact_collector=host_facts,
                    linux_fact_collector=lambda _config: self.fail("base collector must not run"),
                    directory_fact_collector=lambda _config: self.fail("base collector must not run"),
                    identity_fact_collector=identity,
                )
            after = sorted((path.name, path.read_bytes()) for path in root.iterdir())

        self.assertEqual(code, 5)
        self.assertEqual(calls, ["ubuntu"])
        self.assertEqual(before, after)
        payload = json.loads(output.getvalue())
        self.assertEqual(payload["phase"], "identity-access")
        self.assertEqual(payload["actions"], [])

    def test_phase_apply_and_malformed_operator_are_rejected_before_collection(self) -> None:
        called = False

        def collector(_config):
            nonlocal called
            called = True
            return discovered()

        with contextlib.redirect_stderr(io.StringIO()):
            apply_code = main(
                ["install", "--phase", "identity-access"],
                fact_collector=host_facts,
                identity_fact_collector=collector,
            )
        self.assertEqual(apply_code, 2)
        self.assertFalse(called)

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "bad.yaml"
            path.write_text("identity:\n  operator: root\n", encoding="utf-8")
            with contextlib.redirect_stderr(io.StringIO()):
                config_code = main(
                    ["install", "--phase", "identity-access", "--dry-run", "--config", str(path)],
                    fact_collector=host_facts,
                    identity_fact_collector=collector,
                )
        self.assertEqual(config_code, 2)
        self.assertFalse(called)


if __name__ == "__main__":
    unittest.main()
