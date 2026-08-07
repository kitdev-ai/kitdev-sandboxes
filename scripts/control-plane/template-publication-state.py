#!/usr/bin/env python3
"""Validate and atomically update the stable-template ownership journal."""

from __future__ import annotations

import argparse
import json
import os
import re
import stat
import tempfile
from datetime import UTC, datetime
from pathlib import Path


ALIASES = {
    "coding": "kitdev-coding",
    "browser-heavy": "kitdev-browser-heavy",
}
ID_RE = re.compile(r"[a-z0-9]{16,32}")
BUILD_RE = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
VERSION_RE = re.compile(r"v[1-9][0-9]{0,5}")
HASH_RE = re.compile(r"[0-9a-f]{64}")


class StateError(RuntimeError):
    pass


def now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def validate_record(record: object) -> dict[str, object]:
    if not isinstance(record, dict):
        raise StateError("record_not_object")
    required = {"schema_version", "product", "alias", "version", "state", "definition_sha256", "created_at"}
    allowed = required | {"template_id", "build_id", "published_at", "rolled_back_at"}
    if not required <= set(record) or not set(record) <= allowed:
        raise StateError("record_keys_invalid")
    product = record["product"]
    if product not in ALIASES or record["alias"] != ALIASES[product]:
        raise StateError("record_product_invalid")
    if record["schema_version"] != 1 or not VERSION_RE.fullmatch(str(record["version"])):
        raise StateError("record_version_invalid")
    if record["state"] not in {"reserved", "qualified_private", "published", "rolled_back"}:
        raise StateError("record_state_invalid")
    if not HASH_RE.fullmatch(str(record["definition_sha256"])):
        raise StateError("record_hash_invalid")
    if record["state"] != "reserved":
        if not ID_RE.fullmatch(str(record.get("template_id", ""))):
            raise StateError("record_template_id_invalid")
        if not BUILD_RE.fullmatch(str(record.get("build_id", ""))):
            raise StateError("record_build_id_invalid")
    return record


def load(path: Path) -> dict[str, object] | None:
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        return None
    if (
        not stat.S_ISREG(metadata.st_mode)
        or metadata.st_uid != 0
        or metadata.st_gid != 0
        or stat.S_IMODE(metadata.st_mode) != 0o600
        or metadata.st_nlink != 1
        or metadata.st_size > 16_384
    ):
        raise StateError("journal_metadata_invalid")
    return validate_record(json.loads(path.read_text(encoding="ascii")))


def write(path: Path, record: dict[str, object]) -> None:
    validate_record(record)
    directory = path.parent
    metadata = directory.lstat()
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or metadata.st_uid != 0
        or metadata.st_gid != 0
        or stat.S_IMODE(metadata.st_mode) != 0o700
    ):
        raise StateError("journal_directory_invalid")
    payload = (json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n").encode("ascii")
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=directory)
    try:
        os.fchmod(descriptor, 0o600)
        os.write(descriptor, payload)
        os.fsync(descriptor)
        os.close(descriptor)
        descriptor = -1
        os.replace(temporary, path)
        directory_fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


def expected(args: argparse.Namespace) -> tuple[str, str, str]:
    alias = ALIASES[args.product]
    if not VERSION_RE.fullmatch(args.version):
        raise StateError("version_invalid")
    if not HASH_RE.fullmatch(args.definition_sha256):
        raise StateError("definition_hash_invalid")
    return alias, args.version, args.definition_sha256


def require_match(record: dict[str, object], args: argparse.Namespace) -> None:
    alias, version, digest = expected(args)
    if (
        record["product"] != args.product
        or record["alias"] != alias
        or record["version"] != version
        or record["definition_sha256"] != digest
    ):
        raise StateError("journal_ownership_conflict")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("operation", choices=("reserve", "candidate", "publish", "show", "rollback"))
    parser.add_argument("--journal", type=Path, required=True)
    parser.add_argument("--product", choices=tuple(ALIASES), required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--definition-sha256", required=True)
    parser.add_argument("--template-id")
    parser.add_argument("--build-id")
    args = parser.parse_args()
    alias, version, digest = expected(args)
    record = load(args.journal)

    if args.operation == "reserve":
        if record is None:
            record = {
                "schema_version": 1,
                "product": args.product,
                "alias": alias,
                "version": version,
                "state": "reserved",
                "definition_sha256": digest,
                "created_at": now(),
            }
            write(args.journal, record)
        else:
            require_match(record, args)
    else:
        if record is None:
            raise StateError("journal_missing")
        require_match(record, args)

    assert record is not None
    if args.operation == "candidate":
        if record["state"] == "reserved":
            if not ID_RE.fullmatch(args.template_id or "") or not BUILD_RE.fullmatch(args.build_id or ""):
                raise StateError("candidate_ids_invalid")
            record.update(template_id=args.template_id, build_id=args.build_id, state="qualified_private")
            write(args.journal, record)
        elif record.get("template_id") != args.template_id or record.get("build_id") != args.build_id:
            raise StateError("candidate_ownership_conflict")
    elif args.operation == "publish":
        if record["state"] == "qualified_private":
            record.update(state="published", published_at=now())
            write(args.journal, record)
        elif record["state"] != "published":
            raise StateError("publish_state_invalid")
    elif args.operation == "rollback":
        if record["state"] not in {"qualified_private", "published", "rolled_back"}:
            raise StateError("rollback_state_invalid")
        if record["state"] != "rolled_back":
            record.update(state="rolled_back", rolled_back_at=now())
            write(args.journal, record)

    print(json.dumps(record, sort_keys=True, separators=(",", ":")))


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, json.JSONDecodeError, StateError) as error:
        message = str(error) if str(error) else error.__class__.__name__
        print(f"status=error operation=template-publication-state reason={message}", file=os.sys.stderr)
        raise SystemExit(65) from error
