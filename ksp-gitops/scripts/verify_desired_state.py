#!/usr/bin/env python3
"""Fail-closed validation for the promotion-signed KSP desired-state document.

This validator checks structure and non-cryptographic policy. Cryptographic authority
is verified separately by verify_signed_desired_state.sh using the pinned promotion
key and build key. Git is never an authority or runtime input.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

SHA256_RE = re.compile(r"^sha256:[a-f0-9]{64}$")
OCI_RE = re.compile(r"^[^@\s]+@sha256:[a-f0-9]{64}$")
SEMVER_RE = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+$")
REQUIRED_TOP = {
    "schema_version",
    "environment",
    "sequence",
    "target_release_sequence",
    "issued_at",
    "expires_at",
    "release_manifest_ref",
    "release_manifest_digest",
    "software",
    "knowledge",
    "compiled_learner_bundle_digest",
    "qualification_bundle_digest",
    "revocation_list_digest",
    "revocation_min_sequence",
    "db_schema",
    "migration",
    "minimum_reconciler_version",
    "expected_open_ports",
}
OPTIONAL_TOP = {"rollback"}
REQUIRED_KNOWLEDGE = {"pharmacology", "medsurg", "bridge"}


def fail(message: str) -> None:
    raise ValueError(message)


def load(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        fail("desired state must be a JSON object")
    return data


def parse_time(value: object, field: str) -> datetime:
    if not isinstance(value, str):
        fail(f"{field} must be RFC3339 text")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        fail(f"{field} must be valid RFC3339: {exc}")
    if parsed.tzinfo is None:
        fail(f"{field} must include timezone")
    return parsed.astimezone(timezone.utc)


def parse_semver(value: object, field: str) -> tuple[int, int, int]:
    if not isinstance(value, str) or not SEMVER_RE.fullmatch(value):
        fail(f"{field} must be MAJOR.MINOR.PATCH")
    return tuple(int(x) for x in value.split("."))


def validate(
    data: dict,
    previous: dict | None = None,
    expected_environment: str | None = None,
    current_reconciler_version: str | None = None,
    current_db_schema: int | None = None,
) -> None:
    missing = REQUIRED_TOP - data.keys()
    if missing:
        fail(f"missing required fields: {sorted(missing)}")
    unknown = set(data) - REQUIRED_TOP - OPTIONAL_TOP
    if unknown:
        fail(f"unknown top-level fields are forbidden: {sorted(unknown)}")

    if data["schema_version"] != "1.0":
        fail("schema_version must be 1.0")
    if data["environment"] not in {"staging", "production"}:
        fail("environment must be staging or production")
    if expected_environment and data["environment"] != expected_environment:
        fail(f"environment binding mismatch: expected {expected_environment}")

    for field in ("sequence", "target_release_sequence", "revocation_min_sequence"):
        if not isinstance(data[field], int) or isinstance(data[field], bool) or data[field] < 1:
            fail(f"{field} must be an integer >= 1")

    issued = parse_time(data["issued_at"], "issued_at")
    expires = parse_time(data["expires_at"], "expires_at")
    if expires <= issued:
        fail("expires_at must be after issued_at")

    if not isinstance(data["release_manifest_ref"], str) or not OCI_RE.fullmatch(data["release_manifest_ref"]):
        fail("release_manifest_ref must be immutable OCI ref pinned by sha256 digest")
    if ":latest" in data["release_manifest_ref"]:
        fail("release_manifest_ref must not use latest")

    for field in (
        "release_manifest_digest",
        "compiled_learner_bundle_digest",
        "qualification_bundle_digest",
        "revocation_list_digest",
    ):
        if not isinstance(data[field], str) or not SHA256_RE.fullmatch(data[field]):
            fail(f"{field} must be sha256:<64 lowercase hex>")

    if not data["release_manifest_ref"].endswith("@" + data["release_manifest_digest"]):
        fail("release_manifest_ref digest must equal release_manifest_digest")

    software = data["software"]
    if not isinstance(software, dict) or not software:
        fail("software must be a non-empty object")
    for name, ref in software.items():
        if not isinstance(name, str) or not name:
            fail("software service names must be non-empty strings")
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

    db_schema = data["db_schema"]
    if not isinstance(db_schema, dict) or set(db_schema) != {"min", "max"}:
        fail("db_schema must contain exactly min and max")
    if any(not isinstance(db_schema[x], int) or isinstance(db_schema[x], bool) or db_schema[x] < 1 for x in ("min", "max")):
        fail("db_schema min/max must be positive integers")
    if db_schema["min"] > db_schema["max"]:
        fail("db_schema min must be <= max")
    if current_db_schema is not None and not (db_schema["min"] <= current_db_schema <= db_schema["max"]):
        fail("current database schema is outside desired-state compatibility range")

    migration = data["migration"]
    if not isinstance(migration, dict) or set(migration) != {"phase", "bundle_digest"}:
        fail("migration must contain exactly phase and bundle_digest")
    if migration["phase"] not in {"none", "expand", "contract"}:
        fail("migration.phase must be none, expand, or contract")
    if migration["phase"] == "none":
        if migration["bundle_digest"] is not None:
            fail("migration.bundle_digest must be null when phase is none")
    else:
        if not isinstance(migration["bundle_digest"], str) or not SHA256_RE.fullmatch(migration["bundle_digest"]):
            fail("migration.bundle_digest must be sha256 digest for expand/contract")
    if migration["phase"] == "contract":
        fail("contract-phase migrations are never executed by the reconciler")

    minimum_version = parse_semver(data["minimum_reconciler_version"], "minimum_reconciler_version")
    if current_reconciler_version is not None:
        current_version = parse_semver(current_reconciler_version, "current_reconciler_version")
        if current_version < minimum_version:
            fail("reconciler version is below minimum_reconciler_version")

    ports = data["expected_open_ports"]
    if not isinstance(ports, list) or any(not isinstance(p, int) or isinstance(p, bool) or not 1 <= p <= 65535 for p in ports):
        fail("expected_open_ports must be an array of TCP/UDP port integers")
    if len(ports) != len(set(ports)):
        fail("expected_open_ports must be unique")

    rollback = data.get("rollback")
    if rollback is not None:
        if not isinstance(rollback, dict) or set(rollback) != {"record_digest"}:
            fail("rollback must be null or contain exactly record_digest")
        if not isinstance(rollback["record_digest"], str) or not SHA256_RE.fullmatch(rollback["record_digest"]):
            fail("rollback.record_digest must be sha256 digest")

    if previous is not None:
        if data["environment"] != previous.get("environment"):
            fail("candidate and previous desired states must target the same environment")
        if data["sequence"] <= previous.get("sequence", 0):
            fail("channel sequence must increase monotonically")
        prior_target = previous.get("target_release_sequence", previous.get("sequence", 0))
        if data["target_release_sequence"] < prior_target and rollback is None:
            fail("lower target_release_sequence requires a signed rollback record")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("candidate", type=Path)
    parser.add_argument("--previous", type=Path)
    parser.add_argument("--expected-environment", choices=("staging", "production"))
    parser.add_argument("--current-reconciler-version")
    parser.add_argument("--current-db-schema", type=int)
    args = parser.parse_args()
    try:
        candidate = load(args.candidate)
        previous = load(args.previous) if args.previous else None
        validate(
            candidate,
            previous,
            expected_environment=args.expected_environment,
            current_reconciler_version=args.current_reconciler_version,
            current_db_schema=args.current_db_schema,
        )
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        print(f"desired-state-validation: FAIL: {exc}", file=sys.stderr)
        return 1
    print(
        "desired-state-validation: PASS "
        f"environment={candidate['environment']} sequence={candidate['sequence']} "
        f"target_release_sequence={candidate['target_release_sequence']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
