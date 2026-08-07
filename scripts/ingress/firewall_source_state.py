#!/usr/bin/env python3
"""Validate and atomically maintain the ingress source ownership manifest."""

from __future__ import annotations

import ipaddress
import json
import os
import stat
import sys
import tempfile
from pathlib import Path
from typing import Any, NoReturn

STATE = Path("/etc/kitdev-sandboxes/ingress/allowed-sources.json")
MAXIMUM_BYTES = 65_536


def die(reason: str, code: int = 65) -> NoReturn:
    print(f"status=error reason={reason}", file=sys.stderr)
    raise SystemExit(code)


def normalize_cidr(
    value: str, *, allow_non_public: bool, allow_broad_range: bool
) -> tuple[str, bool, bool]:
    try:
        network = ipaddress.ip_network(value, strict=True)
    except ValueError:
        die("source_cidr_invalid", 64)
    canonical = str(network)
    if value != canonical or network.prefixlen == 0:
        die("source_cidr_not_canonical", 64)
    non_public = not network.is_global or any(
        (
            network.is_private,
            network.is_loopback,
            network.is_link_local,
            network.is_multicast,
            network.is_reserved,
            network.is_unspecified,
        )
    )
    if non_public and not allow_non_public:
        die("source_cidr_non_public_requires_override", 64)
    broad_range = network.prefixlen < (24 if network.version == 4 else 64)
    if broad_range and not allow_broad_range:
        die("source_cidr_broad_range_requires_override", 64)
    return canonical, non_public, broad_range


def empty_document() -> dict[str, Any]:
    return {"schema_version": 1, "sources": []}


def validate_document(value: object) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != {"schema_version", "sources"}:
        die("source_manifest_invalid")
    if value["schema_version"] != 1 or not isinstance(value["sources"], list):
        die("source_manifest_invalid")
    sources: list[dict[str, object]] = []
    previous: tuple[int, int, int] | None = None
    for item in value["sources"]:
        if not isinstance(item, dict) or set(item) != {
            "cidr",
            "non_public_override",
            "broad_range_override",
        }:
            die("source_manifest_invalid")
        cidr = item["cidr"]
        override = item["non_public_override"]
        broad_override = item["broad_range_override"]
        if (
            not isinstance(cidr, str)
            or not isinstance(override, bool)
            or not isinstance(broad_override, bool)
        ):
            die("source_manifest_invalid")
        canonical, non_public, broad_range = normalize_cidr(
            cidr,
            allow_non_public=override,
            allow_broad_range=broad_override,
        )
        network = ipaddress.ip_network(canonical)
        key = (network.version, int(network.network_address), network.prefixlen)
        if (
            canonical != cidr
            or override != non_public
            or broad_override != broad_range
            or (previous is not None and key <= previous)
        ):
            die("source_manifest_invalid")
        if any(network.overlaps(ipaddress.ip_network(source["cidr"])) for source in sources):
            die("source_manifest_overlapping_cidrs")
        previous = key
        sources.append(
            {
                "cidr": canonical,
                "non_public_override": override,
                "broad_range_override": broad_override,
            }
        )
    return {"schema_version": 1, "sources": sources}


