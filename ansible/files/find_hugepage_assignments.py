#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import re
import stat
from pathlib import Path


ASSIGNMENT = re.compile(rb"^[ \t]*vm\.nr_hugepages[ \t]*=", re.MULTILINE)
MAX_FILE_BYTES = 1024 * 1024


def find_assignments(root: Path) -> list[str]:
    root_stat = root.lstat()
    if not stat.S_ISDIR(root_stat.st_mode) or stat.S_ISLNK(root_stat.st_mode):
        raise ValueError("sysctl root is not a direct directory")
    assignments: list[str] = []
    for path in sorted(root.iterdir()):
        metadata = path.lstat()
        if stat.S_ISLNK(metadata.st_mode):
            raise ValueError(f"sysctl directory contains symlink: {path.name}")
        if not stat.S_ISREG(metadata.st_mode):
            continue
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        try:
            opened = os.fstat(descriptor)
            if (opened.st_dev, opened.st_ino) != (metadata.st_dev, metadata.st_ino):
                raise ValueError("sysctl file changed during read")
            if opened.st_size > MAX_FILE_BYTES:
                raise ValueError(f"sysctl file is oversized: {path.name}")
            content = os.read(descriptor, MAX_FILE_BYTES + 1)
            if len(content) != opened.st_size:
                raise ValueError("sysctl file changed or could not be read completely")
        finally:
            os.close(descriptor)
        if ASSIGNMENT.search(content):
            assignments.append(str(path))
    return assignments


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path("/etc/sysctl.d"))
    args = parser.parse_args()
    print(json.dumps({"assignments": find_assignments(args.root)}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
