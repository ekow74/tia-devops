from __future__ import annotations

import importlib.util
import json
import os
import tempfile
import unittest
from argparse import Namespace
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = ROOT.parent
REVIEW_SCRIPT = ROOT / "scripts/ci/ksp_independent_review.py"
GATE_SCRIPT = ROOT / "scripts/ci/check_gate_integrity.py"
WORKFLOW = REPO_ROOT / ".github/workflows/ksp-independent-review.yml"
POLICY = ROOT / "policies/protected-paths.txt"
GATECHANGE = ROOT / "governance/gate-changes/GC-0007.json"
FORGE_FIXTURE = ROOT / "tests/fixtures/review_gate/readonly_token_forge.py"
INJECTION_FIXTURE = ROOT / "tests/fixtures/review_gate/prompt_injection.txt"
HEAD_A = "a" * 40
HEAD_B = "b" * 40
BASE = "c" * 40


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


review = load_module("ksp_independent_review", REVIEW_SCRIPT)
gate = load_module("gc0007_gate_integrity", GATE_SCRIPT)


def args(**overrides):
    values = {
        "repository": "ekow74/tia-devops",
        "pr_number": "42",
        "head_sha": HEAD_A,
        "base_sha": BASE,
        "run_id": "123456",
        "run_url": "https://github.com/ekow74/tia-devops/actions/runs/123456",
        "base_ref": "main",
        "default_branch": "main",
        "policy": str(POLICY),
    }
    values.update(overrides)
    return Namespace(**values)


