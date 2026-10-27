from __future__ import annotations

import importlib.util
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = ROOT.parent
CHECKER = ROOT / "scripts/ci/check_pull_request_target_exceptions.py"
POLICY = ROOT / "policies/protected-paths.txt"
REQUIREMENTS = ROOT / "requirements-ci.txt"
CI_WORKFLOW = REPO_ROOT / ".github/workflows/ci.yml"
SAFETY_WORKFLOW = REPO_ROOT / ".github/workflows/ksp-safety-gates.yml"


def load_checker():
    spec = importlib.util.spec_from_file_location("gc6_trigger_checker", CHECKER)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


checker = load_checker()


class GC0006HardeningTests(unittest.TestCase):
    def test_n3_real_yaml_parser_closes_quote_state_bypasses(self):
        samples = {
            "single-quoted-stray-double": "name: 'say \"hi'\non: {\"pull_request\\u005ftarget\": {}}\n",
            "inline-comment-stray-double": "name: ci # \"\non: {\"pull_request\\u005ftarget\": {}}\n",
            "plain-scalar-stray-double": "name: 5\"\non: {\"pull_request\\u005ftarget\": {}}\n",
            "escaped-line-break": 'on: [push, "pull_request_\\\n  target"]\n',
        }
        for name, text in samples.items():
            with self.subTest(name=name):
                self.assertTrue(checker.uses_pull_request_target(text))

    def test_n3_alias_and_literal_forms_still_detect(self):
        samples = [
            "triggers: &t [push, pull_request_target]\non: *t\n",
            "on: pull_request_target\n",
            "on:\n  pull_request_target:\n",
            "on:\n  - push\n  - pull_request_target\n",
        ]
        for text in samples:
            with self.subTest(text=text):
                self.assertTrue(checker.uses_pull_request_target(text))

    def test_n3_malformed_workflow_fails_closed(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            workflows = root / ".github/workflows"
            workflows.mkdir(parents=True)
            allowlist = root / "governance/workflow-exceptions/pull-request-target.txt"
            allowlist.parent.mkdir(parents=True)
            allowlist.write_text("# none\n")
            (workflows / "broken.yml").write_text("on: [push,\n")
            errors = checker.validate(root, workflows, allowlist)
            self.assertTrue(
                any("workflow trigger validation failed" in error for error in errors),
                errors,
            )

    def test_m1_all_zizmor_config_locations_are_prohibited(self):
        for config_path in checker.ZIZMOR_CONFIG_PATHS:
            with self.subTest(config_path=config_path), tempfile.TemporaryDirectory() as td:
                root = Path(td)
                workflows = root / ".github/workflows"
                workflows.mkdir(parents=True)
                (workflows / "safe.yml").write_text("on: push\njobs: {}\n")
                allowlist = root / "governance/workflow-exceptions/pull-request-target.txt"
                allowlist.parent.mkdir(parents=True)
                allowlist.write_text("# none\n")
                target = root / config_path
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text("rules:\n  dangerous-triggers: ignore\n")
                errors = checker.validate(root, workflows, allowlist)
                self.assertTrue(
                    any(config_path in error and "prohibited" in error for error in errors),
                    errors,
                )

    def test_m1_zizmor_config_paths_and_parser_requirement_are_protected(self):
        policy = POLICY.read_text().splitlines()
        for path in (
            "zizmor.yml",
            "zizmor.yaml",
            ".github/zizmor.yml",
            ".github/zizmor.yaml",
            "ksp-bootstrap/requirements-ci.txt",
        ):
            self.assertIn(path, policy)

    def test_parser_dependency_is_exact_and_hash_pinned(self):
        req = REQUIREMENTS.read_text()
        self.assertIn("PyYAML==6.0.3", req)
        self.assertIn(
            "sha256:ba1cc08a7ccde2d2ec775841541641e4548226580ab850948cbfda66a1befcdc",
            req,
        )
        for workflow in (CI_WORKFLOW, SAFETY_WORKFLOW):
            text = workflow.read_text()
            self.assertIn("--require-hashes -r ksp-bootstrap/requirements-ci.txt", text)
            self.assertIn("--only-binary=:all:", text)
            self.assertIn("--no-deps", text)


if __name__ == "__main__":
    unittest.main()
