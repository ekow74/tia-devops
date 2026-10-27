#!/usr/bin/env python3
"""Fail closed when protected KSP changes lack a bound, base-branch external approval."""
from __future__ import annotations

import argparse
import fnmatch
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

POLICY_PATH = "ksp-bootstrap/policies/protected-paths.txt"
AUTHORITY_POLICY_PATH = "ksp-bootstrap/policies/gate-authority.json"
GATE_DIR = "ksp-bootstrap/governance/gate-changes"
APPROVAL_DIR = "ksp-bootstrap/governance/approvals"

SUSPICIOUS_ADDITIONS = (
    "pytest.mark.skip",
    "pytest.mark.xfail",
    "@unittest.skip",
    "continue-on-error: true",
    "continue-on-error:true",
    "|| true",
)
THRESHOLD_RE = re.compile(r"(?i)(threshold|minimum|min_|recall|coverage)[^0-9-]*(-?\d+(?:\.\d+)?)")
PERMISSION_WRITE_RE = re.compile(
    r"""(?im)(?:["']?(actions|attestations|checks|contents|deployments|discussions|id-token|issues|packages|pages|pull-requests|repository-projects|security-events|statuses)["']?\s*:\s*["']?write\b|write-all\b)"""
)
PERMISSIONS_EXPRESSION_RE = re.compile(
    r"""(?im)^\s*["']?permissions["']?\s*:\s*(?:.*\$\{\{|[&*|>])"""
)
PERMISSION_INDIRECT_RE = re.compile(
    r"""(?im)["']?(actions|attestations|checks|contents|deployments|discussions|id-token|issues|packages|pages|pull-requests|repository-projects|security-events|statuses)["']?\s*:\s*(?:[&*!|>])"""
)
PERMISSION_VALUE_RE = re.compile(
    r"""(?im)^\s*["']?(actions|attestations|checks|contents|deployments|discussions|id-token|issues|packages|pages|pull-requests|repository-projects|security-events|statuses)["']?\s*:\s*(.*?)\s*$"""
)
YAML_ESCAPE_RE = re.compile(
    r"\\(?:u[0-9a-f]{4}|U[0-9a-f]{8}|x[0-9a-f]{2})", re.I
)
YAML_ANCHOR_ALIAS_RE = re.compile(r"(?m)(?:^|[\s{,])(?:&|\*)")
YAML_EXPLICIT_KEY_RE = re.compile(r"(?m)^\s*[?:]\s+")
YAML_TAG_RE = re.compile(r"(?m)(?:^|[\s{,])!(?:!|<|[A-Za-z0-9])")
YAML_FLOW_PERMISSIONS_RE = re.compile(
    r"""(?m)^\s*["']?permissions["']?\s*:\s*[\[{]"""
)
YAML_LINE_CONTINUATION_RE = re.compile(r"\\\r?\n")
YAML_QUOTED_CONTINUATION_RE = re.compile(r"(?m)^\s*['\"][^\n]*\\$")
YAML_BLOCK_SCALAR_RE = re.compile(r"^\s*[^#\s].*:\s*[|>][1-9]?[+-]?\s*(?:#.*)?$")
YAML_ALIAS_KEY_RE = re.compile(r"(?m)^\s*\*[^\s:]+:")
YAML_TAGGED_KEY_RE = re.compile(r"(?m)^\s*![^\s]+\s+[^:\n]+:")
PULL_REQUEST_RE = re.compile(r"(?<![A-Za-z0-9_])pull_request(?![A-Za-z0-9_])")
PULL_REQUEST_TARGET_RE = re.compile(
    r"(?<![A-Za-z0-9_])pull_request_target(?![A-Za-z0-9_])"
)

ROOT = Path.cwd()
_POLICY_REF: str | None = None


def configure_repo(root: str | Path | None = None, policy_ref: str | None = None) -> None:
    global ROOT, _POLICY_REF
    if root is None:
        root_text = subprocess.check_output(
            ["git", "rev-parse", "--show-toplevel"], text=True, stderr=subprocess.STDOUT
        ).strip()
        ROOT = Path(root_text)
    else:
        ROOT = Path(root).resolve()
    _POLICY_REF = policy_ref


