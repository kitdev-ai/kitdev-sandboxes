#!/usr/bin/env python3
"""Fail closed unless enabled APT sources are Ubuntu archive sources."""

from __future__ import annotations

import argparse
import json
import re
import shlex
import sys
from pathlib import Path
from urllib.parse import urlparse

SOURCE_ROOTS = (Path("/etc/apt/sources.list"), Path("/etc/apt/sources.list.d"))
KEYRING = Path("/usr/share/keyrings/ubuntu-archive-keyring.gpg")
ALLOWED_HOSTS = {
    "archive.ubuntu.com",
    "security.ubuntu.com",
    "ports.ubuntu.com",
    "old-releases.ubuntu.com",
    # Installed by this project's own docker role. Without it the validator
    # passes on a bare host and then refuses on every reapply, because the
    # source it is rejecting is one the play itself added.
    "download.docker.com",
}

# A provider mirror is an explicit operator decision, not a default. The OVH
# mirror used to sit in the built-in set, which quietly made this validator
# refuse every host that was not on that provider.
EXTRA_ALLOWED_HOSTS: set[str] = set()


def allowed_host(host: str) -> bool:
    return (
        host in ALLOWED_HOSTS
        or host in EXTRA_ALLOWED_HOSTS
        or host.endswith(".archive.ubuntu.com")
    )


def validate_uri(value: str) -> None:
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or not allowed_host(parsed.hostname):
        raise ValueError(f"non-Ubuntu APT origin: {value}")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError(f"unsafe APT origin syntax: {value}")


def source_files() -> list[Path]:
    result: list[Path] = []
    for root in SOURCE_ROOTS:
        if root.is_file():
            result.append(root)
        elif root.is_dir():
            result.extend(sorted((*root.glob("*.list"), *root.glob("*.sources"))))
    return result


def validate_list(path: Path, codename: str) -> int:
    count = 0
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        fields = shlex.split(line, comments=True)
        if not fields or fields[0] not in {"deb", "deb-src"}:
            raise ValueError(f"unsupported APT line in {path}")
        options = ""
        if len(fields) > 1 and fields[1].startswith("["):
            end = next((i for i, field in enumerate(fields[1:], 1) if field.endswith("]")), -1)
            if end < 0:
                raise ValueError(f"malformed APT options in {path}")
            options = " ".join(fields[1 : end + 1]).lower()
            if "trusted=yes" in options or "allow-insecure=yes" in options:
                raise ValueError(f"insecure APT option in {path}")
            signed_by = re.search(r"signed-by=([^\s\]]+)", options)
            if signed_by and signed_by.group(1) != str(KEYRING):
                raise ValueError(f"unexpected APT signing key in {path}")
            fields = [fields[0], *fields[end + 1 :]]
        if len(fields) < 3:
            raise ValueError(f"malformed APT source in {path}")
        validate_uri(fields[1])
        if fields[2] != codename and not fields[2].startswith(codename + "-"):
            raise ValueError(f"unexpected suite {fields[2]} in {path}")
        count += 1
    return count


def validate_deb822(path: Path, codename: str) -> int:
    count = 0
    paragraphs = re.split(r"\n\s*\n", path.read_text(encoding="utf-8").strip())
    for paragraph in paragraphs:
        fields: dict[str, str] = {}
        current = ""
        for line in paragraph.splitlines():
            if not line.strip() or line.lstrip().startswith("#"):
                continue
            if line.startswith((" ", "\t")) and current:
                fields[current] += " " + line.strip()
                continue
            if ":" not in line:
                raise ValueError(f"malformed deb822 source in {path}")
            current, value = line.split(":", 1)
            current = current.lower()
            fields[current] = value.strip()
        if fields.get("enabled", "yes").lower() == "no":
            continue
        source_types = set(fields.get("types", "").split())
        if not source_types or not source_types.issubset({"deb", "deb-src"}):
            raise ValueError(f"unsupported APT source type in {path}")
        if fields.get("trusted", "no").lower() == "yes" or fields.get("allow-insecure", "no").lower() == "yes":
            raise ValueError(f"insecure APT option in {path}")
        if fields.get("signed-by") != str(KEYRING):
            raise ValueError(f"unexpected APT signing key in {path}")
        uris = fields.get("uris", "").split()
        if not uris:
            raise ValueError(f"missing APT source URI in {path}")
        for uri in uris:
            validate_uri(uri)
        suites = fields.get("suites", "").split()
        if not suites or any(s != codename and not s.startswith(codename + "-") for s in suites):
            raise ValueError(f"unexpected APT suite in {path}")
        count += 1
    return count


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--codename", choices=("plucky", "resolute"), required=True)
    parser.add_argument(
        "--allow-host",
        action="append",
        default=[],
        metavar="HOST",
        help="additional APT mirror host the operator deliberately trusts",
    )
    args = parser.parse_args()
    for host in args.allow_host:
        if not re.fullmatch(r"[a-z0-9]([a-z0-9.-]{0,251}[a-z0-9])?", host):
            raise ValueError(f"invalid mirror host: {host}")
        EXTRA_ALLOWED_HOSTS.add(host)
    if not KEYRING.is_file() or KEYRING.is_symlink():
        raise ValueError("Ubuntu archive keyring is absent or unsafe")
    files = source_files()
    count = sum(validate_deb822(p, args.codename) if p.suffix == ".sources" else validate_list(p, args.codename) for p in files)
    if count == 0:
        raise ValueError("no enabled Ubuntu APT sources")
    print(json.dumps({"codename": args.codename, "enabled_sources": count, "status": "pass"}, sort_keys=True))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, UnicodeError, ValueError) as error:
        print(f"apt-source-validation: {error}", file=sys.stderr)
        raise SystemExit(65)
