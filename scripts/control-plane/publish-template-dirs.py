#!/usr/bin/env python3
from __future__ import annotations

import ctypes
import errno
import os
import stat
import sys
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import NoReturn

BUILD_DIRS = (
    "2d9a8389-f5f5-4449-b0eb-e1d364ee98ae",
    "d757f43f-4871-4828-9d16-a54da5291f00",
    "6dfbb2b8-62a2-4a2b-a62a-cf94ffcdb5e5",
    "5f25449a-464b-4e10-83ba-e021db8b9b8e",
    "b2e8d4fb-f5ea-4e24-aec8-9af4fbe77c50",
    "6be65ea2-c917-43f5-8a56-fc8daa66fca4",
)
RENAME_NOREPLACE = 1


def fail() -> NoReturn:
    raise SystemExit(65)


def rename_noreplace(source: Path, target: Path) -> None:
    libc = ctypes.CDLL(None, use_errno=True)
    renameat2 = getattr(libc, "renameat2", None)
    if renameat2 is None:
        raise OSError(errno.ENOSYS, "renameat2 unavailable")
    renameat2.argtypes = [
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_uint,
    ]
    renameat2.restype = ctypes.c_int
    result = renameat2(
        -100,
        os.fsencode(source),
        -100,
        os.fsencode(target),
        RENAME_NOREPLACE,
    )
    if result != 0:
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error), target)


def entries(root: Path) -> dict[str, os.stat_result]:
    result: dict[str, os.stat_result] = {}
    for current, directories, files in os.walk(root, topdown=True, followlinks=False):
        current_path = Path(current)
        for name in directories + files:
            path = current_path / name
            metadata = os.lstat(path)
            if stat.S_ISLNK(metadata.st_mode) or not (
                stat.S_ISDIR(metadata.st_mode) or stat.S_ISREG(metadata.st_mode)
            ):
                fail()
            result[str(path.relative_to(root))] = metadata
    return result


def files_equal(first: Path, second: Path) -> bool:
    first_fd = os.open(first, os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0))
    try:
        second_fd = os.open(second, os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0))
        try:
            while True:
                left = os.read(first_fd, 1024 * 1024)
                right = os.read(second_fd, 1024 * 1024)
                if left != right:
                    return False
                if not left:
                    return True
        finally:
            os.close(second_fd)
    finally:
        os.close(first_fd)


def trees_equal(first: Path, second: Path) -> bool:
    try:
        first_root = os.lstat(first)
        second_root = os.lstat(second)
        if not stat.S_ISDIR(first_root.st_mode) or not stat.S_ISDIR(second_root.st_mode):
            return False
        first_entries = entries(first)
        second_entries = entries(second)
    except (FileNotFoundError, OSError, SystemExit):
        return False
    fields = ("st_uid", "st_gid", "st_mode", "st_nlink", "st_size")
    if any(getattr(first_root, field) != getattr(second_root, field) for field in fields):
        return False
    if set(first_entries) != set(second_entries):
        return False
    for relative, first_metadata in first_entries.items():
        second_metadata = second_entries[relative]
        if any(
            getattr(first_metadata, field) != getattr(second_metadata, field) for field in fields
        ):
            return False
        if stat.S_ISREG(first_metadata.st_mode) and not files_equal(
            first / relative, second / relative
        ):
            return False
    return True


def fsync_tree(root: Path) -> None:
    directories = [root]
    for current, names, files in os.walk(root, topdown=True, followlinks=False):
        current_path = Path(current)
        for name in files:
            path = current_path / name
            metadata = os.lstat(path)
            if not stat.S_ISREG(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode):
                fail()
            descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0))
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
        directories.extend(current_path / name for name in names)
    for path in reversed(directories):
        descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)


def fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def publish(
    source_parent: Path,
    target_parent: Path,
    names: Iterable[str] = BUILD_DIRS,
    rename: Callable[[Path, Path], None] = rename_noreplace,
) -> None:
    names = tuple(names)
    if (
        len(names) != len(set(names))
        or any(not name or name in {".", ".."} or "/" in name for name in names)
        or set(os.listdir(source_parent)) != set(names)
    ):
        fail()
    if os.lstat(source_parent).st_dev != os.lstat(target_parent).st_dev:
        fail()
    for name in names:
        source = source_parent / name
        target = target_parent / name
        fsync_tree(source)
        try:
            rename(source, target)
        except OSError as error:
            if error.errno != errno.EEXIST or not trees_equal(source, target):
                fail()
        fsync_directory(target_parent)
    fsync_directory(target_parent)


def main() -> None:
    if len(sys.argv) != 3 or not sys.platform.startswith("linux"):
        fail()
    publish(Path(sys.argv[1]), Path(sys.argv[2]))
    print("status=pass operation=publish-template-dirs")


if __name__ == "__main__":
    main()
