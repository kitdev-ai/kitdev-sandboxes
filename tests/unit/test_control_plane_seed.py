from __future__ import annotations

import errno
import importlib.util
import os
import shutil
import stat
import subprocess
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parents[2]
NORMALIZER = ROOT / "scripts" / "control-plane" / "normalize-copy-sql.py"
SEED = ROOT / "scripts" / "control-plane" / "seed-local-template.sh"
PUBLISHER = ROOT / "scripts" / "control-plane" / "publish-template-dirs.py"
LAYOUT = ROOT / "scripts" / "control-plane" / "prepare-layout.sh"
BUILD_ID = "2d9a8389-f5f5-4449-b0eb-e1d364ee98ae"
TEAM_ID = "11111111-2222-4333-8444-555555555555"
ENV_ID = "abcdefghijklmnopqrst"

PUBLISHER_SPEC = importlib.util.spec_from_file_location("publish_template_dirs", PUBLISHER)
assert PUBLISHER_SPEC is not None and PUBLISHER_SPEC.loader is not None
publish_template_dirs = importlib.util.module_from_spec(PUBLISHER_SPEC)
PUBLISHER_SPEC.loader.exec_module(publish_template_dirs)


def upstream_sql(build_id: str = BUILD_ID, team_id: str = TEAM_ID) -> str:
    return (
        "BEGIN;\n"
        "INSERT INTO public.envs (id, team_id, updated_at, public, source)\n"
        f"VALUES ('{ENV_ID}', '{team_id}', NOW(), FALSE, 'template');\n\n"
        "INSERT INTO public.env_builds (id, env_id, updated_at, finished_at, status, "
        "ram_mb, vcpu, kernel_version, firecracker_version, envd_version, "
        "free_disk_size_mb, total_disk_size_mb)\n"
        f"VALUES ('{build_id}', '{ENV_ID}', NOW(), NOW(), 'uploaded', 1024, 2, "
        "'vmlinux-6.1.158', 'v1.14.1_431f1fc', '0.6.13', 1024, 1024);\n\n"
        "INSERT INTO public.env_build_assignments (env_id, build_id, tag)\n"
        f"VALUES ('{ENV_ID}', '{build_id}', 'default');\n"
        "COMMIT;\n"
    )


