#!/usr/bin/env python3
"""Fail closed when a PR changes KSP safety gates without a bound, single-use GateChange approval."""
from __future__ import annotations

import argparse
import fnmatch
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[3]
POLICY = ROOT / "ksp-bootstrap/policies/protected-paths.txt"
GATE_DIR = "ksp-bootstrap/governance/gate-changes"

SUSPICIOUS_ADDITIONS = (
    "pytest.mark.skip",
    "pytest.mark.xfail",
    "@unittest.skip",
    "continue-on-error: true",
    "continue-on-error:true",
    "|| true",
)
THRESHOLD_RE = re.compile(r"(?i)(threshold|minimum|min_|recall|coverage)[^0-9-]*(-?\d+(?:\.\d+)?)")


def run(*args: str) -> str:
    return subprocess.check_output(args, cwd=ROOT, text=True, stderr=subprocess.STDOUT)


def patterns() -> list[str]:
    return [x.strip() for x in POLICY.read_text().splitlines() if x.strip() and not x.startswith("#")]


def path_matches(path: str, pattern: str) -> bool:
    if pattern.endswith("/"):
        return path.startswith(pattern)
    return fnmatch.fnmatch(path, pattern)


def protected(path: str) -> bool:
    return any(path_matches(path, p) for p in patterns())


def changed_files(base: str, head: str) -> list[tuple[str, str]]:
    out = run("git", "diff", "--name-status", f"{base}...{head}")
    rows: list[tuple[str, str]] = []
    for line in out.splitlines():
        parts = line.split("\t")
        if len(parts) >= 2:
            rows.append((parts[0], parts[-1]))
    return rows