class GC0007WorkflowPermissionTests(unittest.TestCase):
    def setUp(self):
        gate.configure_repo(REPO_ROOT)

    def _workflow_errors(self, text: str, filename: str = "unsafe.yml") -> list[str]:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            workflow_dir = root / ".github/workflows"
            workflow_dir.mkdir(parents=True)
            (workflow_dir / filename).write_text(text, encoding="utf-8")
            gate.configure_repo(root)
            return gate.validate_workflow_permissions(workflow_dir)

    def test_workflow_level_checks_write_is_rejected(self):
        errors = self._workflow_errors(
            "name: unsafe\non: pull_request\npermissions:\n  checks: write\njobs:\n  test:\n    runs-on: ubuntu-latest\n"
        )
        self.assertTrue(any("checks/statuses write" in error for error in errors), errors)

    def test_workflow_level_statuses_write_is_rejected(self):
        errors = self._workflow_errors(
            "name: unsafe\non: pull_request\npermissions:\n  statuses: write\njobs:\n  test:\n    runs-on: ubuntu-latest\n"
        )
        self.assertTrue(any("checks/statuses write" in error for error in errors), errors)

    def test_job_level_checks_and_statuses_write_are_rejected(self):
        for permission in ("checks", "statuses"):
            with self.subTest(permission=permission):
                errors = self._workflow_errors(
                    "name: unsafe\non: pull_request\npermissions:\n  contents: read\njobs:\n"
                    f"  test:\n    permissions:\n      {permission}: write\n    runs-on: ubuntu-latest\n"
                )
                self.assertTrue(any("checks/statuses write" in error for error in errors), errors)

    def test_pr_head_workflow_rejects_contents_and_pull_request_writes(self):
        for permission in ("contents", "pull-requests"):
            for scope in ("workflow", "job"):
                with self.subTest(permission=permission, scope=scope):
                    if scope == "workflow":
                        text = (
                            "name: unsafe\non: pull_request\npermissions:\n"
                            f"  {permission}: write\njobs:\n  test:\n    runs-on: ubuntu-latest\n"
                        )
                    else:
                        text = (
                            "name: unsafe\non: pull_request\npermissions:\n  contents: read\njobs:\n"
                            f"  test:\n    permissions:\n      {permission}: write\n"
                            "    runs-on: ubuntu-latest\n"
                        )
                    errors = self._workflow_errors(text)
                    self.assertTrue(
                        any("PR-head workflow cannot grant write permission" in error for error in errors),
                        errors,
                    )

    def test_same_named_head_workflow_cannot_forge_authoritative_check(self):
        errors = self._workflow_errors(
            "name: KSP Independent Review\non: pull_request\n"
            "permissions:\n  checks: write\njobs:\n  spoof:\n    runs-on: ubuntu-latest\n"
        )
        self.assertTrue(
            any("checks/statuses write permission is not allowed" in error for error in errors),
            errors,
        )

    def test_unlisted_workflow_write_all_is_rejected_and_exact_exception_is_scoped(self):
        text = "name: unsafe\non: push\npermissions: write-all\njobs: {}\n"
        errors = self._workflow_errors(text)
        self.assertTrue(any("checks/statuses write" in error for error in errors), errors)
        errors = self._workflow_errors(
            text, filename="ksp-gate-authority.yml"
        )
        self.assertFalse(any("checks/statuses write" in error for error in errors), errors)

    def test_pr_workflow_must_not_inherit_owner_default_permissions(self):
        errors = self._workflow_errors(
            "name: unsafe\non: pull_request\njobs:\n  test:\n    runs-on: ubuntu-latest\n"
        )
        self.assertTrue(any("explicitly set effective permissions" in error for error in errors), errors)

    def test_malformed_workflow_yaml_fails_closed(self):
        errors = self._workflow_errors("name: [broken\n")
        self.assertTrue(any("workflow YAML parse failed" in error for error in errors), errors)

    def test_all_current_pr_workflows_have_explicit_permissions(self):
        gate.configure_repo(REPO_ROOT)
        self.assertEqual(gate.validate_workflow_permissions(), [])

    def test_gc0007_scope_covers_protected_review_surfaces(self):
        record = json.loads(GATECHANGE.read_text())
        scope = record["scope"]
        required = [
            ".gitignore",
            ".github/workflows/ksp-gate-authority.yml",
            ".github/workflows/ksp-independent-review.yml",
            "ksp-bootstrap/policies/protected-paths.txt",
            "ksp-bootstrap/scripts/ci/check_gate_integrity.py",
            "ksp-bootstrap/scripts/ci/ksp_independent_review.py",
            "ksp-bootstrap/tests/test_gc0007_independent_review.py",
            "ksp-bootstrap/tests/fixtures/review_gate/readonly_token_forge.py",
            "ksp-bootstrap/governance/qualification/phase0-independent-review.md",
        ]
        for path in required:
            self.assertIn(path, scope)
            self.assertTrue(gate.protected(path), path)

    def test_reviewer_workflow_uses_default_branch_code_and_read_only_token(self):
        import yaml

        document = yaml.load(WORKFLOW.read_text(), Loader=yaml.BaseLoader)
        self.assertEqual(document["name"], "KSP Independent Review")
        self.assertEqual(document["permissions"]["contents"], "read")
        self.assertEqual(document["permissions"]["pull-requests"], "read")
        job = document["jobs"]["independent-review"]
        checkout = next(
            step for step in job["steps"]
            if step.get("uses", "").startswith("actions/checkout@")
        )
        self.assertEqual(
            checkout["with"]["ref"],
            "${{ github.event.repository.default_branch }}",
        )
        self.assertEqual(checkout["with"]["persist-credentials"], "false")
        self.assertNotIn("head.sha", checkout["with"]["ref"])
        provider_steps = [
            step for step in job["steps"]
            if step.get("name") == "Call independent reviewer (tool-free adapter)"
        ]
        self.assertEqual(len(provider_steps), 1)
        self.assertEqual(
            list(provider_steps[0]["env"]),
            ["ANTHROPIC_API_KEY", "REVIEW_INPUT", "KSP_BASE_SHA"],
        )
        self.assertEqual(WORKFLOW.read_text().count("${{ secrets.ANTHROPIC_API_KEY }}"), 1)
        self.assertIn("steps.trusted-base.outputs.sha", WORKFLOW.read_text())
        self.assertIn("Require default-branch target before checkout", WORKFLOW.read_text())


