#!/usr/bin/env python3
"""Fail closed at the protected-path merge boundary.

A protected PR cannot authorize itself. Merge authorization must already exist on the
protected base branch and must bind ONE GateChange ID + ONE review + the exact canonical
protected-diff SHA-256. A live review attestation is also required before an approval
can be consumed; this is supplied out-of-band by the Expert Review Gateway/Drive readback.
"""
from __future__ import annotations

import argparse
import fnmatch
import hashlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[3]
POLICY = ROOT / "ksp-bootstrap/policies/protected-paths.txt"
BASE_REVIEW_INDEX = "ksp-bootstrap/governance/review-index.json"
BASE_GATE_PREFIX = "ksp-bootstrap/governance/gate-changes/"

SUSPICIOUS_ADDITIONS = (
    "pytest.mark.skip",
    "pytest.mark.xfail",
    "@unittest.skip",
    "continue-on-error: true",
    "continue-on-error:true",
    "|| true",
)
THRESHOLD_RE = re.compile(r"(?i)(threshold|minimum|min_|recall|coverage)[^0-9-]*(-?\d+(?:\.\d+)?)")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
AUTHORIZING_DECISIONS = {"APPROVED", "APPROVED_WITH_MINOR_CHANGES", "ACCEPTED", "RN_DECISION", "OWNER_DECISION"}


def run(*args: str) -> str:
    return subprocess.check_output(args, cwd=ROOT, text=True, stderr=subprocess.STDOUT)


def patterns() -> list[str]:
    return [x.strip() for x in POLICY.read_text().splitlines() if x.strip() and not x.startswith("#")]


def protected(path: str) -> bool:
    return any(fnmatch.fnmatch(path, p) for p in patterns())


def changed_files(base: str, head: str) -> list[tuple[str, str]]:
    out = run("git", "diff", "--name-status", f"{base}...{head}")
    rows: list[tuple[str, str]] = []
    for line in out.splitlines():
        parts = line.split("\t")
        if len(parts) >= 2:
            rows.append((parts[0], parts[-1]))
    return rows


def git_show_json(ref: str, path: str) -> Any:
    raw = run("git", "show", f"{ref}:{path}")
    return json.loads(raw)


def base_paths(ref: str, prefix: str) -> list[str]:
    out = run("git", "ls-tree", "-r", "--name-only", ref)
    return [p for p in out.splitlines() if p.startswith(prefix)]


def canonical_protected_diff(base: str, head: str, paths: list[str]) -> tuple[str, str]:
    if not paths:
        return "", hashlib.sha256(b"").hexdigest()
    text = run(
        "git", "diff", "--binary", "--full-index", "--no-color", "--no-ext-diff",
        f"{base}...{head}", "--", *sorted(paths)
    )
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    return normalized, hashlib.sha256(normalized.encode("utf-8")).hexdigest()


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
    return any(rk == ak and av < rv for rk, rv in removed for ak, av in added)


