from __future__ import annotations

import importlib.util
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = ROOT.parent
GATE_CHECKER = ROOT / "scripts/ci/check_gate_integrity.py"
PR_TARGET_CHECKER = ROOT / "scripts/ci/check_pull_request_target_exceptions.py"
SAFETY_WORKFLOW = REPO_ROOT / ".github/workflows/ksp-safety-gates.yml"
AUTHORITY_WORKFLOW = REPO_ROOT / ".github/workflows/ksp-gate-authority.yml"
CI_WORKFLOW = REPO_ROOT / ".github/workflows/ci.yml"


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


gate_mod = load_module("gc4_gate_checker", GATE_CHECKER)
trigger_mod = load_module("gc4_pr_target_checker", PR_TARGET_CHECKER)


def git(cwd: Path, *args: str) -> str:
    return subprocess.check_output(
        ["git", *args], cwd=cwd, text=True, stderr=subprocess.STDOUT
    ).strip()


class GC0004HardeningTests(unittest.TestCase):
    def tearDown(self):
        gate_mod.configure_repo(REPO_ROOT)

    def test_me1_rename_reports_old_and_new_paths(self):
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td)
            git(repo, "init", "-q")
            git(repo, "config", "user.email", "test@example.invalid")
            git(repo, "config", "user.name", "KSP Test")
            (repo / "tests").mkdir()
            (repo / "tests/test_safety.py").write_text("protected\n")
            git(repo, "add", ".")
            git(repo, "commit", "-q", "-m", "base")
            base = git(repo, "rev-parse", "HEAD")

            (repo / "docs").mkdir()
            git(repo, "mv", "tests/test_safety.py", "docs/old_notes.py")
            git(repo, "commit", "-q", "-m", "rename")
            head = git(repo, "rev-parse", "HEAD")

            gate_mod.configure_repo(repo)
            changes = gate_mod.changed_files(base, head)
            self.assertIn(("D", "tests/test_safety.py"), changes)
            self.assertIn(("A", "docs/old_notes.py"), changes)

    def test_r1_detects_all_supported_pull_request_target_forms(self):
        samples = [
            "on:\n  pull_request_target:\n",
            "on: pull_request_target\n",
            "on: [push, pull_request_target]\n",
            "on:\n  - push\n  - pull_request_target\n",
            "on: ['pull_request_target', push]\n",
        ]
        for sample in samples:
            with self.subTest(sample=sample):
                self.assertTrue(trigger_mod.uses_pull_request_target(sample))

        self.assertFalse(
            trigger_mod.uses_pull_request_target(
                "# on: pull_request_target\nname: pull_request_target docs\non: push\n"
            )
        )

    def test_r1_unlisted_scalar_and_flow_forms_fail_validation(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            workflows = root / ".github/workflows"
            workflows.mkdir(parents=True)
            allowlist = root / "governance/workflow-exceptions/pull-request-target.txt"
            allowlist.parent.mkdir(parents=True)
            allowlist.write_text("# none approved\n")

            for name, content in {
                "scalar.yml": "name: scalar\non: pull_request_target\n",
                "flow.yml": "name: flow\non: [push, pull_request_target]\n",
            }.items():
                (workflows / name).write_text(content)

            errors = trigger_mod.validate(root, workflows, allowlist)
            self.assertTrue(any("scalar.yml" in error for error in errors))
            self.assertTrue(any("flow.yml" in error for error in errors))

    def test_template_injection_context_values_are_step_env(self):
        text = SAFETY_WORKFLOW.read_text()
        gate_job = text.split("  gate-integrity:\n", 1)[1]
        self.assertIn("BASE_REF: ${{ github.event.pull_request.base.ref }}", gate_job)
        self.assertIn("HEAD_SHA: ${{ github.event.pull_request.head.sha }}", gate_job)
        self.assertIn("PR_NUMBER: ${{ github.event.pull_request.number }}", gate_job)
        run_block = gate_job.split("run: |", 1)[1]
        self.assertNotIn("'${{ github.event.pull_request.base.ref }}'", run_block)
        self.assertNotIn("'${{ github.event.pull_request.head.sha }}'", run_block)
        self.assertNotIn("'${{ github.event.pull_request.number }}'", run_block)

    def test_zizmor_high_findings_are_blocking_and_scoped(self):
        text = CI_WORKFLOW.read_text()
        self.assertIn("advanced-security: false", text)
        self.assertIn("annotations: true", text)
        self.assertIn("min-severity: high", text)
        self.assertIn("inputs: .github/workflows", text)
        self.assertIn("version: v1.30.1", text)
        authority = AUTHORITY_WORKFLOW.read_text()
        self.assertIn("zizmor: ignore[dangerous-triggers]", authority)


if __name__ == "__main__":
    unittest.main()