class GC0007ClassificationTests(unittest.TestCase):
    def setUp(self):
        gate.configure_repo(REPO_ROOT)

    def test_protected_rename_checks_both_old_and_new_paths(self):
        protected, paths = review.classify_files(
            [
                {
                    "filename": "docs/renamed-away.txt",
                    "previous_filename": ".github/workflows/old.yml",
                    "status": "renamed",
                }
            ],
            [".github/workflows/**"],
        )
        self.assertTrue(protected)
        self.assertEqual(paths, [".github/workflows/old.yml"])

    def test_protected_deletion_requires_review(self):
        protected, paths = review.classify_files(
            [{"filename": "ksp-bootstrap/policies/protected-paths.txt", "status": "removed"}],
            ["ksp-bootstrap/policies/**"],
        )
        self.assertTrue(protected)
        self.assertEqual(paths, ["ksp-bootstrap/policies/protected-paths.txt"])

    def test_complete_file_list_is_paginated_until_short_page(self):
        calls = []

        def fetch(page):
            calls.append(page)
            if page == 1:
                return [{"filename": f"docs/{n}.md", "status": "modified"} for n in range(100)]
            return [{"filename": ".github/CODEOWNERS", "status": "modified"}]

        files = review.list_pr_files(fetch)
        self.assertEqual(calls, [1, 2])
        self.assertEqual(len(files), 101)
        self.assertTrue(review.classify_files(files, [".github/CODEOWNERS"])[0])

    def test_github_file_list_completeness_limit_fails_closed(self):
        with self.assertRaisesRegex(review.ReviewError, "completeness limit"):
            review.list_pr_files(
                lambda _page: [
                    {"filename": f"docs/{index}.md", "status": "modified"}
                    for index in range(review.PAGE_SIZE)
                ]
            )

    def test_pagination_error_fails_review_required(self):
        with tempfile.TemporaryDirectory() as td:
            output, summary = Path(td) / "output", Path(td) / "summary"
            with patch.dict(
                os.environ,
                {"GITHUB_OUTPUT": str(output), "GITHUB_STEP_SUMMARY": str(summary)},
                clear=False,
            ), patch.object(
                review,
                "_pull_request",
                return_value={"head": {"sha": HEAD_A}},
            ), patch.object(
                review,
                "_request_files",
                side_effect=review.ReviewError("pagination failed"),
            ):
                result = review.classify_command(args())
            self.assertEqual(result, 1)
            self.assertIn("classification=error", output.read_text())
            evidence = summary.read_text()
            self.assertIn("REVIEW REQUIRED (fail closed)", evidence)
            self.assertIn(HEAD_A, evidence)
            self.assertIn("123456", evidence)

    def test_ordinary_unprotected_change_is_not_reviewed(self):
        with tempfile.TemporaryDirectory() as td:
            output, summary = Path(td) / "output", Path(td) / "summary"
            with patch.dict(
                os.environ,
                {"GITHUB_OUTPUT": str(output), "GITHUB_STEP_SUMMARY": str(summary)},
                clear=False,
            ), patch.object(
                review,
                "_pull_request",
                return_value={"head": {"sha": HEAD_A}},
            ), patch.object(
                review,
                "_request_files",
                return_value=[{"filename": "docs/readme.md", "status": "modified"}],
            ):
                result = review.classify_command(args())
            self.assertEqual(result, 0)
            self.assertIn("classification=not_required", output.read_text())
            self.assertIn("not reviewed: unprotected", summary.read_text())
            self.assertIn("NOT_REQUIRED", summary.read_text())
            self.assertIn("no model review", summary.read_text())

    def test_protected_addition_enters_review_required_path(self):
        with tempfile.TemporaryDirectory() as td:
            output, summary = Path(td) / "output", Path(td) / "summary"
            with patch.dict(
                os.environ,
                {"GITHUB_OUTPUT": str(output), "GITHUB_STEP_SUMMARY": str(summary)},
                clear=False,
            ), patch.object(
                review,
                "_pull_request",
                return_value={"head": {"sha": HEAD_A}},
            ), patch.object(
                review,
                "_request_files",
                return_value=[
                    {"filename": ".github/workflows/new.yml", "status": "added"}
                ],
            ):
                result = review.classify_command(args())
            self.assertEqual(result, 0)
            self.assertIn("classification=review_required", output.read_text())
            self.assertNotIn("NOT_REQUIRED", summary.read_text() if summary.exists() else "")

    def test_protected_head_workflow_override_is_classified_from_base_policy(self):
        protected, paths = review.classify_files(
            [
                {
                    "filename": ".github/workflows/ksp-independent-review.yml",
                    "status": "modified",
                    "patch": "name: KSP Independent Review\n# force PASS",
                }
            ],
            review.protected_paths_from_text(POLICY.read_text()),
        )
        self.assertTrue(protected)
        self.assertEqual(paths, [".github/workflows/ksp-independent-review.yml"])
        self.assertIn(
            "${{ github.event.repository.default_branch }}",
            WORKFLOW.read_text(),
        )

    def test_sha_a_pass_is_invalidated_when_current_head_becomes_sha_b(self):
        self.assertEqual(
            review.parse_verdict(review.offline_fake_adapter({"diff": "safe"}))["verdict"],
            "APPROVE",
        )
        with patch.object(
            review,
            "_pull_request",
            return_value={"head": {"sha": HEAD_B}},
        ):
            result = review.verify_head_command(
                Namespace(
                    repository="ekow74/tia-devops",
                    pr_number="42",
                    head_sha=HEAD_A,
                )
            )
        self.assertEqual(result, 1)


