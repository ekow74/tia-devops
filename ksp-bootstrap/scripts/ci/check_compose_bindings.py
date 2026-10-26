#!/usr/bin/env python3
"""Fail closed on Docker Compose network/privilege exposure bypasses."""
from __future__ import annotations

import argparse
import ipaddress
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
RFC1918 = tuple(ipaddress.ip_network(net) for net in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16"))
ULA_V6 = ipaddress.ip_network("fc00::/7")
DOCKER_SOCKET_ALLOWLIST = {"deployment-agent"}
TRUE_RE = re.compile(r"^(?:true|yes|on|1)$", re.I)


def safe_host(host: str) -> bool:
    try:
        ip = ipaddress.ip_address(host.strip())
    except ValueError:
        return False
    if ip.is_unspecified or ip.is_multicast:
        return False
    if ip.is_loopback:
        return True
    if isinstance(ip, ipaddress.IPv4Address):
        return any(ip in network for network in RFC1918)
    return ip in ULA_V6


def short_syntax_host(value: str) -> str | None:
    core = value.split("/", 1)[0]
    if core.startswith("["):
        close = core.find("]")
        if close < 0:
            return None
        host = core[1:close]
        remainder = core[close + 1 :]
        if not remainder.startswith(":") or remainder.count(":") < 2:
            return None
        return host
    parts = core.rsplit(":", 2)
    if len(parts) != 3:
        return None
    return parts[0]


def yaml_scalar(stripped: str, key: str) -> str | None:
    prefix = key + ":"
    if not stripped.startswith(prefix):
        return None
    return stripped[len(prefix):].strip().strip('"\'')


def check(path: Path) -> list[str]:
    lines = path.read_text(errors="replace").splitlines()
    errors: list[str] = []
    in_ports = False
    ports_indent = -1
    long_entry_host: str | None = None
    long_entry_published = False

    in_services = False
    services_indent = -1
    service_indent: int | None = None
    current_service: str | None = None

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

        if stripped == "services:":
            in_services = True
            services_indent = indent
            service_indent = None
            current_service = None
            continue
        if in_services:
            if indent <= services_indent:
                in_services = False
                service_indent = None
                current_service = None
            elif service_indent is None and stripped.endswith(":") and not stripped.startswith("-"):
                service_indent = indent
                current_service = stripped[:-1].strip().strip('"\'')
            elif service_indent is not None and indent == service_indent and stripped.endswith(":") and not stripped.startswith("-"):
                current_service = stripped[:-1].strip().strip('"\'')

        network_mode = yaml_scalar(stripped, "network_mode")
        if network_mode == "host":
            errors.append(f"{path.relative_to(ROOT)}:{i}: network_mode: host is forbidden")

        privileged = yaml_scalar(stripped, "privileged")
        if privileged is not None and TRUE_RE.fullmatch(privileged):
            errors.append(f"{path.relative_to(ROOT)}:{i}: privileged: true is forbidden")

        if "docker.sock" in stripped and not stripped.startswith("#"):
            if current_service not in DOCKER_SOCKET_ALLOWLIST:
                errors.append(
                    f"{path.relative_to(ROOT)}:{i}: Docker socket mount is forbidden for service {current_service or '<unknown>'}"
                )

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
            if not value or value.startswith(("target:", "published:", "protocol:", "mode:", "host_ip:")):
                if value.startswith("published:"):
                    long_entry_published = True
                if value.startswith("host_ip:"):
                    long_entry_host = value.split(":", 1)[1].strip().strip('"\'')
                continue
            host = short_syntax_host(value)
            if host is None:
                errors.append(f"{path.relative_to(ROOT)}:{i}: published port must bind explicit loopback/private IP: {value}")
            elif not safe_host(host):
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
