from __future__ import annotations

import importlib.util
import os
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = ROOT.parent
CHECKER = ROOT / "scripts/ci/check_gate_integrity.py"
PR_TARGET_CHECKER = ROOT / "scripts/ci/check_pull_request_target_exceptions.py"
STATIC_WORKFLOW = REPO_ROOT / ".github/workflows/ksp-safety-gates.yml"
AUTHORITY_WORKFLOW = REPO_ROOT / ".github/workflows/ksp-gate-authority.yml"
CODEOWNERS = REPO_ROOT / ".github/CODEOWNERS"
D0023 = ROOT / "governance/decisions/D-0023.json"
POLICY = ROOT / "policies/protected-paths.txt"
UMBRELLA_VALIDATOR = REPO_ROOT / "scripts/ci/validate_devops_umbrella.sh"
PR_TARGET_EXCEPTION = REPO_ROOT / "governance/workflow-exceptions/pull-request-target.txt"

spec = importlib.util.spec_from_file_location("check_gate_integrity_gc3", CHECKER)
mod = importlib.util.module_from_spec(spec)
assert spec and spec.loader
spec.loader.exec_module(mod)


def git(*args: str) -> str:
    return subprocess.check_output(
        ["git", *args], cwd=REPO_ROOT, text=True, stderr=subprocess.STDOUT
    ).strip()


def synthetic_approval(record: dict, head: str) -> dict:
    gid = str(record["gate_change_id"])
    return {
        "decision": "APPROVED",
        "approval_id": f"TEST-{gid}",
        "approved_gate_change_id": gid,
        "approved_scope": list(record["scope"]),
        "approved_threshold": record["approval_threshold"],
        "review_reference": record["review_reference"],
        "review_revision_id": "TEST-REVISION",
        "approved_head_sha": head,
        "approval_consumed_by": gid,
    }


class GateAuthorityHardeningTests(unittest.TestCase):
    def test_r1_static_safety_runs_on_every_pull_request(self):
        text = STATIC_WORKFLOW.read_text()
        trigger = text.split("concurrency:", 1)[0]
        self.assertIn("  pull_request:\n", trigger)
        self.assertNotIn("paths:", trigger)

    def test_r2_authority_uses_base_trusted_pull_request_target(self):
        text = AUTHORITY_WORKFLOW.read_text()
        self.assertIn("pull_request_target:", text)
        self.assertIn("name: KSP trusted gate authority", text)
        self.assertIn("permissions:\n  contents: read", text)
        self.assertIn("persist-credentials: false", text)
        self.assertIn('git show "${BASE_SHA}:${CHECKER}" > "${TMP_CHECKER}"', text)
        self.assertIn('python3 -I "${TMP_CHECKER}"', text)
        self.assertIn('refs/pull/${KSP_PR_NUMBER}/head', text)
        self.assertIn('if [[ "${HEAD_SHA}" != "${KSP_EVENT_HEAD_SHA}" ]]', text)
        self.assertNotIn("ref: ${{ github.event.pull_request.head.sha }}", text)
        self.assertNotRegex(text, r"(?m)^\s*git\s+(checkout|switch)\b")

    def test_r2_reviewed_exception_is_exact_rule_scoped_and_protected(self):
        exception_lines = [
            line.strip()
            for line in PR_TARGET_EXCEPTION.read_text().splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        ]
        self.assertEqual(
            exception_lines,
            [
                ".github/workflows/ksp-gate-authority.yml pull_request_target",
                ".github/workflows/ksp-gate-authority.yml zizmor:dangerous-triggers",
                ".github/workflows/phase0-review-gate.yml pull_request_target",
                ".github/workflows/phase0-review-gate.yml zizmor:dangerous-triggers",
            ],
        )
        validator = UMBRELLA_VALIDATOR.read_text()
        self.assertIn("check_pull_request_target_exceptions.py", validator)
        self.assertIn('--repo-root "$ROOT"', validator)
        helper = PR_TARGET_CHECKER.read_text()
        self.assertIn("pull-request-target.txt", helper)
        self.assertIn("exact-path/rule exception", helper)
        self.assertIn("stale workflow exception", helper)
        self.assertIn("zizmor inline ignore lacks reviewed exact path/rule exception", helper)
        policy = POLICY.read_text().splitlines()
        self.assertIn("governance/**", policy)
        self.assertIn("scripts/ci/validate_devops_umbrella.sh", policy)
        self.assertIn("ksp-bootstrap/scripts/ci/**", policy)

    def test_r2_codeowners_and_policy_protect_gate_authority_surfaces(self):
        owners = CODEOWNERS.read_text()
        self.assertIn("/.github/workflows/** @ekow74", owners)
        self.assertIn("/.github/CODEOWNERS @ekow74", owners)
        policy = POLICY.read_text().splitlines()
        self.assertIn(".github/workflows/**", policy)
        self.assertIn(".github/CODEOWNERS", policy)

    def test_r2_d0023_requires_unspoofable_workflow_and_negative_cases(self):
        text = D0023.read_text()
        self.assertIn('"record_type": "OWNER_DECISION"', text)
        self.assertIn("same-named status check is not equivalent", text)
        self.assertIn("never checkout, source, import, or execute PR-head code", text)
        self.assertIn("removes or renames the trusted gate workflow/job", text)
        self.assertIn("adds a same-named passing job", text)
        self.assertIn(
            "Before any ksp-platform protected-path merge, or if tia-devops gains production authority.",
            text,
        )
        self.assertIn("This record authorizes no spend.", text)

    def test_current_pr_protected_diff_is_covered_by_gc_scope(self):
        head = os.environ.get("KSP_CURRENT_PR_HEAD_SHA")
        base = os.environ.get("KSP_CURRENT_PR_BASE_SHA")
        if not head or not base:
            self.skipTest("current PR diff assertion runs in pull_request CI")

        git("cat-file", "-e", f"{base}^{{commit}}")
        git("cat-file", "-e", f"{head}^{{commit}}")
        mod.configure_repo(REPO_ROOT, head)

        records = mod.current_gate_records(base, head)
        changes = mod.changed_files(base, head)
        protected_paths = [
            path
            for _, path in changes
            if mod.protected(path, head) and not path.startswith(mod.GATE_DIR + "/")
        ]
        if not protected_paths:
            self.skipTest("current PR contains no protected changes")

        self.assertTrue(records, "protected PR must carry at least one GateChange")
        receipts = [synthetic_approval(record, head) for record in records]
        mod.validate_gatechange_records(
            records, protected_paths, head_sha=head, approvals=receipts
        )


if __name__ == "__main__":
    unittest.main()