def run(*args: str) -> str:
    return subprocess.check_output(args, cwd=ROOT, text=True, stderr=subprocess.STDOUT)


def git_show(ref: str, path: str) -> str:
    return run("git", "show", f"{ref}:{path}")


def patterns(policy_ref: str | None = None) -> list[str]:
    ref = policy_ref if policy_ref is not None else _POLICY_REF
    text = git_show(ref, POLICY_PATH) if ref else (ROOT / POLICY_PATH).read_text()
    return [x.strip() for x in text.splitlines() if x.strip() and not x.startswith("#")]


def path_matches(path: str, pattern: str) -> bool:
    if pattern.endswith("/"):
        return path.startswith(pattern)
    return fnmatch.fnmatch(path, pattern)


def protected(path: str, policy_ref: str | None = None) -> bool:
    return any(path_matches(path, p) for p in patterns(policy_ref))


def changed_files(base: str, head: str) -> list[tuple[str, str]]:
    out = run("git", "diff", "--no-renames", "--name-status", f"{base}...{head}")
    rows: list[tuple[str, str]] = []
    for line in out.splitlines():
        parts = line.split("\t")
        if len(parts) >= 2:
            rows.append((parts[0], parts[-1]))
    return rows


def _active_workflow_text(text: str) -> str:
    return "\n".join(line.split("#", 1)[0] for line in text.splitlines())


def _permission_blocks(text: str) -> list[str]:
    lines = text.splitlines()
    blocks: list[str] = []
    for index, line in enumerate(lines):
        match = re.match(r"""^(\s*)["']?permissions["']?\s*:(.*)$""", line)
        if not match:
            continue
        indent = len(match.group(1))
        block = [line]
        for following in lines[index + 1 :]:
            if following.strip() and len(following) - len(following.lstrip()) <= indent:
                break
            block.append(following)
        blocks.append("\n".join(block))
    return blocks


def _has_quoted_yaml_continuation(text: str) -> bool:
    block_indent: int | None = None
    for line in text.splitlines():
        indent = len(line) - len(line.lstrip())
        if block_indent is not None:
            if not line.strip() or indent > block_indent:
                continue
            block_indent = None
        if YAML_BLOCK_SCALAR_RE.match(line):
            block_indent = indent
        elif YAML_QUOTED_CONTINUATION_RE.match(line):
            return True
    return False


