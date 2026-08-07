#!/usr/bin/env python3
"""Verify the live kernel state without shell parsing."""

from __future__ import annotations

import sys
from pathlib import Path


def integer(path: str) -> int:
    return int(Path(path).read_text(encoding="ascii").strip())


def main() -> int:
    if len(sys.argv) != 4:
        return 64
    nbd_devices, nbd_partitions, hugepages = map(int, sys.argv[1:])
    expected = {
        "/proc/sys/net/ipv4/ip_forward": 1,
        "/proc/sys/vm/nr_hugepages": hugepages,
    }
    minimums = {
        "/sys/module/nbd/parameters/nbds_max": nbd_devices,
        "/sys/module/nbd/parameters/max_part": nbd_partitions,
    }
    if any(integer(path) != value for path, value in expected.items()):
        return 65
    if any(integer(path) < value for path, value in minimums.items()):
        return 65
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
