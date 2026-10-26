#!/usr/bin/env python3
"""Fail closed when a PR changes KSP safety gates without an approved GateChange record."""
from __future__ import annotations

import argparse
import fnmatch
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
POLICY = ROOT / "ksp-bootstrap/policies/protected-paths.txt"
GATE_DIR = ROOT / "ksp-bootstrap/governance/gate-changes"

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


def protected(path: str) -> bool:
    return any(fnmatch.fnmatch(path, p) for p in patterns())


def changed_files(base: str, head: str) -> list[tuple[str, str]]:
    out = run("git", "diff", "--name-status", f"{base}...{head}")
    rows = []
    for line in out.splitlines():
        parts = line.split("\t")
        if len(parts) >= 2:
            rows.append((parts[0], parts[-1]))
    return rows


def approved_gate_changes(base: str, head: str) -> list[dict]:
    records: list[dict] = []
    for status, path in changed_files(base, head):
        if not path.startswith("ksp-bootstrap/governance/gate-changes/") or not path.endswith(".json"):
            continue
        if status.startswith("D"):
            continue
        try:
            data = json.loads((ROOT / path).read_text())
        except Exception as exc:
            raise SystemExit(f"Invalid GateChange record {path}: {exc}")
        required = ["gate_change_id", "decision_log_id", "reason", "reviewer_kind", "review_status"]
        missing = [k for k in required if not data.get(k)]
        if missing:
            raise SystemExit(f"GateChange {path} missing: {', '.join(missing)}")
        if not re.fullmatch(r"D-\d{4}", data["decision_log_id"]):
            raise SystemExit(f"GateChange {path}: decision_log_id must match D-####")
        if data["review_status"] != "APPROVED":
            raise SystemExit(f"GateChange {path}: review_status must be APPROVED")
        if data["reviewer_kind"] not in {"CLAUDE", "RN", "OWNER"}:
            raise SystemExit(f"GateChange {path}: reviewer_kind must be CLAUDE, RN, or OWNER")
        records.append(data)
    return records


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

    records = approved_gate_changes(ns.base, ns.head)
    protected_paths = [p for _, p in protected_changes if not p.startswith("ksp-bootstrap/governance/gate-changes/")]
    diff = diff_text(ns.base, ns.head, protected_paths)

    reasons: list[str] = []
    if not records:
        reasons.append("protected path changed without an APPROVED GateChange record")
    if any(s.startswith("D") for s, _ in protected_changes):
        reasons.append("protected file deleted")
    additions = [line[1:] for line in diff.splitlines() if line.startswith("+") and not line.startswith("+++")]
    if any(token in line for token in SUSPICIOUS_ADDITIONS for line in additions):
        reasons.append("skip/xfail/continue-on-error/fail-open construct added")
    if threshold_decrease(diff):
        reasons.append("numeric safety threshold appears to decrease")

    # GateChange is the only documented exception for protected changes.
    if reasons and not records:
        print("gate-integrity: FAIL")
        for r in reasons:
            print(f" - {r}")
        return 1

    if records:
        print(f"gate-integrity: PASS with {len(records)} approved GateChange record(s)")
        for r in reasons:
            print(f" - approved exception: {r}")
        return 0

    print("gate-integrity: FAIL (unclassified protected change)")
    return 1


if __name__ == "__main__":
    sys.exit(main())
