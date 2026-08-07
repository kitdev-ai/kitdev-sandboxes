#!/usr/bin/env python3
"""Validate a HugeTLB request using total, available, and current pool memory."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def parse_meminfo(text: str) -> dict[str, int]:
    result: dict[str, int] = {}
    for line in text.splitlines():
        fields = line.split()
        if len(fields) not in {2, 3} or not fields[0].endswith(":"):
            continue
        if not fields[1].isdigit() or (len(fields) == 3 and fields[2] != "kB"):
            continue
        result[fields[0][:-1]] = int(fields[1])
    return result


def validate_capacity(
    values: dict[str, int], requested_pages: int, max_ram_percent: int, reserve_mb: int
) -> None:
    required = {"MemTotal", "MemAvailable", "HugePages_Total", "Hugepagesize"}
    if not required.issubset(values) or values["Hugepagesize"] != 2048:
        raise ValueError("required 2 MiB hugepage memory facts unavailable")
    requested_kb = requested_pages * values["Hugepagesize"]
    if requested_kb * 100 > values["MemTotal"] * max_ram_percent:
        raise ValueError("requested hugepage pool exceeds total-RAM budget")
    additional_pages = max(requested_pages - values["HugePages_Total"], 0)
    additional_kb = additional_pages * values["Hugepagesize"]
    if additional_kb + reserve_mb * 1024 > values["MemAvailable"]:
        raise ValueError("insufficient currently available memory for hugepage allocation")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--hugepages", type=int, required=True)
    parser.add_argument("--max-ram-percent", type=int, required=True)
    parser.add_argument("--min-available-mb-after", type=int, required=True)
    parser.add_argument("--meminfo", type=Path, default=Path("/proc/meminfo"))
    args = parser.parse_args()
    if args.hugepages < 0 or not 1 <= args.max_ram_percent <= 50 or args.min_available_mb_after < 512:
        raise ValueError("invalid capacity policy")
    values = parse_meminfo(args.meminfo.read_text(encoding="ascii"))
    validate_capacity(values, args.hugepages, args.max_ram_percent, args.min_available_mb_after)
    print(json.dumps({"hugepages": args.hugepages, "status": "pass"}, sort_keys=True))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, UnicodeError, ValueError) as error:
        print(f"host-capacity-validation: {error}", file=sys.stderr)
        raise SystemExit(65)
