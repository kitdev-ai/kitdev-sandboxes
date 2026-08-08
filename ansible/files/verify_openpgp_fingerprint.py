#!/usr/bin/env python3
"""Verify an ASCII-armoured OpenPGP key's primary fingerprint.

Downloading a signing key over HTTPS proves only that something answered. The
fingerprint is what binds the key to the publisher, so it is checked here
before the key is ever used to authenticate a package source.

Implemented without gnupg so the prerequisite package set stays minimal: the
primary public-key packet is parsed directly and its fingerprint computed per
RFC 4880 (v4) and RFC 9580 (v6).
"""

from __future__ import annotations

import base64
import hashlib
import re
import sys
from pathlib import Path

ARMOUR = re.compile(
    r"-----BEGIN PGP PUBLIC KEY BLOCK-----(.*?)-----END PGP PUBLIC KEY BLOCK-----",
    re.DOTALL,
)


def fail(reason: str) -> "NoReturn":  # noqa: F821
    print(f"status=error reason={reason}", file=sys.stderr)
    raise SystemExit(65)


def armour_payload(text: str) -> bytes:
    match = ARMOUR.search(text)
    if match is None:
        fail("key_not_ascii_armoured")
    body: list[str] = []
    for line in match.group(1).splitlines():
        line = line.strip()
        if not line or ":" in line:
            continue
        if line.startswith("="):  # CRC24 checksum terminator
            break
        body.append(line)
    if not body:
        fail("key_body_empty")
    try:
        return base64.b64decode("".join(body), validate=True)
    except (ValueError, TypeError):
        fail("key_body_invalid")


def first_public_key_packet(data: bytes) -> tuple[int, bytes]:
    """Return (version, body) of the first public-key packet."""
    if not data:
        fail("key_empty")
    header = data[0]
    if not header & 0x80:
        fail("key_packet_invalid")
    if header & 0x40:  # new format
        tag = header & 0x3F
        length_octet = data[1]
        if length_octet < 192:
            offset, length = 2, length_octet
        elif length_octet < 224:
            offset = 3
            length = ((length_octet - 192) << 8) + data[2] + 192
        elif length_octet == 255:
            offset = 6
            length = int.from_bytes(data[2:6], "big")
        else:
            fail("key_packet_length_unsupported")
    else:  # old format
        tag = (header >> 2) & 0x0F
        size = header & 0x03
        if size == 0:
            offset, length = 2, data[1]
        elif size == 1:
            offset, length = 3, int.from_bytes(data[1:3], "big")
        elif size == 2:
            offset, length = 5, int.from_bytes(data[1:5], "big")
        else:
            fail("key_packet_length_unsupported")
    if tag != 6:  # public-key packet
        fail("key_first_packet_not_public_key")
    body = data[offset : offset + length]
    if len(body) != length or not body:
        fail("key_packet_truncated")
    return body[0], body


def fingerprint(version: int, body: bytes) -> str:
    if version == 4:
        prefix = b"\x99" + len(body).to_bytes(2, "big")
        return hashlib.sha1(prefix + body).hexdigest().upper()  # noqa: S324
    if version == 6:
        prefix = b"\x9b" + len(body).to_bytes(4, "big")
        return hashlib.sha256(prefix + body).hexdigest().upper()
    fail("key_version_unsupported")


def main() -> None:
    if len(sys.argv) != 3:
        fail("invalid_arguments")
    path, expected = Path(sys.argv[1]), sys.argv[2].replace(" ", "").upper()
    if not re.fullmatch(r"[0-9A-F]{40}|[0-9A-F]{64}", expected):
        fail("expected_fingerprint_invalid")
    try:
        text = path.read_text(encoding="ascii")
    except (OSError, UnicodeDecodeError):
        fail("key_unreadable")
    version, body = first_public_key_packet(armour_payload(text))
    observed = fingerprint(version, body)
    if observed != expected:
        fail("key_fingerprint_mismatch")
    print(f"status=pass operation=verify-openpgp-fingerprint fingerprint={observed}")


if __name__ == "__main__":
    main()
