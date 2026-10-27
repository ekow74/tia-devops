#!/usr/bin/env python3
"""Trusted approval-publisher read-back for an external GateChange review.

This utility is intentionally run outside untrusted PR CI by the trusted
approval-ledger publisher.  It consumes the actual review text read from the
external review store and emits a ledger receipt only when one fixed approval
line exactly matches the GateChange ID, approval ID, head SHA, scope,
threshold, and review reference.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

PREFIX = "GateChange approval: APPROVED |"
REQUIRED_FIELDS = {
    "approval_id",
    "gate_change_id",
    "approved_head_sha",
    "approved_scope",
    "approved_threshold",
    "review_reference",
}


def parse_approval_lines(text: str) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line.startswith(PREFIX):
            continue
        fields: dict[str, str] = {}
        for part in line.split("|")[1:]:
            part = part.strip()
            if "=" not in part:
                continue
            key, value = part.split("=", 1)
            fields[key.strip()] = value.strip()
        if REQUIRED_FIELDS.issubset(fields):
            rows.append(fields)
    return rows


def build_receipt(
    gate: dict[str, Any],
    review_text: str,
    *,
    head: str,
    approval_id: str,
    review_revision_id: str,
) -> dict[str, Any]:
    gid = str(gate.get("gate_change_id", ""))
    scope = gate.get("scope")
    threshold = str(gate.get("approval_threshold", ""))
    review_reference = str(gate.get("review_reference", ""))
    if not gid or not isinstance(scope, list) or not scope or not threshold or not review_reference:
        raise ValueError("GateChange is missing gate_change_id, scope, approval_threshold, or review_reference")
    if not head or not approval_id or not review_revision_id:
        raise ValueError("head, approval_id, and review_revision_id are required")

    candidates = [
        row
        for row in parse_approval_lines(review_text)
        if row.get("gate_change_id") == gid and row.get("approval_id") == approval_id
    ]
    if len(candidates) != 1:
        raise ValueError(
            f"expected exactly one external APPROVED line for {gid}/{approval_id}; found {len(candidates)}"
        )
    row = candidates[0]
    expected_scope = [str(x) for x in scope]
    observed_scope = [x for x in row["approved_scope"].split(";") if x]
    checks = {
        "approved_head_sha": row.get("approved_head_sha") == head,
        "approved_scope": observed_scope == expected_scope,
        "approved_threshold": row.get("approved_threshold") == threshold,
        "review_reference": row.get("review_reference") == review_reference,
    }
    failed = [name for name, ok in checks.items() if not ok]
    if failed:
        raise ValueError("external GateChange approval read-back mismatch: " + ", ".join(failed))

    return {
        "decision": "APPROVED",
        "approval_id": approval_id,
        "approved_gate_change_id": gid,
        "approved_scope": expected_scope,
        "approved_threshold": threshold,
        "review_reference": review_reference,
        "review_revision_id": review_revision_id,
        "approved_head_sha": head,
        "approval_consumed_by": gid,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("gatechange", type=Path)
    ap.add_argument("review_text", type=Path, help="Actual external GateChange review text read outside the PR")
    ap.add_argument("--head", required=True, help="Exact PR head SHA being approved")
    ap.add_argument("--approval-id", required=True)
    ap.add_argument("--review-revision-id", required=True)
    ap.add_argument("--output", type=Path, help="Write the validated ledger receipt JSON here")
    ns = ap.parse_args()

    gate = json.loads(ns.gatechange.read_text())
    try:
        receipt = build_receipt(
            gate,
            ns.review_text.read_text(),
            head=ns.head,
            approval_id=ns.approval_id,
            review_revision_id=ns.review_revision_id,
        )
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc

    encoded = json.dumps(receipt, indent=2, sort_keys=True) + "\n"
    if ns.output:
        ns.output.parent.mkdir(parents=True, exist_ok=True)
        ns.output.write_text(encoded)
    else:
        print(encoded, end="")
    print("PASS: exact external GateChange APPROVED line validated", file=__import__("sys").stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
