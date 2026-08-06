from __future__ import annotations

import fcntl
import json
import multiprocessing
import os
import stat
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from kitdev_sandboxes.journal import (
    JournalConflict,
    JournalCorrupt,
    JournalRecord,
    JournalSecurityError,
    JournalSecurityPolicy,
    JournalState,
    JournalStore,
    ResourceRecord,
)


PLAN_HASH = "sha256:" + "a" * 64
OTHER_HASH = "sha256:" + "b" * 64


def _competing_creator(root: str, start: object, results: object) -> None:
    path = Path(root)
    policy = JournalSecurityPolicy(
        expected_owner_uid=os.getuid(),
        expected_owner_gid=os.getegid(),
        trusted_prefix=temporary_trust_boundary(path),
    )
    start.wait()  # type: ignore[attr-defined]
    try:
        JournalStore(path, policy).create(record())
    except JournalConflict:
        results.put("conflict")  # type: ignore[attr-defined]
    else:
        results.put("created")  # type: ignore[attr-defined]


def _try_session_lock(root: str, results: object) -> None:
    path = Path(root)
    policy = JournalSecurityPolicy(
        expected_owner_uid=os.getuid(),
        expected_owner_gid=os.getegid(),
        trusted_prefix=temporary_trust_boundary(path),
    )
    try:
        with JournalStore(path, policy).locked():
            results.put("acquired")  # type: ignore[attr-defined]
    except JournalConflict:
        results.put("conflict")  # type: ignore[attr-defined]


def _exit_while_holding_session(root: str) -> None:
    path = Path(root)
    policy = JournalSecurityPolicy(
        expected_owner_uid=os.getuid(),
        expected_owner_gid=os.getegid(),
        trusted_prefix=temporary_trust_boundary(path),
    )
    with JournalStore(path, policy).locked():
        os._exit(0)


def resources() -> tuple[ResourceRecord, ...]:
    return (
        ResourceRecord("directory.state", "/var/lib/kitdev-sandboxes", "absent", "dir:0750:0:0"),
        ResourceRecord(
            "file.lock",
            "/etc/kitdev-sandboxes/versions.lock.yaml",
            "absent",
            "file:0644:sha256:" + "c" * 64,
        ),
    )


def record(install_id: str = "install-001") -> JournalRecord:
    return JournalRecord(install_id, "bootstrap-v1", PLAN_HASH, resources())


def temporary_trust_boundary(root: Path) -> Path:
    chain = (Path("/"),) + tuple(reversed(root.parents[:-1])) + (root,)
    for path in chain:
        metadata = path.stat()
        if metadata.st_uid != 0 or metadata.st_mode & 0o022:
            return path
    return root


class JournalTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name).resolve()
        self.root.chmod(0o700)
        self.policy = JournalSecurityPolicy(
            expected_owner_uid=os.getuid(),
            expected_owner_gid=os.getegid(),
            trusted_prefix=temporary_trust_boundary(self.root),
        )
        self.store = JournalStore(self.root, self.policy)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def path(self, install_id: str = "install-001") -> Path:
        return self.root / f"{install_id}.journal.json"

    def test_create_load_and_bytes_are_canonical_and_durable_shape(self) -> None:
        created = self.store.create(record())
        raw = self.path().read_bytes()

        self.assertEqual(created, record())
        self.assertEqual(self.store.load("install-001"), record())
        self.assertEqual(raw[-1:], b"\n")
        self.assertEqual(
            raw,
            (
                json.dumps(record().as_dict(), sort_keys=True, separators=(",", ":"))
                + "\n"
            ).encode("ascii"),
        )
        self.assertEqual(self.path().stat().st_mode & 0o777, 0o600)
        self.assertEqual(list(self.root.glob(".*.tmp")), [])

    def test_resume_requires_exact_plan_identity_hash_and_resources(self) -> None:
        self.store.create(record())
        self.assertEqual(
            self.store.resume("install-001", "bootstrap-v1", PLAN_HASH, resources()).state,
            JournalState.PLANNED,
        )
        changed = (
            ResourceRecord(
                "directory.state",
                "/var/lib/kitdev-sandboxes",
                "present",
                "dir:0750:0:0",
            ),
            resources()[1],
        )
        for plan_id, plan_hash, observed in (
            ("bootstrap-v2", PLAN_HASH, resources()),
            ("bootstrap-v1", OTHER_HASH, resources()),
            ("bootstrap-v1", PLAN_HASH, changed),
        ):
            with self.subTest(plan_id=plan_id, plan_hash=plan_hash, observed=observed):
                with self.assertRaises(JournalConflict):
                    self.store.resume("install-001", plan_id, plan_hash, observed)

    def test_legal_transitions_are_append_only_and_reopen_after_every_state(self) -> None:
        current = self.store.create(record())
        expected = [JournalState.PLANNED]
        for state in (JournalState.APPLYING, JournalState.APPLIED, JournalState.VALIDATED):
            current = JournalStore(self.root, self.policy).transition(
                "install-001", "bootstrap-v1", PLAN_HASH, resources(), state
            )
            expected.append(state)
            reopened = JournalStore(self.root, self.policy).load("install-001")
            self.assertEqual(reopened, current)
            self.assertEqual(reopened.transitions, tuple(expected))
        with self.assertRaises(JournalConflict):
            self.store.resume("install-001", "bootstrap-v1", PLAN_HASH, resources())

    def test_rollback_and_failed_paths_reopen(self) -> None:
        self.store.create(record("rollback-001"))
        for state in (
            JournalState.APPLYING,
            JournalState.FAILED,
            JournalState.ROLLING_BACK,
            JournalState.ROLLED_BACK,
        ):
            updated = JournalStore(self.root, self.policy).transition(
                "rollback-001", "bootstrap-v1", PLAN_HASH, resources(), state
            )
            self.assertEqual(JournalStore(self.root, self.policy).load("rollback-001"), updated)

    def test_illegal_transition_does_not_modify_journal(self) -> None:
        self.store.create(record())
        before = self.path().read_bytes()
        for state in (JournalState.PLANNED, JournalState.APPLIED, JournalState.VALIDATED):
            with self.subTest(state=state), self.assertRaises(JournalConflict):
                self.store.transition(
                    "install-001", "bootstrap-v1", PLAN_HASH, resources(), state
                )
            self.assertEqual(self.path().read_bytes(), before)

    def test_corrupt_noncanonical_and_oversized_files_fail_closed(self) -> None:
        cases = (
            b"not-json\n",
            json.dumps(record().as_dict(), indent=2).encode("utf-8"),
            b"x" * (1_048_576 + 1),
        )
        for index, content in enumerate(cases):
            install_id = f"bad-{index}"
            path = self.path(install_id)
            path.write_bytes(content)
            path.chmod(0o600)
            with self.subTest(index=index), self.assertRaises(JournalCorrupt):
                self.store.load(install_id)

    def test_symlink_root_and_final_are_rejected_without_following(self) -> None:
        real = self.root / "real"
        real.mkdir(mode=0o700)
        linked = self.root / "linked"
        linked.symlink_to(real, target_is_directory=True)
        with self.assertRaises(JournalSecurityError):
            JournalStore(linked, self.policy).create(record())

        target = self.root / "outside"
        target.write_bytes(b"untouched")
        self.path().symlink_to(target)
        with self.assertRaises(JournalConflict):
            self.store.create(record())
        with self.assertRaises(JournalSecurityError):
            self.store.load("install-001")
        self.assertEqual(target.read_bytes(), b"untouched")

    def test_mode_and_injected_owner_policy_checks(self) -> None:
        self.root.chmod(0o755)
        with self.assertRaises(JournalSecurityError):
            self.store.create(record())
        self.root.chmod(0o700)
        rejecting = JournalSecurityPolicy(
            expected_owner_uid=os.getuid(),
            expected_owner_gid=os.getegid(),
            stat_validator=lambda _metadata, _directory: False,
            trusted_prefix=temporary_trust_boundary(self.root),
        )
        with self.assertRaises(JournalSecurityError):
            JournalStore(self.root, rejecting).create(record())

        self.store.create(record())
        self.path().chmod(0o644)
        with self.assertRaises(JournalSecurityError):
            self.store.load("install-001")

    def test_existing_journal_is_never_overwritten_or_adopted_by_name(self) -> None:
        self.store.create(record())
        before = self.path().read_bytes()
        with self.assertRaises(JournalConflict):
            self.store.create(
                JournalRecord("install-001", "different-plan", OTHER_HASH, resources())
            )
        self.assertEqual(self.path().read_bytes(), before)

    def test_concurrent_directory_lock_fails_without_writing(self) -> None:
        descriptor = os.open(self.root, os.O_RDONLY | os.O_DIRECTORY)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with self.assertRaises(JournalConflict):
                self.store.create(record())
        finally:
            os.close(descriptor)
        self.assertFalse(self.path().exists())

    def test_locked_session_reuses_one_root_and_avoids_nested_locking(self) -> None:
        with patch.object(self.store, "_open_root", wraps=self.store._open_root) as opened:
            with self.store.locked() as session:
                self.assertEqual(session.create(record()), record())
                self.assertEqual(session.load("install-001"), record())
                updated = session.transition(
                    "install-001",
                    "bootstrap-v1",
                    PLAN_HASH,
                    resources(),
                    JournalState.APPLYING,
                )
                self.assertEqual(updated.state, JournalState.APPLYING)
                self.assertEqual(opened.call_count, 1)
                with self.assertRaises(JournalConflict):
                    self.store.load("install-001")
            self.assertEqual(opened.call_count, 2)

        with self.assertRaises(JournalConflict):
            session.load("install-001")

    def test_shared_session_is_read_only(self) -> None:
        self.store.create(record())
        with self.store.locked(exclusive=False) as session:
            self.assertEqual(session.load("install-001"), record())
            with self.assertRaises(JournalConflict):
                session.create(record("install-002"))
            with self.assertRaises(JournalConflict):
                session.transition(
                    "install-001",
                    "bootstrap-v1",
                    PLAN_HASH,
                    resources(),
                    JournalState.APPLYING,
                )

    def test_shared_session_does_not_repair_linked_create_residue(self) -> None:
        self.store.create(record())
        residue = self.root / (".install-001.journal.json.tmp." + "4" * 32)
        os.link(self.path(), residue)

        with self.store.locked(exclusive=False) as session:
            with self.assertRaises(JournalSecurityError):
                session.load("install-001")
        self.assertTrue(residue.exists())

        self.assertEqual(self.store.load("install-001"), record())
        self.assertFalse(residue.exists())

    def test_session_lock_excludes_processes_and_is_released_on_process_exit(self) -> None:
        context = multiprocessing.get_context("fork")
        results = context.Queue()
        with self.store.locked():
            worker = context.Process(
                target=_try_session_lock, args=(str(self.root), results)
            )
            worker.start()
            self.assertEqual(results.get(timeout=5), "conflict")
            worker.join(timeout=5)
            self.assertEqual(worker.exitcode, 0)

        worker = context.Process(target=_exit_while_holding_session, args=(str(self.root),))
        worker.start()
        worker.join(timeout=5)
        self.assertEqual(worker.exitcode, 0)
        with self.store.locked():
            pass

    def test_two_actual_competing_creators_publish_exactly_once(self) -> None:
        context = multiprocessing.get_context("fork")
        start = context.Event()
        results = context.Queue()
        workers = [
            context.Process(target=_competing_creator, args=(str(self.root), start, results))
            for _ in range(2)
        ]
        for worker in workers:
            worker.start()
        start.set()
        observed = sorted(results.get(timeout=5) for _ in workers)
        for worker in workers:
            worker.join(timeout=5)
            self.assertEqual(worker.exitcode, 0)

        self.assertEqual(observed, ["conflict", "created"])
        self.assertEqual(self.store.load("install-001"), record())

    def test_out_of_band_creator_wins_publish_race_without_overwrite(self) -> None:
        real_link = os.link
        foreign = b"out-of-band creator"

        def competing_link(
            source: str,
            destination: str,
            *,
            src_dir_fd: int,
            dst_dir_fd: int,
            follow_symlinks: bool,
        ) -> None:
            self.path().write_bytes(foreign)
            self.path().chmod(0o600)
            real_link(
                source,
                destination,
                src_dir_fd=src_dir_fd,
                dst_dir_fd=dst_dir_fd,
                follow_symlinks=follow_symlinks,
            )

        with patch("kitdev_sandboxes.journal.os.link", side_effect=competing_link):
            with self.assertRaises(JournalConflict):
                self.store.create(record())
        self.assertEqual(self.path().read_bytes(), foreign)
        self.assertEqual(list(self.root.glob(".*.tmp.*")), [])

    def test_atomic_replace_failure_cleans_temporary_file(self) -> None:
        self.store.create(record())
        with patch("kitdev_sandboxes.journal.os.replace", side_effect=OSError("interrupted")):
            with self.assertRaises(OSError):
                self.store.transition(
                    "install-001",
                    "bootstrap-v1",
                    PLAN_HASH,
                    resources(),
                    JournalState.APPLYING,
                )
        self.assertEqual(self.store.load("install-001").state, JournalState.PLANNED)
        self.assertEqual(list(self.root.glob(".*.tmp.*")), [])

    def test_transition_directory_fsync_failure_leaves_updated_record_reopenable(self) -> None:
        self.store.create(record())
        with patch(
            "kitdev_sandboxes.journal.os.fsync",
            side_effect=(None, OSError("interrupted after transition replace")),
        ):
            with self.assertRaises(OSError):
                self.store.transition(
                    "install-001",
                    "bootstrap-v1",
                    PLAN_HASH,
                    resources(),
                    JournalState.APPLYING,
                )

        reopened = self.store.load("install-001")
        self.assertEqual(reopened.state, JournalState.APPLYING)
        self.assertEqual(
            self.store.resume("install-001", "bootstrap-v1", PLAN_HASH, resources()),
            reopened,
        )
        self.assertEqual(list(self.root.glob(".*.tmp.*")), [])

    def test_directory_fsync_interruption_leaves_a_complete_reopenable_record(self) -> None:
        with patch(
            "kitdev_sandboxes.journal.os.fsync",
            side_effect=(None, OSError("interrupted after replace")),
        ):
            with self.assertRaises(OSError):
                self.store.create(record())

        self.assertEqual(JournalStore(self.root, self.policy).load("install-001"), record())
        self.assertEqual(list(self.root.glob(".*.tmp.*")), [])

    def test_transition_recovers_exact_current_record_residue_after_create_crash(self) -> None:
        with patch(
            "kitdev_sandboxes.journal.os.unlink",
            side_effect=OSError("crash before initial temp unlink"),
        ):
            with self.assertRaises(OSError):
                self.store.create(record())
        self.assertEqual(self.store.load("install-001"), record())
        self.assertEqual(list(self.root.glob(".*.tmp.*")), [])

        updated = self.store.transition(
            "install-001",
            "bootstrap-v1",
            PLAN_HASH,
            resources(),
            JournalState.APPLYING,
        )
        self.assertEqual(updated.state, JournalState.APPLYING)
        self.assertEqual(list(self.root.glob(".*.tmp.*")), [])

    def test_transition_recovers_exact_requested_updated_record_residue(self) -> None:
        self.store.create(record())
        requested = JournalRecord(
            "install-001",
            "bootstrap-v1",
            PLAN_HASH,
            resources(),
            (JournalState.PLANNED, JournalState.APPLYING),
        )
        residue = self.root / (".install-001.journal.json.tmp." + "3" * 32)
        residue.write_bytes(
            (
                json.dumps(requested.as_dict(), sort_keys=True, separators=(",", ":"))
                + "\n"
            ).encode("ascii")
        )
        residue.chmod(0o600)

        updated = self.store.transition(
            "install-001",
            "bootstrap-v1",
            PLAN_HASH,
            resources(),
            JournalState.APPLYING,
        )
        self.assertEqual(updated, requested)
        self.assertEqual(list(self.root.glob(".*.tmp.*")), [])

    def test_default_ancestry_policy_and_injected_temp_boundary_are_explicit(self) -> None:
        self.assertEqual(JournalSecurityPolicy().expected_owner_gid, os.getegid())
        self.assertTrue(
            JournalSecurityPolicy().accepts_ancestor(
                Path("/").stat(), below_trusted=False
            )
        )
        with self.assertRaises(JournalSecurityError):
            JournalStore(Path("/")).create(record("root-rejected"))
        with self.assertRaises(JournalSecurityError):
            JournalStore(self.root).create(record())
        self.assertEqual(self.store.create(record()), record())

    def test_explicit_wrong_gid_is_rejected_for_root_and_files(self) -> None:
        wrong_gid = JournalSecurityPolicy(
            expected_owner_uid=os.getuid(),
            expected_owner_gid=os.getegid() + 1,
            trusted_prefix=temporary_trust_boundary(self.root),
        )
        with self.assertRaises(JournalSecurityError):
            JournalStore(self.root, wrong_gid).create(record())

        self.store.create(record())
        metadata_values = list(self.path().stat())
        metadata_values[5] = os.getegid() + 1
        self.assertFalse(
            self.policy.accepts(os.stat_result(metadata_values), directory=False)
        )

    def test_unexplained_hard_link_to_published_journal_is_rejected(self) -> None:
        self.store.create(record())
        extra = self.root / "unexplained-link"
        os.link(self.path(), extra)

        with self.assertRaises(JournalSecurityError):
            self.store.load("install-001")
        self.assertTrue(extra.exists())
        self.assertEqual(self.path().stat().st_nlink, 2)

    def test_multiple_canonical_temp_artifacts_are_rejected_untouched(self) -> None:
        canonical = (
            json.dumps(record().as_dict(), sort_keys=True, separators=(",", ":")) + "\n"
        ).encode("ascii")
        residues = [
            self.root / (f".install-001.journal.json.tmp.{digit * 32}")
            for digit in ("1", "2")
        ]
        for residue in residues:
            residue.write_bytes(canonical)
            residue.chmod(0o600)

        with self.assertRaises(JournalSecurityError):
            self.store.create(record())
        self.assertTrue(all(residue.exists() for residue in residues))
        self.assertFalse(self.path().exists())

    def test_path_replacement_during_read_is_rejected(self) -> None:
        self.store.create(record())
        original = self.path().read_bytes()
        real_read = os.read
        replaced = False

        def replacing_read(descriptor: int, size: int) -> bytes:
            nonlocal replaced
            chunk = real_read(descriptor, size)
            if chunk and not replaced:
                replacement = self.root / "replacement"
                replacement.write_bytes(original)
                replacement.chmod(0o600)
                os.replace(replacement, self.path())
                replaced = True
            return chunk

        with patch("kitdev_sandboxes.journal.os.read", side_effect=replacing_read):
            with self.assertRaises(JournalSecurityError):
                self.store.load("install-001")
        self.assertTrue(replaced)

    def test_in_place_change_during_read_is_rejected(self) -> None:
        self.store.create(record())
        real_read = os.read
        changed = False

        def changing_read(descriptor: int, size: int) -> bytes:
            nonlocal changed
            chunk = real_read(descriptor, size)
            if chunk and not changed:
                with self.path().open("ab") as journal:
                    journal.write(b" ")
                    journal.flush()
                    os.fsync(journal.fileno())
                changed = True
            return chunk

        with patch("kitdev_sandboxes.journal.os.read", side_effect=changing_read):
            with self.assertRaises(JournalSecurityError):
                self.store.load("install-001")
        self.assertTrue(changed)

    def test_world_writable_sticky_and_wrong_owner_ancestors_are_rejected(self) -> None:
        for mode in (0o777, 0o1777):
            with self.subTest(mode=oct(mode)):
                parent = self.root / f"parent-{mode:o}"
                parent.mkdir(mode=0o700)
                target = parent / "journal"
                target.mkdir(mode=0o700)
                parent.chmod(mode)
                policy = JournalSecurityPolicy(
                    expected_owner_uid=os.getuid(),
                    expected_owner_gid=os.getegid(),
                    trusted_prefix=self.root,
                )
                with self.assertRaises(JournalSecurityError):
                    JournalStore(target, policy).create(record(f"mode-{mode:o}"))

        owned = self.root / "owned"
        owned.mkdir(mode=0o700)
        wrong_owner = JournalSecurityPolicy(
            expected_owner_uid=os.getuid() + 1,
            expected_owner_gid=os.getegid(),
            trusted_prefix=temporary_trust_boundary(self.root),
            stat_validator=lambda metadata, directory: (
                stat.S_ISDIR(metadata.st_mode) if directory else stat.S_ISREG(metadata.st_mode)
            ),
        )
        with self.assertRaises(JournalSecurityError):
            JournalStore(owned, wrong_owner).create(record("wrong-owner"))

    def test_exact_crash_residue_is_recovered_but_suspicious_artifact_is_untouched(self) -> None:
        canonical = (
            json.dumps(record().as_dict(), sort_keys=True, separators=(",", ":")) + "\n"
        ).encode("ascii")
        residue = self.root / (".install-001.journal.json.tmp." + "1" * 32)
        residue.write_bytes(canonical)
        residue.chmod(0o600)

        self.assertEqual(self.store.create(record()), record())
        self.assertFalse(residue.exists())

        suspicious = self.root / (".install-002.journal.json.tmp." + "2" * 32)
        suspicious.write_bytes(b"foreign")
        suspicious.chmod(0o600)
        with self.assertRaises(JournalSecurityError):
            self.store.create(record("install-002"))
        self.assertEqual(suspicious.read_bytes(), b"foreign")
        self.assertFalse(self.path("install-002").exists())

    def test_partial_write_and_file_fsync_fail_without_publishing(self) -> None:
        real_write = os.write
        calls = 0

        def interrupted_write(descriptor: int, payload: bytes) -> int:
            nonlocal calls
            calls += 1
            if calls == 1:
                return real_write(descriptor, payload[:7])
            raise OSError("interrupted partial write")

        with patch("kitdev_sandboxes.journal.os.write", side_effect=interrupted_write):
            with self.assertRaises(OSError):
                self.store.create(record())
        self.assertFalse(self.path().exists())
        self.assertEqual(list(self.root.iterdir()), [])

        with patch("kitdev_sandboxes.journal.os.fsync", side_effect=OSError("file fsync")):
            with self.assertRaises(OSError):
                self.store.create(record())
        self.assertFalse(self.path().exists())
        self.assertEqual(list(self.root.iterdir()), [])

    def test_traversal_precompleted_secret_and_bidi_inputs_are_rejected(self) -> None:
        with self.assertRaises(JournalSecurityError):
            JournalStore(self.root / "safe" / "..", self.policy).create(record())
        for component in ("bad\u202ename", "bad\ud800name"):
            with self.subTest(component=repr(component)), self.assertRaises(
                JournalSecurityError
            ):
                JournalStore(self.root / component, self.policy).create(record())
        for target in ("/etc/../shadow", "/etc/%2e%2e/shadow", "/etc//shadow"):
            with self.subTest(target=target), self.assertRaises(ValueError):
                ResourceRecord("file.target", target, "absent", "present")
        for field in ("token=visible", "safe\u202esecret"):
            with self.subTest(field=field), self.assertRaises(ValueError):
                ResourceRecord("file.target", "/safe", field, "present")

        precompleted = JournalRecord(
            "install-001",
            "bootstrap-v1",
            PLAN_HASH,
            resources(),
            (JournalState.PLANNED, JournalState.APPLYING),
        )
        with self.assertRaises(JournalConflict):
            self.store.create(precompleted)
        self.assertFalse(self.path().exists())

    def test_journal_contract_explicitly_excludes_resource_reconciliation(self) -> None:
        import kitdev_sandboxes.journal as journal

        self.assertIn("does not inspect or reconcile resources", journal.__doc__ or "")
        self.assertIn("caller", ResourceRecord.__doc__ or "")

    def test_record_validation_rejects_duplicates_order_and_invalid_history(self) -> None:
        duplicate = (resources()[0], resources()[0])
        for observed in (duplicate, tuple(reversed(resources()))):
            with self.subTest(observed=observed), self.assertRaises(ValueError):
                JournalRecord("install-001", "bootstrap-v1", PLAN_HASH, observed)
        with self.assertRaises(ValueError):
            JournalRecord(
                "install-001",
                "bootstrap-v1",
                PLAN_HASH,
                resources(),
                (JournalState.PLANNED, JournalState.APPLIED),
            )


if __name__ == "__main__":
    unittest.main()
