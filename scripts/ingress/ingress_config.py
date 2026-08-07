#!/usr/bin/env python3
from __future__ import annotations

import os
import re
import stat
import sys
from pathlib import Path

CONFIG = Path("/etc/kitdev-sandboxes/ingress/ingress.env")
EXPECTED_KEYS = {
    "KITDEV_INGRESS_DOMAIN",
    "KITDEV_ACME_EMAIL",
    "KITDEV_DNS_PROVIDER",
    "KITDEV_ACME_SERVER",
}
ACME_SERVERS = {
    "https://acme-v02.api.letsencrypt.org/directory",
    "https://acme-staging-v02.api.letsencrypt.org/directory",
}


def die(reason: str) -> "NoReturn":
    print(f"status=error reason={reason}", file=sys.stderr)
    raise SystemExit(65)


def read_config() -> dict[str, str]:
    try:
        parent = os.lstat(CONFIG.parent)
        metadata = os.lstat(CONFIG)
    except OSError:
        die("ingress_config_missing")
    if (
        not stat.S_ISDIR(parent.st_mode)
        or stat.S_ISLNK(parent.st_mode)
        or parent.st_uid != 0
        or parent.st_gid != 0
        or stat.S_IMODE(parent.st_mode) != 0o700
    ):
        die("ingress_config_parent_untrusted")
    if (
        not stat.S_ISREG(metadata.st_mode)
        or stat.S_ISLNK(metadata.st_mode)
        or metadata.st_uid != 0
        or metadata.st_gid != 0
        or stat.S_IMODE(metadata.st_mode) != 0o600
        or metadata.st_nlink != 1
        or metadata.st_size > 8192
    ):
        die("ingress_config_untrusted")
    flags = os.O_RDONLY | os.O_CLOEXEC | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(CONFIG, flags)
    try:
        opened = os.fstat(descriptor)
        if (opened.st_dev, opened.st_ino) != (metadata.st_dev, metadata.st_ino):
            die("ingress_config_changed")
        raw = os.read(descriptor, 8193)
    finally:
        os.close(descriptor)
    if len(raw) > 8192 or b"\0" in raw:
        die("ingress_config_invalid")
    try:
        text = raw.decode("ascii")
    except UnicodeDecodeError:
        die("ingress_config_invalid")
    values: dict[str, str] = {}
    for line in text.splitlines():
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            die("ingress_config_invalid")
        key, value = line.split("=", 1)
        if key not in EXPECTED_KEYS or key in values or not value:
            die("ingress_config_invalid")
        values[key] = value
    if set(values) != EXPECTED_KEYS:
        die("ingress_config_incomplete")
    if values["KITDEV_INGRESS_DOMAIN"] != "sandbox.kitdev.ai":
        die("ingress_domain_mismatch")
    email = values["KITDEV_ACME_EMAIL"]
    if email == "operator@example.invalid" or not re.fullmatch(r"[^\s@]+@[^\s@]+", email):
        die("acme_email_required")
    provider = values["KITDEV_DNS_PROVIDER"]
    if provider == "replace-with-lego-provider-code" or not re.fullmatch(
        r"[a-z][a-z0-9-]{0,63}", provider
    ):
        die("dns_provider_required")
    if values["KITDEV_ACME_SERVER"] not in ACME_SERVERS:
        die("acme_server_not_allowed")
    return values


def main() -> None:
    if len(sys.argv) != 2 or sys.argv[1] not in {"verify", "get"}:
        die("invalid_operation")
    values = read_config()
    if sys.argv[1] == "get":
        for key in (
            "KITDEV_INGRESS_DOMAIN",
            "KITDEV_ACME_EMAIL",
            "KITDEV_DNS_PROVIDER",
            "KITDEV_ACME_SERVER",
        ):
            print(values[key])
    else:
        print("status=pass operation=verify-ingress-config")


if __name__ == "__main__":
    main()