def load_base_authorizations(base: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    try:
        review_index = git_show_json(base, BASE_REVIEW_INDEX)
    except Exception:
        review_index = {}
    if not isinstance(review_index, dict):
        raise ValueError("base-branch review index must be an object")

    records: list[dict[str, Any]] = []
    for path in base_paths(base, BASE_GATE_PREFIX):
        if not path.endswith(".json"):
            continue
        try:
            data = git_show_json(base, path)
        except Exception as exc:
            raise ValueError(f"invalid base GateChange {path}: {exc}") from exc
        if isinstance(data, dict):
            data = dict(data)
            data["_path"] = path
            records.append(data)
    return review_index, records


def bound_authorization(
    review_index: dict[str, Any],
    records: list[dict[str, Any]],
    pr_hash: str,
) -> tuple[dict[str, Any], dict[str, Any]] | None:
    matches: list[tuple[dict[str, Any], dict[str, Any]]] = []
    review_use: dict[str, list[str]] = {}

    for rec in records:
        review_id = str(rec.get("review_id", ""))
        gate_id = str(rec.get("gate_change_id", ""))
        if review_id and gate_id and rec.get("review_status") == "APPROVED":
            review_use.setdefault(review_id, []).append(gate_id)

        if rec.get("review_status") != "APPROVED":
            continue
        if rec.get("merge_identity") != "ksp-gate-merge-bot":
            continue
        if rec.get("pr_content_sha256") != pr_hash:
            continue
        if not re.fullmatch(r"GC-[0-9]{4,}", gate_id):
            continue
        entry = review_index.get(review_id)
        if not isinstance(entry, dict):
            continue
        if entry.get("decision") not in AUTHORIZING_DECISIONS:
            continue
        # ME1: the independent approval itself must name this exact GateChange and hash.
        if entry.get("gate_change_id") != gate_id:
            continue
        if entry.get("pr_content_sha256") != pr_hash:
            continue
        matches.append((rec, entry))

    # A1-T05f: one approval can authorize at most one GateChange.
    for review_id, gates in review_use.items():
        if len(set(gates)) > 1:
            raise ValueError(f"review approval reused by multiple GateChanges: {review_id}")

    if len(matches) > 1:
        raise ValueError("multiple GateChange approvals bind the same protected diff")
    return matches[0] if matches else None


def verify_live_attestation(path: Path, record: dict[str, Any], entry: dict[str, Any], pr_hash: str) -> None:
    data = json.loads(path.read_text(encoding="utf-8"))
    expected = {
        "gate_change_id": record["gate_change_id"],
        "review_id": record["review_id"],
        "pr_content_sha256": pr_hash,
        "drive_file_id": entry.get("drive_file_id"),
        "revision_id": entry.get("revision_id"),
        "decision": entry.get("decision"),
    }
    for key, value in expected.items():
        if data.get(key) != value:
            raise ValueError(f"live review attestation mismatch for {key}")
    if data.get("authority") not in {"CLAUDE_DRIVE", "DECISION_LOG"}:
        raise ValueError("live review attestation authority is not trusted")
    if data.get("verified") is not True:
        raise ValueError("live review attestation is not verified")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", required=True)
    ap.add_argument("--head", default="HEAD")
    ap.add_argument(
        "--live-review-attestation",
        type=Path,
        default=Path(os.environ["KSP_LIVE_REVIEW_ATTESTATION_FILE"]) if os.environ.get("KSP_LIVE_REVIEW_ATTESTATION_FILE") else None,
    )
    ns = ap.parse_args()

    changes = changed_files(ns.base, ns.head)
    protected_changes = [(s, p) for s, p in changes if protected(p)]
    if not protected_changes:
        print("gate-integrity: PASS (no protected paths changed)")
        return 0

    changed_paths = {p for _, p in protected_changes}
    approval_paths = {p for p in changed_paths if p == BASE_REVIEW_INDEX or p.startswith(BASE_GATE_PREFIX)}
    substantive_paths = sorted(changed_paths - approval_paths)

    # Same-PR review-index/GateChange approval cannot authorize substantive protected changes.
    if approval_paths and substantive_paths:
        print("gate-integrity: FAIL")
        print(" - protected PR may not modify review-index/GateChange approval records alongside substantive protected changes")
        return 1

    diff, pr_hash = canonical_protected_diff(ns.base, ns.head, substantive_paths or sorted(changed_paths))
    reasons: list[str] = []
    if any(s.startswith("D") for s, _ in protected_changes):
        reasons.append("protected file deleted")
    additions = [line[1:] for line in diff.splitlines() if line.startswith("+") and not line.startswith("+++")]
    if any(token in line for token in SUSPICIOUS_ADDITIONS for line in additions):
        reasons.append("skip/xfail/continue-on-error/fail-open construct added")
    if threshold_decrease(diff):
        reasons.append("numeric safety threshold appears to decrease")

    try:
        review_index, records = load_base_authorizations(ns.base)
        auth = bound_authorization(review_index, records, pr_hash)
    except (ValueError, json.JSONDecodeError, subprocess.CalledProcessError) as exc:
        print("gate-integrity: FAIL")
        print(f" - authorization validation error: {exc}")
        return 1

    if auth is None:
        print("gate-integrity: FAIL")
        print(" - protected diff has no base-branch GateChange approval bound to its exact SHA-256")
        print(f" - required_pr_content_sha256={pr_hash}")
        for r in reasons:
            print(f" - {r}")
        return 1

    record, entry = auth
    if ns.live_review_attestation is None:
        print("gate-integrity: FAIL")
        print(" - bound approval exists but live authoritative review attestation is required")
        print(f" - gate_change_id={record['gate_change_id']} review_id={record['review_id']}")
        return 1
    try:
        verify_live_attestation(ns.live_review_attestation, record, entry, pr_hash)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print("gate-integrity: FAIL")
        print(f" - live review verification failed: {exc}")
        return 1

    print(
        "gate-integrity: PASS "
        f"gate_change_id={record['gate_change_id']} review_id={record['review_id']} pr_content_sha256={pr_hash}"
    )
    for r in reasons:
        print(f" - independently approved exception: {r}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