class ControlPlaneSeedTests(unittest.TestCase):
    def normalize(self, source: Path, target: Path, build: str = BUILD_ID, team: str = TEAM_ID):
        return subprocess.run(
            ["python3", "-I", "-B", "-S", str(NORMALIZER), str(source), str(target), build, team],
            capture_output=True,
            text=True,
        )

    def test_normalizer_accepts_only_exact_upstream_shape_and_corrects_total(self) -> None:
        with TemporaryDirectory(dir=ROOT) as directory:
            root = Path(directory)
            source = root / "source.sql"
            target = root / "target.sql"
            source.write_text(upstream_sql(), encoding="ascii")
            source.chmod(0o600)
            result = self.normalize(source, target)
            self.assertEqual(result.returncode, 0, result.stderr)
            normalized = target.read_text(encoding="ascii")
            self.assertIn("1024, 3722);", normalized)
            self.assertNotIn("1024, 1024);", normalized)
            self.assertEqual(normalized.count("BEGIN;"), 1)
            self.assertEqual(normalized.count("COMMIT;"), 1)
            metadata = target.stat()
            self.assertEqual(stat.S_IMODE(metadata.st_mode), 0o600)
            self.assertEqual(metadata.st_nlink, 1)

    def test_normalizer_rejects_symlink_hardlink_and_wrong_mode_sources(self) -> None:
        with TemporaryDirectory(dir=ROOT) as directory:
            root = Path(directory)
            valid = root / "valid.sql"
            valid.write_text(upstream_sql(), encoding="ascii")
            valid.chmod(0o600)

            symlink = root / "symlink.sql"
            symlink.symlink_to(valid.name)
            result = self.normalize(symlink, root / "symlink.out")
            self.assertNotEqual(result.returncode, 0)

            hardlink = root / "hardlink.sql"
            os.link(valid, hardlink)
            result = self.normalize(hardlink, root / "hardlink.out")
            self.assertNotEqual(result.returncode, 0)
            hardlink.unlink()

            valid.chmod(0o640)
            result = self.normalize(valid, root / "mode.out")
            self.assertNotEqual(result.returncode, 0)

    def test_normalizer_rejects_changed_shape_wrong_ids_and_oversize(self) -> None:
        mutations = {
            "changed_status": upstream_sql().replace("'uploaded'", "'ready'"),
            "extra_statement": upstream_sql() + "SELECT 1;\n",
            "wrong_build": upstream_sql(build_id="aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee"),
            "wrong_team": upstream_sql(team_id="aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee"),
        }
        with TemporaryDirectory(dir=ROOT) as directory:
            root = Path(directory)
            for name, content in mutations.items():
                with self.subTest(name=name):
                    source = root / f"{name}.sql"
                    target = root / f"{name}.out"
                    source.write_text(content, encoding="ascii")
                    source.chmod(0o600)
                    result = self.normalize(source, target)
                    self.assertNotEqual(result.returncode, 0)
                    self.assertFalse(target.exists())
            sparse = root / "oversize-sparse.sql"
            sparse.touch(mode=0o600)
            with sparse.open("r+b") as stream:
                stream.truncate(1024 * 1024 * 1024)
            sparse_target = root / "oversize-sparse.out"
            result = self.normalize(sparse, sparse_target)
            self.assertNotEqual(result.returncode, 0)
            self.assertFalse(sparse_target.exists())

    def test_normalizer_never_replaces_existing_target(self) -> None:
        with TemporaryDirectory(dir=ROOT) as directory:
            root = Path(directory)
            source = root / "source.sql"
            target = root / "target.sql"
            source.write_text(upstream_sql(), encoding="ascii")
            source.chmod(0o600)
            target.write_text("foreign\n", encoding="ascii")
            result = self.normalize(source, target)
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(target.read_text(encoding="ascii"), "foreign\n")

    def test_seed_locks_ancestor_chain_and_database_relationships(self) -> None:
        seed = SEED.read_text(encoding="ascii")
        self.assertIn('path = ancestor_root / "rootfs.ext4"', seed)
        for digest in (
            "eab0cb327228384ec58ca3e087e3f6df2c605d623c23e22e0cc9610a6e5e8b9c",
            "3fb9e84587adb78c0fcbe6a4dd41e7d402eb68e45c7279627c52839ab159977b",
            "ec9c4ac7e1cd01eeacec3e50597e7bf7de09a92fd038a7fed1530e7796497add",
            "e206cf1e356ea1a0eb36718f24503bd34c583f6eaf1a0b4a90c98b0f14aa2996",
            "155b8acd5a6318136884acae6777364ddc3c687986283da05b70851686356baa",
        ):
            self.assertIn(digest, seed)
        self.assertIn("JOIN env_builds b ON b.id = a.build_id AND b.env_id = a.env_id", seed)
        self.assertIn("JOIN envs e ON e.id = b.env_id", seed)
        self.assertIn("is_banned = FALSE", seed)
        self.assertIn("e.public = FALSE AND e.source = 'template' AND e.deleted_at IS NULL", seed)
        self.assertIn('!= {"rootfs.ext4"}', seed)
        self.assertIn("stat.S_IMODE(metadata.st_mode) != 0o644", seed)
        self.assertIn("require_command rsync", seed)

    def test_seed_preflights_source_and_database_before_mutation(self) -> None:
        seed = SEED.read_text(encoding="ascii")
        source_verify = seed.index('verify_artifacts "$SOURCE_STORAGE/templates/$BUILD_ID" source')
        database_classify = seed.index('existing="$(psql_query --command')
        stage_create = seed.index('stage="$(mktemp -d')
        copy_start = seed.index('GOOGLE_APPLICATION_CREDENTIALS="$adc" "$COPY_BUILD"')
        publish_start = seed.index('publish_artifacts "$staged_storage"')
        self.assertLess(source_verify, database_classify)
        self.assertLess(database_classify, stage_create)
        self.assertLess(stage_create, copy_start)
        self.assertLess(copy_start, publish_start)
        existing_branch = seed[seed.index("    1)\n") : stage_create]
        self.assertIn("verify_database", existing_branch)
        self.assertIn(
            'verify_artifacts "$DESTINATION_STORAGE/templates/$BUILD_ID" complete',
            existing_branch,
        )
        self.assertIn("return 0", existing_branch)
        self.assertNotIn("ensure_directory", existing_branch)
        self.assertNotIn("$COPY_BUILD", existing_branch)

    def test_seed_source_and_destination_metadata_contracts_are_distinct(self) -> None:
        seed = SEED.read_text(encoding="ascii")
        self.assertIn('directory_mode = 0o2755 if mode == "source" else 0o2700', seed)
        self.assertIn("storage_metadata.st_gid != kitdev_gid", seed)
        self.assertIn("stat.S_IMODE(storage_metadata.st_mode) != 0o2755", seed)
        self.assertIn(
            'if mode == "source":\n        if "rootfs.ext4" not in ancestor_entries:', seed
        )
        self.assertIn('elif ancestor_entries != {"rootfs.ext4"}:', seed)

    def test_seed_refuses_production_before_external_commands_or_mutation(self) -> None:
        seed = SEED.read_text(encoding="ascii")
        production_gate = seed.index('[[ "$KITDEV_LIFECYCLE" != production ]]')
        self.assertLess(production_gate, seed.index("require_command docker"))
        self.assertLess(production_gate, seed.index('verify_artifacts "$SOURCE_STORAGE'))
        self.assertLess(production_gate, seed.index('stage="$(mktemp -d'))

    def test_layout_precreates_private_setgid_template_store(self) -> None:
        layout = LAYOUT.read_text(encoding="ascii")
        self.assertIn(
            'ensure_directory "$KITDEV_RUNTIME_ROOT/orchestrator/template-storage/templates" '
            "root kitdev 2700",
            layout,
        )


