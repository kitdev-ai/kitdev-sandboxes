#!/usr/bin/env python3
from __future__ import annotations

import os
import re
import stat
import subprocess
import sys
from pathlib import Path

CREDENTIALS = Path("/etc/kitdev-sandboxes/ingress/acme-provider.env")
LEGO = Path("/opt/kitdev-sandboxes/bin/lego")
BLOCKED_KEYS = {
    "BASH_ENV",
    "ENV",
    "HOME",
    "IFS",
    "LD_PRELOAD",
    "LD_LIBRARY_PATH",
    "PATH",
    "SHELLOPTS",
}


def die(reason: str) -> "NoReturn":
    print(f"status=error reason={reason}", file=sys.stderr)
    raise SystemExit(65)


def read_credentials() -> dict[str, str]:
    try:
        metadata = os.lstat(CREDENTIALS)
    except OSError:
        die("dns_provider_credentials_missing")
    if (
        not stat.S_ISREG(metadata.st_mode)
        or stat.S_ISLNK(metadata.st_mode)
        or metadata.st_uid != 0
        or metadata.st_gid != 0
        or stat.S_IMODE(metadata.st_mode) != 0o600
        or metadata.st_nlink != 1
        or not 0 < metadata.st_size <= 16384
    ):
        die("dns_provider_credentials_untrusted")
    flags = os.O_RDONLY | os.O_CLOEXEC | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(CREDENTIALS, flags)
    try:
        opened = os.fstat(descriptor)
        if (opened.st_dev, opened.st_ino) != (metadata.st_dev, metadata.st_ino):
            die("dns_provider_credentials_changed")
        raw = os.read(descriptor, 16385)
    finally:
        os.close(descriptor)
    if len(raw) > 16384 or b"\0" in raw:
        die("dns_provider_credentials_invalid")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        die("dns_provider_credentials_invalid")
    values: dict[str, str] = {}
    for line in text.splitlines():
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            die("dns_provider_credentials_invalid")
        key, value = line.split("=", 1)
        if (
            not re.fullmatch(r"[A-Z][A-Z0-9_]{0,127}", key)
            or key in BLOCKED_KEYS
            or key.startswith("LD_")
            or key in values
            or not value
            or len(value) > 4096
            or any(ord(character) < 32 for character in value)
        ):
            die("dns_provider_credentials_invalid")
        values[key] = value
    if not values:
        die("dns_provider_credentials_empty")
    return values


def main() -> None:
    if len(sys.argv) != 7:
        die("invalid_operation")
    provider, email, server, state, domain, operation = sys.argv[1:]
    if not re.fullmatch(r"[a-z][a-z0-9-]{0,63}", provider):
        die("dns_provider_invalid")
    if operation not in {"run", "renew"}:
        die("invalid_operation")
    if domain != "sandbox.kitdev.ai":
        die("ingress_domain_mismatch")
    environment = {
        "HOME": "/root",
        "LANG": "C",
        "LC_ALL": "C",
        "PATH": "/usr/sbin:/usr/bin:/sbin:/bin",
    }
    environment.update(read_credentials())
    command = [
        str(LEGO),
        "--accept-tos",
        "--path",
        state,
        "--email",
        email,
        "--server",
        server,
        "--dns",
        provider,
        "--domains",
        f"*.{domain}",
        operation,
    ]
    if operation == "renew":
        command.extend(("--days", "30"))
    raise SystemExit(subprocess.run(command, env=environment, check=False).returncode)


if __name__ == "__main__":
    main()
