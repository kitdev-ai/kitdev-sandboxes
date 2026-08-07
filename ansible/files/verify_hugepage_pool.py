#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path


def parse_meminfo(text: str) -> dict[str, int]:
    values: dict[str, int] = {}
    for line in text.splitlines():
        if ":" not in line:
            continue
        name, raw = line.split(":", 1)
        fields = raw.split()
        if fields and fields[0].isdigit():
            values[name] = int(fields[0])
    return values


def verify_pool(values: dict[str, int], pages: int, reserve_mib: int) -> dict[str, int | str]:
    required = {
        "MemAvailable",
        "HugePages_Total",
        "HugePages_Free",
        "HugePages_Rsvd",
        "HugePages_Surp",
        "Hugepagesize",
    }
    if not required.issubset(values):
        raise ValueError("incomplete meminfo")
    if values["Hugepagesize"] != 2048:
        raise ValueError("host does not expose 2 MiB hugepages")
    if values["HugePages_Total"] != pages or values["HugePages_Free"] != pages:
        raise ValueError("hugepage pool is not fully free at the requested size")
    if values["HugePages_Rsvd"] != 0 or values["HugePages_Surp"] != 0:
        raise ValueError("hugepage pool has reserved or surplus pages")
    available_mib = values["MemAvailable"] // 1024
    if available_mib < reserve_mib:
        raise ValueError("ordinary available memory is below the required reserve")
    return {
        "hugepages_2m_free": values["HugePages_Free"],
        "hugepages_2m_total": values["HugePages_Total"],
        "mem_available_mib": available_mib,
        "status": "pass",
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pages", type=int, required=True)
    parser.add_argument("--reserve-mib", type=int, required=True)
    parser.add_argument("--meminfo", type=Path, default=Path("/proc/meminfo"))
    args = parser.parse_args()
    if args.pages < 1 or args.reserve_mib < 4096:
        raise ValueError("invalid hugepage verification policy")
    values = parse_meminfo(args.meminfo.read_text(encoding="ascii"))
    print(json.dumps(verify_pool(values, args.pages, args.reserve_mib), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
