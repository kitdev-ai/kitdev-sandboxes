from __future__ import annotations

import hashlib
import json
import multiprocessing
import os
import stat
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from kitdev_sandboxes.journal import JournalRecord, JournalState, JournalStore
from kitdev_sandboxes.stage05 import (
    CONFIG_ROOT,
    EXPERIMENTS_ROOT,
    JOURNAL_PATH,
    JOURNAL_ROOT,
    MARKER_PATH,
    STATE_ROOT,
    WORKSPACE,
    Stage05Crash,
    Stage05Error,
    Stage05Paths,
    Stage05Reconciler,
    build_plan,
)


BUNDLE_DIGEST = "0" * 64


def temporary_trust_boundary(root: Path) -> Path:
    chain = (Path("/"),) + tuple(reversed(root.parents[:-1])) + (root,)
    for path in chain:
        metadata = path.stat()
        if metadata.st_uid != 0 or metadata.st_gid != 0 or metadata.st_mode & 0o022:
            return path
    return root


def prepare_test_root(root: Path) -> Stage05Paths:
    root.chmod(0o700)
    for relative in (
        "var/lib",
        "etc/systemd/system/multi-user.target.wants",
        "usr/lib/systemd/system",
        "lib/systemd/system",
        "opt",
    ):
        path = root / relative
        path.mkdir(parents=True, exist_ok=True)
        current = path
        while current != root:
            current.chmod(0o755)
            current = current.parent
    return Stage05Paths(root, trusted_prefix=temporary_trust_boundary(root))


def hold_stage05_lock(root: str, ready: object) -> None:
    path = Path(root)
    paths = Stage05Paths(path, trusted_prefix=temporary_trust_boundary(path))
    reconciler = Stage05Reconciler(
        BUNDLE_DIGEST,
        paths=paths,
        expected_uid=os.getuid(),
        expected_gid=os.getgid(),
        mount_id=lambda _descriptor: 7,
        xattrs=lambda _descriptor: (),
        service_state=lambda _unit: "absent",
    )
    store = JournalStore(paths.actual(JOURNAL_ROOT), reconciler._journal_policy())
    with store.locked():
        ready.set()  # type: ignore[attr-defined]
        time.sleep(60)


