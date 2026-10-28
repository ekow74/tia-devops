from __future__ import annotations

import importlib.util
import json
import subprocess
import tempfile
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = ROOT.parent
MODULE_PATH = ROOT / "scripts/ci/phase0_review_gate.py"
AUTHORITY = ROOT / "scripts/ci/check_gate_integrity.py"
AUTHORITY_WORKFLOW = REPO_ROOT / ".github/workflows/ksp-gate-authority.yml"
WORKFLOW = REPO_ROOT / ".github/workflows/phase0-review-gate.yml"
ALLOWLIST = REPO_ROOT / "governance/workflow-exceptions/pull-request-target.txt"
AUTHORITY_POLICY = ROOT / "policies/gate-authority.json"
FIXTURES = ROOT / "tests/fixtures/reviewer"


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


review = load_module("phase0_review_gate", MODULE_PATH)
authority = load_module("phase0_gate_authority", AUTHORITY)
POLICY = json.loads(AUTHORITY_POLICY.read_text())
SHA = "a" * 40
BASE = "b" * 40


def envelope(verdict: str, identity: str, reviewed: bool) -> dict:
    return review.make_verdict(
        repository="ekow74/tia-devops",
        pr_number=11,
        head_sha=SHA,
        base_sha=BASE,
        reviewer_identity=identity,
        verdict=verdict,
        artifact_reference="run:https://github.com/ekow74/tia-devops/actions/runs/123",
        workflow_version="phase0-review/v0+" + "c" * 40,
        run_id=123,
        reviewed=reviewed,
    )


def git(root: Path, *args: str) -> str:
    return subprocess.check_output(
        ["git", *args], cwd=root, text=True, stderr=subprocess.STDOUT
    ).strip()


