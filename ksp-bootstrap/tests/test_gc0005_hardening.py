from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = ROOT.parent
GATE_CHECKER = ROOT / "scripts/ci/check_gate_integrity.py"
TRIGGER_CHECKER = ROOT / "scripts/ci/check_pull_request_target_exceptions.py"
CI_WORKFLOW = REPO_ROOT / ".github/workflows/ci.yml"
ZIZMOR_FIXTURE = ROOT / "tests/fixtures/zizmor/unsafe_pull_request_target.yml"


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


trigger_mod = load_module("gc5_pr_target_checker", TRIGGER_CHECKER)


def git(cwd: Path, *args: str) -> str:
    return subprocess.check_output(["git", *args], cwd=cwd, text=True, stderr=subprocess.STDOUT).strip()


class GC0005HardeningTests(unittest.TestCase):
    def test_n1_detects_all_four_claude_missed_forms(self):
        samples = [
            "on: [push,\n  pull_request_target]\n",
            '"on": [push, pull_request_target]\n',
            "env:\n  on: harmless\non:\n  pull_request_target:\n",
            "triggers: &t [push, pull_request_target]\non: *t\n",
        ]
        for sample in samples:
            with self.subTest(sample=sample):
                self.assertTrue(trigger_mod.uses_pull_request_target(sample))

    def test_n1_unlisted_missed_forms_and_copied_zizmor_ignore_fail(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            workflows = root / ".github/workflows"
            workflows.mkdir(parents=True)
            allowlist = root / "governance/workflow-exceptions/pull-request-target.txt"
            allowlist.parent.mkdir(parents=True)
            allowlist.write_text("# none approved\n")
            (workflows / "missed.yml").write_text(
                "name: missed\non: [push,\n  pull_request_target] # zizmor: ignore[dangerous-triggers]\n"
            )
            errors = trigger_mod.validate(root, workflows, allowlist)
            self.assertTrue(any("pull_request_target workflow lacks" in error for error in errors))
            self.assertTrue(any("zizmor inline ignore lacks" in error for error in errors))

    def test_n1_current_authority_exception_is_still_exact_path_only(self):
        errors = trigger_mod.validate(
            REPO_ROOT,
            REPO_ROOT / ".github/workflows",
            REPO_ROOT / "governance/workflow-exceptions/pull-request-target.txt",
        )
        self.assertEqual(errors, [])

    def test_m1_checker_fails_rename_then_passes_with_bound_gatechange(self):
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td)
            git(repo, "init", "-q")
            git(repo, "config", "user.email", "test@example.invalid")
            git(repo, "config", "user.name", "KSP Test")

            (repo / "tests").mkdir()
            (repo / "tests/test_safety.py").write_text("protected\n")
            policy = repo / "ksp-bootstrap/policies/protected-paths.txt"
            policy.parent.mkdir(parents=True)
            policy.write_text("tests/\n")

            approval = repo / "ksp-bootstrap/governance/approvals/GC-9999.json"
            approval.parent.mkdir(parents=True)
            scope = ["tests/test_safety.py"]
            threshold = "GC-0005 end-to-end rename proof"
            review = "TEST-REVIEW"
            approval.write_text(json.dumps({
                "decision": "APPROVED",
                "approval_id": "TEST-GC-9999",
                "approved_gate_change_id": "GC-9999",
                "approved_scope": scope,
                "approved_threshold": threshold,
                "review_reference": review,
                "review_revision_id": "TEST-REVISION",
                "approved_head_sha": "HEAD",
                "approval_consumed_by": "GC-9999",
            }))
            git(repo, "add", ".")
            git(repo, "commit", "-q", "-m", "base")
            base = git(repo, "rev-parse", "HEAD")

            (repo / "docs").mkdir()
            git(repo, "mv", "tests/test_safety.py", "docs/old_notes.py")
            git(repo, "commit", "-q", "-m", "rename")

            command = [
                sys.executable, "-I", str(GATE_CHECKER),
                "--repo-root", str(repo), "--base", base, "--head", "HEAD",
            ]
            failed = subprocess.run(command, text=True, capture_output=True)
            self.assertEqual(failed.returncode, 1, failed.stdout + failed.stderr)
            self.assertIn("protected path changed without a GateChange record", failed.stdout)

            gate = repo / "ksp-bootstrap/governance/gate-changes/GC-9999.json"
            gate.parent.mkdir(parents=True, exist_ok=True)
            gate.write_text(json.dumps({
                "gate_change_id": "GC-9999",
                "decision_log_id": "D-9999",
                "reason": "test protected rename approval",
                "reviewer_kind": "CLAUDE",
                "review_reference": review,
                "scope": scope,
                "approval_threshold": threshold,
            }))
            git(repo, "add", ".")
            git(repo, "commit", "-q", "-m", "add gatechange")
            passed = subprocess.run(command, text=True, capture_output=True)
            self.assertEqual(passed.returncode, 0, passed.stdout + passed.stderr)
            self.assertIn("gate-integrity: PASS", passed.stdout)

    def test_m2_ci_proves_unsuppressed_high_is_blocked(self):
        workflow = CI_WORKFLOW.read_text()
        fixture = ZIZMOR_FIXTURE.read_text()
        self.assertIn("id: zizmor-negative", workflow)
        self.assertIn("continue-on-error: true", workflow)
        self.assertIn("ZIZMOR_NEGATIVE_OUTCOME: ${{ steps.zizmor-negative.outcome }}", workflow)
        self.assertIn('[[ "${ZIZMOR_NEGATIVE_OUTCOME}" != "failure" ]]', workflow)
        self.assertIn("pull_request_target:", fixture)
        self.assertNotIn("zizmor: ignore", fixture)


if __name__ == "__main__":
    unittest.main()
