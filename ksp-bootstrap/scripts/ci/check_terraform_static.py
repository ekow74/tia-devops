#!/usr/bin/env python3
"""Static Terraform policy for untrusted PRs. Never needs cloud/provider credentials."""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
ALLOWLIST = ROOT / "ksp-bootstrap/policies/terraform-provider-allowlist.txt"

BANNED = {
    r'\bdata\s+"external"\s+"': "external data source is forbidden in PR-evaluated Terraform",
    r'\bdata\s+"http"\s+"': "http data source is forbidden in PR-evaluated Terraform",
    r'\bprovisioner\s+"local-exec"': "local-exec provisioner is forbidden",
    r'\bprovisioner\s+"remote-exec"': "remote-exec provisioner is forbidden",
}
SOURCE_RE = re.compile(r'\bsource\s*=\s*"([^"]+)"')
PROVIDER_SOURCE_RE = re.compile(r'\bsource\s*=\s*"([a-zA-Z0-9_-]+/[a-zA-Z0-9_-]+)"')


def allowed_providers() -> set[str]:
    return {x.strip() for x in ALLOWLIST.read_text().splitlines() if x.strip() and not x.startswith("#")}


def check_file(path: Path, allowed: set[str]) -> list[str]:
    text = path.read_text(errors="replace")
    errors: list[str] = []
    for pattern, reason in BANNED.items():
        if re.search(pattern, text):
            errors.append(f"{path.relative_to(ROOT)}: {reason}")
    for src in SOURCE_RE.findall(text):
        if src.startswith(("git::", "github.com/", "https://github.com/")) and "?ref=" not in src:
            errors.append(f"{path.relative_to(ROOT)}: remote module source must pin ?ref=: {src}")
    # Required provider source addresses are checked against a small explicit allowlist.
    for src in PROVIDER_SOURCE_RE.findall(text):
        if src not in allowed and "/modules/" not in src:
            errors.append(f"{path.relative_to(ROOT)}: provider source not allowlisted: {src}")
    return errors


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("paths", nargs="*", default=["platform/terraform", "ksp-bootstrap/terraform"])
    ns = ap.parse_args()
    allowed = allowed_providers()
    files: list[Path] = []
    for raw in ns.paths:
        p = ROOT / raw
        if p.is_file() and p.suffix == ".tf":
            files.append(p)
        elif p.is_dir():
            files.extend(p.rglob("*.tf"))
    errors: list[str] = []
    for f in sorted(set(files)):
        errors.extend(check_file(f, allowed))
    if errors:
        print("terraform-static-policy: FAIL")
        for e in errors:
            print(f" - {e}")
        return 1
    print(f"terraform-static-policy: PASS ({len(files)} .tf files; no credentials used)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