class GC0007AdapterTests(unittest.TestCase):
    def test_injection_is_data_and_provider_request_has_no_tools_or_secret(self):
        hostile = INJECTION_FIXTURE.read_text()
        credential = "test-provider-key-never-log"
        observed = {}

        class Response:
            def __enter__(self):
                return self

            def __exit__(self, *_):
                return False

            def read(self, _limit):
                return json.dumps(
                    {
                        "content": [
                            {
                                "type": "text",
                                "text": json.dumps(
                                    {
                                        "verdict": "REQUEST_CHANGES",
                                        "summary": "Injection instructions are untrusted data.",
                                    }
                                ),
                            }
                        ]
                    }
                ).encode()

        def opener(request, timeout):
            observed["body"] = json.loads(request.data)
            observed["timeout"] = timeout
            return Response()

        result = review.anthropic_adapter(
            {
                "repository": "ekow74/tia-devops",
                "head_sha": HEAD_A,
                "title": hostile,
                "body": hostile,
                "diff": hostile,
            },
            credential,
            opener=opener,
        )
        verdict = review.parse_verdict(result, credential)
        self.assertEqual(verdict["verdict"], "REQUEST_CHANGES")
        message_data = json.loads(observed["body"]["messages"][0]["content"])
        self.assertEqual(message_data["title"], hostile)
        self.assertEqual(message_data["diff"], hostile)
        self.assertNotIn(credential, json.dumps(observed["body"]))
        self.assertNotIn("tools", observed["body"])
        self.assertIn("untrusted data", observed["body"]["system"])
        self.assertEqual(observed["timeout"], 45)
        self.assertNotIn(credential, json.dumps(verdict))

    def test_malformed_or_unknown_provider_verdict_fails_closed(self):
        for text in (
            "not JSON",
            '{"verdict":"PASS","summary":"Unknown verdict"}',
            '{"verdict":"APPROVE","summary":"ok","extra":true}',
            '{"verdict":"APPROVE","summary":""}',
        ):
            with self.subTest(text=text), self.assertRaises(review.ReviewError):
                review.parse_verdict(text)
        with self.assertRaisesRegex(review.ReviewError, "schema validation"):
            review.parse_verdict(
                '{"verdict":"APPROVE","summary":"do not disclose key"}',
                "do not disclose key",
            )

    def test_provider_timeout_fails_closed_without_logging_credential(self):
        credential = "never-print-this"

        def timeout(_request, timeout):
            raise TimeoutError(credential)

        with self.assertRaisesRegex(review.ReviewError, "provider request failed") as raised:
            review.anthropic_adapter({"diff": "x"}, credential, opener=timeout)
        self.assertNotIn(credential, str(raised.exception))

    def test_provider_http_error_fails_closed(self):
        import urllib.error

        def server_error(request, timeout):
            raise urllib.error.HTTPError(
                request.full_url, 500, "provider error", {}, None
            )

        with self.assertRaisesRegex(review.ReviewError, "provider request failed"):
            review.anthropic_adapter({"diff": "x"}, "test-key", opener=server_error)

    def test_offline_valid_verdict_is_bound_to_exact_head_and_run_metadata(self):
        payload = {
            "repository": "ekow74/tia-devops",
            "pr_number": 42,
            "head_sha": HEAD_A,
            "trusted_base_sha": BASE,
            "diff": "safe test diff",
        }
        with tempfile.TemporaryDirectory() as td:
            input_path = Path(td) / "input.json"
            summary_path = Path(td) / "summary.md"
            input_path.write_text(json.dumps(payload))
            with patch.dict(os.environ, {"GITHUB_STEP_SUMMARY": str(summary_path)}):
                result = review.review_command(
                    Namespace(
                        input=str(input_path),
                        adapter="offline",
                        repository=payload["repository"],
                        pr_number="42",
                        head_sha=HEAD_A,
                        base_sha=BASE,
                        run_id="987654",
                        run_url="https://github.com/ekow74/tia-devops/actions/runs/987654",
                    )
                )
            self.assertEqual(result, 0)
            evidence = summary_path.read_text()
        for value in (HEAD_A, BASE, "987654"):
            self.assertIn(value, evidence)
        self.assertIn("offline fake adapter", evidence)
        self.assertIn("APPROVE", evidence)
        self.assertIn("Workflow/reviewer version", evidence)
        self.assertIn("Timestamp", evidence)
        self.assertIn("Review artifact/reference", evidence)
        self.assertNotIn("NOT_REQUIRED", evidence)

    def test_forge_fixture_gets_403_from_checks_and_statuses_mock(self):
        fixture = load_module("readonly_token_forge", FORGE_FIXTURE)
        api = fixture.ReadOnlyGitHubAPI()
        checks, status = fixture.attempt_passing_evidence(api, "ekow74/tia-devops", HEAD_A)
        self.assertEqual((checks[0], status[0]), (403, 403))
        self.assertEqual(api.requests[0][1]["head_sha"], HEAD_A)
        self.assertEqual(api.requests[1][1]["state"], "success")
        self.assertEqual(len(api.requests), 2)


if __name__ == "__main__":
    unittest.main()
