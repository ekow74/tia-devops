#!/usr/bin/env python3
"""Fail-closed KSP desired-state validation with no third-party dependencies."""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

SHA256_RE = re.compile(r"^sha256:[a-f0-9]{64}$")
OCI_RE = re.compile(r"^[^@\s]+@sha256:[a-f0-9]{64}$")
REQUIRED_TOP = {
    "environment",
    "sequence",
    "release_manifest_digest",
    "software",
    "knowledge",
    "compiler_digest",
    "qualification_bundle_digest",
    "revocation_list_digest",
}
REQUIRED_KNOWLEDGE = {"pharmacology", "medsurg", "bridge"}


def fail(message: str) -> None:
    raise ValueError(message)


def load(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        fail("desired state must be a JSON object")
    return data


def validate(data: dict, previous: dict | None = None) -> None:
    missing = REQUIRED_TOP - data.keys()
    if missing:
        fail(f"missing required fields: {sorted(missing)}")
    if data["environment"] not in {"staging", "production"}:
        fail("environment must be staging or production")
    if not isinstance(data["sequence"], int) or data["sequence"] < 1:
        fail("sequence must be an integer >= 1")

    for field in (
        "release_manifest_digest",
        "compiler_digest",
        "qualification_bundle_digest",
        "revocation_list_digest",
    ):
        if not isinstance(data[field], str) or not SHA256_RE.fullmatch(data[field]):
            fail(f"{field} must be sha256:<64 lowercase hex>")

    software = data["software"]
    if not isinstance(software, dict) or not software:
        fail("software must be a non-empty object")
    for name, ref in software.items():
        if not isinstance(ref, str) or not OCI_RE.fullmatch(ref):
            fail(f"software.{name} must be an immutable OCI ref pinned by sha256 digest")
        if ":latest" in ref or ref.endswith("latest"):
            fail(f"software.{name} must not use latest")

    knowledge = data["knowledge"]
    if not isinstance(knowledge, dict) or set(knowledge) != REQUIRED_KNOWLEDGE:
        fail("knowledge must contain exactly pharmacology, medsurg, bridge")
    for name, digest in knowledge.items():
        if not isinstance(digest, str) or not SHA256_RE.fullmatch(digest):
            fail(f"knowledge.{name} must be sha256:<64 lowercase hex>")

    rollback = data.get("rollback_parent_sequence")
    if rollback is not None and (not isinstance(rollback, int) or rollback < 1 or rollback >= data["sequence"]):
        fail("rollback_parent_sequence must be null or a positive sequence lower than candidate sequence")

    if previous is not None:
        if data["environment"] != previous.get("environment"):
            fail("candidate and previous desired states must target the same environment")
        if data["sequence"] <= previous.get("sequence", 0):
            fail("candidate sequence must increase monotonically")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("candidate", type=Path)
    parser.add_argument("--previous", type=Path)
    args = parser.parse_args()
    try:
        candidate = load(args.candidate)
        previous = load(args.previous) if args.previous else None
        validate(candidate, previous)
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        print(f"desired-state-validation: FAIL: {exc}", file=sys.stderr)
        return 1
    print(f"desired-state-validation: PASS environment={candidate['environment']} sequence={candidate['sequence']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
