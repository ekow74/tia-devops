#!/usr/bin/env python3
"""Compare nmap greppable open TCP ports to an explicit allowlist."""
from __future__ import annotations

import argparse
import re
from pathlib import Path

PORT_RE = re.compile(r"(\d+)/open/tcp")


def parse_expected(raw: str) -> set[int]:
    if not raw.strip():
        return set()
    out: set[int] = set()
    for item in raw.split(","):
        item = item.strip()
        if not item:
            continue
        port = int(item)
        if not 1 <= port <= 65535:
            raise ValueError(f"invalid expected port: {port}")
        out.add(port)
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("scan", type=Path)
    ap.add_argument("--expected", required=True)
    ap.add_argument("--family", choices=("ipv4", "ipv6"), required=True)
    args = ap.parse_args()

    expected = parse_expected(args.expected)
    text = args.scan.read_text(encoding="utf-8", errors="replace")
    actual = {int(m.group(1)) for m in PORT_RE.finditer(text)}

    if actual != expected:
        missing = sorted(expected - actual)
        unexpected = sorted(actual - expected)
        print(f"external-port-scan: FAIL family={args.family}")
        print(f" expected={sorted(expected)}")
        print(f" actual={sorted(actual)}")
        if missing:
            print(f" missing_expected={missing}")
        if unexpected:
            print(f" unexpected_open={unexpected}")
        return 1

    print(f"external-port-scan: PASS family={args.family} open={sorted(actual)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