def validate_workflow_permissions(
    path: str, text: str, policy: dict[str, Any]
) -> None:
    active = _active_workflow_text(text)
    if YAML_ESCAPE_RE.search(active):
        raise ValueError(f"{path}: escaped YAML syntax is not reviewable")
    if _has_quoted_yaml_continuation(active):
        raise ValueError(f"{path}: escaped YAML key continuation is not reviewable")
    if YAML_EXPLICIT_KEY_RE.search(active):
        raise ValueError(f"{path}: explicit YAML mapping keys are not reviewable")
    if YAML_ALIAS_KEY_RE.search(active) or YAML_TAGGED_KEY_RE.search(active):
        raise ValueError(f"{path}: aliased or tagged YAML mapping keys are not reviewable")
    blocks = _permission_blocks(active)
    if any(YAML_LINE_CONTINUATION_RE.search(block) for block in blocks):
        raise ValueError(f"{path}: escaped YAML permission continuation is not reviewable")
    if any(YAML_ANCHOR_ALIAS_RE.search(block) for block in blocks):
        raise ValueError(f"{path}: YAML permission anchors and aliases are not reviewable")
    if any(YAML_TAG_RE.search(block) for block in blocks):
        raise ValueError(f"{path}: YAML permission tags are not reviewable")
    if any(YAML_EXPLICIT_KEY_RE.search(block) for block in blocks):
        raise ValueError(f"{path}: explicit YAML permission keys are not reviewable")
    if any(YAML_FLOW_PERMISSIONS_RE.search(block) for block in blocks):
        raise ValueError(f"{path}: flow-style permissions are not reviewable")
    for block in blocks:
        if PERMISSIONS_EXPRESSION_RE.search(block):
            raise ValueError(f"{path}: noncanonical workflow permissions are not reviewable")
        if PERMISSION_INDIRECT_RE.search(block):
            raise ValueError(f"{path}: indirect checks/statuses permissions are not reviewable")
        for match in PERMISSION_VALUE_RE.finditer(block):
            value = match.group(2).strip()
            if value not in {"read", "write", "none", '"read"', '"write"', '"none"', "'read'", "'write'", "'none'"}:
                raise ValueError(f"{path}: noncanonical workflow permission value")
    grants = {
        match.group(1).lower()
        for block in blocks
        for match in PERMISSION_WRITE_RE.finditer(block)
        if match.group(1)
    }
    if any("write-all" in block for block in blocks):
        grants.add("write-all")
    if not grants:
        return

    if PULL_REQUEST_RE.search(active):
        raise ValueError(f"{path}: write permissions are forbidden on pull_request jobs")
    if grants & {"contents", "pull-requests", "write-all"}:
        raise ValueError(f"{path}: contents/pull-requests/write-all permissions are prohibited")
    if PULL_REQUEST_TARGET_RE.search(active) and grants - {"checks", "statuses"}:
        raise ValueError(f"{path}: pull_request_target write permissions are not reviewed")
    if not grants & {"checks", "statuses"}:
        return
    if grants - {"checks", "statuses"}:
        raise ValueError(f"{path}: only checks/statuses writes may be considered for review")

    authorized_paths = policy.get("reviewed_authority_paths")
    if not isinstance(authorized_paths, list) or not all(
        isinstance(item, str) for item in authorized_paths
    ):
        raise ValueError("base Gate Authority policy has an invalid authority path list")
    if path not in authorized_paths:
        raise ValueError(f"{path}: checks/statuses write is outside reviewed authority paths")
    if not PULL_REQUEST_TARGET_RE.search(active):
        raise ValueError(f"{path}: write permission lacks a trusted pull_request_target trigger")

    blocked = policy.get("blocked_permissions")
    if blocked != ["checks", "statuses"]:
        raise ValueError("base Gate Authority policy does not block checks and statuses writes")


def validate_head_workflow_permissions(base: str, head: str) -> None:
    """Apply the base-trusted authority allowlist to all workflow files at PR head."""
    listing = run("git", "ls-tree", "-r", "--name-only", head, "--", ".github/workflows")
    if not listing.splitlines():
        return
    policy = _load_json(git_show(base, AUTHORITY_POLICY_PATH), AUTHORITY_POLICY_PATH)
    if set(policy) != {
        "policy_version",
        "reviewed_authority_paths",
        "blocked_permissions",
        "always_read_only_permissions",
    }:
        raise ValueError("base Gate Authority policy does not match the strict schema")
    if policy.get("policy_version") != 1:
        raise ValueError("base Gate Authority policy version is unsupported")
    if policy.get("blocked_permissions") != ["checks", "statuses"]:
        raise ValueError("base Gate Authority policy must block checks and statuses writes")
    if policy.get("always_read_only_permissions") != ["contents", "pull-requests"]:
        raise ValueError("base Gate Authority policy must keep contents and pull-requests read-only")
    paths = policy.get("reviewed_authority_paths")
    if not isinstance(paths, list) or not paths or not all(
        isinstance(path, str) and path.startswith(".github/workflows/")
        for path in paths
    ):
        raise ValueError("base Gate Authority policy has an invalid authority path list")
    if len(paths) != len(set(paths)):
        raise ValueError("base Gate Authority policy has an invalid authority path list")
    for path in listing.splitlines():
        if not path.endswith((".yml", ".yaml")):
            continue
        try:
            text = git_show(head, path)
        except subprocess.CalledProcessError as exc:
            raise ValueError(f"cannot read workflow from {head}:{path}") from exc
        validate_workflow_permissions(path, text, policy)