class Phase0ReviewGateTests(unittest.TestCase):
    def test_base_policy_classifies_renames_and_deletions(self):
        patterns = review.parse_patterns(
            "protected/**\nother/\n# ignored\n"
        )
        files = [
            {
                "filename": "unprotected/new.txt",
                "previous_filename": "protected/renamed.txt",
                "status": "renamed",
            },
            {"filename": "other/deleted.txt", "status": "removed"},
            {"filename": "unprotected/edit.txt", "status": "modified"},
        ]
        self.assertEqual(
            review.classify_files(files, patterns),
            ["other/deleted.txt", "protected/renamed.txt"],
        )

    def test_unknown_status_and_invalid_paths_fail_closed(self):
        for item in (
            {"filename": "new.txt", "status": "unknown"},
            {"filename": "../protected/file", "status": "removed"},
            {
                "filename": "new.txt",
                "previous_filename": "../protected/file",
                "status": "renamed",
            },
        ):
            with self.subTest(item=item), self.assertRaises(review.GateError):
                review.paths_from_file_listing([item])

    def test_pagination_requires_consistent_complete_pages(self):
        pages = {
            1: ([{"filename": f"file-{i}", "status": "added"} for i in range(100)],
                '<https://api.github.com/next?page=2>; rel="next"'),
            2: ([{"filename": "last", "status": "added"}], None),
        }
        result = review.collect_paginated_files(lambda page: pages[page])
        self.assertEqual(len(result), 101)

        with self.assertRaisesRegex(review.GateError, "missing its next-page link"):
            review.collect_paginated_files(
                lambda _page: (
                    [{"filename": f"file-{i}", "status": "added"} for i in range(100)],
                    None,
                )
            )
        with self.assertRaisesRegex(review.GateError, "inconsistent"):
            review.collect_paginated_files(
                lambda _page: (
                    [{"filename": "last", "status": "added"}],
                    '<https://api.github.com/next?page=2>; rel="next"',
                )
            )

    def test_pagination_cap_fails_instead_of_accepting_truncation(self):
        def full_page(_page):
            return (
                [{"filename": "file.txt", "status": "added"}] * 100,
                '<https://api.github.com/next?page=2>; rel="next"',
            )

        with self.assertRaisesRegex(review.GateError, "pagination bound"):
            review.collect_paginated_files(full_page)

    def test_api_pagination_link_must_stay_on_expected_page(self):
        with self.assertRaisesRegex(review.GateError, "outside the expected API page"):
            review._page_link_is_expected(
                '<https://attacker.example/file?page=2>; rel="next"',
                api_root="https://api.github.com",
                route="/repos/ekow74/tia-devops/pulls/11/files",
                page=1,
            )

    def test_strict_verdict_schema_and_exact_bindings(self):
        value = envelope("UNAVAILABLE", "provider-unconfigured", False)
        expected = {
            "repository": "ekow74/tia-devops",
            "pr_number": 11,
            "head_sha": SHA,
            "base_sha": BASE,
            "run_id": 123,
        }
        self.assertEqual(
            review.validate_verdict(value, expected=expected, protected=True), value
        )
        for mutate in (
            lambda v: v.update(extra="field"),
            lambda v: v.update(head_sha="HEAD"),
            lambda v: v.update(run_id=124),
            lambda v: v.update(verdict="NOT_REQUIRED", reviewer_identity="not-reviewed"),
            lambda v: v.update(
                artifact_reference="run:https://github.com/ekow74/tia-devops/actions/runs/999"
            ),
            lambda v: v.update(workflow_version="phase0-review/v0+unknown"),
        ):
            changed = dict(value)
            mutate(changed)
            with self.subTest(changed=changed), self.assertRaises(review.GateError):
                review.validate_verdict(changed, expected=expected, protected=True)

    def test_not_required_is_explicitly_not_reviewed(self):
        value = envelope("NOT_REQUIRED", "not-reviewed", False)
        self.assertEqual(
            review.validate_verdict(value, protected=False)["verdict"],
            "NOT_REQUIRED",
        )
        with self.assertRaisesRegex(review.GateError, "must not claim"):
            review.validate_verdict(
                envelope("NOT_REQUIRED", "offline-test-adapter", True),
                protected=False,
            )

    def test_test_adapter_does_not_follow_injected_instructions(self):
        fixture = json.loads((FIXTURES / "prompt-injection.json").read_text())
        verdict, reviewed = review.OfflineTestAdapter().review(fixture)
        self.assertEqual((verdict, reviewed), ("APPROVED", True))
        value = envelope(verdict, "offline-test-adapter", reviewed)
        self.assertEqual(
            review.validate_verdict(value, protected=True)["reviewer_identity"],
            "offline-test-adapter",
        )
        self.assertNotIn("GITHUB_TOKEN", json.dumps(value))

    def test_unavailable_adapter_fails_closed_for_protected_paths(self):
        verdict, reviewed = review.UnconfiguredAdapter().review(["ksp-bootstrap/tests/x"])
        self.assertEqual((verdict, reviewed), ("UNAVAILABLE", False))
        value = envelope(verdict, "provider-unconfigured", reviewed)
        self.assertEqual(
            review.validate_verdict(value, protected=True)["verdict"],
            "UNAVAILABLE",
        )

    def test_permission_policy_blocks_untrusted_check_and_status_writers(self):
        for filename in (
            "permission-checks-write.yml",
            "permission-block-scalar.yml",
            "permission-statuses-write-head.yml",
            "permission-alias-key.yml",
            "permission-explicit-key.yml",
            "permission-line-continuation.yml",
        ):
            with self.subTest(fixture=filename), self.assertRaises(ValueError):
                authority.validate_workflow_permissions(
                    ".github/workflows/untrusted.yml",
                    (FIXTURES / filename).read_text(),
                    POLICY,
                )

        reproductions = (
            (
                "flow root mapping",
                "{name: test, on: push, permissions: {statuses: write}, "
                "jobs: {test: {runs-on: ubuntu-latest, steps: [{run: echo test}]}}}",
            ),
            (
                "job flow permission without explicit workflow permission",
                "on: push\n"
                "jobs: {test: {permissions: {statuses: write}, "
                "runs-on: ubuntu-latest, steps: [{run: echo test}]}}",
            ),
            (
                "flow job permission mapping",
                "on: push\npermissions: {contents: read}\n"
                "jobs: {test: {permissions: {statuses: write}, "
                "runs-on: ubuntu-latest, steps: [{run: echo test}]}}",
            ),
        )
        for label, workflow in reproductions:
            expected_error = (
                "workflow-level permissions must be explicit"
                if label.startswith("job flow permission without")
                else "outside reviewed authority paths"
            )
            with self.subTest(label=label), self.assertRaisesRegex(ValueError, expected_error):
                authority.validate_workflow_permissions(
                    ".github/workflows/untrusted.yml", workflow, POLICY
                )

        for scope in ("checks", "statuses"):
            with self.subTest(scope=scope), self.assertRaisesRegex(
                ValueError, "outside reviewed authority paths"
            ):
                authority.validate_workflow_permissions(
                    ".github/workflows/untrusted.yml",
                    f"on: push\npermissions: {{contents: read}}\n"
                    f"jobs: {{test: {{permissions: {{{scope}: write}}}}}}",
                    POLICY,
                )

        for workflow in (
            "on: pull_request_target\npermissions: {checks: write}\n"
            "jobs: {review: {runs-on: ubuntu-latest}}",
            "on: pull_request_target\npermissions: {statuses: write}\n"
            "jobs: {review: {permissions: {contents: read, statuses: write}}}",
            "on: {pull_request_target: {}, workflow_dispatch: {}}\n"
            "permissions: {checks: write}\n"
            "jobs: {review: {runs-on: ubuntu-latest}}",
        ):
            self.assertIsNone(
                authority.validate_workflow_permissions(
                    ".github/workflows/phase0-review-gate.yml", workflow, POLICY
                )
            )

        read_only = (
            "on: [push, pull_request]\npermissions: {contents: read}\n"
            "jobs: {build: {runs-on: ubuntu-latest}, "
            "test: {permissions: read-all, runs-on: ubuntu-latest}}"
        )
        self.assertIsNone(
            authority.validate_workflow_permissions(
                ".github/workflows/untrusted.yml", read_only, POLICY
            )
        )
        with self.assertRaisesRegex(ValueError, "repository defaults are unverified"):
            authority.validate_workflow_permissions(
                ".github/workflows/untrusted.yml",
                "on: push\njobs: {test: {runs-on: ubuntu-latest}}",
                POLICY,
            )

        with self.assertRaisesRegex(ValueError, "outside reviewed authority paths"):
            authority.validate_workflow_permissions(
                ".github/workflows/untrusted.yml",
                "on: push\npermissions: {statuses: write}\n"
                "jobs: {test: {permissions: {statuses: write}}}",
                POLICY,
            )
        self.assertIsNone(
            authority.validate_workflow_permissions(
                ".github/workflows/phase0-review-gate.yml",
                "on: pull_request_target\npermissions: {statuses: write}\n"
                "jobs: {review: {permissions: {contents: read}}}",
                POLICY,
            )
        )
        self.assertIsNone(
            authority.validate_workflow_permissions(
                ".github/workflows/untrusted.yml",
                "on: push\npermissions: write-all\n"
                "jobs: {build: {permissions: {contents: read}}}",
                POLICY,
            )
        )

    def test_permission_decoder_handles_aliases_merges_and_write_all(self):
        merged_permissions = (
            "on: pull_request_target\n"
            "read_permissions: &read_permissions {contents: read}\n"
            "write_permission: &write_permission {checks: write}\n"
            "permissions:\n  <<: [*read_permissions, *write_permission]\n"
            "jobs: {review: {runs-on: ubuntu-latest}}"
        )
        self.assertIsNone(
            authority.validate_workflow_permissions(
                ".github/workflows/phase0-review-gate.yml",
                merged_permissions,
                POLICY,
            )
        )

        for permissions in (
            "write-all",
            "{checks: write}",
            '{\"chec\\u006bs\": write}',
            '{checks: \"writ\\u0065\"}',
        ):
            workflow = (
                "on: push\npermissions: "
                + permissions
                + "\njobs: {test: {runs-on: ubuntu-latest}}"
            )
            with self.subTest(permissions=permissions), self.assertRaises(ValueError):
                authority.validate_workflow_permissions(
                    ".github/workflows/untrusted.yml", workflow, POLICY
                )

        malformed = (
            "on: push\npermissions: {checks: [write]}\n"
            "jobs: {test: {runs-on: ubuntu-latest}}",
            "on: push\npermissions: {checks: read, checks: write}\n"
            "jobs: {test: {runs-on: ubuntu-latest}}",
            "on: push\npermissions: {unknown-scope: write}\n"
            "jobs: {test: {runs-on: ubuntu-latest}}",
        )
        for workflow in malformed:
            with self.subTest(workflow=workflow), self.assertRaises(ValueError):
                authority.validate_workflow_permissions(
                    ".github/workflows/untrusted.yml", workflow, POLICY
                )

        mixed_triggers = (
            "on: [pull_request_target, pull_request]\n"
            "permissions: {checks: write}\n"
            "jobs: {review: {runs-on: ubuntu-latest}}"
        )
        with self.assertRaisesRegex(ValueError, "forbidden on pull_request"):
            authority.validate_workflow_permissions(
                ".github/workflows/phase0-review-gate.yml",
                mixed_triggers,
                POLICY,
            )

    def test_authority_permission_scan_uses_base_policy_on_head_tree(self):
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td)
            git(repo, "init", "-q")
            git(repo, "config", "user.email", "test@example.invalid")
            git(repo, "config", "user.name", "Phase0 test")
            policy_path = repo / "ksp-bootstrap/policies/gate-authority.json"
            policy_path.parent.mkdir(parents=True)
            policy_path.write_text(AUTHORITY_POLICY.read_text())
            workflow = repo / ".github/workflows/ksp-gate-authority.yml"
            workflow.parent.mkdir(parents=True)
            workflow.write_text(
                "on:\n  pull_request_target:\n"
                "permissions:\n  contents: read\n"
                "jobs:\n  authority:\n    runs-on: ubuntu-latest\n"
            )
            git(repo, "add", ".")
            git(repo, "commit", "-q", "-m", "trusted base")
            base = git(repo, "rev-parse", "HEAD")

            spoof = repo / ".github/workflows/reviewer-workflow-spoof.yml"
            spoof.write_text(
                "on:\n  pull_request:\n"
                "permissions:\n  statuses: write\n"
                "jobs:\n  test:\n    runs-on: ubuntu-latest\n"
            )
            head_policy = json.loads(AUTHORITY_POLICY.read_text())
            head_policy["reviewed_authority_paths"] = [
                ".github/workflows/reviewer-workflow-spoof.yml"
            ]
            policy_path.write_text(json.dumps(head_policy))
            git(repo, "add", ".")
            git(repo, "commit", "-q", "-m", "untrusted workflow and policy")
            head = git(repo, "rev-parse", "HEAD")
            authority.configure_repo(repo)
            try:
                with self.assertRaisesRegex(
                    ValueError,
                    "outside reviewed authority paths|write permissions are forbidden",
                ):
                    authority.validate_head_workflow_permissions(base, head)
            finally:
                authority.configure_repo(REPO_ROOT)

    def test_current_reviewer_workflow_is_base_trusted_and_read_only(self):
        text = WORKFLOW.read_text()
        self.assertIn("pull_request_target:", text)
        self.assertIn(
            "types: [opened, synchronize, reopened, ready_for_review, edited]", text
        )
        self.assertIn("cancel-in-progress: true", text)
        self.assertIn("ref: ${{ github.event.pull_request.base.sha }}", text)
        self.assertIn("persist-credentials: false", text)
        self.assertNotIn("checks: write", text)
        self.assertNotIn("statuses: write", text)
        self.assertNotIn("permissions: write-all", text)
        self.assertNotIn(
            "ref: ${{ github.event.pull_request.head.sha }}", text
        )
        self.assertNotIn("git checkout", text)
        self.assertEqual(text.count("${{ secrets.GITHUB_TOKEN }}"), 1)

    def test_authority_installs_existing_hash_pinned_yaml_parser(self):
        text = AUTHORITY_WORKFLOW.read_text()
        self.assertIn("Install hash-pinned YAML parser", text)
        self.assertIn("--require-hashes -r ksp-bootstrap/requirements-ci.txt", text)
        self.assertIn("--only-binary=:all:", text)
        self.assertIn("--no-deps", text)

    def test_all_current_workflows_have_decodable_permissions(self):
        for path in sorted((REPO_ROOT / ".github/workflows").glob("*.y*ml")):
            with self.subTest(path=path.name):
                authority.validate_workflow_permissions(
                    path.relative_to(REPO_ROOT).as_posix(),
                    path.read_text(),
                    POLICY,
                )

    def test_exact_target_exception_and_authority_policy_are_present(self):
        entries = {
            tuple(line.split())
            for line in ALLOWLIST.read_text().splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        }
        self.assertIn(
            (".github/workflows/phase0-review-gate.yml", "pull_request_target"),
            entries,
        )
        self.assertIn(
            (
                ".github/workflows/phase0-review-gate.yml",
                "zizmor:dangerous-triggers",
            ),
            entries,
        )
        self.assertEqual(POLICY["blocked_permissions"], ["checks", "statuses"])


if __name__ == "__main__":
    unittest.main()
