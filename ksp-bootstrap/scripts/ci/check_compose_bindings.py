#!/usr/bin/env python3
"""Reject Docker Compose published ports that bind to all interfaces."""
from __future__ import annotations

import argparse
import ipaddress
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
SHORT = re.compile(r'^\s*-\s*["\']?([^#"\']+)["\']?\s*(?:#.*)?$')


def safe_host(host: str) -> bool:
    host = host.strip()
    if host == "127.0.0.1" or host == "::1":
        return True
    try:
        ip = ipaddress.ip_address(host)
        return ip.is_private or ip.is_loopback
    except ValueError:
        return False


def check(path: Path) -> list[str]:
    lines = path.read_text(errors="replace").splitlines()
    errors: list[str] = []
    in_ports = False
    ports_indent = -1
    long_entry_host: str | None = None
    long_entry_published = False

    def flush_long(line_no: int) -> None:
        nonlocal long_entry_host, long_entry_published
        if long_entry_published and not long_entry_host:
            errors.append(f"{path.relative_to(ROOT)}:{line_no}: long-syntax published port missing host_ip")
        elif long_entry_published and not safe_host(long_entry_host or ""):
            errors.append(f"{path.relative_to(ROOT)}:{line_no}: unsafe host_ip {long_entry_host}")
        long_entry_host = None
        long_entry_published = False

    for i, raw in enumerate(lines, 1):
        stripped = raw.strip()
        indent = len(raw) - len(raw.lstrip())
        if not stripped or stripped.startswith("#"):
            continue
        if stripped == "ports:":
            flush_long(i)
            in_ports = True
            ports_indent = indent
            continue
        if in_ports and indent <= ports_indent and not stripped.startswith("-"):
            flush_long(i)
            in_ports = False
        if not in_ports:
            continue

        if stripped.startswith("-"):
            flush_long(i)
            value = stripped[1:].strip().strip('"\'')
            # Long syntax starts with '- target:' or just '-'.
            if not value or value.startswith(("target:", "published:", "protocol:", "mode:", "host_ip:")):
                if value.startswith("published:"):
                    long_entry_published = True
                if value.startswith("host_ip:"):
                    long_entry_host = value.split(":", 1)[1].strip().strip('"\'')
                continue
            # Short syntax: HOST_IP:HOST_PORT:CONTAINER_PORT is required.
            core = value.split("/", 1)[0]
            parts = core.split(":")
            if len(parts) < 3:
                errors.append(f"{path.relative_to(ROOT)}:{i}: published port must bind explicit loopback/private IP: {value}")
            else:
                host = parts[0].strip("[]")
                if not safe_host(host):
                    errors.append(f"{path.relative_to(ROOT)}:{i}: published port binds unsafe host {host}: {value}")
            continue

        if stripped.startswith("published:"):
            long_entry_published = True
        elif stripped.startswith("host_ip:"):
            long_entry_host = stripped.split(":", 1)[1].strip().strip('"\'')

    flush_long(len(lines) + 1)
    return errors


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("paths", nargs="*", default=["platform/compose", "ksp-bootstrap/compose"])
    ns = ap.parse_args()
    files: list[Path] = []
    for raw in ns.paths:
        p = ROOT / raw
        if p.is_file():
            files.append(p)
        elif p.is_dir():
            files.extend(x for x in p.rglob("*") if x.suffix in {".yml", ".yaml"})
    errors: list[str] = []
    for f in sorted(set(files)):
        errors.extend(check(f))
    if errors:
        print("compose-binding-policy: FAIL")
        for e in errors:
            print(f" - {e}")
        return 1
    print(f"compose-binding-policy: PASS ({len(files)} YAML files)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