def _load_json(text: str, source: str) -> dict[str, Any]:
    try:
        data = json.loads(text)
    except Exception as exc:
        raise ValueError(f"Invalid JSON {source}: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError(f"{source} must be a JSON object")
    return data


def current_gate_records(base: str, head: str) -> list[dict[str, Any]]:
    """Read changed GateChange records from the named head object, never the worktree."""
    records: list[dict[str, Any]] = []
    for status, path in changed_files(base, head):
        if not path.startswith(GATE_DIR + "/") or not path.endswith(".json") or status.startswith("D"):
            continue
        try:
            text = git_show(head, path)
        except subprocess.CalledProcessError as exc:
            raise ValueError(f"cannot read GateChange record from {head}:{path}") from exc
        records.append(_load_json(text, f"{head}:{path}"))
    return records


def base_approval_records(base: str) -> list[dict[str, Any]]:
    """Read external approval receipts only from the PR base commit, never PR head."""
    try:
        listing = run("git", "ls-tree", "-r", "--name-only", base, "--", APPROVAL_DIR)
    except subprocess.CalledProcessError:
        return []
    records: list[dict[str, Any]] = []
    for path in listing.splitlines():
        if not path.endswith(".json"):
            continue
        try:
            text = git_show(base, path)
        except subprocess.CalledProcessError:
            continue
        records.append(_load_json(text, f"{base}:{path}"))
    return records


def bootstrap_base_is_receipts_only(anchor: str, base: str) -> bool:
    """Allow PR #2 bootstrap bases only when they descend from anchor via approval receipts only."""
    try:
        subprocess.check_output(
            ["git", "merge-base", "--is-ancestor", anchor, base],
            cwd=ROOT,
            text=True,
            stderr=subprocess.STDOUT,
        )
    except subprocess.CalledProcessError:
        return False

    out = run("git", "diff", "--name-status", f"{anchor}..{base}")
    for line in out.splitlines():
        parts = line.split("\t")
        if len(parts) < 2:
            return False
        status = parts[0]
        paths = parts[1:]
        if status.startswith(("D", "R", "C")):
            return False
        if any(not p.startswith(APPROVAL_DIR + "/") for p in paths):
            return False
    return True


def _scope_covers(path: str, scope: list[str]) -> bool:
    return any(path_matches(path, p) for p in scope)


def validate_gatechange_records(
    records: list[dict[str, Any]],
    protected_paths: list[str],
    *,
    head_sha: str,
    approvals: list[dict[str, Any]],
) -> None:
    """Validate scope-bound, head-bound, single-use approvals from trusted base state."""
    if not records:
        raise ValueError("protected path changed without a GateChange record")
    if not re.fullmatch(r"[0-9a-f]{40}", head_sha):
        raise ValueError("resolved PR head must be a lowercase 40-hex commit SHA")

    approvals_by_gate: dict[str, dict[str, Any]] = {}
    approval_ids: dict[str, str] = {}
    for approval in approvals:
        gid = str(approval.get("approved_gate_change_id", ""))
        aid = str(approval.get("approval_id", ""))
        if not gid or not aid:
            raise ValueError("base approval ledger entry missing approved_gate_change_id or approval_id")
        if gid in approvals_by_gate:
            raise ValueError(f"multiple base approvals found for {gid}")
        if aid in approval_ids and approval_ids[aid] != gid:
            raise ValueError(f"approval {aid} is reused by {gid} and {approval_ids[aid]}")
        approvals_by_gate[gid] = approval
        approval_ids[aid] = gid

    coverage: list[list[str]] = []
    for record in records:
        required = [
            "gate_change_id",
            "decision_log_id",
            "reason",
            "reviewer_kind",
            "review_reference",
            "scope",
            "approval_threshold",
        ]
        missing = [k for k in required if not record.get(k)]
        if missing:
            raise ValueError(f"GateChange missing: {', '.join(missing)}")
        gid = str(record["gate_change_id"])
        if not re.fullmatch(r"GC-\d{4}", gid):
            raise ValueError("gate_change_id must match GC-####")
        if not re.fullmatch(r"D-\d{4}", str(record["decision_log_id"])):
            raise ValueError("decision_log_id must match D-####")
        if record["reviewer_kind"] not in {"CLAUDE", "RN", "OWNER"}:
            raise ValueError(f"GateChange {gid}: reviewer_kind must be CLAUDE, RN, or OWNER")
        scope = record.get("scope")
        if not isinstance(scope, list) or not scope or not all(isinstance(x, str) and x for x in scope):
            raise ValueError(f"GateChange {gid}: scope must be a non-empty list")

        approval = approvals_by_gate.get(gid)
        if approval is None:
            raise ValueError(f"GateChange {gid}: trusted base-branch approval ledger entry is missing")
        if approval.get("decision") != "APPROVED":
            raise ValueError(f"GateChange {gid}: base approval decision must be APPROVED")
        required_approval = [
            "approval_id",
            "approved_gate_change_id",
            "approved_scope",
            "approved_threshold",
            "review_reference",
            "review_revision_id",
            "approved_head_sha",
        ]
        missing_approval = [k for k in required_approval if not approval.get(k)]
        if missing_approval:
            raise ValueError(f"GateChange {gid}: base approval missing {', '.join(missing_approval)}")
        if approval["approved_gate_change_id"] != gid:
            raise ValueError(f"GateChange {gid}: approval is bound to a different GateChange")
        approved_head_sha = str(approval["approved_head_sha"])
        if not re.fullmatch(r"[0-9a-f]{40}", approved_head_sha):
            raise ValueError(f"GateChange {gid}: approved_head_sha must be a lowercase 40-hex commit SHA")
        if approved_head_sha != head_sha:
            raise ValueError(f"GateChange {gid}: approval is bound to head {approved_head_sha}, not {head_sha}")
        if approval["review_reference"] != record["review_reference"]:
            raise ValueError(f"GateChange {gid}: approval review reference mismatch")
        if approval["approved_threshold"] != record["approval_threshold"]:
            raise ValueError(f"GateChange {gid}: approval threshold mismatch")
        approved_scope = approval.get("approved_scope")
        if not isinstance(approved_scope, list) or not approved_scope:
            raise ValueError(f"GateChange {gid}: approved_scope must be a non-empty list")
        if approved_scope != scope:
            raise ValueError(f"GateChange {gid}: approved_scope must exactly equal the GateChange scope")
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
    ap.add_argument("--repo-root")
    ap.add_argument("--policy-ref")
    ap.add_argument("--bootstrap-anchor")
    ns = ap.parse_args()

    try:
        configure_repo(ns.repo_root, ns.policy_ref)
        resolved_head_sha = run("git", "rev-parse", f"{ns.head}^{{commit}}").strip()
    except (OSError, subprocess.CalledProcessError) as exc:
        print("gate-integrity: FAIL")
        print(f" - cannot establish repository root/head: {exc}")
        return 1

    if ns.bootstrap_anchor and not bootstrap_base_is_receipts_only(ns.bootstrap_anchor, ns.base):
        print("gate-integrity: FAIL")
        print(" - bootstrap base is not the pinned anchor plus approval-ledger receipts only")
        return 1

    try:
        changes = changed_files(ns.base, ns.head)
        protected_changes = [(s, p) for s, p in changes if protected(p)]
    except (OSError, subprocess.CalledProcessError, ValueError) as exc:
        print("gate-integrity: FAIL")
        print(f" - cannot evaluate protected paths: {exc}")
        return 1

    try:
        validate_head_workflow_permissions(ns.base, ns.head)
    except (OSError, subprocess.CalledProcessError, ValueError) as exc:
        print("gate-integrity: FAIL")
        print(f" - cannot verify workflow authority permissions: {exc}")
        return 1

    if not protected_changes:
        print("gate-integrity: PASS (no protected paths changed)")
        return 0

    try:
        records = current_gate_records(ns.base, ns.head)
    except ValueError as exc:
        print("gate-integrity: FAIL")
        print(f" - {exc}")
        return 1

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
        validate_gatechange_records(
            records,
            protected_paths,
            head_sha=resolved_head_sha,
            approvals=base_approval_records(ns.base),
        )
    except ValueError as exc:
        print("gate-integrity: FAIL")
        print(f" - {exc}")
        for reason in reasons:
            print(f" - {reason}")
        return 1

    print(f"gate-integrity: PASS with {len(records)} externally bound base-branch GateChange approval(s)")
    for reason in reasons:
        print(f" - approved exception: {reason}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