def _load_gate_record_text(text: str, source: str) -> dict[str, Any]:
    try:
        data = json.loads(text)
    except Exception as exc:
        raise ValueError(f"Invalid GateChange record {source}: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError(f"GateChange {source} must be a JSON object")
    return data


def current_gate_records(base: str, head: str) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for status, path in changed_files(base, head):
        if not path.startswith(GATE_DIR + "/") or not path.endswith(".json") or status.startswith("D"):
            continue
        records.append(_load_gate_record_text((ROOT / path).read_text(), path))
    return records


def base_gate_records(base: str) -> list[dict[str, Any]]:
    try:
        listing = run("git", "ls-tree", "-r", "--name-only", base, "--", GATE_DIR)
    except subprocess.CalledProcessError:
        return []
    records: list[dict[str, Any]] = []
    for path in listing.splitlines():
        if not path.endswith(".json"):
            continue
        try:
            text = run("git", "show", f"{base}:{path}")
        except subprocess.CalledProcessError:
            continue
        records.append(_load_gate_record_text(text, f"{base}:{path}"))
    return records


def _approval_id(record: dict[str, Any]) -> str:
    approval = record.get("approval")
    return str(approval.get("approval_id", "")) if isinstance(approval, dict) else ""


def _scope_covers(path: str, scope: list[str]) -> bool:
    return any(path_matches(path, p) for p in scope)


def validate_gatechange_records(
    records: list[dict[str, Any]],
    protected_paths: list[str],
    *,
    base_records: list[dict[str, Any]] | None = None,
) -> None:
    """Validate approval-to-change binding and single-use semantics."""
    if not records:
        raise ValueError("protected path changed without a GateChange record")

    base_records = base_records or []
    consumed: dict[str, str] = {}
    for item in base_records:
        if str(item.get("review_status", "")) != "APPROVED":
            continue
        aid = _approval_id(item)
        gid = str(item.get("gate_change_id", ""))
        if aid and gid:
            consumed[aid] = gid

    seen_current: dict[str, str] = {}
    coverage: list[list[str]] = []
    for record in records:
        required = ["gate_change_id", "decision_log_id", "reason", "reviewer_kind", "review_status", "review_reference", "scope", "approval"]
        missing = [k for k in required if not record.get(k)]
        if missing:
            raise ValueError(f"GateChange missing: {', '.join(missing)}")
        gid = str(record["gate_change_id"])
        if not re.fullmatch(r"GC-\d{4}", gid):
            raise ValueError("gate_change_id must match GC-####")
        if not re.fullmatch(r"D-\d{4}", str(record["decision_log_id"])):
            raise ValueError("decision_log_id must match D-####")
        if record["review_status"] != "APPROVED":
            raise ValueError(f"GateChange {gid}: review_status must be APPROVED")
        if record["reviewer_kind"] not in {"CLAUDE", "RN", "OWNER"}:
            raise ValueError(f"GateChange {gid}: reviewer_kind must be CLAUDE, RN, or OWNER")

        scope = record.get("scope")
        if not isinstance(scope, list) or not scope or not all(isinstance(x, str) and x for x in scope):
            raise ValueError(f"GateChange {gid}: scope must be a non-empty list")

        approval = record.get("approval")
        if not isinstance(approval, dict):
            raise ValueError(f"GateChange {gid}: approval must be an object")
        approval_required = ["approval_id", "approved_gate_change_id", "approved_scope", "approved_threshold", "review_revision_id"]
        missing_approval = [k for k in approval_required if not approval.get(k)]
        if missing_approval:
            raise ValueError(f"GateChange {gid}: approval missing {', '.join(missing_approval)}")
        if str(approval["approved_gate_change_id"]) != gid:
            raise ValueError(f"GateChange {gid}: approval is bound to a different GateChange")
        approved_scope = approval.get("approved_scope")
        if not isinstance(approved_scope, list) or not approved_scope:
            raise ValueError(f"GateChange {gid}: approved_scope must be a non-empty list")
        for path_pattern in scope:
            if not any(path_matches(path_pattern, allowed) or path_matches(allowed, path_pattern) for allowed in approved_scope):
                raise ValueError(f"GateChange {gid}: claimed scope {path_pattern!r} is not externally approved")

        aid = str(approval["approval_id"])
        if aid in consumed and consumed[aid] != gid:
            raise ValueError(f"GateChange {gid}: approval {aid} was already consumed by {consumed[aid]}")
        if aid in seen_current and seen_current[aid] != gid:
            raise ValueError(f"GateChange {gid}: approval {aid} is reused in this PR by {seen_current[aid]}")
        seen_current[aid] = gid
        coverage.append([str(x) for x in approved_scope])

    for path in protected_paths:
        if not any(_scope_covers(path, scope) for scope in coverage):
            raise ValueError(f"Protected change {path} is outside every approved GateChange scope")


def diff_text(base: str, head: str, paths: list[str]) -> str:
    if not paths:
        return ""
    return run("git", "diff", "--unified=0", f"{base}...{head}", "--", *paths)


def threshold_decrease(diff: str) -> bool:
    removed: list[tuple[str, float]] = []
    added: list[tuple[str, float]] = []
    for line in diff.splitlines():
        if line.startswith("---") or line.startswith("+++"):
            continue
        m = THRESHOLD_RE.search(line[1:]) if line[:1] in "+-" else None
        if not m:
            continue
        item = (m.group(1).lower(), float(m.group(2)))
        (added if line.startswith("+") else removed).append(item)
    for rk, rv in removed:
        for ak, av in added:
            if rk == ak and av < rv:
                return True
    return False


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", required=True)
    ap.add_argument("--head", default="HEAD")
    ns = ap.parse_args()

    changes = changed_files(ns.base, ns.head)
    protected_changes = [(s, p) for s, p in changes if protected(p)]
    if not protected_changes:
        print("gate-integrity: PASS (no protected paths changed)")
        return 0

    records = current_gate_records(ns.base, ns.head)
    protected_paths = [p for _, p in protected_changes if not p.startswith(GATE_DIR + "/")]
    policy_scan_paths = [p for p in protected_paths if p != "ksp-bootstrap/scripts/ci/check_gate_integrity.py"]
    diff = diff_text(ns.base, ns.head, policy_scan_paths)

    reasons: list[str] = []
    if any(s.startswith("D") for s, _ in protected_changes):
        reasons.append("protected file deleted")
    additions = [line[1:] for line in diff.splitlines() if line.startswith("+") and not line.startswith("+++")]
    if any(token in line for token in SUSPICIOUS_ADDITIONS for line in additions):
        reasons.append("skip/xfail/continue-on-error/fail-open construct added")
    if threshold_decrease(diff):
        reasons.append("numeric safety threshold appears to decrease")

    try:
        validate_gatechange_records(records, protected_paths, base_records=base_gate_records(ns.base))
    except ValueError as exc:
        print("gate-integrity: FAIL")
        print(f" - {exc}")
        for reason in reasons:
            print(f" - {reason}")
        return 1

    print(f"gate-integrity: PASS with {len(records)} bound, single-use approved GateChange record(s)")
    for reason in reasons:
        print(f" - approved exception: {reason}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