class TemplateDirectoryPublisherTests(unittest.TestCase):
    @staticmethod
    def make_tree(parent: Path, name: str, content: bytes = b"snapshot") -> Path:
        root = parent / name
        root.mkdir(mode=0o700)
        artifact = root / "rootfs.ext4"
        artifact.write_bytes(content)
        artifact.chmod(0o600)
        return root

    @staticmethod
    def rename_noreplace(source: Path, target: Path) -> None:
        if target.exists() or target.is_symlink():
            raise OSError(errno.EEXIST, "target exists", target)
        source.rename(target)

    def test_publisher_recovers_a_partial_set_of_complete_directories(self) -> None:
        with TemporaryDirectory(dir=ROOT) as directory:
            root = Path(directory)
            source = root / "source"
            target = root / "target"
            source.mkdir()
            target.mkdir()
            self.make_tree(source, "first")
            self.make_tree(source, "second")
            shutil.copytree(source / "first", target / "first", copy_function=shutil.copy2)

            publish_template_dirs.publish(
                source,
                target,
                names=("first", "second"),
                rename=self.rename_noreplace,
            )

            self.assertTrue((source / "first").is_dir())
            self.assertFalse((source / "second").exists())
            self.assertEqual((target / "first" / "rootfs.ext4").read_bytes(), b"snapshot")
            self.assertEqual((target / "second" / "rootfs.ext4").read_bytes(), b"snapshot")

    def test_publisher_accepts_equal_eexist_race_without_replacing_target(self) -> None:
        with TemporaryDirectory(dir=ROOT) as directory:
            root = Path(directory)
            source = root / "source"
            target = root / "target"
            source.mkdir()
            target.mkdir()
            staged = self.make_tree(source, "build")

            def raced(source_path: Path, target_path: Path) -> None:
                shutil.copytree(source_path, target_path, copy_function=shutil.copy2)
                raise OSError(errno.EEXIST, "raced", target_path)

            publish_template_dirs.publish(source, target, names=("build",), rename=raced)
            self.assertTrue(staged.is_dir())
            self.assertEqual((target / "build" / "rootfs.ext4").read_bytes(), b"snapshot")

    def test_publisher_rejects_mismatch_root_metadata_and_other_errors(self) -> None:
        with TemporaryDirectory(dir=ROOT) as directory:
            root = Path(directory)
            source = root / "source"
            target = root / "target"
            source.mkdir()
            target.mkdir()
            self.make_tree(source, "build")
            raced = self.make_tree(target, "build")
            raced.chmod(0o755)
            with self.assertRaises(SystemExit):
                publish_template_dirs.publish(
                    source,
                    target,
                    names=("build",),
                    rename=self.rename_noreplace,
                )
            self.assertEqual((target / "build" / "rootfs.ext4").read_bytes(), b"snapshot")

            shutil.rmtree(target / "build")

            def cross_device(_source: Path, target_path: Path) -> None:
                raise OSError(errno.EXDEV, "cross-device", target_path)

            with self.assertRaises(SystemExit):
                publish_template_dirs.publish(
                    source,
                    target,
                    names=("build",),
                    rename=cross_device,
                )
            self.assertFalse((target / "build").exists())

    def test_publisher_rejects_extra_stage_entries_and_foreign_target(self) -> None:
        with TemporaryDirectory(dir=ROOT) as directory:
            root = Path(directory)
            source = root / "source"
            target = root / "target"
            source.mkdir()
            target.mkdir()
            self.make_tree(source, "build")
            self.make_tree(source, "extra")
            with self.assertRaises(SystemExit):
                publish_template_dirs.publish(
                    source,
                    target,
                    names=("build",),
                    rename=self.rename_noreplace,
                )

            shutil.rmtree(source / "extra")
            self.make_tree(target, "build", content=b"foreign")
            with self.assertRaises(SystemExit):
                publish_template_dirs.publish(
                    source,
                    target,
                    names=("build",),
                    rename=self.rename_noreplace,
                )
            self.assertEqual((target / "build" / "rootfs.ext4").read_bytes(), b"foreign")


if __name__ == "__main__":
    unittest.main()