class Stage05Tests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.paths = prepare_test_root(self.root)

    def actual(self, canonical: Path) -> Path:
        return self.paths.actual(canonical)

    def reconciler(self, **kwargs: object) -> Stage05Reconciler:
        mount_id = kwargs.pop("mount_id", lambda _descriptor: 7)
        xattrs = kwargs.pop("xattrs", lambda _descriptor: ())
        service_state = kwargs.pop("service_state", lambda _unit: "absent")
        return Stage05Reconciler(
            BUNDLE_DIGEST,
            paths=self.paths,
            expected_uid=os.getuid(),
            expected_gid=os.getgid(),
            mount_id=mount_id,  # type: ignore[arg-type]
            xattrs=xattrs,  # type: ignore[arg-type]
            service_state=service_state,  # type: ignore[arg-type]
            **kwargs,  # type: ignore[arg-type]
        )

    def test_canonical_marker_plan_and_journal_bytes_match_contract_fixture(self) -> None:
        plan = build_plan(BUNDLE_DIGEST)
        self.assertEqual(len(plan.marker_bytes), 236)
        self.assertEqual(
            plan.marker_hash,
            "sha256:08ddad347af2341bbb84361c3d6d4667fc849dc0079633a4dac68f84c7738a45",
        )
        self.assertEqual(len(plan.plan_bytes), 1108)
        self.assertEqual(
            plan.plan_hash,
            "sha256:f54dc001485f6dfab20559bc7b06a3008fe69bf50b6637892b57b3b3fcbcd65f",
        )
        encoded_record = (
            json.dumps(plan.record.as_dict(), sort_keys=True, separators=(",", ":")) + "\n"
        ).encode("ascii")
        self.assertEqual(len(encoded_record), 1005)
        self.assertEqual(
            "sha256:" + hashlib.sha256(encoded_record).hexdigest(),
            "sha256:0932ab76797533b27bf4e5b6a45b9dc8994016f5b4507269fafb0e4ae7e2ce66",
        )
        self.assertEqual(
            [resource.resource_id for resource in plan.resources],
            [
                "directory.config",
                "directory.experiments",
                "directory.workspace",
                "file.authorization",
            ],
        )

    def test_pristine_apply_apply_and_rollback_rollback(self) -> None:
        reconciler = self.reconciler()
        first = reconciler.execute()
        self.assertEqual(first.journal_state, "validated")
        marker_before = self.actual(MARKER_PATH).read_bytes()
        journal_before = self.actual(JOURNAL_PATH).read_bytes()

        second = self.reconciler().execute()
        self.assertEqual(second.journal_state, "validated")
        self.assertEqual(self.actual(MARKER_PATH).read_bytes(), marker_before)
        self.assertEqual(self.actual(JOURNAL_PATH).read_bytes(), journal_before)

        rolled = self.reconciler().rollback()
        self.assertEqual(rolled.journal_state, "rolled_back")
        for path in (MARKER_PATH, WORKSPACE, EXPERIMENTS_ROOT, CONFIG_ROOT):
            self.assertFalse(self.actual(path).exists())
        retained = self.actual(JOURNAL_PATH).read_bytes()
        again = self.reconciler().rollback()
        self.assertEqual(again.journal_state, "rolled_back")
        self.assertEqual(self.actual(JOURNAL_PATH).read_bytes(), retained)
        self.assertTrue(self.actual(STATE_ROOT).is_dir())
        self.assertTrue(self.actual(JOURNAL_ROOT).is_dir())

    def test_observe_absent_never_allocates_roots(self) -> None:
        result = self.reconciler().observe("before")
        self.assertEqual(result.journal_root, "absent")
        self.assertFalse(self.actual(STATE_ROOT).exists())

    def test_journal_is_durable_before_first_managed_resource(self) -> None:
        def crash(point: str) -> None:
            if point == "before_create_config":
                raise Stage05Crash()

        with self.assertRaises(Stage05Crash):
            self.reconciler(fault=crash).execute()
        self.assertTrue(self.actual(JOURNAL_PATH).is_file())
        self.assertFalse(self.actual(CONFIG_ROOT).exists())
        store = JournalStore(self.actual(JOURNAL_ROOT), self.reconciler()._journal_policy())
        self.assertEqual(store.load("ovh-lab-stage05-v1").state, JournalState.APPLYING)

    def test_exact_provisional_0700_config_is_recovered_only_with_journal(self) -> None:
        fired = False

        def crash(point: str) -> None:
            nonlocal fired
            if point == "after_mkdir_config" and not fired:
                fired = True
                raise Stage05Crash()

        with self.assertRaises(Stage05Crash):
            self.reconciler(fault=crash).execute()
        provisional = self.actual(CONFIG_ROOT)
        self.assertEqual(stat.S_IMODE(provisional.stat().st_mode), 0o700)
        observed = self.reconciler().observe("before")
        self.assertEqual(observed.journal_state, "applying")
        self.assertEqual(stat.S_IMODE(provisional.stat().st_mode), 0o700)
        result = self.reconciler().execute()
        self.assertEqual(result.journal_state, "validated")
        self.assertEqual(stat.S_IMODE(provisional.stat().st_mode), 0o755)

    def test_exact_empty_provisional_state_root_is_the_only_bootstrap_mode_repair(self) -> None:
        fired = False

        def crash(point: str) -> None:
            nonlocal fired
            if point == "after_mkdir_state_root" and not fired:
                fired = True
                raise Stage05Crash()

        with self.assertRaises(Stage05Crash):
            self.reconciler(fault=crash).execute()
        state = self.actual(STATE_ROOT)
        self.assertEqual(stat.S_IMODE(state.stat().st_mode), 0o700)
        observed = self.reconciler().observe("before")
        self.assertEqual(observed.journal_root, "bootstrap-residue")
        self.assertEqual(stat.S_IMODE(state.stat().st_mode), 0o700)
        self.assertEqual(self.reconciler().execute().journal_state, "validated")
        self.assertEqual(stat.S_IMODE(state.stat().st_mode), 0o755)

        self.reconciler().rollback()
        state.chmod(0o700)
        (state / "foreign").write_bytes(b"untouched")
        with self.assertRaises(Stage05Error):
            self.reconciler().rollback()
        self.assertEqual(stat.S_IMODE(state.stat().st_mode), 0o700)
        self.assertEqual((state / "foreign").read_bytes(), b"untouched")

    def test_marker_is_published_last_and_rollback_removes_it_first(self) -> None:
        def before_marker(point: str) -> None:
            if point == "before_write_marker":
                raise Stage05Crash()

        with self.assertRaises(Stage05Crash):
            self.reconciler(fault=before_marker).execute()
        for path in (CONFIG_ROOT, EXPERIMENTS_ROOT, WORKSPACE):
            self.assertTrue(self.actual(path).is_dir())
        self.assertFalse(self.actual(MARKER_PATH).exists())
        self.reconciler().execute()

        def after_marker_removal(point: str) -> None:
            if point == "after_remove_marker":
                raise Stage05Crash()

        with self.assertRaises(Stage05Crash):
            self.reconciler(fault=after_marker_removal).rollback()
        self.assertFalse(self.actual(MARKER_PATH).exists())
        for path in (CONFIG_ROOT, EXPERIMENTS_ROOT, WORKSPACE):
            self.assertTrue(self.actual(path).is_dir())
        self.assertEqual(self.reconciler().rollback().journal_state, "rolled_back")

    def test_linked_marker_publish_residue_is_recovered_exactly(self) -> None:
        def crash(point: str) -> None:
            if point == "after_publish_parent_fsync_marker":
                raise Stage05Crash()

        with self.assertRaises(Stage05Crash):
            self.reconciler(fault=crash).execute()
        marker = self.actual(MARKER_PATH)
        residues = list(marker.parent.glob(".disposable-ovh-lab.tmp.*"))
        self.assertEqual(len(residues), 1)
        self.assertEqual(marker.stat().st_ino, residues[0].stat().st_ino)
        self.assertEqual(marker.stat().st_nlink, 2)
        observed = self.reconciler().observe("before")
        self.assertEqual(observed.journal_state, "applying")
        self.assertEqual(marker.stat().st_nlink, 2)
        result = self.reconciler().execute()
        self.assertEqual(result.journal_state, "validated")
        self.assertEqual(marker.stat().st_nlink, 1)
        self.assertEqual(list(marker.parent.glob(".disposable-ovh-lab.tmp.*")), [])

    def test_read_only_observation_accepts_but_never_repairs_linked_journal_residue(self) -> None:
        real_unlink = os.unlink

        def crash_journal_unlink(path: object, *args: object, **kwargs: object) -> None:
            if str(path).startswith(".ovh-lab-stage05-v1.journal.json.tmp."):
                raise Stage05Crash()
            real_unlink(path, *args, **kwargs)  # type: ignore[arg-type]

        with patch("kitdev_sandboxes.journal.os.unlink", side_effect=crash_journal_unlink):
            with self.assertRaises(Stage05Crash):
                self.reconciler().execute()
        journal = self.actual(JOURNAL_PATH)
        residues = tuple(journal.parent.glob(f".{journal.name}.tmp.*"))
        self.assertEqual(len(residues), 1)
        residue = residues[0]
        self.assertEqual(journal.stat().st_nlink, 2)
        observed = self.reconciler().observe("before")
        self.assertEqual(observed.journal_state, "planned")
        self.assertTrue(residue.exists())
        self.assertEqual(journal.stat().st_nlink, 2)
        self.assertEqual(self.reconciler().execute().journal_state, "validated")
        self.assertFalse(residue.exists())
        self.assertEqual(journal.stat().st_nlink, 1)

    def test_caught_stable_failure_records_failed_and_allows_only_rollback(self) -> None:
        def fail(point: str) -> None:
            if point == "before_write_marker":
                raise Stage05Error("resource_desired_mismatch")

        with self.assertRaisesRegex(Stage05Error, "resource_desired_mismatch"):
            self.reconciler(fault=fail).execute()
        store = JournalStore(self.actual(JOURNAL_ROOT), self.reconciler()._journal_policy())
        self.assertEqual(store.load("ovh-lab-stage05-v1").state, JournalState.FAILED)
        with self.assertRaisesRegex(Stage05Error, "illegal_recovery_state"):
            self.reconciler().execute()
        self.assertEqual(self.reconciler().rollback().journal_state, "rolled_back")

    def test_foreign_workspace_entry_blocks_rollback_without_removing_marker(self) -> None:
        self.reconciler().execute()
        foreign = self.actual(WORKSPACE) / "foreign"
        foreign.write_bytes(b"untouched")
        marker = self.actual(MARKER_PATH).read_bytes()
        with self.assertRaisesRegex(Stage05Error, "rollback_foreign_state"):
            self.reconciler().rollback()
        self.assertEqual(foreign.read_bytes(), b"untouched")
        self.assertEqual(self.actual(MARKER_PATH).read_bytes(), marker)

    def test_symlinked_production_path_and_present_service_fail_closed(self) -> None:
        production = self.actual(Path("/etc/kitdev-sandboxes/production"))
        production.parent.mkdir(mode=0o755)
        production.symlink_to(self.root / "outside")
        with self.assertRaisesRegex(Stage05Error, "production_state_unknown"):
            self.reconciler().observe("before")
        production.unlink()
        with self.assertRaisesRegex(Stage05Error, "production_state_present"):
            self.reconciler(service_state=lambda _unit: "present").observe("before")

    def test_wrong_mode_xattr_and_mount_boundary_block_adoption(self) -> None:
        state = self.actual(STATE_ROOT)
        state.mkdir(mode=0o700)
        state.chmod(0o755)
        (state / "foreign").write_bytes(b"untouched")
        with self.assertRaises(Stage05Error):
            self.reconciler().execute()
        self.assertEqual((state / "foreign").read_bytes(), b"untouched")

        (state / "foreign").unlink()
        state.rmdir()
        self.reconciler().execute()
        with self.assertRaises(Stage05Error):
            self.reconciler(xattrs=lambda _descriptor: ("security.capability",)).execute()

    def test_existing_resource_mount_boundary_wrong_mode_xattr_and_hardlink_block(self) -> None:
        state = self.actual(STATE_ROOT)
        state.mkdir(mode=0o755)
        state.chmod(0o755)
        state_inode = state.stat().st_ino

        def split_mount(descriptor: int) -> int:
            return 8 if os.fstat(descriptor).st_ino == state_inode else 7

        with self.assertRaisesRegex(Stage05Error, "mount_boundary"):
            self.reconciler(mount_id=split_mount).execute()
        state.rmdir()

        self.reconciler().execute()
        workspace = self.actual(WORKSPACE)
        workspace.chmod(0o755)
        with self.assertRaises(Stage05Error):
            self.reconciler().execute()
        workspace.chmod(0o700)

        marker = self.actual(MARKER_PATH)
        marker_inode = marker.stat().st_ino

        def marker_xattr(descriptor: int) -> tuple[str, ...]:
            return (
                ("security.capability",)
                if os.fstat(descriptor).st_ino == marker_inode
                else ()
            )

        with self.assertRaises(Stage05Error):
            self.reconciler(xattrs=marker_xattr).execute()

        for attribute in ("system.posix_acl_access", "system.posix_acl_default"):
            with self.subTest(attribute=attribute), self.assertRaises(Stage05Error):
                self.reconciler(
                    xattrs=lambda descriptor, value=attribute: (
                        (value,) if os.fstat(descriptor).st_ino == marker_inode else ()
                    )
                ).execute()

        real_fstat = os.fstat

        def wrong_owner(descriptor: int) -> os.stat_result:
            metadata = real_fstat(descriptor)
            if metadata.st_ino != marker_inode:
                return metadata
            values = list(metadata)
            values[4] = os.getuid() + 1
            return os.stat_result(values)

        with patch("kitdev_sandboxes.stage05.os.fstat", side_effect=wrong_owner):
            with self.assertRaises(Stage05Error):
                self.reconciler().execute()

        hardlink = self.root / "marker-hardlink"
        os.link(marker, hardlink)
        with self.assertRaises(Stage05Error):
            self.reconciler().execute()
        self.assertEqual(hardlink.read_bytes(), marker.read_bytes())

    def test_final_and_ancestor_symlinks_are_never_followed(self) -> None:
        self.reconciler().execute()
        workspace = self.actual(WORKSPACE)
        moved = self.root / "moved-workspace"
        workspace.rename(moved)
        workspace.symlink_to(moved, target_is_directory=True)
        with self.assertRaises(Stage05Error):
            self.reconciler().execute()
        self.assertTrue(workspace.is_symlink())
        self.assertTrue(moved.is_dir())

    def test_operation_lock_blocks_readers_and_second_mutator(self) -> None:
        self.reconciler().execute()
        store = JournalStore(self.actual(JOURNAL_ROOT), self.reconciler()._journal_policy())
        with store.locked():
            with self.assertRaisesRegex(Stage05Error, "transaction_busy"):
                self.reconciler().observe("after")
            with self.assertRaisesRegex(Stage05Error, "transaction_busy"):
                self.reconciler().execute()

    def test_operation_lock_blocks_observation_before_final_journal_exists(self) -> None:
        state = self.actual(STATE_ROOT)
        state.mkdir(mode=0o755)
        state.chmod(0o755)
        journal = self.actual(JOURNAL_ROOT)
        journal.mkdir(mode=0o700)
        store = JournalStore(journal, self.reconciler()._journal_policy())
        with store.locked():
            with self.assertRaisesRegex(Stage05Error, "transaction_busy"):
                self.reconciler().observe("after")

    def test_observe_rejects_unrelated_canonical_journal_temp(self) -> None:
        self.reconciler().execute()
        residue = self.actual(JOURNAL_ROOT) / (
            ".ovh-lab-stage05-v1.journal.json.tmp." + "d" * 32
        )
        residue.write_bytes(b"not-json\n")
        residue.chmod(0o600)
        with self.assertRaisesRegex(Stage05Error, "journal_corrupt"):
            self.reconciler().observe("after")
        self.assertEqual(residue.read_bytes(), b"not-json\n")

    def test_execute_does_not_fsync_corrupt_unpublished_journal_residue(self) -> None:
        state = self.actual(STATE_ROOT)
        state.mkdir(mode=0o755)
        state.chmod(0o755)
        journal = self.actual(JOURNAL_ROOT)
        journal.mkdir(mode=0o700)
        residue = journal / (".ovh-lab-stage05-v1.journal.json.tmp." + "e" * 32)
        residue.write_bytes(b"not-json\n")
        residue.chmod(0o600)
        real_fsync = os.fsync
        synced: list[int] = []

        def recording_fsync(descriptor: int) -> None:
            synced.append(descriptor)
            real_fsync(descriptor)

        with patch("kitdev_sandboxes.stage05.os.fsync", side_effect=recording_fsync):
            with self.assertRaises(Stage05Error):
                self.reconciler().execute()
        self.assertEqual(synced, [])
        self.assertEqual(residue.read_bytes(), b"not-json\n")

    def test_validated_execute_rejects_and_preserves_separate_exact_temp(self) -> None:
        self.reconciler().execute()
        final_before = self.actual(JOURNAL_PATH).read_bytes()
        residue = self.actual(JOURNAL_ROOT) / (
            ".ovh-lab-stage05-v1.journal.json.tmp." + "a" * 32
        )
        payload = (
            json.dumps(
                build_plan(BUNDLE_DIGEST).record.as_dict(),
                sort_keys=True,
                separators=(",", ":"),
            )
            + "\n"
        ).encode("ascii")
        residue.write_bytes(payload)
        residue.chmod(0o600)
        with self.assertRaisesRegex(Stage05Error, "journal_conflict"):
            self.reconciler().execute()
        self.assertEqual(residue.read_bytes(), payload)
        self.assertEqual(self.actual(JOURNAL_PATH).read_bytes(), final_before)

    def test_validated_linked_journal_residue_is_impossible_and_preserved(self) -> None:
        self.reconciler().execute()
        journal = self.actual(JOURNAL_PATH)
        residue = journal.parent / (f".{journal.name}.tmp." + "f" * 32)
        os.link(journal, residue)
        with self.assertRaisesRegex(Stage05Error, "journal_conflict"):
            self.reconciler().observe("after")
        with self.assertRaisesRegex(Stage05Error, "journal_conflict"):
            self.reconciler().execute()
        self.assertTrue(residue.exists())
        self.assertEqual(journal.stat().st_nlink, 2)

    def test_rolled_back_rollback_rejects_and_preserves_every_separate_temp(self) -> None:
        self.reconciler().execute()
        self.reconciler().rollback()
        final_before = self.actual(JOURNAL_PATH).read_bytes()
        planned = (
            json.dumps(
                build_plan(BUNDLE_DIGEST).record.as_dict(),
                sort_keys=True,
                separators=(",", ":"),
            )
            + "\n"
        ).encode("ascii")
        for suffix, payload in (("b", b"not-json\n"), ("c", planned)):
            residue = self.actual(JOURNAL_ROOT) / (
                ".ovh-lab-stage05-v1.journal.json.tmp." + suffix * 32
            )
            residue.write_bytes(payload)
            residue.chmod(0o600)
            with self.subTest(payload=payload[:8]), self.assertRaises(Stage05Error):
                self.reconciler().rollback()
            self.assertEqual(residue.read_bytes(), payload)
            self.assertEqual(self.actual(JOURNAL_PATH).read_bytes(), final_before)
            residue.unlink()

    def test_nonterminal_exact_transition_temp_is_recovered_before_mutation(self) -> None:
        def crash(point: str) -> None:
            if point == "before_journal_transition_applied":
                raise Stage05Crash()

        with self.assertRaises(Stage05Crash):
            self.reconciler(fault=crash).execute()
        store = JournalStore(self.actual(JOURNAL_ROOT), self.reconciler()._journal_policy())
        applying = store.load("ovh-lab-stage05-v1")
        applied = JournalRecord(
            applying.install_id,
            applying.plan_id,
            applying.plan_hash,
            applying.resources,
            applying.transitions + (JournalState.APPLIED,),
        )
        residue = self.actual(JOURNAL_ROOT) / (
            ".ovh-lab-stage05-v1.journal.json.tmp." + "7" * 32
        )
        residue.write_bytes(
            (
                json.dumps(applied.as_dict(), sort_keys=True, separators=(",", ":"))
                + "\n"
            ).encode("ascii")
        )
        residue.chmod(0o600)
        self.assertEqual(self.reconciler().execute().journal_state, "validated")
        self.assertFalse(residue.exists())

    def test_rollback_recovers_every_exact_outbound_execute_transition_temp(self) -> None:
        cases = (
            (
                "before_journal_transition_applying",
                JournalState.PLANNED,
                JournalState.APPLYING,
            ),
            (
                "before_journal_transition_applied",
                JournalState.APPLYING,
                JournalState.APPLIED,
            ),
            (
                "before_journal_transition_applied",
                JournalState.APPLYING,
                JournalState.FAILED,
            ),
            (
                "after_journal_transition_applied",
                JournalState.APPLIED,
                JournalState.VALIDATED,
            ),
        )
        for crash_point, current_state, residue_state in cases:
            with self.subTest(
                current=current_state.value, residue=residue_state.value
            ), tempfile.TemporaryDirectory() as directory:
                root = Path(directory).resolve()
                paths = prepare_test_root(root)
                arguments = {
                    "paths": paths,
                    "expected_uid": os.getuid(),
                    "expected_gid": os.getgid(),
                    "mount_id": lambda _descriptor: 7,
                    "xattrs": lambda _descriptor: (),
                    "service_state": lambda _unit: "absent",
                }

                def crash(point: str) -> None:
                    if point == crash_point:
                        raise Stage05Crash()

                with self.assertRaises(Stage05Crash):
                    Stage05Reconciler(BUNDLE_DIGEST, fault=crash, **arguments).execute()
                reconciler = Stage05Reconciler(BUNDLE_DIGEST, **arguments)
                store = JournalStore(paths.actual(JOURNAL_ROOT), reconciler._journal_policy())
                current = store.load("ovh-lab-stage05-v1")
                self.assertIs(current.state, current_state)
                residue_record = JournalRecord(
                    current.install_id,
                    current.plan_id,
                    current.plan_hash,
                    current.resources,
                    current.transitions + (residue_state,),
                )
                residue_path = paths.actual(JOURNAL_ROOT) / (
                    ".ovh-lab-stage05-v1.journal.json.tmp." + "8" * 32
                )
                residue_path.write_bytes(
                    (
                        json.dumps(
                            residue_record.as_dict(),
                            sort_keys=True,
                            separators=(",", ":"),
                        )
                        + "\n"
                    ).encode("ascii")
                )
                residue_path.chmod(0o600)
                self.assertEqual(reconciler.rollback().journal_state, "rolled_back")
                self.assertFalse(residue_path.exists())

    def test_process_lock_excludes_stage_and_abrupt_exit_releases_it(self) -> None:
        self.reconciler().execute()
        context = multiprocessing.get_context("fork")
        ready = context.Event()
        worker = context.Process(
            target=hold_stage05_lock,
            args=(str(self.root), ready),
        )
        worker.start()
        self.assertTrue(ready.wait(timeout=5))
        with self.assertRaisesRegex(Stage05Error, "transaction_busy"):
            self.reconciler().observe("after")
        worker.terminate()
        worker.join(timeout=5)
        self.assertFalse(worker.is_alive())
        self.assertEqual(self.reconciler().observe("after").journal_state, "validated")

    def test_forward_crash_points_all_resume_to_validated(self) -> None:
        points = (
            "after_mkdir_state_root",
            "after_fchmod_state_root",
            "after_directory_fsync_state_root",
            "after_parent_fsync_state_root",
            "after_mkdir_journal_root",
            "after_directory_fsync_journal_root",
            "after_parent_fsync_journal_root",
            "before_journal_transition_applying",
            "after_journal_transition_applying",
            "after_mkdir_config",
            "after_fchmod_config",
            "after_directory_fsync_config",
            "after_parent_fsync_config",
            "after_mkdir_experiments",
            "after_directory_fsync_experiments",
            "after_parent_fsync_experiments",
            "after_mkdir_workspace",
            "after_directory_fsync_workspace",
            "after_parent_fsync_workspace",
            "after_write_marker",
            "after_file_fsync_marker",
            "after_link_marker",
            "after_publish_parent_fsync_marker",
            "after_temp_unlink_marker",
            "after_cleanup_parent_fsync_marker",
            "before_journal_transition_applied",
            "after_journal_transition_applied",
            "before_journal_transition_validated",
            "after_journal_transition_validated",
        )
        for point in points:
            with self.subTest(point=point), tempfile.TemporaryDirectory() as directory:
                root = Path(directory).resolve()
                paths = prepare_test_root(root)
                fired = False

                def crash(observed: str) -> None:
                    nonlocal fired
                    if observed == point and not fired:
                        fired = True
                        raise Stage05Crash()

                arguments = {
                    "paths": paths,
                    "expected_uid": os.getuid(),
                    "expected_gid": os.getgid(),
                    "mount_id": lambda _descriptor: 7,
                    "xattrs": lambda _descriptor: (),
                    "service_state": lambda _unit: "absent",
                }
                with self.assertRaises(Stage05Crash):
                    Stage05Reconciler(BUNDLE_DIGEST, fault=crash, **arguments).execute()
                result = Stage05Reconciler(BUNDLE_DIGEST, **arguments).execute()
                self.assertTrue(fired)
                self.assertEqual(result.journal_state, "validated")

    def test_retry_replays_every_interrupted_durability_barrier(self) -> None:
        cases = {
            "after_fchmod_state_root": "after_recovery_directory_fsync_state_root",
            "after_mkdir_journal_root": "after_recovery_directory_fsync_journal_root",
            "after_fchmod_config": "after_recovery_directory_fsync_config",
            "after_mkdir_experiments": "after_recovery_directory_fsync_experiments",
            "after_mkdir_workspace": "after_recovery_directory_fsync_workspace",
            "after_temp_unlink_marker": "after_recovery_file_fsync_marker",
        }
        for crash_point, replay_point in cases.items():
            with self.subTest(crash_point=crash_point), tempfile.TemporaryDirectory() as directory:
                root = Path(directory).resolve()
                paths = prepare_test_root(root)
                fired = False

                def crash(point: str) -> None:
                    nonlocal fired
                    if point == crash_point and not fired:
                        fired = True
                        raise Stage05Crash()

                arguments = {
                    "paths": paths,
                    "expected_uid": os.getuid(),
                    "expected_gid": os.getgid(),
                    "mount_id": lambda _descriptor: 7,
                    "xattrs": lambda _descriptor: (),
                    "service_state": lambda _unit: "absent",
                }
                with self.assertRaises(Stage05Crash):
                    Stage05Reconciler(BUNDLE_DIGEST, fault=crash, **arguments).execute()
                replay_crashed = False

                def crash_replay(point: str) -> None:
                    nonlocal replay_crashed
                    if point == replay_point and not replay_crashed:
                        replay_crashed = True
                        raise Stage05Crash()

                with self.assertRaises(Stage05Crash):
                    Stage05Reconciler(
                        BUNDLE_DIGEST, fault=crash_replay, **arguments
                    ).execute()
                replayed: list[str] = []
                result = Stage05Reconciler(
                    BUNDLE_DIGEST, fault=replayed.append, **arguments
                ).execute()
                self.assertTrue(fired)
                self.assertTrue(replay_crashed)
                self.assertIn(replay_point, replayed)
                self.assertEqual(result.journal_state, "validated")

    def test_direct_rollback_recovers_every_applying_marker_residue(self) -> None:
        for crash_point in (
            "after_write_marker",
            "after_file_fsync_marker",
            "after_link_marker",
            "after_publish_parent_fsync_marker",
        ):
            with self.subTest(crash_point=crash_point), tempfile.TemporaryDirectory() as directory:
                root = Path(directory).resolve()
                paths = prepare_test_root(root)
                fired = False

                def crash(point: str) -> None:
                    nonlocal fired
                    if point == crash_point and not fired:
                        fired = True
                        raise Stage05Crash()

                arguments = {
                    "paths": paths,
                    "expected_uid": os.getuid(),
                    "expected_gid": os.getgid(),
                    "mount_id": lambda _descriptor: 7,
                    "xattrs": lambda _descriptor: (),
                    "service_state": lambda _unit: "absent",
                }
                with self.assertRaises(Stage05Crash):
                    Stage05Reconciler(BUNDLE_DIGEST, fault=crash, **arguments).execute()
                result = Stage05Reconciler(BUNDLE_DIGEST, **arguments).rollback()
                self.assertTrue(fired)
                self.assertEqual(result.journal_state, "rolled_back")

    def test_direct_rollback_recovers_journal_proven_config_provisional(self) -> None:
        def crash(point: str) -> None:
            if point == "after_mkdir_config":
                raise Stage05Crash()

        with self.assertRaises(Stage05Crash):
            self.reconciler(fault=crash).execute()
        self.assertEqual(stat.S_IMODE(self.actual(CONFIG_ROOT).stat().st_mode), 0o700)
        self.assertEqual(self.reconciler().rollback().journal_state, "rolled_back")
        self.assertFalse(self.actual(CONFIG_ROOT).exists())

    def test_rollback_crash_points_all_resume_to_rolled_back(self) -> None:
        points = (
            "before_journal_transition_rolling_back",
            "after_journal_transition_rolling_back",
            "after_unlink_marker",
            "after_remove_marker",
            "after_rmdir_workspace",
            "after_remove_workspace",
            "after_rmdir_experiments",
            "after_remove_experiments",
            "after_rmdir_config",
            "after_remove_config",
            "before_journal_transition_rolled_back",
            "after_journal_transition_rolled_back",
        )
        for point in points:
            with self.subTest(point=point), tempfile.TemporaryDirectory() as directory:
                root = Path(directory).resolve()
                paths = prepare_test_root(root)
                arguments = {
                    "paths": paths,
                    "expected_uid": os.getuid(),
                    "expected_gid": os.getgid(),
                    "mount_id": lambda _descriptor: 7,
                    "xattrs": lambda _descriptor: (),
                    "service_state": lambda _unit: "absent",
                }
                Stage05Reconciler(BUNDLE_DIGEST, **arguments).execute()
                fired = False

                def crash(observed: str) -> None:
                    nonlocal fired
                    if observed == point and not fired:
                        fired = True
                        raise Stage05Crash()

                with self.assertRaises(Stage05Crash):
                    Stage05Reconciler(BUNDLE_DIGEST, fault=crash, **arguments).rollback()
                result = Stage05Reconciler(BUNDLE_DIGEST, **arguments).rollback()
                self.assertTrue(fired)
                self.assertEqual(result.journal_state, "rolled_back")

    def test_production_probe_requires_stable_absence_and_known_systemd_state(self) -> None:
        target = self.actual(Path("/opt/kitdev-sandboxes"))
        target.mkdir(mode=0o700)
        with self.assertRaisesRegex(Stage05Error, "production_state_present"):
            self.reconciler().observe("before")
        target.rmdir()
        for state in ("unknown", "malformed"):
            with self.subTest(state=state), self.assertRaisesRegex(
                Stage05Error, "production_state_unknown"
            ):
                self.reconciler(service_state=lambda _unit, value=state: value).observe(
                    "before"
                )

        calls = 0
        real_state = self.reconciler().tree.path_state

        def changing_state(path: Path) -> str:
            nonlocal calls
            calls += 1
            if path == Path("/etc/kitdev-sandboxes/production") and calls == 2:
                return "present"
            return real_state(path)

        reconciler = self.reconciler()
        with patch.object(reconciler.tree, "path_state", side_effect=changing_state):
            with self.assertRaisesRegex(Stage05Error, "production_state_unknown"):
                reconciler.observe("before")

    def test_usrmerge_lib_symlink_is_ignored_but_retained_ancestry_symlink_blocks(self) -> None:
        lib = self.root / "lib"
        (lib / "systemd/system").rmdir()
        (lib / "systemd").rmdir()
        lib.rmdir()
        lib.symlink_to("usr/lib", target_is_directory=True)

        self.assertEqual(self.reconciler().observe("before").journal_root, "absent")

        systemd = self.root / "usr/lib/systemd"
        moved = self.root / "usr/lib/systemd-real"
        systemd.rename(moved)
        systemd.symlink_to(moved.name, target_is_directory=True)
        with self.assertRaisesRegex(Stage05Error, "production_state_unknown"):
            self.reconciler().observe("before")

    def test_suspicious_marker_residue_is_preserved_and_blocks(self) -> None:
        def crash(point: str) -> None:
            if point == "before_write_marker":
                raise Stage05Crash()

        with self.assertRaises(Stage05Crash):
            self.reconciler(fault=crash).execute()
        suspicious = self.actual(CONFIG_ROOT) / (".disposable-ovh-lab.tmp." + "f" * 31)
        suspicious.write_bytes(b"foreign")
        suspicious.chmod(0o600)
        with self.assertRaises(Stage05Error):
            self.reconciler().execute()
        self.assertEqual(suspicious.read_bytes(), b"foreign")

    def test_absent_marker_temp_with_unexplained_hard_link_is_preserved(self) -> None:
        def crash(point: str) -> None:
            if point == "after_file_fsync_marker":
                raise Stage05Crash()

        with self.assertRaises(Stage05Crash):
            self.reconciler(fault=crash).execute()
        candidates = tuple(self.actual(CONFIG_ROOT).glob(".disposable-ovh-lab.tmp.*"))
        self.assertEqual(len(candidates), 1)
        outside = self.root / "outside-marker-link"
        os.link(candidates[0], outside)
        with self.assertRaisesRegex(Stage05Error, "marker_content_mismatch"):
            self.reconciler().execute()
        self.assertTrue(candidates[0].exists())
        self.assertTrue(outside.exists())
        self.assertFalse(self.actual(MARKER_PATH).exists())

    def test_post_transition_journal_policy_blocks_before_resource_mutation(self) -> None:
        dirty = False

        def fault(point: str) -> None:
            nonlocal dirty
            if point == "after_journal_transition_applying":
                dirty = True

        def xattrs(descriptor: int) -> tuple[str, ...]:
            metadata = os.fstat(descriptor)
            if dirty and stat.S_ISREG(metadata.st_mode):
                return ("security.capability",)
            return ()

        with self.assertRaisesRegex(Stage05Error, "journal_conflict"):
            self.reconciler(fault=fault, xattrs=xattrs).execute()
        self.assertFalse(self.actual(CONFIG_ROOT).exists())

    def test_provisional_recovery_retains_original_descriptor_identity(self) -> None:
        config = self.actual(CONFIG_ROOT)
        config.mkdir(mode=0o700)
        real_stat = os.stat
        published_reads = 0

        def changed_identity(
            path: object, *args: object, **kwargs: object
        ) -> os.stat_result:
            nonlocal published_reads
            metadata = real_stat(path, *args, **kwargs)  # type: ignore[arg-type]
            if path == CONFIG_ROOT.name and kwargs.get("dir_fd") is not None:
                published_reads += 1
                if published_reads == 2:
                    values = list(metadata)
                    values[1] += 1
                    return os.stat_result(values)
            return metadata

        with patch("kitdev_sandboxes.stage05.os.stat", side_effect=changed_identity):
            with self.assertRaisesRegex(Stage05Error, "illegal_recovery_state"):
                self.reconciler().tree.recover_provisional_directory(
                    CONFIG_ROOT, 0o755, (), "config"
                )

    def test_changed_bundle_cannot_resume_original_transaction(self) -> None:
        def crash(point: str) -> None:
            if point == "before_write_marker":
                raise Stage05Crash()

        with self.assertRaises(Stage05Crash):
            self.reconciler(fault=crash).execute()
        changed = Stage05Reconciler(
            "1" * 64,
            paths=self.paths,
            expected_uid=os.getuid(),
            expected_gid=os.getgid(),
            mount_id=lambda _descriptor: 7,
            xattrs=lambda _descriptor: (),
            service_state=lambda _unit: "absent",
        )
        with self.assertRaisesRegex(Stage05Error, "journal_conflict"):
            changed.execute()


if __name__ == "__main__":
    unittest.main()