def decode_document(payload: bytes) -> dict[str, Any]:
    if len(payload) > MAXIMUM_BYTES or b"\0" in payload:
        die("source_manifest_invalid")
    try:
        value = json.loads(payload.decode("ascii"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        die("source_manifest_invalid")
    return validate_document(value)


def secure_read(path: Path, *, missing_is_empty: bool) -> dict[str, Any]:
    try:
        before = os.lstat(path)
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    except FileNotFoundError:
        if missing_is_empty:
            return empty_document()
        die("source_manifest_missing")
    except OSError:
        die("source_manifest_unreadable")
    try:
        opened = os.fstat(descriptor)
        if (
            not stat.S_ISREG(opened.st_mode)
            or stat.S_ISLNK(before.st_mode)
            or (before.st_dev, before.st_ino) != (opened.st_dev, opened.st_ino)
            or opened.st_uid != os.geteuid()
            or opened.st_gid != os.getegid()
            or stat.S_IMODE(opened.st_mode) != 0o600
            or opened.st_nlink != 1
            or opened.st_size > MAXIMUM_BYTES
        ):
            die("source_manifest_untrusted")
        payload = os.read(descriptor, MAXIMUM_BYTES + 1)
        if len(payload) > MAXIMUM_BYTES or os.read(descriptor, 1):
            die("source_manifest_invalid")
    finally:
        os.close(descriptor)
    return decode_document(payload)


def encode_document(document: dict[str, Any]) -> bytes:
    return (json.dumps(document, sort_keys=True, separators=(",", ":")) + "\n").encode(
        "ascii"
    )


def install_document(document: dict[str, Any]) -> None:
    parent = STATE.parent
    try:
        metadata = os.lstat(parent)
    except OSError:
        die("source_manifest_parent_untrusted")
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or stat.S_ISLNK(metadata.st_mode)
        or metadata.st_uid != os.geteuid()
        or metadata.st_gid != os.getegid()
        or stat.S_IMODE(metadata.st_mode) != 0o700
    ):
        die("source_manifest_parent_untrusted")
    descriptor, name = tempfile.mkstemp(prefix=".allowed-sources.", dir=parent)
    temporary = Path(name)
    try:
        os.fchmod(descriptor, 0o600)
        os.write(descriptor, encode_document(document))
        os.fsync(descriptor)
        os.close(descriptor)
        descriptor = -1
        os.replace(temporary, STATE)
        directory = os.open(parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def render(document: dict[str, Any]) -> None:
    sys.stdout.buffer.write(encode_document(document))


def candidate(
    action: str, cidr: str, allow_non_public: bool, allow_broad_range: bool
) -> dict[str, Any]:
    document = secure_read(STATE, missing_is_empty=True)
    canonical, non_public, broad_range = normalize_cidr(
        cidr,
        allow_non_public=allow_non_public if action == "add" else True,
        allow_broad_range=allow_broad_range if action == "add" else True,
    )
    entries = {item["cidr"]: item for item in document["sources"]}
    if action == "add":
        entries[canonical] = {
            "cidr": canonical,
            "non_public_override": non_public,
            "broad_range_override": broad_range,
        }
    else:
        entries.pop(canonical, None)
    document["sources"] = sorted(
        entries.values(),
        key=lambda item: (
            ipaddress.ip_network(item["cidr"]).version,
            int(ipaddress.ip_network(item["cidr"]).network_address),
            ipaddress.ip_network(item["cidr"]).prefixlen,
        ),
    )
    return validate_document(document)


def main() -> None:
    arguments = sys.argv[1:]
    if arguments == ["export"]:
        render(secure_read(STATE, missing_is_empty=True))
        return
    if arguments == ["list"]:
        document = secure_read(STATE, missing_is_empty=True)
        print(
            json.dumps(
                {
                    "schema_version": 1,
                    "command": "firewall source list",
                    "status": "pass",
                    "sources": document["sources"],
                },
                sort_keys=True,
                separators=(",", ":"),
            )
        )
        return
    if len(arguments) == 2 and arguments[0] == "get-file":
        document = secure_read(Path(arguments[1]), missing_is_empty=False)
        for item in document["sources"]:
            print(item["cidr"])
        return
    if len(arguments) == 2 and arguments[0] == "install-file":
        install_document(secure_read(Path(arguments[1]), missing_is_empty=False))
        return
    if len(arguments) >= 2 and arguments[0] in {"candidate-add", "candidate-remove"}:
        flags = arguments[2:]
        if len(flags) != len(set(flags)) or any(
            flag not in {"--allow-non-public", "--allow-broad-range"} for flag in flags
        ):
            die("invalid_operation", 64)
        if arguments[0] == "candidate-remove" and flags:
            die("invalid_operation", 64)
        render(
            candidate(
                arguments[0].removeprefix("candidate-"),
                arguments[1],
                "--allow-non-public" in flags,
                "--allow-broad-range" in flags,
            )
        )
        return
    die("invalid_operation", 64)


if __name__ == "__main__":
    main()
