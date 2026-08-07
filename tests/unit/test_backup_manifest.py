from __future__ import annotations

import importlib.util
import io
import tarfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    "backup_manifest", ROOT / "scripts/control-plane/backup_manifest.py"
)
assert SPEC is not None and SPEC.loader is not None
backup_manifest = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(backup_manifest)


def _archive(path: Path, relative: str, payload: bytes = b"value") -> None:
    with tarfile.open(path, "w", format=tarfile.PAX_FORMAT) as archive:
        directory = tarfile.TarInfo(relative)
        directory.type = tarfile.DIRTYPE
        directory.mode = 0o700
        archive.addfile(directory)
        item = tarfile.TarInfo(f"{relative}/item")
        item.size = len(payload)
        item.mode = 0o600
        archive.addfile(item, io.BytesIO(payload))


def _complete_backup(tmp_path: Path) -> tuple[Path, Path, Path, Path, Path]:
    backup = tmp_path / "20260807T120000Z-0123456789abcdef"
    backup.mkdir()
    compose = tmp_path / "compose.yaml"
    images = tmp_path / "images.lock.json"
    private_env = tmp_path / "control-plane.env"
    state = tmp_path / "state"
    compose.write_text("services: {}\n", encoding="ascii")
    images.write_text("{}\n", encoding="ascii")
    private_env.write_text("secret=" + "a" * 64 + "\n", encoding="ascii")
    for name, relative in backup_manifest.COMPONENTS.items():
        component = state / relative
        component.mkdir(parents=True)
        (component / "item").write_bytes(b"value")
        _archive(backup / f"{name}.tar", relative)
    backup_manifest.build_manifest(
        backup, backup.name, "stopped", compose, images, private_env, state
    )
    return backup, compose, images, private_env, state


def test_manifest_round_trip_and_exact_release_gate(tmp_path: Path) -> None:
    backup, compose, images, private_env, _ = _complete_backup(tmp_path)

    document = backup_manifest.validate_manifest(backup, compose, images, private_env)

    assert document["format"] == "kitdev-offline-physical-v1"
    assert document["secrets"]["included"] is False
    compose.write_text("services: {changed: {}}\n", encoding="ascii")
    with pytest.raises(backup_manifest.BackupError, match="backup_release_incompatible"):
        backup_manifest.validate_manifest(backup, compose, images, private_env)


def test_manifest_rejects_archive_tampering(tmp_path: Path) -> None:
    backup, compose, images, private_env, _ = _complete_backup(tmp_path)
    with (backup / "redis.tar").open("ab") as stream:
        stream.write(b"tamper")

    with pytest.raises(backup_manifest.BackupError, match="archive_size_mismatch"):
        backup_manifest.validate_manifest(backup, compose, images, private_env)


def test_archive_rejects_path_escape_and_symlink(tmp_path: Path) -> None:
    escaped = tmp_path / "escaped.tar"
    with tarfile.open(escaped, "w") as archive:
        item = tarfile.TarInfo("data/postgres/../../etc/shadow")
        item.size = 1
        archive.addfile(item, io.BytesIO(b"x"))
    with pytest.raises(backup_manifest.BackupError, match="archive_path_invalid"):
        backup_manifest.validate_archive(escaped, "data/postgres")

    linked = tmp_path / "linked.tar"
    with tarfile.open(linked, "w") as archive:
        root = tarfile.TarInfo("data/postgres")
        root.type = tarfile.DIRTYPE
        archive.addfile(root)
        item = tarfile.TarInfo("data/postgres/link")
        item.type = tarfile.SYMTYPE
        item.linkname = "/etc"
        archive.addfile(item)
    with pytest.raises(backup_manifest.BackupError, match="archive_special_member_forbidden"):
        backup_manifest.validate_archive(linked, "data/postgres")


def test_source_tree_rejects_symlink(tmp_path: Path) -> None:
    root = tmp_path / "tree"
    root.mkdir()
    (root / "target").write_text("value", encoding="ascii")
    (root / "link").symlink_to("target")

    with pytest.raises(backup_manifest.BackupError, match="source_symlink_forbidden"):
        backup_manifest.validate_source_tree(root)


def test_manifest_rejects_unexpected_backup_entry(tmp_path: Path) -> None:
    backup, compose, images, private_env, _ = _complete_backup(tmp_path)
    (backup / "secret.env").write_text("secret=value\n", encoding="ascii")

    with pytest.raises(backup_manifest.BackupError, match="backup_directory_entries_invalid"):
        backup_manifest.validate_manifest(backup, compose, images, private_env)


def test_manifest_binds_exact_private_environment(tmp_path: Path) -> None:
    backup, compose, images, private_env, _ = _complete_backup(tmp_path)
    private_env.write_text("secret=" + "b" * 64 + "\n", encoding="ascii")

    with pytest.raises(backup_manifest.BackupError, match="backup_release_incompatible"):
        backup_manifest.validate_manifest(backup, compose, images, private_env)


def test_resume_authenticates_published_component(tmp_path: Path) -> None:
    backup, _, _, _, state = _complete_backup(tmp_path)
    source = state / backup_manifest.COMPONENTS["redis"]
    published = tmp_path / "published-redis"
    source.rename(published)

    backup_manifest.validate_component_tree(backup / "manifest.json", "redis", published)
    (published / "item").write_bytes(b"corrupt")
    with pytest.raises(backup_manifest.BackupError, match="component_tree_mismatch"):
        backup_manifest.validate_component_tree(backup / "manifest.json", "redis", published)
