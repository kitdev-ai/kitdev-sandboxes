from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_backup_trap_precedes_first_quiesce_mutation() -> None:
    script = (ROOT / "scripts/control-plane/backup-restore.sh").read_text(encoding="ascii")
    function = script.split("make_backup() {", 1)[1].split("\n}", 1)[0]

    assert function.index("trap cleanup EXIT INT TERM HUP") < function.index("quiesce_for_backup")


def test_restore_journal_preserves_stage_and_authenticates_resume() -> None:
    script = (ROOT / "scripts/control-plane/backup-restore.sh").read_text(encoding="ascii")
    restore = script.split("restore_backup() {", 1)[1].split("\n}", 1)[0]
    publish = script.split("publish_components() {", 1)[1].split("\n}", 1)[0]

    assert restore.index("publication_started=1") < restore.index("write_restore_journal")
    assert restore.index("require_staged_component_integrity") < restore.index(
        "write_restore_journal"
    )
    assert "require_matching_journal" in restore
    assert "validate-tree" in publish
    assert publish.index("validate-tree") < publish.index("continue")


def test_installer_publishes_both_backup_assets() -> None:
    lifecycle = (ROOT / "scripts/control-plane/lifecycle.sh").read_text(encoding="ascii")

    assert "backup-restore.sh" in lifecycle
    assert "backup_manifest.py" in lifecycle
