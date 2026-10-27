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
ALLOWLIST = REPO_ROOT / "governance/workflow-exceptions/pull-request-target.txt"


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


trigger_mod = load_module("gc5_pr_target_checker", TRIGGER_CHECKER)


def git(cwd: Path, *args: str) -> str:
    return subprocess.check_output(
        ["git", *args], cwd=cwd, text=True, stderr=subprocess.STDOUT
    ).strip()


def run_checker(repo: Path, base: str, head: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            "-I",
            str(GATE_CHECKER),
            "--repo-root",
            str(repo),
            "--base",
            base,
            "--head",
            head,
        ],
        cwd=repo,
        text=True,
        capture_output=True,
    )


def write_receipt(
    repo: Path,
    *,
    approved_head_sha: str,
    scope: list[str],
    threshold: str,
    review_reference: str,
) -> None:
    path = repo / "ksp-bootstrap/governance/approvals/GC-9999.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "decision": "APPROVED",
                "approval_id": "TEST-GC-9999",
                "approved_gate_change_id": "GC-9999",
                "approved_scope": scope,
                "approved_threshold": threshold,
                "review_reference": review_reference,
                "review_revision_id": "TEST-REVISION",
                "approved_head_sha": approved_head_sha,
                "approval_consumed_by": "GC-9999",
            }
        )
    )


