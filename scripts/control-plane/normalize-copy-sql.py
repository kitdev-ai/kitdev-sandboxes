#!/usr/bin/env python3
from __future__ import annotations

import os
import re
import stat
import sys
from pathlib import Path
from typing import NoReturn


def fail() -> NoReturn:
    raise SystemExit(65)


if len(sys.argv) != 5:
    fail()
source, target = map(Path, sys.argv[1:3])
build_id, team_id = sys.argv[3:]
uuid_pattern = r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
if not re.fullmatch(uuid_pattern, build_id) or not re.fullmatch(uuid_pattern, team_id):
    fail()
before = os.lstat(source)
if (
    not stat.S_ISREG(before.st_mode)
    or stat.S_ISLNK(before.st_mode)
    or before.st_uid != os.getuid()
    or before.st_gid != os.getgid()
    or stat.S_IMODE(before.st_mode) != 0o600
    or before.st_nlink != 1
    or before.st_size > 32_768
):
    fail()
descriptor = os.open(source, os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0))
try:
    opened = os.fstat(descriptor)
    if (before.st_dev, before.st_ino) != (opened.st_dev, opened.st_ino):
        fail()
    data = bytearray()
    while True:
        chunk = os.read(descriptor, min(32_769 - len(data), 8_192))
        if not chunk:
            break
        data.extend(chunk)
        if len(data) > 32_768:
            fail()
    after = os.fstat(descriptor)
    if (opened.st_size, opened.st_mtime_ns, opened.st_ctime_ns) != (
        after.st_size,
        after.st_mtime_ns,
        after.st_ctime_ns,
    ):
        fail()
finally:
    os.close(descriptor)
try:
    sql = bytes(data).decode("ascii")
except UnicodeDecodeError:
    fail()

pattern = re.compile(
    r"BEGIN;\n"
    r"INSERT INTO public\.envs \(id, team_id, updated_at, public, source\)\n"
    rf"VALUES \('(?P<env>[a-z0-9]{{16,32}})', '{re.escape(team_id)}', "
    r"NOW\(\), FALSE, 'template'\);\n\n"
    r"INSERT INTO public\.env_builds \(id, env_id, updated_at, finished_at, status, "
    r"ram_mb, vcpu, kernel_version, firecracker_version, envd_version, "
    r"free_disk_size_mb, total_disk_size_mb\)\n"
    rf"VALUES \('{re.escape(build_id)}', '(?P=env)', NOW\(\), NOW\(\), "
    r"'uploaded', 1024, 2, 'vmlinux-6\.1\.158', 'v1\.14\.1_431f1fc', "
    r"'0\.6\.13', 1024, 1024\);\n\n"
    r"INSERT INTO public\.env_build_assignments \(env_id, build_id, tag\)\n"
    rf"VALUES \('(?P=env)', '{re.escape(build_id)}', 'default'\);\n"
    r"COMMIT;\n"
)
match = pattern.fullmatch(sql)
if match is None:
    fail()
env_id = match.group("env")
normalized = (
    "BEGIN;\n"
    "INSERT INTO public.envs (id, team_id, updated_at, public, source)\n"
    f"VALUES ('{env_id}', '{team_id}', NOW(), FALSE, 'template');\n\n"
    "INSERT INTO public.env_builds (id, env_id, updated_at, finished_at, status, "
    "ram_mb, vcpu, kernel_version, firecracker_version, envd_version, "
    "free_disk_size_mb, total_disk_size_mb)\n"
    f"VALUES ('{build_id}', '{env_id}', NOW(), NOW(), 'uploaded', 1024, 2, "
    "'vmlinux-6.1.158', 'v1.14.1_431f1fc', '0.6.13', 1024, 3722);\n\n"
    "INSERT INTO public.env_build_assignments (env_id, build_id, tag)\n"
    f"VALUES ('{env_id}', '{build_id}', 'default');\n"
    "COMMIT;\n"
).encode("ascii")
descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
try:
    view = memoryview(normalized)
    while view:
        written = os.write(descriptor, view)
        if written <= 0:
            fail()
        view = view[written:]
    os.fsync(descriptor)
finally:
    os.close(descriptor)
