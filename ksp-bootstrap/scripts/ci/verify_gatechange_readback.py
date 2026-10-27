#!/usr/bin/env python3
"""Trusted merge-service check for an external GateChange approval read-back."""
from __future__ import annotations

import argparse
import json
from pathlib import Path


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("gatechange", type=Path)
    ap.add_argument("readback", type=Path, help="Trusted Drive/Decision-Log read-back produced outside the PR")
    ns = ap.parse_args()

    gate = json.loads(ns.gatechange.read_text())
    rb = json.loads(ns.readback.read_text())
    approval = gate.get("approval", {})

    checks = {
        "decision": rb.get("decision") in {"APPROVED", "APPROVED_WITH_MINOR_CHANGES", "ACCEPTED", "RN_DECISION", "OWNER_DECISION"},
        "gate_change_id": rb.get("approved_gate_change_id") == gate.get("gate_change_id") == approval.get("approved_gate_change_id"),
        "approval_id": rb.get("approval_id") == approval.get("approval_id"),
        "review_reference": rb.get("review_reference") == gate.get("review_reference"),
        "review_revision_id": rb.get("review_revision_id") == approval.get("review_revision_id"),
        "approved_scope": rb.get("approved_scope") == approval.get("approved_scope"),
        "approved_threshold": rb.get("approved_threshold") == approval.get("approved_threshold"),
    }
    failed = [name for name, ok in checks.items() if not ok]
    if failed:
        raise SystemExit("external GateChange approval read-back mismatch: " + ", ".join(failed))

    consumed_by = rb.get("approval_consumed_by")
    if consumed_by not in (None, "", gate.get("gate_change_id")):
        raise SystemExit(f"approval already consumed by {consumed_by}")

    print("PASS: external approval is bound to this GateChange/scope and is not replayed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