class GC0005HardeningTests(unittest.TestCase):
    def test_n1_detects_all_reviewed_trigger_bypass_forms(self):
        samples = {
            "multiline-flow": "on: [push,\n  pull_request_target]\n",
            "quoted-on": '"on": [push, pull_request_target]\n',
            "nested-on": "env:\n  on: harmless\non:\n  pull_request_target:\n",
            "anchor-alias": "triggers: &t [push, pull_request_target]\non: *t\n",
            "plain-scalar-hash": "on: {push: {branches: [x#y]}, pull_request_target: {}}\n",
            "escaped-key": 'on: {"pull_request\\u005ftarget": {}}\n',
        }
        for name, sample in samples.items():
            with self.subTest(name=name):
                self.assertTrue(trigger_mod.uses_pull_request_target(sample))

        self.assertFalse(
            trigger_mod.uses_pull_request_target(
                "# on: pull_request_target\n# purely commented example\non: push\n"
            )
        )

    def test_n1_apostrophe_does_not_hide_zizmor_ignore(self):
        sample = (
            "on: {push: {branches: [it's]}, pull_request_target: {}} "
            "# zizmor: ignore[dangerous-triggers]\n"
        )
        self.assertTrue(trigger_mod.has_zizmor_ignore_comment(sample))
        self.assertEqual(
            trigger_mod.zizmor_ignore_rules(sample), {"dangerous-triggers"}
        )

    def test_n1_combined_escape_plus_apostrophe_bypass_fails_closed(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            workflows = root / ".github/workflows"
            workflows.mkdir(parents=True)
            allowlist = root / "governance/workflow-exceptions/pull-request-target.txt"
            allowlist.parent.mkdir(parents=True)
            allowlist.write_text("# none\n")
            (workflows / "evil.yml").write_text(
                "on: {push: {branches: [it's]}, \"pull_request\\u005ftarget\": {}} "
                "# zizmor: ignore[dangerous-triggers]\n"
                "jobs: {}\n"
            )
            errors = trigger_mod.validate(root, workflows, allowlist)
            self.assertTrue(
                any("pull_request_target workflow lacks" in error for error in errors),
                errors,
            )
            self.assertTrue(
                any("zizmor inline ignore lacks" in error for error in errors),
                errors,
            )

    def test_n1_zizmor_exceptions_are_rule_scoped(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            workflows = root / ".github/workflows"
            workflows.mkdir(parents=True)
            workflow = workflows / "authority.yml"
            workflow.write_text(
                "on:\n  pull_request_target:\n"
                "# zizmor: ignore[dangerous-triggers]\n"
                "jobs: {}\n"
            )
            allowlist = root / "governance/workflow-exceptions/pull-request-target.txt"
            allowlist.parent.mkdir(parents=True)
            allowlist.write_text(
                ".github/workflows/authority.yml pull_request_target\n"
            )
            errors = trigger_mod.validate(root, workflows, allowlist)
            self.assertTrue(
                any("dangerous-triggers" in error for error in errors), errors
            )

            allowlist.write_text(
                ".github/workflows/authority.yml pull_request_target\n"
                ".github/workflows/authority.yml zizmor:dangerous-triggers\n"
            )
            self.assertEqual(trigger_mod.validate(root, workflows, allowlist), [])

            workflow.write_text(
                "on:\n  pull_request_target:\n"
                "# zizmor: ignore[template-injection]\n"
                "jobs: {}\n"
            )
            errors = trigger_mod.validate(root, workflows, allowlist)
            self.assertTrue(
                any("template-injection" in error for error in errors), errors
            )

    def test_n1_current_authority_exception_is_exact_path_and_rule_scoped(self):
        entries, errors = trigger_mod.load_allowlist(ALLOWLIST)
        self.assertEqual(errors, [])
        self.assertEqual(
            set(entries),
            {
                (".github/workflows/ksp-gate-authority.yml", "pull_request_target"),
                (
                    ".github/workflows/ksp-gate-authority.yml",
                    "zizmor:dangerous-triggers",
                ),
            },
        )
        self.assertEqual(
            trigger_mod.validate(
                REPO_ROOT,
                REPO_ROOT / ".github/workflows",
                ALLOWLIST,
            ),
            [],
        )

    def test_m1_end_to_end_requires_trusted_base_receipt_and_exact_sha(self):
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
            git(repo, "add", ".")
            git(repo, "commit", "-q", "-m", "anchor")
            anchor = git(repo, "rev-parse", "HEAD")

            git(repo, "checkout", "-q", "-b", "pr-head")
            (repo / "docs").mkdir()
            git(repo, "mv", "tests/test_safety.py", "docs/old_notes.py")
            scope = ["tests/test_safety.py"]
            threshold = "GC-0005 end-to-end rename proof"
            review = "TEST-REVIEW"
            gate = repo / "ksp-bootstrap/governance/gate-changes/GC-9999.json"
            gate.parent.mkdir(parents=True, exist_ok=True)
            gate.write_text(
                json.dumps(
                    {
                        "gate_change_id": "GC-9999",
                        "decision_log_id": "D-9999",
                        "reason": "test protected rename approval",
                        "reviewer_kind": "CLAUDE",
                        "review_reference": review,
                        "scope": scope,
                        "approval_threshold": threshold,
                    }
                )
            )
            git(repo, "add", ".")
            git(repo, "commit", "-q", "-m", "rename with gatechange")
            head = git(repo, "rev-parse", "HEAD")
            self.assertRegex(head, r"^[0-9a-f]{40}$")

            missing = run_checker(repo, anchor, head)
            self.assertEqual(missing.returncode, 1, missing.stdout + missing.stderr)
            self.assertIn(
                "trusted base-branch approval ledger entry is missing", missing.stdout
            )

            write_receipt(
                repo,
                approved_head_sha=head,
                scope=scope,
                threshold=threshold,
                review_reference=review,
            )
            git(repo, "add", ".")
            git(repo, "commit", "-q", "-m", "head-only receipt")
            head_only = git(repo, "rev-parse", "HEAD")
            head_only_result = run_checker(repo, anchor, head_only)
            self.assertEqual(
                head_only_result.returncode,
                1,
                head_only_result.stdout + head_only_result.stderr,
            )
            self.assertIn(
                "trusted base-branch approval ledger entry is missing",
                head_only_result.stdout,
            )

            git(repo, "checkout", "-q", "-b", "base-approved", anchor)
            write_receipt(
                repo,
                approved_head_sha=head,
                scope=scope,
                threshold=threshold,
                review_reference=review,
            )
            git(repo, "add", ".")
            git(repo, "commit", "-q", "-m", "trusted base receipt")
            base_approved = git(repo, "rev-parse", "HEAD")
            passed = run_checker(repo, base_approved, head)
            self.assertEqual(passed.returncode, 0, passed.stdout + passed.stderr)
            self.assertIn("gate-integrity: PASS", passed.stdout)

            git(repo, "checkout", "-q", "-b", "base-mismatch", anchor)
            write_receipt(
                repo,
                approved_head_sha="0" * 40,
                scope=scope,
                threshold=threshold,
                review_reference=review,
            )
            git(repo, "add", ".")
            git(repo, "commit", "-q", "-m", "mismatched receipt")
            base_mismatch = git(repo, "rev-parse", "HEAD")
            mismatch = run_checker(repo, base_mismatch, head)
            self.assertEqual(mismatch.returncode, 1, mismatch.stdout + mismatch.stderr)
            self.assertIn("approval is bound to head", mismatch.stdout)

            git(repo, "checkout", "-q", "-b", "base-symbolic", anchor)
            write_receipt(
                repo,
                approved_head_sha="HEAD",
                scope=scope,
                threshold=threshold,
                review_reference=review,
            )
            git(repo, "add", ".")
            git(repo, "commit", "-q", "-m", "symbolic receipt")
            base_symbolic = git(repo, "rev-parse", "HEAD")
            symbolic = run_checker(repo, base_symbolic, head)
            self.assertEqual(symbolic.returncode, 1, symbolic.stdout + symbolic.stderr)
            self.assertIn("lowercase 40-hex commit SHA", symbolic.stdout)

    def test_m2_ci_asserts_specific_zizmor_exit_and_finding(self):
        workflow = CI_WORKFLOW.read_text()
        fixture = ZIZMOR_FIXTURE.read_text()
        self.assertIn(
            "ksp-bootstrap/tests/fixtures/zizmor/unsafe_pull_request_target.yml",
            workflow,
        )
        self.assertIn("ghcr.io/zizmorcore/zizmor@sha256:", workflow)
        self.assertIn('[[ "${zizmor_rc}" -ne 14 ]]', workflow)
        self.assertIn('grep -Fq "error[dangerous-triggers]"', workflow)
        fail_open_token = "continue-on-error" + ": true"
        self.assertNotIn(fail_open_token, workflow)
        self.assertIn("pull_request_target:", fixture)
        self.assertNotIn("zizmor: ignore", fixture)

    def test_gate_checker_rejects_non_hex_head_receipts_directly(self):
        spec = importlib.util.spec_from_file_location("gc5_gate_checker", GATE_CHECKER)
        module = importlib.util.module_from_spec(spec)
        assert spec and spec.loader
        spec.loader.exec_module(module)
        record = {
            "gate_change_id": "GC-9999",
            "decision_log_id": "D-9999",
            "reason": "test",
            "reviewer_kind": "CLAUDE",
            "review_reference": "TEST",
            "scope": ["tests/x.py"],
            "approval_threshold": "test",
        }
        approval = {
            "decision": "APPROVED",
            "approval_id": "TEST",
            "approved_gate_change_id": "GC-9999",
            "approved_scope": ["tests/x.py"],
            "approved_threshold": "test",
            "review_reference": "TEST",
            "review_revision_id": "REV",
            "approved_head_sha": "HEAD",
        }
        with self.assertRaisesRegex(ValueError, "40-hex"):
            module.validate_gatechange_records(
                [record],
                ["tests/x.py"],
                head_sha="a" * 40,
                approvals=[approval],
            )


if __name__ == "__main__":
    unittest.main()
